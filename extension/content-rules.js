const pageSuffix = /\s*[（(]第\s*\d+(?:\s*\/\s*\d+)?\s*页[）)]\s*$/u;

export function cleanChapterTitle(value) {
  return String(value || '').replace(pageSuffix, '').trim();
}

export function cleanChapter(sourceId, chapter) {
  const title = cleanChapterTitle(chapter.title);
  if (!['wxdy', 'txxt'].includes(sourceId)) return { ...chapter, title };
  let body = String(chapter.text || '').replace(/\r/g, '').replace(/\u00a0/g, ' ');
  if (sourceId === 'wxdy') {
    body = body.replace(/一秒记住【订阅文学网\s+https?:\/\/(?:www\.)?wxdy\.cc\/?】，精彩小说无弹窗免费阅读[！!]/gu, '');
  }
  body = body.replace(/[（(]本章未完[，,]?\s*请点击下一页继续阅读[）)]/gu, '');
  const headingKey = value => cleanChapterTitle(value).replace(/\s+/gu, '');
  const lines = body.split('\n').map(line => line.trim()).filter(line => {
    if (!line || (sourceId === 'txxt' && line === '二三书库')) return false;
    return headingKey(line) !== headingKey(title);
  });
  return { ...chapter, title, text: lines.join('\n') };
}
