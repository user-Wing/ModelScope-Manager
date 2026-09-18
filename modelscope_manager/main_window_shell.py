"""设置恢复、语言主题、托盘与窗口生命周期。"""

from __future__ import annotations

import json
import secrets
import sys
import time
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyle, QSystemTrayIcon, QTabWidget, QTableWidget, QTextEdit, QTimeEdit, QTreeWidget, QWidget, QWidgetAction
from collections import deque
from pathlib import Path
from qfluentwidgets import Theme, qconfig, setTheme, setThemeColor
from . import __version__
from .app_helpers import PUBLIC_ACCOUNT_ID, format_size, format_speed, restore_combo_setting, thumbnail_batch_policy
from .app_workers import DownloadThread, ThumbnailThread, UploadThread
from .database import AccountRecord
from .fluent_ui import CleanComboBox, FluentSwitchButton, PanelSettingCard
from .player_installer import POTPLAYER_ARCHIVE_SIZE
from .plugin_installer import FFMPEG_ARCHIVE_SIZE
from .resource_monitor import ProcessResourceMonitor
from .security import load_secret
from .service import ModelScopeService, RemoteEntry, Repository, configure_upload_limit_supplier
from .startup import set_windows_startup, windows_startup_enabled
from .storage import APP_DIR, PLAYER_DOWNLOAD_DIR, PLUGIN_DOWNLOAD_DIR, destroy_saved_token, restore_device_bound_token
from .styles import theme_qss
from .skin_theme import render_note
from .transfer_policy import TransferPolicy
from .webdav_mapping import load_webdav_mappings


class WindowShellMixin:
    """设置恢复、语言主题、托盘与窗口生命周期。"""

    def _restore_settings(self) -> None:
        self._restoring_settings = True
        default_download = Path.home() / "Downloads"
        self.download_path_edit.setText(str(self.settings.value("download_path", str(default_download))))
        self.drop_upload_threshold_mb.setValue(int(self.settings.value("upload/drop_threshold_mb", 1024)))
        self.upload_queue_count.setValue(int(self.settings.value("upload/queue_count", 4)))
        self.image_dest_edit.setText(str(self.settings.value("image/destination", "images")))
        self.image_auto_avif.setChecked(str(self.settings.value("image/auto_avif", "false")).lower() == "true")
        self.avif_quality.setValue(int(self.settings.value("image/avif_quality", 70)))
        self.avif_speed.setValue(int(self.settings.value("image/avif_speed", 5)))
        restore_combo_setting(self.settings, "image/avif_chroma", self.avif_chroma, "auto")
        restore_combo_setting(self.settings, "image/avif_bit_depth", self.avif_bit_depth, "auto")
        self.avif_keep_metadata.setChecked(str(self.settings.value("image/avif_keep_metadata", "true")).lower() == "true")
        self.aria_small_limit.setValue(float(self.settings.value("aria2/small_limit_mb", 1.0)))
        self.aria_small_segments.setValue(int(self.settings.value("aria2/small_segments", 1)))
        self.aria_medium_segments.setValue(int(self.settings.value("aria2/medium_segments", 32)))
        self.aria_large_limit.setValue(float(self.settings.value("aria2/large_limit_mb", 100.0)))
        self.aria_large_segments.setValue(int(self.settings.value("aria2/large_segments", 64)))
        raw_transfer_policy = str(self.settings.value("transfer/speed_policy", ""))
        try:
            self.transfer_policy = TransferPolicy.from_dict(json.loads(raw_transfer_policy) if raw_transfer_policy else {})
        except (TypeError, ValueError, json.JSONDecodeError):
            self.transfer_policy = TransferPolicy()
        self.speed_limit_enabled.setChecked(self.transfer_policy.enabled)
        self.base_upload_limit.setValue(self.transfer_policy.upload_mib)
        self.base_download_limit.setValue(self.transfer_policy.download_mib)
        self.speed_rule_table.setRowCount(0)
        for rule in self.transfer_policy.rules:
            self._add_speed_rule(rule.start, rule.end, rule.upload_mib, rule.download_mib)
        restore_combo_setting(self.settings, "language", self.language_combo, "zh_CN")
        self.font_size_spin.setValue(int(self.settings.value("font_size", 10)))
        western_font = str(self.settings.value("font/western", "Segoe UI"))
        chinese_font = str(self.settings.value("font/chinese", "Microsoft YaHei UI"))
        self.western_font_combo.setCurrentIndex(max(0, self.western_font_combo.findText(western_font)))
        self.chinese_font_combo.setCurrentIndex(max(0, self.chinese_font_combo.findText(chinese_font)))
        self._load_skin_controls_from_config()
        self.gpu_acceleration_checkbox.setChecked(
            str(self.settings.value("graphics/gpu_acceleration", "true")).lower() == "true"
        )
        self.acrylic_checkbox.setChecked(
            str(self.settings.value("graphics/acrylic", "true")).lower() == "true"
        )
        self.auto_memory_release.setChecked(
            str(self.settings.value("resources/auto_release", "true")).lower() == "true"
        )
        self.memory_release_threshold.setValue(int(self.settings.value("resources/release_threshold_mb", 512)))
        restore_combo_setting(self.settings, "close_behavior", self.close_behavior_combo, "ask")
        self.startup_checkbox.setChecked(windows_startup_enabled())
        self.auto_update_checkbox.setChecked(
            str(self.settings.value("update/automatic", "true")).lower() == "true"
        )
        self.plaintext_credentials_switch.setChecked(self.plaintext_credentials_enabled)
        self.disable_device_destruction_switch.setChecked(self.device_destruction_disabled)
        self.compact_view_button.setChecked(
            str(self.settings.value("compact_view", "false")).lower() == "true"
        )
        self.background_index_minutes.setValue(int(self.settings.value("index/background_minutes", 5)))
        self.thumbnail_maximum_mb.setValue(float(self.settings.value("preview/thumbnail_maximum_mb", 100.0)))
        self.thumbnail_workers.setValue(int(self.settings.value("preview/thumbnail_workers", 16)))
        self.copy_threshold_value.setValue(float(self.settings.value("copy/threshold_value", 100.0)))
        restore_combo_setting(self.settings, "copy/threshold_unit", self.copy_threshold_unit, 1024 ** 2)
        raw_players = str(self.settings.value("external_players", ""))
        try:
            players = json.loads(raw_players) if raw_players else []
        except (TypeError, ValueError):
            players = []
        legacy_player = str(self.settings.value("external_player", ""))
        if not players and legacy_player:
            players = [{"name": Path(legacy_player).stem or "播放器 1", "path": legacy_player}]
            self.settings.remove("external_player")
        self.external_players = players or [{"name": "播放器 1", "path": ""}]
        self._render_players()
        self.builtin_player_enabled.setChecked(
            str(self.settings.value("builtin_player_enabled", "true")).lower() == "true"
        )
        self._refresh_builtin_player_status()
        if str(self.settings.value("player/potplayer_install_pending", "false")).lower() == "true":
            archive = PLAYER_DOWNLOAD_DIR / "PotPlayer.7z"
            self.potplayer_install_archive = archive
            if archive.is_file() and archive.stat().st_size == POTPLAYER_ARCHIVE_SIZE:
                QTimer.singleShot(0, lambda: self._start_potplayer_extraction(archive))
            else:
                self.builtin_player_status.setText(self._t("PotPlayer：下载未完成，点击按钮继续"))
        self._refresh_ffmpeg_status()
        if str(self.settings.value("plugin/ffmpeg_install_pending", "false")).lower() == "true":
            archive = PLUGIN_DOWNLOAD_DIR / "FFmpeg.7z"
            self.ffmpeg_install_archive = archive
            if archive.is_file() and archive.stat().st_size == FFMPEG_ARCHIVE_SIZE:
                QTimer.singleShot(0, lambda: self._start_ffmpeg_extraction(archive))
            else:
                self.ffmpeg_status.setText(self._t("FFmpeg：下载未完成，点击按钮继续"))
        restore_combo_setting(self.settings, "alist/host", self.alist_host_combo, "127.0.0.1")
        self.alist_port.setValue(int(self.settings.value("alist/port", 9867)))
        self.alist_username.setText(str(self.settings.value("alist/username", "modelscope")))
        encrypted_alist_password = str(self.settings.value("alist/password", ""))
        if encrypted_alist_password:
            try:
                alist_password = load_secret(encrypted_alist_password)
            except Exception:
                alist_password = secrets.token_urlsafe(12)
        else:
            alist_password = secrets.token_urlsafe(12)
        self.alist_password.setText(alist_password)
        self.alist_auto_start.setChecked(
            str(self.settings.value("alist/auto_start", "false")).lower() == "true"
        )
        self.webdav_mappings = load_webdav_mappings(
            str(self.settings.value("webdav/custom_mappings", "[]"))
        )
        self._render_webdav_mappings()
        self._restore_webdav_local_mounts()
        self._apply_remote_column_visibility()
        self.disable_settings_wheel.setChecked(
            str(self.settings.value("disable_settings_wheel", "true")).lower() == "true"
        )
        token = restore_device_bound_token(
            self.settings,
            self.device_id,
            self.token_destroyed_on_start,
            destroy_on_device_change=not self.device_destruction_disabled,
        )
        existing_accounts = self.account_store.list_accounts()
        if token and not existing_accounts:
            migrated = AccountRecord("", "默认账户", token=token, remember=True)
            self.account_store.save(migrated)
            destroy_saved_token(self.settings)
        self.accounts = self.account_store.list_accounts()
        self.web_accounts = self.account_store.list_web_accounts()
        self.session_tokens = {account.account_id: account.token for account in self.accounts if account.token}
        self._render_accounts()
        self._render_web_accounts()
        self.backup_jobs = self.backup_store.list_jobs()
        self.image_records = self.image_store.list_records()
        self._render_backup_account_options()
        self._render_backup_jobs()
        self._render_image_account_options()
        self._render_image_records()
        if self.accounts:
            self.account_table.selectRow(0)
        self._restoring_settings = False
        self._save_players()
        self._save_alist_settings()
        self._update_alist_url()
        self._render_public_history()
        self._refresh_tag_filter()
        self._apply_theme()
        self._refresh_theme_notes()
        self._restore_transfer_queues()
        self._refresh_transfer_history()
        self._compact_view_changed(self.compact_view_button.isChecked())
        self._apply_background_index_interval()
        configure_upload_limit_supplier(lambda: self.transfer_policy.limits()[0])
        self._refresh_transfer_limit_status()
        self._update_experimental_risk_banner()

    @staticmethod
    def _stepper(control: QSpinBox | QDoubleSpinBox) -> QWidget:
        control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(control, 1)
        increase = QPushButton("增大")
        increase.clicked.connect(control.stepUp)
        layout.addWidget(increase)
        decrease = QPushButton("减小")
        decrease.clicked.connect(control.stepDown)
        layout.addWidget(decrease)
        return wrapper

    def _navigate(self, index: int) -> None:
        page = self._page_by_id.get(index)
        if page is not None and self.page_stack.currentWidget() is not page:
            self.switchTo(page)

    def switchTo(self, interface: QWidget) -> None:
        """Use a shorter Fluent transition to avoid repainting complex pages for 300 ms."""
        self.stackedWidget.view.setCurrentWidget(interface, duration=160)
        self._apply_page_background(interface)

    def _set_view_mode(self, mode: str) -> None:
        self.resource_view_mode = mode
        self.view_button.setText(self._t("查看：缩略图 ▾" if mode == "thumbnails" else "查看：详细信息 ▾"))
        self.remote_detail_tree.setVisible(mode == "details")
        self.remote_thumbnail_list.setVisible(mode == "thumbnails")
        self._render_remote_details()
        self._update_remote_selection_actions()
        if mode == "thumbnails":
            self._schedule_visible_thumbnails()

    def _schedule_visible_thumbnails(self) -> None:
        direct = self.remote_direct_cache.get(self.current_directory_path)
        if direct is None:
            direct = self._direct_remote_entries(self.remote_entries, self.current_directory_path)
            self.remote_direct_cache[self.current_directory_path] = direct
        self._enqueue_thumbnail_entries(direct, prioritize=True)
        if not (self.thumbnail_task and self.thumbnail_task.isRunning()):
            self.thumbnail_timer.start(350 if len(direct) <= 100 else 900)

    def _enqueue_thumbnail_entries(self, entries: list[RemoteEntry], prioritize: bool = False) -> None:
        maximum_size = int(self.thumbnail_maximum_mb.value() * 1024 * 1024)
        eligible = [
            entry for entry in entries
            if ThumbnailThread.is_eligible(entry, maximum_size)
            and entry.path not in self.thumbnail_paths
            and entry.path not in self.thumbnail_attempted
        ]
        if prioritize:
            priority_paths = {entry.path for entry in eligible}
            self.thumbnail_queue = deque(
                entry for entry in self.thumbnail_queue if entry.path not in priority_paths
            )
            for entry in reversed(eligible):
                self.thumbnail_queue.appendleft(entry)
            self.thumbnail_queued.update(priority_paths)
            return
        for entry in eligible:
            if entry.path in self.thumbnail_queued:
                continue
            self.thumbnail_queue.append(entry)
            self.thumbnail_queued.add(entry.path)

    def _reset_thumbnail_queue(self, entries: list[RemoteEntry]) -> None:
        if self.thumbnail_task and self.thumbnail_task.isRunning():
            try:
                self.thumbnail_task.ready.disconnect(self._thumbnails_ready)
            except RuntimeError:
                pass
            self.thumbnail_task.requestInterruption()
        self.thumbnail_timer.stop()
        self.thumbnail_queue.clear()
        self.thumbnail_queued.clear()
        self.thumbnail_paths.clear()
        self.thumbnail_attempted.clear()
        recursive = sorted(entries, key=lambda entry: (entry.path.count("/"), entry.path.casefold()))
        self._enqueue_thumbnail_entries(recursive)
        if self.thumbnail_queue:
            self.thumbnail_timer.start(150)

    def _load_visible_thumbnails(self) -> None:
        if not self.service or not self.selected_repo or self.thumbnail_task:
            return
        idle_for = time.monotonic() - self._last_user_interaction
        if idle_for < 0.15:
            self.thumbnail_timer.start(max(30, int((0.15 - idle_for) * 1000)))
            return
        batch_size, worker_limit, batch_delay = thumbnail_batch_policy(len(self.remote_entries))
        visible: list[RemoteEntry] = []
        while self.thumbnail_queue and len(visible) < batch_size:
            entry = self.thumbnail_queue.popleft()
            self.thumbnail_queued.discard(entry.path)
            if entry.path not in self.thumbnail_paths and entry.path not in self.thumbnail_attempted:
                visible.append(entry)
        maximum_size = int(self.thumbnail_maximum_mb.value() * 1024 * 1024)
        if not visible:
            return
        self.thumbnail_task = ThumbnailThread(
            self.service, self.selected_repo, visible, maximum_size,
            min(self.thumbnail_workers.value(), worker_limit), self,
        )
        self.thumbnail_task.ready.connect(self._thumbnails_ready)
        self.thumbnail_task.finished.connect(self.thumbnail_task.deleteLater)
        task = self.thumbnail_task
        self.thumbnail_task.finished.connect(lambda: self._thumbnail_task_finished(task))
        self.thumbnail_task.start()

    def _thumbnail_task_finished(self, task: ThumbnailThread) -> None:
        if not task.isInterruptionRequested():
            self.thumbnail_attempted.update(entry.path for entry in task.entries)
        if self.thumbnail_task is task:
            self.thumbnail_task = None
        if self.thumbnail_queue:
            self.thumbnail_timer.start(thumbnail_batch_policy(len(self.remote_entries))[2])

    def _thumbnails_ready(self, paths: dict[str, str]) -> None:
        self.thumbnail_paths.update(paths)
        for index in range(self.remote_thumbnail_list.count()):
            item = self.remote_thumbnail_list.item(index)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry, RemoteEntry) and entry.path in paths:
                item.setIcon(QIcon(paths[entry.path]))

    def _t(self, source: str) -> str:
        return self.locale.text(source)

    def _tf(self, source: str, **values) -> str:
        return self._t(source).format(**values)

    def _apply_language(self) -> None:
        language = str(self.language_combo.currentData()) if hasattr(self, "language_combo") else "zh_CN"
        self.locale.load(language)
        text_widgets = self.findChildren(QLabel) + self.findChildren(QPushButton) + self.findChildren(QCheckBox)
        for widget in text_widgets:
            source = widget.property("i18nSourceText")
            if source is None:
                source = widget.text()
                widget.setProperty("i18nSourceText", source)
            widget.setText(self._t(str(source)))
            tooltip_source = widget.property("i18nSourceTooltip")
            if tooltip_source is None and widget.toolTip():
                tooltip_source = widget.toolTip()
                widget.setProperty("i18nSourceTooltip", tooltip_source)
            if tooltip_source:
                widget.setToolTip(self._t(str(tooltip_source)))
        for switch in self.findChildren(FluentSwitchButton):
            source = switch.property("i18nSourceText")
            if source is None:
                source = switch.sourceText()
                switch.setProperty("i18nSourceText", source)
            switch.setDisplayText(self._t(str(source)))
        for widget in self.findChildren(QWidget):
            tooltip_source = widget.property("i18nSourceTooltip")
            if tooltip_source is None and widget.toolTip():
                tooltip_source = widget.toolTip()
                widget.setProperty("i18nSourceTooltip", tooltip_source)
            if tooltip_source:
                widget.setToolTip(self._t(str(tooltip_source)))
        for card in self.findChildren(PanelSettingCard):
            card.setTranslator(self._t)
        for edit in self.findChildren(QLineEdit):
            source = edit.property("i18nPlaceholder")
            if source is None and edit.placeholderText():
                source = edit.placeholderText()
                edit.setProperty("i18nPlaceholder", source)
            if source:
                edit.setPlaceholderText(self._t(str(source)))
        for combo in self.findChildren(QComboBox) + self.findChildren(CleanComboBox):
            sources = combo.property("i18nItems")
            if sources is None:
                sources = [combo.itemText(index) for index in range(combo.count())]
                combo.setProperty("i18nItems", sources)
            for index, source in enumerate(sources):
                if index < combo.count():
                    combo.setItemText(index, self._t(str(source)))
        for tabs in self.findChildren(QTabWidget):
            sources = tabs.property("i18nTabs")
            if sources is None:
                sources = [tabs.tabText(index) for index in range(tabs.count())]
                tabs.setProperty("i18nTabs", sources)
            for index, source in enumerate(sources):
                tabs.setTabText(index, self._t(str(source)))
        for table in self.findChildren(QTableWidget):
            for column in range(table.columnCount()):
                item = table.horizontalHeaderItem(column)
                if item is None:
                    continue
                source = item.data(Qt.ItemDataRole.UserRole + 10)
                if source is None:
                    source = item.text()
                    item.setData(Qt.ItemDataRole.UserRole + 10, source)
                item.setText(self._t(str(source)))
        for tree in self.findChildren(QTreeWidget):
            item = tree.headerItem()
            if item is None:
                continue
            for column in range(tree.columnCount()):
                source = item.data(column, Qt.ItemDataRole.UserRole + 10)
                if source is None:
                    source = item.text(column)
                    item.setData(column, Qt.ItemDataRole.UserRole + 10, source)
                item.setText(column, self._t(str(source)))
        english = self.locale.language == "en_US"
        self.aria_small_limit.setSuffix(" MB or less" if english else " MB 以下")
        self.aria_large_limit.setSuffix(" MB or more" if english else " MB 以上")
        self.backup_download_limit.setSuffix(" MB or less" if english else " MB 以下")
        self.background_index_minutes.setSuffix(" min" if english else " 分钟")
        self.drop_upload_threshold_mb.setSuffix(" MB")
        self.resource_path_label.set_path(self.current_directory_path, self._t("根目录"))
        for route_key, source in (
            ("resourceInterface", "资源管理"),
            ("searchInterface", "资源搜索"),
            ("transferInterface", "传输列表"),
            ("backupInterface", "备份文件夹"),
            ("imageInterface", "图床"),
            ("webdavMappingInterface", "WebDAV 映射"),
            ("settingsInterface", "设置"),
        ):
            item = self.navigationInterface.widget(route_key)
            if item is not None and hasattr(item, "setText"):
                item.setText(self._t(source))
        self._refresh_transfer_statistics()
        self._refresh_transfer_limit_status()
        if hasattr(self, "update_status_label") and self.available_update is None:
            self.update_status_label.setText(self._tf("当前版本：{version}", version=__version__))
        self._refresh_theme_notes()
        if hasattr(self, "queue_table"):
            self._render_upload_queue()
        if hasattr(self, "web_account_table"):
            self._render_web_accounts()
        if hasattr(self, "backup_table"):
            self._render_backup_jobs()
        if hasattr(self, "repo_list"):
            self._render_repositories()
        self._update_alist_url()
        if hasattr(self, "tray_show_action"):
            self.tray_show_action.setText(self._t("显示 ModelScope Manager"))
            self.tray_quit_action.setText(self._t("退出"))
            self._refresh_tray_status()

    def _language_changed(self) -> None:
        if self._restoring_settings:
            return
        language = str(self.language_combo.currentData())
        self.settings.setValue("language", language)
        self.locale.load(language)
        self._apply_language()
        self._log(self._t("语言设置已立即应用"))

    def _system_is_dark(self) -> bool:
        hints = QApplication.instance().styleHints()
        if hasattr(hints, "colorScheme"):
            return hints.colorScheme() == Qt.ColorScheme.Dark
        return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _apply_theme(self) -> None:
        mode = str(self.theme_combo.currentData()) if hasattr(self, "theme_combo") else "system"
        dark = self._system_is_dark() if mode == "system" else mode == "dark"
        acrylic = bool(hasattr(self, "acrylic_checkbox") and self.acrylic_checkbox.isChecked())
        accent = self._skin_accent_color().name()
        page = self.page_stack.currentWidget() if hasattr(self, "page_stack") else None
        background = self._has_skin_images()
        western = self.western_font_combo.currentText() if hasattr(self, "western_font_combo") else "Segoe UI"
        chinese = self.chinese_font_combo.currentText() if hasattr(self, "chinese_font_combo") else "Microsoft YaHei UI"
        target_theme = Theme.DARK if dark else Theme.LIGHT
        if qconfig.theme != target_theme:
            setTheme(target_theme)
        if qconfig.themeColor.value.name().casefold() != accent.casefold():
            setThemeColor(accent)
        app = QApplication.instance()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#202124" if dark else "#f3f3f3"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#24272c" if dark else "#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2a2d32" if dark else "#f7f7f7"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#e8e8e8" if dark else "#202020"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#e8e8e8" if dark else "#202020"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#30333a" if dark else "#ffffff"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ededed" if dark else "#202020"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#174d73" if dark else "#cce8ff"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff" if dark else "#202020"))
        app.setPalette(palette)
        qss = theme_qss(dark, acrylic, (western, chinese), accent, "skin" if background else "")
        if getattr(self, "_last_app_qss", "") != qss:
            app.setStyleSheet(qss)
            self._last_app_qss = qss
        if hasattr(self, "navigationInterface"):
            self.navigationInterface.setAcrylicEnabled(acrylic)
            self.navigationInterface.style().unpolish(self.navigationInterface)
            self.navigationInterface.style().polish(self.navigationInterface)
            self.navigationInterface.update()
        if hasattr(self, "status_bar"):
            self.status_bar.style().unpolish(self.status_bar)
            self.status_bar.style().polish(self.status_bar)
            self.status_bar.update()
        self._apply_page_background(page)
        if hasattr(self, "queue_table"):
            self._render_upload_queue()
        self.setProperty("theme", "dark" if dark else "light")
        self._apply_window_effects(dark, acrylic)
        self._sync_matplotlib_theme(dark)
        if hasattr(self, "remote_thumbnail_list"):
            self.remote_thumbnail_list.style().unpolish(self.remote_thumbnail_list)
            self.remote_thumbnail_list.style().polish(self.remote_thumbnail_list)
            self.remote_thumbnail_list.viewport().update()

    def _apply_window_effects(self, dark: bool | None = None, acrylic: bool | None = None) -> None:
        if not hasattr(self, "acrylic_checkbox"):
            return
        if dark is None:
            mode = str(self.theme_combo.currentData())
            dark = self._system_is_dark() if mode == "system" else mode == "dark"
        if acrylic is None:
            acrylic = self.acrylic_checkbox.isChecked()
        # FluentWindow already owns the Windows compositor effect. A second DWM
        # backdrop plus a translucent Qt top-level causes stale backing-store frames.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        supported = sys.platform == "win32" and sys.getwindowsversion().build >= 22000
        self.setMicaEffectEnabled(bool(acrylic and supported))
        if hasattr(self, "graphics_status"):
            if acrylic and supported:
                self.graphics_status.setText("Mica 由 FluentWindow 和 Windows 合成器单层渲染。")
            elif acrylic:
                self.graphics_status.setText(self._t("当前系统不支持 Mica，已使用不透明背景以避免残影。"))
            else:
                self.graphics_status.setText("Mica 已关闭，使用低开销不透明背景。")

    def _graphics_settings_changed(self) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("graphics/gpu_acceleration", self.gpu_acceleration_checkbox.isChecked())
        self.settings.setValue("graphics/acrylic", self.acrylic_checkbox.isChecked())
        self._apply_theme()

    def _resource_settings_changed(self, *_args) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("resources/auto_release", self.auto_memory_release.isChecked())
        self.settings.setValue("resources/release_threshold_mb", self.memory_release_threshold.value())

    def _sample_process_resources(self) -> None:
        try:
            if self.resource_monitor is None:
                self.resource_monitor = ProcessResourceMonitor()
            sample = self.resource_monitor.sample()
        except Exception as exc:
            self.resource_usage_label.setText(self._tf("资源监控不可用：{error}", error=exc))
            return
        self._last_resource_sample = sample
        gpu = "不可用" if sample.gpu_dedicated_bytes is None else (
            f"专用 {format_size(sample.gpu_dedicated_bytes)} / 共享 {format_size(sample.gpu_shared_bytes or 0)}"
        )
        self.resource_usage_label.setText(
            self._tf(
                "CPU：{cpu:.1f}% · 内存：{working} · 私有内存：{private} · 显存：{gpu}",
                cpu=sample.cpu_percent,
                working=format_size(sample.working_set_bytes),
                private=format_size(sample.private_bytes),
                gpu=gpu,
            )
        )
        self._refresh_theme_notes()
        background = not self.isVisible() or QApplication.applicationState() != Qt.ApplicationState.ApplicationActive
        active_transfer = bool(
            (self.task and self.task.isRunning())
            or (self.backup_thread and self.backup_thread.isRunning())
            or (self.image_upload_thread and self.image_upload_thread.isRunning())
        )
        now = time.monotonic()
        threshold = self.memory_release_threshold.value() * 1024**2
        if (
            self.auto_memory_release.isChecked()
            and background
            and not active_transfer
            and sample.working_set_bytes >= threshold
            and now - self._last_memory_trim >= 60
        ):
            self.resource_monitor.trim_working_set()
            self._last_memory_trim = now

    def _release_process_memory(self, *_args) -> None:
        if self.resource_monitor is None:
            self.resource_monitor = ProcessResourceMonitor()
        released = self.resource_monitor.trim_working_set()
        self._last_memory_trim = time.monotonic()
        self.status_bar.showMessage(self._t("已请求释放进程工作集") if released else self._t("当前系统不支持工作集释放"), 5000)
        QTimer.singleShot(100, self._sample_process_resources)

    def _theme_changed(self) -> None:
        if self._restoring_settings:
            return
        self.skin_config.color_mode = str(self.theme_combo.currentData())
        # A brightness tuned against a dark palette can make the light palette
        # look permanently dim (and vice versa). Mode changes intentionally
        # restore every page assignment to the neutral 100% baseline.
        for image in self.skin_config.images:
            image.pages = {page: 100 for page in image.pages}
        self._save_skin_config()
        self._render_skin_table()
        self._apply_theme()

    def _refresh_theme_notes(self) -> None:
        sample = self._last_resource_sample
        runtime = max(0, int(time.time() - self.session_started_at))
        days, remainder = divmod(runtime, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        runtime_text = f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}" if days else f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        values = {
            "US": format_speed(self.current_upload_speed),
            "DS": format_speed(self.current_download_speed),
            "AU": format_size(self.lifetime_upload_bytes),
            "AD": format_size(self.lifetime_download_bytes),
            "CU": format_size(self.session_upload_bytes),
            "CD": format_size(self.session_download_bytes),
            "VER": __version__,
            "CPU": f"{sample.cpu_percent:.1f}%" if sample else "--",
            "RAM": format_size(sample.working_set_bytes) if sample else "--",
            "RT": runtime_text,
        }
        self.setWindowTitle(render_note(self.skin_config.top_note, values) or "ModelScope Manager")
        if hasattr(self, "status_note_label"):
            text = render_note(self.skin_config.bottom_note, values)
            self.status_note_label.setText(text)
            self.status_note_label.setToolTip(text)
            self._update_status_note_width()
            self.status_note_label.setVisible(bool(text))

    def _update_status_note_width(self) -> None:
        if not hasattr(self, "status_bar") or not hasattr(self, "status_note_label"):
            return
        text_width = self.status_note_label.fontMetrics().horizontalAdvance(
            self.status_note_label.text()
        ) + 20
        risk_width = self.experimental_risk_banner.sizeHint().width() if self.experimental_risk_banner.isVisible() else 0
        transient_reserve = 220 if self.status_bar.currentMessage() else 24
        available = max(0, self.status_bar.width() - risk_width - transient_reserve)
        # Size to the actual note instead of a fixed percentage. This lets long
        # skin notes use a wide window while still yielding space to a live
        # AList/aria2 message when one is present.
        self.status_note_label.setFixedWidth(min(text_width, available))

    def _font_size_changed(self, value: int) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("font_size", value)
        self._apply_font_scale(value)

    def _apply_font_scale(self, point_size: int) -> None:
        app = QApplication.instance()
        base_size = 10.0
        widgets = app.allWidgets()
        previous_size = max(1, int(getattr(self, "_current_font_point_size", 10)))
        for widget in widgets:
            if widget.property("fluentBasePointSize") is None:
                current_size = widget.font().pointSizeF()
                widget.setProperty(
                    "fluentBasePointSize",
                    (current_size * base_size / previous_size) if current_size > 0 else base_size,
                )
        western = self.western_font_combo.currentText() if hasattr(self, "western_font_combo") else "Segoe UI"
        chinese = self.chinese_font_combo.currentText() if hasattr(self, "chinese_font_combo") else "Microsoft YaHei UI"
        app_font = QFont()
        app_font.setFamilies([western, chinese])
        app_font.setPointSize(point_size)
        app.setFont(app_font)
        for widget in widgets:
            font = widget.font()
            baseline = widget.property("fluentBasePointSize")
            font.setPointSizeF(max(8.0, float(baseline) * point_size / base_size))
            font.setFamilies([western, chinese])
            widget.setFont(font)
        self._current_font_point_size = int(point_size)
        self._sync_matplotlib_theme(
            self.property("theme") == "dark" if self.property("theme") else self._system_is_dark()
        )

    def _sync_matplotlib_theme(self, dark: bool) -> None:
        if "matplotlib" not in sys.modules:
            return
        import matplotlib as mpl

        size = self.font_size_spin.value() if hasattr(self, "font_size_spin") else 10
        western = self.western_font_combo.currentText() if hasattr(self, "western_font_combo") else "Segoe UI"
        chinese = self.chinese_font_combo.currentText() if hasattr(self, "chinese_font_combo") else "Microsoft YaHei UI"
        background = "#202124" if dark else "#f3f3f3"
        foreground = "#e8e8e8" if dark else "#202020"
        mpl.rcParams.update({
            "font.family": [western, chinese, "sans-serif"],
            "font.size": size,
            "figure.facecolor": background,
            "axes.facecolor": background,
            "axes.edgecolor": foreground,
            "axes.labelcolor": foreground,
            "text.color": foreground,
            "xtick.color": foreground,
            "ytick.color": foreground,
        })
        for widget in QApplication.instance().allWidgets():
            figure = getattr(widget, "figure", None)
            if figure is not None:
                figure.set_facecolor(background)
                widget.draw_idle()

    def _system_theme_changed(self, _scheme) -> None:
        if str(self.theme_combo.currentData()) == "system":
            self._apply_theme()

    def _close_behavior_changed(self) -> None:
        if not self._restoring_settings:
            self.settings.setValue("close_behavior", self.close_behavior_combo.currentData())

    def _startup_changed(self, checked: bool) -> None:
        if self._restoring_settings:
            return
        try:
            set_windows_startup(checked, APP_DIR)
        except OSError as exc:
            self.startup_checkbox.blockSignals(True)
            self.startup_checkbox.setChecked(not checked)
            self.startup_checkbox.blockSignals(False)
            QMessageBox.warning(self, self._t("开机自启设置失败"), str(exc))

    def _compact_view_changed(self, checked: bool) -> None:
        height = 26 if checked else 32
        for tree_name in ("remote_tree", "remote_detail_tree", "global_search_tree", "search_remote_tree"):
            tree = getattr(self, tree_name, None)
            if tree is not None:
                tree.setStyleSheet(f"QTreeWidget::item {{ min-height: {height}px; }}")
        if hasattr(self, "repo_list"):
            left_height = 24 if checked else 36
            self.repo_list.setStyleSheet(f"QTreeWidget::item {{ min-height: {left_height}px; }}")
        if hasattr(self, "resource_splitter"):
            self.resource_splitter.widget(0).setMinimumWidth(245 if checked else 180)
            self.resource_splitter.setSizes([270, 820] if checked else [405, 685])
        if not self._restoring_settings:
            self.settings.setValue("compact_view", checked)

    def _wheel_setting_changed(self, checked: bool) -> None:
        if not self._restoring_settings:
            self.settings.setValue("disable_settings_wheel", checked)

    def _background_index_interval_changed(self, value: int) -> None:
        if not self._restoring_settings:
            self.settings.setValue("index/background_minutes", value)
        self._apply_background_index_interval()

    def _drop_upload_threshold_changed(self, value: int) -> None:
        if not self._restoring_settings:
            self.settings.setValue("upload/drop_threshold_mb", value)

    def _upload_queue_count_changed(self, value: int) -> None:
        if not self._restoring_settings:
            self.settings.setValue("upload/queue_count", value)

    def _save_preview_settings(self) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("preview/thumbnail_maximum_mb", self.thumbnail_maximum_mb.value())
        self.settings.setValue("preview/thumbnail_workers", self.thumbnail_workers.value())
        self.settings.setValue("copy/threshold_value", self.copy_threshold_value.value())
        self.settings.setValue("copy/threshold_unit", self.copy_threshold_unit.currentData())

    def _copy_threshold_bytes(self) -> int:
        return int(self.copy_threshold_value.value() * int(self.copy_threshold_unit.currentData() or 1024 ** 2))

    def _apply_background_index_interval(self) -> None:
        if hasattr(self, "background_index_minutes"):
            self.background_index_timer.setInterval(max(1, self.background_index_minutes.value()) * 60000)
            if not self.background_index_timer.isActive():
                self.background_index_timer.start()

    def eventFilter(self, watched, event) -> bool:
        if not self._event_filter_ready:
            return super().eventFilter(watched, event)
        interaction_events = {
            QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel,
        }
        if event.type() in interaction_events:
            self._last_user_interaction = time.monotonic()
            if self.thumbnail_queue and not (self.thumbnail_task and self.thumbnail_task.isRunning()):
                self.thumbnail_timer.start(900)
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Backspace
            and hasattr(self, "resource_page")
            and self.page_stack.currentWidget() is self.resource_page
            and isinstance(watched, QWidget)
            and self.resource_page.isAncestorOf(watched)
            and not isinstance(watched, (QLineEdit, QTextEdit, QAbstractSpinBox))
            and self._go_to_previous_directory()
        ):
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and hasattr(self, "image_page")
            and self.page_stack.currentWidget() is self.image_page
            and event.matches(QKeySequence.StandardKey.Paste)
        ):
            self._paste_images_from_clipboard()
            return True
        if (
            self.dirty_repositories
            and event.type() in interaction_events
        ):
            self.index_idle_timer.start()
        if (
            event.type() == QEvent.Type.Wheel
            and hasattr(self, "disable_settings_wheel")
            and self.disable_settings_wheel.isChecked()
            and isinstance(watched, (QComboBox, CleanComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit))
            and hasattr(self, "settings_page")
            and self.settings_page.isAncestorOf(watched)
        ):
            bar = self.settings_page.findChild(QScrollArea).verticalScrollBar()
            bar.setValue(bar.value() - int(event.angleDelta().y() / 2))
            return True
        return super().eventFilter(watched, event)

    def _build_tray(self) -> None:
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
            self.setWindowIcon(icon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        tray_menu = QMenu(self)
        self.tray_menu = tray_menu

        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(10, 5, 8, 5)
        status_layout.setSpacing(7)
        self.tray_webdav_dot = QLabel("●")
        self.tray_webdav_dot.setFixedWidth(13)
        status_layout.addWidget(self.tray_webdav_dot)
        self.tray_webdav_label = QLabel()
        status_layout.addWidget(self.tray_webdav_label, 1)
        self.tray_webdav_button = QPushButton()
        self.tray_webdav_button.setFixedHeight(27)
        self.tray_webdav_button.clicked.connect(self._toggle_webdav_from_tray)
        status_layout.addWidget(self.tray_webdav_button)
        status_action = QWidgetAction(self)
        status_action.setDefaultWidget(status_widget)
        tray_menu.addAction(status_action)

        speed_widget = QWidget()
        speed_layout = QHBoxLayout(speed_widget)
        speed_layout.setContentsMargins(30, 3, 10, 7)
        self.tray_speed_label = QLabel()
        speed_layout.addWidget(self.tray_speed_label)
        speed_action = QWidgetAction(self)
        speed_action.setDefaultWidget(speed_widget)
        tray_menu.addAction(speed_action)
        tray_menu.addSeparator()

        self.tray_show_action = QAction(self._t("显示 ModelScope Manager"), self)
        self.tray_show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(self.tray_show_action)
        self.tray_quit_action = QAction(self._t("退出"), self)
        self.tray_quit_action.triggered.connect(self._quit_from_tray)
        tray_menu.addAction(self.tray_quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(
            lambda reason: self._show_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self.tray_refresh_timer = QTimer(self)
        self.tray_refresh_timer.setInterval(500)
        self.tray_refresh_timer.timeout.connect(self._refresh_tray_status)
        self.tray_refresh_timer.start()
        self._refresh_tray_status()

    def _refresh_tray_status(self) -> None:
        if not hasattr(self, "tray_webdav_label"):
            return
        running = bool(self.webdav and self.webdav.running)
        self.tray_webdav_dot.setStyleSheet(f"color: {'#16a34a' if running else '#d13438'}; font-size: 15px;")
        self.tray_webdav_label.setText(self._t(
            "WebDAV 监听已开启" if running else "WebDAV 监听已关闭"
        ))
        self.tray_webdav_button.setText(self._t("关闭" if running else "开启"))
        if self._has_active_transfer():
            self.tray_speed_label.setText(
                f"↑ {format_speed(self.current_upload_speed)}  ↓ {format_speed(self.current_download_speed)}"
            )
        else:
            self.tray_speed_label.setText(self._t("当前无任务"))

    def _toggle_webdav_from_tray(self) -> None:
        running = bool(self.webdav and self.webdav.running)
        if running:
            self.stop_alist()
        else:
            self.apply_alist_settings()
        self._refresh_tray_status()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_close = True
        self.close()

    def _has_active_transfer(self) -> bool:
        return bool(
            (self.backup_thread and self.backup_thread.isRunning())
            or (self.image_upload_thread and self.image_upload_thread.isRunning())
            or (self.potplayer_install_thread and self.potplayer_install_thread.isRunning())
            or (self.ffmpeg_install_thread and self.ffmpeg_install_thread.isRunning())
            or (self.update_prepare_thread and self.update_prepare_thread.isRunning())
            or
            self.task
            and self.task.isRunning()
            and isinstance(self.task, (UploadThread, DownloadThread))
        )

    def _confirm_terminate_transfers(self) -> bool:
        if not self._has_active_transfer():
            return True
        answer = QMessageBox.question(
            self,
            self._t("传输正在进行"),
            self._t("当前有任务在进行，是否停止任务、保留断点和队列记录后关闭？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _shutdown_services(self) -> None:
        self._shutting_down = True
        self._save_transfer_queues()
        for item in self.upload_items:
            if item.status not in {"uploading", "paused"}:
                continue
            key = "upload:" + str(item.path)
            if key in self._transfer_started:
                self.transfer_history_store.add(
                    "upload", str(item.path), "/" + item.target, False,
                    "程序关闭，断点和队列已保留", self._transfer_started.pop(key),
                )
        for spec in self.active_download_specs:
            path = str(spec.local_path)
            if path in self._history_recorded:
                continue
            self._history_recorded.add(path)
            self.transfer_history_store.add(
                "download", spec.remote_path, path, False,
                "程序关闭，断点和队列已保留",
                self._transfer_started.pop("download:" + path, time.time()),
            )
        self.thumbnail_timer.stop()
        self.thumbnail_queue.clear()
        if self.thumbnail_task and self.thumbnail_task.isRunning():
            self.thumbnail_task.requestInterruption()
            self.thumbnail_task.wait(60000)
        if self.download_runner:
            try:
                self.download_runner.stop()
            except Exception:
                pass
        if isinstance(self.task, DownloadThread) and self.task.isRunning():
            if not self.task.wait(15000):
                self.task.terminate()
                self.task.wait(3000)
        if self.webdav:
            self.webdav.stop()
            self.webdav = None
        if self.index_task and self.index_task.isRunning():
            self.index_task.requestInterruption()
            self.index_task.wait(3000)
        if self.backup_thread and self.backup_thread.isRunning():
            self.backup_thread.requestInterruption()
            self.backup_thread.wait(3000)
        if self.image_upload_thread and self.image_upload_thread.isRunning():
            self.image_upload_thread.requestInterruption()
            self.image_upload_thread.wait(3000)
        if self.potplayer_install_thread and self.potplayer_install_thread.isRunning():
            self.potplayer_install_thread.wait(3000)
        if self.ffmpeg_install_thread and self.ffmpeg_install_thread.isRunning():
            self.ffmpeg_install_thread.wait(3000)
        if self.update_check_thread and self.update_check_thread.isRunning():
            self.update_check_thread.requestInterruption()
            self.update_check_thread.wait(3000)
        if self.update_prepare_thread and self.update_prepare_thread.isRunning():
            self.update_prepare_thread.cancel()
            self.update_prepare_thread.wait(3000)
        if self.global_search_task and self.global_search_task.isRunning():
            self.global_search_task.requestInterruption()
            self.global_search_task.wait(3000)
        if isinstance(self.task, UploadThread) and self.task.isRunning():
            self.task.cancel()
            if not self.task.wait(15000):
                self.task.terminate()
                self.task.wait(3000)
        self._save_transfer_queues()
        self.settings.sync()
        if self.resource_monitor is not None:
            self.resource_monitor.close()
        self.media_proxy.stop()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.skin_background_layer.setGeometry(self.rect())
        self.skin_background_layer.lower()
        QTimer.singleShot(0, self._apply_window_effects)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "skin_background_layer"):
            self.skin_background_layer.setGeometry(self.rect())
            self.skin_background_layer.lower()
        self._update_status_note_width()

    def closeEvent(self, event) -> None:
        behavior = "close" if self._force_close else str(self.close_behavior_combo.currentData())
        remember = False
        if behavior == "ask":
            box = QMessageBox(self)
            box.setWindowTitle(self._t("关闭窗口"))
            box.setText(self._t("关闭程序，还是最小化到通知区域？"))
            close_button = box.addButton(self._t("关闭程序"), QMessageBox.ButtonRole.AcceptRole)
            tray_button = box.addButton(self._t("最小化"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(self._t("取消"), QMessageBox.ButtonRole.RejectRole)
            remember_box = QCheckBox(self._t("是否记住"))
            box.setCheckBox(remember_box)
            box.exec()
            clicked = box.clickedButton()
            if clicked is close_button:
                behavior = "close"
            elif clicked is tray_button:
                behavior = "tray"
            else:
                event.ignore()
                self._force_close = False
                return
            remember = remember_box.isChecked()
        if behavior == "tray" and QSystemTrayIcon.isSystemTrayAvailable():
            if remember:
                self.settings.setValue("close_behavior", "tray")
                self.close_behavior_combo.setCurrentIndex(self.close_behavior_combo.findData("tray"))
            self.tray_icon.show()
            self.hide()
            self.tray_icon.showMessage(
                "ModelScope Manager",
                self._t("程序仍在通知区域运行，传输任务不会中断。"),
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            event.ignore()
            self._force_close = False
            return
        if not self._confirm_terminate_transfers():
            event.ignore()
            self._force_close = False
            return
        if remember:
            self.settings.setValue("close_behavior", "close")
            self.close_behavior_combo.setCurrentIndex(self.close_behavior_combo.findData("close"))
        self._shutdown_services()
        event.accept()
