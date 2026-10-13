from dataclasses import dataclass
from datetime import datetime

FIXED_CATEGORIES: dict[str, str] = {
    "10": "Music",
    "20": "Gaming",
    "24": "Entertainment",
    "25": "News & Politics",
    "17": "Sports",
    "1": "Film & Animation",
    "28": "Science & Technology",
    "23": "Comedy",
}
DEFAULT_CATEGORY_NAMES: dict[str, str] = dict(FIXED_CATEGORIES)
OTHER = "other"
OTHER_NAME = "기타"


@dataclass(frozen=True)
class Item:
    rank: int
    id: str
    title: str
    channel_id: str
    channel_title: str
    category_id: str
    thumbnail: str
    published_at: str
    views: int
    likes: int
    comments: int
    description: str = ""   # 앞 200자, 태깅 입력용


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    bucket: str
    items: tuple[Item, ...]
