# Trend Radar — 설계 문서

- 작성일: 2026-10-13
- 상태: 구현 완료 (Phase 1~4, 2026-10-13). 구현 중 확정된 사항은 §14
- 범위: 유튜브 한국 급상승(mostPopular) 수집·분석 서비스, 단일 프로세스(:8000)

## 0. 목표와 제약

**목표**: YouTube Data API v3의 한국 인기 동영상 30개를 주기적으로 스냅샷으로 저장하고,
스냅샷 간 비교로 순위 변화·신규 진입·이탈·카테고리 점유율을 파생해 한 장짜리 웹 화면과
Bedrock(Claude) 한국어 브리핑으로 보여준다.

**제약**
- 시간 2시간, 자동화 테스트 생략. 검증은 curl과 브라우저로 한다.
- 백엔드는 Python 3.11 이상 + FastAPI. 이 환경은 Python 3.14이고 `uv`로 관리한다.
- 프론트엔드는 빌드 없는 정적 HTML 한 장이다. FastAPI가 같은 포트에서 함께 서빙한다.
- 키(`YT_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`)는 환경변수로만 받는다. 코드, API 응답,
  로그 어디에도 키를 노출하지 않는다.
- 원작(운영판)은 수집 주기 3600초에 DynamoDB를 쓴다. 이번 워크샵판은 60초에 인메모리를 쓰고,
  나중에 교체할 수 있도록 인터페이스를 원작과 같게 맞춘다.

**성공 기준**
- `:8000` 하나로 화면과 API가 모두 동작한다.
- 두 번째 수집 이후 delta, NEW, 이탈 수가 나온다.
- 카테고리 필터, 시계열 차트, now/daily 브리핑이 동작한다.
- 응답과 로그를 `grep`으로 검사해서 키 문자열이 나오지 않는다.

## 1. 환경 관련 결정 (원 요구사항과 다른 점)

| 항목 | 원 요구사항 | 결정 | 이유 |
|---|---|---|---|
| Bedrock 리전 | `ap-northeast-2` | `BEDROCK_REGION` 설정값, 기본 `us-east-1` | 조직 SCP가 서울 리전의 `bedrock:InvokeModel`을 명시적으로 거부한다(2026-10-13 직접 호출해서 확인). `us-east-1`은 같은 모델로 호출에 성공했다 |
| Bedrock 인증 | Bearer만 사용 | `AWS_BEARER_TOKEN_BEDROCK`이 있으면 Bearer, 없으면 IAM 역할 자격 증명으로 SigV4 서명 | 이 EC2에는 Bedrock 권한이 있는 인스턴스 역할이 있고, `.env`에는 Bearer 토큰이 없다 |
| SDK 미사용 원칙 | httpx로 직접 REST 호출 | 그대로 지킨다. `botocore`는 SigV4 서명 헤더 생성에만 쓴다 | 요청 본문 구성과 HTTP 전송은 모두 httpx가 한다 |

## 2. 구조

```
capstone-4/                       (CDK 파일은 그대로 두고 옆에 추가)
├─ backend/
│  ├─ pyproject.toml              fastapi, uvicorn[standard], httpx, python-dotenv, botocore
│  └─ app/
│     ├─ __init__.py
│     ├─ config.py                Settings (frozen dataclass), load_dotenv
│     ├─ models.py                Item, Snapshot, 카테고리 상수
│     ├─ collector/
│     │  ├─ __init__.py
│     │  └─ youtube.py            YouTubeClient, UpstreamError, collect_loop
│     ├─ store/
│     │  ├─ __init__.py
│     │  ├─ base.py               SnapshotStore Protocol, bucket_of()
│     │  └─ memory.py             MemoryStore (링버퍼)
│     ├─ derive/
│     │  ├─ __init__.py
│     │  └─ ranking.py            rank_deltas, category_share, trend_series, stats
│     ├─ llm/
│     │  ├─ __init__.py
│     │  └─ bedrock.py            BedrockClient, BriefError, BriefService(캐시)
│     ├─ api/
│     │  ├─ __init__.py
│     │  └─ routes.py             APIRouter
│     └─ main.py                  lifespan(수집 태스크) + 라우터 + StaticFiles
└─ frontend/
   └─ index.html
```

실행: `cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`

### 2.1 스케줄링 방식

FastAPI `lifespan`에서 `asyncio.create_task(collect_loop(...))`를 띄우고, 종료할 때 cancel한다.
APScheduler는 이번 범위에 과하다. 수집기를 별도 프로세스로 빼면 인메모리 스토어를 공유할 수
없다. 그래서 둘 다 쓰지 않는다.

## 3. 설정 (`config.py`)

- 모듈을 불러올 때 `load_dotenv(Path.home()/"capstone/.env", override=False)`를 실행한다.
  이미 설정된 실제 환경변수가 `.env`보다 우선한다.
- `Settings`는 frozen dataclass다. `get_settings()`가 한 번 만들고 그 뒤로는 같은 객체를 돌려준다.

| 필드 | 환경변수 | 기본값 |
|---|---|---|
| `yt_api_key` | `YT_API_KEY` | 필수. 없으면 기동 시 에러 |
| `region_code` | `REGION_CODE` | `KR` |
| `max_results` | `MAX_RESULTS` | `30` |
| `collect_interval_sec` | `COLLECT_INTERVAL_SEC` | `60` (원작은 `3600`) |
| `ring_size` | `RING_SIZE` | `48`. 2 미만이면 기동 시 에러 |
| `bedrock_region` | `BEDROCK_REGION` | `us-east-1` |
| `bedrock_model` | `BEDROCK_MODEL` | `global.anthropic.claude-sonnet-4-6` |
| `bedrock_bearer` | `AWS_BEARER_TOKEN_BEDROCK` | `None` |
| `brief_max_tokens` | `BRIEF_MAX_TOKENS` | `800` |

- `Settings.__repr__`는 키 필드를 `***`로 가린다. 디버그 출력에 설정 객체가 찍혀도 키가 보이지 않게 하기 위해서다.

## 4. 데이터 모델 (`models.py`)

```python
@dataclass(frozen=True)
class Item:
    rank: int            # 1부터 시작, 수집 응답 순서
    id: str              # videoId
    title: str
    channel_id: str
    channel_title: str
    category_id: str
    thumbnail: str       # medium(320x180), 없으면 default
    published_at: str
    views: int
    likes: int
    comments: int

@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime   # 실제 수집 시각 (UTC)
    bucket: str             # 시각 버킷 키
    items: tuple[Item, ...]
```

- 고정 카테고리 8개: `FIXED_CATEGORIES = {"1": "Film & Animation", "10": "Music", "17": "Sports",
  "20": "Gaming", "23": "Comedy", "24": "Entertainment", "25": "News & Politics",
  "28": "Science & Technology"}`
- `DEFAULT_CATEGORY_NAMES = dict(FIXED_CATEGORIES)`(영문 이름)이다. `videoCategories`를 불러오지
  못했을 때 그대로 쓴다. 성공하면 한국어 이름(hl=ko)으로 덮어쓴다. 이름표에 없는 ID는 `"기타"`로 표시한다.
- 8개에 들지 않는 카테고리는 점유율과 차트에서 `other`로 묶는다.

## 5. 수집기 (`collector/youtube.py`)

### 5.1 YouTubeClient
- `httpx.AsyncClient`를 하나 만들어 계속 쓴다(타임아웃 10초). `aclose()`는 lifespan이 끝날 때 호출한다.
- `fetch_categories() -> dict[str, str]`
  - `GET /youtube/v3/videoCategories?part=snippet&regionCode=KR&hl=ko&key=…`
  - 실패하면 경고 로그를 남기고 기본 이름표를 돌려준다. 예외를 밖으로 던지지 않는다.
  - 성공하면 결과를 기본 이름표 위에 덮어써서 돌려준다.
- `fetch_trending() -> list[Item]`
  - `GET /youtube/v3/videos?part=snippet,statistics&chart=mostPopular&regionCode=KR&maxResults=30&key=…`
  - 상태 코드가 200이 아니면 응답 본문 앞 500자를 `logger.warning`으로 남기고
    `UpstreamError(status_code)`를 던진다.
  - 네트워크 오류나 타임아웃이면 `UpstreamError(0)`을 던진다.
  - `items`를 하나씩 `_parse_item(raw, rank)`로 변환한다. 변환 중 예외가 나면 그 아이템만
    건너뛰고 경고 로그(videoId와 예외 타입)를 남긴다.
- `_to_int(v)`는 `int(v)`를 시도하고 실패하면 0을 돌려준다(None, 비숫자, 음수 포함).

### 5.2 키 노출 방지 규칙
1. `logging.getLogger("httpx")`와 `httpcore` 로거를 WARNING으로 설정한다. httpx는 INFO
   레벨에서 요청 URL을 로그로 남기는데, 이 URL의 쿼리에 키가 들어 있다.
2. httpx 예외에 `str(exc)`나 `repr(exc)`를 절대 쓰지 않는다. 메시지에 URL이 들어 있다.
   로그에는 `type(exc).__name__`만 남긴다.
3. `UpstreamError`가 담는 정보는 `status_code` 하나뿐이고, 메시지는 `"youtube upstream error: {code}"`다.
4. 상류 응답 본문은 로그에만 남긴다. API 응답에는 절대 싣지 않는다.

### 5.3 collect_loop
```
categories = await client.fetch_categories()
app.state.categories = categories
while True:
    try:
        items = await client.fetch_trending()
        if items:
            now = utcnow()
            stored = store.put(Snapshot(now, bucket_of(now, interval), tuple(items)))
            log.info("collected %d items bucket=%s stored=%s", ...)
    except UpstreamError as e:
        log.warning("collect failed: status=%s", e.status_code)
    except Exception as e:
        # log.exception은 쓰지 않는다: traceback에 httpx 예외 메시지(=키가 든 URL)가 섞일 수 있다
        log.error("collect crashed: %s", type(e).__name__)
    await asyncio.sleep(interval)
```
- 서버가 켜지면 바로 1회 수집하고, 그다음부터 `interval`마다 수집한다.
- 어떤 예외가 나도 루프는 멈추지 않는다. `CancelledError`만 예외로, 그대로 위로 전파한다.

## 6. 스냅샷 스토어 (`store/`)

### 6.1 인터페이스
```python
def bucket_of(ts: datetime, interval_sec: int) -> str:
    """floor(epoch / interval) * interval → 'YYYY-MM-DDTHH:MM:SSZ' (UTC)"""

class SnapshotStore(Protocol):
    def put(self, snap: Snapshot) -> bool: ...          # 이미 있는 버킷이면 False (멱등)
    def latest(self) -> Snapshot | None: ...
    def previous(self) -> Snapshot | None: ...          # latest 바로 앞 = 기준선
    def since(self, ts: datetime) -> list[Snapshot]: ...  # captured_at >= ts, 오래된 순
    def nearest_before(self, ts: datetime) -> Snapshot | None: ...  # captured_at <= ts 중 최신
    def oldest(self) -> Snapshot | None: ...
    def __len__(self) -> int: ...
```

### 6.2 MemoryStore
- `deque[Snapshot](maxlen=ring_size)`과 `set[str]`(지금 들어 있는 버킷 목록)으로 구현한다.
- `put` 동작:
  - 버킷이 집합에 이미 있으면 False를 돌려준다.
  - 버퍼가 가득 차 있으면 가장 오래된 스냅샷의 버킷을 집합에서 먼저 지운다. 그다음 append한다.
- 모든 메서드는 같은 이벤트 루프 안에서만 호출한다. 그래서 락이 필요 없다.

### 6.3 DynamoDB로 바꿀 때의 대응 (docstring에 기록, 이번에는 구현하지 않음)
| 메서드 | DynamoDB |
|---|---|
| `put` | `PutItem(pk=scope, sk=bucket, ConditionExpression="attribute_not_exists(sk)")`. `ConditionalCheckFailedException`이 나면 False |
| `latest`/`previous` | `Query(pk=scope, ScanIndexForward=False, Limit=2)` |
| `since` | `Query(pk=scope, sk >= bucket_of(ts))` |
| `nearest_before` | `Query(pk=scope, sk <= bucket_of(ts), ScanIndexForward=False, Limit=1)` |

`scope`는 예를 들어 `"trending#KR"`이다.

## 7. 파생 (`derive/ranking.py`), 모두 순수 함수

```python
@dataclass(frozen=True)
class Ranked:
    item: Item
    delta: int | None   # baseline_rank - rank. 양수=▲, 음수=▼, 0=유지, None=기준선 없음 또는 NEW
    is_new: bool

def rank_deltas(latest: Snapshot, baseline: Snapshot | None) -> tuple[list[Ranked], int]:
    """(순위 정보가 붙은 아이템 목록, 이탈 수)를 돌려준다."""
```
- `baseline`이 None이면(첫 스냅샷) 모든 아이템이 `delta=None, is_new=False`이고, 이탈 수는 0이다.
- `baseline`이 있을 때:
  - 기준선에 있던 아이템: `delta = 기준선 순위 - 현재 순위`, `is_new=False`
  - 기준선에 없던 아이템: `delta=None`, `is_new=True`
  - 이탈 수 = 기준선의 id 집합에서 현재 id 집합을 뺀 개수
- `category_share(snap) -> dict[str, float]`: 8개 고정 ID와 `other`의 비율. 분모는 `len(items)`이고 합계는 1.0이다.
- `trend_series(snaps: list[Snapshot]) -> list[dict]`: 각 시각마다
  `{captured_at, shares, entered, exited}`를 만든다. `entered`/`exited`는 리스트에서 바로 앞
  스냅샷과 비교한 값이다. 첫 점은 둘 다 0이다.
- `summary_stats(latest, categories, dropped) -> dict`:
  `{total_views, channels(채널 수), top_category{id, name, count}, dropped}`

## 8. API 계약 (`api/routes.py`)

공통: 상태가 필요한 의존성(`store`, `categories`, `brief_service`, `settings`)은 `request.app.state`에서 꺼낸다.

### `GET /healthz`
`200 {"status":"ok","snapshots":N,"last_captured_at":ISO|null}`. 스냅샷이 없어도 200을 돌려준다.

### `GET /api/trending[?category=ID]`
- 스냅샷이 없으면 `503 {"detail":"no snapshot yet"}`을 돌려준다.
- `category`가 있는데 `FIXED_CATEGORIES`에 없는 값이면 `400 {"detail":"unknown category"}`을 돌려준다.
- 응답:
```json
{
  "captured_at": "...", "baseline_at": "..." | null,
  "category": "10" | null,
  "categories": {"10": "음악", "...": "..."},
  "stats": {"total_views": 0, "channels": 0, "top_category": {"id":"10","name":"음악","count":7}, "dropped": 3},
  "items": [{"rank":1, "id":"...", "title":"...", "channel":"...", "category_id":"10",
             "category_name":"음악", "thumbnail":"...", "views":0, "likes":0,
             "delta": 2 | null, "is_new": false, "url":"https://www.youtube.com/watch?v=..."}]
}
```
- 필터를 걸면 `items`만 해당 카테고리로 거른다. `rank`는 전체 30개 기준 순위를 그대로 두고, `stats`도 전체 30개 기준으로 계산한다.

### `GET /api/trends?hours=N`
- `hours`는 1~48의 정수이고 기본값은 6이다. 범위를 벗어나면 FastAPI 검증이 422를 돌려준다.
- `store.since(now - hours)`의 결과에 `trend_series`를 적용한다.
- 응답: `{"hours":N, "categories":{"1":"...", ..., "other":"기타"}, "points":[{"captured_at","shares":{...},"entered","exited"}]}`
- 스냅샷이 없으면 `points: []`, 상태 코드 200.

### `POST /api/brief?mode=now|daily`
- `mode`가 그 밖의 값이면 422. 스냅샷이 없으면 503.
- 기준선:
  - `now`: `store.previous()`. 진입/이탈 정보를 참고로만 쓴다.
  - `daily`: `store.nearest_before(latest.captured_at - 24 × interval)`. 원작에서는 24시간 전,
    워크샵에서는 24분 전이 된다. 그런 스냅샷이 없으면 `store.oldest()`를 쓴다. 그 결과가
    latest와 같으면 기준선 없음으로 처리한다.
- 성공: `200 {"mode","captured_at","baseline_at","text","cached":bool}`
- Bedrock 실패: `{"detail":"bedrock error: {status}"}`. 타임아웃이면 **504**(`timeout`), 그 밖은 **502**다.
  502의 `status`는 HTTP 상태 코드, `network`, `parse`, `no-credentials` 중 하나다.

## 9. 브리핑 (`llm/bedrock.py`)

### 9.1 BedrockClient.converse(system, user_text) -> str
- URL: `https://bedrock-runtime.{region}.amazonaws.com/model/{quote(model, safe='')}/converse`
- body:
```json
{"system":[{"text": SYSTEM}],
 "messages":[{"role":"user","content":[{"text": USER}]}],
 "inferenceConfig":{"maxTokens":800,"temperature":0.5}}
```
- body는 `json.dumps(..., ensure_ascii=False).encode()`로 **한 번만** 직렬화한다. 서명과 전송에 같은 바이트를 쓴다.
- 인증:
  - `bearer`가 있으면 `Authorization: Bearer {token}` 헤더를 쓴다.
  - 없으면 `botocore.session.get_session().get_credentials()`로 자격 증명을 얻는다. None이면
    `BriefError("no-credentials")`. 자격 증명이 있으면 `get_frozen_credentials()`를 거쳐
    `AWSRequest(method="POST", url, data=body, headers={"Content-Type":"application/json"})`를 만들고
    `SigV4Auth(creds, "bedrock", region).add_auth(req)`로 서명한 뒤 `dict(req.headers)`를 httpx에 넘긴다.
  - 자격 증명 조회는 IMDS를 호출할 수 있다. 그래서 서명은 `asyncio.to_thread`로 이벤트 루프 밖에서 한다.
- `httpx.AsyncClient(timeout=30)`
  - `TimeoutException`이면 `BriefError("timeout")`
  - 그 밖의 네트워크 오류면 `BriefError("network")`
  - 상태 코드가 200이 아니면 응답 본문에서 자격 증명(`x-amz-security-token:`, `Credential=`, `Bearer`)을
    `***`로 지운 뒤 앞 500자를 로그에 남기고 `BriefError(str(status))`. SigV4 불일치(403) 본문에는
    canonical request가 들어 있어 세션 토큰이 섞일 수 있다
  - `stopReason == "max_tokens"`면 잘림 경고를 로그에 남긴다
  - 응답 파싱: `data["output"]["message"]["content"]` 중 `text` 키가 있는 블록을 이어 붙인다.
    KeyError, TypeError가 나거나 결과가 빈 문자열이면 `BriefError("parse")`.

### 9.2 프롬프트
- **SYSTEM (한국어)**: "너는 한국 유튜브 트렌드 분석가다. 입력은 현재 인기 동영상 30개(그리고
  선택적으로 기준선 대비 변화)다. 다음 형식으로 한국어 마크다운을 쓴다:
  ## 주제 클러스터 (3~4개, 각 클러스터마다 대표 영상과 왜 뜨는지 1~2문장),
  ## 카테고리 흐름 (분포에서 읽히는 흐름 2~3문장),
  ## 인사이트 (제작/시청 관점 2~3줄 불릿).
  전체 600자 이내, 세 섹션을 모두 쓴다, 구분선 금지. 입력에 없는 사실은 지어내지 않는다."
  (한국어는 거의 1글자 1토큰이라 maxTokens 800에서 900자 지시도 잘렸다. 600자에서 700자 안팎으로 완결된다)
- **USER**: 첫 줄에 수집 시각과 모드를 쓴다. 그다음 아이템을 한 줄에 하나씩 쓴다:
  `{rank}. {title} | {channel} | {category_name} | 조회 {views} | {▲n/▼n/=/NEW/-}`.
  daily 모드면 `기준선 {baseline_at} 대비: 진입 N개(제목들), 이탈 M개(제목들)`와
  두 시점의 카테고리 점유율을 덧붙인다.

### 9.3 BriefService (캐시)
- `_cache: dict[(mode, bucket), str]`, `_inflight: dict[(mode, bucket), asyncio.Task]`
- 요청 키의 bucket이 캐시의 bucket과 다르면 오래된 항목을 모두 지운다.
- 캐시에 없으면 진행 중인 Task를 찾아 함께 기다리고, 없으면 새로 만든다. 같은 키의 동시 요청은
  성공이든 실패든 **한 번의 호출 결과를 공유**한다. 장애 중 N개 요청이 와도 상류 호출은 1회다.
- `asyncio.shield`로 기다리므로 요청 하나가 끊겨도 다른 요청이 기다리는 호출은 취소되지 않는다.
- 실패는 캐시하지 않는다. 다음 요청이 다시 시도한다.
- 응답의 `cached`는 이 요청이 Bedrock을 직접 부르지 않았으면 true다.

## 10. 화면 (`frontend/index.html`)

- 파일 하나에 인라인 `<style>`과 `<script>`를 넣는다. 외부 스크립트나 라이브러리는 쓰지 않는다.
- **테마**:
  - 색은 모두 `:root` CSS 변수로 정의한다(`--bg --surface --text --muted --border --accent
    --up --down --new`와 카테고리 9색).
  - 다크 값은 두 곳에 정의한다. `@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {…} }`와
    `:root[data-theme=dark] {…}`.
  - 토글을 누르면 `document.documentElement.dataset.theme`를 바꾸고 `localStorage`에 저장한다.
    `localStorage` 접근은 `try/catch`로 감싼다.
- **화면 구성**
  1. 헤더: 타이틀 "Trend Radar", 마지막 수집 시각(KST 기준, 상대 시간 병기), 새로고침 버튼, 테마 토글
  2. 통계 바: 합산 조회수 / 채널 수 / 최다 카테고리(이름과 개수) / 이탈 수
  3. 카테고리 칩: "전체"와 8개 칩. 선택된 칩을 강조한다. 칩을 누르면 `/api/trending?category=` 요청
  4. 카드 그리드: `grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))`
     - 썸네일은 `aspect-ratio: 16/9; object-fit: cover`, `loading="lazy"`
     - 썸네일 위 왼쪽에 순위 배지, 오른쪽에 delta 배지(▲n 초록 / ▼n 빨강 / NEW 강조색)
     - 제목은 `-webkit-line-clamp: 2`로 두 줄까지만 보여 준다. 그 아래에 채널, 조회수, 좋아요를 표시한다
     - 카드 전체가 `<a href=url target=_blank rel="noopener">`다
  5. 시계열: `/api/trends?hours=N`(N은 1/6/24 중 선택, 기본 6)
     - 인라인 SVG `viewBox` 안에 시각마다 막대 하나를 그리고, 9개 범주를 비율만큼 세로로 쌓는다
     - 막대 아래에 `+entered/−exited`를 작은 글씨로, 위에 범례를 둔다. 막대마다 `<title>`로 툴팁을 단다
     - 점이 0개면 "수집 데이터가 쌓이는 중"을 표시한다
  6. 브리핑 카드: "지금 요약(now)"과 "기준선 비교(daily)" 버튼. 요청 중에는 버튼을 비활성화하고
     "생성 중…"을 표시한다. 결과 마크다운은 간단한 변환기로 렌더링한다. 지원 범위는 `##`, `-`,
     `**`, 문단이고, 텍스트는 escape한 다음 변환한다. 에러가 나면 detail을 표시한다.
- **숫자 축약**: 1만 미만은 콤마, 1억 미만은 `x.x만`, 그 이상은 `x.x억`
- **갱신**: 처음 열 때 trending과 trends를 불러온다. 이후 60초마다 자동으로 다시 불러오고
  (`document.hidden`이면 건너뛴다), 새로고침 버튼으로도 다시 불러온다.
- **XSS**: API에서 받은 문자열(제목, 채널)은 모두 `textContent`로 넣거나 escape해서 넣는다.
- 모든 요청은 **상대 경로**(`api/trending`)다. code-server 프록시(`/proxy/8000/`) 아래에서도 동작해야 하기 때문이다.
- `fetch`에는 타임아웃(목록 15초, 브리핑 60초)을 건다. 요청이 진행 중일 때 필터가 바뀌면, 끝난 뒤 다시 불러온다.
  탭이 다시 보이게 되면 즉시 갱신한다.
- 시계열 색은 고정 8색 + 기타 회색이다. 라이트/다크 각각 `validate_palette.js`를 통과했다. 라이트에서 3색이
  배경 대비 3:1 미만이라 범례, 막대별 툴팁, "표로 보기"를 함께 제공한다.
- 화면이 데이터를 위해 호출하는 곳은 같은 출처의 `/api/*`뿐이다. 썸네일 이미지만은 `i.ytimg.com`에서 직접 불러온다. API 호출이 아니고 키도 필요 없다.

## 11. 앱 조립 (`main.py`)

```python
@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    app.state.settings = settings
    app.state.store = MemoryStore(settings.ring_size)
    app.state.categories = DEFAULT_CATEGORY_NAMES
    yt = YouTubeClient(settings)
    app.state.brief = BriefService(BedrockClient(settings))
    task = asyncio.create_task(collect_loop(app, yt, settings))
    yield
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
    await yt.aclose(); await app.state.brief.aclose()

app = FastAPI(title="Trend Radar", lifespan=lifespan)
app.include_router(router)                                   # /healthz, /api/*
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True))  # 반드시 라우터 다음에 마운트
```
- `FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"`
- 로깅은 `logging.basicConfig(level=INFO)`로 설정하고, `httpx`와 `httpcore` 로거만 WARNING으로 올린다.

## 12. 구현 순서와 검증

| Phase | 내용 | 검증 |
|---|---|---|
| 1 | config, models, store, collector, derive(rank_deltas, summary_stats), `/healthz`, `/api/trending` | `uv run uvicorn …` → `curl :8000/healthz`, `curl :8000/api/trending \| jq '.items\|length'`가 30. 60초 뒤 `jq '[.items[].delta]'`에 null이 아닌 값이 있는지. `?category=10`이 필터되는지, `?category=999`가 400인지 |
| 2 | index.html: 테마, 헤더, 통계 바, 칩, 카드 그리드, 배지 | 브라우저: 칩 필터, ▲▼/NEW 배지, 새 탭 링크, 테마 토글을 바꾸고 새로고침해도 유지되는지 |
| 3 | `trend_series`, `/api/trends`, SVG 차트 | `curl ':8000/api/trends?hours=1' \| jq '.points\|length'`가 늘어나는지, 막대 높이 합이 100%인지 |
| 4 | bedrock 클라이언트, BriefService, `/api/brief`, 브리핑 카드, 마감 정리 | `curl -X POST ':8000/api/brief?mode=now'`를 두 번 호출해 두 번째가 `cached:true`인지. daily 응답에 `baseline_at`이 있는지. 키 노출 검사(아래) |

**키 노출 검사 (Phase 4 마지막)**: 서버 로그를 파일로 남기고, 모든 엔드포인트 응답과 함께
`YT_API_KEY` 값(그리고 Bearer 토큰이 있다면 그 값)이 들어 있는지 `grep -F`로 확인한다.
결과가 0건이어야 한다. 키 값을 출력하지 않도록 `grep -c`로 개수만 센다.

## 13. 범위 밖 (YAGNI)

- DynamoDB 구현, CDK 배포, 인증·권한, 여러 국가(regionCode) 지원, 서버 재시작 후에도 데이터가 남는 영속화, 자동화 테스트
- 링버퍼 48개라서 워크샵 모드에서는 "최근 N시간"이 실제로는 최대 48분이다. 원작 주기(3600초)에서는 48시간이 된다.

## 14. 구현 중 확정된 사항 (2026-10-13)

| 항목 | 결정 | 이유 |
|---|---|---|
| 수집 스케줄 | APScheduler 대신 `lifespan`의 `asyncio` 태스크 | 의존성을 늘리지 않고 프로세스 하나로 끝낸다 |
| 라우트 | 모두 `async def` | `def` 라우트는 스레드풀에서 돌아 락 없는 스토어와 경쟁한다. 실제로 기준선이 최신과 같아져 delta가 전부 0이 되는 경로가 있었다 |
| 아이템 격리 | dict가 아닌 아이템도 건너뛴다 | 예외 처리 블록의 `raw.get`이 다시 예외를 내서 사이클 전체를 잃었다 |
| `/api/trending` | `categories` 맵 추가 | 칩 8개에 한국어 이름을 붙이기 위해서다 |
| Bedrock 실패 코드 | 타임아웃 504, 그 외 502 | 워크샵 가이드를 따랐다 |
| 브리핑 분량 | 600자 이내 | maxTokens 800 안에서 세 섹션을 완결하기 위해서다 |
| 로그 마스킹 | Bedrock 에러 본문의 자격 증명을 지운다 | 403 본문에 세션 토큰이 섞인다 |
| 동시 브리핑 | 진행 중인 호출 공유 | 장애 시 대기 시간이 N×30초가 되는 것을 막는다 |

## 15. 추가 기능: 영상별 AI 태그와 hover 미리보기 (2026-10-13)

예시 사이트(youtube-trend.whchoi.net)의 TOP10 hover 동작을 따른다.

- **태거** (`app/llm/tagger.py`): 수집이 끝날 때마다 `Tagger.schedule()`가 태그 없는 영상만 골라 백그라운드 Task 하나로 태깅한다. 이미 돌고 있으면 다음 사이클에 맡긴다.
  - 모델: `TAG_MODEL`(기본 `global.anthropic.claude-haiku-4-5-20251001-v1:0`), `TAG_MAX_TOKENS` 3000, temperature 0.3
  - 입력: id, 제목, 채널, 카테고리, 설명 앞 200자(`Item.description`)
  - 출력: `{id: {comment, topics[≤2], age, vibe}}` JSON. `_clean()`이 타입과 길이를 검증하고, 요청하지 않은 id는 버린다. 실패하면 로그만 남기고 다음 사이클에 다시 시도한다.
  - comment는 제목을 반복하지 않고 "원인 + 반응"으로 쓴다(예: "컴백 MV 공개로 팬덤 스트리밍 집중").
  - 링버퍼에 남은 스냅샷에 없는 영상의 태그는 지운다.
- **API**: `/api/trending` 아이템에 `description`(160자)과 `tags`(없으면 null)를 더한다.
- **화면**:
  - hover가 되는 기기에서 카드에 커서를 올리면 `scale(1.06)`과 그림자. `prefers-reduced-motion`이면 확대하지 않는다.
  - 0.25초 뒤 미리보기 카드(`position: fixed`, 카드 오른쪽 → 왼쪽 → 위 순서로 화면 안에 들어가는 자리). 내용은 썸네일, 제목, 순위·채널·카테고리·조회·좋아요, 설명 2줄, `AI` 칩과 한 줄 이유, 태그 칩. 태그가 아직 없으면 "AI 분석 중…".
  - 키보드 focus에도 같은 미리보기를 띄운다. 스크롤하거나 그리드를 다시 그리면 숨긴다.
  - hover가 없는 터치 기기에서는 카드 본문에 `AI` 칩과 한 줄 이유를 보여 준다.
- **비용**: 첫 수집 때 Haiku 1회. 그 뒤로는 새 진입이 있을 때만 호출한다. YouTube 쿼터는 늘지 않는다.
