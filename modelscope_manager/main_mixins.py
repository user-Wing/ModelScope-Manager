"""MainWindow 的模块化组合入口。"""

from __future__ import annotations

from .main_accounts import AccountsMixin
from .main_backups import BackupsMixin
from .main_image_bed import ImageBedMixin
from .main_integrations import IntegrationsMixin
from .main_remote_actions import RemoteActionsMixin
from .main_repository_browser import RepositoryBrowserMixin
from .main_repository_search import RepositorySearchMixin
from .main_transfers import TransfersMixin
from .main_window_shell import WindowShellMixin
from .page_backup import BackupPageMixin
from .page_image_bed import ImageBedPageMixin
from .page_resource import ResourcePageMixin
from .page_search import SearchPageMixin
from .page_settings import SettingsPageMixin
from .page_shell import PageShellMixin
from .page_transfer import TransferPageMixin


class MainWindowMixin(
    PageShellMixin,
    ResourcePageMixin,
    TransferPageMixin,
    SettingsPageMixin,
    SearchPageMixin,
    BackupPageMixin,
    ImageBedPageMixin,
    AccountsMixin,
    BackupsMixin,
    ImageBedMixin,
    WindowShellMixin,
    IntegrationsMixin,
    RepositoryBrowserMixin,
    RepositorySearchMixin,
    RemoteActionsMixin,
    TransfersMixin,
):
    """组合全部页面构建与业务行为，供 app.MainWindow 保持稳定入口。"""

    pass
