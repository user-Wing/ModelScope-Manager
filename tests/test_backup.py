import tempfile
import unittest
from pathlib import Path

from modelscope_manager.app import BackupThread
from modelscope_manager.backup import BackupJob, BackupStore
from modelscope_manager.database import initialize_database
from modelscope_manager.service import Repository


class FakeUploadService:
    def __init__(self):
        self.uploads = []

    def upload_file_as(self, repo, local_path, remote_path):
        self.uploads.append((repo, Path(local_path), remote_path))


class BackupTests(unittest.TestCase):
    def test_store_detects_changes_and_persists_state_without_size_filtering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "manager.sqlite3"
            initialize_database(database)
            local = root / "local"
            local.mkdir()
            (local / "small.txt").write_text("small", encoding="utf-8")
            (local / "large.bin").write_bytes(b"large")
            store = BackupStore(database)
            job = store.save_job(BackupJob(
                name="Vault", account_id="a", local_path=str(local),
                repo_id="alice/data", interval_value=1,
            ))
            changed, oversized = store.scan_changes(job)
            self.assertEqual({item.relative_path for item in changed}, {"small.txt", "large.bin"})
            self.assertEqual(oversized, [])
            self.assertEqual(len(changed), 2)
            store.mark_uploaded(job.job_id, changed[0], f"backup/{changed[0].relative_path}")
            remaining, _ = store.scan_changes(job)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(len(store.due_jobs()), 1)
            store.mark_scan(job.job_id)
            self.assertEqual(len(store.due_jobs()), 0)

    def test_incremental_and_replace_modes_generate_expected_remote_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "manager.sqlite3"
            initialize_database(database)
            local = root / "local"
            (local / "nested").mkdir(parents=True)
            (local / "nested" / "file.txt").write_text("payload", encoding="utf-8")
            store = BackupStore(database)
            repo = Repository("alice/data", "dataset")

            incremental = store.save_job(BackupJob(
                name="Incremental", account_id="a", local_path=str(local),
                repo_id=repo.repo_id, dest_dir="vault", mode="incremental",
            ))
            incremental_service = FakeUploadService()
            BackupThread(store, incremental, incremental_service, repo).run()
            incremental_path = incremental_service.uploads[0][2]
            self.assertRegex(incremental_path, r"^vault/\d{8}-\d{6}/nested/file\.txt$")

            (local / "nested" / "file.txt").write_text("changed payload", encoding="utf-8")
            replacement = store.save_job(BackupJob(
                name="Replace", account_id="a", local_path=str(local),
                repo_id=repo.repo_id, dest_dir="mirror", mode="replace",
            ))
            replacement_service = FakeUploadService()
            BackupThread(store, replacement, replacement_service, repo).run()
            self.assertEqual(replacement_service.uploads[0][2], "mirror/nested/file.txt")
