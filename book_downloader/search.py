from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .errors import AccessBlockedError, ConfigurationError, DownloaderError, NetworkError, SearchError
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


def _normalize_for_match(value: str) -> str:
    """去掉空格和标点，用于比较用户输入与站点返回的书名。"""
    return "".join(char.casefold() for char in value if char.isalnum())


def _search_query(query: str) -> tuple[str, bool]:
    """选择一次站点搜索关键词，并标记是否需要本地标题过滤。"""
    cleaned = _clean_query(query)
    segments = tuple(part for part in re.split(r"[^\w]+", cleaned) if part)
    if len(segments) <= 1:
        return cleaned, False
    return max(segments, key=len), True


def _filter_normalized_hits(
    hits: tuple[SiteSearchHit, ...],
    query: str,
) -> tuple[SiteSearchHit, ...]:
    """只保留去掉标点/空格后仍包含完整关键词的宽松搜索结果。"""
    normalized_query = _normalize_for_match(query)
    if len(normalized_query) < 3:
        return ()
    return tuple(
        hit
        for hit in hits
        if normalized_query in _normalize_for_match(hit.title)
    )


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
    query: str,
    limit: int,
    filter_hits: bool,
    *,
    verification_timeout: float,
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

    if not hits:
        # HTTP 拿不到结果时，若客户端支持且适配器声明了 search_via_page，
        # 就在真实页面里再尝试一次（用于绕过 WAF/Turnstile 之类的页面级验证）。
        page_search = getattr(client, "page_search", None)
        if page_search is not None and hasattr(adapter, "search_via_page"):
            try:
                hits = page_search(
                    adapter,
                    query,
                    limit,
                    verification_timeout=verification_timeout,
                )
            except (ConfigurationError, AccessBlockedError, NetworkError, DownloaderError):
                hits = ()

    if filter_hits:
        hits = _filter_normalized_hits(hits, query)
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
    query: str,
    limit: int,
    filter_hits: bool,
    *,
    verification_timeout: float,
) -> tuple[tuple[str, tuple[SearchResult, ...]], ...]:
    if len(site_requests) <= 1 or not getattr(client, "supports_concurrent_requests", False):
        return tuple(
            _search_one_site(
                client,
                adapter,
                request,
                query,
                limit,
                filter_hits,
                verification_timeout=verification_timeout,
            )
            for adapter, request in site_requests
        )

    with ThreadPoolExecutor(max_workers=min(4, len(site_requests))) as executor:
        futures = tuple(
            executor.submit(
                _search_one_site,
                client,
                adapter,
                request,
                query,
                limit,
                filter_hits,
                verification_timeout=verification_timeout,
            )
            for adapter, request in site_requests
        )
        # 按注册顺序收集，保证最终交错结果稳定；请求本身已经并发发出。
        return tuple(future.result() for future in futures)


def search_sites(
    client: HttpClient,
    query: str,
    limit: int = DEFAULT_RESULT_LIMIT,
    *,
    verification_timeout: float = 180.0,
) -> tuple[SearchResult, ...]:
    cleaned = _clean_query(query)
    if not cleaned:
        raise SearchError("搜索关键词不能为空")
    if not 1 <= limit <= MAX_RESULT_LIMIT:
        raise SearchError(f"搜索结果数必须在 1 到 {MAX_RESULT_LIMIT} 之间")
    if verification_timeout <= 0:
        raise SearchError("verification_timeout 必须大于 0")
    search_query, filter_hits = _search_query(cleaned)

    site_results: list[tuple[SearchResult, ...]] = []
    available_sites: list[str] = []
    unavailable_sites: list[str] = []
    blocked_sites: list[str] = []

    site_requests: list[tuple[SiteAdapter, SiteSearchRequest]] = []
    for adapter in searchable_adapters():
        request = adapter.build_search_request(search_query, limit)
        if not request:
            unavailable_sites.append(adapter.name)
            continue
        available_sites.append(adapter.name)
        site_requests.append((adapter, request))

    outcomes = _search_site_requests(
        client,
        tuple(site_requests),
        cleaned,
        limit,
        filter_hits,
        verification_timeout=verification_timeout,
    )
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
