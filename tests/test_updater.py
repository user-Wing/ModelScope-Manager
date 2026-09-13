from __future__ import annotations

import unittest

from modelscope_manager.service import RemoteEntry
from modelscope_manager.updater import (
    Release,
    latest_newer_release,
    release_from_entry,
    validate_archive_paths,
    version_key,
)


class UpdaterTests(unittest.TestCase):
    def test_versions_sort_numerically(self) -> None:
        self.assertGreater(version_key("1.10.0"), version_key("1.9.9"))
        releases = [
            Release("1.9.9", "ModelScope-Manager/1.9.9.7z", "old"),
            Release("1.10.0", "ModelScope-Manager/1.10.0.7z", "new"),
        ]
        self.assertEqual(latest_newer_release("1.0.6", releases).version, "1.10.0")
        self.assertIsNone(latest_newer_release("1.10.0", releases))

    def test_only_direct_version_archives_are_releases(self) -> None:
        entry = RemoteEntry("ModelScope-Manager/1.0.6.7z", 42, "abc")
        release = release_from_entry(entry, "https://example.invalid/archive")
        self.assertEqual(release.version, "1.0.6")
        self.assertEqual(release.size, 42)
        self.assertIsNone(release_from_entry(RemoteEntry("other/1.0.7.7z"), "url"))
        self.assertIsNone(release_from_entry(RemoteEntry("ModelScope-Manager/latest.7z"), "url"))
        self.assertIsNone(release_from_entry(RemoteEntry("ModelScope-Manager/old/1.0.7.7z"), "url"))

    def test_archive_rejects_data_and_traversal(self) -> None:
        validate_archive_paths(["main.py", "runtime/python.exe", "modelscope_manager/app.py"])
        unsafe_paths = (
            "data/settings.ini",
            "ModelScope-Manager/data/settings.ini",
            "../escape.txt",
            "C:/escape.txt",
        )
        for unsafe in unsafe_paths:
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                validate_archive_paths([unsafe])


if __name__ == "__main__":
    unittest.main()
