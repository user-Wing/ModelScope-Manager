import os
import sys
from pathlib import Path


bundled_site_packages = Path(__file__).resolve().parent / "runtime" / "Lib" / "site-packages"
if bundled_site_packages.is_dir():
    sys.path.insert(0, str(bundled_site_packages))

from modelscope_manager.app import run


if __name__ == "__main__":
    # The window shutdown path already flushes settings and stops child
    # processes.  Do not let a third-party HTTP worker keep pythonw.exe alive.
    os._exit(run())
