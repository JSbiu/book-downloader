"""Browser bridge contracts, without websites or the user's browser/profile."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest("需要可选 Web 测试依赖")

from book_downloader.errors import AccessBlockedError, BrowserInteractionTimeout
from book_downloader.operation import OperationCancelled, operation_scope
from book_downloader.web.app import create_app
from book_downloader.web.browser_bridge import BrowserBridge, ExtensionClient
from book_downloader.web.tasks import TaskManager
from tests.test_web import CATALOG, PAGES, URLS, BODIES


class BridgeTests(unittest.TestCase):
    def test_connection_permissions_legacy_and_focus_are_separate(self):
        bridge = BrowserBridge()
        bridge.poll("browser", permissions=False, version="1.0.1")
        self.assertTrue(bridge.connected)
        self.assertFalse(bridge.ready)
        self.assertFalse(bridge.status()["permissions_granted"])
        bridge.request_focus("task-session")
        self.assertEqual(bridge.poll("browser", permissions=True, version="1.0.1")["focus_session"], "task-session")
        self.assertIsNone(bridge.poll("browser", permissions=True, version="1.0.1")["focus_session"])
        self.assertTrue(bridge.ready)
        bridge.poll("browser")
        self.assertTrue(bridge.ready)  # Old installed extensions keep working.
        self.assertIsNone(bridge.status()["permissions_granted"])
        with self.assertRaises(ValueError):
            bridge.request_focus("task-session")

    def test_command_delivery_busy_owner_and_cancellation_reject_late_reply(self):
        bridge, cancel = BrowserBridge(), Event()
        bridge.poll("browser")
        caught = []
        def run():
            try:
                with operation_scope(cancel, lambda _: None):
                    bridge.call("session", "snapshot", 2)
            except OperationCancelled:
                caught.append("stopped")
        thread = Thread(target=run)
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while bridge.command is None and time.monotonic() < deadline:
                Event().wait(.01)
            self.assertIsNone(bridge.poll("browser", ready=False)["command"])
            command = bridge.poll("browser")["command"]
            self.assertEqual(command["action"], "snapshot")
            self.assertIsNone(bridge.poll("browser")["command"])
            with self.assertRaises(ValueError):
                bridge.poll("another-browser")
            cancel.set()
            thread.join(2)
            self.assertEqual(caught, ["stopped"])
            self.assertFalse(bridge.complete(command["id"], "browser", {"html": "late"}))
            self.assertIsNone(bridge.poll("browser")["active_id"])
        finally:
            cancel.set()
            thread.join(2)

    def test_timeout_clears_command_and_allows_future_work(self):
        bridge = BrowserBridge()
        with self.assertRaises(AccessBlockedError):
            bridge.call("session", "snapshot", .01)
        self.assertIsNone(bridge.command)

    def test_browser_notice_waits_in_same_page_and_continues(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        notice = {"unreadable": True, "reason": "browser_notice", "url": CATALOG}
        document = {"url": CATALOG, "html": PAGES[CATALOG], "ready": "complete"}
        events = []
        with operation_scope(Event(), events.append), patch.object(client, "call", side_effect=[notice, document, document]) as calls:
            page = client.fetch(CATALOG)
        self.assertEqual(page.text, PAGES[CATALOG])
        self.assertEqual([call.args[0] for call in calls.call_args_list], ["navigate", "snapshot", "snapshot"])
        self.assertTrue(any(event["kind"] == "verification" and "安全提示" in event["message"] for event in events))

    def test_navigation_timeout_inspects_original_tab_without_fallback(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        document = {"url": CATALOG, "html": PAGES[CATALOG], "ready": "complete"}
        with operation_scope(Event(), lambda _: None), patch.object(client, "call", side_effect=[AccessBlockedError("导航超时"), document, document]) as calls:
            self.assertEqual(client.fetch(CATALOG).url, CATALOG)
        self.assertEqual([call.args[0] for call in calls.call_args_list], ["navigate", "snapshot", "snapshot"])

    def test_navigation_without_immediate_snapshot_waits_for_existing_document(self):
        client = ExtensionClient(BrowserBridge(), 1, .05)
        document = {"url": CATALOG, "html": PAGES[CATALOG], "ready": "complete"}
        with operation_scope(Event(), lambda _: None), patch.object(client, "call", side_effect=[{}, None, document, document]) as calls:
            self.assertEqual(client.fetch(CATALOG).text, PAGES[CATALOG])
        self.assertEqual([call.args[0] for call in calls.call_args_list], ["navigate", "snapshot", "snapshot", "snapshot"])

    def test_permission_wait_changes_to_site_verification_without_repeated_events(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        permission = {"unreadable": True, "reason": "permission_required", "url": CATALOG}
        challenge = {"url": CATALOG, "html": '<title>Just a moment...</title><div id="cf-challenge-running">Verify you are human</div>', "ready": "complete"}
        readable = {"url": CATALOG, "html": PAGES[CATALOG], "ready": "complete"}
        events = []
        with operation_scope(Event(), events.append), patch.object(client, "call", side_effect=[permission, challenge, readable]), patch("book_downloader.web.browser_bridge.wait"):
            self.assertEqual(client.wait_for_verification(permission), readable)
        self.assertEqual([event["kind"] for event in events], ["needs_browser", "verification", "running"])

    def test_search_form_action_waits_for_browser_notice_without_another_navigation(self):
        bridge = BrowserBridge()
        client = ExtensionClient(bridge, .1, 1)
        notice = {"unreadable": True, "reason": "browser_notice", "url": CATALOG}
        document = {"url": CATALOG, "html": PAGES[CATALOG], "ready": "complete"}
        with patch.object(bridge, "call", side_effect=[notice, document, {"count": 1}]) as calls, patch("book_downloader.web.browser_bridge.wait"):
            self.assertEqual(client.call("query", selector="input"), {"count": 1})
        self.assertEqual([call.args[1] for call in calls.call_args_list], ["query", "snapshot", "query"])

    def test_browser_notice_can_stop_and_timeout_without_navigating_again(self):
        notice = {"unreadable": True, "reason": "browser_notice", "url": CATALOG}
        for stop in (True, False):
            with self.subTest(stop=stop):
                client = ExtensionClient(BrowserBridge(), .1, .01)
                cancel = Event()
                def event(value):
                    if stop and value["kind"] == "verification":
                        cancel.set()
                with operation_scope(cancel, event), patch.object(client, "call", return_value=notice) as calls:
                    with self.assertRaises(OperationCancelled if stop else BrowserInteractionTimeout):
                        client.fetch(CATALOG)
                self.assertEqual(sum(call.args[0] == "navigate" for call in calls.call_args_list), 1)

    def test_reading_rejects_another_chapter_in_same_site(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        first = {"url": URLS[0], "html": PAGES[URLS[0]], "ready": "complete"}
        other = {"url": URLS[1], "html": PAGES[URLS[1]], "ready": "complete"}
        with patch.object(client, "call", side_effect=[{"url": URLS[0]}, first, other]):
            with self.assertRaisesRegex(AccessBlockedError, "页面地址已变化"):
                client.fetch(URLS[0])

    def test_plain_http_denial_is_not_reported_as_human_verification(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        forbidden = {"url": CATALOG, "html": "<h1>Access denied</h1>", "ready": "complete", "status": 403}
        events = []
        with operation_scope(Event(), events.append), patch.object(client, "call", side_effect=[{"url": CATALOG}, forbidden, forbidden]):
            with self.assertRaisesRegex(AccessBlockedError, "HTTP 403"):
                client.fetch(CATALOG)
        self.assertFalse(any(event["kind"] == "verification" for event in events))

    def test_reading_rejects_changed_chapter_query_and_accepts_final_redirect(self):
        client = ExtensionClient(BrowserBridge(), .1, 1)
        initial = "https://example.test/read.php?id=1"
        first = {"url": initial, "html": "<p>示例正文</p>", "ready": "complete"}
        other = {**first, "url": "https://example.test/read.php?id=2"}
        with patch.object(client, "call", side_effect=[{"url": initial}, first, other]):
            with self.assertRaisesRegex(AccessBlockedError, "页面地址已变化"):
                client.fetch(initial)
        final = "https://example.test/book/"
        redirected = {**first, "url": final}
        with patch.object(client, "call", side_effect=[{"url": final}, redirected, redirected]):
            self.assertEqual(client.fetch("https://example.test/book.htm").url, final)

    def test_notice_timeout_never_falls_back_to_search_homepage(self):
        from book_downloader.search import search_sites
        from book_downloader.sites.trxs_cc import TrxsCcAdapter
        class Client:
            def fetch(self, *args, **kwargs):
                raise BrowserInteractionTimeout("等待浏览器确认超时")
            def page_search(self, *args, **kwargs):
                raise AssertionError("不能跳转首页")
        with patch("book_downloader.search.searchable_adapters", return_value=(TrxsCcAdapter(),)):
            with self.assertRaises(BrowserInteractionTimeout):
                search_sites(Client(), "示例")


class ExtensionApiTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = TaskManager(self.root)
        self.manager.update_settings({**self.manager.settings.model_dump(), "delay": 0, "timeout": 2})
        self.client = self.enterContext(TestClient(create_app(manager=self.manager)))
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("真实网络被禁用")))
        self.token = self.client.get("/api/browser/pairing").json()["token"]
        self.auth = {"Authorization": "Bearer " + self.token, "Origin": "chrome-extension://test-extension"}

    def test_pairing_authentication_and_narrow_cross_origin_access(self):
        self.assertEqual(self.client.get("/api/browser/pairing", headers={"Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/browser/extension/work?client=test").status_code, 401)
        self.assertEqual(self.client.post("/api/tasks", headers=self.auth, json={"kind": "search", "query": "示例"}).status_code, 403)
        self.assertEqual(self.client.post("/api/browser/extension/reply", headers=self.auth,
                                        json={"id": "expired", "client": "test"}).status_code, 410)
        archive = self.client.get("/api/browser/download")
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.headers["content-type"], "application/zip")
        from io import BytesIO
        from zipfile import ZipFile
        with ZipFile(BytesIO(archive.content)) as content:
            self.assertIn("manifest.json", content.namelist())
            self.assertIn("background.js", content.namelist())

    def test_focus_resolves_active_task_session_without_starting_new_request(self):
        self.client.get("/api/browser/extension/work?client=test&permissions=true&version=1.0.1", headers=self.auth)
        created = self.client.post("/api/tasks", json={"kind": "download", "url": CATALOG, "force_browser": True}).json()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            task = self.manager.get(created["id"])
            if task.get("browser_session"):
                break
            Event().wait(.01)
        response = self.client.post(f"/api/tasks/{created['id']}/focus", json={})
        self.assertEqual(response.status_code, 200, response.text)
        work = self.client.get("/api/browser/extension/work?client=test&ready=false&permissions=true&version=1.0.1", headers=self.auth).json()
        self.assertEqual(work["focus_session"], task["browser_session"])
        self.assertIsNone(work["command"])
        self.client.post(f"/api/tasks/{created['id']}/cancel", json={})

    def test_download_through_extension_manual_verification_and_page_independence(self):
        self.client.get("/api/browser/extension/work?client=test", headers=self.auth)
        created = self.client.post("/api/tasks", json={"kind": "download", "url": CATALOG, "force_browser": True}).json()
        current_url, challenged, verified, saw_wait = CATALOG, 0, False, False
        commands = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            work = self.client.get("/api/browser/extension/work?client=test", headers=self.auth).json()
            command = work["command"]
            if command:
                commands.append(command["action"])
                if command["action"] == "navigate":
                    current_url = command["url"]
                    result = {}
                else:
                    self.assertEqual(command["action"], "snapshot")
                    if challenged < 2:
                        challenged += 1
                        html = '<title>Just a moment...</title><div id="cf-challenge-running">Verify you are human</div>'
                    else:
                        html = PAGES[current_url]
                        if self.manager.get(created["id"])["status"] == "waiting_verification":
                            saw_wait, verified = True, True
                    result = {"url": current_url, "html": html, "ready": "complete"}
                reply = self.client.post("/api/browser/extension/reply", headers=self.auth,
                                         json={"id": command["id"], "client": "test", "result": result})
                self.assertEqual(reply.status_code, 200, reply.text)
            task = self.manager.get(created["id"])
            if task["status"] not in {"queued", "running", "waiting_verification", "needs_browser"}:
                break
            Event().wait(.01)
        self.assertEqual(task["status"], "completed", task)
        self.assertTrue(saw_wait and verified)
        self.assertEqual(commands.count("navigate"), 4)
        text = self.client.get(f"/api/tasks/{created['id']}/file").text
        self.assertTrue(all(body in text for body in BODIES))
        self.assertTrue(self.client.get("/api/browser/status").json()["connected"])

    def test_waiting_for_extension_can_stop_without_launching_browser(self):
        with patch("book_downloader.web.tasks.BrowserHttpClient", side_effect=AssertionError("不能启动独立浏览器")):
            task = self.client.post("/api/tasks", json={"kind": "download", "url": CATALOG, "force_browser": True}).json()
            deadline = time.monotonic() + 2
            while self.manager.get(task["id"])["status"] != "needs_browser" and time.monotonic() < deadline:
                Event().wait(.01)
            self.client.post(f"/api/tasks/{task['id']}/cancel", json={})
            while self.manager.get(task["id"])["status"] != "cancelled" and time.monotonic() < deadline:
                Event().wait(.01)
            self.assertEqual(self.manager.get(task["id"])["status"], "cancelled")
            self.assertEqual(self.manager.books(), [])


if __name__ == "__main__":
    unittest.main()
