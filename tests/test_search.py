import unittest

from book_downloader.cli import select_search_result
from book_downloader.errors import DownloaderError
from book_downloader.http import FetchedPage
from book_downloader.search import (
    build_google_query,
    parse_google_results,
    search_google,
)


class SearchClient:
    def __init__(self, html: str):
        self.html = html
        self.fetched: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.fetched.append(url)
        return FetchedPage(url=url, text=self.html)


class SearchTests(unittest.TestCase):
    def test_google_query_is_limited_to_registered_sites(self):
        query = build_google_query("  测试   小说 ")
        self.assertEqual(
            query,
            "(site:trxs.cc OR site:23txxt.com) 测试 小说",
        )

    def test_google_results_decode_redirects_and_filter_unknown_sites(self):
        html = """
        <div class="MjjYud">
          <a href="/url?q=https%3A%2F%2Fwww.trxs.cc%2Ftongren%2F11699%2F147.html&amp;sa=U">
            <h3>目标小说 第147章</h3>
          </a>
          <div class="VwiC3b">这是目标章节的搜索摘要。</div>
        </div>
        <div>
          <a href="https://example.com/book"><h3>不支持的站点</h3></a>
        </div>
        <div>
          <a href="http://23txxt.com/bqg/111084/1.html"><h3>另一个站点结果</h3></a>
          <span class="aCOpRe">来自另一个已纳入站点。</span>
        </div>
        """
        results = parse_google_results(html)

        self.assertEqual([result.site for result in results], ["trxs.cc", "23txxt.com"])
        self.assertEqual(results[0].url, "https://www.trxs.cc/tongren/11699/147.html")
        self.assertEqual(results[0].snippet, "这是目标章节的搜索摘要。")
        self.assertEqual(results[1].title, "另一个站点结果")

    def test_search_uses_google_query_and_returns_limited_results(self):
        html = '<a href="https://www.trxs.cc/tongren/11699.html"><h3>目录</h3></a>'
        client = SearchClient(html)

        results = search_google(client, "测试", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(client.fetched), 1)
        self.assertIn("site%3Atrxs.cc", client.fetched[0])
        self.assertIn("site%3A23txxt.com", client.fetched[0])

    def test_search_result_selection_is_one_based(self):
        html = """
        <a href="https://www.trxs.cc/one.html"><h3>第一条</h3></a>
        <a href="https://www.trxs.cc/two.html"><h3>第二条</h3></a>
        """
        results = parse_google_results(html)

        self.assertEqual(select_search_result(results, 2).title, "第二条")
        with self.assertRaises(DownloaderError):
            select_search_result(results, 3)


if __name__ == "__main__":
    unittest.main()
