"""기준선 비교 파생. 전부 순수 함수."""
from collections import Counter
from dataclasses import dataclass

from app.models import FIXED_CATEGORIES, OTHER, OTHER_NAME, Item, Snapshot


@dataclass(frozen=True)
class Ranked:
    item: Item
    delta: int | None  # baseline_rank - rank (양수=▲), None=기준선 없음 또는 NEW
    is_new: bool


def rank_deltas(latest: Snapshot, baseline: Snapshot | None) -> tuple[list[Ranked], int]:
    if baseline is None:
        return [Ranked(it, None, False) for it in latest.items], 0
    prev = {it.id: it.rank for it in baseline.items}
    ranked = [
        Ranked(it, prev[it.id] - it.rank, False) if it.id in prev else Ranked(it, None, True)
        for it in latest.items
    ]
    dropped = len(prev.keys() - {it.id for it in latest.items})
    return ranked, dropped


def category_share(snap: Snapshot) -> dict[str, float]:
    n = len(snap.items) or 1
    counts = Counter(it.category_id if it.category_id in FIXED_CATEGORIES else OTHER for it in snap.items)
    return {cid: counts.get(cid, 0) / n for cid in [*FIXED_CATEGORIES, OTHER]}


def trend_series(snaps: list[Snapshot]) -> list[dict]:
    """시각별 점유율과 진입/이탈 수. 진입/이탈은 리스트상 바로 앞 스냅샷 대비, 첫 점은 0."""
    points = []
    prev_ids: set[str] | None = None
    for s in snaps:
        ids = {it.id for it in s.items}
        points.append({
            "captured_at": s.captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "shares": category_share(s),
            "entered": len(ids - prev_ids) if prev_ids is not None else 0,
            "exited": len(prev_ids - ids) if prev_ids is not None else 0,
        })
        prev_ids = ids
    return points


def category_name(cid: str, names: dict[str, str]) -> str:
    return names.get(cid, OTHER_NAME)


def summary_stats(latest: Snapshot, names: dict[str, str], dropped: int) -> dict:
    counts = Counter(it.category_id for it in latest.items)
    top = None
    if counts:
        cid, cnt = counts.most_common(1)[0]
        top = {"id": cid, "name": category_name(cid, names), "count": cnt}
    return {
        "total_views": sum(it.views for it in latest.items),
        "channels": len({it.channel_id for it in latest.items}),
        "top_category": top,
        "dropped": dropped,
    }
