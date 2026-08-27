import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from book_downloader.cli import select_search_result
from book_downloader.errors import AccessBlockedError, DownloaderError, NetworkError
from book_downloader.http import FetchedPage
from book_downloader.models import SiteSearchHit
from book_downloader.search import (
    SearchResult,
    _merge_result_sets,
    _search_query,
    search_sites,
)
from book_downloader.sites.base import SiteAdapter
from book_downloader.sites.bixiange import BixiangeAdapter
from book_downloader.sites.shuba import ShubaAdapter
from book_downloader.sites.trxs_cc import TrxsCcAdapter


class SearchClient:
    def __init__(self, html: str):
        self.html = html
        self.supports_concurrent_requests = True
        self.fetched: list[dict[str, object]] = []

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        response_encoding: str | None = None,
    ) -> FetchedPage:
        self.fetched.append(
            {
                "url": url,
                "method": method,
                "data": data,
                "headers": headers or {},
                "response_encoding": response_encoding,
            }
        )
        return FetchedPage(url=url, text=self.html)


class FakeSearchAdapter(SiteAdapter):
    name = "fake"
    hosts = ("fake.example",)

    def __init__(
        self,
        results: tuple[SiteSearchHit, ...],
        name: str = "fake",
        host: str = "fake.example",
    ):
        self.results = results
        self.name = name
        self.hosts = (host,)

    def build_search_url(self, query: str, limit: int) -> str:
        return f"https://{self.hosts[0]}/search?q={query}&limit={limit}"

    def parse_search_results(self, html: str, page_url: str, limit: int):
        del html, page_url
        return self.results[:limit]


class FallbackSearchAdapter(FakeSearchAdapter):
    def parse_search_results(self, html: str, page_url: str, limit: int):
        del html
        if "q=示例小说&limit=" not in page_url:
            return ()
        return self.results[:limit]


class SearchTests(unittest.TestCase):
    def test_search_query_uses_longest_segment_and_marks_local_filtering(self):
        self.assertEqual(
            _search_query("示例小说，续篇"),
            ("示例小说", True),
        )
        self.assertEqual(_search_query("示例小说"), ("示例小说", False))

    def test_trxs_search_url_uses_gbk_form_encoding(self):
        url = TrxsCcAdapter().build_search_url("  示例小说  ", 10)
        params = parse_qs(urlsplit(url).query, encoding="gb2312")

        self.assertEqual(params["keyboard"], ["示例小说"])
        self.assertEqual(params["show"], ["title"])
        self.assertEqual(params["classid"], ["0"])

    def test_trxs_search_request_uses_post_and_gbk_form_encoding(self):
        request = TrxsCcAdapter().build_search_request("示例小说", 10)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://www.trxs.cc/e/search/index.php")
        self.assertEqual(request.response_encoding, "gb2312")
        self.assertEqual(
            dict(request.headers)["Content-Type"],
            "application/x-www-form-urlencoded",
        )

        params = parse_qs(request.data.decode("ascii"), encoding="gb2312")
        self.assertEqual(params["keyboard"], ["示例小说"])
        self.assertEqual(params["show"], ["title"])
        self.assertEqual(params["classid"], ["0"])

    def test_trxs_results_parse_book_title_and_snippet(self):
        html = """
        <div class="bk">
          <h3><a href="/tongren/11699.html">示例小说</a></h3>
          <div class="booknews">作者：示例作者</div>
          <p>这是一本测试小说的简介。</p>
        </div>
        <div class="bk">
          <h3><a href="https://example.com/book">不支持的站点</a></h3>
        </div>
        """

        results = TrxsCcAdapter().parse_search_results(
            html,
            "https://www.trxs.cc/e/search/index.php",
            10,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "示例小说")
        self.assertEqual(results[0].url, "https://www.trxs.cc/tongren/11699.html")
        self.assertEqual(results[0].snippet, "作者：示例作者")

    def test_bixiange_search_request_uses_gbk_form_encoding(self):
        request = BixiangeAdapter().build_search_request("示例小说", 10)

        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://www.bixiange.top/e/search/indexpage.php")
        self.assertEqual(request.response_encoding, "gb18030")
        params = parse_qs(request.data.decode("ascii"), encoding="gb2312")
        self.assertEqual(params["keyboard"], ["示例小说"])
        self.assertEqual(params["show"], ["title"])
        self.assertEqual(params["classid"], ["0"])

    def test_bixiange_results_parse_title_and_description(self):
        html = """
        <div class="list"><ul>
          <li>
            <div class="cover"><a href="/xhqh/12345"><img alt="示例小说"></a></div>
            <div class="info">
              <div class="title"><strong><a href="/xhqh/12345">示例小说</a></strong></div>
              <div class="descript"><a href="/xhqh/12345">这是测试简介。</a></div>
            </div>
          </li>
        </ul></div>
        """

        results = BixiangeAdapter().parse_search_results(
            html,
            "https://www.bixiange.top/e/search/result/",
            10,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "示例小说")
        self.assertEqual(results[0].url, "https://www.bixiange.top/xhqh/12345")
        self.assertEqual(results[0].snippet, "这是测试简介。")

    def test_shuba_search_request_uses_gbk_form_encoding(self):
        request = ShubaAdapter().build_search_request("示例小说", 10)

        self.assertEqual(request.method, "POST")
        self.assertEqual(
            request.url,
            "https://www.69shuba.com/modules/article/search.php",
        )
        self.assertEqual(request.response_encoding, "gb18030")
        params = parse_qs(request.data.decode("ascii"), encoding="gb2312")
        self.assertEqual(params["searchkey"], ["示例小说"])
        self.assertEqual(params["submit"], ["Search"])

    def test_shuba_results_parse_book_links(self):
        html = """
        <div class="result">
          <a href="/book/12345/">示例小说</a>
          <p>作者：示例作者</p>
        </div>
        <a href="/book/12345/">示例小说</a>
        <a href="/book/67890.htm">另一个示例</a>
        """

        results = ShubaAdapter().parse_search_results(
            html,
            "https://www.69shuba.com/modules/article/search.php",
            10,
        )

        self.assertEqual(
            [(item.title, item.url) for item in results],
            [
                ("示例小说", "https://www.69shuba.com/book/12345/"),
                # .htm 详情页地址被统一规范化到完整目录页
                ("另一个示例", "https://www.69shuba.com/book/67890/"),
            ],
        )
        self.assertEqual(results[0].snippet, "示例小说 作者：示例作者")

    def test_trxs_results_parse_card_with_link_wrapping_entire_item(self):
        html = """
        <div class="books m-cols">
          <div class="bk">
            <a href="/tongren/12098.html">
              <div class="infos">
                <h3>示例小说 (1-10)</h3>
                <div class="booknews">作者：示例作者 <label class="date">2026-01-01</label></div>
                <p>这是卡片摘要。</p>
              </div>
            </a>
          </div>
        </div>
        """

        results = TrxsCcAdapter().parse_search_results(
            html,
            "https://www.trxs.cc/e/search/index.php",
            10,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "示例小说 (1-10)")
        self.assertEqual(results[0].url, "https://www.trxs.cc/tongren/12098.html")
        self.assertEqual(results[0].snippet, "作者：示例作者 2026-01-01")

    def test_search_sites_queries_only_adapters_with_internal_search(self):
        html = """
        <div class="bk">
          <h3><a href="/tongren/11699.html">目标小说</a></h3>
          <p>目标简介</p>
        </div>
        """
        client = SearchClient(html)

        results = search_sites(client, "目标小说", limit=5)

        self.assertEqual([result.title for result in results], ["目标小说"])
        self.assertEqual(len(client.fetched), 4)
        self.assertEqual(
            [item["method"] for item in client.fetched],
            ["POST", "GET", "POST", "POST"],
        )
        self.assertEqual(
            [item["url"] for item in client.fetched],
            [
                "https://www.trxs.cc/e/search/index.php",
                "http://www.23txxt.com/ar.php?keyWord=%E7%9B%AE%E6%A0%87%E5%B0%8F%E8%AF%B4",
                "https://www.bixiange.top/e/search/indexpage.php",
                "https://www.69shuba.com/modules/article/search.php",
            ],
        )
        self.assertEqual(client.fetched[0]["response_encoding"], "gb2312")
        self.assertEqual(client.fetched[1]["response_encoding"], "utf-8")
        self.assertEqual(client.fetched[2]["response_encoding"], "gb18030")
        self.assertEqual(client.fetched[3]["response_encoding"], "gb18030")

    def test_search_results_are_interleaved_by_site(self):
        first = tuple(
            SearchResult(f"a{index}", f"https://a.example/{index}", "", "a.example")
            for index in range(1, 4)
        )
        second = tuple(
            SearchResult(f"b{index}", f"https://b.example/{index}", "", "b.example")
            for index in range(1, 3)
        )

        merged = _merge_result_sets((first, second), 5)

        self.assertEqual([item.title for item in merged], ["a1", "b1", "a2", "b2", "a3"])

    def test_search_result_selection_is_one_based(self):
        results = (
            SearchResult("第一条", "https://www.trxs.cc/one.html", "", "trxs.cc"),
            SearchResult("第二条", "https://www.trxs.cc/two.html", "", "trxs.cc"),
        )

        self.assertEqual(select_search_result(results, 2).title, "第二条")
        with self.assertRaises(DownloaderError):
            select_search_result(results, 3)

    def test_search_sites_falls_back_to_page_search_when_http_empty(self):
        empty_adapter = FakeSearchAdapter((), name="empty", host="empty.example")
        empty_adapter.search_via_page = lambda page, query, limit, **kwargs: (
            SiteSearchHit("页面级结果", "https://empty.example/book/1", "页面摘要"),
        )

        class PageSearchClient:
            supports_concurrent_requests = True

            def __init__(self):
                self.page_calls = []

            def fetch(self, url, **kwargs):
                return FetchedPage(url=url, text="<html>no results</html>")

            def page_search(self, adapter, query, limit, *, verification_timeout=None):
                self.page_calls.append(
                    {
                        "adapter": adapter,
                        "query": query,
                        "limit": limit,
                        "verification_timeout": verification_timeout,
                    }
                )
                return adapter.search_via_page(None, query, limit)

        client = PageSearchClient()
        with patch("book_downloader.search.searchable_adapters", return_value=(empty_adapter,)):
            results = search_sites(client, "示例", limit=5, verification_timeout=42)

        self.assertEqual([result.title for result in results], ["页面级结果"])
        self.assertEqual(
            client.page_calls,
            [
                {
                    "adapter": empty_adapter,
                    "query": "示例",
                    "limit": 5,
                    "verification_timeout": 42,
                }
            ],
        )

    def test_search_sites_falls_back_to_page_search_when_http_blocked(self):
        blocked_adapter = FakeSearchAdapter((), name="blocked", host="blocked.example")
        blocked_adapter.search_via_page = lambda page, query, limit, **kwargs: (
            SiteSearchHit("页面级结果", "https://blocked.example/book/1", "页面摘要"),
        )

        class BrowserClient:
            supports_concurrent_requests = False

            def __init__(self):
                self.page_calls = []

            def fetch(self, url, **kwargs):
                raise AccessBlockedError(f"{url} 返回 HTTP 403")

            def page_search(self, adapter, query, limit, *, verification_timeout=None):
                self.page_calls.append((adapter, query, limit, verification_timeout))
                return adapter.search_via_page(None, query, limit)

        client = BrowserClient()
        with patch("book_downloader.search.searchable_adapters", return_value=(blocked_adapter,)):
            results = search_sites(client, "示例", limit=5, verification_timeout=42)

        self.assertEqual([result.title for result in results], ["页面级结果"])
        self.assertEqual(client.page_calls, [(blocked_adapter, "示例", 5, 42)])

    def test_search_sites_blocks_site_when_page_search_unavailable(self):
        adapter = FakeSearchAdapter((), name="blocked", host="blocked.example")

        class BlockedClient:
            supports_concurrent_requests = False

            def fetch(self, url, **kwargs):
                raise AccessBlockedError(f"{url} 返回 HTTP 403")

        client = BlockedClient()
        with patch("book_downloader.search.searchable_adapters", return_value=(adapter,)):
            with self.assertRaises(Exception):
                search_sites(client, "示例", limit=5)

    def test_search_sites_silently_skips_page_search_failure(self):
        empty_adapter = FakeSearchAdapter((), name="empty", host="empty.example")
        empty_adapter.search_via_page = lambda *args, **kwargs: (_ for _ in ()).throw(
            NetworkError("页面级搜索也失败了")
        )

        class PageSearchClient:
            supports_concurrent_requests = False

            def fetch(self, url, **kwargs):
                return FetchedPage(url=url, text="<html>no results</html>")

            def page_search(self, adapter, query, limit, *, verification_timeout=None):
                return adapter.search_via_page(None, query, limit)

        client = PageSearchClient()
        with patch("book_downloader.search.searchable_adapters", return_value=(empty_adapter,)):
            with self.assertRaises(Exception):
                search_sites(client, "示例", limit=5)

    def test_fake_sites_can_be_interleaved_by_coordinator(self):
        adapters = (
            FakeSearchAdapter(
                (
                    SiteSearchHit("甲", "https://fake.example/a", ""),
                    SiteSearchHit("乙", "https://fake.example/b", ""),
                )
            ),
        )
        client = SearchClient("")

        with patch("book_downloader.search.searchable_adapters", return_value=adapters):
            results = search_sites(client, "测试", limit=2)

        self.assertEqual([result.title for result in results], ["甲", "乙"])

    def test_search_sites_orders_by_relevance(self):
        adapters = (
            FakeSearchAdapter(
                (SiteSearchHit("完全不相关小说", "https://noisy.example/a", ""),),
                name="noisy",
                host="noisy.example",
            ),
            FakeSearchAdapter(
                (SiteSearchHit("以一龙之力打倒整个世界！", "https://exact.example/b", ""),),
                name="exact",
                host="exact.example",
            ),
        )
        client = SearchClient("")

        with patch("book_downloader.search.searchable_adapters", return_value=adapters):
            results = search_sites(client, "以一龙之力打倒整个世界", limit=5)

        self.assertEqual(
            [result.title for result in results],
            ["以一龙之力打倒整个世界！", "完全不相关小说"],
        )

    def test_relevance_score_ranks_exact_over_partial(self):
        from book_downloader.search import _relevance_score

        self.assertEqual(
            _relevance_score("以一龙之力打倒整个世界！", "以一龙之力打倒整个世界"), 100
        )
        self.assertEqual(
            _relevance_score("以一龙之力打倒整个世界续集", "以一龙之力打倒整个世界"), 90
        )
        self.assertEqual(
            _relevance_score("我的以一龙之力打倒整个世界", "以一龙之力打倒整个世界"), 70
        )
        self.assertEqual(
            _relevance_score("以一龙之力", "以一龙之力打倒整个世界"), 40
        )
        self.assertEqual(
            _relevance_score("方舟女尊博士的辛苦生活", "以一龙之力打倒整个世界"), 0
        )

    def test_search_sites_isolates_one_blocked_site(self):
        good = FakeSearchAdapter(
            (SiteSearchHit("可用结果", "https://good.example/book", ""),),
            name="good",
            host="good.example",
        )
        blocked = FakeSearchAdapter(
            (SiteSearchHit("不应出现", "https://blocked.example/book", ""),),
            name="blocked",
            host="blocked.example",
        )

        class PartiallyBlockedClient(SearchClient):
            def fetch(self, url: str, **kwargs) -> FetchedPage:
                if "blocked.example" in url:
                    raise NetworkError("模拟站点访问失败")
                return super().fetch(url, **kwargs)

        with patch(
            "book_downloader.search.searchable_adapters",
            return_value=(good, blocked),
        ):
            results = search_sites(PartiallyBlockedClient(""), "测试", limit=5)

        self.assertEqual([result.title for result in results], ["可用结果"])

    def test_search_sites_uses_one_segment_query_and_filters_full_title(self):
        adapter = FallbackSearchAdapter(
            (SiteSearchHit("示例小说续篇 (1-10)", "https://fake.example/book", ""),)
        )
        client = SearchClient("")

        with patch(
            "book_downloader.search.searchable_adapters",
            return_value=(adapter,),
        ):
            results = search_sites(client, "示例小说，续篇", limit=5)

        self.assertEqual([result.title for result in results], ["示例小说续篇 (1-10)"])
        self.assertEqual(len(client.fetched), 1)
        self.assertIn("q=示例小说&limit=", str(client.fetched[0]["url"]))


if __name__ == "__main__":
    unittest.main()
