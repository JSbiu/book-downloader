// The prototype opens as a complete extension page and needs no local service.
async function openShuye() {
  const url = chrome.runtime.getURL('index.html');
  const contexts = await chrome.runtime.getContexts({contextTypes: ['TAB']});
  const existing = contexts.find(context => context.tabId >= 0
    && context.documentUrl.split('#')[0] === url);
  if (existing) {
    const tab = await chrome.tabs.update(existing.tabId, {active: true});
    await chrome.windows.update(tab.windowId, {focused: true});
    return;
  }
  await chrome.tabs.create({url});
}

chrome.action.onClicked.addListener(openShuye);
