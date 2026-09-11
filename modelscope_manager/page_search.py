"""公共资源搜索页面构建。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
from .app_widgets import RepositoryTree


class SearchPageMixin:
    """公共资源搜索页面构建。"""

    def _build_search_page(self) -> QWidget:
        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.setContentsMargins(24, 20, 24, 22)
        search_layout.setSpacing(14)
        search_layout.addWidget(QLabel("资源搜索", objectName="title"))
        search_layout.addWidget(QLabel("输入他人的公开数据集或模型链接，无需登录即可浏览和下载。", objectName="subtitle"))
        search_bar = QHBoxLayout()
        self.search_url_edit = QComboBox()
        self.search_url_edit.setEditable(True)
        self.search_url_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.search_url_edit.lineEdit().setPlaceholderText("https://www.modelscope.cn/datasets/账户/仓库")
        self.search_url_edit.lineEdit().returnPressed.connect(self.load_public_resource)
        self.search_url_edit.activated.connect(lambda: self.load_public_resource())
        search_bar.addWidget(self.search_url_edit, 1)
        self.search_load_button = QPushButton("加载", objectName="primary")
        self.search_load_button.clicked.connect(self.load_public_resource)
        search_bar.addWidget(self.search_load_button)
        search_history_button = QPushButton("搜索历史")
        search_history_button.clicked.connect(self.show_search_history)
        search_bar.addWidget(search_history_button)
        search_layout.addLayout(search_bar)
        search_card = QFrame(objectName="card")
        search_card_layout = QVBoxLayout(search_card)
        search_card_layout.setContentsMargins(16, 16, 16, 16)
        self.search_heading = QLabel("等待输入公开资源链接", objectName="section")
        search_card_layout.addWidget(self.search_heading)
        file_search_row = QHBoxLayout()
        self.public_file_search_edit = QLineEdit()
        self.public_file_search_edit.setClearButtonEnabled(True)
        self.public_file_search_edit.setPlaceholderText(
            "搜索文件：空格分词且全部匹配；支持 path:、name:、ext:、type: 和 * ?"
        )
        self.public_file_search_edit.textChanged.connect(self._render_public_search_results)
        file_search_row.addWidget(self.public_file_search_edit, 1)
        file_search_row.addWidget(QLabel("排序"))
        self.public_search_sort_combo = QComboBox()
        for label, column in (("名称", 0), ("类型", 1), ("大小", 2), ("路径", 3)):
            self.public_search_sort_combo.addItem(label, column)
        self.public_search_sort_combo.currentIndexChanged.connect(self._public_search_sort_combo_changed)
        file_search_row.addWidget(self.public_search_sort_combo)
        self.public_search_direction_button = QPushButton("升序")
        self.public_search_direction_button.clicked.connect(self._toggle_public_search_direction)
        file_search_row.addWidget(self.public_search_direction_button)
        search_card_layout.addLayout(file_search_row)
        self.public_search_count_label = QLabel("", objectName="subtitle")
        search_card_layout.addWidget(self.public_search_count_label)
        self.search_remote_tree = RepositoryTree()
        self.search_remote_tree.setAcceptDrops(False)
        self.search_remote_tree.setObjectName("repositoryTree")
        self.search_remote_tree.setSortingEnabled(False)
        self.search_remote_tree.setRootIsDecorated(False)
        self.search_remote_tree.setUniformRowHeights(True)
        self.search_remote_tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.search_remote_tree.setHeaderLabels(["名称", "类型", "大小", "路径"])
        search_header = self.search_remote_tree.header()
        search_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        search_header.setStretchLastSection(True)
        search_header.setSectionsClickable(True)
        search_header.setSortIndicatorShown(True)
        search_header.sectionClicked.connect(self._change_public_search_sort)
        self.search_remote_tree.setColumnWidth(0, 340)
        self.search_remote_tree.setColumnWidth(1, 90)
        self.search_remote_tree.setColumnWidth(2, 110)
        self.search_remote_tree.setColumnWidth(3, 330)
        self.search_remote_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_remote_tree.customContextMenuRequested.connect(self._search_context_menu)
        search_card_layout.addWidget(self.search_remote_tree, 1)
        search_layout.addWidget(search_card, 1)

        return search_page
