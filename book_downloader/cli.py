from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .browser import BrowserHttpClient
from .discovery import discover_book
from .errors import DownloaderError
from .fallback import AutoSwitchHttpClient
from .http import HttpClient
from .library import LibraryEntry, load_library, match_entries
from .runner import download_book, fill_cache_gaps, merge_from_cache
from .search import SearchResult, search_sites
from .sites.registry import adapter_for_url

SOURCE_LABELS = {"cache": "缓存", "sidecar": "来源文件"}


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def format_number_ranges(numbers: tuple[int, ...]) -> str:
    if not numbers:
        return ""
    groups: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        groups.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = number
    groups.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(groups)


def print_search_results(results: tuple[SearchResult, ...]) -> None:
    sites = ", ".join(sorted({item.site for item in results}))
    print(f"站内搜索结果（已查询并限定在 {sites}）")
    for index, result in enumerate(results, start=1):
        print(f"[{index}] {result.title}")
        print(f"    站点：{result.site}")
        print(f"    链接：{result.url}")
        if result.snippet:
            print(f"    摘要：{result.snippet}")


def select_search_result(
    results: tuple[SearchResult, ...],
    selected: int | None = None,
) -> SearchResult:
    if not results:
        raise DownloaderError("没有可供选择的搜索结果")

    if selected is None:
        while True:
            try:
                raw = input("请输入要下载的结果编号（输入 0 取消）：").strip()
            except (EOFError, KeyboardInterrupt) as error:
                raise DownloaderError("已取消选择搜索结果") from error
            if raw == "0":
                raise DownloaderError("已取消选择搜索结果")
            try:
                selected = int(raw)
            except ValueError:
                print("请输入结果编号。")
                continue
            if 1 <= selected <= len(results):
                break
            print(f"结果编号必须在 1 到 {len(results)} 之间。")

    if not 1 <= selected <= len(results):
        raise DownloaderError(f"搜索结果编号必须在 1 到 {len(results)} 之间")
    return results[selected - 1]


def select_library_entry(matches: list[LibraryEntry]) -> LibraryEntry:
    """命中唯一结果时直接选择；多本命中时交互选择。"""
    if len(matches) == 1:
        return matches[0]
    for index, entry in enumerate(matches, start=1):
        print(f"[{index}] {entry.title}（{entry.site}）")
    while True:
        try:
            raw = input("匹配到多本书，请输入要更新的编号（输入 0 取消）：").strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise DownloaderError("已取消选择更新目标") from error
        if raw == "0":
            raise DownloaderError("已取消选择更新目标")
        try:
            selected = int(raw)
        except ValueError:
            print("请输入编号。")
            continue
        if 1 <= selected <= len(matches):
            return matches[selected - 1]
        print(f"编号必须在 1 到 {len(matches)} 之间。")


def print_library(entries: list[LibraryEntry]) -> None:
    if not entries:
        print("暂无已收录书目；先通过 URL 或 --search 下载一本书。")
        return
    print(f"已收录书目（{len(entries)} 本）")
    for index, entry in enumerate(entries, start=1):
        print(f"[{index}] {entry.title}")
        print(f"    站点：{entry.site}")
        if entry.cached_chapters is not None:
            print(
                f"    章节：目录 {entry.catalog_chapters} 章 / "
                f"已缓存 {entry.cached_chapters} 章"
            )
        print(f"    目录：{entry.catalog_url}")
        labels = "、".join(SOURCE_LABELS.get(source, source) for source in entry.sources)
        print(f"    来源：{labels}")


def resolve_offline_catalog(args) -> str:
    """离线模式下确定要合并的书：--update 用已收录记录，否则由 URL 推目录。"""
    if args.update:
        entries = load_library(args.cache_root, args.output_dir)
        matches = match_entries(entries, args.update)
        if not matches:
            raise DownloaderError(
                "没有找到匹配的书目；可用 --list 查看已收录的书，或直接提供目录 URL"
            )
        selected = select_library_entry(matches)
        print(f"已选择：{selected.title}（{selected.site}）")
        return selected.catalog_url
    # 输入可能是章节页：用适配器的目录推断换成缓存记录的目录地址。
    adapter = adapter_for_url(args.url)
    return adapter.guess_catalog_url(args.url) or args.url


def report_result(result, label: str = "") -> int:
    suffix = f"（{label}）" if label else ""
    print(f"已合并 {result.total_chapters} 章：{result.output}{suffix}")
    if result.skipped_chapters:
        print(
            f"本次按限制未处理 {result.skipped_chapters} 个章节；"
            "去掉 --max-chapters 后可继续处理。"
        )
    if result.failed_chapters:
        missing = format_number_ranges(result.failed_chapters)
        print(f"未成功章节：{missing}", file=sys.stderr)
        print("重新运行相同命令即可利用缓存，只重试缺失章节。", file=sys.stderr)
        return 2
    if result.interrupted:
        print(
            "已响应 Ctrl+C，已完成章节已写入缓存和部分 TXT；"
            "下次运行相同命令即可继续。",
            file=sys.stderr,
        )
        return 130
    return 0


def merge_offline(args) -> int:
    """只用缓存重新合并：不创建任何 HTTP 客户端，不发起网络请求。"""
    try:
        catalog_url = resolve_offline_catalog(args)
        adapter = adapter_for_url(catalog_url)
        result = merge_from_cache(
            adapter=adapter,
            cache_root=args.cache_root,
            catalog_url=adapter.canonical_catalog_url(catalog_url),
            output=args.output,
            output_dir=args.output_dir,
            max_chapters=args.max_chapters,
        )
    except DownloaderError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    return report_result(result, "离线")


def fill_gaps(client, args) -> int:
    """只补齐缓存缺失章节，然后重新合并。"""
    try:
        catalog_url = resolve_offline_catalog(args)
        adapter = adapter_for_url(catalog_url)
        result = fill_cache_gaps(
            client=client,
            adapter=adapter,
            cache_root=args.cache_root,
            catalog_url=adapter.canonical_catalog_url(catalog_url),
            output=args.output,
            output_dir=args.output_dir,
            delay=args.delay,
            max_pages=args.max_pages,
            retry_delay=args.retry_delay,
            retry_rounds=args.retry_rounds,
        )
    except DownloaderError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    return report_result(result, "补齐缺口")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从任意目录页或章节页发现并合并公开章节"
    )
    parser.add_argument("url", nargs="?", help="小说目录页或任意章节页 URL")
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="使用已纳入站点的公开站内搜索，然后选择下载结果",
    )
    parser.add_argument(
        "--update",
        metavar="关键词",
        help="按书名关键词在已收录书目（缓存与来源文件）中查找并更新下载",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出已收录的书目（缓存与来源文件），不发起网络请求",
    )
    parser.add_argument(
        "--search-results",
        type=positive_int,
        default=10,
        help="最多展示多少条站内搜索结果；默认 10",
    )
    parser.add_argument(
        "--search-result",
        type=positive_int,
        help="直接选择搜索结果编号（从 1 开始）；不传则交互选择",
    )
    parser.add_argument("--output", "-o", type=Path, help="输出 TXT；默认使用书名生成")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="默认输出目录")
    parser.add_argument("--cache-root", type=Path, default=Path("cache"), help="缓存根目录")
    parser.add_argument("--delay", type=float, default=1.0, help="章节间等待秒数")
    parser.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    parser.add_argument("--retries", type=positive_int, default=3, help="失败重试次数")
    parser.add_argument("--max-pages", type=positive_int, default=50, help="单章最多跟随的续页数")
    parser.add_argument(
        "--max-chapters",
        type=positive_int,
        help="只处理目录前 N 个章节；不传则处理全部",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=positive_int,
        default=5,
        help="连续失败多少个待下载章节后停止；默认 5",
    )
    parser.add_argument(
        "--browser-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "普通模式检测到真人验证页时，先询问再切换到可见浏览器"
            "（默认开启，--no-browser-fallback 关闭）"
        ),
    )
    parser.add_argument("--refresh", action="store_true", help="忽略已有缓存并重新抓取")
    browser_modes = parser.add_mutually_exclusive_group()
    browser_modes.add_argument(
        "--browser",
        action="store_true",
        help="使用可见浏览器；遇到验证时手动完成，脚本自动等待",
    )
    browser_modes.add_argument(
        "--browser-connect",
        nargs="?",
        const="auto",
        metavar="CDP_URL",
        help=(
            "连接已启动的 Chrome 调试端口，例如 http://127.0.0.1:9222；"
            "不带地址时自动复用已开的调试窗口，没有则自动启动默认浏览器"
        ),
    )
    parser.add_argument(
        "--browser-profile",
        type=Path,
        default=Path("cache/browser-profile"),
        help="浏览器会话目录；默认保存在缓存目录",
    )
    parser.add_argument(
        "--browser-executable",
        type=Path,
        help="Chrome/Edge 可执行文件路径；默认自动查找",
    )
    parser.add_argument(
        "--verification-timeout",
        type=positive_int,
        default=180,
        help="等待浏览器验证完成的秒数",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="只用缓存重新合并输出，不发起任何网络请求（配合 URL 或 --update）",
    )
    parser.add_argument(
        "--fill-gaps",
        action="store_true",
        help="只补齐缓存中缺失的章节后重新合并（配合 URL 或 --update）",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=30.0,
        help="补齐缺口时每轮重试前的等待秒数；默认 30",
    )
    parser.add_argument(
        "--retry-rounds",
        type=positive_int,
        default=2,
        help="补齐缺口时的额外重试轮数；默认 2",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    selected_modes = [
        bool(args.url),
        bool(args.search),
        bool(args.update),
        bool(args.list),
    ]
    if sum(selected_modes) != 1:
        parser.error(
            "请提供小说 URL，或使用 --search / --update 关键词，或 --list（四选一）"
        )
    if args.search_result is not None and not args.search:
        parser.error("--search-result 只能和 --search 一起使用")
    if args.merge_only and (args.search or args.list):
        parser.error("--merge-only 只能和小说 URL 或 --update 一起使用")
    if args.fill_gaps and (args.search or args.list):
        parser.error("--fill-gaps 只能和小说 URL 或 --update 一起使用")
    if args.merge_only and args.fill_gaps:
        parser.error("--merge-only 与 --fill-gaps 不能同时使用")
    if args.retry_delay < 0:
        parser.error("--retry-delay 不能小于 0")
    if args.delay < 0 or args.timeout <= 0:
        parser.error("--delay 不能小于 0，--timeout 必须大于 0")

    if args.list:
        print_library(load_library(args.cache_root, args.output_dir))
        return 0

    if args.merge_only:
        return merge_offline(args)

    client = None
    try:
        # 普通 HTTP 客户端始终存在：浏览器模式下作为"无需验证站点"的静默通道。
        http_client = HttpClient(timeout=args.timeout, retries=args.retries)
        if args.browser_connect:
            client = BrowserHttpClient(
                timeout=args.timeout,
                profile_dir=args.browser_profile,
                executable_path=args.browser_executable,
                verification_timeout=args.verification_timeout,
                cdp_url=args.browser_connect,
                http_fallback=http_client,
            )
        elif args.browser:
            client = BrowserHttpClient(
                timeout=args.timeout,
                profile_dir=args.browser_profile,
                executable_path=args.browser_executable,
                verification_timeout=args.verification_timeout,
                http_fallback=http_client,
            )
        else:
            if args.browser_fallback:
                client = AutoSwitchHttpClient(
                    http_client,
                    browser_factory=lambda: BrowserHttpClient(
                        timeout=args.timeout,
                        profile_dir=args.browser_profile,
                        executable_path=args.browser_executable,
                        verification_timeout=args.verification_timeout,
                        http_fallback=http_client,
                    ),
                )
            else:
                client = http_client
        if args.fill_gaps:
            return fill_gaps(client, args)
        source_url = args.url
        if args.update:
            entries = load_library(args.cache_root, args.output_dir)
            matches = match_entries(entries, args.update)
            if not matches:
                raise DownloaderError(
                    "没有找到匹配的书目；可用 --list 查看已收录的书，或直接提供目录 URL"
                )
            selected = select_library_entry(matches)
            source_url = selected.catalog_url
            print(f"已选择：{selected.title}（{selected.site}）")
        if args.search:
            results = search_sites(
                client,
                args.search,
                limit=args.search_results,
                verification_timeout=args.verification_timeout,
            )
            print_search_results(results)
            selected = select_search_result(results, args.search_result)
            source_url = selected.url
            print(f"已选择：{selected.title}（{selected.site}）")

        adapter = adapter_for_url(source_url)
        plan = discover_book(client, adapter, source_url)
        print(f"书名：{plan.title}")
        print(f"目录：{plan.catalog_url}")
        print(f"章节：{len(plan.chapters)}")
        result = download_book(
            client=client,
            adapter=adapter,
            plan=plan,
            output=args.output,
            output_dir=args.output_dir,
            cache_root=args.cache_root,
            delay=args.delay,
            max_pages=args.max_pages,
            refresh=args.refresh,
            max_consecutive_failures=args.max_consecutive_failures,
            max_chapters=args.max_chapters,
        )
    except DownloaderError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消操作；已保存的缓存不会丢失。", file=sys.stderr)
        return 130
    finally:
        if client is not None:
            client.close()

    return report_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
