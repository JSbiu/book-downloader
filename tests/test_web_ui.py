"""Opt-in local-browser acceptance test, with real APIs and offline source pages.

Run with BOOK_DOWNLOADER_UI_TESTS=1; no real book cache or website is used.
"""

import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

try:
    import uvicorn
except ImportError:
    raise unittest.SkipTest("需要安装可选 Web 测试依赖")

from book_downloader.browser import find_chrome_executable
from book_downloader.cache import cache_key
from book_downloader.errors import AccessBlockedError
from book_downloader.http import FetchedPage
from book_downloader.web.app import create_app
from book_downloader.web.tasks import TaskManager
from tests.test_web import BODIES, CATALOG, PAGES, URLS


@unittest.skipUnless(os.environ.get("BOOK_DOWNLOADER_UI_TESTS") == "1", "按需运行本地浏览器验收")
class WebBrowserTests(unittest.TestCase):
    def test_page_download_settings_partial_results_and_stop(self):
        from playwright.sync_api import sync_playwright, expect
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages, calls = dict(PAGES), []
            gate, started = Event(), Event()
            hold = [False]
            class Client:
                supports_concurrent_requests = False
                def __init__(self, *args):
                    pass
                def fetch(self, url, **kwargs):
                    calls.append(url)
                    if hold[0] and url == URLS[1]:
                        started.set()
                        if not gate.wait(10):
                            raise AssertionError("停止测试没有释放请求")
                    value = pages[url]
                    if isinstance(value, Exception):
                        raise value
                    return FetchedPage(url, value)
                def close(self):
                    pass
            manager = TaskManager(root, client_factory=Client)
            manager.update_settings({**manager.settings.model_dump(), "delay": 0, "retry_rounds": 0})
            app = create_app(manager=manager)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                address = f"http://127.0.0.1:{sock.getsockname()[1]}"
                server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
                thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
                with patch("requests.sessions.Session.request", side_effect=AssertionError("真实网络被禁用")):
                    thread.start()
                    try:
                        deadline = time.monotonic() + 5
                        while not server.started and time.monotonic() < deadline:
                            Event().wait(.01)
                        self.assertTrue(server.started)
                        with sync_playwright() as playwright:
                            browser_env = dict(os.environ)
                            # The UI opens in Windows by default. Headless WSL may have no CJK fonts;
                            # reference the existing Windows font without copying it into the repo.
                            windows_font = Path("/mnt/c/Windows/Fonts/msyh.ttc")
                            if windows_font.is_file():
                                fonts = root / "fonts"
                                fonts.mkdir()
                                (fonts / "msyh.ttc").symlink_to(windows_font)
                                config = root / "fonts.conf"
                                config.write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>{fonts}</dir><cachedir>{root / "font-cache"}</cachedir></fontconfig>')
                                browser_env["FONTCONFIG_FILE"] = str(config)
                            browser = playwright.chromium.launch(executable_path=str(find_chrome_executable()), headless=True, env=browser_env)
                            page = browser.new_page(viewport={"width": 1440, "height": 1000})
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            # Source fetches belong to the fixture client, and UI resources must stay local.
                            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(address) else route.abort())
                            page.goto(address)
                            expect(page.locator("#books-empty")).to_be_visible()
                            page.locator('.nav-item[data-view="add"]').click()
                            page.locator("#download-url").fill(CATALOG)
                            page.locator("#download-submit").click()
                            expect(page.locator("#task-detail")).to_contain_text("已完成", timeout=10000)
                            with page.expect_download() as download_info:
                                page.locator("#task-detail").get_by_role("link", name="下载TXT", exact=True).click()
                            download = download_info.value
                            download.save_as(root / "downloaded.txt")
                            self.assertTrue(all(body in (root / "downloaded.txt").read_text() for body in BODIES))
                            page.reload()
                            page.locator('.nav-item[data-view="tasks"]').click()
                            expect(page.locator("#tasks-body")).to_contain_text("已完成")
                            page.locator('.nav-item[data-view="settings"]').click()
                            page.locator("#advanced-settings summary").click()
                            page.locator("#set-timeout").fill("5")
                            page.wait_for_timeout(1200)
                            expect(page.locator("#set-timeout")).to_have_value("5")
                            page.locator("#settings-save").click()
                            expect(page.locator("#settings-status")).to_contain_text("设置已保存，将用于之后创建的任务。")
                            self.assertEqual(manager.settings.timeout, 5)
                            chapter = root / "cache/trxs_cc" / cache_key(CATALOG) / "chapters/000002.txt"
                            chapter.unlink()
                            pages[URLS[1]] = AccessBlockedError("HTTP 403；请求被拒绝")
                            page.locator('.nav-item[data-view="books"]').click()
                            page.locator("#refresh-books").click()
                            page.locator("#books-body").get_by_role("button", name="补齐缺口", exact=True).click()
                            expect(page.locator("#task-detail")).to_contain_text("部分完成", timeout=10000)
                            expect(page.locator("#task-detail")).to_contain_text("部分结果")
                            expect(page.locator("#task-detail")).to_contain_text("403")
                            expect(page.locator("#task-detail").get_by_role("button", name="通过浏览器重试")).to_be_visible()
                            expect(page.locator("#task-detail .pill").first).to_have_text("部分完成")
                            partial = next(t for t in manager.list_tasks() if t["kind"] == "fill_gaps" and t["status"] == "partial")
                            page.locator('.nav-item[data-view="settings"]').click()
                            page.locator("#set-timeout").fill("6")
                            page.locator("#settings-save").click()
                            expect(page.locator("#settings-status")).to_contain_text("设置已保存，将用于之后创建的任务。")
                            page.locator('.nav-item[data-view="tasks"]').click()
                            page.locator("#tasks-body tr").first.get_by_role("button", name="查看详情", exact=True).click()
                            page.locator("#task-detail").get_by_role("button", name="按当前设置重试", exact=True).click()
                            deadline = time.monotonic() + 10
                            while time.monotonic() < deadline:
                                retried = manager.list_tasks()[0]
                                if retried["id"] != partial["id"] and retried["status"] not in {"queued", "running", "needs_browser", "waiting_verification", "stopping"}:
                                    break
                                page.wait_for_timeout(100)
                            self.assertNotEqual(retried["id"], partial["id"])
                            self.assertEqual(manager.get(retried["id"])["status"], "partial")
                            self.assertEqual(retried["settings"]["timeout"], 6)
                            self.assertEqual(manager.get(partial["id"])["settings"]["timeout"], 5)
                            page.screenshot(path="/tmp/book-downloader-web-preview.png", full_page=True)
                            pages[URLS[1]] = PAGES[URLS[1]]
                            hold[0] = True
                            page.locator('.nav-item[data-view="books"]').click()
                            page.locator("#refresh-books").click()
                            page.locator("#books-body").get_by_role("button", name="补齐缺口", exact=True).click()
                            self.assertTrue(started.wait(3))
                            page.locator("#task-detail").get_by_role("button", name="停止任务", exact=True).click()
                            gate.set()
                            expect(page.locator("#task-detail")).to_contain_text("已取消", timeout=10000)
                            hold[0] = False
                            page.locator('.nav-item[data-view="books"]').click()
                            page.locator("#refresh-books").click()
                            output = manager.book_file(manager.books()[0]["id"])
                            cache_dir = chapter.parent.parent
                            page.locator("#books-body").get_by_role("button", name="删除", exact=True).click()
                            dialog = page.locator("#remove-book-dialog")
                            expect(dialog).to_be_visible()
                            expect(page.locator("#remove-book-files")).not_to_be_checked()
                            page.locator("#remove-book-cancel").click()
                            expect(dialog).not_to_be_visible()
                            expect(page.locator("#books-body tr")).to_have_count(1)
                            page.locator("#books-body").get_by_role("button", name="删除", exact=True).click()
                            page.locator("#remove-book-confirm").click()
                            expect(page.locator("#books-empty")).to_be_visible()
                            self.assertTrue(output.is_file())
                            self.assertTrue(cache_dir.is_dir())
                            page.reload()
                            expect(page.locator("#books-empty")).to_be_visible()
                            page.locator('.nav-item[data-view="add"]').click()
                            page.locator("#download-url").fill(CATALOG)
                            page.locator("#download-submit").click()
                            expect(page.locator("#task-detail")).to_contain_text("已完成", timeout=10000)
                            page.locator('.nav-item[data-view="books"]').click()
                            expect(page.locator("#books-body tr")).to_have_count(1)
                            page.locator("#books-body").get_by_role("button", name="删除", exact=True).click()
                            page.locator("#remove-book-files").check()
                            expect(page.locator("#remove-book-confirm")).to_have_text("永久删除文件并移除")
                            page.locator("#remove-book-confirm").click()
                            expect(page.locator("#books-empty")).to_be_visible()
                            self.assertFalse(output.exists())
                            self.assertFalse(output.with_suffix(".url").exists())
                            self.assertFalse(cache_dir.exists())
                            self.assertEqual(errors, [])
                            browser.close()
                    finally:
                        gate.set()
                        server.should_exit = True
                        thread.join(timeout=10)
                        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
