"""YouTube 수집기.

키 노출 방지 규칙:
- 키는 URL 쿼리에 실리므로 httpx/httpcore 로거는 WARNING 이상만(main.py에서 설정).
- httpx 예외의 str/repr은 URL(=키)을 담으므로 쓰지 않고 type 이름만 남긴다.
- 상류 응답 본문은 로그에만, 밖으로는 상태 코드만.
"""
import logging

import httpx

from app.config import Settings
from app.models import DEFAULT_CATEGORY_NAMES, Item
from app.upstream import error_summary

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"


class UpstreamError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"youtube upstream error: {status_code}")
        self.status_code = status_code


def _to_int(v: object) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def _parse_item(raw: dict, rank: int) -> Item:
    sn = raw["snippet"]
    st = raw.get("statistics") or {}
    thumbs = sn.get("thumbnails") or {}
    thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    return Item(
        rank=rank,
        id=raw["id"],
        title=sn.get("title", ""),
        channel_id=sn.get("channelId", ""),
        channel_title=sn.get("channelTitle", ""),
        category_id=str(sn.get("categoryId", "")),
        thumbnail=thumb,
        published_at=sn.get("publishedAt", ""),
        views=_to_int(st.get("viewCount")),
        likes=_to_int(st.get("likeCount")),
        comments=_to_int(st.get("commentCount")),
        description=(sn.get("description") or "")[:200],
    )


class YouTubeClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._http = httpx.AsyncClient(base_url=API, timeout=10)

    async def _get(self, path: str, params: dict) -> dict:
        try:
            r = await self._http.get(path, params={**params, "key": self._s.yt_api_key})
        except httpx.HTTPError as e:
            log.warning("youtube %s network error: %s", path, type(e).__name__)
            raise UpstreamError(0) from None
        if r.status_code != 200:
            log.warning("youtube %s status=%s %s", path, r.status_code, error_summary(r))
            raise UpstreamError(r.status_code)
        return r.json()

    async def fetch_categories(self) -> dict[str, str]:
        names = dict(DEFAULT_CATEGORY_NAMES)
        try:
            data = await self._get(
                "/videoCategories",
                {"part": "snippet", "regionCode": self._s.region_code, "hl": "ko"},
            )
            for c in data.get("items", []):
                title = (c.get("snippet") or {}).get("title")
                if title:
                    names[str(c["id"])] = title
        except Exception as e:
            log.warning("category load failed, using defaults: %s", type(e).__name__)
        return names

    async def fetch_trending(self) -> list[Item]:
        data = await self._get(
            "/videos",
            {
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "regionCode": self._s.region_code,
                "maxResults": self._s.max_results,
            },
        )
        items: list[Item] = []
        for raw in data.get("items", []):
            try:
                items.append(_parse_item(raw, len(items) + 1))
            except Exception as e:
                rid = raw.get("id") if isinstance(raw, dict) else None
                log.warning("skip item id=%s: %s", rid, type(e).__name__)
        return items

    async def aclose(self) -> None:
        await self._http.aclose()
