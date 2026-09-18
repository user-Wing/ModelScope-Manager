from __future__ import annotations

import hashlib
import shutil
import subprocess
import uuid
from pathlib import Path


FFMPEG_ARCHIVE_SIZE = 27_210_043
FFMPEG_ARCHIVE_SHA256 = "cb3fa11b8b6421f8d53b51844ae1c847d41476cb08a205323db00e27d91ef3a8"
FFMPEG_REPOSITORY = "ARXChem/Software-List"
FFMPEG_REMOTE_PATH = "ffmpeg/FFmpeg.7z"


def verify_ffmpeg_archive(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != FFMPEG_ARCHIVE_SIZE:
        raise ValueError("FFmpeg 压缩包大小校验失败")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != FFMPEG_ARCHIVE_SHA256:
        raise ValueError("FFmpeg 压缩包 SHA-256 校验失败")


def find_installed_ffmpeg(directory: Path) -> Path | None:
    executable = directory / "ffmpeg.exe"
    return executable.resolve() if executable.is_file() else None


def install_ffmpeg(archive: Path, seven_zip: Path, destination: Path) -> Path:
    verify_ffmpeg_archive(archive)
    if not seven_zip.is_file():
        raise FileNotFoundError(f"缺少 7z-zstd 解压工具：{seven_zip}")
    parent = destination.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".ffmpeg-install-{uuid.uuid4().hex}"
    backup = parent / f".ffmpeg-backup-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        process = subprocess.run(
            [str(seven_zip), "x", str(archive), f"-o{staging}", "-y", "-bso0", "-bsp0"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=600,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stdout.strip() or f"7z-zstd 返回代码 {process.returncode}")
        executable = next(iter(sorted(staging.rglob("ffmpeg.exe"), key=lambda item: len(item.parts))), None)
        if executable is None:
            raise FileNotFoundError("压缩包中未找到 ffmpeg.exe")
        payload = executable.parent
        if destination.exists():
            destination.replace(backup)
        shutil.move(str(payload), str(destination))
        installed = find_installed_ffmpeg(destination)
        if installed is None:
            raise FileNotFoundError("FFmpeg 解压后主程序不存在")
        probe = subprocess.run(
            [str(installed), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=30,
        )
        if probe.returncode != 0:
            raise RuntimeError("FFmpeg 安装后自检失败")
        if backup.exists():
            shutil.rmtree(backup)
        archive.unlink(missing_ok=True)
        return installed
    except Exception:
        if destination.exists() and backup.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
