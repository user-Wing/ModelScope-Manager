from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import tempfile
import winreg
from ctypes import wintypes
from pathlib import Path


APP_NAME = "ModelScope Manager"
THIS_PC_CLSID = "{61D07D39-3D6D-4F80-8A06-8C8FC01B30A7}"
WEBDAV_PARAMETERS = r"SYSTEM\CurrentControlSet\Services\WebClient\Parameters"


def _refresh_explorer() -> None:
    # SHCNE_ASSOCCHANGED asks Explorer to rebuild namespace/icon caches.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def _launch_target(app_dir: Path) -> tuple[str, str, str]:
    starter = app_dir / "start.bat"
    if starter.is_file():
        return str(starter), "", str(app_dir)
    return sys.executable, f'"{app_dir / "main.py"}"', str(app_dir)


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _powershell_process(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded_powershell(script)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _process_error(result: subprocess.CompletedProcess) -> str:
    detail = "\n".join(value.strip() for value in (result.stderr, result.stdout) if value and value.strip())
    return detail or f"PowerShell 返回代码 {result.returncode}"


def _run_powershell(script: str) -> None:
    result = _powershell_process(script)
    if result.returncode:
        raise RuntimeError(_process_error(result))


def run_elevated_powershell(script: str) -> None:
    """Run one narrowly scoped PowerShell script through the Windows UAC prompt."""
    handle = tempfile.NamedTemporaryFile(prefix="modelscope-admin-", suffix=".log", delete=False)
    log_path = Path(handle.name)
    handle.close()
    log_path.unlink(missing_ok=True)
    escaped_log = str(log_path).replace("'", "''")
    wrapped = (
        "$ErrorActionPreference='Stop'; try { " + script +
        " } catch { ($_ | Format-List * -Force | Out-String) | "
        f"Set-Content -LiteralPath '{escaped_log}' -Encoding UTF8; exit 1 }}"
    )
    command = (
        "$ErrorActionPreference='Stop'; try { "
        "$p=Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru "
        f"-ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand','{_encoded_powershell(wrapped)}'); "
        "exit $p.ExitCode } catch { ($_ | Format-List * -Force | Out-String) | "
        f"Set-Content -LiteralPath '{escaped_log}' -Encoding UTF8; exit 1 }}"
    )
    try:
        result = _powershell_process(command)
        if result.returncode:
            try:
                detail = log_path.read_text(encoding="utf-8-sig").strip()
            except OSError:
                detail = ""
            raise RuntimeError(detail or _process_error(result))
    finally:
        log_path.unlink(missing_ok=True)


def _shortcut_script(path: Path, app_dir: Path) -> str:
    target, arguments, working = _launch_target(app_dir)
    icon = app_dir / "main.py"
    values = {"path": path, "target": target, "arguments": arguments, "working": working, "icon": icon}
    escaped = {key: str(value).replace("'", "''") for key, value in values.items()}
    return (
        f"$p='{escaped['path']}'; New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null; "
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p); "
        f"$s.TargetPath='{escaped['target']}'; $s.Arguments='{escaped['arguments']}'; "
        f"$s.WorkingDirectory='{escaped['working']}'; $s.Description='{APP_NAME}'; "
        f"$s.IconLocation='{escaped['target']},0'; $s.Save()"
    )


def create_desktop_shortcut(app_dir: Path) -> Path:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    path = desktop / f"{APP_NAME}.lnk"
    _run_powershell(_shortcut_script(path, app_dir))
    return path


def create_start_menu_shortcut(app_dir: Path, public: bool = False) -> Path:
    if public:
        root = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs"
    else:
        root = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    path = root / f"{APP_NAME}.lnk"
    script = _shortcut_script(path, app_dir)
    (run_elevated_powershell if public else _run_powershell)(script)
    return path


def create_this_pc_shortcut(app_dir: Path) -> None:
    target, arguments, working = _launch_target(app_dir)
    clsid_path = rf"Software\Classes\CLSID\{THIS_PC_CLSID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clsid_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "System.IsPinnedToNameSpaceTree", 0, winreg.REG_DWORD, 1)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clsid_path + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{target},0")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clsid_path + r"\shell\open\command") as key:
        command = f'"{target}" {arguments}'.strip()
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    namespace = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\MyComputer\NameSpace\{THIS_PC_CLSID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, namespace):
        pass
    _refresh_explorer()


def cleanup_shortcuts(app_dir: Path) -> None:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{APP_NAME}.lnk"
    user_start = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / f"{APP_NAME}.lnk"
    for path in (desktop, user_start):
        path.unlink(missing_ok=True)
    for path in (
        rf"Software\Microsoft\Windows\CurrentVersion\Explorer\MyComputer\NameSpace\{THIS_PC_CLSID}",
        rf"Software\Classes\CLSID\{THIS_PC_CLSID}",
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            if path.endswith(THIS_PC_CLSID):
                _run_powershell(f"Remove-Item -LiteralPath 'Registry::HKEY_CURRENT_USER\\{path}' -Recurse -Force -ErrorAction SilentlyContinue")
    public_path = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs" / f"{APP_NAME}.lnk"
    if public_path.exists():
        escaped = str(public_path).replace("'", "''")
        run_elevated_powershell(f"Remove-Item -LiteralPath '{escaped}' -Force -ErrorAction SilentlyContinue")
    _refresh_explorer()


def configure_local_http_webdav() -> None:
    script = (
        f"$reg='HKLM\\{WEBDAV_PARAMETERS}'; "
        "$serviceDll=Join-Path $env:SystemRoot 'System32\\webclnt.dll'; "
        "if (-not (Test-Path -LiteralPath $serviceDll -PathType Leaf)) { "
        "throw ('Windows WebClient 组件缺失：' + $serviceDll + '。请在 Windows 可选功能或系统修复中恢复 WebDAV Redirector。') }; "
        "& reg.exe ADD $reg /v ServiceDll /t REG_EXPAND_SZ /d '%SystemRoot%\\System32\\webclnt.dll' /f | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { throw '修复 WebClient ServiceDll 注册项失败' }; "
        "& reg.exe ADD $reg /v ServiceDllUnloadOnStop /t REG_DWORD /d 1 /f | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { throw '写入 ServiceDllUnloadOnStop 失败' }; "
        "& reg.exe ADD $reg /v BasicAuthLevel /t REG_DWORD /d 2 /f | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { throw '写入 BasicAuthLevel 失败' }; "
        "& reg.exe ADD $reg /v FileSizeLimitInBytes /t REG_DWORD /d 0xffffffff /f | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { throw '写入 FileSizeLimitInBytes 失败' }; "
        "& reg.exe ADD $reg /v FileAttributesLimitInBytes /t REG_DWORD /d 20000000 /f | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { throw '写入 FileAttributesLimitInBytes 失败' }; "
        "$service=Get-Service -Name WebClient -ErrorAction Stop; "
        "Set-Service -Name WebClient -StartupType Manual -ErrorAction Stop; "
        "if ($service.Status -eq 'Running') { Restart-Service -Name WebClient -Force -ErrorAction Stop } "
        "else { Start-Service -Name WebClient -ErrorAction Stop }; "
        "(Get-Service -Name WebClient).WaitForStatus('Running',[TimeSpan]::FromSeconds(15))"
    )
    run_elevated_powershell(script)


class NETRESOURCEW(ctypes.Structure):
    _fields_ = [
        ("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
        ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
        ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
        ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR),
    ]


def webdav_unc(host: str, port: int, path: str = "/dav/") -> str:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Windows 直挂仅允许本机 WebDAV，避免通过 HTTP 明文传输凭据")
    suffix = path.strip("/").replace("/", "\\")
    return rf"\\{host}@{int(port)}\DavWWWRoot\{suffix}"


def mount_webdav_drive(
    letter: str, host: str, port: int, username: str, password: str, path: str = "/dav",
) -> str:
    local = letter.rstrip(":").upper() + ":"
    remote = webdav_unc(host, port, path)
    resource = NETRESOURCEW(0, 1, 0, 0, local, remote, None, None)
    result = ctypes.windll.mpr.WNetAddConnection2W(ctypes.byref(resource), password, username, 1)
    if result:
        raise OSError(result, ctypes.FormatError(result))
    return local


def unmount_webdav_drive(letter: str) -> None:
    local = letter.rstrip(":").upper() + ":"
    result = ctypes.windll.mpr.WNetCancelConnection2W(local, 0, True)
    if result not in (0, 2250):
        raise OSError(result, ctypes.FormatError(result))


def mapped_drive_remote(letter: str) -> str:
    """Return the current network target for a drive letter, or an empty string."""
    local = letter.rstrip(":").upper() + ":"
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    result = ctypes.windll.mpr.WNetGetConnectionW(local, buffer, ctypes.byref(size))
    if result == 0:
        return buffer.value
    if result in (2250, 1200):  # not connected / not a network name
        return ""
    raise OSError(result, ctypes.FormatError(result))
