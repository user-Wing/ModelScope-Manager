"""Software update UI and background tasks."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .updater import (
    ARIA2_EXE,
    Release,
    discover_releases,
    latest_newer_release,
    launch_apply_script,
    prepare_release,
    write_apply_script,
)
from .download_service import Aria2DownloadRunner


class UpdateCheckThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            release = latest_newer_release(__version__, discover_releases())
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(release)


class UpdatePrepareThread(QThread):
    progress = Signal(int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, release: Release, parent=None):
        super().__init__(parent)
        self.release = release
        self.runner = Aria2DownloadRunner(ARIA2_EXE, "")

    def cancel(self) -> None:
        self.requestInterruption()
        self.runner.stop()

    def run(self) -> None:
        try:
            payload = prepare_release(
                self.release,
                self.progress.emit,
                self.runner,
                cancelled=self.isInterruptionRequested,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(str(payload))


class UpdatesMixin:
    """Check, download, and hand an update to the deferred installer."""

    def _auto_update_changed(self, checked: bool) -> None:
        if not self._restoring_settings:
            self.settings.setValue("update/automatic", checked)

    def start_auto_update_check(self) -> None:
        if self.auto_update_checkbox.isChecked():
            self.check_for_updates(manual=False)

    def check_for_updates(self, _checked=False, *, manual: bool = True) -> None:
        if self.update_check_thread and self.update_check_thread.isRunning():
            if manual:
                self.status_bar.showMessage(self._t("正在检查更新…"), 3000)
            return
        self.update_check_manual = manual
        self.update_check_button.setEnabled(False)
        self.update_status_label.setText(self._t("正在检查 ModelScope 更新…"))
        worker = UpdateCheckThread(self)
        worker.completed.connect(self._update_check_completed)
        worker.failed.connect(self._update_check_failed)
        worker.finished.connect(self._update_check_finished)
        worker.finished.connect(worker.deleteLater)
        self.update_check_thread = worker
        worker.start()

    def _update_check_completed(self, release: Release | None) -> None:
        if release is None:
            self.update_status_label.setText(self._tf("当前已是最新版（{version}）", version=__version__))
            if self.update_check_manual:
                QMessageBox.information(
                    self, self._t("检查更新"),
                    self._tf("当前已是最新版（{version}）。", version=__version__),
                )
            return
        self.available_update = release
        self.update_status_label.setText(self._tf("发现新版本 {version}", version=release.version))
        answer = QMessageBox.question(
            self,
            self._t("发现软件更新"),
            self._tf(
                "ModelScope 上发现版本 {version}，是否使用 aria2-next 下载并准备更新？",
                version=release.version,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._prepare_update(release)

    def _update_check_failed(self, error: str) -> None:
        self.update_status_label.setText(self._t("检查更新失败"))
        self._log(f"检查软件更新失败：{error}")
        if self.update_check_manual:
            QMessageBox.warning(self, self._t("检查更新失败"), error)

    def _update_check_finished(self) -> None:
        self.update_check_thread = None
        if not (self.update_prepare_thread and self.update_prepare_thread.isRunning()):
            self.update_check_button.setEnabled(True)

    def _prepare_update(self, release: Release) -> None:
        if self.update_prepare_thread and self.update_prepare_thread.isRunning():
            return
        self.update_check_button.setEnabled(False)
        self.update_status_label.setText(self._tf("正在下载版本 {version}：0%", version=release.version))
        worker = UpdatePrepareThread(release, self)
        worker.progress.connect(
            lambda percent: self.update_status_label.setText(
                self._tf("正在下载版本 {version}：{percent}%", version=release.version, percent=percent)
            )
        )
        worker.completed.connect(lambda path: self._update_prepared(release, Path(path)))
        worker.failed.connect(self._update_prepare_failed)
        worker.finished.connect(self._update_prepare_finished)
        worker.finished.connect(worker.deleteLater)
        self.update_prepare_thread = worker
        worker.start()

    def _update_prepared(self, release: Release, payload_root: Path) -> None:
        self.prepared_update = (release, payload_root)
        self.settings.setValue("update/pending_version", release.version)
        self.settings.sync()
        self.update_status_label.setText(self._tf("版本 {version} 已准备，等待重启", version=release.version))
        self.update_restart_button.show()
        answer = QMessageBox.question(
            self,
            self._t("更新已准备完成"),
            self._t("更新将在退出后替换程序文件，data 数据会保留。是否立即重启？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.restart_to_install_update()

    def _update_prepare_failed(self, error: str) -> None:
        self.update_restart_button.hide()
        self.update_status_label.setText(self._t("更新准备失败"))
        self._log(f"软件更新准备失败：{error}")
        QMessageBox.warning(self, self._t("更新准备失败"), error)

    def _update_prepare_finished(self) -> None:
        self.update_prepare_thread = None
        self.update_check_button.setEnabled(True)

    def restart_to_install_update(self) -> None:
        if not self.prepared_update or not self._confirm_terminate_transfers():
            return
        _release, payload_root = self.prepared_update
        try:
            script = write_apply_script(payload_root)
            launch_apply_script(script, payload_root)
        except Exception as exc:
            QMessageBox.warning(self, self._t("无法启动更新"), str(exc))
            return
        self._shutdown_services()
        self.hide()
        QApplication.instance().quit()
