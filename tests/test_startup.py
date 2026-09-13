import tempfile
import unittest
from pathlib import Path

from modelscope_manager.startup import startup_command


class StartupTests(unittest.TestCase):
    def test_command_uses_pythonw_and_absolute_main_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "runtime" / "python.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"")
            pythonw = executable.with_name("pythonw.exe")
            pythonw.write_bytes(b"")
            command = startup_command(root, executable)
            self.assertIn(str(pythonw.resolve()), command)
            self.assertIn(str((root / "main.py").resolve()), command)
            self.assertIn(" -s ", command)
