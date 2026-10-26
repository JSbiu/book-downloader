const REGISTERED_SOURCES = [
  {
    id: 'txxt',
    name: '23txxt',
    hosts: ['23txxt.com', 'www.23txxt.com'],
    queryHost: '23txxt.com',
    downloadable: true,
    paths: [/^\/bqg\/\d+(?:\.html?\/?|\/(?:[^/]+)?)?$/i]
  },
  {
    id: 'trxs',
    name: 'trxs.cc',
    hosts: ['trxs.cc', 'www.trxs.cc'],
    queryHost: 'trxs.cc',
    downloadable: false,
    paths: [/^\/tongren\/\d+(?:\.html?|\/(?:\d+(?:_\d+)?\.html?)?)?\/?$/i]
  },
  {
    id: 'bixiange',
    name: '笔仙阁',
    hosts: ['bixiange.top', 'www.bixiange.top', 'bxg123.top', 'www.bxg123.top',
      'bixiange.me', 'www.bixiange.me'],
    queryHost: 'bixiange.top',
    downloadable: false,
    paths: [/^\/[^/]+\/\d+(?:\/(?:index\/)?\d+\.html?)?\/?$/i]
  },
  {
    id: 'shuba',
    name: '69shuba',
    hosts: ['69shuba.com', 'www.69shuba.com'],
    queryHost: '69shuba.com',
    downloadable: false,
    paths: [/^\/book\/\d+(?:\.html?)?\/?$/i, /^\/txt\/\d+\/[^/]+$/i]
  },
  {
    id: 'wxdy',
    name: 'wxdy.cc',
    hosts: ['wxdy.cc', 'www.wxdy.cc'],
    queryHost: 'wxdy.cc',
    downloadable: true,
    paths: [/^\/books\/\d+\/(?:index_\d+\.html|\d+(?:_\d+)?\.html)?$/i]
  }
];

const registeredHostMap = new Map(REGISTERED_SOURCES.flatMap(source =>
  source.hosts.map(host => [host, source])));
const SEO_TAIL = /(?:\s*[-–—|｜]\s*(?:最新章节|全文阅读|无弹窗|免费阅读|手机版|无弹窗手机版|笔趣阁))+\s*$/iu;
const SEO_INLINE_TAIL = /\s*(?:最新章节|全文阅读|无弹窗手机版|手机版|无弹窗|免费阅读|笔趣阁)\s*$/iu;
const HOST_ALIAS = /^(?:www|m|wap|mobile)\./i;

export const REGISTERED_SOURCE_HOSTS = REGISTERED_SOURCES.map(source => source.queryHost);

function normalizedHost(url) {
  return url.hostname.replace(/\.$/, '').toLowerCase();
}

function registeredHost(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
  const hostname = normalizedHost(url);
  return { url, hostname, source: registeredHostMap.get(hostname) || null };
}

export function sourceKeyForUrl(value) {
  const found = registeredHost(value);
  if (!found) return null;
  if (found.source) return found.source.id;
  const hostname = found.hostname.replace(HOST_ALIAS, '');
  return registeredHostMap.get(hostname)?.id || hostname;
}

export function cleanBookTitle(value) {
  const clean = text => String(text ?? '')
    .normalize('NFKC')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^[《〈「『【\[]+\s*/, '')
    .replace(/\s*[》〉」』】\]]+$/, '')
    .trim()
    .replace(SEO_TAIL, '')
    .replace(SEO_INLINE_TAIL, '')
    .trim();
  return clean(clean(value));
}

function registeredBookKey(sourceId, url) {
  const path = url.pathname;
  if (sourceId === 'wxdy') return path.match(/^\/books\/(\d+)\//i)?.[1] || null;
  if (sourceId === 'txxt') {
    const match = path.match(/^\/bqg\/(\d+)(?:\.html?\/?|\/(?:[^/]+)?)?$/i);
    return match ? match[1] : null;
  }
  if (sourceId === 'trxs') {
    const match = path.match(
      /^\/tongren\/(\d+)(?:\.html?|\/(?:\d+(?:_\d+)?\.html?)?)?\/?$/i
    );
    return match ? match[1] : null;
  }
  if (sourceId === 'bixiange') {
    const match = path.match(
      /^\/([^/]+)\/(\d+)(?:\/(?:index\/)?\d+\.html?)?\/?$/i
    );
    return match ? `${match[1].toLowerCase()}/${match[2]}` : null;
  }
  if (sourceId === 'shuba') {
    const book = path.match(/^\/book\/(\d+)(?:\.html?)?\/?$/i);
    if (book) return book[1];
    const chapter = path.match(/^\/txt\/(\d+)\/[^/]+$/i);
    return chapter ? chapter[1] : null;
  }
  return null;
}

export function registeredSourceForUrl(value) {
  const found = registeredHost(value);
  if (!found?.source || !found.source.paths.some(pattern => pattern.test(found.url.pathname))) return null;
  const bookKey = registeredBookKey(found.source.id, found.url);
  if (!bookKey) return null;
  return {
    id: found.source.id,
    name: found.source.name,
    hostname: found.hostname,
    downloadable: found.source.downloadable,
    bookKey
  };
}

export function sourceForUrl(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
  if (['wxdy.cc', 'www.wxdy.cc'].includes(url.hostname.toLowerCase())) {
    const book = url.pathname.match(/^\/books\/(\d+)\/(?:index_\d+\.html|\d+(?:_\d+)?\.html)?$/i);
    if (!book) return null;
    url.hash = '';
    return { id: 'wxdy', url: url.href, catalogUrl: `${url.origin}/books/${book[1]}/`, bookNumber: book[1] };
  }
  if (!['23txxt.com', 'www.23txxt.com'].includes(url.hostname.toLowerCase())) return null;
  const match = url.pathname.match(/^\/bqg\/(\d+)(?:\.html?\/?|\/(?:[^/]+)?)?$/i);
  if (!match) return null;
  url.hash = '';
  return {
    id: 'txxt', url: url.href,
    catalogUrl: `${url.origin}/bqg/${match[1]}/`,
    bookNumber: match[1]
  };
}

export function canonicalBookUrl(value) {
  const source = sourceForUrl(value);
  if (source?.id === 'wxdy') return `https://www.wxdy.cc/books/${source.bookNumber}/`;
  return source ? `https://www.23txxt.com/bqg/${source.bookNumber}/` : null;
}

export function canonicalChapterUrl(value) {
  const source = sourceForUrl(value);
  if (!source) return null;
  const url = new URL(source.url);
  url.hostname = source.id === 'wxdy' ? 'www.wxdy.cc' : 'www.23txxt.com';
  url.protocol = 'https:';
  url.pathname = url.pathname.replace(/_\d+(?=\.html?$)/i, '');
  url.searchParams.sort();
  return url.href;
}

export function samePage(expected, actual) {
  const left = sourceForUrl(expected), right = sourceForUrl(actual);
  if (!left || !right || left.id !== right.id || left.bookNumber !== right.bookNumber) return false;
  const normalize = value => {
    const url = new URL(value);
    url.protocol = 'https:';
    url.hostname = left.id === 'wxdy' ? 'www.wxdy.cc' : 'www.23txxt.com';
    url.hash = '';
    url.searchParams.sort();
    return url.href;
  };
  return normalize(expected) === normalize(actual);
}
