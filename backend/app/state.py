"""상태 파일(JSON) ↔ 메모리 객체. 손상된 파일은 경고 후 빈 상태로 시작한다."""
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models import Item, Snapshot
from app.store.memory import MemoryStore

log = logging.getLogger(__name__)

_ITEM_FIELDS = {f.name for f in dataclasses.fields(Item)}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_to_dict(s: Snapshot) -> dict:
    return {"captured_at": _iso(s.captured_at), "bucket": s.bucket,
            "items": [dataclasses.asdict(it) for it in s.items]}


def snapshot_from_dict(d: dict) -> Snapshot:
    items = tuple(Item(**{k: v for k, v in it.items() if k in _ITEM_FIELDS}) for it in d["items"])
    captured = datetime.fromisoformat(d["captured_at"].replace("Z", "+00:00"))
    return Snapshot(captured, d["bucket"], items)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("state file unreadable, starting fresh: %s (%s)", path.name, type(e).__name__)
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_store(path: Path, size: int) -> MemoryStore:
    store = MemoryStore(size)
    raw = read_json(path, [])
    try:
        snaps = sorted((snapshot_from_dict(d) for d in raw), key=lambda s: s.captured_at)
    except (KeyError, TypeError, ValueError) as e:
        log.warning("snapshots unreadable, starting fresh: %s", type(e).__name__)
        snaps = []
    for s in snaps:
        store.put(s)
    return store


def save_store(store: MemoryStore, path: Path) -> None:
    write_json(path, [snapshot_to_dict(s) for s in store.since(EPOCH)])
