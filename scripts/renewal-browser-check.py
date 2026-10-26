"""Run the real extension with a program-only credential and an isolated profile.

Only fixed status fields and counts are printed. No credential, HTTP headers,
book titles, search text, response body, browser storage or screenshots are logged.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import traceback

from playwright.sync_api import sync_playwright

from book_downloader.browser import _browser_environment, find_chrome_executable


ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / '.local/renewal/credentials/deepseek.key'


def report(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def command(page, kind, **values):
    result = page.evaluate('(message) => chrome.runtime.sendMessage(message)', {'type': kind, **values})
    return result if isinstance(result, dict) else {'ok': False, 'code': 'no_response'}


def main() -> int:
    parser = argparse.ArgumentParser()
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument('--search-only', action='store_true')
    phase.add_argument('--source-only', action='store_true')
    parser.add_argument('--query', default='示例小说')
    parser.add_argument('--source-url')
    parser.add_argument('--check-source-exclusion', action='store_true',
                        help='Headless search-only check of book-specific blocking and result reuse.')
    args = parser.parse_args()
    if not args.source_only and not KEY_PATH.is_file():
        report(status='key_not_configured')
        return 1
    try:
        # This value stays inside the validation process. Never add it to output.
        key = None if args.source_only else KEY_PATH.read_text(encoding='utf-8').strip()
        with TemporaryDirectory(prefix='shuye-real-check-') as temporary, sync_playwright() as playwright:
            folder = Path(temporary)
            profile = folder / 'profile'
            outputs = folder / 'downloads'
            outputs.mkdir()
            extension = ROOT / 'extension'
            process = None
            if args.search_only:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), executable_path=str(find_chrome_executable()), headless=True,
                    env=_browser_environment(profile), downloads_path=str(outputs),
                    args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
            else:
                # A normal isolated Chrome process, observed through its standard
                # debugging interface. No challenge interaction or browser flags
                # that disable security / alter automation detection are used.
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                process = subprocess.Popen([str(find_chrome_executable()), f'--user-data-dir={profile}',
                    f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1',
                    '--no-first-run', '--no-default-browser-check', '--start-minimized', f'--load-extension={extension}', 'about:blank'],
                    env=_browser_environment(profile) or dict(os.environ),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                browser = None
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=1000)
                        break
                    except Exception:
                        time.sleep(0.2)
                if browser is None:
                    process.terminate()
                    process.wait(timeout=5)
                    report(status='isolated_chrome_start_failed')
                    return 1
                context = browser.contexts[0]
            try:
                search_requests = []
                source_navigation_errors = []
                worker = None
                def observe_worker(item):
                    nonlocal worker
                    if item.url.startswith('chrome-extension://') and item.url.endswith('/worker.js'):
                        worker = item
                context.on('serviceworker', observe_worker)
                context.on('request', lambda request: search_requests.append(1)
                           if request.url.startswith('https://api.deepseek.com/v1/tools/search') else None)
                def navigation_failed(request):
                    if request.is_navigation_request() and 'wxdy.cc/books/' in request.url:
                        code = re.search(r'ERR_[A-Z_]+', request.failure or '')
                        source_navigation_errors.append(code.group(0) if code else 'navigation_failed')
                context.on('requestfailed', navigation_failed)
                is_book_worker = lambda item: item.url.startswith('chrome-extension://') and item.url.endswith('/worker.js')
                worker = worker or next((item for item in context.service_workers if is_book_worker(item)), None)
                if worker is None:
                    worker = context.wait_for_event('serviceworker', predicate=is_book_worker)
                extension_id = worker.url.split('/')[2]
                page = context.new_page() if args.search_only else context.pages[0]
                page.goto(f'chrome-extension://{extension_id}/index.html')
                browser_cdp = context.browser.new_browser_cdp_session()
                browser_cdp.send('Browser.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': str(outputs), 'eventsEnabled': True})
                candidate = None
                if not args.source_only:
                    if args.search_only:
                        page.locator('#settings-open').click()
                        page.locator('#api-key').fill(key)
                        page.locator('#settings-form button[type=submit]').click()
                        page.locator('#settings-dialog').wait_for(state='hidden')
                    elif not command(page, 'SAVE_KEY', key=key).get('ok'):
                        report(status='credential_save_failed')
                        return 1
                    if page.locator('#api-key').input_value():
                        report(status='credential_input_not_cleared')
                        return 1
                    state = command(page, 'STATE')
                    if not state.get('ok') or not state['value']['configured'] or key in json.dumps(state):
                        report(status='credential_boundary_failed')
                        return 1
                    report(phase='settings', status='saved_and_input_cleared', key_exposed=False)
                    result = command(page, 'SEARCH', query=args.query)
                    if not result.get('ok'):
                        report(phase='search', status='failed', code=result.get('code'))
                        return 1
                    value = result['value']
                    report(phase='search', status='success', candidates=len(value['candidates']), calls=value['calls'],
                           candidate_reasons=value.get('diagnostics'))
                    if not 0 <= len(value['candidates']) <= 100 or value['calls'] > 2:
                        report(status='search_contract_failed')
                        return 1
                    if args.search_only:
                        if args.check_source_exclusion:
                            candidate = next((item for item in value['candidates'] if item['supported']), None)
                            if candidate is None:
                                report(status='api_no_supported_candidate')
                                return 2
                            added = command(page, 'ADD_BOOK', candidate=candidate)
                            if not added.get('ok'):
                                report(status='shelf_add_failed')
                                return 1
                            book_id = added['value']['id']
                            if not command(page, 'BLOCK_SOURCE', id=book_id).get('ok'):
                                report(status='book_source_block_failed')
                                return 1
                            alternative = command(page, 'SEARCH', bookId=book_id, includeOtherSources=True)
                            if not alternative.get('ok'):
                                report(status='alternative_search_failed', code=alternative.get('code'))
                                return 1
                            alternatives = alternative['value']
                            keys = page.evaluate('''async urls => {
                              const {sourceKeyForUrl} = await import('./source-rules.js');
                              return urls.map(sourceKeyForUrl);
                            }''', [candidate['url']] + [item['url'] for item in alternatives['candidates']])
                            if any(key == keys[0] for key in keys[1:]):
                                report(status='blocked_source_returned')
                                return 1
                            before_requests = len(search_requests)
                            repeated = command(page, 'SEARCH', bookId=book_id, includeOtherSources=True)
                            page.reload()
                            state = command(page, 'STATE')['value']
                            book = state['books'][0]
                            valid = repeated.get('ok') and repeated['value']['calls'] == 0
                            valid = valid and len(search_requests) == before_requests
                            valid = valid and book['status'] == 'blocked' and book['savedCount'] == 0
                            if not valid:
                                report(status='book_source_exclusion_recovery_failed')
                                return 1
                            report(phase='book_source_exclusion', status='verified',
                                   alternatives=len(alternatives['candidates']), extra_calls=alternatives['calls'],
                                   reused_repeat_calls=0, refresh_preserved=True, whole_book_verified=False)
                            return 0
                        page.reload()
                        page.locator('#search-summary').wait_for()
                        total = len(value['candidates'])
                        shown = page.locator('.book-result').count()
                        if shown != min(10, total):
                            report(status='initial_visible_count_failed')
                            return 1
                        before_requests = len(search_requests)
                        if total > 10:
                            page.locator('#search-more').click()
                            shown = min(20, total)
                            page.wait_for_function('(count) => document.querySelectorAll(".book-result").length === count', arg=shown)
                            page.wait_for_function('async (count) => (await chrome.storage.session.get("searchView")).searchView?.shown === count', arg=shown)
                        page.reload()
                        page.wait_for_function('(count) => document.querySelectorAll(".book-result").length === count', arg=shown)
                        if len(search_requests) != before_requests:
                            report(status='cached_view_repeated_api_request')
                            return 1
                        report(phase='cached_results', status='verified', total=total, restored_visible=shown,
                               extra_api_requests=0, whole_book_verified=False)
                        return 0
                    candidate = next((item for item in value['candidates'] if item['supported']), None)
                    if candidate is None:
                        report(phase='source_discovery', status='api_no_supported_candidate')
                        return 2
                elif args.source_url:
                    result = command(page, 'SEARCH', query=args.source_url)
                    if not result.get('ok'):
                        report(phase='source_discovery', status='failed', code=result.get('code'))
                        return 1
                    candidate = next((item for item in result['value']['candidates'] if item['supported']), None)
                if candidate is None:
                    if not args.source_only:
                        report(phase='source_discovery', status='api_no_supported_candidate', next_check='independent_direct_link_entry')
                    home = context.new_page()
                    try:
                        home.goto('https://www.23txxt.com/', wait_until='domcontentloaded', timeout=25000)
                    except Exception as error:
                        network = re.search(r'net::(ERR_[A-Z_]+)', str(error))
                        code = network.group(1) if network else 'navigation_failed'
                        report(phase='source_home', status='navigation_interrupted', network_code=code)
                        home.screenshot(path=str(ROOT / '.local/renewal/source-navigation.png'))
                        if code not in ('ERR_ABORTED', 'ERR_TIMED_OUT'):
                            return 2
                        home.wait_for_timeout(1000)
                    deadline = time.monotonic() + 240
                    notified = False
                    while time.monotonic() < deadline:
                        links = home.evaluate('''() => [...document.querySelectorAll('a[href]')].map(a => a.href)
                            .filter(value => { try { const url = new URL(value); return ['23txxt.com','www.23txxt.com'].includes(url.hostname)
                                && /^\\/bqg\\/\\d+\\/$/.test(url.pathname); } catch { return false; } })''')
                        if links:
                            candidate = {'id': links[0], 'url': links[0], 'title': '待读取公开书目', 'supported': True}
                            report(phase='source_discovery', status='direct_public_link_found', search_to_book_verified=False)
                            home.close()
                            break
                        needs_confirmation = home.evaluate('''() => Boolean(document.querySelector('#cf-challenge-running, #challenge-form, .cf-turnstile'))
                            || /Just a moment|验证您是真人|人机验证|安全验证/i.test(document.title)''')
                        if not needs_confirmation:
                            report(phase='source_home', status='catalog_links_not_recognized')
                            return 2
                        if not notified:
                            home.screenshot(path=str(ROOT / '.local/renewal/source-confirmation.png'))
                            report(phase='source_home', status='needs_action')
                            home.bring_to_front()
                            notified = True
                        home.wait_for_timeout(1000)
                    if candidate is None:
                        report(phase='source_home', status='confirmation_timeout')
                        return 2
                started = command(page, 'DOWNLOAD', candidate=candidate)
                if not started.get('ok'):
                    report(phase='source', status='failed', code=started.get('code'))
                    return 1
                book_id = started['value']['id']
                deadline = time.monotonic() + 180
                prior = None
                while time.monotonic() < deadline:
                    value = command(page, 'STATE')
                    if not value.get('ok'):
                        report(phase='source', status='state_unavailable')
                        return 1
                    book = next(item for item in value['value']['books'] if item['id'] == book_id)
                    marker = (book['status'], book['reason'], book['chapterIndex'], (book.get('artifact') or {}).get('state'))
                    changed = marker != prior
                    if changed:
                        report(phase='source', status=book['status'], reason=book['reason'],
                               saved_chapters=book['chapterIndex'], scope_chapters=book['rangeTotal'],
                               catalog_items=book['catalogCount'], catalog_pages=len(book.get('catalogPages', [])),
                               stage=book['stage'],
                               navigation_error=source_navigation_errors[-1] if source_navigation_errors else None,
                               target_page=re.search(r'(?:index_\d+|\d+(?:_\d+)?)\.html$', (book.get('lastIssue') or {}).get('targetUrl') or '')[0]
                                   if re.search(r'(?:index_\d+|\d+(?:_\d+)?)\.html$', (book.get('lastIssue') or {}).get('targetUrl') or '') else None,
                               file_state=marker[-1])
                        prior = marker
                    if book['status'] == 'needs_action' or (book['status'] == 'partial' and book['reason'] == 'page_unreadable'):
                        return 2
                    elif book['status'] in ('partial', 'paused'):
                        return 2
                    if (book.get('artifact') or {}).get('state') == 'complete':
                        result = page.evaluate('async (id) => chrome.downloads.search({id})', book['artifact']['downloadId'])
                        file = Path(result[0]['filename']) if len(result) == 1 else None
                        if not file or not file.is_file():
                            report(phase='file', status='file_not_found')
                            return 1
                        saved = file.read_text(encoding='utf-8-sig')
                        records = page.evaluate('async id => (await import("./db.js")).listChapters(id)', book_id)
                        full_book = page.evaluate('async id => (await import("./db.js")).getBook(id)', book_id)
                        valid = len(records) == book['rangeTotal'] == book['chapterIndex']
                        valid = valid and len({row['checksum'] for row in records}) == len(records)
                        valid = valid and all(row['url'] == full_book['catalog'][index]['url']
                            and hashlib.sha256(row['text'].encode()).hexdigest() == row['checksum']
                            and row['text'] in saved for index, row in enumerate(records))
                        if not valid:
                            report(phase='file', status='content_or_chapter_identity_failed')
                            return 1
                        report(phase='file', status='verified', saved_chapters=book['chapterIndex'],
                               bytes=file.stat().st_size, catalog_pages=len(full_book.get('catalogPages', [])),
                               chapter_pages=[len(row['pages']) for row in records],
                               chapter_identity_and_checksum_verified=True, whole_book_verified=False)
                        return 0
                    page.wait_for_timeout(500)
                report(phase='source', status='confirmation_or_progress_timeout', whole_book_verified=False)
                return 2
            finally:
                context.close()
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    except Exception as error:
        # Exceptions may carry browser protocol arguments. Never print their
        # message/traceback while a real credential is in this process.
        locations = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                     if Path(frame.filename).resolve() == Path(__file__).resolve()]
        network = re.search(r'net::(ERR_[A-Z_]+)', str(error))
        report(status='browser_check_failed', error_category=type(error).__name__, source_lines=locations,
               network_code=network.group(1) if network else None, key_exposed=False)
        return 1


if __name__ == '__main__':
    sys.exit(main())
