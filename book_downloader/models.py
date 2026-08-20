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


@dataclass(frozen=True)
class SiteSearchHit:
    """站点公开搜索返回的一条小说结果。"""

    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class SiteSearchRequest:
    """站点公开搜索所需的 HTTP 请求描述。"""

    url: str
    method: str = "GET"
    data: bytes | str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    response_encoding: str | None = None
