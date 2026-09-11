from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from modelscope_manager.app import MainWindow, UploadThread


class AppStructureTests(unittest.TestCase):
    def test_app_is_a_small_compatibility_entrypoint(self) -> None:
        app_path = Path(inspect.getfile(MainWindow))
        self.assertLessEqual(len(app_path.read_text(encoding="utf-8").splitlines()), 500)
        self.assertEqual(MainWindow.__dict__.keys() & {"_build_ui", "load_repositories", "start_upload"}, set())

    def test_major_responsibilities_live_in_their_own_modules(self) -> None:
        expected_modules = {
            "_build_ui": "modelscope_manager.page_shell",
            "_build_settings_page": "modelscope_manager.page_settings",
            "open_online_login": "modelscope_manager.main_accounts",
            "_start_backup_job": "modelscope_manager.main_backups",
            "_upload_images": "modelscope_manager.main_image_bed",
            "_apply_language": "modelscope_manager.main_window_shell",
            "apply_alist_settings": "modelscope_manager.main_integrations",
            "load_repositories": "modelscope_manager.main_repository_browser",
            "_perform_global_search": "modelscope_manager.main_repository_search",
            "_delete_remote_entry": "modelscope_manager.main_remote_actions",
            "start_upload": "modelscope_manager.main_transfers",
        }
        for method_name, module_name in expected_modules.items():
            with self.subTest(method=method_name):
                self.assertEqual(getattr(MainWindow, method_name).__module__, module_name)
        self.assertEqual(UploadThread.__module__, "modelscope_manager.app_workers")

    def test_split_modules_stay_below_the_previous_god_file_threshold(self) -> None:
        package = Path(inspect.getfile(MainWindow)).parent
        split_modules = [
            path for path in package.glob("*.py")
            if path.stem.startswith(("app_", "main_", "page_")) or path.stem == "login_dialog"
        ]
        oversized = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in split_modules
            if len(path.read_text(encoding="utf-8").splitlines()) > 1000
        }
        self.assertEqual(oversized, {})


if __name__ == "__main__":
    unittest.main()
