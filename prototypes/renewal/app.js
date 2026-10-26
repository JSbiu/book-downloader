(function () {
  'use strict';

  const STORAGE_KEY = 'shuye-renewal-demo-v1';
  const SCHEMA_VERSION = 1;
  const CHAPTER_MS = 700;
  const SEARCH_DELAY_MS = 320;
  const ACTIVE_STATES = new Set(['queued', 'downloading', 'needs_action']);
  const ATTENTION_STATES = new Set(['paused', 'partial']);
  const ALL_STATES = new Set([
    'queued',
    'downloading',
    'needs_action',
    'paused',
    'partial',
    'ready'
  ]);

  const BOOK_DEFINITIONS = [
    {
      id: 'a',
      title: '示例小说 A',
      author: '示例作者甲',
      category: '示例书目',
      cover: 'cover-blue',
      description: '一本用于体验找书、来源确认和下载流程的示例书。所有内容均为原创演示文字。',
      sources: [
        {
          id: 'a-source-one',
          name: '公开来源一',
          chapters: 12,
          url: 'https://example.com/books/example-a/source-one',
          description: '12 章 · 可预览正文'
        },
        {
          id: 'a-source-two',
          name: '公开来源二',
          chapters: 10,
          url: 'https://example.com/books/example-a/source-two',
          description: '10 章 · 另一版本'
        }
      ]
    },
    {
      id: 'b',
      title: '示例小说 B',
      author: '示例作者乙',
      category: '示例书目',
      cover: 'cover-sage',
      description: '一本已经完成示例下载的书，用来展示书架中的“已就绪”状态与导出入口。',
      sources: [
        {
          id: 'b-source-one',
          name: '公开来源一',
          chapters: 18,
          url: 'https://example.com/books/example-b/source-one',
          description: '18 章 · 已完成示例下载'
        }
      ]
    },
    {
      id: 'c',
      title: '示例小说 C',
      author: '示例作者丙',
      category: '示例书目',
      cover: 'cover-clay',
      description: '一本下载到一半的示例书，用来体验部分结果、继续下载和已有内容导出。',
      sources: [
        {
          id: 'c-source-one',
          name: '公开来源一',
          chapters: 10,
          url: 'https://example.com/books/example-c/source-one',
          description: '10 章 · 已取得 6 章'
        }
      ]
    }
  ];

  const BOOKS = new Map(BOOK_DEFINITIONS.map(function (book) {
    return [book.id, book];
  }));

  const STATUS_LABELS = {
    queued: '排队中',
    downloading: '正在下载',
    needs_action: '等待人工确认',
    paused: '已暂停',
    partial: '部分完成',
    ready: '已就绪'
  };

  const STAGE_LABELS = {
    queued: '等待执行',
    download: '取得目录内容',
    awaiting_confirmation: '等待来源确认',
    partial: '部分内容未取得',
    paused: '用户暂停',
    complete: '全部章节已取得'
  };

  const REASON_LABELS = {
    new_task: '新建示例下载',
    downloading: '示例内容取得中',
    manual_verification: '来源页面需要人工确认',
    verification_completed: '人工确认已记录',
    demo_partial_stop: '到达部分下载演示停止点',
    existing_gap: '已有部分内容，等待继续',
    user_paused: '用户主动暂停',
    continued: '用户继续下载',
    complete: '范围内章节全部取得'
  };

  const byId = function (id) {
    return document.getElementById(id);
  };

  function safeRead(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function safeWrite(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (_) {
      return false;
    }
  }

  function safeRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_) {
      /* The prototype still works in memory when storage is unavailable. */
    }
  }

  function numeric(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function bookById(id) {
    return BOOKS.get(String(id || '')) || null;
  }

  function sourceById(book, id) {
    return book && book.sources.find(function (source) {
      return source.id === id;
    }) || null;
  }

  function firstSource(book) {
    return book && book.sources.length ? book.sources[0] : null;
  }

  function makeRecord(book, source, overrides) {
    const now = Date.now();
    return Object.assign({
      id: book.id,
      status: 'queued',
      sourceId: source.id,
      current: 0,
      total: source.chapters,
      stage: 'queued',
      reason: 'new_task',
      scenario: 'normal',
      verificationCompleted: false,
      partialStopHandled: false,
      queuedAt: now,
      startedAt: null,
      completedAt: null,
      chapterClockAt: now,
      updatedAt: now
    }, overrides || {});
  }

  function createSeedState() {
    const now = Date.now();
    const b = bookById('b');
    const c = bookById('c');
    const bSource = firstSource(b);
    const cSource = firstSource(c);

    return {
      schema: SCHEMA_VERSION,
      view: 'search',
      filter: 'all',
      query: '',
      search: { kind: '', ids: [], ready: true },
      scenario: 'normal',
      selectedBookId: 'c',
      queue: [],
      books: {
        b: makeRecord(b, bSource, {
          status: 'ready',
          current: 18,
          total: 18,
          stage: 'complete',
          reason: 'complete',
          queuedAt: now - 7200000,
          startedAt: now - 7100000,
          completedAt: now - 6500000,
          chapterClockAt: now - 6500000,
          updatedAt: now - 300000
        }),
        c: makeRecord(c, cSource, {
          status: 'partial',
          current: 6,
          total: 10,
          stage: 'partial',
          reason: 'existing_gap',
          partialStopHandled: true,
          queuedAt: now - 1800000,
          startedAt: now - 1700000,
          chapterClockAt: now - 1200000,
          updatedAt: now - 1200000
        })
      }
    };
  }

  function normalizeRecord(id, value, fallback) {
    const book = bookById(id);
    if (!book || !value || typeof value !== 'object') {
      return null;
    }
    const candidateSource = sourceById(book, String(value.sourceId || ''));
    const source = candidateSource || firstSource(book);
    if (!source) {
      return null;
    }
    const status = ALL_STATES.has(value.status) ? value.status : 'paused';
    const total = clamp(Math.round(numeric(value.total, source.chapters)), 1, source.chapters);
    let current = clamp(Math.round(numeric(value.current, 0)), 0, total);
    if (status === 'ready') {
      current = total;
    }
    const now = Date.now();
    return {
      id: id,
      status: status,
      sourceId: source.id,
      current: current,
      total: total,
      stage: typeof value.stage === 'string' ? value.stage : (status === 'ready' ? 'complete' : 'paused'),
      reason: typeof value.reason === 'string' ? value.reason : (status === 'ready' ? 'complete' : 'user_paused'),
      scenario: ['normal', 'verification', 'partial'].includes(value.scenario) ? value.scenario : 'normal',
      verificationCompleted: Boolean(value.verificationCompleted),
      partialStopHandled: Boolean(value.partialStopHandled),
      queuedAt: numeric(value.queuedAt, now),
      startedAt: value.startedAt === null ? null : numeric(value.startedAt, now),
      completedAt: value.completedAt === null ? null : numeric(value.completedAt, now),
      chapterClockAt: numeric(value.chapterClockAt, now),
      updatedAt: numeric(value.updatedAt, now)
    };
  }

  function normalizeState(value) {
    const fallback = createSeedState();
    if (!value || typeof value !== 'object' || value.schema !== SCHEMA_VERSION) {
      return fallback;
    }

    const state = fallback;
    state.view = ['search', 'library'].includes(value.view) ? value.view : 'search';
    state.filter = ['all', 'active', 'attention', 'ready'].includes(value.filter) ? value.filter : 'all';
    state.query = typeof value.query === 'string' ? value.query : '';
    state.scenario = ['normal', 'verification', 'partial'].includes(value.scenario)
      ? value.scenario
      : 'normal';
    state.selectedBookId = typeof value.selectedBookId === 'string' ? value.selectedBookId : null;

    const rawSearch = value.search && typeof value.search === 'object' ? value.search : {};
    state.search = {
      kind: rawSearch.kind === 'url' ? 'url' : (state.query ? 'text' : ''),
      ids: Array.isArray(rawSearch.ids)
        ? rawSearch.ids.filter(function (id) { return Boolean(bookById(id)); })
        : [],
      ready: rawSearch.ready !== false
    };

    state.books = {};
    if (value.books && typeof value.books === 'object' && !Array.isArray(value.books)) {
      Object.keys(value.books).forEach(function (id) {
        const record = normalizeRecord(id, value.books[id], fallback);
        if (record) {
          state.books[id] = record;
        }
      });
    }

    const seen = new Set();
    state.queue = Array.isArray(value.queue) ? value.queue.filter(function (id) {
      const record = state.books[id];
      if (!record || !ACTIVE_STATES.has(record.status) || seen.has(id)) {
        return false;
      }
      seen.add(id);
      return true;
    }) : [];

    Object.keys(state.books).forEach(function (id) {
      const record = state.books[id];
      if (ACTIVE_STATES.has(record.status) && !state.queue.includes(id)) {
        state.queue.push(id);
      }
    });

    if (state.selectedBookId && !state.books[state.selectedBookId]) {
      state.selectedBookId = null;
    }
    return state;
  }

  function loadState() {
    const raw = safeRead(STORAGE_KEY);
    if (!raw) {
      return createSeedState();
    }
    try {
      return normalizeState(JSON.parse(raw));
    } catch (_) {
      return createSeedState();
    }
  }

  function saveState() {
    const saved = safeWrite(STORAGE_KEY, JSON.stringify(state));
    if (!saved) {
      announce('浏览器未允许本地存储；刷新后演示状态可能丢失。');
    }
  }

  function createNode(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text !== undefined) {
      element.textContent = String(text);
    }
    return element;
  }

  function createIcon(name) {
    const namespace = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(namespace, 'svg');
    const use = document.createElementNS(namespace, 'use');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    use.setAttribute('href', '#i-' + name);
    svg.appendChild(use);
    return svg;
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) {
      element.textContent = String(value);
    }
  }

  function relativeTime(timestamp) {
    const delta = Math.max(0, Date.now() - numeric(timestamp, Date.now()));
    if (delta < 60000) {
      return '刚刚';
    }
    if (delta < 3600000) {
      return Math.floor(delta / 60000) + ' 分钟前';
    }
    if (delta < 86400000) {
      return Math.floor(delta / 3600000) + ' 小时前';
    }
    return Math.floor(delta / 86400000) + ' 天前';
  }

  function recordSource(record) {
    if (!record) {
      return null;
    }
    const book = bookById(record.id);
    return sourceById(book, record.sourceId) || firstSource(book);
  }

  function progressPercent(record) {
    if (!record || !record.total) {
      return 0;
    }
    return clamp((record.current / record.total) * 100, 0, 100);
  }

  function progressLabel(record) {
    if (!record) {
      return '';
    }
    return record.current + '/' + record.total + ' 章';
  }

  function statusBadge(record, labelOverride, extraClass) {
    const status = record && record.status || '';
    const badge = createNode(
      'span',
      'status-badge' + (extraClass ? ' ' + extraClass : ''),
      labelOverride || STATUS_LABELS[status] || status
    );
    badge.dataset.state = status || 'idle';
    return badge;
  }

  function diagnosticText(record) {
    if (!record) {
      return '';
    }
    const source = recordSource(record);
    switch (record.status) {
      case 'queued':
        return '已进入示例队列，前面的任务完成后会继续。';
      case 'downloading':
        return '正在取得第 ' + Math.min(record.current + 1, record.total) + ' 章，来源保持为' + source.name + '。';
      case 'needs_action':
        return '示例来源需要人工确认；当前页面和已取得内容都会保留。';
      case 'paused':
        return '已暂停，当前已取得的 ' + record.current + ' 章会保留。';
      case 'partial':
        return '已取得 ' + record.current + ' 章，剩余 ' + Math.max(0, record.total - record.current) + ' 章尚未取得。';
      case 'ready':
        return '范围内 ' + record.total + ' 章已全部取得，可以导出示例 TXT。';
      default:
        return record.reason || '';
    }
  }

  function footerNote(record) {
    if (!record) {
      return '选择来源后，开始这份示例下载。';
    }
    switch (record.status) {
      case 'queued':
        return '已加入下载队列，轮到时自动开始。';
      case 'downloading':
        return '进度会跟随这本书保存；刷新页面后继续推进。';
      case 'needs_action':
        return '完成示例确认后，会从当前章节继续。';
      case 'paused':
        return '已暂停。继续下载会从已有进度接着走。';
      case 'partial':
        return '还差 ' + Math.max(0, record.total - record.current) + ' 章，可先导出已有部分，或继续下载。';
      case 'ready':
        return '内容已就绪，可以导出这份示例 TXT。';
      default:
        return '当前状态会随这本书保存。';
    }
  }

  function actionForRecord(record) {
    if (!record) {
      return { action: 'start', label: '加入书架并下载' };
    }
    switch (record.status) {
      case 'queued':
        return { action: 'wait', label: '已加入队列' };
      case 'downloading':
        return { action: 'wait', label: '正在下载' };
      case 'needs_action':
        return { action: 'verify', label: '前往确认页面' };
      case 'paused':
      case 'partial':
        return { action: 'continue', label: '继续下载' };
      case 'ready':
        return { action: 'export', label: '导出TXT' };
      default:
        return { action: 'open', label: '查看书籍' };
    }
  }

  function makeCover(book, extraClass, smallText) {
    const cover = createNode('div', 'book-cover ' + book.cover + (extraClass ? ' ' + extraClass : ''));
    cover.appendChild(createNode('span', 'cover-kicker', '示例书'));
    cover.appendChild(createNode('strong', '', book.title));
    cover.appendChild(createNode('small', '', smallText || book.category));
    return cover;
  }

  function paintCover(target, book, smallText) {
    target.className = 'detail-cover book-cover ' + book.cover;
    target.replaceChildren();
    target.appendChild(createNode('span', 'cover-kicker', '示例书'));
    target.appendChild(createNode('strong', '', book.title));
    target.appendChild(createNode('small', '', smallText || book.category));
  }

  function makeActionButton(label, className, action, bookId) {
    const button = createNode('button', className, label);
    button.type = 'button';
    button.dataset.bookAction = action;
    button.dataset.bookId = bookId;
    return button;
  }

  function sourceSummary(book) {
    if (book.sources.length === 1) {
      return book.sources[0].chapters + ' 章';
    }
    return book.sources.length + ' 个来源可选';
  }

  function searchBookCard(book) {
    const record = state.books[book.id] || null;
    const card = createNode('article', 'book-result');
    card.dataset.openBook = book.id;
    card.tabIndex = 0;
    card.setAttribute('aria-label', '查看《' + book.title + '》的来源');

    const top = createNode('div', 'result-top');
    top.appendChild(makeCover(book, '', book.sources[0].chapters + ' 章'));
    const copy = createNode('div', 'result-copy');
    copy.appendChild(createNode('h3', '', book.title));
    copy.appendChild(createNode('p', 'book-author', book.author));
    copy.appendChild(createNode('p', 'book-description', book.description));

    const facts = createNode('div', 'book-facts');
    facts.appendChild(createNode('span', '', sourceSummary(book)));
    if (book.sources.length > 1) {
      facts.appendChild(createNode('span', '', book.sources.map(function (source) {
        return source.chapters + ' 章';
      }).join(' / ')));
    }
    if (record) {
      facts.appendChild(statusBadge(record));
    }
    copy.appendChild(facts);
    top.appendChild(copy);
    card.appendChild(top);

    const bottom = createNode('div', 'result-bottom');
    bottom.appendChild(createNode(
      'span',
      'result-source-note',
      record ? '已在书架，打开查看当前进度' : '确认来源与正文样例后再加入书架'
    ));
    const open = createNode('button', 'button button-secondary', '查看来源');
    open.type = 'button';
    open.dataset.openBook = book.id;
    open.appendChild(createIcon('arrow'));
    bottom.appendChild(open);
    card.appendChild(bottom);
    return card;
  }

  function makeShelfCard(record) {
    const book = bookById(record.id);
    const source = recordSource(record);
    const card = createNode('article', 'shelf-book');
    card.dataset.openBook = book.id;
    card.dataset.bookId = book.id;
    card.tabIndex = 0;
    card.setAttribute('aria-label', '打开《' + book.title + '》');
    const cover = makeCover(book, '', progressLabel(record));
    cover.dataset.shelfCover = '';
    card.appendChild(cover);

    const copy = createNode('div', 'shelf-copy');
    const top = createNode('div', 'shelf-top');
    const titleWrap = createNode('div', '');
    titleWrap.appendChild(createNode('h3', '', book.title));
    titleWrap.appendChild(createNode('p', 'book-author', book.author));
    top.appendChild(titleWrap);
    const badge = statusBadge(record, null, 'shelf-status');
    badge.dataset.shelfStatus = '';
    top.appendChild(badge);
    copy.appendChild(top);

    const description = createNode('p', 'shelf-description');
    const sourceLabel = createNode('span', '', source.name + ' · ');
    sourceLabel.dataset.shelfSource = '';
    description.appendChild(sourceLabel);
    const progress = createNode('span', '', progressLabel(record));
    progress.dataset.progressLabel = '';
    progress.dataset.bookId = book.id;
    description.appendChild(progress);
    copy.appendChild(description);

    const track = createNode('div', 'progress-track');
    const fill = createNode('span', '');
    fill.dataset.progressFill = '';
    fill.dataset.bookId = book.id;
    fill.style.width = progressPercent(record) + '%';
    track.appendChild(fill);
    copy.appendChild(track);

    const actions = createNode('div', 'shelf-actions');
    actions.dataset.shelfActions = '';
    appendShelfAction(actions, record);
    actions.appendChild(makeActionButton('查看', 'text-button', 'open', book.id));
    copy.appendChild(actions);
    card.appendChild(copy);
    return card;
  }

  function shelfAction(record) {
    const action = actionForRecord(record);
    if (record.status === 'queued' || record.status === 'downloading') {
      return { action: 'pause', label: '暂停' };
    }
    return action;
  }

  function appendShelfAction(actions, record) {
    const action = shelfAction(record);
    const button = makeActionButton(
      action.action === 'verify' ? '确认' : action.label,
      'button button-secondary',
      action.action,
      record.id
    );
    button.dataset.shelfAction = '';
    actions.appendChild(button);
  }

  function updateShelfCard(card, record) {
    const cover = card.querySelector('[data-shelf-cover] small');
    if (cover) {
      cover.textContent = progressLabel(record);
    }
    const badge = card.querySelector('[data-shelf-status]');
    if (badge) {
      badge.textContent = STATUS_LABELS[record.status];
      badge.dataset.state = record.status;
    }
    const source = card.querySelector('[data-shelf-source]');
    if (source) {
      source.textContent = recordSource(record).name + ' · ';
    }
    const actions = card.querySelector('[data-shelf-actions]');
    if (!actions) {
      return;
    }
    const action = shelfAction(record);
    let button = actions.querySelector('[data-shelf-action]');
    if (!button) {
      button = makeActionButton('', 'button button-secondary', action.action, record.id);
      button.dataset.shelfAction = '';
      actions.prepend(button);
    }
    const nextLabel = action.action === 'verify' ? '确认' : action.label;
    if (button.textContent !== nextLabel) {
      button.textContent = nextLabel;
    }
    if (button.dataset.bookAction !== action.action) {
      button.dataset.bookAction = action.action;
    }
    if (button.dataset.bookId !== record.id) {
      button.dataset.bookId = record.id;
    }
  }

  function makeHomeCard(record) {
    const book = bookById(record.id);
    const card = createNode('article', 'home-book');
    card.dataset.openBook = book.id;
    card.tabIndex = 0;
    card.setAttribute('aria-label', '打开《' + book.title + '》');
    card.appendChild(makeCover(book, 'home-cover', book.category));

    const copy = createNode('div', 'home-book-copy');
    copy.appendChild(createNode('h3', '', book.title));
    copy.appendChild(createNode('p', 'book-author', book.author));
    const line = createNode('p', 'home-book-progress');
    const label = createNode('span', '', progressLabel(record) + ' · ' + STATUS_LABELS[record.status]);
    label.dataset.progressLabel = '';
    label.dataset.bookId = book.id;
    line.appendChild(label);
    copy.appendChild(line);
    card.appendChild(copy);
    return card;
  }

  function sortedRecords() {
    return Object.keys(state.books)
      .map(function (id) { return state.books[id]; })
      .sort(function (left, right) { return right.updatedAt - left.updatedAt; });
  }

  function librariesForFilter() {
    return sortedRecords().filter(function (record) {
      if (state.filter === 'active') {
        return ACTIVE_STATES.has(record.status);
      }
      if (state.filter === 'attention') {
        return ATTENTION_STATES.has(record.status);
      }
      if (state.filter === 'ready') {
        return record.status === 'ready';
      }
      return true;
    });
  }

  function setSearchResultState() {
    const hasQuery = Boolean(state.query);
    const resultsSection = byId('search-results-section');
    const home = byId('home-content');
    if (!resultsSection || !home) {
      return;
    }
    resultsSection.hidden = !hasQuery;
    home.hidden = hasQuery;
    if (!hasQuery) {
      return;
    }

    const ready = state.search.ready;
    const ids = state.search.ids;
    const isUrl = state.search.kind === 'url';
    const searchSignature = JSON.stringify([ready, ids]);
    const note = byId('search-note');
    if (note) {
      note.hidden = !isUrl;
      note.textContent = isUrl
        ? '链接识别流程演示，展示示例书籍，未访问此网址'
        : '';
    }
    setText(
      'search-summary',
      ready
        ? '找到 ' + ids.length + ' 本示例书籍。'
        : '正在示例书目中查找…'
    );
    const submit = byId('search-submit');
    if (submit) {
      submit.disabled = !ready;
      submit.textContent = ready ? '找一本书' : '正在查找…';
      if (ready) {
        submit.appendChild(createIcon('arrow'));
      }
    }

    const empty = byId('search-empty');
    if (empty) {
      empty.hidden = !ready || ids.length > 0;
    }
    const list = byId('search-results');
    if (!list) {
      return;
    }
    if (list.dataset.signature === searchSignature && (ready || !list.childElementCount)) {
      return;
    }
    if (!ready) {
      list.dataset.signature = searchSignature;
      list.replaceChildren();
      return;
    }
    if (list.dataset.signature === searchSignature) {
      return;
    }
    list.dataset.signature = searchSignature;
    list.replaceChildren.apply(list, ids.map(function (id) {
      return searchBookCard(bookById(id));
    }));
  }

  function renderView() {
    const searchView = byId('view-search');
    const libraryView = byId('view-library');
    if (!searchView || !libraryView) {
      return;
    }
    searchView.hidden = state.view !== 'search';
    libraryView.hidden = state.view !== 'library';
    document.querySelectorAll('.main-nav [data-view]').forEach(function (button) {
      if (button.dataset.view === state.view) {
        button.setAttribute('aria-current', 'page');
      } else {
        button.removeAttribute('aria-current');
      }
    });
  }

  function renderCounts() {
    const count = Object.keys(state.books).length;
    setText('library-nav-count', count);
    setText('library-count', count);
    const ready = sortedRecords().filter(function (record) {
      return record.status === 'ready';
    }).length;
    const attention = sortedRecords().filter(function (record) {
      return ATTENTION_STATES.has(record.status);
    }).length;
    const active = sortedRecords().filter(function (record) {
      return ACTIVE_STATES.has(record.status);
    }).length;
    const summary = [];
    summary.push(count + ' 本');
    if (active) {
      summary.push(active + ' 本进行中');
    }
    if (attention) {
      summary.push(attention + ' 本待继续');
    }
    if (ready) {
      summary.push(ready + ' 本已就绪');
    }
    setText('library-summary', summary.join(' · ') || '下载好的书、还没完成的进度，都留在这里。');
  }

  function renderHome() {
    const list = byId('home-books');
    if (!list) {
      return;
    }
    const recent = sortedRecords().slice(0, 2);
    const signature = recent.map(function (record) {
      return record.id + ':' + record.status;
    }).join('|');
    if (list.dataset.signature === signature) {
      return;
    }
    list.dataset.signature = signature;
    if (!recent.length) {
      list.replaceChildren(createNode('p', 'empty-state', '书架还是空的，先找一本书吧。'));
      return;
    }
    list.replaceChildren.apply(list, recent.map(makeHomeCard));
  }

  function renderLibrary() {
    const list = byId('library-list');
    const empty = byId('library-empty');
    if (!list || !empty) {
      return;
    }
    document.querySelectorAll('[data-filter]').forEach(function (button) {
      const active = button.dataset.filter === state.filter;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });

    const records = librariesForFilter();
    const structureSignature = [
      state.filter,
      records.map(function (record) {
        return record.id;
      }).join('|')
    ].join('||');
    if (list.dataset.signature !== structureSignature) {
      list.dataset.signature = structureSignature;
      list.replaceChildren.apply(list, records.map(makeShelfCard));
    } else {
      records.forEach(function (record) {
        const card = list.querySelector('.shelf-book[data-book-id="' + record.id + '"]');
        if (card) {
          updateShelfCard(card, record);
        }
      });
    }
    empty.hidden = records.length > 0;
    if (!records.length) {
      const heading = empty.querySelector('h2');
      const copy = empty.querySelector('p');
      const total = Object.keys(state.books).length;
      if (!total) {
        if (heading) {
          heading.textContent = '这里还没有书';
        }
        if (copy) {
          copy.textContent = '找到想要的书，确认后就会留在书架。';
        }
      } else if (state.filter === 'active') {
        if (heading) {
          heading.textContent = '没有正在下载的书';
        }
        if (copy) {
          copy.textContent = '新加入的示例下载会显示在这里。';
        }
      } else if (state.filter === 'attention') {
        if (heading) {
          heading.textContent = '没有待继续的书';
        }
        if (copy) {
          copy.textContent = '暂停或部分完成的示例书会显示在这里。';
        }
      } else if (state.filter === 'ready') {
        if (heading) {
          heading.textContent = '还没有准备好的书';
        }
        if (copy) {
          copy.textContent = '完成全部范围内章节后，就可以从这里导出。';
        }
      }
    }
  }

  function updateProgressLabels() {
    document.querySelectorAll('[data-progress-label]').forEach(function (element) {
      const record = state.books[element.dataset.bookId];
      if (record) {
        const nextText = progressLabel(record) + (element.closest('.home-book') ? ' · ' + STATUS_LABELS[record.status] : '');
        if (element.textContent !== nextText) {
          element.textContent = nextText;
        }
      }
    });
    document.querySelectorAll('[data-progress-fill]').forEach(function (element) {
      const record = state.books[element.dataset.bookId];
      const nextWidth = (record ? progressPercent(record) : 0) + '%';
      if (element.style.width !== nextWidth) {
        element.style.width = nextWidth;
      }
    });
  }

  function renderActivity() {
    const list = byId('activity-list');
    const count = byId('activity-count');
    if (!list || !count) {
      return;
    }
    const actionable = sortedRecords().filter(function (record) {
      return ACTIVE_STATES.has(record.status) || ATTENTION_STATES.has(record.status);
    });
    count.hidden = actionable.length === 0;
    count.textContent = String(actionable.length);

    const rows = sortedRecords();
    const signature = [
      rows.map(function (record) {
        return record.id + ':' + record.status + ':' + record.current;
      }).join('|'),
      actionable.length
    ].join('||');
    if (list.dataset.signature === signature) {
      return;
    }
    list.dataset.signature = signature;
    if (!rows.length) {
      list.replaceChildren(createNode('p', 'empty-state', '还没有下载动态。'));
      return;
    }
    list.replaceChildren.apply(list, rows.map(function (record) {
      const book = bookById(record.id);
      const item = createNode('div', 'activity-item');
      const copy = createNode('button', 'activity-copy');
      copy.type = 'button';
      copy.dataset.openBook = book.id;
      copy.appendChild(createNode('strong', '', book.title));
      copy.appendChild(createNode(
        'span',
        '',
        diagnosticText(record) + ' · ' + relativeTime(record.updatedAt)
      ));
      item.appendChild(copy);
      item.appendChild(statusBadge(record, null, 'activity-status'));
      return item;
    }));
  }

  function dockRecord() {
    for (let index = 0; index < state.queue.length; index += 1) {
      const record = state.books[state.queue[index]];
      if (record) {
        return record;
      }
    }
    const attention = sortedRecords().find(function (record) {
      return ATTENTION_STATES.has(record.status);
    });
    if (attention) {
      return attention;
    }
    if (state.selectedBookId && state.books[state.selectedBookId]) {
      return state.books[state.selectedBookId];
    }
    return sortedRecords().find(function (record) {
      return record.status === 'ready';
    }) || null;
  }

  function renderDock() {
    const dock = byId('download-dock');
    if (!dock) {
      return;
    }
    const record = dockRecord();
    dock.hidden = !record;
    if (!record) {
      return;
    }
    const book = bookById(record.id);
    const defaultAction = actionForRecord(record);
    const action = record.status === 'queued' || record.status === 'downloading'
      ? { action: 'pause', label: '暂停' }
      : defaultAction;
    setText('dock-title', book.title);
    setText('dock-status', STATUS_LABELS[record.status] + ' · ' + progressLabel(record));
    setText('dock-progress', Math.round(progressPercent(record)) + '%');
    const fill = byId('dock-fill');
    if (fill) {
      fill.style.width = progressPercent(record) + '%';
    }
    const actionButton = byId('dock-action');
    if (actionButton) {
      actionButton.hidden = false;
      actionButton.textContent = action.action === 'verify' ? '前往确认' : action.label;
      actionButton.dataset.bookAction = action.action;
      actionButton.dataset.bookId = book.id;
      actionButton.setAttribute('aria-label', action.label + '：' + book.title);
    }
  }

  function renderDetailFacts(book, source, record) {
    const facts = byId('detail-facts');
    if (!facts) {
      return;
    }
    facts.replaceChildren();
    facts.appendChild(createNode('span', '', book.sources.length + ' 个来源'));
    facts.appendChild(createNode('span', '', source.chapters + ' 章'));
    facts.appendChild(createNode('span', '', record ? '当前下载来源' : '将从此来源保存'));
  }

  function syncSelectedSource(book, record) {
    const source = record ? recordSource(record) : sourceById(book, currentSourceId) || firstSource(book);
    if (!source) {
      return null;
    }
    currentSourceId = source.id;
    document.querySelectorAll('#source-list input[name="source"]').forEach(function (input) {
      input.checked = input.value === source.id;
      input.disabled = Boolean(record);
    });
    const legend = document.querySelector('.source-fieldset legend span');
    if (legend) {
      legend.textContent = record
        ? '当前下载来源，继续时不会混用其他来源'
        : '正文按这个来源保存';
    }
    renderDetailFacts(book, source, record);
    return source;
  }

  function previewText(book, source, chapterNumber) {
    return [
      '这是《' + book.title + '》的第 ' + chapterNumber + ' 个示例章节，仅用于演示下载与导出。',
      '清晨的光落在桌角，纸页被风轻轻翻过。人物沿着安静的小路向前，记下沿途看到的变化。',
      '这一幕没有真实的站点来源，也不会把示例内容混入正式书架；它只负责展示阅读和文件导出的样子。'
    ];
  }

  function renderPreview(book, source) {
    const preview = byId('chapter-preview');
    if (!preview) {
      return;
    }
    preview.replaceChildren();
    preview.appendChild(createNode('h3', '', '第1章 示例章节1'));
    previewText(book, source, 1).forEach(function (paragraph) {
      preview.appendChild(createNode('p', '', paragraph));
    });
    const meta = createNode('p', 'source-preview-note', '正文样例来自「' + source.name + '」，导出时会使用同一个来源范围。');
    preview.appendChild(meta);
  }

  function renderSources(book, record, selectedSourceId) {
    const list = byId('source-list');
    if (!list) {
      return;
    }
    const locked = Boolean(record);
    const legend = document.querySelector('.source-fieldset legend span');
    if (legend) {
      legend.textContent = locked
        ? '当前下载来源，继续时不会混用其他来源'
        : '正文按这个来源保存';
    }
    list.replaceChildren();
    book.sources.forEach(function (source) {
      const option = createNode('label', 'source-option');
      const input = createNode('input', '');
      input.type = 'radio';
      input.name = 'source';
      input.value = source.id;
      input.checked = source.id === selectedSourceId;
      input.disabled = locked;
      const copy = createNode('span', 'source-copy');
      copy.appendChild(createNode('strong', 'source-name', source.name));
      copy.appendChild(createNode('span', 'source-description', source.description));
      copy.appendChild(createNode('span', 'source-badge', source.chapters + ' 章'));
      option.appendChild(input);
      option.appendChild(copy);
      list.appendChild(option);
    });
  }

  function renderDiagnostics(record) {
    const details = byId('detail-diagnostics');
    const content = byId('detail-diagnostics-content');
    if (!details || !content) {
      return;
    }
    details.hidden = !record;
    if (!record) {
      content.replaceChildren();
      return;
    }
    const source = recordSource(record);
    const pairs = [
      ['阶段', STAGE_LABELS[record.stage] || record.stage || '未知'],
      ['原因', REASON_LABELS[record.reason] || record.reason || '未记录'],
      ['来源', source.name + ' · ' + source.url],
      ['范围', progressLabel(record)],
      ['最近更新', relativeTime(record.updatedAt)]
    ];
    content.replaceChildren();
    pairs.forEach(function (pair) {
      content.appendChild(createNode('dt', '', pair[0]));
      content.appendChild(createNode('dd', '', pair[1]));
    });
  }

  function renderDetailDynamic() {
    if (!currentBookId) {
      return;
    }
    const book = bookById(currentBookId);
    const record = book && state.books[book.id] || null;
    if (!book) {
      closeBookDialog();
      return;
    }
    const activeSource = syncSelectedSource(book, record);
    const statusSection = byId('detail-state');
    if (statusSection) {
      statusSection.hidden = !record;
    }

    if (record) {
      const status = byId('detail-status');
      if (status) {
        status.textContent = STATUS_LABELS[record.status];
        status.className = 'status-badge';
        status.dataset.state = record.status;
      }
      setText('detail-progress', progressLabel(record));
      const fill = byId('detail-fill');
      if (fill) {
        fill.style.width = progressPercent(record) + '%';
      }
      setText('detail-activity', diagnosticText(record));
      if (activeSource) {
        renderPreview(book, activeSource);
        paintCover(byId('detail-cover'), book, activeSource.chapters + ' 章');
      }
    }

    const primary = byId('detail-primary');
    const action = actionForRecord(record);
    if (primary) {
      primary.hidden = false;
      primary.disabled = action.action === 'wait';
      primary.textContent = action.label;
      primary.dataset.bookAction = action.action;
      primary.dataset.bookId = book.id;
      if (action.action !== 'wait') {
        primary.appendChild(createIcon('arrow'));
      }
    }
    const exportButton = byId('detail-export');
    if (exportButton) {
      const canExportPartial = record && record.current > 0 && record.current < record.total
        && (record.status === 'partial' || record.status === 'paused');
      exportButton.hidden = !canExportPartial;
      exportButton.dataset.bookAction = 'export';
      exportButton.dataset.bookId = book.id;
    }
    const stop = byId('detail-stop');
    if (stop) {
      stop.hidden = !(record && ACTIVE_STATES.has(record.status));
      stop.dataset.bookAction = 'pause';
      stop.dataset.bookId = book.id;
    }
    const remove = byId('detail-remove');
    if (remove) {
      remove.hidden = !record;
      remove.dataset.bookAction = 'remove';
      remove.dataset.bookId = book.id;
    }
    setText('detail-footer-note', footerNote(record));
    renderDiagnostics(record);
  }

  function openBookDialog(bookId) {
    const book = bookById(bookId);
    if (!book) {
      return;
    }
    const dialog = byId('book-dialog');
    if (!dialog) {
      return;
    }
    currentBookId = book.id;
    const record = state.books[book.id] || null;
    currentSourceId = record ? record.sourceId : firstSource(book).id;
    state.selectedBookId = record ? book.id : state.selectedBookId;

    setText('detail-category', book.category + ' · 交互原型示例');
    setText('detail-title', book.title);
    setText('detail-author', book.author);
    setText('detail-description', book.description);
    const source = sourceById(book, currentSourceId) || firstSource(book);
    paintCover(byId('detail-cover'), book, source.chapters + ' 章');
    renderDetailFacts(book, source, record);
    renderSources(book, record, source.id);
    renderPreview(book, source);
    const details = byId('detail-diagnostics');
    if (details) {
      details.open = false;
    }
    renderDetailDynamic();
    if (!dialog.open) {
      dialog.showModal();
    }
    const focusTarget = byId('detail-primary');
    window.requestAnimationFrame(function () {
      if (focusTarget && focusTarget.offsetParent !== null) {
        focusTarget.focus();
      }
    });
    saveState();
    renderDock();
  }

  function closeBookDialog() {
    const dialog = byId('book-dialog');
    if (dialog && dialog.open) {
      dialog.close();
    }
    currentBookId = null;
    currentSourceId = null;
  }

  function setView(view, options) {
    if (!['search', 'library'].includes(view)) {
      return;
    }
    const changed = state.view !== view;
    state.view = view;
    if (!options || options.persist !== false) {
      saveState();
    }
    renderView();
    if (changed && (!options || options.focus !== false)) {
      const heading = view === 'library' ? byId('library-title') : byId('search-title');
      if (heading && document.activeElement && document.activeElement.matches('[data-view]')) {
        heading.focus({ preventScroll: false });
      }
    }
    if (!options || options.hash !== false) {
      if (window.location.hash !== '#' + view) {
        history.replaceState(null, '', '#' + view);
      }
    }
  }

  function clearSearchTimer() {
    if (searchTimer) {
      window.clearTimeout(searchTimer);
      searchTimer = null;
    }
  }

  function searchIdsFor(raw) {
    let url = null;
    try {
      const parsed = new URL(raw);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        url = parsed;
      }
    } catch (_) {
      url = null;
    }
    if (url) {
      return { kind: 'url', ids: ['a'] };
    }
    const needle = raw.toLocaleLowerCase('zh-CN');
    return {
      kind: 'text',
      ids: BOOK_DEFINITIONS.filter(function (book) {
        return book.title.toLocaleLowerCase('zh-CN').includes(needle)
          || book.author.toLocaleLowerCase('zh-CN').includes(needle);
      }).map(function (book) {
        return book.id;
      })
    };
  }

  function runSearch(rawValue, options) {
    const raw = String(rawValue || '').trim();
    const input = byId('search-input');
    if (input && (!options || options.updateInput !== false)) {
      input.value = raw;
    }
    clearSearchTimer();
    if (!raw) {
      resetSearch();
      return;
    }
    setView('search', { persist: false, focus: false });
    const matched = searchIdsFor(raw);
    const request = ++searchRequestId;
    state.query = raw;
    state.search = { kind: matched.kind, ids: matched.ids, ready: false };
    saveState();
    setSearchResultState();

    searchTimer = window.setTimeout(function () {
      if (request !== searchRequestId) {
        return;
      }
      state.search.ready = true;
      saveState();
      setSearchResultState();
      announce('示例搜索完成，找到 ' + state.search.ids.length + ' 本示例书籍。');
    }, options && options.delay === false ? 0 : SEARCH_DELAY_MS);
  }

  function resetSearch() {
    clearSearchTimer();
    searchRequestId += 1;
    state.query = '';
    state.search = { kind: '', ids: [], ready: true };
    const input = byId('search-input');
    if (input) {
      input.value = '';
    }
    saveState();
    setSearchResultState();
    setView('search', { persist: false });
    if (input) {
      input.focus();
    }
    announce('已回到找书首页。');
  }

  function renderAll() {
    renderView();
    renderCounts();
    renderHome();
    setSearchResultState();
    renderLibrary();
    updateProgressLabels();
    renderActivity();
    renderDock();
    renderDetailDynamic();
  }

  function addAndDownload(bookId, sourceId) {
    if (state.books[bookId]) {
      renderDetailDynamic();
      toast('这本书已在书架，不会重置已有进度。');
      return;
    }
    const book = bookById(bookId);
    const source = sourceById(book, sourceId) || firstSource(book);
    if (!book || !source) {
      toast('请选择一个示例来源。');
      return;
    }
    const now = Date.now();
    const record = makeRecord(book, source, {
      scenario: book.id === 'a' ? state.scenario : 'normal',
      queuedAt: now,
      chapterClockAt: now,
      updatedAt: now
    });
    state.books[book.id] = record;
    state.queue.push(book.id);
    state.selectedBookId = book.id;
    currentSourceId = source.id;
    saveState();
    renderAll();
    toast('《' + book.title + '》已加入演示书架和下载队列。');
    announce('已加入书架并开始示例下载。');
  }

  function pauseBook(bookId) {
    const record = state.books[bookId];
    if (!record || !ACTIVE_STATES.has(record.status)) {
      return;
    }
    record.status = 'paused';
    record.stage = 'paused';
    record.reason = 'user_paused';
    record.updatedAt = Date.now();
    state.queue = state.queue.filter(function (id) { return id !== bookId; });
    saveState();
    renderAll();
    toast('已暂停；已取得的示例内容会保留。');
    announce('下载已暂停。');
  }

  function continueBook(bookId) {
    const record = state.books[bookId];
    if (!record || !ATTENTION_STATES.has(record.status)) {
      return;
    }
    if (record.status === 'partial') {
      record.partialStopHandled = true;
    }
    record.status = 'queued';
    record.stage = 'queued';
    record.reason = 'continued';
    record.queuedAt = Date.now();
    record.chapterClockAt = Date.now();
    record.updatedAt = Date.now();
    if (!state.queue.includes(bookId)) {
      state.queue.push(bookId);
    }
    saveState();
    renderAll();
    toast(record.current ? '已继续下载剩余章节。' : '已重新加入下载队列。');
    announce('已继续下载。');
  }

  function openVerification(bookId) {
    const record = state.books[bookId];
    if (!record || record.status !== 'needs_action') {
      toast('这本书当前不需要人工确认。');
      return;
    }
    const opened = window.open(
      'verification.html?book=' + encodeURIComponent(bookId),
      'shuye-renewal-verification-' + bookId
    );
    if (!opened) {
      toast('浏览器阻止了新窗口，请允许此页面打开确认窗口。');
      return;
    }
    opened.focus();
    announce('已打开示例确认页面，主页面不会离开当前位置。');
  }

  function chapterBlock(book, source, chapterNumber) {
    return '第' + chapterNumber + '章 示例章节' + chapterNumber
      + '\n\n'
      + previewText(book, source, chapterNumber).join('\n\n');
  }

  function exportBook(bookId) {
    const record = state.books[bookId];
    const book = bookById(bookId);
    if (!record || !book || record.current <= 0) {
      toast('当前还没有可导出的示例内容。');
      return;
    }
    const source = recordSource(record);
    const partial = record.current < record.total;
    const generated = versionedTxt(bookId);
    const blob = new Blob([generated.text], { type: 'text/plain;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href;
    link.download = generated.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () {
      URL.revokeObjectURL(href);
    }, 1000);
    toast(partial ? '已导出当前示例部分内容。' : '示例 TXT 已导出。');
    announce('示例 TXT 导出已开始。');
  }

  function versionedTxt(bookId) {
    const record = state.books[bookId];
    const book = bookById(bookId);
    if (!record || !book || record.current <= 0) {
      return null;
    }
    const source = recordSource(record);
    const partial = record.current < record.total;
    const blocks = [];
    for (let number = 1; number <= record.current; number += 1) {
      blocks.push(chapterBlock(book, source, number));
    }
    return {
      filename: book.title + '-示例-' + source.name + (partial ? '-部分' : '') + '.txt',
      text: [
        '交互原型示例内容，非真实站点下载',
        '书名：' + book.title,
        '来源：' + source.name + ' · ' + source.url,
        '范围：' + record.current + '/' + record.total + ' 章',
        '',
        '',
        blocks.join('\n\n')
      ].join('\n')
    };
  }

  function openRemoveDialog(bookId) {
    const record = state.books[bookId];
    const book = bookById(bookId);
    if (!record || !book) {
      return;
    }
    removeBookId = bookId;
    setText('remove-title', '移出《' + book.title + '》？');
    const bookDialog = byId('book-dialog');
    if (bookDialog && bookDialog.open) {
      bookDialog.close();
    }
    currentBookId = null;
    const dialog = byId('remove-dialog');
    if (dialog && !dialog.open) {
      dialog.showModal();
    }
  }

  function removeBook(bookId) {
    const record = state.books[bookId];
    if (!record) {
      return;
    }
    state.queue = state.queue.filter(function (id) { return id !== bookId; });
    delete state.books[bookId];
    if (state.selectedBookId === bookId) {
      state.selectedBookId = null;
    }
    saveState();
    renderAll();
    const dialog = byId('remove-dialog');
    if (dialog && dialog.open) {
      dialog.close();
    }
    removeBookId = null;
    toast('已从演示书架移出，未触及真实文件。');
    announce('已从演示书架移出书籍。');
  }

  function handleBookAction(action, bookId) {
    if (!action || !bookId) {
      return;
    }
    if (action === 'open') {
      openBookDialog(bookId);
    } else if (action === 'start') {
      addAndDownload(bookId, currentSourceId);
    } else if (action === 'pause') {
      pauseBook(bookId);
    } else if (action === 'continue') {
      continueBook(bookId);
    } else if (action === 'verify') {
      openVerification(bookId);
    } else if (action === 'export') {
      exportBook(bookId);
    } else if (action === 'remove') {
      openRemoveDialog(bookId);
    }
  }

  function toast(message) {
    const region = byId('toast-region');
    if (!region) {
      return;
    }
    while (region.children.length >= 2) {
      region.firstElementChild.remove();
    }
    const item = createNode('div', 'toast', message);
    region.appendChild(item);
    window.setTimeout(function () {
      item.remove();
    }, 4200);
  }

  function announce(message) {
    const status = byId('app-status');
    if (!status) {
      return;
    }
    status.textContent = '';
    window.setTimeout(function () {
      status.textContent = message;
    }, 20);
  }

  function transitionMessage(record) {
    if (!record) {
      return '';
    }
    if (record.status === 'needs_action') {
      return '示例来源需要人工确认，下载已停在当前进度。';
    }
    if (record.status === 'partial') {
      return '示例下载停在部分结果，可先导出或继续。';
    }
    if (record.status === 'ready') {
      return '《' + bookById(record.id).title + '》的示例内容已就绪。';
    }
    return '';
  }

  function simulationTick(silent) {
    let changed = false;
    let transition = null;
    const now = Date.now();
    const validQueue = state.queue.filter(function (id) {
      const record = state.books[id];
      return record && ACTIVE_STATES.has(record.status);
    });
    if (validQueue.length !== state.queue.length) {
      state.queue = validQueue;
      changed = true;
    }
    if (!state.queue.length) {
      if (changed) {
        saveState();
      }
      return transition;
    }

    const id = state.queue[0];
    const record = state.books[id];
    if (!record) {
      return transition;
    }
    if (record.status === 'queued') {
      record.status = 'downloading';
      record.stage = 'download';
      record.reason = 'downloading';
      record.startedAt = record.startedAt || now;
      record.chapterClockAt = now;
      record.updatedAt = now;
      changed = true;
    }
    if (record.status !== 'downloading') {
      if (changed) {
        saveState();
      }
      return transition;
    }

    const verifyAt = record.scenario === 'verification' && !record.verificationCompleted
      ? Math.max(1, Math.ceil(record.total * 0.15))
      : record.total;
    const partialAt = record.scenario === 'partial' && !record.partialStopHandled
      ? Math.max(1, Math.round(record.total * 0.75))
      : record.total;
    const stopAt = Math.min(record.total, verifyAt, partialAt);

    if (record.current >= stopAt) {
      if (record.scenario === 'verification' && !record.verificationCompleted && stopAt === verifyAt) {
        record.status = 'needs_action';
        record.stage = 'awaiting_confirmation';
        record.reason = 'manual_verification';
        transition = 'needs_action';
      } else if (record.scenario === 'partial' && !record.partialStopHandled && stopAt === partialAt) {
        record.status = 'partial';
        record.stage = 'partial';
        record.reason = 'demo_partial_stop';
        state.queue.shift();
        transition = 'partial';
      }
      record.updatedAt = now;
      saveState();
      if (!silent && transition) {
        toast(transitionMessage(record));
      }
      return transition;
    }

    let clock = numeric(record.chapterClockAt, now);
    let due = clock + CHAPTER_MS;
    while (record.current < stopAt && now >= due) {
      record.current += 1;
      record.chapterClockAt = due;
      due += CHAPTER_MS;
      changed = true;
    }

    if (record.current >= stopAt) {
      if (record.scenario === 'verification' && !record.verificationCompleted && stopAt === verifyAt) {
        record.status = 'needs_action';
        record.stage = 'awaiting_confirmation';
        record.reason = 'manual_verification';
        transition = 'needs_action';
      } else if (record.scenario === 'partial' && !record.partialStopHandled && stopAt === partialAt) {
        record.status = 'partial';
        record.stage = 'partial';
        record.reason = 'demo_partial_stop';
        state.queue.shift();
        transition = 'partial';
      } else if (record.current >= record.total) {
        record.status = 'ready';
        record.stage = 'complete';
        record.reason = 'complete';
        record.completedAt = now;
        state.queue.shift();
        transition = 'ready';
      }
    }

    if (changed || transition) {
      record.updatedAt = now;
      if (!transition && record.status === 'downloading') {
        record.stage = 'download';
        record.reason = 'downloading';
      }
      saveState();
    }
    if (!silent && transition) {
      toast(transitionMessage(record));
    }
    return transition;
  }

  let state = loadState();
  let currentBookId = null;
  let currentSourceId = null;
  let removeBookId = null;
  let searchTimer = null;
  let searchRequestId = 0;

  function initVerificationPage() {
    const params = new URLSearchParams(window.location.search);
    const requestedId = params.get('book') || '';
    let verificationState = loadState();
    let lastRaw = safeRead(STORAGE_KEY);
    let bookId = requestedId;
    if (!bookId) {
      bookId = Object.keys(verificationState.books).find(function (id) {
        return verificationState.books[id].status === 'needs_action';
      }) || '';
    }
    const book = bookById(bookId);
    const status = byId('verification-status');
    const done = byId('verification-done');
    const back = byId('verification-back');

    function currentRecord() {
      return verificationState.books[bookId] || null;
    }

    function renderHelper() {
      const record = currentRecord();
      if (!book) {
        setText('verification-book', '没有找到对应的示例书籍。');
        if (done) {
          done.disabled = true;
        }
        if (status) {
          status.textContent = '请回到书页重新打开确认入口。';
        }
        return;
      }
      setText('verification-book', '当前书籍：' + book.title + '。' + (record
        ? '状态：' + STATUS_LABELS[record.status] + '，' + progressLabel(record) + '。'
        : '当前不在演示书架。'));
      if (back) {
        back.href = 'index.html#' + (verificationState.view || 'search');
      }
      if (done) {
        done.disabled = !record || record.status !== 'needs_action';
      }
      if (status) {
        if (record && record.status === 'needs_action') {
          status.textContent = '等待你在这页完成示例确认。';
        } else if (record && record.verificationCompleted) {
          status.textContent = '这本书已经确认过，不会重复触发。可以回到书页继续查看。';
        } else {
          status.textContent = record
            ? '当前状态不需要再次确认。'
            : '没有待确认的示例下载。';
        }
      }
    }

    renderHelper();
    if (done) {
      done.addEventListener('click', function () {
        const record = currentRecord();
        if (!record || record.status !== 'needs_action') {
          renderHelper();
          return;
        }
        const now = Date.now();
        record.status = 'downloading';
        record.stage = 'download';
        record.reason = 'verification_completed';
        record.verificationCompleted = true;
        record.chapterClockAt = now;
        record.updatedAt = now;
        if (!verificationState.queue.includes(bookId)) {
          verificationState.queue.push(bookId);
        }
        if (safeWrite(STORAGE_KEY, JSON.stringify(verificationState))) {
          if (status) {
            status.textContent = '确认已记录。回到书页后，下载会从当前章节继续。';
          }
          done.disabled = true;
        } else if (status) {
          status.textContent = '本地存储不可用，确认没有保存。';
        }
      });
    }
    window.addEventListener('storage', function (event) {
      if (event.key !== STORAGE_KEY) {
        return;
      }
      lastRaw = event.newValue;
      if (!event.newValue) {
        verificationState = createSeedState();
        renderHelper();
        return;
      }
      try {
        verificationState = normalizeState(JSON.parse(event.newValue));
        renderHelper();
      } catch (_) {
        /* Ignore an unrelated or malformed write. */
      }
    });
    window.setInterval(function () {
      const currentRaw = safeRead(STORAGE_KEY);
      if (currentRaw === lastRaw) {
        return;
      }
      lastRaw = currentRaw;
      verificationState = currentRaw ? normalizeState(JSON.parse(currentRaw)) : createSeedState();
      renderHelper();
    }, 800);
  }

  function initMainPage() {
    const hashView = window.location.hash.replace(/^#/, '');
    if (hashView === 'search' || hashView === 'library') {
      state.view = hashView;
    }
    if (!window.location.hash) {
      history.replaceState(null, '', '#' + state.view);
    }

    const scenarioControl = byId('demo-scenario');
    if (scenarioControl) {
      scenarioControl.value = state.scenario;
    }
    const input = byId('search-input');
    if (input) {
      input.value = state.query;
    }

    simulationTick(true);
    renderAll();
    if (state.query && !state.search.ready) {
      runSearch(state.query, { updateInput: false, delay: false });
    }

    window.addEventListener('storage', function (event) {
      if (event.key !== STORAGE_KEY) {
        return;
      }
      if (!event.newValue) {
        state = createSeedState();
      } else {
        try {
          state = normalizeState(JSON.parse(event.newValue));
        } catch (_) {
          return;
        }
      }
      const scenarioControl = byId('demo-scenario');
      if (scenarioControl) {
        scenarioControl.value = state.scenario;
      }
      renderAll();
      if (currentBookId) {
        openBookDialog(currentBookId);
      }
    });

    window.addEventListener('hashchange', function () {
      const view = window.location.hash.replace(/^#/, '');
      if (view === 'search' || view === 'library') {
        state.view = view;
        saveState();
        renderView();
      }
    });

    document.addEventListener('click', function (event) {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) {
        return;
      }
      const actionTarget = target.closest('[data-book-action]');
      if (actionTarget) {
        event.preventDefault();
        if (actionTarget.closest('#detail-actions, .book-dialog-footer')) {
          const matchingAction = actionTarget.dataset.bookAction;
          if (matchingAction === 'start' && state.books[actionTarget.dataset.bookId]) {
            renderDetailDynamic();
            toast('这本书已在书架，不会重置已有进度。');
            return;
          }
        }
        handleBookAction(actionTarget.dataset.bookAction, actionTarget.dataset.bookId);
        return;
      }
      const bookTarget = target.closest('[data-open-book]');
      if (bookTarget) {
        event.preventDefault();
        openBookDialog(bookTarget.dataset.openBook);
        return;
      }
      const viewTarget = target.closest('[data-view]');
      if (viewTarget) {
        event.preventDefault();
        setView(viewTarget.dataset.view);
        return;
      }
      const queryTarget = target.closest('[data-query]');
      if (queryTarget) {
        event.preventDefault();
        runSearch(queryTarget.dataset.query);
        return;
      }
      const filterTarget = target.closest('[data-filter]');
      if (filterTarget) {
        state.filter = filterTarget.dataset.filter;
        saveState();
        renderLibrary();
      }
    });

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') {
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.matches('button, input, textarea, select, a')) {
        return;
      }
      const card = target.closest('[data-open-book]');
      if (card && target === card) {
        event.preventDefault();
        openBookDialog(card.dataset.openBook);
      }
    });

    const searchForm = byId('search-form');
    if (searchForm) {
      searchForm.addEventListener('submit', function (event) {
        event.preventDefault();
        runSearch(input ? input.value : '');
      });
    }
    const searchClear = byId('search-clear');
    if (searchClear) {
      searchClear.addEventListener('click', resetSearch);
    }

    const sourceList = byId('source-list');
    if (sourceList) {
      sourceList.addEventListener('change', function (event) {
        if (!(event.target instanceof HTMLInputElement) || event.target.name !== 'source') {
          return;
        }
        if (!currentBookId || state.books[currentBookId]) {
          return;
        }
        const book = bookById(currentBookId);
        const source = sourceById(book, event.target.value);
        if (!book || !source) {
          return;
        }
        currentSourceId = source.id;
        paintCover(byId('detail-cover'), book, source.chapters + ' 章');
        renderDetailFacts(book, source, null);
        renderPreview(book, source);
      });
    }

    if (scenarioControl) {
      scenarioControl.addEventListener('change', function () {
        if (!['normal', 'verification', 'partial'].includes(scenarioControl.value)) {
          return;
        }
        state.scenario = scenarioControl.value;
        saveState();
        toast('下载场景已切换，仅影响下一次新开始的示例书籍 A。');
      });
    }

    const reset = byId('demo-reset');
    if (reset) {
      reset.addEventListener('click', function () {
        if (!window.confirm('只清除这个交互原型的本地进度，并恢复示例书架？不会影响其他本地数据。')) {
          return;
        }
        clearSearchTimer();
        searchRequestId += 1;
        safeRemove(STORAGE_KEY);
        state = createSeedState();
        const scenarioControl = byId('demo-scenario');
        if (scenarioControl) {
          scenarioControl.value = state.scenario;
        }
        const searchInput = byId('search-input');
        if (searchInput) {
          searchInput.value = '';
        }
        ['book-dialog', 'remove-dialog', 'about-dialog'].forEach(function (id) {
          const dialog = byId(id);
          if (dialog && dialog.open) {
            dialog.close();
          }
        });
        currentBookId = null;
        currentSourceId = null;
        removeBookId = null;
        setView('search', { persist: false });
        saveState();
        renderAll();
        toast('演示数据已恢复。');
        announce('演示数据已重置。');
      });
    }

    const about = byId('demo-about');
    if (about) {
      about.addEventListener('click', function () {
        const dialog = byId('about-dialog');
        if (dialog && !dialog.open) {
          dialog.showModal();
        }
      });
    }

    const activityToggle = byId('activity-toggle');
    const activityPanel = byId('activity-panel');
    if (activityToggle && activityPanel) {
      activityToggle.addEventListener('click', function () {
        const opening = activityPanel.hidden;
        activityPanel.hidden = !opening;
        activityToggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
        if (opening) {
          renderActivity();
        }
      });
    }
    const activityClose = byId('activity-close');
    if (activityClose && activityPanel && activityToggle) {
      activityClose.addEventListener('click', function () {
        activityPanel.hidden = true;
        activityToggle.setAttribute('aria-expanded', 'false');
        activityToggle.focus();
      });
    }

    const dockOpen = byId('dock-open');
    if (dockOpen) {
      dockOpen.addEventListener('click', function () {
        const record = dockRecord();
        if (record) {
          openBookDialog(record.id);
        }
      });
    }

    const bookClose = byId('book-close');
    if (bookClose) {
      bookClose.addEventListener('click', closeBookDialog);
    }
    const dialog = byId('book-dialog');
    if (dialog) {
      dialog.addEventListener('close', function () {
        currentBookId = null;
        currentSourceId = null;
      });
    }

    const removeDialog = byId('remove-dialog');
    const removeCancel = byId('remove-cancel');
    const removeConfirm = byId('remove-confirm');
    if (removeCancel && removeDialog) {
      removeCancel.addEventListener('click', function () {
        removeDialog.close();
      });
    }
    if (removeConfirm) {
      removeConfirm.addEventListener('click', function () {
        if (removeBookId) {
          removeBook(removeBookId);
        }
      });
    }
    if (removeDialog) {
      removeDialog.addEventListener('close', function () {
        removeBookId = null;
      });
    }

    window.setInterval(function () {
      const transition = simulationTick(false);
      renderAll();
      if (transition) {
        announce(transitionMessage(state.books[state.queue[0]] || null) || '下载状态已更新。');
      }
    }, 350);
  }

  if (document.body && document.body.dataset.page === 'verification') {
    initVerificationPage();
  } else {
    window.bookDownloaderDemo = {
      getState: function () {
        return JSON.parse(JSON.stringify(state));
      },
      exportText: versionedTxt
    };
    initMainPage();
  }
})();
