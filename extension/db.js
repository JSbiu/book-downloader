const NAME = 'shuye-renewal-engine-v1';
let opening;

export function database() {
  if (!opening) opening = new Promise((resolve, reject) => {
    const request = indexedDB.open(NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      db.createObjectStore('books', { keyPath: 'id' });
      db.createObjectStore('chapters', { keyPath: ['bookId', 'url'] });
      db.createObjectStore('artifacts', { keyPath: 'id' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('storage_failed'));
  });
  return opening;
}

function readRequest(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('storage_failed'));
  });
}

export async function getBook(id) {
  const db = await database();
  return readRequest(db.transaction('books').objectStore('books').get(id));
}

export async function listBooks() {
  const db = await database();
  const records = await readRequest(db.transaction('books').objectStore('books').getAll());
  return records.sort((a, b) => a.createdAt - b.createdAt);
}

export async function listChapters(bookId) {
  const db = await database();
  const range = IDBKeyRange.bound([bookId, ''], [bookId, '\uffff']);
  const records = await readRequest(db.transaction('chapters').objectStore('chapters').getAll(range));
  return records.sort((a, b) => a.index - b.index);
}

// Positions can change when the directory is refreshed. Reuse only the same
// source chapter URL; records from a previous source remain stored separately.
export function chaptersForBook(book, records) {
  const byUrl = new Map(records.map(record => [canonicalChapterUrl(record.url) || record.url, record]));
  return book.catalog.flatMap((entry, index) => {
    const record = byUrl.get(canonicalChapterUrl(entry.url) || entry.url);
    return record ? [{ ...record, storedUrl: record.url, url: entry.url, index }] : [];
  });
}

export async function getArtifact(id) {
  const db = await database();
  return readRequest(db.transaction('artifacts').objectStore('artifacts').get(id));
}

export async function listArtifacts() {
  const db = await database();
  return readRequest(db.transaction('artifacts').objectStore('artifacts').getAll());
}

export async function putArtifact(record) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('artifacts', 'readwrite');
    transaction.objectStore('artifacts').put(record);
    transaction.oncomplete = () => resolve(record);
    transaction.onabort = transaction.onerror = () => reject(new Error('storage_failed'));
  });
}

// All checks and chapter/cursor writes happen in one transaction. The callback
// must be synchronous: awaiting inside it would allow IndexedDB to auto-commit.
export async function changeBook(id, update, chapter = null) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['books', 'chapters'], 'readwrite');
    const books = transaction.objectStore('books');
    const request = books.get(id);
    let result;
    request.onsuccess = () => {
      try {
        result = update(request.result);
        if (!result) return;
        result.updatedAt = Date.now();
        books.put(result);
        if (chapter) transaction.objectStore('chapters').put(chapter);
      } catch { transaction.abort(); }
    };
    transaction.oncomplete = () => resolve(result);
    transaction.onabort = transaction.onerror = () => reject(new Error('storage_failed'));
  });
}
import { canonicalChapterUrl } from './source-rules.js';
