import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SearchError, searchBooks } from '../../extension/search.js';
import {
  canonicalBookUrl,
  canonicalChapterUrl,
  cleanBookTitle,
  registeredSourceForUrl,
  sourceForUrl,
  sourceKeyForUrl,
  samePage
} from '../../extension/source-rules.js';

const fixture = JSON.parse(await readFile(
  new URL('./fixtures/deepseek-search.json', import.meta.url), 'utf8'));
const liveFixture = JSON.parse(await readFile(
  new URL('./fixtures/deepseek-search-live.json', import.meta.url), 'utf8'));
const endpoint = 'https://api.deepseek.com/v1/tools/search';
const token = 'fixture-token';

function response(value = { results: [] }, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => value
  };
}

function stubFetch(values) {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    const value = values[requests.length - 1];
    if (value instanceof Error) throw value;
    return value || response();
  };
  return { fetchImpl, requests };
}

async function rejectsWith(promise, code) {
  await assert.rejects(promise, error => error instanceof SearchError && error.code === code);
}

test.beforeEach(() => {
  globalThis.fetch = async () => {
    throw new Error('global fetch must not be used by searchBooks');
  };
});

test('wxdy book and chapter identities preserve source boundaries and actual access ports', () => {
  const chapter = 'http://wxdy.cc:8123/books/42/101_2.html';
  assert.equal(sourceForUrl(chapter).catalogUrl, 'http://wxdy.cc:8123/books/42/');
  assert.equal(canonicalBookUrl(chapter), 'https://www.wxdy.cc/books/42/');
  assert.equal(canonicalChapterUrl(chapter), 'https://www.wxdy.cc:8123/books/42/101.html');
  assert.equal(registeredSourceForUrl(chapter).downloadable, true);
  assert.equal(samePage(chapter, 'http://www.23txxt.com:8123/bqg/42/101_2.html'), false);
  assert.equal(sourceForUrl('https://wxdy.cc.example.test/books/42/'), null);
});

test('source keys collapse registered aliases and only strip common host aliases for unknown sites', () => {
  assert.equal(sourceKeyForUrl('https://www.23txxt.com/bqg/42/'), 'txxt');
  assert.equal(sourceKeyForUrl('https://23txxt.com/not-a-book'), 'txxt');
  assert.equal(sourceKeyForUrl('https://m.23txxt.com/bqg/42/'), 'txxt');
  assert.equal(sourceKeyForUrl('https://www.bxg123.top/xhqh/42/'), 'bixiange');
  assert.equal(sourceKeyForUrl('https://www.bixiange.me/xhqh/42/'), 'bixiange');
  assert.equal(sourceKeyForUrl('https://m.wxdy.cc/books/42/'), 'wxdy');
  assert.equal(sourceKeyForUrl('https://www.reader.example.test/book/42'), 'reader.example.test');
  assert.equal(sourceKeyForUrl('https://m.reader.example.test/book/42'), 'reader.example.test');
  assert.equal(sourceKeyForUrl('https://wap.reader.example.test/book/42'), 'reader.example.test');
  assert.equal(sourceKeyForUrl('https://mobile.reader.example.test/book/42'), 'reader.example.test');
  assert.equal(sourceKeyForUrl('https://notreader.example.test/book/42'), 'notreader.example.test');
  assert.equal(sourceKeyForUrl('https://reader.example.test.evil/book/42'), 'reader.example.test.evil');
  assert.equal(sourceKeyForUrl('https://www.m.reader.example.test/book/42'), 'm.reader.example.test');
  assert.equal(sourceKeyForUrl('ftp://reader.example.test/book/42'), null);
});

test('book title cleaning shares display suffix rules while preserving title words', () => {
  assert.equal(cleanBookTitle('《示例小说》 - 最新章节'), '示例小说');
  assert.equal(cleanBookTitle('示例小说 - 无弹窗手机版 - 最新章节'), '示例小说');
  assert.equal(cleanBookTitle('示例小说无弹窗手机版'), '示例小说');
  assert.equal(cleanBookTitle('前言里的示例小说'), '前言里的示例小说');
  assert.equal(cleanBookTitle('示例小说（求月票）'), '示例小说(求月票)');
});

test('a special-heading word inside a book name is preserved', async () => {
  const raw = [{title:'前言里的示例小说',url:'https://reader.test/book/42',summary:'作者：示例作者。字数10000，连载。'}];
  const {fetchImpl} = stubFetch([response(),response({results:raw})]);
  const value = await searchBooks('前言里的示例小说',{apiKey:token,fetchImpl});
  assert.equal(value.candidates.length,1);
  assert.equal(value.candidates[0].title,'前言里的示例小说');
});

test('missing fetchImpl is rejected before any request', async () => {
  await rejectsWith(searchBooks('示例小说', { apiKey: token }), 'fetch_impl_missing');
});

test('targeted exact supported hit returns immediately with one call', async () => {
  const { fetchImpl, requests } = stubFetch([response(fixture.targeted)]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 1);
  assert.equal(result.broadened, false);
  assert.equal(result.preferredId, 'https://www.23txxt.com/bqg/100/');
  assert.equal(result.candidates.length, 1);
  assert.deepEqual(result.candidates[0], {
    id: 'https://www.23txxt.com/bqg/100/',
    url: 'https://www.23txxt.com/bqg/100/',
    title: '示例小说',
    author: '示例作者',
    sourceId: 'txxt',
    registeredSourceId: 'txxt',
    registeredSourceName: '23txxt',
    supported: true,
    snippet: '示例来源目录',
    exact: true
  });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, endpoint);
  assert.equal(requests[0].options.headers.Authorization, `Bearer ${token}`);
  const body = JSON.parse(requests[0].options.body);
  assert.deepEqual(body, {
    query: '"示例小说" 小说 章节目录 site:23txxt.com OR site:trxs.cc OR site:bixiange.top OR site:69shuba.com OR site:wxdy.cc',
    ori_question: '寻找《示例小说》的小说书籍详情、章节目录或正文入口；不要推书、讨论、资讯或推广页面。',
    count: 50,
    need_content: false
  });
});

test('the sanitized live response shape still parses, while non-book evidence is filtered', async () => {
  const { fetchImpl } = stubFetch([response({ results: [] }), response(liveFixture)]);
  const result = await searchBooks('普通技术文档', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.deepEqual(result.candidates, []);
  assert.equal(result.preferredId, null);
});

test('targeted miss makes exactly one broad call with count 50', async () => {
  const { fetchImpl, requests } = stubFetch([
    response({ results: [] }),
    response(fixture.broad)
  ]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.equal(result.broadened, true);
  assert.equal(requests.length, 2);
  const body = JSON.parse(requests[1].options.body);
  assert.ok(body.query.startsWith('"示例小说" 小说 章节目录'));
  for (const host of ['xiaoshuo.qq.com', 'qidian.com', 'book.qq.com', 'yuedu.qq.com']) {
    assert.ok(body.query.includes(`-site:${host}`));
  }
  assert.ok([...body.query].length <= 128);
  assert.equal(body.ori_question, '寻找《示例小说》的小说书籍详情、章节目录或正文入口；不要推书、讨论、资讯或推广页面。');
  assert.equal(body.count, 50);
  assert.equal(body.need_content, false);
  assert.ok(result.candidates.every(candidate => candidate.id !== 'https://www.23txxt.com/bqg/100/'));
  assert.ok(result.candidates.every(candidate => !candidate.url.includes('qidian.com/book/300')));
  assert.ok(result.candidates.every(candidate => !candidate.url.includes('notqidian.com/book/400')));
});

test('a blocked targeted exact hit cannot short-circuit the broad search or become preferred', async () => {
  const targeted = { results: [{
    title: '换源示例小说',
    url: 'https://www.wxdy.cc/books/100/',
    author: '作者甲',
    snippet: '章节目录'
  }] };
  const broad = { results: [{
    title: '换源示例小说',
    url: 'https://www.23txxt.com/bqg/200/',
    author: '作者乙',
    snippet: '章节目录'
  }] };
  const { fetchImpl, requests } = stubFetch([response(targeted), response(broad)]);
  const result = await searchBooks('换源示例小说', {
    apiKey: token,
    fetchImpl,
    includeOtherSources: true,
    isBlocked: async candidate => candidate.sourceId === 'wxdy'
  });
  assert.equal(requests.length, 2);
  assert.equal(result.calls, 2);
  assert.equal(result.broadened, true);
  assert.deepEqual(result.candidates.map(candidate => candidate.sourceId), ['txxt']);
  assert.equal(result.preferredId, null);
});

test('same title candidates remain one contiguous group without changing source order', async () => {
  const results = [
    { title: '分组示例小说', url: 'https://www.wxdy.cc/books/100/', author: '作者乙', snippet: '章节目录' },
    { title: '分组示例小说', url: 'https://www.23txxt.com/bqg/200/', author: '作者甲', snippet: '章节目录' },
    { title: '另一本示例小说', url: 'https://www.23txxt.com/bqg/300/', author: '作者丙', snippet: '章节目录' },
    { title: '分组示例小说', url: 'https://www.trxs.cc/tongren/400.html', author: '作者甲', snippet: '章节目录' },
  ];
  const { fetchImpl } = stubFetch([response({ results })]);
  const result = await searchBooks('分组示例小说', { apiKey: token, fetchImpl });
  const titles = result.candidates.map(candidate => candidate.title);
  assert.equal(titles.filter(title => title === '分组示例小说').length, 3);
  assert.equal(titles.indexOf('分组示例小说'), 0);
  assert.equal(titles.lastIndexOf('分组示例小说'), 2);
});

test('same-title candidates sort by explicit author while unknown authors stay last', async () => {
  const results = [
    { title: '作者排序示例小说', url: 'https://www.wxdy.cc/books/501/', author: '作者乙', snippet: '章节目录' },
    { title: '作者排序示例小说', url: 'https://www.23txxt.com/bqg/502/', author: '作者甲', snippet: '章节目录' },
    { title: '作者排序示例小说', url: 'https://www.trxs.cc/tongren/503.html' }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results })]);
  const result = await searchBooks('作者排序示例小说', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => candidate.author), ['作者甲', '作者乙', null]);
});

test('same-author candidates preserve declared source and entry priority', async () => {
  const results = [
    { title: '同作者排序示例小说', url: 'https://www.23txxt.com/bqg/602/101.html', author: '作者甲', snippet: '章节目录' },
    { title: '同作者排序示例小说', url: 'https://www.wxdy.cc/books/603/101.html', author: '作者甲', snippet: '章节目录' },
    { title: '同作者排序示例小说', url: 'https://www.23txxt.com/bqg/604/', author: '作者甲', snippet: '章节目录' }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results })]);
  const result = await searchBooks('同作者排序示例小说', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => [candidate.sourceId, candidate.exact]), [
    ['txxt', true],
    ['wxdy', true],
    ['txxt', true]
  ]);
});

test('an exact title without metadata stays visible but is marked unverified and not preselected', async () => {
  const results = [{
    title: '待确认示例小说',
    url: 'https://reader.example.test/book/42',
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results })]);
  const result = await searchBooks('待确认示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].identityUnverified, true);
  assert.equal(result.preferredId, null);
});

test('a blocked-only exact hit still broadens and returns no preference when nothing remains', async () => {
  const targeted = { results: [{
    title: '单源示例小说',
    url: 'https://www.wxdy.cc/books/101/',
    author: '示例作者',
    snippet: '章节目录'
  }] };
  const { fetchImpl, requests } = stubFetch([response(targeted), response()]);
  const result = await searchBooks('单源示例小说', {
    apiKey: token,
    fetchImpl,
    isBlocked: candidate => candidate.sourceId === 'wxdy'
  });
  assert.equal(requests.length, 2);
  assert.equal(result.calls, 2);
  assert.equal(result.broadened, true);
  assert.deepEqual(result.candidates, []);
  assert.equal(result.preferredId, null);
});

test('includeOtherSources broadens after a usable targeted exact hit', async () => {
  const targeted = { results: [{
    title: '保留定向示例小说',
    url: 'https://www.wxdy.cc/books/102/',
    snippet: '章节目录'
  }] };
  const broad = { results: [{
    title: '保留定向示例小说',
    url: 'https://www.23txxt.com/bqg/202/',
    snippet: '章节目录'
  }] };
  const { fetchImpl, requests } = stubFetch([response(targeted), response(broad)]);
  const result = await searchBooks('保留定向示例小说', {
    apiKey: token,
    fetchImpl,
    includeOtherSources: true
  });
  assert.equal(requests.length, 2);
  assert.equal(result.calls, 2);
  assert.equal(result.broadened, true);
  assert.deepEqual(result.candidates.map(candidate => candidate.sourceId), ['wxdy', 'txxt']);
  assert.equal(result.preferredId, null);
});

test('previousResult reuses a non-broadened targeted result and makes only the broad request', async () => {
  const previousResult = await searchBooks('复用定向示例小说', {
    apiKey: token,
    fetchImpl: stubFetch([response({ results: [{
      title: '复用定向示例小说',
      url: 'https://www.wxdy.cc/books/103/',
      snippet: '章节目录'
    }] })]).fetchImpl
  });
  assert.equal(previousResult.calls, 1);
  assert.equal(previousResult.broadened, false);

  const { fetchImpl, requests } = stubFetch([response({ results: [{
    title: '复用定向示例小说',
    url: 'https://www.23txxt.com/bqg/203/',
    snippet: '章节目录'
  }] })]);
  const result = await searchBooks('复用定向示例小说', {
    apiKey: token,
    fetchImpl,
    includeOtherSources: true,
    previousResult
  });
  assert.equal(requests.length, 1);
  assert.ok(JSON.parse(requests[0].options.body).query.startsWith('"复用定向示例小说" 小说'));
  assert.equal(result.calls, 1);
  assert.equal(result.broadened, true);
  assert.equal(result.reused, true);
  assert.deepEqual(result.candidates.map(candidate => candidate.sourceId), ['wxdy', 'txxt']);
});

test('previousResult does not reuse a broadened or mismatched result', async () => {
  const broadened = {
    query: '不重用示例小说',
    candidates: [],
    preferredId: null,
    calls: 2,
    broadened: true,
    reused: false
  };
  const first = stubFetch([response(), response()]);
  const broadenedResult = await searchBooks('不重用示例小说', {
    apiKey: token,
    fetchImpl: first.fetchImpl,
    includeOtherSources: true,
    previousResult: broadened
  });
  assert.equal(first.requests.length, 2);
  assert.ok(!broadenedResult.reused);

  const mismatch = {
    query: '另一个查询',
    candidates: [],
    preferredId: null,
    calls: 1,
    broadened: false,
    reused: false
  };
  const second = stubFetch([response(), response()]);
  const mismatchResult = await searchBooks('查询不匹配小说', {
    apiKey: token,
    fetchImpl: second.fetchImpl,
    includeOtherSources: true,
    previousResult: mismatch
  });
  assert.equal(second.requests.length, 2);
  assert.ok(!mismatchResult.reused);
});

test('persisted identity conflict survives JSON, blocking a lone remaining candidate', async () => {
  const persisted = JSON.parse(JSON.stringify({
    query: '持久冲突示例小说',
    candidates: [{
      id: 'https://www.23txxt.com/bqg/901/',
      url: 'https://www.23txxt.com/bqg/901/',
      title: '持久冲突示例小说',
      author: null,
      sourceId: 'txxt',
      registeredSourceId: 'txxt',
      registeredSourceName: '23txxt',
      supported: true,
      snippet: '同名书的作者证据冲突',
      exact: true,
      identityConflict: true
    }],
    preferredId: null,
    calls: 1,
    broadened: false,
    reused: false
  }));
  assert.equal(persisted.candidates[0].identityConflict, true);

  const result = await searchBooks('持久冲突示例小说', {
    apiKey: token,
    fetchImpl: stubFetch([response({ results: [{
      title: '持久冲突示例小说',
      url: 'https://www.wxdy.cc/books/902/',
      author: '作者甲',
      snippet: '章节目录'
    }] })]).fetchImpl,
    includeOtherSources: true,
    isBlocked: candidate => candidate.sourceId === 'txxt',
    previousResult: persisted
  });
  assert.equal(result.reused, true);
  assert.equal(result.calls, 1);
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].sourceId, 'wxdy');
  assert.equal(result.preferredId, null);

  const retained = await searchBooks('持久冲突示例小说', {
    apiKey: token,
    fetchImpl: stubFetch([response({ results: [] })]).fetchImpl,
    includeOtherSources: true,
    previousResult: persisted
  });
  assert.equal(retained.reused, true);
  assert.equal(retained.candidates[0].identityConflict, true);
  assert.equal(retained.preferredId, null);
});

test('zero results remain a successful empty list', async () => {
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: [] })]);
  const result = await searchBooks('没有结果', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.equal(result.broadened, true);
  assert.deepEqual(result.candidates, []);
  assert.equal(result.preferredId, null);
});

test('diagnostics count fixed exclusion reasons without changing filtering', async () => {
  const raw = [
    { title: '诊断示例小说', url: 'not-a-url', snippet: '作者：诊断甲。章节目录。' },
    { title: '诊断示例小说', url: 'https://qidian.com/book/1', snippet: '作者：诊断甲。章节目录。' },
    { title: '诊断示例小说', url: 'https://bbs.example.test/thread/2', snippet: '作者：诊断甲。章节目录。' },
    { title: '另一个标题', url: 'https://reader.example.test/book/3', snippet: '作者：诊断甲。章节目录。' },
    { title: '诊断示例小说', url: 'https://reader.example.test/read/4', snippet: '作者：诊断甲。资源下载。' },
    { title: '诊断示例小说', url: 'https://reader.example.test/book/5', snippet: '作者：诊断甲。章节目录。' }
  ];
  const summaries = [];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('诊断示例小说', {
    apiKey: token,
    fetchImpl,
    onDiagnostics: value => summaries.push(value)
  });
  assert.equal(result.candidates.length, 2);
  assert.equal(summaries.length, 1);
  assert.equal(summaries[0].rounds.length, 2);
  assert.deepEqual(summaries[0].rounds.map(value => value.phase), ['targeted', 'broad']);
  assert.deepEqual(summaries[0].rounds[1], {
    phase: 'broad',
    raw: 6,
    filtered: 2,
    unique: 2,
    duplicates_removed: 0,
    exclusions: {
      invalid_url: 1,
      platform_domain: 1,
      empty_title: 0,
      forum_or_promotion: 1,
      unregistered_source: 0,
      title_mismatch: 1,
      evidence_insufficient: 0
    }
  });
  assert.deepEqual(summaries[0].totals.exclusions, {
    invalid_url: 1,
    platform_domain: 1,
    empty_title: 0,
    forum_or_promotion: 1,
    unregistered_source: 0,
    title_mismatch: 1,
    evidence_insufficient: 0
  });
  assert.equal(summaries[0].cache_use, 'fresh_broad');
});

test('diagnostics separate merge, blocked attachment and reused cache stages', async () => {
  const previous = {
    query: '诊断复用示例小说',
    candidates: [{
      id: 'https://www.23txxt.com/bqg/1101/',
      url: 'https://www.23txxt.com/bqg/1101/',
      title: '诊断复用示例小说',
      author: '作者甲',
      sourceId: 'txxt',
      registeredSourceId: 'txxt',
      registeredSourceName: '23txxt',
      supported: true,
      snippet: '章节目录',
      exact: true
    }],
    preferredId: null,
    calls: 1,
    broadened: false
  };
  const summaries = [];
  const { fetchImpl } = stubFetch([response({ results: [
    { title: '诊断复用示例小说', url: 'https://www.wxdy.cc/books/1103/', snippet: '章节目录' },
    { title: '诊断复用示例小说', url: 'https://www.wxdy.cc/books/1103/?utm_source=diagnostic', snippet: '章节目录' }
  ] })]);
  const result = await searchBooks('诊断复用示例小说', {
    apiKey: token,
    fetchImpl,
    includeOtherSources: true,
    previousResult: previous,
    isBlocked: candidate => candidate.sourceId === 'txxt',
    onDiagnostics: value => summaries.push(value)
  });
  assert.equal(result.reused, true);
  assert.equal(result.calls, 1);
  assert.equal(summaries.length, 1);
  assert.deepEqual(summaries[0].rounds.map(value => value.phase), ['broad']);
  assert.deepEqual(summaries[0].merge, { input: 2, unique: 2, duplicates_removed: 0 });
  assert.deepEqual(summaries[0].identity_conflicts, {
    note: 'included_in_final_candidates',
    count: 0
  });
  assert.deepEqual(summaries[0].blocked, { input: 2, removed: 1, remaining: 1 });
  assert.equal(summaries[0].cached_candidates, 1);
  assert.equal(summaries[0].final_candidates, 1);
  assert.equal(summaries[0].cache_use, 'previous_targeted');
});

test('diagnostics do not change the normal search result', async () => {
  const raw = [
    { title: '等价诊断示例小说', url: 'https://www.wxdy.cc/books/1201/', author: '作者乙', snippet: '章节目录' },
    { title: '等价诊断示例小说', url: 'https://www.23txxt.com/bqg/1202/', author: '作者甲', snippet: '章节目录' },
    { title: '等价诊断示例小说', url: 'https://reader.example.test/book/1203', snippet: '章节目录' }
  ];
  const calls = values => stubFetch([response({ results: [] }), response({ results: values })]);
  const withoutDiagnostics = await searchBooks('等价诊断示例小说', {
    apiKey: token,
    fetchImpl: calls(raw).fetchImpl
  });
  const withDiagnostics = await searchBooks('等价诊断示例小说', {
    apiKey: token,
    fetchImpl: calls(raw).fetchImpl,
    onDiagnostics: () => {}
  });
  assert.deepEqual(withDiagnostics, withoutDiagnostics);
});

test('empty input returns no candidates without a request', async () => {
  let called = false;
  const result = await searchBooks('   ', {
    apiKey: token,
    fetchImpl: async () => { called = true; return response(); }
  });
  assert.deepEqual(result, {
    query: '',
    candidates: [],
    preferredId: null,
    calls: 0,
    broadened: false
  });
  assert.equal(called, false);
});

test('query length counts Unicode code points at 128 and rejects 129', async () => {
  const atLimit = '书'.repeat(127) + '😀';
  const { fetchImpl, requests } = stubFetch([response({ results: [] })]);
  await rejectsWith(searchBooks(atLimit, { apiKey: token, fetchImpl }), 'query_too_long');
  assert.equal(requests.length, 0);

  const overLimit = '书'.repeat(128) + '😀';
  let called = false;
  await rejectsWith(searchBooks(overLimit, {
    apiKey: token,
    fetchImpl: async () => { called = true; return response({ results: [] }); }
  }), 'query_too_long');
  assert.equal(called, false);
});

test('a one-hundred-character title keeps the book title whole and stays within 128 characters', async () => {
  const title = '书'.repeat(100);
  const { fetchImpl, requests } = stubFetch([response({ results: [] })]);
  await searchBooks(title, { apiKey: token, fetchImpl });
  const body = JSON.parse(requests[0].options.body);
  assert.equal(body.query, `"${title}" 小说 章节目录 site:23txxt.com`);
  assert.ok([...body.query].length <= 128);
  assert.ok([...body.ori_question].length <= 128);
});

test('an unencodable title reports query_too_long without sending a truncated request', async () => {
  const title = `${'书'.repeat(62)}"${'名'.repeat(65)}`;
  let calls = 0;
  const result = searchBooks(title, {
    apiKey: token,
    fetchImpl: async () => {
      calls += 1;
      return response();
    }
  });
  await rejectsWith(result, 'query_too_long');
  assert.equal(calls, 0);
});

test('quotes and operators in a title cannot become controlled query syntax', async () => {
  const { fetchImpl, requests } = stubFetch([response({ results: [] }), response({ results: [] })]);
  await searchBooks('示例 "书名" OR site:example.test', { apiKey: token, fetchImpl });
  const targeted = JSON.parse(requests[0].options.body).query;
  const broad = JSON.parse(requests[1].options.body).query;
  assert.ok(targeted.startsWith('"示例  书名  OR site:example.test" 小说'));
  assert.ok(targeted.includes('site:23txxt.com OR site:trxs.cc'));
  assert.ok([...targeted].length <= 128);
  assert.equal(broad.startsWith('"示例  书名  OR site:example.test" 小说 章节目录'), true);
});

test('a short fragment keeps novel intent and rejects forum or promotion pages that repeat the whole title', async () => {
  const query = '短片段';
  const raw = [
    {
      title: '短片段 小说 章节目录',
      url: 'https://forum.example.test/book/forum-copy',
      snippet: '作者：示例作者 章节目录'
    },
    {
      title: '短片段小说在线全文阅读',
      url: 'https://www.ngabbs.com/read.php?tid=123',
      snippet: '短片段 小说 章节目录 正文'
    },
    {
      title: '短片段小说全文阅读',
      url: 'https://m.sohu.com/a/123456',
      snippet: '短片段 小说 章节目录 正文'
    },
    {
      title: '短片段小说推广活动',
      url: 'https://promo.example.test/book/123',
      snippet: '短片段 小说 章节目录 限时优惠'
    },
    {
      title: '短片段小说 - 最新章节',
      url: 'https://reader.example.test/book/short',
      snippet: '作者：示例作者 章节列表 正文'
    }
  ];
  const { fetchImpl, requests } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks(query, { apiKey: token, fetchImpl });
  assert.equal(requests.length, 2);
  const broad = JSON.parse(requests[1].options.body);
  assert.ok(broad.query.includes('小说'));
  assert.ok([...broad.query].length <= 128);
  assert.ok([...broad.ori_question].length <= 128);
  assert.deepEqual(result.candidates.map(candidate => candidate.url), [
    'https://reader.example.test/book/short'
  ]);
  assert.equal(result.preferredId, null);
});

test('pages whose summary is only the whole title are rejected', async () => {
  const raw = [
    {
      title: '完整书名示例',
      url: 'https://unknown.example.test/2026/story-1',
      snippet: '完整书名示例'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response({ results: [] })]);
  const result = await searchBooks('完整书名示例', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates, []);
});

test('summary-only author and catalog evidence can establish a book when the snippet lacks it', async () => {
  const raw = [{
    title: '摘要证据小说 - 最新章节',
    url: 'https://reader.example.test/book/summary',
    snippet: '普通搜索摘要。',
    summary: '作者：示例作者。作品简介与章节目录。'
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('摘要证据小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].author, '示例作者');
  assert.ok(result.candidates[0].snippet.includes('章节目录'));
});

test('a query found only inside a chapter heading does not match the work title', async () => {
  const raw = [{
    title: '另一部小说 - 第384章 目标片段',
    url: 'https://reader.example.test/book/wrong-chapter',
    snippet: '作者：示例作者。章节目录。正文摘录。'
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('目标片段', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates, []);
});

test('Chinese chapter numerals before the snippet evidence are recognized', async () => {
  const raw = [{
    title: '中文数字小说 - 第三百八十四章 示例',
    url: 'https://reader.example.test/book/chinese-number',
    snippet: '作者：示例作者。连载中，字数 120 万。'
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('中文数字小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
});

test('forum and promotion pages repeating the exact book title are rejected', async () => {
  const query = '完整书名过滤示例';
  const raw = [
    {
      title: query,
      url: 'https://www.ngabbs.com/read.php?tid=456',
      snippet: `${query} 小说 章节目录 正文`
    },
    {
      title: query,
      url: 'https://promo.example.test/book/456',
      snippet: `${query} 小说 章节目录 限时优惠`
    },
    {
      title: query,
      url: 'https://reader.example.test/book/456',
      snippet: `${query} 作者：示例作者 章节目录`
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks(query, { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => candidate.url), [
    'https://reader.example.test/book/456'
  ]);
});

test('excluded hosts use exact domain or subdomain boundaries', async () => {
  const raw = [
    { title: '边界 A 小说', url: 'https://qidian.com/a', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 B 小说', url: 'https://m.qidian.com/b', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 C 小说', url: 'https://book.qq.com/c', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 D 小说', url: 'https://yuedu.qq.com/d', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 E 小说', url: 'https://notqidian.com/e',
      snippet: '作者：示例作者。章节目录。' },
    { title: '边界 F 小说', url: 'https://qq.com/f',
      snippet: '作者：示例作者。章节目录。' },
    { title: '边界 G 小说', url: 'https://qidian.com.example.test/g',
      snippet: '作者：示例作者。章节目录。' },
    { title: '边界 H 小说', url: 'https://xiaoshuo.qq.com/h', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 I 小说', url: 'https://m.xiaoshuo.qq.com/i', snippet: '作者：示例作者。章节目录。' },
    { title: '边界 J 小说', url: 'https://notxiaoshuo.qq.com/j',
      snippet: '作者：示例作者。章节目录。' }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('边界', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => candidate.title).sort(),
    ['边界 E 小说', '边界 F 小说', '边界 G 小说', '边界 J 小说']);
});

test('fragments and explicit tracking parameters deduplicate normalized URLs', async () => {
  const raw = [
    { title: '示例小说 - 最新章节', url: 'https://reader.example.test/book?id=7&utm_source=a#one',
      snippet: '作者：示例作者。章节目录。' },
    { title: '示例小说 - 最新章节', url: 'https://reader.example.test/book?id=7&utm_campaign=b#two',
      snippet: '作者：示例作者。章节目录。' }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].url, 'https://reader.example.test/book?id=7');
});

test('semantic query parameters remain part of unknown-site URL identity', async () => {
  const raw = [
    { title: '示例小说 - 最新章节', url: 'https://reader.example.test/book?id=7&from=list',
      snippet: '作者：示例作者。章节目录。' },
    { title: '示例小说 - 最新章节', url: 'https://reader.example.test/book?id=7&source=list',
      snippet: '作者：示例作者。章节目录。' },
    { title: '示例小说 - 最新章节', url: 'https://reader.example.test/book?id=7&ref=search',
      snippet: '作者：示例作者。章节目录。' }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.deepEqual(
    result.candidates.map(candidate => candidate.url).sort(),
    [
      'https://reader.example.test/book?id=7&from=list',
      'https://reader.example.test/book?id=7&ref=search',
      'https://reader.example.test/book?id=7&source=list'
    ]
  );
});

test('query and result title words are compared without unverified suffix trimming', async () => {
  for (const [query, title] of [
    ['示例小说', '示例'],
    ['示例', '示例小说']
  ]) {
    const { fetchImpl } = stubFetch([
      response({ results: [{ title, url: 'https://www.23txxt.com/bqg/701/' }] }),
      response({ results: [] })
    ]);
    const result = await searchBooks(query, { apiKey: token, fetchImpl });
    assert.equal(result.preferredId, null);
    assert.ok(result.candidates.every(candidate => candidate.exact === false));
  }

  const wrapped = stubFetch([response({ results: [
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/702/' }
  ] })]);
  const wrappedResult = await searchBooks('《示例小说》', {
    apiKey: token,
    fetchImpl: wrapped.fetchImpl
  });
  assert.equal(wrappedResult.preferredId, 'https://www.23txxt.com/bqg/702/');
  assert.equal(wrappedResult.calls, 1);
});

test('only a clearly separated display-site suffix is removed from result titles', async () => {
  const raw = [
    { title: '示例小说 - 最新章节', url: 'https://www.23txxt.com/bqg/801/' },
    { title: '示例小说无弹窗手机版', url: 'https://www.23txxt.com/bqg/802/?from=detail' }
  ];
  const { fetchImpl } = stubFetch([
    response({ results: [] }),
    response({ results: raw })
  ]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 2);
  assert.deepEqual(
    result.candidates.map(candidate => [candidate.title, candidate.exact]),
    [
      ['示例小说', true],
      ['示例小说', true]
    ]
  );
  assert.equal(result.preferredId, null);
});

test('different known catalog URLs stay distinct and produce no preference', async () => {
  const raw = [
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/501/' },
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/502/' }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response({ results: [] })]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.equal(result.candidates.length, 2);
  assert.equal(result.preferredId, null);
});

test('known catalog identity ignores URL tracking differences', async () => {
  const raw = [
    { title: '示例小说', url: 'https://23txxt.com/bqg/777/?utm_source=fixture#top' },
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/777/' }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw })]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.preferredId, 'https://www.23txxt.com/bqg/777/');
});

test('an extra same-title supported candidate prevents automatic preference', async () => {
  const raw = [
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/601/' },
    { title: '示例小说', url: 'https://www.23txxt.com/bqg/602/' }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw })]);
  const result = await searchBooks('示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.preferredId, null);
});

test('author conflicts block preselect while different book identities stay as separate candidates', async () => {
  const raw = [
    {
      title: '同名示例小说',
      url: 'https://reader.example.test/book/author-a',
      snippet: '作者：作者甲 章节目录'
    },
    {
      title: '同名示例小说',
      url: 'https://reader.example.test/book/author-b',
      snippet: '作者：作者乙 章节目录'
    },
    {
      title: '同名示例小说续作',
      url: 'https://other.example.test/book/author-c',
      snippet: '作者：作者丙 章节目录'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('同名示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 3);
  assert.deepEqual(
    result.candidates.map(candidate => candidate.author).sort(),
    ['作者丙', '作者乙', '作者甲'].sort()
  );
  assert.equal(result.preferredId, null);
});

test('registered sources are ranked ahead of unknown sites while only 23txxt remains downloadable', async () => {
  const raw = [
    {
      title: '来源优先小说 - 最新章节',
      url: 'https://unknown.example.test/book/1',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源优先小说 - 最新章节',
      url: 'https://www.trxs.cc/tongren/11699.html',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源优先小说 - 最新章节',
      url: 'https://www.bixiange.top/xhqh/12345/index/1.html',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源优先小说 - 最新章节',
      url: 'https://www.69shuba.com/book/12345/',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源优先小说',
      url: 'https://www.23txxt.com/bqg/100/',
      snippet: '章节目录'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response({ results: [] })]);
  const result = await searchBooks('来源优先小说', { apiKey: token, fetchImpl });
  assert.ok(result.candidates.filter(candidate => !candidate.registeredSourceId)
    .every(candidate => !candidate.supported));
  assert.deepEqual(result.candidates.filter(candidate => candidate.supported)
    .map(candidate => candidate.registeredSourceId), ['txxt']);
  assert.ok(['trxs.cc', '笔仙阁', '69shuba'].every(name =>
    result.candidates.some(candidate => candidate.registeredSourceName === name)));
});

test('bixiange mirrors share a registered source identity but keep distinct book URLs', async () => {
  const raw = [
    {
      title: '镜像身份小说 甲',
      url: 'https://www.bxg123.top/xhqh/12345/index/1.html',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '镜像身份小说 乙',
      url: 'https://www.bixiange.me/xhqh/12346/index/1.html',
      snippet: '作者：示例作者。章节目录。'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response({ results: [] })]);
  const result = await searchBooks('镜像身份', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 2);
  assert.ok(result.candidates.every(candidate => candidate.registeredSourceId === 'bixiange'));
});

test('registered source rules accept their catalog and chapter URL forms', async () => {
  const raw = [
    {
      title: '来源页面形式小说 - 最新章节',
      url: 'https://www.trxs.cc/tongren/11699.html',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源页面形式小说 - 最新章节',
      url: 'https://www.trxs.cc/tongren/11700/147.html',
      snippet: '作者：示例作者。第147章 示例章节。'
    },
    {
      title: '来源页面形式小说 三 - 最新章节',
      url: 'https://www.bixiange.top/xhqh/12345/index/1.html',
      snippet: '作者：示例作者。章节目录。'
    },
    {
      title: '来源页面形式小说 四 - 最新章节',
      url: 'https://www.bixiange.top/xhqh/12346/',
      snippet: '作者：示例作者。章节目录。'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response({ results: [] })]);
  const result = await searchBooks('来源页面形式', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate =>
    `${candidate.registeredSourceId} ${candidate.url}`).sort(), [
    'bixiange https://www.bixiange.top/xhqh/12345/index/1.html',
    'bixiange https://www.bixiange.top/xhqh/12346/',
    'trxs https://www.trxs.cc/tongren/11699.html',
    'trxs https://www.trxs.cc/tongren/11700/147.html'
  ]);
});

test('diagnostic, loan and Baidu-style pages need real book metadata and are not admitted by path words', async () => {
  const raw = [
    {
      title: '短片段贷款问答',
      url: 'https://reader-a.example.test/read/123',
      snippet: '贷款申请条件及操作流程。'
    },
    {
      title: '短片段TXT下载_百度网盘',
      url: 'https://reader-b.example.test/txt/456',
      snippet: '资源下载链接。'
    },
    {
      title: '短片段小说 - 最新章节',
      url: 'https://reader-c.example.test/book/789',
      snippet: '作者：示例作者。章节目录。'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('短片段', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => candidate.url), [
    'https://reader-c.example.test/book/789'
  ]);
});

test('unknown candidates from multiple hosts are interleaved without relying on a relevance threshold', async () => {
  const raw = [
    { title: '多样 示例甲一 - 最新章节', url: 'https://a.example.test/book/1',
      snippet: '作者：示例作者。章节目录。', rerank_score: 0.41 },
    { title: '多样 示例甲二 - 最新章节', url: 'https://a.example.test/book/2',
      snippet: '作者：示例作者。章节目录。', rerank_score: 0.9 },
    { title: '多样 示例乙 - 最新章节', url: 'https://b.example.test/book/3',
      snippet: '作者：示例作者。章节目录。', rerank_score: 0.52 },
    { title: '多样 示例丙 - 最新章节', url: 'https://c.example.test/book/4',
      snippet: '作者：示例作者。章节目录。', rerank_score: 0.67 }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('多样', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(candidate => candidate.url), [
    'https://a.example.test/book/1',
    'https://b.example.test/book/3',
    'https://c.example.test/book/4',
    'https://a.example.test/book/2'
  ]);
});

test('same-host known catalogs rank before an earlier chapter result', async () => {
  const raw = [
    {
      title: '章节示例小说',
      url: 'https://www.23txxt.com/bqg/903/101.html',
      snippet: '正文'
    },
    {
      title: '章节示例小说 - 最新章节',
      url: 'https://www.23txxt.com/bqg/903/',
      snippet: '章节目录'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('章节示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].id, 'https://www.23txxt.com/bqg/903/');
  assert.equal(result.candidates[0].title, '章节示例小说');
  assert.equal(result.candidates[0].snippet, '章节目录');
});

test('known catalog identity does not rewrite the actual source protocol or port', async () => {
  const raw = [{ title: '中性长篇', url: 'http://23txxt.com:8123/bqg/990/101.html' }];
  const { fetchImpl } = stubFetch([response({ results: raw })]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.equal(result.candidates[0].id, 'https://www.23txxt.com/bqg/990/');
  assert.equal(result.candidates[0].url, 'http://23txxt.com:8123/bqg/990/');
});

test('a merged known candidate points at the exact catalog URL even when a chapter appeared first', async () => {
  const raw = [
    {
      title: '章节示例小说',
      url: 'https://www.23txxt.com/bqg/906/101.html',
      snippet: '正文'
    },
    {
      title: '章节示例小说',
      url: 'https://www.23txxt.com/bqg/906/',
      snippet: '章节目录'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw })]);
  const result = await searchBooks('章节示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].url, 'https://www.23txxt.com/bqg/906/');
  assert.equal(result.preferredId, 'https://www.23txxt.com/bqg/906/');
});

test('known catalog pages and chapter links of the same book merge to one catalog identity', async () => {
  const raw = [
    {
      title: '目录页',
      url: 'https://www.23txxt.com/bqg/901/',
      snippet: '章节目录'
    },
    {
      title: '第一章',
      url: 'https://www.23txxt.com/bqg/901/101.html',
      snippet: '正文'
    },
    {
      title: '第一章续页',
      url: 'https://www.23txxt.com/bqg/901/101_2.html',
      snippet: '正文'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('目录页', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].id, 'https://www.23txxt.com/bqg/901/');
  assert.equal(result.candidates[0].title, '目录页');
  assert.equal(result.candidates[0].snippet, '章节目录');
});

test('same known book with conflicting authors remains present but cannot be preselected', async () => {
  const raw = [
    {
      title: '冲突示例小说',
      url: 'https://www.23txxt.com/bqg/902/',
      snippet: '作者：作者甲 章节目录'
    },
    {
      title: '冲突示例小说',
      url: 'https://www.23txxt.com/bqg/902/101.html',
      snippet: '作者：作者乙 正文'
    }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw })]);
  const result = await searchBooks('冲突示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.preferredId, null);
});

test('unknown forum-style query parameters alone do not erase a valid unknown book page', async () => {
  const raw = [{
    title: '参数示例小说 - 最新章节',
    url: 'https://reader.example.test/book/param?tid=kept&from=list',
    snippet: '作者：示例作者 章节目录'
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('参数示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].url, 'https://reader.example.test/book/param?tid=kept&from=list');
});

test('all fifty filtered candidates are returned without a five-item cap', async () => {
  const raw = Array.from({ length: 8 }, (_, index) => ({
    title: `限制 示例 ${index}`,
    url: `https://reader${index}.example.test/book/${index}?utm_source=x`,
    snippet: '作者：示例作者。章节目录。'
  }));
  raw.push({
    title: '限制 重复',
    url: 'https://reader0.example.test/book/0?utm_source=y',
    snippet: '作者：示例作者。章节目录。'
  });
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('限制', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 8);

  const short = await searchBooks('短', {
    apiKey: token,
    fetchImpl: stubFetch([response({ results: [] }), response({
      results: [{
        title: '短 唯一小说',
        url: 'https://reader.example.test/book/only',
        snippet: '作者：示例作者。章节目录。'
      }]
    })]).fetchImpl
  });
  assert.equal(short.candidates.length, 1);
});

test('a duplicate known book across the targeted and broad calls is counted once', async () => {
  const targeted = {
    results: [{
      title: '跨轮示例小说续作',
      url: 'https://www.23txxt.com/bqg/905/',
      snippet: '章节目录'
    }]
  };
  const broad = {
    results: [{
      title: '跨轮示例小说',
      url: 'https://23txxt.com/bqg/904/101.html',
      snippet: '正文'
    }]
  };
  const { fetchImpl } = stubFetch([response(targeted), response(broad)]);
  const result = await searchBooks('跨轮示例小说', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.equal(result.candidates.length, 2);
  assert.deepEqual(result.candidates.map(candidate => candidate.id).sort(), [
    'https://www.23txxt.com/bqg/904/',
    'https://www.23txxt.com/bqg/905/'
  ]);
});

test('a query found only in a plot or recommendation snippet does not identify a book', async () => {
  const raw = [
    { title: '其他中性小说 - 最新章节', url: 'https://reader-a.test/book/1',
      snippet: '作者：示例作者。简介中出现目标片段，推荐目标片段小说。' },
    { title: '目标片段小说 - 最新章节', url: 'https://reader-b.test/book/2',
      snippet: '作者：示例作者。章节目录。' }
  ];
  const { fetchImpl } = stubFetch([response(), response({ results: raw })]);
  const result = await searchBooks('目标片段', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(row => row.url), ['https://reader-b.test/book/2']);
});

test('an explicitly labeled author can match an author query without a title match', async () => {
  const raw = [{ title: '中性长篇 - 最新章节', url: 'https://reader.test/book/1',
    snippet: '作者：示例作者。章节目录。' }];
  const { fetchImpl } = stubFetch([response(), response({ results: raw })]);
  const result = await searchBooks('示例作者', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].author, '示例作者');
  assert.equal(result.preferredId, null);
});

test('a PHP reader is kept while a thread URL and www forum host are excluded', async () => {
  const raw = [
    { title: '中性长篇 - 最新章节', url: 'https://example.test/read.php?id=1', snippet: '作者：示例作者。章节目录。' },
    { title: '中性长篇 - 最新章节', url: 'https://example.test/read.php?tid=1', snippet: '章节目录' },
    { title: '中性长篇 - 最新章节', url: 'https://www.bbs.example.test/book/1', snippet: '章节目录' }
  ];
  const { fetchImpl } = stubFetch([response(), response({ results: raw })]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(row => row.url), ['https://example.test/read.php?id=1']);
});

test('desktop and mobile host aliases share a diversity group without merging book identities', async () => {
  const raw = [
    { title: '中性长篇 甲 - 最新章节', url: 'https://www.reader-a.test/book/1',
      snippet: '作者：示例作者。章节目录。' },
    { title: '中性长篇 乙 - 最新章节', url: 'https://m.reader-a.test/book/2',
      snippet: '作者：示例作者。章节目录。' },
    { title: '中性长篇 丙 - 最新章节', url: 'https://reader-b.test/book/3',
      snippet: '作者：示例作者。章节目录。' }
  ];
  const { fetchImpl } = stubFetch([response(), response({ results: raw })]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.deepEqual(result.candidates.map(row => row.url), [raw[0].url, raw[2].url, raw[1].url]);
});

test('conflicting author evidence is not presented as a known first author', async () => {
  const raw = [
    { title: '中性长篇', url: 'https://www.23txxt.com/bqg/991/', author: '作者甲' },
    { title: '中性长篇', url: 'https://www.23txxt.com/bqg/991/101.html', author: '作者乙' }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response()]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.equal(result.candidates[0].author, null);
  assert.equal(result.preferredId, null);
});

test('known chapter authorship conflicts survive a chapter-title relevance filter', async () => {
  const raw = [
    { title: '中性长篇', url: 'https://www.23txxt.com/bqg/992/', author: '作者甲' },
    { title: '第1章 示例章节', url: 'https://www.23txxt.com/bqg/992/101.html', author: '作者乙' }
  ];
  const { fetchImpl } = stubFetch([response({ results: raw }), response()]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.equal(result.calls, 2);
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].author, null);
  assert.equal(result.preferredId, null);
});

test('a broad-result author conflict survives merging into an earlier targeted candidate', async () => {
  const targeted = [{ title: '中性长篇续作', url: 'https://www.23txxt.com/bqg/993/', author: '作者甲' }];
  const broad = [
    { title: '中性长篇', url: 'https://www.23txxt.com/bqg/993/', author: '作者甲' },
    { title: '第1章 示例章节', url: 'https://www.23txxt.com/bqg/993/101.html', author: '作者乙' }
  ];
  const { fetchImpl } = stubFetch([response({ results: targeted }), response({ results: broad })]);
  const result = await searchBooks('中性长篇', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].author, null);
  assert.equal(result.preferredId, null);
});

test('fifty API rows are all retained when every row has book evidence', async () => {
  const raw = Array.from({ length: 50 }, (_, index) => ({
    title: `五十 示例 ${index}小说`,
    url: `https://reader-${index}.example.test/book/${index}`,
    snippet: '作者：示例作者 章节目录'
  }));
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('五十', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 50);
});

test('a long title drops both optional operators and the targeted call without truncating the title', async () => {
  const title = '书'.repeat(110);
  const { fetchImpl, requests } = stubFetch([response({ results: [] })]);
  await searchBooks(title, { apiKey: token, fetchImpl });
  assert.equal(requests.length, 1);
  assert.equal(JSON.parse(requests[0].options.body).query, `"${title}" 小说 章节目录`);
});

test('unsupported candidates are retained with null source identity', async () => {
  const raw = [{
    title: '其他来源 - 最新章节',
    url: 'https://reader.example.test/book/other',
    snippet: '作者：示例作者。章节目录。'
  }];
  const { fetchImpl } = stubFetch([response({ results: [] }), response({ results: raw })]);
  const result = await searchBooks('其他', { apiKey: token, fetchImpl });
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].sourceId, null);
  assert.equal(result.candidates[0].supported, false);
});

for (const [status, code] of [
  [401, 'authentication_failed'],
  [402, 'insufficient_balance'],
  [429, 'rate_limited'],
  [504, 'gateway_timeout']
]) {
  test(`HTTP ${status} maps to ${code}`, async () => {
    const { fetchImpl } = stubFetch([response({ results: [] }, status)]);
    await rejectsWith(searchBooks('错误', { apiKey: token, fetchImpl }), code);
  });
}

test('network failure and invalid JSON have distinct codes', async () => {
  const network = stubFetch([new TypeError('synthetic network failure')]);
  await rejectsWith(searchBooks('网络', { apiKey: token, fetchImpl: network.fetchImpl }), 'network_error');

  const invalid = stubFetch([{
    ok: true,
    status: 200,
    json: async () => { throw new SyntaxError('synthetic parse failure'); }
  }]);
  await rejectsWith(searchBooks('解析', { apiKey: token, fetchImpl: invalid.fetchImpl }), 'invalid_response');
});

test('timeout aborts the request without exposing the API key', async () => {
  const original = globalThis.setTimeout;
  globalThis.setTimeout = (callback, _delay, ...args) => original(callback, 0, ...args);
  try {
    const fetchImpl = async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('synthetic abort')), { once: true });
    });
    await rejectsWith(searchBooks('超时', { apiKey: token, fetchImpl }), 'timeout');
  } finally {
    globalThis.setTimeout = original;
  }
});

test('timeout also covers a response body that never finishes parsing', async () => {
  const original = globalThis.setTimeout;
  globalThis.setTimeout = (callback, _delay, ...args) => original(callback, 0, ...args);
  try {
    const fetchImpl = async (_url, options) => ({
      ok: true,
      status: 200,
      json: () => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('synthetic abort')), { once: true });
      })
    });
    await rejectsWith(searchBooks('解析超时', { apiKey: token, fetchImpl }), 'timeout');
  } finally {
    globalThis.setTimeout = original;
  }
});

test('caller cancellation is distinguished from timeout', async () => {
  const controller = new AbortController();
  const fetchImpl = async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('synthetic abort')), { once: true });
  });
  const pending = searchBooks('取消', { apiKey: token, fetchImpl, signal: controller.signal });
  controller.abort();
  await rejectsWith(pending, 'aborted');
});

test('authorization is sent only in the header and the key never appears in body', async () => {
  const { fetchImpl, requests } = stubFetch([response({ results: [] }), response({ results: [] })]);
  await searchBooks('认证', { apiKey: token, fetchImpl });
  assert.equal(requests.length, 2);
  for (const request of requests) {
    assert.equal(request.options.headers.Authorization, `Bearer ${token}`);
    assert.equal(request.options.body.includes(token), false);
  }
});
