import unittest
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import re
import threading
from tempfile import TemporaryDirectory
import time

from modelscope_manager import __version__
from modelscope_manager.app import UploadQueueItem, UploadThread
from modelscope_manager.service import ModelScopeService, Repository
from modelscope_manager.transfer_statistics import TransferStatistics, UploadHealthMonitor


class TransferStatisticsTests(unittest.TestCase):
    def test_current_application_version(self):
        self.assertEqual(__version__, "1.0.7")

    def test_query_and_totals_use_requested_period(self):
        stats = TransferStatistics(100)
        stats.record_speeds(101, 10, 20)
        stats.add_bytes(101, upload=7, download=11)
        stats.add_bytes(105, upload=13, download=17)

        samples = stats.query(100, 102)

        self.assertEqual([sample.timestamp for sample in samples], [101])
        self.assertEqual(stats.totals(100, 102), (7, 11))
        self.assertEqual(stats.totals(100, 110), (20, 28))


class UploadHealthMonitorTests(unittest.TestCase):
    def test_reconnects_only_after_learning_and_sustained_slow_speed(self):
        monitor = UploadHealthMonitor(learning_duration=10, slow_duration=5, minimum_speed=1)

        self.assertFalse(monitor.update(0, 100, True))
        self.assertFalse(monitor.update(10, 100, True))
        self.assertEqual(monitor.learned_speed, 100)
        self.assertFalse(monitor.update(11, 49, True))
        self.assertFalse(monitor.update(15, 49, True))
        self.assertTrue(monitor.update(16, 49, True))
        self.assertFalse(monitor.update(30, 49, True))

        monitor.reset_after_reconnect()
        self.assertFalse(monitor.update(31, 60, True))
        self.assertFalse(monitor.update(32, 49, True))
        self.assertTrue(monitor.update(37, 49, True))

    def test_idle_time_does_not_count_as_slow_uploading(self):
        monitor = UploadHealthMonitor(learning_duration=1, slow_duration=2, minimum_speed=1)
        monitor.update(0, 100, True)
        monitor.update(1, 100, True)

        self.assertFalse(monitor.update(2, 0, False))
        self.assertFalse(monitor.update(20, 0, False))
        self.assertFalse(monitor.update(21, 49, True))
        self.assertTrue(monitor.update(23, 49, True))

    def test_short_gap_between_files_does_not_reset_learning(self):
        monitor = UploadHealthMonitor(
            learning_duration=10, slow_duration=5, minimum_speed=1, inactive_grace=3,
        )
        monitor.update(0, 100, True)
        monitor.update(5, 0, False)

        self.assertFalse(monitor.update(6, 100, True))
        self.assertFalse(monitor.update(10, 100, True))
        self.assertEqual(monitor.learned_speed, 100)


class UploadReconnectBoundaryTests(unittest.TestCase):
    def test_folder_uploads_files_concurrently_and_resumes_only_remaining_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "folder"
            root.mkdir()
            for name in ("a.bin", "b.bin", "c.bin", "d.bin"):
                (root / name).write_bytes(name.encode() * 10)
            item = UploadQueueItem(root, "target")
            service = _FakeUploadService()
            worker = UploadThread(
                service, Repository("owner/repo", "dataset"), item, True, 2,
            )
            service.after_upload = worker.request_reconnect

            worker.run()

            self.assertTrue(worker.stopped_for_reconnect)
            self.assertEqual(service.peak_active, 2)
            self.assertEqual(len(item.completed_files), 2)

            resumed = UploadThread(
                service, Repository("owner/repo", "dataset"), item, True, 2,
            )
            resumed.run()

            self.assertEqual(len(item.completed_files), 4)
            self.assertEqual(len(service.uploads), 4)
            self.assertEqual(
                {remote for _, remote in service.uploads},
                {f"target/folder/{name}" for name in ("a.bin", "b.bin", "c.bin", "d.bin")},
            )

    def test_successful_files_remain_committed_when_one_file_fails(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "folder"
            root.mkdir()
            for name in ("a.bin", "b.bin", "c.bin"):
                (root / name).write_bytes(name.encode())
            item = UploadQueueItem(root, "target")
            service = _FakeUploadService(fail_names={"b.bin"})
            worker = UploadThread(
                service, Repository("owner/repo", "dataset"), item, False, 3,
            )
            results = []
            worker.item_done.connect(lambda path, success, message: results.append((success, message)))

            worker.run()

            self.assertEqual({path.name for path in item.completed_files}, {"a.bin", "c.bin"})
            self.assertEqual(
                {remote for _, remote in service.uploads},
                {"target/a.bin", "target/c.bin"},
            )
            self.assertEqual(results[0][0], False)
            self.assertIn("2/3", results[0][1])

    def test_service_allows_independent_file_commits_to_run_concurrently(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "a.bin"
            second = root / "b.bin"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            api = _ConcurrentFakeApi()
            service = ModelScopeService.__new__(ModelScopeService)
            service.api = api

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        service.upload_file_as,
                        Repository("owner/repo", "dataset"),
                        path,
                        f"target/{path.name}",
                    )
                    for path in (first, second)
                ]
                for future in futures:
                    future.result()

            self.assertEqual(api.peak_active, 2)
            self.assertEqual(api.uploader._client.peak_active, 1)
            self.assertEqual(set(api.remote_paths), {"target/a.bin", "target/b.bin"})

    def test_sdk_progress_callbacks_are_isolated_per_file_thread(self):
        import modelscope_hub._upload as upload_module

        service = ModelScopeService.__new__(ModelScopeService)
        barrier = threading.Barrier(2)
        results = {"first": [], "second": []}

        def report(label, amount):
            with service.track_upload_progress(results[label].append):
                progress = upload_module.tqdm(total=amount, unit="B", disable=True)
                barrier.wait()
                progress.update(amount)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(report, "first", 11),
                executor.submit(report, "second", 22),
            ]
            for future in futures:
                future.result()

        self.assertEqual(results, {"first": [11], "second": [22]})


class LocalizationCoverageTests(unittest.TestCase):
    def test_all_translated_ui_calls_have_english_entries(self):
        project = Path(__file__).resolve().parents[1]
        translations = json.loads(
            (project / "modelscope_manager/locales/en_US.json").read_text(encoding="utf-8")
        )["ui"]
        used = set()
        for name in ("app.py", "fluent_ui.py"):
            tree = ast.parse((project / "modelscope_manager" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"_t", "_tf"} or not node.args:
                    continue
                source = node.args[0]
                if isinstance(source, ast.Constant) and isinstance(source.value, str):
                    if re.search(r"[\u4e00-\u9fff]", source.value):
                        used.add(source.value)
        self.assertEqual(used - translations.keys(), set())


class _FakeUploadService:
    def __init__(self, fail_names=None):
        self.callbacks = threading.local()
        self.lock = threading.Lock()
        self.after_upload = None
        self.fail_names = set(fail_names or ())
        self.uploads = []
        self.active = 0
        self.peak_active = 0

    @contextmanager
    def track_upload_progress(self, callback):
        self.callbacks.value = callback
        try:
            yield
        finally:
            del self.callbacks.value

    def upload_file_as(self, repo, local_path, remote_path):
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.02)
            self.callbacks.value(local_path.stat().st_size)
            if local_path.name in self.fail_names:
                raise RuntimeError(f"failed {local_path.name}")
            trigger = None
            with self.lock:
                self.uploads.append((local_path, remote_path))
                if self.after_upload is not None:
                    trigger, self.after_upload = self.after_upload, None
            if trigger:
                trigger()
        finally:
            with self.lock:
                self.active -= 1


class _ConcurrentFakeApi:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak_active = 0
        self.remote_paths = []
        self.uploader = _FakeUploader()

    def upload_file(self, repo_id, repo_type, local_path, remote_path, **kwargs):
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.02)
            with self.lock:
                self.remote_paths.append(remote_path)
        finally:
            with self.lock:
                self.active -= 1
        self.uploader._client.create_commit(repo_id=repo_id, repo_type=repo_type)


class _FakeUploader:
    def __init__(self):
        self._client = _FakeCommitClient()


class _FakeCommitClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak_active = 0

    def create_commit(self, **kwargs):
        with self.lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.02)
        finally:
            with self.lock:
                self.active -= 1


if __name__ == "__main__":
    unittest.main()
