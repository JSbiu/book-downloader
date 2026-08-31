import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from book_downloader.cli import select_library_entry
from book_downloader.errors import DownloaderError
from book_downloader.http import FetchedPage
from book_downloader.library import LibraryEntry, load_library, match_entries
from book_downloader.models import BookPlan, ChapterLink
from book_downloader.runner import download_book
from book_downloader.sites.trxs_cc import TrxsCcAdapter


SAMPLE_TITLE = "示例小说"


class FakeClient:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def fetch(self, url: str) -> FetchedPage:
        return FetchedPage(url=url, text=self.pages[url])


def write_cache_book(
    cache_root: Path,
    site: str,
    title: str,
    catalog_url: str,
    chapter_count: int = 3,
    cached_count: int = 2,
) -> None:
    plan_dir = cache_root / site / "hash123"
    chapter_dir = plan_dir / "chapters"
    chapter_dir.mkdir(parents=True)
    data = {
        "title": title,
        "catalog_url": catalog_url,
        "chapters": [
            {"number": number, "title": f"第{number}章", "url": f"{catalog_url}{number}"}
            for number in range(1, chapter_count + 1)
        ],
    }
    (plan_dir / "book.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    for number in range(1, cached_count + 1):
        (chapter_dir / f"{number:06d}.txt").write_text(
            f"第{number}章\n\n正文", encoding="utf-8"
        )


class LoadLibraryTests(unittest.TestCase):
    def test_merges_cache_and_sidecar_entries(self):
        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            output_dir = Path(temporary) / "outputs"
            output_dir.mkdir()
            write_cache_book(
                cache_root,
                "trxs_cc",
                SAMPLE_TITLE,
                "https://www.trxs.cc/tongren/11699.html",
            )
            sidecar_url = "https://www.trxs.cc/tongren/11699.html"
            (output_dir / f"{SAMPLE_TITLE}.url").write_text(
                sidecar_url + "\n", encoding="utf-8"
            )
            (output_dir / "示例小说二.url").write_text(
                "https://www.bixiange.top/trxs/23940\n", encoding="utf-8"
            )

            entries = load_library(cache_root, output_dir)

            self.assertEqual(len(entries), 2)
            merged = entries[0]
            self.assertEqual(merged.title, SAMPLE_TITLE)
            self.assertEqual(merged.site, "trxs_cc")
            self.assertEqual(merged.catalog_chapters, 3)
            self.assertEqual(merged.cached_chapters, 2)
            self.assertEqual(merged.sources, ("cache", "sidecar"))
            sidecar_only = entries[1]
            self.assertEqual(sidecar_only.title, "示例小说二")
            self.assertEqual(sidecar_only.site, "bixiange")
            self.assertIsNone(sidecar_only.cached_chapters)
            self.assertEqual(sidecar_only.sources, ("sidecar",))

    def test_ignores_broken_cache_metadata_and_empty_sidecars(self):
        with TemporaryDirectory() as temporary:
            cache_root = Path(temporary) / "cache"
            output_dir = Path(temporary) / "outputs"
            broken_dir = cache_root / "trxs_cc" / "broken"
            broken_dir.mkdir(parents=True)
            (broken_dir / "book.json").write_text("{oops", encoding="utf-8")
            output_dir.mkdir()
            (output_dir / "空.url").write_text("   \n", encoding="utf-8")

            self.assertEqual(load_library(cache_root, output_dir), [])


class MatchEntriesTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            LibraryEntry(
                title="姐姐，我也要一起当女仆吗？",
                catalog_url="https://www.trxs.cc/tongren/11699.html",
                site="trxs_cc",
                catalog_chapters=3,
                cached_chapters=3,
                sources=("cache",),
            ),
            LibraryEntry(
                title="示例小说",
                catalog_url="https://www.bixiange.top/trxs/23940",
                site="bixiange",
                catalog_chapters=3,
                cached_chapters=None,
                sources=("sidecar",),
            ),
        ]

    def test_matches_ignore_spaces_and_punctuation(self):
        matches = match_entries(self.entries, "姐姐 我也要一起当女仆吗")
        self.assertEqual([entry.title for entry in matches], ["姐姐，我也要一起当女仆吗？"])

    def test_partial_keyword_matches_and_exact_comes_first(self):
        book = LibraryEntry(
            title="女仆",
            catalog_url="https://example.com/book/1",
            site="trxs_cc",
            catalog_chapters=1,
            cached_chapters=None,
            sources=("sidecar",),
        )
        matches = match_entries([*self.entries, book], "女仆")
        self.assertEqual([entry.title for entry in matches], ["女仆", "姐姐，我也要一起当女仆吗？"])

    def test_blank_keyword_matches_nothing(self):
        self.assertEqual(match_entries(self.entries, "，。！"), [])


class SelectLibraryEntryTests(unittest.TestCase):
    def make_entry(self, title: str) -> LibraryEntry:
        return LibraryEntry(
            title=title,
            catalog_url="https://example.com/book/1",
            site="trxs_cc",
            catalog_chapters=1,
            cached_chapters=None,
            sources=("cache",),
        )

    def test_single_match_is_selected_directly(self):
        entry = self.make_entry("示例小说")
        self.assertIs(select_library_entry([entry]), entry)

    def test_multiple_matches_prompt_for_index(self):
        first = self.make_entry("示例小说一")
        second = self.make_entry("示例小说二")
        with patch("builtins.input", return_value="2"):
            selected = select_library_entry([first, second])
        self.assertIs(selected, second)

    def test_cancel_or_invalid_input_is_handled(self):
        first = self.make_entry("示例小说一")
        second = self.make_entry("示例小说二")
        with patch("builtins.input", side_effect=["9", "x", "0"]):
            with self.assertRaises(DownloaderError):
                select_library_entry([first, second])


class SidecarTests(unittest.TestCase):
    def test_download_book_writes_source_sidecar(self):
        first_url = "https://www.trxs.cc/tongren/11699/1.html"
        client = FakeClient(
            {
                first_url: (
                    "<html><head><title>示例小说 第1章</title></head><body>"
                    '<div class="read_chapterDetail"><p>第一章正文内容，足够长。</p></div>'
                    "</body></html>"
                )
            }
        )
        plan = BookPlan(
            title=SAMPLE_TITLE,
            catalog_url="https://www.trxs.cc/tongren/11699.html",
            chapters=(ChapterLink(1, "第1章", first_url),),
        )

        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "book.txt"
            download_book(
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
            sidecar = output.with_suffix(".url")

            self.assertTrue(sidecar.is_file())
            self.assertEqual(
                sidecar.read_text(encoding="utf-8").strip(),
                "https://www.trxs.cc/tongren/11699.html",
            )
            entries = load_library(
                Path(temporary) / "cache", Path(temporary)
            )
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].catalog_url, plan.catalog_url)


if __name__ == "__main__":
    unittest.main()
