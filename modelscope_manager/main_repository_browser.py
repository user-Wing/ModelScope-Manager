"""仓库连接、目录浏览与资源视图行为。"""

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


class RepositoryBrowserMixin:
    """仓库连接、目录浏览与资源视图行为。"""

    def _log(self, message: str) -> None:
        translated = self._t(message)
        self.log.append(translated)
        if hasattr(self, "status_bar"):
            self.status_bar.showMessage(translated, 5000)

    def _busy(self, value: bool, message: str = "") -> None:
        self.connect_button.setEnabled(not value)
        self.refresh_repos_button.setEnabled(not value)
        self.refresh_files_button.setEnabled(not value and self.selected_repo is not None)
        if value:
            self.repo_heading.setText(self._t(message or "正在处理…"))

    def _update_resource_upload_actions(self) -> None:
        writable = bool(
            self.selected_repo
            and not self.selected_repo_public
            and self._token_service_for_repo(self.selected_repo) is not None
        )
        self.upload_file_button.setEnabled(writable)
        self.upload_folder_button.setEnabled(writable)

    def _run_task(self, action: Callable[[], Any], success: Callable[[Any], None], label: str) -> None:
        self._busy(True, label)
        worker = TaskThread(action, self)
        worker.succeeded.connect(success)
        worker.failed.connect(self._task_failed)
        worker.finished.connect(lambda: self._task_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.task = worker
        worker.start()

    def _task_finished(self, worker: QThread) -> None:
        if self.task is worker:
            self.task = None
        self._busy(False)
        self._update_upload_enabled()
        self._update_download_enabled()

    def _task_failed(self, error: str) -> None:
        self.repo_heading.setText(self._t("操作失败"))
        if self.service is None:
            self.account_label.setText(self._t("令牌验证失败"))
            self.account_label.setObjectName("error")
            self.account_label.style().unpolish(self.account_label)
            self.account_label.style().polish(self.account_label)
        self._log(f"失败：{error}")
        QMessageBox.warning(self, self._t("操作失败"), error)

    def connect_account(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.information(self, self._t("需要令牌"), self._t("请输入 ModelScope Access Token。"))
            return
        self.account_label.setText(self._t("正在验证令牌…"))
        account_id = self._selected_account_id() or ""
        label = self.account_name_edit.text().strip()
        remember = self.remember_token.isChecked()

        def action():
            service = ModelScopeService(token)
            return service, service.verify(), account_id, label, token, remember

        self._run_task(action, self._connected, "正在验证令牌…")

    def _connected(self, result: tuple[ModelScopeService, str, str, str, str, bool]) -> None:
        service, username, account_id, label, token, remember = result
        account = next((item for item in self.accounts if item.account_id == account_id), None)
        if account is None:
            account = AccountRecord(account_id, label or username, username, token, remember, True, "connected")
            self.accounts.append(account)
        else:
            account.label = label or account.label or username
            account.username = username
            account.token = token
            account.remember = remember
            account.status = "connected"
        try:
            self.account_store.save(account)
        except Exception as exc:
            if not remember:
                raise
            account.remember = False
            self.account_store.save(account)
            QMessageBox.warning(
                self, self._t("令牌已连接"),
                self._t("当前用户 DPAPI 不可用，Token 仅保留在本次运行内存中，不会写入磁盘。"),
            )
            self._log(f"Token 安全持久化失败，已降级为仅本次运行：{exc}")
        self.session_tokens[account.account_id] = token
        self.account_services[account.account_id] = service
        self.active_account_id = account.account_id
        self.active_account_kind = "token"
        self.service = service
        self._render_accounts()
        for row in range(self.account_table.rowCount()):
            if self.account_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == account.account_id:
                self.account_table.selectRow(row)
                break
        self.account_label.setText(self._tf("已连接：{username}", username=username))
        self.account_label.setObjectName("success")
        self.account_label.style().unpolish(self.account_label)
        self.account_label.style().polish(self.account_label)
        self._log(f"令牌验证成功，账户：{username}")
        self.refresh_repos_button.setEnabled(True)
        if self.webdav or self.alist_auto_start.isChecked():
            self.apply_alist_settings()
        self._navigate(0)
        QTimer.singleShot(0, self.load_repositories)

    def load_repositories(self) -> None:
        account_tokens = {
            account.account_id: self.session_tokens.get(account.account_id, account.token)
            for account in self.accounts
            if account.enabled and self.session_tokens.get(account.account_id, account.token)
        }
        web_sessions = {
            self._web_account_key(account.account_id): session
            for account in self.web_accounts
            if (session := self._web_session_for_key(self._web_account_key(account.account_id))) is not None
        }
        if not account_tokens and not web_sessions:
            self._prompt_for_settings()
            return

        def action():
            successes = []
            failures = []
            for account_id, token in account_tokens.items():
                try:
                    service = ModelScopeService(token)
                    username = service.verify()
                    repos = service.list_repositories()
                    successes.append((account_id, service, username, repos))
                except Exception as exc:
                    failures.append((account_id, str(exc)))
            for account_key, session in web_sessions.items():
                try:
                    service = ModelScopeWebService(session)
                    username = service.verify()
                    repos = service.list_repositories()
                    successes.append((account_key, service, username, repos))
                except Exception as exc:
                    failures.append((account_key, str(exc)))
            return successes, failures

        self._run_task(action, self._repositories_loaded, "正在读取所有账户仓库…")

    def _repositories_loaded(self, result) -> None:
        successes, failures = result
        self.account_services.clear()
        self.account_repositories.clear()
        total = 0
        for account_id, service, username, repos in successes:
            self.account_services[account_id] = service
            self.account_repositories[account_id] = repos
            total += len(repos)
            account = next((item for item in self.accounts if item.account_id == account_id), None)
            if account:
                account.username = username
                account.status = "connected"
                account.token = self.session_tokens.get(account_id, account.token)
                self.account_store.save(account)
            elif account_id.startswith("web:"):
                raw_id = account_id.removeprefix("web:")
                web_account = next((item for item in self.web_accounts if item.account_id == raw_id), None)
                if web_account:
                    web_account.username = username
                    web_account.status = "connected"
                    self.account_store.save_web_account(web_account)
            self.account_store.cache_repositories(account_id, repos)
        for account_id, error in failures:
            account = next((item for item in self.accounts if item.account_id == account_id), None)
            if account:
                account.status = "failed"
            web_account = None
            if account_id.startswith("web:"):
                raw_id = account_id.removeprefix("web:")
                web_account = next((item for item in self.web_accounts if item.account_id == raw_id), None)
                if web_account:
                    web_account.status = "failed"
            self._log(f"账户 {(account or web_account).label if (account or web_account) else account_id} 验证失败：{error}")
        self.repositories = [repo for repos in self.account_repositories.values() for repo in repos]
        if self.active_account_id not in self.account_services and successes:
            self.active_account_id = successes[0][0]
        self.active_account_kind = (
            "web" if str(self.active_account_id or "").startswith("web:")
            else "token" if self.active_account_id else None
        )
        self.service = self.account_services.get(self.active_account_id) if self.active_account_id else None
        self._render_accounts()
        self._render_web_accounts()
        self._render_repositories()
        self._render_backup_account_options()
        self._render_backup_jobs()
        self._render_image_account_options()
        self.account_label.setText(self._tf("已连接 · {accounts} 个账户 · {count} 个仓库", accounts=len(successes), count=total))
        self.account_label.setObjectName("success")
        self.account_label.style().unpolish(self.account_label)
        self.account_label.style().polish(self.account_label)
        self.repo_heading.setText(self._tf("已读取 {count} 个仓库，请选择一个仓库", count=total))
        self._log(f"已读取 {len(successes)} 个账户、{total} 个模型/数据集仓库")
        self._start_folder_indexing(True)
        if self.webdav or self.alist_auto_start.isChecked():
            self.apply_alist_settings()

    def _render_repositories(self) -> None:
        if not hasattr(self, "repo_list"):
            return
        selected_type = self.type_combo.currentData()
        self.repo_list.clear()
        labels = {"model": self._t("模型"), "dataset": self._t("数据集")}
        public_repos = self.public_pool_store.repositories()
        public_node = QTreeWidgetItem(["Public"])
        public_node.setData(0, Qt.ItemDataRole.UserRole, ("public", ""))
        public_node.setExpanded(True)
        self.repo_list.addTopLevelItem(public_node)
        for repo_type in ("model", "dataset"):
            if selected_type != "all" and repo_type != selected_type:
                continue
            matching = [repo for repo in public_repos if repo.repo_type == repo_type]
            if not matching:
                continue
            group = QTreeWidgetItem([labels[repo_type]])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group.setExpanded(True)
            public_node.addChild(group)
            for repo in matching:
                child = QTreeWidgetItem([repo.repo_id])
                child.setData(0, Qt.ItemDataRole.UserRole, ("public", repo))
                child.setToolTip(0, f"{repo.repo_type} · public · 只读")
                group.addChild(child)

        separator = QTreeWidgetItem(["────────────────────────"])
        separator.setFlags(separator.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.repo_list.addTopLevelItem(separator)
        token_heading = QTreeWidgetItem(["Token 登录账户（删除/移动/重命名不可用）"])
        token_heading.setFlags(token_heading.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        token_heading.setToolTip(0, "请使用在线登录列表执行")
        self.repo_list.addTopLevelItem(token_heading)
        for account in self.accounts:
            repos = self.account_repositories.get(account.account_id, [])
            account_node = QTreeWidgetItem([account.label or account.username])
            account_node.setData(0, Qt.ItemDataRole.UserRole, ("account", account.account_id))
            account_node.setExpanded(True)
            self.repo_list.addTopLevelItem(account_node)
            for repo_type in ("model", "dataset"):
                if selected_type != "all" and repo_type != selected_type:
                    continue
                matching = [repo for repo in repos if repo.repo_type == repo_type]
                group = QTreeWidgetItem([labels[repo_type]])
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                group.setExpanded(True)
                account_node.addChild(group)
                for repo in matching:
                    child = QTreeWidgetItem([repo.repo_id])
                    child.setData(0, Qt.ItemDataRole.UserRole, (account.account_id, repo))
                    child.setToolTip(0, f"{repo.repo_type} · {repo.visibility}")
                    group.addChild(child)

        separator = QTreeWidgetItem(["────────────────────────"])
        separator.setFlags(separator.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.repo_list.addTopLevelItem(separator)
        web_heading = QTreeWidgetItem(["网页登录账户（删除/移动/重命名支持）"])
        web_heading.setFlags(web_heading.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.repo_list.addTopLevelItem(web_heading)
        for account in self.web_accounts:
            account_key = self._web_account_key(account.account_id)
            repos = self.account_repositories.get(account_key, [])
            account_node = QTreeWidgetItem([account.label or account.username])
            account_node.setData(0, Qt.ItemDataRole.UserRole, ("web-account", account.account_id))
            account_node.setExpanded(True)
            self.repo_list.addTopLevelItem(account_node)
            for repo_type in ("model", "dataset"):
                if selected_type != "all" and repo_type != selected_type:
                    continue
                matching = [repo for repo in repos if repo.repo_type == repo_type]
                group = QTreeWidgetItem([labels[repo_type]])
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                group.setExpanded(True)
                account_node.addChild(group)
                for repo in matching:
                    child = QTreeWidgetItem([repo.repo_id])
                    child.setData(0, Qt.ItemDataRole.UserRole, (account_key, repo))
                    child.setToolTip(0, f"{repo.repo_type} · {repo.visibility} · 网页登录可写")
                    group.addChild(child)

    def _repo_selected(self) -> None:
        items = self.repo_list.selectedItems()
        if not items:
            return
        selected = items[0].data(0, Qt.ItemDataRole.UserRole)
        if isinstance(selected, RemoteEntry) and selected.is_dir:
            iterator = QTreeWidgetItemIterator(self.remote_tree)
            while iterator.value():
                candidate = iterator.value()
                entry = candidate.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(entry, RemoteEntry) and entry.path == selected.path:
                    self.remote_tree.setCurrentItem(candidate)
                    return
                iterator += 1
            return
        if not (isinstance(selected, tuple) and len(selected) == 2 and isinstance(selected[1], Repository)):
            return
        account_id, repo = selected
        public = account_id == "public"
        service = ModelScopeService("", require_token=False) if public else self.account_services.get(account_id)
        if service is None:
            return
        self.active_account_id = None if public else account_id
        self.active_account_kind = "public" if public else ("web" if str(account_id).startswith("web:") else "token")
        self.service = service
        self.selected_repo = repo
        self.selected_repo_public = public
        self.remote_detail_tree.clearSelection()
        self.remote_thumbnail_list.clearSelection()
        self.repo_heading.setText(f"{repo.repo_id} · {repo.repo_type}")
        self.directory_history.clear()
        self._set_current_directory("", remember=False)
        self.refresh_files_button.setEnabled(True)
        self.new_folder_button.setEnabled(not public)
        self._update_resource_upload_actions()
        self._update_upload_enabled()
        self.load_remote_files()

    def _populate_repository_directories(self, entries: list[RemoteEntry]) -> None:
        """Attach the selected repository's directory navigation to the left tree only."""
        selected_items = self.repo_list.selectedItems()
        repo_item = selected_items[0] if selected_items else None
        if repo_item is None:
            return
        data = repo_item.data(0, Qt.ItemDataRole.UserRole)
        if not (isinstance(data, tuple) and len(data) == 2 and isinstance(data[1], Repository)):
            return
        while repo_item.childCount():
            repo_item.removeChild(repo_item.child(0))
        root = QTreeWidgetItem(["根目录"])
        root.setData(0, Qt.ItemDataRole.UserRole, RemoteEntry("", is_dir=True))
        repo_item.addChild(root)
        nodes = {"": root}
        for folder in sorted(repository_directories(entry.path for entry in entries), key=lambda value: (value.count("/"), value.lower())):
            parent_path, _, name = folder.rpartition("/")
            parent = nodes.get(parent_path)
            if parent is None:
                continue
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.ItemDataRole.UserRole, RemoteEntry(folder, is_dir=True))
            parent.addChild(item)
            nodes[folder] = item
        repo_item.setExpanded(True)
        root.setExpanded(True)

    def _repository_context_menu(self, position) -> None:
        item = self.repo_list.itemAt(position)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(data, RemoteEntry) and data.is_dir and self.service and self.selected_repo:
            self._show_remote_menu(
                self.repo_list, position, data, self.service, self.selected_repo, self.remote_entries,
                PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or ""),
            )
            return
        if not (isinstance(data, tuple) and len(data) == 2 and isinstance(data[1], Repository)):
            return
        account_id, repo = data
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        open_action = menu.addAction("打开网页端")
        remove_action = menu.addAction("移除") if account_id == "public" else None
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        move_action = menu.addAction("移动")
        rename_action = menu.addAction("重命名（区分大小写）")
        tooltip = (
            "这是只读" if account_id == "public"
            else "请在仓库内选择文件或文件夹" if str(account_id).startswith("web:")
            else "请使用在线登录列表执行"
        )
        for action in (delete_action, move_action, rename_action):
            action.setEnabled(False)
            action.setToolTip(tooltip)
        chosen = menu.exec(self.repo_list.viewport().mapToGlobal(position))
        if chosen is open_action:
            QDesktopServices.openUrl(QUrl(self._repository_web_url(repo)))
        elif remove_action is not None and chosen is remove_action:
            self._remove_public_repository(repo)

    def load_remote_files(self) -> None:
        if not self.service:
            self._prompt_for_settings()
            return
        if not self.selected_repo:
            return
        repo = self.selected_repo
        if self.selected_repo_public:
            cached = self.account_store.repository_entries(PUBLIC_ACCOUNT_ID, repo.repo_type, repo.repo_id)
            if cached:
                self._files_loaded([
                    RemoteEntry(entry.path, entry.size, entry.sha256, entry.is_dir) for entry in cached
                ])
                return
        self._run_task(lambda: self.service.list_entries(repo), self._files_loaded, "正在读取仓库目录…")

    def _files_loaded(self, entries: list[RemoteEntry], persist: bool = True) -> None:
        self.remote_entries = entries
        self.remote_direct_cache.clear()
        self._reset_thumbnail_queue(entries)
        if self.selected_repo and persist:
            self.account_store.cache_entries(
                PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or ""),
                self.selected_repo,
                entries,
            )
        if self.selected_repo:
            self._refresh_tag_filter()
        paths = self._populate_remote_tree(
            self.remote_tree, entries, self.selected_repo, self.selected_repo_public,
        )
        self._populate_repository_directories(entries)
        self._select_remote_directory(self.current_directory_path)
        self._render_remote_details()
        if self.selected_repo:
            self.repo_heading.setText(self._tf("{repo} · 已读取 {count} 项", repo=self.selected_repo.repo_id, count=len(paths)))
        self._log(f"仓库目录已刷新，共 {len(paths)} 项")
        if self.pending_search_path:
            pending = self.pending_search_path
            self.pending_search_path = ""
            directories = repository_directories(entry.path for entry in entries)
            self._select_remote_directory(pending if pending in directories else pending.rpartition("/")[0])

    def _populate_remote_tree(
        self,
        tree: QTreeWidget,
        entries: list[RemoteEntry],
        repo: Repository | None = None,
        public: bool = False,
    ) -> list[str]:
        paths = [entry.path for entry in entries]
        tree.clear()
        root_size = self.folder_index.cached_folder_size(repo, "", public) if repo else None
        root = QTreeWidgetItem([
            self._t("根目录"), self._t("文件夹"),
            format_size(root_size) if root_size is not None else "--", "/",
        ])
        root.setData(0, Qt.ItemDataRole.UserRole, RemoteEntry("", is_dir=True))
        root.setExpanded(True)
        tree.addTopLevelItem(root)
        nodes: dict[str, QTreeWidgetItem] = {"": root}
        directory_paths = repository_directories(paths)
        for folder in sorted(directory_paths, key=lambda path: (path.count("/"), path.casefold())):
            parent_path, _, name = folder.rpartition("/")
            parent = nodes.get(parent_path)
            if parent is None:
                continue
            cached_size = self.folder_index.cached_folder_size(repo, folder, public) if repo else None
            item = QTreeWidgetItem([
                name, self._t("文件夹"), format_size(cached_size) if cached_size is not None else "--", folder,
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, RemoteEntry(folder, is_dir=True))
            parent.addChild(item)
            nodes[folder] = item
        tree.resizeColumnToContents(1)
        return paths

    def _remote_selected(self) -> None:
        items = self.remote_tree.selectedItems()
        if not items:
            return
        entry = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteEntry):
            return
        if entry.is_dir:
            target = entry.path
        else:
            target = entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
        self._set_current_directory(target)
        self._render_remote_details()
        self._schedule_visible_thumbnails()

    def _render_remote_details(self) -> None:
        if not hasattr(self, "remote_detail_tree"):
            return
        folder = self.current_directory_path
        cached_direct = self.remote_direct_cache.get(folder)
        if cached_direct is None:
            cached_direct = self._direct_remote_entries(self.remote_entries, folder)
            self.remote_direct_cache[folder] = cached_direct
        direct = list(cached_direct)
        self.remote_detail_tree.setUpdatesEnabled(False)
        self.remote_detail_tree.clear()
        if self.resource_view_mode == "thumbnails":
            self.remote_thumbnail_list.setUpdatesEnabled(False)
            self.remote_thumbnail_list.clear()
        group_by = str(self.group_by_combo.currentData() or "") if hasattr(self, "group_by_combo") else ""
        self.remote_detail_tree.setRootIsDecorated(bool(group_by))
        parents: dict[str, QTreeWidgetItem] = {}
        direct.sort(key=self._detail_sort_key, reverse=self.detail_sort_order == Qt.SortOrder.DescendingOrder)
        for entry in direct:
            name = entry.path.rsplit("/", 1)[-1]
            size = self.folder_index.cached_folder_size(self.selected_repo, entry.path, self.selected_repo_public) if entry.is_dir and self.selected_repo else entry.size
            item = QTreeWidgetItem([
                f"\u2002{name}",
                self._t("文件夹") if entry.is_dir else self._t("文件"),
                format_size(size) if size is not None else "--",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            if group_by:
                key = name[:1].upper() if group_by == "name" else ("文件夹" if entry.is_dir else Path(name).suffix.lower() or "文件") if group_by == "type" else self._size_group(size)
                parent = parents.get(key)
                if parent is None:
                    parent = QTreeWidgetItem([key])
                    parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    parent.setExpanded(True)
                    parents[key] = parent
                    self.remote_detail_tree.addTopLevelItem(parent)
                parent.addChild(item)
            else:
                self.remote_detail_tree.addTopLevelItem(item)
            if self.resource_view_mode == "thumbnails":
                thumbnail_item = QListWidgetItem(name)
                thumbnail_item.setData(Qt.ItemDataRole.UserRole, entry)
                thumbnail_item.setFlags(thumbnail_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                thumbnail_item.setCheckState(Qt.CheckState.Unchecked)
                thumbnail_item.setToolTip(f"{self._t('文件夹') if entry.is_dir else self._t('文件')} · {format_size(size) if size is not None else '--'}")
                thumbnail = self.thumbnail_paths.get(entry.path)
                if thumbnail:
                    thumbnail_item.setIcon(QIcon(thumbnail))
                else:
                    icon = QStyle.StandardPixmap.SP_DirIcon if entry.is_dir else QStyle.StandardPixmap.SP_FileIcon
                    thumbnail_item.setIcon(self.style().standardIcon(icon))
                self.remote_thumbnail_list.addItem(thumbnail_item)
        self.remote_detail_tree.setUpdatesEnabled(True)
        if self.resource_view_mode == "thumbnails":
            self.remote_thumbnail_list.setUpdatesEnabled(True)
        self._update_remote_selection_actions()

    @staticmethod
    def _direct_remote_entries(entries: list[RemoteEntry], folder: str) -> list[RemoteEntry]:
        """Return only the immediate children of a normalized directory path."""
        folder = folder.strip("/")
        directories = repository_directories(entry.path for entry in entries)
        entries_by_path = {entry.path: entry for entry in entries}
        direct: list[RemoteEntry] = []
        known: set[str] = set()
        prefix = f"{folder}/" if folder else ""
        for entry in entries:
            if folder and not entry.path.startswith(prefix):
                continue
            relative = entry.path[len(prefix):] if folder else entry.path
            if not relative:
                continue
            name = relative.split("/", 1)[0]
            path = f"{folder}/{name}".strip("/")
            if name in known:
                continue
            known.add(name)
            source = entries_by_path.get(path)
            is_dir = path in directories or bool(source and source.is_dir)
            direct.append(source or RemoteEntry(path, is_dir=is_dir))
        return direct

    def _detail_sort_key(self, entry: RemoteEntry) -> tuple:
        name = entry.path.rsplit("/", 1)[-1]
        if self.detail_sort_column == 1:
            return (self._t("文件夹") if entry.is_dir else self._t("文件"), name.casefold())
        if self.detail_sort_column == 2:
            size = self.folder_index.cached_folder_size(self.selected_repo, entry.path, self.selected_repo_public) if entry.is_dir and self.selected_repo else entry.size
            return (size is None, size if size is not None else 0, name.casefold())
        return (name.casefold(),)

    def _change_detail_sort(self, column: int) -> None:
        self.detail_sort_order = (
            Qt.SortOrder.DescendingOrder if column == self.detail_sort_column and self.detail_sort_order == Qt.SortOrder.AscendingOrder
            else Qt.SortOrder.AscendingOrder
        )
        self.detail_sort_column = column
        self.remote_detail_tree.header().setSortIndicator(column, self.detail_sort_order)
        self._render_remote_details()

    @staticmethod
    def _size_group(size: int | None) -> str:
        if size is None:
            return "未索引"
        if size < 1024 * 1024:
            return "<1MB"
        if size <= 1024 * 1024 * 1024:
            return "1MB-1GB"
        return ">1GB"

    def _set_current_directory(self, path: str, remember: bool = True) -> None:
        target = path.strip("/")
        if remember and target != self.current_directory_path and self.selected_repo:
            if not self.directory_history or self.directory_history[-1] != self.current_directory_path:
                self.directory_history.append(self.current_directory_path)
        self.current_directory_path = target
        self.resource_path_label.set_path(self.current_directory_path, self._t("根目录"))
        self.resource_back_button.setEnabled(bool(self.selected_repo and self.current_directory_path))
        directory = RemoteEntry(self.current_directory_path, is_dir=True)
        self.remote_detail_tree.set_drop_directory(directory)
        self.remote_thumbnail_list.set_drop_directory(directory)
        self._update_resource_upload_actions()

    def _go_to_directory(self, path: str) -> bool:
        if not self.selected_repo:
            return False
        target = path.replace("\\", "/").strip("/")
        if target == self.current_directory_path:
            return True
        directories = repository_directories(entry.path for entry in self.remote_entries)
        directories.update(entry.path for entry in self.remote_entries if entry.is_dir)
        if target and target not in directories:
            return False
        self._set_current_directory(target)
        self._select_remote_directory(target)
        self._render_remote_details()
        self._schedule_visible_thumbnails()
        return True

    def _go_to_parent_directory(self) -> bool:
        if not self.selected_repo or not self.current_directory_path:
            return False
        parent = self.current_directory_path.rpartition("/")[0]
        return self._go_to_directory(parent)

    def _go_to_previous_directory(self) -> bool:
        if not self.selected_repo:
            return False
        directories = repository_directories(entry.path for entry in self.remote_entries)
        directories.update(entry.path for entry in self.remote_entries if entry.is_dir)
        while self.directory_history:
            target = self.directory_history.pop()
            if target == self.current_directory_path or (target and target not in directories):
                continue
            self._set_current_directory(target, remember=False)
            self._select_remote_directory(target)
            self._render_remote_details()
            self._schedule_visible_thumbnails()
            return True
        return False

    def _select_remote_directory(self, path: str) -> None:
        target = path.strip("/")
        iterator = QTreeWidgetItemIterator(self.remote_tree)
        while iterator.value():
            item = iterator.value()
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, RemoteEntry) and entry.is_dir and entry.path == target:
                self.remote_tree.setCurrentItem(item)
                return
            iterator += 1
        self._set_current_directory("", remember=False)

    def _remote_thumbnail_selected(self) -> None:
        if getattr(self, "_syncing_remote_checks", False):
            return
        self._syncing_remote_checks = True
        for index in range(self.remote_thumbnail_list.count()):
            item = self.remote_thumbnail_list.item(index)
            item.setCheckState(
                Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked
            )
        self._syncing_remote_checks = False
        self._update_remote_selection_actions()

    def _remote_thumbnail_item_checked(self, item: QListWidgetItem) -> None:
        if getattr(self, "_syncing_remote_checks", False):
            return
        self._syncing_remote_checks = True
        item.setSelected(item.checkState() == Qt.CheckState.Checked)
        self._syncing_remote_checks = False
        self._update_remote_selection_actions()

    def _open_remote_thumbnail(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, RemoteEntry) and entry.is_dir:
            self._set_current_directory(entry.path)
            self._select_remote_directory(entry.path)
            self._render_remote_details()
            self._schedule_visible_thumbnails()

    def _remote_thumbnail_context_menu(self, position) -> None:
        item = self.remote_thumbnail_list.itemAt(position)
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(entry, RemoteEntry) and self.service and self.selected_repo:
            self._show_remote_menu(
                self.remote_thumbnail_list, position, entry, self.service, self.selected_repo,
                self.remote_entries, PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or ""),
            )
        elif self.service and self.selected_repo:
            self._show_paste_menu(self.remote_thumbnail_list, position, self.service, self.selected_repo, self.current_directory_path)

    def _remote_detail_selected(self) -> None:
        if getattr(self, "_syncing_remote_checks", False):
            return
        self._syncing_remote_checks = True
        iterator = QTreeWidgetItemIterator(self.remote_detail_tree)
        while iterator.value():
            item = iterator.value()
            if isinstance(item.data(0, Qt.ItemDataRole.UserRole), RemoteEntry):
                item.setCheckState(
                    0, Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked
                )
            iterator += 1
        self._syncing_remote_checks = False
        self._update_remote_selection_actions()

    def _remote_detail_item_checked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0 or getattr(self, "_syncing_remote_checks", False):
            return
        if not isinstance(item.data(0, Qt.ItemDataRole.UserRole), RemoteEntry):
            return
        self._syncing_remote_checks = True
        item.setSelected(item.checkState(0) == Qt.CheckState.Checked)
        self._syncing_remote_checks = False
        self._update_remote_selection_actions()

    def _selected_visible_remote_entries(self) -> list[RemoteEntry]:
        if hasattr(self, "global_search_tree") and not self.global_search_tree.isHidden():
            return []
        if self.resource_view_mode == "thumbnails":
            values = [item.data(Qt.ItemDataRole.UserRole) for item in self.remote_thumbnail_list.selectedItems()]
        else:
            values = [item.data(0, Qt.ItemDataRole.UserRole) for item in self.remote_detail_tree.selectedItems()]
        return [entry for entry in values if isinstance(entry, RemoteEntry)]

    def _update_remote_selection_actions(self) -> None:
        selected = self._selected_visible_remote_entries()
        available = bool(selected and self.selected_repo is not None and self.service is not None)
        self.download_selected_button.setEnabled(available)
        self.web_manage_button.setEnabled(
            available and self.active_account_kind == "web"
        )

    def _open_remote_detail(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteEntry) or not entry.is_dir:
            return
        iterator = QTreeWidgetItemIterator(self.remote_tree)
        while iterator.value():
            candidate = iterator.value()
            data = candidate.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, RemoteEntry) and data.path == entry.path:
                self.remote_tree.setCurrentItem(candidate)
                candidate.setExpanded(True)
                return
            iterator += 1

    def _remote_detail_context_menu(self, position) -> None:
        item = self.remote_detail_tree.itemAt(position)
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(entry, RemoteEntry) and self.service and self.selected_repo:
            self._show_remote_menu(
                self.remote_detail_tree, position, entry, self.service, self.selected_repo,
                self.remote_entries, PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or ""),
            )
        elif self.service and self.selected_repo:
            self._show_paste_menu(self.remote_detail_tree, position, self.service, self.selected_repo, self.current_directory_path)

    def _download_selected_remote(self) -> None:
        for entry in self._selected_visible_remote_entries():
            self.add_remote_download(entry)

    def _delete_selected_remote(self) -> None:
        selected = self._selected_visible_remote_entries()
        if selected and self.selected_repo:
            self._delete_remote_entries(
                str(self.active_account_id or ""), self.selected_repo, selected, self.remote_entries,
            )
