from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from modelscope_manager.page_webdav_mapping import WebDAVMappingPageMixin
from modelscope_manager.service import Repository


class Harness(WebDAVMappingPageMixin):
    def __init__(self, *, with_token: bool):
        self.selected_repo = Repository("alice/demo", "dataset")
        self.selected_repo_public = False
        self.active_account_kind = "web"
        self.active_account_id = "web:web-alice"
        self.current_directory_path = ""
        self.web_accounts = [
            SimpleNamespace(account_id="web-alice", label="Alice Web", username="Alice"),
        ]
        self.accounts = []
        self.account_services = {}
        self.account_repositories = {}
        if with_token:
            account = SimpleNamespace(account_id="token-alice", label="Alice Token", username="alice")
            self.accounts.append(account)
            self.account_services[account.account_id] = SimpleNamespace(token="ms-test")
            self.account_repositories[account.account_id] = [self.selected_repo]
        self.mounted = None

    def _t(self, source: str) -> str:
        return source

    def _tf(self, source: str, **values) -> str:
        return source.format(**values)

    def _selected_visible_remote_entries(self):
        return []

    def _append_webdav_mount(self, source: str, suggested_name: str = "") -> None:
        self.mounted = (source, suggested_name)


class WebDAVMappingPageTests(unittest.TestCase):
    def test_web_account_mount_auto_uses_matching_token_account(self):
        harness = Harness(with_token=True)

        with patch.object(QMessageBox, "information") as information:
            harness._mount_current_remote_entry()

        self.assertEqual(harness.mounted, ("datasets/alice/demo", "demo"))
        self.assertIn("已自动使用 Token 账户", information.call_args.args[2])
        self.assertIn("Alice Token", information.call_args.args[2])

    def test_web_account_mount_is_blocked_without_matching_token_account(self):
        harness = Harness(with_token=False)

        with patch.object(QMessageBox, "information") as information:
            harness._mount_current_remote_entry()

        self.assertIsNone(harness.mounted)
        self.assertEqual(
            information.call_args.args[2],
            "网页登录账户用于补全刚需的删除功能，请使用token登录账户进行挂载。",
        )


if __name__ == "__main__":
    unittest.main()
