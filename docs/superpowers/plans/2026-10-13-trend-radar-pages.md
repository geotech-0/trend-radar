# Trend Radar 영구 공개판 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실습 환경이 만료돼도 계속 갱신되는 Trend Radar를 GitHub Pages 고정 URL로 공개하고, 휴대폰 친화적인 디자인 시스템 화면으로 바꾼다.

**Architecture:** GitHub Actions가 30분마다 `app.build_site`를 실행한다. 이 스크립트는 `data` 브랜치의 상태를 읽어 수집, 파생, Gemini 태깅/브리핑을 수행한 뒤 정적 `site/`(HTML + `data/*.json`)를 Pages에 배포한다. 워크샵판의 수집기, 스토어, 파생, 태거 로직은 재사용하고, LLM 전송부만 Bedrock에서 Gemini REST로 바꾼다. FastAPI 상주 서버는 정리한다.

**Tech Stack:** Python 3.12 + httpx + python-dotenv (uv), Gemini `generateContent` REST, 빌드 없는 단일 HTML + 인라인 SVG, GitHub Actions(`actions/checkout@v4`, `astral-sh/setup-uv@v6`, `actions/upload-pages-artifact@v3`, `actions/deploy-pages@v4`), gh CLI.

**Spec:** `docs/superpowers/specs/2026-10-13-trend-radar-pages-design.md`

## Global Constraints

- 자동화 테스트 스위트는 만들지 않는다(워크샵 제약 유지). 각 Step의 검증은 명령 실행과 Expected 비교로 한다.
- 키(`YT_API_KEY`, `GEMINI_API_KEY`)는 환경변수/Secrets로만 받는다. 코드, 사이트 파일, 상태 파일, 로그에 남기지 않는다. 검사할 때는 `grep -cF`로 개수만 센다.
- Gemini 키는 `x-goog-api-key` 헤더로만 보낸다. URL 쿼리에 넣지 않는다.
- httpx 예외에 `str(e)`/`log.exception`을 쓰지 않는다. `type(e).__name__`만 남긴다.
- 수집 주기는 1800초, 링버퍼는 48개(24시간), daily 기준선은 24시간 전이다.
- 화면의 색은 `--color-*` 토큰(spec §6.1 = 디자인 문서 §14)만 쓴다. 글자 크기는 승인된 토큰만, 굵기는 400/600/700만 쓴다.
- 로고 자산은 쓰지 않는다. favicon과 앱 아이콘은 `trending-up-regular.svg`다.
- 화면의 모든 요청은 상대 경로(`data/*.json`)다.
- 새 저장소에는 CDK 스캐폴드(`bin/ lib/ test/ cdk.json cdk.out node_modules package*.json tsconfig.json jest.config.js .npmignore`)를 올리지 않는다.

## Review Focus

1. **첫 실행 (상태 없음):** `data` 브랜치와 state 파일이 없을 때 빈 상태로 시작한다. trending의 delta가 null이고, trends 점이 1개이며, daily 브리핑은 "기준선 없음"으로 생성돼야 한다. → Task 2 Step 3
2. **YouTube가 실패한 실행:** 기존 상태로 사이트를 다시 만들고 `meta.last_error`에 상태 코드만 남긴다. 종료 코드는 0이고 키는 없어야 한다. → Task 2 Step 5
3. **같은 버킷 재실행 (수동 실행이 겹침):** `stored=False`. Gemini 브리핑은 다시 호출하지 않는다(버킷이 같으므로). → Task 2 Step 4
4. **Gemini 실패/한도 초과(429):** 이전 태그와 브리핑을 유지하고 브리핑에 `stale:true`를 표시한다. 빌드는 성공해야 한다. → Task 2 Step 5
5. **375px 화면:** 가로 스크롤이 없고, 칩 줄이 sticky이며, 카드가 2열이고, 터치 영역이 40px 이상이다. → Task 3 Step 6(CSS 검사)

---

### Task 1: Gemini 클라이언트로 LLM 교체, 서버 코드 정리

**Files:**
- Create: `backend/app/llm/gemini.py`, `backend/app/llm/brief.py`
- Modify: `backend/app/llm/tagger.py`, `backend/app/config.py`, `backend/app/collector/youtube.py`(collect_loop 제거), `backend/pyproject.toml`
- Delete: `backend/app/main.py`, `backend/app/api/`, `backend/app/llm/bedrock.py`

**Interfaces:**
- Produces:
  - `class LLMError(Exception): status: str`
  - `class GeminiClient: __init__(settings); async generate(system: str, user: str, *, max_tokens: int, temperature: float = 0.5, json_mode: bool = False) -> str; async aclose()`
  - `brief.build_prompt(mode, latest, baseline, names) -> tuple[str, str]`
  - `brief.SYSTEM_PROMPT`, `brief.DAILY_EXTRA` (워크샵판 문구 그대로 옮김)
  - `Tagger(client: GeminiClient, max_tokens: int)`: `get(id)`, `async tag(items, keep_ids) -> int`(동기 실행, 추가 개수 반환), `load(dict)`, `dump() -> dict`
  - `Settings`: `yt_api_key, region_code, max_results, collect_interval_sec(기본 1800), ring_size(48), gemini_api_key, gemini_model(기본 "gemini-2.5-flash"), brief_max_tokens(1200), tag_max_tokens(4000)`

- [ ] **Step 1: gemini.py 작성**

```python
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_SECRET_RE = re.compile(r"(x-goog-api-key[:=]\s*|key=)\S+", re.I)

class GeminiClient:
    def __init__(self, s): self._s = s; self._http = httpx.AsyncClient(timeout=60)
    async def generate(self, system, user, *, max_tokens, temperature=0.5, json_mode=False):
        cfg = {"maxOutputTokens": max_tokens, "temperature": temperature,
               "thinkingConfig": {"thinkingBudget": 0}}
        if json_mode: cfg["responseMimeType"] = "application/json"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": cfg}
        try:
            r = await self._http.post(URL.format(model=self._s.gemini_model), json=body,
                                      headers={"x-goog-api-key": self._s.gemini_api_key})
        except httpx.TimeoutException: raise LLMError("timeout") from None
        except httpx.HTTPError as e: log.warning("gemini network: %s", type(e).__name__); raise LLMError("network") from None
        if r.status_code != 200:
            log.warning("gemini status=%s body=%s", r.status_code, _SECRET_RE.sub(r"\1***", r.text)[:500])
            raise LLMError(str(r.status_code))
        try:
            cand = r.json()["candidates"][0]
            if cand.get("finishReason") == "MAX_TOKENS": log.warning("gemini truncated at %s", max_tokens)
            text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError, ValueError): raise LLMError("parse") from None
        if not text: raise LLMError("parse")
        return text
```

- [ ] **Step 2: brief.py로 프롬프트 이동, tagger.py 클라이언트 교체**
  - `bedrock.py`의 `SYSTEM_PROMPT`, `DAILY_EXTRA`, `_mark`, `_shares_line`, `build_prompt`를 `brief.py`로 그대로 옮긴다.
  - `tagger.py`는 `BriefError`를 `LLMError`로 바꾸고, `converse(..., model=)`를 `generate(..., max_tokens=, temperature=0.3, json_mode=True)`로 바꾼다.
  - `schedule()`(백그라운드 Task)는 `async tag(items, keep_ids) -> int`로 바꾼다. 수집 후 바로 await하는 배치 실행이다.
  - `load(d)`/`dump()`로 태그를 JSON 상태와 주고받는다.

- [ ] **Step 3: 서버 코드 삭제, 의존성 정리**

Run: `cd backend && git rm -rq app/main.py app/api app/llm/bedrock.py && uv remove fastapi uvicorn botocore && uv sync`
Expected: 오류가 없고, `grep -rn "fastapi\|botocore\|bedrock" app/` 결과 0줄

- [ ] **Step 4: 실제 Gemini 호출 검증 (태그 + 인젝션)**

Run: 스크래치 스크립트로 `Tagger.tag([정상 영상 A, 지시문을 넣은 영상 B], keep_ids)` 실행
Expected: 반환값 2. A의 comment가 '사기/신고' 같은 주입 문구가 아니다. 로그에 키 문자열 0건.

- [ ] **Step 5: 실제 브리핑 호출 검증**

Run: `build_prompt("now", snap, None, names)`로 `generate(..., max_tokens=1200)` 호출
Expected: `## ` 섹션이 3개이고 잘림 경고가 없다

- [ ] **Step 6: Commit** `refactor: swap Bedrock for Gemini REST; retire FastAPI server`

---

### Task 2: 빌드 파이프라인 `app.build_site`

**Files:**
- Create: `backend/app/build_site.py`, `backend/app/state.py`
- Modify: `backend/app/models.py`(dict 변환), `backend/app/store/memory.py`(`to_list`/`from_list`)

**Interfaces:**
- Consumes: Task 1의 `GeminiClient`, `Tagger`, `brief.build_prompt`, `LLMError`
- Produces:
  - CLI: `python -m app.build_site --state DIR --out DIR [--frontend DIR]` → `DIR/snapshots.json`, `DIR/tags.json`, `DIR/brief.json`, `OUT/index.html…`, `OUT/data/{trending,trends,brief}.json`
  - `state.load_store(path, size) -> MemoryStore`, `state.save_store(store, path)`
  - JSON 모양은 spec §3.1과 같다

- [ ] **Step 1: 직렬화**
  - `Item`↔dict는 `dataclasses.asdict`와 `Item(**d)`(모르는 키는 버림)로 한다.
  - `Snapshot`↔`{"captured_at": iso, "bucket", "items": [...]}`
  - 손상된 파일은 `log.warning` 후 빈 스토어로 시작한다.

- [ ] **Step 2: build_site.main 작성** (spec §3의 순서 1~7)

```python
async def run(state_dir, out_dir, frontend_dir):
    s = get_settings(); now = datetime.now(timezone.utc)
    store = load_store(state_dir / "snapshots.json", s.ring_size)
    tags = read_json(state_dir / "tags.json", {}); briefs = read_json(state_dir / "brief.json", {})
    yt, llm = YouTubeClient(s), GeminiClient(s); last_error = None
    try:
        names = await yt.fetch_categories()
        try:
            items = await yt.fetch_trending()
            if items:
                stored = store.put(Snapshot(now, bucket_of(now, s.collect_interval_sec), tuple(items)))
                log.info("collected %d stored=%s", len(items), stored)
        except UpstreamError as e:
            last_error = f"youtube {e.status_code}"; log.warning("collect failed: %s", last_error)
        latest = store.latest()
        if latest:
            tagger = Tagger(llm, s.tag_max_tokens); tagger.load(tags)
            keep = {it.id for sn in store.since(EPOCH) for it in sn.items}
            await tagger.tag(latest.items, keep); tags = tagger.dump()
            briefs = await refresh_briefs(llm, store, latest, names, briefs, s)
        write_site(out_dir, frontend_dir, store, names, tags, briefs, last_error, s, now)
        save_state(state_dir, store, tags, briefs)
    finally:
        await yt.aclose(); await llm.aclose()
```
  - `refresh_briefs`: 모드마다 `briefs[mode]["bucket"] == latest.bucket`이면 건너뛴다. 실패하면 이전 값에 `stale=True`를 붙여 유지한다.
  - daily 기준선은 `store.nearest_before(latest.captured_at - timedelta(hours=24)) or store.oldest()`이고, `is latest`이면 None이다.
  - `write_site`: `frontend/`를 `out/`에 복사(`shutil.copytree(dirs_exist_ok=True)`)하고 JSON 3개를 쓴다(`ensure_ascii=False`, separators 압축).

- [ ] **Step 3: 첫 실행(상태 없음) 검증**

Run: `cd backend && rm -rf /tmp/tr && uv run python -m app.build_site --state /tmp/tr/state --out /tmp/tr/site`
Expected: 종료 0, `jq '.items|length' /tmp/tr/site/data/trending.json`가 30, `.baseline_at`이 null, `jq '.points|length' trends.json`가 1, `jq -r '.daily.text' brief.json`에 `## `가 3개, 태그가 null인 아이템 0개

- [ ] **Step 4: 같은 버킷 재실행 검증**

Run: 같은 명령을 바로 다시 실행
Expected: 로그에 `stored=False`, Gemini 브리핑 호출 0회(로그에 brief 생성 줄 없음), 사이트 파일 정상

- [ ] **Step 5: 실패 격리 검증**

Run: `YT_API_KEY=invalid uv run python -m app.build_site --state /tmp/tr/state --out /tmp/tr/site`, 그리고 `GEMINI_API_KEY=invalid`로 별도 실행
Expected: 둘 다 종료 0. 첫 번째는 `meta.last_error == "youtube 400"`(또는 403)이고 items는 이전 30개 그대로다. 두 번째는 브리핑/태그가 이전 값 그대로다. 두 경우 모두 사이트와 state에서 실제 키 grep 0건.

- [ ] **Step 6: Commit** `feat: static site builder with persisted snapshot state`

---

### Task 3: 디자인 시스템 + 휴대폰 화면 (`frontend/`)

**Files:**
- Modify: `frontend/index.html`(전면 개편)
- Create: `frontend/manifest.webmanifest`, `frontend/icons/icon.svg`, `frontend/icons/icon-192.png`, `icon-512.png`, `apple-touch-icon.png`(PNG는 만들 수 있을 때만)

**Interfaces:**
- Consumes: Task 2의 `data/trending.json`, `data/trends.json`, `data/brief.json`(spec §3.1)

- [ ] **Step 1: 토큰과 기본 골격**
  - 디자인 문서 §14의 `:root`와 `[data-theme=dark]` 블록을 그대로 넣는다. 다크는 `@media (prefers-color-scheme: dark){:root:not([data-theme=light]){…}}`에도 똑같이 둔다.
  - Pretendard는 `<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">`로 불러온다.
  - 글자 크기는 `--font-*` 토큰으로 정의한다(Heading2 20/700, Heading3 18/700, Body3 14/400, Label1 13/600, Label2 12/600, Caption1 11/400, Title3 24/700, Heading1 22/700).

- [ ] **Step 2: 아이콘**
  - `sun, moon, refresh(없으면 arrow 계열 순환 아이콘), chart-bar-vertical-01, trending-up, trending-down, heart-01, show, external-link`의 regular SVG에서 `<path>`만 뽑는다.
  - JS 상수 `ICONS = {name: '<path …/>'}`로 인라인하고, `stroke="#1C1C1C"`는 `currentColor`로 바꾼다.
  - `icons/icon.svg`는 primary 색 배경의 둥근 사각형(radius 12/24 비율) 안에 흰 `trending-up`을 넣는다. 로고가 아니라 앱 아이콘 슬롯이다.

- [ ] **Step 3: 레이아웃과 컴포넌트** (spec §6.4, §7)
  - 헤더: 서비스명, 마지막 갱신(상대 시간), 아이콘 버튼 2개(새로고침, 테마). 40px 원형이고 `aria-label`과 `title`을 붙인다.
  - Alert(warning): `meta.last_error`가 있거나 `generated_at`이 1시간보다 오래됐을 때 표시
  - 통계 4칸 → 칩(sticky) → Skeleton 12장 → 카드 그리드 → 점유율 차트(6/12/24시간) → 브리핑(탭 2개, 미리 생성된 텍스트, stale이면 caption 표시)
  - 카드 hover `scale(1.03)`과 `--shadow-level-3`, 미리보기 popover는 `--shadow-level-4`. 터치 기기에서는 AI 한 줄을 카드 안에 표시한다.
  - 필터는 화면에서 처리한다(`items.filter(i => i.category_id === sel)`).
  - XS(<600px): 여백 20px, 카드 `minmax(150px,1fr)`, 칩 높이 40px, 통계 2×2, 차트 `overflow-x:auto`이고 막대를 탭하면 툴팁

- [ ] **Step 4: 차트 색 검증**

Run: `node <dataviz>/scripts/validate_palette.js "<8 hex>" --mode light --surface "#ffffff"`와 `--mode dark --surface "#1d1f24"`
Expected: 두 모드 모두 FAIL이 없다(WARN이면 범례, 툴팁, 표로 보완). 결과를 spec §6.5에 기록한다.

- [ ] **Step 5: manifest + PNG 아이콘**
  - manifest: `{"name":"Trend Radar — 유튜브 급상승","short_name":"트렌드 레이더","start_url":".","scope":".","display":"standalone","background_color":"#EEF1F5","theme_color":"#0F6FFF","icons":[svg any, png 192, png 512]}`
  - PNG: `python3 -c "import cairosvg"` 또는 `rsvg-convert`가 있으면 생성한다. 없으면 SVG만 쓰고 README에 적는다.

- [ ] **Step 6: 정적 검증**

Run: Task 2 결과물에 새 frontend를 복사하고 `python3 -m http.server 8010 -d /tmp/tr/site &` 실행 → `curl -s -o /dev/null -w "%{http_code}" localhost:8010/ data/trending.json manifest.webmanifest icons/icon.svg`, 인라인 JS `node --check`, `grep -nE "#[0-9a-fA-F]{3,6}" index.html | grep -v -- "--color-\|--c-"`(토큰 정의 밖의 hex)
Expected: 모두 200, JS 문법 OK, 토큰 정의 밖의 hex 0줄. XS CSS에 `minmax(150px`, sticky 칩, `min-height: 40px`이 있다.

- [ ] **Step 7: Commit** `feat: design-system UI, mobile layout, PWA manifest`

---

### Task 4: 새 저장소, 워크플로, 첫 배포

**Files:**
- Create: `.github/workflows/collect.yml`, `.github/keepalive`(빈 파일), 새 `.gitignore`
- 새 작업 디렉터리: `~/trend-radar`(capstone-4에서 `backend/ frontend/ docs/ CLAUDE.md README.md`만 복사, git 기록은 새로 시작)

**Interfaces:**
- Consumes: Task 2 CLI, Task 3 frontend
- Produces: `https://<login>.github.io/trend-radar/`

- [ ] **Step 1: collect.yml 작성**

```yaml
name: collect
on:
  schedule: [{cron: "*/30 * * * *"}]
  workflow_dispatch:
  push: {branches: [main], paths: ["backend/**", "frontend/**", ".github/workflows/**"]}
concurrency: {group: collect, cancel-in-progress: false}
permissions: {contents: write, pages: write, id-token: write}
jobs:
  build:
    runs-on: ubuntu-latest
    environment: {name: github-pages, url: "${{ steps.deploy.outputs.page_url }}"}
    steps:
      - uses: actions/checkout@v4
      - name: Fetch state
        run: git fetch origin data:data 2>/dev/null && git worktree add state-src data || mkdir -p state-src/state
      - uses: astral-sh/setup-uv@v6
      - name: Build
        working-directory: backend
        env: {YT_API_KEY: "${{ secrets.YT_API_KEY }}", GEMINI_API_KEY: "${{ secrets.GEMINI_API_KEY }}"}
        run: uv run --python 3.12 python -m app.build_site --state ../state-src/state --out ../site --frontend ../frontend
      - name: Secret guard
        env: {YT: "${{ secrets.YT_API_KEY }}", GM: "${{ secrets.GEMINI_API_KEY }}"}
        run: |
          for k in "$YT" "$GM"; do [ -n "$k" ] && ! grep -rqF -- "$k" site state-src/state || { [ -z "$k" ] || exit 1; }; done
      - name: Save state
        run: |
          cd state-src && git checkout --orphan tmp-state && git add -A state
          git -c user.name=trend-bot -c user.email=bot@users.noreply.github.com commit -qm "state $(date -u +%FT%TZ)"
          git push -f origin HEAD:data
      - name: Keepalive
        run: |
          if [ $(( $(date +%s) - $(git log -1 --format=%ct) )) -gt $((45*86400)) ]; then
            date -u +%F > .github/keepalive && git add .github/keepalive
            git -c user.name=trend-bot -c user.email=bot@users.noreply.github.com commit -qm "chore: keepalive" && git push
          fi
      - uses: actions/upload-pages-artifact@v3
        with: {path: site}
      - id: deploy
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: 새 저장소 디렉터리 구성 + 로컬 빌드 재확인**

Run: `rsync`로 필요한 것만 `~/trend-radar`에 복사 → `git init -b main` → `cd backend && uv run python -m app.build_site --state /tmp/tr2/state --out /tmp/tr2/site`
Expected: 종료 0. `git ls-files`에 CDK 파일, `.env`, `uv` 캐시가 없다.

- [ ] **Step 3: GitHub 저장소 생성, push, Secrets, Pages**

Run:
```bash
gh repo create trend-radar --public --source ~/trend-radar --push
set -a; . ~/capstone/.env; set +a
printf %s "$YT_API_KEY" | gh secret set YT_API_KEY -R <login>/trend-radar
printf %s "$GEMINI_API_KEY" | gh secret set GEMINI_API_KEY -R <login>/trend-radar
gh api -X POST repos/<login>/trend-radar/pages -f build_type=workflow
```
Expected: 저장소 URL 출력, `gh secret list`에 2개, pages API 201(이미 있으면 409이고 무시)

- [ ] **Step 4: 첫 실행과 배포 확인**

Run: `gh workflow run collect.yml && gh run watch --exit-status` → `curl -s -o /dev/null -w "%{http_code}" https://<login>.github.io/trend-radar/` → `curl -s .../data/trending.json | jq '.items|length'`
Expected: run 성공, 200, 30. 배포된 사이트 파일에서 키 `grep -cF`가 0. `data` 브랜치 존재.

- [ ] **Step 5: Commit/push** (워크플로가 push 트리거로 한 번 더 돌 수 있다. 정상이다.)

---

### Task 5: 문서와 마무리

**Files:**
- Modify: `README.md`, `CLAUDE.md`(새 저장소 기준), 메모리

- [ ] **Step 1: README**: 공개 URL, 구조(Actions → Pages), 로컬 미리보기 명령, Secrets 2개, Gemini 무료 한도, keepalive 설명, 키 재발급 방법
- [ ] **Step 2: CLAUDE.md**: FastAPI/Bedrock 관련 서술을 제거하고, Gemini 헤더 키 규칙, state 브랜치, 디자인 토큰 규칙, 로고 미사용 지시를 적는다
- [ ] **Step 3: Commit/push**, 두 번째 예약 실행(30분 뒤) 이후 delta가 채워지는지 `gh run list`와 `jq`로 확인한다
