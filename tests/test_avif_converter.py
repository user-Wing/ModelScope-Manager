from __future__ import annotations

import unittest
from pathlib import Path

from modelscope_manager.avif_converter import AvifOptions, build_ffmpeg_command, pixel_format, quality_to_crf


class AvifConverterTests(unittest.TestCase):
    def test_awj_default_maps_to_reference_ffmpeg_profile(self) -> None:
        options = AvifOptions()
        command = build_ffmpeg_command(Path("ffmpeg.exe"), Path("input.png"), Path("output.avif"), options)

        self.assertEqual(options.quality, 70)
        self.assertEqual(options.speed, 5)
        self.assertEqual(quality_to_crf(options.quality), 23)
        self.assertEqual(pixel_format(options), "yuv420p10le")
        self.assertIn("libaom-av1", command)
        self.assertEqual(command[command.index("-cpu-used") + 1], "5")
        self.assertEqual(command[command.index("-crf") + 1], "23")

    def test_explicit_chroma_and_depth_are_applied(self) -> None:
        self.assertEqual(pixel_format(AvifOptions(chroma="444", bit_depth="8")), "yuv444p")
        self.assertEqual(pixel_format(AvifOptions(chroma="422", bit_depth="12")), "yuv422p12le")

    def test_invalid_options_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AvifOptions(quality=0).validate()
        with self.assertRaises(ValueError):
            AvifOptions(speed=11).validate()


if __name__ == "__main__":
    unittest.main()
