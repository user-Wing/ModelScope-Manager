"""本地索引搜索与公共仓库搜索行为。"""

from __future__ import annotations

from PySide6.QtCore import QProcess, QThread, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidgetItem, QMenu, QMessageBox, QStyle, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote
from .app_helpers import MEDIA_EXTENSIONS, PUBLIC_ACCOUNT_ID, format_size, local_paths_size, repository_file_url, repository_identity, repository_is_public
from .app_workers import CopyThread, DeleteThread, RelocateThread, TaskThread, UploadThread
from .database import AccountRecord, IndexedEntry, classify_file, everything_search_match
from .image_bed import IMAGE_EXTENSIONS
from .fluent_ui import CleanComboBox
from .service import ModelScopeService, ModelScopeWebService, RemoteEntry, Repository, normalize_remote_path, parse_modelscope_repository_url, repository_directories
from .web_session import ModelScopeWebSession, delete_repository_file


class RepositorySearchMixin:
    """本地索引搜索与公共仓库搜索行为。"""

    @staticmethod
    def _indexed_search_sort_key(record: IndexedEntry, column: int) -> tuple:
        values = (
            record.name.casefold(), record.file_type.casefold(), record.size,
            record.repo_id.casefold(), record.path.casefold(),
        )
        return values[column], record.path.casefold()

    def show_resource_search(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("搜索已索引资源")
        dialog.resize(820, 520)
        layout = QVBoxLayout(dialog)
        filters = QHBoxLayout()
        scope = CleanComboBox()
        scope.addItem("全部仓库", userData="all")
        scope.addItem("当前账户", userData="account")
        scope.addItem("当前仓库", userData="repository")
        scope.addItem("当前目录", userData="directory")
        kind = CleanComboBox()
        kind.addItem("全部类型", userData="all")
        kind.addItem("视频", userData="video")
        kind.addItem("图片", userData="image")
        kind.addItem("文档", userData="document")
        kind.addItem("压缩包", userData="archive")
        tag = CleanComboBox()
        tag.addItem("全部标签", userData="")
        for value in self.account_store.all_tags():
            tag.addItem(value, userData=value)
        query = QLineEdit()
        query.setPlaceholderText("高级搜索：路径片段 文件名前段 后段")
        filters.addWidget(scope)
        filters.addWidget(kind)
        filters.addWidget(tag)
        filters.addWidget(query, 1)
        layout.addLayout(filters)
        result_count = QLabel("正在搜索本地索引…")
        layout.addWidget(result_count)
        results = QTreeWidget()
        results.setHeaderLabels(["名称", "类型", "大小", "仓库", "路径"])
        results.setRootIsDecorated(False)
        results.setUniformRowHeights(True)
        header = results.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.setStretchLastSection(True)
        layout.addWidget(results, 1)
        sort_column = 0
        sort_order = Qt.SortOrder.AscendingOrder
        search_generation = 0
        search_worker: TaskThread | None = None
        search_pending = False
        closing = False
        cached_records: list[IndexedEntry] = []
        render_index = 0
        search_timer = QTimer(dialog)
        search_timer.setSingleShot(True)
        search_timer.setInterval(220)
        render_timer = QTimer(dialog)
        render_timer.setInterval(0)

        def show_result_menu(position) -> None:
            item = results.itemAt(position)
            record = item.data(0, Qt.ItemDataRole.UserRole) if item else None
            if not isinstance(record, IndexedEntry):
                return
            service = ModelScopeService("", require_token=False) if record.account_id == PUBLIC_ACCOUNT_ID else self.account_services.get(record.account_id)
            if service is None:
                return
            repo = next((candidate for candidate in self.account_repositories.get(record.account_id, [])
                         if candidate.repo_type == record.repo_type and candidate.repo_id == record.repo_id), None)
            repo = repo or Repository(record.repo_id, record.repo_type, "public" if record.account_id == PUBLIC_ACCOUNT_ID else "")
            entries = [RemoteEntry(value.path, value.size, value.sha256, value.is_dir) for value in self.account_store.repository_entries(record.account_id, record.repo_type, record.repo_id)]
            entry = RemoteEntry(record.path, record.size, record.sha256, record.is_dir)
            self._show_remote_menu(results, position, entry, service, repo, entries or [entry], record.account_id)

        def search_parameters() -> tuple[str, str, str | None, str | None, str | None, str | None, str]:
            account_id = repo_type = repo_id = path_prefix = None
            selected_scope = str(scope.currentData())
            if selected_scope != "all":
                account_id = PUBLIC_ACCOUNT_ID if self.selected_repo_public else self.active_account_id
            if selected_scope in {"repository", "directory"} and self.selected_repo:
                repo_type, repo_id = self.selected_repo.repo_type, self.selected_repo.repo_id
            if selected_scope == "directory":
                path_prefix = self.current_directory_path
            return (
                query.text().strip(), str(kind.currentData()), account_id, repo_type, repo_id,
                path_prefix, str(tag.currentData() or ""),
            )

        def sort_key(record: IndexedEntry):
            return self._indexed_search_sort_key(record, sort_column)

        def render_next_chunk() -> None:
            nonlocal render_index
            records = cached_records[render_index:render_index + 400]
            if not records:
                render_timer.stop()
                return
            items = []
            for record in records:
                item = QTreeWidgetItem([record.name, record.file_type, format_size(record.size), record.repo_id, record.path])
                item.setData(0, Qt.ItemDataRole.UserRole, record)
                items.append(item)
            results.setUpdatesEnabled(False)
            results.addTopLevelItems(items)
            results.setUpdatesEnabled(True)
            render_index += len(records)
            if render_index >= len(cached_records):
                render_timer.stop()
                results.viewport().update()

        def render_results() -> None:
            nonlocal render_index
            render_timer.stop()
            cached_records.sort(
                key=sort_key,
                reverse=sort_order == Qt.SortOrder.DescendingOrder,
            )
            results.clear()
            render_index = 0
            result_count.setText(f"搜索结果：{len(cached_records)} 项")
            if cached_records:
                render_timer.start()

        def search_ready(records: list[IndexedEntry], generation: int) -> None:
            nonlocal cached_records
            if generation != search_generation or closing:
                return
            cached_records = records
            render_results()

        def search_failed(error: str, generation: int) -> None:
            if generation == search_generation and not closing:
                result_count.setText(f"搜索失败：{error}")

        def search_finished(worker: TaskThread) -> None:
            nonlocal search_worker, search_pending
            if search_worker is worker:
                search_worker = None
            if search_pending and not closing:
                search_pending = False
                QTimer.singleShot(0, start_search)

        def start_search() -> None:
            nonlocal search_worker, search_pending
            if search_worker and search_worker.isRunning():
                search_pending = True
                return
            parameters = search_parameters()
            generation = search_generation
            worker = TaskThread(lambda: self.account_store.search_entries(*parameters), dialog)
            worker.succeeded.connect(lambda records, value=generation: search_ready(records, value))
            worker.failed.connect(lambda error, value=generation: search_failed(error, value))
            worker.finished.connect(lambda value=worker: search_finished(value))
            worker.finished.connect(worker.deleteLater)
            search_worker = worker
            search_pending = False
            result_count.setText("正在搜索本地索引…")
            worker.start()

        def schedule_search() -> None:
            nonlocal search_generation
            search_generation += 1
            render_timer.stop()
            search_timer.start()

        def submit_search() -> None:
            search_timer.stop()
            start_search()

        def change_sort(column: int) -> None:
            nonlocal sort_column, sort_order
            sort_order = Qt.SortOrder.DescendingOrder if column == sort_column and sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
            sort_column = column
            header.setSortIndicator(column, sort_order)
            render_results()

        for control in (scope, kind, tag):
            control.currentIndexChanged.connect(schedule_search)
        query.textChanged.connect(schedule_search)
        query.returnPressed.connect(submit_search)
        results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        results.customContextMenuRequested.connect(show_result_menu)
        header.sectionClicked.connect(change_sort)
        search_timer.timeout.connect(start_search)
        render_timer.timeout.connect(render_next_chunk)
        schedule_search()
        dialog.exec()
        closing = True
        search_timer.stop()
        render_timer.stop()
        if search_worker and search_worker.isRunning():
            search_worker.requestInterruption()
            search_worker.wait(3000)

    def _schedule_global_search(self) -> None:
        self.global_search_generation += 1
        self.global_search_render_timer.stop()
        self.resource_search_timer.start()

    def _refresh_tag_filter(self) -> None:
        if not hasattr(self, "resource_search_tag"):
            return
        current = self.resource_search_tag.currentData()
        self.resource_search_tag.blockSignals(True)
        self.resource_search_tag.clear()
        self.resource_search_tag.addItem("全部标签", "")
        for tag in self.account_store.all_tags():
            self.resource_search_tag.addItem(tag, tag)
        index = self.resource_search_tag.findData(current)
        self.resource_search_tag.setCurrentIndex(max(0, index))
        self.resource_search_tag.blockSignals(False)

    def _set_global_search_visible(self, visible: bool) -> None:
        self.remote_tree.setVisible(not visible)
        self.resource_drop_hint.setVisible(not visible)
        self.remote_detail_tree.setVisible(not visible and self.resource_view_mode == "details")
        self.remote_thumbnail_list.setVisible(not visible and self.resource_view_mode == "thumbnails")
        self.global_search_tree.setVisible(visible)
        self.global_search_label.setVisible(visible)
        self._update_remote_selection_actions()

    def _perform_global_search(self) -> None:
        query = self.resource_search_edit.text().strip()
        file_type = str(self.resource_search_type.currentData() or "all")
        tag_name = str(self.resource_search_tag.currentData() or "")
        if not query and file_type == "all" and not tag_name:
            self.global_search_results = []
            self.global_search_tree.clear()
            self._set_global_search_visible(False)
            return
        scope = str(self.resource_search_scope.currentData() or "all")
        account_id = None
        repo_type = repo_id = None
        path_prefix = None
        if scope == "account":
            account_id = PUBLIC_ACCOUNT_ID if self.selected_repo_public else self.active_account_id
            if not account_id:
                self.global_search_results = []
                self.global_search_tree.clear()
                self.global_search_label.setText(self._t("请先选择搜索账户"))
                self._set_global_search_visible(True)
                return
        elif scope in {"repository", "directory"}:
            account_id = PUBLIC_ACCOUNT_ID if self.selected_repo_public else self.active_account_id
            if self.selected_repo:
                repo_type, repo_id = self.selected_repo.repo_type, self.selected_repo.repo_id
                if scope == "directory":
                    path_prefix = self.current_directory_path
            else:
                self.global_search_results = []
                self.global_search_tree.clear()
                self.global_search_label.setText(self._t("请先选择搜索仓库"))
                self._set_global_search_visible(True)
                return
        if self.global_search_task and self.global_search_task.isRunning():
            self.global_search_pending = True
            return
        generation = self.global_search_generation
        worker = TaskThread(
            lambda: self.account_store.search_entries(
                query, file_type, account_id, repo_type, repo_id, path_prefix, tag_name,
            ),
            self,
        )
        worker.succeeded.connect(
            lambda records, value=generation: self._global_search_ready(records, value)
        )
        worker.failed.connect(lambda error: self._log(f"索引搜索失败：{error}"))
        worker.finished.connect(lambda value=worker: self._global_search_finished(value))
        worker.finished.connect(worker.deleteLater)
        self.global_search_task = worker
        self.global_search_pending = False
        self.global_search_label.setText(self._t("正在搜索本地索引…"))
        self._set_global_search_visible(True)
        worker.start()

    def _global_search_finished(self, worker: TaskThread) -> None:
        if self.global_search_task is worker:
            self.global_search_task = None
        if self.global_search_pending:
            self.global_search_pending = False
            QTimer.singleShot(0, self._perform_global_search)

    def _global_search_ready(self, records: list[IndexedEntry], generation: int) -> None:
        if generation != self.global_search_generation:
            return
        self.global_search_results = records
        self._start_global_search_render()

    def _start_global_search_render(self) -> None:
        account_labels = {account.account_id: account.label for account in self.accounts}
        account_labels.update({self._web_account_key(account.account_id): account.label for account in self.web_accounts})
        account_labels[PUBLIC_ACCOUNT_ID] = "Public"
        self._global_search_account_labels = account_labels
        self._global_search_type_labels = {
            "video": self._t("视频"), "image": self._t("图片"),
            "document": self._t("文档"), "archive": self._t("压缩包"),
            "other": self._t("其他"),
        }
        self.global_search_results.sort(
            key=self._global_search_sort_key,
            reverse=self.global_search_sort_order == Qt.SortOrder.DescendingOrder,
        )
        self.global_search_tree.clear()
        self.global_search_render_index = 0
        self.global_search_label.setText(self._tf(
            "搜索结果：{count} 项", count=len(self.global_search_results)
        ))
        self._set_global_search_visible(True)
        if self.global_search_results:
            self.global_search_render_timer.start()

    def _render_global_search_chunk(self) -> None:
        start = self.global_search_render_index
        records = self.global_search_results[start:start + 400]
        if not records:
            self.global_search_render_timer.stop()
            return
        items: list[QTreeWidgetItem] = []
        for record in records:
            item = QTreeWidgetItem([
                record.name,
                self._global_search_type_labels.get(record.file_type, record.file_type),
                format_size(record.size),
                f"{self._global_search_account_labels.get(record.account_id, record.account_id)} · {record.repo_id}",
                record.path,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            items.append(item)
        self.global_search_tree.setUpdatesEnabled(False)
        self.global_search_tree.addTopLevelItems(items)
        self.global_search_tree.setUpdatesEnabled(True)
        self.global_search_render_index += len(records)
        if self.global_search_render_index >= len(self.global_search_results):
            self.global_search_render_timer.stop()
            self.global_search_tree.viewport().update()

    def _global_search_sort_key(self, record: IndexedEntry) -> tuple:
        return self._indexed_search_sort_key(record, self.global_search_sort_column)

    def _change_global_search_sort(self, column: int) -> None:
        self.global_search_sort_order = (
            Qt.SortOrder.DescendingOrder if column == self.global_search_sort_column and self.global_search_sort_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        self.global_search_sort_column = column
        self.global_search_tree.header().setSortIndicator(column, self.global_search_sort_order)
        self.global_search_render_timer.stop()
        self._start_global_search_render()

    def _global_search_context_menu(self, position) -> None:
        item = self.global_search_tree.itemAt(position)
        record = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(record, IndexedEntry):
            return
        service = self.account_services.get(record.account_id)
        if record.account_id == PUBLIC_ACCOUNT_ID:
            service = ModelScopeService("", require_token=False)
        elif not service:
            return
        repo = next((candidate for candidate in self.account_repositories.get(record.account_id, [])
                     if candidate.repo_type == record.repo_type and candidate.repo_id == record.repo_id), None)
        if repo is None:
            repo = Repository(record.repo_id, record.repo_type)
        entry = RemoteEntry(record.path, record.size, record.sha256, record.is_dir)
        indexed_entries = self.account_store.repository_entries(record.account_id, repo.repo_type, repo.repo_id)
        entries = [
            RemoteEntry(value.path, value.size, value.sha256, value.is_dir) for value in indexed_entries
        ] or [entry]
        self._show_remote_menu(self.global_search_tree, position, entry, service, repo, entries, record.account_id)

    def _open_global_search_result(self, item: QTreeWidgetItem, column: int = 0) -> None:
        record = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(record, IndexedEntry):
            return
        service = self.account_services.get(record.account_id)
        public = record.account_id == PUBLIC_ACCOUNT_ID
        if public:
            service = ModelScopeService("", require_token=False)
        elif not service:
            return
        repo = next((candidate for candidate in ([] if public else self.account_repositories.get(record.account_id, []))
                     if candidate.repo_type == record.repo_type and candidate.repo_id == record.repo_id), None)
        if repo is None:
            repo = Repository(record.repo_id, record.repo_type)
        self.active_account_id = None if public else record.account_id
        self.active_account_kind = "public" if public else ("web" if record.account_id.startswith("web:") else "token")
        self.service = service
        self.selected_repo = repo
        self.selected_repo_public = public
        self.pending_search_path = record.path
        self.resource_search_edit.clear()
        self.resource_search_type.setCurrentIndex(0)
        self._set_global_search_visible(False)
        iterator = QTreeWidgetItemIterator(self.repo_list)
        self.repo_list.blockSignals(True)
        while iterator.value():
            candidate = iterator.value()
            data = candidate.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and len(data) == 2 and data[0] == ("public" if public else record.account_id) and data[1] == repo:
                self.repo_list.setCurrentItem(candidate)
                break
            iterator += 1
        self.repo_list.blockSignals(False)
        self.repo_heading.setText(f"{repo.repo_id} · {repo.repo_type}")
        self.load_remote_files()

    def load_public_resource(self) -> None:
        if self.task and self.task.isRunning():
            QMessageBox.information(self, self._t("请稍候"), self._t("当前操作完成后再加载公开资源。"))
            return
        try:
            search_url = self.search_url_edit.currentText().strip()
            repo = parse_modelscope_repository_url(search_url)
        except ValueError as exc:
            QMessageBox.warning(self, self._t("链接无效"), str(exc))
            return
        self.search_heading.setText(self._tf("正在读取 {repo}…", repo=repo.repo_id))
        self.search_load_button.setEnabled(False)

        def action():
            service = ModelScopeService("", require_token=False)
            return service, repo, service.list_entries(repo), search_url

        worker = TaskThread(action, self)
        worker.succeeded.connect(self._public_resource_loaded)
        worker.failed.connect(self._public_resource_failed)
        worker.finished.connect(lambda: self._public_resource_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.task = worker
        worker.start()

    def _public_resource_loaded(self, result: tuple[ModelScopeService, Repository, list[RemoteEntry], str]) -> None:
        self.search_service, self.search_repo, self.search_entries, search_url = result
        self.public_pool_store.add(search_url, self.search_repo)
        if self.webdav:
            self.webdav.refresh_public_pools()
        self.account_store.cache_entries(PUBLIC_ACCOUNT_ID, self.search_repo, self.search_entries)
        self.folder_index.update_repository(self.search_repo, self.search_entries, True)
        self._render_public_history()
        self._render_repositories()
        self._refresh_tag_filter()
        self.search_url_edit.setEditText(search_url)
        self.public_file_search_edit.blockSignals(True)
        self.public_file_search_edit.clear()
        self.public_file_search_edit.blockSignals(False)
        self._render_public_search_results()
        count = len(self.search_entries)
        self.search_heading.setText(self._tf("{repo} · 已读取 {count} 项", repo=self.search_repo.repo_id, count=count))
        self._log(f"已读取公开资源 {self.search_repo.repo_id}，共 {count} 项")

    def _public_search_sort_key(self, entry: RemoteEntry) -> tuple:
        name = entry.path.rsplit("/", 1)[-1]
        size = self.folder_index.cached_folder_size(self.search_repo, entry.path, True) if entry.is_dir and self.search_repo else entry.size
        extension, file_type = classify_file(entry.path, entry.is_dir)
        values = (
            name.casefold(),
            (file_type, extension, name.casefold()),
            (size is None, size or 0),
            entry.path.casefold(),
        )
        return (values[self.public_search_sort_column], entry.path.casefold())

    def _change_public_search_sort(self, column: int) -> None:
        self.public_search_sort_order = (
            Qt.SortOrder.DescendingOrder
            if column == self.public_search_sort_column and self.public_search_sort_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        self.public_search_sort_column = column
        self.search_remote_tree.header().setSortIndicator(column, self.public_search_sort_order)
        self._sync_public_search_sort_controls()
        self._render_public_search_results()

    def _public_search_sort_combo_changed(self, _index: int = -1) -> None:
        column = int(self.public_search_sort_combo.currentData() or 0)
        if column == self.public_search_sort_column:
            return
        self.public_search_sort_column = column
        self.search_remote_tree.header().setSortIndicator(column, self.public_search_sort_order)
        self._render_public_search_results()

    def _toggle_public_search_direction(self) -> None:
        self.public_search_sort_order = (
            Qt.SortOrder.DescendingOrder
            if self.public_search_sort_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        self.search_remote_tree.header().setSortIndicator(
            self.public_search_sort_column, self.public_search_sort_order,
        )
        self._sync_public_search_sort_controls()
        self._render_public_search_results()

    def _sync_public_search_sort_controls(self) -> None:
        index = self.public_search_sort_combo.findData(self.public_search_sort_column)
        self.public_search_sort_combo.blockSignals(True)
        self.public_search_sort_combo.setCurrentIndex(max(0, index))
        self.public_search_sort_combo.blockSignals(False)
        self.public_search_direction_button.setText(
            "升序" if self.public_search_sort_order == Qt.SortOrder.AscendingOrder else "降序"
        )

    def _render_public_search_results(self) -> None:
        if not hasattr(self, "search_remote_tree"):
            return
        query = self.public_file_search_edit.text().strip() if hasattr(self, "public_file_search_edit") else ""
        matched = [
            entry for entry in self.search_entries
            if everything_search_match(entry.path, entry.is_dir, query)
        ]
        entries = sorted(
            matched,
            key=self._public_search_sort_key,
            reverse=self.public_search_sort_order == Qt.SortOrder.DescendingOrder,
        )
        self.search_remote_tree.setUpdatesEnabled(False)
        self.search_remote_tree.clear()
        items: list[QTreeWidgetItem] = []
        type_labels = {
            "folder": self._t("文件夹"), "video": self._t("视频"), "image": self._t("图片"),
            "document": self._t("文档"), "archive": self._t("压缩包"), "other": self._t("其他"),
        }
        for entry in entries:
            name = entry.path.rsplit("/", 1)[-1]
            size = self.folder_index.cached_folder_size(self.search_repo, entry.path, True) if entry.is_dir and self.search_repo else entry.size
            extension, file_type = classify_file(entry.path, entry.is_dir)
            kind = type_labels[file_type]
            if extension:
                kind = f"{kind} · {extension.lstrip('.').upper()}"
            item = QTreeWidgetItem([name, kind, format_size(size) if size is not None else "--", entry.path])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            items.append(item)
        self.search_remote_tree.addTopLevelItems(items)
        self.search_remote_tree.setUpdatesEnabled(True)
        self.search_remote_tree.viewport().update()
        self.search_remote_tree.scrollToTop()
        if hasattr(self, "public_search_count_label"):
            self.public_search_count_label.setText(
                f"显示 {len(entries)} / {len(self.search_entries)} 项"
                + (" · 空格分隔的条件需全部匹配" if query else "")
            )

    def _public_resource_failed(self, error: str) -> None:
        self.search_heading.setText(self._t("公开资源加载失败"))
        self._log(f"公开资源加载失败：{error}")
        QMessageBox.warning(self, self._t("加载失败"), error)

    def _public_resource_finished(self, worker: QThread) -> None:
        if self.task is worker:
            self.task = None
        self.search_load_button.setEnabled(True)
        self._update_upload_enabled()
        self._update_download_enabled()
