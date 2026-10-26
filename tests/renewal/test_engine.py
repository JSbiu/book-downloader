"""Exercise the new MV3 sample against local pages and a fixed fake API only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

from book_downloader.browser import _browser_environment, find_chrome_executable


PROJECT = Path(__file__).resolve().parents[2]
FAKE_KEY = 'offline-fixture-key'


class EngineBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='shuye-engine-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.verified = set()
        self.requests = []
        self.api_calls = []
        self.search_results = None
        self.wxdy_chapters = [1, 2, 3, 4]
        self.wxdy_title = '中性长篇'
        self.wxdy_author = '示例作者'
        self.wxdy_noise = False
        self.wxdy_catalog_started = Event()
        self.release_wxdy_catalog = Event()
        self.release_wxdy_catalog.set()
        self.addCleanup(self.release_wxdy_catalog.set)
        self.page_two_started = Event()
        self.release_page_two = Event()
        self.release_page_two.set()
        self.addCleanup(self.release_page_two.set)
        self.old_search_started = Event()
        self.release_old_search = Event()
        self.addCleanup(self.release_old_search.set)
        owner = self

        class Pages(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, value, status=200, mime='text/html; charset=utf-8'):
                body = value.encode()
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass  # Cancellation intentionally closes the local request.

            def do_POST(self):
                if self.path != '/search-api':
                    self.reply('', 404)
                    return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.api_calls.append(body)
                if '旧查询' in body['query']:
                    owner.old_search_started.set()
                    owner.release_old_search.wait(20)
                if self.headers.get('Authorization') != 'Bearer ' + FAKE_KEY:
                    self.reply('{"error":{"message":"unauthorized"}}', 401, 'application/json')
                    return
                results = owner.search_results if owner.search_results is not None else [
                           {'title': '示例小说 A', 'url': owner.source + '/bqg/10001/',
                            'snippet': '示例作者甲的中性测试书目', 'site_name': '公开来源'}]
                self.reply(json.dumps({'results': results}), mime='application/json')

            def do_GET(self):
                path = urlsplit(self.path).path
                owner.requests.append(path)
                if path.startswith('/books/20001/'):
                    if path in ('/books/20001/', '/books/20001/index_1.html'):
                        second = path.endswith('index_1.html')
                        if second:
                            owner.wxdy_catalog_started.set()
                            owner.release_wxdy_catalog.wait(15)
                        chapters = owner.wxdy_chapters[1:] if second else owner.wxdy_chapters[:2]
                        self.reply(f'<meta property="og:novel:book_name" content="{owner.wxdy_title}">'
                                   f'<meta property="og:novel:author" content="{owner.wxdy_author}">'
                                   '<div class="info"><h1>中性长篇</h1>'
                                   '<a href="/books/20001/999.html">第九十九章 置顶最新</a></div>'
                                   '<ul class="mulu_list">'
                                   + ''.join(f'<li><a href="/books/20001/{100+i}.html">第{i}章 示例章节</a></li>' for i in chapters)
                                   + '</ul>' + ('' if second else '<a href="/books/20001/index_1.html">更多章节</a>'))
                        return
                    suffix = path.rsplit('/', 1)[-1]
                    first = suffix.startswith('101')
                    continuation = suffix == '101_1.html'
                    body = ('续页示例正文。' if continuation else '首面示例正文。') * 20
                    if owner.wxdy_noise:
                        body = ('第一章示例章节（第12页）<br>' + body
                                + '<br>一秒记住【订阅文学网 https://www.wxdy.cc】，精彩小说无弹窗免费阅读！'
                                + '<br>' + ('（本章未完，请点击下一页继续阅读）' if suffix == '101.html' else '')
                                + '第一章示例章节（第22页）')
                    target = '101_1.html' if suffix == '101.html' else '102.html' if first else f'{int(suffix.split(".")[0])+1}.html'
                    self.reply('<div id="content" class="yd-content"><h1>第一章 示例章节</h1>'
                               '<div class="read-titlelinke">目录控件</div><p>正文外广告</p>'
                               '<div id="htmlContent" class="chapter-content">' + body
                               + '<span style="display:none">隐藏干扰</span></div><div>正文外备注</div></div>'
                               f'<a href="/books/20001/{target}">下一章</a>')
                    return
                if path == '/fixture-confirm':
                    owner.verified.add('10001')
                    self.send_response(302)
                    self.send_header('Location', '/bqg/10001/102.html')
                    self.end_headers()
                    return
                parts = path.strip('/').split('/')
                if len(parts) not in (2, 3) or parts[0] != 'bqg':
                    self.reply('', 404)
                    return
                book = parts[1]
                if len(parts) == 2:
                    self.reply('<meta property="og:novel:book_name" content="示例小说 A">'
                               '<meta property="og:novel:author" content="示例作者甲">'
                               '<h1>示例小说 A</h1><div id="list"><dl>'
                               + ''.join(f'<dd><a href="/bqg/{book}/{100+i}.html">第{i}章 示例章节</a></dd>' for i in range(1, 5))
                               + f'<dd><a href="/bqg/{book}/999.html">作者公告</a></dd></dl></div>')
                    return
                filename = parts[2]
                if book == '10002':
                    self.reply('<title>Forbidden</title><h1>Access denied</h1>', 403)
                    return
                if book == '10001' and filename == '101_2.html':
                    owner.page_two_started.set()
                    owner.release_page_two.wait(30)
                if book == '10001' and filename == '102.html' and book not in owner.verified:
                    self.reply('<title>Just a moment...</title><div id="cf-challenge-running">离线人工确认夹具</div>'
                               '<a id="fixture-confirm" href="/fixture-confirm">完成夹具确认</a>')
                    return
                title = '第1章 示例章节' if filename.startswith('101') else f'第{int(filename.split(".")[0])-100}章 示例章节'
                body = ('前页示例正文。' * 20 if filename == '101.html' else
                        '续页示例正文。' * 20 if filename == '101_2.html' else '后续示例正文。' * 20)
                next_page = ''
                if filename == '101.html':
                    body += '\n（本章未完，请点击下一页继续阅读）'
                    next_page = f'<a href="/bqg/{book}/101_2.html">下一页</a>'
                elif filename == '101_2.html':
                    next_page = f'<a href="/bqg/{book}/102.html">下一章</a>'
                self.reply(f'<h1>{title}</h1><div id="content">二三书库<br>{title}<br>{body}</div>{next_page}')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.source = f'http://www.23txxt.com:{self.server.server_port}'
        self.wxdy_source = f'http://www.wxdy.cc:{self.server.server_port}'
        self.extension = self.root / 'extension'
        shutil.copytree(PROJECT / 'extension', self.extension)
        api = self.extension / 'search.js'
        api.write_text(api.read_text().replace('https://api.deepseek.com/v1/tools/search',
                                               f'http://127.0.0.1:{self.server.server_port}/search-api'))
        manifest = self.extension / 'manifest.json'
        data = json.loads(manifest.read_text())
        if self._testMethodName == 'test_local_update_waits_for_download_and_reopens_with_settings':
            data['host_permissions'] = [host for host in data['host_permissions'] if 'wxdy.cc' not in host]
        data['host_permissions'].append(f'http://127.0.0.1:{self.server.server_port}/*')
        manifest.write_text(json.dumps(data))
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.profile = self.root / 'profile'
        self.outputs = self.root / 'downloads'
        self.outputs.mkdir()
        self.context = None
        self.launch()
        self.addCleanup(lambda: self.context.close() if self.context else None)

    def launch(self):
        self.context = self.playwright.chromium.launch_persistent_context(
            str(self.profile), executable_path=str(find_chrome_executable()), headless=True,
            env=_browser_environment(self.profile), downloads_path=str(self.outputs),
            args=[f'--disable-extensions-except={self.extension}', f'--load-extension={self.extension}',
                  '--host-resolver-rules=MAP * 127.0.0.1, EXCLUDE localhost', '--no-proxy-server'])
        # Every external request is blocked, even if the production module tried
        # another hostname. The API URL in this temporary copy is loopback only.
        allowed = (self.source, self.wxdy_source, f'http://127.0.0.1:{self.server.server_port}', 'chrome-extension://')
        self.context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(allowed) else route.abort())
        worker = self.context.service_workers[0] if self.context.service_workers else self.context.wait_for_event('serviceworker')
        self.extension_id = worker.url.split('/')[2]
        self.page = self.context.new_page()
        self.page.goto(f'chrome-extension://{self.extension_id}/index.html')
        self.cdp = self.context.new_cdp_session(self.page)
        self.browser_cdp = self.context.browser.new_browser_cdp_session()
        self.browser_cdp.send('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(self.outputs), 'eventsEnabled': True})

    def command(self, kind, **values):
        result = self.page.evaluate('(message) => chrome.runtime.sendMessage(message)', {'type': kind, **values})
        self.assertTrue(result.get('ok'), result)
        return result['value']

    def state(self):
        return self.command('STATE')

    def until(self, predicate, timeout=20000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            value = self.state()
            if predicate(value):
                return value
            self.page.wait_for_timeout(100)
        summary = [{key: book.get(key) for key in ('status', 'reason', 'stage', 'chapterIndex', 'expectedUrl', 'previousUrl')}
                   for book in value.get('books', [])]
        self.fail('工程状态未在限定时间达到预期：' + json.dumps(summary, ensure_ascii=False))

    def start_link(self, number):
        result = self.command('SEARCH', query=self.source + f'/bqg/{number}/')
        self.assertEqual(result['calls'], 0)
        return self.command('DOWNLOAD', candidate=result['candidates'][0])['id']

    def test_shelf_catalog_only_and_update_reuse_chapter_identity(self):
        result = self.command('SEARCH', query=self.wxdy_source + '/books/20001/')
        book_id = self.command('ADD_BOOK', candidate=result['candidates'][0])['id']
        self.page.wait_for_timeout(250)
        self.assertEqual(self.state()['books'][0]['status'], 'shelved')
        self.assertEqual(self.requests, [])
        self.command('UPDATE_CATALOG', id=book_id)
        book = self.until(lambda s: s['books'][0]['reason'] == 'catalog_updated')['books'][0]
        self.assertEqual((book['status'], book['catalogCount'], book['savedCount']), ('shelved', 4, 0))
        self.assertFalse(any(p.endswith('/101.html') for p in self.requests))
        self.assertEqual(list(self.outputs.glob('*.txt')), [])
        self.command('RESUME', id=book_id)
        self.until(lambda s: s['books'][0]['artifact'] and s['books'][0]['artifact']['state'] == 'complete')
        before = self.requests.count('/books/20001/101.html')
        self.wxdy_chapters = [0, 1, 2, 3, 4, 5]
        self.command('UPDATE_CATALOG', id=book_id)
        book = self.until(lambda s: s['books'][0]['reason'] == 'catalog_updated')['books'][0]
        self.assertEqual((book['catalogCount'], book['savedCount'], book['rangeSavedCount'], book['newChapters']), (6, 3, 2, 2))
        self.assertEqual(len(list(self.outputs.glob('*.txt'))), 1)
        self.command('RESUME', id=book_id)
        book = self.until(lambda s: s['books'][0]['artifact'] and s['books'][0]['artifact']['state'] == 'complete')['books'][0]
        self.assertEqual((book['chapterIndex'], book['rangeSavedCount']), (3, 3))
        self.assertEqual(self.requests.count('/books/20001/101.html'), before)
        self.assertEqual(self.requests.count('/books/20001/100.html'), 1)
        records = self.page.evaluate('async id => (await import("./db.js")).listChapters(id)', book_id)
        self.assertEqual(len(records), 4)
        self.assertEqual(len(list(self.outputs.glob('*.txt'))), 2)

    def test_book_source_block_cancels_late_page_and_survives_refresh(self):
        self.verified.add('10001')
        self.release_page_two.clear()
        book_id = self.start_link('10001')
        deadline = time.monotonic() + 10
        while not self.page_two_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.page_two_started.is_set())
        self.command('BLOCK_SOURCE', id=book_id)
        self.release_page_two.set()
        self.page.wait_for_timeout(300)
        self.page.reload()
        book = self.state()['books'][0]
        self.assertEqual((book['status'], book['chapterIndex']), ('blocked', 0))
        self.assertEqual(book['blockedSources'][0]['key'], 'txxt')
        refused = self.page.evaluate('(id) => chrome.runtime.sendMessage({type:"RESUME",id})', book_id)
        self.assertEqual(refused['code'], 'source_blocked')
        records = self.page.evaluate('async id => (await import("./db.js")).listChapters(id)', book_id)
        self.assertEqual(records, [])
        self.command('SAVE_KEY', key=FAKE_KEY)
        self.search_results = [
            {'title': '示例小说 A', 'url': self.source + '/bqg/10001/', 'snippet': '作者：示例作者甲。章节目录。'},
            {'title': '示例小说 A', 'url': self.wxdy_source + '/books/20001/', 'snippet': '作者：示例作者甲。章节目录。'},
            {'title': '示例小说 B', 'url': self.source + '/bqg/10004/', 'snippet': '作者：示例作者乙。章节目录。'},
        ]
        result = self.command('SEARCH', query='示例小说 A')
        self.assertEqual([c['sourceId'] for c in result['candidates']], ['wxdy'])
        self.search_results.append({'title': '示例小说 A', 'url': self.source + '/bqg/10005/',
                                    'snippet': '作者：示例作者乙。章节目录。'})
        same_name = self.command('SEARCH', query='示例小说 A')
        self.assertTrue(any('/10005/' in c['url'] for c in same_name['candidates']))
        self.assertIsNone(same_name['preferredId'])
        self.page.evaluate('async id => (await import("./db.js")).changeBook(id, book => ({...book,author:""}))', book_id)
        unknown_author = self.command('SEARCH', query='示例小说 A')
        self.assertTrue(any('/10005/' in c['url'] for c in unknown_author['candidates']))
        self.assertFalse(any('/10001/' in c['url'] for c in unknown_author['candidates']))
        other = self.command('SEARCH', query='示例小说 B')
        self.assertEqual([c['sourceId'] for c in other['candidates']], ['txxt'])
        self.command('UNBLOCK_SOURCE', id=book_id, key='txxt')
        self.assertEqual(self.state()['books'][0]['status'], 'shelved')
        self.assertEqual(self.state()['books'][0]['blockedSources'], [])
        self.assertEqual(list(self.outputs.glob('*.txt')), [])

    def test_other_sources_reuses_search_and_switch_keeps_content_separate(self):
        self.command('SAVE_KEY', key=FAKE_KEY)
        first = {'title': '示例小说 A', 'url': self.source + '/bqg/10003/',
                 'snippet': '作者：示例作者甲。章节目录。'}
        self.search_results = [first]
        initial = self.command('SEARCH', query='示例小说 A')
        self.assertEqual(len(self.api_calls), 1)
        book_id = self.command('DOWNLOAD', candidate=initial['candidates'][0])['id']
        self.until(lambda s: s['books'][0]['artifact'] and s['books'][0]['artifact']['state'] == 'complete')
        replacement = {'title': '示例小说 A', 'url': self.wxdy_source + '/books/20001/',
                       'snippet': '作者：示例作者甲。章节目录。'}
        self.search_results = [first, replacement]
        self.page.locator('.main-nav [data-view="library"]').click()
        self.page.locator('#library-list [data-action="OPEN"]').click()
        self.page.locator('#detail-block').click()
        self.until(lambda s: s['search']['broadened'])
        self.page.wait_for_function('() => document.getElementById("search-note").hidden')
        result = self.state()['search']
        self.assertEqual((len(self.api_calls), result['calls']), (2, 1))
        self.assertEqual(len(result['candidates']), 1)
        self.command('SEARCH', bookId=book_id, includeOtherSources=True)
        self.assertEqual(len(self.api_calls), 2)
        source_card = self.page.locator('.book-result').filter(has_text='wxdy.cc')
        self.assertEqual(source_card.count(), 1)
        source_card.locator('[data-action="CANDIDATE"]').first.click()
        expect(self.page.locator('#detail-primary')).to_have_text('选为本书书源')
        self.page.locator('#detail-primary').click()
        self.until(lambda s: s['books'][0]['sourceId'] == 'wxdy')
        book = self.state()['books'][0]
        self.assertEqual((len(self.state()['books']), book['id'], book['status'], book['savedCount']), (1, book_id, 'shelved', 0))
        self.assertIsNone(book['artifact'])
        self.wxdy_title = '示例小说 A'
        self.wxdy_author = '示例作者甲'
        self.command('RESUME', id=book_id)
        book = self.until(lambda s: s['books'][0]['artifact'] and s['books'][0]['artifact']['state'] == 'complete')['books'][0]
        records = self.page.evaluate('async id => (await import("./db.js")).listChapters(id)', book_id)
        self.assertEqual(len(records), 6)
        newest = max(self.outputs.glob('*.txt'), key=lambda p: p.stat().st_mtime_ns).read_text(encoding='utf-8-sig')
        self.assertIn('首面示例正文。', newest)
        self.assertNotIn('前页示例正文。', newest)
        self.command('UNBLOCK_SOURCE', id=book_id, key='txxt')
        self.search_results = [first]
        self.command('SEARCH', query='示例小说 A')
        self.command('SET_SOURCE', id=book_id, candidate=first)
        book = self.state()['books'][0]
        self.assertEqual((book['sourceId'], book['title'], book['author'], book['rangeSavedCount']),
                         ('txxt', '示例小说 A', '示例作者甲', 3))
        self.command('EXPORT', id=book_id)
        self.until(lambda s: len(list(self.outputs.glob('*.txt'))) == 3
                   and s['books'][0]['artifact']['state'] == 'complete')
        restored = max(self.outputs.glob('*.txt'), key=lambda p: p.stat().st_mtime_ns).read_text(encoding='utf-8-sig')
        self.assertTrue(restored.startswith('示例小说 A\n作者：示例作者甲\n'))
        self.assertNotIn('首面示例正文。', restored)

    def test_source_identity_requires_same_title_and_author(self):
        self.command('SAVE_KEY', key=FAKE_KEY)
        first = {'title': '示例小说 A', 'url': self.source + '/bqg/10003/',
                 'snippet': '作者：示例作者甲。章节目录。'}
        same_title_other_author = {'title': '示例小说 A', 'url': self.wxdy_source + '/books/20001/',
                                   'snippet': '作者：示例作者乙。章节目录。'}
        self.search_results = [first, same_title_other_author]
        initial = self.command('SEARCH', query='示例小说 A')
        book_id = self.command('ADD_BOOK', candidate=initial['candidates'][0])['id']

        failed = self.page.evaluate(
            '(message) => chrome.runtime.sendMessage(message)',
            {'type': 'SET_SOURCE', 'id': book_id, 'candidate': same_title_other_author})
        self.assertEqual(failed['code'], 'work_mismatch')
        book = self.state()['books'][0]
        self.assertEqual((book['id'], book['sourceId'], book['title'], book['author']),
                         (book_id, 'txxt', '示例小说 A', '示例作者甲'))
        self.assertEqual(len(self.state()['books']), 1)

        same_author = {'title': '示例小说 A', 'url': self.wxdy_source + '/books/20002/',
                       'snippet': '作者：示例作者甲。章节目录。'}
        unknown_author = {'title': '示例小说 A', 'url': self.wxdy_source + '/books/20003/',
                          'snippet': '章节目录。'}
        self.search_results = [first, same_author, unknown_author]
        self.command('SEARCH', query='示例小说 A')
        search = self.state()['search']
        if not any('/20003/' in candidate['url'] for candidate in search['candidates']):
            self.fail(json.dumps(search, ensure_ascii=False))
        unknown = self.page.evaluate(
            '(message) => chrome.runtime.sendMessage(message)',
            {'type': 'SET_SOURCE', 'id': book_id, 'candidate': unknown_author})
        self.assertEqual(unknown['code'], 'work_mismatch')
        self.assertEqual(self.state()['books'][0]['sourceId'], 'txxt')
        self.command('SET_SOURCE', id=book_id, candidate=same_author)
        book = self.state()['books'][0]
        self.assertEqual((book['id'], book['sourceId'], book['title'], book['author']),
                         (book_id, 'wxdy', '示例小说 A', '示例作者甲'))
        self.assertEqual(len(self.state()['books']), 1)

    def test_adding_same_work_from_another_source_reuses_book_id(self):
        self.command('SAVE_KEY', key=FAKE_KEY)
        first = {'title': '同作示例小说', 'url': self.source + '/bqg/10003/',
                 'snippet': '作者：示例作者甲。章节目录。'}
        second = {'title': '同作示例小说', 'url': self.wxdy_source + '/books/20001/',
                  'snippet': '作者：示例作者甲。章节目录。'}
        other = {'title': '同作示例小说', 'url': self.source + '/bqg/10004/',
                 'snippet': '作者：示例作者乙。章节目录。'}
        self.search_results = [first, second, other]
        result = self.command('SEARCH', query='同作示例小说')
        first_id = self.command('ADD_BOOK', candidate=result['candidates'][0])['id']
        second_id = self.command('ADD_BOOK', candidate=result['candidates'][1])['id']
        other_id = self.command('ADD_BOOK', candidate=result['candidates'][2])['id']
        books = self.state()['books']
        self.assertEqual(first_id, second_id)
        self.assertNotEqual(first_id, other_id)
        self.assertEqual(len(books), 2)
        self.assertIn('示例作者甲', [book['author'] for book in books])
        self.assertIn('示例作者乙', [book['author'] for book in books])
        self.assertIn('txxt', [book['sourceId'] for book in books])
        self.assertIn('wxdy', [book['sourceId'] for book in books])

    def test_catalog_identity_mismatch_does_not_rename_existing_book(self):
        self.command('SAVE_KEY', key=FAKE_KEY)
        first = {'title': '目录核对示例小说', 'url': self.source + '/bqg/10003/',
                 'snippet': '作者：示例作者甲。章节目录。'}
        self.search_results = [first]
        result = self.command('SEARCH', query='目录核对示例小说')
        book_id = self.command('ADD_BOOK', candidate=result['candidates'][0])['id']
        self.page.evaluate('async id => { const db = await import("./db.js"); await db.changeBook(id, book => ({...book, title:"示例小说 C", author:"示例作者乙"})); }', book_id)
        self.command('UPDATE_CATALOG', id=book_id)
        book = self.until(lambda state: state['books'][0]['reason'] == 'work_mismatch')['books'][0]
        self.assertEqual((book['title'], book['author'], book['catalogCount']), ('示例小说 C', '示例作者乙', 0))

    def test_block_during_automatic_txt_build_does_not_deliver_file(self):
        worker = self.context.service_workers[-1]
        worker.evaluate('''() => {
          const original = chrome.runtime.sendMessage.bind(chrome.runtime);
          chrome.runtime.sendMessage = async message => {
            const value = await original(message);
            if (message.target === 'offscreen' && message.type === 'BUILD') {
              await new Promise(resolve => { globalThis.releaseBuild = resolve; });
            }
            return value;
          };
        }''')
        book_id = self.start_link('10003')
        self.until(lambda s: (s['books'][0].get('artifact') or {}).get('state') == 'preparing')
        self.page.wait_for_function('async () => (await chrome.runtime.getContexts({contextTypes:["OFFSCREEN_DOCUMENT"]})).length === 1')
        deadline = time.monotonic() + 5
        while not worker.evaluate('() => !!globalThis.releaseBuild') and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(worker.evaluate('() => !!globalThis.releaseBuild'))
        self.command('BLOCK_SOURCE', id=book_id)
        worker.evaluate('() => globalThis.releaseBuild()')
        self.until(lambda s: s['books'][0]['artifact']['state'] == 'interrupted')
        self.assertEqual(self.state()['books'][0]['status'], 'blocked')
        self.assertEqual(list(self.outputs.glob('*.txt')), [])

    def test_block_after_artifact_write_still_cancels_automatic_delivery(self):
        worker = self.context.service_workers[-1]
        worker.evaluate('''() => {
          const original = IDBObjectStore.prototype.put;
          IDBObjectStore.prototype.put = function(value) {
            if (this.name === 'artifacts' && value.state === 'starting') {
              const transaction = this.transaction;
              Object.defineProperty(transaction, 'oncomplete', {set(handler) {
                transaction.addEventListener('complete', event => {
                  globalThis.releaseStarting = () => handler(event);
                }, {once:true});
              }});
            }
            return original.call(this, value);
          };
        }''')
        book_id = self.start_link('10003')
        self.until(lambda s: (s['books'][0].get('artifact') or {}).get('state') == 'starting')
        self.assertTrue(worker.evaluate('() => !!globalThis.releaseStarting'))
        self.command('BLOCK_SOURCE', id=book_id)
        worker.evaluate('() => globalThis.releaseStarting()')
        self.until(lambda s: s['books'][0]['artifact']['state'] == 'interrupted')
        self.assertEqual(self.state()['books'][0]['status'], 'blocked')
        self.assertEqual(list(self.outputs.glob('*.txt')), [])

    def test_key_search_worker_recovery_confirmation_ui_close_and_txt(self):
        self.release_page_two.clear()
        self.assertFalse(self.state()['configured'])
        self.page.locator('#settings-open').click()
        self.page.locator('#api-key').fill(FAKE_KEY)
        self.page.locator('#settings-form button[type=submit]').click()
        expect(self.page.locator('#settings-dialog')).not_to_be_visible()
        self.assertEqual(self.page.locator('#api-key').input_value(), '')
        self.assertTrue(self.state()['configured'])
        self.assertNotIn(FAKE_KEY, json.dumps(self.state()))
        self.page.locator('#search-input').fill('示例小说 A')
        self.page.locator('#search-submit').click()
        expect(self.page.locator('#book-dialog')).to_be_visible()
        self.assertEqual(self.state()['books'], [])
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.set_viewport_size({'width': 1280, 'height': 800})
        expect(self.page.locator('#detail-primary')).to_have_text('加入书架')
        self.page.locator('#detail-primary').click()
        self.until(lambda state: bool(state['books']) and state['books'][0]['status'] == 'shelved')
        self.assertEqual(self.requests, [])
        expect(self.page.locator('#detail-primary')).to_have_text('下载前 3 章')
        self.page.locator('#detail-primary').click()
        self.until(lambda state: bool(state['books']) and state['books'][0]['expectedUrl'] is not None)
        deadline = time.monotonic() + 10
        while not self.page_two_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.page_two_started.is_set())
        # Explicitly terminate, without attaching a worker inspector.
        self.cdp.send('ServiceWorker.enable')
        self.cdp.send('ServiceWorker.stopAllWorkers')
        self.page.close()
        self.release_page_two.set()
        deadline = time.monotonic() + 45
        source_page = None
        while time.monotonic() < deadline:
            source_page = next((p for p in self.context.pages if '/102.html' in p.url), None)
            if source_page and source_page.locator('#fixture-confirm').count():
                break
            self.context.pages[0].wait_for_timeout(100)
        self.assertIsNotNone(source_page)
        # This is our own local button, never a real challenge or safety prompt.
        source_page.locator('#fixture-confirm').click()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not list(self.outputs.glob('*.txt')):
            source_page.wait_for_timeout(100)
        files = list(self.outputs.glob('*.txt'))
        if not files:
            self.page = self.context.new_page()
            self.page.goto(f'chrome-extension://{self.extension_id}/index.html')
            value = self.state()['books'][0]
            self.fail('TXT未交付：' + json.dumps({key: value.get(key) for key in
                      ('status', 'reason', 'chapterIndex', 'artifact')}, ensure_ascii=False))
        self.assertEqual(len(files), 1)
        output = files[0].read_text(encoding='utf-8-sig')
        self.assertEqual(output.count('前页示例正文。'), 20)
        self.assertEqual(output.count('续页示例正文。'), 20)
        self.assertNotIn('本章未完', output)
        self.assertIn('已取得 3 章；来源目录 4 项', output)
        self.assertNotIn('/104.html', ''.join(self.requests))
        self.page = self.context.new_page()
        self.page.goto(f'chrome-extension://{self.extension_id}/index.html')
        state = self.until(lambda state: state['books'][0]['artifact']['state'] == 'complete')
        self.assertEqual(state['books'][0]['chapterIndex'], 3)
        contexts = self.page.evaluate('() => chrome.runtime.getContexts({contextTypes:["OFFSCREEN_DOCUMENT"]})')
        self.assertEqual(contexts, [])
        self.assertEqual(len(self.api_calls), 1)
        self.command('EXPORT', id=state['books'][0]['id'])
        self.until(lambda state: state['books'][0]['artifact']['state'] == 'complete')
        self.assertEqual(len(list(self.outputs.glob('*.txt'))), 2)
        self.cdp = self.context.new_cdp_session(self.page)
        self.cdp.send('ServiceWorker.enable')
        self.cdp.send('ServiceWorker.stopAllWorkers')
        self.page.wait_for_timeout(150)
        self.state()
        self.page.wait_for_timeout(300)
        self.assertEqual(len(list(self.outputs.glob('*.txt'))), 2)

    def test_pause_late_page_and_browser_restart(self):
        self.verified.add('10001')
        self.release_page_two.clear()
        book_id = self.start_link('10001')
        deadline = time.monotonic() + 10
        while not self.page_two_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.page_two_started.is_set())
        self.command('PAUSE', id=book_id)
        self.release_page_two.set()
        self.page.wait_for_timeout(600)
        book = self.state()['books'][0]
        self.assertEqual((book['status'], book['chapterIndex']), ('paused', 0))
        self.command('RESUME', id=book_id)
        self.until(lambda state: state['books'][0]['chapterIndex'] >= 1)
        self.context.close()
        self.context = None
        self.launch()
        book = self.until(lambda state: state['books'][0]['status'] == 'paused')['books'][0]
        self.assertEqual(book['reason'], 'browser_restarted')
        self.command('RESUME', id=book_id)
        self.until(lambda state: state['books'][0]['status'] == 'ready')
        chapters = self.page.evaluate('async (id) => { const db = await import("./db.js"); return db.listChapters(id); }', book_id)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0]['text'].count('前页示例正文。'), 20)

    def test_denied_source_and_closed_source_preserve_job(self):
        book_id = self.start_link('10002')
        book = self.until(lambda state: state['books'][0]['status'] == 'partial')['books'][0]
        self.assertEqual(book['reason'], 'access_denied')
        self.assertTrue(book['lastIssue']['targetUrl'].endswith('/101.html'))
        self.assertEqual(book['lastIssue']['stage'], 'chapter')
        self.assertEqual(book['chapterIndex'], 0)
        self.assertEqual(list(self.outputs.glob('*.txt')), [])
        self.command('RESUME', id=book_id)
        book = self.until(lambda state: state['books'][0]['status'] == 'downloading')['books'][0]
        self.page.evaluate('(id) => chrome.tabs.remove(id)', book['tabId'])
        book = self.until(lambda state: state['books'][0]['status'] == 'paused')['books'][0]
        self.assertEqual(book['reason'], 'source_closed')
        self.assertEqual(len(self.state()['books']), 1)

    def test_atomic_chapter_failure_does_not_advance(self):
        worker = self.context.service_workers[-1]
        worker.evaluate('''() => {
          const original = IDBObjectStore.prototype.put;
          IDBObjectStore.prototype.put = function(value) {
            if (this.name === 'chapters') throw new DOMException('fixture failure', 'QuotaExceededError');
            return original.call(this, value);
          };
        }''')
        book_id = self.start_link('10003')
        book = self.until(lambda state: state['books'][0]['status'] == 'partial')['books'][0]
        self.assertEqual(book['reason'], 'storage_failed')
        self.assertEqual(book['chapterIndex'], 0)
        chapters = self.page.evaluate('async (id) => (await import("./db.js")).listChapters(id)', book_id)
        self.assertEqual(chapters, [])

    def test_source_parser_rejects_missing_and_wrong_continuations(self):
        page = self.context.new_page()
        page.goto(self.source + '/bqg/10006/101.html')
        body = '示例正文内容。' * 30
        for next_url in [None, '/bqg/10006/101.html', '/bqg/10006/102.html', '/bqg/99999/101_2.html']:
            link = f'<a href="{next_url}">下一页</a>' if next_url else ''
            page.set_content(f'<h1>第1章 示例章节</h1><div id="content">{body}（本章未完，请点击下一页继续阅读）</div>{link}')
            page.add_script_tag(path=str(self.extension / 'read-source.js'))
            result = page.evaluate('() => shuyeReadSource("chapter")')
            self.assertEqual(result['kind'], 'content_incomplete')
        page.set_content(f'<h1>第1章 示例章节</h1><div id="content">{body}</div><a href="/bqg/10006/102.html">下一章</a>')
        page.add_script_tag(path=str(self.extension / 'read-source.js'))
        self.assertEqual(page.evaluate('() => shuyeReadSource("chapter").next'), None)
        page.goto(self.source + '/bqg/10006/')
        page.add_script_tag(path=str(self.extension / 'read-source.js'))
        result = page.evaluate('() => shuyeReadSource("catalog")')
        self.assertEqual(len(result['chapters']), 4)
        self.assertEqual([item['title'] for item in result['chapters']], [f'第{i}章 示例章节' for i in range(1, 5)])
        page.set_content('<div id="list"><dl>'
            '<dd><a href="/bqg/10006/101.html">作者公告</a></dd>'
            '<dd><a href="/bqg/10006/101.html">第1章 示例章节（求月票）</a></dd>'
            '<dd><a href="/bqg/10006/999.html">上架感言</a></dd></dl></div>')
        page.add_script_tag(path=str(self.extension / 'read-source.js'))
        result = page.evaluate('() => shuyeReadSource("catalog")')
        self.assertEqual(len(result['chapters']), 1)
        self.assertEqual(result['chapters'][0]['title'], '第1章 示例章节（求月票）')

    def test_novel_results_expand_and_restore_without_another_api_request(self):
        self.command('SAVE_KEY', key=FAKE_KEY)
        self.search_results = [
            {'title': '中性长篇新书推广', 'url': 'https://m.sohu.com/a/101',
             'snippet': '推荐小说中性长篇，作者访谈与新书资讯。'},
            {'title': '[推书] 中性长篇', 'url': 'https://ngabbs.com/read.php?tid=102',
             'snippet': '小说推荐，讨论中性长篇最新章节。'},
            {'title': '中性长篇 - 最新章节', 'url': 'https://xiaoshuo.qq.com/book/103',
             'snippet': '作者：示例作者。章节目录。'},
            {'title': '中性长篇 - 最新章节', 'url': self.source + '/bqg/104/',
             'snippet': '作者：示例作者。章节目录。'},
            {'title': '中性长篇 - 最新章节', 'url': self.source + '/bqg/104/1.html',
             'snippet': '作者：示例作者。章节目录。'},
        ] + [
            {'title': f'中性长篇 {index} - 最新章节', 'url': f'https://reader-{index}.test/book/104/',
             'snippet': '作者：示例作者。章节目录，全文阅读。'}
            for index in range(23)
        ]
        self.page.locator('#search-input').fill('中性')
        self.page.locator('#search-submit').click()
        expect(self.page.locator('.book-result')).to_have_count(10)
        expect(self.page.locator('#search-summary')).to_have_text('筛选后共 24 条，已显示 10 条')
        facts = self.page.locator('.book-result .book-facts').all_text_contents()
        self.assertEqual(len(set(facts)), 10)
        self.assertFalse({'m.sohu.com', 'ngabbs.com', 'xiaoshuo.qq.com'}.intersection(facts))
        self.assertEqual(len(self.api_calls), 2)
        self.page.locator('#search-more').click()
        expect(self.page.locator('.book-result')).to_have_count(20)
        self.page.locator('#search-more').click()
        expect(self.page.locator('.book-result')).to_have_count(24)
        expect(self.page.locator('#search-more')).not_to_be_visible()
        self.page.wait_for_function('async () => (await chrome.storage.session.get("searchView")).searchView?.shown === 24')
        self.page.reload()
        expect(self.page.locator('.book-result')).to_have_count(24)
        self.page.locator('.main-nav [data-view="library"]').click()
        self.page.locator('.main-nav [data-view="search"]').click()
        expect(self.page.locator('.book-result')).to_have_count(24)
        self.page.locator('#search-collapse').click()
        expect(self.page.locator('.book-result')).to_have_count(10)
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertEqual(len(self.api_calls), 2)
        self.assertEqual(self.state()['books'], [])
        self.assertEqual(self.requests, [])
        expect(self.page.locator('#book-dialog')).not_to_be_visible()
        previous_search = self.state()['search']['sessionId']
        self.page.locator('#search-submit').click()
        self.until(lambda state: state['search']['sessionId'] != previous_search)
        expect(self.page.locator('.book-result')).to_have_count(10)
        self.page.wait_for_function('() => document.getElementById("search-note").hidden')
        self.assertEqual(len(self.api_calls), 4)

    def test_local_update_waits_for_download_and_reopens_with_settings(self):
        manager = self.context.new_page()
        manager.goto('chrome://extensions')
        manager.evaluate('() => chrome.developerPrivate.updateProfileConfiguration({inDeveloperMode:true})')
        manager.close()
        self.page.bring_to_front()
        self.command('SAVE_KEY', key=FAKE_KEY)
        self.release_page_two.clear()
        book_id = self.start_link('10001')
        deadline = time.monotonic() + 10
        while not self.page_two_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.page_two_started.is_set())
        before = self.page.evaluate('() => chrome.runtime.getManifest().version')
        manifest = self.extension / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['version'] = '0.2.99'
        data['host_permissions'].append('https://*.wxdy.cc/*')
        manifest.write_text(json.dumps(data))
        self.page.evaluate('() => window.dispatchEvent(new Event("focus"))')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.evaluate('() => chrome.runtime.getManifest().version'), before)
        self.assertEqual(self.state()['books'][0]['status'], 'downloading')
        self.command('PAUSE', id=book_id)
        self.release_page_two.set()
        self.page.evaluate('() => window.dispatchEvent(new Event("focus"))')
        deadline = time.monotonic() + 12
        updated = None
        while time.monotonic() < deadline:
            for page in self.context.pages:
                if page.is_closed() or not page.url.startswith(f'chrome-extension://{self.extension_id}/index.html'):
                    continue
                try:
                    if page.evaluate('() => chrome.runtime.getManifest().version') == '0.2.99':
                        updated = page
                        break
                except Exception:
                    pass  # The extension reload destroys its old page context.
            if updated:
                break
            self.context.pages[0].wait_for_timeout(100)
        if updated is None:
            diagnostics = []
            for page in self.context.pages:
                if page.is_closed() or not page.url.startswith(f'chrome-extension://{self.extension_id}/index.html'):
                    continue
                try:
                    diagnostics.append(page.evaluate('''async () => ({
                        loaded: chrome.runtime.getManifest().version,
                        disk: (await (await fetch(chrome.runtime.getURL('manifest.json'), {cache:'no-store'})).json()).version,
                        hidden: document.hidden,
                        dialogs: [...document.querySelectorAll('dialog[open]')].map(d => d.id),
                        update: (await chrome.storage.local.get('reopenAfterUpdate')).reopenAfterUpdate
                    })'''))
                except Exception as error:
                    diagnostics.append({'page_error': type(error).__name__})
            self.fail('本地更新未应用：' + json.dumps(diagnostics, ensure_ascii=False))
        self.page = updated
        expect(self.page.locator('#key-state')).to_have_text('搜索已配置')
        self.assertTrue(self.state()['configured'])
        self.assertEqual(self.state()['books'][0]['id'], book_id)
        self.assertEqual(self.state()['books'][0]['status'], 'paused')
        self.assertTrue(self.page.evaluate('() => chrome.permissions.contains({origins:["https://*.wxdy.cc/*"]})'))
        self.assertEqual(self.api_calls, [])

    def test_wxdy_catalog_checkpoint_continuations_and_txt(self):
        self.wxdy_noise = True
        self.wxdy_title = '中性长篇无弹窗手机版'
        self.release_wxdy_catalog.clear()
        self.page.locator('#search-input').fill(self.wxdy_source + '/books/20001/')
        self.page.locator('#search-submit').click()
        expect(self.page.locator('#book-dialog')).to_be_visible()
        expect(self.page.locator('#detail-primary')).to_be_enabled()
        self.assertEqual(self.state()['search']['calls'], 0)
        self.assertEqual(self.state()['search']['candidates'][0]['sourceId'], 'wxdy')
        self.page.locator('#detail-primary').click()
        expect(self.page.locator('#detail-primary')).to_have_text('下载前 3 章')
        self.page.locator('#detail-primary').click()
        book_id = self.until(lambda state: bool(state['books']))['books'][0]['id']
        deadline = time.monotonic() + 10
        while not self.wxdy_catalog_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.wxdy_catalog_started.is_set())
        book = self.state()['books'][0]
        self.assertEqual((book['stage'], book['catalogCount'], book['chapterIndex']), ('catalog', 2, 0))
        self.cdp.send('ServiceWorker.enable')
        self.cdp.send('ServiceWorker.stopAllWorkers')
        self.release_wxdy_catalog.set()
        self.state()
        value = self.until(lambda state: (state['books'][0].get('artifact') or {}).get('state') == 'complete')
        book = value['books'][0]
        self.assertEqual((book['id'], book['catalogCount'], book['chapterIndex']), (book_id, 4, 3))
        self.assertEqual(len(book['catalogPages']), 2)
        self.assertNotIn('/books/20001/999.html', self.requests)
        chapters = self.page.evaluate('async (id) => (await import("./db.js")).listChapters(id)', book_id)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(len(chapters[0]['pages']), 2)
        self.assertEqual(chapters[0]['text'].count('续页示例正文。'), 20)
        saved = next(self.outputs.glob('*.txt')).read_text()
        self.assertNotIn('隐藏干扰', saved)
        self.assertNotIn('正文外广告', saved)
        self.assertNotIn('正文外备注', saved)
        self.assertNotIn('订阅文学网', saved)
        self.assertNotIn('第12页', saved)
        self.assertNotIn('本章未完', saved)
        self.assertNotIn('无弹窗手机版', saved)
        self.assertEqual(self.api_calls, [])
        # Simulate content stored by an earlier release. Re-export must apply
        # the current cleanup too, without another source request.
        count = len(self.requests)
        self.page.evaluate('''async id => {
          const db = await import('./db.js');
          const chapter = (await db.listChapters(id))[0];
          chapter.text = chapter.title + '（第12页）\\n' + chapter.text
            + '\\n一秒记住【订阅文学网 https://www.wxdy.cc】，精彩小说无弹窗免费阅读！';
          await db.changeBook(id, book => ({...book, title:'中性长篇无弹窗手机版'}), chapter);
        }''', book_id)
        self.command('EXPORT', id=book_id)
        self.until(lambda s: s['books'][0]['artifact']['state'] == 'complete'
                   and len(list(self.outputs.glob('*.txt'))) == 2)
        newest = max(self.outputs.glob('*.txt'), key=lambda p: p.stat().st_mtime_ns)
        self.assertNotIn('无弹窗手机版', newest.name)
        self.assertNotIn('订阅文学网', newest.read_text())
        self.assertNotIn('第12页', newest.read_text())
        self.assertEqual(len(self.requests), count)

    def test_wxdy_parser_rejects_missing_pages_and_wrong_catalogs(self):
        page = self.context.new_page()
        page.goto(self.wxdy_source + '/books/20001/101.html')
        page.add_script_tag(path=str(self.extension / 'read-wxdy.js'))
        result = page.evaluate('() => shuyeReadSource("chapter")')
        self.assertEqual(result['next'], self.wxdy_source + '/books/20001/101_1.html')
        self.assertNotIn('隐藏干扰', result['text'])
        template = '<div id="content"><h1>第一章 示例章节</h1><div id="htmlContent" class="chapter-content">' + '示例正文。' * 20 + '{tail}</div></div>{link}'
        for tail, link in [
            ('本章未完', ''),
            ('', '<a href="/books/20001/101_2.html">下一章</a>'),
            ('', '<a href="/books/20002/101_1.html">下一页</a>'),
            ('', '<a href="/books/20001/101.html">下一章</a>')
        ]:
            page.set_content(template.format(tail=tail, link=link))
            page.add_script_tag(path=str(self.extension / 'read-wxdy.js'))
            self.assertEqual(page.evaluate('() => shuyeReadSource("chapter")')['kind'], 'content_incomplete')
        page.set_content('<div class="info"><a href="/books/20001/999.html">第九十九章 最新</a></div>')
        page.add_script_tag(path=str(self.extension / 'read-wxdy.js'))
        self.assertEqual(page.evaluate('() => shuyeReadSource("catalog")')['kind'], 'structure_mismatch')

        page.goto(self.wxdy_source + '/books/20001/')
        catalog = ('<meta property="og:novel:book_name" content="中性长篇">'
                   '<meta property="og:novel:author" content="示例作者">'
                   '<meta property="og:novel:latest_chapter_url" content="{latest}">'
                   '<ul class="mulu_list"><li><a href="/books/20001/101.html">第一章 示例章节</a></li>'
                   '<li><a href="/books/20001/102.html">第二章 示例章节</a></li></ul>{links}')
        for latest in ['http://wxdy.cc/books/20001/102.html?utm_source=fixture#end', '/books/20001/102.html']:
            page.set_content(catalog.format(latest=latest, links='<a href="/books/20001/index_1.html">更多章节</a>'))
            page.add_script_tag(path=str(self.extension / 'read-wxdy.js'))
            self.assertIsNone(page.evaluate('() => shuyeReadSource("catalog")')['nextCatalog'])
        for links, expected in [
            ('<a href="/comments?page=2">下一页</a><a href="/books/20001/index_1.html?utm_source=fixture">更多章节</a>', 'catalog'),
            ('<a href="/books/20001/index_2.html">更多章节</a>', 'content_incomplete'),
            ('<a href="/books/20001/index_1.html">更多章节</a><a href="/books/20001/index_3.html">下一页</a>', 'content_incomplete')
        ]:
            page.set_content(catalog.format(latest='/books/20001/104.html', links=links))
            page.add_script_tag(path=str(self.extension / 'read-wxdy.js'))
            self.assertEqual(page.evaluate('() => shuyeReadSource("catalog")')['kind'], expected)

    def test_search_during_download_and_late_search_cancellation(self):
        self.release_page_two.clear()
        book_id = self.start_link('10001')
        self.command('SAVE_KEY', key=FAKE_KEY)
        deadline = time.monotonic() + 10
        while not self.page_two_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.page_two_started.is_set())
        found = self.command('SEARCH', query='示例小说 A')
        self.assertEqual(len(found['candidates']), 1)
        self.assertEqual(self.state()['books'][0]['chapterIndex'], 0)
        self.page.evaluate('''() => { window.oldSearch = chrome.runtime.sendMessage({type:'SEARCH',query:'旧查询'}); }''')
        deadline = time.monotonic() + 5
        while not self.old_search_started.is_set() and time.monotonic() < deadline:
            self.page.wait_for_timeout(50)
        self.assertTrue(self.old_search_started.is_set())
        direct = self.source + '/bqg/10003/'
        self.command('SEARCH', query=direct)
        self.release_old_search.set()
        old = self.page.evaluate('() => window.oldSearch')
        self.assertFalse(old['ok'])
        self.assertEqual(old['code'], 'aborted')
        self.assertEqual(self.state()['search']['query'], direct)
        self.assertEqual(len(self.state()['books']), 1)
        self.command('PAUSE', id=book_id)
        self.release_page_two.set()

    def test_unconfirmed_delivery_is_not_repeated_after_worker_recovery(self):
        worker = self.context.service_workers[-1]
        worker.evaluate('''() => { chrome.downloads.download = async () => { throw new Error('fixture gap'); }; }''')
        book_id = self.start_link('10003')
        state = self.until(lambda state: (state['books'][0].get('artifact') or {}).get('state') == 'needs_confirmation')
        self.assertEqual(state['books'][0]['chapterIndex'], 3)
        self.assertEqual(list(self.outputs.glob('*.txt')), [])
        self.cdp.send('ServiceWorker.enable')
        self.cdp.send('ServiceWorker.stopAllWorkers')
        self.page.wait_for_timeout(100)
        state = self.state()
        self.assertEqual(state['books'][0]['artifact']['state'], 'needs_confirmation')
        self.assertEqual(self.page.evaluate('() => chrome.downloads.search({})'), [])
        self.command('EXPORT', id=book_id)
        self.until(lambda state: state['books'][0]['artifact']['state'] == 'complete')
        self.assertEqual(len(list(self.outputs.glob('*.txt'))), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
