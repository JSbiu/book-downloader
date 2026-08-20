from dataclasses import dataclass


@dataclass(frozen=True)
class ChapterLink:
    """目录中的一个逻辑章节入口。"""

    number: int
    title: str
    url: str


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    content: str

    @property
    def block(self) -> str:
        return f"{self.title}\n\n{self.content}".strip()


@dataclass(frozen=True)
class BookPlan:
    title: str
    catalog_url: str
    chapters: tuple[ChapterLink, ...]
