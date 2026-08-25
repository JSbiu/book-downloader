from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

import requests

from .errors import AccessBlockedError, NetworkError, VerificationPageError


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


@dataclass(frozen=True)
class FetchedPage:
    url: str
    text: str


def looks_like_verification_page(text: str) -> bool:
    sample = text[:20000].lower()
    markers = (
        "just a moment",
        "cf-chl-",
        "challenge-platform",
        "verify you are human",
        "验证您是否为真人",
        "请完成安全验证",
        "人机验证",
        "正在进行安全验证",
        "验证你不是自动程序",
        "恶意自动程序",
    )
    return any(marker in sample for marker in markers)


class HttpClient:
    supports_concurrent_requests = True

    def __init__(self, timeout: float, retries: int, *, unattended: bool = False):
        self.timeout = timeout
        self.retries = retries
        self.unattended = unattended
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _blocked_message(self, url: str, detail: str) -> str:
        if self.unattended:
            return (
                f"{url} {detail}；--auto 不会等待人工验证，也不会绕过站点防护；"
                "请改用站点允许的公开接口或授权访问方式"
            )
        return (
            f"{url} {detail}；"
            "脚本不会绕过登录、Cloudflare、验证码或反爬验证"
        )

    def close(self) -> None:
        self.session.close()

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        response_encoding: str | None = None,
    ) -> FetchedPage:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    data=data,
                    headers=dict(headers or {}),
                    timeout=self.timeout,
                )
                if response.status_code in (401, 403, 429):
                    raise AccessBlockedError(
                        self._blocked_message(
                            url,
                            f"返回 HTTP {response.status_code}",
                        )
                    )
                response.raise_for_status()
                response.encoding = (
                    response_encoding
                    or response.apparent_encoding
                    or "utf-8"
                )
                if looks_like_verification_page(response.text):
                    raise VerificationPageError(
                        self._blocked_message(
                            url,
                            "返回了真人验证页面",
                        )
                    )
                return FetchedPage(url=response.url, text=response.text)
            except AccessBlockedError:
                raise
            except requests.RequestException as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
        raise NetworkError(f"请求失败：{url}；{last_error}")
