from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from pathlib import Path
from shutil import copy2

from .models import BookPlan, Chapter


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


class BookCache:
    def __init__(self, root: Path, site: str, catalog_url: str):
        self.path = root / site / cache_key(catalog_url)
        self.path.mkdir(parents=True, exist_ok=True)
        self.chapter_dir = self.path / "chapters"
        self.chapter_dir.mkdir(parents=True, exist_ok=True)

    def save_plan(self, plan: BookPlan) -> int:
        """Save the current catalog and realign reusable cache files by URL.

        Chapter positions can move when a site prepends updates or removes author
        notices.  The URL is the stable identity here: preserve the old directory
        for recovery, then copy matching cached chapters into their new positions.
        """
        data = {
            "title": plan.title,
            "catalog_url": plan.catalog_url,
            "chapters": [
                {"number": item.number, "title": item.title, "url": item.url}
                for item in plan.chapters
            ],
        }
        plan_path = self.path / "book.json"
        migrated = 0
        if plan_path.exists():
            old_chapters: list[dict] = []
            try:
                old_data = json.loads(plan_path.read_text(encoding="utf-8"))
                old_chapters = old_data.get("chapters", [])
                old_by_number = {
                    item["number"]: item["url"]
                    for item in old_chapters
                }
                new_by_number = {
                    item["number"]: item["url"] for item in data["chapters"]
                }
                common_numbers = old_by_number.keys() & new_by_number.keys()
                mapping_changed = bool(old_by_number and new_by_number) and (
                    not common_numbers
                    or any(
                        old_by_number[number] != new_by_number[number]
                        for number in common_numbers
                    )
                )
            except (OSError, KeyError, TypeError, ValueError):
                mapping_changed = True
            if mapping_changed and any(self.chapter_dir.iterdir()):
                stale_dir = self.path / f"chapters.stale-{time.time_ns()}"
                self.chapter_dir.rename(stale_dir)
                self.chapter_dir.mkdir(parents=True, exist_ok=True)

                old_number_by_url: dict[str, int] = {}
                for item in old_chapters:
                    try:
                        old_number_by_url.setdefault(str(item["url"]), int(item["number"]))
                    except (KeyError, TypeError, ValueError):
                        continue
                for item in data["chapters"]:
                    old_number = old_number_by_url.get(item["url"])
                    if old_number is None:
                        continue
                    source = stale_dir / f"{old_number:06d}.txt"
                    if not source.is_file():
                        continue
                    copy2(source, self.chapter_path(item["number"]))
                    migrated += 1

        self._write_text_atomic(
            plan_path,
            json.dumps(data, ensure_ascii=False, indent=2),
        )
        return migrated

    def chapter_path(self, number: int) -> Path:
        return self.chapter_dir / f"{number:06d}.txt"

    def read(self, number: int) -> str | None:
        path = self.chapter_path(number)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def read_chapter(self, number: int) -> Chapter | None:
        text = self.read(number)
        if text is None:
            return None
        title, separator, content = text.partition("\n\n")
        if not separator:
            return Chapter(number=number, title=f"第{number}章", content=title)
        return Chapter(number=number, title=title.strip(), content=content.strip())

    def write(self, chapter: Chapter) -> None:
        self._write_text_atomic(self.chapter_path(chapter.number), chapter.block + "\n")

    def all_blocks(self, numbers: Iterable[int] | None = None) -> list[tuple[int, str]]:
        blocks: list[tuple[int, str]] = []
        if numbers is None:
            chapter_numbers = sorted(
                int(path.stem)
                for path in self.chapter_dir.glob("*.txt")
                if path.stem.isdigit()
            )
        else:
            chapter_numbers = list(numbers)
        for number in chapter_numbers:
            text = self.read(number)
            if text:
                blocks.append((number, text))
        return blocks

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
