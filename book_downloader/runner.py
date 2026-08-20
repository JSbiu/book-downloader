from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from .cache import BookCache
from .errors import AccessBlockedError, DownloaderError
from .http import HttpClient
from .models import BookPlan, Chapter
from .sites.base import SiteAdapter


@dataclass(frozen=True)
class DownloadResult:
    output: Path
    total_chapters: int
    failed_chapters: tuple[int, ...]


def safe_filename(name: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name).strip(" .")
    return value[:120] or "未命名小说"


def chapter_from_pages(
    client: HttpClient,
    adapter: SiteAdapter,
    link,
    book_title: str,
    max_pages: int,
) -> Chapter:
    current_url = adapter.canonical_chapter_url(link.url)
    visited: set[str] = set()
    page_contents: list[str] = []
    title = link.title

    for _ in range(max_pages):
        normalized_current = adapter.normalize_url(current_url)
        if normalized_current in visited:
            raise DownloaderError(f"第 {link.number} 章检测到分页链接循环")
        visited.add(normalized_current)

        page = client.fetch(current_url)
        parsed = adapter.parse_chapter(page.text, link.number)
        if not title or not title.lstrip().startswith("第"):
            title = parsed.title
        page_contents.append(parsed.content)

        soup = BeautifulSoup(page.text, "html.parser")
        next_url = adapter.find_next_page(soup, page.url)
        if not next_url:
            break
        current_url = next_url
    else:
        raise DownloaderError(
            f"第 {link.number} 章分页超过 {max_pages} 页，已停止以避免异常循环"
        )

    chapter = Chapter(
        number=link.number,
        title=title or f"第{link.number}章",
        content="\n\n".join(page_contents),
    )
    return adapter.sanitize_chapter(
        chapter,
        book_title=book_title,
        first_chapter=link.number == 1,
    )


def download_book(
    *,
    client: HttpClient,
    adapter: SiteAdapter,
    plan: BookPlan,
    output: Path | None,
    output_dir: Path,
    cache_root: Path,
    delay: float,
    max_pages: int,
    refresh: bool,
    max_consecutive_failures: int = 5,
) -> DownloadResult:
    cache = BookCache(cache_root, adapter.name, plan.catalog_url)
    migrated = cache.save_plan(plan)
    if migrated:
        print(f"已按章节 URL 对齐并复用 {migrated} 个旧缓存。")
    failed: set[int] = set()
    consecutive_failures = 0
    stopped_at: int | None = None

    for index, link in enumerate(plan.chapters, start=1):
        if cache.read(link.number) and not refresh:
            print(f"[{index}/{len(plan.chapters)}] {link.title}：使用缓存")
            continue

        print(f"[{index}/{len(plan.chapters)}] {link.title}：{link.url}")
        try:
            chapter = chapter_from_pages(
                client=client,
                adapter=adapter,
                link=link,
                book_title=plan.title,
                max_pages=max_pages,
            )
            cache.write(chapter)
            consecutive_failures = 0
        except AccessBlockedError as error:
            failed.add(link.number)
            stopped_at = index
            print(f"  已停止：{error}")
            break
        except DownloaderError as error:
            failed.add(link.number)
            consecutive_failures += 1
            print(f"  跳过：{error}")
            if consecutive_failures >= max_consecutive_failures:
                stopped_at = index
                print(
                    f"连续 {consecutive_failures} 个待下载章节失败，已提前停止，"
                    "避免继续请求异常页面。"
                )
                break

        if delay and index < len(plan.chapters):
            time.sleep(delay)

    if stopped_at is not None:
        for link in plan.chapters[stopped_at:]:
            if refresh or cache.read(link.number) is None:
                failed.add(link.number)

    blocks: list[tuple[int, str]] = []
    cleaned_cache_count = 0
    for link in plan.chapters:
        cached = cache.read_chapter(link.number)
        if cached is None:
            failed.add(link.number)
            continue
        cleaned = adapter.sanitize_chapter(
            cached,
            book_title=plan.title,
            first_chapter=link.number == 1,
        )
        if cleaned.block != cached.block:
            cache.write(cleaned)
            cleaned_cache_count += 1
        blocks.append((link.number, cleaned.block))

    if cleaned_cache_count:
        print(f"已用当前净化规则更新 {cleaned_cache_count} 个缓存章节。")
    if not blocks:
        raise DownloaderError("没有成功抓到任何章节，未生成 TXT")

    output_path = output or (output_dir / f"{safe_filename(plan.title)}.txt")
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_output.write_text(
        "\n\n".join(block for _, block in blocks) + "\n",
        encoding="utf-8",
    )
    temporary_output.replace(output_path)
    return DownloadResult(
        output=output_path,
        total_chapters=len(blocks),
        failed_chapters=tuple(sorted(failed)),
    )
