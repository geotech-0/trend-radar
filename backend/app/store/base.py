from datetime import datetime, timezone
from typing import Protocol

from app.models import Snapshot


def bucket_of(ts: datetime, interval_sec: int) -> str:
    """floor(epoch / interval) * interval → 'YYYY-MM-DDTHH:MM:SSZ' (UTC)."""
    epoch = int(ts.timestamp()) // interval_sec * interval_sec
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SnapshotStore(Protocol):
    """스냅샷 저장소 계약. 원작은 같은 계약을 DynamoDB로 구현한다.

    DynamoDB 대응 (pk=scope 예: "trending#KR", sk=bucket):
      put            PutItem + ConditionExpression="attribute_not_exists(sk)",
                     ConditionalCheckFailedException → False
      latest/previous Query(pk=scope, ScanIndexForward=False, Limit=2)
      since          Query(pk=scope, sk >= bucket_of(ts))
      nearest_before Query(pk=scope, sk <= bucket_of(ts), ScanIndexForward=False, Limit=1)
    """

    def put(self, snap: Snapshot) -> bool: ...
    def latest(self) -> Snapshot | None: ...
    def previous(self) -> Snapshot | None: ...
    def since(self, ts: datetime) -> list[Snapshot]: ...
    def nearest_before(self, ts: datetime) -> Snapshot | None: ...
    def oldest(self) -> Snapshot | None: ...
    def __len__(self) -> int: ...
