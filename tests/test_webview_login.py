from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from http.cookies import SimpleCookie
from pathlib import Path

from modelscope_manager.webview_login import _write_result, extract_modelscope_cookies, main


class WebViewLoginTests(unittest.TestCase):
    def test_extracts_only_required_modelscope_cookies(self) -> None:
        wanted = SimpleCookie()
        wanted["m_session_id"] = "session"
        wanted["m_session_id"]["domain"] = ".modelscope.cn"
        csrf = SimpleCookie()
        csrf["csrf_session"] = "csrf-session"
        csrf["csrf_session"]["domain"] = "www.modelscope.cn"
        ignored = SimpleCookie()
        ignored["csrf_token"] = "attacker"
        ignored["csrf_token"]["domain"] = "example.com"

        self.assertEqual(
            extract_modelscope_cookies([wanted, csrf, ignored]),
            {"m_session_id": "session", "csrf_session": "csrf-session"},
        )

    def test_result_write_is_utf8_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            _write_result(output, {"error": "登录取消"})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"error": "登录取消"})

    def test_webview_private_profile_lives_until_window_closes(self) -> None:
        captured = {}

        def create_window(*_args, **_kwargs):
            return object()

        def start(_action, _action_args, **kwargs):
            captured.update(kwargs)
            captured["exists_during_start"] = Path(kwargs["storage_path"]).is_dir()

        fake_webview = SimpleNamespace(create_window=create_window, start=start)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.dict(sys.modules, {"webview": fake_webview}):
                self.assertEqual(main(["--output", str(output), "--smoke-test"]), 0)

        self.assertTrue(captured["private_mode"])
        self.assertTrue(captured["exists_during_start"])
        self.assertFalse(Path(captured["storage_path"]).exists())


if __name__ == "__main__":
    unittest.main()
