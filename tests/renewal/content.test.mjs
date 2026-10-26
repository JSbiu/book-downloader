import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanChapter } from '../../extension/content-rules.js';

test('removes confirmed wxdy notices and glued page headings, including flattened page counts', () => {
  const chapter = { title: '第一章 示例章节（第12页）', text: [
    '第一章示例章节（第12页）', '原有正文应当保留。',
    '一秒记住【订阅文学网 https://www.wxdy.cc】，精彩小说无弹窗免费阅读！',
    '（本章未完，请点击下一页继续阅读）第一章示例章节（第22页）', '下一页的正文。'
  ].join('\n') };
  assert.deepEqual(cleanChapter('wxdy', chapter), {
    title: '第一章 示例章节', text: '原有正文应当保留。\n下一页的正文。'
  });
});

test('keeps story paragraphs, page mentions, and similar site names', () => {
  const text = '她翻到第12页，记住了那句话。\n订阅文学网是文中提到的名字。\n（本章完）\n他发现了一件事。';
  assert.equal(cleanChapter('wxdy', { title: '第一章 示例', text }).text, text);
  assert.equal(cleanChapter('other', { title: '第一章 示例', text: '二三书库\n正文。' }).text, '二三书库\n正文。');
});

test('reapplying cleanup to cached text is stable', () => {
  const once = cleanChapter('wxdy', { title: '第一章 示例（第1/2页）', text: '第一章 示例（第1/2页）\n保留的正文。' });
  assert.deepEqual(cleanChapter('wxdy', once), once);
});
