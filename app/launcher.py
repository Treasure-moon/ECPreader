#!/usr/bin/env python
"""真正的启动逻辑：本机 127.0.0.1 上启动服务，并打开独立桌面窗口。

`run.py` 只是入口薄壳，因此即使 run.py 被误清空，也可以用
`python -m app.launcher` 正常启动（start.bat 就是这么做的）。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import uvicorn

from app import config, desktop

BANNER = r"""
  ______ _____ _____    ____                 _
 |  ____/ ____|  __ \  |  _ \ ___  __ _  __| | ___ _ __
 | |__ | |    | |__) | | |_) / _ \/ _` |/ _` |/ _ \ '__|
 |  __|| |    |  ___/  |  _ <  __/ (_| | (_| |  __/ |
 |____| \____|_|       |_| \_\___|\__,_|\__,_|\___|_|

 论文双语对照阅读器 · 数据与凭据全部保存在本机
"""


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        if _port_available(host, port):
            return port
    raise SystemExit(f"端口 {preferred}-{preferred + 19} 都被占用，请用 --port 指定其他端口")


def _wait_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.12)
    return False


def _is_ecp_instance(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ECP-Reader"})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            head = response.read(4096).decode("utf-8", "ignore")
        return "ECPreader" in head and "ecp-token" in head
    except Exception:
        return False


def _setup_streams() -> None:
    """pythonw 启动时没有控制台，把输出写进日志，避免 print 崩溃。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    config.ensure_dirs()
    log = open(config.DATA_DIR / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        with open(config.DATA_DIR / "app.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _start_server(host: str, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="ecp-server", daemon=True)
    thread.start()
    if not _wait_port(host, port):
        raise SystemExit("服务启动失败：端口没有就绪")
    return server, thread


def _keep_alive(server: uvicorn.Server, thread: threading.Thread) -> None:
    try:
        while thread.is_alive():
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=4)


def main() -> None:
    _setup_streams()
    parser = argparse.ArgumentParser(description="ECPreader")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不打开窗口")
    parser.add_argument("--browser", action="store_true", help="用默认浏览器打开，而不是桌面窗口")
    parser.add_argument("--check", action="store_true", help="只做依赖自检，不启动服务")
    args = parser.parse_args()

    if args.check:
        from app import __version__
        from app.main import app  # noqa: F401

        print(f"ECPreader {__version__} · Python {sys.version.split()[0]} · 自检通过")
        return

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    # 已经有一个实例在跑：直接连上去，避免开第二份数据库。
    # 由「更改数据目录 → 立即重启」拉起的新实例要跳过这一步，否则会连到即将退出的旧实例。
    restarting = os.environ.get("ECP_RESTARTED") == "1"
    preferred_url = f"http://{args.host}:{args.port}/"
    if not restarting and not args.no_browser and not args.browser and _is_ecp_instance(preferred_url):
        print(f"  ECPreader 已在运行：{preferred_url}")
        _log(f"复用已运行实例 {preferred_url}")
        try:
            desktop.launch(preferred_url)
        except Exception:
            webbrowser.open(preferred_url)
        return

    port = _pick_port(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    server, thread = _start_server(args.host, port)
    _log(f"服务已启动 {url}")

    console = sys.stdout is not None and sys.stdout.isatty()
    if console:
        print(BANNER)
        print(f"  地址：{url}")
        print(f"  数据目录：{config.DATA_DIR}")
        print("  凭据已使用 Windows DPAPI + AES-256-GCM 加密，仅本机可解密")
        print("  关闭窗口即退出程序\n")

    if args.no_browser:
        try:
            _keep_alive(server, thread)
        except KeyboardInterrupt:
            pass
        return

    if args.browser or not desktop.available():
        webbrowser.open(url)
        print("  已在浏览器中打开；关闭本窗口或按 Ctrl+C 退出。")
        _keep_alive(server, thread)
        return

    try:
        mode = desktop.launch(url)
        _log(f"桌面窗口已打开（{mode}）")
    except Exception as exc:  # pragma: no cover - 依赖本机运行库
        _log(f"桌面窗口失败，回退浏览器：{exc}")
        print(f"  无法打开桌面窗口（{exc}），已改用浏览器。")
        webbrowser.open(url)
        _keep_alive(server, thread)
        return
    finally:
        server.should_exit = True
        thread.join(timeout=4)


if __name__ == "__main__":
    main()