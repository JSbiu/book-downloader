import { getArtifact, getBook, listChapters, chaptersForBook } from './db.js';
import { cleanBookTitle } from './source-rules.js';
import { cleanChapter } from './content-rules.js';

const blobs = new Map();

async function build(id) {
  if (blobs.has(id)) return blobs.get(id);
  const artifact = await getArtifact(id);
  const book = artifact && await getBook(artifact.bookId);
  if (!book) throw new Error('missing_content');
  const records = await listChapters(book.id);
  const chapters = (artifact.chapterUrls
    ? artifact.chapterUrls.map(url => records.find(chapter => chapter.url === url)).filter(Boolean)
    : chaptersForBook(book, records).filter(chapter => chapter.index < book.rangeTotal))
    .map(chapter => cleanChapter(artifact.sourceId || book.sourceId, chapter));
  if (chapters.length !== artifact.chapterCount || !chapters.length) throw new Error('missing_content');
  const catalogCount = artifact.catalogCount ?? book.catalog.length;
  const title = cleanBookTitle(artifact.title || book.title);
  const scope = artifact.chapterCount < catalogCount
    ? `部分范围：已取得 ${artifact.chapterCount} 章；来源目录 ${catalogCount} 项。其余内容未包含。`
    : '范围：本次来源目录内的已识别章节。';
  const author = artifact.author ?? book.author;
  const text = ['\ufeff' + title, author ? '作者：' + author : '作者：未知',
    '来源：' + (artifact.catalogUrl || book.catalogUrl), '书页工程样机 · ' + scope,
    ...(artifact.sourceBlocked ? ['注意：此书源已被本书拉黑，已有正文可能不可用。'] : []), '',
    ...chapters.flatMap(chapter => [chapter.title, chapter.text, ''])].join('\n');
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const filename = (title || '书页TXT').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100)
    + (artifact.chapterCount < catalogCount ? '-部分范围' : '') + '.txt';
  const result = { blobUrl: URL.createObjectURL(blob), filename, bytes: blob.size };
  blobs.set(id, result);
  return result;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.target !== 'offscreen') return false;
  if (message.type === 'RELEASE') {
    const blob = blobs.get(message.artifactId);
    if (blob) URL.revokeObjectURL(blob.blobUrl);
    blobs.delete(message.artifactId);
    respond({ ok: true });
    return false;
  }
  if (message.type === 'BUILD') {
    build(message.artifactId).then(value => respond({ ok: true, ...value }),
      () => respond({ ok: false, code: 'export_failed' }));
    return true;
  }
  return false;
});
