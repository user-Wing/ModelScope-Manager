"""About page with Markdown-rendered project information."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

from . import __version__
from .storage import APP_DIR


def _read_project_markdown(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return fallback


class AboutPageMixin:
    """Build the About page from the project's maintained Markdown sources."""

    def _about_markdown(self) -> str:
        changelog = _read_project_markdown(
            APP_DIR / "CHANGELOG.md",
            "更新日志文件暂时不可用。",
        ).strip()
        notices = _read_project_markdown(
            APP_DIR / "THIRD_PARTY_NOTICES.md",
            "第三方开源库引用文件暂时不可用。",
        ).strip()
        return (
            f"# ModelScope Manager\n\n"
            f"当前版本：**{__version__}**\n\n"
            "## 软件更新日志\n\n"
            f"{changelog}\n\n"
            "## 使用的开源库引用\n\n"
            f"{notices}\n\n"
            "## 支持者列表\n\n"
            "当前暂无支持者。\n"
        )

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        self.about_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(QLabel("关于", objectName="title"))
        browser = QTextBrowser()
        browser.setObjectName("aboutMarkdown")
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(self._about_markdown())
        self.about_browser = browser
        layout.addWidget(browser, 1)
        return page
