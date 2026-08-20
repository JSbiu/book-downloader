from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import AccessBlockedError, NetworkError, SearchError
from .http import HttpClient
from .models import SiteSearchHit, SiteSearchRequest
from .sites.base import SiteAdapter
from .sites.registry import searchable_adapters


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


def _site_name(url: str, adapter: SiteAdapter) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or adapter.name


def _convert_hit(hit: SiteSearchHit, adapter: SiteAdapter) -> SearchResult | None:
    url = adapter.normalize_url(hit.url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not adapter.matches(url):
        return None
    return SearchResult(
        title=hit.title.strip(),
        url=url,
        snippet=hit.snippet.strip(),
        site=_site_name(url, adapter),
    )


def _merge_result_sets(
    result_sets: tuple[tuple[SearchResult, ...], ...],
    limit: int,
) -> tuple[SearchResult, ...]:
    """交错合并各站点结果，避免第一个站点占满所有展示位置。"""
    merged: list[SearchResult] = []
    positions = [0] * len(result_sets)
    seen: set[str] = set()
    while len(merged) < limit:
        added = False
        for index, result_set in enumerate(result_sets):
            position = positions[index]
            if position >= len(result_set):
                continue
            result = result_set[position]
            positions[index] += 1
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result)
            added = True
            if len(merged) >= limit:
                break
        if not added:
            break
    return tuple(merged)


def _search_one_site(
    client: HttpClient,
    adapter: SiteAdapter,
    request: SiteSearchRequest,
    limit: int,
) -> tuple[str, tuple[SearchResult, ...]]:
    try:
        page = client.fetch(
            request.url,
            method=request.method,
            data=request.data,
            headers=dict(request.headers),
            response_encoding=request.response_encoding,
        )
    except (AccessBlockedError, NetworkError):
        return "blocked", ()

    hits = adapter.parse_search_results(page.text, page.url or request.url, limit)
    converted = tuple(
        result
        for hit in hits
        if hit.title.strip()
        for result in (_convert_hit(hit, adapter),)
        if result is not None
    )
    return "ok", converted


def _search_site_requests(
    client: HttpClient,
    site_requests: tuple[tuple[SiteAdapter, SiteSearchRequest], ...],
    limit: int,
) -> tuple[tuple[str, tuple[SearchResult, ...]], ...]:
    if len(site_requests) <= 1 or not getattr(client, "supports_concurrent_requests", False):
        return tuple(
            _search_one_site(client, adapter, request, limit)
            for adapter, request in site_requests
        )

    with ThreadPoolExecutor(max_workers=min(4, len(site_requests))) as executor:
        futures = tuple(
            executor.submit(_search_one_site, client, adapter, request, limit)
            for adapter, request in site_requests
        )
        # 按注册顺序收集，保证最终交错结果稳定；请求本身已经并发发出。
        return tuple(future.result() for future in futures)


def search_sites(
    client: HttpClient,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[SearchResult, ...]:
    cleaned = _clean_query(query)
    if not cleaned:
        raise SearchError("搜索关键词不能为空")
    if not 1 <= limit <= MAX_RESULT_LIMIT:
        raise SearchError(f"搜索结果数必须在 1 到 {MAX_RESULT_LIMIT} 之间")

    site_results: list[tuple[SearchResult, ...]] = []
    available_sites: list[str] = []
    unavailable_sites: list[str] = []
    blocked_sites: list[str] = []

    site_requests: list[tuple[SiteAdapter, SiteSearchRequest]] = []
    for adapter in searchable_adapters():
        request = adapter.build_search_request(cleaned, limit)
        if not request:
            unavailable_sites.append(adapter.name)
            continue
        available_sites.append(adapter.name)
        site_requests.append((adapter, request))

    outcomes = _search_site_requests(client, tuple(site_requests), limit)
    for (adapter, _), (status, converted) in zip(site_requests, outcomes):
        if status == "blocked":
            blocked_sites.append(adapter.name)
        site_results.append(converted)

    results = _merge_result_sets(tuple(site_results), limit)
    if results:
        return results

    if not available_sites:
        raise SearchError(
            "已纳入站点目前没有配置公开的站内搜索入口；"
            "请先为站点适配器补充搜索规则"
        )

    details: list[str] = []
    if unavailable_sites:
        details.append(f"未配置：{', '.join(unavailable_sites)}")
    if blocked_sites:
        details.append(f"访问受阻：{', '.join(blocked_sites)}")
    suffix = f"（{'；'.join(details)}）" if details else ""
    raise SearchError(f"站内搜索没有返回已纳入站点的结果{suffix}")
