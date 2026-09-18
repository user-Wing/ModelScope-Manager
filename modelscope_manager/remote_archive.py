from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from .http_security import safe_urlopen


SUPPORTED_REMOTE_ARCHIVES = {".7z", ".zip", ".tar", ".iso", ".gz", ".xz"}


@dataclass(frozen=True)
class ArchiveProbeResult:
    supported: bool
    listing: str
    fetched_bytes: int
    total_bytes: int
    message: str = ""


def _fetch_range(url: str, headers: dict[str, str], start: int, end: int) -> bytes:
    request_headers = dict(headers)
    request_headers["Range"] = f"bytes={start}-{end}"
    with safe_urlopen(Request(url, headers=request_headers), timeout=45) as response:
        status = int(getattr(response, "status", 200))
        data = response.read()
        if status != 206 or not response.headers.get("Content-Range"):
            raise RuntimeError("远端未返回 HTTP 206，不能保证按需读取压缩包")
        return data


def _seven_zip_sparse_listing(
    url: str, headers: dict[str, str], total_size: int, seven_zip: Path, suffix: str,
) -> ArchiveProbeResult:
    head_bytes = {
        ".7z": 64 * 1024, ".zip": 1024 * 1024, ".iso": 4 * 1024**2,
        ".gz": 4 * 1024**2, ".xz": 4 * 1024**2,
    }.get(suffix, 1024 * 1024)
    tail_bytes = 16 * 1024**2 if suffix in {".7z", ".zip", ".iso"} else 1024 * 1024
    head = _fetch_range(url, headers, 0, min(total_size - 1, head_bytes - 1))
    signatures = {
        ".7z": head.startswith(b"7z\xbc\xaf\x27\x1c"),
        ".zip": head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")),
        ".iso": len(head) > 0x8006 and head[0x8001:0x8006] == b"CD001",
        ".gz": head.startswith(b"\x1f\x8b"),
        ".xz": head.startswith(b"\xfd7zXZ\x00"),
    }
    if suffix in signatures and not signatures[suffix]:
        return ArchiveProbeResult(False, "", len(head), total_size, f"{suffix[1:].upper()} 签名不匹配")
    tail_start = max(len(head), total_size - max(64 * 1024, tail_bytes))
    tail = _fetch_range(url, headers, tail_start, total_size - 1) if tail_start < total_size else b""
    handle = tempfile.NamedTemporaryFile(prefix="modelscope-remote-archive-", suffix=suffix, delete=False)
    sparse = Path(handle.name)
    try:
        with handle:
            handle.truncate(total_size)
            handle.seek(0)
            handle.write(head)
            if tail:
                handle.seek(tail_start)
                handle.write(tail)
        result = subprocess.run(
            [str(seven_zip), "l", "-slt", "-ba", str(sparse)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        listing = result.stdout.strip()
        supported = "Path = " in listing and (result.returncode == 0 or suffix in {".gz", ".xz", ".iso"})
        if not supported and suffix in {".gz", ".xz"}:
            # GZ/XZ are single compressed streams, not multi-file containers.
            # A valid signature is enough to expose that one logical member even
            # when this 7z build cannot infer its uncompressed size from sparse data.
            logical_name = "compressed-stream"
            listing = (
                f"Path = {logical_name}\n"
                f"Type = {suffix[1:].upper()} stream\n"
                f"Packed Size = {total_size}\n"
                "Size = unknown"
            )
            supported = True
        if supported:
            detail = "仅读取远端索引成功"
            if suffix in {".gz", ".xz"}:
                detail = "已读取单流压缩层元数据；通用 GZ/XZ 不含可随机访问的内部目录"
        else:
            detail = result.stderr.strip() or "压缩包目录不在已读取的 Range 窗口内"
        return ArchiveProbeResult(supported, listing, len(head) + len(tail), total_size, detail)
    finally:
        sparse.unlink(missing_ok=True)


def _tar_text(header: bytes, start: int, end: int) -> str:
    return header[start:end].split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def _tar_listing(url: str, headers: dict[str, str], total_size: int) -> ArchiveProbeResult:
    """Walk TAR headers with Range jumps, never fetching member payloads."""
    offset = 0
    fetched = 0
    rows: list[str] = []
    zero_blocks = 0
    while offset + 512 <= total_size and len(rows) < 10000:
        header = _fetch_range(url, headers, offset, offset + 511)
        fetched += len(header)
        if len(header) != 512:
            break
        if header == b"\0" * 512:
            zero_blocks += 1
            if zero_blocks >= 2:
                break
            offset += 512
            continue
        zero_blocks = 0
        name = _tar_text(header, 0, 100)
        prefix = _tar_text(header, 345, 500)
        if prefix:
            name = prefix + "/" + name
        try:
            size = int(_tar_text(header, 124, 136) or "0", 8)
        except ValueError:
            return ArchiveProbeResult(False, "\n".join(rows), fetched, total_size, "TAR 文件头大小字段无效")
        kind = header[156:157]
        folder = kind == b"5" or name.endswith("/")
        rows.append(f"Path = {name.rstrip('/')}\nFolder = {'+' if folder else '-'}\nSize = {size}")
        offset += 512 + math.ceil(max(0, size) / 512) * 512
    supported = bool(rows)
    message = "按 TAR 文件头逐项跳过内容并读取目录成功" if supported else "未找到有效 TAR 文件头"
    return ArchiveProbeResult(supported, "\n\n".join(rows), fetched, total_size, message)


def probe_archive_listing(
    url: str, headers: dict[str, str], total_size: int, seven_zip: Path, filename: str,
) -> ArchiveProbeResult:
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_REMOTE_ARCHIVES:
        return ArchiveProbeResult(False, "", 0, total_size, "该压缩格式不支持远端按需浏览")
    if total_size <= 0:
        return ArchiveProbeResult(False, "", 0, total_size, "远端文件大小未知")
    if suffix == ".tar":
        return _tar_listing(url, headers, total_size)
    return _seven_zip_sparse_listing(url, headers, total_size, seven_zip, suffix)


def probe_7z_listing(
    url: str, headers: dict[str, str], total_size: int, seven_zip: Path, tail_bytes: int = 16 * 1024**2,
) -> ArchiveProbeResult:
    """Backward-compatible wrapper retained for integrations."""
    del tail_bytes
    return probe_archive_listing(url, headers, total_size, seven_zip, "archive.7z")
