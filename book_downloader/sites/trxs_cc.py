from __future__ import annotations

import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import Chapter, SiteSearchHit, SiteSearchRequest
from .base import SiteAdapter
from .common import absolute_url, clean_lines, extract_content


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

    search_url = "https://www.trxs.cc/e/search/index.php"

    def extract_book_title(self, soup, page_url: str) -> str:
        title = super().extract_book_title(soup, page_url)
        return re.sub(r"\s*[（(][^（）()]{1,80}[）)]$", "", title).strip()

    def build_search_url(self, query: str, limit: int) -> str:
        del limit
        # 站点表单声明为 GBK；按该编码构造 URL，避免中文关键词被站点误解码。
        params = urlencode(
            {"keyboard": " ".join(query.split()), "show": "title", "classid": "0"},
            encoding="gb2312",
        )
        return f"{self.search_url}?{params}"

    def build_search_request(self, query: str, limit: int) -> SiteSearchRequest:
        del limit
        # 站点实际搜索表单使用 POST；表单字段按 GB2312 百分号编码，
        # 与公开的 trxs.cc 适配实现保持一致。
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
                ("Origin", "https://www.trxs.cc"),
                ("Referer", "https://www.trxs.cc/"),
            ),
            response_encoding="gb2312",
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
        for item in soup.select("div.bk"):
            # 站点有两种结果布局：旧版把链接放在 h3 内，新版把整张卡片
            # 放进 a 标签；两者都保留 h3 作为稳定的书名节点。
            anchor = item.select_one("h3 a[href]") or item.select_one("a[href]")
            if not anchor:
                continue
            url = absolute_url(anchor.get("href"), page_url)
            if not url or not self.matches(url) or url in seen:
                continue
            title_node = item.select_one("h3")
            title = clean_lines(
                title_node.get_text(" ", strip=True)
                if title_node
                else anchor.get_text(" ", strip=True)
            )
            if not title:
                continue
            seen.add(url)
            snippets = [
                clean_lines(node.get_text(" ", strip=True))
                for node in item.select(".booknews, p")
            ]
            snippet = next((text for text in snippets if text), "")
            hits.append(SiteSearchHit(title=title, url=url, snippet=snippet))
            if len(hits) >= limit:
                break
        return tuple(hits)

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
