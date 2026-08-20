from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from ..models import Chapter, ChapterLink
from .base import SiteAdapter
from .common import (
    absolute_url,
    chapter_number_from_text,
    clean_lines,
    same_origin,
)


PAGE_HEADING = re.compile(
    r"^\s*(?P<title>.+?)\s*[（(]\s*第\s*\d+\s*/\s*\d+\s*页\s*[）)]\s*$"
)
CONTINUATION_NOTICE = re.compile(
    r"^\s*[（(]?\s*本章未完[，,]?\s*请点击下一页继续阅读\s*[）)]?\s*$"
)


class TxxtAdapter(SiteAdapter):
    name = "23txxt"
    hosts = ("23txxt.com",)
    catalog_selectors = (
        "#list dl dd a",
        ".listmain dl dd a",
        ".book_list ul li a",
        "#chapterList li a",
        "#all_chapter a",
        "#catalog a",
        "#chapterlist a",
        ".booklist a",
    )
    content_selectors = (
        "#content",
        ".read_chapterDetail",
        ".readContent",
        ".content",
        ".showtxt",
        ".novel_content",
        ".box_box",
    )

    @staticmethod
    def _is_extra_chapter_title(title: str) -> bool:
        text = title.strip()
        if re.search(r"月票|上架感言|求票|抽奖|请假|推书", text):
            return False
        return bool(re.match(r"^(?:番外|完本感言|后记|序章|楔子|前言)", text))

    def extract_chapter_links(self, soup, page_url: str):
        raw_links = super().extract_chapter_links(soup, page_url)
        numbered: list[tuple[int, int, ChapterLink]] = []
        special: list[tuple[int, ChapterLink]] = []
        explicit_numbers: set[int] = set()

        for order, link in enumerate(raw_links):
            number = chapter_number_from_text(link.title, 0)
            if number > 0:
                numbered.append((number, order, link))
                explicit_numbers.add(number)

        leading_number = re.compile(r"^\s*(\d{1,7})(?=[\u4e00-\u9fff])")
        for order, link in enumerate(raw_links):
            if chapter_number_from_text(link.title, 0) > 0:
                continue
            match = leading_number.match(link.title)
            if match and int(match.group(1)) not in explicit_numbers:
                numbered.append((int(match.group(1)), order, link))
                explicit_numbers.add(int(match.group(1)))
                continue
            if self._is_extra_chapter_title(link.title):
                special.append((order, link))

        start_link = next(
            (item for item in raw_links if item.title.strip() == "开始阅读"),
            None,
        )
        if 1 not in explicit_numbers and start_link is not None:
            numbered.append((1, -1, start_link))
            explicit_numbers.add(1)
        special = [item for item in special if item[1] is not start_link]

        by_number: dict[int, tuple[int, ChapterLink]] = {}
        for number, order, link in numbered:
            by_number.setdefault(number, (order, link))
        ordered = [
            link for _, _, link in sorted(
                ((number, item[0], item[1]) for number, item in by_number.items()),
                key=lambda item: (item[0], item[1]),
            )
        ]
        ordered.extend(link for _, link in special)
        return [
            ChapterLink(number=index, title=link.title, url=link.url)
            for index, link in enumerate(ordered, start=1)
        ]

    def guess_catalog_url(self, page_url: str) -> str | None:
        parts = urlsplit(page_url)
        match = re.search(r"/bqg/([^/]+)/", parts.path)
        if not match:
            return None
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/bqg/{match.group(1)}/", "", "")
        )

    def canonical_chapter_url(self, url: str) -> str:
        parts = urlsplit(url)
        path = re.sub(r"_\d+(?=\.html?$)", "", parts.path, flags=re.IGNORECASE)
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))

    def parse_chapter(self, html: str, chapter: int) -> Chapter:
        parsed = super().parse_chapter(html, chapter)
        title = parsed.title
        for line in clean_lines(parsed.content).splitlines():
            match = PAGE_HEADING.match(line)
            if match:
                title = clean_lines(match.group("title"))
                break
        return Chapter(number=parsed.number, title=title, content=parsed.content)

    def sanitize_chapter(
        self,
        chapter: Chapter,
        book_title: str,
        first_chapter: bool,
    ) -> Chapter:
        del book_title, first_chapter
        title = clean_lines(chapter.title)
        lines: list[str] = []
        page_title = ""
        for line in clean_lines(chapter.content).splitlines():
            heading = PAGE_HEADING.match(line)
            if heading:
                if not page_title:
                    page_title = clean_lines(heading.group("title"))
                continue
            if CONTINUATION_NOTICE.match(line):
                continue
            lines.append(line)

        if page_title:
            title = page_title
        elif title == "二三书库":
            title = f"第{chapter.number}章"

        return Chapter(
            number=chapter.number,
            title=title or f"第{chapter.number}章",
            content=clean_lines("\n".join(lines)),
        )

    def find_next_page(self, soup, page_url: str) -> str | None:
        linked = super().find_next_page(soup, page_url)
        if (
            linked
            and self.normalize_url(linked) != self.normalize_url(page_url)
            and self.canonical_chapter_url(linked)
            == self.canonical_chapter_url(page_url)
        ):
            return linked

        parts = urlsplit(page_url)
        match = re.fullmatch(r"(.*?)(?:_(\d+))?\.html?", parts.path, re.IGNORECASE)
        if not match:
            return None
        chapter_stem = match.group(1)
        current_page = int(match.group(2) or 1)
        numbered_page = re.compile(
            rf"^{re.escape(chapter_stem)}_(\d+)\.html?$", re.IGNORECASE
        )
        candidates: list[tuple[int, str]] = []
        for anchor in soup.select("a[href]"):
            href = absolute_url(anchor.get("href"), page_url)
            if not href or not same_origin(href, page_url):
                continue
            target = numbered_page.fullmatch(urlsplit(href).path)
            if not target:
                continue
            page_number = int(target.group(1))
            if page_number > current_page:
                candidates.append((page_number, href))
        return min(candidates)[1] if candidates else None
