"""图床上传与记录行为。"""

from __future__ import annotations

import tempfile
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QTableWidgetItem
from datetime import datetime
from pathlib import Path
from .app_helpers import is_supported_image_file
from .app_workers import ImageUploadThread
from .avif_converter import AvifOptions, find_ffmpeg
from .image_bed import IMAGE_EXTENSIONS, ImageRecord
from .service import Repository, normalize_remote_path


class ImageBedMixin:
    """图床上传与记录行为。"""

    def _render_image_account_options(self) -> None:
        if not hasattr(self, "image_account_combo"):
            return
        selected = self.image_account_combo.currentData() or self.settings.value("image/account_id", "")
        self.image_account_combo.blockSignals(True)
        self.image_account_combo.clear()
        account_options = [(account.account_id, account.label or account.username) for account in self.accounts]
        for account_id, label in account_options:
            self.image_account_combo.addItem(label, account_id)
        index = self.image_account_combo.findData(selected)
        self.image_account_combo.setCurrentIndex(index if index >= 0 else (0 if account_options else -1))
        self.image_account_combo.blockSignals(False)
        self._render_image_repository_options()

    def _render_image_repository_options(self) -> None:
        if not hasattr(self, "image_repo_combo"):
            return
        account_id = str(self.image_account_combo.currentData() or "")
        selected = self._image_repository_selections.get(account_id)
        if not selected:
            selected = (
                str(self.settings.value(
                    f"image/repositories/{account_id}/repo_type",
                    self.settings.value("image/repo_type", ""),
                )),
                str(self.settings.value(
                    f"image/repositories/{account_id}/repo_id",
                    self.settings.value("image/repo_id", ""),
                )),
            )
        self.image_repo_combo.blockSignals(True)
        self.image_repo_combo.clear()
        for repo in self.account_repositories.get(account_id, []):
            self.image_repo_combo.addItem(repo.repo_id, (repo.repo_type, repo.repo_id))
        index = next((
            candidate for candidate in range(self.image_repo_combo.count())
            if self.image_repo_combo.itemData(candidate) == selected
        ), -1)
        self.image_repo_combo.setCurrentIndex(index if index >= 0 else (0 if self.image_repo_combo.count() else -1))
        self.image_repo_combo.blockSignals(False)
        self._save_image_settings()

    def _save_image_settings(self) -> None:
        if self._restoring_settings or not hasattr(self, "image_account_combo") or not hasattr(self, "avif_keep_metadata"):
            return
        self.settings.setValue("image/account_id", self.image_account_combo.currentData() or "")
        repo_data = self.image_repo_combo.currentData()
        if isinstance(repo_data, tuple) and len(repo_data) == 2:
            account_id = str(self.image_account_combo.currentData() or "")
            self._image_repository_selections[account_id] = repo_data
            self.settings.setValue("image/repo_type", repo_data[0])
            self.settings.setValue("image/repo_id", repo_data[1])
            self.settings.setValue(f"image/repositories/{account_id}/repo_type", repo_data[0])
            self.settings.setValue(f"image/repositories/{account_id}/repo_id", repo_data[1])
        self.settings.setValue("image/destination", self.image_dest_edit.text().strip("/"))
        self.settings.setValue("image/auto_avif", self.image_auto_avif.isChecked())
        self.settings.setValue("image/avif_quality", self.avif_quality.value())
        self.settings.setValue("image/avif_speed", self.avif_speed.value())
        self.settings.setValue("image/avif_chroma", self.avif_chroma.currentData() or "auto")
        self.settings.setValue("image/avif_bit_depth", self.avif_bit_depth.currentData() or "auto")
        self.settings.setValue("image/avif_keep_metadata", self.avif_keep_metadata.isChecked())

    def _render_image_records(self) -> None:
        if not hasattr(self, "image_table"):
            return
        self.image_table.setRowCount(0)
        for record in self.image_records:
            row = self.image_table.rowCount()
            self.image_table.insertRow(row)
            name_item = QTableWidgetItem(Path(record.remote_path).name)
            name_item.setData(Qt.ItemDataRole.UserRole, record.image_id)
            cache = Path(record.cache_path)
            if cache.is_file():
                name_item.setIcon(QIcon(str(cache)))
            self.image_table.setItem(row, 0, name_item)
            self.image_table.setItem(row, 1, QTableWidgetItem(record.repo_id))
            self.image_table.setItem(row, 2, QTableWidgetItem(record.remote_path))
            link_item = QTableWidgetItem(record.direct_url)
            link_item.setToolTip(record.direct_url)
            self.image_table.setItem(row, 3, link_item)
            self.image_table.setItem(row, 4, QTableWidgetItem(self._t("已缓存") if cache.is_file() else self._t("缓存缺失")))
            self.image_table.setItem(row, 5, QTableWidgetItem(datetime.fromtimestamp(record.created_at).strftime("%Y-%m-%d %H:%M")))

    def _selected_image_record(self) -> ImageRecord | None:
        row = self.image_table.currentRow() if hasattr(self, "image_table") else -1
        item = self.image_table.item(row, 0) if row >= 0 else None
        image_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        return next((record for record in self.image_records if record.image_id == image_id), None)

    def _pick_images(self) -> None:
        extensions = " ".join(f"*{extension}" for extension in sorted(IMAGE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, self._t("选择图片"), "", f"Images ({extensions});;All files (*)")
        self._upload_images(paths)

    def _upload_images(self, raw_paths: list[str], temporary_paths: set[Path] | None = None) -> None:
        def discard_temporary_paths() -> None:
            for path in temporary_paths or set():
                path.unlink(missing_ok=True)

        if self.image_upload_thread and self.image_upload_thread.isRunning():
            discard_temporary_paths()
            QMessageBox.information(self, self._t("图片上传中"), self._t("请等待当前图片上传完成。"))
            return
        if (self.task and self.task.isRunning()) or (self.backup_thread and self.backup_thread.isRunning()):
            discard_temporary_paths()
            QMessageBox.information(self, self._t("请稍候"), self._t("当前传输完成后再上传图片。"))
            return
        paths = [Path(raw).resolve() for raw in raw_paths if is_supported_image_file(Path(raw))]
        if not paths:
            discard_temporary_paths()
            QMessageBox.information(self, self._t("没有可上传图片"), self._t("请选择支持的常见图片格式。"))
            return
        account_id = str(self.image_account_combo.currentData() or "")
        repo_data = self.image_repo_combo.currentData()
        service = self.account_services.get(account_id)
        repo = None
        if isinstance(repo_data, tuple) and len(repo_data) == 2:
            repo = next((candidate for candidate in self.account_repositories.get(account_id, [])
                         if candidate.repo_type == repo_data[0] and candidate.repo_id == repo_data[1]), None)
        if not service or not repo:
            discard_temporary_paths()
            QMessageBox.warning(self, self._t("图床配置无效"), self._t("请选择已经验证且可写入的账户仓库。"))
            return
        try:
            destination = normalize_remote_path(self.image_dest_edit.text())
        except ValueError as exc:
            discard_temporary_paths()
            QMessageBox.warning(self, self._t("路径无效"), str(exc))
            return
        avif_options = None
        ffmpeg_path = None
        if self.image_auto_avif.isChecked():
            avif_options = AvifOptions(
                quality=self.avif_quality.value(),
                speed=self.avif_speed.value(),
                chroma=str(self.avif_chroma.currentData() or "auto"),
                bit_depth=str(self.avif_bit_depth.currentData() or "auto"),
                keep_metadata=self.avif_keep_metadata.isChecked(),
            )
            ffmpeg_path = find_ffmpeg()
            if ffmpeg_path is None and any(path.suffix.lower() != ".avif" for path in paths):
                discard_temporary_paths()
                QMessageBox.warning(
                    self,
                    self._t("未找到 FFmpeg"),
                    self._t("自动转换为 AVIF 需要 FFmpeg。请安装到 PATH，或放入 embedded-tools/ffmpeg/ffmpeg.exe。"),
                )
                return
        worker = ImageUploadThread(
            self.image_store, account_id, service, repo, paths, destination,
            temporary_paths=temporary_paths,
            avif_options=avif_options,
            ffmpeg_path=ffmpeg_path,
            parent=self,
        )
        worker.uploaded.connect(self._image_uploaded)
        worker.item_done.connect(self._image_item_done)
        worker.completed.connect(lambda ok, failed, aid=account_id, target=repo: self._image_upload_completed(aid, target, ok, failed))
        worker.finished.connect(lambda: self._image_upload_finished(worker))
        worker.finished.connect(worker.deleteLater)
        self.image_upload_thread = worker
        self.image_status_label.setText(self._tf("正在上传 {count} 张图片…", count=len(paths)))
        self._save_image_settings()
        worker.start()

    def _paste_images_from_clipboard(self) -> None:
        mime_data = QApplication.clipboard().mimeData()
        if mime_data.hasUrls():
            paths = [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]
            self._upload_images(paths)
            return
        if mime_data.hasImage():
            image = QApplication.clipboard().image()
            if not image.isNull():
                with tempfile.NamedTemporaryFile(prefix="modelscope-clipboard-", suffix=".png", delete=False) as handle:
                    path = Path(handle.name).resolve()
                if image.save(str(path), "PNG"):
                    self._upload_images([str(path)], {path})
                    return
                path.unlink(missing_ok=True)
        QMessageBox.information(self, self._t("没有可上传图片"), self._t("剪贴板内容不是图片。"))

    def _image_uploaded(self, record: ImageRecord) -> None:
        self.image_records.insert(0, record)
        self._render_image_records()
        QApplication.clipboard().setText(record.direct_url)
        self.image_status_label.setText(self._t("上传成功，最新图片直链已复制"))

    def _image_item_done(self, path: str, success: bool, message: str) -> None:
        self._log(f"图床 {'完成' if success else '失败'}：{Path(path).name} · {message}")

    def _image_upload_completed(self, account_id: str, repo: Repository, ok: int, failed: int) -> None:
        if ok:
            self._mark_repository_dirty(account_id, repo)
        self.image_status_label.setText(self._tf("图片上传完成：{ok} 个成功，{failed} 个失败", ok=ok, failed=failed))

    def _image_upload_finished(self, worker: ImageUploadThread) -> None:
        if self.image_upload_thread is worker:
            self.image_upload_thread = None

    def _copy_selected_image_link(self) -> None:
        record = self._selected_image_record()
        if record:
            QApplication.clipboard().setText(record.direct_url)
            self.image_status_label.setText(self._t("图片直链已复制"))

    def _open_selected_image(self) -> None:
        record = self._selected_image_record()
        if not record:
            return
        cache_path = Path(record.cache_path)
        if cache_path.is_file():
            self._open_local_media(cache_path)
        else:
            QMessageBox.information(self, self._t("缓存缺失"), self._t("本地缓存已不存在，请重新上传或从仓库下载。"))

    def _remove_selected_image(self) -> None:
        record = self._selected_image_record()
        if not record:
            return
        answer = QMessageBox.question(
            self, self._t("移除图床记录"),
            self._t("仅移除本地记录和缓存，远端文件不会删除。是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.image_store.remove(record.image_id)
        self.image_records = self.image_store.list_records()
        self._render_image_records()
