from __future__ import annotations

import json
import random
import shutil
import subprocess
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton, QTableWidgetItem, QColorDialog

from .fluent_ui import CleanComboBox
from .skin_theme import (
    SKIN_ROOT, SkinImage, load_skin_config, parse_page_assignments,
    safe_skin_folder_name, safe_skin_image, save_skin_config, skin_directories,
    unique_skin_directory,
)
from .storage import APP_DIR, SEVEN_ZIP_ZSTD_EXE
from .windows_integration import (
    cleanup_shortcuts as cleanup_shortcuts_impl,
    configure_local_http_webdav,
    create_desktop_shortcut as create_desktop_shortcut_impl,
    create_start_menu_shortcut as create_start_menu_shortcut_impl,
    create_this_pc_shortcut as create_this_pc_shortcut_impl,
    mapped_drive_remote,
    mount_webdav_drive,
    unmount_webdav_drive,
    webdav_unc,
)

class CustomizationMixin:
    def _font_family_changed(self, *_args) -> None:
        if self._restoring_settings:
            return
        self.settings.setValue("font/western", self.western_font_combo.currentText())
        self.settings.setValue("font/chinese", self.chinese_font_combo.currentText())
        self._apply_font_scale(self.font_size_spin.value())

    def _choose_skin_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择背景图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._install_skin_image(Path(path))

    def _install_skin_image(self, source: Path) -> None:
        if not source.is_file() or source.stat().st_size > 10 * 1024**2:
            QMessageBox.warning(self, "背景图片无效", "图片必须存在且不超过 10 MB。")
            return
        if QImage(str(source)).isNull():
            QMessageBox.warning(self, "背景图片无效", "Qt 无法读取该图片。")
            return
        self.skin_directory.mkdir(parents=True, exist_ok=True)
        target = self.skin_directory / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        self._dominant_skin_color.cache_clear()
        existing = next((item for item in self.skin_config.images if item.filename.casefold() == target.name.casefold()), None)
        if existing is None:
            existing = SkinImage(target.name, {})
            self.skin_config.images.append(existing)
        self.skin_config.auto_color_image = self.skin_config.auto_color_image or target.name
        self._skin_random_assignments.clear()
        self._save_skin_config()
        self._render_skin_table(target.name)
        self._refresh_skin_color_controls()
        self._apply_theme()

    @staticmethod
    @lru_cache(maxsize=16)
    def _dominant_skin_color(path: Path) -> QColor:
        image = QImage(str(path)).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio)
        if image.isNull():
            return QColor("#0078D4")
        colors = []
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() > 32 and color.saturation() > 45:
                    colors.append(color)
        if not colors:
            colors = [image.pixelColor(image.width() // 2, image.height() // 2)]
        color = max(colors, key=lambda item: item.saturation() * 2 + item.value())
        color.setHsv(color.hue(), min(220, max(100, color.saturation())), min(220, max(100, color.value())))
        return color

    def _skin_accent_color(self) -> QColor:
        if str(self.skin_config.color).lower() != "auto":
            color = QColor(self.skin_config.color)
            return color if color.isValid() else QColor("#0078D4")
        names = [item.filename for item in self.skin_config.images]
        filename = self.skin_config.auto_color_image if self.skin_config.auto_color_image in names else (names[0] if names else "")
        path = safe_skin_image(self.skin_directory, filename) if filename else None
        return self._dominant_skin_color(path) if path and path.is_file() else QColor("#0078D4")

    def _save_skin_config(self) -> None:
        save_skin_config(self.skin_config_path, self.skin_config)

    def _render_skin_table(self, select_filename: str = "") -> None:
        if not hasattr(self, "skin_image_table"):
            return
        table = self.skin_image_table
        table.blockSignals(True)
        table.setRowCount(len(self.skin_config.images))
        selected_row = -1
        editable = not self.skin_config.random_bg
        for row, image in enumerate(self.skin_config.images):
            name_item = QTableWidgetItem(image.filename)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            pages_item = QTableWidgetItem(image.page_text())
            if not editable:
                pages_item.setFlags(pages_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            pages_item.setToolTip("页面编号：关于=-1，设置=0，资源管理=1，资源搜索=2，传输列表=3，备份文件夹=4，图床=5，WebDAV 映射=6")
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, pages_item)
            if image.filename == select_filename:
                selected_row = row
        table.blockSignals(False)
        if selected_row < 0 and table.rowCount():
            selected_row = 0
        if selected_row >= 0:
            table.selectRow(selected_row)
        self._skin_selection_changed()

    def _skin_table_changed(self, row: int, column: int) -> None:
        if self._restoring_settings or self.skin_config.random_bg or column != 1:
            return
        if not 0 <= row < len(self.skin_config.images):
            return
        item = self.skin_image_table.item(row, column)
        image = self.skin_config.images[row]
        image.pages = parse_page_assignments(item.text() if item else "")
        normalized = image.page_text()
        if item and item.text() != normalized:
            self.skin_image_table.blockSignals(True)
            item.setText(normalized)
            self.skin_image_table.blockSignals(False)
        self._save_skin_config()
        self._apply_theme()

    def _skin_selection_changed(self) -> None:
        if not hasattr(self, "skin_brightness_slider"):
            return
        row = self.skin_image_table.currentRow()
        enabled = 0 <= row < len(self.skin_config.images) and not self.skin_config.random_bg
        self.skin_brightness_slider.setEnabled(enabled)
        if enabled:
            self.skin_brightness_slider.blockSignals(True)
            self.skin_brightness_slider.setValue(self.skin_config.images[row].brightness())
            self.skin_brightness_slider.blockSignals(False)

    def _skin_brightness_changed(self, value: int) -> None:
        if self._restoring_settings:
            return
        row = self.skin_image_table.currentRow()
        if not 0 <= row < len(self.skin_config.images) or self.skin_config.random_bg:
            return
        image = self.skin_config.images[row]
        image.pages = {page: int(value) for page in image.pages}
        self._save_skin_config()
        self._apply_theme()

    def _skin_settings_changed(self, *_args) -> None:
        if self._restoring_settings:
            return
        color = QColor(self.skin_color_edit.text().strip())
        if not color.isValid():
            color = QColor("#0078D4")
            self.skin_color_edit.setText(color.name())
        self.skin_config.color = color.name()
        self.skin_color_source_combo.blockSignals(True)
        self.skin_color_source_combo.setCurrentIndex(self.skin_color_source_combo.findData("custom"))
        self.skin_color_source_combo.blockSignals(False)
        self._save_skin_config()
        self._refresh_skin_color_controls()
        self._apply_theme()

    def _choose_skin_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.skin_color_edit.text()), self, "选择主题颜色")
        if color.isValid():
            self.skin_color_edit.setText(color.name())
            self._skin_settings_changed()

    def _skin_color_source_changed(self) -> None:
        if self._restoring_settings:
            return
        source = str(self.skin_color_source_combo.currentData())
        if source == "auto" and not self.skin_config.images:
            self.skin_color_source_combo.setCurrentIndex(self.skin_color_source_combo.findData("custom"))
            return
        self.skin_config.color = "auto" if source == "auto" else self.skin_color_edit.text().strip()
        self._save_skin_config()
        self._refresh_skin_color_controls()
        self._apply_theme()

    def _skin_auto_image_changed(self) -> None:
        if self._restoring_settings:
            return
        self.skin_config.auto_color_image = str(self.skin_auto_image_combo.currentData() or "")
        self._save_skin_config()
        if self.skin_config.color.lower() == "auto":
            self._apply_theme()

    def _skin_random_changed(self, checked: bool) -> None:
        if self._restoring_settings:
            return
        self.skin_config.random_bg = bool(checked)
        self._skin_random_assignments.clear()
        self._save_skin_config()
        self._render_skin_table()
        self._apply_theme()

    def _skin_notes_changed(self) -> None:
        if self._restoring_settings:
            return
        self.skin_config.top_note = self.skin_top_note_edit.text()
        self.skin_config.bottom_note = self.skin_bottom_note_edit.text()
        self._save_skin_config()
        self._refresh_theme_notes()

    def _refresh_skin_color_controls(self) -> None:
        if not hasattr(self, "skin_color_source_combo"):
            return
        names = [item.filename for item in self.skin_config.images]
        self.skin_color_source_combo.setItemEnabled(0, bool(names))
        self.skin_auto_image_combo.blockSignals(True)
        self.skin_auto_image_combo.clear()
        for name in names:
            self.skin_auto_image_combo.addItem(name, userData=name)
        self.skin_auto_image_combo.setProperty("i18nItems", list(names))
        selected = self.skin_config.auto_color_image if self.skin_config.auto_color_image in names else (names[0] if names else "")
        if selected:
            self.skin_config.auto_color_image = selected
            self.skin_auto_image_combo.setCurrentIndex(self.skin_auto_image_combo.findData(selected))
        self.skin_auto_image_combo.blockSignals(False)
        self.skin_auto_image_label.setVisible(len(names) > 1)
        self.skin_auto_image_combo.setVisible(len(names) > 1)
        automatic = self.skin_config.color.lower() == "auto" and bool(names)
        self.skin_color_source_combo.blockSignals(True)
        self.skin_color_source_combo.setCurrentIndex(
            max(0, self.skin_color_source_combo.findData("auto" if automatic else "custom"))
        )
        self.skin_color_source_combo.blockSignals(False)
        self.skin_color_edit.setEnabled(not automatic)
        self.skin_color_button.setEnabled(not automatic)

    def _remove_skin_background(self) -> None:
        row = self.skin_image_table.currentRow()
        if not 0 <= row < len(self.skin_config.images):
            return
        image = self.skin_config.images.pop(row)
        path = safe_skin_image(self.skin_directory, image.filename)
        if path:
            path.unlink(missing_ok=True)
        if self.skin_config.auto_color_image == image.filename:
            self.skin_config.auto_color_image = ""
        self._skin_random_assignments.clear()
        self._save_skin_config()
        self._render_skin_table()
        self._refresh_skin_color_controls()
        self._apply_theme()

    def _resolve_page_skin(self, page_number: int) -> tuple[str, int]:
        images = [
            item for item in self.skin_config.images
            if (safe_skin_image(self.skin_directory, item.filename) or Path()).is_file()
        ]
        if not images:
            return "", 100
        if self.skin_config.random_bg:
            filename = self._skin_random_assignments.get(page_number)
            image = next((item for item in images if item.filename == filename), None)
            if image is None:
                image = random.choice(images)
                self._skin_random_assignments[page_number] = image.filename
            path = safe_skin_image(self.skin_directory, image.filename)
            return (str(path), 100) if path else ("", 100)
        for image in images:
            if page_number in image.pages:
                path = safe_skin_image(self.skin_directory, image.filename)
                return (str(path), image.pages[page_number]) if path else ("", 100)
        return "", 100

    def _has_skin_images(self) -> bool:
        return any(
            path is not None and path.is_file()
            for item in self.skin_config.images
            for path in (safe_skin_image(self.skin_directory, item.filename),)
        )

    def _apply_page_background(self, page=None) -> None:
        if not hasattr(self, "skin_background_layer"):
            return
        page = page or (self.page_stack.currentWidget() if hasattr(self, "page_stack") else None)
        number = getattr(self, "_skin_page_numbers", {}).get(page)
        background, brightness = self._resolve_page_skin(number) if number is not None else ("", 100)
        if page is not None:
            page.setProperty("skinBackground", "true" if background else "false")
            page.style().unpolish(page)
            page.style().polish(page)
        self.skin_background_layer.setGeometry(self.rect())
        self.skin_background_layer.set_background(background, brightness)
        self.skin_background_layer.lower()

    def _refresh_skin_selector(self) -> None:
        if not hasattr(self, "skin_selector_combo"):
            return
        combo = self.skin_selector_combo
        combo.blockSignals(True)
        combo.clear()
        for directory in skin_directories():
            config = load_skin_config(directory / "skin.ini")
            combo.addItem(config.skin_name or directory.name, userData=directory.name)
        index = combo.findData(self.skin_directory.name)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _switch_skin(self, *_args) -> None:
        if self._restoring_settings or not hasattr(self, "skin_selector_combo"):
            return
        folder = str(self.skin_selector_combo.currentData() or "")
        directory = next((item for item in skin_directories() if item.name == folder), None)
        if directory is None or directory == self.skin_directory:
            return
        self.skin_directory = directory
        self.skin_config_path = directory / "skin.ini"
        self.skin_config = load_skin_config(self.skin_config_path)
        self.settings.setValue("skin/active_folder", directory.name)
        self._skin_random_assignments.clear()
        self._load_skin_controls_from_config()
        self._apply_theme()

    def _skin_name_changed(self) -> None:
        if self._restoring_settings:
            return
        name = self.skin_name_edit.text().strip() or self.skin_directory.name
        self.skin_name_edit.setText(name)
        self.skin_config.skin_name = name
        self._save_skin_config()
        self._refresh_skin_selector()

    @staticmethod
    def _skin_source_directory(root: Path) -> Path:
        if (root / "skin.ini").is_file():
            return root
        candidates = [item for item in root.iterdir() if item.is_dir() and (item / "skin.ini").is_file()]
        if len(candidates) != 1:
            raise ValueError("皮肤包必须只包含一个带 skin.ini 的皮肤文件夹。")
        return candidates[0]

    def _install_skin_pack(self, source: Path, suggested_name: str = "") -> Path:
        source = self._skin_source_directory(source.resolve())
        imported = load_skin_config(source / "skin.ini")
        if not imported.skin_name.strip():
            imported.skin_name = suggested_name or source.name
        valid: list[SkinImage] = []
        for image in imported.images:
            source_image = safe_skin_image(source, image.filename)
            if (
                source_image is None or not source_image.is_file()
                or source_image.stat().st_size > 10 * 1024**2
                or QImage(str(source_image)).isNull()
            ):
                continue
            valid.append(image)
        imported.images = valid
        preferred = imported.skin_name or suggested_name or source.name
        target = unique_skin_directory(SKIN_ROOT, safe_skin_folder_name(preferred))
        target.mkdir(parents=True, exist_ok=False)
        try:
            for image in valid:
                shutil.copy2(source / image.filename, target / image.filename)
            save_skin_config(target / "skin.ini", imported)
        except Exception:
            for item in target.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)
            target.rmdir()
            raise
        self.skin_directory = target
        self.skin_config_path = target / "skin.ini"
        self.skin_config = imported
        self.settings.setValue("skin/active_folder", target.name)
        self._skin_random_assignments.clear()
        self._load_skin_controls_from_config()
        self._apply_theme()
        return target

    def _import_skin_pack(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择包含 skin.ini 的皮肤文件夹")
        if not path:
            return
        try:
            target = self._install_skin_pack(Path(path))
            QMessageBox.information(self, "皮肤导入", f"已导入“{self.skin_config.skin_name}”到 {target}。")
        except Exception as exc:
            QMessageBox.warning(self, "皮肤导入失败", str(exc))

    @staticmethod
    def _extract_skin_archive(archive: Path, destination: Path) -> None:
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as package:
                for info in package.infolist():
                    member = Path(info.filename.replace("\\", "/"))
                    if member.is_absolute() or ".." in member.parts:
                        raise ValueError("压缩包包含不安全的路径。")
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("压缩包不能包含符号链接。")
                package.extractall(destination)
            return
        if archive.suffix.lower() != ".7z":
            raise ValueError("仅支持 ZIP 和 7z 皮肤包。")
        if not SEVEN_ZIP_ZSTD_EXE.is_file():
            raise FileNotFoundError("内置 7z 工具不存在。")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        listing = subprocess.run(
            [str(SEVEN_ZIP_ZSTD_EXE), "l", "-slt", str(archive)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=creation_flags, check=True,
        ).stdout
        records = listing.split("----------", 1)[-1]
        for line in records.splitlines():
            if not line.startswith("Path = "):
                continue
            member = Path(line[7:].replace("\\", "/"))
            if member.is_absolute() or ".." in member.parts:
                raise ValueError("压缩包包含不安全的路径。")
        subprocess.run(
            [str(SEVEN_ZIP_ZSTD_EXE), "x", str(archive), f"-o{destination}", "-y"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=creation_flags, check=True,
        )

    def _import_skin_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入皮肤压缩包", "", "皮肤包 (*.zip *.7z)")
        if not path:
            return
        archive = Path(path).resolve()
        try:
            with tempfile.TemporaryDirectory(prefix="modelscope-skin-") as temporary:
                extracted = Path(temporary)
                self._extract_skin_archive(archive, extracted)
                target = self._install_skin_pack(extracted, archive.stem)
            QMessageBox.information(self, "皮肤导入", f"已导入“{self.skin_config.skin_name}”到 {target}。")
        except Exception as exc:
            QMessageBox.warning(self, "皮肤导入失败", str(exc))

    def _export_skin_pack(self) -> None:
        default_name = safe_skin_folder_name(self.skin_config.skin_name or self.skin_directory.name)
        default_path = APP_DIR / f"{default_name}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "导出当前皮肤", str(default_path), "ZIP 压缩包 (*.zip)")
        if not path:
            return
        archive = Path(path)
        if archive.suffix.lower() != ".zip":
            archive = archive.with_suffix(".zip")
        try:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
                prefix = self.skin_directory.name
                for item in self.skin_directory.iterdir():
                    if item.is_file():
                        package.write(item, f"{prefix}/{item.name}")
            QMessageBox.information(self, "皮肤导出", f"已导出到 {archive}。")
        except Exception as exc:
            QMessageBox.warning(self, "皮肤导出失败", str(exc))

    def _load_skin_controls_from_config(self) -> None:
        if not hasattr(self, "theme_combo"):
            return
        was_restoring = self._restoring_settings
        self._restoring_settings = True
        self._refresh_skin_selector()
        self.skin_name_edit.setText(self.skin_config.skin_name)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.skin_config.color_mode)))
        source = "auto" if self.skin_config.color.lower() == "auto" and self.skin_config.images else "custom"
        self.skin_color_source_combo.setCurrentIndex(max(0, self.skin_color_source_combo.findData(source)))
        if source == "custom":
            color = QColor(self.skin_config.color)
            self.skin_color_edit.setText(color.name() if color.isValid() else "#0078D4")
        self.skin_random_switch.setChecked(self.skin_config.random_bg)
        self.skin_top_note_edit.setText(self.skin_config.top_note)
        self.skin_bottom_note_edit.setText(self.skin_config.bottom_note)
        self._restoring_settings = was_restoring
        self._render_skin_table()
        self._refresh_skin_color_controls()

    @staticmethod
    def _portable_setting_allowed(key: str) -> bool:
        lower = key.lower()
        if lower == "theme" or lower.startswith(("skin/", "experiments/", "statistics/")):
            return False
        return lower not in {
            "token", "token_device_id", "alist/password", "storage/migrated_from_registry",
            "transfer/pending_uploads", "transfer/pending_downloads",
        }

    def _export_settings_ini(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出设置", "ModelScope-Manager-settings.ini", "INI 文件 (*.ini)")
        if not path:
            return
        exported = QSettings(path, QSettings.Format.IniFormat)
        exported.clear()
        for key in self.settings.allKeys():
            if self._portable_setting_allowed(key):
                exported.setValue(key, self.settings.value(key))
        exported.sync()
        QMessageBox.information(self, "导出设置", "设置已导出；皮肤、实验性功能、凭据、流量统计和未完成队列未包含。")

    def _import_settings_ini(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入设置", "", "INI 文件 (*.ini)")
        if not path:
            return
        imported = QSettings(path, QSettings.Format.IniFormat)
        keys = [key for key in imported.allKeys() if self._portable_setting_allowed(key)]
        if not keys:
            QMessageBox.warning(self, "导入设置", "该文件中没有可导入的设置。")
            return
        for key in keys:
            self.settings.setValue(key, imported.value(key))
        self.settings.sync()
        self._restore_settings()
        QMessageBox.information(self, "导入设置", f"已导入 {len(keys)} 项设置；GPU 启动模式会在下次启动生效。")

    def _integration_result(self, title: str, action) -> None:
        try:
            action()
        except Exception as exc:
            QMessageBox.warning(self, self._t(title), str(exc))
        else:
            QMessageBox.information(self, self._t(title), self._t("操作完成。"))

    def create_desktop_shortcut(self) -> None:
        self._integration_result("桌面快捷方式", lambda: create_desktop_shortcut_impl(APP_DIR))

    def create_start_menu_shortcut(self, public: bool) -> None:
        self._integration_result("开始菜单快捷方式", lambda: create_start_menu_shortcut_impl(APP_DIR, public))

    def create_this_pc_shortcut(self) -> None:
        self._integration_result("此电脑快捷方式", lambda: create_this_pc_shortcut_impl(APP_DIR))

    def cleanup_windows_shortcuts(self) -> None:
        self._integration_result("清理快捷入口", lambda: cleanup_shortcuts_impl(APP_DIR))

    @staticmethod
    def _valid_local_webdav_path(value: str) -> str:
        clean = "/" + str(value or "dav").replace("\\", "/").strip("/")
        if "/" in clean[1:] or clean in {"/", "/."}:
            return "/dav"
        return clean

    def _restore_webdav_local_mounts(self) -> None:
        raw = str(self.settings.value("webdav/local_mounts", ""))
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            values = []
        mounts: list[dict[str, str]] = []
        used: set[str] = set()
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            letter = str(value.get("letter", "")).rstrip(":").upper()
            if letter not in "DEFGHIJKLMNOPQRSTUVWXYZ" or letter in used:
                continue
            mounts.append({"letter": letter, "path": self._valid_local_webdav_path(value.get("path", "/dav"))})
            used.add(letter)
        self.webdav_local_mounts = mounts or [{"letter": "Z", "path": "/dav"}]
        self._render_webdav_drive_table()

    def _save_webdav_local_mounts(self) -> None:
        if getattr(self, "_restoring_settings", False):
            return
        self.settings.setValue(
            "webdav/local_mounts",
            json.dumps(self.webdav_local_mounts, ensure_ascii=False, separators=(",", ":")),
        )
        self.settings.sync()

    def _available_webdav_mount_paths(self) -> list[str]:
        return ["/dav", *(f"/{item.name}" for item in self.webdav_mappings)]

    def _render_webdav_drive_table(self) -> None:
        if not hasattr(self, "webdav_drive_table"):
            return
        self._rendering_webdav_drives = True
        table = self.webdav_drive_table
        table.setRowCount(len(self.webdav_local_mounts))
        paths = self._available_webdav_mount_paths()
        for row, value in enumerate(self.webdav_local_mounts):
            drive_combo = CleanComboBox()
            for letter in "ZYXWVUTSRQPONMLKJIHGFED":
                drive_combo.addItem(letter + ":", userData=letter)
            drive_combo.setCurrentIndex(max(0, drive_combo.findData(value["letter"])))
            drive_combo.currentIndexChanged.connect(
                lambda _index, target_row=row, combo=drive_combo: self._webdav_drive_row_changed(
                    target_row, letter=str(combo.currentData() or "Z")
                )
            )
            table.setCellWidget(row, 0, drive_combo)

            point_combo = CleanComboBox()
            row_paths = list(paths)
            if value["path"] not in row_paths:
                row_paths.append(value["path"])
            for path in row_paths:
                point_combo.addItem(path, userData=path)
            point_combo.setCurrentIndex(max(0, point_combo.findData(value["path"])))
            point_combo.currentIndexChanged.connect(
                lambda _index, target_row=row, combo=point_combo: self._webdav_drive_row_changed(
                    target_row, path=str(combo.currentData() or "/dav")
                )
            )
            table.setCellWidget(row, 1, point_combo)

            status = QTableWidgetItem(self._t("尚未挂载"))
            status.setFlags(status.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 2, status)
            mount_button = QPushButton(self._t("管理员身份挂载"))
            mount_button.clicked.connect(lambda _checked=False, target_row=row: self._mount_webdav_row(target_row))
            table.setCellWidget(row, 3, mount_button)
            unmount_button = QPushButton(self._t("卸载"))
            unmount_button.clicked.connect(lambda _checked=False, target_row=row: self._unmount_webdav_row(target_row))
            table.setCellWidget(row, 4, unmount_button)
        self._rendering_webdav_drives = False
        self._refresh_webdav_drive_statuses()

    def _webdav_drive_row_changed(self, row: int, *, letter: str | None = None, path: str | None = None) -> None:
        if getattr(self, "_rendering_webdav_drives", False) or not 0 <= row < len(self.webdav_local_mounts):
            return
        if letter is not None:
            duplicate = next((index for index, item in enumerate(self.webdav_local_mounts) if index != row and item["letter"] == letter), None)
            if duplicate is not None:
                QMessageBox.warning(self, self._t("盘符重复"), self._t("同一个盘符只能配置一次。"))
                self._render_webdav_drive_table()
                return
            self.webdav_local_mounts[row]["letter"] = letter
        if path is not None:
            self.webdav_local_mounts[row]["path"] = self._valid_local_webdav_path(path)
        self._save_webdav_local_mounts()
        self._refresh_webdav_drive_statuses()

    def _add_webdav_drive_mapping(self) -> None:
        used = {item["letter"] for item in self.webdav_local_mounts}
        letter = next((value for value in "ZYXWVUTSRQPONMLKJIHGFED" if value not in used), "")
        if not letter:
            QMessageBox.warning(self, self._t("无法添加盘符"), self._t("没有可用的盘符可供配置。"))
            return
        self.webdav_local_mounts.append({"letter": letter, "path": "/dav"})
        self._save_webdav_local_mounts()
        self._render_webdav_drive_table()
        self.webdav_drive_table.selectRow(len(self.webdav_local_mounts) - 1)

    def _remove_webdav_drive_mapping(self) -> None:
        row = self.webdav_drive_table.currentRow()
        if not 0 <= row < len(self.webdav_local_mounts):
            return
        try:
            connected = bool(mapped_drive_remote(self.webdav_local_mounts[row]["letter"]))
        except OSError:
            connected = False
        if connected:
            QMessageBox.warning(self, self._t("盘符仍在挂载"), self._t("请先卸载该盘符，再删除配置。"))
            return
        self.webdav_local_mounts.pop(row)
        if not self.webdav_local_mounts:
            self.webdav_local_mounts.append({"letter": "Z", "path": "/dav"})
        self._save_webdav_local_mounts()
        self._render_webdav_drive_table()

    def _refresh_webdav_drive_mount_points(self) -> None:
        if hasattr(self, "webdav_drive_table") and hasattr(self, "webdav_local_mounts"):
            self._render_webdav_drive_table()

    def _webdav_mapping_renamed(self, old_name: str, new_name: str) -> None:
        old_path = "/" + old_name
        for value in self.webdav_local_mounts:
            if value["path"].casefold() == old_path.casefold():
                value["path"] = "/" + new_name
        self._save_webdav_local_mounts()

    def _webdav_mapping_removed(self, name: str) -> None:
        removed = "/" + name
        for value in self.webdav_local_mounts:
            if value["path"].casefold() == removed.casefold():
                value["path"] = "/dav"
        self._save_webdav_local_mounts()

    def _refresh_webdav_drive_statuses(self) -> None:
        if not hasattr(self, "webdav_drive_table"):
            return
        for row, value in enumerate(self.webdav_local_mounts):
            item = self.webdav_drive_table.item(row, 2)
            if item is None:
                continue
            try:
                actual = mapped_drive_remote(value["letter"])
                expected = webdav_unc("127.0.0.1", self.alist_port.value(), value["path"])
                normal = bool(actual) and actual.rstrip("\\").casefold() == expected.rstrip("\\").casefold()
                item.setText(self._t("正常" if normal else "尚未挂载"))
                item.setToolTip(actual if actual and not normal else expected)
            except OSError as exc:
                item.setText(self._t("尚未挂载"))
                item.setToolTip(str(exc))

    def _mount_webdav_row(self, row: int) -> None:
        if not 0 <= row < len(self.webdav_local_mounts):
            return
        if not self.webdav or not self.webdav.running:
            self.apply_alist_settings()
        if not self.webdav or not self.webdav.running:
            return
        if self.alist_host_combo.currentData() != "127.0.0.1":
            QMessageBox.warning(
                self, self._t("无法直挂"), self._t("请先把监听范围改为“仅本机（127.0.0.1）”。")
            )
            return
        answer = QMessageBox.warning(
            self, self._t("启用 Windows HTTP WebDAV"),
            self._t(
                "将通过管理员权限校验 WebClient 系统组件、修复缺失的服务注册项，"
                "配置 BasicAuthLevel 和文件/目录上限后重启服务。"
                "HTTP Basic Auth 不适合局域网，本功能仅挂载 127.0.0.1；Windows 内置客户端仍不适合超过 4 GiB 的单文件。是否继续？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        value = self.webdav_local_mounts[row]
        def action():
            configure_local_http_webdav()
            mount_webdav_drive(
                value["letter"], "127.0.0.1", self.alist_port.value(),
                self.alist_username.text(), self.alist_password.text(), value["path"],
            )
        self._integration_result("Windows WebDAV 挂载", action)
        self._refresh_webdav_drive_statuses()

    def _unmount_webdav_row(self, row: int) -> None:
        if not 0 <= row < len(self.webdav_local_mounts):
            return
        self._integration_result(
            "卸载 WebDAV", lambda: unmount_webdav_drive(self.webdav_local_mounts[row]["letter"])
        )
        self._refresh_webdav_drive_statuses()

    def mount_webdav_locally(self) -> None:
        self._mount_webdav_row(max(0, self.webdav_drive_table.currentRow()))

    def unmount_webdav_locally(self) -> None:
        self._unmount_webdav_row(max(0, self.webdav_drive_table.currentRow()))
