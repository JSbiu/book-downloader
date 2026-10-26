import { searchBooks } from './search.js';
import { canonicalBookUrl, canonicalChapterUrl, registeredSourceForUrl, sourceForUrl, samePage,
  sourceKeyForUrl, cleanBookTitle } from './source-rules.js';
import { changeBook, getBook, listBooks, listChapters, chaptersForBook, getArtifact, listArtifacts, putArtifact } from './db.js';
import { cleanChapter } from './content-rules.js';

const SAMPLE_CHAPTERS = 3;
const PAGE_TIMEOUT = 25000;
const PAGE_DELAY = 1000;
const ALARM = 'shuye-download-recovery';
let running = false, rerun = false, timer, searching, exporting = false;

const TEXT = {
  shelved: '已加入书架；下载与更新目录可分别发起。',
  source_selected: '已选择新的书源；点击下载取得此来源正文。',
  source_blocked: '本书已拉黑此书源，已有内容保留供核对；请查看其他书源。',
  source_restored: '已恢复此书源；点击下载或更新目录后才会访问来源。',
  catalog_updated: '目录已更新；没有自动下载正文。',
  book_busy: '请先暂停当前下载，再更新目录或选择书源。',
  book_missing: '书架中未找到这本书。',
  queued: '等待前一本书完成，搜索仍可继续使用。',
  reading: '正在读取来源页面，完整章节保存后更新进度。',
  key_missing: '请先在设置中保存 DeepSeek API Key。',
  invalid_key: 'DeepSeek API Key 无效，请在设置中重新输入。',
  authentication_failed: 'DeepSeek API Key 无效，请在设置中重新输入。',
  insufficient_balance: 'DeepSeek API 余额不足，已有下载进度保留。',
  rate_limited: '搜索服务暂时限流，请稍后重试。',
  timeout: '搜索服务响应超时，请重试。',
  gateway_timeout: '搜索服务响应超时，请重试。',
  network_error: '未能连接搜索服务，请重试。',
  invalid_response: '搜索服务返回的数据无法识别。',
  query_too_long: '查询过长，请缩短书名或作者。',
  query_empty: '请输入书名或链接。',
  aborted: '搜索已停止，已有结果保留。',
  unsupported_source: '当前工程样机尚未支持此站点的下载。',
  needs_action: '来源需要本人确认，处理后书页会继续当前书籍。',
  access_denied: '来源拒绝访问，已保存的内容保留。',
  page_timeout: '来源页面加载超时，继续时从当前章重试。',
  page_unreadable: '来源页面暂时不可读，请查看来源页后继续。',
  structure_mismatch: '来源页面结构未能识别，已保存内容保留。',
  content_incomplete: '当前章的正文或续页未收齐，未将它计为完成。',
  storage_failed: '本地保存失败，正文和进度未前移。',
  source_closed: '来源标签页已关闭，继续时会重新打开。',
  source_changed: '来源标签页已被改为其他页面，当前书籍已暂停。',
  source_frozen: '来源页被冻结，请恢复来源页后继续。',
  work_mismatch: '所选来源与当前书籍的书名或作者不一致，未切换。',
  browser_restarted: '浏览器已重启，点击继续恢复保存的断点。',
  user_paused: '已暂停；已提交的章节保留。',
  sample_complete: '样机范围已取得，正在交付 TXT。',
  file_saved: '样机范围 TXT 已保存；整本验收尚未完成。',
  export_failed: 'TXT 未能交付，已保存的正文仍可重新导出。',
  delivery_unknown: '文件交付待确认；请先检查浏览器下载记录。',
  download_interrupted: '文件下载中断，可重新导出已保存正文。'
};
const detail = code => TEXT[code] || '操作未完成，已保存的内容保留。';
const wakeSoon = delay => {
  clearTimeout(timer);
  timer = setTimeout(() => void pump(), delay);
};

async function browserSession() {
  let { engineSession } = await chrome.storage.session.get('engineSession');
  if (!engineSession) {
    engineSession = crypto.randomUUID();
    await chrome.storage.session.set({ engineSession });
  }
  return engineSession;
}

async function alarmForWork() {
  const work = (await listBooks()).some(book => ['queued', 'downloading', 'needs_action'].includes(book.status));
  const file = (await listArtifacts()).some(item => ['starting', 'in_progress'].includes(item.state));
  if (work || file) {
    if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
  } else await chrome.alarms.clear(ALARM);
}

async function recover() {
  await chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' });
  const session = await browserSession();
  for (const book of await listBooks()) {
    if (book.session && book.session !== session && ['queued', 'downloading', 'needs_action'].includes(book.status)) {
      await changeBook(book.id, current => ({ ...current, status: 'paused', reason: 'browser_restarted',
        expectedUrl: null, tabId: null, attempt: current.attempt + 1, pendingPages: [], continuationUrl: null }));
    }
  }
  await reconcileExports();
  for (const book of await listBooks()) {
    if (book.status === 'ready') {
      try { await exportBook(book.id, false); } catch { /* The persisted artifact describes the failure. */ }
    }
  }
  await alarmForWork();
}

const ready = recover();

async function snapshot() {
  await ready;
  const settings = await chrome.storage.local.get('deepseekApiKey');
  const { lastSearch } = await chrome.storage.session.get('lastSearch');
  const artifacts = await listArtifacts();
  const books = await Promise.all((await listBooks()).map(async book => {
    const chapters = chaptersForBook(book, await listChapters(book.id));
    return { ...book, title: cleanBookTitle(book.title), pendingPages: undefined, catalog: undefined,
      catalogDraft: undefined, sourceHistory: undefined, catalogCount: book.catalog.length || (book.catalogDraft || []).length,
      savedCount: chapters.length, rangeSavedCount: chapters.filter(chapter => chapter.index < book.rangeTotal).length,
      supported: Boolean(sourceForUrl(book.catalogUrl)), sourceBlocked: isSourceBlocked(book, book.catalogUrl),
      message: detail(book.reason), artifact: artifacts.filter(item => item.bookId === book.id
        && item.snapshotId === book.snapshotId && (!item.canonicalUrl || item.canonicalUrl === book.canonicalUrl))
        .sort((a, b) => b.createdAt - a.createdAt)[0] || null };
  }));
  return { configured: Boolean(settings.deepseekApiKey), search: lastSearch || null, books, sampleChapters: SAMPLE_CHAPTERS };
}

const busy = book => ['queued', 'downloading', 'needs_action'].includes(book.status);
const titleKey = value => cleanBookTitle(value).replace(/\s+/g, '').toLocaleLowerCase();
const authorKey = value => String(value || '').normalize('NFKC').replace(/\s+/g, '').toLocaleLowerCase();
const PLACEHOLDER_TITLE = '待读取书籍';
const errorCode = value => {
  const code = value?.code || value?.message;
  return typeof code === 'string' && Object.hasOwn(TEXT, code) ? code : 'operation_failed';
};
const sourceFields = ['sourceId', 'canonicalUrl', 'catalogUrl', 'catalog', 'snapshotId', 'chapterIndex', 'rangeTotal'];
const sourceSnapshot = book => Object.fromEntries(sourceFields.map(key => [key, book[key]]));
const identityEvidence = (book, candidate) => ({
  bookTitle: cleanBookTitle(book?.title),
  candidateTitle: cleanBookTitle(candidate?.title),
  bookAuthor: String(book?.author || ''),
  candidateAuthor: String(candidate?.author || '')
});
const identityRelation = (book, candidate) => {
  if (candidate?.identityConflict) return 'conflict';
  const bookTitle = titleKey(book?.title);
  const candidateTitle = titleKey(candidate?.title);
  if (!bookTitle || bookTitle === titleKey(PLACEHOLDER_TITLE) || !candidateTitle) return 'unknown';
  if (bookTitle !== candidateTitle) return 'different';
  const bookAuthor = authorKey(book?.author);
  const candidateAuthor = authorKey(candidate?.author);
  if (!bookAuthor || !candidateAuthor) return 'unknown';
  return bookAuthor === candidateAuthor ? 'same' : 'different';
};
const trustedCandidate = (value, candidates) => {
  const canonical = canonicalBookUrl(value?.url);
  if (!canonical) return null;
  return (candidates || []).find(candidate => canonicalBookUrl(candidate.url) === canonical) || null;
};
const searchCandidate = async value => {
  const { lastSearch } = await chrome.storage.session.get('lastSearch');
  return trustedCandidate(value, lastSearch?.candidates);
};
const isSourceBlocked = (book, url) => (book.blockedSources || []).some(item => item.key === sourceKeyForUrl(url));
const sameWork = (book, candidate) => canonicalBookUrl(candidate.url) === book.canonicalUrl
  || identityRelation(book, candidate) === 'same';
const candidateBlocked = (books, candidate) => books.some(book => sameWork(book, candidate) && isSourceBlocked(book, candidate.url));

async function addBook(candidate) {
  candidate = await searchCandidate(candidate) || candidate;
  let url;
  try { url = new URL(candidate?.url); } catch { throw new Error('unsupported_source'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('unsupported_source');
  const source = sourceForUrl(candidate?.url);
  const canonical = canonicalBookUrl(url.href) || candidate.url;
  const books = await listBooks();
  const existing = books.find(book => book.canonicalUrl === canonical);
  if (existing) return existing.id;
  const sameIdentity = source && books.find(book => identityRelation(book, candidate) === 'same');
  if (sameIdentity) {
    await changeSource(sameIdentity.id, candidate);
    return sameIdentity.id;
  }
  const now = Date.now(), id = crypto.randomUUID();
  await changeBook(id, () => ({ id, sourceId: source?.id || null, canonicalUrl: canonical, catalogUrl: source?.catalogUrl || url.href,
    title: cleanBookTitle(candidate.title || '待读取书籍'), author: candidate.author || '', createdAt: now, updatedAt: now,
    status: 'shelved', stage: 'catalog', catalog: [], snapshotId: null, chapterIndex: 0, blockedSources: [],
    rangeTotal: 0, attempt: 0, pendingPages: [], expectedUrl: null, tabId: null,
    session: null, reason: 'shelved', nextAt: 0 }));
  return id;
}

async function changeSource(id, candidate) {
  candidate = await searchCandidate(candidate);
  if (!candidate) throw new Error('work_mismatch');
  const found = await getBook(id);
  if (!found) throw new Error('book_missing');
  if (busy(found)) throw new Error('book_busy');
  if (isSourceBlocked(found, candidate?.url)) throw new Error('source_blocked');
  if (!sourceForUrl(candidate?.url)) throw new Error('unsupported_source');
  const source = sourceForUrl(candidate?.url);
  let url;
  try { url = new URL(candidate?.url); } catch { throw new Error('unsupported_source'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('unsupported_source');
  const canonical = canonicalBookUrl(url.href) || url.href;
  const relation = found.canonicalUrl === canonical ? 'same' : identityRelation(found, candidate);
  if (relation !== 'same') {
    const error = new Error('work_mismatch');
    error.detail = identityEvidence(found, candidate);
    throw error;
  }
  const changed = await changeBook(id, book => {
    if (busy(book) || isSourceBlocked(book, candidate.url)) return null;
    if (book.canonicalUrl === canonical) return book;
    const previous = sourceSnapshot(book);
    const history = book.sourceHistory || [];
    const restored = history.find(item => item.canonicalUrl === canonical);
    const restoredSource = restored
      ? Object.fromEntries(sourceFields.map(key => [key, restored[key]])) : null;
    const nextSource = restoredSource || { sourceId: source?.id || null, canonicalUrl: canonical,
      catalogUrl: source?.catalogUrl || url.href, catalog: [], snapshotId: null, chapterIndex: 0, rangeTotal: 0 };
    const sourceHistory = [...history.filter(item => ![book.canonicalUrl, canonical].includes(item.canonicalUrl)), previous];
    return { ...book, ...nextSource,
      title: book.title === PLACEHOLDER_TITLE ? cleanBookTitle(candidate.title || book.title) : book.title,
      author: book.author || candidate.author || '',
      sourceHistory,
      status: 'shelved', reason: 'source_selected', stage: restoredSource?.catalog.length ? 'chapter' : 'catalog',
      tabId: null, expectedUrl: null, pendingPages: [], continuationUrl: null, catalogDraft: [], catalogPages: [],
      catalogNextUrl: null, catalogOnly: false, newChapters: null, attempt: book.attempt + 1 };
  });
  if (!changed) throw new Error('book_busy');
  return relation;
}

async function blockSource(id, restoreKey = null) {
  searching?.abort();
  searching = null;
  await changeBook(id, book => {
    if (!book) return null;
    const key = sourceKeyForUrl(book.catalogUrl);
    const blockedSources = restoreKey ? (book.blockedSources || []).filter(item => item.key !== restoreKey)
      : [...(book.blockedSources || []).filter(item => item.key !== key),
        { key, label: registeredSourceForUrl(book.catalogUrl)?.name || new URL(book.catalogUrl).hostname, url: book.catalogUrl }];
    const currentBlocked = blockedSources.some(item => item.key === key);
    const affected = !restoreKey || restoreKey === key;
    return { ...book, blockedSources, ...(affected ? { status: currentBlocked ? 'blocked' : 'shelved',
      reason: currentBlocked ? 'source_blocked' : 'source_restored', expectedUrl: null, pendingPages: [],
      continuationUrl: null, attempt: book.attempt + 1 } : {}) };
  });
  const { lastSearch } = await chrome.storage.session.get('lastSearch');
  if (lastSearch) {
    const books = await listBooks();
    const candidates = lastSearch.candidates.filter(candidate => !candidateBlocked(books, candidate));
    await chrome.storage.session.set({ lastSearch: { ...lastSearch, candidates,
      preferredId: candidates.some(item => item.id === lastSearch.preferredId) ? lastSearch.preferredId : null } });
  }
  void pump();
}

async function updateCatalog(id) {
  const book = await getBook(id);
  if (!book) throw new Error('book_missing');
  if (isSourceBlocked(book, book.catalogUrl)) throw new Error('source_blocked');
  if (!sourceForUrl(book.catalogUrl)) throw new Error('unsupported_source');
  if (busy(book)) throw new Error('book_busy');
  const changed = await changeBook(id, current => !busy(current) && !isSourceBlocked(current, current.catalogUrl)
    ? { ...current, status: 'queued', reason: 'queued',
    stage: 'catalog', catalogOnly: true, catalogDraft: [], catalogPages: [], catalogNextUrl: null,
    expectedUrl: null, pendingPages: [], continuationUrl: null, attempt: current.attempt + 1, nextAt: 0 } : null);
  if (!changed) throw new Error('book_busy');
  void pump();
}

async function pauseBook(id, reason = 'user_paused') {
  await changeBook(id, book => book && ({ ...book, status: 'paused', reason,
    ...(reason === 'user_paused' ? {} : { lastIssue: { reason, stage: book.stage,
      targetUrl: book.expectedUrl, observedUrl: null, time: Date.now() } }),
    expectedUrl: null, pendingPages: [], continuationUrl: null, attempt: book.attempt + 1 }));
  void pump();
}

async function resumeBook(id) {
  const found = await getBook(id);
  if (!found) throw new Error('book_missing');
  if (isSourceBlocked(found, found.catalogUrl)) throw new Error('source_blocked');
  if (!sourceForUrl(found.catalogUrl)) throw new Error('unsupported_source');
  if (busy(found)) return;
  const changed = await changeBook(id, book => book && !busy(book) && !isSourceBlocked(book, book.catalogUrl)
    ? { ...book, status: 'queued', reason: 'queued', expectedUrl: null, pendingPages: [],
      continuationUrl: null, attempt: book.attempt + 1, nextAt: 0 } : null);
  if (!changed) return;
  void pump();
}

async function failBook(id, attempt, reason, observedUrl = null) {
  await changeBook(id, book => {
    if (!book || book.attempt !== attempt || !['downloading', 'needs_action'].includes(book.status)) return null;
    const status = reason === 'needs_action' ? 'needs_action'
      : ['source_closed', 'source_changed', 'source_frozen'].includes(reason) ? 'paused' : 'partial';
    return { ...book, status, reason,
      lastIssue: { reason, stage: book.stage, targetUrl: book.expectedUrl, observedUrl, time: Date.now() },
      ...(reason === 'needs_action' ? {} : { expectedUrl: null, pendingPages: [], continuationUrl: null, attempt: book.attempt + 1 }) };
  });
}

function matches(book, actual) {
  if (book.stage === 'catalog') {
    const source = sourceForUrl(actual);
    if (source?.id === 'wxdy') {
      return canonicalBookUrl(actual) === book.canonicalUrl
        && /^\/books\/\d+\/(?:index_\d+\.html)?$/i.test(new URL(actual).pathname)
        && new URL(book.expectedUrl || book.catalogUrl).pathname === new URL(actual).pathname;
    }
    return source && canonicalBookUrl(actual) === book.canonicalUrl
      && new URL(actual).pathname.match(/^\/bqg\/\d+(?:\/|\.html?)?$/i);
  }
  return samePage(book.expectedUrl, actual);
}

async function navigate(book, url) {
  let tab;
  if (book.tabId) {
    try {
      const previous = await chrome.tabs.get(book.tabId);
      if (sourceForUrl(previous.url) && canonicalBookUrl(previous.url) === book.canonicalUrl) tab = previous;
    } catch { /* A closed task page is replaced only after the user resumes. */ }
  }
  if (!tab) tab = await chrome.tabs.create({ url: 'about:blank', active: false });
  const changed = await changeBook(book.id, current => {
    if (!current || current.attempt !== book.attempt || current.status !== 'downloading') return null;
    return { ...current, tabId: tab.id, expectedUrl: url, attempt: current.attempt + 1,
      previousUrl: tab.url, navigationAt: Date.now(), reason: 'reading' };
  });
  if (changed) {
    try { await chrome.tabs.update(tab.id, { url }); }
    catch { await failBook(book.id, changed.attempt, 'page_unreadable'); }
  }
}

async function readPage(book, tab) {
  if (tab.frozen) { await failBook(book.id, book.attempt, 'source_frozen'); return; }
  if (tab.discarded) {
    await chrome.tabs.reload(tab.id);
    await changeBook(book.id, current => current.attempt === book.attempt
      ? { ...current, navigationAt: Date.now() } : null);
    return;
  }
  if (tab.status === 'loading' && Date.now() - book.navigationAt < PAGE_TIMEOUT) return;
  if (!matches(book, tab.url)) {
    if (tab.pendingUrl && matches(book, tab.pendingUrl)) return;
    if (tab.url === book.previousUrl && Date.now() - book.navigationAt < PAGE_TIMEOUT) return;
    if (tab.url === 'about:blank' && Date.now() - book.navigationAt < PAGE_TIMEOUT) return;
    await failBook(book.id, book.attempt, tab.url?.startsWith('chrome') ? 'page_unreadable' : 'source_changed', tab.url);
    return;
  }
  if (tab.status !== 'complete') {
    if (Date.now() - book.navigationAt > PAGE_TIMEOUT) await failBook(book.id, book.attempt, 'page_timeout');
    return;
  }
  let result;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id },
      files: [book.sourceId === 'wxdy' ? 'read-wxdy.js' : 'read-source.js'] });
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id },
      func: stage => globalThis.shuyeReadSource(stage), args: [book.stage] });
    const value = results.find(item => item.frameId === 0);
    if (!value?.documentId) throw new Error();
    result = value.result;
    if (!result || !matches(book, result.url)) throw new Error();
    // Re-check the live document after extraction, before any database write.
    const currentTab = await chrome.tabs.get(tab.id);
    if (!matches(book, currentTab.url)) throw new Error();
    const stillLive = await chrome.scripting.executeScript({ target: { tabId: tab.id, documentIds: [value.documentId] },
      func: () => location.href });
    if (stillLive.length !== 1 || !matches(book, stillLive[0].result)) throw new Error();
  } catch { await failBook(book.id, book.attempt, 'page_unreadable'); return; }
  if (result.kind === 'needs_action') { await failBook(book.id, book.attempt, 'needs_action'); return; }
  if (!['catalog', 'chapter'].includes(result.kind)) {
    await failBook(book.id, book.attempt, result.kind); return;
  }
  if (book.stage === 'catalog' && result.kind === 'catalog') {
    // The directory URL only proves which source is being read; it does not
    // prove that its metadata still names the selected work.
    const relation = identityRelation(book, result);
    if (['different', 'conflict'].includes(relation)) {
      await failBook(book.id, book.attempt, 'work_mismatch');
      return;
    }
    const catalog = [...new Map([...(book.catalogDraft || []), ...result.chapters]
      .map(item => [canonicalChapterUrl(item.url) || item.url, item])).values()];
    const catalogPages = [...(book.catalogPages || []), result.url];
    if (result.nextCatalog && (book.sourceId !== 'wxdy'
        || canonicalBookUrl(result.nextCatalog) !== book.canonicalUrl
        || catalogPages.some(url => samePage(url, result.nextCatalog)) || catalogPages.length >= 50)) {
      await failBook(book.id, book.attempt, 'content_incomplete');
      return;
    }
    const saved = chaptersForBook({ ...book, catalog }, await listChapters(book.id));
    const rangeTotal = Math.min(SAMPLE_CHAPTERS, catalog.length);
    const savedIndexes = new Set(saved.map(chapter => chapter.index));
    let chapterIndex = 0;
    while (chapterIndex < rangeTotal && savedIndexes.has(chapterIndex)) chapterIndex += 1;
    const previousUrls = new Set(book.catalog.map(chapter => canonicalChapterUrl(chapter.url) || chapter.url));
    await changeBook(book.id, current => {
      if (current.attempt !== book.attempt || !['downloading', 'needs_action'].includes(current.status)) return null;
      const sameTitle = titleKey(current.title) === titleKey(result.title);
      return { ...current,
        title: current.title === PLACEHOLDER_TITLE || sameTitle ? cleanBookTitle(result.title) : current.title,
        author: current.author || result.author || '', catalogPages,
        catalogDraft: result.nextCatalog ? catalog : [],
        catalogNextUrl: result.nextCatalog || null,
        ...(result.nextCatalog ? {} : { catalog, snapshotId: crypto.randomUUID(), rangeTotal, chapterIndex,
          newChapters: catalog.filter(chapter => !previousUrls.has(canonicalChapterUrl(chapter.url) || chapter.url)).length,
          catalogUpdatedAt: Date.now() }),
        status: !result.nextCatalog && current.catalogOnly ? 'shelved' : 'downloading',
        stage: result.nextCatalog ? 'catalog' : 'chapter',
        expectedUrl: null, reason: !result.nextCatalog && current.catalogOnly ? 'catalog_updated' : 'reading',
        catalogOnly: result.nextCatalog ? current.catalogOnly : false, nextAt: Date.now() + PAGE_DELAY };
    });
    return;
  }
  if (book.stage !== 'chapter' || result.kind !== 'chapter') {
    await failBook(book.id, book.attempt, 'structure_mismatch'); return;
  }
  const chapter = book.catalog[book.chapterIndex];
  const pages = [...book.pendingPages, { url: result.url, title: result.title, text: result.text }];
  if (pages.slice(0, -1).some(page => samePage(page.url, result.url)) || pages.length > 40) {
    await failBook(book.id, book.attempt, 'content_incomplete'); return;
  }
  if (result.next) {
    if (canonicalChapterUrl(result.next) !== canonicalChapterUrl(chapter.url)
        || pages.some(page => samePage(page.url, result.next))) {
      await failBook(book.id, book.attempt, 'content_incomplete'); return;
    }
    const changed = await changeBook(book.id, current => {
      if (current.attempt !== book.attempt || !['downloading', 'needs_action'].includes(current.status)) return null;
      return { ...current, status: 'downloading', pendingPages: pages, expectedUrl: null,
        continuationUrl: result.next, nextAt: Date.now() + PAGE_DELAY };
    });
    if (changed) wakeSoon(PAGE_DELAY);
    return;
  }
  const cleaned = cleanChapter(book.sourceId, { title: pages[0].title || chapter.title,
    text: pages.map(page => page.text).join('\n') });
  const text = cleaned.text;
  if (text.length < 40) { await failBook(book.id, book.attempt, 'content_incomplete'); return; }
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const checksum = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  await changeBook(book.id, current => {
    if (current.attempt !== book.attempt || !['downloading', 'needs_action'].includes(current.status)
        || current.chapterIndex !== book.chapterIndex) return null;
    return { ...current, status: 'downloading', chapterIndex: current.chapterIndex + 1,
      pendingPages: [], continuationUrl: null, expectedUrl: null, nextAt: Date.now() + PAGE_DELAY };
  }, { bookId: book.id, url: chapter.url, snapshotId: book.snapshotId, index: book.chapterIndex,
    title: cleaned.title, text, pages: pages.map(page => page.url), checksum });
}

async function pump() {
  await ready;
  if (running) { rerun = true; return; }
  running = true;
  try {
    let books = await listBooks();
    let book = books.find(item => ['downloading', 'needs_action'].includes(item.status))
      || books.find(item => item.status === 'queued');
    if (!book) return;
    if (book.status === 'queued') {
      const session = await browserSession();
      book = await changeBook(book.id, current => current.status === 'queued'
        ? { ...current, session, status: 'downloading', reason: 'reading' } : null);
      if (!book) return;
    }
    if (book.stage === 'chapter' && !book.continuationUrl) {
      const saved = new Set(chaptersForBook(book, await listChapters(book.id)).map(chapter => chapter.index));
      let index = book.chapterIndex;
      while (index < book.rangeTotal && saved.has(index)) index += 1;
      if (index !== book.chapterIndex) {
        book = await changeBook(book.id, current => current.attempt === book.attempt && current.status === 'downloading'
          ? { ...current, chapterIndex: index } : null);
        if (!book) return;
      }
    }
    if (book.stage === 'chapter' && book.chapterIndex >= book.rangeTotal) {
      const completed = await changeBook(book.id, current => current.attempt === book.attempt && current.status === 'downloading'
        ? { ...current, status: 'ready', reason: 'sample_complete', expectedUrl: null } : null);
      if (completed) await exportBook(book.id, false);
      wakeSoon(100);
      return;
    }
    if (!book.expectedUrl) {
      if (Date.now() < book.nextAt) { wakeSoon(book.nextAt - Date.now()); return; }
      const url = book.stage === 'catalog' ? book.catalogNextUrl || book.catalogUrl : book.continuationUrl || book.catalog[book.chapterIndex]?.url;
      if (!url) { await failBook(book.id, book.attempt, 'structure_mismatch'); return; }
      await navigate(book, url);
    } else {
      let tab;
      try { tab = await chrome.tabs.get(book.tabId); }
      catch { await failBook(book.id, book.attempt, 'source_closed'); return; }
      await readPage(book, tab);
      const current = await getBook(book.id);
      if (current?.status === 'downloading' && !current.expectedUrl) wakeSoon(Math.max(0, current.nextAt - Date.now()));
    }
  } catch {
    const book = (await listBooks()).find(item => item.status === 'downloading');
    if (book) await failBook(book.id, book.attempt, 'storage_failed');
  } finally {
    running = false;
    await alarmForWork();
    const books = await listBooks();
    if (rerun) { rerun = false; wakeSoon(0); }
    else if (!books.some(book => ['downloading', 'needs_action'].includes(book.status))
        && books.some(book => book.status === 'queued')) wakeSoon(0);
  }
}

async function offscreen() {
  const url = chrome.runtime.getURL('offscreen.html');
  const contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [url] });
  if (!contexts.length) await chrome.offscreen.createDocument({ url: 'offscreen.html', reasons: ['BLOBS'],
    justification: '将本地已提交正文生成为 TXT，并交付给浏览器下载。' });
}

async function closeExport(artifact) {
  try { await chrome.runtime.sendMessage({ target: 'offscreen', type: 'RELEASE', artifactId: artifact.id }); } catch { /* Already gone. */ }
  if (!(await listArtifacts()).some(item => ['preparing', 'starting', 'in_progress', 'needs_confirmation'].includes(item.state))) {
    try { await chrome.offscreen.closeDocument(); } catch { /* Already closed. */ }
  }
}

async function reconcileExports() {
  for (const artifact of await listArtifacts()) {
    if (!['starting', 'in_progress', 'needs_confirmation'].includes(artifact.state)) continue;
    if (artifact.state === 'starting' && exporting && artifact.downloadId == null) continue;
    let downloads = [];
    if (artifact.downloadId != null) downloads = await chrome.downloads.search({ id: artifact.downloadId });
    else if (artifact.blobUrl) downloads = await chrome.downloads.search({ url: artifact.blobUrl });
    if (downloads.length === 1) {
      const item = downloads[0];
      artifact.downloadId = item.id;
      artifact.state = item.state === 'complete' ? 'complete' : item.state === 'interrupted' ? 'interrupted' : 'in_progress';
      await putArtifact(artifact);
      if (artifact.state === 'complete') {
        await changeBook(artifact.bookId, book => book?.status === 'ready' && book.snapshotId === artifact.snapshotId
          ? { ...book, reason: 'file_saved' } : null);
      }
      if (artifact.state !== 'in_progress') await closeExport(artifact);
    } else {
      artifact.state = 'needs_confirmation';
      await putArtifact(artifact);
    }
  }
}

async function exportBook(id, explicit) {
  if (exporting) throw new Error('export_failed');
  exporting = true;
  let artifact;
  try {
    const book = await getBook(id);
    if (!explicit && book && isSourceBlocked(book, book.catalogUrl)) return;
    const chapters = book && chaptersForBook(book, await listChapters(id)).filter(chapter => chapter.index < book.rangeTotal);
    if (!book || !chapters.length) throw new Error('export_failed');
    const prior = (await listArtifacts()).find(item => item.bookId === id && item.snapshotId === book.snapshotId
      && item.chapterCount === chapters.length);
    if (prior && !explicit && prior.state !== 'preparing') return;
    if (explicit) {
      for (const pending of (await listArtifacts()).filter(item => item.bookId === id && item.state === 'needs_confirmation')) {
        await putArtifact({ ...pending, state: 'interrupted' });
        await closeExport(pending);
      }
    }
    await offscreen();
    artifact = prior?.state === 'preparing' && !explicit ? prior : { id: crypto.randomUUID(), bookId: id, chapterCount: chapters.length,
      chapterUrls: chapters.map(chapter => chapter.storedUrl || chapter.url), canonicalUrl: book.canonicalUrl,
      title: cleanBookTitle(book.title), author: book.author, catalogUrl: book.catalogUrl, catalogCount: book.catalog.length,
      sourceId: book.sourceId, sourceBlocked: isSourceBlocked(book, book.catalogUrl),
      snapshotId: book.snapshotId, state: 'preparing', createdAt: Date.now() };
    await putArtifact(artifact);
    const built = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'BUILD', artifactId: artifact.id });
    if (!built?.ok) throw new Error('export_failed');
    artifact = { ...artifact, state: 'starting', blobUrl: built.blobUrl, filename: built.filename, bytes: built.bytes };
    await putArtifact(artifact);
    // This is the last await before issuing the download. A block that arrived
    // while the artifact was being saved must still cancel automatic delivery.
    const current = await getBook(id);
    if (!explicit && (!current || current.snapshotId !== book.snapshotId
        || current.canonicalUrl !== book.canonicalUrl || isSourceBlocked(current, current.catalogUrl))) {
      await putArtifact({ ...artifact, state: 'interrupted' });
      await closeExport(artifact);
      return;
    }
    const downloadId = await chrome.downloads.download({ url: built.blobUrl, filename: built.filename,
      conflictAction: 'uniquify', saveAs: false });
    await putArtifact({ ...artifact, state: 'in_progress', downloadId });
    await reconcileExports();
  } catch {
    if (artifact?.state === 'preparing') {
      await putArtifact({ ...artifact, state: 'interrupted' });
      await closeExport(artifact);
    }
    await changeBook(id, book => book && ({ ...book, reason: 'export_failed' }));
    throw new Error('export_failed');
  } finally {
    exporting = false;
    await reconcileExports();
    await alarmForWork();
  }
}

async function route(message) {
  await ready;
  if (message.type === 'STATE') return snapshot();
  if (message.type === 'PREVIEW') {
    const book = await getBook(message.id);
    const chapters = book ? chaptersForBook(book, await listChapters(message.id)) : [];
    const chapter = chapters[0] && cleanChapter(book.sourceId, chapters[0]);
    return { text: chapter ? chapter.title + '\n\n' + chapter.text.slice(0, 1800) : '尚无已提交正文。' };
  }
  if (message.type === 'SAVE_KEY') {
    const key = String(message.key || '').trim();
    if (!key || /\s/.test(key)) throw new Error('invalid_key');
    await chrome.storage.local.set({ deepseekApiKey: key });
    return { configured: true };
  }
  if (message.type === 'CLEAR_KEY') {
    await chrome.storage.local.remove('deepseekApiKey');
    return { configured: false };
  }
  if (message.type === 'STOP_SEARCH') {
    searching?.abort();
    searching = null;
    if (message.clear) await chrome.storage.session.remove(['lastSearch', 'searchView']);
    return { stopped: true };
  }
  if (message.type === 'SEARCH') {
    searching?.abort();
    searching = null;
    const books = await listBooks();
    const context = message.bookId && books.find(book => book.id === message.bookId);
    if (message.bookId && !context) throw new Error('book_missing');
    const query = context ? cleanBookTitle(context.title) : String(message.query || '').trim();
    const includeOtherSources = Boolean(context || message.includeOtherSources);
    const isBlocked = candidate => context ? isSourceBlocked(context, candidate.url) : candidateBlocked(books, candidate);
    if (sourceForUrl(query)) {
      const candidate = { id: canonicalBookUrl(query), url: query, title: '待读取书籍',
        author: null, sourceId: sourceForUrl(query).id, supported: true, exact: true, snippet: '将从此链接读取目录和正文。' };
      const blocked = isBlocked(candidate);
      const result = { query, candidates: blocked ? [] : [candidate], preferredId: blocked ? null : candidate.id,
        broadened: false, calls: 0, sessionId: crypto.randomUUID() };
      await chrome.storage.session.set({ lastSearch: result, searchView: null });
      return result;
    }
    if (/^https?:\/\//i.test(query)) {
      const registered = registeredSourceForUrl(query);
      if (!registered) throw new Error('unsupported_source');
      const candidate = { id: query, url: query, title: '待读取书籍', author: null, sourceId: null,
        registeredSourceId: registered.id, registeredSourceName: registered.name,
        supported: false, exact: false, snippet: '已有书源，当前插件的目录与正文读取尚未接入。' };
      const result = { query, candidates: isBlocked(candidate) ? [] : [candidate], preferredId: null,
        broadened: false, calls: 0, sessionId: crypto.randomUUID() };
      await chrome.storage.session.set({ lastSearch: result, searchView: null });
      return result;
    }
    const { lastSearch } = await chrome.storage.session.get('lastSearch');
    const previousResult = includeOtherSources && lastSearch && titleKey(lastSearch.query) === titleKey(query)
      ? lastSearch : null;
    if (previousResult?.broadened) {
      const result = { ...previousResult, candidates: previousResult.candidates.filter(candidate => !isBlocked(candidate)),
        preferredId: null, calls: 0, reused: true, bookId: context?.id || null };
      await chrome.storage.session.set({ lastSearch: result });
      return result;
    }
    const { deepseekApiKey } = await chrome.storage.local.get('deepseekApiKey');
    if (!deepseekApiKey) throw new Error('key_missing');
    const controller = new AbortController();
    searching = controller;
    const result = { ...await searchBooks(query, { apiKey: deepseekApiKey, fetchImpl: fetch, signal: controller.signal,
      includeOtherSources, isBlocked, previousResult }), sessionId: crypto.randomUUID(), bookId: context?.id || null };
    if (includeOtherSources) result.preferredId = null;
    if (searching !== controller || controller.signal.aborted) throw new Error('aborted');
    await chrome.storage.session.set({ lastSearch: result, searchView: null });
    return result;
  }
  if (message.type === 'ADD_BOOK') return { id: await addBook(message.candidate) };
  if (message.type === 'DOWNLOAD') {
    const id = await addBook(message.candidate);
    await resumeBook(id);
    return { id };
  }
  if (message.type === 'SET_SOURCE') { await changeSource(message.id, message.candidate); return { id: message.id }; }
  if (message.type === 'BLOCK_SOURCE') { await blockSource(message.id); return {}; }
  if (message.type === 'UNBLOCK_SOURCE') { await blockSource(message.id, message.key); return {}; }
  if (message.type === 'UPDATE_CATALOG') { await updateCatalog(message.id); return {}; }
  if (message.type === 'PAUSE') { await pauseBook(message.id); return {}; }
  if (message.type === 'RESUME') { await resumeBook(message.id); return {}; }
  if (message.type === 'EXPORT') { await exportBook(message.id, true); return {}; }
  if (message.type === 'FOCUS_SOURCE') {
    const book = await getBook(message.id);
    if (book?.tabId) {
      const tab = await chrome.tabs.update(book.tabId, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
    }
    return {};
  }
  throw new Error('unknown_action');
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.target === 'offscreen' || sender.id !== chrome.runtime.id
      || !sender.url?.startsWith(chrome.runtime.getURL('index.html'))) return false;
  route(message).then(value => respond({ ok: true, value }), error => {
    const code = errorCode(error);
    const response = { ok: false, code, message: detail(code) };
    if (error?.detail) response.detail = error.detail;
    respond(response);
  });
  return true;
});
chrome.action.onClicked.addListener(async () => {
  const url = chrome.runtime.getURL('index.html');
  const pages = await chrome.tabs.query({});
  const existing = pages.find(tab => tab.url?.split('#')[0] === url);
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    await chrome.windows.update(existing.windowId, { focused: true });
  } else await chrome.tabs.create({ url });
});
chrome.runtime.onInstalled.addListener(async () => {
  const { reopenAfterUpdate } = await chrome.storage.local.get('reopenAfterUpdate');
  if (!reopenAfterUpdate) return;
  await chrome.storage.local.remove('reopenAfterUpdate');
  const url = chrome.runtime.getURL('index.html');
  const existing = (await chrome.tabs.query({})).find(tab => tab.url?.split('#')[0] === url);
  if (existing) await chrome.tabs.update(existing.id, { url });
  else await chrome.tabs.create({ url });
});
chrome.tabs.onUpdated.addListener(() => { void pump(); });
chrome.tabs.onRemoved.addListener((tabId, removal) => {
  if (removal.isWindowClosing) return;
  void (async () => {
    await ready;
    for (const book of await listBooks()) {
      if (book.tabId === tabId && ['downloading', 'needs_action'].includes(book.status)) await pauseBook(book.id, 'source_closed');
    }
  })();
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === ALARM) void ready.then(async () => { await reconcileExports(); await pump(); });
});
chrome.downloads.onChanged.addListener(() => { void ready.then(reconcileExports); });
void ready.then(pump);
