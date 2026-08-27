import unittest

from book_downloader.browser import should_use_browser
from book_downloader.sites.registry import adapter_for_url


class SiteTieringTests(unittest.TestCase):
    """站点分级：无需验证的站点在浏览器模式下走普通 HTTP 静默通道。"""

    def test_silent_sites_do_not_require_browser(self):
        self.assertFalse(
            adapter_for_url("https://www.trxs.cc/tongren/1.html").requires_browser
        )
        self.assertFalse(
            adapter_for_url("https://www.bixiange.top/xxx/1/").requires_browser
        )

    def test_verified_sites_require_browser(self):
        self.assertTrue(
            adapter_for_url("http://www.23txxt.com/bqg/1/").requires_browser
        )
        self.assertTrue(
            adapter_for_url("https://www.69shuba.com/book/1/").requires_browser
        )

    def test_without_fallback_always_uses_browser(self):
        self.assertTrue(
            should_use_browser("https://www.trxs.cc/tongren/1.html", None)
        )

    def test_silent_sites_downgrade_to_http(self):
        fallback = object()
        self.assertFalse(
            should_use_browser("https://www.trxs.cc/tongren/1.html", fallback)
        )
        self.assertFalse(
            should_use_browser("https://www.bixiange.top/xxx/1/", fallback)
        )

    def test_verified_sites_stay_on_browser(self):
        fallback = object()
        self.assertTrue(
            should_use_browser("http://www.23txxt.com/bqg/1/", fallback)
        )
        self.assertTrue(
            should_use_browser("https://www.69shuba.com/book/1/", fallback)
        )


if __name__ == "__main__":
    unittest.main()
