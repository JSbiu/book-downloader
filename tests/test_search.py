import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from book_downloader.cli import select_search_result
from book_downloader.errors import DownloaderError, NetworkError
from book_downloader.http import FetchedPage
from book_downloader.models import SiteSearchHit
from book_downloader.search import SearchResult, _merge_result_sets, search_sites
from book_downloader.sites.base import SiteAdapter
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


class SearchTests(unittest.TestCase):
    def test_trxs_search_url_uses_gbk_form_encoding(self):
        url = TrxsCcAdapter().build_search_url("  我的魔法没有上限  ", 10)
        params = parse_qs(urlsplit(url).query, encoding="gb2312")

        self.assertEqual(params["keyboard"], ["我的魔法没有上限"])
        self.assertEqual(params["show"], ["title"])
        self.assertEqual(params["classid"], ["0"])

    def test_trxs_search_request_uses_post_and_gbk_form_encoding(self):
        request = TrxsCcAdapter().build_search_request("我的魔法没有上限", 10)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, "https://www.trxs.cc/e/search/index.php")
        self.assertEqual(request.response_encoding, "gb2312")
        self.assertEqual(
            dict(request.headers)["Content-Type"],
            "application/x-www-form-urlencoded",
        )

        params = parse_qs(request.data.decode("ascii"), encoding="gb2312")
        self.assertEqual(params["keyboard"], ["我的魔法没有上限"])
        self.assertEqual(params["show"], ["title"])
        self.assertEqual(params["classid"], ["0"])

    def test_trxs_results_parse_book_title_and_snippet(self):
        html = """
        <div class="bk">
          <h3><a href="/tongren/11699.html">我的魔法没有上限</a></h3>
          <div class="booknews">作者：测试作者</div>
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
        self.assertEqual(results[0].title, "我的魔法没有上限")
        self.assertEqual(results[0].url, "https://www.trxs.cc/tongren/11699.html")
        self.assertEqual(results[0].snippet, "作者：测试作者")

    def test_trxs_results_parse_card_with_link_wrapping_entire_item(self):
        html = """
        <div class="books m-cols">
          <div class="bk">
            <a href="/tongren/12098.html">
              <div class="infos">
                <h3>你们都是我的翅膀！(1-636)</h3>
                <div class="booknews">作者：田流酒 <label class="date">2026-08-15</label></div>
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
        self.assertEqual(results[0].title, "你们都是我的翅膀！(1-636)")
        self.assertEqual(results[0].url, "https://www.trxs.cc/tongren/12098.html")
        self.assertEqual(results[0].snippet, "作者：田流酒 2026-08-15")

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
        self.assertEqual(len(client.fetched), 1)
        self.assertEqual(client.fetched[0]["method"], "POST")
        self.assertEqual(client.fetched[0]["response_encoding"], "gb2312")
        self.assertEqual(
            client.fetched[0]["url"],
            "https://www.trxs.cc/e/search/index.php",
        )
        self.assertNotIn("23txxt", str(client.fetched))

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


if __name__ == "__main__":
    unittest.main()
