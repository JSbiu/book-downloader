from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from .errors import SearchError
from .http import HttpClient
from .sites.base import SiteAdapter
from .sites.registry import searchable_adapters


GOOGLE_SEARCH_URL = "https://www.google.com/search"
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 100


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    site: str


def _clean_query(query: str) -> str:
    return " ".join(query.split())


def build_google_query(
    query: str,
    adapters: tuple[SiteAdapter, ...] | None = None,
) -> str:
    cleaned = _clean_query(query)
    if not cleaned:
        raise SearchError("Google 搜索关键词不能为空")

    selected_adapters = adapters or searchable_adapters()
    hosts = tuple(
        host
        for adapter in selected_adapters
        for host in adapter.hosts
    )
    if not hosts:
        raise SearchError("没有配置可搜索的站点")
    site_clause = " OR ".join(f"site:{host}" for host in dict.fromkeys(hosts))
    return f"({site_clause}) {cleaned}"


def build_google_search_url(
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
    adapters: tuple[SiteAdapter, ...] | None = None,
) -> str:
    if not 1 <= limit <= MAX_RESULT_LIMIT:
        raise SearchError(f"搜索结果数必须在 1 到 {MAX_RESULT_LIMIT} 之间")
    return f"{GOOGLE_SEARCH_URL}?{urlencode({
        'q': build_google_query(query, adapters),
        'num': limit,
        'filter': '0',
        'hl': 'zh-CN',
    })}"


def _result_target(href: str, base_url: str) -> str | None:
    value = unescape(href.strip())
    if not value:
        return None
    absolute = urljoin(base_url, value)
    parts = urlsplit(absolute)
    google_host = parts.netloc.lower()
    if (google_host == "google.com" or google_host.endswith(".google.com")) and parts.path == "/url":
        params = parse_qs(parts.query)
        value = (params.get("q") or params.get("url") or [""])[0]
        absolute = urljoin(base_url, unescape(value))
        parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _supported_adapter(url: str, adapters: tuple[SiteAdapter, ...]) -> SiteAdapter | None:
    for adapter in adapters:
        if adapter.matches(url):
            return adapter
    return None


def _snippet_for(anchor: Tag, title: str) -> str:
    snippet_selectors = (
        "div.VwiC3b",
        "div[data-sncf]",
        "span.aCOpRe",
    )
    node: Tag | None = anchor
    for _ in range(6):
        if node is None:
            break
        for selector in snippet_selectors:
            snippet = node.select_one(selector)
            if snippet:
                return " ".join(snippet.get_text(" ", strip=True).split())
        parent = node.parent
        node = parent if isinstance(parent, Tag) else None

    parent = anchor.parent
    text = " ".join(parent.get_text(" ", strip=True).split()) if parent else ""
    if text.startswith(title):
        text = text[len(title):].strip(" -—|·")
    return text[:300]


def parse_google_results(
    html: str,
    adapters: tuple[SiteAdapter, ...] | None = None,
) -> tuple[SearchResult, ...]:
    selected_adapters = adapters or searchable_adapters()
    soup = BeautifulSoup(html, "html.parser")
    results: list[SearchResult] = []
    seen: set[tuple[str, str, str]] = set()

    for heading in soup.select("h3"):
        anchor = heading.find_parent("a", href=True)
        if not anchor:
            continue
        url = _result_target(anchor.get("href", ""), GOOGLE_SEARCH_URL)
        if not url:
            continue
        adapter = _supported_adapter(url, selected_adapters)
        if adapter is None:
            continue
        title = " ".join(heading.get_text(" ", strip=True).split())
        if not title:
            continue
        parts = urlsplit(url)
        key = (parts.netloc.lower().removeprefix("www."), parts.path, parts.query)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=_snippet_for(anchor, title),
                site=parts.netloc.lower().removeprefix("www."),
            )
        )
    return tuple(results)


def search_google(
    client: HttpClient,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[SearchResult, ...]:
    adapters = searchable_adapters()
    search_url = build_google_search_url(query, limit, adapters)
    page = client.fetch(search_url)
    results = parse_google_results(page.text, adapters)
    wait_for_results = getattr(client, "wait_for_google_results", None)
    if not results and callable(wait_for_results):
        page = wait_for_results(page.url or search_url)
        results = parse_google_results(page.text, adapters)
    if not results:
        raise SearchError(
            "Google 没有返回已纳入站点的结果；Google 可能要求启用 JavaScript、"
            "同意页面或人工验证。请使用 --browser，在可见浏览器中完成正常操作后重试"
        )
    return results[:limit]
