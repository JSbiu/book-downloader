"""Opt-in acceptance with an actual extension, temporary profiles and offline pages."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

try:
    import uvicorn
except ImportError:
    raise unittest.SkipTest("需要可选 Web 依赖")

from bs4 import BeautifulSoup
from book_downloader.browser import _browser_environment, find_chrome_executable
from book_downloader.errors import NetworkError, VerificationPageError
from book_downloader.models import SiteSearchHit, SiteSearchRequest
from book_downloader.operation import check_cancelled
from book_downloader.web.app import create_app
from book_downloader.web.tasks import TaskManager
from book_downloader.sites.registry import GenericAdapter


@unittest.skipUnless(os.environ.get("BOOK_DOWNLOADER_UI_TESTS") == "1", "按需运行浏览器扩展验收")
class ExtensionBrowserTests(unittest.TestCase):
    def test_search_manual_verification_ui_close_download_and_stop(self):
        from playwright.sync_api import sync_playwright, expect
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            requests_seen = []
            verified = Event()
            class Pages(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass
                def do_GET(self):
                    requests_seen.append(self.path)
                    parts = urlsplit(self.path)
                    if parts.path == '/search':
                        if parse_qs(parts.query).get('q'):
                            html = '<div id="results"><a href="/book/">示例小说</a></div>'
                        else:
                            html = '<form action="/search"><input name="q"><button>搜索</button></form>'
                    elif parts.path == '/book/':
                        html = '<h1>示例小说</h1><div id="list"><a href="/book/1.html">第1章 示例章节</a><a href="/book/2.html">第2章 示例章节</a></div>'
                    elif parts.path == '/book/1.html' and not verified.is_set():
                        if parts.query:
                            verified.set()
                            html = '<h1>第1章 示例章节</h1><div class="read-content">' + '示例正文。' * 50 + '</div>'
                        else:
                            html = '<title>Just a moment...</title><div id="cf-challenge-running">Verify you are human</div><a href="?verified=1">手动完成验证</a>'
                    elif parts.path == '/denied':
                        html = '<h1>Access denied</h1>'
                    else:
                        html = '<h1>第2章 示例章节</h1><div class="read-content">' + '第二章正文。' * 50 + '</div>'
                    body = html.encode()
                    self.send_response(403 if parts.path == '/denied' else 200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            fixture = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
            fixture_thread = Thread(target=fixture.serve_forever, daemon=True)
            fixture_thread.start()
            self.addCleanup(fixture.server_close)
            self.addCleanup(fixture.shutdown)
            source = f'http://fixture.test:{fixture.server_port}'
            class Adapter(GenericAdapter):
                name = 'fixture'
                hosts = ('fixture.test',)
                def matches(self, url):
                    return urlsplit(url).hostname == 'fixture.test'
                def build_search_request(self, query, limit):
                    return SiteSearchRequest(source + '/search')
                def parse_search_results(self, html, page_url, limit):
                    soup = BeautifulSoup(html, 'html.parser')
                    return tuple(SiteSearchHit(a.get_text(), source + a['href'], '') for a in soup.select('#results a'))
                def search_via_page(self, page, query, limit, *, navigation_timeout, verification_timeout):
                    page.goto(source + '/search', timeout=int(navigation_timeout*1000))
                    box = page.locator('input[name="q"]').first
                    box.wait_for(state='visible', timeout=2000)
                    box.fill(query)
                    box.press('Enter')
                    deadline = time.monotonic()+verification_timeout
                    while time.monotonic()<deadline:
                        if page.locator('#results a').count():
                            return self.parse_search_results(page.content(), page.url, limit)
                        page.wait_for_timeout(100)
                    raise AssertionError('没有收到本地搜索结果')
            second_started, release_second = Event(), Event()
            self.addCleanup(release_second.set)
            class SecondAdapter(Adapter):
                name = 'second'
                def matches(self, url):
                    return urlsplit(url).hostname == 'fixture.test'
                def build_search_request(self, query, limit):
                    return SiteSearchRequest(source + '/search?second=1')
                def parse_search_results(self, html, page_url, limit):
                    second_started.set()
                    return super().parse_search_results(html, page_url, limit)
                def search_via_page(self, page, query, limit, *, navigation_timeout, verification_timeout):
                    second_started.set()
                    while not release_second.wait(0.05):
                        check_cancelled()
                    return ()
            manager = TaskManager(root)
            manager.update_settings({**manager.settings.model_dump(), 'delay': 0, 'timeout': 8, 'verification_timeout': 20})
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
                address = f'http://127.0.0.1:{port}'
                server = uvicorn.Server(uvicorn.Config(create_app(manager=manager), log_level='error'))
                thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
                second_search = source + '/search?second=1'
                def offline_fetch(url, **kwargs):
                    del kwargs
                    if url == second_search:
                        raise NetworkError('第二站需要页面级搜索')
                    raise VerificationPageError('离线夹具要求浏览器访问')

                with patch('requests.sessions.Session.request', side_effect=AssertionError('真实网络被禁用')), patch(
                    'book_downloader.web.tasks.HttpClient.fetch', side_effect=offline_fetch
                ), patch('book_downloader.search.searchable_adapters', return_value=(Adapter(), SecondAdapter())):
                    thread.start()
                    try:
                        deadline = time.monotonic()+5
                        while not server.started and time.monotonic()<deadline:
                            Event().wait(.01)
                        self.assertTrue(server.started)
                        extension = root/'extension'
                        shutil.copytree(Path(__file__).parents[1]/'book_downloader/web/extension', extension)
                        for path in extension.glob('*'):
                            if path.suffix in {'.js','.json'}:
                                path.write_text(path.read_text().replace(':8765', ':'+str(port)))
                        manifest_path = extension/'manifest.json'
                        manifest = json.loads(manifest_path.read_text())
                        manifest_path.write_text(json.dumps(manifest))
                        with sync_playwright() as p:
                            profile = root/'chrome-unauthorized'
                            context = p.chromium.launch_persistent_context(str(profile), executable_path=str(find_chrome_executable()),
                                headless=True, env=_browser_environment(profile) or dict(os.environ),
                                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}',
                                      '--host-resolver-rules=MAP fixture.test 127.0.0.1', '--no-proxy-server'])
                            # Fail closed: browser resources must stay on our two local fixture servers.
                            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith((address, source, 'chrome-extension://')) else route.abort())
                            page = context.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto(address)
                            expect(page.locator('#browser-extension-status')).to_have_text('扩展已连接，待授权小说站点', timeout=20000)
                            worker = context.service_workers[0]
                            extension_id = worker.url.split('/')[2]
                            popup = context.new_page()
                            popup.goto(f'chrome-extension://{extension_id}/popup.html')
                            self.assertFalse(popup.evaluate("() => chrome.permissions.contains({origins: ['http://*/*', 'https://*/*']})"))
                            popup.close()
                            context.close()

                            authorized_extension = root/'extension-authorized'
                            shutil.copytree(extension, authorized_extension)
                            authorized_manifest_path = authorized_extension/'manifest.json'
                            authorized_manifest = json.loads(authorized_manifest_path.read_text())
                            authorized_manifest['host_permissions'].extend(['http://*/*', 'https://*/*'])
                            authorized_manifest_path.write_text(json.dumps(authorized_manifest))
                            profile = root/'chrome-authorized'
                            context = p.chromium.launch_persistent_context(str(profile), executable_path=str(find_chrome_executable()),
                                headless=True, env=_browser_environment(profile) or dict(os.environ),
                                args=[f'--disable-extensions-except={authorized_extension}', f'--load-extension={authorized_extension}',
                                      '--host-resolver-rules=MAP fixture.test 127.0.0.1', '--no-proxy-server'])
                            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith((address, source, 'chrome-extension://')) else route.abort())
                            page = context.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto(address)
                            expect(page.locator('#browser-extension-status')).to_have_text('当前浏览器已连接', timeout=10000)
                            worker = context.service_workers[0]
                            original_page = page
                            original_url = address + '/'
                            task_tab_pages = lambda: [
                                tab for tab in context.pages
                                if '/book/1.html' in tab.url or '/book/2.html' in tab.url
                            ]
                            page.locator('.nav-item[data-view="add"]').click()
                            page.locator('#search-query').fill('示例')
                            page.locator('#search-submit').click()
                            try:
                                expect(page.locator('#search-results')).to_contain_text('示例小说', timeout=20000)
                            except AssertionError:
                                self.fail(str(manager.list_tasks()) + '\nRequests: ' + str(requests_seen))
                            search_task = next(t for t in manager.list_tasks() if t['kind'] == 'search')
                            deadline = time.monotonic() + 5
                            while manager.get(search_task['id'])['status'] != 'running' and time.monotonic() < deadline:
                                page.wait_for_timeout(50)
                            self.assertEqual(manager.get(search_task['id'])['status'], 'running')
                            self.assertTrue(manager.get(search_task['id'])['results'])
                            deadline = time.monotonic() + 12
                            while not second_started.is_set() and time.monotonic() < deadline:
                                page.wait_for_timeout(50)
                            search_state = manager.get(search_task['id'])
                            self.assertTrue(second_started.is_set(), {
                                'task': search_state,
                                'requests': requests_seen,
                            })
                            page.locator('#search-results').get_by_role('button', name='下载', exact=True).click(
                                timeout=5000,
                            )
                            expect(page.locator('#toast-region')).to_contain_text('已加入下载队列', timeout=5000)
                            page.locator('#search-panel').get_by_role('button', name='结束搜索', exact=True).click()
                            deadline = time.monotonic() + 5
                            while manager.get(search_task['id'])['status'] != 'cancelled' and time.monotonic() < deadline:
                                page.wait_for_timeout(50)
                            self.assertEqual(manager.get(search_task['id'])['status'], 'cancelled')
                            self.assertTrue(manager.get(search_task['id'])['results'])
                            release_second.set()
                            task = next(t for t in manager.list_tasks() if t['kind']=='download')
                            page.locator('.nav-item[data-view="tasks"]').click()
                            page.locator('#tasks-body tr').first.get_by_role('button', name='查看详情', exact=True).click()
                            expect(page.locator('#task-detail')).to_contain_text('等待人工验证', timeout=20000)
                            task_page = next(tab for tab in context.pages if '/book/1.html' in tab.url)
                            self.assertEqual(len(task_tab_pages()), 1)
                            self.assertEqual(page.url, original_url)
                            page.bring_to_front()
                            page.locator('#task-detail').get_by_role('button', name='前往任务标签页', exact=True).click()
                            deadline = time.monotonic() + 3
                            while time.monotonic() < deadline:
                                active = worker.evaluate(
                                    "async () => { const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true}); return tab && tab.url; }"
                                )
                                if active and '/book/1.html' in active:
                                    break
                                task_page.wait_for_timeout(100)
                            self.assertTrue(active and '/book/1.html' in active)
                            self.assertEqual(page.url, original_url)
                            self.assertEqual(task_page.url, source + '/book/1.html')
                            self.assertEqual(page.url, original_url)
                            page.close()
                            task_page.get_by_role('link', name='手动完成验证').click()
                            deadline = time.monotonic()+15
                            while manager.get(task['id'])['status'] not in {'completed','failed','blocked'} and time.monotonic()<deadline:
                                task_page.wait_for_timeout(100)
                            finished = manager.get(task['id'])
                            self.assertEqual(finished['status'], 'completed', finished)
                            self.assertIn('示例正文。', Path(finished['result']['output']).read_text())
                            self.assertIn('/book/2.html', requests_seen)
                            self.assertEqual(task_page.url, source+'/book/2.html')
                            # A second task waits for verification; stop must not fetch its next chapter.
                            verified.clear()
                            before_second = requests_seen.count('/book/2.html')
                            from book_downloader.cache import cache_key
                            chapter = root/'cache/generic'/cache_key(source+'/book/')/'chapters/000001.txt'
                            chapter.unlink()
                            new = manager.submit('download', url=source+'/book/', force_browser=True)
                            deadline=time.monotonic()+10
                            while manager.get(new['id'])['status']!='waiting_verification' and time.monotonic()<deadline:
                                task_page.wait_for_timeout(100)
                            self.assertEqual(manager.get(new['id'])['status'], 'waiting_verification')
                            manager.cancel(new['id'])
                            deadline=time.monotonic()+5
                            while manager.get(new['id'])['status']!='cancelled' and time.monotonic()<deadline:
                                task_page.wait_for_timeout(100)
                            self.assertEqual(manager.get(new['id'])['status'], 'cancelled')
                            self.assertEqual(requests_seen.count('/book/2.html'), before_second)
                            denied = manager.submit('download', url=source+'/denied', force_browser=True)
                            deadline = time.monotonic()+10
                            while manager.get(denied['id'])['status'] not in {'blocked', 'failed', 'completed'} and time.monotonic()<deadline:
                                task_page.wait_for_timeout(100)
                            denied_result = manager.get(denied['id'])
                            self.assertEqual(denied_result['status'], 'blocked', denied_result)
                            self.assertIn('HTTP 403', denied_result['error'])
                            self.assertIsNone(denied_result['result'])
                            self.assertEqual(requests_seen.count('/denied'), 1)
                            self.assertEqual(errors, [])
                            context.close()
                    finally:
                        server.should_exit=True
                        thread.join(10)
                        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
