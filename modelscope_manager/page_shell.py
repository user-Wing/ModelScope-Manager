"""主窗口页面装配。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QStatusBar, QVBoxLayout
from qfluentwidgets import FluentIcon as FIF, NavigationItemPosition


class PageShellMixin:
    """页面装配、导航注册与初始路由。"""

    def _build_ui(self) -> None:
        self.page_stack = self.stackedWidget
        self.navigationInterface.setExpandWidth(210)
        self.navigationInterface.setMinimumExpandWidth(920)
        self.navigationInterface.setAcrylicEnabled(True)
        self.status_bar = QStatusBar(self)
        self.status_bar.setObjectName("fluentStatusBar")
        self.status_note_label = QLabel()
        self.status_note_label.setObjectName("statusVersion")
        self.status_note_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.status_note_label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.status_note_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.status_note_label.setMinimumWidth(0)
        self.status_note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.experimental_risk_banner = QLabel("测试模式")
        self.experimental_risk_banner.setObjectName("statusRiskMode")
        self.experimental_risk_banner.setToolTip("软件运行在高风险测试模式下")
        self.experimental_risk_banner.setVisible(False)
        self.status_bar.addPermanentWidget(self.experimental_risk_banner)
        # Permanent widgets stay visible while showMessage() displays transient
        # AList/aria2 activity on the left side of the status bar.
        self.status_bar.addPermanentWidget(self.status_note_label)
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
        webdav_mapping_page = self._build_webdav_mapping_page()
        about_page = self._build_about_page()
        page_specs = (
            (resource_page, "resourceInterface", FIF.FOLDER, "资源管理", NavigationItemPosition.TOP),
            (transfer_page, "transferInterface", FIF.SYNC, "传输列表", NavigationItemPosition.TOP),
            (about_page, "aboutInterface", FIF.INFO, "关于", NavigationItemPosition.BOTTOM),
            (settings_page, "settingsInterface", FIF.SETTING, "设置", NavigationItemPosition.BOTTOM),
            (search_page, "searchInterface", FIF.SEARCH, "资源搜索", NavigationItemPosition.TOP),
            (backup_page, "backupInterface", FIF.SAVE, "备份文件夹", NavigationItemPosition.TOP),
            (image_page, "imageInterface", FIF.PHOTO, "图床", NavigationItemPosition.TOP),
            (webdav_mapping_page, "webdavMappingInterface", FIF.CONNECT, "WebDAV 映射", NavigationItemPosition.TOP),
        )
        for page, name, icon, text, position in page_specs:
            page.setObjectName(name)
            # Page backgrounds come from the application theme. Keeping the
            # stack transparent also allows the custom wallpaper layer to show.
            self.addSubInterface(page, icon, text, position=position, isTransparent=True)
        top_layout = self.navigationInterface.panel.topLayout
        for offset, route_key in enumerate((
            "resourceInterface", "searchInterface", "transferInterface",
            "backupInterface", "imageInterface", "webdavMappingInterface",
        )):
            item = self.navigationInterface.widget(route_key)
            top_layout.removeWidget(item)
            top_layout.insertWidget(offset + 2, item, 0, Qt.AlignmentFlag.AlignTop)
        self._page_by_id = {
            -1: about_page,
            0: resource_page,
            1: transfer_page,
            2: settings_page,
            3: search_page,
            4: backup_page,
            5: image_page,
            6: webdav_mapping_page,
        }
        # Skin page numbers are independent from the historical navigation ids.
        # About uses -1, settings uses 0, and top pages use 1 through 6.
        self._skin_page_numbers = {
            about_page: -1,
            settings_page: 0,
            resource_page: 1,
            search_page: 2,
            transfer_page: 3,
            backup_page: 4,
            image_page: 5,
            webdav_mapping_page: 6,
        }
        self._refresh_transfer_statistics()
        self._navigate(0)
