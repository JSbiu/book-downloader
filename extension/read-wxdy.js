(() => {
  const clean = value => String(value || '').replace(/\u00a0/g, ' ').replace(/\r/g, '').trim();
  const book = location.pathname.match(/^\/books\/(\d+)\//)?.[1];
  const urlFor = value => {
    try {
      const url = new URL(value, location.href);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
          || !['wxdy.cc', 'www.wxdy.cc'].includes(url.hostname.toLowerCase())
          || !url.pathname.startsWith(`/books/${book}/`)) return null;
      url.hash = '';
      return url;
    } catch { return null; }
  };
  const chapterPath = url => url?.pathname.match(/^\/books\/\d+\/(\d+)(?:_(\d+))?\.html$/i);
  globalThis.shuyeReadSource = kind => {
    const pageTitle = document.title || '';
    if (document.querySelector('#cf-challenge-running, #challenge-form, .cf-turnstile')
        || /Just a moment|人机验证|安全验证/i.test(pageTitle)) return { kind: 'needs_action', url: location.href };
    if (/403|Forbidden|Access denied|拒绝访问/i.test(pageTitle)) return { kind: 'access_denied', url: location.href };
    if (!book) return { kind: 'structure_mismatch', url: location.href };
    if (kind === 'catalog') {
      if (!/^\/books\/\d+\/(?:index_\d+\.html)?$/i.test(location.pathname)) return { kind: 'structure_mismatch', url: location.href };
      const chapters = [], seen = new Set();
      for (const anchor of document.querySelectorAll('ul.mulu_list a[href]')) {
        const url = urlFor(anchor.getAttribute('href'));
        const title = clean(anchor.textContent);
        const match = chapterPath(url);
        if (!match || match[2] || seen.has(url.href)
            || !/^(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章回节]|序章|楔子|番外|后记)/u.test(title)) continue;
        seen.add(url.href);
        chapters.push({ title, url: url.href });
      }
      if (!chapters.length) return { kind: 'structure_mismatch', url: location.href };
      let nextCatalog = null;
      const latest = urlFor(document.querySelector('meta[property="og:novel:latest_chapter_url"]')?.content);
      const latestChapter = chapterPath(latest)?.[1];
      const reachedLatest = latestChapter && chapters.some(chapter => chapterPath(new URL(chapter.url))?.[1] === latestChapter);
      const currentPage = Number(location.pathname.match(/index_(\d+)\.html$/i)?.[1] || 0);
      for (const anchor of document.querySelectorAll('a[href]')) {
        if (reachedLatest) break;
        if (!/更多章节|下一页/.test(anchor.textContent)) continue;
        const url = urlFor(anchor.getAttribute('href'));
        const next = url?.pathname.match(/\/index_(\d+)\.html$/i);
        if (!next) continue;
        if (Number(next[1]) !== currentPage + 1) return { kind: 'content_incomplete', url: location.href };
        if (!nextCatalog) nextCatalog = url.href;
      }
      const title = clean(document.querySelector('meta[property="og:novel:book_name"]')?.content
        || document.querySelector('.info h1')?.textContent);
      const author = clean(document.querySelector('meta[property="og:novel:author"]')?.content);
      if (!title) return { kind: 'structure_mismatch', url: location.href };
      return { kind: 'catalog', url: location.href, title, author, chapters, nextCatalog };
    }
    const current = chapterPath(new URL(location.href));
    const node = document.querySelector('#htmlContent.chapter-content');
    if (!current || !node || getComputedStyle(node).display === 'none') return { kind: 'structure_mismatch', url: location.href };
    const title = clean(document.querySelector('#content > h1')?.textContent);
    const text = clean(node.innerText);
    if (!title || text.length < 40) return { kind: 'content_incomplete', url: location.href };
    const continuations = [];
    let invalid = false;
    for (const anchor of document.querySelectorAll('a[href]')) {
      const url = urlFor(anchor.getAttribute('href'));
      const match = chapterPath(url);
      const explicit = /下一页|继续阅读/.test(anchor.textContent);
      if (match && match[1] === current[1]) {
        if (Number(match[2] || 0) === Number(current[2] || 0) + 1) continuations.push(url.href);
        else if (/下一章|下一页|继续阅读/.test(anchor.textContent)) invalid = true;
      } else if (explicit) invalid = true;
    }
    const next = [...new Set(continuations)];
    if (invalid || next.length > 1 || (/本章未完|本章未结束/.test(text) && !next.length)) {
      return { kind: 'content_incomplete', url: location.href };
    }
    return { kind: 'chapter', url: location.href, title, text, next: next[0] || null };
  };
})();
