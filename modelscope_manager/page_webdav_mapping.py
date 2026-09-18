"""Custom WebDAV mount-point editor and combined tree preview."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
    QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .fluent_ui import CleanComboBox
from .service import RemoteEntry
from .webdav_mapping import (
    WebDAVMapping, WebDAVMount, dump_webdav_mappings, validate_mapping_name,
    validate_source_path, validate_virtual_target,
)


class _AdaptiveWebDAVPage(QWidget):
    """Re-run the hidden-page layout when FluentWindow first exposes it."""

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.layout() is not None:
            self.layout().invalidate()
            self.layout().activate()
        QTimer.singleShot(0, self.updateGeometry)


class WebDAVMappingPageMixin:
    """Build and manage named virtual WebDAV trees."""

    def _build_webdav_mapping_page(self) -> QWidget:
        page = _AdaptiveWebDAVPage()
        self.webdav_mapping_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(QLabel("WebDAV 自定义映射", objectName="title"))
        intro = QLabel(
            "把不同仓库中的文件和目录组合到独立挂载点；填写父目录即可自动生成中间层级。",
            objectName="subtitle",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.webdav_mapping_tabs = QTabWidget()
        self.webdav_mapping_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(8, 12, 8, 8)
        editor_layout.setSpacing(12)

        point_card = QFrame(objectName="card")
        point_layout = QGridLayout(point_card)
        point_layout.setContentsMargins(18, 15, 18, 16)
        point_layout.setHorizontalSpacing(10)
        point_layout.setVerticalSpacing(10)
        point_layout.addWidget(QLabel("新建挂载点", objectName="section"), 0, 0)
        self.webdav_new_mapping_name = QLineEdit()
        self.webdav_new_mapping_name.setPlaceholderText("例如：TEST")
        self.webdav_new_mapping_name.setMinimumWidth(150)
        self.webdav_new_mapping_name.textChanged.connect(self._update_webdav_mapping_preview)
        point_layout.addWidget(self.webdav_new_mapping_name, 0, 1)
        create_button = QPushButton("新建")
        create_button.clicked.connect(self._add_webdav_mapping)
        point_layout.addWidget(create_button, 0, 2)
        self.webdav_new_mapping_preview = QLabel(objectName="pathPill")
        self.webdav_new_mapping_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.webdav_new_mapping_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        point_layout.addWidget(self.webdav_new_mapping_preview, 0, 3, 1, 2)

        point_layout.addWidget(QLabel("选择挂载点", objectName="section"), 1, 0)
        self.webdav_mapping_combo = CleanComboBox()
        self.webdav_mapping_combo.setMinimumWidth(180)
        self.webdav_mapping_combo.currentIndexChanged.connect(self._webdav_mapping_selected)
        point_layout.addWidget(self.webdav_mapping_combo, 1, 1)
        rename_button = QPushButton("重命名当前")
        rename_button.clicked.connect(self._rename_webdav_mapping)
        point_layout.addWidget(rename_button, 1, 2)
        delete_button = QPushButton("删除当前")
        delete_button.clicked.connect(self._remove_webdav_mapping)
        point_layout.addWidget(delete_button, 1, 3)
        self.webdav_mapping_readonly = QCheckBox("只读")
        self.webdav_mapping_readonly.setChecked(True)
        self.webdav_mapping_readonly.toggled.connect(self._webdav_readonly_changed)
        point_layout.addWidget(self.webdav_mapping_readonly, 1, 4)
        point_layout.setColumnStretch(1, 1)
        point_layout.setColumnStretch(3, 2)
        editor_layout.addWidget(point_card)

        tree_card = QFrame(objectName="card")
        tree_layout = QVBoxLayout(tree_card)
        tree_layout.setContentsMargins(18, 15, 18, 16)
        tree_layout.setSpacing(9)
        tree_layout.addWidget(QLabel("当前挂载点的目录层级", objectName="section"))
        help_label = QLabel(
            "映射内位置填写父目录，例如 /test/1；挂载 Animation-List 后会出现在 /test/1/Animation-List。"
            "双击表格中的映射位置可以移动或重命名节点。",
            objectName="subtitle",
        )
        help_label.setWordWrap(True)
        tree_layout.addWidget(help_label)
        self.webdav_mapping_items = QTableWidget(0, 2)
        self.webdav_mapping_items.setHorizontalHeaderLabels(["映射内位置（可编辑）", "源节点"])
        item_header = self.webdav_mapping_items.horizontalHeader()
        item_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        item_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        item_header.setMinimumSectionSize(100)
        self.webdav_mapping_items.setColumnWidth(0, 300)
        self.webdav_mapping_items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.webdav_mapping_items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.webdav_mapping_items.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.webdav_mapping_items.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.webdav_mapping_items.itemChanged.connect(self._webdav_mapping_item_changed)
        tree_layout.addWidget(self.webdav_mapping_items, 1)
        item_actions = QHBoxLayout()
        for text, callback in (
            ("＋ 创建文件夹", self._add_webdav_folder),
            ("＋ 按路径挂载", self._add_webdav_mount),
            ("挂载当前资源节点", self._mount_current_remote_entry),
            ("－ 删除所选", self._remove_webdav_mapping_item),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            item_actions.addWidget(button)
        item_actions.addStretch()
        tree_layout.addLayout(item_actions)
        editor_layout.addWidget(tree_card, 1)
        self.webdav_mapping_tabs.addTab(editor_page, "编辑挂载点")

        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.setContentsMargins(8, 12, 8, 8)
        overview_layout.addWidget(QLabel(
            "展示全部挂载点及自动生成的目录层级。叶节点右侧显示实际源节点。",
            objectName="subtitle",
        ))
        self.webdav_mapping_overview = QTreeWidget()
        self.webdav_mapping_overview.setHeaderLabels(["挂载目录", "源节点 / 访问地址", "权限"])
        overview_header = self.webdav_mapping_overview.header()
        overview_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        overview_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        overview_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.webdav_mapping_overview.setColumnWidth(0, 330)
        self.webdav_mapping_overview.setUniformRowHeights(True)
        overview_layout.addWidget(self.webdav_mapping_overview, 1)
        self.webdav_mapping_tabs.addTab(overview_page, "全部挂载预览")
        layout.addWidget(self.webdav_mapping_tabs, 1)

        self._render_webdav_mappings()
        self._update_webdav_mapping_preview()
        return page

    def _save_webdav_mappings(self) -> None:
        self.settings.setValue("webdav/custom_mappings", dump_webdav_mappings(self.webdav_mappings))
        self.settings.sync()
        self._render_webdav_mapping_overview()
        if hasattr(self, "_refresh_webdav_drive_mount_points"):
            self._refresh_webdav_drive_mount_points()

    def _mapping_url(self, name: str) -> str:
        port = self.alist_port.value() if hasattr(self, "alist_port") else 9867
        return f"http://localhost:{port}/{name}"

    def _update_webdav_mapping_preview(self, *_args) -> None:
        if not hasattr(self, "webdav_new_mapping_preview"):
            return
        name = self.webdav_new_mapping_name.text().strip().strip("/\\")
        suffix = name or self._t("挂载点名称")
        self.webdav_new_mapping_preview.setText(self._mapping_url(suffix))

    def _render_webdav_mappings(self, select: int | None = None) -> None:
        if not hasattr(self, "webdav_mapping_combo"):
            return
        combo = self.webdav_mapping_combo
        current = combo.currentIndex() if select is None else select
        combo.blockSignals(True)
        combo.clear()
        for mapping in self.webdav_mappings:
            combo.addItem(f"/{mapping.name}", userData=mapping.name)
        combo.setCurrentIndex(min(max(0, current), len(self.webdav_mappings) - 1) if self.webdav_mappings else -1)
        combo.blockSignals(False)
        self._webdav_mapping_selected()
        self._render_webdav_mapping_overview()

    def _selected_webdav_mapping(self) -> WebDAVMapping | None:
        if not hasattr(self, "webdav_mapping_combo"):
            return None
        name = str(self.webdav_mapping_combo.currentData() or "")
        return next((item for item in self.webdav_mappings if item.name == name), None)

    def _webdav_mapping_selected(self, *_args) -> None:
        mapping = self._selected_webdav_mapping()
        self.webdav_mapping_readonly.blockSignals(True)
        self.webdav_mapping_readonly.setChecked(mapping.read_only if mapping else True)
        self.webdav_mapping_readonly.setEnabled(mapping is not None)
        self.webdav_mapping_readonly.blockSignals(False)
        self._render_webdav_mapping_items()

    def _render_webdav_mapping_items(self) -> None:
        if not hasattr(self, "webdav_mapping_items"):
            return
        mapping = self._selected_webdav_mapping()
        rows = []
        if mapping:
            rows.extend(("folder", path) for path in mapping.folders)
            rows.extend(("mount", mount) for mount in mapping.mounts)
            rows.sort(key=lambda item: (item[1] if item[0] == "folder" else item[1].target).casefold())
        self._webdav_mapping_item_rows = rows
        table = self.webdav_mapping_items
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for row, (kind, value) in enumerate(rows):
            target = value if kind == "folder" else value.target
            target_item = QTableWidgetItem("/" + target)
            target_item.setData(Qt.ItemDataRole.UserRole, target)
            table.setItem(row, 0, target_item)
            source = "（" + self._t("虚拟文件夹") + "）" if kind == "folder" else "/" + value.source
            source_item = QTableWidgetItem(source)
            source_item.setFlags(source_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            source_item.setToolTip(source_item.text())
            table.setItem(row, 1, source_item)
        table.blockSignals(False)

    def _render_webdav_mapping_overview(self) -> None:
        if not hasattr(self, "webdav_mapping_overview"):
            return
        tree = self.webdav_mapping_overview
        tree.clear()
        for mapping in sorted(self.webdav_mappings, key=lambda item: item.name.casefold()):
            root = QTreeWidgetItem([mapping.name, self._mapping_url(mapping.name), self._t("只读" if mapping.read_only else "可写")])
            root.setToolTip(1, root.text(1))
            tree.addTopLevelItem(root)
            nodes: dict[str, QTreeWidgetItem] = {"": root}
            sources = {mount.target: mount.source for mount in mapping.mounts}
            paths = set(mapping.folders) | set(sources)
            for path in sorted(paths, key=lambda value: (value.count("/"), value.casefold())):
                parent_path = ""
                for part in path.split("/"):
                    current_path = "/".join(value for value in (parent_path, part) if value)
                    if current_path not in nodes:
                        node = QTreeWidgetItem([part, "", ""])
                        nodes[parent_path].addChild(node)
                        nodes[current_path] = node
                    parent_path = current_path
                if path in sources:
                    nodes[path].setText(1, "/" + sources[path])
                    nodes[path].setToolTip(1, nodes[path].text(1))
            root.setExpanded(True)

    def _add_webdav_mapping(self) -> None:
        raw = self.webdav_new_mapping_name.text()
        try:
            name = validate_mapping_name(raw)
            if any(item.name.casefold() == name.casefold() for item in self.webdav_mappings):
                raise ValueError("已经存在同名挂载点")
        except ValueError as exc:
            QMessageBox.warning(self, self._t("挂载点名称无效"), self._t(str(exc)))
            return
        self.webdav_mappings.append(WebDAVMapping(name))
        self.webdav_new_mapping_name.clear()
        self._save_webdav_mappings()
        self._render_webdav_mappings(len(self.webdav_mappings) - 1)

    def _rename_webdav_mapping(self) -> None:
        mapping = self._selected_webdav_mapping()
        if mapping is None:
            return
        name, accepted = QInputDialog.getText(
            self, self._t("重命名挂载点"), self._t("新的挂载点名称："), text=mapping.name,
        )
        if not accepted:
            return
        try:
            name = validate_mapping_name(name)
            if any(item is not mapping and item.name.casefold() == name.casefold() for item in self.webdav_mappings):
                raise ValueError("已经存在同名挂载点")
        except ValueError as exc:
            QMessageBox.warning(self, self._t("挂载点名称无效"), self._t(str(exc)))
            return
        old_name = mapping.name
        mapping.name = name
        if hasattr(self, "_webdav_mapping_renamed"):
            self._webdav_mapping_renamed(old_name, name)
        index = self.webdav_mappings.index(mapping)
        self._save_webdav_mappings()
        self._render_webdav_mappings(index)

    def _remove_webdav_mapping(self) -> None:
        mapping = self._selected_webdav_mapping()
        if mapping is None:
            return
        answer = QMessageBox.question(
            self, self._t("删除挂载点"),
            self._tf("确定删除挂载点“{name}”及其全部节点吗？源仓库内容不会被删除。", name=mapping.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        index = self.webdav_mappings.index(mapping)
        old_name = mapping.name
        self.webdav_mappings.remove(mapping)
        if hasattr(self, "_webdav_mapping_removed"):
            self._webdav_mapping_removed(old_name)
        self._save_webdav_mappings()
        self._render_webdav_mappings(index - 1)

    def _webdav_readonly_changed(self, checked: bool) -> None:
        mapping = self._selected_webdav_mapping()
        if mapping is None:
            return
        if not checked and mapping.read_only:
            first = QMessageBox.warning(
                self, self._t("关闭只读保护"),
                self._t("关闭后，WebDAV 客户端可以向可写仓库上传文件。是否继续？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            second = QMessageBox.question(
                self, self._t("再次确认写入权限"),
                self._tf("确认允许挂载点“{name}”执行写入？公开池和无权限仓库仍会拒绝操作。", name=mapping.name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) if first == QMessageBox.StandardButton.Yes else QMessageBox.StandardButton.No
            if second != QMessageBox.StandardButton.Yes:
                self.webdav_mapping_readonly.blockSignals(True)
                self.webdav_mapping_readonly.setChecked(True)
                self.webdav_mapping_readonly.blockSignals(False)
                return
        mapping.read_only = bool(checked)
        self._save_webdav_mappings()

    def _webdav_mapping_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        mapping = self._selected_webdav_mapping()
        row = item.row()
        rows = getattr(self, "_webdav_mapping_item_rows", [])
        if mapping is None or not 0 <= row < len(rows):
            return
        kind, value = rows[row]
        old_target = value if kind == "folder" else value.target
        try:
            target = validate_virtual_target(item.text(), allow_empty=False)
            if kind == "mount":
                occupied = {path.casefold() for path in mapping.folders}
                occupied.update(mount.target.casefold() for mount in mapping.mounts if mount != value)
                if target.casefold() in occupied:
                    raise ValueError("该映射位置已经存在")
            else:
                old_key = old_target.casefold()
                old_parts = old_target.split("/")

                def under(path: str) -> bool:
                    key = path.casefold()
                    return key == old_key or key.startswith(old_key + "/")

                def moved(path: str) -> str:
                    return "/".join([target, *path.split("/")[len(old_parts):]])

                outside = {path.casefold() for path in mapping.folders if not under(path)}
                outside.update(mount.target.casefold() for mount in mapping.mounts if not under(mount.target))
                moved_targets = [moved(path) for path in mapping.folders if under(path)]
                moved_targets.extend(moved(mount.target) for mount in mapping.mounts if under(mount.target))
                moved_keys = [path.casefold() for path in moved_targets]
                if len(moved_keys) != len(set(moved_keys)) or any(key in outside for key in moved_keys):
                    raise ValueError("移动后会与已有映射位置冲突")
        except ValueError as exc:
            QMessageBox.warning(self, self._t("路径无效"), self._t(str(exc)))
            self._render_webdav_mapping_items()
            return
        if target == old_target:
            return
        if kind == "mount":
            mapping.mounts[mapping.mounts.index(value)] = WebDAVMount(target, value.source)
        else:
            mapping.folders = [moved(path) if under(path) else path for path in mapping.folders]
            mapping.mounts = [
                WebDAVMount(moved(mount.target), mount.source) if under(mount.target) else mount
                for mount in mapping.mounts
            ]
        self._save_webdav_mappings()
        self._render_webdav_mapping_items()

    def _add_webdav_folder(self) -> None:
        mapping = self._selected_webdav_mapping()
        if mapping is None:
            QMessageBox.information(self, self._t("需要挂载点"), self._t("请先新建或选择一个挂载点。"))
            return
        folder, accepted = QInputDialog.getText(
            self, self._t("创建虚拟文件夹"), self._t("映射内路径（可包含多级目录）："), text="/",
        )
        if not accepted:
            return
        try:
            folder = validate_virtual_target(folder, allow_empty=False)
            occupied = {path.casefold() for path in mapping.folders}
            occupied.update(mount.target.casefold() for mount in mapping.mounts)
            if folder.casefold() in occupied:
                raise ValueError("该映射位置已经存在")
        except ValueError as exc:
            QMessageBox.warning(self, self._t("路径无效"), self._t(str(exc)))
            return
        mapping.folders.append(folder)
        self._save_webdav_mappings()
        self._render_webdav_mapping_items()

    def _append_webdav_mount(self, source: str, suggested_name: str = "") -> None:
        mapping = self._selected_webdav_mapping()
        if mapping is None:
            QMessageBox.information(self, self._t("需要挂载点"), self._t("请先新建或选择一个挂载点。"))
            return
        parent, accepted = QInputDialog.getText(
            self, self._t("挂载节点"),
            self._t("映射内父目录（中间目录会自动生成）："), text="/",
        )
        if not accepted:
            return
        try:
            source = validate_source_path(source)
            parent = validate_virtual_target(parent, allow_empty=True)
            alias = validate_virtual_target(suggested_name or Path(source).name, allow_empty=False)
            if "/" in alias:
                raise ValueError("挂载节点名称不能包含目录分隔符")
            target = "/".join(value for value in (parent, alias) if value)
            occupied = {path.casefold() for path in mapping.folders}
            occupied.update(item.target.casefold() for item in mapping.mounts)
            if target.casefold() in occupied:
                raise ValueError("该映射位置已经存在")
        except ValueError as exc:
            QMessageBox.warning(self, self._t("路径无效"), self._t(str(exc)))
            return
        mapping.mounts.append(WebDAVMount(target, source))
        self._save_webdav_mappings()
        self._render_webdav_mapping_items()

    def _add_webdav_mount(self) -> None:
        source, accepted = QInputDialog.getText(
            self, self._t("按路径挂载"),
            self._t("源节点路径（/models、/datasets 或 /public 开头）："),
        )
        if accepted:
            name = Path(source.replace("\\", "/").rstrip("/")).name
            self._append_webdav_mount(source, name)

    def _current_webdav_source(self) -> tuple[str, str] | None:
        if not self.selected_repo:
            return None
        selected = self._selected_visible_remote_entries()
        entry = selected[0] if len(selected) == 1 else RemoteEntry(self.current_directory_path, is_dir=True)
        if self.selected_repo_public:
            mount = next((
                value for value in self.public_pool_store.mounts()
                if value.repo == self.selected_repo
                and (entry.path == value.root_path or entry.path.startswith(value.root_path.rstrip("/") + "/") or not value.root_path)
            ), None)
            if mount is None:
                return None
            relative = entry.path[len(mount.root_path):].strip("/") if mount.root_path else entry.path
            source = "/".join(part for part in ("public", mount.mount_name, relative) if part)
        else:
            category = "datasets" if self.selected_repo.repo_type == "dataset" else "models"
            source = "/".join(part for part in (category, self.selected_repo.repo_id, entry.path) if part)
        return source, Path(entry.path or self.selected_repo.repo_id).name

    def _matching_token_account_for_webdav(self):
        if getattr(self, "active_account_kind", None) != "web" or not self.selected_repo:
            return None
        raw_account_id = str(getattr(self, "active_account_id", "") or "").removeprefix("web:")
        web_account = next(
            (account for account in self.web_accounts if account.account_id == raw_account_id),
            None,
        )
        username = str(getattr(web_account, "username", "") or "").strip().casefold()
        if not username:
            return None
        for account in self.accounts:
            if str(getattr(account, "username", "") or "").strip().casefold() != username:
                continue
            service = self.account_services.get(account.account_id)
            if service is None or not str(getattr(service, "token", "") or ""):
                continue
            if any(
                repo.repo_type == self.selected_repo.repo_type and repo.repo_id == self.selected_repo.repo_id
                for repo in self.account_repositories.get(account.account_id, [])
            ):
                return account
        return None

    def _mount_current_remote_entry(self) -> None:
        if getattr(self, "active_account_kind", None) == "web":
            token_account = self._matching_token_account_for_webdav()
            if token_account is None:
                QMessageBox.information(
                    self,
                    self._t("请使用 Token 账户进行挂载"),
                    self._t("网页登录账户用于补全刚需的删除功能，请使用token登录账户进行挂载。"),
                )
                return
            QMessageBox.information(
                self,
                self._t("已自动切换 Token 挂载账户"),
                self._tf(
                    "检测到同一账户的 Token 登录，已自动使用 Token 账户“{name}”进行挂载。",
                    name=token_account.label or token_account.username,
                ),
            )
        current = self._current_webdav_source()
        if current is None:
            QMessageBox.information(
                self, self._t("没有可挂载节点"),
                self._t("请先在资源管理页打开仓库，并选中一个文件/文件夹或进入目标目录。"),
            )
            return
        self._append_webdav_mount(*current)

    def _remove_webdav_mapping_item(self) -> None:
        mapping = self._selected_webdav_mapping()
        row = self.webdav_mapping_items.currentRow()
        rows = getattr(self, "_webdav_mapping_item_rows", [])
        if mapping is None or not 0 <= row < len(rows):
            return
        kind, value = rows[row]
        if kind == "mount":
            mapping.mounts.remove(value)
        else:
            key = value.casefold()
            prefix = key + "/"
            mapping.folders = [
                path for path in mapping.folders
                if path.casefold() != key and not path.casefold().startswith(prefix)
            ]
            mapping.mounts = [
                mount for mount in mapping.mounts
                if mount.target.casefold() != key and not mount.target.casefold().startswith(prefix)
            ]
        self._save_webdav_mappings()
        self._render_webdav_mapping_items()
