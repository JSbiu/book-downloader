import unittest

from book_downloader.errors import DownloaderError
from book_downloader.sites.shuba import ShubaAdapter


class _FakeLocator:
    """最小化的 Playwright Locator 替身，支持 .first、fill/press/wait_for/count。"""

    def __init__(self, *, count_sequence):
        self._count_sequence = list(count_sequence)
        self.filled: list[str] = []
        self.pressed: list[str] = []
        self.waited: list[str] = []

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=0):
        del timeout
        self.waited.append(state)

    def fill(self, value: str):
        self.filled.append(value)

    def press(self, key: str):
        self.pressed.append(key)

    def count(self) -> int:
        if not self._count_sequence:
            return 1
        return self._count_sequence.pop(0)


class _FakePage:
    def __init__(self, *, results_html, results_count_sequence):
        self.results_html = results_html
        self.results_locator = _FakeLocator(count_sequence=results_count_sequence)
        self.input_locator = _FakeLocator(count_sequence=[1])
        self.url = "https://www.69shuba.com/modules/article/search.php"
        self.goto_calls: list[str] = []

    def goto(self, url, wait_until=None, timeout=0):
        del wait_until, timeout
        self.goto_calls.append(url)

    def locator(self, selector: str):
        if selector == "input[name='searchkey']":
            return self.input_locator
        if selector.startswith("a[href*='/book/']"):
            return self.results_locator
        raise AssertionError(f"未处理的 selector: {selector}")

    def wait_for_url(self, pattern, timeout=0):
        del pattern, timeout

    def wait_for_timeout(self, ms: int):
        del ms

    def content(self) -> str:
        return self.results_html


class ShubaPageSearchTests(unittest.TestCase):
    def test_search_via_page_returns_results_after_polling(self):
        results_html = """
        <a href="/book/12345/">示例小说</a>
        <a href="/book/67890.htm">另一个示例</a>
        """
        # 前两次轮询返回 0（结果未出现），第三次返回 1（结果到位）
        page = _FakePage(
            results_html=results_html,
            results_count_sequence=[0, 0, 1],
        )

        adapter = ShubaAdapter()
        hits = adapter.search_via_page(
            page,
            "示例小说",
            limit=10,
            navigation_timeout=20,
            verification_timeout=5,
        )

        self.assertEqual(
            [(h.title, h.url) for h in hits],
            [
                ("示例小说", "https://www.69shuba.com/book/12345/"),
                ("另一个示例", "https://www.69shuba.com/book/67890.htm"),
            ],
        )
        self.assertEqual(page.input_locator.filled, ["示例小说"])
        self.assertEqual(page.input_locator.pressed, ["Enter"])

    def test_search_via_page_times_out_when_no_results_appear(self):
        class _AlwaysEmptyResultsLocator(_FakeLocator):
            def count(self) -> int:  # type: ignore[override]
                return 0

        page = _FakePage(
            results_html="",
            results_count_sequence=[0],
        )
        page.results_locator = _AlwaysEmptyResultsLocator(count_sequence=[])

        adapter = ShubaAdapter()
        with self.assertRaises(DownloaderError):
            adapter.search_via_page(
                page,
                "示例小说",
                limit=10,
                navigation_timeout=1,
                verification_timeout=0.1,
            )


if __name__ == "__main__":
    unittest.main()