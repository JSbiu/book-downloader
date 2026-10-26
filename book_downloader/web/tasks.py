"""Single-worker task queue sharing the existing downloader and cache format."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import logging
import shutil
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..browser import BrowserHttpClient
from ..cache import cache_key
from ..discovery import discover_book
from ..errors import AccessBlockedError, DownloaderError, VerificationPageError
from ..http import HttpClient
from ..library import load_library
from ..operation import OperationCancelled, check_cancelled, emit, operation_scope
from ..runner import download_book, fill_cache_gaps, merge_from_cache, safe_filename
from ..search import search_sites
from ..sites.registry import adapter_for_url
from .browser_bridge import BrowserBridge, ExtensionClient


ACTIVE = {"queued", "running", "needs_browser", "waiting_verification", "stopping"}
KINDS = {"search", "download", "update", "fill_gaps", "merge"}
LOGGER = logging.getLogger(__name__)


class BookBusyError(ValueError):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_dir: str = "outputs"
    delay: float = Field(default=1, ge=0, le=120)
    timeout: float = Field(default=20, gt=0, le=300)
    retries: int = Field(default=3, ge=1, le=10)
    retry_rounds: int = Field(default=2, ge=0, le=20)
    retry_delay: float = Field(default=30, ge=0, le=600)
    browser_executable: str = ""
    browser_mode: Literal["extension", "separate"] = "extension"
    verification_timeout: float = Field(default=180, gt=0, le=3600)

    @field_validator("output_dir")
    @classmethod
    def nonempty_output(cls, value):
        if not value.strip():
            raise ValueError("输出目录不能为空")
        return value.strip()


def canonical(url: str) -> str:
    adapter = adapter_for_url(url)
    return adapter.normalize_url(adapter.canonical_catalog_url(url))


def book_id(url: str) -> str:
    return sha256(canonical(url).encode()).hexdigest()[:20]


class WebClient:
    """All HTTP and browser operations belong to the queue's one worker thread."""

    supports_concurrent_requests = False

    def __init__(self, settings, cancel, approved, force_browser=False, browser_factory=BrowserHttpClient):
        self.settings = settings
        self.cancel = cancel
        self.approved = approved
        self.force_browser = force_browser
        self.browser_factory = browser_factory
        self.http = HttpClient(settings.timeout, settings.retries)
        self.browser = None
        self.extension_bridge = None

    def _browser(self):
        if self.browser is None:
            if self.extension_bridge is not None and self.settings.browser_mode == "extension":
                deadline = time.monotonic() + self.settings.verification_timeout
                if not self.extension_bridge.ready:
                    emit("needs_browser", message="请安装并连接浏览器扩展，然后刷新书页；任务将自动在当前浏览器继续")
                while not self.extension_bridge.ready:
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise AccessBlockedError("等待浏览器扩展连接超时；安装扩展并刷新书页后可重试。")
                    self.cancel.wait(.1)
                check_cancelled()
                emit("running", message="正在当前浏览器的任务标签页继续")
                self.browser = ExtensionClient(self.extension_bridge, self.settings.timeout, self.settings.verification_timeout)
                return self.browser
            emit("needs_browser", message="需要浏览器访问，请点击“打开验证浏览器”")
            deadline = time.monotonic() + self.settings.verification_timeout
            while not self.approved.is_set():
                check_cancelled()
                if time.monotonic() >= deadline:
                    raise AccessBlockedError("等待打开浏览器超时；可点击通过浏览器重试。")
                self.cancel.wait(0.1)
            check_cancelled()
            emit("running", message="正在连接验证浏览器")
            self.browser = self.browser_factory(
                timeout=self.settings.timeout,
                executable_path=self.settings.browser_executable or None,
                verification_timeout=self.settings.verification_timeout,
                cdp_url="auto",
                profile_dir=self.profile_dir,
            )
        return self.browser

    def fetch(self, url, **kwargs):
        check_cancelled()
        if self.force_browser or self.browser is not None or adapter_for_url(url).requires_browser:
            return self._browser().fetch(url, **kwargs)
        try:
            return self.http.fetch(url, **kwargs)
        except VerificationPageError:
            return self._browser().fetch(url, **kwargs)
        except AccessBlockedError as error:
            emit("blocked", message=str(error))
            raise

    def page_search(self, adapter, query, limit, *, verification_timeout=None):
        check_cancelled()
        return self._browser().page_search(adapter, query, limit, verification_timeout=verification_timeout)

    def close(self):
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            self.http.close()


class TaskManager:
    def __init__(self, root: Path, *, state_dir=None, cache_root=None, client_factory=WebClient):
        self.root = Path(root).resolve()
        self.state_dir = Path(state_dir or self.root / ".local/web")
        self.cache_root = Path(cache_root or self.root / "cache")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.client_factory = client_factory
        self.browser_bridge = BrowserBridge()
        self.lock = RLock()
        self.queue = Queue()
        self.shutdown_event = Event()
        self.controls = {}
        self.notices = []
        self.settings = Settings.model_validate(self._load("settings.json", {}))
        self.hidden_books = self._load("books.json", {})
        self.removals = self._load("removals.json", {})
        self.tasks = self._load("tasks.json", {})
        for task in self.tasks.values():
            if task["status"] in ACTIVE:
                task["status"] = "paused" if task["status"] == "queued" else "interrupted"
                task["progress"]["message"] = "后台已重启，点击继续创建恢复任务"
        self._save_tasks()
        self.thread = Thread(target=self._work, name="book-download-worker", daemon=True)

    def _load(self, filename, default):
        path = self.state_dir / filename
        if not path.exists():
            return default
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("记录必须是对象")
            return value
        except (OSError, ValueError) as error:
            raise DownloaderError(f"无法读取界面记录：{path}；请先备份该文件后恢复。") from error

    def _save(self, filename, value):
        path = self.state_dir / filename
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _save_tasks(self):
        self._save("tasks.json", self.tasks)

    def start(self):
        self.thread.start()

    def output_dir(self, settings=None):
        value = Path((settings or self.settings).output_dir).expanduser()
        return (self.root / value).resolve() if not value.is_absolute() else value.resolve()

    def update_settings(self, value):
        settings = Settings.model_validate(value)
        with self.lock:
            self._save("settings.json", settings.model_dump())
            self.settings = settings
        return settings.model_dump()

    def list_tasks(self):
        with self.lock:
            return [self._public(t) for t in reversed(list(self.tasks.values()))]

    def get(self, task_id):
        with self.lock:
            return self._public(self.tasks[task_id])

    def _public(self, task):
        result = deepcopy(task)
        result["file_available"] = self.task_file(task["id"]) is not None
        return result

    def submit(self, kind, *, url="", query="", force_browser=False, settings=None):
        if kind not in KINDS:
            raise ValueError("不支持的任务类型")
        url, query = url.strip(), query.strip()
        if kind == "search":
            if not query:
                raise ValueError("请输入搜索关键词")
        else:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError("请输入有效的 HTTP 或 HTTPS 目录/章节链接")
        with self.lock:
            if self.shutdown_event.is_set():
                raise ValueError("后台正在退出，暂不接收新任务")
            snapshot = Settings.model_validate(settings or self.settings.model_dump()).model_dump()
            request = dict(kind=kind, url=url, query=query, force_browser=force_browser, settings=snapshot)
            for task in self.tasks.values():
                if task["status"] in ACTIVE and task.get("request") == request:
                    return self._public(task)
            if kind != "search":
                adapter = adapter_for_url(url)
                identifier = book_id(adapter.guess_catalog_url(url) or url)
                if identifier in self.removals:
                    raise ValueError("该书有未完成的永久删除；请先在书架重新确认并完成文件清理。")
                self._restore_book(adapter.guess_catalog_url(url) or url)
            task_id = uuid4().hex
            task = dict(
                id=task_id, kind=kind, url=url, query=query, title=query or url,
                status="queued", created_at=time.time(), updated_at=time.time(),
                settings=snapshot, force_browser=force_browser, request=request,
                identity_resolved=kind not in {"download", "update"},
                progress=dict(phase="queued", current=0, total=0, cached=0, message="等待前面的任务完成"),
                result=None, results=[], warnings=[], error=None, detail=None,
            )
            self.tasks[task_id] = task
            self.controls[task_id] = (Event(), Event())
            try:
                self._save_tasks()
            except OSError:
                del self.tasks[task_id]
                del self.controls[task_id]
                raise
            self.queue.put(task_id)
            return self._public(task)

    def cancel(self, task_id):
        with self.lock:
            task = self.tasks[task_id]
            if task["status"] not in ACTIVE:
                return self._public(task)
            self.controls[task_id][0].set()
            task["status"] = "cancelled" if task["status"] == "queued" else "stopping"
            task["updated_at"] = time.time()
            task["progress"]["message"] = "已取消排队" if task["status"] == "cancelled" else "正在停止；已完成缓存将保留"
            self._save_tasks()
            return self._public(task)

    def approve_browser(self, task_id):
        with self.lock:
            task = self.tasks[task_id]
            if task["status"] != "needs_browser":
                raise ValueError("该任务当前无需打开验证浏览器")
            self.controls[task_id][1].set()
            return self._public(task)

    def focus_browser(self, task_id):
        with self.lock:
            task = self.tasks[task_id]
            session = task.get("browser_session")
            if task["status"] not in ACTIVE or not session or task["settings"].get("browser_mode", "extension") != "extension":
                raise ValueError("该任务当前没有可前往的浏览器任务标签页。")
            self.browser_bridge.request_focus(session)
            return {"message": "已请求切回该任务的标签页"}

    def retry(self, task_id, force_browser=False, use_current_settings=False):
        with self.lock:
            task = deepcopy(self.tasks[task_id])
        if task["status"] in ACTIVE:
            raise ValueError("请先等待任务结束或停止任务")
        return self.submit(task["kind"], url=task["url"], query=task["query"],
                           force_browser=force_browser or task["force_browser"],
                           settings=None if use_current_settings else task["settings"])

    def stop(self):
        with self.lock:
            self.shutdown_event.set()
            for task_id in list(self.tasks):
                if self.tasks[task_id]["status"] in ACTIVE:
                    self.cancel(task_id)

    def _on_event(self, task, event):
        with self.lock:
            kind = event["kind"]
            if kind == "browser_tab":
                task["browser_session"] = event["session"]
            elif kind == "search_results":
                task["results"] = event["results"]
                task["progress"].update(phase="search", current=event["current"], total=event["total"],
                    message=f"已搜索 {event['current']}/{event['total']} 个站点，找到 {len(event['results'])} 个结果")
            elif kind == "warning":
                if event["message"] not in task["warnings"]:
                    task["warnings"].append(event["message"])
            elif kind == "blocked":
                task["error"] = event["message"]
            elif kind == "output":
                task["result"] = {key: value for key, value in event.items() if key != "kind"}
                stat = Path(event["output"]).stat()
                task["output_stamp"] = [stat.st_mtime_ns, stat.st_size]
            elif kind in {"needs_browser", "verification", "running"}:
                if task["status"] != "stopping":
                    task["status"] = "waiting_verification" if kind == "verification" else kind
                task["progress"]["message"] = event.get("message", "")
            elif kind == "progress":
                if task["status"] not in {"stopping", "needs_browser"}:
                    task["status"] = "running"
                for key in ("phase", "current", "total", "message"):
                    if key in event:
                        task["progress"][key] = event[key]
                if event.get("cached"):
                    task["progress"]["cached"] += 1
            task["updated_at"] = time.time()
            if kind != "progress":
                self._save_tasks()

    def _work(self):
        while not self.shutdown_event.is_set():
            try:
                task_id = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                with self.lock:
                    task = self.tasks[task_id]
                    if task["status"] != "queued":
                        continue
                    task["status"] = "running"
                    task["updated_at"] = time.time()
                    self._save_tasks()
                self._execute(task)
            except OSError:
                LOGGER.exception("Could not persist task %s", task_id)
                with self.lock:
                    task["status"] = "failed"
                    task["error"] = "任务记录保存失败；已完成缓存和 TXT 保留，请检查 .local/web 的权限或磁盘空间后重试。"
                    task["updated_at"] = time.time()
            finally:
                self.queue.task_done()

    def _execute(self, task):
        cancel, approved = self.controls[task["id"]]
        settings = Settings.model_validate(task["settings"])
        client = None
        try:
            with operation_scope(cancel, lambda event: self._on_event(task, event)):
                check_cancelled()
                if task["kind"] != "merge":
                    client = self.client_factory(settings, cancel, approved, task["force_browser"])
                    if self.client_factory is WebClient:
                        client.extension_bridge = self.browser_bridge
                    client.profile_dir = self.cache_root / "browser-profile"
                if task["kind"] == "search":
                    results = search_sites(client, task["query"], verification_timeout=settings.verification_timeout)
                    task["results"] = [asdict(item) for item in results]
                    if task["error"]:
                        if task["error"] not in task["warnings"]:
                            task["warnings"].append(task["error"])
                        task["error"] = None
                else:
                    adapter = adapter_for_url(task["url"])
                    output_dir = self.output_dir(settings)
                    common = dict(adapter=adapter, cache_root=self.cache_root, output_dir=output_dir)
                    if task["kind"] in {"download", "update"}:
                        emit("progress", phase="discover", message="正在识别目录")
                        plan = discover_book(client, adapter, task["url"])
                        with self.lock:
                            task["title"] = plan.title
                            task["url"] = canonical(plan.catalog_url)
                            task["identity_resolved"] = True
                            self._restore_book(task["url"])
                            self._save_tasks()
                        result = download_book(client=client, plan=plan,
                            output=self._output_path(output_dir, plan.title, task["url"]),
                            delay=settings.delay, max_pages=50, refresh=False, **common)
                    else:
                        url = adapter.guess_catalog_url(task["url"]) or task["url"]
                        task["url"] = canonical(url)
                        plan_path = self.cache_root / adapter.name / cache_key(task["url"]) / "book.json"
                        title = task["title"]
                        if plan_path.is_file():
                            title = json.loads(plan_path.read_text(encoding="utf-8"))["title"]
                        task["title"] = title
                        output = self._output_path(output_dir, title, task["url"])
                        if task["kind"] == "merge":
                            result = merge_from_cache(catalog_url=task["url"], output=output, **common)
                        else:
                            result = fill_cache_gaps(client=client, catalog_url=task["url"], output=output,
                                delay=settings.delay, max_pages=50, retry_delay=settings.retry_delay,
                                retry_rounds=settings.retry_rounds, **common)
                    task["result"] = {**asdict(result), "output": str(result.output)}
                if cancel.is_set():
                    task["status"] = "cancelled"
                elif task["result"] and (task["result"]["missing_chapters"] or task["result"]["failed_chapters"]):
                    task["status"] = "partial"
                else:
                    task["status"] = "completed"
        except OperationCancelled:
            task["status"] = "cancelled"
        except AccessBlockedError as error:
            task["status"], task["error"] = "blocked", str(error)
        except (DownloaderError, OSError, ValueError) as error:
            task["status"] = "cancelled" if cancel.is_set() else ("blocked" if task["error"] else "failed")
            task["error"] = task["error"] or str(error)
        except Exception:
            LOGGER.exception("Unexpected failure in task %s", task["id"])
            task["status"], task["error"] = "failed", "任务发生内部错误，请查看后台日志后重试。"
            task["detail"] = f"任务编号：{task['id']}；日志：{self.state_dir / 'server.log'}"
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                LOGGER.exception("Could not close task client")
            with self.lock:
                task["updated_at"] = time.time()
                task["progress"]["message"] = {
                    "completed": "任务完成", "partial": "当前 TXT 是部分结果，请查看缺失项并补齐",
                    "cancelled": "已停止，已完成缓存保留", "blocked": "访问受阻，可通过浏览器重试",
                    "failed": "任务失败，请查看恢复建议",
                }.get(task["status"], task["status"])
                self._save_tasks()

    def _output_path(self, output_dir, title, url):
        for sidecar, source in self._sidecars(output_dir):
            if canonical(source) == canonical(url):
                path = sidecar.with_suffix(".txt")
                if not path.resolve().is_relative_to(output_dir):
                    raise DownloaderError(f"输出路径指向配置目录以外：{path}；请修正该链接后重试。")
                return path
        path = output_dir / f"{safe_filename(title)}.txt"
        if path.exists() or path.with_suffix(".url").exists():
            path = output_dir / f"{safe_filename(title)}-{book_id(url)[:8]}.txt"
        if not path.resolve().is_relative_to(output_dir) or path.exists():
            raise DownloaderError(f"输出路径已被其他文件占用或指向目录外：{path}；请修正输出目录后重试。")
        return path

    def _notice(self, message):
        with self.lock:
            if message not in self.notices:
                self.notices.append(message)
        emit("warning", message=message)

    def _sidecars(self, output_dir):
        for sidecar in output_dir.glob("*.url"):
            if not sidecar.is_file():
                continue
            try:
                source = sidecar.read_text(encoding="utf-8").strip()
                if source:
                    canonical(source)
                    yield sidecar, source
            except (OSError, ValueError):
                self._notice(f"来源文件无法读取：{sidecar}；请先备份并修正或移走该文件，其他书目仍可使用。")

    def task_file(self, task_id):
        task = self.tasks[task_id]
        result = task.get("result")
        if not result:
            return None
        path = Path(result["output"]).resolve()
        if path.is_relative_to(self.output_dir(Settings.model_validate(task["settings"]))) and path.is_file():
            stat = path.stat()
            if task.get("output_stamp") != [stat.st_mtime_ns, stat.st_size]:
                return None
            source = path.with_suffix(".url")
            if source.is_file():
                try:
                    if canonical(source.read_text(encoding="utf-8").strip()) != canonical(task["url"]):
                        return None
                except (OSError, ValueError):
                    return None
            return path
        return None

    def books(self):
        with self.lock:
            output_dir = self.output_dir()
            self.notices = []
            rows = []
            for entry in load_library(self.cache_root, output_dir, on_warning=self._notice):
                url = canonical(entry.catalog_url)
                if book_id(url) in self.hidden_books:
                    continue
                path = self.cache_root / entry.site / cache_key(url) / "book.json"
                cached, total, issue = None, None, None
                if path.is_file():
                    try:
                        chapters = json.loads(path.read_text(encoding="utf-8"))["chapters"]
                        total = len(chapters)
                        cached = 0
                        for chapter in chapters:
                            file = path.parent / "chapters" / f"{int(chapter['number']):06d}.txt"
                            if file.is_file() and file.read_text(encoding="utf-8").strip():
                                cached += 1
                    except (OSError, ValueError, KeyError, TypeError):
                        issue = "部分缓存或目录记录无法读取，请查看任务中的恢复建议"
                latest = next((t for t in reversed(list(self.tasks.values())) if t["kind"] != "search" and canonical(t["url"]) == url), None)
                rows.append(dict(id=book_id(url), title=entry.title, site=entry.site,
                    catalog_url=url, catalog_chapters=total, cached_chapters=cached,
                    missing_count=total - cached if total is not None and cached is not None else None,
                    has_cache=path.is_file(), file_available=self.book_file(book_id(url)) is not None,
                    last_task={k: latest[k] for k in ("id", "status", "updated_at")} if latest else None,
                    warning=issue))
            for identifier, removal in self.removals.items():
                row = next((item for item in rows if item["id"] == identifier), None)
                if row is None:
                    rows.append(self._pending_book_row(identifier, removal))
                else:
                    row["pending_cleanup"] = True
                    row["warning"] = "上次永久删除未完成，请重新确认继续清理。"
            return rows

    def _pending_book_row(self, identifier, removal):
        url = canonical(removal["catalog_url"])
        latest = next((t for t in reversed(list(self.tasks.values())) if t["kind"] != "search" and canonical(t["url"]) == url), None)
        return dict(
            id=identifier, title=removal.get("title") or "未知书名",
            site=removal.get("site") or adapter_for_url(url).name, catalog_url=url,
            catalog_chapters=None, cached_chapters=None, missing_count=None,
            has_cache=any(Path(path).is_dir() for path in removal.get("cache_dirs", [])),
            file_available=self.book_file(identifier) is not None,
            last_task={k: latest[k] for k in ("id", "status", "updated_at")} if latest else None,
            warning="上次永久删除未完成，请重新确认继续清理。", pending_cleanup=True,
        )

    def _restore_book(self, url):
        identifier = book_id(url)
        if identifier in self.hidden_books:
            updated = {key: value for key, value in self.hidden_books.items() if key != identifier}
            self._save("books.json", updated)
            self.hidden_books = updated

    def _check_book_idle(self, book):
        for task in self.tasks.values():
            if task["kind"] == "search" or task["status"] not in ACTIVE:
                continue
            adapter = adapter_for_url(task["url"])
            if not task.get("identity_resolved", False):
                if adapter.name == book["site"]:
                    raise BookBusyError("该书站点有正在识别目录的任务，请等待任务确定目标后再删除。")
                continue
            guessed = adapter.guess_catalog_url(task["url"])
            if book_id(guessed or task["url"]) == book["id"]:
                raise BookBusyError("该书有排队或执行中的任务，请先停止任务并等待结束后再删除。")

    @staticmethod
    def _removal_path(path, root):
        # Refuse redirected paths before making any destructive changes.
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"无法清理目录外的链接：{path}；请先检查并移走该链接。")
        current = path
        while current != root:
            if current.is_symlink():
                raise ValueError(f"无法自动清理符号链接：{current}；请先检查并移走该链接。")
            current = current.parent
        return path

    @staticmethod
    def _recorded_output_root(value):
        root = Path(value)
        if not root.is_absolute():
            raise ValueError(f"清理记录中的输出目录不是绝对路径：{root}；请检查该记录。")
        try:
            redirected = root.resolve() != root
        except (OSError, RuntimeError) as error:
            raise ValueError(f"无法核验清理记录中的输出目录：{root}；请检查该路径。") from error
        if redirected:
            raise ValueError(
                f"清理记录中的输出目录已被链接替换：{root}；"
                "请先恢复原目录后再重试，不会扫描链接目标。"
            )
        return root

    def _removal_targets(self, book, pending=None):
        directories, files, sidecars = set(), set(), set()
        site_cache = self.cache_root / adapter_for_url(book["catalog_url"]).name
        for manifest in site_cache.glob("*/book.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                matches = book_id(data["catalog_url"]) == book["id"]
            except (OSError, ValueError, KeyError, TypeError):
                # Corrupt metadata cannot establish ownership; do not guess.
                continue
            if matches:
                self._removal_path(manifest, self.cache_root)
                directories.add(self._removal_path(manifest.parent, self.cache_root))
        for value in (pending or {}).get("cache_dirs", []):
            directory = self._removal_path(Path(value), self.cache_root)
            manifest = directory / "book.json"
            if manifest.is_file():
                try:
                    current = json.loads(manifest.read_text(encoding="utf-8"))
                    matches = book_id(current["catalog_url"]) == book["id"]
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"待清理缓存目录的目录记录无法核验：{manifest}；请先检查该缓存。") from error
                if not matches:
                    raise ValueError(f"待清理缓存目录已属于其他书目：{directory}；请先检查该缓存。")
            directories.add(directory)
        roots = {self.output_dir()}
        for value in (pending or {}).get("output_dirs", []):
            roots.add(self._recorded_output_root(value))
        for task in self.tasks.values():
            if task["kind"] != "search" and book_id(task["url"]) == book["id"]:
                output_root = self.output_dir(Settings.model_validate(task["settings"]))
                roots.add(output_root)
                result = task.get("result") or {}
                if result.get("output"):
                    self._removal_path(Path(result["output"]), output_root)
                path = self.task_file(task["id"])
                if path is not None:
                    files.add(self._removal_path(path, output_root))
        for root in roots:
            for source, url in self._sidecars(root):
                if book_id(url) != book["id"]:
                    continue
                sidecars.add(self._removal_path(source, root))
                output = source.with_suffix(".txt")
                self._removal_path(output, root)
                if output.exists():
                    if not output.is_file():
                        raise ValueError(f"TXT 路径不是文件：{output}；请先检查该路径。")
                    files.add(output)
        return sorted(directories), sorted(files), sorted(sidecars), sorted(roots)

    def removal(self, identifier):
        with self.lock:
            book = next((row for row in self.books() if row["id"] == identifier), None)
            if book is None:
                raise KeyError(identifier)
            self._check_book_idle(book)
            return book

    def removal_preview(self, identifier):
        with self.lock:
            book = self.removal(identifier)
            pending = self.removals.get(identifier)
            try:
                directories, files, sidecars, _ = self._removal_targets(book, pending)
            except ValueError as error:
                return {"title": book["title"], "cache_dirs": [], "files": [],
                        "cleanup_error": str(error), "pending_cleanup": bool(pending)}
            return {"title": book["title"], "cache_dirs": [str(p) for p in directories],
                    "files": [str(p) for p in files + sidecars], "pending_cleanup": bool(pending)}

    def remove_book(self, identifier, *, confirm=False, delete_files=False):
        if not confirm:
            raise ValueError("请先确认删除书籍；默认仅从书架移除并保留本地文件。")
        with self.lock:
            book = self.removal(identifier)
            if delete_files:
                pending = self.removals.get(identifier)
                directories, files, sidecars, output_dirs = self._removal_targets(book, pending)
                updated_removals = {
                    **self.removals,
                    identifier: {
                        "catalog_url": book["catalog_url"],
                        "title": book["title"],
                        "site": book["site"],
                        "cache_dirs": [str(path) for path in directories],
                        "output_dirs": [str(path) for path in output_dirs],
                    },
                }
                self._save("removals.json", updated_removals)
                self.removals = updated_removals
                try:
                    for path in files:
                        path.unlink(missing_ok=True)
                    for path in directories:
                        if path.exists():
                            shutil.rmtree(path)
                    # Keep source identity until content cleanup succeeds.
                    for path in sidecars:
                        path.unlink(missing_ok=True)
                except OSError as error:
                    raise ValueError(f"清理未完成，部分文件可能已删除：{error}。清理记录已保留，请检查路径权限后重新确认；任务历史保留。") from error
                updated_hidden = {**self.hidden_books, identifier: book["catalog_url"]}
                self._save("books.json", updated_hidden)
                self.hidden_books = updated_hidden
                updated_removals = {key: value for key, value in self.removals.items() if key != identifier}
                self._save("removals.json", updated_removals)
                self.removals = updated_removals
            else:
                if identifier in self.removals:
                    raise ValueError("该书有未完成的永久删除；请重新确认并完成文件清理。")
                updated = {**self.hidden_books, identifier: book["catalog_url"]}
                self._save("books.json", updated)
                self.hidden_books = updated
            return {"message": "已删除书籍及识别到的本地文件" if delete_files else "已从书架移除，本地文件保留"}

    def book_file(self, identifier):
        output_dir = self.output_dir()
        for sidecar, source in self._sidecars(output_dir):
            if book_id(source) == identifier:
                file = sidecar.with_suffix(".txt").resolve()
                if file.is_relative_to(output_dir) and file.is_file():
                    return file
        return None
