# Trend Radar

유튜브 한국 급상승 30개를 주기적으로 수집해 스냅샷으로 쌓고, 순위 변동(▲▼)과 NEW 진입, 이탈, 카테고리 점유율의 흐름을 읽어 Bedrock(Claude) 한국어 브리핑으로 해설하는 서비스입니다. 배포 없이 로컬에서 완결됩니다.

```
[수집 루프] 60초마다 videos.list(mostPopular, KR, 30) → 정규화(부분 실패 격리) → 링버퍼 48개(같은 시각 버킷은 한 번만)
[조회 경로] 브라우저 → /api/trending, /api/trends → 저장된 스냅샷 + 기준선 비교 (YouTube를 직접 치지 않음)
[브리핑]   버튼 → /api/brief → Bedrock Converse REST(httpx) → 시각 버킷 캐시
```

## 실행

```bash
cd backend
uv sync
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `~/capstone/.env`에 `YT_API_KEY`가 있어야 합니다. Bedrock은 `AWS_BEARER_TOKEN_BEDROCK`이 있으면 Bearer로, 없으면 AWS 자격 증명(SigV4)으로 인증합니다.
- 브라우저: `http://localhost:8000/` 또는 code-server 프록시 `https://<도메인>/proxy/8000/`

## API

| 경로 | 설명 |
|---|---|
| `GET /healthz` | 상태, 스냅샷 수, 마지막 수집 시각 |
| `GET /api/trending[?category=ID]` | 최신 30개 + rank/delta/NEW + 카테고리명 + 통계. 카테고리 8종 필터 |
| `GET /api/trends?hours=N` | 시각별 카테고리 점유율, 진입/이탈 수 (N은 1~48) |
| `POST /api/brief?mode=now\|daily` | 한국어 브리핑. 같은 시각 버킷 동안 캐시. 타임아웃이면 504, 그 밖의 실패는 502 |

## 설정 (환경변수)

| 변수 | 기본값 |
|---|---|
| `COLLECT_INTERVAL_SEC` | `60` (원작은 3600) |
| `RING_SIZE` | `48` |
| `BEDROCK_REGION` | `us-east-1` |
| `BEDROCK_MODEL` | `global.anthropic.claude-sonnet-4-6` |
| `BRIEF_MAX_TOKENS` | `800` |

설계 문서는 `docs/superpowers/specs/2026-10-13-trend-radar-design.md`에 있습니다.
