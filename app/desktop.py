"""桌面窗口封装：优先 pywebview（系统自带 WebView2），失败时回退 Edge 应用窗口。

目标：双击启动后就是一个独立窗口，没有浏览器地址栏，也没有黑色命令行窗口。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

WINDOW_TITLE = f"{config.APP_NAME} · 论文双语对照阅读器"


def available() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def run_window(url: str) -> None:
    """打开应用窗口并阻塞到窗口关闭；不返回则说明窗口无法创建。"""
    import webview

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1460,
        height=940,
        min_size=(1024, 660),
        background_color="#FAF6EA",
        # pywebview 默认 text_select=False，会注入 body{user-select:none}，
        # 导致逐句对照里根本划选不了文字；这里必须显式打开。
        text_select=True,
    )
    selftest = os.environ.get("ECP_DESKTOP_SELFTEST", "")
    if selftest:
        def _close_after() -> None:
            time.sleep(float(selftest))
            try:
                window.destroy()
            except Exception:
                pass

        threading.Thread(target=_close_after, daemon=True).start()
    icon_path = config.WEB_DIR / "assets" / "img" / "logo.ico"
    webview.start(
        debug=os.environ.get("ECP_DESKTOP_DEBUG") == "1",
        private_mode=False,
        storage_path=str(config.DATA_DIR / "webview"),
        icon=str(icon_path) if icon_path.is_file() else None,
    )


def _edge_executable() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for item in candidates:
        if item.is_file():
            return item
    return None


def run_edge_app(url: str) -> bool:
    """没有 WebView2 时，用 Edge 的「应用模式」开一个无地址栏窗口。"""
    edge = _edge_executable()
    if not edge:
        return False
    profile = config.DATA_DIR / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            [
                str(edge),
                f"--app={url}",
                "--window-size=1460,940",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            close_fds=True,
        )
    except OSError:
        return False
    try:
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
    return True


def launch(url: str) -> str:
    """打开桌面窗口，返回实际使用的模式：webview / edge。"""
    if available():
        try:
            run_window(url)
            return "webview"
        except Exception as exc:  # pragma: no cover - 依赖本机运行库
            print(f"[desktop] pywebview 启动失败，改用 Edge 应用窗口：{exc}")
    if run_edge_app(url):
        return "edge"
    raise RuntimeError("无法创建桌面窗口（缺少 WebView2 与 Edge）")