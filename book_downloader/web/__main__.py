"""One-click launcher and a singleton loopback-only server."""

import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"
APP_ID = "book-downloader-web"


def probe(root=None):
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"{URL}/api/health", timeout=1) as response:
            data = json.load(response)
        return (data.get("app") == APP_ID and data.get("version") == 1
                and not data.get("stopping") and (root is None or data.get("root") == str(root)))
    except (OSError, URLError, ValueError):
        return False


def port_busy():
    with socket.socket() as client:
        return client.connect_ex((HOST, PORT)) == 0


def open_page():
    # WSL's xdg-open need not be associated with the Windows default browser.
    if os.environ.get("WSL_DISTRO_NAME"):
        powershell = shutil.which("powershell.exe")
        if powershell:
            subprocess.Popen([powershell, "-NoProfile", "-NonInteractive", "-Command", f"Start-Process '{URL}'"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open(URL)


def serve(root):
    import uvicorn
    from .app import create_app
    state = root / ".local/web"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("后台已经在运行；请使用启动入口打开页面。") from None
        logging.basicConfig(filename=state / "server.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        app = create_app(root, on_shutdown=lambda: setattr(server, "should_exit", True))
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="info", access_log=False))
        server.run()


def launch(root, open_browser):
    if probe(root):
        if open_browser:
            open_page()
        print(f"本地页面：{URL}")
        return 0
    if port_busy():
        raise RuntimeError(f"端口 {PORT} 已被其他程序占用；请先释放端口，不会连接未知服务。")
    state = root / ".local/web"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "server.log").open("a") as log:
        child = subprocess.Popen([sys.executable, "-m", "book_downloader.web", "--serve", "--root", str(root)],
                                 cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if probe(root):
            if open_browser:
                open_page()
            print(f"本地页面：{URL}")
            return 0
        # Concurrent launchers may lose the lock while the winner is still starting.
        time.sleep(0.2)
    raise RuntimeError(f"后台启动超时；请查看 {state / 'server.log'}")


def main():
    parser = argparse.ArgumentParser(description="书页 · 本地 Web 下载界面")
    parser.add_argument("--open", action="store_true", help="启动后台并打开本地页面")
    parser.add_argument("--serve", action="store_true", help="在当前进程运行后台服务")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        import fastapi
        import uvicorn
        if args.serve:
            serve(args.root.resolve())
            return 0
        return launch(args.root.resolve(), args.open)
    except ImportError:
        print("Web 依赖缺失。请在项目中执行：uv pip install --python .venv/bin/python -e '.[web,browser]'", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as error:
        print(f"无法启动本地界面：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
