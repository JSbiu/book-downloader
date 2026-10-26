import unittest
from contextlib import redirect_stderr
from io import StringIO

from bs4 import BeautifulSoup

from book_downloader.errors import DiscoveryError, DownloaderError
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
                # .htm 详情页地址被统一规范化到完整目录页
                ("另一个示例", "https://www.69shuba.com/book/67890/"),
            ],
        )
        self.assertEqual(page.input_locator.filled, ["示例小说"])
        self.assertEqual(page.input_locator.pressed, ["Enter"])

    def test_parse_search_results_normalizes_book_url_to_catalog(self):
        html = """
        <a href="/book/89702.htm">示例小说</a>
        <a href="/book/67890/">另一个示例</a>
        """
        adapter = ShubaAdapter()
        hits = adapter.parse_search_results(
            html, "https://www.69shuba.com/", limit=10
        )
        self.assertEqual(
            [hit.url for hit in hits],
            [
                "https://www.69shuba.com/book/89702/",
                "https://www.69shuba.com/book/67890/",
            ],
        )

    def test_extract_chapter_links_sorts_pinned_latest_chapter(self):
        # 69shuba 目录页顶部有"最新章节"置顶区（第570章），与完整目录重复；
        # 提取后应按章节号排序，第570章归位到末尾，而不是出现在最前面。
        html = """
        <div class="catalog">
          <ul>
            <li><a href="/txt/89702/41039598">第570章 亚特兰之主</a></li>
          </ul>
        </div>
        <div class="catalog">
          <ul>
            <li><a href="/txt/89702/40266247">第1章 红铁之子</a></li>
            <li><a href="/txt/89702/40266248">第2章 水亲和</a></li>
            <li><a href="/txt/89702/41039598">第570章 亚特兰之主</a></li>
          </ul>
        </div>
        """
        adapter = ShubaAdapter()
        links = adapter.extract_chapter_links(
            BeautifulSoup(html, "html.parser"), "https://www.69shuba.com/book/89702/"
        )
        self.assertEqual(
            [link.title for link in links],
            ["第1章 红铁之子", "第2章 水亲和", "第570章 亚特兰之主"],
        )
        self.assertEqual(len(links), 3)

    def test_guess_catalog_url_handles_book_detail_page(self):
        adapter = ShubaAdapter()
        self.assertEqual(
            adapter.guess_catalog_url("https://www.69shuba.com/book/89702.htm"),
            "https://www.69shuba.com/book/89702/",
        )
        self.assertEqual(
            adapter.guess_catalog_url("https://www.69shuba.com/txt/89702/41039551"),
            "https://www.69shuba.com/book/89702/",
        )
        self.assertIsNone(adapter.guess_catalog_url("https://www.69shuba.com/"))

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


class ShubaCatalogTests(unittest.TestCase):
    def extract(self, html, page_url="https://www.69shuba.com/book/12345/"):
        warning = StringIO()
        with redirect_stderr(warning):
            links = ShubaAdapter().extract_chapter_links(
                BeautifulSoup(html, "html.parser"), page_url
            )
        return links, warning.getvalue()

    def test_preserves_unnumbered_middle_chapter_in_complete_catalogue_order(self):
        html = """
        <div class="catalog"><h3>最新章节</h3><ul>
          <li><a href="/txt/12345/10007">第3章 示例后续</a></li>
        </ul></div>
        <div class="catalog"><ul>
          <li><a href="/txt/12345/10009">第1章 示例开篇</a></li>
          <li><a href="/txt/12345/10001">示例中间章节</a></li>
          <li><a href="/txt/12345/10001">示例中间章节</a></li>
          <li><a href="/txt/12345/10007">第3章 示例后续</a></li>
        </ul></div>
        """
        links, warning = self.extract(html)

        self.assertEqual([link.title for link in links], [
            "第1章 示例开篇", "示例中间章节", "第3章 示例后续",
        ])
        self.assertEqual([link.url for link in links], [
            "https://www.69shuba.com/txt/12345/10009",
            "https://www.69shuba.com/txt/12345/10001",
            "https://www.69shuba.com/txt/12345/10007",
        ])
        self.assertEqual([link.number for link in links], [1, 2, 3])
        self.assertIn("无法确认章号连续性", warning)

    def test_short_and_normal_catalogues_use_complete_dom_instead_of_pinned_order(self):
        entries = [
            ("10009", "第1章 示例开篇"),
            ("10001", "第2章 示例中段"),
            ("10007", "第3章 示例后续"),
        ]
        for count in (2, 3):
            with self.subTest(count=count):
                latest_id, latest_title = entries[count - 1]
                body = "".join(
                    f'<li><a href="/txt/12345/{chapter_id}">{title}</a></li>'
                    for chapter_id, title in entries[:count]
                )
                html = (
                    f'<div class="catalog"><ul><li><a href="/txt/12345/{latest_id}">'
                    f'{latest_title}</a></li></ul></div>'
                    f'<div class="catalog"><ul>{body}</ul></div>'
                )
                links, warning = self.extract(html)
                self.assertEqual([link.title for link in links], [title for _, title in entries[:count]])
                self.assertEqual(warning, "")

    def test_known_display_numbers_do_not_override_dom_order_and_gaps_are_reported(self):
        for numbers in ((1, 3), (1, 3, 2)):
            with self.subTest(numbers=numbers):
                body = "".join(
                    f'<li><a href="/txt/12345/{90000 - number}">第{number}章 示例章节</a></li>'
                    for number in numbers
                )
                links, warning = self.extract(f'<div class="catalog"><ul>{body}</ul></div>')
                self.assertEqual([link.title for link in links], [f"第{number}章 示例章节" for number in numbers])
                self.assertIn("章号不连续", warning)

    def test_collects_only_same_book_chapter_urls_inside_catalogue(self):
        html = """
        <div class="catalog"><ul>
          <li><a href="/txt/12345/10009">第1章 示例开篇</a></li>
          <li><a href="/txt/12345/10001">第2章 示例后续</a></li>
          <li><a href="/txt/99999/10002">第3章 其他书籍</a></li>
          <li><a href="https://example.test/txt/12345/10003">第4章 外部链接</a></li>
          <li><a href="/txt/12345/10004/extra">第5章 非章节路径</a></li>
        </ul></div>
        <div><a href="/txt/12345/10005">第6章 目录外链接</a></div>
        """
        links, warning = self.extract(html)

        self.assertEqual([link.url for link in links], [
            "https://www.69shuba.com/txt/12345/10009",
            "https://www.69shuba.com/txt/12345/10001",
        ])
        self.assertEqual(warning, "")

    def test_latest_preview_alone_is_not_accepted_as_a_complete_catalogue(self):
        html = """
        <div class="catalog"><h3>最新章节</h3><ul>
          <li><a href="/txt/12345/10009">第8章 示例章节</a></li>
          <li><a href="/txt/12345/10001">第9章 示例章节</a></li>
        </ul></div>
        """
        with self.assertRaisesRegex(DiscoveryError, "完整.*目录"):
            self.extract(html)

    def test_conflicting_catalogue_blocks_require_explicit_review(self):
        first = '<li><a href="/txt/12345/10001">第1章 示例章节</a></li>'
        second = '<li><a href="/txt/12345/10002">第2章 示例章节</a></li>'
        third = '<li><a href="/txt/12345/10003">第3章 示例章节</a></li>'
        for other in (first + third, second + first, first.replace("示例章节", "另一标题") + second):
            with self.subTest(other=other):
                html = (
                    f'<div class="catalog"><ul>{first}{second}</ul></div>'
                    f'<div class="catalog"><ul>{other}</ul></div>'
                )
                with self.assertRaisesRegex(DiscoveryError, "完整.*目录"):
                    self.extract(html)

    def test_existing_catalogue_container_variants_remain_supported(self):
        for start, end, item in (
            ('<div class="catalog"><ul>', '</ul></div>', 'li'),
            ('<div class="listmain"><dl>', '</dl></div>', 'dd'),
            ('<div id="list"><dl>', '</dl></div>', 'dd'),
            ('<div class="chapterlist">', '</div>', 'p'),
        ):
            with self.subTest(start=start):
                body = "".join(
                    f'<{item}><a href="/txt/12345/{number}">第{number}章 示例章节</a></{item}>'
                    for number in (1, 2)
                )
                links, warning = self.extract(start + body + end)
                self.assertEqual([link.title for link in links], ["第1章 示例章节", "第2章 示例章节"])
                self.assertEqual(warning, "")

    def test_detail_and_chapter_pages_are_not_full_catalogue_sources(self):
        html = """
        <div class="catalog"><ul>
          <li><a href="/txt/12345/10001">第1章 示例开篇</a></li>
          <li><a href="/txt/12345/10002">第2章 示例后续</a></li>
        </ul></div>
        """
        for page_url in (
            "https://www.69shuba.com/book/12345.htm",
            "https://www.69shuba.com/txt/12345/10001",
        ):
            with self.subTest(page_url=page_url):
                links, _ = self.extract(html, page_url)
                self.assertEqual(links, [])


if __name__ == "__main__":
    unittest.main()