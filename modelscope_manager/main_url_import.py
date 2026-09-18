from __future__ import annotations

import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout,
)

from .http_security import safe_urlopen
from .service import normalize_remote_path


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def parse_http_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in str(text).splitlines():
        url = line.strip()
        if not url:
            continue
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"仅支持 http(s) 链接：{url}")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        raise ValueError("请至少输入一个 http(s) 链接")
    return urls


def url_download_filename(url: str, index: int, used: set[str]) -> str:
    raw = Path(unquote(urlsplit(url).path.rstrip("/"))).name
    name = _INVALID_FILENAME.sub("_", raw).strip().rstrip(". ")
    if not name:
        name = f"download-{index}"
    stem = Path(name).stem or "download"
    suffix = Path(name).suffix
    candidate = name
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def import_http_urls(urls, service, repo, target_folder: str) -> tuple[list[str], list[tuple[str, str]]]:
    uploaded: list[str] = []
    failures: list[tuple[str, str]] = []
    used: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="modelscope-url-import-") as directory:
        root = Path(directory)
        for index, url in enumerate(urls, 1):
            filename = url_download_filename(url, index, used)
            local_path = root / filename
            try:
                request = Request(url, headers={"User-Agent": "ModelScope-Manager/1.0"})
                with safe_urlopen(request, timeout=60) as response, local_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                remote_path = normalize_remote_path(target_folder, filename)
                service.upload_file_as(repo, local_path, remote_path)
            except Exception as exc:
                failures.append((url, str(exc)))
                local_path.unlink(missing_ok=True)
            else:
                uploaded.append(remote_path)
    return uploaded, failures


class UrlImportMixin:
    """Download HTTP(S) resources to a temporary directory and upload them to the current repository."""

    def show_url_import_dialog(self) -> None:
        if not self.selected_repo or self.selected_repo_public:
            QMessageBox.information(
                self, self._t("需要可写仓库"),
                self._t("请先在资源管理页打开一个可写的 ModelScope 仓库。"),
            )
            return
        service = self._token_service_for_repo(self.selected_repo)
        if service is None:
            QMessageBox.information(
                self, self._t("需要 Token 账户"),
                self._t("从链接导入需要可访问当前仓库的 Token 登录账户。"),
            )
            return
        if self.task and self.task.isRunning():
            QMessageBox.information(
                self, self._t("任务正在进行"),
                self._t("请等待当前上传、下载或仓库任务完成后再从链接导入。"),
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(self._t("从链接导入文件"))
        dialog.resize(620, 390)
        layout = QVBoxLayout(dialog)
        warning = QLabel(self._t("文件较大时谨慎使用此功能。"))
        warning.setObjectName("section")
        layout.addWidget(warning)
        hint = QLabel(self._t("一行一个链接，仅支持 http:// 或 https://。文件会先下载到本地临时目录，再上传到当前目录。"))
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setPlaceholderText("https://example.com/file1.zip\nhttps://example.com/file2.bin")
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self._t("开始导入"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            urls = parse_http_urls(editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, self._t("链接无效"), self._t(str(exc)))
            return

        repo = self.selected_repo
        target_folder = self.current_directory_path
        repo_identity = (repo.repo_type, repo.repo_id)

        def action():
            uploaded, failures = import_http_urls(urls, service, repo, target_folder)
            return uploaded, failures, repo_identity

        def success(result) -> None:
            uploaded, failures, original_repo = result
            for path in uploaded:
                self._log(f"链接导入完成：{path}")
            for url, error in failures:
                self._log(f"链接导入失败：{url} · {error}")
            current = self.selected_repo
            if (
                current
                and (current.repo_type, current.repo_id) == original_repo
                and uploaded
            ):
                self.load_remote_files()
            if failures:
                sample = "\n".join(f"{url}\n  {error}" for url, error in failures[:5])
                QMessageBox.warning(
                    self,
                    self._t("链接导入完成"),
                    self._tf(
                        "成功 {ok} 个，失败 {failed} 个。\n\n{details}",
                        ok=len(uploaded), failed=len(failures), details=sample,
                    ),
                )
            else:
                QMessageBox.information(
                    self,
                    self._t("链接导入完成"),
                    self._tf("已成功导入 {count} 个文件。", count=len(uploaded)),
                )

        self._run_task(action, success, f"正在从链接导入 {len(urls)} 个文件…")
