# Trend Radar

지금 한국 유튜브 급상승 30개의 순위 변동(▲▼, NEW, 이탈), 카테고리 점유율 흐름, AI 브리핑을 보여 주는 정적 사이트입니다.

**👉 https://geotech-0.github.io/trend-radar/** (로그인 없이 누구나, 휴대폰에서도)

## 동작 방식

```
GitHub Actions (매일 07:00 KST + 수동 실행)
  └─ backend/app/build_site.py
       data 브랜치 상태 읽기 → YouTube videos.list(mostPopular, KR, 30) → 멱등 저장(링버퍼 48)
       → 파생(delta/NEW/이탈/점유율) → Gemini 태깅·브리핑 → site/ (HTML + data/*.json)
  └─ GitHub Pages 배포, 새 상태는 data 브랜치에 저장
브라우저 → data/*.json만 읽는다 (키는 GitHub Secrets에만 있다)
```

## 휴대폰(아이폰)에서 앱처럼 쓰기

Safari로 위 주소를 연 다음 **공유 → 홈 화면에 추가**를 누르세요. 아이콘으로 바로 열리고, 주소창 없이 전체 화면으로 보입니다.

## 갱신

- **자동:** 매일 오전 7시(KST)에 갱신합니다. GitHub 예약 실행은 붐비면 수십 분 늦을 수 있습니다.
- **수동:** 화면의 **지금 업데이트** 버튼을 누르면 GitHub Actions 화면이 열립니다. **Run workflow → Run workflow**를 누르고 1~2분 뒤에 ↻ 버튼을 누르세요. 저장소 주인만 실행할 수 있습니다.
  - 아이폰에서는 GitHub 앱(Actions → collect → Run workflow)이 가장 편합니다.
- ↻ 버튼은 이미 만들어진 최신 데이터를 다시 불러오기만 합니다.

## 설정 (GitHub → Settings → Secrets and variables → Actions)

| Secret | 내용 |
|---|---|
| `YT_API_KEY` | YouTube Data API v3 키 (Google Cloud Console) |
| `GEMINI_API_KEY` | Gemini API 키 (aistudio.google.com) |

키를 바꿀 때는 새 키를 발급받아 같은 이름으로 다시 등록하면 됩니다. 다음 실행부터 적용됩니다.

선택 환경변수(워크플로 `env`에 추가): `GEMINI_MODEL`(기본 `gemini-flash-latest`), `TAG_MODEL`(기본 `gemini-flash-lite-latest`), `BRIEF_EVERY_SEC`(3600), `TAG_EVERY_SEC`(1800).

### 무료 한도

- **Gemini 무료 등급은 모델당 하루 20회**입니다. 브리핑(now/daily 2회)과 태깅(1회)은 서로 다른 모델을 쓰고, 마지막 시도 시각을 기준으로 간격을 둡니다. 자동 갱신 1회와 수동 실행 몇 번으로는 한도를 넘지 않습니다.
- YouTube `videos.list`는 호출당 1유닛이라 하루 10,000유닛 한도의 1% 미만만 씁니다.
- GitHub Actions와 Pages는 public 저장소에서 무료입니다.
- GitHub는 public 저장소에 60일 동안 활동이 없으면 예약 실행을 끕니다. 워크플로가 45일마다 `.github/keepalive`를 커밋해서 이를 막습니다.

## 로컬 미리보기

```bash
cd backend && uv sync
YT_API_KEY=... GEMINI_API_KEY=... uv run python -m app.build_site --state /tmp/tr/state --out /tmp/tr/site
python3 -m http.server 8010 -d /tmp/tr/site   # http://localhost:8010/
```

`~/capstone/.env`가 있으면 키를 거기서 읽습니다.

## 설계 문서

- `docs/superpowers/specs/2026-10-13-trend-radar-pages-design.md`: 현재 구조
- `docs/superpowers/specs/2026-10-13-trend-radar-design.md`: 워크샵판(FastAPI 상주 서버)
- 디자인: 사내 Enterprise AI 디자인 시스템 토큰과 아이콘(로고는 사용하지 않음)
