from __future__ import annotations

from bs4 import BeautifulSoup

from .errors import DiscoveryError
from .http import HttpClient
from .models import BookPlan
from .sites.base import SiteAdapter


def discover_book(client: HttpClient, adapter: SiteAdapter, input_url: str) -> BookPlan:
    first_page = client.fetch(input_url)
    first_soup = BeautifulSoup(first_page.text, "html.parser")

    guessed_catalog = adapter.guess_catalog_url(first_page.url)
    input_is_probably_chapter = (
        guessed_catalog is not None
        and adapter.normalize_url(guessed_catalog)
        != adapter.normalize_url(first_page.url)
    )
    chapter_links = (
        []
        if input_is_probably_chapter
        else adapter.extract_chapter_links(first_soup, first_page.url)
    )
    if chapter_links:
        return BookPlan(
            title=adapter.extract_book_title(first_soup, first_page.url),
            catalog_url=adapter.normalize_url(first_page.url),
            chapters=tuple(chapter_links),
        )

    catalog_url = adapter.find_catalog_url(first_soup, first_page.url)
    if not catalog_url:
        catalog_url = guessed_catalog
    if not catalog_url or adapter.normalize_url(catalog_url) == adapter.normalize_url(first_page.url):
        raise DiscoveryError(
            "输入页没有识别到章节目录。请确认链接是目录页或包含“目录”链接的章节页。"
        )

    catalog_page = client.fetch(catalog_url)
    catalog_soup = BeautifulSoup(catalog_page.text, "html.parser")
    chapter_links = adapter.extract_chapter_links(catalog_soup, catalog_page.url)
    if not chapter_links:
        raise DiscoveryError(
            f"已打开目录页，但没有识别到章节链接：{catalog_page.url}"
        )

    return BookPlan(
        title=adapter.extract_book_title(catalog_soup, catalog_page.url),
        catalog_url=adapter.normalize_url(catalog_page.url),
        chapters=tuple(chapter_links),
    )
