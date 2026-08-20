from __future__ import annotations

from .base import SiteAdapter
from .trxs_cc import TrxsCcAdapter
from .txxt import TxxtAdapter


class GenericAdapter(SiteAdapter):
    name = "generic"
    catalog_selectors = (
        "#list a",
        "#catalog a",
        "#chapterList a",
        ".chapter-list a",
        ".book-list a",
        ".listmain a",
    )
    content_selectors = (
        ".read-content",
        ".readContent",
        ".content",
        ".showtxt",
        "article",
    )


def adapter_for_url(url: str) -> SiteAdapter:
    for adapter in searchable_adapters():
        if adapter.matches(url):
            return adapter
    return GenericAdapter()


def searchable_adapters() -> tuple[SiteAdapter, ...]:
    """返回已明确纳入站点搜索的适配器。

    GenericAdapter 只是未知站点的兜底解析器，不应被拼进 Google 的 site: 条件。
    """
    return (TrxsCcAdapter(), TxxtAdapter())
