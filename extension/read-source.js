(() => {
  const clean = value => String(value || '').replace(/\u00a0/g, ' ').replace(/\r/g, '').trim();
  const urlFor = value => {
    try {
      const url = new URL(value, location.href);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      if (!['23txxt.com', 'www.23txxt.com'].includes(url.hostname)) return null;
      url.hash = '';
      return url;
    } catch { return null; }
  };
  const bookPath = location.pathname.match(/^\/bqg\/(\d+)\//)?.[1];
  const sameBook = url => url && url.pathname.startsWith(`/bqg/${bookPath}/`);
  const chapterNumber = title => {
    const match = title.match(/第\s*(\d+)\s*[章节回]/) || title.match(/^\s*(\d+)[.、\s]/)
      || title.match(/^\s*(\d+)(?=[\u4e00-\u9fff])/);
    return match ? Number(match[1]) : 0;
  };
  const notices = () => {
    const title = document.title || '';
    const text = document.body?.innerText || '';
    if (document.querySelector('#cf-challenge-running, #challenge-form, .cf-turnstile, iframe[src*="challenges.cloudflare.com"]')
        || /Just a moment|验证您是真人|人机验证|安全验证/i.test(title)) return 'needs_action';
    if (/Access denied|Forbidden|拒绝访问/i.test(title) || /^\s*(403|Access denied|Forbidden)\b/i.test(text)) return 'access_denied';
    return null;
  };
  const content = () => document.querySelector('#content, .read_chapterDetail, .readContent, .showtxt, .novel_content, .box_box, .content');

  globalThis.shuyeReadSource = kind => {
    const notice = notices();
    if (notice) return { kind: notice, url: location.href };
    if (!bookPath) return { kind: 'structure_mismatch', url: location.href };
    if (kind === 'catalog') {
      const anchors = document.querySelectorAll('#list dl dd a, .listmain dl dd a, .book_list ul li a, #chapterList li a, #all_chapter a, #catalog a, #chapterlist a, .booklist a');
      const numbered = new Map(), special = [], seen = new Set();
      let start;
      for (const anchor of anchors) {
        const url = urlFor(anchor.getAttribute('href'));
        const title = clean(anchor.textContent);
        if (!sameBook(url) || !/\/[^/]+\.html?$/i.test(url.pathname) || seen.has(url.href)) continue;
        const number = chapterNumber(title);
        if (number) {
          if (!numbered.has(number)) numbered.set(number, { title, url: url.href, number });
          seen.add(url.href);
          continue;
        }
        if (/月票|上架感言|求票|抽奖|请假|推书|公告/.test(title)) continue;
        if (title === '开始阅读') { start = { title, url: url.href }; seen.add(url.href); continue; }
        if (/^(?:番外|完本感言|后记|序章|楔子|前言)/.test(title)) {
          special.push({ title, url: url.href });
          seen.add(url.href);
        }
      }
      if (!numbered.has(1) && start) numbered.set(1, { ...start, number: 1 });
      const chapters = [...numbered.values()].sort((a, b) => a.number - b.number).concat(special);
      if (!chapters.length) return { kind: 'structure_mismatch', url: location.href };
      const title = clean(document.querySelector('meta[property="og:novel:book_name"]')?.content
        || document.querySelector('#info h1, .book-info h1, h1')?.textContent || '未命名书籍');
      const author = clean(document.querySelector('meta[property="og:novel:author"]')?.content
        || [...document.querySelectorAll('#info p, .book-info p')].find(p => /作\s*者/.test(p.textContent))?.textContent?.replace(/^.*?作\s*者\s*[:：]\s*/, ''));
      return { kind: 'catalog', url: location.href, title, author, chapters };
    }
    const node = content();
    if (!node) return { kind: 'structure_mismatch', url: location.href };
    const copy = node.cloneNode(true);
    copy.querySelectorAll('script, style, iframe, ins, .ads, .advertisement').forEach(item => item.remove());
    copy.querySelectorAll('br').forEach(item => item.replaceWith('\n'));
    copy.querySelectorAll('p, div').forEach(item => item.append('\n'));
    const raw = clean(copy.textContent);
    const unfinished = /本章未完[，,]?\s*请点击下一页继续阅读/.test(raw);
    let title = clean(document.querySelector('.bookname h1, .reader h1, h1')?.textContent);
    const lines = raw.split('\n').map(clean).filter(Boolean).filter(line => {
      const heading = line.match(/^\s*(?:\d+\.)?\s*(.+?)\s*[（(]第\s*\d+\s*\/\s*\d+\s*页[）)]\s*$/);
      if (heading) { title = clean(heading[1]); return false; }
      return line !== '二三书库' && !/^[（(]?本章未完[，,]?\s*请点击下一页继续阅读[）)]?$/.test(line);
    });
    while (lines[0] === title) lines.shift();
    const text = lines.join('\n');
    if (text.length < 40) return { kind: 'content_incomplete', url: location.href };
    const stem = location.pathname.replace(/_\d+(?=\.html?$)/i, '');
    const pageNo = Number(location.pathname.match(/_(\d+)\.html?$/i)?.[1] || 1);
    const continuations = [];
    let invalidContinuation = false;
    for (const anchor of document.querySelectorAll('a[href]')) {
      const label = clean(anchor.textContent);
      const url = urlFor(anchor.getAttribute('href'));
      const explicit = /下一页|继续阅读/.test(label) && !/下一章/.test(label);
      if (!url) { if (explicit) invalidContinuation = true; continue; }
      const candidateStem = url.pathname.replace(/_\d+(?=\.html?$)/i, '');
      const candidateNo = Number(url.pathname.match(/_(\d+)\.html?$/i)?.[1] || 1);
      if (sameBook(url) && candidateStem === stem && candidateNo > pageNo) continuations.push({ url: url.href, page: candidateNo });
      else if (explicit) invalidContinuation = true;
    }
    continuations.sort((a, b) => a.page - b.page);
    const next = continuations[0]?.url || null;
    if (invalidContinuation || (unfinished && !next)) return { kind: 'content_incomplete', url: location.href };
    return { kind: 'chapter', url: location.href, title, text, next };
  };
})();
