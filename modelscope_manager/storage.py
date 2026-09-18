from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings

from .security import protect, protect_compatible, unprotect


APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = APP_DIR / "data"
SETTINGS_PATH = CONFIG_DIR / "settings.ini"
PUBLIC_POOLS_PATH = CONFIG_DIR / "public_pools.json"
FOLDER_INDEX_PATH = CONFIG_DIR / "folder_sizes.sqlite3"
MANAGER_DB_PATH = CONFIG_DIR / "manager.sqlite3"
DEVICE_ID_PATH = CONFIG_DIR / "device.id"
IMAGE_CACHE_DIR = CONFIG_DIR / "image_cache"
THUMBNAIL_CACHE_DIR = CONFIG_DIR / "thumbnails"
PLAYER_DOWNLOAD_DIR = CONFIG_DIR / "player_download"
PLUGIN_DOWNLOAD_DIR = CONFIG_DIR / "plugin_download"
POTPLAYER_DIR = CONFIG_DIR / "players" / "potplayer"
EMBEDDED_TOOLS_DIR = APP_DIR / "embedded-tools"
FFMPEG_DIR = EMBEDDED_TOOLS_DIR / "ffmpeg"
SEVEN_ZIP_ZSTD_EXE = EMBEDDED_TOOLS_DIR / "7zip-zstd" / "7z.exe"


def portable_settings() -> QSettings:
    """Return the portable INI settings store, migrating the legacy registry once."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(SETTINGS_PATH), QSettings.Format.IniFormat)
    if not settings.contains("storage/migrated_from_registry"):
        legacy = QSettings("ARXChem", "ModelScopeManager")
        if legacy.allKeys() and not settings.allKeys():
            for key in legacy.allKeys():
                settings.setValue(key, legacy.value(key))
        settings.setValue("storage/migrated_from_registry", True)
        settings.sync()
        if legacy.allKeys():
            legacy.clear()
            legacy.sync()
    return settings


class DeviceIdentity:
    """A random device ID whose on-disk form can only be opened on its device."""

    def __init__(
        self,
        path: Path = DEVICE_ID_PATH,
        protector: Callable[[str], str] = protect_compatible,
        unprotector: Callable[[str], str] = unprotect,
    ):
        self.path = path
        self.protector = protector
        self.unprotector = unprotector

    def load_or_create(self) -> tuple[str, bool]:
        """Return (device_id, replaced_invalid_copy)."""
        had_identity = self.path.exists()
        if had_identity:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                device_id = self.unprotector(str(payload["protected_id"]))
                uuid.UUID(device_id)
                return device_id, False
            except (OSError, ValueError, KeyError, TypeError):
                pass
        device_id = str(uuid.uuid4())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "protected_id": self.protector(device_id)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return device_id, had_identity


def restore_device_bound_token(
    settings: QSettings,
    device_id: str,
    identity_replaced: bool,
    *,
    destroy_on_device_change: bool = True,
) -> str:
    """Restore a token only when it is bound to this device, destroying copied data."""
    encrypted = str(settings.value("token", ""))
    if not encrypted:
        settings.remove("token_device_id")
        return ""
    bound_id = str(settings.value("token_device_id", ""))
    if identity_replaced or (bound_id and bound_id != device_id):
        if destroy_on_device_change:
            destroy_saved_token(settings)
        return ""
    try:
        token = unprotect(encrypted)
    except Exception:
        if destroy_on_device_change:
            destroy_saved_token(settings)
        return ""
    # Bind tokens migrated from the former registry-based settings on first use.
    if not bound_id:
        settings.setValue("token_device_id", device_id)
        settings.sync()
    return token


def save_device_bound_token(settings: QSettings, token: str, device_id: str) -> None:
    settings.setValue("token", protect(token))
    settings.setValue("token_device_id", device_id)
    settings.sync()


def destroy_saved_token(settings: QSettings) -> None:
    settings.setValue("token", "")
    settings.setValue("token_device_id", "")
    settings.sync()
    settings.remove("token")
    settings.remove("token_device_id")
    settings.sync()
