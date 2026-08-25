from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .browser import BrowserHttpClient
from .discovery import discover_book
from .errors import DownloaderError
from .fallback import AutoSwitchHttpClient
from .http import HttpClient
from .runner import download_book
from .search import SearchResult, search_sites
from .sites.registry import adapter_for_url


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
        metavar="CDP_URL",
        help="连接已手动启动的普通 Chrome，例如 http://127.0.0.1:9222",
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if bool(args.url) == bool(args.search):
        parser.error("请提供小说 URL，或使用 --search 关键词（二选一）")
    if args.search_result is not None and not args.search:
        parser.error("--search-result 只能和 --search 一起使用")
    if args.delay < 0 or args.timeout <= 0:
        parser.error("--delay 不能小于 0，--timeout 必须大于 0")

    client = None
    try:
        if args.browser_connect:
            client = BrowserHttpClient(
                timeout=args.timeout,
                verification_timeout=args.verification_timeout,
                cdp_url=args.browser_connect,
            )
        elif args.browser:
            client = BrowserHttpClient(
                timeout=args.timeout,
                profile_dir=args.browser_profile,
                executable_path=args.browser_executable,
                verification_timeout=args.verification_timeout,
            )
        else:
            http_client = HttpClient(timeout=args.timeout, retries=args.retries)
            if args.browser_fallback:
                client = AutoSwitchHttpClient(
                    http_client,
                    browser_factory=lambda: BrowserHttpClient(
                        timeout=args.timeout,
                        profile_dir=args.browser_profile,
                        executable_path=args.browser_executable,
                        verification_timeout=args.verification_timeout,
                    ),
                )
            else:
                client = http_client
        source_url = args.url
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

    print(f"已合并 {result.total_chapters} 章：{result.output}")
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
