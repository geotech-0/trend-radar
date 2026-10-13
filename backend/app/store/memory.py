from collections import deque
from datetime import datetime

from app.models import Snapshot


class MemoryStore:
    """인메모리 링버퍼. 같은 버킷은 한 번만 저장(멱등). 단일 이벤트 루프 전용이라 락 없음."""

    def __init__(self, size: int) -> None:
        self._buf: deque[Snapshot] = deque(maxlen=size)
        self._buckets: set[str] = set()

    def put(self, snap: Snapshot) -> bool:
        if snap.bucket in self._buckets:
            return False
        if len(self._buf) == self._buf.maxlen:
            self._buckets.discard(self._buf[0].bucket)
        self._buf.append(snap)
        self._buckets.add(snap.bucket)
        return True

    def latest(self) -> Snapshot | None:
        return self._buf[-1] if self._buf else None

    def previous(self) -> Snapshot | None:
        return self._buf[-2] if len(self._buf) >= 2 else None

    def since(self, ts: datetime) -> list[Snapshot]:
        return [s for s in self._buf if s.captured_at >= ts]

    def nearest_before(self, ts: datetime) -> Snapshot | None:
        hits = [s for s in self._buf if s.captured_at <= ts]
        return hits[-1] if hits else None

    def oldest(self) -> Snapshot | None:
        return self._buf[0] if self._buf else None

    def __len__(self) -> int:
        return len(self._buf)
