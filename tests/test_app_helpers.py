import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeyEvent
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import (
    QApplication, QLabel, QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTreeWidgetItem,
)
from qfluentwidgets import SettingCardGroup

import modelscope_manager.app as app_module
import modelscope_manager.app_workers as app_workers_module
from modelscope_manager.app import (
    CopyThread, DeleteThread, MainWindow, ModelScopeLoginDialog, PathBreadcrumb, RelocateThread,
    RepositoryList, RepositoryTree, ThumbnailThread,
    THUMBNAIL_RENDER_SIZE, VIDEO_THUMBNAIL_SEEK_SECONDS,
    UploadQueueItem,
    breadcrumb_levels, copy_name, everything_search_match, find_available_port,
    local_path_identity, local_paths_size, repository_file_url, repository_identity,
    repository_is_public, running_download_percent,
    thumbnail_batch_policy,
)
from modelscope_manager.fluent_ui import ControlSettingCard, PanelSettingCard
from modelscope_manager.download_service import DownloadSpec
from modelscope_manager.service import RemoteEntry, Repository


class FakeDropEvent:
    def __init__(self, position: QPointF, paths: list[Path]):
        self._position = position
        self._mime_data = QMimeData()
        self._mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        self.accepted = False
        self.ignored = False

    def position(self) -> QPointF:
        return self._position

    def mimeData(self) -> QMimeData:
        return self._mime_data

    def acceptProposedAction(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class AppHelperTests(unittest.TestCase):
    def test_modelscope_login_cookie_parts_accept_qt_domain_string(self):
        cookie = QNetworkCookie(b"m_session_id", b"session-value")
        cookie.setDomain(".modelscope.cn")
        self.assertEqual(
            ModelScopeLoginDialog._cookie_parts(cookie),
            ("m_session_id", "session-value", "modelscope.cn"),
        )

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_everything_search_matches_split_path_and_name_terms(self):
        path = "images/2026/08/ABC_final_render.jpg"
        self.assertTrue(everything_search_match(path, False, "2026 render"))
        self.assertTrue(everything_search_match(path, False, "abc final"))
        self.assertTrue(everything_search_match(path, False, "path:2026 name:render"))
        self.assertTrue(everything_search_match(path, False, "ext:jpg type:image"))
        self.assertTrue(everything_search_match(path, False, '"final_render"'))
        self.assertFalse(everything_search_match(path, False, "2026 missing"))

    def test_repository_file_urls_separate_public_and_private_forms(self):
        repo = Repository("ARXChem/Pictures-Share", "dataset")
        path = "images/2026/08/example image.jpg"
        self.assertEqual(
            repository_file_url(repo, path, True),
            "https://modelscope.cn/datasets/ARXChem/Pictures-Share/resolve/master/images/2026/08/example%20image.jpg",
        )
        self.assertEqual(
            repository_file_url(repo, "README.md", False),
            "https://modelscope.cn/api/v1/datasets/ARXChem/Pictures-Share/repo?Revision=master&FilePath=README.md",
        )

    def test_transfer_callback_identities_ignore_refresh_metadata_and_path_spelling(self):
        before = Repository("alice/demo", "model", "private", "2026-08-24")
        refreshed = Repository("alice/demo", "model", "private", "2026-08-25")
        self.assertEqual(repository_identity(before), repository_identity(refreshed))
        self.assertEqual(local_path_identity(r"C:\Downloads\File.bin"), local_path_identity(r"c:\downloads\file.bin"))

    def test_running_download_progress_reserves_100_percent_for_completion(self):
        self.assertEqual(running_download_percent(0, 100), 0)
        self.assertEqual(running_download_percent(100, 100), 99)

    def test_download_status_callback_updates_canonical_queue_path(self):
        class Harness:
            _download_item_update = MainWindow._download_item_update
            _t = staticmethod(lambda text: text)
            _tf = staticmethod(lambda text, **values: text.format(**values))

        harness = Harness()
        canonical = Path(r"C:\Downloads\File.bin")
        harness.download_specs = [DownloadSpec("File.bin", canonical, "https://example.test/File.bin", 100)]
        harness.download_states = {str(canonical): "waiting"}
        harness.download_table = QTableWidget(1, 3)
        harness.download_table.setItem(0, 2, QTableWidgetItem("等待下载"))
        harness.backup_sync_job_paths = {}
        harness.backup_jobs = []
        harness.potplayer_install_archive = None

        harness._download_item_update(r"c:\downloads\file.bin", "downloading", 25, 100, "下载中")

        self.assertEqual(harness.download_states[str(canonical)], "downloading")
        self.assertEqual(harness.download_table.item(0, 2).text(), "25% · 下载中")

    def test_repository_visibility_accepts_modelscope_numeric_public_value(self):
        self.assertTrue(repository_is_public(Repository("alice/public", "dataset", "5"), "token"))
        self.assertTrue(repository_is_public(Repository("alice/public", "dataset", "public"), "token"))
        self.assertFalse(repository_is_public(Repository("alice/unknown", "dataset", ""), ""))
        self.assertFalse(repository_is_public(Repository("alice/private", "dataset", "1"), "token"))
        self.assertFalse(repository_is_public(Repository("alice/internal", "dataset", "3"), "token"))
        self.assertFalse(repository_is_public(Repository("alice/unknown", "dataset", ""), "token"))

    def test_thumbnail_queues_use_aggressive_parallelism(self):
        self.assertEqual(thumbnail_batch_policy(100), (48, 16, 20))
        self.assertEqual(thumbnail_batch_policy(101), (96, 32, 10))

    def test_video_thumbnail_fast_seeks_and_renders_larger_source(self):
        class Service:
            token = ""

            @staticmethod
            def get_download_url(_repo, _path):
                return "https://example.invalid/video.mp4"

        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            thread = ThumbnailThread(
                Service(), Repository("alice/videos", "dataset"),
                [RemoteEntry("clip.mp4", size=50_000_000)], 1, workers=1,
            )

            def complete_thumbnail(command, **_kwargs):
                Path(command[-1]).write_bytes(b"jpeg")

            with patch.object(app_workers_module, "THUMBNAIL_CACHE_DIR", cache), patch.object(
                app_workers_module.subprocess, "run", side_effect=complete_thumbnail
            ) as run:
                result = thread._create_thumbnail(thread.entries[0], "ffmpeg.exe")

            self.assertIsNotNone(result)
            command = run.call_args.args[0]
            self.assertLess(command.index("-ss"), command.index("-i"))
            self.assertEqual(command[command.index("-ss") + 1], str(VIDEO_THUMBNAIL_SEEK_SECONDS))
            width, height = THUMBNAIL_RENDER_SIZE
            self.assertIn(f"scale={width}:{height}", command[command.index("-vf") + 1])
            self.assertEqual(command[command.index("-q:v") + 1], "3")

    def test_copy_name_preserves_file_suffix_and_directory_dots(self):
        self.assertEqual(copy_name("frame.png"), "frame-copy.png")
        self.assertEqual(copy_name("archive.tar.gz"), "archive.tar-copy.gz")
        self.assertEqual(copy_name("folder.v1", is_dir=True), "folder.v1-copy")

    def test_same_folder_copy_renames_local_and_remote_targets(self):
        class Source:
            token = ""

            def __init__(self, payload: Path):
                self.payload = payload

            def get_download_url(self, _repo, _path):
                return self.payload.as_uri()

        class Destination:
            token = ""

            def __init__(self):
                self.uploads = []

            def upload_file_as(self, _repo, local_path, remote_path):
                self.uploads.append((local_path.name, local_path.parent.name, remote_path))

        with tempfile.TemporaryDirectory() as folder:
            payload = Path(folder) / "payload.bin"
            payload.write_bytes(b"copy")
            repo = Repository("alice/example", "dataset")
            destination = Destination()
            CopyThread(
                Source(payload), repo, [RemoteEntry("frame.png", 4)], RemoteEntry("frame.png", 4),
                destination, repo, "",
            ).run()
            self.assertEqual(destination.uploads[0][0], "frame-copy.png")
            self.assertEqual(destination.uploads[0][2], "frame-copy.png")

            destination.uploads.clear()
            CopyThread(
                Source(payload), repo, [RemoteEntry("folder/a.txt", 4)], RemoteEntry("folder", is_dir=True),
                destination, repo, "",
            ).run()
            self.assertEqual(destination.uploads, [("a.txt", "folder-copy", "folder-copy/a.txt")])

    def test_relocate_deletes_sources_only_after_every_upload_succeeds(self):
        class Source:
            def download_to_file(self, _repo, remote_path, target):
                target.write_text(remote_path, encoding="utf-8")

        class Destination:
            def __init__(self, fail=""):
                self.fail = fail

            def upload_file_as(self, _repo, _local, remote_path):
                if remote_path == self.fail:
                    raise RuntimeError("upload failed")

        repo = Repository("alice/example", "dataset")
        mappings = {"old/a.txt": "new/a.txt", "old/b.txt": "new/b.txt"}
        deleted = []
        failed = RelocateThread(Source(), repo, Destination("new/b.txt"), repo, mappings, deleted.append)
        failed.run()
        self.assertEqual(deleted, [])
        self.assertEqual(failed.result["upload_failed"], ["old/b.txt"])

        succeeded = RelocateThread(Source(), repo, Destination(), repo, mappings, deleted.append)
        succeeded.run()
        self.assertEqual(deleted, ["old/b.txt", "old/a.txt"])
        self.assertEqual(succeeded.result["deleted"], ["old/b.txt", "old/a.txt"])

    def test_folder_mutations_expand_to_files_and_preserve_relative_paths(self):
        selected = RemoteEntry("old/folder", is_dir=True)
        entries = [
            RemoteEntry("old/folder/a.txt", 1),
            RemoteEntry("old/folder/deep/B.txt", 2),
            RemoteEntry("old/other.txt", 3),
        ]
        self.assertEqual(
            MainWindow._entry_file_paths(selected, entries),
            ["old/folder/deep/B.txt", "old/folder/a.txt"],
        )
        self.assertEqual(
            MainWindow._relocate_mappings(selected, entries, "new/Folder"),
            {
                "old/folder/a.txt": "new/Folder/a.txt",
                "old/folder/deep/B.txt": "new/Folder/deep/B.txt",
            },
        )

    def test_delete_thread_batches_files_and_reconciles_an_uncertain_commit(self):
        repo = Repository("alice/example", "dataset")
        paths = ["folder/a.txt", "folder/deep/b.txt"]
        results = []
        thread = DeleteThread(object(), repo, paths)
        thread.completed.connect(results.append)
        with (
            patch.object(app_workers_module, "delete_repository_files", side_effect=RuntimeError("timeout")) as batch,
            patch.object(app_workers_module, "list_repository_file_paths", return_value=["folder/deep/b.txt"]),
            patch.object(app_workers_module, "delete_repository_file") as single,
        ):
            thread.run()

        batch.assert_called_once()
        single.assert_called_once_with(thread.session, repo.repo_id, repo.repo_type, "folder/deep/b.txt")
        self.assertEqual(results[0], {"deleted": paths, "failures": {}})

    def test_size_groups_use_requested_boundaries(self):
        self.assertEqual(MainWindow._size_group(1024 * 1024 - 1), "<1MB")
        self.assertEqual(MainWindow._size_group(1024 * 1024), "1MB-1GB")
        self.assertEqual(MainWindow._size_group(1024 * 1024 * 1024), "1MB-1GB")
        self.assertEqual(MainWindow._size_group(1024 * 1024 * 1024 + 1), ">1GB")

    def test_direct_entries_exclude_paths_outside_selected_folder(self):
        entries = [
            RemoteEntry("folder/a.bin", 2),
            RemoteEntry("folder/deep/b.bin", 3),
            RemoteEntry("root.bin", 4),
        ]
        direct = MainWindow._direct_remote_entries(entries, "folder")
        self.assertEqual([entry.path for entry in direct], ["folder/a.bin", "folder/deep"])

    def test_port_probe_skips_an_occupied_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            occupied = listener.getsockname()[1]
            self.assertNotEqual(find_available_port("127.0.0.1", occupied), occupied)

    def test_folder_share_url_preserves_nested_path(self):
        repo = Repository("alice/example", "dataset")
        url = MainWindow._repository_web_url(repo, "magia record/视频")
        self.assertEqual(
            url,
            "https://www.modelscope.cn/datasets/alice/example/files?path=magia%20record/%E8%A7%86%E9%A2%91",
        )

    def test_local_paths_size_counts_files_and_folders_without_nested_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            standalone = root / "standalone.bin"
            standalone.write_bytes(b"1234")
            folder = root / "folder"
            folder.mkdir()
            nested = folder / "nested.bin"
            nested.write_bytes(b"123456")

            self.assertEqual(local_paths_size([standalone, folder]), 10)
            self.assertEqual(local_paths_size([folder, nested]), 6)

    def test_breadcrumb_levels_include_root_and_each_ancestor(self):
        self.assertEqual(
            breadcrumb_levels("models/video/frames"),
            [("根目录", ""), ("models", "models"), ("video", "models/video"), ("frames", "models/video/frames")],
        )

    def test_path_breadcrumb_uses_overflow_menu_when_ancestors_do_not_fit(self):
        breadcrumb = PathBreadcrumb()
        breadcrumb.setFixedWidth(180)
        breadcrumb.set_path("models/very-long-directory/video/frames")
        breadcrumb.show()
        QApplication.processEvents()

        self.assertTrue(breadcrumb.overflow_button.isVisible())
        self.assertGreater(len(breadcrumb.overflow_button.menu().actions()), 0)
        breadcrumb.close()

    def test_repository_tree_drops_file_and_folder_on_directory_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file_path = root / "file.txt"
            file_path.write_text("content", encoding="utf-8")
            folder_path = root / "folder"
            folder_path.mkdir()
            tree = RepositoryTree()
            tree.resize(360, 240)
            item = QTreeWidgetItem(["target"])
            entry = RemoteEntry("models/target", is_dir=True)
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            tree.addTopLevelItem(item)
            tree.show()
            QApplication.processEvents()
            dropped = []
            tree.paths_dropped.connect(lambda paths, target: dropped.append((paths, target)))

            event = FakeDropEvent(QPointF(tree.visualItemRect(item).center()), [file_path, folder_path])
            tree.dropEvent(event)

            self.assertTrue(event.accepted)
            self.assertEqual(
                [Path(path).resolve() for path in dropped[0][0]],
                [file_path.resolve(), folder_path.resolve()],
            )
            self.assertEqual(dropped[0][1], entry)
            tree.close()

    def test_repository_tree_drops_on_current_directory_background(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder_path = Path(temporary) / "folder"
            folder_path.mkdir()
            tree = RepositoryTree()
            tree.resize(360, 240)
            current = RemoteEntry("models/current", is_dir=True)
            tree.set_drop_directory(current)
            tree.show()
            QApplication.processEvents()
            dropped = []
            tree.paths_dropped.connect(lambda paths, target: dropped.append((paths, target)))

            position = QPoint(20, tree.viewport().height() - 10)
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(str(folder_path))])
            enter_event = QDragEnterEvent(
                position, Qt.DropAction.CopyAction, mime_data,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(tree.viewport(), enter_event)
            event = QDropEvent(
                QPointF(position), Qt.DropAction.CopyAction, mime_data,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(tree.viewport(), event)

            self.assertTrue(enter_event.isAccepted())
            self.assertTrue(event.isAccepted())
            self.assertEqual([Path(path).resolve() for path in dropped[0][0]], [folder_path.resolve()])
            self.assertEqual(dropped[0][1], current)
            tree.close()

    def test_repository_list_drops_on_current_directory_background(self):
        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "file.txt"
            file_path.write_text("content", encoding="utf-8")
            widget = RepositoryList()
            widget.resize(360, 240)
            current = RemoteEntry("images/current", is_dir=True)
            widget.set_drop_directory(current)
            widget.addItem(QListWidgetItem("existing.bin"))
            widget.show()
            QApplication.processEvents()
            dropped = []
            widget.paths_dropped.connect(lambda paths, target: dropped.append((paths, target)))

            position = QPoint(20, widget.viewport().height() - 10)
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(str(file_path))])
            enter_event = QDragEnterEvent(
                position, Qt.DropAction.CopyAction, mime_data,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(widget.viewport(), enter_event)
            event = QDropEvent(
                QPointF(position), Qt.DropAction.CopyAction, mime_data,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(widget.viewport(), event)

            self.assertTrue(enter_event.isAccepted())
            self.assertTrue(event.isAccepted())
            self.assertEqual([Path(path).resolve() for path in dropped[0][0]], [file_path.resolve()])
            self.assertEqual(dropped[0][1], current)
            widget.close()

    def test_main_window_background_drop_queues_file_and_folder_and_auto_starts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file_path = root / "file.txt"
            file_path.write_text("content", encoding="utf-8")
            folder_path = root / "folder"
            folder_path.mkdir()
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            for key in ("language", "theme", "close_behavior", "copy/threshold_unit", "alist/host"):
                settings.setValue(key, None)
            paths = {
                "DEVICE_ID_PATH": root / "device.id",
                "FOLDER_INDEX_PATH": root / "folder_sizes.sqlite3",
                "IMAGE_CACHE_DIR": root / "image_cache",
                "THUMBNAIL_CACHE_DIR": root / "thumbnails",
                "MANAGER_DB_PATH": root / "manager.sqlite3",
                "PLAYER_DOWNLOAD_DIR": root / "player_download",
                "POTPLAYER_DIR": root / "players" / "potplayer",
                "PUBLIC_POOLS_PATH": root / "public_pools.json",
            }
            with (
                patch.multiple(app_module, **paths),
                patch.object(app_module, "portable_settings", return_value=settings),
                patch.object(app_module.DeviceIdentity, "load_or_create", return_value=("device", False)),
                patch.object(app_module, "windows_startup_enabled", return_value=False),
            ):
                window = MainWindow()
                self.assertFalse(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
                self.assertTrue(all(
                    window.stackedWidget.widget(index).property("isStackedTransparent") is False
                    for index in range(window.stackedWidget.count())
                ))
                self.assertIsInstance(window.alist_port_control.parent(), ControlSettingCard)
                self.assertEqual(window.alist_port.value(), 9867)
                self.assertEqual(window.language_combo.currentData(), "zh_CN")
                self.assertEqual(window.theme_combo.currentData(), "system")
                self.assertEqual(window.close_behavior_combo.currentData(), "ask")
                self.assertEqual(window.copy_threshold_unit.currentData(), 1024 ** 2)
                self.assertEqual(window.alist_host_combo.currentData(), "127.0.0.1")
                self.assertEqual(settings.value("language"), "zh_CN")
                self.assertEqual(settings.value("theme"), "system")
                self.assertEqual(settings.value("close_behavior"), "ask")
                self.assertEqual(int(settings.value("copy/threshold_unit")), 1024 ** 2)
                self.assertEqual(settings.value("alist/host"), "127.0.0.1")
                account_texts = {label.text() for label in window.settings_page.findChildren(QLabel)}
                self.assertIn("Token 登录", account_texts)
                self.assertIn("ModelScope 账户 在线登录", account_texts)
                panel_cards = {
                    card.titleLabel.text(): card
                    for card in window.settings_page.findChildren(PanelSettingCard)
                }
                account_card = panel_cards["ModelScope 账户"]
                account_card.setExpanded(True, animated=False)
                QApplication.processEvents()
                self.assertEqual(window.token_heading_label.y(), window.add_account_button.y())
                self.assertEqual(window.token_heading_label.y(), window.remove_account_button.y())
                self.assertLess(window.token_heading_label.height(), window.add_account_button.height())
                self.assertGreaterEqual(
                    window.online_separator.y() - (window.account_label.y() + window.account_label.height()), 12,
                )
                player_card = panel_cards["媒体播放器"]
                player_card.setExpanded(True, animated=False)
                QApplication.processEvents()
                self.assertEqual(window.player_heading_label.y(), window.add_player_button.y())
                self.assertEqual(window.player_heading_label.y(), window.remove_player_button.y())
                self.assertLess(window.player_heading_label.height(), window.add_player_button.height())
                download_card = panel_cards["下载与传输"]
                self.assertEqual(download_card.panel.layout().spacing(), 14)
                aria_heading = next(
                    label for label in download_card.panel.findChildren(QLabel)
                    if label.text() == "aria2-next 详细配置"
                )
                self.assertEqual(aria_heading.parentWidget().layout().spacing(), 14)
                close_event = QCloseEvent()
                with (
                    patch.object(QMessageBox, "exec", return_value=0) as close_prompt,
                    patch.object(QMessageBox, "clickedButton", return_value=None),
                ):
                    window.closeEvent(close_event)
                close_prompt.assert_called_once()
                self.assertFalse(close_event.isAccepted())
                self.assertEqual(
                    {group.titleLabel.text() for group in window.settings_page.findChildren(SettingCardGroup)},
                    {
                        "基本设置", "个性化", "账号设置", "下载设置", "WebDAV 设置",
                        "播放设置", "索引和预览", "资源监控",
                    },
                )

                window.language_combo.setCurrentIndex(window.language_combo.findData("en_US"))
                window.theme_combo.setCurrentIndex(window.theme_combo.findData("dark"))
                window.upload_items = [UploadQueueItem(file_path, "", "uploading")]
                window._render_upload_queue()
                self.assertGreater(window.queue_table.item(0, 3).foreground().color().lightness(), 128)
                window.upload_items.clear()
                window._render_upload_queue()
                window.close_behavior_combo.setCurrentIndex(window.close_behavior_combo.findData("tray"))
                window.font_size_spin.setValue(11)
                window.gpu_acceleration_checkbox.setChecked(False)
                window.acrylic_checkbox.setChecked(False)
                window.disable_settings_wheel.setChecked(False)
                window.background_index_minutes.setValue(9)
                window.thumbnail_maximum_mb.setValue(88.0)
                window.thumbnail_workers.setValue(7)
                window.copy_threshold_value.setValue(2.0)
                window.copy_threshold_unit.setCurrentIndex(window.copy_threshold_unit.findData(1024 ** 3))
                window.aria_small_limit.setValue(2.0)
                window.aria_small_segments.setValue(2)
                window.aria_medium_segments.setValue(16)
                window.aria_large_limit.setValue(200.0)
                window.aria_large_segments.setValue(32)
                window.speed_limit_enabled.setChecked(True)
                window.base_upload_limit.setValue(3.0)
                window.base_download_limit.setValue(4.0)
                window.builtin_player_enabled.setChecked(False)
                window.alist_host_combo.setCurrentIndex(window.alist_host_combo.findData("0.0.0.0"))
                window.alist_port.setValue(9876)
                window.alist_username.setText("tester")

                self.assertEqual(settings.value("language"), "en_US")
                self.assertEqual(window.locale.language, "en_US")
                self.assertEqual(settings.value("theme"), "dark")
                self.assertEqual(window.property("theme"), "dark")
                self.assertEqual(settings.value("close_behavior"), "tray")
                self.assertEqual(int(settings.value("font_size")), 11)
                self.assertEqual(settings.value("graphics/gpu_acceleration"), False)
                self.assertEqual(settings.value("graphics/acrylic"), False)
                self.assertEqual(settings.value("disable_settings_wheel"), False)
                self.assertEqual(int(settings.value("index/background_minutes")), 9)
                self.assertEqual(float(settings.value("preview/thumbnail_maximum_mb")), 88.0)
                self.assertEqual(int(settings.value("preview/thumbnail_workers")), 7)
                self.assertEqual(float(settings.value("copy/threshold_value")), 2.0)
                self.assertEqual(int(settings.value("copy/threshold_unit")), 1024 ** 3)
                self.assertEqual(float(settings.value("aria2/small_limit_mb")), 2.0)
                self.assertEqual(int(settings.value("aria2/small_segments")), 2)
                self.assertEqual(int(settings.value("aria2/medium_segments")), 16)
                self.assertEqual(float(settings.value("aria2/large_limit_mb")), 200.0)
                self.assertEqual(int(settings.value("aria2/large_segments")), 32)
                self.assertTrue(window.transfer_policy.enabled)
                self.assertEqual(window.transfer_policy.upload_mib, 3.0)
                self.assertEqual(window.transfer_policy.download_mib, 4.0)
                self.assertEqual(settings.value("builtin_player_enabled"), False)
                self.assertEqual(settings.value("alist/host"), "0.0.0.0")
                self.assertEqual(int(settings.value("alist/port")), 9876)
                self.assertEqual(settings.value("alist/username"), "tester")
                window.accounts = [app_module.AccountRecord("account-a", "Alice", "alice")]
                window.account_repositories = {
                    "account-a": [
                        Repository("alice/first", "dataset"),
                        Repository("alice/last-used", "dataset"),
                    ]
                }
                window._render_image_account_options()
                self.assertEqual(
                    [window.image_repo_combo.itemData(index) for index in range(window.image_repo_combo.count())],
                    [("dataset", "alice/first"), ("dataset", "alice/last-used")],
                )
                window.image_repo_combo.setCurrentIndex(1)
                window._save_image_settings()
                self.assertEqual(
                    settings.value("image/repositories/account-a/repo_id"), "alice/last-used"
                )
                window._image_repository_selections.clear()
                window.image_repo_combo.clear()
                window._render_image_repository_options()
                self.assertEqual(
                    window.image_repo_combo.currentData(), ("dataset", "alice/last-used")
                )
                window.language_combo.setCurrentIndex(window.language_combo.findData("zh_CN"))
                window.service = object()
                window.selected_repo = Repository("alice/example", "dataset")
                window.active_account_id = "account-a"
                window.remote_entries = [RemoteEntry("models/current/nested/file.bin", 1)]
                window._populate_remote_tree(
                    window.remote_tree,
                    window.remote_entries,
                )
                window._set_current_directory("models/current")
                started = []
                window.start_upload = lambda: started.append(True)

                event = FakeDropEvent(
                    QPointF(20, window.remote_detail_tree.viewport().height() - 10),
                    [file_path, folder_path],
                )
                window.remote_detail_tree.dropEvent(event)
                QApplication.processEvents()

                self.assertTrue(event.accepted)
                self.assertEqual(
                    [(item.path, item.target) for item in window.upload_items],
                    [(file_path.resolve(), "models/current"), (folder_path.resolve(), "models/current")],
                )
                self.assertEqual(started, [True])
                self.assertEqual(window.page_stack.currentIndex(), 0)
                self.assertEqual(window.drop_upload_threshold_mb.value(), 1024)

                window.directory_history.clear()
                window._set_current_directory("models/current", remember=False)
                window._go_to_directory("models/current/nested")
                window._go_to_directory("models")
                window._navigate(0)
                window.remote_detail_tree.setFocus()
                QApplication.processEvents()
                backspace = QKeyEvent(
                    QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(window.remote_detail_tree, backspace)
                self.assertEqual(window.current_directory_path, "models/current/nested")

                QApplication.processEvents()
                models_buttons = [
                    button for button in window.resource_path_label.findChildren(QPushButton)
                    if button.property("breadcrumbPath") == "models"
                ]
                if models_buttons:
                    models_buttons[-1].click()
                else:
                    models_action = next(
                        action for action in window.resource_path_label.overflow_button.menu().actions()
                        if action.text() == "models"
                    )
                    models_action.trigger()
                self.assertEqual(window.current_directory_path, "models")
                window._set_current_directory("models/current")
                window.resource_back_button.click()
                self.assertEqual(window.current_directory_path, "models")

                window._set_current_directory("models/current")
                window.resource_search_edit.setText("abc")
                QApplication.sendEvent(
                    window.resource_search_edit,
                    QKeyEvent(
                        QEvent.Type.KeyPress, Qt.Key.Key_Backspace,
                        Qt.KeyboardModifier.NoModifier,
                    ),
                )
                self.assertEqual(window.resource_search_edit.text(), "ab")
                self.assertEqual(window.current_directory_path, "models/current")

                large_path = root / "large.bin"
                large_path.write_bytes(b"x" * (1024 * 1024 + 1))
                for answer, expected_page in (
                    (QMessageBox.StandardButton.No, 0),
                    (QMessageBox.StandardButton.Yes, 1),
                ):
                    window.upload_items.clear()
                    window._render_upload_queue()
                    window.drop_upload_threshold_mb.setValue(1)
                    self.assertEqual(int(settings.value("upload/drop_threshold_mb")), 1)
                    window._navigate(0)
                    event = FakeDropEvent(
                        QPointF(20, window.remote_detail_tree.viewport().height() - 10),
                        [large_path],
                    )
                    with patch.object(QMessageBox, "question", return_value=answer) as question:
                        window.remote_detail_tree.dropEvent(event)
                    self.assertEqual(question.call_args.args[1], "大文件上传")
                    self.assertEqual(window.page_stack.currentIndex(), expected_page)
                window._force_close = True
                window.close()
