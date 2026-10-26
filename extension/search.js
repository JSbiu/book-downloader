import {
  canonicalBookUrl,
  cleanBookTitle,
  REGISTERED_SOURCE_HOSTS,
  registeredSourceForUrl,
  sourceForUrl
} from './source-rules.js';

const SEARCH_URL = 'https://api.deepseek.com/v1/tools/search';
const MAX_QUERY_LENGTH = 128;
const MAX_API_RESULTS = 50;
const REQUEST_TIMEOUT = 15000;
const NOVEL_INTENT = '小说 章节目录';
const COMPACT_NOVEL_INTENT = '小说 目录';
const MINIMUM_NOVEL_INTENT = '小说';
const ORI_QUESTION = '寻找小说书籍详情、章节目录或正文入口；不要推书、讨论、资讯或推广页面。';
const EXCLUDED_HOSTS = [
  'xiaoshuo.qq.com',
  'qidian.com',
  'qdmm.com',
  'book.qq.com',
  'yuedu.qq.com',
  'qqreader.qq.com',
  'm.sohu.com',
  'ngabbs.com'
];

const ERROR_CODES = new Set([
  'fetch_impl_missing',
  'query_too_long',
  'authentication_failed',
  'insufficient_balance',
  'rate_limited',
  'gateway_timeout',
  'network_error',
  'timeout',
  'invalid_response',
  'aborted'
]);

const FORUM_HOST = /^(?:bbs|forum|club|tieba)(?:\.|$)/i;
const FORUM_PATH = /(?:^|\/)(?:viewthread\.php|showthread\.php|threads?|forums?|topics?|posts?|discussion|viewtopic\.php|showtopic)(?:\/|$)/i;
const NON_BOOK_PATH = /(?:^|\/)(?:article|news|press|promotion|promo|advert|ads?|shop|product|mall)(?:\/|$)/i;
const NON_BOOK_TEXT = /(?:推书|书单|书荒|求书|论坛|帖子|讨论区|资讯|新闻|广告|推广|促销|优惠|贴吧)/i;
const CHAPTER_HEADING = /(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回集卷篇部]|序章|楔子|番外|前言)/iu;
const AUTHOR_FIELD = /作者\s*[:：]\s*([^|｜,，。;；\s]{1,40})/u;
const candidateEntryRank = new WeakMap();
const candidateRegisteredKey = new WeakMap();
const EXCLUSION_REASONS = [
  'invalid_url',
  'platform_domain',
  'empty_title',
  'forum_or_promotion',
  'unregistered_source',
  'title_mismatch',
  'evidence_insufficient'
];
const emptyExclusions = () => Object.fromEntries(EXCLUSION_REASONS.map(reason => [reason, 0]));

export class SearchError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = 'SearchError';
    this.code = ERROR_CODES.has(code) ? code : 'network_error';
  }
}

function searchError(code) {
  return new SearchError(code);
}

function normalizeText(value) {
  return String(value ?? '').normalize('NFKC').replace(/\s+/g, ' ').trim();
}

function stripWrappingTitleMarks(value) {
  return value
    .replace(/^[《〈「『【\[]+\s*/, '')
    .replace(/\s*[》〉」』】\]]+$/, '')
    .trim();
}

function queryTitle(value) {
  return stripWrappingTitleMarks(normalizeText(value));
}

function workTitle(value) {
  const title = cleanBookTitle(value);
  const chapter = /第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回集卷篇部]/iu.exec(title);
  return (chapter ? title.slice(0, chapter.index) : title)
    .replace(/[\s\-–—|｜:：]+$/u, '')
    .trim();
}

function escapeQueryText(value) {
  return normalizeText(value)
    .replace(/[《〈「『【\[]+\s*/g, ' ')
    .replace(/\s*[》〉」』】\]]+$/g, ' ')
    .replace(/["\\\u0000-\u001f\u007f]/g, ' ')
    .trim();
}

function withinQueryLimit(value) {
  return [...value].length <= MAX_QUERY_LENGTH;
}

function normalizedUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
  url.hash = '';
  for (const key of [...url.searchParams.keys()]) {
    if (/^(?:utm_.+|fbclid|gclid|mc_cid|mc_eid)$/i.test(key)) {
      url.searchParams.delete(key);
    }
  }
  url.hostname = url.hostname.toLowerCase();
  return url.href;
}

function isExcludedHost(value) {
  let hostname;
  try {
    hostname = new URL(value).hostname.replace(/\.$/, '').toLowerCase();
  } catch {
    return false;
  }
  return EXCLUDED_HOSTS.some(host => hostname === host || hostname.endsWith(`.${host}`));
}

function evidenceText(result) {
  return [result.snippet, result.summary, result.content]
    .map(normalizeText)
    .filter(Boolean)
    .join(' \n ');
}

function resultAuthor(result, evidence = evidenceText(result)) {
  if (result.author != null) {
    const author = normalizeText(result.author);
    if (author) return author;
  }
  const labeled = evidence.match(AUTHOR_FIELD);
  return labeled ? normalizeText(labeled[1]) : null;
}

function newRound(phase) {
  return {
    phase,
    raw: 0,
    filtered: 0,
    unique: 0,
    duplicates_removed: 0,
    exclusions: emptyExclusions()
  };
}

function candidateFrom(result, reject = () => {}) {
  const rawUrl = normalizedUrl(result.url);
  if (!rawUrl) {
    reject('invalid_url');
    return null;
  }
  if (isExcludedHost(rawUrl)) {
    reject('platform_domain');
    return null;
  }
  const title = cleanBookTitle(result.title);
  if (!title) {
    reject('empty_title');
    return null;
  }
  const source = sourceForUrl(rawUrl);
  const registered = registeredSourceForUrl(rawUrl);
  const sourceUrl = source ? canonicalBookUrl(rawUrl) : null;
  const evidence = evidenceText(result);
  const candidate = {
    id: sourceUrl || rawUrl,
    url: source ? source.catalogUrl : rawUrl,
    title: workTitle(title),
    author: resultAuthor(result, evidence),
    sourceId: source ? source.id : null,
    registeredSourceId: registered?.id || null,
    registeredSourceName: registered?.name || null,
    supported: Boolean(source),
    snippet: evidence,
    exact: false
  };
  candidateEntryRank.set(candidate, rawEntryRank(rawUrl, registered));
  if (registered) candidateRegisteredKey.set(candidate, `${registered.id}:${registered.bookKey}`);
  return candidate;
}

function candidateUrl(candidate) {
  try {
    return new URL(candidate.url);
  } catch {
    return null;
  }
}

function rawEntryRank(value, registered = registeredSourceForUrl(value)) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return 2;
  }
  if (sourceForUrl(value) && (/^\/bqg\/\d+\/?(?:\.html?)?$/i.test(url.pathname)
      || /^\/books\/\d+\/(?:index_\d+\.html)?$/i.test(url.pathname))) return 0;
  if (sourceForUrl(value)) return 1;
  if (registered) return 0;
  if (/(?:^|\/)(?:book|books|novel|novels|bqg|catalog|directory|list)(?:[\/._-]|$)/i.test(url.pathname)) return 0;
  if (/(?:^|\/)(?:chapter|chapters|read|reader|txt)(?:[\/._-]|$)/i.test(url.pathname)) return 1;
  return 2;
}

function isForumOrNonBookPage(candidate) {
  const url = candidateUrl(candidate);
  if (!url) return true;
  if (/(?:^|\.)promo\./i.test(url.hostname.replace(/^www\./i, ''))) return true;
  if (FORUM_HOST.test(url.hostname.replace(/^www\./i, '')) || FORUM_PATH.test(url.pathname)
      || (/\bread\.php$/i.test(url.pathname) && url.searchParams.has('tid'))
      || NON_BOOK_PATH.test(url.pathname)) {
    return true;
  }
  return NON_BOOK_TEXT.test(candidate.title);
}

function titleMatches(candidate, normalizedQuery) {
  return workTitle(candidate.title).includes(normalizedQuery);
}

function authorMatches(candidate, normalizedQuery) {
  return Boolean(candidate.author?.includes(normalizedQuery));
}

function metadataEvidence(candidate) {
  const text = normalizeText(`${candidate.title} ${candidate.snippet}`);
  const chapter = CHAPTER_HEADING.test(text);
  const author = Boolean(candidate.author);
  const directory = /(?:章节目录|章节列表|目录|连载|完结|字数|更新至|作品简介|内容简介)/iu.test(text);
  return {
    chapter,
    author,
    strongMetadata: author && (chapter || directory)
  };
}

function isBookCandidate(candidate, normalizedQuery, {
  allowUnverifiedExact = false,
  reject = () => {}
} = {}) {
  if (isForumOrNonBookPage(candidate)) {
    reject('forum_or_promotion');
    return false;
  }
  if (!normalizedQuery) {
    reject('title_mismatch');
    return false;
  }

  if (candidate.registeredSourceId) {
    if (titleMatches(candidate, normalizedQuery) || authorMatches(candidate, normalizedQuery)) return true;
    reject('title_mismatch');
    return false;
  }

  const titleMatch = titleMatches(candidate, normalizedQuery);
  const authorMatch = authorMatches(candidate, normalizedQuery);
  if (!titleMatch && !authorMatch) {
    reject('title_mismatch');
    return false;
  }

  const evidence = metadataEvidence(candidate);
  if (!evidence.strongMetadata) {
    if (allowUnverifiedExact && titleMatch && workTitle(candidate.title) === normalizedQuery) {
      candidate.identityUnverified = true;
      return true;
    }
    reject('evidence_insufficient');
    return false;
  }
  return true;
}

function targetedCandidate(result, title, reject) {
  const candidate = candidateFrom(result, reject);
  if (!candidate) return null;
  if (!candidate.registeredSourceId) {
    reject('unregistered_source');
    return null;
  }
  candidate.exact = workTitle(candidate.title) === title;
  return isBookCandidate(candidate, title, { reject }) ? candidate : null;
}

function localCandidate(result, title, reject) {
  const candidate = candidateFrom(result, reject);
  if (!candidate) return null;
  candidate.exact = workTitle(candidate.title) === title;
  return isBookCandidate(candidate, title, { allowUnverifiedExact: true, reject }) ? candidate : null;
}

function candidateKey(candidate) {
  const registeredKey = candidateRegisteredKey.get(candidate);
  if (registeredKey) return registeredKey;
  const registered = registeredSourceForUrl(candidate.url);
  if (registered) return `${registered.id}:${registered.bookKey}`;
  return candidate.supported ? candidate.id : candidate.url;
}

function mergeCandidates(target, incoming) {
  if (!target) return incoming;
  const targetRank = candidateEntryRank.get(target) ?? 2;
  const incomingRank = candidateEntryRank.get(incoming) ?? 2;
  if (incomingRank < targetRank) {
    target.title = incoming.title;
    target.snippet = incoming.snippet;
    candidateEntryRank.set(target, incomingRank);
  }
  if (target.snippet === '') target.snippet = incoming.snippet;
  if ((target.author && incoming.author && target.author !== incoming.author)
      || target.identityConflict || incoming.identityConflict) target.identityConflict = true;
  if (target.identityConflict) target.author = null;
  if (target.author === null && !target.identityConflict) target.author = incoming.author;
  if (target.registeredSourceId === null) {
    target.registeredSourceId = incoming.registeredSourceId;
    target.registeredSourceName = incoming.registeredSourceName;
  }
  if (incoming.exact) target.url = incoming.url;
  target.exact ||= incoming.exact;
  return target;
}

function deduplicate(candidates) {
  const byKey = new Map();
  for (const candidate of candidates) {
    const key = candidateKey(candidate);
    byKey.set(key, mergeCandidates(byKey.get(key), candidate));
  }
  return [...byKey.values()];
}

function candidatesFrom(results, title, targeted, round) {
  const candidates = [];
  const authorsByBook = new Map();
  round.raw = results.length;
  const reject = reason => {
    round.exclusions[reason] += 1;
  };
  for (const result of results) {
    const bookId = canonicalBookUrl(result.url);
    const author = resultAuthor(result);
    if (bookId && author) {
      if (!authorsByBook.has(bookId)) authorsByBook.set(bookId, new Set());
      authorsByBook.get(bookId).add(author);
    }
    const candidate = targeted
      ? targetedCandidate(result, title, reject)
      : localCandidate(result, title, reject);
    if (candidate) candidates.push(candidate);
  }
  round.filtered = candidates.length;
  const consolidated = deduplicate(candidates);
  round.unique = consolidated.length;
  round.duplicates_removed = candidates.length - consolidated.length;
  for (const candidate of consolidated) {
    if (candidate.supported && authorsByBook.get(candidate.id)?.size > 1) {
      candidate.identityConflict = true;
      candidate.author = null;
    }
  }
  return consolidated;
}

function hasAuthorConflict(candidates) {
  if (candidates.some(candidate => candidate.identityConflict)) return true;
  const authors = new Set(candidates
    .filter(candidate => candidate.exact && candidate.author)
    .map(candidate => candidate.author.normalize('NFKC').toLocaleLowerCase()));
  return authors.size > 1;
}

function preferredCandidate(candidates, blockedCandidates = []) {
  const exact = candidates.filter(candidate => candidate.exact);
  const identity = [...exact, ...blockedCandidates.filter(candidate => candidate.exact)];
  return exact.length === 1 && exact[0].supported && !exact[0].identityUnverified
    && !hasAuthorConflict(identity) ? exact[0] : null;
}

function entryRank(candidate) {
  return candidateEntryRank.get(candidate)
    ?? rawEntryRank(candidate.url, registeredSourceForUrl(candidate.url));
}

function categoryRank(candidate) {
  if (candidate.exact && candidate.supported) return 0;
  if (candidate.supported) return 1;
  if (candidate.registeredSourceId) return candidate.exact ? 2 : 3;
  return 4;
}

function diversifyByHost(candidates) {
  const categories = new Map();
  for (const candidate of candidates) {
    const category = categoryRank(candidate);
    if (!categories.has(category)) categories.set(category, new Map());
    const hosts = categories.get(category);
    const host = (candidateUrl(candidate)?.hostname || '').replace(/\.$/, '').replace(/^(?:www|m|wap|mobile)\./i, '');
    if (!hosts.has(host)) hosts.set(host, []);
    hosts.get(host).push(candidate);
  }

  const ordered = [];
  for (const category of [...categories.keys()].sort((left, right) => left - right)) {
    const groups = [...categories.get(category).values()].map(group => group
      .map((candidate, index) => ({ candidate, index }))
      .sort((left, right) => entryRank(left.candidate) - entryRank(right.candidate)
        || left.index - right.index)
      .map(item => item.candidate));
    for (let index = 0; groups.some(group => index < group.length); index += 1) {
      for (const group of groups) {
        if (group[index]) ordered.push(group[index]);
      }
    }
  }
  return ordered;
}

function groupByTitle(candidates) {
  const byTitle = new Map();
  for (const candidate of candidates) {
    const key = workTitle(candidate.title);
    if (!byTitle.has(key)) byTitle.set(key, []);
    byTitle.get(key).push(candidate);
  }
  return [...byTitle.values()].flatMap(group => group
    .map((candidate, index) => ({ candidate, index }))
    .sort((left, right) => {
      const leftAuthor = normalizeText(left.candidate.author).normalize('NFKC').toLocaleLowerCase();
      const rightAuthor = normalizeText(right.candidate.author).normalize('NFKC').toLocaleLowerCase();
      if (!leftAuthor || !rightAuthor) return (leftAuthor ? 0 : 1) - (rightAuthor ? 0 : 1);
      return leftAuthor.localeCompare(rightAuthor, 'zh-CN')
        || categoryRank(left.candidate) - categoryRank(right.candidate)
        || entryRank(left.candidate) - entryRank(right.candidate)
        || left.index - right.index;
    })
    .map(item => item.candidate));
}

function cleanQuery(value) {
  return normalizeText(value).replace(/\s+/g, ' ');
}

function targetedQuery(title) {
  const phrase = `"${escapeQueryText(title)}"`;
  const ranges = [
    REGISTERED_SOURCE_HOSTS,
    REGISTERED_SOURCE_HOSTS.filter(host => ['23txxt.com', 'trxs.cc', 'bixiange.top'].includes(host)),
    REGISTERED_SOURCE_HOSTS.filter(host => ['23txxt.com', 'trxs.cc'].includes(host)),
    REGISTERED_SOURCE_HOSTS.filter(host => host === '23txxt.com')
  ];
  for (const hosts of ranges) {
    for (const intent of [NOVEL_INTENT, COMPACT_NOVEL_INTENT, MINIMUM_NOVEL_INTENT]) {
      const scope = hosts.map(host => `site:${host}`).join(' OR ');
      const query = `${phrase} ${intent} ${scope}`;
      if (withinQueryLimit(query)) return query;
    }
  }
  return null;
}

function broadQuery(title) {
  const phrase = `"${escapeQueryText(title)}"`;
  let query = `${phrase} ${NOVEL_INTENT}`;
  if (!withinQueryLimit(query)) query = `${phrase} ${COMPACT_NOVEL_INTENT}`;
  if (!withinQueryLimit(query)) query = `${phrase} ${MINIMUM_NOVEL_INTENT}`;
  if (!withinQueryLimit(query)) return null;

  for (const host of EXCLUDED_HOSTS) {
    const candidate = `${query} -site:${host}`;
    if (withinQueryLimit(candidate)) query = candidate;
  }
  return query;
}

function oriQuestion(title) {
  const withTitle = `寻找《${escapeQueryText(title)}》的小说书籍详情、章节目录或正文入口；不要推书、讨论、资讯或推广页面。`;
  return withinQueryLimit(withTitle) ? withTitle : ORI_QUESTION;
}

function timeoutSignal(signal) {
  const controller = new AbortController();
  let callerAborted = false;
  let timedOut = false;
  const abort = () => {
    callerAborted = true;
    controller.abort(signal.reason);
  };
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT);
  return {
    signal: controller.signal,
    finish() {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
    },
    error() {
      if (callerAborted) return searchError('aborted');
      if (timedOut) return searchError('timeout');
      return null;
    }
  };
}

function responseError(status) {
  if (status === 401) return searchError('authentication_failed');
  if (status === 402) return searchError('insufficient_balance');
  if (status === 429) return searchError('rate_limited');
  if (status === 504) return searchError('gateway_timeout');
  return searchError('network_error');
}

function responseResults(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.results)) {
    throw searchError('invalid_response');
  }
  const results = [];
  for (const row of value.results) {
    if (!row || typeof row !== 'object') throw searchError('invalid_response');
    if (typeof row.url !== 'string' || typeof row.title !== 'string') {
      throw searchError('invalid_response');
    }
    results.push(row);
  }
  return results;
}

async function request(apiKey, query, question, fetchImpl, signal) {
  if (signal?.aborted) throw searchError('aborted');
  const requestSignal = timeoutSignal(signal);
  try {
    let response;
    try {
      response = await fetchImpl(SEARCH_URL, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          query,
          ori_question: question,
          count: MAX_API_RESULTS,
          need_content: false
        }),
        signal: requestSignal.signal
      });
    } catch {
      throw requestSignal.error() || searchError('network_error');
    }
    if (!response || typeof response.status !== 'number') throw searchError('network_error');
    if (!response.ok) throw responseError(response.status);
    let value;
    try {
      value = await response.json();
    } catch {
      throw requestSignal.error() || searchError('invalid_response');
    }
    return responseResults(value);
  } finally {
    requestSignal.finish();
  }
}

async function filterBlocked(candidates, isBlocked, blockedCandidates) {
  if (typeof isBlocked !== 'function') return candidates;
  const visible = [];
  for (const candidate of candidates) {
    if (await isBlocked(candidate)) blockedCandidates.push(candidate);
    else visible.push(candidate);
  }
  return visible;
}

function buildDiagnostics({
  rounds,
  mergeInput,
  merged,
  visible,
  blockedCandidates,
  cachedCandidates = 0,
  cacheUse = 'none',
  broadened
}) {
  return {
    rounds,
    totals: {
      raw: rounds.reduce((sum, round) => sum + round.raw, 0),
      filtered: rounds.reduce((sum, round) => sum + round.filtered, 0),
      exclusions: Object.fromEntries(EXCLUSION_REASONS.map(reason =>
        [reason, rounds.reduce((sum, round) => sum + round.exclusions[reason], 0)]))
    },
    merge: {
      input: mergeInput,
      unique: merged.length,
      duplicates_removed: mergeInput - merged.length
    },
    identity_conflicts: {
      note: 'included_in_final_candidates',
      count: merged.filter(candidate => candidate.identityConflict).length
    },
    blocked: {
      input: merged.length,
      removed: blockedCandidates.length,
      remaining: visible.length
    },
    cached_candidates: cachedCandidates,
    final_candidates: visible.length,
    broadened,
    cache_use: cacheUse
  };
}

function diagnosticsForEmpty() {
  return buildDiagnostics({
    rounds: [],
    mergeInput: 0,
    merged: [],
    visible: [],
    blockedCandidates: [],
    broadened: false
  });
}

function result(value, candidates, calls, broadened, blockedCandidates = [], reused = false) {
  const consolidated = deduplicate(candidates);
  const preferred = preferredCandidate(consolidated, blockedCandidates);
  const valueResult = {
    query: value,
    candidates: groupByTitle(diversifyByHost(consolidated)).flat(),
    preferredId: preferred ? preferred.id : null,
    calls,
    broadened
  };
  if (reused) valueResult.reused = true;
  return valueResult;
}

export async function searchBooks(query, {
  apiKey,
  fetchImpl,
  signal,
  includeOtherSources = false,
  isBlocked,
  previousResult,
  onDiagnostics
} = {}) {
  if (typeof fetchImpl !== 'function') throw searchError('fetch_impl_missing');
  const value = cleanQuery(query);
  if ([...value].length > MAX_QUERY_LENGTH) throw searchError('query_too_long');
  if (signal?.aborted) throw searchError('aborted');
  if (!value) {
    onDiagnostics?.(diagnosticsForEmpty());
    return { query: value, candidates: [], preferredId: null, calls: 0, broadened: false };
  }

  const title = queryTitle(value);
  if (!title) {
    onDiagnostics?.(diagnosticsForEmpty());
    return { query: value, candidates: [], preferredId: null, calls: 0, broadened: false };
  }
  const broad = broadQuery(title);
  if (!broad) throw searchError('query_too_long');
  const question = oriQuestion(title);

  let calls = 0;
  let candidates = [];
  const blockedCandidates = [];
  const rounds = [];
  const notify = round => {
    rounds.push(round);
  };
  const reused = includeOtherSources && previousResult?.query === value && previousResult.broadened === false
    && Array.isArray(previousResult.candidates);
  try {
    if (reused) {
      candidates = [...previousResult.candidates];
    } else {
      const targeted = targetedQuery(title);
      if (targeted) {
        calls += 1;
        const round = newRound('targeted');
        const targetedCandidates = candidatesFrom(
          await request(apiKey, targeted, question, fetchImpl, signal),
          title,
          true,
          round
        );
        if (!includeOtherSources) {
          const visible = await filterBlocked(targetedCandidates, isBlocked, blockedCandidates);
          if (preferredCandidate(visible, blockedCandidates)) {
            round.blocked = blockedCandidates.length;
            notify(round);
            onDiagnostics?.(buildDiagnostics({
              rounds,
              mergeInput: targetedCandidates.length,
              merged: targetedCandidates,
              visible,
              blockedCandidates,
              broadened: false,
              cacheUse: 'fresh_targeted'
            }));
            return result(value, visible, calls, false, blockedCandidates);
          }
        }
        round.blocked = blockedCandidates.length;
        notify(round);
        candidates.push(...targetedCandidates);
      }
    }

    calls += 1;
    const round = newRound('broad');
    candidates.push(...candidatesFrom(
      await request(apiKey, broad, question, fetchImpl, signal),
      title,
      false,
      round
    ));
    notify(round);
  } catch (error) {
    if (error instanceof SearchError) throw error;
    if (signal?.aborted) throw searchError('aborted');
    throw searchError('network_error');
  }

  const consolidated = deduplicate(candidates);
  const filtered = await filterBlocked(consolidated, isBlocked, blockedCandidates);
  onDiagnostics?.(buildDiagnostics({
    rounds,
    mergeInput: candidates.length,
    merged: consolidated,
    visible: filtered,
    blockedCandidates,
    cachedCandidates: reused ? previousResult.candidates.length : 0,
    broadened: true,
    cacheUse: reused ? 'previous_targeted' : 'fresh_broad'
  }));
  return result(value, filtered, calls, true, blockedCandidates, reused);
}
