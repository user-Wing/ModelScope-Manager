"""应用级纯函数、格式化与常量。"""

from __future__ import annotations

import os
import socket
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImageReader
from pathlib import Path
from typing import Iterable
from urllib.parse import quote
from .image_bed import IMAGE_EXTENSIONS
from .service import Repository, normalize_remote_path


MEDIA_EXTENSIONS = {
    ".3gp", ".aac", ".ac3", ".aiff", ".alac", ".ape", ".avi", ".flac", ".flv",
    ".m2ts", ".m4a", ".m4v", ".mka", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg",
    ".mpg", ".mts", ".ogg", ".ogv", ".opus", ".rm", ".rmvb", ".ts", ".wav",
    ".webm", ".wma", ".wmv",
}

PUBLIC_ACCOUNT_ID = "__public__"

THUMBNAIL_RENDER_SIZE = (320, 180)

VIDEO_THUMBNAIL_SEEK_SECONDS = 1.5

def copy_name(name: str, is_dir: bool = False) -> str:
    """Add -copy before a file suffix, or after a directory name."""
    if is_dir:
        return f"{name}-copy"
    path = Path(name)
    return f"{path.stem}-copy{path.suffix}" if path.suffix else f"{name}-copy"

def thumbnail_batch_policy(entry_count: int) -> tuple[int, int, int]:
    """Return batch size, worker cap and inter-batch delay in milliseconds."""
    return (96, 32, 10) if entry_count > 100 else (48, 16, 20)

def repository_file_url(repo: Repository, path: str, public: bool) -> str:
    kind = "datasets" if repo.repo_type == "dataset" else "models"
    repo_id = quote(repo.repo_id, safe="/")
    remote_path = quote(normalize_remote_path(path), safe="/")
    if public:
        return f"https://modelscope.cn/{kind}/{repo_id}/resolve/master/{remote_path}"
    return f"https://modelscope.cn/api/v1/{kind}/{repo_id}/repo?Revision=master&FilePath={remote_path}"

def repository_is_public(repo: Repository, service_token: str = "") -> bool:
    """Interpret both SDK labels and ModelScope's numeric visibility values."""
    visibility = str(repo.visibility).strip().casefold()
    if visibility in {"public", "5"}:
        return True
    if visibility in {"private", "internal", "1", "3"}:
        return False
    # Unknown/new visibility values fail closed; never infer public from missing credentials.
    return False

def repository_identity(repo: Repository) -> tuple[str, str]:
    """Return the stable fields that identify a repository across refreshes."""
    return repo.repo_type, repo.repo_id

def local_path_identity(path: str | Path) -> str:
    """Normalize Windows path spelling for transfer-row callback matching."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))

def running_download_percent(completed: int, total: int) -> int:
    """Return the byte-accurate percentage for the download phase."""
    return min(100, int(completed * 100 / max(1, total)))

def is_supported_image_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in IMAGE_EXTENSIONS
        and QImageReader(str(path)).canRead()
    )

def format_speed(value: float) -> str:
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    value = max(0.0, value)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return "--"

def format_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "--"

def local_paths_size(raw_paths: Iterable[str | Path]) -> int:
    """Return the total byte size of unique dropped files and folder contents."""
    candidates: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        try:
            path = Path(raw).resolve()
        except OSError:
            continue
        key = str(path).casefold()
        if not path.exists() or key in seen:
            continue
        seen.add(key)
        candidates.append(path)

    roots: list[Path] = []
    for path in sorted(candidates, key=lambda value: len(value.parts)):
        if any(parent.is_dir() and path.is_relative_to(parent) for parent in roots):
            continue
        roots.append(path)

    total = 0
    for path in roots:
        files = path.rglob("*") if path.is_dir() else (path,)
        for file_path in files:
            if not file_path.is_file():
                continue
            try:
                total += max(0, file_path.stat().st_size)
            except OSError:
                continue
    return total

def breadcrumb_levels(path: str, root_text: str = "根目录") -> list[tuple[str, str]]:
    parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part]
    levels = [(root_text, "")]
    for index, part in enumerate(parts):
        levels.append((part, "/".join(parts[:index + 1])))
    return levels

def format_eta(seconds: int) -> str:
    if seconds < 0:
        return "--"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

def find_available_port(host: str, preferred: int, attempts: int = 40) -> int:
    candidates = [preferred]
    for offset in range(1, attempts + 1):
        candidates.extend((preferred + offset, preferred - offset))
    for port in candidates:
        if not 1024 <= port <= 65535:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
        return port
    raise OSError("未找到可用 WebDAV 端口")

def restore_combo_setting(settings: QSettings, key: str, combo, default) -> None:
    """Restore a combo choice and repair invalid values left in the INI file."""
    value = settings.value(key, default)
    index = combo.findData(value)
    if index < 0:
        value = default
        index = combo.findData(default)
        settings.setValue(key, default)
    combo.setCurrentIndex(max(0, index))
