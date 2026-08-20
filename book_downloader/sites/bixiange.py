from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import Chapter, SiteSearchHit, SiteSearchRequest
from .base import SiteAdapter
from .common import absolute_url, clean_lines, extract_content


CHAPTER_HEADING = re.compile(
    r"^\s*(?:第\s*(?:\d+|[零〇一二三四五六七八九十百千万两]+)\s*"
    r"[章回集卷篇部](?=\s|$|[：:—\-·、,，。！？《「【])|"
    r"序章|楔子|前言|番外)"
)
SECTION_HEADING = re.compile(
    r"^\s*第\s*(?:\d+|[零〇一二三四五六七八九十百千万两]+)\s*节"
    r"(?:\s+.*)?$"
)
POLLUTION_LINE = re.compile(r"^\s*\?9\s*提供最快\s*$")


class BixiangeAdapter(SiteAdapter):
    """笔仙阁及其可用镜像的目录、搜索和章节解析。"""

    name = "bixiange"
    hosts = ("bixiange.top", "bxg123.top", "bixiange.me")
    catalog_selectors = (".catalog a",)
    content_selectors = ("#mycontent",)

    search_url = "https://www.bixiange.top/e/search/indexpage.php"

    def extract_book_title(self, soup: BeautifulSoup, page_url: str) -> str:
        title = super().extract_book_title(soup, page_url)
        return re.sub(r"\s*[（(]\s*\d+\s*[-—]\s*\d+\s*[）)]$", "", title).strip()

    def build_search_request(
        self,
        query: str,
        limit: int,
    ) -> SiteSearchRequest:
        del limit
        body = urlencode(
            {"keyboard": " ".join(query.split()), "show": "title", "classid": "0"},
            encoding="gb2312",
        ).encode("ascii")
        return SiteSearchRequest(
            url=self.search_url,
            method="POST",
            data=body,
            headers=(
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Origin", "https://www.bixiange.top"),
                ("Referer", "https://www.bixiange.top/"),
            ),
            response_encoding="gb18030",
        )

    def parse_search_results(
        self,
        html: str,
        page_url: str,
        limit: int,
    ) -> tuple[SiteSearchHit, ...]:
        soup = BeautifulSoup(html, "html.parser")
        hits: list[SiteSearchHit] = []
        seen: set[str] = set()
        for item in soup.select(".list li"):
            anchor = (
                item.select_one(".info .title a[href]")
                or item.select_one(".cover a[href]")
                or item.select_one("a[href]")
            )
            if not anchor:
                continue
            url = absolute_url(anchor.get("href"), page_url)
            if not url or not self.matches(url) or url in seen:
                continue

            title_node = item.select_one(".info .title a[href]") or anchor
            title = clean_lines(title_node.get_text(" ", strip=True))
            if not title:
                continue

            snippets = [
                clean_lines(node.get_text(" ", strip=True))
                for node in item.select(".descript, .tips")
            ]
            snippet = next((text for text in snippets if text), "")
            seen.add(url)
            hits.append(SiteSearchHit(title=title, url=url, snippet=snippet))
            if len(hits) >= limit:
                break
        return tuple(hits)

    def guess_catalog_url(self, page_url: str) -> str | None:
        parts = urlsplit(page_url)
        match = re.fullmatch(
            r"/([^/]+)/([^/]+)/(?:index/)?\d+\.html?",
            parts.path.rstrip("/"),
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        section, book_id = match.groups()
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/{section}/{book_id}/", "", "")
        )

    def parse_chapter(self, html: str, chapter: int) -> Chapter:
        soup = BeautifulSoup(html, "html.parser")
        return extract_content(soup, self.content_selectors, chapter)

    def sanitize_chapter(
        self,
        chapter: Chapter,
        book_title: str,
        first_chapter: bool,
    ) -> Chapter:
        lines = [
            line
            for line in clean_lines(chapter.content).splitlines()
            if (
                line.strip()
                and not SECTION_HEADING.fullmatch(line)
                and not POLLUTION_LINE.fullmatch(line)
            )
        ]
        title = clean_lines(chapter.title)
        normalized_book_title = clean_lines(book_title)
        if normalized_book_title and title.startswith(normalized_book_title):
            title = title[len(normalized_book_title) :].strip(" ：:—-_")

        # 首节页面把书籍简介放在正文容器前面；后续章节保留正文内部标题，
        # 暂不做未经样本验证的经验性替换。
        if first_chapter:
            start = next(
                (index for index, line in enumerate(lines) if CHAPTER_HEADING.match(line)),
                None,
            )
            if start is not None:
                lines = lines[start:]

            for index, line in enumerate(lines[:2]):
                if CHAPTER_HEADING.match(line):
                    title = line
                    del lines[index]
                    break

        return Chapter(
            number=chapter.number,
            title=title or f"第{chapter.number}节",
            content=clean_lines("\n".join(lines)),
        )

    def assemble_chapters(
        self,
        chapters: list[Chapter],
        book_title: str,
    ) -> tuple[Chapter, ...]:
        del book_title
        if not chapters:
            return ()

        assembled: list[Chapter] = []
        current_title = (
            None
            if SECTION_HEADING.fullmatch(chapters[0].title)
            else chapters[0].title
        )
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_title, current_lines
            if not current_title:
                return
            assembled.append(
                Chapter(
                    number=len(assembled) + 1,
                    title=current_title,
                    content=clean_lines("\n".join(current_lines)),
                )
            )
            current_lines = []

        for chapter in chapters:
            for line in clean_lines(chapter.content).splitlines():
                if CHAPTER_HEADING.match(line):
                    flush()
                    current_title = line
                    continue
                current_lines.append(line)

        flush()
        return tuple(assembled)
