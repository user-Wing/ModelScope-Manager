import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from modelscope_manager.app import UploadQueueItem, UploadThread
from modelscope_manager.service import Repository


class FakeUploadService:
    def __init__(self):
        self.callback = None
        self.calls = []

    @contextmanager
    def track_upload_progress(self, callback):
        self.callback = callback
        yield

    def upload_file_as(self, repo, path, target):
        self.calls.append((repo, path, target))
        self.callback(path.stat().st_size)


class UploadThreadTests(unittest.TestCase):
    def test_upload_uses_the_item_target_and_reports_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example.txt"
            path.write_text("ok", encoding="utf-8")
            service = FakeUploadService()
            completed = []
            worker = UploadThread(
                service, Repository("alice/demo", "dataset"),
                UploadQueueItem(path, "one/two"), True,
            )
            worker.item_done.connect(lambda *result: completed.append(result))
            worker.run()
        self.assertEqual(service.calls[0][2], "one/two/example.txt")
        self.assertEqual(completed, [(str(path), True, "上传完成：1 个文件已分别提交")])

    def test_cancelled_upload_does_not_start_the_sdk_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example.txt"
            path.write_text("ok", encoding="utf-8")
            service = FakeUploadService()
            cancelled = []
            worker = UploadThread(
                service, Repository("alice/demo", "dataset"), UploadQueueItem(path, ""), True,
            )
            worker.cancelled.connect(cancelled.append)
            worker.cancel()
            worker.run()
        self.assertEqual(service.calls, [])
        self.assertEqual(cancelled, [str(path)])
