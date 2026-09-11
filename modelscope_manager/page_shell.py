"""主窗口页面装配。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStatusBar, QVBoxLayout
from qfluentwidgets import FluentIcon as FIF, NavigationItemPosition
from . import __version__


class PageShellMixin:
    """页面装配、导航注册与初始路由。"""

    def _build_ui(self) -> None:
        self.page_stack = self.stackedWidget
        self.navigationInterface.setExpandWidth(210)
        self.navigationInterface.setMinimumExpandWidth(920)
        self.navigationInterface.setAcrylicEnabled(True)
        self.status_bar = QStatusBar(self)
        self.status_bar.setObjectName("fluentStatusBar")
        self.status_bar.showMessage(f"ModelScope Manager {__version__}")
        self.widgetLayout.removeWidget(self.stackedWidget)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.stackedWidget, 1)
        content_layout.addWidget(self.status_bar)
        self.widgetLayout.addLayout(content_layout, 1)

        resource_page = self._build_resource_page()
        transfer_page = self._build_transfer_page()
        settings_page = self._build_settings_page()
        search_page = self._build_search_page()
        backup_page = self._build_backup_page()
        image_page = self._build_image_page()
        page_specs = (
            (resource_page, "resourceInterface", FIF.FOLDER, "资源管理", NavigationItemPosition.TOP),
            (transfer_page, "transferInterface", FIF.SYNC, "传输列表", NavigationItemPosition.TOP),
            (settings_page, "settingsInterface", FIF.SETTING, "设置", NavigationItemPosition.BOTTOM),
            (search_page, "searchInterface", FIF.SEARCH, "资源搜索", NavigationItemPosition.TOP),
            (backup_page, "backupInterface", FIF.SAVE, "备份文件夹", NavigationItemPosition.TOP),
            (image_page, "imageInterface", FIF.PHOTO, "图床", NavigationItemPosition.TOP),
        )
        for page, name, icon, text, position in page_specs:
            page.setObjectName(name)
            self.addSubInterface(page, icon, text, position=position, isTransparent=False)
        top_layout = self.navigationInterface.panel.topLayout
        for offset, route_key in enumerate((
            "resourceInterface", "searchInterface", "transferInterface",
            "backupInterface", "imageInterface",
        )):
            item = self.navigationInterface.widget(route_key)
            top_layout.removeWidget(item)
            top_layout.insertWidget(offset + 2, item, 0, Qt.AlignmentFlag.AlignTop)
        self._page_by_id = {
            0: resource_page,
            1: transfer_page,
            2: settings_page,
            3: search_page,
            4: backup_page,
            5: image_page,
        }
        self._refresh_transfer_statistics()
        self._navigate(0)
