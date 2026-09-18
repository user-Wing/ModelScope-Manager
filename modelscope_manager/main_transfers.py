"""上传下载队列与传输统计行为。"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime
from PySide6.QtCore import QDateTime, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QTableWidgetItem
from pathlib import Path
from .app_helpers import PUBLIC_ACCOUNT_ID, format_eta, format_size, format_speed, local_path_identity, running_download_percent
from .app_workers import DownloadThread, UploadQueueItem, UploadThread
from .backup import LocalBackupFile
from .download_service import Aria2DownloadRunner, DownloadSpec, build_download_specs
from .local_paths import iter_contained_files
from .service import ModelScopeService, RemoteEntry, Repository, normalize_remote_path


class TransfersMixin:
    """上传下载队列与传输统计行为。"""

    def add_remote_download(
        self,
        entry: RemoteEntry,
        service: ModelScopeService | None = None,
        repo: Repository | None = None,
        entries: list[RemoteEntry] | None = None,
    ) -> None:
        service = service or self.service
        repo = repo or self.selected_repo
        entries = self.remote_entries if entries is None else entries
        if not service or not repo:
            return
        destination = self.download_path_edit.text().strip()
        if not destination:
            QMessageBox.information(self, self._t("需要下载路径"), self._t("请先在设置中选择默认下载路径。"))
            self._navigate(2)
            return
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, self._t("下载路径不可用"), str(exc))
            self._navigate(2)
            return
        try:
            specs = build_download_specs(
                service,
                repo,
                entries,
                entry,
                Path(destination),
            )
            account_id = PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or "")
            specs = [replace(spec, account_id=account_id, repo_type=repo.repo_type, repo_id=repo.repo_id) for spec in specs]
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法添加下载"), str(exc))
            return
        if not specs:
            QMessageBox.information(self, self._t("空文件夹"), self._t("所选目录中没有可下载的文件。"))
            return
        specs = self._resolve_download_conflicts(specs)
        if not specs:
            return
        self._enqueue_download_specs(specs)

    def _resolve_download_conflicts(self, specs: list[DownloadSpec]) -> list[DownloadSpec]:
        conflicts = [
            spec for spec in specs
            if spec.local_path.exists() or Path(str(spec.local_path) + ".aria2").exists()
        ]
        if not conflicts:
            return specs
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._t("检测到同名文件"))
        box.setText(self._tf(
            "检测到本地存在 {count} 个同名文件，是否覆盖？", count=len(conflicts),
        ))
        box.setInformativeText(self._t(
            "恢复模式会先比较大小并校验 SHA-256；已一致的文件直接跳过，部分文件交给 aria2 尝试断点续传。"
        ))
        overwrite_button = box.addButton(self._t("强制覆盖"), QMessageBox.ButtonRole.DestructiveRole)
        resume_button = box.addButton(self._t("恢复模式"), QMessageBox.ButtonRole.AcceptRole)
        skip_button = box.addButton(self._t("不覆盖"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(resume_button)
        box.exec()
        chosen = box.clickedButton()
        conflict_paths = {str(spec.local_path).casefold() for spec in conflicts}
        if chosen is overwrite_button:
            return [replace(spec, conflict_mode="overwrite") for spec in specs]
        if chosen is resume_button:
            return [replace(spec, conflict_mode="resume") for spec in specs]
        if chosen is skip_button:
            kept = [spec for spec in specs if str(spec.local_path).casefold() not in conflict_paths]
            self._log(f"已跳过 {len(specs) - len(kept)} 个本地同名文件")
            return kept
        return []

    def _enqueue_download_specs(
        self, specs: list[DownloadSpec], auto_start_delay_ms: int = 0, *, auto_start: bool = True,
    ) -> int:
        known = {str(spec.local_path).lower(): index for index, spec in enumerate(self.download_specs)}
        added = 0
        for spec in specs:
            key = str(spec.local_path).lower()
            if key in known:
                row = known[key]
                state = self.download_states.get(str(self.download_specs[row].local_path), "waiting")
                if state not in {"completed", "failed", "stopped"}:
                    continue
                old_path = str(self.download_specs[row].local_path)
                self.download_specs[row] = spec
                self.download_states.pop(old_path, None)
                self.download_states[str(spec.local_path)] = "waiting"
                self.download_table.item(row, 0).setText(spec.remote_path)
                self.download_table.item(row, 1).setText(str(spec.local_path))
                self.download_table.item(row, 2).setText(self._t("等待下载"))
                added += 1
                continue
            known[key] = len(self.download_specs)
            self.download_specs.append(spec)
            self.download_states[str(spec.local_path)] = "waiting"
            row = self.download_table.rowCount()
            self.download_table.insertRow(row)
            self.download_table.setItem(row, 0, QTableWidgetItem(spec.remote_path))
            local_item = QTableWidgetItem(str(spec.local_path))
            local_item.setToolTip(str(spec.local_path))
            self.download_table.setItem(row, 1, local_item)
            self.download_table.setItem(row, 2, QTableWidgetItem("等待下载"))
            added += 1
        self._navigate(1)
        self.queue_tabs.setCurrentIndex(1)
        self._update_download_enabled()
        self._log(f"已添加 {added} 个文件到下载队列")
        self._save_transfer_queues()
        if added and auto_start:
            QTimer.singleShot(auto_start_delay_ms, self._auto_start_download)
        return added

    def new_folder(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not accepted or not name.strip():
            return
        try:
            target = normalize_remote_path(self.current_directory_path, name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, self._t("路径无效"), str(exc))
            return
        self._set_current_directory(target)
        self.settings.setValue("target_folder", target)
        self._log(f"目标文件夹设为：/{target}（上传内容后会在仓库中创建）")

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件")
        if files:
            self._repository_paths_dropped(files, RemoteEntry(self.current_directory_path, is_dir=True))

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要上传的文件夹")
        if folder:
            self._repository_paths_dropped(
                [folder], RemoteEntry(self.current_directory_path, is_dir=True),
            )

    def add_paths(self, raw_paths: list[str], target: str | None = None) -> int:
        try:
            target = normalize_remote_path(self.current_directory_path if target is None else target)
        except ValueError as exc:
            QMessageBox.warning(self, self._t("路径无效"), str(exc))
            return 0
        known = {(str(item.path).lower(), item.target) for item in self.upload_items}
        added = 0
        for raw in raw_paths:
            path = Path(raw)
            try:
                resolved = path.resolve()
            except OSError:
                continue
            key = (str(resolved).lower(), target)
            if not resolved.exists() or key in known:
                continue
            known.add(key)
            self.upload_items.append(UploadQueueItem(resolved, target))
            added += 1
        self._render_upload_queue()
        self._update_upload_enabled()
        self._save_transfer_queues()
        return added

    def clear_queue(self) -> None:
        self.upload_items = [
            item for item in self.upload_items if item.status in {"uploading", "paused"}
        ]
        self._render_upload_queue()
        self._update_upload_enabled()
        self._save_transfer_queues()

    def _render_upload_queue(self) -> None:
        self.queue_table.setRowCount(0)
        labels = {
            "waiting": self._t("等待"),
            "uploading": self._t("上传中"),
            "paused": self._t("已暂停"),
            "completed": self._t("完成"),
            "failed": self._t("失败"),
            "cancelled": self._t("已取消"),
            "skipped": self._t("已跳过"),
        }
        for item in self.upload_items:
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            local = QTableWidgetItem(str(item.path))
            local.setFlags(local.flags() & ~Qt.ItemFlag.ItemIsEditable)
            kind = QTableWidgetItem("文件夹" if item.path.is_dir() else "文件")
            kind.setFlags(kind.flags() & ~Qt.ItemFlag.ItemIsEditable)
            target = QTableWidgetItem(item.target or "/")
            status = QTableWidgetItem(labels.get(item.status, item.status))
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if item.status == "completed":
                status.setForeground(QColor("#0f7b0f"))
            elif item.status in {"failed", "cancelled", "skipped"}:
                status.setForeground(QColor("#c42b1c"))
            else:
                status.setForeground(self.queue_table.palette().color(QPalette.ColorRole.Text))
            self.queue_table.setItem(row, 0, local)
            self.queue_table.setItem(row, 1, kind)
            self.queue_table.setItem(row, 2, target)
            self.queue_table.setItem(row, 3, status)

    def _set_upload_status(self, item: UploadQueueItem, status: str, text: str | None = None, message: str = "") -> None:
        item.status = status
        row = self.upload_items.index(item)
        status_item = self.queue_table.item(row, 3)
        if status_item is None:
            self._render_upload_queue()
            status_item = self.queue_table.item(row, 3)
        status_item.setText(text or self._t({
            "waiting": "等待", "uploading": "上传中", "paused": "已暂停",
            "completed": "完成", "failed": "失败", "cancelled": "已取消",
            "skipped": "已跳过",
        }.get(status, status)))
        status_item.setToolTip(message)
        if status == "completed":
            status_item.setForeground(QColor("#0f7b0f"))
        elif status in {"failed", "cancelled", "skipped"}:
            status_item.setForeground(QColor("#c42b1c"))
        else:
            status_item.setForeground(self.queue_table.palette().color(QPalette.ColorRole.Text))
        self._save_transfer_queues()

    def clear_download_queue(self) -> None:
        if self.task and self.task.isRunning():
            return
        self.download_specs.clear()
        self.download_states.clear()
        self.backup_sync_job_paths.clear()
        self.active_download_specs.clear()
        self.download_table.setRowCount(0)
        self.download_progress.setValue(0)
        self.download_stats.setText(self._t("速度：-- · 剩余：--"))
        self._update_download_enabled()
        self._save_transfer_queues()

    def _save_transfer_queues(self) -> None:
        if getattr(self, "_restoring_settings", False):
            return
        uploads = [
            {
                "path": str(item.path), "target": item.target,
                "status": "waiting" if item.status in {"uploading", "paused"} else item.status,
                "completed_files": [str(path) for path in item.completed_files],
            }
            for item in self.upload_items if item.status not in {"completed", "cancelled"}
        ]
        downloads = [
            {
                "remote_path": spec.remote_path, "local_path": str(spec.local_path), "url": spec.url,
                "size": spec.size, "sha256": spec.sha256, "account_id": spec.account_id,
                "repo_type": spec.repo_type, "repo_id": spec.repo_id,
                "conflict_mode": spec.conflict_mode,
            }
            for spec in self.download_specs
            if self.download_states.get(str(spec.local_path), "waiting") != "completed"
        ]
        self.settings.setValue("transfer/pending_uploads", json.dumps(uploads, ensure_ascii=False))
        self.settings.setValue("transfer/pending_downloads", json.dumps(downloads, ensure_ascii=False))

    def _restore_transfer_queues(self) -> None:
        try:
            uploads = json.loads(str(self.settings.value("transfer/pending_uploads", "[]")))
            downloads = json.loads(str(self.settings.value("transfer/pending_downloads", "[]")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        for item in uploads:
            path = Path(str(item.get("path", "")))
            if path.exists():
                queue_item = UploadQueueItem(path, str(item.get("target", "")), "waiting")
                queue_item.completed_files = {Path(value) for value in item.get("completed_files", [])}
                self.upload_items.append(queue_item)
        specs = []
        for item in downloads:
            if not item.get("url") or not item.get("remote_path"):
                continue
            specs.append(DownloadSpec(
                str(item["remote_path"]), Path(str(item.get("local_path", ""))), str(item["url"]),
                int(item.get("size", 0)), str(item.get("sha256", "")), "",
                str(item.get("account_id", "")), str(item.get("repo_type", "")), str(item.get("repo_id", "")),
                str(item.get("conflict_mode", "resume")),
            ))
        if specs:
            # A private account may not be connected yet during startup. Keep
            # restored downloads ready instead of failing before login finishes.
            self._enqueue_download_specs(specs, auto_start=False)
        self._render_upload_queue()
        self._update_upload_enabled()
        self._save_transfer_queues()

    def _update_upload_enabled(self) -> None:
        active = bool(
            self.service and self.selected_repo
            and not self.selected_repo_public
            and any(item.status == "waiting" for item in self.upload_items)
        )
        if self.task and self.task.isRunning() and not isinstance(self.task, UploadThread):
            active = False
        if self.backup_thread and self.backup_thread.isRunning():
            active = False
        self.upload_button.setEnabled(active)

    def _update_download_enabled(self) -> None:
        active = any(
            self.download_states.get(str(spec.local_path), "waiting") in {"waiting", "failed", "stopped"}
            for spec in self.download_specs
        )
        if self.task and self.task.isRunning():
            active = False
        if self.backup_thread and self.backup_thread.isRunning():
            active = False
        self.download_button.setEnabled(active)

    def _sample_transfer_statistics(self) -> None:
        now = time.time()
        active_upload = bool(
            isinstance(self.task, UploadThread)
            and self.task.isRunning()
            and any(item.status == "uploading" for item in self.upload_items)
        )
        active_download = bool(isinstance(self.task, DownloadThread) and self.task.isRunning())
        self.transfer_statistics.record_speeds(
            now,
            self.current_upload_speed if active_upload else 0,
            self.current_download_speed if active_download else 0,
        )
        self.settings.setValue("statistics/lifetime_upload_bytes", self.lifetime_upload_bytes)
        self.settings.setValue("statistics/lifetime_download_bytes", self.lifetime_download_bytes)
        self._refresh_theme_notes()
        if self.upload_health_monitor.update(
            time.monotonic(), self.current_upload_speed, active_upload,
        ):
            worker = self.task
            if isinstance(worker, UploadThread) and worker.isRunning():
                worker.request_reconnect()
                self._log(self._t(
                    "上传速度连续 30 分钟低于自学习最大速度的一半；将在当前批次完成后重建连接"
                ))
        learned = self.upload_health_monitor.learned_speed
        self.statistics_learned_value.setText(format_speed(learned))
        if learned > 0:
            self.statistics_note.setText(self._tf(
                "仅统计本次软件启动以来的数据；已学习上传最大速度：{speed}，慢速阈值：{threshold}。",
                speed=format_speed(learned), threshold=format_speed(learned / 2),
            ))
        if self.queue_tabs.currentIndex() == 2:
            self._refresh_transfer_statistics()

    def _refresh_transfer_statistics(self) -> None:
        start = self.statistics_start_edit.dateTime().toSecsSinceEpoch()
        if self.statistics_live_checkbox.isChecked():
            end = int(time.time())
            self.statistics_end_edit.setDateTime(QDateTime.fromSecsSinceEpoch(end))
        else:
            end = self.statistics_end_edit.dateTime().toSecsSinceEpoch()
        if start > end:
            start, end = end, start
        samples = self.transfer_statistics.query(start, end)
        upload_total, download_total = self.transfer_statistics.totals(start, end)
        self.statistics_upload_total_value.setText(format_size(upload_total))
        self.statistics_download_total_value.setText(format_size(download_total))
        self.statistics_summary.setText(self._tf(
            "上传总量：{upload}    下载总量：{download}",
            upload=format_size(upload_total), download=format_size(download_total),
        ))
        upload_speeds = [sample.upload_speed for sample in samples]
        download_speeds = [sample.download_speed for sample in samples]
        self.upload_chart_metrics.setText(self._tf(
            "平均 {average}  ·  峰值 {peak}",
            average=format_speed(sum(upload_speeds) / max(1, len(upload_speeds))),
            peak=format_speed(max(upload_speeds, default=0)),
        ))
        self.download_chart_metrics.setText(self._tf(
            "平均 {average}  ·  峰值 {peak}",
            average=format_speed(sum(download_speeds) / max(1, len(download_speeds))),
            peak=format_speed(max(download_speeds, default=0)),
        ))
        self.upload_chart.set_data(samples, start, end)
        self.download_chart.set_data(samples, start, end)

    def _refresh_transfer_history(self) -> None:
        if not hasattr(self, "transfer_history_table"):
            return
        records = self.transfer_history_store.list()
        self.transfer_history_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = (
                "上传" if record.direction == "upload" else "下载",
                record.source, record.destination, "成功" if record.success else "失败",
                format_eta(record.duration), datetime.fromtimestamp(record.completed_at).strftime("%Y-%m-%d %H:%M:%S"),
                record.message,
            )
            for column, value in enumerate(values):
                self.transfer_history_table.setItem(row, column, QTableWidgetItem(str(value)))

    def start_upload(self) -> None:
        if isinstance(self.task, UploadThread) and self.task.isRunning():
            return
        if not self.upload_items or not any(item.status == "waiting" for item in self.upload_items):
            return
        if self.upload_session_service is None:
            if not self.service or not self.selected_repo:
                return
            upload_service = self._token_service_for_repo(self.selected_repo)
            if upload_service is None:
                QMessageBox.information(
                    self, "需要 Token 账户", "请先添加可访问该仓库的 Token 账户；上传不会使用网页登录接口。",
                )
                return
            for row, item in enumerate(self.upload_items):
                if item.status != "waiting":
                    continue
                try:
                    item.target = normalize_remote_path(self.queue_table.item(row, 2).text())
                except ValueError as exc:
                    QMessageBox.warning(self, self._t("路径无效"), str(exc))
                    return
            self.upload_session_service = upload_service
            self.upload_session_repo = self.selected_repo
            self.upload_session_account_id = self.active_account_id
            self.upload_ok = self.upload_failed = self.upload_cancelled = 0
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._start_next_upload()

    def _start_next_upload(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        if self.task and self.task.isRunning():
            return
        item = next((item for item in self.upload_items if item.status == "waiting"), None)
        if item is None:
            self._finish_upload_queue()
            return
        if not self.upload_session_service or not self.upload_session_repo:
            return
        try:
            oversized: set[Path] = set()
            if self.upload_session_repo.repo_type != "model":
                # Validate dataset folders too; size filtering is model-only, path containment is not.
                for _ in iter_contained_files(item.path):
                    pass
        except Exception as exc:
            self._set_upload_status(item, "failed", message=str(exc))
            self.upload_failed += 1
            self._log(f"上传路径被拒绝：{item.path} · {exc}")
            QTimer.singleShot(0, self._start_next_upload)
            return
        self.settings.setValue("target_folder", item.target)
        self._set_upload_status(item, "uploading", "0%")
        worker = UploadThread(
            self.upload_session_service,
            self.upload_session_repo,
            item,
            self.keep_folder_name.isChecked(),
            self.upload_queue_count.value(),
            oversized,
            self,
        )
        worker.item_done.connect(self._upload_item_done)
        worker.cancelled.connect(self._upload_cancelled)
        worker.progress_info.connect(self._upload_progress_info)
        worker.bytes_transferred.connect(self._record_upload_bytes)
        worker.reconnect_ready.connect(self._upload_reconnect_ready)
        worker.finished.connect(lambda: self._upload_thread_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.task = worker
        self.current_upload_speed = 0.0
        self._refresh_tray_status()
        self.pause_upload_button.setEnabled(True)
        self.resume_upload_button.setEnabled(False)
        self.cancel_upload_button.setEnabled(True)
        self._update_upload_enabled()
        self._update_download_enabled()
        self._log(f"开始上传 {item.path.name} 到 /{item.target}")
        self._transfer_started["upload:" + str(item.path)] = time.time()
        worker.start()

    def _upload_progress_info(self, path: str, percent: int, speed: float, eta: int) -> None:
        self.current_upload_speed = max(0.0, speed)
        self.progress.setValue(percent)
        self.upload_stats.setText(self._tf("速度：{speed} · 剩余：{eta}", speed=format_speed(speed), eta=format_eta(eta)))
        if not path:
            return
        for item in self.upload_items:
            if str(item.path) == path and item.status == "uploading":
                self._set_upload_status(item, "uploading", f"{percent}%")
                break

    def _record_upload_bytes(self, amount: int) -> None:
        amount = max(0, int(amount))
        self.transfer_statistics.add_bytes(time.time(), upload=amount)
        self.session_upload_bytes += amount
        self.lifetime_upload_bytes += amount

    def _upload_reconnect_ready(self, path: str) -> None:
        for item in self.upload_items:
            if str(item.path) == path and item.status == "uploading":
                self._set_upload_status(item, "waiting", self._t("等待重连"))
                break

    def _upload_item_done(self, path: str, success: bool, message: str) -> None:
        for item in self.upload_items:
            if str(item.path) == path and item.status in {"uploading", "paused"}:
                self._set_upload_status(item, "completed" if success else "failed", message=message)
                self.upload_ok += int(success)
                self.upload_failed += int(not success)
                break
        self._log(f"{'完成' if success else '失败'}：{Path(path).name} · {message}")
        destination = "/" + next((item.target for item in self.upload_items if str(item.path) == path), "")
        self.transfer_history_store.add(
            "upload", path, destination, success, message,
            self._transfer_started.pop("upload:" + path, time.time()),
        )
        self._refresh_transfer_history()

    def _upload_cancelled(self, path: str) -> None:
        shutting_down = bool(getattr(self, "_shutting_down", False))
        for item in self.upload_items:
            if str(item.path) == path and item.status in {"uploading", "paused"}:
                self._set_upload_status(item, "waiting" if shutting_down else "cancelled")
                if not shutting_down:
                    self.upload_cancelled += 1
                break
        message = "程序关闭，断点和队列已保留" if shutting_down else "已取消"
        self._log(f"{'已中断' if shutting_down else '已取消'}：{Path(path).name}")
        destination = "/" + next((item.target for item in self.upload_items if str(item.path) == path), "")
        history_key = "upload:" + path
        if not shutting_down or history_key in self._transfer_started:
            self.transfer_history_store.add(
                "upload", path, destination, False, message,
                self._transfer_started.pop(history_key, time.time()),
            )
        self._refresh_transfer_history()

    def _upload_thread_finished(self, worker: UploadThread) -> None:
        if self.task is worker:
            self.task = None
        self.current_upload_speed = 0.0
        self._refresh_tray_status()
        self.pause_upload_button.setEnabled(False)
        self.resume_upload_button.setEnabled(False)
        self.cancel_upload_button.setEnabled(False)
        self._update_upload_enabled()
        self._update_download_enabled()
        pending_upload = any(item.status == "waiting" for item in self.upload_items)
        if worker.stopped_for_reconnect and pending_upload and self.upload_session_service:
            try:
                self.upload_session_service.reconnect()
            except Exception as exc:
                self._log(self._tf("上传连接重建失败，将继续尝试：{error}", error=exc))
            else:
                self._log(self._t("上传连接已重建，将继续剩余队列"))
        if worker.stopped_for_reconnect:
            self.upload_health_monitor.reset_after_reconnect()
        QTimer.singleShot(0, self._start_next_upload)

    def pause_upload(self) -> None:
        if not isinstance(self.task, UploadThread) or not self.task.isRunning():
            return
        self.task.pause()
        item = next((item for item in self.upload_items if item.status == "uploading"), None)
        if item:
            self._set_upload_status(item, "paused")
        self.pause_upload_button.setEnabled(False)
        self.resume_upload_button.setEnabled(True)

    def resume_upload(self) -> None:
        if not isinstance(self.task, UploadThread) or not self.task.isRunning():
            return
        self.task.resume()
        item = next((item for item in self.upload_items if item.status == "paused"), None)
        if item:
            self._set_upload_status(item, "uploading")
        self.pause_upload_button.setEnabled(True)
        self.resume_upload_button.setEnabled(False)

    def cancel_upload(self) -> None:
        if isinstance(self.task, UploadThread) and self.task.isRunning():
            self.task.cancel()
            self.pause_upload_button.setEnabled(False)
            self.resume_upload_button.setEnabled(False)
            self.cancel_upload_button.setEnabled(False)

    def _finish_upload_queue(self) -> None:
        if self.upload_session_service is None:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.upload_stats.setText(self._t("速度：0 B/s · 剩余：00:00"))
        if self.upload_ok and self.upload_session_repo and self.upload_session_account_id:
            self._mark_repository_dirty(self.upload_session_account_id, self.upload_session_repo)
            self.repo_heading.setText(self._t("上传完成，目录将在空闲时自动刷新"))
        self._log(f"上传队列结束：{self.upload_ok} 个成功，{self.upload_failed} 个失败，{self.upload_cancelled} 个取消")
        self.upload_session_service = None
        self.upload_session_repo = None
        self.upload_session_account_id = None
        self._update_upload_enabled()

    def start_download(self) -> None:
        specs = [
            spec for spec in self.download_specs
            if self.download_states.get(str(spec.local_path), "waiting") in {"waiting", "failed", "stopped"}
        ]
        self._start_download_specs(specs)

    def _auto_start_download(self) -> None:
        if self.task and self.task.isRunning():
            return
        specs = [
            spec for spec in self.download_specs
            if self.download_states.get(str(spec.local_path), "waiting") == "waiting"
        ]
        self._start_download_specs(specs)

    def _start_download_specs(self, specs: list[DownloadSpec]) -> None:
        if not specs or (self.task and self.task.isRunning()):
            return
        specs = [
            replace(
                spec,
                token=str(getattr(self.account_services.get(spec.account_id), "token", "") or spec.token),
            )
            for spec in specs
        ]
        aria2_path = Path(__file__).resolve().parent.parent / "runtime" / "tools" / "aria2-next.exe"
        try:
            tuning = self._aria2_tuning()
        except ValueError as exc:
            QMessageBox.warning(self, self._t("aria2-next 配置无效"), str(exc))
            self._navigate(2)
            return
        runner = Aria2DownloadRunner(
            aria2_path, "", tuning,
            download_limit_supplier=lambda: self.transfer_policy.limits()[1],
        )
        self.download_runner = runner
        self.current_download_speed = 0.0
        self._download_stat_last_completed = 0
        self._download_stat_initialized = False
        self._download_verify_progress = {str(spec.local_path): 0 for spec in specs}
        self._download_verify_totals = {str(spec.local_path): max(0, spec.size) for spec in specs}
        self.active_download_specs = list(specs)
        for spec in specs:
            self._history_recorded.discard(str(spec.local_path))
            self._transfer_started["download:" + str(spec.local_path)] = time.time()
        self.download_progress.setValue(0)
        self.download_progress.setFormat("0%-下载")
        self.download_stats.setText(self._t("速度：0 B/s · 剩余：--"))
        active_paths = {str(spec.local_path) for spec in specs}
        for row, spec in enumerate(self.download_specs):
            if str(spec.local_path) in active_paths:
                self.download_states[str(spec.local_path)] = "waiting"
                self.download_table.item(row, 2).setText(self._t("准备下载"))
        worker = DownloadThread(runner, specs, self)
        worker.progress_info.connect(self._download_progress_info)
        worker.item_update.connect(self._download_item_update)
        worker.completed.connect(self._download_completed)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(worker.deleteLater)
        self.task = worker
        self._refresh_tray_status()
        self.pause_download_button.setEnabled(True)
        self.resume_download_button.setEnabled(False)
        self.stop_download_button.setEnabled(True)
        self._update_upload_enabled()
        self._update_download_enabled()
        self._log(f"开始下载 {len(specs)} 个文件（aria2-next）")
        worker.start()

    def pause_download(self) -> None:
        if not self.download_runner:
            return
        try:
            changed = self.download_runner.pause()
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法暂停"), str(exc))
            return
        if changed:
            self.current_download_speed = 0.0
            self.pause_download_button.setEnabled(False)
            self.resume_download_button.setEnabled(True)
            self.download_stats.setText(self._t("已暂停 · 已下载内容会保留"))
            self._log("下载已暂停")

    def resume_download(self) -> None:
        if not self.download_runner:
            return
        try:
            changed = self.download_runner.resume()
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法恢复"), str(exc))
            return
        if changed:
            self.pause_download_button.setEnabled(True)
            self.resume_download_button.setEnabled(False)
            self._log("下载已恢复")

    def stop_download(self) -> None:
        if not self.download_runner:
            return
        try:
            changed = self.download_runner.stop()
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法停止"), str(exc))
            return
        if changed:
            self.current_download_speed = 0.0
            self.pause_download_button.setEnabled(False)
            self.resume_download_button.setEnabled(False)
            self.stop_download_button.setEnabled(False)
            self.download_stats.setText(self._t("正在停止… · 已下载内容不会删除"))
            self._log("正在停止下载，已下载内容和断点文件将保留")

    def _download_progress_info(self, completed: int, total: int, speed: float, eta: int) -> None:
        self.current_download_speed = max(0.0, speed)
        if not self._download_stat_initialized:
            downloaded = 0
            self._download_stat_last_completed = max(0, completed)
            self._download_stat_initialized = True
        else:
            downloaded = max(0, completed - self._download_stat_last_completed)
            self._download_stat_last_completed = max(self._download_stat_last_completed, completed)
        if downloaded:
            self.transfer_statistics.add_bytes(time.time(), download=downloaded)
            self.session_download_bytes += downloaded
            self.lifetime_download_bytes += downloaded
        percent = running_download_percent(completed, total)
        self.download_progress.setValue(percent)
        self.download_progress.setFormat(f"{percent}%-下载")
        self.download_stats.setText(self._tf("速度：{speed} · 剩余：{eta}", speed=format_speed(speed), eta=format_eta(eta)))

    def _download_item_update(self, local_path: str, state: str, completed: int, total: int, message: str) -> None:
        for row, spec in enumerate(self.download_specs):
            if local_path_identity(spec.local_path) != local_path_identity(local_path):
                continue
            canonical_path = str(spec.local_path)
            self.download_states[canonical_path] = state
            if local_path != canonical_path:
                self.download_states.pop(local_path, None)
            status = self.download_table.item(row, 2)
            percent = min(100, max(0, int(completed * 100 / total))) if total > 0 else 0
            labels = {
                "waiting": self._t("等待下载"),
                "downloading": f"{percent}%-{self._t('下载')}",
                "paused": self._tf("{percent}% · 已暂停", percent=percent),
                "verifying": f"{percent}%-{self._t('校验')}",
                "completed": f"100%-{self._t('校验')}",
                "failed": self._tf("失败 · {message}", message=self._t(message)),
                "stopped": self._tf("已停止 · {percent}%（可继续）", percent=percent),
            }
            status.setText(labels.get(state, message))
            status.setToolTip(message)
            if state == "verifying":
                self._download_verify_progress[canonical_path] = min(max(0, completed), max(0, total))
                self._download_verify_totals[canonical_path] = max(0, total)
                verify_total = sum(self._download_verify_totals.values())
                verified = sum(self._download_verify_progress.get(str(item.local_path), 0) for item in self.active_download_specs)
                verify_percent = int(verified * 100 / max(1, verify_total))
                self.download_progress.setValue(min(100, verify_percent))
                self.download_progress.setFormat(f"{min(100, verify_percent)}%-{self._t('校验')}")
            if state in {"completed", "failed", "stopped"}:
                color = "#0f7b0f" if state == "completed" else ("#9a6700" if state == "stopped" else "#c42b1c")
                status.setForeground(QColor(color))
            else:
                status.setForeground(self.download_table.palette().color(QPalette.ColorRole.Text))
            self._save_transfer_queues()
            if state == "completed":
                job_id = self.backup_sync_job_paths.pop(canonical_path, "")
                job = next((candidate for candidate in self.backup_jobs if candidate.job_id == job_id), None)
                local_file = spec.local_path
                if job and local_file.is_file():
                    try:
                        stat = local_file.stat()
                        relative = local_file.resolve().relative_to(Path(job.local_path).resolve()).as_posix()
                        self.backup_store.mark_uploaded(
                            job.job_id,
                            LocalBackupFile(local_file.resolve(), relative, stat.st_size, stat.st_mtime_ns),
                            spec.remote_path,
                        )
                    except (OSError, ValueError):
                        pass
                if self.potplayer_install_archive and local_file.resolve() == self.potplayer_install_archive.resolve():
                    QTimer.singleShot(0, lambda media=local_file: self._start_potplayer_extraction(media))
                ffmpeg_archive = getattr(self, "ffmpeg_install_archive", None)
                if ffmpeg_archive and local_file.resolve() == ffmpeg_archive.resolve():
                    QTimer.singleShot(0, lambda media=local_file: self._start_ffmpeg_extraction(media))
            if state in {"completed", "failed", "stopped"} and canonical_path not in self._history_recorded:
                self._history_recorded.add(canonical_path)
                self.transfer_history_store.add(
                    "download", spec.remote_path, canonical_path, state == "completed", message,
                    self._transfer_started.pop("download:" + canonical_path, time.time()),
                )
                self._refresh_transfer_history()
            break

    def _download_completed(self, ok: int, failed: int) -> None:
        stopped = bool(self.download_runner and self.download_runner.stopped)
        self.task = None
        self.download_runner = None
        self.current_download_speed = 0.0
        self._refresh_tray_status()
        self.active_download_specs.clear()
        self.pause_download_button.setEnabled(False)
        self.resume_download_button.setEnabled(False)
        self.stop_download_button.setEnabled(False)
        if stopped:
            self.download_stats.setText(self._t("已停止 · 已下载内容和断点已保留"))
        else:
            self.download_progress.setValue(100)
            self.download_progress.setFormat(f"100%-{self._t('校验')}")
            self.download_stats.setText(self._t("速度：0 B/s · 剩余：00:00"))
        self._update_upload_enabled()
        self._update_download_enabled()
        if stopped:
            self._log("下载已停止，可点击“开始下载”从断点继续")
        elif failed:
            self._log(f"下载结束：{ok} 个成功，{failed} 个失败")
            QMessageBox.warning(self, self._t("下载完成"), self._tf("{ok} 个文件成功，{failed} 个失败。", ok=ok, failed=failed))
        else:
            self._log(f"下载结束：{ok} 个成功，{failed} 个失败")
            QMessageBox.information(self, self._t("下载完成"), self._tf("{ok} 个文件已下载并通过校验。", ok=ok))
        if not stopped:
            QTimer.singleShot(0, self._auto_start_download)

    def _download_failed(self, error: str) -> None:
        for spec in self.active_download_specs:
            path = str(spec.local_path)
            if self.download_states.get(path) not in {"completed", "stopped"}:
                self.download_states[path] = "failed"
            if path not in self._history_recorded:
                self._history_recorded.add(path)
                self.transfer_history_store.add(
                    "download", spec.remote_path, path, False, error,
                    self._transfer_started.pop("download:" + path, time.time()),
                )
        self._refresh_transfer_history()
        self._save_transfer_queues()
        self.task = None
        self.download_runner = None
        self.current_download_speed = 0.0
        self._refresh_tray_status()
        self.active_download_specs.clear()
        self.pause_download_button.setEnabled(False)
        self.resume_download_button.setEnabled(False)
        self.stop_download_button.setEnabled(False)
        self._update_upload_enabled()
        self._update_download_enabled()
        self._log(f"下载失败：{error}")
        QMessageBox.warning(self, self._t("下载失败"), error)
