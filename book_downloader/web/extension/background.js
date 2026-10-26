const API = '/api/browser/extension/';
let connection = null;
let busy = false;
let performing = false;
let activeId = null;
let currentTab = null;
let timer;
const VERSION = chrome.runtime.getManifest().version;
const SITE_ORIGINS = ['http://*/*', 'https://*/*'];

async function credentials() {
  if (!connection) connection = (await chrome.storage.local.get('connection')).connection;
  return connection;
}

async function api(path, body) {
  const info = await credentials();
  if (!info) throw new Error('请先打开书页建立连接');
  const response = await fetch(info.base + API + path, {
    method: body ? 'POST' : 'GET', cache: 'no-store',
    headers: {'Authorization': 'Bearer ' + info.token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})
  });
  if (!response.ok) throw new Error('书页连接失败（' + response.status + '），请刷新本地书页');
  return response.json();
}

function publicUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('仅支持公开 HTTP/HTTPS 页面');
  if (['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error('任务标签页不能访问本地管理接口');
  return url.href;
}

async function sitePermissions() {
  return chrome.permissions.contains({origins: SITE_ORIGINS});
}

function unreadable(url, reason = 'browser_notice') {
  return {unreadable: true, reason, url: url || ''};
}

async function focusRequestedTab(bridgeSession) {
  if (!bridgeSession) return;
  const state = (await chrome.storage.session.get('taskTab')).taskTab;
  if (!state || state.session !== bridgeSession) return;
  try {
    const tab = await chrome.tabs.get(state.id);
    if (tab.windowId !== undefined) await chrome.windows.update(tab.windowId, {focused: true});
    await chrome.tabs.update(tab.id, {active: true});
  } catch (_) { /* A closed task tab must not cause a new page to open. */ }
}

async function taskTab(session, url) {
  const state = (await chrome.storage.session.get('taskTab')).taskTab;
  if (state) {
    try {
      const tab = await chrome.tabs.get(state.id);
      if (state.session === session) return tab.id;
    } catch (_) { /* A user may close the previous task tab. */ }
  }
  if (!url) throw new Error('任务标签页已关闭，请重试任务');
  // Always create our own tab; never reuse or navigate an existing user's tab.
  publicUrl(url);
  const tab = await chrome.tabs.create({url: 'about:blank', active: true});
  await chrome.storage.session.set({taskTab: {id: tab.id, session, host: new URL(url).hostname.replace(/^www\./, '')}});
  return tab.id;
}

// This function executes only in the extension-owned, permitted public page.
function pageAction(command) {
  const snapshot = () => ({html: document.documentElement.outerHTML, url: location.href, ready: document.readyState,
    status: performance.getEntriesByType('navigation')[0]?.responseStatus || 200});
  if (command.action === 'snapshot') return snapshot();
  const nodes = command.selector ? [...document.querySelectorAll(command.selector)] : [];
  const node = nodes[0];
  if (command.action === 'query') return {
    count: nodes.length, visible: Boolean(node && node.getClientRects().length),
    form_html: node?.closest('form')?.outerHTML || ''
  };
  if (command.action === 'fill') {
    if (!node) throw new Error('搜索输入框尚未出现，请先完成页面验证');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(node, command.value);
    node.dispatchEvent(new Event('input', {bubbles: true}));
    node.dispatchEvent(new Event('change', {bubbles: true}));
    return {};
  }
  if (command.action === 'submit') {
    const form = node?.closest('form');
    if (!form) throw new Error('找不到站点搜索表单');
    form.requestSubmit();
    return {};
  }
  if (command.action === 'fetch') {
    const url = new URL(command.url);
    if (url.origin !== location.origin) throw new Error('搜索请求必须与当前站点同源');
    return fetch(url.href, {method: command.method, body: command.body, headers: command.headers, credentials: 'include'})
      .then(async response => ({html: new TextDecoder(command.encoding).decode(await response.arrayBuffer()),
        url: response.url, status: response.status, ready: 'complete'}));
  }
  throw new Error('不支持的浏览器操作');
}

async function perform(command) {
  if (activeId !== command.id) throw new Error('任务已停止');
  const expired = Date.now() >= command.expires_at * 1000;
  if (expired && command.action === 'navigate') return unreadable(command.url || '', 'browser_notice');
  if (expired) throw new Error('任务已超时');
  if (command.action === 'navigate') {
    publicUrl(command.url);
    if (!await sitePermissions()) return unreadable(command.url, 'permission_required');
    currentTab = await taskTab(command.session, command.url);
    await chrome.storage.session.set({taskTab: {id: currentTab, session: command.session,
      host: new URL(command.url).hostname.replace(/^www\./, '')}});
    if (activeId !== command.id) throw new Error('任务已停止');
    const navigation = await new Promise((resolve, reject) => {
      let started = false;
      const finish = (error, result) => {
        clearInterval(check);
        chrome.tabs.onUpdated.removeListener(updated);
        error ? reject(error) : resolve(result);
      };
      const updated = (id, change) => {
        if (id !== currentTab) return;
        if (change.status === 'loading') started = true;
        if (started && change.status === 'complete') finish();
      };
      const check = setInterval(() => {
        if (activeId !== command.id) finish(new Error('导航已停止'));
        else if (Date.now() >= command.expires_at * 1000) {
          finish(null, unreadable(command.url, 'browser_notice'));
        }
      }, 100);
      chrome.tabs.onUpdated.addListener(updated);
      chrome.tabs.update(currentTab, {url: command.url}).catch(finish);
    });
    if (navigation?.unreadable) return navigation;
    const tab = await chrome.tabs.get(currentTab);
    try {
      const results = await chrome.scripting.executeScript({target: {tabId: currentTab}, func: () => location.href});
      return {url: results[0]?.result || tab.url || command.url};
    } catch (_) {
      return unreadable(tab.url || command.url);
    }
  }
  currentTab = await taskTab(command.session);
  const tab = await chrome.tabs.get(currentTab);
  if (!await sitePermissions()) return unreadable(tab.url, 'permission_required');
  try { publicUrl(tab.url); } catch (_) { return unreadable(tab.url); }
  const state = (await chrome.storage.session.get('taskTab')).taskTab;
  if (new URL(tab.url).hostname.replace(/^www\./, '') !== state.host) {
    throw new Error('任务标签页已离开当前站点，请回到原站点后重试任务');
  }
  try {
    const results = await chrome.scripting.executeScript({target: {tabId: currentTab}, func: pageAction, args: [command]});
    return results[0]?.result;
  } catch (error) {
    if (command.action === 'snapshot') return unreadable(tab.url);
    throw error;
  }
}

async function poll() {
  try {
    // An active connection must survive MV3's idle worker timeout while the UI is closed.
    await chrome.runtime.getPlatformInfo();
    const info = await credentials();
    if (!info) return;
    const permissions = await sitePermissions();
    const value = await api('work?client=' + encodeURIComponent(info.client) + '&ready=' + !busy
      + '&permissions=' + (permissions ? 'true' : 'false') + '&version=' + encodeURIComponent(VERSION));
    const previous = activeId;
    activeId = value.active_id;
    if (value.focus_session) await focusRequestedTab(value.focus_session);
    if (performing && previous && previous !== activeId && currentTab !== null) {
      // Stop our current load only. Preserve the tab and never touch other tabs.
      chrome.scripting.executeScript({target: {tabId: currentTab}, func: () => window.stop()}).catch(() => {});
    }
    if (value.command && !busy) {
      busy = true;
      const command = value.command;
      (async () => {
        let result = null, error = null;
        performing = true;
        try { result = await perform(command); } catch (failure) { error = failure.message; }
        finally { performing = false; }
        try { await api('reply', {id: command.id, client: info.client, result, error}); } catch (_) {}
        busy = false;
      })();
    }
  } catch (_) { /* Backend restarts are re-paired when its local page is refreshed. */ }
  finally { clearTimeout(timer); timer = setTimeout(poll, 400); }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type !== 'pair' || !sender.url) return;
  const origin = new URL(sender.url).origin;
  if (!['http://127.0.0.1:8765', 'http://localhost:8765'].includes(origin) || message.base !== origin) return;
  (async () => {
    const old = await credentials();
    connection = {base: origin, token: message.token, client: old?.client || crypto.randomUUID()};
    await chrome.storage.local.set({connection});
    reply({connected: true});
    poll();
  })();
  return true;
});
chrome.alarms.onAlarm.addListener(() => poll());
chrome.runtime.onStartup.addListener(() => poll());
chrome.runtime.onInstalled.addListener(() => chrome.alarms.create('book-downloader-connect', {periodInMinutes: .5}));
poll();
