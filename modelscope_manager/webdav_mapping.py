"""Persistent custom WebDAV virtual-tree definitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


RESERVED_ROOT_NAMES = {"dav", "models", "datasets", "public"}
INVALID_WEBDAV_SEGMENT = re.compile(r'[\\:*?"<>|\x00-\x1f]')


def normalize_virtual_path(value: str, *, allow_empty: bool = True) -> str:
    parts = [part.strip() for part in str(value).replace("\\", "/").split("/") if part.strip()]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("路径不能包含 . 或 ..")
    result = "/".join(parts)
    if not result and not allow_empty:
        raise ValueError("路径不能为空")
    return result


def validate_virtual_target(value: str, *, allow_empty: bool = True) -> str:
    """Validate a path that must also be usable through Windows WebDAV."""
    target = normalize_virtual_path(value, allow_empty=allow_empty)
    for part in target.split("/") if target else ():
        if INVALID_WEBDAV_SEGMENT.search(part) or part.endswith((" ", ".")):
            raise ValueError("映射路径包含 Windows WebDAV 不允许的字符")
    return target


def validate_mapping_name(value: str) -> str:
    name = str(value).strip().strip("/\\")
    if not name or name.casefold() in RESERVED_ROOT_NAMES:
        raise ValueError("映射名称不能为空，也不能使用 dav、models、datasets 或 public")
    if name in {".", ".."} or re.search(r"[\\/:*?\"<>|\x00-\x1f]", name):
        raise ValueError("映射名称包含 Windows/WebDAV 不允许的字符")
    return name


def validate_source_path(value: str) -> str:
    source = normalize_virtual_path(value, allow_empty=False)
    if source.split("/", 1)[0] not in {"models", "datasets", "public"}:
        raise ValueError("源节点必须位于 /models、/datasets 或 /public 下")
    return source


@dataclass(frozen=True)
class WebDAVMount:
    target: str
    source: str

    @classmethod
    def from_dict(cls, value: dict) -> "WebDAVMount":
        return cls(
            validate_virtual_target(str(value.get("target", "")), allow_empty=False),
            validate_source_path(str(value.get("source", ""))),
        )

    def to_dict(self) -> dict[str, str]:
        return {"target": self.target, "source": self.source}


@dataclass
class WebDAVMapping:
    name: str
    read_only: bool = True
    folders: list[str] = field(default_factory=list)
    mounts: list[WebDAVMount] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict) -> "WebDAVMapping":
        name = validate_mapping_name(str(value.get("name", "")))
        folders: list[str] = []
        folder_keys: set[str] = set()
        for raw in value.get("folders", []):
            try:
                folder = validate_virtual_target(str(raw), allow_empty=False)
            except ValueError:
                continue
            key = folder.casefold()
            if key not in folder_keys:
                folders.append(folder)
                folder_keys.add(key)
        mounts: list[WebDAVMount] = []
        mount_keys: set[str] = set()
        for raw in value.get("mounts", []):
            try:
                mount = WebDAVMount.from_dict(raw)
            except (TypeError, ValueError):
                continue
            key = mount.target.casefold()
            if key not in mount_keys:
                mounts.append(mount)
                mount_keys.add(key)
        folders = [folder for folder in folders if folder.casefold() not in mount_keys]
        return cls(name, bool(value.get("read_only", True)), folders, mounts)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "read_only": bool(self.read_only),
            "folders": list(self.folders),
            "mounts": [item.to_dict() for item in self.mounts],
        }

    def virtual_directories(self) -> set[str]:
        output = {""}
        for path in [*self.folders, *(mount.target for mount in self.mounts)]:
            parts = path.split("/")
            output.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
        return output


def load_webdav_mappings(raw: str) -> list[WebDAVMapping]:
    try:
        payload = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    output: list[WebDAVMapping] = []
    used: set[str] = set()
    for value in payload if isinstance(payload, list) else []:
        try:
            mapping = WebDAVMapping.from_dict(value)
        except (TypeError, ValueError):
            continue
        key = mapping.name.casefold()
        if key not in used:
            output.append(mapping)
            used.add(key)
    return output


def dump_webdav_mappings(values: list[WebDAVMapping]) -> str:
    return json.dumps([value.to_dict() for value in values], ensure_ascii=False, separators=(",", ":"))
