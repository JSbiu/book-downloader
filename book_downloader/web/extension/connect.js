// Runs only on the local application's page; credentials stay in extension storage.
(async () => {
  if (!['http://127.0.0.1:8765', 'http://localhost:8765'].includes(location.origin)) return;
  try {
    const response = await fetch('/api/browser/pairing', {cache: 'no-store'});
    if (!response.ok) return;
    const pairing = await response.json();
    await chrome.runtime.sendMessage({type: 'pair', token: pairing.token, base: location.origin});
  } catch (_) { /* The local backend may be restarting. Refresh the page to reconnect. */ }
})();
