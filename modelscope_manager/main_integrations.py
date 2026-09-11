"""下载参数、播放器、WebDAV 与索引集成。"""

from __future__ import annotations

import json
import socket
from PySide6.QtCore import QProcess, QTime, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDoubleSpinBox, QFileDialog, QLabel, QMessageBox, QTableWidgetItem, QTimeEdit
from pathlib import Path
from .app_helpers import PUBLIC_ACCOUNT_ID, find_available_port, format_speed
from .app_workers import FolderIndexThread, PotPlayerInstallThread
from .download_service import Aria2Tuning, DownloadSpec
from .player_installer import POTPLAYER_ARCHIVE_SHA256, POTPLAYER_ARCHIVE_SIZE, POTPLAYER_REMOTE_PATH, POTPLAYER_REPOSITORY, find_potplayer
from .security import protect
from .service import ModelScopeService, MultiAccountService, RemoteEntry, Repository, configure_upload_limit_supplier
from .storage import PLAYER_DOWNLOAD_DIR, POTPLAYER_DIR
from .transfer_policy import SpeedRule, TransferPolicy
from .webdav_server import ModelScopeWebDAV


class IntegrationsMixin:
    """下载参数、播放器、WebDAV 与索引集成。"""

    def _save_aria2_settings(self) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("aria2/small_limit_mb", self.aria_small_limit.value())
        self.settings.setValue("aria2/small_segments", self.aria_small_segments.value())
        self.settings.setValue("aria2/medium_segments", self.aria_medium_segments.value())
        self.settings.setValue("aria2/large_limit_mb", self.aria_large_limit.value())
        self.settings.setValue("aria2/large_segments", self.aria_large_segments.value())

    def reset_aria2_settings(self) -> None:
        self.aria_small_limit.setValue(1.0)
        self.aria_small_segments.setValue(1)
        self.aria_medium_segments.setValue(32)
        self.aria_large_limit.setValue(100.0)
        self.aria_large_segments.setValue(64)
        self._save_aria2_settings()
        self._log("aria2-next 配置已重置为默认值")

    def _aria2_tuning(self) -> Aria2Tuning:
        return Aria2Tuning(
            self.aria_small_limit.value(),
            self.aria_small_segments.value(),
            self.aria_medium_segments.value(),
            self.aria_large_limit.value(),
            self.aria_large_segments.value(),
        ).validated()

    def _add_speed_rule(
        self,
        start: str = "22:00",
        end: str = "08:00",
        upload_mib: float = 0.0,
        download_mib: float = 0.0,
    ) -> None:
        row = self.speed_rule_table.rowCount()
        self.speed_rule_table.insertRow(row)
        start_edit = QTimeEdit(QTime.fromString(start, "HH:mm"))
        end_edit = QTimeEdit(QTime.fromString(end, "HH:mm"))
        for editor in (start_edit, end_edit):
            editor.setDisplayFormat("HH:mm")
            editor.timeChanged.connect(self._save_transfer_policy)
        upload_edit = QDoubleSpinBox()
        download_edit = QDoubleSpinBox()
        for editor, value in ((upload_edit, upload_mib), (download_edit, download_mib)):
            editor.setRange(0, 102400)
            editor.setDecimals(2)
            editor.setValue(value)
            editor.valueChanged.connect(self._save_transfer_policy)
        self.speed_rule_table.setCellWidget(row, 0, start_edit)
        self.speed_rule_table.setCellWidget(row, 1, end_edit)
        self.speed_rule_table.setCellWidget(row, 2, upload_edit)
        self.speed_rule_table.setCellWidget(row, 3, download_edit)
        self._save_transfer_policy()

    def _remove_speed_rule(self) -> None:
        row = self.speed_rule_table.currentRow()
        if row < 0:
            return
        self.speed_rule_table.removeRow(row)
        self._save_transfer_policy()

    def _transfer_policy_from_controls(self) -> TransferPolicy:
        rules: list[SpeedRule] = []
        for row in range(self.speed_rule_table.rowCount()):
            start = self.speed_rule_table.cellWidget(row, 0)
            end = self.speed_rule_table.cellWidget(row, 1)
            upload = self.speed_rule_table.cellWidget(row, 2)
            download = self.speed_rule_table.cellWidget(row, 3)
            rules.append(SpeedRule(
                start.time().toString("HH:mm"), end.time().toString("HH:mm"),
                upload.value(), download.value(),
            ))
        return TransferPolicy(
            self.speed_limit_enabled.isChecked(),
            self.base_upload_limit.value(), self.base_download_limit.value(), rules,
        )

    def _save_transfer_policy(self, *_args) -> None:
        if self._restoring_settings:
            return
        try:
            self.transfer_policy = self._transfer_policy_from_controls()
        except ValueError as exc:
            self.speed_limit_status.setText(str(exc))
            return
        self.settings.setValue(
            "transfer/speed_policy",
            json.dumps(self.transfer_policy.to_dict(), ensure_ascii=False),
        )
        configure_upload_limit_supplier(lambda: self.transfer_policy.limits()[0])
        self._refresh_transfer_limit_status()

    def _reset_transfer_policy(self) -> None:
        restoring = self._restoring_settings
        self._restoring_settings = True
        self.speed_limit_enabled.setChecked(False)
        self.base_upload_limit.setValue(0)
        self.base_download_limit.setValue(0)
        self.speed_rule_table.setRowCount(0)
        self._restoring_settings = restoring
        self._save_transfer_policy()

    def _refresh_transfer_limit_status(self) -> None:
        if not hasattr(self, "speed_limit_status"):
            return
        upload, download = self.transfer_policy.limits()
        upload_text = format_speed(upload) if upload else self._t("不限速")
        download_text = format_speed(download) if download else self._t("不限速")
        self.speed_limit_status.setText(self._tf(
            "当前：上传 {upload} · 下载 {download}", upload=upload_text, download=download_text,
        ))

    def _render_players(self) -> None:
        restoring = self._restoring_settings
        self._restoring_settings = True
        self.player_table.setRowCount(0)
        for player in self.external_players:
            row = self.player_table.rowCount()
            self.player_table.insertRow(row)
            self.player_table.setItem(row, 0, QTableWidgetItem(player.get("name", f"播放器 {row + 1}")))
            path_item = QTableWidgetItem(player.get("path", ""))
            path_item.setToolTip(player.get("path", ""))
            self.player_table.setItem(row, 1, path_item)
        self._restoring_settings = restoring

    def _players_edited(self) -> None:
        if self._restoring_settings:
            return
        players = []
        for row in range(self.player_table.rowCount()):
            name = self.player_table.item(row, 0).text().strip() or f"播放器 {row + 1}"
            path = self.player_table.item(row, 1).text().strip()
            players.append({"name": name, "path": path})
        self.external_players = players or [{"name": "播放器 1", "path": ""}]
        self._save_players()

    def _save_players(self) -> None:
        self.settings.setValue("external_players", json.dumps(self.external_players, ensure_ascii=False))

    @staticmethod
    def _builtin_player_available() -> bool:
        return find_potplayer(POTPLAYER_DIR) is not None

    def _refresh_builtin_player_status(self) -> None:
        if not hasattr(self, "builtin_player_status"):
            return
        available = self._builtin_player_available()
        self.builtin_player_status.setText(self._t(
            "PotPlayer：已安装" if available else "PotPlayer：尚未安装"
        ))
        self.potplayer_install_button.setText(self._t(
            "重新安装 PotPlayer" if available else "下载并安装 PotPlayer"
        ))
        self.potplayer_folder_button.setEnabled(available)

    def _builtin_player_setting_changed(self, checked: bool) -> None:
        if not self._restoring_settings:
            self.settings.setValue("builtin_player_enabled", checked)
        self._refresh_builtin_player_status()

    def _open_local_media(self, path: Path) -> None:
        path = path.resolve()
        if self.builtin_player_enabled.isChecked() and self._launch_builtin_target(str(path)):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _launch_builtin_target(self, target: str) -> bool:
        executable = find_potplayer(POTPLAYER_DIR)
        if executable is None:
            return False
        started = QProcess.startDetached(
            str(executable), [target], str(executable.parent),
        )
        return started[0] if isinstance(started, tuple) else bool(started)

    def open_builtin_remote(self, entry: RemoteEntry, service: ModelScopeService, repo: Repository) -> None:
        try:
            direct_url = service.get_download_url(repo, entry.path)
            playback_url = (
                self.media_proxy.stream_url(direct_url, service.token)
                if service.token else direct_url
            )
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法打开媒体"), str(exc))
            return
        if not self._launch_builtin_target(playback_url):
            QMessageBox.warning(self, self._t("无法打开媒体"), self._t("PotPlayer 尚未安装或启动失败。"))
            return
        self._log(f"PotPlayer 在线打开：{entry.path}")

    def install_potplayer_from_modelscope(self) -> None:
        if self.potplayer_install_thread and self.potplayer_install_thread.isRunning():
            QMessageBox.information(self, self._t("正在安装 PotPlayer"), self._t("请等待当前解压安装完成。"))
            return
        if self.task and self.task.isRunning():
            QMessageBox.information(
                self, self._t("传输正在进行"),
                self._t("PotPlayer 将加入下载队列，并在当前下载完成后自动开始。"),
            )
        PLAYER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        archive = PLAYER_DOWNLOAD_DIR / "PotPlayer.7z"
        service = ModelScopeService("", require_token=False)
        repo = Repository(POTPLAYER_REPOSITORY, "dataset", "public")
        spec = DownloadSpec(
            POTPLAYER_REMOTE_PATH,
            archive,
            service.get_download_url(repo, POTPLAYER_REMOTE_PATH),
            POTPLAYER_ARCHIVE_SIZE,
            POTPLAYER_ARCHIVE_SHA256,
        )
        self.potplayer_install_archive = archive
        self.settings.setValue("player/potplayer_install_pending", True)
        self.builtin_player_status.setText(self._t("PotPlayer：等待下载"))
        self._enqueue_download_specs([spec])

    def _start_potplayer_extraction(self, archive: Path) -> None:
        if self.potplayer_install_thread and self.potplayer_install_thread.isRunning():
            return
        self.builtin_player_status.setText(self._t("PotPlayer：正在校验并解压"))
        self.potplayer_install_button.setEnabled(False)
        worker = PotPlayerInstallThread(archive, self)
        worker.completed.connect(self._potplayer_installed)
        worker.failed.connect(self._potplayer_install_failed)
        worker.finished.connect(self._potplayer_install_finished)
        worker.finished.connect(worker.deleteLater)
        self.potplayer_install_thread = worker
        worker.start()

    def _potplayer_installed(self, executable: str) -> None:
        self.settings.remove("player/potplayer_install_pending")
        self.settings.setValue("player/potplayer_executable", executable)
        self.potplayer_install_archive = None
        self.builtin_player_status.setText(self._t("PotPlayer：已安装"))
        self._log(f"PotPlayer 安装完成：{executable}")
        QMessageBox.information(self, self._t("PotPlayer 安装完成"), self._t("播放器已经可以作为默认内置播放器使用。"))

    def _potplayer_install_failed(self, error: str) -> None:
        self.builtin_player_status.setText(self._t("PotPlayer：安装失败"))
        self._log(f"PotPlayer 安装失败：{error}")
        QMessageBox.warning(self, self._t("PotPlayer 安装失败"), error)

    def _potplayer_install_finished(self) -> None:
        self.potplayer_install_thread = None
        self.potplayer_install_button.setEnabled(True)
        self._refresh_builtin_player_status()

    def open_potplayer_folder(self) -> None:
        executable = find_potplayer(POTPLAYER_DIR)
        if executable:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(executable.parent)))

    def add_external_player(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择 mpv、PotPlayer 或其他播放器", "", "播放器程序 (*.exe);;所有文件 (*)"
        )
        if not selected:
            return
        player = {"name": Path(selected).stem or f"播放器 {len(self.external_players) + 1}", "path": selected}
        if len(self.external_players) == 1 and not self.external_players[0].get("path"):
            self.external_players[0] = player
        else:
            self.external_players.append(player)
        self._render_players()
        self._save_players()

    def remove_external_player(self) -> None:
        row = self.player_table.currentRow()
        if row < 0:
            return
        self.external_players.pop(row)
        if not self.external_players:
            self.external_players = [{"name": "播放器 1", "path": ""}]
        self._render_players()
        self._save_players()

    @staticmethod
    def _local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"

    def _update_alist_url(self) -> None:
        host = str(self.alist_host_combo.currentData())
        shown_host = "127.0.0.1" if host == "127.0.0.1" else self._local_ip()
        self.alist_url_label.setText(self._tf(
            "WebDAV 地址：{url}", url=f"http://{shown_host}:{self.alist_port.value()}/dav/"
        ))

    def _save_alist_settings(self) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("alist/auto_start", self.alist_auto_start.isChecked())
        self.settings.setValue("alist/host", self.alist_host_combo.currentData())
        self.settings.setValue("alist/port", self.alist_port.value())
        self.settings.setValue("alist/username", self.alist_username.text().strip())
        try:
            self.settings.setValue("alist/password", protect(self.alist_password.text(), allow_machine_fallback=True))
        except Exception as exc:
            self._log(f"AList 密码未能安全保存：{exc}")

    def apply_alist_settings(self) -> None:
        self._save_alist_settings()
        if self.webdav:
            self.webdav.stop()
            self.webdav = None
        username = self.alist_username.text().strip()
        password = self.alist_password.text()
        if not username or not password:
            QMessageBox.warning(self, self._t("AList 配置无效"), self._t("WebDAV 用户名和密码不能为空。"))
            return
        try:
            requested_port = self.alist_port.value()
            selected_port = find_available_port(str(self.alist_host_combo.currentData()), requested_port)
            if selected_port != requested_port:
                self.alist_port.setValue(selected_port)
                self._show_top_notice(f"端口 {requested_port} 已被占用，已切换到 {selected_port}")
            gateway_service = None
            if self.account_services:
                gateway_service = MultiAccountService(self.account_services, self.account_repositories)
            self.webdav = ModelScopeWebDAV(
                lambda service=gateway_service: service,
                str(self.alist_host_combo.currentData()),
                selected_port,
                username,
                password,
                self.public_pool_store.repositories,
                self.folder_index,
            )
            self.webdav.start()
            with socket.create_connection(("127.0.0.1", selected_port), timeout=2):
                pass
        except Exception as exc:
            self.webdav = None
            self.alist_status.setText(self._t("启动失败"))
            self._refresh_tray_status()
            QMessageBox.warning(self, self._t("WebDAV 启动失败"), str(exc))
            return
        bind_host = str(self.alist_host_combo.currentData())
        self.alist_status.setText(self._tf(
            "运行中 · 正在监听 {host}:{port}", host=bind_host, port=self.alist_port.value()
        ))
        self._log(self._t("AList WebDAV 网关已启动"))
        self._refresh_tray_status()

    def _show_top_notice(self, message: str) -> None:
        notice = getattr(self, "_top_notice", None)
        if notice is None:
            notice = QLabel(self)
            notice.setObjectName("pathPill")
            notice.setStyleSheet("font-weight: 600;")
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._top_notice = notice
        notice.setText(message)
        notice.adjustSize()
        notice.setFixedWidth(max(300, notice.width() + 28))
        notice.move(max(8, (self.width() - notice.width()) // 2), 8)
        notice.show()
        notice.raise_()
        QTimer.singleShot(4200, notice.hide)

    def stop_alist(self) -> None:
        if self.webdav:
            self.webdav.stop()
            self.webdav = None
        self.alist_status.setText(self._t("未启动"))
        self._refresh_tray_status()

    def _start_folder_indexing(self, force: bool = False) -> None:
        if self.index_task and self.index_task.isRunning():
            if force:
                self._index_refresh_pending = True
            return
        jobs: list[tuple[ModelScopeService, Repository, bool, str]] = []
        job_keys: set[tuple[str, str, str, bool]] = set()
        for account_id, repos in self.account_repositories.items():
            token = self.session_tokens.get(account_id, "")
            if not token:
                continue
            private_service = ModelScopeService(token)
            current_service = self.account_services.get(account_id)
            if current_service:
                private_service.user = current_service.user
            for repo in repos:
                key = (account_id, repo.repo_type, repo.repo_id, False)
                if force or key in self.dirty_repositories:
                    jobs.append((private_service, repo, False, account_id))
                    job_keys.add(key)
        public_repos = self.public_pool_store.repositories()
        if force and public_repos:
            public_service = ModelScopeService("", require_token=False)
            for repo in public_repos:
                key = ("", repo.repo_type, repo.repo_id, True)
                if force or key in self.dirty_repositories:
                    jobs.append((public_service, repo, True, PUBLIC_ACCOUNT_ID))
                    job_keys.add(key)
        if not jobs:
            return
        self._index_refresh_pending = False
        self.index_inflight_keys = job_keys
        self.dirty_repositories.difference_update(job_keys)
        worker = FolderIndexThread(self.folder_index, self.account_store, jobs, self)
        worker.repository_indexed.connect(self._folder_indexed)
        worker.completed.connect(self._folder_index_completed)
        worker.finished.connect(lambda: self._folder_index_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.index_task = worker
        self.update_index_button.setEnabled(False)
        self._log(f"正在更新文件夹大小索引，共 {len(jobs)} 个仓库")
        worker.start()

    def _folder_index_completed(self, ok: int, failed: int) -> None:
        if failed:
            self.dirty_repositories.update(self.index_inflight_keys)
        self._log(f"文件夹大小索引已更新：{ok} 个成功，{failed} 个失败")

    def _folder_indexed(self, repo_id: str) -> None:
        if self.selected_repo and self.selected_repo.repo_id == repo_id:
            self._populate_remote_tree(
                self.remote_tree, self.remote_entries, self.selected_repo, self.selected_repo_public,
            )
            self._select_remote_directory(self.current_directory_path)
            self._render_remote_details()
        if self.search_repo and self.search_repo.repo_id == repo_id:
            self._render_public_search_results()

    def _folder_index_finished(self, worker: FolderIndexThread) -> None:
        if self.index_task is worker:
            self.index_task = None
        self.index_inflight_keys.clear()
        self.update_index_button.setEnabled(True)
        if self._index_refresh_pending:
            QTimer.singleShot(0, lambda: self._start_folder_indexing(True))

    def update_all_indexes(self) -> None:
        self._start_folder_indexing(True)

    def _mark_repository_dirty(self, account_id: str, repo: Repository, public: bool = False) -> None:
        self.dirty_repositories.add((account_id, repo.repo_type, repo.repo_id, public))
        self.index_idle_timer.start()

    def _run_idle_index_refresh(self) -> None:
        if not self.dirty_repositories:
            return
        if (self.task and self.task.isRunning()) or (self.backup_thread and self.backup_thread.isRunning()):
            self.index_idle_timer.start()
            return
        if self.page_stack.currentWidget() is self.resource_page and self.selected_repo and self.active_account_id:
            key = (self.active_account_id, self.selected_repo.repo_type, self.selected_repo.repo_id, False)
            if key in self.dirty_repositories:
                self.load_remote_files()
                return
        self._start_folder_indexing(False)

    def _background_index_tick(self) -> None:
        if self.isHidden() or not self.isActiveWindow():
            self._start_folder_indexing(False)

    def _prompt_for_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            self._t("需要访问令牌"),
            self._t("尚未设置并验证 ModelScope 访问令牌。是否转到设置？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._navigate(2)
            self.token_edit.setFocus()

    def change_download_path(self) -> None:
        current = self.download_path_edit.text().strip() or str(Path.home() / "Downloads")
        selected = QFileDialog.getExistingDirectory(self, "选择默认下载路径", current)
        if selected:
            self.download_path_edit.setText(selected)
            self.settings.setValue("download_path", selected)
