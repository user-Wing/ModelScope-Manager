import inspect
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from modelscope_manager.app import CopyThread, MainWindow
from modelscope_manager.database import IndexedEntry
from modelscope_manager.page_resource import ResourcePageMixin
from modelscope_manager.service import RemoteEntry, Repository


class ResourceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _selection_harness():
        class Harness:
            _selected_visible_remote_entries = MainWindow._selected_visible_remote_entries
            _update_remote_selection_actions = MainWindow._update_remote_selection_actions
            _remote_detail_selected = MainWindow._remote_detail_selected
            _remote_detail_item_checked = MainWindow._remote_detail_item_checked
            _download_selected_remote = MainWindow._download_selected_remote

            def add_remote_download(self, entry):
                self.downloaded.append(entry.path)

        harness = Harness()
        harness.resource_view_mode = "details"
        harness.remote_detail_tree = QTreeWidget()
        harness.remote_detail_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        harness.remote_thumbnail_list = QListWidget()
        harness.remote_thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        harness.download_selected_button = QPushButton()
        harness.web_manage_button = QPushButton()
        harness.selected_repo = Repository("alice/demo", "dataset")
        harness.service = object()
        harness.active_account_kind = "web"
        harness.downloaded = []
        return harness

    def test_empty_active_view_ignores_stale_selection_and_cannot_download(self):
        harness = self._selection_harness()
        stale = QListWidgetItem("stale.mp4", harness.remote_thumbnail_list)
        stale.setData(Qt.ItemDataRole.UserRole, RemoteEntry("libvvenc/stale.mp4", 1))
        stale.setSelected(True)

        harness._update_remote_selection_actions()
        harness._download_selected_remote()

        self.assertFalse(harness.download_selected_button.isEnabled())
        self.assertFalse(harness.web_manage_button.isEnabled())
        self.assertEqual(harness.downloaded, [])

    def test_detail_checkboxes_and_extended_selection_stay_in_sync(self):
        harness = self._selection_harness()
        items = []
        for path in ("a.bin", "b.bin"):
            item = QTreeWidgetItem([path, "文件", "1 B"])
            item.setData(0, Qt.ItemDataRole.UserRole, RemoteEntry(path, 1))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            harness.remote_detail_tree.addTopLevelItem(item)
            item.setSelected(True)
            items.append(item)

        harness._remote_detail_selected()

        self.assertEqual(
            [entry.path for entry in harness._selected_visible_remote_entries()],
            ["a.bin", "b.bin"],
        )
        self.assertTrue(all(item.checkState(0) == Qt.CheckState.Checked for item in items))
        self.assertTrue(harness.download_selected_button.isEnabled())
        self.assertTrue(harness.web_manage_button.isEnabled())

        items[1].setCheckState(0, Qt.CheckState.Unchecked)
        harness._remote_detail_item_checked(items[1], 0)
        self.assertEqual(
            [entry.path for entry in harness._selected_visible_remote_entries()],
            ["a.bin"],
        )

    def test_context_menu_keeps_multi_selection_when_clicked_item_is_selected(self):
        tree = QTreeWidget()
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.resize(320, 220)
        entries = [RemoteEntry(f"{name}.bin", 1) for name in ("a", "b", "c")]
        items = []
        for entry in entries:
            item = QTreeWidgetItem([entry.path])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            tree.addTopLevelItem(item)
            items.append(item)
        tree.show()
        QApplication.processEvents()
        items[0].setSelected(True)
        items[1].setSelected(True)

        selected = MainWindow._context_selected_entries(
            tree, tree.visualItemRect(items[0]).center(), entries[0],
        )
        self.assertEqual({entry.path for entry in selected}, {"a.bin", "b.bin"})

        selected = MainWindow._context_selected_entries(
            tree, tree.visualItemRect(items[2]).center(), entries[2],
        )
        self.assertEqual([entry.path for entry in selected], ["c.bin"])
        tree.close()

    def test_multi_selection_copy_processes_every_selected_file(self):
        class Source:
            def download_to_file(self, _repo, remote_path, target):
                target.write_text(remote_path, encoding="utf-8")

        class Destination:
            def __init__(self):
                self.uploads = []

            def upload_file_as(self, _repo, local_path, remote_path):
                self.uploads.append((Path(local_path).read_text(encoding="utf-8"), remote_path))

        repo = Repository("alice/demo", "dataset")
        selected = [RemoteEntry("a.bin", 1), RemoteEntry("b.bin", 1)]
        destination = Destination()
        CopyThread(Source(), repo, selected, selected, destination, repo, "target").run()

        self.assertEqual(destination.uploads, [("a.bin", "target/a.bin"), ("b.bin", "target/b.bin")])

    def test_upload_actions_require_a_private_repository_and_token_service(self):
        class Harness:
            _update_resource_upload_actions = MainWindow._update_resource_upload_actions

            def _token_service_for_repo(self, _repo):
                return self.token_service

        harness = Harness()
        harness.upload_file_button = QPushButton()
        harness.upload_folder_button = QPushButton()
        harness.selected_repo = Repository("alice/demo", "dataset")
        harness.selected_repo_public = False
        harness.token_service = object()

        harness._update_resource_upload_actions()
        self.assertTrue(harness.upload_file_button.isEnabled())
        self.assertTrue(harness.upload_folder_button.isEnabled())

        harness.selected_repo_public = True
        harness._update_resource_upload_actions()
        self.assertFalse(harness.upload_file_button.isEnabled())
        self.assertFalse(harness.upload_folder_button.isEnabled())

    def test_all_index_columns_have_distinct_sort_keys(self):
        class Harness:
            _global_search_sort_key = MainWindow._global_search_sort_key
            _indexed_search_sort_key = staticmethod(MainWindow._indexed_search_sort_key)

        harness = Harness()
        first = IndexedEntry("a", "dataset", "z/repo", "z/path.txt", "a.txt", ".txt", "document", 20, "", False)
        second = IndexedEntry("a", "dataset", "a/repo", "a/path.mp4", "z.mp4", ".mp4", "video", 10, "", False)
        expected_first = [first, first, second, second, second]
        for column, expected in enumerate(expected_first):
            harness.global_search_sort_column = column
            with self.subTest(column=column):
                self.assertIs(min((first, second), key=harness._global_search_sort_key), expected)

    def test_large_index_results_are_rendered_in_bounded_chunks(self):
        class Harness:
            _render_global_search_chunk = MainWindow._render_global_search_chunk

        harness = Harness()
        harness.global_search_tree = QTreeWidget()
        harness.global_search_render_timer = QTimer()
        harness.global_search_render_index = 0
        harness._global_search_account_labels = {"a": "Account"}
        harness._global_search_type_labels = {"document": "文档"}
        harness.global_search_results = [
            IndexedEntry(
                "a", "dataset", "alice/demo", f"files/{index}.txt", f"{index}.txt",
                ".txt", "document", index, "", False,
            )
            for index in range(1001)
        ]

        harness._render_global_search_chunk()
        self.assertEqual(harness.global_search_tree.topLevelItemCount(), 400)
        self.assertEqual(harness.global_search_render_index, 400)
        harness._render_global_search_chunk()
        self.assertEqual(harness.global_search_tree.topLevelItemCount(), 800)
        harness._render_global_search_chunk()
        self.assertEqual(harness.global_search_tree.topLevelItemCount(), 1001)

    def test_actual_index_search_dialog_enables_headers_and_uses_clean_combos(self):
        source = inspect.getsource(MainWindow.show_resource_search)
        self.assertIn("header.setSectionsClickable(True)", source)
        self.assertIn("header.sectionClicked.connect(change_sort)", source)
        self.assertGreaterEqual(source.count("CleanComboBox()"), 3)
        self.assertNotIn('addItem("全部类型", "all")', source)
        self.assertIn('addItem("全部类型", userData="all")', source)

    def test_resource_group_selector_uses_clean_combo(self):
        source = inspect.getsource(ResourcePageMixin._build_resource_page)
        self.assertIn("self.group_by_combo = CleanComboBox()", source)
        self.assertIn('self.group_by_combo.addItem("无", userData="")', source)


if __name__ == "__main__":
    unittest.main()
