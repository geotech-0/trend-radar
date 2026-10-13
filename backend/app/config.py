"""환경변수 → Settings. 키는 환경변수(로컬은 ~/capstone/.env, CI는 Actions Secrets)로만 받고 repr에서 가린다."""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# 실제 환경변수가 .env보다 우선한다 (.env가 없는 CI에서는 아무 일도 하지 않는다)
load_dotenv(Path.home() / "capstone" / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    yt_api_key: str = field(repr=False)
    gemini_api_key: str = field(repr=False)
    region_code: str
    max_results: int
    collect_interval_sec: int
    ring_size: int
    gemini_model: str
    brief_max_tokens: int
    tag_max_tokens: int


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


@lru_cache
def get_settings() -> Settings:
    ring_size = int(os.environ.get("RING_SIZE", "48"))
    if ring_size < 2:
        raise RuntimeError("RING_SIZE must be >= 2 (a baseline needs two snapshots)")
    return Settings(
        yt_api_key=_require("YT_API_KEY"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        region_code=os.environ.get("REGION_CODE", "KR"),
        max_results=int(os.environ.get("MAX_RESULTS", "30")),
        collect_interval_sec=int(os.environ.get("COLLECT_INTERVAL_SEC", "1800")),
        ring_size=ring_size,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
        brief_max_tokens=int(os.environ.get("BRIEF_MAX_TOKENS", "1200")),
        tag_max_tokens=int(os.environ.get("TAG_MAX_TOKENS", "8000")),
    )
