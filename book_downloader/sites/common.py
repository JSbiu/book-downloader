from __future__ import annotations

import re
from statistics import median
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..errors import ChapterExtractionError
from ..models import Chapter, ChapterLink


CHAPTER_SIGNAL = re.compile(
    r"(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回集卷篇部]|"
    r"序章|楔子|番外|前言|chapter|part|^\s*\d{1,6}\s*[.、:：\-—\s])",
    re.IGNORECASE,
)
GENERIC_TEXT_SIGNAL = re.compile(
    r"^\s*(?:\d{1,6}|[一二三四五六七八九十百千万]+)(?:[.、:：\-—\s]|$)"
)
CHAPTER_NUMBER = re.compile(
    r"(?:第\s*)?(\d{1,7})\s*[章节回集卷篇部页]|"
    r"^\s*(\d{1,7})\s*[.、:：\-—\s]",
    re.IGNORECASE,
)


def clean_lines(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def absolute_url(href: str | None, base_url: str) -> str | None:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    url = urljoin(base_url, href.strip())
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def same_origin(url_a: str, url_b: str) -> bool:
    return urlsplit(url_a).netloc.lower() == urlsplit(url_b).netloc.lower()


def anchor_text(anchor: Tag) -> str:
    return clean_lines(anchor.get_text(" ", strip=True))


def is_navigation_text(text: str) -> bool:
    return text in {
        "首页",
        "目录",
        "章节目录",
        "上一章",
        "下一章",
        "上一页",
        "下一页",
        "登录",
        "注册",
        "下载",
        "返回顶部",
    }


def is_likely_chapter_link(text: str, url: str) -> bool:
    if is_navigation_text(text):
        return False
    if CHAPTER_SIGNAL.search(text) or GENERIC_TEXT_SIGNAL.search(text):
        return True
    path = urlsplit(url).path.lower()
    return bool(
        re.search(r"(?:chapter|part)(?:[-_/]|$)", path)
        or re.search(r"/(?:[^/]+/){2,}\d{2,}(?:_\d+)?\.html?$", path)
    )


def chapter_number_from_text(text: str, fallback: int) -> int:
    match = CHAPTER_NUMBER.search(text)
    if match:
        return int(match.group(1) or match.group(2))
    return fallback


def dedupe_links(links: list[ChapterLink]) -> list[ChapterLink]:
    seen: set[str] = set()
    result: list[ChapterLink] = []
    for link in links:
        if link.url in seen:
            continue
        seen.add(link.url)
        result.append(link)

    known = [chapter_number_from_text(link.title, 0) for link in result]
    known = [number for number in known if number > 0]
    if len(known) >= 3:
        deltas = [b - a for a, b in zip(known, known[1:]) if a != b]
        if deltas and median(deltas) < 0:
            result.reverse()

    return [
        ChapterLink(number=index, title=link.title or f"第{index}章", url=link.url)
        for index, link in enumerate(result, start=1)
    ]


def extract_chapter_links(
    soup: BeautifulSoup,
    page_url: str,
    selectors: tuple[str, ...],
) -> list[ChapterLink]:
    def collect(anchors) -> list[ChapterLink]:
        links: list[ChapterLink] = []
        for anchor in anchors:
            href = absolute_url(anchor.get("href"), page_url)
            if not href or not same_origin(href, page_url):
                continue
            text = anchor_text(anchor)
            if len(text) > 120 or not is_likely_chapter_link(text, href):
                continue
            links.append(ChapterLink(number=0, title=text, url=href))
        return dedupe_links(links)

    for selector in selectors:
        links = collect(soup.select(selector))
        if len(links) >= 2:
            return links

    links = collect(soup.select("a[href]"))
    return links if len(links) >= 2 else []


def extract_book_title(soup: BeautifulSoup, page_url: str) -> str:
    selectors = (
        "#info h1",
        ".info h1",
        ".book-info h1",
        ".bookinfo h1",
        ".bookNm a",
        ".title span",
        ".f20h",
        ".caption p",
        "#bookdetail #info h1",
        ".tna a",
        "meta[property='og:title']",
        "title",
        "h1",
        "h2",
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue
        text = node.get("content", "") if node.name == "meta" else node.get_text(" ", strip=True)
        text = clean_lines(text)
        if len(text) < 2:
            continue
        text = re.sub(r"\s*[-_|｜].*(?:小说|章节|阅读|同人小说).*$", "", text).strip()
        text = re.sub(r"\s+第\s*\d+\s*[章节回].*$", "", text).strip()
        if len(text) >= 2:
            return text[:120]

    path_part = urlsplit(page_url).path.rstrip("/").split("/")[-1]
    return path_part or "未命名小说"


def extract_content(
    soup: BeautifulSoup,
    selectors: tuple[str, ...],
    chapter: int,
) -> Chapter:
    title_selectors = (
        ".box_con .bookname h1",
        "#book .content h1",
        "#box_con .bookname h1",
        ".readAreaBox h1",
        ".art_tit",
        "h1",
        "h2",
        "h3",
    )
    title_node = soup.select_one(", ".join(title_selectors))
    title = clean_lines(title_node.get_text(" ", strip=True)) if title_node else f"第{chapter}章"

    candidates: list[tuple[int, int, Tag]] = []
    all_selectors = selectors + (
        "[id*='content']",
        "[class*='content']",
        "[id*='chapter']",
        "[class*='chapter']",
        "[id*='read']",
        "[class*='read']",
        "[id*='article']",
        "[class*='article']",
        "[id*='text']",
        "[class*='text']",
    )
    for priority, selector in enumerate(all_selectors):
        for node in soup.select(selector):
            text = clean_lines(node.get_text("\n", strip=True))
            minimum_length = 10 if priority < len(selectors) else 80
            if len(text) < minimum_length:
                continue
            link_penalty = len(node.select("a[href]")) * 25
            score = len(text) - link_penalty - priority * 1000
            candidates.append((score, priority, node))

    if not candidates:
        raise ChapterExtractionError(
            "找不到正文区域；页面可能改版、返回了验证页，或该章节没有公开正文"
        )

    _, content_priority, content_node = max(candidates, key=lambda item: item[0])
    for unwanted in content_node.select("script, style, noscript, a[href]"):
        unwanted.decompose()
    content = clean_lines(content_node.get_text("\n", strip=True))
    if len(content) < (10 if content_priority < len(selectors) else 20):
        raise ChapterExtractionError("正文内容过短，未写入缓存")
    return Chapter(number=chapter, title=title, content=content)


def find_link_by_text(
    soup: BeautifulSoup,
    page_url: str,
    texts: tuple[str, ...],
) -> str | None:
    for anchor in soup.select("a[href]"):
        text = anchor_text(anchor)
        if any(marker in text for marker in texts):
            href = absolute_url(anchor.get("href"), page_url)
            if href and same_origin(href, page_url):
                return href
    return None


def find_next_page_link(soup: BeautifulSoup, page_url: str) -> str | None:
    markers = ("下一页", "下页", "继续阅读", "下一頁")
    for anchor in soup.select("a[href]"):
        text = anchor_text(anchor)
        if len(text) > 12 or not any(marker in text for marker in markers):
            continue
        href = absolute_url(anchor.get("href"), page_url)
        if href and same_origin(href, page_url):
            return href
    return None
