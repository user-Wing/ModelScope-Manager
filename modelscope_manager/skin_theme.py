"""Multi-background skin configuration and note-template helpers."""

from __future__ import annotations

import configparser
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .storage import CONFIG_DIR


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SKIN_ROOT = CONFIG_DIR / "skin"
DEFAULT_TOP_NOTE = "ModelScope Manager"
DEFAULT_BOTTOM_NOTE = "上传速度：[US] | 下载速度：[DS]"
RESERVED_KEYS = {
    "skin_name", "color_mode", "color", "auto_color_image", "random_bg", "top_note", "bottom_note",
    "image", "theme_color", "brightness",
}


@dataclass
class SkinImage:
    filename: str
    pages: dict[int, int] = field(default_factory=dict)

    def page_text(self) -> str:
        return ",".join(
            f"{page}#{_clamp_brightness(brightness)}"
            for page, brightness in sorted(self.pages.items())
        )

    def brightness(self) -> int:
        return next(iter(self.pages.values()), 100)


@dataclass
class SkinConfig:
    skin_name: str = "默认皮肤"
    color_mode: str = "system"
    color: str = "auto"
    auto_color_image: str = ""
    random_bg: bool = False
    top_note: str = DEFAULT_TOP_NOTE
    bottom_note: str = DEFAULT_BOTTOM_NOTE
    images: list[SkinImage] = field(default_factory=list)


def _clamp_brightness(value: int) -> int:
    return min(180, max(20, int(value)))


def parse_page_assignments(value: str) -> dict[int, int]:
    pages: dict[int, int] = {}
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(-1|[0-6])(?:#(\d{1,3}))?", part)
        if not match:
            continue
        pages[int(match.group(1))] = _clamp_brightness(int(match.group(2) or 100))
    return pages


def safe_skin_image(directory: Path, filename: str) -> Path | None:
    name = Path(str(filename)).name
    if name != str(filename) or Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        return None
    path = (directory / name).resolve()
    try:
        path.relative_to(directory.resolve())
    except ValueError:
        return None
    return path


def safe_skin_folder_name(value: str, fallback: str = "skin") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip().rstrip(". ")
    if not name:
        name = fallback
    if name.upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = f"_{name}"
    return name[:80]


def skin_directories(root: Path = SKIN_ROOT) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (item for item in root.iterdir() if item.is_dir() and (item / "skin.ini").is_file()),
        key=lambda item: item.name.casefold(),
    )


def unique_skin_directory(root: Path, preferred: str) -> Path:
    base = safe_skin_folder_name(preferred)
    candidate = root / base
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base}-{suffix}"
        suffix += 1
    return candidate


def migrate_legacy_skin(root: Path = SKIN_ROOT) -> Path | None:
    """Move the former flat skin layout into one library child directory."""
    legacy_ini = root / "skin.ini"
    if not legacy_ini.is_file():
        return None
    try:
        had_skin_name = bool(re.search(r"^\s*skin_name\s*=", legacy_ini.read_text(encoding="utf-8-sig"), re.MULTILINE | re.IGNORECASE))
    except (OSError, UnicodeError):
        had_skin_name = False
    config = load_skin_config(legacy_ini)
    if not had_skin_name or not config.skin_name.strip():
        config.skin_name = "当前皮肤"
    target = unique_skin_directory(root, "current")
    target.mkdir(parents=True, exist_ok=False)
    legacy_images = [item for item in root.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES]
    try:
        for item in legacy_images:
            shutil.copy2(item, target / item.name)
        save_skin_config(target / "skin.ini", config)
    except Exception:
        for item in target.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
        target.rmdir()
        raise
    legacy_ini.unlink(missing_ok=True)
    for item in legacy_images:
        item.unlink(missing_ok=True)
    return target


def ensure_skin_library(active_folder: str = "", root: Path = SKIN_ROOT) -> tuple[Path, SkinConfig]:
    root.mkdir(parents=True, exist_ok=True)
    migrated = migrate_legacy_skin(root)
    requested = Path(str(active_folder)).name
    selected = next((item for item in skin_directories(root) if item.name == requested), None)
    if selected is None and migrated is not None:
        selected = migrated
    if selected is None:
        directories = skin_directories(root)
        selected = directories[0] if directories else root / "default"
    config_path = selected / "skin.ini"
    if not config_path.is_file():
        selected.mkdir(parents=True, exist_ok=True)
        save_skin_config(config_path, SkinConfig())
    config = load_skin_config(config_path)
    if not config.skin_name.strip():
        config.skin_name = selected.name
        save_skin_config(config_path, config)
    return selected, config


def load_skin_config(path: Path) -> SkinConfig:
    config = SkinConfig()
    if not path.is_file():
        return config
    try:
        raw = path.read_text(encoding="utf-8-sig")
        if not re.search(r"^\s*\[.+?\]\s*$", raw, re.MULTILINE):
            raw = "[skin]\n" + raw
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(raw)
        section = parser["skin"] if parser.has_section("skin") else parser[parser.sections()[0]]
    except (OSError, UnicodeError, configparser.Error, IndexError):
        return config

    lower_values = {key.lower(): value for key, value in section.items()}
    config.skin_name = re.sub(
        r"[\x00\r\n]+", " ", lower_values.get("skin_name", path.parent.name)
    ).strip() or path.parent.name
    mode = lower_values.get("color_mode", "system").strip().lower()
    config.color_mode = mode if mode in {"light", "dark", "system"} else "system"
    color = lower_values.get("color", "auto").strip()
    if "color" not in lower_values and lower_values.get("theme_color", "").strip():
        color = lower_values["theme_color"].strip()
    config.color = color or "auto"
    config.auto_color_image = Path(lower_values.get("auto_color_image", "").strip()).name
    config.random_bg = lower_values.get("random_bg", "0").strip().lower() in {"1", "true", "yes", "on"}
    config.top_note = lower_values.get("top_note", DEFAULT_TOP_NOTE)
    config.bottom_note = lower_values.get("bottom_note", DEFAULT_BOTTOM_NOTE)
    for key, value in section.items():
        if key.lower() in RESERVED_KEYS or Path(key).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        image = safe_skin_image(path.parent, key)
        if image is None or not image.is_file():
            continue
        config.images.append(SkinImage(image.name, parse_page_assignments(value)))
    if not config.images and lower_values.get("image", "").strip():
        legacy = safe_skin_image(path.parent, lower_values["image"].strip())
        if legacy is not None and legacy.is_file():
            try:
                brightness = _clamp_brightness(int(lower_values.get("brightness", "100") or 100))
            except ValueError:
                brightness = 100
            config.images.append(SkinImage(legacy.name, {page: brightness for page in [-1, *range(7)]}))
            config.auto_color_image = legacy.name
    return config


def save_skin_config(path: Path, config: SkinConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"skin_name={re.sub(r'[\x00\r\n]+', ' ', config.skin_name).strip() or path.parent.name}",
        f"color_mode={config.color_mode}",
        f"color={config.color}",
        f"auto_color_image={config.auto_color_image}",
        f"random_bg={1 if config.random_bg else 0}",
        f"top_note={config.top_note}",
        f"bottom_note={config.bottom_note}",
    ]
    for image in config.images:
        assignments = ",".join(
            f"{page}#{_clamp_brightness(brightness)}"
            for page, brightness in sorted(image.pages.items())
        )
        lines.append(f"{image.filename}={assignments}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def render_note(template: str, values: dict[str, str]) -> str:
    text = str(template)
    for token, value in values.items():
        text = text.replace(f"[{token}]", str(value))
    return re.sub(r"\s*\|\s*", "  |  ", text).strip()
