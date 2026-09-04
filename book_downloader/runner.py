from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from bs4 import BeautifulSoup

from .cache import BookCache
from .errors import AccessBlockedError, DownloaderError
from .http import HttpClient
from .models import BookPlan, Chapter, ChapterLink
from .sites.base import SiteAdapter


@dataclass(frozen=True)
class DownloadResult:
    output: Path
    total_chapters: int
    failed_chapters: tuple[int, ...]
    skipped_chapters: int = 0
    interrupted: bool = False


def safe_filename(name: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name).strip(" .")
    return value[:120] or "未命名小说"


# 自检阈值：正文短于此值视为疑似残缺（正常章节远长于这个长度）。
MIN_CHAPTER_CHARS = 50


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
    max_chapters: int | None = None,
) -> DownloadResult:
    # 缓存身份用适配器规范化的目录地址：同一本书的多种地址形态
    # （如 69shuba 详情页 .htm 与完整目录 /）落到同一份缓存。
    canonical_catalog = adapter.canonical_catalog_url(plan.catalog_url)
    if canonical_catalog != plan.catalog_url:
        plan = replace(plan, catalog_url=canonical_catalog)
    cache = BookCache(cache_root, adapter.name, plan.catalog_url)
    migrated = cache.save_plan(plan)
    if migrated:
        print(f"已按章节 URL 对齐并复用 {migrated} 个旧缓存。")
    selected_chapters = (
        plan.chapters
        if max_chapters is None
        else plan.chapters[:max_chapters]
    )
    skipped_chapters = len(plan.chapters) - len(selected_chapters)
    if skipped_chapters:
        print(
            f"本次只处理前 {len(selected_chapters)} 个章节，"
            f"剩余 {skipped_chapters} 个章节下次继续。"
        )
    failed: set[int] = set()
    consecutive_failures = 0
    stopped_at: int | None = None
    interrupted = False

    for index, link in enumerate(selected_chapters, start=1):
        try:
            if cache.read(link.number) and not refresh:
                print(f"[{index}/{len(selected_chapters)}] {link.title}：使用缓存")
                continue

            print(f"[{index}/{len(selected_chapters)}] {link.title}：{link.url}")
            chapter = chapter_from_pages(
                client=client,
                adapter=adapter,
                link=link,
                book_title=plan.title,
                max_pages=max_pages,
            )
            cache.write(chapter)
            consecutive_failures = 0
            if delay and index < len(selected_chapters):
                time.sleep(delay)
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
        except KeyboardInterrupt:
            interrupted = True
            print("检测到 Ctrl+C，已停止后续下载；正在整理已完成缓存。")
            break

    if stopped_at is not None:
        for link in selected_chapters[stopped_at:]:
            if refresh or cache.read(link.number) is None:
                failed.add(link.number)

    return merge_cached_chapters(
        adapter=adapter,
        cache=cache,
        plan=plan,
        selected_chapters=selected_chapters,
        output=output,
        output_dir=output_dir,
        failed=failed,
        skipped_chapters=skipped_chapters,
        interrupted=interrupted,
    )


def validate_assembled(chapters: list[Chapter]) -> list[str]:
    """合并结果自检：编号连续性、重复标题、空章与疑似残缺章节。

    分段与净化规则改坏时，这些问题会直接体现在输出里（历史上踩过
    "第N节"重复编号、误切尾章两次），合并后立刻报出来比事后发现便宜。
    """
    findings: list[str] = []
    for position, chapter in enumerate(chapters, start=1):
        if chapter.number != position:
            findings.append(
                f"章节编号不连续：第 {position} 章位置上的编号是 {chapter.number}"
            )
            break

    seen: dict[str, int] = {}
    for chapter in chapters:
        key = chapter.title.strip()
        if key in seen:
            findings.append(f"重复章节标题：{key}（第{seen[key]}、{chapter.number}章）")
        else:
            seen[key] = chapter.number

    def preview(numbers: list[int]) -> str:
        shown = "、".join(str(number) for number in numbers[:10])
        return f"{shown}{'…' if len(numbers) > 10 else ''}"

    empty = [c.number for c in chapters if not c.content.strip()]
    if empty:
        findings.append(f"空章节：{preview(empty)}")
    short = [
        c.number
        for c in chapters
        if 0 < len(c.content.strip()) < MIN_CHAPTER_CHARS
    ]
    if short:
        findings.append(
            f"疑似残缺章节（正文少于 {MIN_CHAPTER_CHARS} 字）：{preview(short)}"
        )
    return findings


def merge_cached_chapters(
    *,
    adapter: SiteAdapter,
    cache: BookCache,
    plan: BookPlan,
    selected_chapters: tuple[ChapterLink, ...],
    output: Path | None,
    output_dir: Path,
    failed: set[int],
    skipped_chapters: int = 0,
    interrupted: bool = False,
    offline: bool = False,
) -> DownloadResult:
    """把缓存章节按当前净化规则重新处理并合并成 TXT。

    重跑净化与分段规则都走这里，因此缓存内容不需要重新下载即可被
    修正。offline=True 时不做任何网络请求：缺失的缓存只提示，不计失败。
    """
    cleaned_chapters: list[Chapter] = []
    cleaned_cache_count = 0
    missing: list[int] = []
    for link in selected_chapters:
        cached = cache.read_chapter(link.number)
        if cached is None:
            if offline:
                missing.append(link.number)
            elif not interrupted:
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
        cleaned_chapters.append(cleaned)

    if cleaned_cache_count:
        print(f"已用当前净化规则更新 {cleaned_cache_count} 个缓存章节。")
    if missing:
        preview = "、".join(str(number) for number in missing[:10])
        suffix = "…" if len(missing) > 10 else ""
        print(
            f"缓存缺失 {len(missing)} 章（{preview}{suffix}）；"
            "去掉 --merge-only 重新运行可补齐后再合并。",
            file=sys.stderr,
        )

    assembled_chapters = adapter.assemble_chapters(
        cleaned_chapters,
        book_title=plan.title,
    )
    for finding in validate_assembled(list(assembled_chapters)):
        print(f"自检提示：{finding}", file=sys.stderr)
    blocks = [
        (chapter.number, chapter.block)
        for chapter in assembled_chapters
    ]
    if not blocks:
        if interrupted:
            raise DownloaderError(
                "已取消下载，尚未成功抓到章节；已保留目录和已有缓存"
            )
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
    # 目录 URL 写进输出旁边的 .url 来源文件：缓存被清理后，--update 仍能找到更新入口。
    sidecar_path = output_path.with_suffix(".url")
    sidecar_path.write_text(f"{plan.catalog_url}\n", encoding="utf-8")
    return DownloadResult(
        output=output_path,
        total_chapters=len(blocks),
        failed_chapters=tuple(sorted(failed)),
        skipped_chapters=skipped_chapters,
        interrupted=interrupted,
    )


def load_plan_from_cache(
    cache: BookCache,
    cache_root: Path,
    adapter: SiteAdapter,
    catalog_url: str,
) -> tuple[BookCache, BookPlan]:
    """从缓存的目录记录重建 BookPlan；缓存里没有这本书时报错。"""
    plan_path = cache.path / "book.json"
    if not plan_path.is_file():
        raise DownloaderError(
            f"缓存里没有这本书的目录记录：{catalog_url}；请联网运行一次下载"
        )
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = BookPlan(
            title=str(data["title"]),
            catalog_url=str(data["catalog_url"]),
            chapters=tuple(
                ChapterLink(
                    number=int(item["number"]),
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                )
                for item in data.get("chapters") or []
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DownloaderError(
            f"缓存目录记录无法解析：{plan_path}（{error}）"
        ) from error
    if not plan.chapters:
        raise DownloaderError("缓存中的目录为空；请联网重新发现目录")
    return cache, plan


def merge_from_cache(
    *,
    adapter: SiteAdapter,
    cache_root: Path,
    catalog_url: str,
    output: Path | None,
    output_dir: Path,
    max_chapters: int | None = None,
) -> DownloadResult:
    """离线合并：只用缓存，不发起任何网络请求。"""
    cache = BookCache(cache_root, adapter.name, catalog_url)
    cache, plan = load_plan_from_cache(cache, cache_root, adapter, catalog_url)

    selected_chapters = (
        plan.chapters if max_chapters is None else plan.chapters[:max_chapters]
    )
    skipped_chapters = len(plan.chapters) - len(selected_chapters)
    return merge_cached_chapters(
        adapter=adapter,
        cache=cache,
        plan=plan,
        selected_chapters=selected_chapters,
        output=output,
        output_dir=output_dir,
        failed=set(),
        skipped_chapters=skipped_chapters,
        offline=True,
    )


def fill_cache_gaps(
    *,
    client: HttpClient,
    adapter: SiteAdapter,
    cache_root: Path,
    catalog_url: str,
    output: Path | None,
    output_dir: Path,
    delay: float,
    max_pages: int,
    retry_delay: float = 30.0,
    retry_rounds: int = 2,
) -> DownloadResult:
    """只补齐缓存里缺失的章节，然后重新合并。

    站点验证页或限流会让一次下载在中段中断，留下连续缺口。这里跳过已有
    缓存的章节，只请求缺失部分；失败时按轮次退避重试（浏览器模式下等待
    人工验证），因此可反复运行直到补齐。
    """
    cache = BookCache(cache_root, adapter.name, catalog_url)
    cache, plan = load_plan_from_cache(cache, cache_root, adapter, catalog_url)

    pending = [
        link for link in plan.chapters if cache.read(link.number) is None
    ]
    if not pending:
        print("缓存没有缺失章节；直接重新合并。")
    else:
        print(f"待补齐 {len(pending)} 章（{pending[0].number}–{pending[-1].number}）。")

    failed: set[int] = set()
    for round_index in range(retry_rounds + 1):
        if round_index and failed:
            print(f"等待 {retry_delay:.0f} 秒后重试 {len(failed)} 个失败章节……")
            time.sleep(retry_delay)
        still_failed: set[int] = set()
        for index, link in enumerate(pending, start=1):
            try:
                print(f"[{index}/{len(pending)}] {link.title}：{link.url}")
                chapter = chapter_from_pages(
                    client=client,
                    adapter=adapter,
                    link=link,
                    book_title=plan.title,
                    max_pages=max_pages,
                )
                cache.write(chapter)
                if delay and index < len(pending):
                    time.sleep(delay)
            except AccessBlockedError as error:
                print(f"  已停止本轮：{error}")
                still_failed.update(link.number for link in pending[index - 1:])
                break
            except DownloaderError as error:
                print(f"  跳过：{error}")
                still_failed.add(link.number)
            except KeyboardInterrupt:
                print("检测到 Ctrl+C，已停止补齐；已完成章节保留在缓存。")
                still_failed.update(link.number for link in pending[index - 1:])
                break
        failed = still_failed
        if not failed:
            break
        pending = [link for link in pending if link.number in failed]

    return merge_cached_chapters(
        adapter=adapter,
        cache=cache,
        plan=plan,
        selected_chapters=plan.chapters,
        output=output,
        output_dir=output_dir,
        failed=set(),
        offline=True,
    )
