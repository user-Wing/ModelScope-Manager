"""ModelScope release discovery, download, extraction, and deferred installation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .download_service import Aria2DownloadRunner, DownloadSpec
from .service import ModelScopeService, RemoteEntry, Repository
from .storage import APP_DIR, CONFIG_DIR, SEVEN_ZIP_ZSTD_EXE


UPDATE_REPOSITORY = "ARXChem/Software-List"
UPDATE_FOLDER = "ModelScope-Manager"
UPDATE_DIR = CONFIG_DIR / "updates"
ARIA2_EXE = APP_DIR / "runtime" / "tools" / "aria2-next.exe"
_VERSION_ARCHIVE = re.compile(r"^(\d+(?:\.\d+)*)\.7z$", re.IGNORECASE)


@dataclass(frozen=True)
class Release:
    version: str
    remote_path: str
    url: str
    size: int = 0
    sha256: str = ""

    @property
    def version_key(self) -> tuple[int, ...]:
        return version_key(self.version)


def version_key(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+)*", version.strip()):
        raise ValueError(f"无效版本号：{version}")
    values = tuple(int(part) for part in version.split("."))
    return values + (0,) * (4 - len(values))


def release_from_entry(entry: RemoteEntry, url: str) -> Release | None:
    path = entry.path.replace("\\", "/").strip("/")
    prefix = UPDATE_FOLDER + "/"
    if entry.is_dir or not path.startswith(prefix):
        return None
    relative = path[len(prefix):]
    if "/" in relative:
        return None
    match = _VERSION_ARCHIVE.fullmatch(relative)
    if not match:
        return None
    return Release(match.group(1), path, url, entry.size, entry.sha256)


def discover_releases(service: ModelScopeService | None = None) -> list[Release]:
    service = service or ModelScopeService("", require_token=False)
    repository = Repository(UPDATE_REPOSITORY, "dataset", "public")
    releases: list[Release] = []
    for entry in service.list_entries(repository):
        release = release_from_entry(entry, service.get_download_url(repository, entry.path))
        if release is not None:
            releases.append(release)
    return sorted(releases, key=lambda release: release.version_key, reverse=True)


def latest_newer_release(current_version: str, releases: Iterable[Release]) -> Release | None:
    current = version_key(current_version)
    return next(
        (release for release in sorted(releases, key=lambda item: item.version_key, reverse=True)
         if release.version_key > current),
        None,
    )


def _archive_paths(seven_zip: Path, archive: Path) -> list[str]:
    result = subprocess.run(
        [str(seven_zip), "l", "-slt", str(archive)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "7z 无法读取更新包")
    details = result.stdout.split("----------", 1)
    if len(details) != 2:
        raise RuntimeError("7z 未返回更新包文件列表")
    return [
        line.split(" = ", 1)[1].strip()
        for line in details[1].splitlines()
        if line.startswith("Path = ")
    ]


def validate_archive_paths(paths: Iterable[str]) -> None:
    found = False
    for raw_path in paths:
        normalized = raw_path.replace("\\", "/").strip("/")
        if not normalized:
            continue
        found = True
        pure = PurePosixPath(normalized)
        if raw_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw_path):
            raise ValueError(f"更新包包含绝对路径：{raw_path}")
        if ".." in pure.parts:
            raise ValueError(f"更新包包含越界路径：{raw_path}")
        parts = tuple(part.lower() for part in pure.parts)
        if parts[0] == "data" or (len(parts) > 1 and parts[1] == "data"):
            raise ValueError("更新包不能包含 data 目录")
    if not found:
        raise ValueError("更新包为空")


def _payload_root(staging_dir: Path) -> Path:
    candidates = [staging_dir]
    children = list(staging_dir.iterdir())
    if len(children) == 1 and children[0].is_dir():
        candidates.insert(0, children[0])
    for candidate in candidates:
        required = (
            candidate / "main.py",
            candidate / "start.bat",
            candidate / "modelscope_manager",
            candidate / "runtime",
        )
        if all(path.exists() for path in required):
            return candidate.resolve()
    raise ValueError("更新包不是完整的 ModelScope Manager 便携包")


def prepare_release(
    release: Release,
    progress_callback: Callable[[int], None] | None = None,
    runner: Aria2DownloadRunner | None = None,
    seven_zip: Path = SEVEN_ZIP_ZSTD_EXE,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    archive = UPDATE_DIR / f"{release.version}.7z"
    staging_dir = UPDATE_DIR / f"staging-{release.version}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    download_runner = runner or Aria2DownloadRunner(ARIA2_EXE, "")
    spec = DownloadSpec(release.remote_path, archive, release.url, release.size, release.sha256)

    def report(completed: int, total: int, _speed: float, _eta: int) -> None:
        if progress_callback:
            progress_callback(round(completed * 100 / max(1, total)))

    ok, failed = download_runner.run([spec], report, lambda *_args: None)
    if ok != 1 or failed:
        raise RuntimeError("更新包下载或校验失败")
    if cancelled and cancelled():
        raise RuntimeError("更新已取消")
    validate_archive_paths(_archive_paths(seven_zip, archive))
    staging_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(seven_zip), "x", str(archive), f"-o{staging_dir}", "-y", "-bb0"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    while process.poll() is None:
        if cancelled and cancelled():
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("更新已取消")
        time.sleep(0.1)
    error = process.stderr.read() if process.stderr else ""
    if process.returncode != 0:
        raise RuntimeError(error.strip() or "更新包解压失败")
    return _payload_root(staging_dir)


def write_apply_script(payload_root: Path, target_dir: Path = APP_DIR) -> Path:
    payload_root = payload_root.resolve()
    target_dir = target_dir.resolve()
    if not payload_root.is_relative_to(UPDATE_DIR.resolve()):
        raise ValueError("更新暂存目录不安全")
    script_path = UPDATE_DIR / "apply_update.ps1"
    script = r'''param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target
)
$ErrorActionPreference = 'Stop'
$logPath = Join-Path $Target 'data\update.log'
try {
    Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
    & robocopy.exe $Source $Target /E /COPY:DAT /DCOPY:DAT /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
    "$(Get-Date -Format o) update installed" | Set-Content -LiteralPath $logPath -Encoding UTF8
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', (Join-Path $Target 'start.bat')) -WorkingDirectory $Target -WindowStyle Hidden
} catch {
    "$(Get-Date -Format o) update failed: $($_.Exception.Message)" | Set-Content -LiteralPath $logPath -Encoding UTF8
}
'''
    script_path.write_text(script, encoding="utf-8-sig")
    return script_path


def launch_apply_script(script: Path, payload_root: Path, target_dir: Path = APP_DIR) -> None:
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", str(script), "-ParentPid", str(os.getpid()),
        "-Source", str(payload_root.resolve()), "-Target", str(target_dir.resolve()),
    ]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
