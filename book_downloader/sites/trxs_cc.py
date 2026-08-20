from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from ..models import Chapter
from .base import SiteAdapter
from .common import clean_lines, extract_content


class TrxsCcAdapter(SiteAdapter):
    name = "trxs_cc"
    hosts = ("trxs.cc",)
    catalog_selectors = (
        "#list dl dd a",
        ".listmain dl dd a",
        ".book_list ul li a",
        "#chapterList li a",
        ".volume-wrap ul li a",
        "#all_chapter a",
        ".section-box .section-list li a",
        "#list ul li a",
    )
    content_selectors = (
        ".read_chapterDetail",
        "#readContent_set",
        "div.readDetail",
        ".read-content",
        "#chaptercontent",
    )

    def extract_book_title(self, soup, page_url: str) -> str:
        title = super().extract_book_title(soup, page_url)
        return re.sub(r"\s*[（(][^（）()]{1,80}[）)]$", "", title).strip()

    def guess_catalog_url(self, page_url: str) -> str | None:
        parts = urlsplit(page_url)
        match = re.search(r"/tongren/([^/]+)(?:/[^/]*)?/?$", parts.path)
        if not match:
            return None
        novel_id = re.sub(r"\.html?$", "", match.group(1), flags=re.IGNORECASE)
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/tongren/{novel_id}.html", "", "")
        )

    def sanitize_chapter(self, chapter: Chapter, book_title: str, first_chapter: bool) -> Chapter:
        lines = [line for line in clean_lines(chapter.content).splitlines() if line.strip()]
        title_pattern = re.compile(
            rf"^\s*{re.escape(book_title)}\s*第\s*\d+\s*[章节回].*$"
        )
        lines = [line for line in lines if not title_pattern.match(line)]
        lines = [line for line in lines if line.strip() not in {"正文卷", "正文"}]

        chapter_heading = re.compile(r"^\s*第\s*\d+\s*[章节回].*$")
        if first_chapter:
            for index, line in enumerate(lines):
                if chapter_heading.match(line):
                    lines = lines[index:]
                    break

        normalized_title = clean_lines(chapter.title)
        if normalized_title.startswith(book_title):
            normalized_title = normalized_title[len(book_title) :].strip(" ：:—-_")
        for line in lines[:2]:
            if chapter_heading.match(line):
                normalized_title = line
                lines.remove(line)
                break
        if not normalized_title or normalized_title == book_title:
            normalized_title = f"第{chapter.number}章"

        return Chapter(
            number=chapter.number,
            title=normalized_title,
            content=clean_lines("\n".join(lines)),
        )
