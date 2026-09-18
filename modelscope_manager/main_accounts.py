"""账户与搜索历史行为。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from .app_helpers import PUBLIC_ACCOUNT_ID
from .database import WebAccountRecord
from .login_dialog import ModelScopeLoginDialog
from .service import ModelScopeService, Repository
from .web_session import ModelScopeWebSession, web_session_username


class AccountsMixin:
    """账户与搜索历史行为。"""

    def _render_public_history(self) -> None:
        current = self.search_url_edit.currentText().strip()
        self.search_url_edit.blockSignals(True)
        self.search_url_edit.clear()
        for item in self.public_pool_store.load():
            label = item.get("url") or f"https://www.modelscope.cn/{item['repo_type']}s/{item['repo_id']}"
            self.search_url_edit.addItem(label, item)
        if current:
            self.search_url_edit.setEditText(current)
        self.search_url_edit.blockSignals(False)

    def show_search_history(self) -> None:
        if self.search_history_window and self.search_history_window.isVisible():
            self.search_history_window.raise_()
            self.search_history_window.activateWindow()
            return
        window = QWidget(None, Qt.WindowType.Window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.setWindowTitle("搜索历史")
        window.resize(720, 420)
        layout = QVBoxLayout(window)
        clear_button = QPushButton("清空搜索历史")
        clear_button.clicked.connect(self._clear_search_history)
        layout.addWidget(clear_button, alignment=Qt.AlignmentFlag.AlignLeft)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["仓库", "类型", "链接", ""])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setColumnWidth(3, 72)
        self._populate_search_history_table(table)
        layout.addWidget(table)
        window.destroyed.connect(lambda: setattr(self, "search_history_window", None))
        self.search_history_window = window
        window.show()

    def _populate_search_history_table(self, table: QTableWidget) -> None:
        table.setRowCount(0)
        for item in self.public_pool_store.load():
            row = table.rowCount()
            table.insertRow(row)
            root = item.get("root_path", "")
            table.setItem(row, 0, QTableWidgetItem(item["repo_id"] + (f" / {root}" if root else "")))
            table.setItem(row, 1, QTableWidgetItem(item["repo_type"]))
            table.setItem(row, 2, QTableWidgetItem(item.get("url", "")))
            remove = QPushButton("删除")
            repo = Repository(item["repo_id"], item["repo_type"], "public")
            remove.clicked.connect(lambda checked=False, value=repo, mount_root=root: self._remove_public_repository(value, mount_root))
            table.setCellWidget(row, 3, remove)

    def _refresh_search_history_window(self) -> None:
        if self.search_history_window and self.search_history_window.isVisible():
            table = self.search_history_window.findChild(QTableWidget)
            if table:
                self._populate_search_history_table(table)

    def _remove_public_repository(self, repo: Repository, root_path: str | None = None) -> None:
        self.public_pool_store.remove(repo, root_path)
        if not any(mount.repo == repo for mount in self.public_pool_store.mounts()):
            self.account_store.remove_repository_entries(PUBLIC_ACCOUNT_ID, repo.repo_type, repo.repo_id)
            self.folder_index.remove_repository(repo, True)
        if self.webdav:
            self.webdav.refresh_public_pools()
        if self.selected_repo_public and self.selected_repo == repo:
            self.selected_repo = None
            self.selected_repo_public = False
            self.remote_entries = []
            self.remote_tree.clear()
            self.refresh_files_button.setEnabled(False)
            self.new_folder_button.setEnabled(False)
            self.upload_file_button.setEnabled(False)
            self.upload_folder_button.setEnabled(False)
        self._render_public_history()
        self._render_repositories()
        self._refresh_tag_filter()
        self._refresh_search_history_window()

    def _clear_search_history(self) -> None:
        for repo in self.public_pool_store.repositories():
            self.account_store.remove_repository_entries(PUBLIC_ACCOUNT_ID, repo.repo_type, repo.repo_id)
            self.folder_index.remove_repository(repo, True)
        self.public_pool_store.clear()
        if self.webdav:
            self.webdav.refresh_public_pools()
        if self.selected_repo_public:
            self.selected_repo = None
            self.selected_repo_public = False
            self.remote_entries = []
            self.remote_tree.clear()
            self.refresh_files_button.setEnabled(False)
            self.new_folder_button.setEnabled(False)
            self.upload_file_button.setEnabled(False)
            self.upload_folder_button.setEnabled(False)
        self._render_public_history()
        self._render_repositories()
        self._refresh_tag_filter()
        self._refresh_search_history_window()

    def _render_accounts(self) -> None:
        if not hasattr(self, "account_table"):
            return
        selected_id = self._selected_account_id()
        self.account_table.setUpdatesEnabled(False)
        self.account_table.blockSignals(True)
        self.account_table.setRowCount(len(self.accounts))
        for row, account in enumerate(self.accounts):
            label_item = QTableWidgetItem(account.label)
            label_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            self.account_table.setItem(row, 0, label_item)
            self.account_table.setItem(row, 1, QTableWidgetItem(account.username or "--"))
            token = self.session_tokens.get(account.account_id, account.token)
            token_widget = QWidget()
            token_layout = QHBoxLayout(token_widget)
            token_layout.setContentsMargins(2, 0, 2, 0)
            token_label = QLabel("••••••••" if token else "未保存")
            token_layout.addWidget(token_label, 1)
            show_button = QPushButton("显示")
            show_button.setEnabled(bool(token))
            show_button.setFixedWidth(54)

            def toggle_token(checked=False, value=token, label=token_label, button=show_button):
                showing = button.text() == self._t("显示")
                label.setText(value if showing else "••••••••")
                button.setText(self._t("隐藏") if showing else self._t("显示"))

            show_button.clicked.connect(toggle_token)
            token_layout.addWidget(show_button)
            self.account_table.setCellWidget(row, 2, token_widget)
            remember = QCheckBox()
            remember.setChecked(account.remember)
            remember.setEnabled(bool(token))
            remember.toggled.connect(
                lambda checked, account_id=account.account_id: self._account_remember_changed(account_id, checked)
            )
            remember_wrapper = QWidget()
            remember_layout = QHBoxLayout(remember_wrapper)
            remember_layout.setContentsMargins(0, 0, 0, 0)
            remember_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            remember_layout.addWidget(remember)
            self.account_table.setCellWidget(row, 3, remember_wrapper)
            status_labels = {
                "connected": self._t("已连接"),
                "failed": self._t("验证失败"),
                "token_required": self._t("需要 Token"),
                "waiting": self._t("等待验证"),
            }
            self.account_table.setItem(row, 4, QTableWidgetItem(status_labels.get(account.status, account.status)))
            if account.account_id == selected_id:
                self.account_table.selectRow(row)
        self.account_table.blockSignals(False)
        self.account_table.setUpdatesEnabled(True)
        self.account_table.viewport().update()

    def _selected_account_id(self) -> str | None:
        row = self.account_table.currentRow() if hasattr(self, "account_table") else -1
        item = self.account_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _account_selected(self) -> None:
        account_id = self._selected_account_id()
        account = next((item for item in self.accounts if item.account_id == account_id), None)
        if not account:
            return
        self.active_account_id = account.account_id
        self.account_name_edit.setText(account.label)
        self.token_edit.setText(self.session_tokens.get(account.account_id, account.token))
        self.remember_token.setChecked(account.remember)

    @staticmethod
    def _web_account_key(account_id: str) -> str:
        return f"web:{account_id}"

    def _token_service_for_repo(self, repo: Repository) -> ModelScopeService | None:
        for account in self.accounts:
            service = self.account_services.get(account.account_id)
            if not service:
                continue
            if any(
                candidate.repo_type == repo.repo_type and candidate.repo_id == repo.repo_id
                for candidate in self.account_repositories.get(account.account_id, [])
            ):
                return service
        return None

    def _selected_web_account_id(self) -> str | None:
        row = self.web_account_table.currentRow() if hasattr(self, "web_account_table") else -1
        item = self.web_account_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _render_web_accounts(self) -> None:
        if not hasattr(self, "web_account_table"):
            return
        selected_id = self._selected_web_account_id()
        table = self.web_account_table
        table.blockSignals(True)
        table.setRowCount(len(self.web_accounts))
        for row, account in enumerate(self.web_accounts):
            label_item = QTableWidgetItem(account.label)
            label_item.setData(Qt.ItemDataRole.UserRole, account.account_id)
            table.setItem(row, 0, label_item)
            username_item = QTableWidgetItem(account.username or "--")
            username_item.setFlags(username_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 1, username_item)
            status = self._t("成功") if (self.session_web_sessions.get(account.account_id) or self.account_store.load_web_session(account.account_id)) else self._t("尚未登录")
            status_item = QTableWidgetItem(status)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 2, status_item)
            login_button = QPushButton(self._t("在线登录"), objectName="primary")
            login_button.clicked.connect(
                lambda checked=False, account_id=account.account_id: self.open_online_login(account_id)
            )
            table.setCellWidget(row, 3, login_button)
            if account.account_id == selected_id:
                table.selectRow(row)
        table.blockSignals(False)

    def _web_account_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        account_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        account = next((value for value in self.web_accounts if value.account_id == account_id), None)
        if not account:
            return
        account.label = item.text().strip() or account.username or "ModelScope 账户"
        self.account_store.save_web_account(account)
        self._render_repositories()

    def add_web_account(self) -> None:
        index = len(self.web_accounts) + 1
        account = self.account_store.save_web_account(
            WebAccountRecord("", self._tf("网页登录账户 {index}", index=index), "", "login_required")
        )
        self.web_accounts.append(account)
        self._render_web_accounts()
        self.web_account_table.selectRow(len(self.web_accounts) - 1)
        self.web_account_table.editItem(self.web_account_table.item(len(self.web_accounts) - 1, 0))

    def remove_web_account(self) -> None:
        account_id = self._selected_web_account_id()
        account = next((value for value in self.web_accounts if value.account_id == account_id), None)
        if not account:
            return
        answer = QMessageBox.question(
            self, self._t("移除网页登录账户"),
            self._tf("确定移除网页登录账户 {name}？本地保存的 Cookie 将被销毁。", name=account.label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        key = self._web_account_key(account.account_id)
        self.account_store.remove_web_account(account.account_id)
        self.session_web_sessions.pop(account.account_id, None)
        self.web_accounts = [value for value in self.web_accounts if value.account_id != account.account_id]
        self.account_services.pop(key, None)
        self.account_repositories.pop(key, None)
        self._render_web_accounts()
        self._render_repositories()

    def open_online_login(self, account_id: str | None = None) -> None:
        account_id = account_id or self._selected_web_account_id()
        account = next((item for item in self.web_accounts if item.account_id == account_id), None)
        if not account:
            QMessageBox.information(self, self._t("请选择账户"), self._t("请先添加一个网页登录账户。"))
            return
        dialog = ModelScopeLoginDialog(account.label or account.username, self._t, self)
        dialog.session_captured.connect(
            lambda session, info, current_id=account.account_id: self._online_login_completed(current_id, session, info)
        )
        dialog.finished.connect(dialog.deleteLater)
        self.online_login_dialog = dialog
        dialog.show()

    def _online_login_completed(
        self,
        account_id: str,
        session: ModelScopeWebSession,
        user_info: dict,
    ) -> None:
        account = next((item for item in self.web_accounts if item.account_id == account_id), None)
        if not account:
            return
        web_username = web_session_username(user_info)
        account.username = web_username
        account.status = "connected"
        self.account_store.save_web_account(account)
        persisted = True
        try:
            self.account_store.save_web_session(account_id, session)
        except Exception as exc:
            persisted = False
            self.session_web_sessions[account_id] = session
            self._log(f"网页登录信息无法使用当前用户 DPAPI 保存，仅本次运行有效：{exc}")
        else:
            self.session_web_sessions.pop(account_id, None)
        self._render_web_accounts()
        if persisted:
            self._log(f"网页登录信息已安全保存：{account.label}")
            QMessageBox.information(
                self, self._t("在线登录成功"),
                self._t("网页登录信息已使用当前用户 DPAPI 加密保存。"),
            )
        else:
            QMessageBox.warning(
                self, self._t("在线登录成功"),
                self._t("登录已在本次运行中启用，但当前用户 DPAPI 不可用，因此网页登录信息不会持久保存。"),
            )
        QTimer.singleShot(0, self.load_repositories)

    def add_account(self) -> None:
        self.account_table.clearSelection()
        self.account_table.setCurrentItem(None)
        self.active_account_id = None
        self.account_name_edit.clear()
        self.token_edit.clear()
        self.remember_token.setChecked(True)
        self.account_label.setText(self._t("输入新账户 Token 后保存并验证"))
        self.token_edit.setFocus()

    def remove_account(self) -> None:
        account_id = self._selected_account_id()
        account = next((item for item in self.accounts if item.account_id == account_id), None)
        if not account:
            return
        answer = QMessageBox.question(
            self,
            self._t("移除账户"),
            self._tf("确定移除账户 {name}？本地保存的 Token 将被销毁。", name=account.label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.account_store.remove(account.account_id)
        self.session_tokens.pop(account.account_id, None)
        self.account_services.pop(account.account_id, None)
        self.account_repositories.pop(account.account_id, None)
        self.accounts = [item for item in self.accounts if item.account_id != account.account_id]
        if self.active_account_id == account.account_id:
            self.active_account_id = None
            self.service = None
            self.selected_repo = None
            self.remote_entries = []
            self.remote_tree.clear()
            self.refresh_files_button.setEnabled(False)
            self.new_folder_button.setEnabled(False)
            self.upload_file_button.setEnabled(False)
            self.upload_folder_button.setEnabled(False)
            self._update_upload_enabled()
        self._render_accounts()
        self._render_repositories()
        self._render_backup_account_options()
        self._render_backup_jobs()
        self._render_image_account_options()
        self.add_account()

    def _account_remember_changed(self, account_id: str, checked: bool) -> None:
        if self._restoring_settings:
            return
        account = next((item for item in self.accounts if item.account_id == account_id), None)
        if not account:
            return
        account.remember = checked
        account.token = self.session_tokens.get(account_id, account.token)
        try:
            self.account_store.save(account)
        except Exception as exc:
            self._log(f"账户安全设置保存失败：{exc}")

    def _render_backup_account_options(self) -> None:
        if not hasattr(self, "backup_account_combo"):
            return
        selected = self.backup_account_combo.currentData()
        self.backup_account_combo.blockSignals(True)
        self.backup_account_combo.clear()
        account_options = [(account.account_id, account.label or account.username) for account in self.accounts]
        for account_id, label in account_options:
            self.backup_account_combo.addItem(label, account_id)
        index = self.backup_account_combo.findData(selected)
        self.backup_account_combo.setCurrentIndex(index if index >= 0 else (0 if account_options else -1))
        self.backup_account_combo.blockSignals(False)
        self._render_backup_repository_options()
