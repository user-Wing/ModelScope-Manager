"""本地索引搜索与公共仓库搜索行为。"""

from __future__ import annotations

from __future__ import annotations

from PySide6.QtCore import QProcess, QThread, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QListWidgetItem, QMenu, QMessageBox, QStyle, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote
from .app_helpers import MEDIA_EXTENSIONS, PUBLIC_ACCOUNT_ID, format_size, local_paths_size, repository_file_url, repository_identity, repository_is_public
from .app_workers import CopyThread, DeleteThread, RelocateThread, TaskThread, UploadThread
from .database import AccountRecord, IndexedEntry, classify_file, everything_search_match
from .image_bed import IMAGE_EXTENSIONS
from .service import ModelScopeService, ModelScopeWebService, RemoteEntry, Repository, normalize_remote_path, parse_modelscope_repository_url, repository_directories
from .web_session import ModelScopeWebSession, delete_repository_file


class RepositorySearchMixin:
    """本地索引搜索与公共仓库搜索行为。"""

    def _schedule_global_search(self) -> None:
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
        self.global_search_results = self.account_store.search_entries(
            query, file_type, account_id, repo_type, repo_id, path_prefix, tag_name
        )
        account_labels = {account.account_id: account.label for account in self.accounts}
        account_labels.update({self._web_account_key(account.account_id): account.label for account in self.web_accounts})
        account_labels[PUBLIC_ACCOUNT_ID] = "Public"
        type_labels = {
            "video": self._t("视频"), "image": self._t("图片"),
            "document": self._t("文档"), "archive": self._t("压缩包"),
            "other": self._t("其他"),
        }
        self.global_search_results.sort(
            key=self._global_search_sort_key,
            reverse=self.global_search_sort_order == Qt.SortOrder.DescendingOrder,
        )
        self.global_search_tree.clear()
        for record in self.global_search_results:
            item = QTreeWidgetItem([
                record.name,
                type_labels.get(record.file_type, record.file_type),
                format_size(record.size),
                f"{account_labels.get(record.account_id, record.account_id)} · {record.repo_id}",
                record.path,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            self.global_search_tree.addTopLevelItem(item)
        self.global_search_label.setText(self._tf(
            "搜索结果：{count} 项", count=len(self.global_search_results)
        ))
        self._set_global_search_visible(True)

    def _global_search_sort_key(self, record: IndexedEntry) -> tuple:
        values = (
            record.name.casefold(),
            record.file_type.casefold(),
            record.size,
            record.repo_id.casefold(),
            record.path.casefold(),
        )
        return (values[self.global_search_sort_column], record.path.casefold())

    def _change_global_search_sort(self, column: int) -> None:
        self.global_search_sort_order = (
            Qt.SortOrder.DescendingOrder if column == self.global_search_sort_column and self.global_search_sort_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        self.global_search_sort_column = column
        self.global_search_tree.header().setSortIndicator(column, self.global_search_sort_order)
        self._perform_global_search()

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
