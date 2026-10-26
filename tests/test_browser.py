import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

from book_downloader.browser import BrowserHttpClient, _start_default_browser, find_chrome_executable
from book_downloader.errors import ConfigurationError, NetworkError


class BrowserExecutableTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executable = self.root / "browser"
        self.executable.touch()
        self.which = self.enterContext(
            patch("book_downloader.browser.which", return_value=None)
        )
        self.enterContext(patch.dict("book_downloader.browser.os.environ", {}, clear=True))
        self.home = self.enterContext(
            patch("book_downloader.browser.Path.home", return_value=self.root)
        )
        self.bundled = self.root / "playwright-chromium"
        self.bundled.touch()
        self.playwright = SimpleNamespace(
            chromium=SimpleNamespace(executable_path=str(self.bundled))
        )

    def create_cached_chromium(self, layout="chrome-linux64"):
        executable = self.root / ".cache/ms-playwright/chromium-1243" / layout / "chrome"
        executable.parent.mkdir(parents=True)
        executable.touch()
        return executable

    def test_explicit_path_takes_priority_over_path_search(self):
        self.which.return_value = str(self.root / "other-browser")

        self.create_cached_chromium()
        self.assertEqual(
            find_chrome_executable(self.executable, playwright=self.playwright),
            self.executable.resolve(),
        )
        self.which.assert_not_called()

    def test_invalid_explicit_path_does_not_fall_back(self):
        self.which.return_value = str(self.executable)

        for invalid in (self.root / "missing", self.root):
            with self.subTest(path=invalid):
                with self.assertRaisesRegex(ConfigurationError, "浏览器可执行文件不存在"):
                    find_chrome_executable(invalid, playwright=self.playwright)
        self.which.assert_not_called()

    def test_finds_common_browser_commands_on_path(self):
        self.create_cached_chromium()
        windows = self.root / "Google/Chrome/Application/chrome.exe"
        windows.parent.mkdir(parents=True)
        windows.touch()
        self.enterContext(
            patch.dict("book_downloader.browser.os.environ", {"PROGRAMFILES": str(self.root)})
        )
        for command in (
            "chrome",
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
        ):
            with self.subTest(command=command):
                self.which.side_effect = {command: str(self.executable)}.get
                self.assertEqual(
                    find_chrome_executable(playwright=self.playwright),
                    self.executable.resolve(),
                )

    def test_chrome_on_path_keeps_priority(self):
        self.which.side_effect = lambda command: str(self.root / command)

        self.assertEqual(find_chrome_executable(), (self.root / "chrome").resolve())

    def test_windows_installation_paths_remain_available(self):
        self.create_cached_chromium()
        for index, (variable, suffix) in enumerate((
            ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
            ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
            ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
            ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
            ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
        )):
            with self.subTest(variable=variable, suffix=suffix):
                installation = self.root / str(index)
                executable = installation / suffix
                executable.parent.mkdir(parents=True)
                executable.touch()
                with patch.dict("book_downloader.browser.os.environ", {variable: str(installation)}):
                    self.assertEqual(
                        find_chrome_executable(playwright=self.playwright),
                        executable.resolve(),
                    )

    def test_missing_browser_suggests_explicit_path(self):
        with self.assertRaisesRegex(ConfigurationError, "--browser-executable"):
            find_chrome_executable()

    def test_finds_cached_chromium_without_a_playwright_runtime(self):
        cached = self.create_cached_chromium()

        self.assertEqual(find_chrome_executable(), cached.resolve())

    def test_finds_older_linux_cache_layout(self):
        cached = self.create_cached_chromium("chrome-linux")

        self.assertEqual(find_chrome_executable(), cached.resolve())

    def test_playwright_executable_precedes_cached_chromium(self):
        self.create_cached_chromium()

        self.assertEqual(
            find_chrome_executable(playwright=self.playwright), self.bundled.resolve()
        )
        self.home.assert_not_called()

    def test_unavailable_playwright_falls_back_to_cache_or_configuration_error(self):
        cached = self.create_cached_chromium()
        chromium = Mock()
        type(chromium).executable_path = PropertyMock(side_effect=RuntimeError("probe failed"))
        providers = (
            SimpleNamespace(chromium=chromium),
            object(),
            SimpleNamespace(chromium=SimpleNamespace(executable_path=None)),
            SimpleNamespace(chromium=SimpleNamespace(executable_path=str(self.root / "missing"))),
            SimpleNamespace(chromium=SimpleNamespace(executable_path=str(self.root))),
        )
        for provider in providers:
            with self.subTest(provider=provider, cached=True):
                self.assertEqual(
                    find_chrome_executable(playwright=provider), cached.resolve()
                )
        cached.unlink()
        for provider in providers:
            with self.subTest(provider=provider, cached=False):
                with self.assertRaisesRegex(ConfigurationError, "--browser-executable"):
                    find_chrome_executable(playwright=provider)

    def test_cache_probe_failure_keeps_original_configuration_error(self):
        for target, error in (
            ("book_downloader.browser.Path.home", RuntimeError("home unavailable")),
            ("book_downloader.browser.Path.glob", OSError("cache unavailable")),
        ):
            with self.subTest(target=target), patch(target, side_effect=error):
                with self.assertRaisesRegex(ConfigurationError, "--browser-executable"):
                    find_chrome_executable()

    def test_browser_mode_reuses_started_playwright_for_discovery(self):
        chromium = Mock(executable_path=str(self.bundled))
        runtime = SimpleNamespace(chromium=chromium, stop=Mock())
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = RuntimeError
        sync_api.TimeoutError = TimeoutError
        sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: runtime)
        errors = ModuleType("playwright._impl._errors")
        errors.TargetClosedError = RuntimeError
        self.enterContext(patch.dict("sys.modules", {
            "playwright": ModuleType("playwright"),
            "playwright.sync_api": sync_api,
            "playwright._impl": ModuleType("playwright._impl"),
            "playwright._impl._errors": errors,
        }))

        client = BrowserHttpClient(timeout=1, profile_dir=self.root / "profile")
        self.addCleanup(client.close)

        self.assertEqual(
            chromium.launch_persistent_context.call_args.kwargs["executable_path"],
            str(self.bundled.resolve()),
        )

    def test_auto_connect_reuses_started_playwright_for_discovery(self):
        client = BrowserHttpClient.__new__(BrowserHttpClient)
        client._playwright = self.playwright
        profile = self.root / "profile"
        with (
            patch("book_downloader.browser._probe_cdp", side_effect=[False, False, False, True]),
            patch("book_downloader.browser._start_default_browser") as start,
        ):
            url = client._resolve_auto_cdp_url(profile, None)

        self.assertEqual(url, "http://127.0.0.1:9222")
        start.assert_called_once_with(9222, profile.resolve(), self.bundled.resolve())


class _FakeTargetClosedError(RuntimeError):
    pass


def _install_fake_playwright(test, chromium):
    """让 BrowserHttpClient 导入到基于给定 chromium 的假 Playwright。"""
    runtime = SimpleNamespace(chromium=chromium, stop=Mock())
    sync_api = ModuleType("playwright.sync_api")
    sync_api.Error = RuntimeError
    sync_api.TimeoutError = TimeoutError
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: runtime)
    errors = ModuleType("playwright._impl._errors")
    errors.TargetClosedError = _FakeTargetClosedError
    test.enterContext(patch.dict("sys.modules", {
        "playwright": ModuleType("playwright"),
        "playwright.sync_api": sync_api,
        "playwright._impl": ModuleType("playwright._impl"),
        "playwright._impl._errors": errors,
    }))


class _FakePage:
    def __init__(self, url):
        self.url = url
        self.closed = False
        self.visited = []

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def goto(self, url):
        if self.url != "about:blank" and not self.url.startswith(("http://", "https://")):
            raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
        self.url = url
        self.visited.append(url)
        return url


class BrowserPageTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(
            pages=[], is_closed=Mock(return_value=False), new_page=Mock(side_effect=self.new_page)
        )
        self.chromium = Mock()
        self.chromium.connect_over_cdp.return_value = SimpleNamespace(contexts=[self.context])
        runtime = SimpleNamespace(chromium=self.chromium, stop=Mock())
        sync_api = ModuleType("playwright.sync_api")
        sync_api.Error = RuntimeError
        sync_api.TimeoutError = TimeoutError
        sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: runtime)
        errors = ModuleType("playwright._impl._errors")
        errors.TargetClosedError = _FakeTargetClosedError
        self.enterContext(patch.dict("sys.modules", {
            "playwright": ModuleType("playwright"),
            "playwright.sync_api": sync_api,
            "playwright._impl": ModuleType("playwright._impl"),
            "playwright._impl._errors": errors,
        }))
        self.target = "https://example.test/catalog"
        self.adapter = SimpleNamespace(
            name="offline",
            search_via_page=lambda page, *args, **kwargs: page.goto(self.target),
        )

    def new_page(self):
        page = _FakePage("about:blank")
        self.context.pages.append(page)
        return page

    def connect(self, pages):
        self.context.pages = list(pages)
        self.context.new_page.reset_mock()
        client = BrowserHttpClient(timeout=1, cdp_url="http://127.0.0.1:9333")
        self.addCleanup(client.close)
        return client

    def persistent(self, pages):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        executable = root / "chrome"
        executable.touch()
        self.context.pages = list(pages)
        self.context.is_closed.return_value = False
        self.context.close = Mock()
        self.context.new_page.reset_mock()
        self.chromium.launch_persistent_context.reset_mock()
        self.chromium.launch_persistent_context.return_value = self.context
        client = BrowserHttpClient(
            timeout=1, profile_dir=root / "profile", executable_path=executable
        )
        self.addCleanup(client.close)
        return client

    def test_webui_start_page_is_not_used_for_search(self):
        for url in (
            "chrome://intro/", "chrome-extension://example/start.html", "about:blank",
            "about:newtab", "edge://newtab/", "file:///tmp/start.html",
            "devtools://devtools/bundled/inspector.html",
        ):
            with self.subTest(url=url):
                start = _FakePage(url)
                client = self.connect([start])

                self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
                self.assertIsNot(client._page, start)
                self.context.new_page.assert_called_once_with()
                self.assertEqual(start.visited, [])

    def test_existing_http_page_is_preferred_to_start_pages(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                start = _FakePage("chrome://intro/")
                closed = _FakePage("https://example.test/closed")
                closed.close()
                page = _FakePage(f"{scheme}://example.test/ready")
                other = _FakePage("https://example.test/other")
                client = self.connect([start, closed, page, other])

                self.assertIs(client._page, page)
                self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
                self.context.new_page.assert_not_called()
                self.chromium.connect_over_cdp.assert_called_with("http://127.0.0.1:9333")
                self.chromium.launch.assert_not_called()
                self.chromium.launch_persistent_context.assert_not_called()

    def test_closed_page_rebuild_skips_webui_and_reuses_http(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        original.close()
        page = _FakePage("https://example.test/ready")
        self.context.pages.extend([_FakePage("chrome://intro/"), page])

        client._ensure_page()

        self.assertIs(client._page, page)
        self.context.new_page.assert_not_called()

    def test_closed_page_rebuild_creates_and_reuses_a_blank_navigation_page(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        original.close()
        self.context.pages.append(_FakePage("chrome://intro/"))

        client._ensure_page()
        created = client._page
        client._ensure_page()

        self.assertIs(client._page, created)
        self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
        self.context.new_page.assert_called_once_with()

    def test_live_operation_page_changed_to_webui_is_replaced(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        original.url = "chrome://intro/"

        self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
        self.assertIsNot(client._page, original)
        self.context.new_page.assert_called_once_with()

    def test_target_closed_during_search_recovers_using_safe_page(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        calls = []

        def search(page, *args, **kwargs):
            calls.append(page)
            if len(calls) == 1:
                page.close()
                self.context.pages.append(_FakePage("chrome://intro/"))
                raise _FakeTargetClosedError("tab closed")
            return page.goto(self.target)

        adapter = SimpleNamespace(name="offline", search_via_page=search)
        self.assertEqual(client.page_search(adapter, "示例", 1), self.target)
        self.assertEqual(calls, [original, client._page])
        self.context.new_page.assert_called_once_with()

    def test_navigation_error_during_search_becomes_network_error(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])

        def search(page, *args, **kwargs):
            del page, args, kwargs
            raise RuntimeError("net::ERR_ABORTED")

        adapter = SimpleNamespace(name="offline", search_via_page=search)
        with self.assertRaisesRegex(NetworkError, "net::ERR_ABORTED"):
            client.page_search(adapter, "示例", 1)

    def test_disconnected_browser_keeps_connection_error(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        original.close()
        self.context.is_closed.return_value = True

        with self.assertRaisesRegex(NetworkError, "浏览器连接已失效"):
            client._ensure_page()

    def test_persistent_context_recreates_closed_operation_page(self):
        client = self.persistent([_FakePage("chrome://intro/")])
        self.assertTrue(client._owns_context)
        self.assertIsNone(client._browser)
        client._page.close()
        self.context.new_page.reset_mock()

        self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
        client._ensure_page()

        self.context.new_page.assert_called_once_with()
        self.assertEqual(self.chromium.launch_persistent_context.call_count, 1)
        self.chromium.connect_over_cdp.assert_not_called()

    def test_persistent_context_prefers_existing_http_page_after_tab_closes(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                page = _FakePage(f"{scheme}://example.test/ready")
                client = self.persistent([_FakePage("chrome://intro/"), page])
                client._page.close()
                self.context.new_page.reset_mock()

                self.assertEqual(client.page_search(self.adapter, "示例", 1), self.target)
                self.assertIs(client._page, page)
                self.context.new_page.assert_not_called()
                self.assertEqual(self.chromium.launch_persistent_context.call_count, 1)

    def test_closed_or_missing_persistent_context_reports_closed_window(self):
        for state in ("closed", "missing"):
            with self.subTest(state=state):
                client = self.persistent([])
                self.context.new_page.reset_mock()
                if state == "closed":
                    self.context.is_closed.return_value = True
                else:
                    client._context = None

                with self.assertRaisesRegex(NetworkError, "浏览器窗口已关闭"):
                    client.page_search(self.adapter, "示例", 1)
                self.context.new_page.assert_not_called()
                self.assertEqual(self.chromium.launch_persistent_context.call_count, 1)

    def test_cdp_without_browser_contexts_does_not_reuse_stale_context(self):
        original = _FakePage("https://example.test/original")
        client = self.connect([original])
        original.close()
        client._browser.contexts = []

        with self.assertRaisesRegex(NetworkError, "浏览器连接已失效"):
            client._ensure_page()
        self.context.new_page.assert_not_called()
        self.chromium.launch.assert_not_called()
        self.chromium.launch_persistent_context.assert_not_called()

    def test_closed_cdp_context_is_not_reused_even_if_page_still_appears_open(self):
        client = self.connect([_FakePage("https://example.test/original")])
        self.context.is_closed.return_value = True

        with self.assertRaisesRegex(NetworkError, "浏览器连接已失效"):
            client._ensure_page()
        self.context.new_page.assert_not_called()
        self.chromium.launch_persistent_context.assert_not_called()


class BrowserStartupTests(unittest.TestCase):
    def test_first_run_flags_preserve_executable_profile_and_port(self):
        executable = Path("/tmp/browser with spaces/chrome")
        profile = Path("/tmp/profile with spaces")
        with (
            patch.dict("book_downloader.browser.os.environ", {}, clear=True),
            patch("book_downloader.browser.subprocess.Popen") as popen,
        ):
            _start_default_browser(9444, profile, executable)

        popen.assert_called_once_with(
            [
                str(executable),
                "--remote-debugging-port=9444",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class WslFontConfigTests(unittest.TestCase):
    """WSL 中文渲染修复：只影响新启动的浏览器，且不碰真实机器字体。"""

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile = self.root / "profile"
        self.executable = self.root / "chrome"
        self.executable.touch()
        self.fonts = self.root / "Windows/Fonts"
        self.fonts.mkdir(parents=True)
        self.font = self.fonts / "msyh.ttc"
        self.font.write_bytes(b"fake chinese font")
        self.enterContext(
            patch("book_downloader.browser.WINDOWS_FONTS_DIR", self.fonts)
        )
        self.enterContext(
            patch.dict(
                "book_downloader.browser.os.environ",
                {"WSL_DISTRO_NAME": "Ubuntu-22.04", "HOME": str(self.root)},
                clear=True,
            )
        )
        self.config_dir = self.profile / "fontconfig"
        self.config = self.config_dir / "fonts.conf"
        self.link = self.config_dir / "fonts/msyh.ttc"

    def start(self):
        with patch("book_downloader.browser.subprocess.Popen") as popen:
            _start_default_browser(9444, self.profile, self.executable)
        return popen.call_args

    def launch(self):
        context = SimpleNamespace(
            pages=[],
            is_closed=Mock(return_value=False),
            new_page=Mock(side_effect=lambda: _FakePage("about:blank")),
            close=Mock(),
        )
        chromium = Mock()
        chromium.launch_persistent_context.return_value = context
        _install_fake_playwright(self, chromium)
        client = BrowserHttpClient(
            timeout=1, profile_dir=self.profile, executable_path=self.executable
        )
        self.addCleanup(client.close)
        return chromium

    def test_wsl_start_passes_profile_local_fontconfig(self):
        call = self.start()

        self.assertEqual(call.args[0][0], str(self.executable))
        self.assertEqual(call.kwargs["env"]["FONTCONFIG_FILE"], str(self.config))
        self.assertEqual(call.kwargs["env"]["WSL_DISTRO_NAME"], "Ubuntu-22.04")
        text = self.config.read_text(encoding="utf-8")
        self.assertIn("/etc/fonts/fonts.conf", text)
        self.assertIn(str(self.link.parent), text)
        self.assertIn(str(self.config_dir / "cache"), text)

    def test_windows_font_is_referenced_by_link_instead_of_copied(self):
        self.start()

        self.assertTrue(self.link.is_symlink())
        self.assertEqual(self.link.resolve(), self.font.resolve())
        self.assertEqual(self.font.read_bytes(), b"fake chinese font")
        self.assertEqual([item.name for item in self.link.parent.iterdir()], ["msyh.ttc"])

    def test_wsl_persistent_context_gets_profile_local_fontconfig(self):
        chromium = self.launch()

        kwargs = chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(kwargs["user_data_dir"], str(self.profile))
        self.assertEqual(kwargs["env"]["FONTCONFIG_FILE"], str(self.config))
        self.assertTrue(self.config.is_file())

    def test_repeated_start_reuses_the_same_link(self):
        self.start()
        resolved = self.link.resolve()

        call = self.start()

        self.assertEqual(call.kwargs["env"]["FONTCONFIG_FILE"], str(self.config))
        self.assertEqual(self.link.resolve(), resolved)
        self.assertEqual([item.name for item in self.link.parent.iterdir()], ["msyh.ttc"])

    def test_outside_wsl_keeps_start_unchanged(self):
        with patch.dict("book_downloader.browser.os.environ", {}, clear=True):
            call = self.start()

        self.assertNotIn("env", call.kwargs)
        self.assertFalse(self.config_dir.exists())

    def test_outside_wsl_keeps_persistent_launch_unchanged(self):
        with patch.dict("book_downloader.browser.os.environ", {}, clear=True):
            chromium = self.launch()

        self.assertNotIn("env", chromium.launch_persistent_context.call_args.kwargs)
        self.assertFalse(self.config_dir.exists())

    def test_missing_windows_font_keeps_start_unchanged(self):
        empty = self.root / "empty-fonts"
        empty.mkdir()
        with patch("book_downloader.browser.WINDOWS_FONTS_DIR", empty):
            call = self.start()

        self.assertNotIn("env", call.kwargs)
        self.assertFalse(self.config_dir.exists())

    def test_existing_fontconfig_file_is_left_to_the_user(self):
        with patch.dict(
            "book_downloader.browser.os.environ",
            {"FONTCONFIG_FILE": str(self.root / "custom-fonts.conf")},
        ):
            call = self.start()

        self.assertNotIn("env", call.kwargs)
        self.assertFalse(self.config_dir.exists())

    def test_cdp_reuse_does_not_touch_fontconfig_or_restart(self):
        context = SimpleNamespace(
            pages=[_FakePage("https://example.test/ready")],
            is_closed=Mock(return_value=False),
            new_page=Mock(),
            close=Mock(),
        )
        browser = SimpleNamespace(contexts=[context], close=Mock())
        chromium = Mock()
        chromium.connect_over_cdp.return_value = browser
        _install_fake_playwright(self, chromium)

        client = BrowserHttpClient(
            timeout=1, cdp_url="http://127.0.0.1:9333", profile_dir=self.profile
        )
        self.addCleanup(client.close)
        client.close()

        chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:9333")
        chromium.launch_persistent_context.assert_not_called()
        context.close.assert_not_called()
        browser.close.assert_not_called()
        self.assertFalse(self.profile.exists())


if __name__ == "__main__":
    unittest.main()
