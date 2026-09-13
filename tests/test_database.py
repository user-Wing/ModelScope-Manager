import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modelscope_manager.database import (
    AccountRecord, AccountStore, WebAccountRecord, classify_file, initialize_database,
)
from modelscope_manager.service import ModelScopeService, MultiAccountService, RemoteEntry, Repository
from modelscope_manager.web_session import ModelScopeWebSession


class DatabaseTests(unittest.TestCase):
    def test_tags_filter_entries_and_are_removed_with_missing_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            store = AccountStore(path, "device")
            repo = Repository("alice/demo", "dataset")
            store.cache_entries("account", repo, [RemoteEntry("folder/file.txt", 3)])
            self.assertEqual(
                store.set_entry_tags("account", "dataset", "alice/demo", "folder/file.txt", ["工作", "#2026"]),
                ["#2026", "#工作"],
            )
            self.assertEqual(store.all_tags(), ["#2026", "#工作"])
            self.assertEqual(len(store.search_entries(tag_name="#工作")), 1)
            store.cache_entries("account", repo, [])
            self.assertEqual(store.all_tags(), [])

    def test_legacy_folder_index_is_migrated_into_manager_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "folder_sizes.sqlite3"
            connection = sqlite3.connect(legacy)
            connection.execute(
                "CREATE TABLE folder_sizes(public, repo_type, repo_id, folder_path, total_size, indexed_at, PRIMARY KEY(public, repo_type, repo_id, folder_path))"
            )
            connection.execute("INSERT INTO folder_sizes VALUES (0, 'dataset', 'alice/demo', 'folder', 12, 1)")
            connection.commit()
            connection.close()

            manager = root / "manager.sqlite3"
            initialize_database(manager, legacy)
            connection = sqlite3.connect(manager)
            try:
                row = connection.execute(
                    "SELECT total_size FROM folder_sizes WHERE repo_id='alice/demo' AND folder_path='folder'"
                ).fetchone()
                tables = {item[0] for item in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                connection.close()
            self.assertEqual(row, (12,))
            self.assertTrue({"accounts", "repositories", "entries", "folder_sizes"}.issubset(tables))

    def test_account_tokens_are_encrypted_and_device_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            with patch("modelscope_manager.database.protect", return_value="encrypted-value"), patch(
                "modelscope_manager.database.unprotect", return_value="secret-token"
            ):
                store = AccountStore(path, "device-a")
                saved = store.save(AccountRecord("", "Main", "alice", "secret-token", True))
                loaded = store.list_accounts()[0]
                self.assertEqual(loaded.token, "secret-token")
                self.assertNotIn("secret-token", path.read_bytes().decode("latin1", errors="ignore"))

                copied_store = AccountStore(path, "device-b")
                copied = copied_store.list_accounts()[0]
                self.assertEqual(copied.token, "")
                self.assertEqual(copied.status, "token_required")
                self.assertEqual(copied.account_id, saved.account_id)

    def test_replaced_device_identity_destroys_all_database_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            with patch("modelscope_manager.database.protect", return_value="encrypted-value"):
                original = AccountStore(path, "device-a")
                original.save(AccountRecord("", "Main", "alice", "secret-token", True))
            copied = AccountStore(path, "device-b", identity_replaced=True)
            self.assertTrue(copied.tokens_destroyed)
            self.assertEqual(copied.list_accounts()[0].token, "")
            connection = sqlite3.connect(path)
            try:
                cipher, bound_id = connection.execute(
                    "SELECT token_cipher, token_device_id FROM accounts"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual((cipher, bound_id), ("", ""))

    def test_plaintext_credentials_can_survive_device_change_when_destruction_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            session = ModelScopeWebSession("web-session", "csrf-session", "csrf-token")
            store = AccountStore(
                path, "device-a", plaintext_storage=True, destroy_on_device_change=False,
            )
            account = store.save(AccountRecord("", "Main", "alice", "secret-token", True))
            store.save_web_session(account.account_id, session)

            copied = AccountStore(
                path, "device-b", identity_replaced=True,
                plaintext_storage=True, destroy_on_device_change=False,
            )
            self.assertEqual(copied.list_accounts()[0].token, "secret-token")
            self.assertEqual(copied.load_web_session(account.account_id), session)
            self.assertFalse(copied.tokens_destroyed)
            connection = sqlite3.connect(path)
            try:
                token_cipher, session_cipher = connection.execute(
                    "SELECT token_cipher, session_cipher FROM accounts "
                    "JOIN account_web_sessions USING (account_id)"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(token_cipher, "p:secret-token")
            self.assertIn("web-session", session_cipher)

    def test_switching_plaintext_storage_migrates_saved_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            with patch("modelscope_manager.database.protect", return_value="u:encrypted"), patch(
                "modelscope_manager.database.unprotect", return_value="secret-token"
            ):
                store = AccountStore(path, "device-a")
                store.save(AccountRecord("", "Main", token="secret-token", remember=True))
                self.assertEqual(store.set_plaintext_storage(True), 1)
            connection = sqlite3.connect(path)
            try:
                cipher = connection.execute("SELECT token_cipher FROM accounts").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(cipher, "p:secret-token")

    def test_web_session_is_encrypted_device_bound_and_independent_from_token_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            session = ModelScopeWebSession("web-session", "csrf-session", "csrf-token%3D")
            with patch(
                "modelscope_manager.database.protect",
                side_effect=lambda value: "encrypted:" + value.encode("utf-8").hex(),
            ), patch(
                "modelscope_manager.database.unprotect",
                side_effect=lambda value: bytes.fromhex(value.removeprefix("encrypted:")).decode("utf-8"),
            ):
                store = AccountStore(path, "device-a")
                account = store.save(AccountRecord("", "Main", "alice", "token", True))
                store.save_web_session(account.account_id, session)
                self.assertEqual(store.load_web_session(account.account_id), session)
                self.assertNotIn("web-session", path.read_bytes().decode("latin1", errors="ignore"))
                self.assertIsNone(AccountStore(path, "device-b").load_web_session(account.account_id))

                store.save_web_session(account.account_id, session)
                store.remove(account.account_id)
                self.assertEqual(store.load_web_session(account.account_id), session)

    def test_existing_attached_web_session_migrates_to_independent_web_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            with patch(
                "modelscope_manager.database.protect",
                side_effect=lambda value: "encrypted:" + value.encode("utf-8").hex(),
            ), patch(
                "modelscope_manager.database.unprotect",
                side_effect=lambda value: bytes.fromhex(value.removeprefix("encrypted:")).decode("utf-8"),
            ):
                store = AccountStore(path, "device-a")
                token_account = store.save(AccountRecord("", "Legacy", "alice", "token", True))
                session = ModelScopeWebSession("web", "csrf-session", "csrf-token")
                store.save_web_session(token_account.account_id, session)

                initialize_database(path)
                web_accounts = store.list_web_accounts()

                self.assertEqual(
                    web_accounts,
                    [WebAccountRecord(token_account.account_id, "Legacy", "alice", "connected")],
                )
                self.assertEqual(store.load_web_session(web_accounts[0].account_id), session)

    def test_web_accounts_are_independent_and_removal_destroys_only_their_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            with patch(
                "modelscope_manager.database.protect",
                side_effect=lambda value: "encrypted:" + value.encode("utf-8").hex(),
            ), patch(
                "modelscope_manager.database.unprotect",
                side_effect=lambda value: bytes.fromhex(value.removeprefix("encrypted:")).decode("utf-8"),
            ):
                store = AccountStore(path, "device-a")
                token_account = store.save(AccountRecord("", "Token", "alice", "token", True))
                web_account = store.save_web_account(WebAccountRecord("", "Web", "alice"))
                session = ModelScopeWebSession("web", "csrf-session", "csrf-token")
                store.save_web_session(web_account.account_id, session)

                store.remove_web_account(web_account.account_id)

                self.assertEqual(store.list_accounts()[0].account_id, token_account.account_id)
                self.assertEqual(store.list_web_accounts(), [])
                self.assertIsNone(store.load_web_session(web_account.account_id))

    def test_entry_index_supports_name_type_and_repository_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            store = AccountStore(path, "device-a")
            repo = Repository("alice/media", "dataset")
            store.cache_entries("account-a", repo, [
                RemoteEntry("clips/demo.MP4", 120, "video-hash"),
                RemoteEntry("docs/readme.pdf", 40, "doc-hash"),
                RemoteEntry("clips", is_dir=True),
            ])
            self.assertEqual(store.search_entries("demo")[0].file_type, "video")
            self.assertEqual(store.search_entries(file_type="document")[0].path, "docs/readme.pdf")
            self.assertEqual(store.search_entries(path_prefix="clips")[0].path, "clips/demo.MP4")
            self.assertEqual(len(store.search_entries(account_id="other")), 0)
            self.assertEqual(len(store.repository_entries("account-a", "dataset", "alice/media")), 3)
            self.assertEqual(classify_file("bundle.tar.gz"), (".tar.gz", "archive"))

    def test_remove_entry_prefixes_updates_only_the_selected_subtree(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            store = AccountStore(path, "device")
            repo = Repository("alice/demo", "dataset")
            store.cache_entries("account", repo, [
                RemoteEntry("folder/a.txt", 1),
                RemoteEntry("folder/deep/b.txt", 2),
                RemoteEntry("folder_escaped/keep.txt", 3),
                RemoteEntry("other.txt", 4),
            ])
            store.set_entry_tags("account", "dataset", "alice/demo", "folder/a.txt", ["remove"])

            store.remove_entry_prefixes("account", "dataset", "alice/demo", ["folder"])

            self.assertEqual(
                [entry.path for entry in store.repository_entries("account", "dataset", "alice/demo")],
                ["folder_escaped/keep.txt", "other.txt"],
            )
            self.assertEqual(store.all_tags(), [])

    def test_search_returns_all_files_and_matches_every_term(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manager.sqlite3"
            initialize_database(path)
            store = AccountStore(path, "device")
            repo = Repository("alice/large", "dataset")
            entries = [
                RemoteEntry(f"images/2026/frame_{index:04d}_final.jpg", index)
                for index in range(620)
            ]
            entries.append(RemoteEntry("videos/2026/demo_first_middle_last.mp4", 9999))
            store.cache_entries("account", repo, entries)

            self.assertEqual(len(store.search_entries()), 621)
            self.assertEqual(
                [entry.path for entry in store.search_entries("videos first last")],
                ["videos/2026/demo_first_middle_last.mp4"],
            )
            self.assertEqual(
                [entry.path for entry in store.search_entries("path:2026 name:middle type:video")],
                ["videos/2026/demo_first_middle_last.mp4"],
            )


class FakeAccountService:
    def __init__(self, marker):
        self.marker = marker

    def list_entries(self, repo):
        return [RemoteEntry(f"{self.marker}.txt", 1)]

    def get_download_url(self, repo, path):
        return f"https://example.test/{self.marker}/{path}"

    def upload_file_as(self, repo, local_path, remote_path):
        return self.marker, remote_path


class MultiAccountServiceTests(unittest.TestCase):
    def test_repository_operations_route_to_owning_account(self):
        first_repo = Repository("alice/data", "dataset")
        second_repo = Repository("bob/data", "dataset")
        service = MultiAccountService(
            {"a": FakeAccountService("alice"), "b": FakeAccountService("bob")},
            {"a": [first_repo], "b": [second_repo]},
        )
        self.assertEqual(service.list_entries(first_repo)[0].path, "alice.txt")
        self.assertEqual(service.list_entries(second_repo)[0].path, "bob.txt")
        self.assertEqual(len(service.list_repositories()), 2)
