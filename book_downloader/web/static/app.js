(function () {
  'use strict';

  const POLL_INTERVAL = 1000;
  const PHASE_LABELS = {queued:'排队',search:'搜索',discover:'识别目录',download:'下载',fill:'补齐',merge:'生成 TXT'};
  const ACTIVE_STATUSES = new Set(['queued', 'running', 'needs_browser', 'waiting_verification', 'stopping']);
  const CANCELABLE_STATUSES = new Set(['queued', 'running', 'needs_browser', 'waiting_verification']);
  const RETRYABLE_STATUSES = new Set(['partial', 'blocked', 'failed', 'interrupted', 'cancelled', 'paused']);
  const SETTINGS_FIELDS = [
    'output_dir',
    'delay',
    'timeout',
    'retries',
    'retry_rounds',
    'retry_delay',
    'browser_executable',
    'browser_mode',
    'verification_timeout'
  ];

  const STATUS_META = {
    queued: { label: '排队中', tone: 'info' },
    running: { label: '进行中', tone: 'active' },
    needs_browser: { label: '需要浏览器', tone: 'warn' },
    waiting_verification: { label: '等待人工验证', tone: 'warn' },
    stopping: { label: '正在停止', tone: 'warn' },
    completed: { label: '已完成', tone: 'ok' },
    partial: { label: '部分完成', tone: 'warn' },
    blocked: { label: '被拦截', tone: 'danger' },
    failed: { label: '失败', tone: 'danger' },
    cancelled: { label: '已取消', tone: 'muted' },
    interrupted: { label: '已中断', tone: 'warn' },
    paused: { label: '已暂停', tone: 'muted' }
  };

  const KIND_LABELS = {
    search: '搜索',
    download: '下载',
    update: '更新目录',
    fill_gaps: '补齐缺口',
    merge: '生成TXT'
  };

  const VIEW_META = {
    books: {
      title: '书架',
      subtitle: '管理已缓存的书籍与合并输出。'
    },
    add: {
      title: '添加下载',
      subtitle: '搜索站点目录，或通过链接创建下载任务。'
    },
    tasks: {
      title: '任务中心',
      subtitle: '查看进度、缺章、告警与运行结果。'
    },
    settings: {
      title: '设置',
      subtitle: '输出目录、访问参数与浏览器路径。'
    }
  };

  const state = {
    view: 'books',
    connected: null,
    browserStatus: null,
    browserSig: '',
    shutdown: false,
    books: [],
    booksLoaded: false,
    booksError: '',
    booksBusy: false,
    booksSig: '',
    tasks: [],
    tasksLoaded: false,
    tasksError: '',
    tasksSig: '',
    selectedTaskId: null,
    detail: null,
    detailSig: '',
    searchTaskId: null,
    searchSig: '',
    settings: null,
    settingsLoaded: false,
    settingsDirty: false,
    pending: new Set(),
    openDetails: new Set(),
    pollTimer: null,
    pollBusy: false
  };

  let pollSoonTimer = null;

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.payload = payload;
    }
  }

  function $id(id) {
    return document.getElementById(id);
  }

  function h(tag, options, children) {
    const node = document.createElement(tag);
    const opts = options || {};
    if (opts.className) {
      node.className = opts.className;
    }
    if (opts.text !== undefined && opts.text !== null) {
      node.textContent = String(opts.text);
    }
    if (opts.attrs) {
      Object.keys(opts.attrs).forEach(function (name) {
        const value = opts.attrs[name];
        if (value === undefined || value === null || value === false) {
          return;
        }
        node.setAttribute(name, value === true ? '' : String(value));
      });
    }
    if (children !== undefined && children !== null) {
      appendChildren(node, children);
    }
    return node;
  }

  function appendChildren(node, children) {
    if (children === undefined || children === null) {
      return;
    }
    if (Array.isArray(children)) {
      children.forEach(function (child) {
        appendChildren(node, child);
      });
      return;
    }
    if (children instanceof Node) {
      node.appendChild(children);
      return;
    }
    node.appendChild(document.createTextNode(String(children)));
  }

  function sig(value) {
    try {
      return JSON.stringify(value);
    } catch (error) {
      return String(value);
    }
  }

  function safeJson(value) {
    try {
      const text = JSON.stringify(value, null, 2);
      return typeof text === 'string' ? text : String(value);
    } catch (error) {
      return String(value);
    }
  }

  function displayText(value) {
    if (value === undefined || value === null) {
      return '';
    }
    if (typeof value === 'string') {
      return value;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    return safeJson(value);
  }

  function warningText(value) {
    if (value === undefined || value === null) {
      return '';
    }
    if (typeof value === 'string') {
      return value;
    }
    if (typeof value === 'object') {
      if (typeof value.message === 'string') {
        return value.message;
      }
      if (typeof value.detail === 'string') {
        return value.detail;
      }
      if (typeof value.text === 'string') {
        return value.text;
      }
      return safeJson(value);
    }
    return String(value);
  }

  function num(value) {
    if (typeof value === 'number') {
      return Number.isFinite(value) ? value : null;
    }
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
  }

  function countText(value) {
    const parsed = num(value);
    return parsed === null ? '未知' : String(parsed);
  }

  function resultCount(result, key) {
    if (!result || typeof result !== 'object') {
      return null;
    }
    const value = result[key];
    if (Array.isArray(value)) {
      return value.length;
    }
    return num(value);
  }

  function timeText(value) {
    if (!value) {
      return '—';
    }
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    try {
      return new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      }).format(date);
    } catch (error) {
      return date.toLocaleString();
    }
  }

  function statusInfo(status) {
    const key = typeof status === 'string' ? status : '';
    const meta = STATUS_META[key];
    return {
      key: key,
      label: meta ? meta.label : (key || '未知'),
      tone: meta ? meta.tone : 'muted'
    };
  }

  function kindLabel(kind) {
    return KIND_LABELS[kind] || (kind ? String(kind) : '任务');
  }

  function statusPill(status) {
    const info = statusInfo(status);
    return h('span', { className: 'pill pill-' + info.tone, text: info.label });
  }

  function taskSubject(task) {
    if (!task) {
      return '任务';
    }
    if (typeof task.title === 'string' && task.title.trim()) {
      return task.title.trim();
    }
    if (typeof task.query === 'string' && task.query.trim()) {
      return task.query.trim();
    }
    if (typeof task.url === 'string' && task.url.trim()) {
      return task.url.trim();
    }
    if (task.id !== undefined && task.id !== null) {
      return '任务 ' + String(task.id);
    }
    return '未命名任务';
  }

  function missingEntries(value) {
    if (!Array.isArray(value)) {
      return [];
    }
    return value
      .map(function (item) {
        if (item && typeof item === 'object') {
          if (typeof item.title === 'string') {
            return item.title;
          }
          if (typeof item.url === 'string') {
            return item.url;
          }
          if (typeof item.label === 'string') {
            return item.label;
          }
          return safeJson(item);
        }
        return displayText(item);
      })
      .filter(function (text) {
        return text !== '';
      });
  }

  function collectWarnings(task) {
    const warnings = [];
    const seen = new Set();
    function push(value) {
      const text = warningText(value).trim();
      if (text && !seen.has(text)) {
        seen.add(text);
        warnings.push(text);
      }
    }
    if (task && Array.isArray(task.warnings)) {
      task.warnings.forEach(push);
    }
    if (task && task.result && Array.isArray(task.result.warnings)) {
      task.result.warnings.forEach(push);
    }
    return warnings;
  }

  function escapeSelectorValue(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, '\\$&');
  }

  function withPreservedRender(render) {
    const scrollX = window.scrollX || 0;
    const scrollY = window.scrollY || 0;
    const active = document.activeElement;
    let focusKey = null;
    let selection = null;

    if (active && active !== document.body && typeof active.getAttribute === 'function') {
      focusKey = active.getAttribute('data-focus-key');
      if (focusKey && typeof active.selectionStart === 'number') {
        try {
          selection = [active.selectionStart, active.selectionEnd];
        } catch (error) {
          selection = null;
        }
      }
    }

    render();

    if (window.scrollX !== scrollX || window.scrollY !== scrollY) {
      window.scrollTo(scrollX, scrollY);
    }

    if (!focusKey) {
      return;
    }

    const next = document.querySelector('[data-focus-key="' + escapeSelectorValue(focusKey) + '"]');
    if (!next || typeof next.focus !== 'function' || next === document.activeElement) {
      return;
    }
    next.focus({ preventScroll: true });
    if (selection && typeof next.setSelectionRange === 'function') {
      try {
        next.setSelectionRange(selection[0], selection[1]);
      } catch (error) {
        /* ignore */
      }
    }
  }

  function announce(message) {
    const region = $id('app-status');
    if (region) {
      region.textContent = message;
    }
  }

  function showToast(message, tone) {
    const region = $id('toast-region');
    if (!region) {
      return;
    }
    const text = displayText(message).trim();
    if (!text) {
      return;
    }
    while (region.children.length >= 4) {
      region.removeChild(region.firstElementChild);
    }
    const toast = h('div', {
      className: 'toast toast-' + (tone || 'info'),
      attrs: { role: 'status' },
      text: text
    });
    region.appendChild(toast);
    window.setTimeout(function () {
      toast.classList.add('is-leaving');
      window.setTimeout(function () {
        toast.remove();
      }, 200);
    }, 5200);
  }

  function errorMessage(payload, status) {
    const detail = payload && payload.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim();
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map(function (item) {
          if (item && typeof item === 'object') {
            const location = Array.isArray(item.loc)
              ? item.loc.filter(function (part) {
                  return part !== 'body' && part !== 'path' && part !== 'query';
                }).join('.')
              : '';
            const message = typeof item.msg === 'string'
              ? item.msg
              : (typeof item.message === 'string' ? item.message : '');
            if (location && message) {
              return location + '：' + message;
            }
            if (message) {
              return message;
            }
            return safeJson(item);
          }
          return displayText(item);
        })
        .filter(function (text) {
          return text !== '';
        });
      if (parts.length) {
        return parts.join('；');
      }
    }
    const labels = {
      400: '请求参数有误。',
      404: '请求的资源不存在。',
      409: '当前状态不允许该操作。',
      422: '提交的数据未通过校验。',
      500: '后台处理请求时出错。'
    };
    return labels[status] || ('请求失败（HTTP ' + status + '）。');
  }

  async function api(path, options) {
    const opts = options || {};
    const method = opts.method || 'GET';
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') headers['Content-Type'] = 'application/json';
    const request = {
      method: method,
      headers: headers,
      cache: 'no-store',
      credentials: 'same-origin'
    };
    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      request.body = JSON.stringify(opts.body);
    }

    let response;
    try {
      response = await fetch(path, request);
    } catch (error) {
      throw new ApiError('无法连接后台服务。', 0, null);
    }

    const noticeHeader = response.headers.get('X-Book-Downloader-Warnings');
    if (noticeHeader !== null) {
      const banner = $id('storage-warning');
      try {
        const notices = JSON.parse(noticeHeader);
        banner.textContent = notices.join('；');
        banner.hidden = notices.length === 0;
      } catch (error) { /* keep the last useful notice */ }
    }

    let payload = null;
    let raw = '';
    try {
      raw = await response.text();
    } catch (error) {
      raw = '';
    }
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (error) {
        payload = null;
      }
    }

    if (!response.ok) {
      throw new ApiError(errorMessage(payload, response.status), response.status, payload);
    }
    return payload;
  }

  function taskPath(id, suffix) {
    return '/api/tasks/' + encodeURIComponent(String(id)) + (suffix || '');
  }

  function bookFilePath(id) {
    return '/api/books/' + encodeURIComponent(String(id)) + '/file';
  }

  function taskFilePath(id) {
    return '/api/tasks/' + encodeURIComponent(String(id)) + '/file';
  }

  /* ---------- Connection status ---------- */

  function renderConnection() {
    const conn = $id('conn-status');
    const connText = $id('conn-text');
    const chip = $id('server-status');
    const banner = $id('connection-banner');
    const shutdownButton = $id('shutdown-btn');
    const shutdownPending = state.pending.has('shutdown');

    let label = '连接中…';
    let dataState = 'connecting';

    if (state.shutdown) {
      label = '后台已退出';
      dataState = 'offline';
    } else if (state.connected === true) {
      label = '服务已连接';
      dataState = 'online';
    } else if (state.connected === false) {
      label = '服务离线';
      dataState = 'offline';
    }

    if (connText) {
      connText.textContent = label;
    }
    if (conn) {
      conn.setAttribute('data-state', dataState);
    }
    if (chip) {
      chip.textContent = label;
      chip.setAttribute('data-state', dataState);
    }
    if (banner) {
      banner.hidden = !(state.connected === false && !state.shutdown);
    }
    if (shutdownButton) {
      shutdownButton.disabled = state.shutdown || shutdownPending;
      shutdownButton.textContent = state.shutdown ? '后台已退出' : '退出后台';
    }
  }

  function noteOffline() {
    if (state.shutdown) {
      return;
    }
    if (state.connected !== false) {
      state.connected = false;
      renderConnection();
      showToast('无法连接后台服务，正在自动重试。', 'error');
      announce('服务离线，正在自动重试。');
    } else {
      renderConnection();
    }
  }

  function noteOnline() {
    if (state.shutdown) {
      return;
    }
    if (state.connected !== true) {
      const wasOffline = state.connected === false;
      state.connected = true;
      renderConnection();
      if (wasOffline) {
        showToast('后台服务已恢复连接。', 'success');
        announce('服务已连接。');
      }
    }
  }

  function renderBrowserStatus() {
    const browser = state.browserStatus || {};
    const link = $id('browser-extension-status');
    if (!link) {
      return;
    }
    let text = '连接当前浏览器';
    let stateName = 'offline';
    if (browser.connected !== true) {
      text = '连接当前浏览器';
    } else if (browser.permissions_granted === null || browser.permissions_granted === undefined) {
      text = '浏览器已连接（建议更新扩展）';
      stateName = 'stale';
    } else if (browser.permissions_granted !== true) {
      text = '扩展已连接，待授权小说站点';
      stateName = 'unauthorized';
    } else if (browser.version !== '1.0.1') {
      text = '浏览器已连接（建议更新扩展）';
      stateName = 'stale';
    } else {
      text = '当前浏览器已连接';
      stateName = 'online';
    }
    link.textContent = text;
    link.dataset.state = stateName;
  }

  function reportError(error, fallback) {
    const message = error && typeof error.message === 'string' && error.message.trim()
      ? error.message.trim()
      : (fallback || '操作失败，请稍后重试。');
    if (error instanceof ApiError && error.status === 0) {
      noteOffline();
      return;
    }
    if (!(error instanceof ApiError)) {
      console.error(error);
    }
    showToast(message, 'error');
    announce(message);
  }

  async function runAction(key, button, action) {
    if (state.pending.has(key)) {
      return;
    }
    state.pending.add(key);
    if (button) {
      if (button.disabled) {
        button.dataset.baseDisabled = '1';
      }
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
    }
    try {
      return await action();
    } catch (error) {
      reportError(error);
      return undefined;
    } finally {
      state.pending.delete(key);
      if (button && button.isConnected) {
        button.removeAttribute('aria-busy');
        if (button.dataset.baseDisabled === '1' || state.shutdown) {
          button.disabled = true;
        } else {
          button.disabled = false;
        }
      }
    }
  }

  function pollSoon() {
    if (pollSoonTimer) {
      return;
    }
    pollSoonTimer = window.setTimeout(function () {
      pollSoonTimer = null;
      pollTasks();
    }, 250);
  }

  /* ---------- Polling ---------- */

  async function pollTasks() {
    if (state.shutdown || state.pollBusy) {
      return;
    }
    state.pollBusy = true;
    try {
      const data = await api('/api/tasks');
      if (!Array.isArray(data)) {
        throw new ApiError('任务列表格式异常。', 500, null);
      }
      noteOnline();
      state.tasksError = '';
      api('/api/browser/status').then(function (browser) {
        const next = browser && typeof browser === 'object' ? browser : {};
        const signature = sig(next);
        if (signature !== state.browserSig) {
          state.browserStatus = next;
          state.browserSig = signature;
          renderBrowserStatus();
          refreshBrowserDependentViews();
        }
      }).catch(function () {});

      const previous = new Map();
      state.tasks.forEach(function (task) {
        previous.set(String(task.id), task.status);
      });

      let completedNow = false;
      data.forEach(function (task) {
        const key = String(task.id);
        if (!ACTIVE_STATUSES.has(task.status) && previous.has(key) && previous.get(key) !== task.status) {
          completedNow = true;
        }
      });

      const knownTasks = new Map(state.tasks.map(function (task) { return [String(task.id), task]; }));
      state.tasks = data.map(function (task) {
        const known = knownTasks.get(String(task.id));
        return known && Number(known.updated_at) > Number(task.updated_at) ? known : task;
      });
      state.tasksLoaded = true;
      renderTasks();
      syncDetailFromTasks();
      syncSearchPanel();

      if (completedNow) {
        refreshBooks({ silent: true });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status !== 0) {
        noteOnline();
        setTasksError(error.message);
      } else {
        noteOffline();
        renderTasks();
      }
    } finally {
      state.pollBusy = false;
    }
  }

  function setTasksError(message) {
    if (!message) {
      return;
    }
    if (state.tasksError !== message) {
      state.tasksError = message;
      showToast(message, 'error');
      announce(message);
    }
    renderTasks();
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    if (pollSoonTimer) {
      window.clearTimeout(pollSoonTimer);
      pollSoonTimer = null;
    }
  }

  /* ---------- Bookshelf ---------- */

  async function refreshBooks(options) {
    const opts = options || {};
    if (state.shutdown || state.booksBusy) {
      return;
    }
    state.booksBusy = true;
    const status = $id('books-status');
    if (status) {
      status.textContent = state.booksLoaded ? '正在刷新书架…' : '正在加载书架…';
    }

    try {
      const data = await api('/api/books');
      if (!Array.isArray(data)) {
        throw new ApiError('书架数据格式异常。', 500, null);
      }
      noteOnline();
      state.books = data;
      state.booksLoaded = true;
      state.booksError = '';
      renderBooks();
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) {
        noteOffline();
      } else {
        noteOnline();
      }
      state.booksError = error && typeof error.message === 'string' && error.message.trim()
        ? error.message.trim()
        : '书架加载失败。';
      renderBooks();
      if (opts.manual && !(error instanceof ApiError && error.status === 0)) {
        showToast(state.booksError, 'error');
      }
    } finally {
      state.booksBusy = false;
      if (status) {
        status.textContent = '';
      }
    }
  }

  function renderBooks() {
    const loading = $id('books-loading');
    const empty = $id('books-empty');
    const wrap = $id('books-table-wrap');
    const errorBox = $id('books-error');
    const errorText = $id('books-error-text');
    const summary = $id('books-summary');
    const body = $id('books-body');
    const badge = $id('books-count');

    const books = state.books;
    if (!body || !loading || !empty || !wrap) {
      return;
    }

    if (summary) {
      summary.textContent = state.booksLoaded ? '共 ' + books.length + ' 本' : '';
    }
    if (errorBox) {
      errorBox.hidden = !state.booksError;
    }
    if (errorText) {
      errorText.textContent = state.booksError;
    }
    loading.hidden = state.booksLoaded || Boolean(state.booksError);
    empty.hidden = !(state.booksLoaded && books.length === 0 && !state.booksError);
    wrap.hidden = books.length === 0;

    if (badge) {
      if (state.booksLoaded && books.length > 0) {
        badge.hidden = false;
        badge.textContent = books.length > 99 ? '99+' : String(books.length);
        badge.title = '共 ' + books.length + ' 本';
      } else {
        badge.hidden = true;
      }
    }

    if (!state.booksLoaded && !state.booksError) {
      body.replaceChildren();
      state.booksSig = '';
      return;
    }

    const signature = sig(books);
    if (signature === state.booksSig) {
      return;
    }
    state.booksSig = signature;

    withPreservedRender(function () {
      body.replaceChildren.apply(body, books.map(bookRow));
    });
  }

  function bookRow(book) {
    const data = book && typeof book === 'object' ? book : {};
    const id = String(data.id);
    const hasCache = data.has_cache === true;
    const hasCatalogUrl = typeof data.catalog_url === 'string' && data.catalog_url.trim() !== '';
    const title = typeof data.title === 'string' && data.title.trim() ? data.title.trim() : '未知书名';
    const warning = warningText(data.warning);
    const actions = [];

    const updateButton = actionButton('更新目录', '', 'book:' + id + ':update', function (button) {
      createBookTask(data, 'update', button);
    }, {
      disabled: !hasCatalogUrl,
      title: hasCatalogUrl ? '重新下载完整目录并更新缓存索引。' : '缺少目录地址，无法更新。'
    });

    const fillButton = actionButton('补齐缺口', '', 'book:' + id + ':fill', function (button) {
      createBookTask(data, 'fill_gaps', button);
    }, {
      disabled: !hasCatalogUrl || !hasCache,
      title: !hasCache ? '尚无缓存，请先下载正文。' : (!hasCatalogUrl ? '缺少目录地址，无法补齐。' : '只抓取目录中存在但尚未缓存的章节。')
    });

    const mergeButton = actionButton('生成TXT', '', 'book:' + id + ':merge', function (button) {
      createBookTask(data, 'merge', button);
    }, {
      disabled: !hasCatalogUrl || !hasCache,
      title: !hasCache ? '尚无缓存，请先下载正文。' : (!hasCatalogUrl ? '缺少目录地址，无法合并。' : '按当前缓存重新生成合并 TXT。')
    });

    actions.push(updateButton, fillButton, mergeButton);

    if (data.file_available === true) {
      actions.push(fileLink(bookFilePath(id), '下载TXT', 'book:' + id + ':file'));
    } else {
      actions.push(fileLink('', '下载TXT', 'book:' + id + ':file', {
        disabled: true,
        title: '尚未生成可下载的 TXT 文件。'
      }));
    }

    actions.push(actionButton('删除', 'btn-danger', 'book:' + id + ':remove', function (button) {
      openRemoveBook(data, button);
    }));

    let lastTaskCell;
    const lastTask = data.last_task;
    if (lastTask && lastTask.id !== undefined && lastTask.id !== null) {
      const text = statusInfo(lastTask.status).label + ' · ' + timeText(lastTask.updated_at);
      lastTaskCell = h('button', {
        className: 'linkish',
        attrs: { type: 'button' },
        text: text
      });
      lastTaskCell.dataset.focusKey = 'book:' + id + ':last';
      lastTaskCell.addEventListener('click', function () {
        openTask(lastTask.id);
      });
    } else {
      lastTaskCell = h('span', { className: 'muted', text: '—' });
    }

    const titleCell = h('td', { className: 'cell-title' }, [
      h('div', { className: 'title-line' }, [
        h('strong', { text: title }),
        !hasCache ? h('span', { className: 'site-tag', text: '无缓存' }) : null
      ]),
      warning ? h('p', { className: 'row-warning', text: warning }) : null,
      !hasCache ? h('p', { className: 'row-note', text: '尚无正文缓存，请先下载。' }) : null
    ]);

    return h('tr', {}, [
      titleCell,
      h('td', {}, [h('span', { className: 'site-tag', text: data.site ? String(data.site) : '未知' })]),
      h('td', {}, [countCell(data.catalog_chapters)]),
      h('td', {}, [countCell(data.cached_chapters)]),
      h('td', {}, [
        num(data.missing_count) === null
          ? h('span', { className: 'muted', text: '未知' })
          : h('span', { className: 'num', text: countText(data.missing_count) })
      ]),
      h('td', { className: 'cell-time' }, [lastTaskCell]),
      h('td', { className: 'cell-actions' }, [h('div', { className: 'actions' }, actions)])
    ]);
  }

  function openRemoveBook(book, button) {
    return runAction('book:' + book.id + ':remove', button, async function () {
      const preview = await api('/api/books/' + encodeURIComponent(book.id) + '/removal');
      const dialog = $id('remove-book-dialog');
      const checkbox = $id('remove-book-files');
      const confirm = $id('remove-book-confirm');
      const cancel = $id('remove-book-cancel');
      const error = $id('remove-book-error');
      const pending = preview.pending_cleanup === true;
      $id('remove-book-name').textContent = preview.title;
      $id('remove-book-paths').textContent = preview.cache_dirs.concat(preview.files).join('\n') || '未识别到可清理的文件。';
      $id('remove-book-pending').hidden = !pending;
      dialog.querySelector('details').open = false;
      error.textContent = preview.cleanup_error || '';
      checkbox.checked = pending;
      checkbox.disabled = Boolean(preview.cleanup_error) || pending;
      confirm.textContent = pending ? '继续清理并移除' : '确认移除';
      confirm.disabled = pending && Boolean(preview.cleanup_error);
      cancel.disabled = false;
      checkbox.onchange = function () {
        confirm.textContent = checkbox.checked ? '永久删除文件并移除' : '确认移除';
      };
      cancel.onclick = function () { dialog.close(); };
      dialog.oncancel = function (event) {
        if (confirm.disabled) { event.preventDefault(); }
      };
      confirm.onclick = function () {
        runAction('book:' + book.id + ':confirm-remove', confirm, async function () {
          cancel.disabled = true;
          checkbox.disabled = true;
          try {
            const result = await api('/api/books/' + encodeURIComponent(book.id) + '/remove', {
              method: 'POST', body: { confirm: true, delete_files: checkbox.checked }
            });
            dialog.close();
            showToast(result.message, 'success');
            await refreshBooks({ manual: true });
            pollSoon();
          } catch (failure) {
            error.textContent = failure.message || '删除失败，请重试。';
            throw failure;
          } finally {
            cancel.disabled = false;
            checkbox.disabled = Boolean(preview.cleanup_error) || pending;
          }
        });
      };
      dialog.showModal();
      cancel.focus();
    });
  }

  function countCell(value) {
    const parsed = num(value);
    if (parsed === null) {
      return h('span', { className: 'muted', text: '未知' });
    }
    return h('span', {}, [
      h('span', { className: 'num', text: String(parsed) }),
      h('span', { className: 'unit', text: '项' })
    ]);
  }

  function createBookTask(book, kind, button) {
    const key = 'book:' + String(book.id) + ':' + kind;
    return runAction(key, button, async function () {
      const url = book && typeof book.catalog_url === 'string' ? book.catalog_url.trim() : '';
      if (!url) {
        throw new Error('该书缺少目录地址，无法创建任务。');
      }
      const task = await api('/api/tasks', {
        method: 'POST',
        body: { kind: kind, url: url }
      });
      if (!task || task.id === undefined || task.id === null) {
        throw new Error('后台未返回任务信息。');
      }
      upsertTask(task);
      showToast('已创建' + kindLabel(kind) + '任务。', 'success');
      announce('已创建' + kindLabel(kind) + '任务。');
      pollSoon();
      await openTask(task.id);
    });
  }

  /* ---------- Tasks ---------- */

  function renderTasks() {
    const loading = $id('tasks-loading');
    const empty = $id('tasks-empty');
    const wrap = $id('tasks-table-wrap');
    const errorBox = $id('tasks-error');
    const errorText = $id('tasks-error-text');
    const body = $id('tasks-body');
    const tasks = state.tasks;

    updateBadges();

    if (!loading || !empty || !wrap || !body) {
      return;
    }

    if (errorBox) {
      errorBox.hidden = !state.tasksError;
    }
    if (errorText) {
      errorText.textContent = state.tasksError;
    }

    loading.hidden = state.tasksLoaded || Boolean(state.tasksError);
    empty.hidden = !(state.tasksLoaded && tasks.length === 0 && !state.tasksError);
    wrap.hidden = tasks.length === 0;

    const signature = sig({
      tasks: tasks,
      selected: state.selectedTaskId
    });
    if (signature === state.tasksSig) {
      return;
    }
    state.tasksSig = signature;

    withPreservedRender(function () {
      body.replaceChildren.apply(body, tasks.map(taskRow));
    });
  }

  function updateBadges() {
    const badge = $id('tasks-badge');
    if (!badge) {
      return;
    }
    const active = state.tasks.filter(function (task) {
      return ACTIVE_STATUSES.has(task.status);
    }).length;
    if (active > 0) {
      badge.hidden = false;
      badge.textContent = active > 99 ? '99+' : String(active);
      badge.title = active + ' 个进行中的任务';
    } else {
      badge.hidden = true;
    }
  }

  function taskRow(task) {
    const id = String(task.id);
    const isSelected = state.selectedTaskId !== null && String(state.selectedTaskId) === id;
    const progress = task.progress && typeof task.progress === 'object' ? task.progress : {};
    const progressBits = [];
    if (progress.phase) {
      progressBits.push(PHASE_LABELS[progress.phase] || String(progress.phase));
    }
    if (progress.message) {
      progressBits.push(String(progress.message));
    }
    const current = num(progress.current);
    const total = num(progress.total);
    const cached = num(progress.cached);
    if (current !== null && total !== null) {
      progressBits.push(current + '/' + total);
    }
    if (cached !== null) {
      progressBits.push('缓存 ' + cached);
    }
    const progressText = progressBits.length ? progressBits.join(' · ') : '—';

    let source = '';
    if (task.kind === 'search') {
      source = task.query ? '关键词：' + String(task.query) : '';
    } else if (task.url) {
      source = String(task.url);
    }

    const actions = [
      actionButton('查看详情', '', 'task:' + id + ':open', function () {
        openTask(task.id);
      })
    ];
    if (CANCELABLE_STATUSES.has(task.status)) {
      actions.push(actionButton(task.status === 'queued' ? '取消' : '停止', 'btn-danger', 'task:' + id + ':cancel', function (button) {
        cancelTask(task, button);
      }));
    }
    if (task.status === 'needs_browser') {
      actions.push(actionButton('浏览器', '', 'task:' + id + ':browser', function (button) {
        openBrowserFor(task, button);
      }));
    }

    return h('tr', { className: isSelected ? 'is-selected' : '' }, [
      h('td', {}, [h('span', { className: 'kind-tag', text: kindLabel(task.kind) })]),
      h('td', { className: 'cell-title' }, [
        h('strong', { className: 'task-subject', text: taskSubject(task) }),
        source ? h('span', { className: 'task-source', text: source }) : null
      ]),
      h('td', {}, [statusPill(task.status)]),
      h('td', { className: 'cell-progress' }, [h('span', { text: progressText })]),
      h('td', { className: 'cell-time', text: timeText(task.updated_at) }),
      h('td', { className: 'cell-actions' }, [h('div', { className: 'actions' }, actions)])
    ]);
  }

  function upsertTask(task) {
    if (!task || task.id === undefined || task.id === null) {
      return;
    }
    const id = String(task.id);
    const known = state.tasks.find(function (item) { return String(item.id) === id; });
    if (known && Number(known.updated_at) > Number(task.updated_at)) return;
    state.tasks = [task].concat(state.tasks.filter(function (item) {
      return String(item.id) !== id;
    })).sort(function (a, b) { return Number(b.created_at) - Number(a.created_at); });
    state.tasksLoaded = true;
    renderTasks();
    updateBadges();
  }

  function syncDetailFromTasks() {
    if (!state.selectedTaskId) {
      return;
    }
    const task = state.tasks.find(function (item) {
      return String(item.id) === String(state.selectedTaskId);
    });
    if (task) {
      state.detail = task;
      renderDetail();
    }
  }

  async function openTask(id) {
    if (id === undefined || id === null) {
      return;
    }
    const key = String(id);
    state.selectedTaskId = key;
    state.detail = state.tasks.find(function (task) {
      return String(task.id) === key;
    }) || null;
    state.detailSig = '';
    if (state.view !== 'tasks') {
      switchView('tasks');
    }
    renderDetail();
    window.requestAnimationFrame(function () {
      const card = $id('task-detail');
      if (card && !card.hidden) {
        card.scrollIntoView({ block: 'start' });
      }
    });

    try {
      const task = await api(taskPath(key));
      if (task && state.selectedTaskId === key && String(task.id) === key) {
        upsertTask(task);
        state.detail = state.tasks.find(function (item) { return String(item.id) === key; }) || task;
        state.detailSig = '';
        renderDetail();
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        showToast('任务不存在或已被清理。', 'error');
      } else if (error instanceof ApiError && error.status === 0) {
        noteOffline();
      } else {
        reportError(error, '任务详情加载失败。');
      }
    }
  }

  function closeDetail() {
    state.selectedTaskId = null;
    state.detail = null;
    state.detailSig = '';
    renderDetail();
    const heading = $id('tasks-heading');
    if (heading) {
      heading.focus({ preventScroll: true });
    }
  }

  function renderDetail() {
    const card = $id('task-detail');
    if (!card) {
      return;
    }
    const task = state.detail;
    if (!state.selectedTaskId || !task || String(task.id) !== String(state.selectedTaskId)) {
      card.hidden = true;
      card.replaceChildren();
      state.detailSig = '';
      return;
    }

    card.hidden = false;
    const signature = sig({
      id: String(task.id),
      task: task,
      settings: state.settings
    });
    if (signature === state.detailSig) {
      return;
    }
    state.detailSig = signature;

    withPreservedRender(function () {
      card.replaceChildren.apply(card, detailContent(task));
    });
  }

  function detailContent(task) {
    const id = String(task.id);
    const nodes = [];

    nodes.push(h('div', { className: 'detail-head' }, [
      h('div', { className: 'detail-head-copy' }, [
        h('p', { className: 'detail-kicker', text: kindLabel(task.kind) }),
        h('h2', {
          className: 'detail-title',
          attrs: { id: 'task-detail-title' },
          text: taskSubject(task)
        })
      ]),
      h('div', { className: 'detail-head-actions' }, [
        statusPill(task.status),
        actionButton('关闭', '', 'detail:' + id + ':close', function () {
          closeDetail();
        })
      ])
    ]));

    nodes.push(metaBlock(task));
    nodes.push(progressBlock(task));

    const callout = statusCallout(task);
    if (callout) {
      nodes.push(callout);
    }

    const actions = detailActions(task);
    if (actions.length) {
      nodes.push(h('div', { className: 'detail-actions' }, actions));
    }

    const warnings = collectWarnings(task);
    if (warnings.length) {
      nodes.push(h('section', { className: 'detail-section' }, [
        h('h3', { className: 'subhead', text: '告警（' + warnings.length + '）' }),
        h('ul', { className: 'warning-list' }, warnings.map(function (text) {
          return h('li', { text: text });
        }))
      ]));
    }

    if (task.error) {
      nodes.push(h('section', { className: 'detail-section' }, [
        h('h3', { className: 'subhead', text: '错误' }),
        h('p', { className: 'error-text', text: displayText(task.error) })
      ]));
    }

    if (task.detail) {
      nodes.push(h('section', { className: 'detail-section' }, [
        h('h3', { className: 'subhead', text: '补充说明' }),
        h('p', { className: 'detail-note', text: displayText(task.detail) })
      ]));
    }

    const resultNode = resultBlock(task);
    if (resultNode) {
      nodes.push(resultNode);
    }

    const searchNode = searchResultsBlock(task, 'detail-result:' + id);
    if (searchNode) {
      nodes.push(searchNode);
    }

    nodes.push(techDetails(task));
    return nodes;
  }

  function metaBlock(task) {
    const items = [];
    items.push(metaItem('类型', kindLabel(task.kind)));

    if (task.kind === 'search') {
      if (task.query) {
        items.push(metaItem('搜索关键词', String(task.query)));
      }
    } else if (task.url) {
      items.push(metaItem('来源地址', String(task.url), true));
    }

    items.push(metaItem('任务 ID', String(task.id), true));
    items.push(metaItem('创建时间', timeText(task.created_at)));
    items.push(metaItem('更新时间', timeText(task.updated_at)));

    return h('dl', { className: 'meta-grid' }, items);
  }

  function metaItem(label, value, mono) {
    return h('div', { className: 'meta-item' }, [
      h('dt', { text: label }),
      h('dd', {}, [mono ? h('code', { text: String(value) }) : h('span', { text: String(value) })])
    ]);
  }

  function progressBlock(task) {
    const progress = task.progress && typeof task.progress === 'object' ? task.progress : {};
    const current = num(progress.current);
    const total = num(progress.total);
    const cached = num(progress.cached);
    const bits = [];

    if (progress.phase) {
      bits.push(PHASE_LABELS[progress.phase] || String(progress.phase));
    }
    if (progress.message) {
      bits.push(String(progress.message));
    }
    if (current !== null && total !== null) {
      bits.push(current + '/' + total);
    }
    if (cached !== null) {
      bits.push('缓存 ' + cached);
    }

    const percent = total !== null && total > 0 && current !== null
      ? Math.max(0, Math.min(100, (current / total) * 100))
      : null;
    const hasData = bits.length > 0 || percent !== null;

    const fill = h('div', {
      className: 'progress-fill' + (percent === null ? ' is-indeterminate' : '')
    });
    if (percent !== null) {
      fill.style.width = percent.toFixed(1) + '%';
    }

    const barAttrs = {
      role: 'progressbar',
      'aria-valuemin': '0',
      'aria-valuemax': String(total !== null && total > 0 ? total : 100),
      'aria-valuetext': bits.join('，') || '进度未知'
    };
    if (percent !== null) {
      barAttrs['aria-valuenow'] = String(Math.round(percent));
    }

    const children = [
      h('div', { className: 'progress-head' }, [
        h('h3', { className: 'subhead', text: '进度' }),
        h('span', {
          className: 'progress-value',
          text: percent !== null
            ? Math.round(percent) + '%'
            : (task.status === 'queued' ? '等待开始' : '')
        })
      ])
    ];
    if (hasData) {
      children.push(h('div', { className: 'progress-bar', attrs: barAttrs }, [fill]));
    }
    children.push(h('p', {
      className: 'progress-meta',
      text: hasData ? (bits.join(' · ') || '正在处理…') : '暂无进度信息。'
    }));

    return h('section', { className: 'progress-block' }, children);
  }

  function statusCallout(task) {
    const textMap = {
      needs_browser: task.settings.browser_mode === 'separate' ? '点击“打开验证浏览器”，在独立窗口完成验证后自动继续。' : '请连接浏览器扩展并刷新书页。连接后，任务自动在当前浏览器的新标签页继续。',
      waiting_verification: task.settings.browser_mode === 'separate' ? '请到独立验证窗口手动完成验证；期间也可以停止任务。' : '请在当前浏览器的任务标签页手动完成验证，任务会自动继续；期间也可以停止任务。',
      blocked: '站点拒绝了当前请求。可选择通过浏览器重试；若出现验证，需要手动完成。',
      partial: '部分章节未完成，已生成的内容仍可下载。可补齐缺口或重试。',
      interrupted: '任务已中断，缓存已保留，可重试。',
      paused: '任务已暂停，可重试。',
      stopping: '正在停止任务，已完成的缓存会保留。'
    };
    const progress = task.progress && typeof task.progress === 'object' ? task.progress : {};
    const progressText = progress.message ? String(progress.message) : '';
    const text = task.settings.browser_mode === 'extension' && task.status === 'waiting_verification' && progressText
      ? progressText
      : textMap[task.status];
    if (!text) {
      return null;
    }
    const tone = task.status === 'blocked' ? 'danger' : (task.status === 'waiting_verification' ? 'info' : 'warn');
    return h('p', { className: 'callout callout-' + tone, text: text });
  }

  function detailActions(task) {
    const id = String(task.id);
    const status = task.status;
    const actions = [];
    const hasResult = task.result && typeof task.result === 'object';
    const useBrowser = status === 'blocked' || (status === 'partial' && Boolean(task.error));

    if (task.file_available === true) {
      actions.push(fileLink(
        taskFilePath(id),
        status === 'partial' ? '下载部分结果TXT' : '下载TXT',
        'task:' + id + ':file'
      ));
    } else if (hasResult && task.result.output) {
      actions.push(fileLink('', '下载TXT', 'task:' + id + ':file', {
        disabled: true,
        title: 'TXT 文件暂不可用。'
      }));
    }

    if (status === 'partial' && task.url) {
      actions.push(actionButton('补齐缺口', 'btn-primary', 'task:' + id + ':fill', function (button) {
        fillGaps(task, button);
      }));
    }

    if (RETRYABLE_STATUSES.has(status)) {
      actions.push(actionButton(
        useBrowser ? '通过浏览器重试' : '重试',
        status === 'partial' ? '' : 'btn-primary',
        'task:' + id + ':retry:' + (useBrowser ? 'browser' : 'normal'),
        function (button) {
          retryTask(task, useBrowser, button);
        }
      ));
      if (settingsDifferFromTask(task)) {
        actions.push(actionButton(
          '按当前设置重试',
          '',
          'task:' + id + ':retry:current',
          function (button) {
            retryTask(task, useBrowser, button, true);
          },
          {title: '使用已保存的设置；未保存的表单修改不会生效。'}
        ));
      }
    }

    if (status === 'needs_browser') {
      actions.push(browserAction(task, 'task:' + id + ':browser'));
    }

    if (browserCanFocus(task)) {
      actions.push(actionButton('前往任务标签页', '', 'task:' + id + ':focus', function (button) {
        focusTaskTab(task, button);
      }));
    }

    if (CANCELABLE_STATUSES.has(status)) {
      actions.push(actionButton(status === 'queued' ? '取消任务' : '停止任务', 'btn-danger', 'task:' + id + ':cancel', function (button) {
        cancelTask(task, button);
      }));
    }

    return actions;
  }

  function settingsDifferFromTask(task) {
    if (!state.settingsLoaded || !state.settings || !task || !task.settings) {
      return false;
    }
    return SETTINGS_FIELDS.some(function (name) {
      const current = state.settings[name];
      const snapshot = task.settings[name];
      if (current === snapshot) {
        return false;
      }
      return String(current === undefined || current === null ? '' : current)
        !== String(snapshot === undefined || snapshot === null ? '' : snapshot);
    });
  }

  function resultBlock(task) {
    const result = task.result;
    if (!result || typeof result !== 'object') {
      return null;
    }

    const stats = [];
    if (result.output) {
      stats.push(statItem('输出文件', String(result.output)));
    }

    const numericFields = [
      ['total_chapters', '合并后章节'],
      ['failed_chapters', '失败章节'],
      ['missing_chapters', '缺失章节'],
      ['skipped_chapters', '跳过章节']
    ];
    numericFields.forEach(function (entry) {
      if (Object.prototype.hasOwnProperty.call(result, entry[0])) {
        stats.push(statItem(entry[1], countText(resultCount(result, entry[0]))));
      }
    });
    if (typeof result.interrupted === 'boolean') {
      stats.push(statItem('是否中断', result.interrupted ? '是' : '否'));
    }

    const missing = missingEntries(result.missing_chapters);
    const children = [h('h3', { className: 'subhead', text: '结果' })];

    if (task.status === 'partial') {
      children.push(h('p', {
        className: 'callout callout-warn',
        text: '以下为部分结果，TXT 只包含已成功抓取的章节。'
      }));
    }

    if (stats.length) {
      children.push(h('dl', { className: 'result-grid' }, stats));
    } else {
      children.push(h('p', { className: 'muted', text: '暂无结果统计。' }));
    }

    if (missing.length) {
      children.push(h('div', {}, [
        h('p', { className: 'subhead', text: '缺章（' + missing.length + '）' }),
        h('ul', { className: 'missing-list' }, missing.map(function (text) {
          return h('li', { text: text });
        }))
      ]));
    }

    return h('section', { className: 'detail-section' }, children);
  }

  function statItem(label, value) {
    return h('div', { className: 'result-stat' }, [
      h('dt', { text: label }),
      h('dd', { text: value })
    ]);
  }

  function searchResultsBlock(task, keyPrefix) {
    const results = Array.isArray(task.results) ? task.results : [];
    if (task.kind !== 'search' && results.length === 0) {
      return null;
    }
    const children = [h('h3', { className: 'subhead', text: '搜索结果（' + results.length + '）' })];
    if (results.length === 0) {
      children.push(h('ul', { className: 'result-list' }, [
        h('li', {
          className: 'result-empty',
          text: task.status === 'completed' ? '没有搜索结果。' : '搜索结果会在这里显示。'
        })
      ]));
    } else {
      children.push(h('ul', { className: 'result-list' }, results.map(function (result, index) {
        return searchResultItem(result, index, keyPrefix + ':' + index);
      })));
    }
    return h('section', { className: 'detail-section' }, children);
  }

  function searchResultItem(result, index, key) {
    const data = result && typeof result === 'object' ? result : {};
    const url = typeof data.url === 'string' ? data.url : '';
    const title = typeof data.title === 'string' && data.title.trim() ? data.title.trim() : '未命名结果';
    const site = typeof data.site === 'string' ? data.site : '';
    const snippet = typeof data.snippet === 'string' ? data.snippet : '';
    const meta = [site, url].filter(function (part) {
      return part !== '';
    }).join(' · ');

    return h('li', { className: 'result-item' }, [
      h('div', { className: 'result-main' }, [
        h('h4', { className: 'result-title', text: title }),
        meta ? h('p', { className: 'result-meta', text: meta }) : null,
        snippet ? h('p', { className: 'result-snippet', text: snippet }) : null
      ]),
      actionButton('下载', 'btn-primary', key + ':download', function (button) {
        downloadSearchResult(data, button, key + ':download');
      }, {
        disabled: url === '',
        title: url === '' ? '该结果缺少链接。' : '创建下载任务。'
      })
    ]);
  }

  function techDetails(task) {
    const key = 'tech:' + String(task.id);
    const details = h('details', { className: 'tech-details' }, [
      h('summary', { text: '技术详情（JSON）' }),
      h('pre', { className: 'tech-pre', text: safeJson(task) })
    ]);
    details.open = state.openDetails.has(key);
    details.addEventListener('toggle', function () {
      if (details.open) {
        state.openDetails.add(key);
      } else {
        state.openDetails.delete(key);
      }
    });
    return details;
  }

  /* ---------- Task actions ---------- */

  function cancelTask(task, button) {
    const key = 'task:' + String(task.id) + ':cancel';
    return runAction(key, button, async function () {
      await api(taskPath(task.id, '/cancel'), { method: 'POST' });
      showToast('已请求停止任务。', 'success');
      announce('已请求停止任务。');
      pollSoon();
    });
  }

  function openBrowserFor(task, button) {
    const key = 'task:' + String(task.id) + ':browser';
    return runAction(key, button, async function () {
      await api(taskPath(task.id, '/browser'), { method: 'POST' });
      showToast('已请求打开验证浏览器，请在独立窗口完成验证。', 'success');
      announce('已请求打开验证浏览器。');
      pollSoon();
    });
  }

  function browserAction(task, key) {
    if (task.settings.browser_mode !== 'separate') {
      return h('a', {className: 'btn btn-primary btn-sm', text: '连接当前浏览器',
        attrs: {href: 'browser-extension.html', target: '_blank', rel: 'noopener'}});
    }
    return actionButton('打开验证浏览器', 'btn-primary', key, function (button) {
      openBrowserFor(task, button);
    });
  }

  function browserCanFocus(task) {
    const browser = state.browserStatus || {};
    return task && task.settings && task.settings.browser_mode === 'extension'
      && Boolean(task.browser_session)
      && CANCELABLE_STATUSES.has(task.status)
      && browser.connected === true
      && browser.can_focus === true;
  }

  function focusTaskTab(task, button) {
    return runAction('task:' + String(task.id) + ':focus', button, async function () {
      await api(taskPath(task.id, '/focus'), { method: 'POST' });
      showToast('正在定位该任务的任务标签页。', 'success');
      announce('正在定位该任务的任务标签页。');
    });
  }

  function refreshBrowserDependentViews() {
    const canFocus = state.tasks.some(function (task) {
      return task.browser_session && browserCanFocus(task);
    });
    if (canFocus) {
      state.detailSig = '';
      state.searchSig = '';
      if (state.detail) {
        renderDetail();
      }
      if (state.searchTaskId) {
        syncSearchPanel();
      }
    }
  }

  function retryTask(task, useBrowser, button, useCurrentSettings) {
    const mode = useCurrentSettings ? 'current' : (useBrowser ? 'browser' : 'normal');
    const key = 'task:' + String(task.id) + ':retry:' + mode;
    return runAction(key, button, async function () {
      const created = await api(taskPath(task.id, '/retry'), {
        method: 'POST',
        body: {
          force_browser: Boolean(useBrowser),
          use_current_settings: Boolean(useCurrentSettings)
        }
      });
      showToast('已创建重试任务。', 'success');
      announce('已创建重试任务。');
      if (created && created.id !== undefined && created.id !== null) {
        upsertTask(created);
        pollSoon();
        await openTask(created.id);
      } else {
        pollSoon();
      }
    });
  }

  function fillGaps(task, button) {
    const key = 'task:' + String(task.id) + ':fill';
    return runAction(key, button, async function () {
      const url = typeof task.url === 'string' ? task.url.trim() : '';
      if (!url) {
        throw new Error('该任务缺少目录地址，无法补齐缺口。');
      }
      const created = await api('/api/tasks', {
        method: 'POST',
        body: { kind: 'fill_gaps', url: url }
      });
      showToast('已创建补齐缺口任务。', 'success');
      announce('已创建补齐缺口任务。');
      if (created && created.id !== undefined && created.id !== null) {
        upsertTask(created);
        pollSoon();
        await openTask(created.id);
      } else {
        pollSoon();
      }
    });
  }

  function downloadSearchResult(result, button, key) {
    const url = result && typeof result.url === 'string' ? result.url.trim() : '';
    if (!url) {
      reportError(new Error('该搜索结果缺少链接。'));
      return;
    }
    return runAction(key, button, async function () {
      const searchTask = state.searchTaskId
        ? state.tasks.find(function (item) {
          return String(item.id) === String(state.searchTaskId);
        })
        : null;
      const keepSearching = Boolean(searchTask && ACTIVE_STATUSES.has(searchTask.status));
      const task = await api('/api/tasks', {
        method: 'POST',
        body: {
          kind: 'download',
          url: url,
          force_browser: forceBrowser()
        }
      });
      if (!task || task.id === undefined || task.id === null) {
        throw new Error('后台未返回任务信息。');
      }
      upsertTask(task);
      const message = keepSearching
        ? '已加入下载队列；可继续选书或结束搜索。'
        : '已创建下载任务。';
      showToast(message, 'success');
      announce(message);
      pollSoon();
      if (!keepSearching) {
        await openTask(task.id);
      }
    });
  }

  /* ---------- Search ---------- */

  async function submitSearch() {
    const input = $id('search-query');
    const status = $id('search-status');
    if (!input) {
      return;
    }
    const query = input.value.trim();
    if (!query) {
      if (status) {
        status.textContent = '请输入搜索关键词。';
      }
      showToast('请输入搜索关键词。', 'error');
      announce('请输入搜索关键词。');
      input.focus();
      return;
    }

    await runAction('create:search', $id('search-submit'), async function () {
      if (status) {
        status.textContent = '正在创建搜索任务…';
      }
      try {
        const task = await api('/api/tasks', {
          method: 'POST',
          body: {
            kind: 'search',
            query: query,
            force_browser: forceBrowser()
          }
        });
        if (!task || task.id === undefined || task.id === null) {
          throw new Error('后台未返回任务信息。');
        }
        state.searchTaskId = String(task.id);
        state.searchSig = '';
        upsertTask(task);
        renderSearchTask(task);
        if (status) {
          status.textContent = '';
        }
        showToast('搜索任务已创建。', 'success');
        announce('搜索任务已创建。');
        pollSoon();
      } catch (error) {
        if (status) {
          status.textContent = error && error.message ? error.message : '搜索任务创建失败。';
        }
        throw error;
      }
    });
  }

  function syncSearchPanel() {
    if (!state.searchTaskId) {
      return;
    }
    const task = state.tasks.find(function (item) {
      return String(item.id) === String(state.searchTaskId);
    });
    if (task) {
      renderSearchTask(task);
    }
  }

  function renderSearchTask(task) {
    const panel = $id('search-panel');
    const head = $id('search-panel-head');
    const list = $id('search-results');
    if (!panel || !head || !list || !task) {
      return;
    }

    const signature = sig({
      id: String(task.id),
      status: task.status,
      results: task.results,
      error: task.error,
      detail: task.detail,
      query: task.query,
      progress: task.progress,
      canFocus: browserCanFocus(task)
    });
    if (signature === state.searchSig) {
      panel.hidden = false;
      return;
    }
    state.searchSig = signature;

    withPreservedRender(function () {
      const results = Array.isArray(task.results) ? task.results : [];
      const note = h('p', {
        className: 'inline-status',
        attrs: { role: 'status', 'aria-live': 'polite' },
        text: searchStatusText(task)
      });

      const actions = [
        actionButton('查看任务', '', 'search:' + String(task.id) + ':open', function () {
          openTask(task.id);
        })
      ];
      if (task.status === 'needs_browser') {
        actions.push(browserAction(task, 'search:' + String(task.id) + ':browser'));
      }
      if (CANCELABLE_STATUSES.has(task.status)) {
        const resultCount = Array.isArray(task.results) ? task.results.length : 0;
        actions.push(actionButton('结束搜索', 'btn-danger', 'search:' + String(task.id) + ':cancel', function (button) {
          cancelSearchTask(task, button, resultCount);
        }, {
          title: resultCount
            ? '停止继续搜索；已找到的 ' + resultCount + ' 个结果会保留。'
            : '停止搜索；已找到的结果会保留。'
        }));
      }

      head.replaceChildren(note, h('div', { className: 'inline-actions' }, actions));

      if (results.length) {
        list.replaceChildren.apply(list, results.map(function (result, index) {
          return searchResultItem(result, index, 'search-result:' + String(task.id) + ':' + index);
        }));
      } else {
        list.replaceChildren(h('li', {
          className: 'result-empty',
          text: searchEmptyText(task)
        }));
      }
      panel.hidden = false;
    });
  }

  function searchStatusText(task) {
    const results = Array.isArray(task.results) ? task.results : [];
    const count = results.length;
    const error = task.error ? ' ' + displayText(task.error) : '';
    const progress = task.progress && typeof task.progress === 'object' ? task.progress : {};
    const progressText = progress.message ? ' ' + String(progress.message) : '';

    switch (task.status) {
      case 'queued':
        return '搜索任务排队中，完成后会显示结果。';
      case 'running':
        return '正在搜索…' + (count ? ' 已获得 ' + count + ' 个结果。' : '');
      case 'needs_browser':
        return task.settings.browser_mode === 'separate' ? '请打开验证浏览器，完成后搜索自动继续。' : '请安装并连接扩展，刷新书页后搜索自动在当前浏览器继续。';
      case 'waiting_verification':
        return task.settings.browser_mode === 'separate'
          ? '请在独立验证窗口完成验证，搜索会自动继续。'
          : (progressText || '请在当前浏览器的任务标签页完成验证，搜索会自动继续。');
      case 'stopping':
        return '正在停止搜索任务。';
      case 'completed':
        return count
          ? '搜索完成，共 ' + count + ' 个结果。选择结果即可创建下载任务。'
          : '搜索完成，没有找到结果。';
      case 'partial':
        return count
          ? '搜索部分完成，共 ' + count + ' 个结果。'
          : '搜索部分完成，没有结果。';
      case 'blocked':
        return '搜索被站点拦截。' + error;
      case 'failed':
        return '搜索失败。' + error;
      case 'cancelled':
        return '搜索已取消。';
      case 'interrupted':
        return '搜索已中断。可到任务中心重试。';
      case 'paused':
        return '搜索已暂停。可到任务中心重试。';
      default:
        return '搜索任务状态：' + statusInfo(task.status).label;
    }
  }

  function cancelSearchTask(task, button, resultCount) {
    const key = 'search:' + String(task.id) + ':cancel';
    return runAction(key, button, async function () {
      await api(taskPath(task.id, '/cancel'), { method: 'POST' });
      const suffix = resultCount ? '已找到的 ' + resultCount + ' 个结果会保留。' : '已找到的结果会保留。';
      showToast('已结束搜索。' + suffix, 'success');
      announce('已结束搜索。' + suffix);
      pollSoon();
    });
  }

  function searchEmptyText(task) {
    switch (task.status) {
      case 'queued':
        return '搜索任务排队中，结果会在这里显示。';
      case 'running':
        return '正在搜索，结果会在这里显示。';
      case 'needs_browser':
      case 'waiting_verification':
        return '完成浏览器验证后，结果会在这里显示。';
      case 'completed':
        return '没有匹配的书籍，可尝试更短或更常见的关键词。';
      case 'partial':
        return '搜索部分完成，但没有可显示的结果。';
      case 'blocked':
      case 'failed':
        return '搜索没有返回结果。可查看任务详情或重试。';
      default:
        return '暂无搜索结果。';
    }
  }

  /* ---------- Settings ---------- */

  async function loadSettings(options) {
    const opts = options || {};
    if (state.shutdown) {
      return;
    }
    const status = $id('settings-status');
    await runAction('settings:load', $id('settings-reload'), async function () {
      if (status) {
        status.textContent = '正在读取设置…';
      }
      try {
        const settings = await api('/api/settings');
        if (!settings || typeof settings !== 'object' || Array.isArray(settings)) {
          throw new ApiError('设置数据格式异常。', 500, null);
        }
        noteOnline();
        const keepDraft = state.settingsDirty && !opts.manual;
        state.settings = settings;
        state.settingsLoaded = true;
        if (!keepDraft) {
          state.settingsDirty = false;
          fillSettings(settings);
        }
        showSettingsError('');
        if (status) {
          status.textContent = keepDraft ? '设置已载入，未覆盖未保存的更改。' : '设置已载入。';
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 0) {
          noteOffline();
        } else {
          noteOnline();
        }
        const message = error && error.message ? error.message : '设置读取失败。';
        showSettingsError(message);
        if (status) {
          status.textContent = '';
        }
        if (opts.manual && !(error instanceof ApiError && error.status === 0)) {
          showToast(message, 'error');
        }
      }
    });
  }

  function showSettingsError(message) {
    const box = $id('settings-error');
    const text = $id('settings-error-text');
    if (!box || !text) {
      return;
    }
    box.hidden = !message;
    text.textContent = message || '';
  }

  function fillSettings(settings) {
    const form = $id('settings-form');
    if (!form || !settings) {
      return;
    }
    SETTINGS_FIELDS.forEach(function (name) {
      const field = form.elements.namedItem(name);
      if (!field) {
        return;
      }
      const value = settings[name];
      field.value = value === undefined || value === null ? '' : String(value);
    });
  }

  function readSettingsForm() {
    const form = $id('settings-form');
    if (!form) {
      throw new Error('设置表单不可用。');
    }
    const outputField = form.elements.namedItem('output_dir');
    const outputDir = outputField ? outputField.value.trim() : '';
    if (!outputDir) {
      if (outputField) {
        outputField.focus();
      }
      throw new Error('请填写输出目录。');
    }

    function readNumber(name, label) {
      const field = form.elements.namedItem(name);
      if (!field) {
        throw new Error('缺少设置项：' + label + '。');
      }
      const raw = field.value.trim();
      if (raw === '') {
        field.focus();
        throw new Error('请填写' + label + '。');
      }
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        field.focus();
        throw new Error(label + '必须是非负数字。');
      }
      return value;
    }

    const browserField = form.elements.namedItem('browser_executable');

    return {
      output_dir: outputDir,
      delay: readNumber('delay', '请求间隔'),
      timeout: readNumber('timeout', '请求超时'),
      retries: readNumber('retries', '单章重试次数'),
      retry_rounds: readNumber('retry_rounds', '重试轮次'),
      retry_delay: readNumber('retry_delay', '重试间隔'),
      browser_executable: browserField ? browserField.value.trim() : '',
      browser_mode: form.elements.namedItem('browser_mode').value,
      verification_timeout: readNumber('verification_timeout', '人工验证等待')
    };
  }

  async function saveSettings() {
    const form = $id('settings-form');
    if (!form) {
      return;
    }
    if (typeof form.reportValidity === 'function' && !form.reportValidity()) {
      announce('请检查设置表单。');
      return;
    }

    let payload;
    try {
      payload = readSettingsForm();
    } catch (error) {
      showSettingsError(error.message);
      reportError(error);
      return;
    }

    await runAction('settings:save', $id('settings-save'), async function () {
      const status = $id('settings-status');
      if (status) {
        status.textContent = '正在保存设置…';
      }
      try {
        const saved = await api('/api/settings', {
          method: 'PUT',
          body: payload
        });
        if (!saved || typeof saved !== 'object' || Array.isArray(saved)) {
          throw new ApiError('后台未返回保存后的设置。', 500, null);
        }
        state.settings = saved;
        state.settingsLoaded = true;
        state.settingsDirty = false;
        fillSettings(saved);
        showSettingsError('');
        if (status) {
          status.textContent = '设置已保存，将用于之后创建的任务。';
        }
        showToast('设置已保存。', 'success');
        announce('设置已保存。');
      } catch (error) {
        if (status) {
          status.textContent = '';
        }
        showSettingsError(error && error.message ? error.message : '设置保存失败。');
        throw error;
      }
    });
  }

  /* ---------- Shutdown ---------- */

  async function requestShutdown() {
    if (state.shutdown || state.pending.has('shutdown')) {
      return;
    }
    const confirmed = window.confirm('退出后台将停止当前任务，已下载的缓存与已生成的文件会保留。确定退出吗？');
    if (!confirmed) {
      return;
    }

    await runAction('shutdown', $id('shutdown-btn'), async function () {
      try {
        await api('/api/shutdown', {
          method: 'POST',
          body: { confirm_stop: true }
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 0) {
          finishShutdown('连接已中断，后台可能已经退出。');
          return;
        }
        throw error;
      }
      finishShutdown('后台已退出，任务轮询已停止。缓存与已生成的文件已保留。');
    });
  }

  function finishShutdown(message) {
    state.shutdown = true;
    state.connected = false;
    stopPolling();
    renderConnection();
    document.body.classList.add('is-shutdown');
    const screen = $id('shutdown-screen');
    const text = $id('shutdown-message');
    if (text) {
      text.textContent = message;
    }
    if (screen) {
      screen.hidden = false;
      const reload = $id('shutdown-reload');
      if (reload) {
        reload.focus();
      }
    }
    announce('后台已退出。');
  }

  /* ---------- UI helpers ---------- */

  function actionButton(label, variant, key, handler, options) {
    const opts = options || {};
    const button = h('button', {
      className: 'btn btn-sm' + (variant ? ' ' + variant : ''),
      attrs: { type: 'button' },
      text: label
    });
    button.dataset.focusKey = key;
    if (opts.title) {
      button.title = opts.title;
    }
    if (opts.disabled || state.pending.has(key)) {
      button.disabled = true;
    }
    button.addEventListener('click', function () {
      handler(button);
    });
    return button;
  }

  function fileLink(href, label, key, options) {
    const opts = options || {};
    if (opts.disabled) {
      const span = h('span', {
        className: 'btn btn-sm is-disabled',
        attrs: { 'aria-disabled': 'true' },
        text: label
      });
      if (opts.title) {
        span.title = opts.title;
      }
      return span;
    }
    const link = h('a', {
      className: 'btn btn-sm',
      attrs: { href: href },
      text: label
    });
    link.dataset.focusKey = key;
    return link;
  }

  function accessMode() {
    const checked = document.querySelector('input[name="access-mode"]:checked');
    return checked && checked.value === 'browser' ? 'browser' : 'auto';
  }

  function forceBrowser() {
    return accessMode() === 'browser';
  }

  /* ---------- View navigation ---------- */

  function switchView(view) {
    const target = VIEW_META[view] ? view : 'books';
    const changed = state.view !== target;
    state.view = target;

    document.querySelectorAll('.view').forEach(function (section) {
      section.hidden = section.id !== 'view-' + target;
    });

    document.querySelectorAll('.nav-item[data-view]').forEach(function (button) {
      if (button.dataset.view === target) {
        button.setAttribute('aria-current', 'page');
      } else {
        button.removeAttribute('aria-current');
      }
    });

    const meta = VIEW_META[target];
    const title = $id('view-title');
    const subtitle = $id('view-subtitle');
    if (title) {
      title.textContent = meta.title;
    }
    if (subtitle) {
      subtitle.textContent = meta.subtitle;
    }
    document.title = meta.title + ' · book-downloader';

    if (changed) {
      window.scrollTo(0, 0);
    }

    if (target === 'books') {
      refreshBooks({ silent: true });
    } else if (target === 'tasks') {
      renderTasks();
    } else if (target === 'settings' && !state.settingsLoaded) {
      loadSettings();
    }
  }

  /* ---------- Bindings ---------- */

  function bindEvents() {
    document.querySelectorAll('.nav-item[data-view]').forEach(function (button) {
      button.addEventListener('click', function () {
        switchView(button.dataset.view);
      });
    });

    document.addEventListener('click', function (event) {
      if (!(event.target instanceof Element)) {
        return;
      }
      const goto = event.target.closest('[data-goto]');
      if (goto && goto.dataset.goto) {
        switchView(goto.dataset.goto);
      }
    });

    const refreshButton = $id('refresh-books');
    if (refreshButton) {
      refreshButton.addEventListener('click', function () {
        runAction('books:refresh', refreshButton, function () {
          return refreshBooks({ manual: true });
        });
      });
    }

    const booksRetry = $id('books-retry');
    if (booksRetry) {
      booksRetry.addEventListener('click', function () {
        runAction('books:retry', booksRetry, function () {
          return refreshBooks({ manual: true });
        });
      });
    }

    const tasksRetry = $id('tasks-retry');
    if (tasksRetry) {
      tasksRetry.addEventListener('click', function () {
        runAction('tasks:retry', tasksRetry, function () {
          return pollTasks();
        });
      });
    }

    const connectionRetry = $id('retry-connection');
    if (connectionRetry) {
      connectionRetry.addEventListener('click', function () {
        runAction('connection:retry', connectionRetry, async function () {
          state.connected = null;
          renderConnection();
          await pollTasks();
          if (state.view === 'books') {
            await refreshBooks({ silent: true });
          }
          if (state.connected === false) {
            showToast('仍无法连接后台服务。', 'error');
          } else if (state.connected === true) {
            showToast('后台服务连接正常。', 'success');
          }
        });
      });
    }

    const searchForm = $id('search-form');
    if (searchForm) {
      searchForm.addEventListener('submit', function (event) {
        event.preventDefault();
        submitSearch();
      });
    }

    const urlForm = $id('url-form');
    if (urlForm) {
      urlForm.addEventListener('submit', function (event) {
        event.preventDefault();
        if (state.pending.has('create:download')) {
          return;
        }
        if (typeof urlForm.reportValidity === 'function' && !urlForm.reportValidity()) {
          return;
        }
        const input = $id('download-url');
        const url = input ? input.value.trim() : '';
        if (!url) {
          showToast('请输入书籍链接。', 'error');
          if (input) {
            input.focus();
          }
          return;
        }
        runAction('create:download', $id('download-submit'), async function () {
          const task = await api('/api/tasks', {
            method: 'POST',
            body: {
              kind: 'download',
              url: url,
              force_browser: forceBrowser()
            }
          });
          if (!task || task.id === undefined || task.id === null) {
            throw new Error('后台未返回任务信息。');
          }
          upsertTask(task);
          showToast('已创建下载任务。', 'success');
          announce('已创建下载任务。');
          pollSoon();
          await openTask(task.id);
        });
      });
    }

    const settingsForm = $id('settings-form');
    if (settingsForm) {
      settingsForm.addEventListener('submit', function (event) {
        event.preventDefault();
        saveSettings();
      });
      settingsForm.addEventListener('input', function () {
        state.settingsDirty = true;
        const status = $id('settings-status');
        if (status && !state.pending.has('settings:save')) {
          status.textContent = '有未保存的更改。';
        }
      });
    }

    const settingsReload = $id('settings-reload');
    if (settingsReload) {
      settingsReload.addEventListener('click', function () {
        if (state.settingsDirty && !window.confirm('重新载入设置会丢弃未保存的更改，继续吗？')) {
          return;
        }
        loadSettings({ manual: true });
      });
    }

    const shutdownButton = $id('shutdown-btn');
    if (shutdownButton) {
      shutdownButton.addEventListener('click', function () {
        requestShutdown();
      });
    }

    const shutdownReload = $id('shutdown-reload');
    if (shutdownReload) {
      shutdownReload.addEventListener('click', function () {
        window.location.reload();
      });
    }
  }

  function init() {
    bindEvents();
    renderConnection();
    renderBooks();
    renderTasks();
    renderDetail();
    switchView('books');
    loadSettings();
    pollTasks();
    state.pollTimer = window.setInterval(pollTasks, POLL_INTERVAL);
  }

  init();
})();
