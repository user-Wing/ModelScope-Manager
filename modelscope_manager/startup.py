from __future__ import annotations

import sys
import winreg
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ModelScopeManager"


def startup_command(app_dir: Path, executable: Path | None = None) -> str:
    app_dir = app_dir.resolve()
    executable = (executable or Path(sys.executable)).resolve()
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return f'"{executable}" -s "{app_dir / "main.py"}"'


def set_windows_startup(enabled: bool, app_dir: Path, executable: Path | None = None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, startup_command(app_dir, executable))
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def windows_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
