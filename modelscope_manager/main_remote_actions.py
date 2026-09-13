"""远端复制、移动、删除、播放及拖放上传行为。"""

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


class RemoteActionsMixin:
    """远端复制、移动、删除、播放及拖放上传行为。"""

    @staticmethod
    def _repository_web_url(repo: Repository, folder_path: str = "") -> str:
        kind = "datasets" if repo.repo_type == "dataset" else "models"
        base = f"https://www.modelscope.cn/{kind}/{repo.repo_id}/files"
        return f"{base}?path={quote(folder_path, safe='/')}" if folder_path else base

    def _copy_remote_link(
        self, entry: RemoteEntry, service: ModelScopeService, repo: Repository
    ) -> None:
        self._copy_remote_links([entry], service, repo)

    def _copy_remote_links(
        self, selected: list[RemoteEntry], service: ModelScopeService, repo: Repository,
    ) -> None:
        try:
            public = repository_is_public(repo, service.token)
            links = [
                self._repository_web_url(repo, entry.path)
                if entry.is_dir else repository_file_url(repo, entry.path, public)
                for entry in selected
            ]
        except Exception as exc:
            QMessageBox.warning(self, self._t("复制链接失败"), str(exc))
            return
        QApplication.clipboard().setText("\n".join(links))
        message = "链接已复制" if len(selected) > 1 else ("文件夹链接已复制" if selected[0].is_dir else "文件直链已复制")
        self._log(f"{message}：{len(selected)} 项")
        if any(not entry.is_dir for entry in selected) and not public:
            QMessageBox.information(
                self,
                self._t("私有资源 API 直链"),
                self._t("直链将以 API 形式复制。链接中不包含 Token，但访问者仍需拥有该私有仓库的权限。"),
            )
            self.repo_heading.setText(self._t("私有资源 API 直链已复制"))
        else:
            self.repo_heading.setText(self._t(message))

    @staticmethod
    def _context_selected_entries(view: QWidget, position, clicked: RemoteEntry) -> list[RemoteEntry]:
        item = view.itemAt(position)
        selected_items = view.selectedItems() if hasattr(view, "selectedItems") else []
        selected: list[RemoteEntry] = []
        for selected_item in selected_items:
            value = (
                selected_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(selected_item, QListWidgetItem)
                else selected_item.data(0, Qt.ItemDataRole.UserRole)
            )
            if isinstance(value, RemoteEntry):
                selected.append(value)
        if not any(value.path == clicked.path for value in selected):
            view.clearSelection()
            if item is not None:
                item.setSelected(True)
            return [clicked]
        return selected or [clicked]

    def _remote_context_menu(self, position) -> None:
        item = self.remote_tree.itemAt(position)
        if item is None or not self.service or not self.selected_repo:
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RemoteEntry):
            return
        self._show_remote_menu(
            self.remote_tree, position, entry, self.service, self.selected_repo, self.remote_entries,
            PUBLIC_ACCOUNT_ID if self.selected_repo_public else str(self.active_account_id or ""),
        )

    def _search_context_menu(self, position) -> None:
        item = self.search_remote_tree.itemAt(position)
        if item is None or not self.search_service or not self.search_repo:
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(entry, RemoteEntry):
            self._show_remote_menu(
                self.search_remote_tree,
                position,
                entry,
                self.search_service,
                self.search_repo,
                self.search_entries,
                PUBLIC_ACCOUNT_ID,
            )

    def _show_remote_menu(
        self,
        tree: QTreeWidget,
        position,
        entry: RemoteEntry,
        service: ModelScopeService,
        repo: Repository,
        entries: list[RemoteEntry],
        tag_account_id: str,
    ) -> None:
        selected = self._context_selected_entries(tree, position, entry)
        multiple = len(selected) > 1
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        link_action = menu.addAction(self._t("复制链接" if multiple or entry.is_dir else "复制直链"))
        copy_action = menu.addAction(self._t("复制"))
        paste_action = menu.addAction(self._t("粘贴")) if self.copy_source and not self.selected_repo_public and not multiple else None
        paste_move_action = menu.addAction(self._t("粘贴移动")) if self.move_source and not self.selected_repo_public and not multiple else None
        download_action = menu.addAction(self._t("添加到下载队列"))
        builtin_action = None
        player_actions: dict[QAction, dict[str, str]] = {}
        if not multiple and not entry.is_dir and Path(entry.path).suffix.lower() in MEDIA_EXTENSIONS | IMAGE_EXTENSIONS:
            if self.builtin_player_enabled.isChecked():
                builtin_action = menu.addAction(self._t("使用本地 PotPlayer 打开"))
            player_menu = menu.addMenu(self._t("使用第三方播放器打开"))
            for index, player in enumerate(self.external_players):
                name = player.get("name") or f"播放器 {index + 1}"
                action = player_menu.addAction(name)
                player_actions[action] = player
        tag_menu = menu.addMenu(self._t("标签"))
        assigned_by_path = {
            value.path: set(self.account_store.tags_for_entry(
                tag_account_id, repo.repo_type, repo.repo_id, value.path,
            ))
            for value in selected
        }
        assigned_tags = set.intersection(*assigned_by_path.values()) if assigned_by_path else set()
        tag_actions: dict[QAction, str] = {}
        for tag in self.account_store.all_tags():
            action = tag_menu.addAction(tag)
            action.setCheckable(True)
            action.setChecked(tag in assigned_tags)
            tag_actions[action] = tag
        new_tag_action = tag_menu.addAction("新建标签")
        menu.addSeparator()
        delete_action = menu.addAction(self._t("删除"))
        move_action = menu.addAction(self._t("移动"))
        rename_action = menu.addAction(self._t("重命名（区分大小写）"))
        web_writable = tag_account_id.startswith("web:")
        if not web_writable:
            tooltip = "这是只读" if tag_account_id == PUBLIC_ACCOUNT_ID else "请使用在线登录列表执行"
            for action in (delete_action, move_action, rename_action):
                action.setEnabled(False)
                action.setToolTip(tooltip)
        elif multiple:
            rename_action.setEnabled(False)
            rename_action.setToolTip("多选时不能重命名")
        chosen = menu.exec(tree.viewport().mapToGlobal(position))
        if chosen is link_action:
            self._copy_remote_links(selected, service, repo)
        elif chosen is copy_action:
            self.copy_source = (service, repo, list(entries), list(selected))
            self._log(f"已复制 {len(selected)} 项；请选择可写目录后右键粘贴")
        elif paste_action is not None and chosen is paste_action:
            destination = entry.path if entry.is_dir else entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
            self._paste_remote_copy(service, repo, destination)
        elif paste_move_action is not None and chosen is paste_move_action:
            destination = entry.path if entry.is_dir else entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
            self._paste_remote_move(tag_account_id, service, repo, destination)
        elif chosen is download_action:
            for value in selected:
                self.add_remote_download(value, service, repo, entries)
        elif builtin_action is not None and chosen is builtin_action:
            self.open_builtin_remote(entry, service, repo)
        elif chosen in player_actions:
            self.open_external_player(entry, service, repo, player_actions[chosen])
        elif chosen in tag_actions:
            tag = tag_actions[chosen]
            remove = tag in assigned_tags
            for value in selected:
                tags = assigned_by_path[value.path]
                if remove:
                    tags.discard(tag)
                else:
                    tags.add(tag)
                self._save_entry_tags(tag_account_id, repo, value.path, list(tags))
        elif chosen is new_tag_action:
            name, accepted = QInputDialog.getText(self, "新建标签", "标签名称：")
            if accepted and name.strip():
                for value in selected:
                    self._save_entry_tags(
                        tag_account_id, repo, value.path,
                        list(assigned_by_path[value.path]) + [name],
                    )
        elif chosen is delete_action:
            self._delete_remote_entries(tag_account_id, repo, selected, entries)
        elif chosen is move_action:
            self.move_source = (tag_account_id, service, repo, list(entries), list(selected))
            self._log(f"已选择移动 {len(selected)} 项；请选择目标目录后右键粘贴移动")
        elif chosen is rename_action:
            self._rename_remote_entry(tag_account_id, service, repo, entry, entries)

    def _show_paste_menu(
        self, view: QWidget, position, service: ModelScopeService, repo: Repository, destination: str,
    ) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        paste_action = menu.addAction("粘贴")
        paste_action.setEnabled(bool(self.copy_source) and not self.selected_repo_public)
        paste_move_action = menu.addAction("粘贴移动")
        paste_move_action.setEnabled(bool(self.move_source) and not self.selected_repo_public)
        chosen = menu.exec(view.viewport().mapToGlobal(position))
        if chosen is paste_action and paste_action.isEnabled():
            self._paste_remote_copy(service, repo, destination)
        elif chosen is paste_move_action and paste_move_action.isEnabled():
            account_key = str(self.active_account_id or "")
            self._paste_remote_move(account_key, service, repo, destination)

    @staticmethod
    def _entry_file_paths(entry: RemoteEntry, entries: list[RemoteEntry]) -> list[str]:
        if not entry.is_dir:
            return [entry.path]
        prefix = entry.path.strip("/")
        return sorted(
            [value.path for value in entries if not value.is_dir and value.path.startswith(prefix + "/")],
            reverse=True,
        )

    def _web_session_for_key(self, account_key: str) -> ModelScopeWebSession | None:
        if not account_key.startswith("web:"):
            return None
        account_id = account_key.removeprefix("web:")
        return self.session_web_sessions.get(account_id) or self.account_store.load_web_session(account_id)

    def _delete_remote_entry(
        self, account_key: str, repo: Repository, entry: RemoteEntry, entries: list[RemoteEntry],
    ) -> None:
        self._delete_remote_entries(account_key, repo, [entry], entries)

    def _delete_remote_entries(
        self, account_key: str, repo: Repository,
        selected: list[RemoteEntry], entries: list[RemoteEntry],
    ) -> None:
        session = self._web_session_for_key(account_key)
        if session is None:
            QMessageBox.information(self, self._t("需要在线登录"), self._t("转到设置页面添加账号。"))
            return
        paths = sorted({
            path
            for entry in selected
            for path in self._entry_file_paths(entry, entries)
        }, reverse=True)
        if not paths:
            QMessageBox.information(self, self._t("删除"), self._t("文件夹中没有可删除的文件。"))
            return
        answer = QMessageBox.warning(
            self,
            self._t("确认删除"),
            self._tf(
                "此操作不可逆，确定删除选中的 {selected} 项（共 {count} 个文件）？",
                selected=len(selected), count=len(paths),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_task = DeleteThread(session, repo, paths, self)
        self.delete_task.completed.connect(
            lambda result, key=account_key, value=repo, selected_paths=[item.path for item in selected]: self._remote_delete_completed(
                key, value, result, selected_paths,
            )
        )
        self.delete_task.failed.connect(lambda error: QMessageBox.warning(self, self._t("删除失败"), error))
        self.delete_task.finished.connect(self.delete_task.deleteLater)
        self.delete_task.start()
        self._log(f"开始删除：{repo.repo_id} · {len(selected)} 项 / {len(paths)} 个文件")

    def _cached_remote_entries(self, account_key: str, repo: Repository) -> list[RemoteEntry]:
        if self.selected_repo == repo and (
            (self.selected_repo_public and account_key == PUBLIC_ACCOUNT_ID)
            or (not self.selected_repo_public and account_key == self.active_account_id)
        ):
            return list(self.remote_entries)
        return [
            RemoteEntry(value.path, value.size, value.sha256, value.is_dir)
            for value in self.account_store.repository_entries(account_key, repo.repo_type, repo.repo_id)
        ]

    def _apply_local_remote_changes(
        self,
        account_key: str,
        repo: Repository,
        removed: Iterable[str] = (),
        removed_prefixes: Iterable[str] = (),
        additions: Iterable[RemoteEntry] = (),
    ) -> None:
        removed_set = set(removed)
        prefixes = list(dict.fromkeys(path.strip("/") for path in removed_prefixes if path.strip("/")))
        before = self._cached_remote_entries(account_key, repo)
        values = {
            entry.path: entry
            for entry in before
            if entry.path not in removed_set
            and not any(entry.path == prefix or entry.path.startswith(prefix + "/") for prefix in prefixes)
        }
        additions = list(additions)
        for entry in additions:
            values[entry.path] = entry
        updated = sorted(values.values(), key=lambda value: value.path)
        if additions:
            self.account_store.cache_entries(account_key, repo, updated)
            self.folder_index.update_repository(repo, updated, False)
        else:
            removed_entries = [entry for entry in before if entry.path not in values]
            self.account_store.remove_entry_prefixes(
                account_key, repo.repo_type, repo.repo_id, [*removed_set, *prefixes],
            )
            self.folder_index.remove_entries(repo, removed_entries, prefixes, False)
        current = self.selected_repo == repo and not self.selected_repo_public and account_key == self.active_account_id
        if current:
            self._files_loaded(updated, persist=False)

    def _remote_delete_completed(
        self, account_key: str, repo: Repository, result: dict, selected_path: str | list[str] = "",
    ) -> None:
        self.delete_task = None
        deleted = list(result.get("deleted", []))
        failures = dict(result.get("failures", {}))
        selected_paths = [selected_path] if isinstance(selected_path, str) else selected_path
        remove_prefixes = [path for path in selected_paths if path] if not failures else []
        self._apply_local_remote_changes(
            account_key, repo, removed=deleted, removed_prefixes=remove_prefixes,
        )
        self._log(f"删除完成：{len(deleted)} 个成功，{len(failures)} 个失败")
        if failures:
            QMessageBox.warning(self, self._t("删除完成"), self._tf("已删除 {deleted} 个文件，{failed} 个失败。", deleted=len(deleted), failed=len(failures)))
        else:
            QMessageBox.information(self, self._t("删除完成"), self._tf("已删除 {count} 个文件，当前目录已刷新。", count=len(deleted)))

    def _confirm_relocate_threshold(self, entry: RemoteEntry, entries: list[RemoteEntry], verb: str) -> bool:
        if entry.is_dir:
            total_size = sum(
                value.size for value in entries
                if not value.is_dir and value.path.startswith(entry.path.strip("/") + "/")
            )
        else:
            total_size = entry.size
        if total_size <= self._copy_threshold_bytes():
            return True
        answer = QMessageBox.question(
            self,
            f"{verb}较大资源",
            f"当前文件/文件夹大小为 {format_size(total_size)}，超过下载阈值。是否先下载到临时目录再执行{verb}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    @staticmethod
    def _relocate_mappings(entry: RemoteEntry, entries: list[RemoteEntry], target_path: str) -> dict[str, str]:
        if not entry.is_dir:
            return {entry.path: target_path}
        prefix = entry.path.strip("/")
        return {
            value.path: normalize_remote_path(target_path, value.path[len(prefix):].strip("/"))
            for value in entries
            if not value.is_dir and value.path.startswith(prefix + "/")
        }

    def _paste_remote_move(
        self, destination_key: str, destination_service: ModelScopeService,
        destination_repo: Repository, destination_folder: str,
    ) -> None:
        if not self.move_source:
            return
        source_key, source_service, source_repo, source_entries, selected_items = self.move_source
        if self._web_session_for_key(source_key) is None:
            QMessageBox.information(self, self._t("需要在线登录"), self._t("转到设置页面添加账号。"))
            return
        upload_service = self._token_service_for_repo(destination_repo)
        if upload_service is None:
            QMessageBox.information(
                self, self._t("需要 Token 账户"), self._t("请先添加可访问目标仓库的 Token 账户；上传不会使用网页登录接口。"),
            )
            return
        mappings: dict[str, str] = {}
        for selected in selected_items:
            target = normalize_remote_path(destination_folder, Path(selected.path).name)
            if source_repo == destination_repo and source_key == destination_key:
                source_path = selected.path.strip("/")
                if target == source_path:
                    QMessageBox.information(self, self._t("移动"), self._t("目标路径与原路径相同。"))
                    return
                if selected.is_dir and (destination_folder == source_path or destination_folder.startswith(source_path + "/")):
                    QMessageBox.warning(self, self._t("移动"), self._t("不能把文件夹移动到其自身或子目录中。"))
                    return
            if not self._confirm_relocate_threshold(selected, source_entries, "移动"):
                return
            mappings.update(self._relocate_mappings(selected, source_entries, target))
        self._start_relocate(
            source_key, source_service, source_repo, destination_key,
            upload_service, destination_repo, source_entries, mappings,
        )

    def _rename_remote_entry(
        self, account_key: str, service: ModelScopeService, repo: Repository,
        entry: RemoteEntry, entries: list[RemoteEntry],
    ) -> None:
        current_name = Path(entry.path).name
        new_name, accepted = QInputDialog.getText(
            self, self._t("重命名（区分大小写）"), self._t("新名称（区分大小写）："), text=current_name,
        )
        new_name = new_name.strip()
        if not accepted:
            return
        if not new_name or new_name in {".", ".."} or "/" in new_name or "\\" in new_name:
            QMessageBox.warning(self, self._t("重命名"), self._t("请输入不包含路径分隔符的有效名称。"))
            return
        if new_name == current_name:
            QMessageBox.information(self, self._t("重命名"), self._t("新名称与原名称相同。"))
            return
        if not self._confirm_relocate_threshold(entry, entries, "重命名"):
            return
        upload_service = self._token_service_for_repo(repo)
        if upload_service is None:
            QMessageBox.information(
                self, self._t("需要 Token 账户"), self._t("请先添加可访问该仓库的 Token 账户；上传不会使用网页登录接口。"),
            )
            return
        parent = entry.path.rpartition("/")[0]
        target = normalize_remote_path(parent, new_name)
        mappings = self._relocate_mappings(entry, entries, target)
        self._start_relocate(
            account_key, service, repo, account_key, upload_service, repo, entries, mappings,
        )

    def _start_relocate(
        self,
        source_key: str,
        source_service: ModelScopeService,
        source_repo: Repository,
        destination_key: str,
        destination_service: ModelScopeService,
        destination_repo: Repository,
        source_entries: list[RemoteEntry],
        mappings: dict[str, str],
    ) -> None:
        if not mappings:
            QMessageBox.information(self, self._t("操作"), self._t("没有可传输的文件。"))
            return
        session = self._web_session_for_key(source_key)
        if session is None:
            QMessageBox.information(self, self._t("需要在线登录"), self._t("转到设置页面添加账号。"))
            return
        self.relocate_context = (source_key, source_repo, destination_key, destination_repo, source_entries)
        self.relocate_task = RelocateThread(
            source_service,
            source_repo,
            destination_service,
            destination_repo,
            mappings,
            lambda path: delete_repository_file(session, source_repo.repo_id, source_repo.repo_type, path),
            self,
        )
        self.relocate_task.completed.connect(self._relocate_completed)
        self.relocate_task.failed.connect(lambda error: QMessageBox.warning(self, self._t("操作失败"), error))
        self.relocate_task.finished.connect(self.relocate_task.deleteLater)
        self.relocate_task.start()
        self._log(f"开始下载、上传并移动：{len(mappings)} 个文件")

    def _relocate_completed(self, result: dict) -> None:
        context = self.relocate_context
        self.relocate_task = None
        self.relocate_context = None
        if context is None:
            return
        source_key, source_repo, destination_key, destination_repo, source_entries = context
        mappings = dict(result.get("mappings", {}))
        upload_failed = set(result.get("upload_failed", []))
        uploaded = set(mappings) - upload_failed
        deleted = list(result.get("deleted", []))
        source_by_path = {entry.path: entry for entry in source_entries}
        additions = [
            RemoteEntry(mappings[path], source_by_path[path].size, source_by_path[path].sha256, False)
            for path in uploaded if path in source_by_path
        ]
        self._apply_local_remote_changes(destination_key, destination_repo, additions=additions)
        if deleted:
            self._apply_local_remote_changes(source_key, source_repo, removed=deleted)
        delete_failed = dict(result.get("delete_failed", {}))
        if upload_failed:
            message = f"{len(uploaded)} 个上传成功，{len(upload_failed)} 个上传失败；源文件未删除。"
            QMessageBox.warning(self, self._t("操作未完成"), message)
        elif delete_failed:
            message = f"全部上传成功；{len(deleted)} 个源文件已删除，{len(delete_failed)} 个删除失败。"
            QMessageBox.warning(self, self._t("操作部分完成"), message)
        else:
            message = f"{len(uploaded)} 个文件上传完成并已删除原路径。"
            QMessageBox.information(self, self._t("操作完成"), message)
            self.move_source = None
        self._log(message)

    def _paste_remote_copy(self, destination_service: ModelScopeService, destination_repo: Repository, destination_folder: str) -> None:
        if not self.copy_source or self.selected_repo_public:
            return
        source_service, source_repo, source_entries, selected_items = self.copy_source
        upload_service = self._token_service_for_repo(destination_repo)
        if upload_service is None:
            QMessageBox.information(
                self, self._t("需要 Token 账户"), self._t("请先添加可访问目标仓库的 Token 账户；上传不会使用网页登录接口。"),
            )
            return
        total_size = 0
        for selected in selected_items:
            if selected.is_dir:
                total_size += self.folder_index.update_folder(
                    source_repo, selected.path, source_entries,
                    repository_is_public(source_repo, source_service.token),
                )
            else:
                total_size += selected.size
        if total_size > self._copy_threshold_bytes():
            answer = QMessageBox.question(
                self, "复制较大资源",
                f"当前文件/文件夹大小为 {format_size(total_size)}，是否在后台下载以实施复制操作？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.copy_task = CopyThread(
            source_service, source_repo, source_entries, selected_items,
            upload_service, destination_repo, destination_folder, self,
        )
        self.copy_task.completed.connect(self._remote_copy_completed)
        self.copy_task.failed.connect(lambda error: QMessageBox.warning(self, self._t("复制失败"), error))
        self.copy_task.finished.connect(self.copy_task.deleteLater)
        self.copy_task.start()
        self._log(f"开始后台复制：{len(selected_items)} 项 → {destination_repo.repo_id}/{destination_folder}")

    def _remote_copy_completed(self, ok: int, failed: int) -> None:
        self.copy_task = None
        self._log(f"复制完成：{ok} 个文件成功，{failed} 个失败；临时文件已清除")
        QMessageBox.information(self, self._t("复制完成"), self._tf("{ok} 个文件成功，{failed} 个失败。临时文件已清除。", ok=ok, failed=failed))
        self.load_remote_files()

    def _save_entry_tags(self, account_id: str, repo: Repository, path: str, tags: list[str]) -> None:
        try:
            saved = self.account_store.set_entry_tags(account_id, repo.repo_type, repo.repo_id, path, tags)
        except ValueError as exc:
            QMessageBox.warning(self, self._t("标签"), str(exc))
            return
        self._refresh_tag_filter()
        self._log(f"标签已更新：{path or '/'} · {', '.join(saved) or '无'}")

    def open_external_player(
        self,
        entry: RemoteEntry,
        service: ModelScopeService,
        repo: Repository,
        player_info: dict[str, str],
    ) -> None:
        if not repository_is_public(repo, service.token):
            QMessageBox.information(
                self,
                self._t("私有资源无法直接播放"),
                self._t("外部播放器无法安全接收 ModelScope 访问令牌。请先下载该文件，再从本地播放。"),
            )
            return
        saved = player_info.get("path", "")
        player = Path(saved) if saved else Path()
        if not saved or not player.is_file():
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "选择 mpv、PotPlayer 或其他播放器",
                "",
                "播放器程序 (*.exe);;所有文件 (*)",
            )
            if not selected:
                return
            player = Path(selected)
            player_info["path"] = str(player)
            if not player_info.get("name") or player_info["name"].startswith("播放器 "):
                player_info["name"] = player.stem
            self._render_players()
            self._save_players()
        try:
            url = service.get_download_url(repo, entry.path)
            started = QProcess.startDetached(str(player), [url])
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法打开播放器"), str(exc))
            return
        success = started[0] if isinstance(started, tuple) else bool(started)
        if not success:
            QMessageBox.warning(self, self._t("无法打开播放器"), self._t("播放器未能启动，请重新选择播放器程序。"))
            player_info["path"] = ""
            self._render_players()
            self._save_players()
            return
        self._log(f"已交给外部播放器：{entry.path}")

    def _repository_paths_dropped(self, raw_paths: list[str], directory: RemoteEntry) -> None:
        if self.selected_repo_public:
            QMessageBox.information(self, "Public", self._t("Public 仓库为只读挂载，不能上传。"))
            return
        if not self.service and not self.upload_session_service:
            self._prompt_for_settings()
            return
        if not self.selected_repo:
            QMessageBox.information(self, self._t("请选择仓库"), self._t("请先在左侧选择目标仓库。"))
            return
        if self.task and self.task.isRunning() and not isinstance(self.task, UploadThread):
            QMessageBox.information(self, self._t("传输进行中"), self._t("已有任务正在运行，请完成后再添加上传。"))
            return
        if (
            self.upload_session_repo
            and repository_identity(self.upload_session_repo) != repository_identity(self.selected_repo)
        ):
            QMessageBox.information(self, self._t("传输进行中"), self._t("请在当前上传队列完成后再切换目标仓库。"))
            return
        total_size = local_paths_size(raw_paths)
        added = self.add_paths(raw_paths, directory.path)
        if not added:
            return
        self.queue_tabs.setCurrentIndex(0)
        threshold = self.drop_upload_threshold_mb.value() * 1024 * 1024
        if total_size > threshold:
            monitor = QMessageBox.question(
                self,
                self._t("大文件上传"),
                self._tf(
                    "上传内容总大小约为 {size}，已超过 {threshold} MB。是否跳转到传输列表监控？",
                    size=format_size(total_size),
                    threshold=self.drop_upload_threshold_mb.value(),
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if monitor == QMessageBox.StandardButton.Yes:
                self._navigate(1)
        self._log(f"上传到 /{directory.path}")
        if not (self.task and self.task.isRunning()):
            QTimer.singleShot(0, self.start_upload)
