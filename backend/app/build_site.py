"""정적 사이트 빌더 (GitHub Actions에서 30분마다 실행).

상태 읽기 → 수집(멱등 저장) → 파생 → 태깅/브리핑(Gemini) → site/ JSON 쓰기 → 상태 저장.
어느 단계가 실패해도 이전 데이터로 사이트를 다시 만들고 종료 코드 0으로 끝난다.

    python -m app.build_site --state DIR --out DIR [--frontend DIR]
"""
import argparse
import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.collector.youtube import UpstreamError, YouTubeClient
from app.config import Settings, get_settings
from app.derive.ranking import category_name, rank_deltas, summary_stats, trend_series
from app.llm.brief import build_prompt
from app.llm.gemini import GeminiClient, LLMError
from app.llm.tagger import Tagger
from app.models import DEFAULT_CATEGORY_NAMES, FIXED_CATEGORIES, OTHER, OTHER_NAME, Snapshot
from app.state import EPOCH, load_store, read_json, save_store, write_json
from app.store.base import bucket_of
from app.store.memory import MemoryStore

log = logging.getLogger("build_site")

# 하루 1회 운영: 예약 실행이 늦게 시작돼도 전날 실행을 기준선으로 잡도록 20시간으로 둔다
DAILY_LOOKBACK = timedelta(hours=20)


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def daily_baseline(store: MemoryStore, latest: Snapshot) -> Snapshot | None:
    base = store.nearest_before(latest.captured_at - DAILY_LOOKBACK) or store.previous()
    return None if base is latest else base


def _due(meta: dict, key: str, every_sec: int, now: datetime) -> bool:
    """마지막 시도 후 every_sec가 지났는가. 실패해도 시도 시각을 남겨 한도를 지킨다."""
    last = meta.get(key)
    return not last or (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() >= every_sec


async def refresh_briefs(llm: GeminiClient, store: MemoryStore, latest: Snapshot,
                         names: dict[str, str], briefs: dict, s: Settings,
                         meta: dict, now: datetime) -> dict:
    """brief_every_sec마다, 버킷이 바뀐 모드만 다시 생성한다. 실패하면 이전 브리핑을 stale로 남긴다."""
    out = dict(briefs)
    for mode in ("now", "daily"):
        prev = briefs.get(mode)
        if prev and prev.get("bucket") == latest.bucket and not prev.get("stale"):
            continue
        if not _due(meta, f"brief_{mode}_at", s.brief_every_sec if prev else s.collect_interval_sec, now):
            continue
        meta[f"brief_{mode}_at"] = _iso(now)
        baseline = store.previous() if mode == "now" else daily_baseline(store, latest)
        system, user = build_prompt(mode, latest, baseline, names)
        try:
            text = await llm.generate(system, user, max_tokens=s.brief_max_tokens)
        except LLMError as e:
            log.warning("brief %s failed: status=%s", mode, e.status)
            if prev:
                out[mode] = {**prev, "stale": True}
            continue
        out[mode] = {"text": text, "bucket": latest.bucket, "captured_at": _iso(latest.captured_at),
                     "baseline_at": _iso(baseline.captured_at) if baseline else None, "stale": False}
        log.info("brief %s generated (%d chars)", mode, len(text))
    return out


def trending_payload(store: MemoryStore, names: dict[str, str], tagger: Tagger,
                     s: Settings, now: datetime, last_error: str | None) -> dict:
    latest, baseline = store.latest(), store.previous()
    base = {"generated_at": _iso(now), "interval_sec": s.collect_interval_sec,
            "categories": {cid: category_name(cid, names) for cid in FIXED_CATEGORIES},
            "meta": {"last_error": last_error}}
    if latest is None:
        return {**base, "captured_at": None, "baseline_at": None, "stats": None, "items": []}
    ranked, dropped = rank_deltas(latest, baseline)
    items = [{
        "rank": r.item.rank, "id": r.item.id, "title": r.item.title, "channel": r.item.channel_title,
        "category_id": r.item.category_id, "category_name": category_name(r.item.category_id, names),
        "thumbnail": r.item.thumbnail, "views": r.item.views, "likes": r.item.likes,
        "delta": r.delta, "is_new": r.is_new, "url": f"https://www.youtube.com/watch?v={r.item.id}",
        "description": r.item.description[:160], "tags": tagger.get(r.item.id),
    } for r in ranked]
    return {**base, "captured_at": _iso(latest.captured_at),
            "baseline_at": _iso(baseline.captured_at) if baseline else None,
            "stats": summary_stats(latest, names, dropped), "items": items}


def trends_payload(store: MemoryStore, names: dict[str, str], now: datetime) -> dict:
    return {"categories": {**{cid: category_name(cid, names) for cid in FIXED_CATEGORIES}, OTHER: OTHER_NAME},
            "points": trend_series(store.since(EPOCH))}   # 링버퍼 전체(최대 48회 실행)


def brief_payload(briefs: dict) -> dict:
    return {mode: {k: v for k, v in b.items() if k != "bucket"} for mode, b in briefs.items()}


async def run(state_dir: Path, out_dir: Path, frontend_dir: Path) -> None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    store = load_store(state_dir / "snapshots.json", s.ring_size)
    briefs = read_json(state_dir / "brief.json", {})
    yt, llm = YouTubeClient(s), GeminiClient(s)
    tagger = Tagger(llm, s.tag_max_tokens, s.tag_model)
    meta = read_json(state_dir / "meta.json", {})
    tagger.load(read_json(state_dir / "tags.json", {}))
    names, last_error = dict(DEFAULT_CATEGORY_NAMES), None
    try:
        names = await yt.fetch_categories()
        try:
            items = await yt.fetch_trending()
            if items:
                stored = store.put(Snapshot(now, bucket_of(now, s.collect_interval_sec), tuple(items)))
                log.info("collected %d items stored=%s snapshots=%d", len(items), stored, len(store))
        except UpstreamError as e:
            last_error = f"youtube {e.status_code}"
            log.warning("collect failed: %s", last_error)
        latest = store.latest()
        if latest is not None:
            keep = {it.id for snap in store.since(EPOCH) for it in snap.items}
            if tagger.missing(latest.items) and _due(meta, "tag_at", s.tag_every_sec if tagger.dump() else s.collect_interval_sec, now):
                meta["tag_at"] = _iso(now)
                await tagger.tag(latest.items, keep)
            briefs = await refresh_briefs(llm, store, latest, names, briefs, s, meta, now)
    finally:
        await yt.aclose()
        await llm.aclose()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(frontend_dir, out_dir)
    write_json(out_dir / "data" / "trending.json", trending_payload(store, names, tagger, s, now, last_error))
    write_json(out_dir / "data" / "trends.json", trends_payload(store, names, now))
    write_json(out_dir / "data" / "brief.json", brief_payload(briefs))

    save_store(store, state_dir / "snapshots.json")
    write_json(state_dir / "tags.json", tagger.dump())
    write_json(state_dir / "brief.json", briefs)
    write_json(state_dir / "meta.json", meta)
    log.info("site written to %s", out_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx는 INFO에서 요청 URL(=YouTube 키가 든 쿼리)을 찍는다
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description="Build the Trend Radar static site")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frontend", type=Path, default=root / "frontend")
    a = p.parse_args()
    asyncio.run(run(a.state, a.out, a.frontend))


if __name__ == "__main__":
    main()
