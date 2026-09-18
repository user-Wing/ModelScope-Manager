from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from modelscope_manager.main_url_import import import_http_urls, parse_http_urls, url_download_filename
from modelscope_manager.service import Repository


class UrlImportTests(unittest.TestCase):
    def test_accepts_only_http_and_https_and_deduplicates(self):
        self.assertEqual(
            parse_http_urls("https://example.com/a.bin\nhttp://example.com/b.bin\nhttps://example.com/a.bin"),
            ["https://example.com/a.bin", "http://example.com/b.bin"],
        )
        for invalid in ("ftp://example.com/a.bin", "file:///tmp/a.bin", "example.com/a.bin"):
            with self.assertRaises(ValueError):
                parse_http_urls(invalid)

    def test_url_filename_is_windows_safe_and_unique(self):
        used = set()
        self.assertEqual(url_download_filename("https://example.com/a%20b.zip", 1, used), "a b.zip")
        self.assertEqual(url_download_filename("https://other.example/a%20b.zip", 2, used), "a b-2.zip")
        self.assertEqual(url_download_filename("https://example.com/", 3, used), "download-3")

    def test_import_downloads_to_temporary_file_then_cleans_it(self):
        class Service:
            def __init__(self):
                self.calls = []

            def upload_file_as(self, repo, local_path, remote_path):
                self.calls.append((repo, local_path, remote_path, local_path.read_bytes()))

        service = Service()
        repo = Repository("alice/demo", "dataset")
        with patch("modelscope_manager.main_url_import.safe_urlopen", return_value=io.BytesIO(b"payload")):
            uploaded, failures = import_http_urls(
                ["https://example.com/file.bin"], service, repo, "nested",
            )

        self.assertEqual(failures, [])
        self.assertEqual(uploaded, ["nested/file.bin"])
        self.assertEqual(service.calls[0][2:], ("nested/file.bin", b"payload"))
        self.assertFalse(service.calls[0][1].exists())


if __name__ == "__main__":
    unittest.main()
