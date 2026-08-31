from __future__ import annotations

import re
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..errors import DownloaderError
from ..models import Chapter, ChapterLink, SiteSearchHit, SiteSearchRequest
from .base import SiteAdapter
from .common import (
    absolute_url,
    chapter_number_from_text,
    clean_lines,
    extract_content,
)


SEARCH_RESULTS_MARKER = "a[href*='/book/']"


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
    requires_browser = True
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
            # /book/<id>.htm 是书的详情页（只含最新章节），统一规范化到
            # 完整目录页 /book/<id>/，避免目录发现只提到最后几章。
            hits.append(SiteSearchHit(title=title, url=self.normalize_book_url(url), snippet=snippet))
            if len(hits) >= limit:
                break
        return tuple(hits)

    def normalize_book_url(self, url: str) -> str:
        """把 69shuba 书页地址统一为完整目录格式 /book/<id>/。"""
        parts = urlsplit(url)
        match = re.fullmatch(r"/book/(\d+)(?:\.html?)?", parts.path, re.IGNORECASE)
        if not match:
            return url
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/book/{match.group(1)}/", "", "")
        )

    def extract_chapter_links(self, soup, page_url: str):
        links = list(super().extract_chapter_links(soup, page_url))
        numbered = [
            (chapter_number_from_text(link.title, 0), order, link)
            for order, link in enumerate(links)
        ]
        if sum(1 for number, _, _ in numbered if number > 0) < 3:
            return links
        # 69shuba 目录页顶部有"最新章节"置顶区，与完整目录重复；通用去重只按
        # URL 保留首见，置顶章节会落在最前面，还会被倒序检测误判反转。
        # 这里按章节号稳定排序（无编号的番外保持原顺序排到末尾），
        # 置顶重复章自然归位。
        numbered.sort(key=lambda item: (item[0] == 0, item[0], item[1]))
        return [
            ChapterLink(number=index, title=link.title, url=link.url)
            for index, (_, _, link) in enumerate(numbered, start=1)
        ]

    def search_via_page(
        self,
        page,
        query: str,
        limit: int,
        *,
        navigation_timeout: float,
        verification_timeout: float,
    ) -> tuple[SiteSearchHit, ...]:
        """在可见浏览器页面里直接执行 69shuba 站内搜索。

        该站点搜索入口受 Cloudflare Turnstile 保护，普通 HTTP 请求会被 WAF
        拦截，Turnstile 令牌也只能在真实浏览器内获得。脚本等待验证令牌就绪
        （包括用户手动点选验证控件），随后复用页面级 parse_search_results。
        """
        page.goto(
            "https://www.69shuba.com/",
            wait_until="domcontentloaded",
            timeout=int(navigation_timeout * 1000),
        )
        input_box = page.locator("input[name='searchkey']").first
        input_box.wait_for(state="visible", timeout=int(navigation_timeout * 1000))
        input_box.fill(query)
        input_box.press("Enter")

        try:
            page.wait_for_url(
                "**/modules/article/search.php**",
                timeout=int(navigation_timeout * 1000),
            )
        except Exception:
            pass

        print(
            "\n69shuba 搜索可能弹出 Cloudflare 人机验证；"
            "请在打开的浏览器窗口里点一下验证，脚本会自动继续。",
            flush=True,
        )

        deadline = time.monotonic() + verification_timeout
        while time.monotonic() < deadline:
            if page.locator(SEARCH_RESULTS_MARKER).count() > 0:
                break
            page.wait_for_timeout(1000)
        else:
            raise DownloaderError(
                f"69shuba 搜索在 {verification_timeout} 秒内未拿到结果；"
                "Cloudflare 验证未通过或站点改版。"
            )

        return self.parse_search_results(page.content(), page.url, limit)

    def guess_catalog_url(self, page_url: str) -> str | None:
        parts = urlsplit(page_url)
        path = parts.path.rstrip("/")
        book = re.fullmatch(r"/book/(\d+)(?:\.html?)?", path, re.IGNORECASE)
        if book:
            # /book/<id>.htm 是详情页，完整目录统一在 /book/<id>/。
            return urlunsplit(
                (parts.scheme, parts.netloc, f"/book/{book.group(1)}/", "", "")
            )
        match = re.fullmatch(
            r"/txt/(\d+)/[^/]+",
            path,
            re.IGNORECASE,
        )
        if not match:
            return None
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/book/{match.group(1)}/", "", "")
        )

    def canonical_catalog_url(self, url: str) -> str:
        # 详情页 .htm 与完整目录 / 指向同一本书；缓存身份统一用目录格式，
        # 避免同一本书按 URL 哈希分裂成多份缓存。
        return self.normalize_book_url(url)

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
