document.getElementById('allow').onclick = async () => {
  const status = document.getElementById('status');
  try {
    const granted = await chrome.permissions.request({origins: ['http://*/*', 'https://*/*']});
    if (!granted) { status.textContent = '尚未授权，可稍后再试。'; return; }
    await chrome.tabs.create({url: 'http://127.0.0.1:8765/'});
    status.textContent = '已授权，请在新打开的书页中提交任务。';
  } catch (error) { status.textContent = error.message; }
};
