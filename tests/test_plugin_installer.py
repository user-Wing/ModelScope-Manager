from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modelscope_manager import avif_converter
from modelscope_manager.plugin_installer import (
    FFMPEG_ARCHIVE_SHA256,
    FFMPEG_ARCHIVE_SIZE,
    FFMPEG_REMOTE_PATH,
    FFMPEG_REPOSITORY,
    find_installed_ffmpeg,
    install_ffmpeg,
)


class PluginInstallerTests(unittest.TestCase):
    def test_ffmpeg_archive_metadata_is_pinned(self):
        self.assertEqual(FFMPEG_REPOSITORY, "ARXChem/Software-List")
        self.assertEqual(FFMPEG_REMOTE_PATH, "ffmpeg/FFmpeg.7z")
        self.assertEqual(FFMPEG_ARCHIVE_SIZE, 27_210_043)
        self.assertEqual(
            FFMPEG_ARCHIVE_SHA256,
            "cb3fa11b8b6421f8d53b51844ae1c847d41476cb08a205323db00e27d91ef3a8",
        )

    def test_find_installed_ffmpeg_uses_managed_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "ffmpeg.exe"
            executable.touch()
            self.assertEqual(find_installed_ffmpeg(root), executable.resolve())

    def test_image_bed_ffmpeg_probe_prefers_managed_plugin_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "ffmpeg.exe"
            executable.touch()
            with patch.object(avif_converter, "FFMPEG_DIR", root):
                self.assertEqual(avif_converter.find_ffmpeg(), executable)

    def test_install_extracts_ffmpeg_payload_and_self_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "FFmpeg.7z"
            seven_zip = root / "7z.exe"
            destination = root / "embedded-tools" / "ffmpeg"
            archive.touch()
            seven_zip.touch()

            def fake_run(command, **_kwargs):
                output_arg = next((part for part in command if str(part).startswith("-o")), None)
                if output_arg:
                    payload = Path(str(output_arg)[2:]) / "FFmpeg"
                    payload.mkdir(parents=True)
                    (payload / "ffmpeg.exe").write_bytes(b"ffmpeg")
                return type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

            with patch("modelscope_manager.plugin_installer.verify_ffmpeg_archive"), patch(
                "modelscope_manager.plugin_installer.subprocess.run", side_effect=fake_run
            ):
                installed = install_ffmpeg(archive, seven_zip, destination)

            self.assertEqual(installed, (destination / "ffmpeg.exe").resolve())
            self.assertTrue(installed.is_file())
            self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
