import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup

from book_downloader.browser import same_target_document
from book_downloader.cache import BookCache
from book_downloader.cli import format_number_ranges
from book_downloader.discovery import discover_book
from book_downloader.http import FetchedPage, looks_like_verification_page
from book_downloader.models import BookPlan, Chapter, ChapterLink
from book_downloader.runner import chapter_from_pages, download_book
from book_downloader.sites.trxs_cc import TrxsCcAdapter
from book_downloader.sites.txxt import TxxtAdapter


BOOK_TITLE = "姐姐，我也要一起当女仆吗？"


def chapter_html(number: int, body: str, next_url: str | None = None) -> str:
    next_link = f'<a href="{next_url}">下一页</a>' if next_url else ""
    return f"""
    <html><head><title>{BOOK_TITLE} 第{number}章</title></head><body>
      <h1>{BOOK_TITLE} 第{number}章</h1>
      <div class="read_chapterDetail"><p>{body}</p>{next_link}</div>
    </body></html>
    """


class FakeClient:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.fetched.append(url)
        return FetchedPage(url=url, text=self.pages[url])


class PipelineTests(unittest.TestCase):
    def test_chapter_page_discovers_catalog(self):
        chapter_url = "https://www.trxs.cc/tongren/11699/147.html"
        catalog_url = "https://www.trxs.cc/tongren/11699.html"
        catalog = """
        <h1>姐姐，我也要一起当女仆吗？</h1>
        <div id="list"><dl>
          <dd><a href="/tongren/11699/146.html">第146章 情侣款</a></dd>
          <dd><a href="/tongren/11699/147.html">第147章 你摸我腿干嘛？</a></dd>
          <dd><a href="/tongren/11699/148.html">第148章 原来我这样都能绷住吗？</a></dd>
        </dl></div>
        """
        input_page = (
            f'<a href="{catalog_url}">目录</a>'
            '<a href="/tongren/8911.html">另一本推荐小说</a>'
            '<div class="read_chapterDetail"><p>章节正文内容足够长，用来测试从章节页找到目录链接。</p></div>'
        )
        client = FakeClient({chapter_url: input_page, catalog_url: catalog})
        plan = discover_book(client, TrxsCcAdapter(), chapter_url)
        self.assertEqual(plan.catalog_url, catalog_url)
        self.assertEqual([item.number for item in plan.chapters], [1, 2, 3])
        self.assertEqual(plan.chapters[1].title, "第147章 你摸我腿干嘛？")

    def test_continuation_pages_are_joined(self):
        first_url = "https://www.trxs.cc/tongren/11699/147.html"
        second_url = "https://www.trxs.cc/tongren/11699/147_2.html"
        client = FakeClient(
            {
                first_url: chapter_html(
                    147,
                    "姐姐，我也要一起当女仆吗？ 第147章\n第147章 你摸我腿干嘛？！\n第一分页正文。",
                    second_url,
                ),
                second_url: chapter_html(147, "第二分页正文，应该接在第一分页后面。"),
            }
        )
        link = ChapterLink(147, "第147章 你摸我腿干嘛？！", first_url)
        chapter = chapter_from_pages(
            client=client,
            adapter=TrxsCcAdapter(),
            link=link,
            book_title=BOOK_TITLE,
            max_pages=5,
        )
        self.assertEqual(chapter.title, "第147章 你摸我腿干嘛？！")
        self.assertNotIn(f"{BOOK_TITLE} 第147章", chapter.content)
        self.assertIn("第一分页正文", chapter.content)
        self.assertIn("第二分页正文", chapter.content)

    def test_trxs_sanitizer_removes_intro_and_duplicate_header(self):
        raw = Chapter(
            1,
            f"{BOOK_TITLE} 第1章",
            f"{BOOK_TITLE} 第1章\n作者：电子熊猫\n简介：简介内容\n正文卷\n"
            "第1章 收租，然后遇见医学奇迹\n真正正文。",
        )
        cleaned = TrxsCcAdapter().sanitize_chapter(raw, BOOK_TITLE, True)
        self.assertEqual(cleaned.title, "第1章 收租，然后遇见医学奇迹")
        self.assertNotIn("作者：", cleaned.content)
        self.assertNotIn("简介：", cleaned.content)
        self.assertNotIn("正文卷", cleaned.content)
        self.assertNotIn(f"{BOOK_TITLE} 第1章", cleaned.content)
        self.assertIn("真正正文", cleaned.content)

    def test_trxs_title_removes_site_author_suffix(self):
        soup = BeautifulSoup(
            "<title>姐姐，我也要一起当女仆吗？(电子熊猫)_同人小说网</title>",
            "html.parser",
        )
        title = TrxsCcAdapter().extract_book_title(soup, "https://www.trxs.cc/tongren/11699.html")
        self.assertEqual(title, BOOK_TITLE)

    def test_verification_page_is_detected(self):
        self.assertTrue(looks_like_verification_page("<title>Just a moment...</title>"))
        self.assertTrue(looks_like_verification_page("正在进行安全验证，请稍候"))
        self.assertFalse(looks_like_verification_page("<title>普通章节</title>正文"))

    def test_txxt_numbered_continuation_is_detected(self):
        soup = BeautifulSoup(
            '<a href="/bqg/111084/44601734_2.html">2</a>',
            "html.parser",
        )
        next_url = TxxtAdapter().find_next_page(
            soup, "http://www.23txxt.com/bqg/111084/44601734.html"
        )
        self.assertEqual(
            next_url,
            "http://www.23txxt.com/bqg/111084/44601734_2.html",
        )

    def test_txxt_continuation_never_crosses_into_next_chapter(self):
        soup = BeautifulSoup(
            '<a href="/bqg/111084/44601735.html">下一页</a>',
            "html.parser",
        )
        next_url = TxxtAdapter().find_next_page(
            soup, "http://www.23txxt.com/bqg/111084/44601734_2.html"
        )
        self.assertIsNone(next_url)

    def test_txxt_sanitizer_uses_actual_title_and_removes_page_scaffolding(self):
        raw = Chapter(
            1,
            "二三书库",
            "1.变成蘑菇的公爵千金 (第1/2页)\n"
            "第一页正文。\n"
            "（本章未完，请点击下一页继续阅读）\n"
            "1.变成蘑菇的公爵千金 (第2/2页)\n"
            "第二页正文。",
        )
        cleaned = TxxtAdapter().sanitize_chapter(raw, "这个地下城长蘑菇了", True)
        self.assertEqual(cleaned.title, "1.变成蘑菇的公爵千金")
        self.assertEqual(cleaned.content, "第一页正文。\n第二页正文。")

    def test_txxt_catalog_is_sorted_and_author_notes_are_filtered(self):
        soup = BeautifulSoup(
            """
            <div id="list">
              <a href="/bqg/111084/850.html">850.最后</a>
              <a href="/bqg/111084/8.html">8.第八章</a>
              <a href="/bqg/111084/8-note.html">8点更</a>
              <a href="/bqg/111084/2.html">2.第二章</a>
              <a href="/bqg/111084/42988487.html">开始阅读</a>
              <a href="/bqg/111084/242.html">242通用语MAX</a>
              <a href="/bqg/111084/extra.html">番外·后记</a>
              <a href="/bqg/111084/999999.html">请假条</a>
              <a href="/bqg/111084/999998.html">月票番外说明</a>
            </div>
            """,
            "html.parser",
        )
        links = TxxtAdapter().extract_chapter_links(
            soup, "http://www.23txxt.com/bqg/111084/"
        )
        self.assertEqual(
            [link.title for link in links],
            ["开始阅读", "2.第二章", "8.第八章", "242通用语MAX", "850.最后", "番外·后记"],
        )

    def test_download_book_writes_merged_output_from_cache(self):
        first_url = "https://www.trxs.cc/tongren/11699/1.html"
        second_url = "https://www.trxs.cc/tongren/11699/2.html"
        client = FakeClient(
            {
                first_url: chapter_html(1, "第1章 第一章正文内容，足够长，写入合并文件。"),
                second_url: chapter_html(2, "第二章正文内容，也会写入合并文件。"),
            }
        )
        plan = BookPlan(
            title=BOOK_TITLE,
            catalog_url="https://www.trxs.cc/tongren/11699.html",
            chapters=(
                ChapterLink(1, "第1章 第一章", first_url),
                ChapterLink(2, "第2章 第二章", second_url),
            ),
        )

        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "book.txt"
            result = download_book(
                client=client,
                adapter=TrxsCcAdapter(),
                plan=plan,
                output=output,
                output_dir=Path(temporary),
                cache_root=Path(temporary) / "cache",
                delay=0,
                max_pages=5,
                refresh=False,
            )
            text = output.read_text(encoding="utf-8")

        self.assertEqual(result.total_chapters, 2)
        self.assertIn("第1章 第一章", text)
        self.assertIn("第一章正文内容", text)
        self.assertIn("第2章 第二章", text)
        self.assertIn("第二章正文内容", text)

    def test_cache_moves_chapters_when_number_mapping_changes(self):
        with TemporaryDirectory() as temporary:
            cache = BookCache(
                Path(temporary) / "cache",
                "23txxt",
                "http://www.23txxt.com/bqg/111084/",
            )
            old_plan = BookPlan(
                title="测试",
                catalog_url="http://www.23txxt.com/bqg/111084/",
                chapters=(
                    ChapterLink(1, "开始阅读", "http://example/1.html"),
                    ChapterLink(2, "番外", "http://example/extra.html"),
                    ChapterLink(3, "2.第二章", "http://example/2.html"),
                ),
            )
            cache.save_plan(old_plan)
            cache.write(Chapter(1, "第一章", "第一章旧缓存"))
            cache.write(Chapter(2, "番外", "番外旧缓存"))
            cache.write(Chapter(3, "第二章", "第二章旧缓存"))

            new_plan = BookPlan(
                title="测试",
                catalog_url=old_plan.catalog_url,
                chapters=(
                    old_plan.chapters[0],
                    ChapterLink(2, "2.第二章", "http://example/2.html"),
                    ChapterLink(3, "番外", "http://example/extra.html"),
                ),
            )
            migrated = cache.save_plan(new_plan)

            self.assertEqual(migrated, 3)
            self.assertIn("第一章旧缓存", cache.read(1) or "")
            self.assertIn("第二章旧缓存", cache.read(2) or "")
            self.assertIn("番外旧缓存", cache.read(3) or "")
            self.assertTrue(list(cache.path.glob("chapters.stale-*")))

    def test_runner_stops_after_repeated_failures_and_writes_partial_output(self):
        urls = [f"https://www.trxs.cc/tongren/11699/{number}.html" for number in range(1, 5)]
        client = FakeClient(
            {
                urls[0]: chapter_html(1, "第一章正文内容足够长。"),
                urls[1]: "<html><body>失败</body></html>",
                urls[2]: "<html><body>失败</body></html>",
                urls[3]: chapter_html(4, "第四章不应被请求。"),
            }
        )
        plan = BookPlan(
            title=BOOK_TITLE,
            catalog_url="https://www.trxs.cc/tongren/11699.html",
            chapters=tuple(
                ChapterLink(number, f"第{number}章", url)
                for number, url in enumerate(urls, start=1)
            ),
        )

        with TemporaryDirectory() as temporary:
            result = download_book(
                client=client,
                adapter=TrxsCcAdapter(),
                plan=plan,
                output=Path(temporary) / "partial.txt",
                output_dir=Path(temporary),
                cache_root=Path(temporary) / "cache",
                delay=0,
                max_pages=5,
                refresh=False,
                max_consecutive_failures=2,
            )

        self.assertEqual(client.fetched, urls[:3])
        self.assertEqual(result.total_chapters, 1)
        self.assertEqual(result.failed_chapters, (2, 3, 4))

    def test_compact_failed_ranges(self):
        self.assertEqual(format_number_ranges((2, 3, 4, 8, 10, 11)), "2–4, 8, 10–11")

    def test_browser_target_matching_allows_normal_redirects_only(self):
        self.assertTrue(
            same_target_document(
                "https://23txxt.com/bqg/111084/1.html",
                "http://www.23txxt.com/bqg/111084/1.html",
            )
        )
        self.assertFalse(
            same_target_document(
                "http://www.23txxt.com/bqg/111084/2.html",
                "http://www.23txxt.com/bqg/111084/1.html",
            )
        )
        self.assertTrue(
            same_target_document(
                "https://www.google.com.hk/search?q=test",
                "https://www.google.com/search?q=test",
            )
        )
        self.assertFalse(
            same_target_document(
                "https://www.google.com.hk/sorry/index",
                "https://www.google.com/search?q=test",
            )
        )


if __name__ == "__main__":
    unittest.main()
