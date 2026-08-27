import unittest

from book_downloader.errors import DownloaderError
from book_downloader.sites.txxt import TxxtAdapter


class _FakeLocator:
    """最小化的 Playwright Locator 替身，支持 .first、fill/press/wait_for/count/evaluate。"""

    def __init__(self, *, visible=False, count_sequence=None):
        self._visible = visible
        self._count_sequence = list(count_sequence or [])
        self.filled: list[str] = []
        self.pressed: list[str] = []
        self.waited: list[str] = []
        self.evaluate_result: str = ""

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=0):
        del timeout
        self.waited.append(state)
        if not self._visible:
            raise Exception("not visible")

    def fill(self, value: str):
        self.filled.append(value)

    def press(self, key: str):
        self.pressed.append(key)

    def count(self) -> int:
        if not self._count_sequence:
            return 1
        return self._count_sequence.pop(0)

    def evaluate(self, expr, arg=None):
        del expr, arg
        return self.evaluate_result


class _FakePage:
    def __init__(self, *, results_html, results_count_sequence):
        self.results_html = results_html
        self.input_locator = _FakeLocator(visible=True)
        self.fallback_input_locator = _FakeLocator(visible=False, count_sequence=[0])
        self.button_locator = _FakeLocator(visible=False)
        self.results_locator = _FakeLocator(count_sequence=results_count_sequence)
        self.form_html: str = ""
        self.url = "http://www.23txxt.com/e/search/index.php"
        self.goto_calls: list[str] = []

    def goto(self, url, wait_until=None, timeout=0):
        del wait_until, timeout
        self.goto_calls.append(url)

    def locator(self, selector: str):
        if selector.startswith("input[name="):
            return self.input_locator
        if selector == "input.btn-tosearch":
            return self.button_locator
        if selector.startswith("input["):
            return self.fallback_input_locator
        if selector.startswith("a[href*='/bqg/']"):
            return self.results_locator
        raise AssertionError(f"未处理的 selector: {selector}")

    def wait_for_timeout(self, ms: int):
        del ms

    def content(self) -> str:
        return self.results_html


class _VerificationFirstPage(_FakePage):
    """首页先被验证页拦截：输入框不可见，随后人工验证完成后恢复。"""

    def __init__(self, *, results_html):
        super().__init__(
            results_html=results_html,
            results_count_sequence=[1],
        )
        self.input_locator = _FakeLocator(visible=False)

    def locator(self, selector: str):
        if selector.startswith("input[name="):
            if len(self.input_locator.waited) >= 2:
                return _FakeLocator(visible=True)
            return self.input_locator
        return super().locator(selector)


class TxxtSearchTests(unittest.TestCase):
    def test_build_search_request_uses_get_with_keyword(self):
        adapter = TxxtAdapter()
        request = adapter.build_search_request("示例 小说", limit=10)
        self.assertEqual(request.method, "GET")
        self.assertIn("keyWord=", request.url)
        self.assertIn("ar.php", request.url)
        self.assertEqual(request.response_encoding, "utf-8")

    def test_parse_search_results_keeps_book_links_only(self):
        html = """
        <ul class="list">
          <li><a href="/bqg/111084/">示例小说</a><p>作者简介摘要</p></li>
          <li><a href="/bqg/22222.html">另一本小说</a></li>
          <li><a href="/bqg/111084/44601734.html">第1章 正文章节不应作为结果</a></li>
        </ul>
        """
        adapter = TxxtAdapter()
        hits = adapter.parse_search_results(
            html, "http://www.23txxt.com/e/search/index.php", limit=10
        )
        self.assertEqual(
            [(hit.title, hit.url) for hit in hits],
            [
                ("示例小说", "http://www.23txxt.com/bqg/111084/"),
                ("另一本小说", "http://www.23txxt.com/bqg/22222.html"),
            ],
        )

    def test_search_via_page_fills_form_and_returns_results(self):
        results_html = """
        <a href="/bqg/111084/">示例小说</a>
        <a href="/bqg/22222.html">另一本小说</a>
        """
        page = _FakePage(
            results_html=results_html,
            results_count_sequence=[0, 0, 1],
        )
        adapter = TxxtAdapter()
        hits = adapter.search_via_page(
            page,
            "示例小说",
            limit=10,
            navigation_timeout=20,
            verification_timeout=5,
        )
        self.assertEqual(
            [(hit.title, hit.url) for hit in hits],
            [
                ("示例小说", "http://www.23txxt.com/bqg/111084/"),
                ("另一本小说", "http://www.23txxt.com/bqg/22222.html"),
            ],
        )
        self.assertEqual(page.input_locator.filled, ["示例小说"])
        self.assertEqual(page.input_locator.pressed, ["Enter"])
        self.assertEqual(page.goto_calls, ["http://www.23txxt.com/"])

    def test_search_via_page_waits_for_manual_verification(self):
        results_html = '<a href="/bqg/33333/">验证后的小说</a>'
        page = _VerificationFirstPage(results_html=results_html)
        adapter = TxxtAdapter()
        hits = adapter.search_via_page(
            page,
            "示例",
            limit=10,
            navigation_timeout=20,
            verification_timeout=5,
        )
        self.assertEqual([hit.title for hit in hits], ["验证后的小说"])
        self.assertGreater(len(page.input_locator.waited), 1)

    def test_search_via_page_times_out_when_no_results_appear(self):
        class _AlwaysEmptyResultsLocator(_FakeLocator):
            def count(self) -> int:
                return 0

        page = _FakePage(
            results_html="",
            results_count_sequence=[0],
        )
        page.results_locator = _AlwaysEmptyResultsLocator(count_sequence=[])

        adapter = TxxtAdapter()
        with self.assertRaises(DownloaderError):
            adapter.search_via_page(
                page,
                "示例小说",
                limit=10,
                navigation_timeout=1,
                verification_timeout=0.1,
            )

    def test_search_via_page_falls_back_to_single_text_input(self):
        class _NoNamedInputPage(_FakePage):
            def locator(self, selector):
                if selector.startswith("input[name="):
                    return _FakeLocator(visible=False)
                return super().locator(selector)

        results_html = '<a href="/bqg/111084/">兜底搜索结果</a>'
        page = _NoNamedInputPage(
            results_html=results_html,
            results_count_sequence=[0, 1],
        )
        page.fallback_input_locator = _FakeLocator(visible=True, count_sequence=[1])

        adapter = TxxtAdapter()
        hits = adapter.search_via_page(
            page,
            "示例",
            limit=10,
            navigation_timeout=5,
            verification_timeout=0.5,
        )
        self.assertEqual([hit.title for hit in hits], ["兜底搜索结果"])
        self.assertEqual(page.fallback_input_locator.filled, ["示例"])

    def test_search_via_page_finds_input_via_search_button_form(self):
        class _ButtonFormPage(_FakePage):
            def locator(self, selector):
                if selector.startswith("input[name="):
                    name = selector[len("input[name='") : -2]
                    if name == "wd":
                        return self.input_locator
                    return _FakeLocator(visible=False)
                if selector == "input.btn-tosearch":
                    return self.button_locator
                return super().locator(selector)

        results_html = '<a href="/bqg/111084/">按钮定位结果</a>'
        page = _ButtonFormPage(
            results_html=results_html,
            results_count_sequence=[0, 1],
        )
        page.form_html = (
            '<form action="/e/search/index.php">'
            '<input type="text" name="wd" value="">'
            '<input type="submit" class="btn-tosearch" value="搜索">'
            "</form>"
        )
        page.button_locator = _FakeLocator(visible=True)
        page.button_locator.evaluate_result = page.form_html

        adapter = TxxtAdapter()
        hits = adapter.search_via_page(
            page,
            "示例",
            limit=10,
            navigation_timeout=5,
            verification_timeout=0.5,
        )
        self.assertEqual([hit.title for hit in hits], ["按钮定位结果"])
        self.assertEqual(page.input_locator.filled, ["示例"])
        self.assertEqual(page.input_locator.pressed, ["Enter"])


if __name__ == "__main__":
    unittest.main()
