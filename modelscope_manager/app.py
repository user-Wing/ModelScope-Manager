"""应用入口与旧版导入兼容层；具体 UI/业务职责位于 app_*、page_*、main_* 模块。"""

from __future__ import annotations

import json
import hashlib
import os
import posixpath
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote
from urllib.request import Request

from PySide6.QtCore import (
    QDateTime, QEvent, QPointF, QProcess,
    QSettings, QSize, Qt, QThread, QTime, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont,
    QIcon, QKeySequence, QPainter, QPalette, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QSpinBox,
    QScrollArea,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    NavigationItemPosition,
    ScrollArea as FluentScrollArea,
    SettingCardGroup,
    SpinBox as FluentSpinBox,
    ToolButton,
    Theme,
    setTheme,
    setThemeColor,
)

from . import __version__
from .security import protect, unprotect
from .http_security import modelscope_token_headers, safe_urlopen
from .download_service import Aria2DownloadRunner, Aria2Tuning, DownloadSpec, build_download_specs
from .backup import BackupJob, BackupStore, LocalBackupFile
from .avif_converter import AvifOptions, convert_to_avif, find_ffmpeg
from .database import (
    AccountRecord, AccountStore, IndexedEntry, WebAccountRecord, classify_file,
    everything_search_match, initialize_database,
)
from .folder_index import FolderSizeIndex
from .fluent_ui import CleanComboBox, ControlSettingCard, FluentSwitchButton, PanelSettingCard
from .image_bed import IMAGE_EXTENSIONS, ImageRecord, ImageStore
from .localization import LocaleManager
from .local_paths import iter_contained_files
from .media_proxy import AuthenticatedMediaProxy
from .player_installer import (
    POTPLAYER_ARCHIVE_SHA256,
    POTPLAYER_ARCHIVE_SIZE,
    POTPLAYER_REMOTE_PATH,
    POTPLAYER_REPOSITORY,
    find_potplayer,
    install_potplayer,
)
from .public_pools import PublicPoolStore
from .resource_monitor import ProcessResourceMonitor
from .service import (
    ModelScopeService,
    ModelScopeWebService,
    MultiAccountService,
    RemoteEntry,
    Repository,
    normalize_remote_path,
    oversized_upload_files,
    parse_modelscope_repository_location,
    parse_modelscope_repository_url,
    repository_directories,
)
from .styles import theme_qss
from .skin_theme import SkinImage, ensure_skin_library, save_skin_config
from .storage import (
    APP_DIR,
    DEVICE_ID_PATH,
    FOLDER_INDEX_PATH,
    IMAGE_CACHE_DIR,
    THUMBNAIL_CACHE_DIR,
    MANAGER_DB_PATH,
    PLAYER_DOWNLOAD_DIR,
    PLUGIN_DOWNLOAD_DIR,
    FFMPEG_DIR,
    POTPLAYER_DIR,
    PUBLIC_POOLS_PATH,
    SEVEN_ZIP_ZSTD_EXE,
    DeviceIdentity,
    destroy_saved_token,
    portable_settings,
    restore_device_bound_token,
)
from .startup import set_windows_startup, windows_startup_enabled
from .transfer_policy import SpeedRule, TransferPolicy
from .transfer_statistics import TransferSample, TransferStatistics, UploadHealthMonitor
from .transfer_history import TransferHistoryStore
from .webdav_server import ModelScopeWebDAV
from .webdav_mapping import load_webdav_mappings
from .web_session import (
    DELETE_BATCH_SIZE, ModelScopeWebSession, delete_repository_file, delete_repository_files,
    fetch_web_user_info, list_repository_file_paths, web_session_username,
)


from .app_helpers import (
    MEDIA_EXTENSIONS, PUBLIC_ACCOUNT_ID, THUMBNAIL_RENDER_SIZE, VIDEO_THUMBNAIL_SEEK_SECONDS,
    breadcrumb_levels, copy_name, find_available_port, format_eta, format_size, format_speed,
    is_supported_image_file, local_path_identity, local_paths_size, repository_file_url,
    repository_identity, repository_is_public, restore_combo_setting, running_download_percent,
    thumbnail_batch_policy,
)
from .app_widgets import DropArea, PathBreadcrumb, RepositoryList, RepositoryTree, SkinBackgroundLayer, TransferChart
from .app_workers import (
    BackupThread, CopyThread, DeleteThread, DownloadThread, FFmpegInstallThread, FolderIndexThread, ImageUploadThread,
    PotPlayerInstallThread, RelocateThread, TaskThread, ThumbnailThread, UploadCancelled,
    UploadQueueItem, UploadThread,
)
from .login_dialog import ModelScopeLoginDialog
from .main_mixins import MainWindowMixin

class MainWindow(MainWindowMixin, FluentWindow):
    def __init__(self):
        self._event_filter_ready = False
        super().__init__()
        self.setWindowTitle("ModelScope Manager")
        self.resize(1180, 760)
        self.setMinimumSize(980, 650)
        self.skin_background_layer = SkinBackgroundLayer(self)
        self.skin_background_layer.setGeometry(self.rect())
        self.skin_background_layer.lower()
        self.settings = portable_settings()
        self._current_font_point_size = max(1, int(self.settings.value("font_size", 10)))
        self.skin_directory, self.skin_config = ensure_skin_library(
            str(self.settings.value("skin/active_folder", ""))
        )
        self.skin_config_path = self.skin_directory / "skin.ini"
        self.settings.setValue("skin/active_folder", self.skin_directory.name)
        if (
            not self.settings.value("skin/library_initialized", False, type=bool)
            and not self.skin_config.images
        ):
            legacy_mode = str(self.settings.value("theme", "system"))
            self.skin_config.color_mode = legacy_mode if legacy_mode in {"light", "dark", "system"} else "dark"
            legacy_color = str(self.settings.value("skin/theme_color", "#0078D4"))
            self.skin_config.color = legacy_color if QColor(legacy_color).isValid() else "auto"
            legacy_background = Path(str(self.settings.value("skin/background", "")))
            if legacy_background.is_file():
                self.skin_directory.mkdir(parents=True, exist_ok=True)
                target = self.skin_directory / legacy_background.name
                if legacy_background.resolve() != target.resolve():
                    shutil.copy2(legacy_background, target)
                brightness = min(180, max(20, int(self.settings.value("skin/brightness", 100))))
                self.skin_config.images.append(SkinImage(target.name, {page: brightness for page in range(7)}))
                self.skin_config.auto_color_image = target.name
            save_skin_config(self.skin_config_path, self.skin_config)
        self.settings.setValue("skin/library_initialized", True)
        for legacy_key in ("theme", "skin/theme_color", "skin/background", "skin/brightness"):
            self.settings.remove(legacy_key)
        self._skin_random_assignments: dict[int, str] = {}
        self.plaintext_credentials_enabled = str(
            self.settings.value("experiments/plaintext_credentials", "false")
        ).lower() == "true"
        self.device_destruction_disabled = str(
            self.settings.value("experiments/disable_device_destruction", "false")
        ).lower() == "true"
        self.settings.remove("experiments/allow_large_uploads")
        self.device_id, identity_replaced = DeviceIdentity(DEVICE_ID_PATH).load_or_create()
        self.token_destroyed_on_start = bool(
            identity_replaced
            and not self.device_destruction_disabled
            and self.settings.contains("token")
        )
        initialize_database(MANAGER_DB_PATH, FOLDER_INDEX_PATH)
        self.account_store = AccountStore(
            MANAGER_DB_PATH,
            self.device_id,
            identity_replaced,
            plaintext_storage=self.plaintext_credentials_enabled,
            destroy_on_device_change=not self.device_destruction_disabled,
        )
        self.backup_store = BackupStore(MANAGER_DB_PATH)
        self.image_store = ImageStore(MANAGER_DB_PATH, IMAGE_CACHE_DIR)
        self.token_destroyed_on_start = self.token_destroyed_on_start or self.account_store.tokens_destroyed
        self.locale = LocaleManager(str(self.settings.value("language", "zh_CN")))
        self.public_pool_store = PublicPoolStore(PUBLIC_POOLS_PATH)
        self.folder_index = FolderSizeIndex(MANAGER_DB_PATH)
        self.accounts: list[AccountRecord] = []
        self.web_accounts: list[WebAccountRecord] = []
        self.session_tokens: dict[str, str] = {}
        self.session_web_sessions: dict[str, ModelScopeWebSession] = {}
        self.account_services: dict[str, ModelScopeService] = {}
        self.account_repositories: dict[str, list[Repository]] = {}
        self.active_account_id: str | None = None
        self.active_account_kind: str | None = None
        self.service: ModelScopeService | None = None
        self.repositories: list[Repository] = []
        self.selected_repo: Repository | None = None
        self.selected_repo_public = False
        self.remote_entries: list[RemoteEntry] = []
        self.remote_direct_cache: dict[str, list[RemoteEntry]] = {}
        self.current_directory_path = ""
        self.directory_history: list[str] = []
        self.resource_view_mode = "details"
        self.detail_sort_column = 0
        self.detail_sort_order = Qt.SortOrder.AscendingOrder
        self.global_search_sort_column = 0
        self.global_search_sort_order = Qt.SortOrder.AscendingOrder
        self.public_search_sort_column = 0
        self.public_search_sort_order = Qt.SortOrder.AscendingOrder
        self.thumbnail_task: ThumbnailThread | None = None
        self.thumbnail_paths: dict[str, str] = {}
        self.thumbnail_attempted: set[str] = set()
        self.thumbnail_queue: deque[RemoteEntry] = deque()
        self.thumbnail_queued: set[str] = set()
        self._last_user_interaction = time.monotonic()
        self.thumbnail_timer = QTimer(self)
        self.thumbnail_timer.setSingleShot(True)
        self.thumbnail_timer.setInterval(100)
        self.thumbnail_timer.timeout.connect(self._load_visible_thumbnails)
        self.copy_source: tuple[ModelScopeService, Repository, list[RemoteEntry], list[RemoteEntry]] | None = None
        self.copy_task: CopyThread | None = None
        self.move_source: tuple[str, ModelScopeService, Repository, list[RemoteEntry], list[RemoteEntry]] | None = None
        self.delete_task: DeleteThread | None = None
        self.relocate_task: RelocateThread | None = None
        self.relocate_context: tuple[str, Repository, str, Repository, list[RemoteEntry]] | None = None
        self.global_search_results: list[IndexedEntry] = []
        self.global_search_task: TaskThread | None = None
        self.global_search_generation = 0
        self.global_search_pending = False
        self.global_search_render_index = 0
        self.pending_search_path: str = ""
        self.upload_items: list[UploadQueueItem] = []
        self.upload_session_service: ModelScopeService | None = None
        self.upload_session_repo: Repository | None = None
        self.upload_session_account_id: str | None = None
        self.upload_ok = 0
        self.upload_failed = 0
        self.upload_cancelled = 0
        self.download_specs: list[DownloadSpec] = []
        self.download_states: dict[str, str] = {}
        self.active_download_specs: list[DownloadSpec] = []
        self.download_runner: Aria2DownloadRunner | None = None
        self.backup_jobs: list[BackupJob] = []
        self.backup_thread: BackupThread | None = None
        self.backup_automatic = False
        self.backup_sync_job_paths: dict[str, str] = {}
        self.image_records: list[ImageRecord] = []
        self.image_upload_thread: ImageUploadThread | None = None
        self.media_proxy = AuthenticatedMediaProxy()
        self.potplayer_install_archive: Path | None = None
        self.potplayer_install_thread: PotPlayerInstallThread | None = None
        self.ffmpeg_install_archive: Path | None = None
        self.ffmpeg_install_thread: FFmpegInstallThread | None = None
        self.update_check_thread: QThread | None = None
        self.update_prepare_thread: QThread | None = None
        self.update_check_manual = False
        self.available_update = None
        self.prepared_update = None
        self.search_service: ModelScopeService | None = None
        self.search_repo: Repository | None = None
        self.search_entries: list[RemoteEntry] = []
        self.search_root_path = ""
        self.external_players: list[dict[str, str]] = []
        self._image_repository_selections: dict[str, tuple[str, str]] = {}
        self.search_history_window: QWidget | None = None
        self.transfer_policy = TransferPolicy()
        self.webdav: ModelScopeWebDAV | None = None
        self.webdav_mappings = load_webdav_mappings(
            str(self.settings.value("webdav/custom_mappings", "[]"))
        )
        self.index_task: FolderIndexThread | None = None
        self._index_refresh_pending = False
        self.index_inflight_keys: set[tuple[str, str, str, bool]] = set()
        self.dirty_repositories: set[tuple[str, str, str, bool]] = set()
        self.current_upload_speed = 0.0
        self.current_download_speed = 0.0
        self.session_upload_bytes = 0
        self.session_download_bytes = 0
        self.lifetime_upload_bytes = int(self.settings.value("statistics/lifetime_upload_bytes", 0))
        self.lifetime_download_bytes = int(self.settings.value("statistics/lifetime_download_bytes", 0))
        self._last_resource_sample = None
        self.session_started_at = time.time()
        self.transfer_statistics = TransferStatistics(self.session_started_at)
        self.transfer_history_store = TransferHistoryStore(MANAGER_DB_PATH)
        self._transfer_started: dict[str, float] = {}
        self._history_recorded: set[str] = set()
        self.upload_health_monitor = UploadHealthMonitor()
        # GPU/PDH discovery can take about half a second on Windows. Create it
        # on the first scheduled sample, after the main window is visible.
        self.resource_monitor: ProcessResourceMonitor | None = None
        self._last_memory_trim = 0.0
        self._download_stat_last_completed = 0
        self._download_stat_initialized = False
        self._force_close = False
        self._shutting_down = False
        self._restoring_settings = False
        self.task: QThread | None = None
        self.resource_search_timer = QTimer(self)
        self.resource_search_timer.setSingleShot(True)
        self.resource_search_timer.setInterval(220)
        self.resource_search_timer.timeout.connect(self._perform_global_search)
        self.global_search_render_timer = QTimer(self)
        self.global_search_render_timer.setInterval(0)
        self.global_search_render_timer.timeout.connect(self._render_global_search_chunk)
        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(30000)
        self.backup_timer.timeout.connect(self._check_backup_schedule)
        self.index_idle_timer = QTimer(self)
        self.index_idle_timer.setSingleShot(True)
        self.index_idle_timer.setInterval(5000)
        self.index_idle_timer.timeout.connect(self._run_idle_index_refresh)
        self.background_index_timer = QTimer(self)
        self.background_index_timer.timeout.connect(self._background_index_tick)
        self.transfer_policy_timer = QTimer(self)
        self.transfer_policy_timer.setInterval(30000)
        self.transfer_policy_timer.timeout.connect(self._refresh_transfer_limit_status)
        self.transfer_statistics_timer = QTimer(self)
        self.transfer_statistics_timer.setInterval(1000)
        self.transfer_statistics_timer.timeout.connect(self._sample_transfer_statistics)
        self.resource_monitor_timer = QTimer(self)
        self.resource_monitor_timer.setInterval(2000)
        self.resource_monitor_timer.timeout.connect(self._sample_process_resources)
        self.auto_update_timer = QTimer(self)
        self.auto_update_timer.setSingleShot(True)
        self.auto_update_timer.setInterval(2500)
        self.auto_update_timer.timeout.connect(self.start_auto_update_check)
        # run() installs the final application QSS before these widgets are
        # created. Remember it so _apply_theme() does not repolish the entire
        # window during settings restoration.
        self._last_app_qss = QApplication.instance().styleSheet()
        self._build_ui()
        hints = QApplication.instance().styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._system_theme_changed)
        QApplication.instance().installEventFilter(self)
        self._restore_settings()
        self._apply_language()
        self._build_tray()
        if self.token_destroyed_on_start:
            self.account_label.setText(self._t("检测到设备变化，已销毁已保存的访问令牌"))
            self._log("检测到设备变化，已销毁已保存的访问令牌")
        if any(account.token for account in self.accounts) or any(
            self.account_store.load_web_session(account.account_id) for account in self.web_accounts
        ):
            # Let the first frame reach the compositor before SDK/service work
            # begins. The network work itself continues on TaskThread.
            QTimer.singleShot(160, self.load_repositories)
        else:
            if self.alist_auto_start.isChecked():
                QTimer.singleShot(160, self.apply_alist_settings)
            QTimer.singleShot(220, lambda: self._start_folder_indexing(True))
        self.backup_timer.start()
        self.transfer_policy_timer.start()
        self.transfer_statistics_timer.start()
        self.resource_monitor_timer.start()
        self.auto_update_timer.start()
        self._event_filter_ready = True


def run() -> int:
    launch_settings = portable_settings()
    gpu_acceleration = str(launch_settings.value("graphics/gpu_acceleration", "true")).lower() == "true"
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    QApplication.setAttribute(
        Qt.ApplicationAttribute.AA_UseDesktopOpenGL
        if gpu_acceleration else Qt.ApplicationAttribute.AA_UseSoftwareOpenGL,
        True,
    )
    app = QApplication(sys.argv)
    app.setApplicationName("ModelScope Manager")
    app.setOrganizationName("ARXChem")
    launch_font_families = (
        str(launch_settings.value("font/western", "Segoe UI")),
        str(launch_settings.value("font/chinese", "Microsoft YaHei UI")),
    )
    initial_font = QFont()
    initial_font.setFamilies(list(launch_font_families))
    initial_font.setPointSize(int(launch_settings.value("font_size", 10)))
    app.setFont(initial_font)
    launch_skin_directory, launch_skin = ensure_skin_library(
        str(launch_settings.value("skin/active_folder", ""))
    )
    launch_settings.setValue("skin/active_folder", launch_skin_directory.name)
    launch_dark = (
        QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        if launch_skin.color_mode == "system"
        else launch_skin.color_mode == "dark"
    )
    # Set Fluent's global theme before creating hundreds of widgets. Doing it
    # afterwards forces every control to rebuild its style sheet and dominated
    # the former 5–10 second startup delay.
    setTheme(Theme.DARK if launch_dark else Theme.LIGHT)
    launch_accent = QColor(launch_skin.color)
    if launch_skin.color.lower() == "auto" and launch_skin.images:
        names = [item.filename for item in launch_skin.images]
        name = launch_skin.auto_color_image if launch_skin.auto_color_image in names else names[0]
        launch_accent = MainWindow._dominant_skin_color(launch_skin_directory / name)
    if not launch_accent.isValid():
        launch_accent = QColor("#0078D4")
    setThemeColor(launch_accent.name())
    launch_acrylic = str(launch_settings.value("graphics/acrylic", "true")).lower() == "true"
    app.setStyleSheet(theme_qss(
        launch_dark,
        launch_acrylic,
        launch_font_families,
        launch_accent.name(),
        "skin" if launch_skin.images else "",
    ))
    window = MainWindow()
    window.show()
    return app.exec()
