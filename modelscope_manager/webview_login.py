from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Iterable


LOGIN_URL = "https://www.modelscope.cn/login"
SESSION_URL = "https://www.modelscope.cn/datasets/ARXChem/Animations-List/tree/master/Violet%20Evergarden"
REQUIRED_COOKIES = ("m_session_id", "csrf_session", "csrf_token")


def extract_modelscope_cookies(cookie_jars: Iterable[object]) -> dict[str, str]:
    values: dict[str, str] = {}
    for jar in cookie_jars:
        items = getattr(jar, "items", None)
        if not callable(items):
            continue
        for name, morsel in items():
            domain = str(morsel.get("domain", "")).lstrip(".").lower()
            if name in REQUIRED_COOKIES and (not domain or domain == "modelscope.cn" or domain.endswith(".modelscope.cn")):
                values[name] = str(getattr(morsel, "value", ""))
    return values


def _write_result(output_path: Path, payload: dict[str, object]) -> None:
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, output_path)


def _capture_session(window, output_path: Path, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    opened_session_page = False
    try:
        while time.monotonic() < deadline:
            cookies = extract_modelscope_cookies(window.get_cookies())
            missing = [name for name in REQUIRED_COOKIES if not cookies.get(name)]
            if not missing:
                _write_result(output_path, {"cookies": cookies})
                window.clear_cookies()
                window.destroy()
                return
            if cookies.get("m_session_id") and not opened_session_page:
                opened_session_page = True
                window.set_title("ModelScope 登录成功，正在取得会话凭据…")
                window.load_url(SESSION_URL)
            time.sleep(0.75)
        _write_result(output_path, {"error": "网页登录超时，请重试。"})
        window.destroy()
    except Exception as exc:
        _write_result(output_path, {"error": f"WebView2 登录失败：{exc}"})
        try:
            window.destroy()
        except Exception:
            pass


def _smoke_test(window, output_path: Path) -> None:
    try:
        window.get_current_url()
        window.get_cookies()
        _write_result(output_path, {"renderer": "edgechromium", "ok": True})
    except Exception as exc:
        _write_result(output_path, {"error": f"WebView2 自检失败：{exc}"})
    finally:
        window.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ModelScope Edge WebView2 login helper")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account", default="ModelScope")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)

    try:
        import webview

        title = "ModelScope WebView2 自检" if args.smoke_test else f"ModelScope 在线登录 · {args.account}"
        window = webview.create_window(
            title,
            LOGIN_URL,
            width=1080,
            height=760,
            min_size=(820, 600),
            text_select=True,
        )
        action = _smoke_test if args.smoke_test else _capture_session
        action_args = (window, args.output) if args.smoke_test else (window, args.output, args.timeout)
        profile = tempfile.TemporaryDirectory(prefix="modelscope-webview2-", ignore_cleanup_errors=True)
        try:
            webview.start(
                action,
                action_args,
                gui="edgechromium",
                private_mode=True,
                storage_path=profile.name,
            )
        finally:
            profile.cleanup()
        if not args.output.exists():
            _write_result(args.output, {"error": "登录窗口已关闭，未保存会话。"})
    except Exception as exc:
        _write_result(args.output, {"error": f"无法启动 Edge WebView2：{exc}"})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
