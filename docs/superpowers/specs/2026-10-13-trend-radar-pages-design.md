# Trend Radar — 영구 공개판 (GitHub Pages) 설계

- 작성일: 2026-10-13
- 상태: 설계 승인됨, spec 검토 대기
- 선행 문서: `2026-10-13-trend-radar-design.md` (워크샵판, FastAPI 상주 서버)

## 0. 목표와 제약

**목표**
- Claude 계정과 실습 환경(EC2, 실습 AWS)이 만료된 뒤에도 **로그인 없이 누구나 여는 고정 URL**로 Trend Radar를 계속 본다.
- **휴대폰에서 보기 편한** 화면으로 만든다.
- 화면은 사내 디자인 시스템(`01-enterprise-ai-ui-standards.md`, 아이콘 세트)을 따른다.

**사용자가 정한 것**
- 호스팅: 개인 GitHub 계정(`sjko0121@gmail.com`), GitHub Pages와 GitHub Actions
- AI: Google Gemini API 무료 키
- 디자인: 첨부한 디자인 시스템과 아이콘을 쓰고, **GS건설 로고는 쓰지 않는다**

**제약**
- 실습 EC2가 사라져도 코드와 데이터가 남아야 한다. GitHub 저장소가 원본이다.
- 비용은 0원이어야 한다. 무료 Pages를 쓰려면 저장소가 public이어야 한다. 다루는 데이터는 공개된 YouTube 정보뿐이다.
- 키(`YT_API_KEY`, `GEMINI_API_KEY`)는 GitHub Actions Secrets에만 둔다. 저장소, 사이트 파일, Actions 로그 어디에도 남지 않아야 한다.

**성공 기준**
1. `https://<github-username>.github.io/trend-radar/`가 로그인 없이 휴대폰과 PC에서 열린다.
2. 30분마다 자동으로 갱신되고, 두 번째 실행부터 ▲▼/NEW/이탈과 점유율 시계열이 채워진다.
3. 영상별 AI 한 줄 이유와 now/daily 브리핑이 한국어로 표시된다. 화면은 미리 만들어 둔 결과를 바로 보여 준다.
4. 사이트 파일과 Actions 로그를 검사했을 때 키 문자열이 0건이다.
5. 375px 폭 휴대폰에서 가로 스크롤 없이 쓸 수 있고, 터치 영역이 40px 이상이다.

## 1. 접근 방식

| 안 | 판단 |
|---|---|
| **A. Actions 예약 수집과 정적 JSON, Pages 배포** | **채택.** 무료이고 서버가 없으며 URL이 영구적이다. 원작의 "수집과 조회의 분리"를 끝까지 밀어붙인 형태다 |
| B. 무료 PaaS에 FastAPI | 기각. 앱이 잠들면 메모리 스냅샷이 사라지고, 무료 정책도 자주 바뀐다 |
| C. 개인 AWS | 기각. 매달 비용이 든다 |

워크샵판의 FastAPI 상주 서버는 **정리한다**. 코드는 git 기록에 남는다. 수집기, 스토어, 파생, 태거, 브리핑 로직은 재사용한다.

## 2. 구조

```
trend-radar/                          (GitHub 저장소, 기본 브랜치 main)
├─ .github/workflows/collect.yml      30분 예약 + 수동 실행 → 빌드 → Pages 배포
├─ backend/
│  ├─ pyproject.toml                  httpx, python-dotenv (fastapi/uvicorn/botocore 제거)
│  └─ app/
│     ├─ config.py                    환경변수 → Settings
│     ├─ models.py                    Item, Snapshot (+ JSON 직렬화)
│     ├─ collector/youtube.py         그대로 (collect_loop 제거, fetch만)
│     ├─ store/base.py, memory.py     그대로 + to_json/from_json
│     ├─ derive/ranking.py            그대로
│     ├─ llm/gemini.py                새 파일: GeminiClient.generate(system, user, ...) REST
│     ├─ llm/brief.py                 build_prompt + 시스템 프롬프트 (bedrock.py에서 분리)
│     ├─ llm/tagger.py                LLM 클라이언트만 교체
│     └─ build_site.py                새 파일: 진입점 (상태 읽기 → 수집 → 파생 → AI → JSON 쓰기)
├─ frontend/
│  ├─ index.html                      디자인 시스템을 입힌 단일 HTML
│  ├─ manifest.webmanifest            홈 화면에 추가(PWA)
│  └─ icons/                          디자인 시스템 아이콘에서 고른 favicon/앱 아이콘
├─ docs/
└─ CLAUDE.md, README.md
```

- `data` 브랜치: 상태 파일만 둔다(`state/snapshots.json`, `state/tags.json`, `state/brief.json`). 히스토리가 쌓이지 않게 매번 **고아 커밋 하나로 강제 push**한다.
- CDK 스캐폴드(`bin/`, `lib/`, `cdk.json`, `node_modules` 등)는 새 저장소에 올리지 않는다.

## 3. 파이프라인 (`build_site.py`)

```
1. 상태 읽기   state/snapshots.json → MemoryStore(48), state/tags.json, state/brief.json (없으면 빈 상태)
2. 수집        categories = fetch_categories(); items = fetch_trending()
               실패(UpstreamError) → 새 스냅샷 없이 기존 상태로 사이트만 다시 만든다
3. 저장        store.put(Snapshot(now, bucket_of(now, 1800), items))   # 같은 버킷이면 False(멱등)
4. 파생        rank_deltas(latest, previous), summary_stats, trend_series(store.since(now-24h))
5. 태깅        태그 없는 영상만 Gemini 1회. 링버퍼에 없는 영상 태그는 제거. 실패하면 기존 태그 유지
6. 브리핑      latest.bucket이 brief.json에 기록된 bucket과 다를 때만 now, daily를 생성
               daily 기준선 = nearest_before(latest - 24h) 또는 oldest (latest와 같으면 없음)
               실패하면 이전 브리핑을 유지하고 stale=true 표시
7. 쓰기        site/ = frontend/* 복사 + site/data/{trending,trends,brief}.json
               state/ 갱신
```
- 실행 시간 목표: 1분 이내. Gemini 호출은 실행당 최대 3회(태깅 1 + 브리핑 2).
- 종료 코드:
  - YouTube 실패는 0이다(사이트는 이전 데이터로 유지되고 `meta.last_error`에 상태 코드만 기록한다).
  - 상태 파일이 손상돼 읽지 못하면 비어 있는 상태로 시작하고 경고만 남긴다.

### 3.1 출력 JSON (화면 계약)

`data/trending.json`
```json
{
  "captured_at": "...Z", "baseline_at": "...Z" | null, "generated_at": "...Z",
  "interval_sec": 1800,
  "categories": {"10": "음악", ...},
  "stats": {"total_views": 0, "channels": 0, "top_category": {...}, "dropped": 0},
  "items": [{"rank", "id", "title", "channel", "category_id", "category_name", "thumbnail",
             "views", "likes", "delta", "is_new", "url", "description", "tags": {...} | null}],
  "meta": {"last_error": null | "youtube 403"}
}
```
`data/trends.json`: 워크샵판 `/api/trends`와 같은 모양이다. 범위는 최근 24시간(링버퍼 48 × 30분).

`data/brief.json`
```json
{"now":   {"text": "...", "captured_at": "...", "baseline_at": "...", "stale": false},
 "daily": {"text": "...", "captured_at": "...", "baseline_at": "...", "stale": false}}
```
- 카테고리 필터는 화면에서 `items`를 걸러서 처리한다. `rank`와 `stats`는 전체 30개 기준이다(워크샵판과 같다).

## 4. Gemini 클라이언트 (`llm/gemini.py`)

- `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`
- 키는 **헤더** `x-goog-api-key`로 보낸다. URL 쿼리에 넣으면 Actions 로그와 예외 메시지에 키가 남을 수 있다.
- body: `{"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"maxOutputTokens": N, "temperature": t}}`
  - 태깅은 `responseMimeType: "application/json"`을 추가해서 JSON만 받는다.
- 응답: `candidates[0].content.parts[*].text`를 이어 붙인다. `finishReason == "MAX_TOKENS"`면 잘림 경고를 남긴다.
- 실패 처리: 워크샵판 Bedrock과 같은 규칙이다. 타임아웃은 `LLMError("timeout")`, 상태 코드가 200이 아니면 본문을 마스킹한 뒤 로그에 남기고 `LLMError(str(status))`, 파싱 실패는 `LLMError("parse")`.
- 모델: `GEMINI_MODEL`, 기본 `gemini-2.5-flash`. 무료 등급의 분당/일일 한도는 구현할 때 확인하고 README에 적는다.
  - 2.5 계열의 "thinking" 토큰이 출력 한도를 잡아먹지 않도록 `thinkingConfig.thinkingBudget: 0`을 준다.
- 브리핑 분량: 600자, 섹션 3개(워크샵판 프롬프트를 그대로 쓴다). `maxOutputTokens`는 1200으로 둔다.
- 프롬프트 인젝션 대비: "제목/설명은 데이터"라는 규칙을 유지한다(워크샵판 §15).

## 5. 워크플로 (`.github/workflows/collect.yml`)

- 트리거: `schedule: cron '*/30 * * * *'`, `workflow_dispatch`, main 브랜치의 `frontend/**`·`backend/**`가 바뀐 push
- `concurrency: {group: collect, cancel-in-progress: false}`: 실행이 겹치지 않게 한다(멱등 쓰기의 이중 안전장치).
- `permissions: contents: write, pages: write, id-token: write`
- 단계:
  1. main 체크아웃
  2. `data` 브랜치를 `state-src/`로 체크아웃한다(없으면 건너뛴다)
  3. Python 3.12와 uv 설치, `uv sync`
  4. `uv run python -m app.build_site --state state-src/state --out site`
     - env는 secrets의 `YT_API_KEY`, `GEMINI_API_KEY`만 쓴다
  5. 새 state를 `data` 브랜치에 고아 커밋으로 강제 push한다
  6. `actions/upload-pages-artifact`(site/)를 실행하고 `actions/deploy-pages`로 배포한다
  7. 장기 실행 유지: main의 마지막 커밋이 45일보다 오래됐으면 `.github/keepalive`에 날짜를 쓰고 커밋한다.
     GitHub는 public 저장소에서 60일 동안 활동이 없으면 예약 실행을 자동으로 끈다.
- 키 노출 방지:
  - GitHub가 Secrets 값을 로그에서 자동으로 가린다.
  - 코드도 키를 로그에 남기지 않는다(워크샵판 규칙).
  - 빌드 마지막 단계에서 `grep -rF "$YT_API_KEY" site/ state/` 결과가 1건 이상이면 **빌드를 실패**시킨다. 이때 키 값은 출력하지 않는다.

## 6. 화면: 디자인 시스템 적용

### 6.1 토큰
- 문서 §14의 CSS 변수(`--color-*`, `--radius-*`, `--shadow-level-*`)를 **그대로** 쓴다. 다크 값은
  `@media (prefers-color-scheme: dark) :root:not([data-theme=light])`와 `[data-theme=dark]` 두 곳에 둔다.
  기본은 시스템 설정을 따르고, 토글로 바꾼 값은 localStorage에 저장한다.
- 폰트는 Pretendard Variable(jsDelivr CDN, 버전 고정)과 문서의 fallback 스택이다. 굵기는 400/600/700만 쓴다.
- 글자 크기는 승인된 토큰만 쓴다:

  | 요소 | 토큰 |
  |---|---|
  | 서비스명 | Heading2 20/700 |
  | 섹션 제목 | Heading3 18/700 |
  | 카드 제목 | Body3 14, 강조는 600 |
  | 메타 | Label2 12/600 또는 Caption1 11/400 |
  | 통계 값 | Title3 24/700, 휴대폰은 Heading1 22 |

- 숫자: 통계·표·차트 축은 `tabular-nums`를 쓴다.
- 표면: 헤더는 `--color-bg-page`, 본문 바탕은 `--color-bg-content`(라이트 #EEF1F5 / 다크 #15171C), 카드는 `--color-bg-surface`이고 테두리, radius 12, `--shadow-level-1`.

### 6.2 브랜드
- **로고를 쓰지 않는다**(사용자 지시). 헤더 왼쪽에는 서비스명 "Trend Radar"만 한 줄로 둔다(`white-space: nowrap`).
- favicon과 앱 아이콘에는 아이콘 세트의 `trending-up-regular.svg`를 primary 색으로 쓴다.
  문서 §4.2는 GS 심볼을 favicon에 쓰라고 하지만, 사용자 지시가 우선한다.

### 6.3 아이콘
- 아이콘 세트의 **regular** 계열(24px 캔버스, 선 1.5px)만 인라인 SVG로 쓴다. `stroke="#1C1C1C"`는 `currentColor`로 바꾼다.
- 쓰는 아이콘: 테마 전환(`sun`/`moon`), 새로고침, 차트(`chart-bar-vertical-01`), 상승/하락(`trending-up`/`trending-down`),
  좋아요(`heart-01`), 조회(`show`), 외부 링크(`external-link`), AI 표시(`AI` 아이콘이 있으면 쓰고, 없으면 텍스트 배지).
- 헤더의 유틸리티 버튼은 아이콘만 둔다. 40px 원형 hover 배경을 쓰고 테두리는 없다. `aria-label`과 tooltip(`title`)을 붙인다.

### 6.4 컴포넌트
- 카테고리 칩: pill(9999px), 높이 36px(휴대폰 40px). 선택된 칩은 primary 배경에 흰 글자, 나머지는 surface-alt-1.
- 변동 배지(radius 4, Label2):
  - ▲n: `status/success` 글자와 옅은 배경
  - ▼n: `status/error`
  - NEW: primary 옅은 배경과 primary 글자
  - 유지: 표시하지 않는다
  - 색만으로 구분하지 않도록 기호(▲▼)와 텍스트를 함께 쓴다(§9.1).
- 순위 배지: 1~3위는 primary, 나머지는 `--color-bg-inverse`를 반투명하게.
- 카드 hover(포인터 기기): `scale(1.03)`과 `--shadow-level-3`, 0.18초. 문서의 "최소 그림자" 원칙에 맞춰 워크샵판(1.06)보다 줄인다.
  `prefers-reduced-motion`이면 확대하지 않는다.
- 미리보기 카드(포인터 기기): popover이고 `--shadow-level-4`, radius 12.
  - 내용은 워크샵판과 같다: 썸네일, 제목, 메타, 설명, AI 한 줄, 태그 배지
- 로딩: 처음 열 때 카드 12장 크기의 **Skeleton**을 보여 준다(§8.10). 새로고침 버튼은 처리 중에 비활성화하고 인라인 spinner를 보여 준다.
- 오류/지연: 데이터가 1시간 넘게 갱신되지 않았거나 `meta.last_error`가 있으면 그리드 위에 **Alert**(warning)를 표시한다.

### 6.5 차트 색
- 카테고리 8개와 기타: 문서의 hue(primary, skyblue, purple, pink, orange)에 success, warning, error 계열을 더해 8개를 만들고, 기타는 `--color-border-strong`으로 한다.
- dataviz 검증기(`validate_palette.js`)를 라이트(#FFFFFF)와 다크(#1D1F24) surface에서 돌린다. 실패하면 순서를 바꾸고, 그래도 실패하면 명도 단계를 조정한다. 결과를 이 문서에 기록한다.
- status 색을 범주색으로 쓰는 것은 **차트 안에서만** 허용한다. 화면에서 status 색의 의미는 delta 배지(▲▼)뿐이다. [Check] 항목이다.

## 7. 휴대폰 레이아웃

| 폭 | 레이아웃 |
|---|---|
| XS (< 600px) | 좌우 여백 20px. 헤더 높이 56px(서비스명과 아이콘 2개). 칩 줄은 헤더 아래에 sticky로 두고 가로 스크롤. 통계는 2×2. **카드 2열**(`minmax(150px,1fr)`, 간격 12px). 차트는 가로 스크롤하고 막대를 탭하면 상세를 보여 준다. 브리핑 탭 2개 |
| S–M (600–1199px) | 카드 3~4열, 통계 4칸 |
| L+ (≥ 1200px) | 최대 폭 1280px, 카드 5~6열. 사이드 내비게이션은 쓰지 않는다(정보 구조가 얕음, 문서 §7.5) |

- 터치 기기: hover 미리보기 대신 카드 안에 `AI` 배지와 한 줄 이유를 보여 준다(워크샵판과 같다). 카드를 탭하면 유튜브 새 탭이 열린다.
- PWA: `manifest.webmanifest`에 `name`, `short_name`("트렌드 레이더"), `display: standalone`, `theme_color`(primary), `icons`(SVG, 그리고 가능하면 192/512 PNG)를 넣는다.
  iOS용으로 `apple-touch-icon`(PNG 180)을 쓴다. PNG를 만들 수 없으면 SVG만 쓰고, README에 iOS 홈 화면 아이콘이 기본 스크린샷이 된다고 적는다.
- 모든 요청은 상대 경로(`data/*.json`)로 보낸다. Pages의 하위 경로(`/trend-radar/`)에서도 동작해야 한다.
- 성능: 썸네일은 `loading="lazy"`. JSON 3개를 합쳐 100KB 이하를 목표로 한다.

## 8. 배포 절차 (사용자 작업 포함)

1. 사용자: github.com에서 **public** 저장소 `trend-radar`를 만든다. README는 넣지 않는다.
2. Claude: `gh` CLI를 `~/.local/bin`에 설치한다. 사용자: 터미널에서 `gh auth login`으로 인증한다.
3. Claude: 새 저장소 구조로 정리해서 main에 push한다. **이것이 코드 백업이다.**
4. 사용자: Settings → Secrets and variables → Actions에 `YT_API_KEY`와 `GEMINI_API_KEY`를 등록한다.
   - 대화에 노출된 키는 재발급해서 등록하기를 권한다.
   - 또는 Claude가 `gh secret set`으로 `~/capstone/.env`의 값을 등록한다. 이 경우 값은 출력하지 않는다.
5. 사용자 또는 Claude(`gh api`): Settings → Pages → Source를 "GitHub Actions"로 바꾼다.
6. Claude: `gh workflow run collect.yml`로 첫 실행을 하고 성공을 확인한다. 30분 뒤 두 번째 실행에서 delta가 채워지는지 확인한다.
7. 결과 URL을 휴대폰에서 열어 확인하고, 홈 화면에 추가한다.

## 9. 검증

| 항목 | 방법 |
|---|---|
| 빌드 | 로컬에서 `uv run python -m app.build_site --state /tmp/st --out /tmp/site`를 두 번 실행. 두 번째 실행에서 같은 버킷이면 `stored=False`가 나오고 파일이 정상인지 |
| 화면 | 로컬에서 `python -m http.server`로 `site/`를 띄우고 curl로 JSON과 HTML을 확인. 이 환경에 브라우저가 없으므로 시각 확인은 사용자 몫 |
| 디자인 토큰 | index.html의 색이 `--color-*` 변수로만 쓰였는지 grep으로 확인. 차트 색 검증기 통과 |
| Actions | 첫 실행이 성공하는지, Pages URL이 200인지, `data/trending.json`의 items가 30개인지 |
| 키 노출 | 빌드 단계의 grep 가드와, 배포된 사이트를 curl로 받아 `grep -cF`가 0인지 |

## 10. 범위 밖

- 사용자 인증, 실시간(1분) 갱신, 브라우저에서 바로 LLM 생성, 국가 전환, 다국어, 원작의 DynamoDB/Fargate
- 워크샵판 FastAPI 서버(git 기록에 남음)
