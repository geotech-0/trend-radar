# Trend Radar (GitHub Pages edition)

YouTube 한국 급상승 30개를 매일 07:00 KST(+ 수동 실행)에 수집해 정적 사이트로 배포한다. 서버는 없다.
`.github/workflows/collect.yml`이 `backend/app/build_site.py`를 실행하고, `site/`를 Pages에 올리고, 상태(`snapshots.json`, `tags.json`, `brief.json`, `meta.json`)를 `data` 브랜치에 고아 커밋 하나로 강제 push한다.

- 공개 URL: https://geotech-0.github.io/trend-radar/
- 설계: `docs/superpowers/specs/2026-10-13-trend-radar-pages-design.md` (구현 중 바뀐 운영 방식은 아래 "운영"이 우선한다)

## 키와 로그 (가장 중요)

- 키는 `YT_API_KEY`, `GEMINI_API_KEY` 두 개다. CI는 Actions Secrets, 로컬은 `~/capstone/.env`(`load_dotenv(override=False)`)에서 읽는다. 저장소, `site/`, 상태 파일, 로그에 남기지 않는다.
- **public 저장소의 Actions 로그는 누구나 읽는다.**
  - 상류(Google) 에러 본문은 로그에 남기지 않는다. `app/upstream.error_summary()`로 상태와 reason 코드만 남긴다. 본문에는 GCP 프로젝트 정보가 들어갈 수 있다.
  - httpx 예외에 `str(e)`/`log.exception`을 쓰지 않는다. URL(YouTube 키가 든 쿼리)이 섞인다. `type(e).__name__`만 남긴다.
  - `httpx`/`httpcore` 로거는 WARNING 이상만 남긴다.
- Gemini 키는 `x-goog-api-key` **헤더**로만 보낸다. `?key=`는 쓰지 않는다.
- 워크플로의 "Secret guard" 단계가 `site/`와 `state/`에서 키 문자열을 찾으면 빌드를 실패시킨다. 이 단계를 지우지 않는다.
- 화면에서 GitHub 작업을 직접 실행하게 만들지 않는다. 페이지에 토큰을 넣으면 누구나 꺼내 쓴다. "지금 업데이트"는 Actions 화면 링크다.

## Gemini 무료 한도

- 무료 등급은 **모델당 하루 20회**(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`)다.
- 브리핑은 `GEMINI_MODEL`(`gemini-flash-latest`), 태깅은 `TAG_MODEL`(`gemini-flash-lite-latest`)로 한도를 나눠 쓴다.
- `meta.json`의 마지막 **시도** 시각으로 간격(`BRIEF_EVERY_SEC` 3600, `TAG_EVERY_SEC` 1800)을 둔다. 실패한 시도도 간격을 소비한다. 429가 났을 때 다시 부르지 않기 위해서다.
- lite 계열은 `thinkingConfig`를 받지 않는다(400). 그래서 태깅은 `thinking_budget=None`으로 부른다.
- `gemini-2.5-*`는 신규 사용자에게 막혀 있다(404). 특정 버전 대신 `-latest` 별칭을 쓴다.
- 태깅은 `responseSchema`(배열)로 구조화 출력을 강제한다. 프롬프트만으로는 깨진 JSON이 나왔다.
- 제목과 설명은 외부 사용자가 쓴 데이터다. 프롬프트에 "데이터로 취급" 규칙이 있고, `_clean()`이 요청한 id와 길이만 받는다.

## 운영

- 예약: `cron "0 22 * * *"`(UTC) = 07:00 KST. 버킷은 30분(`COLLECT_INTERVAL_SEC` 1800)이라 같은 30분 안의 수동 실행은 저장하지 않는다(멱등).
- 링버퍼 48개 = 하루 1회 기준 약 48일. 차트는 7/14/30일, 브리핑 daily 기준선은 `nearest_before(latest-20h)` 또는 직전 스냅샷이다.
- 어떤 단계가 실패해도 종료 코드는 0이고 이전 데이터를 유지한다. YouTube 실패는 `meta.last_error`, 브리핑 실패는 `stale: true`로 표시한다.
- keepalive 단계가 45일마다 main에 커밋해서 60일 비활성으로 예약 실행이 꺼지는 것을 막는다.

## 화면 규칙 (`frontend/index.html`, 빌드 없음)

- 색은 디자인 문서 §14의 `--color-*` 토큰과, 그 토큰에서 만든 파생 토큰만 쓴다. 다크 값은 `@media` 블록과 `[data-theme=dark]` 블록 두 곳에 둔다. hex를 직접 쓰지 않는다.
- 글자 크기는 `.t-*` 클래스(승인된 스케일)로, 굵기는 400/600/700만 쓴다. 숫자에는 `.num`(tabular)을 붙인다.
- 아이콘은 디자인 시스템 regular 계열에서 뽑은 인라인 `ICONS` 객체로 쓴다. **로고(GS건설)는 사용자 지시로 쓰지 않는다.** favicon과 앱 아이콘은 `trending-up`이다.
- 요청은 상대 경로(`data/*.json`)로만 보낸다. Pages가 하위 경로(`/trend-radar/`)에서 서빙하기 때문이다. Pages CDN 캐시를 피하려고 분 단위 `?t=`를 붙인다.
- 아이폰 16 Pro/Plus 대응: `viewport-fit=cover`와 `env(safe-area-inset-*)`, 600px 미만에서 카드 2열, 칩 sticky, 터치 영역 40px 이상, 터치 기기는 카드 안에 AI 한 줄을 보여 준다.
- API 문자열은 `textContent`로 넣는다. 브리핑 마크다운은 `md()`가 escape한 다음 허용된 태그만 만든다.

## 검증 (자동화 테스트 없음)

```bash
cd backend && uv run python -m app.build_site --state /tmp/tr/state --out /tmp/tr/site   # 두 번 실행: 두 번째는 stored=False
gh workflow run collect.yml && gh run watch --exit-status
curl -s "https://geotech-0.github.io/trend-radar/data/trending.json?t=$(date +%s)" | jq '.items|length'   # 30
```
