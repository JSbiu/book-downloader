from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Protocol

from .errors import ConfigurationError, DownloaderError, VerificationPageError
from .http import FetchedPage


class _FetchClient(Protocol):
    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        response_encoding: str | None = None,
    ) -> FetchedPage: ...

    def close(self) -> None: ...


def _interactive_confirm(message: str) -> bool:
    """在交互终端询问用户是否切换浏览器；非交互环境直接返回 False。"""
    try:
        if not sys.stdin.isatty():
            return False
        answer = input(f"{message} [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")


class AutoSwitchHttpClient:
    """HTTP 客户端包装：请求返回真人验证页时，切换到可见浏览器继续。

    脚本本身不破解验证；验证始终由用户在弹出的浏览器窗口里手动完成，
    完成后脚本自动恢复下载，后续请求全部复用这个浏览器会话。
    用户拒绝、无法启动浏览器或处于非交互环境时，按原样抛出错误。
    """

    def __init__(
        self,
        primary: _FetchClient,
        browser_factory: Callable[[], _FetchClient],
        *,
        confirm: Callable[[str], bool] | None = None,
    ):
        self._primary = primary
        self._browser_factory = browser_factory
        self._confirm = confirm or _interactive_confirm
        self._browser: _FetchClient | None = None
        self._lock = threading.RLock()

    @property
    def switched(self) -> bool:
        return self._browser is not None

    @property
    def supports_concurrent_requests(self) -> bool:
        # 切换后所有请求必须经过浏览器会话，不能并发复用同一个页面。
        return self._browser is None and getattr(
            self._primary, "supports_concurrent_requests", False
        )

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        response_encoding: str | None = None,
    ) -> FetchedPage:
        with self._lock:
            client = self._browser or self._primary
            try:
                return client.fetch(
                    url,
                    method=method,
                    data=data,
                    headers=headers,
                    response_encoding=response_encoding,
                )
            except VerificationPageError as error:
                if self._browser is not None:
                    raise
                self._switch_to_browser(error)
                assert self._browser is not None
                return self._browser.fetch(
                    url,
                    method=method,
                    data=data,
                    headers=headers,
                    response_encoding=response_encoding,
                )

    def _switch_to_browser(self, error: VerificationPageError) -> None:
        if not self._confirm(
            "检测到真人验证页。是否打开可见浏览器，"
            "由你手动完成验证后继续下载？"
        ):
            print(
                "已保持普通模式；也可以重新运行命令并加 --browser 参数。",
                file=sys.stderr,
            )
            raise error
        try:
            self._browser = self._browser_factory()
        except ConfigurationError as config_error:
            print(f"无法启动浏览器模式：{config_error}", file=sys.stderr)
            raise error from config_error
        print("已切换到浏览器模式；验证完成后会自动继续，后续请求复用该会话。")

    def page_search(self, adapter, query: str, limit: int, *, verification_timeout: float | None = None):
        """把页面级搜索委派给已开启的浏览器会话。"""
        if self._browser is None:
            raise ConfigurationError(
                "当前未在浏览器模式，无法执行页面级搜索；"
                "请使用 --browser 或 --browser-connect 重新运行"
            )
        return self._browser.page_search(
            adapter,
            query,
            limit,
            verification_timeout=verification_timeout,
        )

    def close(self) -> None:
        with self._lock:
            for client in (self._primary, self._browser):
                if client is None:
                    continue
                try:
                    client.close()
                except DownloaderError:
                    pass
