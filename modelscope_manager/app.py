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
    QIcon, QImageReader, QKeySequence, QPainter, QPalette, QPen, QPolygonF,
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
    QStatusBar,
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
    parse_modelscope_repository_url,
    repository_directories,
    configure_upload_limit_supplier,
)
from .styles import theme_qss
from .storage import (
    APP_DIR,
    DEVICE_ID_PATH,
    FOLDER_INDEX_PATH,
    IMAGE_CACHE_DIR,
    THUMBNAIL_CACHE_DIR,
    MANAGER_DB_PATH,
    PLAYER_DOWNLOAD_DIR,
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
from .webdav_server import ModelScopeWebDAV
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
from .app_widgets import DropArea, PathBreadcrumb, RepositoryList, RepositoryTree, TransferChart
from .app_workers import (
    BackupThread, CopyThread, DeleteThread, DownloadThread, FolderIndexThread, ImageUploadThread,
    PotPlayerInstallThread, RelocateThread, TaskThread, ThumbnailThread, UploadCancelled,
    UploadQueueItem, UploadThread,
)
from .login_dialog import ModelScopeLoginDialog
from .main_mixins import MainWindowMixin

class MainWindow(MainWindowMixin, FluentWindow):
    def __init__(self):
        self._event_filter_ready = False
        super().__init__()
        self.setWindowTitle(f"ModelScope Manager {__version__}")
        self.resize(1180, 760)
        self.setMinimumSize(980, 650)
        self.settings = portable_settings()
        self.device_id, identity_replaced = DeviceIdentity(DEVICE_ID_PATH).load_or_create()
        self.token_destroyed_on_start = bool(identity_replaced and self.settings.contains("token"))
        initialize_database(MANAGER_DB_PATH, FOLDER_INDEX_PATH)
        self.account_store = AccountStore(MANAGER_DB_PATH, self.device_id, identity_replaced)
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
        self.copy_source: tuple[ModelScopeService, Repository, list[RemoteEntry], RemoteEntry] | None = None
        self.copy_task: CopyThread | None = None
        self.move_source: tuple[str, ModelScopeService, Repository, list[RemoteEntry], RemoteEntry] | None = None
        self.delete_task: DeleteThread | None = None
        self.relocate_task: RelocateThread | None = None
        self.relocate_context: tuple[str, Repository, str, Repository, list[RemoteEntry]] | None = None
        self.global_search_results: list[IndexedEntry] = []
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
        self.search_service: ModelScopeService | None = None
        self.search_repo: Repository | None = None
        self.search_entries: list[RemoteEntry] = []
        self.external_players: list[dict[str, str]] = []
        self._image_repository_selections: dict[str, tuple[str, str]] = {}
        self.search_history_window: QWidget | None = None
        self.transfer_policy = TransferPolicy()
        self.webdav: ModelScopeWebDAV | None = None
        self.index_task: FolderIndexThread | None = None
        self._index_refresh_pending = False
        self.index_inflight_keys: set[tuple[str, str, str, bool]] = set()
        self.dirty_repositories: set[tuple[str, str, str, bool]] = set()
        self.current_upload_speed = 0.0
        self.current_download_speed = 0.0
        self.session_started_at = time.time()
        self.transfer_statistics = TransferStatistics(self.session_started_at)
        self.upload_health_monitor = UploadHealthMonitor()
        self.resource_monitor = ProcessResourceMonitor()
        self._last_memory_trim = 0.0
        self._download_stat_last_completed = 0
        self._force_close = False
        self._restoring_settings = False
        self.task: QThread | None = None
        self.resource_search_timer = QTimer(self)
        self.resource_search_timer.setSingleShot(True)
        self.resource_search_timer.setInterval(80)
        self.resource_search_timer.timeout.connect(self._perform_global_search)
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
            QTimer.singleShot(0, self.load_repositories)
        else:
            if self.alist_auto_start.isChecked():
                QTimer.singleShot(0, self.apply_alist_settings)
            QTimer.singleShot(0, lambda: self._start_folder_indexing(True))
        self.backup_timer.start()
        self.transfer_policy_timer.start()
        self.transfer_statistics_timer.start()
        self.resource_monitor_timer.start()
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
    initial_font = QFont("Microsoft YaHei UI")
    initial_font.setPointSize(int(launch_settings.value("font_size", 10)))
    app.setFont(initial_font)
    window = MainWindow()
    window.show()
    return app.exec()
