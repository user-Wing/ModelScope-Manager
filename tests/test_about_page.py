from __future__ import annotations

import inspect
import unittest

from modelscope_manager.page_about import AboutPageMixin
from modelscope_manager.page_shell import PageShellMixin
from modelscope_manager.skin_theme import parse_page_assignments


class AboutAndSkinTests(unittest.TestCase):
    def test_about_markdown_contains_maintained_sections(self):
        markdown = AboutPageMixin()._about_markdown()
        self.assertIn("## 软件更新日志", markdown)
        self.assertIn("## 使用的开源库引用", markdown)
        self.assertIn("## 支持者列表", markdown)
        self.assertIn("当前暂无支持者", markdown)

    def test_skin_accepts_about_page_minus_one(self):
        self.assertEqual(
            parse_page_assignments("-1#75,0#90,6#110"),
            {-1: 75, 0: 90, 6: 110},
        )

    def test_about_navigation_is_bottom_and_before_settings(self):
        source = inspect.getsource(PageShellMixin._build_ui)
        about = source.index('(about_page, "aboutInterface"')
        settings = source.index('(settings_page, "settingsInterface"')
        self.assertLess(about, settings)
        self.assertIn("about_page: -1", source)


if __name__ == "__main__":
    unittest.main()
