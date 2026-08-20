from __future__ import annotations

import re
from abc import ABC
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import Chapter, ChapterLink
from .common import (
    extract_book_title,
    extract_chapter_links,
    extract_content,
    find_link_by_text,
    find_next_page_link,
)


class SiteAdapter(ABC):
    name = "generic"
    hosts: tuple[str, ...] = ()
    catalog_selectors: tuple[str, ...] = ()
    content_selectors: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:
        host = urlsplit(url).netloc.lower()
        return any(host == item or host.endswith("." + item) for item in self.hosts)

    def normalize_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

    def extract_book_title(self, soup: BeautifulSoup, page_url: str) -> str:
        return extract_book_title(soup, page_url)

    def extract_chapter_links(self, soup: BeautifulSoup, page_url: str) -> list[ChapterLink]:
        return extract_chapter_links(soup, page_url, self.catalog_selectors)

    def find_catalog_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        return find_link_by_text(soup, page_url, ("章节目录", "目录", "返回目录", "目录页"))

    def guess_catalog_url(self, page_url: str) -> str | None:
        return None

    def parse_chapter(self, html: str, chapter: int) -> Chapter:
        soup = BeautifulSoup(html, "html.parser")
        return extract_content(soup, self.content_selectors, chapter)

    def sanitize_chapter(self, chapter: Chapter, book_title: str, first_chapter: bool) -> Chapter:
        return chapter

    def find_next_page(self, soup: BeautifulSoup, page_url: str) -> str | None:
        return find_next_page_link(soup, page_url)

    def canonical_chapter_url(self, url: str) -> str:
        return self.normalize_url(url)
