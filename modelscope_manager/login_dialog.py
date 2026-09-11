"""Edge WebView2 网页登录进程的 Qt 控制对话框。"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from PySide6.QtCore import QProcess, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from pathlib import Path
from typing import Callable
from .web_session import ModelScopeWebSession, fetch_web_user_info


class ModelScopeLoginDialog(QDialog):
    session_captured = Signal(object, object)

    def __init__(
        self, account_label: str, translator: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._translate = translator or (lambda source: source)
        self.setWindowTitle(self._tf("ModelScope 在线登录 · {account}", account=account_label))
        self.resize(560, 230)
        self.setMinimumSize(520, 210)
        self._account_label = account_label
        self._result_path = Path(tempfile.gettempdir()) / f"modelscope-webview2-{uuid.uuid4().hex}.json"
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel(self._t("Edge WebView2 在线登录"), objectName="title"))
        self.status_label = QLabel(
            self._t("即将打开由系统 Microsoft Edge WebView2 提供的独立窗口。请在 ModelScope 官方页面完成登录；取得会话后窗口会自动关闭。"),
            objectName="subtitle",
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel_button = QPushButton(self._t("取消"))
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(cancel_button)
        retry_button = QPushButton(self._t("重新打开登录窗口"), objectName="primary")
        retry_button.clicked.connect(self._start_webview)
        actions.addWidget(retry_button)
        layout.addLayout(actions)
        QTimer.singleShot(0, self._start_webview)

    def _t(self, source: str) -> str:
        return self._translate(source)

    def _tf(self, source: str, **values) -> str:
        return self._t(source).format(**values)

    @staticmethod
    def _cookie_text(value) -> str:
        if isinstance(value, str):
            return value
        return bytes(value).decode("utf-8", errors="ignore")

    @classmethod
    def _cookie_parts(cls, cookie) -> tuple[str, str, str]:
        return (
            cls._cookie_text(cookie.name()),
            cls._cookie_text(cookie.value()),
            cls._cookie_text(cookie.domain()).lstrip("."),
        )

    def _start_webview(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            return
        self._result_path.unlink(missing_ok=True)
        self.status_label.setText(self._t("正在启动 Edge WebView2 登录窗口…"))
        self._process.start(
            sys.executable,
            ["-m", "modelscope_manager.webview_login", "--output", str(self._result_path), "--account", self._account_label],
        )

    def _process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.status_label.setText(self._t("无法启动 Edge WebView2 登录进程。"))

    def _process_finished(self, _exit_code: int, _exit_status) -> None:
        try:
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            output = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
            result = {"error": output or self._t("登录窗口已关闭，未保存会话。")}
        cookies = result.get("cookies", {}) if isinstance(result, dict) else {}
        if not isinstance(cookies, dict) or not all(cookies.get(name) for name in ("m_session_id", "csrf_session", "csrf_token")):
            error = str(result.get("error", self._t("登录信息不完整，请重试。"))) if isinstance(result, dict) else self._t("登录信息不完整，请重试。")
            self.status_label.setText(error)
            return
        self._validate_and_save_session(cookies)

    def _validate_and_save_session(self, cookies: dict[str, str]) -> None:
        try:
            session = ModelScopeWebSession(
                cookies.get("m_session_id", ""),
                cookies.get("csrf_session", ""),
                cookies.get("csrf_token", ""),
            )
            self.status_label.setText(self._t("正在验证网页登录状态…"))
            QApplication.processEvents()
            user_info = fetch_web_user_info(session)
        except Exception as exc:
            self.status_label.setText(self._t("尚未取得有效登录状态，请完成登录后重试。"))
            QMessageBox.warning(self, self._t("在线登录验证失败"), str(exc))
            return
        self.session_captured.emit(session, user_info)
        self.accept()

    def done(self, result: int) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        self._result_path.unlink(missing_ok=True)
        super().done(result)
