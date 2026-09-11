from __future__ import annotations

import os
import unittest

from modelscope_manager.resource_monitor import ProcessResourceMonitor, _instance_pid


class ResourceMonitorTests(unittest.TestCase):
    def test_instance_pid_parsing(self) -> None:
        self.assertEqual(_instance_pid("pid_1234_luid_0x0000_phys_0"), 1234)
        self.assertEqual(_instance_pid("engtype_3D_pid_42_luid_1"), 42)
        self.assertIsNone(_instance_pid("not-a-gpu-instance"))

    def test_current_process_sample_has_memory(self) -> None:
        monitor = ProcessResourceMonitor()
        try:
            sample = monitor.sample()
            self.assertGreaterEqual(sample.cpu_percent, 0.0)
            if os.name == "nt":
                self.assertGreater(sample.working_set_bytes, 0)
                self.assertGreater(sample.private_bytes, 0)
        finally:
            monitor.close()


if __name__ == "__main__":
    unittest.main()
