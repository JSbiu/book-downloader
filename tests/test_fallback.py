import unittest

from book_downloader.errors import (
    AccessBlockedError,
    ConfigurationError,
    VerificationPageError,
)
from book_downloader.fallback import AutoSwitchHttpClient
from book_downloader.http import FetchedPage


VERIFICATION_HTML = "<html><body>Just a moment... verify you are human</body></html>"


class FakePrimary:
    supports_concurrent_requests = True

    def __init__(self, blocked_urls: set[str]):
        self.blocked_urls = blocked_urls
        self.fetched: list[str] = []
        self.closed = False

    def fetch(self, url, *, method="GET", data=None, headers=None, response_encoding=None):
        self.fetched.append(url)
        if url in self.blocked_urls:
            raise VerificationPageError(f"{url} 返回了真人验证页面")
        return FetchedPage(url=url, text=f"<html>{url}</html>")

    def close(self):
        self.closed = True


class FakeBrowser:
    supports_concurrent_requests = False

    def __init__(self):
        self.fetched: list[str] = []
        self.closed = False

    def fetch(self, url, *, method="GET", data=None, headers=None, response_encoding=None):
        self.fetched.append(url)
        return FetchedPage(url=url, text=f"<html>browser:{url}</html>")

    def close(self):
        self.closed = True


class _PageSearchBrowser:
    supports_concurrent_requests = False

    def __init__(self, links):
        self.links = links
        self.calls = []
        self.closed = False

    def page_search(self, adapter, query, limit, *, verification_timeout=None):
        self.calls.append(
            {
                "adapter": adapter,
                "query": query,
                "limit": limit,
                "verification_timeout": verification_timeout,
            }
        )
        from book_downloader.models import SiteSearchHit

        return tuple(
            SiteSearchHit(title=title, url=url) for url, title in self.links[:limit]
        )

    def fetch(self, url, *, method="GET", data=None, headers=None, response_encoding=None):
        return FetchedPage(url=url, text="<html></html>")

    def close(self):
        self.closed = True


class _AdapterWithPageSearch:
    name = "fake-page"

    def __init__(self, browser):
        self.browser = browser
        self.calls = []

    def search_via_page(self, page, query, limit, *, navigation_timeout, verification_timeout):
        self.calls.append(
            {
                "page": page,
                "query": query,
                "limit": limit,
                "navigation_timeout": navigation_timeout,
                "verification_timeout": verification_timeout,
            }
        )
        return self.browser.page_search(self, query, limit, verification_timeout=verification_timeout)


class BrokenBrowserFactory:
    def __call__(self):
        raise ConfigurationError("浏览器模式需要 Playwright")


class AutoSwitchTests(unittest.TestCase):
    def test_switches_after_user_confirms_and_reuses_browser(self):
        primary = FakePrimary({"https://example.com/1.html"})
        browser = FakeBrowser()
        client = AutoSwitchHttpClient(primary, lambda: browser, confirm=lambda _: True)

        page = client.fetch("https://example.com/1.html")
        self.assertEqual(page.text, "<html>browser:https://example.com/1.html</html>")
        self.assertTrue(client.switched)

        page_two = client.fetch("https://example.com/2.html")
        self.assertEqual(page_two.text, "<html>browser:https://example.com/2.html</html>")
        # 切换后即使普通客户端可用，也全部走浏览器会话
        self.assertNotIn("https://example.com/2.html", primary.fetched)
        self.assertEqual(browser.fetched, ["https://example.com/1.html", "https://example.com/2.html"])

    def test_reraises_when_user_declines(self):
        primary = FakePrimary({"https://example.com/1.html"})
        client = AutoSwitchHttpClient(primary, FakeBrowser(), confirm=lambda _: False)

        with self.assertRaises(VerificationPageError):
            client.fetch("https://example.com/1.html")
        self.assertFalse(client.switched)

    def test_no_switch_without_verification_page(self):
        primary = FakePrimary(set())
        browser = FakeBrowser()
        client = AutoSwitchHttpClient(primary, lambda: browser, confirm=lambda _: True)

        page = client.fetch("https://example.com/1.html")
        self.assertEqual(page.text, "<html>https://example.com/1.html</html>")
        self.assertFalse(client.switched)

    def test_plain_access_blocked_error_propagates_without_prompt(self):
        class BlockedPrimary(FakePrimary):
            def fetch(self, url, **kwargs):
                raise AccessBlockedError(f"{url} 返回 HTTP 403")

        client = AutoSwitchHttpClient(
            BlockedPrimary(set()), FakeBrowser(), confirm=self.fail
        )
        with self.assertRaises(AccessBlockedError):
            client.fetch("https://example.com/1.html")
        self.assertFalse(client.switched)

    def test_browser_configuration_failure_reraises_original(self):
        primary = FakePrimary({"https://example.com/1.html"})
        client = AutoSwitchHttpClient(
            primary, BrokenBrowserFactory(), confirm=lambda _: True
        )
        with self.assertRaises(VerificationPageError):
            client.fetch("https://example.com/1.html")
        self.assertFalse(client.switched)

    def test_concurrency_flag_follows_switch_state(self):
        primary = FakePrimary({"https://example.com/1.html"})
        browser = FakeBrowser()
        client = AutoSwitchHttpClient(primary, lambda: browser, confirm=lambda _: True)
        self.assertTrue(client.supports_concurrent_requests)
        client.fetch("https://example.com/1.html")
        self.assertFalse(client.supports_concurrent_requests)

    def test_close_closes_both_clients(self):
        primary = FakePrimary({"https://example.com/1.html"})
        browser = FakeBrowser()
        client = AutoSwitchHttpClient(primary, lambda: browser, confirm=lambda _: True)
        client.fetch("https://example.com/1.html")
        client.close()
        self.assertTrue(primary.closed)
        self.assertTrue(browser.closed)

    def test_page_search_requires_browser(self):
        primary = FakePrimary(set())
        client = AutoSwitchHttpClient(primary, FakeBrowser(), confirm=lambda _: True)
        with self.assertRaises(ConfigurationError):
            client.page_search(object(), "q", 10)

    def test_page_search_delegates_after_switch(self):
        primary = FakePrimary({"https://example.com/blocked"})
        browser = _PageSearchBrowser([("https://example.com/book/1.html", "示例书名")])
        client = AutoSwitchHttpClient(primary, lambda: browser, confirm=lambda _: True)
        client.fetch("https://example.com/blocked")  # trigger switch
        adapter = _AdapterWithPageSearch(browser)
        hits = client.page_search(adapter, "示例", 5, verification_timeout=10)
        self.assertEqual([hit.title for hit in hits], ["示例书名"])
        self.assertEqual([hit.url for hit in hits], ["https://example.com/book/1.html"])


if __name__ == "__main__":
    unittest.main()
