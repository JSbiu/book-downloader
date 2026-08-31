from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .sites.registry import adapter_for_url


@dataclass(frozen=True)
class LibraryEntry:
    title: str
    catalog_url: str
    site: str
    catalog_chapters: int
    cached_chapters: int | None
    sources: tuple[str, ...]


def normalize_title(value: str) -> str:
    """去掉空格和标点，用于比较更新关键词与已收录书名。"""
    return "".join(char.casefold() for char in value if char.isalnum())


def _count_cached_chapters(chapter_dir: Path) -> int:
    if not chapter_dir.is_dir():
        return 0
    return sum(1 for path in chapter_dir.glob("*.txt") if path.stem.isdigit())


def load_library(cache_root: Path, output_dir: Path) -> list[LibraryEntry]:
    """汇总缓存目录与输出目录的来源文件，返回已收录书目（按目录 URL 去重）。

    缓存侧来源是 ``cache/<站点>/<目录哈希>/book.json``；来源文件侧是
    ``outputs/<书名>.url``（下载完成时写入的目录 URL）。两边可能同时
    记录同一本书：缓存描述抓取进度，来源文件保证缓存被清理后更新入口仍在。
    """
    entries: dict[str, dict] = {}

    if cache_root.is_dir():
        for plan_path in sorted(cache_root.glob("*/*/book.json")):
            try:
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                title = str(data["title"])
                catalog_url = str(data["catalog_url"])
                catalog_chapters = len(data.get("chapters") or [])
            except (OSError, KeyError, TypeError, ValueError):
                continue
            entries[catalog_url] = {
                "title": title,
                "catalog_url": catalog_url,
                "site": plan_path.parent.parent.name,
                "catalog_chapters": catalog_chapters,
                "cached_chapters": _count_cached_chapters(
                    plan_path.parent / "chapters"
                ),
                "sources": ("cache",),
            }

    if output_dir.is_dir():
        for sidecar in sorted(output_dir.glob("*.url")):
            try:
                catalog_url = sidecar.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not catalog_url:
                continue
            existing = entries.get(catalog_url)
            if existing is not None:
                existing["sources"] = ("cache", "sidecar")
                continue
            entries[catalog_url] = {
                "title": sidecar.stem,
                "catalog_url": catalog_url,
                "site": adapter_for_url(catalog_url).name,
                "catalog_chapters": 0,
                "cached_chapters": None,
                "sources": ("sidecar",),
            }

    return [
        LibraryEntry(**info)
        for info in sorted(entries.values(), key=lambda item: item["title"])
    ]


def match_entries(
    entries: Sequence[LibraryEntry], keyword: str
) -> list[LibraryEntry]:
    """按书名关键词过滤；忽略空格与标点，书名完全一致的排最前。"""
    normalized = normalize_title(keyword)
    if not normalized:
        return []
    exact: list[LibraryEntry] = []
    partial: list[LibraryEntry] = []
    for entry in entries:
        normalized_title = normalize_title(entry.title)
        if normalized_title == normalized:
            exact.append(entry)
        elif normalized in normalized_title:
            partial.append(entry)
    return exact + partial
