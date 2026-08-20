# Local Worktree Memory

- Project root: `D:\workspace\Projects\book-downloader`
- Windows-first workflow; use PowerShell and the bundled Python runtime.
- Keep downloaded chapter cache and merged TXT outputs outside version-controlled source directories when possible.
- The original downloader was copied from `trxs_public_chapters_to_txt.py`; preserve its public-HTML-only behavior and do not add login, CAPTCHA, paywall, or anti-bot bypass logic.
- The organized project uses `book_downloader/` with site adapters, a shared cache under `cache/<site>/<catalog-url-hash>/`, and merged outputs under `outputs/`.
- The 23txxt site uses irregular page IDs and may split one logical chapter across multiple URLs; discovery starts from a directory or chapter URL and follows public continuation links without a manifest.
- The 23txxt directory mixes newest chapters, special chapters, and author announcements; its adapter must sort main chapters by displayed number, treat `开始阅读` as chapter 1, and filter announcements before caching.
- When a catalog reorder changes chapter positions, cache migration uses the exact chapter URL as identity, preserves the old directory as `chapters.stale-*`, and copies reusable files into their new positions.
- Actual 23txxt cached text starts with the mistaken site title `二三书库`, repeats headings such as `1.标题 (第1/2页)`, and inserts `（本章未完，请点击下一页继续阅读）`; its sanitizer removes only these confirmed templates and runs again when cached blocks are merged.
- Browser access has two modes: Playwright can launch an isolated visible profile, or `--browser-connect` can attach to a user-started normal Chrome via CDP after manual verification; do not add stealth or challenge-bypass logic.
