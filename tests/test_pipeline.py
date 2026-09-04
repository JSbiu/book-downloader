import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup

from book_downloader.browser import same_target_document
from book_downloader.cache import BookCache, cache_key
from book_downloader.cli import format_number_ranges
from book_downloader.discovery import discover_book
from book_downloader.errors import AccessBlockedError, DownloaderError
from book_downloader.http import FetchedPage, looks_like_verification_page
from book_downloader.models import BookPlan, Chapter, ChapterLink
from book_downloader.runner import (
    chapter_from_pages,
    download_book,
    fill_cache_gaps,
    merge_from_cache,
    validate_assembled,
)
from book_downloader.sites.bixiange import BixiangeAdapter
from book_downloader.sites.shuba import ShubaAdapter
from book_downloader.sites.trxs_cc import TrxsCcAdapter
from book_downloader.sites.txxt import TxxtAdapter


SAMPLE_BOOK_TITLE = "示例小说"


def chapter_html(number: int, body: str, next_url: str | None = None) -> str:
    next_link = f'<a href="{next_url}">下一页</a>' if next_url else ""
    return f"""
    <html><head><title>{SAMPLE_BOOK_TITLE} 第{number}章</title></head><body>
      <h1>{SAMPLE_BOOK_TITLE} 第{number}章</h1>
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
    def test_bixiange_book_page_discovers_catalog(self):
        catalog_url = "https://www.bixiange.top/xhqh/12345/"
        catalog = """
        <div class="desc"><h1>示例小说(1-3)</h1></div>
        <div class="catalog">
          <a href="/xhqh/12345/index/1.html">第1节</a>
          <a href="/xhqh/12345/index/2.html">第2节</a>
          <a href="/xhqh/12345/index/3.html">第3节</a>
        </div>
        """

        client = FakeClient({catalog_url: catalog})
        plan = discover_book(client, BixiangeAdapter(), catalog_url)

        self.assertEqual(plan.title, "示例小说")
        self.assertEqual(plan.catalog_url, catalog_url)
        self.assertEqual([item.number for item in plan.chapters], [1, 2, 3])

    def test_bixiange_first_chapter_removes_intro_only(self):
        chapter_url = "https://www.bixiange.top/xhqh/12345/index/1.html"
        html = """
        <div class="article"><h1>示例小说 第1节</h1>
          <div id="mycontent">
            <p>示例小说</p>
            <p>作者：示例作者</p>
            <p>这是书籍简介。</p>
            <p>第一章 示例章节</p>
            <p>第一段正文。</p>
          </div>
        </div>
        """
        adapter = BixiangeAdapter()
        client = FakeClient({chapter_url: html})
        link = ChapterLink(number=1, title="第1节", url=chapter_url)

        chapter = chapter_from_pages(
            client=client,
            adapter=adapter,
            link=link,
            book_title="示例小说",
            max_pages=5,
        )

        self.assertEqual(chapter.title, "第一章 示例章节")
        self.assertEqual(chapter.content, "第一段正文。")

    def test_bixiange_keeps_later_embedded_chapter_headings(self):
        raw = Chapter(
            3,
            "第3节",
            "前一段正文。\n第三章 示例章节标题\n后一段正文。",
        )

        cleaned = BixiangeAdapter().sanitize_chapter(
            raw,
            book_title="示例小说",
            first_chapter=False,
        )

        self.assertEqual(cleaned.title, "第3节")
        self.assertIn("第三章 示例章节标题", cleaned.content)

    def test_bixiange_removes_section_labels_and_known_pollution(self):
        raw = Chapter(
            2,
            "第2节",
            "第2节\n正文第一段。\n?9提供最快\n正文第二段。",
        )

        cleaned = BixiangeAdapter().sanitize_chapter(
            raw,
            book_title="示例小说",
            first_chapter=False,
        )

        self.assertNotIn("第2节", cleaned.content)
        self.assertNotIn("提供最快", cleaned.content)
        self.assertEqual(cleaned.content, "正文第一段。\n正文第二段。")

    def test_bixiange_reassembles_real_chapters_from_section_pages(self):
        chapters = [
            Chapter(1, "第一章 示例章节", "第一章正文。"),
            Chapter(
                2,
                "第2节",
                "第一章续文。\n第二章 示例标题\n第二章正文。",
            ),
            Chapter(3, "第3节", "第二章续文。\n第三章 另一个标题\n第三章正文。"),
        ]

        assembled = BixiangeAdapter().assemble_chapters(
            chapters,
            book_title="示例小说",
        )

        self.assertEqual(
            [chapter.title for chapter in assembled],
            ["第一章 示例章节", "第二章 示例标题", "第三章 另一个标题"],
        )
        self.assertIn("第一章续文。", assembled[0].content)
        self.assertNotIn("第二章 示例标题", assembled[0].content)
        self.assertIn("第二章续文。", assembled[1].content)
        self.assertNotIn("第2节", "\n".join(chapter.block for chapter in assembled))

    def test_shuba_book_page_discovers_catalog(self):
        catalog_url = "https://www.69shuba.com/book/12345/"
        catalog = """
        <h1>示例小说</h1>
        <div class="catalog"><ul>
          <li><a href="/txt/12345/10001">第1章 示例章节一</a></li>
          <li><a href="/txt/12345/10002">第2章 示例章节二</a></li>
        </ul></div>
        """

        client = FakeClient({catalog_url: catalog})
        plan = discover_book(client, ShubaAdapter(), catalog_url)

        self.assertEqual(plan.title, "示例小说")
        self.assertEqual(plan.catalog_url, catalog_url)
        self.assertEqual([item.number for item in plan.chapters], [1, 2])
        self.assertEqual(
            plan.chapters[0].url,
            "https://www.69shuba.com/txt/12345/10001",
        )

    def test_shuba_chapter_removes_page_scaffolding(self):
        chapter_url = "https://www.69shuba.com/txt/12345/10001"
        html = """
        <html><head><title>示例小说-第1章 示例章节</title></head>
        <body>
          <div class="txtnav">
            <h1>第1章 示例章节</h1>
            <p>2026-01-01 作者：示例作者</p>
            <p>第1章 示例章节</p>
            <p>第一段正文。</p>
            <p>第二段正文。</p>
            <p>(本章完)</p>
            <a href="/txt/12345/10002">下一章</a>
          </div>
        </body></html>
        """

        client = FakeClient({chapter_url: html})
        chapter = chapter_from_pages(
            client=client,
            adapter=ShubaAdapter(),
            link=ChapterLink(1, "第1章 示例章节", chapter_url),
            book_title="示例小说",
            max_pages=5,
        )

        self.assertEqual(chapter.title, "第1章 示例章节")
        self.assertEqual(chapter.content, "第一段正文。\n第二段正文。")

    def test_shuba_chapter_url_guesses_catalog(self):
        self.assertEqual(
            ShubaAdapter().guess_catalog_url(
                "https://www.69shuba.com/txt/12345/10001"
            ),
            "https://www.69shuba.com/book/12345/",
        )

    def test_shuba_detail_page_shares_cache_identity_with_catalog(self):
        canonical_catalog = "https://www.69shuba.com/book/89702/"
        self.assertEqual(
            ShubaAdapter().canonical_catalog_url(
                "https://www.69shuba.com/book/89702.htm"
            ),
            canonical_catalog,
        )
        self.assertEqual(
            ShubaAdapter().canonical_catalog_url(canonical_catalog),
            canonical_catalog,
        )

        chapter_url = "https://www.69shuba.com/txt/89702/10001"
        client = FakeClient(
            {
                chapter_url: (
                    "<html><head><title>示例小说-第1章 示例章节</title></head>"
                    "<body><div class='txtnav'><h1>第1章 示例章节</h1>"
                    "<p>第一段正文。</p><p>(本章完)</p></div></body></html>"
                )
            }
        )
        plan = BookPlan(
            title=SAMPLE_BOOK_TITLE,
            catalog_url="https://www.69shuba.com/book/89702.htm",
            chapters=(ChapterLink(1, "第1章 示例章节", chapter_url),),
        )
        with TemporaryDirectory() as temporary:
            download_book(
                client=client,
                adapter=ShubaAdapter(),
                plan=plan,
                output=Path(temporary) / "out.txt",
                output_dir=Path(temporary),
                cache_root=Path(temporary) / "cache",
                delay=0,
                max_pages=5,
                refresh=False,
            )
            expected_dir = (
                Path(temporary) / "cache" / "69shuba" / cache_key(canonical_catalog)
            )
            plan_data = json.loads(
                (expected_dir / "book.json").read_text(encoding="utf-8")
            )
            chapter_file = expected_dir / "chapters" / "000001.txt"
            self.assertEqual(plan_data["catalog_url"], canonical_catalog)
            self.assertTrue(chapter_file.is_file())

    def test_chapter_page_discovers_catalog(self):
        chapter_url = "https://www.trxs.cc/tongren/11699/147.html"
        catalog_url = "https://www.trxs.cc/tongren/11699.html"
        catalog = """
        <h1>示例小说</h1>
        <div id="list"><dl>
          <dd><a href="/tongren/11699/146.html">第146章 示例章节一</a></dd>
          <dd><a href="/tongren/11699/147.html">第147章 示例章节二</a></dd>
          <dd><a href="/tongren/11699/148.html">第148章 示例章节三</a></dd>
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
        self.assertEqual(plan.chapters[1].title, "第147章 示例章节二")

    def test_continuation_pages_are_joined(self):
        first_url = "https://www.trxs.cc/tongren/11699/147.html"
        second_url = "https://www.trxs.cc/tongren/11699/147_2.html"
        client = FakeClient(
            {
                first_url: chapter_html(
                    147,
                    "示例小说 第147章\n第147章 示例章节标题\n第一分页正文。",
                    second_url,
                ),
                second_url: chapter_html(147, "第二分页正文，应该接在第一分页后面。"),
            }
        )
        link = ChapterLink(147, "第147章 示例章节标题", first_url)
        chapter = chapter_from_pages(
            client=client,
            adapter=TrxsCcAdapter(),
            link=link,
            book_title=SAMPLE_BOOK_TITLE,
            max_pages=5,
        )
        self.assertEqual(chapter.title, "第147章 示例章节标题")
        self.assertNotIn(f"{SAMPLE_BOOK_TITLE} 第147章", chapter.content)
        self.assertIn("第一分页正文", chapter.content)
        self.assertIn("第二分页正文", chapter.content)

    def test_trxs_sanitizer_removes_intro_and_duplicate_header(self):
        raw = Chapter(
            1,
            f"{SAMPLE_BOOK_TITLE} 第1章",
            f"{SAMPLE_BOOK_TITLE} 第1章\n作者：示例作者\n简介：示例简介\n正文卷\n"
            "第1章 示例章节标题\n真正正文。",
        )
        cleaned = TrxsCcAdapter().sanitize_chapter(raw, SAMPLE_BOOK_TITLE, True)
        self.assertEqual(cleaned.title, "第1章 示例章节标题")
        self.assertNotIn("作者：", cleaned.content)
        self.assertNotIn("简介：", cleaned.content)
        self.assertNotIn("正文卷", cleaned.content)
        self.assertNotIn(f"{SAMPLE_BOOK_TITLE} 第1章", cleaned.content)
        self.assertIn("真正正文", cleaned.content)

    def test_trxs_title_removes_site_author_suffix(self):
        soup = BeautifulSoup(
            "<title>示例小说(示例作者)_同人小说网</title>",
            "html.parser",
        )
        title = TrxsCcAdapter().extract_book_title(soup, "https://www.trxs.cc/tongren/11699.html")
        self.assertEqual(title, SAMPLE_BOOK_TITLE)

    def test_trxs_bare_section_title_becomes_chapter(self):
        raw = Chapter(232, "第232节", "正文第一段。\n正文第二段。")
        cleaned = TrxsCcAdapter().sanitize_chapter(raw, SAMPLE_BOOK_TITLE, False)
        self.assertEqual(cleaned.title, "第232章")
        self.assertEqual(cleaned.content, "正文第一段。\n正文第二段。")

    def test_trxs_section_title_with_name_keeps_name(self):
        raw = Chapter(5, "第5节 示例章节名", "正文第一段。")
        cleaned = TrxsCcAdapter().sanitize_chapter(raw, SAMPLE_BOOK_TITLE, False)
        self.assertEqual(cleaned.title, "第5节 示例章节名")

    def test_trxs_assembly_resegments_by_embedded_headings(self):
        blocks = [
            Chapter(1, "第1章 收租，然后遇见医学奇迹", "第一章正文。"),
            Chapter(
                2,
                "第2章",
                "第一章续文。\n第2章 没钱交房租的话，就给我当女仆还债好了\n第二章正文。",
            ),
            Chapter(
                3,
                "第3章",
                "第二章续文。\n第3章 应该...不会再出什么意外了吧?\n第三章正文。",
            ),
            Chapter(4, "第4章", "第三章续文。"),
            Chapter(5, "第5章", "无标记分页正文。"),
        ]

        assembled = TrxsCcAdapter().assemble_chapters(blocks, SAMPLE_BOOK_TITLE)

        # 最后一个带名标题在分页 3，真实章节跨度为 1 个分页：分页 4 在
        # 跨度内并入第3章，分页 5 超出跨度才各自成章续号。
        self.assertEqual(
            [chapter.title for chapter in assembled],
            [
                "第1章 收租，然后遇见医学奇迹",
                "第2章 没钱交房租的话，就给我当女仆还债好了",
                "第3章 应该...不会再出什么意外了吧?",
                "第4章",
            ],
        )
        self.assertEqual([chapter.number for chapter in assembled], [1, 2, 3, 4])
        self.assertIn("第一章续文。", assembled[0].content)
        self.assertNotIn("第二章正文。", assembled[0].content)
        self.assertIn("第二章续文。", assembled[1].content)
        self.assertIn("第三章续文。", assembled[2].content)
        self.assertIn("无标记分页正文。", assembled[3].content)

    def test_trxs_assembly_merges_trailing_pages_of_last_chapter(self):
        # 连载中书籍的末页往往只是最后一章的延续，不该被切成新章。
        blocks = [
            Chapter(1, "第1章 示例开头", "第一章正文。"),
            Chapter(2, "第2章", "延续中的正文，对话还没结束，"),
        ]

        assembled = TrxsCcAdapter().assemble_chapters(blocks, SAMPLE_BOOK_TITLE)

        self.assertEqual(len(assembled), 1)
        self.assertEqual(assembled[0].title, "第1章 示例开头")
        self.assertIn("延续中的正文", assembled[0].content)

    def test_trxs_assembly_without_embedded_headings_keeps_pages(self):
        blocks = [
            Chapter(1, "第1章", "正文一。"),
            Chapter(2, "第2章", "正文二。"),
        ]

        assembled = TrxsCcAdapter().assemble_chapters(blocks, SAMPLE_BOOK_TITLE)

        self.assertEqual(
            [chapter.title for chapter in assembled], ["第1章", "第2章"]
        )
        self.assertEqual([chapter.number for chapter in assembled], [1, 2])

    def test_trxs_heading_detection_handles_junk_prefix_and_promo(self):
        adapter = TrxsCcAdapter()
        # 杂质前缀 + 推广语后缀
        self.assertEqual(
            adapter._real_heading("? 第106章 我没说要给你买啊（求月票~）"),
            (106, "第106章 我没说要给你买啊"),
        )
        # 章号后紧贴名称
        self.assertEqual(
            adapter._real_heading("第32章「你好，江渝白！」"),
            (32, "第32章 「你好，江渝白！」"),
        )
        # 无名称不是标题
        self.assertIsNone(adapter._real_heading("第72章"))
        # 句中提及不算标题
        self.assertIsNone(adapter._real_heading("他翻到第3章才发现线索"))

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
            "示例作品",
            "1.示例章节 (第1/2页)\n"
            "第一页正文。\n"
            "（本章未完，请点击下一页继续阅读）\n"
            "1.示例章节 (第2/2页)\n"
            "第二页正文。",
        )
        cleaned = TxxtAdapter().sanitize_chapter(raw, "示例作品", True)
        self.assertEqual(cleaned.title, "1.示例章节")
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
            title=SAMPLE_BOOK_TITLE,
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

    def test_download_book_can_limit_chapters(self):
        urls = [
            f"https://www.trxs.cc/tongren/11699/{number}.html"
            for number in range(1, 4)
        ]
        client = FakeClient(
            {
                urls[0]: chapter_html(1, "第一章正文内容足够长。"),
                urls[1]: chapter_html(2, "第二章正文内容足够长。"),
                urls[2]: chapter_html(3, "第三章不应被请求。"),
            }
        )
        plan = BookPlan(
            title=SAMPLE_BOOK_TITLE,
            catalog_url="https://www.trxs.cc/tongren/11699.html",
            chapters=tuple(
                ChapterLink(number, f"第{number}章", url)
                for number, url in enumerate(urls, start=1)
            ),
        )

        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "sample.txt"
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
                max_chapters=2,
            )
            text = output.read_text(encoding="utf-8")

        self.assertEqual(client.fetched, urls[:2])
        self.assertEqual(result.total_chapters, 2)
        self.assertEqual(result.skipped_chapters, 1)
        self.assertFalse(result.interrupted)
        self.assertNotIn("第三章不应被请求", text)

    def test_download_book_handles_ctrl_c_and_writes_partial_output(self):
        first_url = "https://www.trxs.cc/tongren/11699/1.html"
        second_url = "https://www.trxs.cc/tongren/11699/2.html"
        third_url = "https://www.trxs.cc/tongren/11699/3.html"

        class InterruptingClient(FakeClient):
            def fetch(self, url: str) -> FetchedPage:
                self.fetched.append(url)
                if url == second_url:
                    raise KeyboardInterrupt
                return FetchedPage(url=url, text=self.pages[url])

        client = InterruptingClient(
            {
                first_url: chapter_html(1, "第一章正文内容足够长。"),
                third_url: chapter_html(3, "第三章不应被请求。"),
            }
        )
        plan = BookPlan(
            title=SAMPLE_BOOK_TITLE,
            catalog_url="https://www.trxs.cc/tongren/11699.html",
            chapters=(
                ChapterLink(1, "第1章", first_url),
                ChapterLink(2, "第2章", second_url),
                ChapterLink(3, "第3章", third_url),
            ),
        )

        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "partial.txt"
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

        self.assertEqual(client.fetched, [first_url, second_url])
        self.assertEqual(result.total_chapters, 1)
        self.assertEqual(result.failed_chapters, ())
        self.assertTrue(result.interrupted)
        self.assertIn("第一章正文内容", text)

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
            title=SAMPLE_BOOK_TITLE,
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

    def test_merge_from_cache_uses_only_cache(self):
        catalog_url = "https://www.trxs.cc/tongren/11699.html"
        urls = [
            f"https://www.trxs.cc/tongren/11699/{number}.html"
            for number in range(1, 4)
        ]
        client = FakeClient(
            {
                urls[0]: chapter_html(1, "第一章正文内容足够长，用于下载。"),
                urls[1]: chapter_html(2, "第二章正文内容足够长，用于下载。"),
                urls[2]: chapter_html(3, "第三章正文内容足够长，用于下载。"),
            }
        )
        plan = BookPlan(
            title=SAMPLE_BOOK_TITLE,
            catalog_url=catalog_url,
            chapters=tuple(
                ChapterLink(number, f"第{number}章", url)
                for number, url in enumerate(urls, start=1)
            ),
        )

        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            download_book(
                client=client,
                adapter=TrxsCcAdapter(),
                plan=plan,
                output=Path(temporary) / "first.txt",
                output_dir=Path(temporary),
                cache_root=cache_root,
                delay=0,
                max_pages=5,
                refresh=False,
            )

            class NoNetworkClient:
                def fetch(self, url: str) -> FetchedPage:
                    raise AssertionError("离线合并不应发起网络请求")

            result = merge_from_cache(
                adapter=TrxsCcAdapter(),
                cache_root=cache_root,
                catalog_url=catalog_url,
                output=Path(temporary) / "offline.txt",
                output_dir=Path(temporary),
            )
            text = result.output.read_text(encoding="utf-8")

        self.assertEqual(result.total_chapters, 3)
        self.assertEqual(result.failed_chapters, ())
        self.assertIn("第一章正文内容", text)
        self.assertIn("第三章正文内容", text)

    def test_merge_from_cache_reports_missing_chapters(self):
        catalog_url = "https://www.trxs.cc/tongren/11699.html"
        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache = BookCache(cache_root, "trxs_cc", catalog_url)
            plan = BookPlan(
                title=SAMPLE_BOOK_TITLE,
                catalog_url=catalog_url,
                chapters=(
                    ChapterLink(1, "第1章", "https://www.trxs.cc/tongren/11699/1.html"),
                    ChapterLink(2, "第2章", "https://www.trxs.cc/tongren/11699/2.html"),
                ),
            )
            cache.save_plan(plan)
            cache.write(Chapter(1, "第1章", "第一章正文内容。"))

            result = merge_from_cache(
                adapter=TrxsCcAdapter(),
                cache_root=cache_root,
                catalog_url=catalog_url,
                output=Path(temporary) / "partial.txt",
                output_dir=Path(temporary),
            )

        self.assertEqual(result.total_chapters, 1)
        self.assertEqual(result.failed_chapters, ())

    def test_merge_from_cache_without_plan_record_fails(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaises(DownloaderError):
                merge_from_cache(
                    adapter=TrxsCcAdapter(),
                    cache_root=Path(temporary) / "cache",
                    catalog_url="https://www.trxs.cc/tongren/11699.html",
                    output=Path(temporary) / "none.txt",
                    output_dir=Path(temporary),
                )

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


class FillGapsTests(unittest.TestCase):
    def make_plan(self, catalog_url: str, count: int = 3) -> BookPlan:
        return BookPlan(
            title=SAMPLE_BOOK_TITLE,
            catalog_url=catalog_url,
            chapters=tuple(
                ChapterLink(
                    number,
                    f"第{number}章",
                    f"https://www.trxs.cc/tongren/11699/{number}.html",
                )
                for number in range(1, count + 1)
            ),
        )

    def test_fill_gaps_only_requests_missing_chapters(self):
        catalog_url = "https://www.trxs.cc/tongren/11699.html"
        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache = BookCache(cache_root, "trxs_cc", catalog_url)
            plan = self.make_plan(catalog_url)
            cache.save_plan(plan)
            cache.write(Chapter(1, "第1章", "第一章正文内容，已缓存。"))
            cache.write(Chapter(3, "第3章", "第三章正文内容，已缓存。"))

            client = FakeClient(
                {
                    "https://www.trxs.cc/tongren/11699/2.html": chapter_html(
                        2, "第二章正文内容，用于补齐。"
                    ),
                }
            )
            result = fill_cache_gaps(
                client=client,
                adapter=TrxsCcAdapter(),
                cache_root=cache_root,
                catalog_url=catalog_url,
                output=Path(temporary) / "filled.txt",
                output_dir=Path(temporary),
                delay=0,
                max_pages=5,
                retry_delay=0,
                retry_rounds=0,
            )
            text = result.output.read_text(encoding="utf-8")

        self.assertEqual(client.fetched, ["https://www.trxs.cc/tongren/11699/2.html"])
        self.assertEqual(result.total_chapters, 3)
        self.assertIn("第二章正文内容", text)

    def test_fill_gaps_retries_blocked_rounds(self):
        catalog_url = "https://www.trxs.cc/tongren/11699.html"

        class BlockedClient(FakeClient):
            def __init__(self):
                super().__init__({})
                self.calls = 0

            def fetch(self, url: str) -> FetchedPage:
                self.calls += 1
                raise AccessBlockedError("站点要求验证")

        client = BlockedClient()
        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            cache = BookCache(cache_root, "trxs_cc", catalog_url)
            plan = self.make_plan(catalog_url, count=2)
            cache.save_plan(plan)
            cache.write(Chapter(1, "第1章", "第一章正文内容，已缓存。"))

            result = fill_cache_gaps(
                client=client,
                adapter=TrxsCcAdapter(),
                cache_root=cache_root,
                catalog_url=catalog_url,
                output=Path(temporary) / "blocked.txt",
                output_dir=Path(temporary),
                delay=0,
                max_pages=5,
                retry_delay=0,
                retry_rounds=2,
            )

        self.assertEqual(client.calls, 3)
        self.assertEqual(result.total_chapters, 1)


class AssembledValidationTests(unittest.TestCase):
    def test_clean_book_has_no_findings(self):
        chapters = [
            Chapter(1, "第1章 开头", f"{'正文内容' * 20}，足够长的第一段。"),
            Chapter(2, "第2章 继续", f"{'正文内容' * 20}，足够长的第二段。"),
        ]
        self.assertEqual(validate_assembled(chapters), [])

    def test_validation_reports_duplicate_and_short_chapters(self):
        chapters = [
            Chapter(1, "第1章 开头", f"{'正文内容' * 20}，足够长。"),
            Chapter(2, "第1章 开头", f"{'正文内容' * 20}，标题重复。"),
            Chapter(4, "第4章 跳号", "太短。"),
        ]
        findings = validate_assembled(chapters)
        joined = "\n".join(findings)
        self.assertIn("重复章节标题", joined)
        self.assertIn("章节编号不连续", joined)
        self.assertIn("疑似残缺章节", joined)


if __name__ == "__main__":
    unittest.main()
