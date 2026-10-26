"""A local extension reads public pages in its own tab in the user's browser."""

from fnmatch import fnmatch
import secrets
from threading import Event, RLock
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from ..browser import same_target_document
from ..errors import AccessBlockedError, BrowserInteractionTimeout, NetworkError
from ..http import FetchedPage, looks_like_verification_page
from ..operation import check_cancelled, emit, wait


class BrowserBridge:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = RLock()
        self.last_seen = 0
        self.owner = None
        self.command = None
        self.permissions_granted = None
        self.version = ""
        self.focus_session = None

    @property
    def connected(self):
        return time.monotonic() - self.last_seen < 5

    @property
    def ready(self):
        return self.connected and self.permissions_granted is not False

    def status(self):
        with self.lock:
            return {"connected": self.connected, "permissions_granted": self.permissions_granted,
                    "version": self.version, "can_focus": self.connected and self.version == "1.0.1"}

    def request_focus(self, session):
        with self.lock:
            if not self.status()["can_focus"]:
                raise ValueError("请连接并更新浏览器扩展后再前往任务标签页。")
            self.focus_session = session

    def authorized(self, authorization):
        return secrets.compare_digest(authorization or "", "Bearer " + self.token)

    def poll(self, owner, ready=True, permissions=None, version=""):
        with self.lock:
            if self.connected and self.owner != owner:
                raise ValueError("另一个浏览器已连接，请使用打开书页的同一个浏览器。")
            self.owner, self.last_seen = owner, time.monotonic()
            self.permissions_granted, self.version = permissions, version
            focus_session, self.focus_session = self.focus_session, None
            command = self.command
            if command is None:
                return {"active_id": None, "command": None, "focus_session": focus_session}
            payload = None
            if ready and not command["sent"]:
                command["sent"] = True
                payload = command["payload"]
            return {"active_id": command["id"], "command": payload, "focus_session": focus_session}

    def complete(self, identifier, owner, result=None, error=None):
        with self.lock:
            command = self.command
            if command is None or command["id"] != identifier or self.owner != owner:
                return False
            command["result"], command["error"] = result, error
            command["done"].set()
            return True

    def call(self, session, action, timeout, **parameters):
        check_cancelled()
        with self.lock:
            if self.command is not None:
                raise NetworkError("浏览器正在处理另一个请求")
            identifier = uuid4().hex
            command = {"id": identifier, "sent": False, "done": Event(), "error": None,
                       "result": None, "payload": {"id": identifier, "session": session,
                       "action": action, "expires_at": time.time() + timeout, **parameters}}
            self.command = command
        deadline = time.monotonic() + timeout
        try:
            while not command["done"].wait(.1):
                check_cancelled()
                if time.monotonic() >= deadline:
                    raise AccessBlockedError("等待当前浏览器超时；请确认扩展已连接、验证标签页未关闭，再重试。")
            check_cancelled()
            if command["error"]:
                raise NetworkError("浏览器标签页：" + str(command["error"]))
            return command["result"]
        finally:
            with self.lock:
                if self.command is command:
                    self.command = None


class ExtensionClient:
    supports_concurrent_requests = False

    def __init__(self, bridge, timeout, verification_timeout):
        self.bridge = bridge
        self.timeout = timeout
        self.verification_timeout = verification_timeout
        self.session = uuid4().hex
        self.page = ExtensionPage(self)
        emit("browser_tab", session=self.session)

    def call(self, action, **parameters):
        result = self.bridge.call(self.session, action, self.timeout, **parameters)
        if action not in {"navigate", "snapshot"} and isinstance(result, dict) and result.get("unreadable"):
            # The extension reports this before executing a page action. Wait on
            # that same tab, then perform the action once the page is readable.
            self.wait_for_verification(result)
            result = self.bridge.call(self.session, action, self.timeout, **parameters)
            if isinstance(result, dict) and result.get("unreadable"):
                raise BrowserInteractionTimeout("任务标签页仍不可读，请处理浏览器提示后重试。")
        return result

    def wait_for_verification(self, snapshot):
        deadline = time.monotonic() + self.verification_timeout
        def needs_user(value):
            return value.get("unreadable") or looks_like_verification_page(value["html"])

        if not needs_user(snapshot):
            return snapshot
        last_reason = None
        while needs_user(snapshot):
            reason = snapshot.get("reason", "browser_notice") if snapshot.get("unreadable") else "site_verification"
            if reason != last_reason:
                if reason == "permission_required":
                    emit("needs_browser", message="浏览器尚未授权小说站点访问，请点击扩展图标完成授权；任务保持原页等待。")
                else:
                    message = ("浏览器正在等待安全提示或页面确认。请前往任务标签页，按浏览器提示自行决定是否继续；任务会保持当前页面等待。"
                               if reason == "browser_notice" else "请在当前浏览器的任务标签页完成人工验证；完成后自动继续")
                    emit("verification", url=snapshot["url"], message=message)
                last_reason = reason
            if time.monotonic() >= deadline:
                raise BrowserInteractionTimeout("等待浏览器确认或人工验证超时；任务已停止，未跳转到其他页面。请处理任务标签页后重试。")
            wait(.5)
            snapshot = self.page.snapshot()
        emit("running", message="人工验证已完成，继续任务")
        return snapshot

    def fetch(self, url, *, method="GET", data=None, headers=None, response_encoding=None):
        check_cancelled()
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("浏览器只能打开 HTTP 或 HTTPS 页面")
        self.page.goto(url, timeout=int(self.timeout * 1000), wait_until="domcontentloaded")
        if method.upper() != "GET":
            # Send the existing encoded public search request from its own origin.
            result = self.call("fetch", url=url, method=method, body=data.decode("ascii") if isinstance(data, bytes) else data,
                               headers={k: v for k, v in (headers or {}).items() if k.lower() == "content-type"},
                               encoding=response_encoding or "utf-8")
        else:
            result = self.page.snapshot()
        if method.upper() != "GET" and not result.get("unreadable") and looks_like_verification_page(result["html"]):
            raise AccessBlockedError("站内搜索请求需要人工验证，请在站点标签页完成后重试搜索。")
        result = self.wait_for_verification(result)
        if result.get("status", 200) >= 400:
            raise AccessBlockedError(f"HTTP {result['status']}；站点拒绝了浏览器请求")
        return FetchedPage(result["url"], result["html"])

    def page_search(self, adapter, query, limit, *, verification_timeout=None):
        return adapter.search_via_page(self.page, query, limit, navigation_timeout=self.timeout,
                                      verification_timeout=verification_timeout or self.verification_timeout)

    def close(self):
        # The extension owns its tab; keep it available for inspection and verification.
        pass


class ExtensionPage:
    """Only the small page/locator interface already used by the site adapters."""

    def __init__(self, client):
        self.client = client
        self._snapshot = None
        self._document_url = None

    def snapshot(self):
        snapshot = self.client.call("snapshot")
        if not isinstance(snapshot, dict):
            return {}
        if not snapshot.get("unreadable") and self._document_url:
            expected_query = parse_qs(urlsplit(self._document_url).query, keep_blank_values=True)
            actual_query = parse_qs(urlsplit(snapshot["url"]).query, keep_blank_values=True)
            if (not same_target_document(snapshot["url"], self._document_url)
                    or any(actual_query.get(key) != values for key, values in expected_query.items())):
                raise AccessBlockedError("任务标签页的页面地址已变化，未读取或缓存其他章节；请重试任务。")
        self._snapshot = snapshot
        return self._snapshot

    @property
    def url(self):
        return (self._snapshot or self.snapshot())["url"]

    def content(self):
        return self.snapshot()["html"]

    def goto(self, url, *, timeout, wait_until=None):
        self._snapshot = None
        try:
            navigation = self.client.call("navigate", url=url)
        except AccessBlockedError:
            # Some browser prompts never produce a completed navigation event.
            # Inspect the same tab instead of letting search navigate to a fallback.
            navigation = {"unreadable": True, "reason": "browser_notice", "url": url}
        self._document_url = navigation.get("url") or url
        if navigation.get("unreadable"):
            # An internal browser document has no trustworthy final URL yet.
            self._document_url = url
            self._snapshot = self.client.wait_for_verification(navigation)
            return
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            check_cancelled()
            try:
                snapshot = self.snapshot()
                if snapshot.get("unreadable") or snapshot.get("ready") in {"interactive", "complete"}:
                    self._snapshot = self.client.wait_for_verification(snapshot)
                    return
            except NetworkError:
                pass  # Navigation briefly replaces the document being inspected.
            wait(.2)
        raise NetworkError("等待站点标签页加载超时，请检查该标签页后重试。")

    def locator(self, selector):
        return ExtensionLocator(self, selector)

    def wait_for_timeout(self, milliseconds):
        wait(milliseconds / 1000)

    def wait_for_url(self, pattern, *, timeout):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if fnmatch(self.snapshot()["url"], pattern):
                return
            wait(.2)
        raise NetworkError("站点搜索尚未跳转到结果页")


class ExtensionLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def query(self):
        return self.page.client.call("query", selector=self.selector)

    def count(self):
        return self.query()["count"]

    def wait_for(self, *, state="visible", timeout):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            if self.query()["visible"]:
                return
            wait(.1)
        raise NetworkError("搜索框尚未出现，请先在站点标签页完成验证")

    def fill(self, value):
        self.page.client.call("fill", selector=self.selector, value=value)

    def press(self, key):
        if key != "Enter":
            raise ValueError("只支持提交搜索表单")
        self.page.client.call("submit", selector=self.selector)
        self.page._snapshot = None
        self.page._document_url = None

    def evaluate(self, script):
        # The sole adapter use reads the containing search form; never execute supplied code.
        return self.query()["form_html"]
