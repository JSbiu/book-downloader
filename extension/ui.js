const $ = id => document.getElementById(id);
const labels = { queued: '排队中', downloading: '正在取得正文', needs_action: '需要本人确认',
  shelved: '已在书架', blocked: '本书已拉黑此书源', paused: '已暂停', partial: '部分未取得', ready: '样机范围已取得' };
let state = { configured: false, books: [], search: null }, selected, view = 'search', filter = 'all';
let signature = '', searching = false, searchVersion = 0, previewSignature = '';
let searchShown = 10;
let checkingUpdate = false;

class CallError extends Error {
  constructor(result) {
    super(result?.message || '书页暂时没有响应，请重试。');
    this.code = result?.code;
    this.detail = result?.detail;
  }
}

async function call(type, values = {}) {
  const result = await chrome.runtime.sendMessage({ type, ...values });
  if (!result?.ok) throw new CallError(result);
  return result.value;
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value || '';
  if (className) node.className = className;
  return node;
}

function button(label, action, id, className = 'button button-secondary') {
  const node = text('button', label, className);
  node.type = 'button';
  node.dataset.action = action;
  node.dataset.id = id;
  return node;
}

function toast(message) {
  const node = text('p', message, 'toast');
  $('toast-region').append(node);
  setTimeout(() => node.remove(), 5000);
}

function setView(next) {
  view = next;
  $('view-search').hidden = view !== 'search';
  $('view-library').hidden = view !== 'library';
  document.querySelectorAll('.main-nav [data-view]').forEach(node => {
    if (node.dataset.view === view) node.setAttribute('aria-current', 'page');
    else node.removeAttribute('aria-current');
  });
  location.hash = view;
}

const countText = book => `${book.rangeSavedCount ?? book.chapterIndex}/${book.rangeTotal || '?'} 章 · 来源目录 ${book.catalogCount || 0} 项`
  + (book.reason === 'catalog_updated' ? ` · 新增 ${book.newChapters || 0} 章` : '');
function delivery(book) {
  if (book.sourceBlocked) return '此书源已被本书拉黑，已有正文可能不可用';
  return { complete: 'TXT 已保存（样机范围）', in_progress: 'TXT 正在保存', starting: 'TXT 正在交付',
    preparing: 'TXT 正在生成', interrupted: '文件保存中断', needs_confirmation: '文件交付待确认' }[book.artifact?.state] || '';
}

function actionFor(book) {
  if (book.sourceBlocked || !book.supported) return ['查看其他书源', 'OTHER_SOURCES'];
  if (book.status === 'needs_action') return ['前往来源确认', 'FOCUS_SOURCE'];
  if (['queued', 'downloading'].includes(book.status)) return ['暂停', 'PAUSE'];
  if (book.status === 'ready') return ['重新导出 TXT', 'EXPORT'];
  if (book.status === 'shelved') return book.rangeTotal && book.rangeSavedCount === book.rangeTotal
    ? ['导出已有 TXT', 'EXPORT'] : [book.rangeSavedCount ? '继续下载' : '下载前 3 章', 'RESUME'];
  if (book.catalogOnly) return ['继续更新目录', 'RESUME'];
  return ['继续下载', 'RESUME'];
}

function updateCards(container, books) {
  const wanted = new Set(books.map(book => book.id));
  [...container.children].forEach(node => { if (!wanted.has(node.dataset.book)) node.remove(); });
  for (const book of books) {
    let card = [...container.children].find(node => node.dataset.book === book.id);
    if (!card) {
      const home = container.id === 'home-books';
      card = text('article', '', home ? 'home-book' : 'shelf-book');
      card.dataset.book = book.id;
      const cover = text('div', '', 'book-cover cover-blue');
      cover.append(text('strong', '书'));
      const copy = text('div', '', home ? 'home-book-copy' : 'shelf-copy');
      const top = text('div', '', 'shelf-top');
      const heading = document.createElement('h3');
      const title = button('', 'OPEN', book.id, 'text-button shelf-title');
      title.dataset.part = 'title';
      heading.append(title);
      const badge = text('span', '', 'status-badge');
      badge.dataset.part = 'status';
      top.append(heading, badge);
      const range = text('p', '', 'shelf-description');
      range.dataset.part = 'range';
      const note = text('p', '', 'shelf-status');
      note.dataset.part = 'note';
      const progress = text('div', '', 'progress-track');
      progress.append(document.createElement('span'));
      const actions = text('div', '', 'shelf-actions');
      copy.append(top, range, progress, note, actions);
      card.append(cover, copy);
      container.append(card);
    }
    const values = { title: book.title, range: countText(book), status: labels[book.status], note: delivery(book) || book.message };
    Object.entries(values).forEach(([part, value]) => {
      const node = card.querySelector(`[data-part="${part}"]`);
      if (node.textContent !== value) node.textContent = value;
    });
    card.querySelector('[data-part="status"]').dataset.state = book.status;
    card.querySelector('.progress-track > span').style.width = `${book.rangeTotal ? book.rangeSavedCount / book.rangeTotal * 100 : 0}%`;
    const [label, action] = actionFor(book);
    const actions = card.querySelector('.shelf-actions');
    const buttons = [button(label, action, book.id)];
    if (book.rangeSavedCount && action !== 'EXPORT') buttons.push(button('导出已有 TXT', 'EXPORT', book.id, 'text-button'));
    actions.replaceChildren(...buttons);
  }
}

function renderBooks() {
  const books = [...state.books].sort((left, right) => left.title.localeCompare(right.title, 'zh-CN')
    || (left.author || '').localeCompare(right.author || '', 'zh-CN')
    || left.createdAt - right.createdAt);
  $('library-count').textContent = books.length;
  $('library-nav-count').textContent = books.length;
  const shown = books.filter(book => filter === 'all' || (filter === 'active' && ['queued', 'downloading'].includes(book.status))
    || (filter === 'attention' && ['paused', 'partial', 'needs_action', 'blocked'].includes(book.status)) || (filter === 'ready' && book.status === 'ready'));
  updateCards($('library-list'), shown);
  updateCards($('home-books'), books.slice(-3).reverse());
  updateCards($('activity-list'), books.filter(book => !['ready', 'shelved', 'blocked'].includes(book.status)));
  $('library-empty').hidden = shown.length > 0;
  $('home-content').hidden = Boolean(state.search) || !books.length;
  const active = books.filter(book => ['queued', 'downloading', 'needs_action'].includes(book.status));
  $('activity-count').hidden = !active.length;
  $('activity-count').textContent = active.length;
  const current = active[0];
  $('download-dock').hidden = !current;
  if (current) {
    $('dock-title').textContent = current.title;
    $('dock-status').textContent = labels[current.status];
    $('dock-progress').textContent = countText(current);
    $('dock-fill').style.width = `${current.rangeTotal ? current.chapterIndex / current.rangeTotal * 100 : 0}%`;
    const [label, action] = actionFor(current);
    $('dock-action').textContent = label;
    $('dock-action').dataset.action = action;
    $('dock-action').dataset.id = current.id;
    $('dock-open').dataset.action = 'OPEN';
    $('dock-open').dataset.id = current.id;
  }
}

function renderSearch() {
  const result = state.search;
  $('search-results-section').hidden = !result;
  if (!result) return;
  const shown = Math.min(searchShown, result.candidates.length);
  $('search-summary').textContent = `筛选后共 ${result.candidates.length} 条，已显示 ${shown} 条`;
  $('search-other-sources').hidden = result.broadened || /^https?:\/\//i.test(result.query);
  $('search-context').hidden = !result.bookId;
  $('search-context').textContent = result.bookId ? '正在为书架中的这本书选择其他来源；本书拉黑的书源已排除。' : '';
  $('search-empty-title').textContent = result.bookId ? '本次未找到其他匹配来源' : '本次未找到匹配候选';
  $('search-empty-note').textContent = result.bookId
    ? '本书拉黑的来源已排除；没有找到其他符合书名与小说信息的结果。书架和已存内容保留，可稍后重新搜索。'
    : '索引可能尚未收录；也可以直接粘贴已支持来源的书籍链接。';
  $('search-empty').hidden = Boolean(result.candidates.length);
  $('search-result-actions').hidden = result.candidates.length <= 10;
  $('search-more').hidden = shown >= result.candidates.length;
  $('search-more').textContent = `更多结果（剩余 ${result.candidates.length - shown} 条）`;
  $('search-collapse').hidden = shown <= 10;
  const cards = result.candidates.slice(0, shown).map(candidate => {
    const card = document.createElement('article');
    card.className = 'book-result';
    const top = text('div', '', 'result-top');
    const copy = text('div', '', 'result-copy');
    const heading = document.createElement('h3');
    heading.append(button(candidate.title, 'CANDIDATE', candidate.id, 'text-button book-result-title'));
    const sourceStatus = candidate.identityUnverified ? `${new URL(candidate.url).hostname} · 书名相同，作者与目录待确认`
      : candidate.supported ? `${new URL(candidate.url).hostname} · 已适配，正文待检查`
      : candidate.registeredSourceId ? `${candidate.registeredSourceName} · 已有书源，下载待接入` : '尚未支持此站点的下载';
    copy.append(heading, text('p', candidate.author ? `作者：${candidate.author}` : '作者：未知', 'book-author'),
      text('p', sourceStatus, 'book-author'),
      text('p', candidate.snippet, 'book-description'), text('p', new URL(candidate.url).hostname, 'book-facts'));
    top.append(copy);
    const bottom = text('div', '', 'result-bottom');
    bottom.append(button('查看', 'CANDIDATE', candidate.id));
    card.append(top, bottom);
    return card;
  });
  $('search-results').replaceChildren(...cards);
  $('home-content').hidden = true;
}

async function openBook(value) {
  selected = value;
  previewSignature = '';
  renderDetail();
  if (!$('book-dialog').open) $('book-dialog').showModal();
}

function renderDetail() {
  if (!selected) return;
  const ownBook = state.books.find(item => item.id === selected.id);
  const sourceTarget = !ownBook && state.books.find(item => item.id === state.search?.bookId);
  const book = ownBook || (!sourceTarget && state.books.find(item => item.canonicalUrl === selected.id));
  const source = book || selected;
  $('detail-title').textContent = source.title;
  $('detail-author').textContent = source.author ? '作者：' + source.author : '作者：未知';
  $('detail-category').textContent = book ? '我的书架' : sourceTarget ? `为《${sourceTarget.title}》选择书源` : '搜索候选';
  $('detail-cover').textContent = '书';
  $('detail-facts').textContent = book ? countText(book) : selected.supported ? '已适配来源，正文待检查'
    : selected.registeredSourceId ? `${selected.registeredSourceName} · 已有书源，下载待接入` : '尚未支持下载';
  $('detail-description').textContent = book ? book.message : selected.snippet || '';
  const url = text('a', book?.catalogUrl || selected.url, 'source-url');
  url.href = book?.catalogUrl || selected.url;
  url.target = '_blank';
  url.rel = 'noopener noreferrer';
  $('source-list').replaceChildren(url);
  const blocked = (book || sourceTarget)?.blockedSources || [];
  $('blocked-sources').hidden = !blocked.length;
  $('blocked-sources').replaceChildren(...blocked.flatMap(item => {
    const restore = button('恢复', 'UNBLOCK_SOURCE', (book || sourceTarget).id, 'text-button');
    restore.dataset.key = item.key;
    return [text('span', `本书已拉黑：${item.label} `), restore];
  }));
  $('chapter-preview').textContent = book?.rangeSavedCount ? '已有正文已提交保存，可导出 TXT。'
    : !book && !selected.supported ? '此来源的目录和正文读取尚未接入。搜索摘要不作为小说正文。'
      : '加入书架只保存书籍；下载或更新目录时才访问来源。搜索摘要不作为小说正文。';
  $('detail-state').hidden = !book;
  $('detail-diagnostics').hidden = !book;
  if (book) {
    $('detail-status').textContent = delivery(book) || labels[book.status];
    $('detail-progress').textContent = countText(book);
    $('detail-fill').style.width = `${book.rangeTotal ? book.rangeSavedCount / book.rangeTotal * 100 : 0}%`;
    $('detail-activity').textContent = book.message;
    $('detail-diagnostics-content').replaceChildren(text('dt', '当前阶段'), text('dd', book.stage === 'catalog' ? '取得目录' : '取得章节'),
      text('dt', '当前原因'), text('dd', book.reason), text('dt', '文件交付'), text('dd', delivery(book) || '尚未生成'));
    if (book.lastIssue) $('detail-diagnostics-content').append(text('dt', '最近情况'),
      text('dd', new Date(book.lastIssue.time).toLocaleString() + ' · ' + book.lastIssue.reason),
      text('dt', '当时目标'), text('dd', book.lastIssue.targetUrl || '无进行中的页面请求'),
      ...(book.lastIssue.observedUrl ? [text('dt', '实际页面'), text('dd', book.lastIssue.observedUrl)] : []));
  }
  const [label, action] = book ? actionFor(book) : sourceTarget ? ['选为本书书源', 'SELECT_SOURCE'] : ['加入书架', 'ADD'];
  $('detail-primary').textContent = label;
  $('detail-primary').disabled = false;
  $('detail-primary').dataset.action = action;
  $('detail-primary').dataset.id = book?.id || selected.id;
  $('detail-export').hidden = !book?.rangeSavedCount || action === 'EXPORT';
  $('detail-export').dataset.action = 'EXPORT';
  $('detail-export').dataset.id = book?.id || '';
  $('detail-stop').hidden = book?.status !== 'needs_action';
  $('detail-stop').dataset.action = 'PAUSE';
  $('detail-stop').dataset.id = book?.id || '';
  $('detail-remove').hidden = true;
  $('detail-update').hidden = !book || !book.supported || book.sourceBlocked;
  $('detail-update').disabled = Boolean(book && ['queued', 'downloading', 'needs_action'].includes(book.status));
  $('detail-update').dataset.action = 'UPDATE_CATALOG';
  $('detail-update').dataset.id = book?.id || '';
  $('detail-other').hidden = action === 'OTHER_SOURCES'
    || (!book && (Boolean(sourceTarget) || state.search?.broadened || /^https?:\/\//i.test(state.search?.query || '')));
  $('detail-other').dataset.action = 'OTHER_SOURCES';
  $('detail-other').dataset.id = book?.id || '';
  $('detail-block').hidden = !book || book.sourceBlocked;
  $('detail-block').dataset.action = 'BLOCK_AND_SEARCH';
  $('detail-block').dataset.id = book?.id || '';
  $('detail-footer-note').textContent = sourceTarget ? '选定后仍保留原书架记录；各来源正文独立保存。'
    : '入架不下载；更新目录不取正文。样机下载范围仍为前 3 章。';
  if (book?.rangeSavedCount) {
    const marker = `${book.id}:${book.canonicalUrl}:${book.rangeSavedCount}`;
    if (marker !== previewSignature) {
      previewSignature = marker;
      void call('PREVIEW', { id: book.id }).then(value => {
        if (previewSignature === marker) $('chapter-preview').textContent = value.text;
      }).catch(() => {});
    }
  }
}

async function refresh() {
  try {
    const incoming = await call('STATE');
    const searchChanged = !searching && JSON.stringify(incoming.search) !== JSON.stringify(state.search);
    const priorSession = state.search?.sessionId;
    state = { ...incoming, search: searching ? state.search : incoming.search };
    if (signature && searchChanged) {
      if (priorSession !== state.search?.sessionId) searchShown = 10;
      renderSearch();
    }
    $('key-state').textContent = state.configured ? '搜索已配置' : '搜索未配置';
    $('settings-status').textContent = state.configured ? '已保存 Key；此处不会显示原值。' : '尚未保存 Key。';
    const next = JSON.stringify(state.books);
    if (next !== signature) { signature = next; renderBooks(); if ($('book-dialog').open) renderDetail(); }
  } catch (error) { $('app-status').textContent = error.message; }
}

$('settings-open').addEventListener('click', () => { $('api-key').value = ''; $('settings-dialog').showModal(); });
$('settings-close').addEventListener('click', () => { $('api-key').value = ''; $('settings-dialog').close(); });
$('settings-dialog').addEventListener('close', () => { $('api-key').value = ''; });
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const key = $('api-key').value;
  $('api-key').value = '';
  try {
    await call('SAVE_KEY', { key });
    $('settings-dialog').close();
    await refresh();
    toast('搜索 Key 已保存。');
  } catch (error) { toast(error.message); }
});
$('settings-clear').addEventListener('click', async () => { await call('CLEAR_KEY'); $('api-key').value = ''; await refresh(); });
$('about-open').addEventListener('click', () => $('about-dialog').showModal());
$('book-close').addEventListener('click', () => $('book-dialog').close());
$('activity-toggle').addEventListener('click', () => {
  $('activity-panel').hidden = !$('activity-panel').hidden;
  $('activity-toggle').setAttribute('aria-expanded', String(!$('activity-panel').hidden));
});
$('activity-close').addEventListener('click', () => { $('activity-panel').hidden = true; $('activity-toggle').setAttribute('aria-expanded', 'false'); });
async function runSearch(bookId = null, includeOtherSources = false) {
  const version = ++searchVersion;
  if (includeOtherSources) { $('book-dialog').close(); setView('search'); }
  searching = true;
  $('search-results-section').hidden = false;
  if (!state.search) $('search-empty').hidden = true;
  $('search-submit').textContent = '重新搜索';
  $('search-note').hidden = false;
  $('search-note').textContent = '正在搜索…';
  try {
    const result = await call('SEARCH', { query: $('search-input').value, bookId, includeOtherSources });
    if (version !== searchVersion) return;
    state.search = result;
    $('search-input').value = result.query;
    searchShown = 10;
    renderSearch();
    const preferred = result.candidates.find(item => item.id === result.preferredId);
    if (preferred && !includeOtherSources) await openBook(preferred);
    $('search-note').hidden = true;
  } catch (error) {
    if (version === searchVersion) { $('search-note').textContent = error.message; $('search-note').hidden = false; }
  } finally {
    if (version === searchVersion) { searching = false; $('search-submit').textContent = '找一本书'; }
  }
}
$('search-form').addEventListener('submit', event => { event.preventDefault(); void runSearch(); });
$('search-other-sources').addEventListener('click', () => void runSearch(state.search?.bookId || null, true));
async function showSearchResults(count) {
  if (!state.search) return;
  searchShown = Math.min(count, state.search.candidates.length);
  renderSearch();
  await chrome.storage.session.set({ searchView: { sessionId: state.search.sessionId, shown: searchShown } });
}
$('search-more').addEventListener('click', () => void showSearchResults(searchShown + 10));
$('search-collapse').addEventListener('click', () => {
  void showSearchResults(10);
  $('search-results-section').scrollIntoView({ block: 'start' });
});
$('search-clear').addEventListener('click', async () => {
  ++searchVersion;
  await call('STOP_SEARCH', { clear: true });
  searching = false;
  $('search-submit').textContent = '找一本书';
  $('search-input').value = '';
  state.search = null;
  searchShown = 10;
  $('search-results-section').hidden = true;
  $('search-note').hidden = true;
  renderBooks();
});
document.addEventListener('click', async event => {
  const target = event.target.closest('button');
  if (!target) return;
  if (target.dataset.view) { setView(target.dataset.view); return; }
  if (target.dataset.filter) {
    filter = target.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(node => {
      node.classList.toggle('is-active', node.dataset.filter === filter);
      node.setAttribute('aria-pressed', String(node.dataset.filter === filter));
    });
    renderBooks(); return;
  }
  const { action, id } = target.dataset;
  if (!action) return;
  try {
    if (action === 'CANDIDATE') await openBook(state.search.candidates.find(item => item.id === id));
    else if (action === 'OPEN') await openBook(state.books.find(item => item.id === id));
    else if (action === 'ADD' || action === 'SELECT_SOURCE') {
      target.disabled = true;
      const value = action === 'ADD' ? await call('ADD_BOOK', { candidate: selected })
        : await call('SET_SOURCE', { id: state.search.bookId, candidate: selected });
      await refresh();
      await openBook(state.books.find(item => item.id === value.id));
    } else if (action === 'OTHER_SOURCES' || action === 'BLOCK_AND_SEARCH') {
      if (action === 'BLOCK_AND_SEARCH') {
        await call('BLOCK_SOURCE', { id });
        await refresh();
        toast('已仅对本书拉黑此书源。已有内容保留。');
      }
      await runSearch(id || null, true);
    } else {
      target.disabled = true;
      await call(action, { id, ...(target.dataset.key ? { key: target.dataset.key } : {}) });
      await refresh();
      if (action === 'EXPORT') toast('已请求导出，文件状态见下载详情。');
    }
  } catch (error) {
    const evidence = error.code === 'work_mismatch' && error.detail
      ? `（当前：${error.detail.bookTitle || '未知'}／${error.detail.bookAuthor || '未知'}；`
        + `候选：${error.detail.candidateTitle || '未知'}／${error.detail.candidateAuthor || '未知'}）`
      : '';
    toast(error.message + evidence);
  }
  finally { target.disabled = false; if ($('book-dialog').open) renderDetail(); }
});

setView(location.hash === '#library' ? 'library' : 'search');
await refresh();
if (state.search) {
  const { searchView } = await chrome.storage.session.get('searchView');
  if (state.search.sessionId && searchView?.sessionId === state.search.sessionId
      && Number.isInteger(searchView.shown) && searchView.shown >= 10) searchShown = searchView.shown;
  $('search-input').value = state.search.query;
  renderSearch();
}

async function checkLocalUpdate() {
  if (checkingUpdate || searching || document.hidden || $('book-dialog').open || $('settings-dialog').open) return;
  checkingUpdate = true;
  try {
    const manifest = await (await fetch(chrome.runtime.getURL('manifest.json'), { cache: 'no-store' })).json();
    if (manifest.version === chrome.runtime.getManifest().version) return;
    const current = await call('STATE');
    if (searching || $('book-dialog').open || $('settings-dialog').open || current.books.some(book =>
      ['queued', 'downloading', 'needs_action'].includes(book.status)
      || ['preparing', 'starting', 'in_progress'].includes(book.artifact?.state))) return;
    await chrome.storage.local.set({ reopenAfterUpdate: true });
    chrome.runtime.reload();
  } catch { /* A local update check must not interrupt reading or searching. */ }
  finally { checkingUpdate = false; }
}
window.addEventListener('focus', () => void checkLocalUpdate());
document.addEventListener('visibilitychange', () => void checkLocalUpdate());
void checkLocalUpdate();
setInterval(() => void checkLocalUpdate(), 15000);
setInterval(() => void refresh(), 1000);
