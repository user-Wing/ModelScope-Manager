"""备份任务行为。"""

from __future__ import annotations

import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QTableWidgetItem
from datetime import datetime
from pathlib import Path
from .app_helpers import format_eta, format_speed
from .app_workers import BackupThread
from .backup import BackupJob
from .download_service import DownloadSpec
from .service import RemoteEntry, normalize_remote_path


class BackupsMixin:
    """备份任务行为。"""

    def _render_backup_repository_options(self) -> None:
        if not hasattr(self, "backup_repo_combo"):
            return
        selected = self.backup_repo_combo.currentData()
        account_id = str(self.backup_account_combo.currentData() or "")
        self.backup_repo_combo.clear()
        for repo in self.account_repositories.get(account_id, []):
            self.backup_repo_combo.addItem(
                f"{repo.repo_id} · {self._t('数据集') if repo.repo_type == 'dataset' else self._t('模型')}",
                (repo.repo_type, repo.repo_id),
            )
        index = self.backup_repo_combo.findData(selected)
        if index >= 0:
            self.backup_repo_combo.setCurrentIndex(index)

    def _render_backup_jobs(self) -> None:
        if not hasattr(self, "backup_table"):
            return
        account_labels = {account.account_id: account.label for account in self.accounts}
        self.backup_table.setRowCount(0)
        for job in self.backup_jobs:
            row = self.backup_table.rowCount()
            self.backup_table.insertRow(row)
            name_item = QTableWidgetItem(job.name)
            name_item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            self.backup_table.setItem(row, 0, name_item)
            self.backup_table.setItem(row, 1, QTableWidgetItem(job.local_path))
            self.backup_table.setItem(row, 2, QTableWidgetItem(account_labels.get(job.account_id, job.account_id)))
            self.backup_table.setItem(row, 3, QTableWidgetItem(job.repo_id))
            self.backup_table.setItem(row, 4, QTableWidgetItem(job.dest_dir or "/"))
            mode = "增量备份" if job.mode == "incremental" else "同路径覆盖"
            self.backup_table.setItem(row, 5, QTableWidgetItem(self._t(mode)))
            unit = "小时" if job.interval_unit == "hour" else "分钟"
            self.backup_table.setItem(row, 6, QTableWidgetItem(f"{job.interval_value:g} {self._t(unit)}"))
            if not job.enabled:
                status = self._t("已停用")
            elif job.last_scan:
                status = self._tf("上次扫描：{time}", time=datetime.fromtimestamp(job.last_scan).strftime("%Y-%m-%d %H:%M"))
            else:
                status = self._t("等待首次扫描")
            self.backup_table.setItem(row, 7, QTableWidgetItem(status))

    def _selected_backup_job(self) -> BackupJob | None:
        row = self.backup_table.currentRow() if hasattr(self, "backup_table") else -1
        item = self.backup_table.item(row, 0) if row >= 0 else None
        job_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        return next((job for job in self.backup_jobs if job.job_id == job_id), None)

    def _backup_selected(self) -> None:
        job = self._selected_backup_job()
        if not job:
            return
        self.backup_name_edit.setText(job.name)
        self.backup_local_edit.setText(job.local_path)
        account_index = self.backup_account_combo.findData(job.account_id)
        if account_index >= 0:
            self.backup_account_combo.setCurrentIndex(account_index)
        self._render_backup_repository_options()
        repo_index = self.backup_repo_combo.findData((job.repo_type, job.repo_id))
        if repo_index >= 0:
            self.backup_repo_combo.setCurrentIndex(repo_index)
        self.backup_dest_edit.setText(job.dest_dir)
        mode_index = self.backup_mode_combo.findData(job.mode)
        self.backup_mode_combo.setCurrentIndex(max(0, mode_index))
        self.backup_interval_value.setValue(job.interval_value)
        unit_index = self.backup_interval_unit.findData(job.interval_unit)
        self.backup_interval_unit.setCurrentIndex(max(0, unit_index))
        self.backup_download_limit.setValue(job.download_limit_mb)
        self.backup_enabled.setChecked(job.enabled)
        self.backup_status_label.setText(self._t("备份任务已加载"))

    def _new_backup_job(self) -> None:
        self.backup_table.clearSelection()
        self.backup_name_edit.clear()
        self.backup_local_edit.clear()
        self.backup_dest_edit.clear()
        self.backup_mode_combo.setCurrentIndex(0)
        self.backup_interval_value.setValue(30)
        self.backup_interval_unit.setCurrentIndex(0)
        self.backup_download_limit.setValue(10)
        self.backup_enabled.setChecked(True)
        self.backup_status_label.setText(self._t("填写后保存新的备份任务"))

    def _browse_backup_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, self._t("选择备份文件夹"), self.backup_local_edit.text().strip()
        )
        if selected:
            self.backup_local_edit.setText(selected)
            if not self.backup_name_edit.text().strip():
                self.backup_name_edit.setText(Path(selected).name)

    def _save_backup_job(self) -> None:
        local_path = Path(self.backup_local_edit.text().strip())
        repo_data = self.backup_repo_combo.currentData()
        if not local_path.is_dir():
            QMessageBox.warning(self, self._t("备份配置无效"), self._t("请选择存在的本地文件夹。"))
            return
        if not (isinstance(repo_data, tuple) and len(repo_data) == 2):
            QMessageBox.warning(self, self._t("备份配置无效"), self._t("请选择可写入的目标仓库。"))
            return
        existing = self._selected_backup_job()
        job = BackupJob(
            existing.job_id if existing else "",
            self.backup_name_edit.text().strip() or local_path.name,
            str(self.backup_account_combo.currentData() or ""),
            str(local_path.resolve()),
            str(repo_data[0]), str(repo_data[1]),
            self.backup_dest_edit.text().replace("\\", "/").strip("/"),
            str(self.backup_mode_combo.currentData()),
            self.backup_interval_value.value(),
            str(self.backup_interval_unit.currentData()),
            self.backup_download_limit.value(),
            self.backup_enabled.isChecked(),
        )
        self.backup_store.save_job(job)
        self.backup_jobs = self.backup_store.list_jobs()
        self._render_backup_jobs()
        self.backup_status_label.setText(self._t("备份任务已保存"))

    def _remove_backup_job(self) -> None:
        job = self._selected_backup_job()
        if not job:
            return
        answer = QMessageBox.question(
            self, self._t("移除备份任务"),
            self._tf("确定移除备份任务 {name}？远端和本地文件不会被删除。", name=job.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.backup_store.remove_job(job.job_id)
        self.backup_jobs = self.backup_store.list_jobs()
        self._render_backup_jobs()
        self._new_backup_job()

    def _scan_selected_backup(self) -> None:
        job = self._selected_backup_job()
        if job:
            self._start_backup_job(job)

    def _check_backup_schedule(self) -> None:
        if self.backup_thread and self.backup_thread.isRunning():
            return
        if self.task and self.task.isRunning():
            return
        for job in self.backup_store.due_jobs():
            if job.account_id in self.account_services:
                self._start_backup_job(job, automatic=True)
                break

    def _start_backup_job(self, job: BackupJob, automatic: bool = False) -> None:
        if self.backup_thread and self.backup_thread.isRunning():
            if not automatic:
                QMessageBox.information(self, self._t("备份进行中"), self._t("请等待当前备份任务完成。"))
            return
        if self.task and self.task.isRunning():
            if not automatic:
                QMessageBox.information(self, self._t("请稍候"), self._t("当前传输完成后再执行备份。"))
            return
        service = self.account_services.get(job.account_id)
        repo = next((candidate for candidate in self.account_repositories.get(job.account_id, [])
                     if candidate.repo_type == job.repo_type and candidate.repo_id == job.repo_id), None)
        if not service or not repo:
            if not automatic:
                QMessageBox.warning(self, self._t("无法开始备份"), self._t("目标账户或仓库当前不可用。"))
            return
        worker = BackupThread(self.backup_store, job, service, repo, self)
        worker.item_done.connect(self._backup_item_done)
        worker.progress_info.connect(self._backup_progress)
        worker.completed.connect(self._backup_completed)
        worker.finished.connect(lambda: self._backup_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.backup_thread = worker
        self.backup_automatic = automatic
        self.backup_scan_button.setEnabled(False)
        self.backup_status_label.setText(self._tf("正在扫描并备份：{name}", name=job.name))
        self._log(f"开始备份任务：{job.name}")
        worker.start()

    def _backup_item_done(self, relative_path: str, success: bool, message: str) -> None:
        self._log(f"备份 {'完成' if success else '失败'}：{relative_path} · {message}")

    def _backup_progress(self, completed: int, total: int, speed: float, eta: int) -> None:
        self.current_upload_speed = speed
        percent = int(completed * 100 / max(1, total))
        self.backup_status_label.setText(self._tf(
            "备份中：{percent}% · {speed} · 剩余 {eta}",
            percent=percent, speed=format_speed(speed), eta=format_eta(eta),
        ))

    def _backup_completed(self, job_id: str, uploaded: int, failed: int, skipped: int) -> None:
        self.current_upload_speed = 0.0
        self.backup_jobs = self.backup_store.list_jobs()
        job = next((candidate for candidate in self.backup_jobs if candidate.job_id == job_id), None)
        if job and uploaded:
            repo = next((candidate for candidate in self.account_repositories.get(job.account_id, [])
                         if candidate.repo_type == job.repo_type and candidate.repo_id == job.repo_id), None)
            if repo:
                self._mark_repository_dirty(job.account_id, repo)
        self._render_backup_jobs()
        self.backup_status_label.setText(self._tf(
            "备份完成：{uploaded} 个上传，{failed} 个失败",
            uploaded=uploaded, failed=failed,
        ))

    def _backup_finished(self, worker: BackupThread) -> None:
        if self.backup_thread is worker:
            self.backup_thread = None
        self.backup_automatic = False
        self.backup_scan_button.setEnabled(True)

    def _sync_selected_backup(self) -> None:
        job = self._selected_backup_job()
        if not job:
            return
        service = self.account_services.get(job.account_id)
        repo = next((candidate for candidate in self.account_repositories.get(job.account_id, [])
                     if candidate.repo_type == job.repo_type and candidate.repo_id == job.repo_id), None)
        if not service or not repo:
            QMessageBox.warning(self, self._t("无法同步"), self._t("目标账户或仓库当前不可用。"))
            return
        if not Path(job.local_path).is_dir():
            QMessageBox.warning(self, self._t("无法同步"), self._t("本地备份文件夹不存在。"))
            return
        self._run_task(
            lambda: (job, service, repo, service.list_entries(repo)),
            self._backup_cloud_entries_loaded,
            "正在读取云端备份…",
        )

    def _backup_cloud_entries_loaded(self, result) -> None:
        job, service, repo, entries = result
        base = job.dest_dir.strip("/")
        candidates: list[tuple[RemoteEntry, str]] = []
        if job.mode == "incremental":
            # New incremental tasks can restore the complete current state even though
            # unchanged files live in older timestamp directories.  The local backup
            # index maps every current relative path to its latest successful upload.
            remote_paths = self.backup_store.current_remote_paths(job.job_id)
            by_remote_path = {entry.path.strip("/"): entry for entry in entries if not entry.is_dir}
            if remote_paths:
                for relative, remote_path in remote_paths.items():
                    entry = by_remote_path.get(remote_path.strip("/"))
                    if entry is not None and entry.size <= int(job.download_limit_mb * 1024**2):
                        candidates.append((entry, relative))
            else:
                # Compatibility fallback for legacy/imported tasks whose local index
                # predates the current-path mapping: retain the original latest-delta behavior.
                timestamps: set[str] = set()
                for entry in entries:
                    path = entry.path.strip("/")
                    relative = path[len(base) + 1:] if base and path.startswith(base + "/") else (path if not base else "")
                    first = relative.split("/", 1)[0] if relative else ""
                    if re.fullmatch(r"\d{8}-\d{6}", first):
                        timestamps.add(first)
                if not timestamps:
                    QMessageBox.information(self, self._t("没有云端备份"), self._t("目标目录中没有时间戳备份。"))
                    return
                latest = max(timestamps)
                prefix = normalize_remote_path(base, latest)
                for entry in entries:
                    if entry.is_dir or entry.size > int(job.download_limit_mb * 1024**2):
                        continue
                    path = entry.path.strip("/")
                    if not path.startswith(prefix + "/"):
                        continue
                    relative = path[len(prefix) + 1:]
                    if relative:
                        candidates.append((entry, relative))
        else:
            prefix = base
            for entry in entries:
                if entry.is_dir or entry.size > int(job.download_limit_mb * 1024**2):
                    continue
                path = entry.path.strip("/")
                if prefix:
                    if not path.startswith(prefix + "/"):
                        continue
                    relative = path[len(prefix) + 1:]
                else:
                    relative = path
                if relative:
                    candidates.append((entry, relative))

        root = Path(job.local_path).resolve()
        specs: list[DownloadSpec] = []
        for entry, relative in candidates:
            local_path = root.joinpath(*[part for part in relative.split("/") if part]).resolve()
            if not local_path.is_relative_to(root):
                continue
            specs.append(DownloadSpec(
                entry.path,
                local_path,
                service.get_download_url(repo, entry.path),
                entry.size,
                entry.sha256,
                str(service.token or ""),
            ))
        if not specs:
            QMessageBox.information(
                self, self._t("没有可同步文件"),
                self._tf("没有小于或等于 {limit:g} MB 的云端文件。", limit=job.download_limit_mb),
            )
            return
        # This callback runs just before the repository-listing worker emits
        # finished. Give that worker time to release the shared transfer slot.
        added = self._enqueue_download_specs(specs, auto_start_delay_ms=150)
        if added:
            for spec in specs:
                if self.download_states.get(str(spec.local_path)) == "waiting":
                    self.backup_sync_job_paths[str(spec.local_path)] = job.job_id
            self.backup_store.mark_sync(job.job_id)
            self.backup_jobs = self.backup_store.list_jobs()
            self._render_backup_jobs()
            self.backup_status_label.setText(self._tf("已添加 {count} 个云端文件到下载队列", count=added))
