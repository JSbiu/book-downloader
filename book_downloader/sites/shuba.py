from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import Chapter, SiteSearchHit, SiteSearchRequest
from .base import SiteAdapter
from .common import absolute_url, clean_lines, extract_content


DATE_LINE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
DATE_AUTHOR_LINE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s+作者\s*[：:].*$")
AUTHOR_LINE = re.compile(r"^\s*作者\s*[：:]\s*.+$")
BOOK_LINK = re.compile(r"^/book/\d+(?:\.html?)?/?$", re.IGNORECASE)
TEMPLATE_LINE = re.compile(
    r"^\s*(?:"
    r"章节错误|举报|加入书签|收藏|目录|设置|白天|上一章|下一章|"
    r"本章未完|点击下一页|手机用户请浏览|更好的阅读体验|"
    r"最新网址|最新章节|手机阅读|69书吧|www\.69shuba\.com|"
    r"\(本章完\)|（本章完）"
    r").*$",
    re.IGNORECASE,
)


class ShubaAdapter(SiteAdapter):
    """69 书吧的目录、搜索和章节解析。"""

    name = "69shuba"
    hosts = ("69shuba.com",)
    catalog_selectors = (
        ".catalog ul li a",
        ".listmain dd a",
        "#list dl dd a",
        ".chapterlist a",
        "a[href*='/txt/']",
    )
    content_selectors = (".txtnav", "#content", ".content", "#txtContent")

    search_url = "https://www.69shuba.com/modules/article/search.php"

    def extract_book_title(self, soup: BeautifulSoup, page_url: str) -> str:
        title = super().extract_book_title(soup, page_url)
        title = re.sub(r"\s*(?:最新章节列表|章节列表|无弹窗广告).*$", "", title)
        return title.strip()

    def build_search_request(
        self,
        query: str,
        limit: int,
    ) -> SiteSearchRequest:
        del limit
        body = urlencode(
            {"searchkey": " ".join(query.split()), "submit": "Search"},
            encoding="gb2312",
        ).encode("ascii")
        return SiteSearchRequest(
            url=self.search_url,
            method="POST",
            data=body,
            headers=(
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Origin", "https://www.69shuba.com"),
                ("Referer", "https://www.69shuba.com/"),
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
        for anchor in soup.select("a[href*='/book/']"):
            url = absolute_url(anchor.get("href"), page_url)
            if not url or not self.matches(url):
                continue
            if not BOOK_LINK.fullmatch(urlsplit(url).path):
                continue
            if url in seen:
                continue

            title = clean_lines(anchor.get_text(" ", strip=True))
            if not title or title in {"书页", "目录"}:
                continue
            parent = anchor.find_parent(["li", "dd", "div"])
            snippet = clean_lines(parent.get_text(" ", strip=True)) if parent else ""
            if snippet == title:
                snippet = ""
            seen.add(url)
            hits.append(SiteSearchHit(title=title, url=url, snippet=snippet))
            if len(hits) >= limit:
                break
        return tuple(hits)

    def guess_catalog_url(self, page_url: str) -> str | None:
        parts = urlsplit(page_url)
        match = re.fullmatch(
            r"/txt/(\d+)/[^/]+",
            parts.path.rstrip("/"),
            re.IGNORECASE,
        )
        if not match:
            return None
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/book/{match.group(1)}/", "", "")
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
        del book_title, first_chapter
        title = clean_lines(chapter.title)
        lines: list[str] = []
        for raw_line in clean_lines(chapter.content).splitlines():
            line = clean_lines(raw_line)
            if (
                not line
                or DATE_LINE.fullmatch(line)
                or DATE_AUTHOR_LINE.fullmatch(line)
                or AUTHOR_LINE.fullmatch(line)
            ):
                continue
            if line == title or line.startswith(f"{title} "):
                continue
            if TEMPLATE_LINE.fullmatch(line):
                continue
            lines.append(line)
        return Chapter(
            number=chapter.number,
            title=title or f"第{chapter.number}章",
            content=clean_lines("\n".join(lines)),
        )
