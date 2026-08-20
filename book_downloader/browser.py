from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from shutil import which
from urllib.parse import urlsplit

from .errors import AccessBlockedError, ConfigurationError, NetworkError
from .http import FetchedPage, looks_like_verification_page


def _is_google_host(host: str) -> bool:
    labels = host.split(".")
    return (
        len(labels) in (2, 3)
        and labels[0] == "google"
        and all(label.isalpha() and 2 <= len(label) <= 3 for label in labels[1:])
    )


def same_target_document(actual_url: str, requested_url: str) -> bool:
    """Allow http/https and www redirects, but never reuse another page's body."""
    actual = urlsplit(actual_url)
    requested = urlsplit(requested_url)
    actual_host = actual.netloc.lower().removeprefix("www.")
    requested_host = requested.netloc.lower().removeprefix("www.")
    same_google_service = _is_google_host(actual_host) and _is_google_host(requested_host)
    return (
        (actual_host == requested_host or same_google_service)
        and actual.path.rstrip("/") == requested.path.rstrip("/")
    )


def find_chrome_executable(explicit: Path | None = None) -> Path:
    """Find a locally installed Chromium-based browser executable."""
    if explicit:
        path = explicit.expanduser().resolve()
        if path.is_file():
            return path
        raise ConfigurationError(f"浏览器可执行文件不存在：{path}")

    path_from_path = which("chrome")
    if path_from_path:
        return Path(path_from_path).resolve()

    candidates: list[Path] = []
    for variable, suffix in (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
    ):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / suffix)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ConfigurationError(
        "找不到 Chrome/Edge 浏览器；请安装 Chromium 浏览器，或通过 "
        "--browser-executable 指定可执行文件"
    )


class BrowserHttpClient:
    """Use a visible browser after the user manually completes verification."""

    # Playwright 页面导航和浏览器上下文不是为并发复用同一个页面设计的。
    supports_concurrent_requests = False

    def __init__(
        self,
        timeout: float,
        profile_dir: Path | None = None,
        executable_path: Path | None = None,
        verification_timeout: float = 180.0,
        cdp_url: str | None = None,
    ):
        try:
            from playwright.sync_api import (
                Error as PlaywrightError,
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )
        except ModuleNotFoundError as error:
            raise ConfigurationError(
                "浏览器模式需要 Playwright；请先执行：python -m pip install playwright"
            ) from error

        self.timeout = timeout
        self.verification_timeout = verification_timeout
        self._playwright_error = PlaywrightError
        self._playwright_timeout_error = PlaywrightTimeoutError
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        self._page = None
        self._owns_browser = False
        self._owns_context = False

        try:
            if cdp_url:
                self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
                if not self._browser.contexts:
                    raise ConfigurationError("已连接 Chrome，但没有可用浏览器上下文")
                self._context = self._browser.contexts[0]
                pages = [page for page in self._context.pages if not page.is_closed()]
                self._page = pages[0] if pages else self._context.new_page()
            else:
                browser_path = find_chrome_executable(executable_path)
                profile_path = (profile_dir or Path("cache/browser-profile")).expanduser().resolve()
                profile_path.mkdir(parents=True, exist_ok=True)
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    executable_path=str(browser_path),
                    headless=False,
                )
                self._page = self._context.new_page()
                self._owns_context = True
        except Exception as error:
            self.close()
            if isinstance(error, ConfigurationError):
                raise
            raise ConfigurationError(f"无法启动浏览器：{error}") from error

    def close(self) -> None:
        if self._owns_context and self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._owns_browser and self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if getattr(self, "_playwright", None) is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _wait_for_manual_verification(self, url: str) -> None:
        print(
            f"\n浏览器正在等待人工验证：{url}\n"
            "请在打开的浏览器窗口中完成验证；脚本会自动等待页面恢复，不需要按回车。"
        )
        deadline = time.monotonic() + self.verification_timeout
        stable_checks = 0
        while time.monotonic() < deadline:
            try:
                if self._target_page_is_readable(url):
                    stable_checks += 1
                    if stable_checks >= 2:
                        return
                else:
                    stable_checks = 0
                self._page.wait_for_timeout(500)
            except self._playwright_error:
                break
        raise AccessBlockedError(
            "浏览器页面仍是验证页，站点可能拒绝了当前浏览器环境；"
            "请确认验证已完成后重新运行，或延长 --verification-timeout"
        )

    def _visible_page_text(self) -> str:
        try:
            return self._page.locator("body").inner_text(timeout=1000)
        except self._playwright_error:
            return self._page.content()

    def _visible_page_is_verification(self) -> bool:
        return looks_like_verification_page(self._visible_page_text())

    def _google_page_needs_manual_action(self) -> bool:
        sample = self._page.content()[:30000].lower()
        markers = (
            "enablejs",
            "unusual traffic",
            "captcha",
            "consent",
            "before you continue",
            "/sorry/",
        )
        return any(marker in sample for marker in markers)

    def wait_for_google_results(self, url: str) -> FetchedPage:
        """Wait for Google JavaScript or a user-completed normal challenge."""
        manual_action = self._google_page_needs_manual_action()
        timeout = self.verification_timeout if manual_action else min(self.timeout, 5.0)
        if manual_action:
            print(
                "\nGoogle 搜索页尚未显示结果；请在打开的浏览器窗口中完成正常的"
                " JavaScript、同意或验证操作，脚本会继续等待。"
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not same_target_document(self._page.url or "", url):
                raise NetworkError(f"浏览器没有停留在 Google 搜索页：{url}")
            try:
                if self._page.locator("h3").count() > 0:
                    return FetchedPage(url=self._page.url or url, text=self._page.content())
            except self._playwright_error as error:
                raise NetworkError(f"读取 Google 搜索结果失败：{error}") from error
            self._page.wait_for_timeout(250)
        return FetchedPage(url=self._page.url or url, text=self._page.content())

    def _target_page_is_readable(self, url: str) -> bool:
        if not same_target_document(self._page.url or "", url):
            return False
        visible_text = self._visible_page_text().strip()
        return len(visible_text) >= 40 and not looks_like_verification_page(visible_text)

    def _wait_for_readable_target(self, url: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._target_page_is_readable(url):
                return True
            if self._visible_page_is_verification():
                return False
            self._page.wait_for_timeout(250)
        return self._target_page_is_readable(url)

    def _fetch_non_navigation_request(
        self,
        url: str,
        *,
        method: str,
        data: bytes | str | None,
        headers: Mapping[str, str] | None,
        response_encoding: str | None,
    ) -> FetchedPage:
        try:
            response = self._context.request.fetch(
                url,
                method=method,
                data=data,
                headers=dict(headers or {}),
                timeout=int(self.timeout * 1000),
                fail_on_status_code=False,
            )
        except self._playwright_error as error:
            raise NetworkError(f"浏览器请求页面失败：{url}；{error}") from error

        if response.status in (401, 403, 429):
            raise AccessBlockedError(
                f"{url} 返回 HTTP {response.status}；"
                "脚本不会绕过登录、Cloudflare、验证码或反爬验证"
            )
        if response.status >= 400:
            raise NetworkError(f"浏览器请求失败：{url} 返回 HTTP {response.status}")

        try:
            body = response.body()
        except self._playwright_error as error:
            raise NetworkError(f"读取浏览器响应失败：{url}；{error}") from error
        text = body.decode(response_encoding or "utf-8", errors="replace")
        if looks_like_verification_page(text):
            raise AccessBlockedError(
                f"{url} 返回了真人验证页面；请使用站点允许的正常访问方式"
            )
        return FetchedPage(url=response.url or url, text=text)

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        response_encoding: str | None = None,
    ) -> FetchedPage:
        if method.upper() != "GET":
            return self._fetch_non_navigation_request(
                url,
                method=method.upper(),
                data=data,
                headers=headers,
                response_encoding=response_encoding,
            )

        navigation_timed_out = False
        try:
            self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self.timeout * 1000),
            )
        except self._playwright_timeout_error:
            navigation_timed_out = True
        except self._playwright_error as error:
            raise NetworkError(f"浏览器打开页面失败：{url}；{error}") from error

        self._page.wait_for_timeout(500)
        if self._visible_page_is_verification():
            self._wait_for_manual_verification(url)
        elif not self._wait_for_readable_target(url, min(self.timeout, 5.0)):
            if self._visible_page_is_verification():
                self._wait_for_manual_verification(url)

        if not self._target_page_is_readable(url):
            if self._visible_page_is_verification():
                raise AccessBlockedError("浏览器仍返回真人验证页，未读取正文")
            detail = "导航超时，且" if navigation_timed_out else ""
            raise NetworkError(
                f"浏览器{detail}没有稳定加载请求页面：{url}；"
                "已拒绝复用上一页内容"
            )

        html = self._page.content()
        return FetchedPage(url=self._page.url or url, text=html)
