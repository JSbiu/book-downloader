import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread, get_ident
import time
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("需要安装可选 Web 测试依赖")

from book_downloader.cache import BookCache
from book_downloader.errors import AccessBlockedError, VerificationPageError
from book_downloader.http import FetchedPage, HttpClient
from book_downloader.models import BookPlan, Chapter, ChapterLink
from book_downloader.operation import OperationCancelled, check_cancelled, emit, operation_scope
from book_downloader.web.app import create_app
from book_downloader.web.tasks import TaskManager, WebClient


CATALOG = "https://www.trxs.cc/tongren/12345.html"
URLS = [f"https://www.trxs.cc/tongren/12345/{n}.html" for n in (1, 2, 3)]
BODIES = [f"示例第{n}章完整正文。" * 12 for n in (1, 2, 3)]
PLAN = BookPlan("示例小说", CATALOG, tuple(ChapterLink(n, f"第{n}章 示例章节{n}", url) for n, url in enumerate(URLS, 1)))
PAGES = {CATALOG: '<h1>示例小说</h1><div id="list"><dl>' + ''.join(
    f'<dd><a href="{link.url}">{link.title}</a></dd>' for link in PLAN.chapters
) + '</dl></div>'}
PAGES.update({url: f'<h1>第{n}章 示例章节{n}</h1><div class="read_chapterDetail">{body}</div>'
              for n, (url, body) in enumerate(zip(URLS, BODIES), 1)})


class FixtureClient:
    supports_concurrent_requests = False

    def __init__(self, owner):
        self.owner = owner
        self.owner.threads.append(get_ident())

    def fetch(self, url, **kwargs):
        self.owner.threads.append(get_ident())
        self.owner.calls.append(url)
        if self.owner.fetch_hook:
            return self.owner.fetch_hook(url)
        page = self.owner.pages[url]
        if isinstance(page, BaseException):
            raise page
        return FetchedPage(url, page)

    def close(self):
        self.owner.threads.append(get_ident())
        self.owner.closed += 1


class WebTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pages = dict(PAGES)
        self.calls, self.threads = [], []
        self.closed = 0
        self.fetch_hook = None
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("真实网络入口被禁用"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.manager = TaskManager(self.root, client_factory=lambda *args: FixtureClient(self))
        self.manager.update_settings({**self.manager.settings.model_dump(), "delay": 0, "retry_delay": 0, "retry_rounds": 0})
        self.client = TestClient(create_app(manager=self.manager))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def submit(self, kind="download", url=CATALOG, **kwargs):
        response = self.client.post("/api/tasks", json=dict(kind=kind, url=url, **kwargs))
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def terminal(self, identifier):
        return self.await_status(identifier, {"completed", "partial", "failed", "blocked", "cancelled"})

    def await_status(self, identifier, statuses):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            value = self.client.get(f"/api/tasks/{identifier}").json()
            if value["status"] in statuses:
                return value
            Event().wait(0.01)
        self.fail(f"任务未到达 {statuses}: {value}")

    def seed(self, numbers=(1, 2, 3)):
        cache = BookCache(self.root / "cache", "trxs_cc", CATALOG)
        cache.save_plan(PLAN)
        for n in numbers:
            cache.write(Chapter(n, PLAN.chapters[n-1].title, BODIES[n-1]))
        return cache

    def test_download_refresh_file_and_book_actions(self):
        result = self.terminal(self.submit()["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["total_chapters"], 3)
        self.assertTrue(result["file_available"])
        content = self.client.get(f"/api/tasks/{result['id']}/file").text
        for body in BODIES:
            self.assertIn(body, content)
        self.assertEqual(self.calls, [CATALOG, *URLS])
        self.assertEqual(len(set(self.threads)), 1)
        self.assertEqual(self.closed, 1)
        # A new page/client sees the same queue rather than creating a new worker.
        another = TestClient(self.client.app)
        self.assertEqual(another.get("/api/tasks").json()[0]["id"], result["id"])
        book = another.get("/api/books").json()[0]
        self.assertEqual((book["catalog_chapters"], book["cached_chapters"], book["missing_count"]), (3, 3, 0))
        self.assertEqual(another.get(f"/api/books/{book['id']}/file").text, content)
        self.calls.clear()
        self.assertEqual(self.terminal(self.submit("update")["id"])["status"], "completed")
        self.assertEqual(self.calls, [CATALOG])

    def test_remove_book_keeps_files_persists_and_readd_restores(self):
        task = self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        endpoint = f"/api/books/{book['id']}/remove"
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={"confirm": True}).status_code, 200)
        self.assertEqual(self.client.get("/api/books").json(), [])
        self.assertTrue(Path(task["result"]["output"]).is_file())
        restored = TaskManager(self.root)
        self.assertEqual(restored.books(), [])
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code, 200)
        self.terminal(self.submit(url=URLS[0])["id"])
        self.assertEqual(len(self.client.get("/api/books").json()), 1)

    def test_remove_book_cleanup_is_scoped_and_includes_old_output(self):
        task = self.terminal(self.submit()["id"])
        output = Path(task["result"]["output"])
        cache = self.seed()
        stale = cache.path / "chapters.stale-example"
        stale.mkdir()
        (stale / "000001.txt").write_text("旧缓存")
        other_url = CATALOG.replace("12345", "54321")
        other = BookCache(self.root / "cache", "trxs_cc", other_url)
        other.save_plan(BookPlan("另一示例", other_url, ()))
        other_file = output.parent / "另一示例.txt"
        other_file.write_text("保留内容")
        other_file.with_suffix(".url").write_text(other_url)
        profile = self.root / "cache/browser-profile"
        profile.mkdir()
        (profile / "keep").write_text("保留登录态")
        self.manager.update_settings({**self.manager.settings.model_dump(), "output_dir": "new-outputs"})
        book = self.client.get("/api/books").json()[0]
        if book["catalog_url"] != CATALOG:
            book = self.client.get("/api/books").json()[1]
        preview = self.client.get(f"/api/books/{book['id']}/removal").json()
        self.assertIn(str(output), preview["files"])
        response = self.client.post(f"/api/books/{book['id']}/remove", json={"confirm": True, "delete_files": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(cache.path.exists())
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".url").exists())
        self.assertTrue(other.path.exists())
        self.assertEqual(other_file.read_text(), "保留内容")
        self.assertTrue((profile / "keep").exists())
        self.assertFalse(self.manager.task_file(task["id"]))

    def test_remove_rejects_active_chapter_task_unknown_id_and_escaping_symlink(self):
        cache = self.seed()
        book = self.client.get("/api/books").json()[0]
        endpoint = f"/api/books/{book['id']}/remove"
        gate, started = Event(), Event()
        self.addCleanup(gate.set)
        def fetch(url):
            started.set()
            self.assertTrue(gate.wait(3))
            return FetchedPage(url, PAGES[url])
        self.fetch_hook = fetch
        task = self.submit(url=URLS[0])
        self.assertTrue(started.wait(2))
        self.assertEqual(self.client.post(endpoint, json={"confirm": True, "delete_files": True}).status_code, 409)
        self.assertTrue(cache.path.exists())
        gate.set()
        self.terminal(task["id"])
        self.assertEqual(self.client.post("/api/books/unknown/remove", json={"confirm": True}).status_code, 404)
        output = self.manager.book_file(book["id"])
        outside = self.root / "outside.txt"
        outside.write_text("不要删除")
        output.unlink()
        output.symlink_to(outside)
        self.assertEqual(self.client.post(endpoint, json={"confirm": True, "delete_files": True}).status_code, 400)
        self.assertEqual(outside.read_text(), "不要删除")
        self.assertTrue(cache.path.exists())

    def test_cleanup_failure_keeps_book_visible_and_overwritten_history_is_preserved(self):
        task = self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        endpoint = f"/api/books/{book['id']}/remove"
        with patch("book_downloader.web.tasks.shutil.rmtree", side_effect=OSError("磁盘只读")):
            response = self.client.post(endpoint, json={"confirm": True, "delete_files": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("未完成", response.json()["detail"])
        self.assertEqual(len(self.client.get("/api/books").json()), 1)
        output = Path(task["result"]["output"])
        output.write_text("另一本书的内容")
        output.with_suffix(".url").write_text(CATALOG.replace("12345", "54321"))
        self.assertEqual(self.client.post(endpoint, json={"confirm": True, "delete_files": True}).status_code, 200)
        self.assertEqual(output.read_text(), "另一本书的内容")

    def test_remove_refuses_redirected_cache_but_allows_nondestructive_removal(self):
        cache = self.seed()
        book = self.manager.books()[0]
        outside = self.root / "outside-cache"
        cache.path.rename(outside)
        cache.path.symlink_to(outside, target_is_directory=True)
        preview = self.client.get(f"/api/books/{book['id']}/removal").json()
        self.assertIn("链接", preview["cleanup_error"])
        endpoint = f"/api/books/{book['id']}/remove"
        self.assertEqual(self.client.post(endpoint, json={"confirm": True, "delete_files": True}).status_code, 400)
        self.assertTrue((outside / "book.json").exists())
        self.assertEqual(self.client.post(endpoint, json={"confirm": True}).status_code, 200)
        self.assertEqual(self.manager.books(), [])
        self.assertTrue((outside / "book.json").exists())

    def test_remove_rejects_historical_output_symlink_without_deleting(self):
        task = self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        output = Path(task["result"]["output"])
        preserved = output.with_name("保留正文.txt")
        output.rename(preserved)
        output.symlink_to(preserved.name)
        output.with_suffix(".url").unlink()
        cache = self.seed()

        response = self.client.post(
            f"/api/books/{book['id']}/remove",
            json={"confirm": True, "delete_files": True},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("符号链接", response.json()["detail"])
        self.assertTrue(preserved.read_text())
        self.assertTrue(output.is_symlink())
        self.assertTrue(cache.path.exists())
        self.assertEqual(len(self.client.get("/api/books").json()), 1)

    def test_remove_rejects_historical_output_parent_symlink(self):
        task = self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        output = Path(task["result"]["output"])
        link_dir = self.root / "linked-output"
        link_dir.symlink_to(output.parent, target_is_directory=True)
        preserved = link_dir / output.name
        moved = preserved.rename(self.root / "preserved.txt")
        preserved.symlink_to((self.root / "preserved.txt").name)
        output.with_suffix(".url").unlink()

        response = self.client.post(
            f"/api/books/{book['id']}/remove",
            json={"confirm": True, "delete_files": True},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("符号链接", response.json()["detail"])
        self.assertTrue(moved.read_text())
        self.assertTrue((self.root / "cache/trxs_cc").is_dir())
        self.assertEqual(len(self.client.get("/api/books").json()), 1)

    def test_pending_cleanup_survives_restart_and_retries_missing_cache(self):
        task = self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        cache_root = self.root / "cache"
        target = next((cache_root / "trxs_cc").glob("*/book.json")).parent

        def fail_after_manifest(path):
            (Path(path) / "book.json").unlink()
            raise OSError("离线模拟：目录删除失败")

        with patch("book_downloader.web.tasks.shutil.rmtree", side_effect=fail_after_manifest):
            first = self.client.post(
                f"/api/books/{book['id']}/remove",
                json={"confirm": True, "delete_files": True},
            )
        self.assertEqual(first.status_code, 400)
        self.assertTrue(target.exists())
        self.assertFalse((target / "book.json").exists())
        restarted = TaskManager(self.root)
        pending = next(row for row in restarted.books() if row["id"] == book["id"])
        self.assertTrue(pending["pending_cleanup"])
        self.assertEqual(restarted.removal_preview(book["id"])["pending_cleanup"], True)
        second = restarted.remove_book(book["id"], confirm=True, delete_files=True)
        self.assertIn("已删除", second["message"])
        self.assertFalse(target.exists())
        self.assertFalse(restarted.removals)
        self.assertFalse(restarted.task_file(task["id"]))

    def test_pending_cleanup_requires_files_until_removal_finishes(self):
        self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        with patch("book_downloader.web.tasks.shutil.rmtree", side_effect=OSError("磁盘只读")):
            failed = self.client.post(
                f"/api/books/{book['id']}/remove",
                json={"confirm": True, "delete_files": True},
            )
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(
            self.client.post(
                f"/api/books/{book['id']}/remove",
                json={"confirm": True},
            ).status_code,
            400,
        )
        self.assertIn("未完成", self.client.get("/api/books").json()[0]["warning"])

    def test_pending_cleanup_record_write_failure_deletes_nothing(self):
        self.terminal(self.submit()["id"])
        book = self.client.get("/api/books").json()[0]
        cache = next((self.root / "cache/trxs_cc").glob("*/book.json")).parent
        output = self.manager.book_file(book["id"])
        original_save = self.manager._save

        def fail_removal_save(filename, value):
            if filename == "removals.json":
                raise OSError("离线模拟：清理记录保存失败")
            return original_save(filename, value)

        with patch.object(self.manager, "_save", side_effect=fail_removal_save):
            response = self.client.post(
                f"/api/books/{book['id']}/remove",
                json={"confirm": True, "delete_files": True},
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue((cache / "book.json").is_file())
        self.assertTrue(output.is_file())
        self.assertEqual(self.manager.removals, {})
        self.assertEqual(len(self.client.get("/api/books").json()), 1)

    def test_pending_cleanup_remembers_cli_output_root_after_settings_change(self):
        source_cache = BookCache(self.root / "cache", "trxs_cc", CATALOG)
        source_cache.save_plan(PLAN)
        original_output = self.root / "old-outputs"
        original_output.mkdir()
        old_txt = original_output / "示例小说.txt"
        old_txt.write_text(next(iter(BODIES)))
        old_sidecar = old_txt.with_suffix(".url")
        old_sidecar.write_text(CATALOG)
        other_url = CATALOG.replace("12345", "54321")
        other_txt = original_output / "另一示例.txt"
        other_txt.write_text("保留内容")
        other_txt.with_suffix(".url").write_text(other_url)
        self.manager.update_settings(
            {**self.manager.settings.model_dump(), "output_dir": "old-outputs"}
        )
        book = next(row for row in self.manager.books() if row["catalog_url"] == CATALOG)
        self.assertFalse(self.manager.tasks)

        with patch(
            "book_downloader.web.tasks.shutil.rmtree",
            side_effect=OSError("离线模拟：目录删除失败"),
        ):
            failed = self.client.post(
                f"/api/books/{book['id']}/remove",
                json={"confirm": True, "delete_files": True},
            )
        self.assertEqual(failed.status_code, 400)
        self.assertFalse(old_txt.exists())
        self.assertTrue(old_sidecar.is_file())
        pending = TaskManager(self.root)
        self.assertEqual(
            pending.removals[book["id"]]["output_dirs"], [str(original_output)]
        )
        pending.update_settings(
            {**pending.settings.model_dump(), "output_dir": "new-outputs"}
        )
        restarted = TaskManager(self.root)
        with patch(
            "book_downloader.web.tasks.shutil.rmtree",
            side_effect=OSError("离线模拟：目录删除失败"),
        ):
            with self.assertRaises(ValueError):
                restarted.remove_book(book["id"], confirm=True, delete_files=True)
        self.assertTrue(old_sidecar.is_file())
        self.assertEqual(other_txt.read_text(), "保留内容")
        restarted.remove_book(book["id"], confirm=True, delete_files=True)
        self.assertFalse(old_sidecar.exists())
        self.assertFalse(source_cache.path.exists())
        self.assertEqual(other_txt.read_text(), "保留内容")
        self.assertEqual(other_txt.with_suffix(".url").read_text(), other_url)

    def test_pending_cleanup_rejects_replaced_output_root(self):
        source_cache = BookCache(self.root / "cache", "trxs_cc", CATALOG)
        source_cache.save_plan(PLAN)
        original_output = self.root / "old-outputs"
        original_output.mkdir()
        old_txt = original_output / "示例小说.txt"
        old_txt.write_text(next(iter(BODIES)))
        old_txt.with_suffix(".url").write_text(CATALOG)
        self.manager.update_settings(
            {**self.manager.settings.model_dump(), "output_dir": "old-outputs"}
        )
        book = next(row for row in self.manager.books() if row["catalog_url"] == CATALOG)
        with patch(
            "book_downloader.web.tasks.shutil.rmtree",
            side_effect=OSError("离线模拟：目录删除失败"),
        ):
            self.assertEqual(
                self.client.post(
                    f"/api/books/{book['id']}/remove",
                    json={"confirm": True, "delete_files": True},
                ).status_code,
                400,
            )
        outside = self.root / "outside-outputs"
        outside.mkdir()
        external = outside / "外部正文.txt"
        external.write_text("不要删除")
        pending = TaskManager(self.root)
        pending.update_settings(
            {**pending.settings.model_dump(), "output_dir": "new-outputs"}
        )
        original_output.rename(self.root / "preserved-outputs")
        original_output.symlink_to(outside, target_is_directory=True)
        restarted = TaskManager(self.root)

        response = self.client.post(
            f"/api/books/{book['id']}/remove",
            json={"confirm": True, "delete_files": True},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("链接替换", response.json()["detail"])
        self.assertTrue(source_cache.path.exists())
        self.assertEqual(external.read_text(), "不要删除")

    def test_remove_waits_for_unresolved_discovery_and_resolved_task(self):
        other = CATALOG.replace("12345", "54321")
        cache = BookCache(self.root / "cache", "trxs_cc", other)
        cache.save_plan(BookPlan("另一示例", other, ()))
        other_book = next(row for row in self.manager.books() if row["catalog_url"] == other)
        discovering, discovered = Event(), Event()
        plan = PLAN

        def discover(client, adapter, url):
            discovering.set()
            self.assertTrue(discovered.wait(3))
            return plan

        with patch("book_downloader.web.tasks.discover_book", side_effect=discover):
            task = self.submit(url=other)
            self.assertTrue(discovering.wait(2))
            response = self.client.post(
                f"/api/books/{other_book['id']}/remove",
                json={"confirm": True},
            )
            self.assertEqual(response.status_code, 409)
            self.assertTrue(cache.path.exists())
            discovered.set()
            resolved = self.await_status(task["id"], {"failed", "completed", "partial", "blocked"})
            self.assertTrue(resolved["identity_resolved"])
        self.assertEqual(
            self.client.post(
                f"/api/books/{other_book['id']}/remove",
                json={"confirm": True},
            ).status_code,
            200,
        )

    def test_retry_can_explicitly_use_saved_current_settings_without_changing_original(self):
        self.seed()
        original = self.terminal(self.submit("merge")["id"])
        old_output = Path(original["result"]["output"])
        old_content = old_output.read_bytes()
        current = {**self.manager.settings.model_dump(), "timeout": 7, "output_dir": "other-outputs", "browser_mode": "separate"}
        self.client.put("/api/settings", json=current)
        endpoint = f"/api/tasks/{original['id']}/retry"
        old_retry = self.client.post(endpoint, json={})
        self.assertEqual(old_retry.status_code, 202, old_retry.text)
        self.assertEqual(old_retry.json()["settings"], original["settings"])
        self.terminal(old_retry.json()["id"])
        retry = self.client.post(endpoint, json={"use_current_settings": True})
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["settings"], current)
        completed = self.terminal(retry.json()["id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(Path(completed["result"]["output"]).parent, self.root / "other-outputs")
        self.assertEqual(old_output.read_bytes(), old_content)
        self.assertEqual(self.manager.get(original["id"])["settings"], original["settings"])
        self.assertEqual(self.calls, [])  # Retrying an offline merge still creates no network client.

    def test_partial_fill_success_and_offline_missing(self):
        cache = self.seed((1,))
        self.pages[URLS[1]] = AccessBlockedError("HTTP 403；请求被拒绝")
        partial = self.terminal(self.submit("fill_gaps")["id"])
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["result"]["missing_chapters"], [2, 3])
        self.assertIn("403", partial["error"])
        self.assertNotIn("Traceback", partial["error"])
        offline = self.terminal(self.submit("merge")["id"])
        self.assertEqual(offline["status"], "partial")
        self.assertEqual(offline["result"]["missing_chapters"], [2, 3])
        self.assertEqual(self.calls, [URLS[1]])
        self.pages[URLS[1]] = PAGES[URLS[1]]
        complete = self.terminal(self.submit("fill_gaps")["id"])
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(self.calls, [URLS[1], URLS[1], URLS[2]])
        self.assertEqual(cache.read_chapter(1).content, BODIES[0])

    def test_blocked_first_chapter_is_not_reported_as_generic_failure(self):
        self.pages[URLS[0]] = AccessBlockedError("HTTP 403；请求被拒绝")
        result = self.terminal(self.submit()["id"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("403", result["error"])
        self.assertFalse(result["file_available"])
        self.assertEqual(self.calls, [CATALOG, URLS[0]])

    def test_duplicate_queue_settings_snapshot_and_cancel_before_start(self):
        gate, started = Event(), Event()
        def fetch(url):
            if url == CATALOG:
                started.set()
                self.assertTrue(gate.wait(3))
            return FetchedPage(url, PAGES[url])
        self.fetch_hook = fetch
        first = self.submit()
        self.assertTrue(started.wait(2))
        duplicate = self.submit()
        self.assertEqual(duplicate["id"], first["id"])
        settings = self.client.get("/api/settings").json()
        self.assertEqual(self.client.put("/api/settings", json={**settings, "timeout": 5}).status_code, 200)
        second = self.submit("update")
        self.assertEqual(second["status"], "queued")
        self.assertEqual(self.client.get(f"/api/tasks/{first['id']}").json()["settings"]["timeout"], 20)
        self.assertEqual(second["settings"]["timeout"], 5)
        cancelled = self.client.post(f"/api/tasks/{second['id']}/cancel", json={}).json()
        self.assertEqual(cancelled["status"], "cancelled")
        gate.set()
        self.assertEqual(self.terminal(first["id"])["status"], "completed")
        self.assertEqual(self.calls.count(CATALOG), 1)

    def test_stop_download_preserves_cache_and_resume_requests_only_missing(self):
        gate, started = Event(), Event()
        def fetch(url):
            if url == URLS[1]:
                started.set()
                self.assertTrue(gate.wait(3))
            return FetchedPage(url, PAGES[url])
        self.fetch_hook = fetch
        task = self.submit()
        self.assertTrue(started.wait(2))
        response = self.client.post(f"/api/tasks/{task['id']}/cancel", json={}).json()
        self.assertEqual(response["status"], "stopping")
        gate.set()
        stopped = self.terminal(task["id"])
        self.assertEqual(stopped["status"], "cancelled")
        self.assertTrue(stopped["file_available"])
        self.assertEqual(self.calls, [CATALOG, URLS[0], URLS[1]])
        self.assertEqual(stopped["result"]["missing_chapters"], [2, 3])
        self.fetch_hook = None
        self.calls.clear()
        self.assertEqual(self.terminal(self.submit("fill_gaps")["id"])["status"], "completed")
        self.assertEqual(self.calls, [URLS[1], URLS[2]])

    def test_stop_during_fill_backoff_never_starts_another_round(self):
        self.seed((1,))
        self.pages[URLS[1]] = AccessBlockedError("请求被拒绝")
        self.manager.update_settings({**self.manager.settings.model_dump(), "retry_delay": 30, "retry_rounds": 3})
        task = self.submit("fill_gaps")
        deadline = time.monotonic() + 2
        while URLS[1] not in self.calls and time.monotonic() < deadline:
            Event().wait(.01)
        self.client.post(f"/api/tasks/{task['id']}/cancel", json={})
        self.assertEqual(self.terminal(task["id"])["status"], "cancelled")
        self.assertEqual(self.calls, [URLS[1]])

    def test_search_results_select_book_and_download(self):
        from book_downloader.sites.bixiange import BixiangeAdapter
        adapter = BixiangeAdapter()
        search_url = adapter.build_search_request("示例", 10).url
        self.pages[search_url] = '<div class="list"><li><div class="info"><div class="title"><a href="https://www.bixiange.top/xhqh/12345/">示例小说</a></div></div></li></div>'
        with patch("book_downloader.search.searchable_adapters", return_value=(adapter,)):
            found = self.terminal(self.submit("search", url="", query="示例")["id"])
        self.assertEqual(found["status"], "completed")
        self.assertEqual(found["results"][0]["title"], "示例小说")
        selected = found["results"][0]["url"]
        self.pages[selected] = '<h1>示例小说</h1><div class="catalog"><a href="index/1.html">第1节</a><a href="index/2.html">第2节</a></div>'
        for n in (1, 2):
            self.pages[selected + f"index/{n}.html"] = f'<h1>第{n}节</h1><div id="mycontent">分段正文完整保留。' * 8 + '</div>'
        downloaded = self.terminal(self.submit(url=selected)["id"])
        self.assertEqual(downloaded["status"], "completed")
        self.assertTrue(any("真实章号" in warning for warning in downloaded["warnings"]))

    def test_search_results_arrive_before_other_site_finishes_and_survive_stop(self):
        from book_downloader.sites.bixiange import BixiangeAdapter
        from book_downloader.sites.trxs_cc import TrxsCcAdapter
        first, second = BixiangeAdapter(), TrxsCcAdapter()
        first_url = first.build_search_request("示例", 10).url
        second_url = second.build_search_request("示例", 10).url
        selected = "https://www.bixiange.top/xhqh/12345/"
        self.pages[selected] = '<h1>示例小说</h1><div class="catalog"><a href="index/1.html">第1章 示例</a><a href="index/2.html">第2章 示例</a></div>'
        for number in (1, 2):
            self.pages[selected + f"index/{number}.html"] = f'<h1>第{number}章 示例</h1><div id="mycontent">' + '示例正文完整保留。' * 12 + '</div>'
        started, release = Event(), Event()
        self.addCleanup(release.set)
        def fetch(url):
            if url == first_url:
                return FetchedPage(url, '<div class="list"><li><div class="info"><div class="title"><a href="' + selected + '">示例小说</a></div></div></li></div>')
            if url == second_url:
                emit("verification", message="第二站等待人工验证")
                started.set()
                while not release.wait(.01):
                    check_cancelled()
                return FetchedPage(url, "<p>没有结果</p>")
            return FetchedPage(url, self.pages[url])
        self.fetch_hook = fetch
        with patch("book_downloader.search.searchable_adapters", return_value=(first, second)):
            search = self.submit("search", url="", query="示例")
            self.assertTrue(started.wait(2))
            current = self.client.get(f"/api/tasks/{search['id']}").json()
            self.assertEqual(current["status"], "waiting_verification")
            self.assertEqual(current["results"][0]["url"], selected)
            self.assertEqual((current["progress"]["current"], current["progress"]["total"]), (1, 2))
            download = self.submit(url=current["results"][0]["url"])
            self.assertEqual(download["status"], "queued")
            self.client.post(f"/api/tasks/{search['id']}/cancel", json={})
            stopped = self.terminal(search["id"])
            self.assertEqual(stopped["status"], "cancelled")
            self.assertEqual(stopped["results"], current["results"])
            finished = self.terminal(download["id"])
            self.assertEqual(finished["status"], "completed", finished)
            self.assertEqual(self.calls.count(second_url), 1)

    def test_successful_browser_search_keeps_initial_http_denial_as_warning(self):
        from book_downloader.models import SiteSearchHit
        from book_downloader.sites.trxs_cc import TrxsCcAdapter
        class SearchClient(FixtureClient):
            def page_search(self, *args, **kwargs):
                return (SiteSearchHit("示例小说", CATALOG),)
        def blocked(url):
            emit("blocked", message="HTTP 403；初次请求被拒绝")
            raise AccessBlockedError("HTTP 403；初次请求被拒绝")
        self.fetch_hook = blocked
        self.manager.client_factory = lambda *args: SearchClient(self)
        with patch("book_downloader.search.searchable_adapters", return_value=(TrxsCcAdapter(),)):
            result = self.terminal(self.submit("search", url="", query="示例")["id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNone(result["error"])
        self.assertTrue(any("403" in message for message in result["warnings"]))
        self.assertEqual(result["results"][0]["url"], CATALOG)

    def test_corrupt_cache_and_sidecar_failure_are_actionable(self):
        cache = self.seed()
        cache.chapter_path(2).write_bytes(b"\xff\xfeinvalid")
        corrupt = self.terminal(self.submit("merge")["id"])
        self.assertEqual(corrupt["status"], "failed")
        self.assertIn("备份", corrupt["error"])
        self.assertEqual(cache.chapter_path(2).read_bytes(), b"\xff\xfeinvalid")
        cache.write(Chapter(2, PLAN.chapters[1].title, BODIES[1]))
        output_dir = self.root / "outputs"
        output_dir.mkdir(exist_ok=True)
        (output_dir / "示例小说.url").mkdir()
        # Occupied names get a book-specific output. Block its sidecar too.
        from book_downloader.web.tasks import book_id
        (output_dir / f"示例小说-{book_id(CATALOG)[:8]}.url").mkdir()
        sidecar = self.terminal(self.submit("merge")["id"])
        self.assertEqual(sidecar["status"], "failed")
        self.assertIn("TXT 已生成", sidecar["error"])
        self.assertTrue(sidecar["file_available"])
        self.assertIn(BODIES[1], self.client.get(f"/api/tasks/{sidecar['id']}/file").text)

    def test_books_ignore_orphan_files_and_file_paths_are_controlled(self):
        cache = self.seed()
        cache.write(Chapter(99, "第99章", "孤立旧文件"))
        row = self.client.get("/api/books").json()[0]
        self.assertEqual(row["cached_chapters"], 3)
        self.assertEqual(self.client.get("/api/tasks/not-a-task/file").status_code, 404)
        self.assertEqual(self.client.post("/api/tasks", json={"kind": "merge", "url": CATALOG, "output": "/etc/passwd"}).status_code, 422)
        self.assertEqual(self.client.post("/api/tasks", json={"kind": "download", "url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks", json={"kind": "search", "query": "示例"}, headers={"Origin": "https://example.com"}).status_code, 403)

    def test_shutdown_requires_confirmation_and_preserves_settings(self):
        self.assertEqual(self.client.post("/api/shutdown", json={}).status_code, 409)
        self.assertEqual(self.client.post("/api/shutdown", json={"confirm_stop": True}).status_code, 200)
        self.assertTrue(self.manager.shutdown_event.is_set())
        loaded = TaskManager(self.root)
        self.assertEqual(loaded.settings.delay, 0)

    def test_submission_storage_failure_rolls_back_and_retry_runs(self):
        with patch.object(self.manager, "_save_tasks", side_effect=OSError("离线模拟：无法保存")):
            response = self.client.post("/api/tasks", json={"kind": "download", "url": CATALOG})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.manager.tasks, {})
        self.assertTrue(self.manager.queue.empty())
        self.assertEqual(self.terminal(self.submit()["id"])["status"], "completed")

    def test_worker_survives_start_and_terminal_persistence_failures(self):
        original = self.manager._save_tasks
        for phase in ("running", "completed"):
            with self.subTest(phase=phase):
                failed_once = []
                def save():
                    if current_thread().name == "book-download-worker" and not failed_once and any(
                        task["status"] == phase for task in self.manager.tasks.values()
                    ):
                        failed_once.append(True)
                        raise OSError("离线模拟：记录保存失败")
                    original()
                with patch.object(self.manager, "_save_tasks", side_effect=save):
                    task = self.submit("update")
                    result = self.terminal(task["id"])
                self.assertEqual(result["status"], "failed")
                self.assertIn("记录保存失败", result["error"])
                self.assertTrue(self.manager.thread.is_alive())
                self.assertEqual(self.terminal(self.submit()["id"])["status"], "completed")

    def test_broken_source_file_does_not_block_other_books(self):
        directory = self.root / "outputs"
        directory.mkdir()
        bad = directory / "broken.url"
        bad.write_bytes(b"\xff")
        result = self.terminal(self.submit()["id"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any("broken.url" in message for message in result["warnings"]))
        books = self.client.get("/api/books")
        self.assertEqual(books.status_code, 200)
        self.assertEqual(len(books.json()), 1)
        self.assertIn("broken.url", books.headers["X-Book-Downloader-Warnings"])
        self.assertEqual(bad.read_bytes(), b"\xff")

    def test_historical_task_never_delivers_another_books_replacement(self):
        result = self.terminal(self.submit()["id"])
        path = Path(result["result"]["output"])
        path.write_text("另一本书的完整内容", encoding="utf-8")
        path.with_suffix(".url").write_text("https://www.trxs.cc/tongren/54321.html", encoding="utf-8")
        self.assertEqual(self.client.get(f"/api/tasks/{result['id']}/file").status_code, 404)
        self.assertFalse(self.client.get(f"/api/tasks/{result['id']}").json()["file_available"])


class OperationTests(unittest.TestCase):
    def test_http_cancellation_prevents_retry_and_next_page(self):
        import requests
        cancel = Event()
        def fail(**kwargs):
            cancel.set()
            raise requests.Timeout("离线超时")
        client = HttpClient(1, 3)
        with patch.object(client.session, "request", side_effect=fail) as request:
            with operation_scope(cancel, lambda event: None), self.assertRaises(OperationCancelled):
                client.fetch(CATALOG)
        self.assertEqual(request.call_count, 1)
        client.close()

    def test_restart_marks_tasks_and_does_not_auto_resume(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = TaskManager(root)
            queued = old.submit("download", url=CATALOG)
            running = old.submit("update", url=CATALOG)
            old.tasks[running["id"]]["status"] = "running"
            old._save_tasks()
            with patch("book_downloader.web.tasks.WebClient", side_effect=AssertionError("不应自动恢复")):
                restored = TaskManager(root)
                self.assertEqual(restored.get(queued["id"])["status"], "paused")
                self.assertEqual(restored.get(running["id"])["status"], "interrupted")
                self.assertTrue(restored.queue.empty())
                recovered = restored.retry(queued["id"])
                self.assertEqual(recovered["status"], "queued")
                self.assertNotEqual(recovered["id"], queued["id"])

    def test_browser_approval_cancel_and_thread_affinity(self):
        with TemporaryDirectory() as temporary:
            threads, events = [], []
            verified = Event()
            class FakeHttp:
                def fetch(self, url, **kwargs):
                    raise VerificationPageError("真人验证")
                def close(self):
                    threads.append(get_ident())
            class FakeBrowser:
                def __init__(self, **kwargs):
                    threads.append(get_ident())
                def fetch(self, url, **kwargs):
                    threads.append(get_ident())
                    emit("verification", message="请手动验证")
                    while not verified.wait(.01):
                        check_cancelled()
                    emit("running", message="验证完成")
                    return FetchedPage(url, PAGES[url])
                def close(self):
                    threads.append(get_ident())
            def factory(settings, cancel, approved, force):
                return WebClient(settings, cancel, approved, force, browser_factory=FakeBrowser)
            manager = TaskManager(Path(temporary), client_factory=factory)
            manager.update_settings({**manager.settings.model_dump(), "delay": 0})
            with patch("book_downloader.web.tasks.HttpClient", return_value=FakeHttp()):
                with TestClient(create_app(manager=manager)) as api:
                    task = api.post("/api/tasks", json={"kind": "download", "url": CATALOG}).json()
                    def await_state(states):
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            value = api.get(f"/api/tasks/{task['id']}").json()
                            if value["status"] in states:
                                return value
                            Event().wait(.01)
                        self.fail(value)
                    await_state({"needs_browser"})
                    self.assertEqual(api.get("/api/health").status_code, 200)
                    api.post(f"/api/tasks/{task['id']}/browser", json={})
                    await_state({"waiting_verification"})
                    verified.set()
                    self.assertEqual(await_state({"completed", "failed"})["status"], "completed")
                    self.assertEqual(len(set(threads)), 1)
                    self.assertNotEqual(threads[0], get_ident())
                    verified.clear()
                    task = api.post("/api/tasks", json={"kind": "download", "url": CATALOG}).json()
                    await_state({"needs_browser"})
                    api.post(f"/api/tasks/{task['id']}/browser", json={})
                    await_state({"waiting_verification"})
                    api.post(f"/api/tasks/{task['id']}/cancel", json={})
                    self.assertEqual(await_state({"cancelled"})["status"], "cancelled")
                    manager.update_settings({**manager.settings.model_dump(), "verification_timeout": .05})
                    task = api.post("/api/tasks", json={"kind": "download", "url": CATALOG}).json()
                    self.assertEqual(await_state({"blocked"})["status"], "blocked")

    def test_stop_prevents_following_a_chapter_continuation(self):
        from book_downloader.runner import chapter_from_pages
        from book_downloader.sites.txxt import TxxtAdapter
        cancel = Event()
        calls = []
        first = "http://www.23txxt.com/bqg/12345/100.html"
        class Client:
            def fetch(self, url):
                calls.append(url)
                cancel.set()
                return FetchedPage(url, '<h1>第1章</h1><div id="content">完整正文。' * 12 + '</div><a href="100_2.html">下一页</a>')
        with operation_scope(cancel, lambda event: None), self.assertRaises(OperationCancelled):
            chapter_from_pages(Client(), TxxtAdapter(), ChapterLink(1, "第1章", first), "示例小说", 50)
        self.assertEqual(calls, [first])


class LauncherTests(unittest.TestCase):
    def test_existing_service_is_reused_and_unknown_port_is_rejected(self):
        from book_downloader.web.__main__ import launch
        with patch("book_downloader.web.__main__.probe", return_value=True), patch(
            "book_downloader.web.__main__.open_page"
        ) as open_page, patch("book_downloader.web.__main__.subprocess.Popen") as spawn:
            self.assertEqual(launch(Path("/tmp"), True), 0)
            open_page.assert_called_once()
            spawn.assert_not_called()
        with patch("book_downloader.web.__main__.probe", return_value=False), patch(
            "book_downloader.web.__main__.port_busy", return_value=True
        ), self.assertRaisesRegex(RuntimeError, "其他程序"):
            launch(Path("/tmp"), True)

    def test_serve_releases_singleton_lock_after_stop(self):
        import fcntl
        from book_downloader.web.__main__ import serve
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("book_downloader.web.__main__.logging.basicConfig"), patch("uvicorn.Server.run"):
                serve(root)
            with (root / ".local/web/server.lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == "__main__":
    unittest.main()
