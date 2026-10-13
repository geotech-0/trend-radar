# Trend Radar

YouTube 한국 급상승 30개를 60초마다 스냅샷으로 쌓고, 직전 스냅샷과 비교해 ▲▼/NEW/이탈과 카테고리 점유율을 보여 주며, Bedrock이 한국어 브리핑을 쓴다. FastAPI 프로세스 하나(:8000)가 API와 `frontend/index.html`을 함께 서빙한다.

- 설계 문서: `docs/superpowers/specs/2026-10-13-trend-radar-design.md` (§14에 구현 중 확정된 사항)
- CDK 파일(`bin/`, `lib/`, `cdk.json`)은 스캐폴드만 있고 스택은 비어 있다. 배포하지 않는다.

## 실행

```bash
cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 의존성: `cd backend && uv sync`
- 브라우저: code-server 프록시 `https://<code-server 도메인>/proxy/8000/` (끝의 `/` 필수). 도메인은 `$VSCODE_PROXY_URI`에 있다.
- 서버를 멈출 때는 `pkill -f "[u]vicorn app.main:app"`처럼 괄호로 감싼다. 괄호 없이 쓰면 그 문자열이 든 셸 명령도 같이 죽는다.
- 스냅샷은 메모리에만 있다. 재시작하면 0부터 다시 쌓이고, 두 번째 수집(약 1분 뒤)부터 배지가 붙는다.

## 키와 자격 증명 (가장 중요)

- `YT_API_KEY`는 `~/capstone/.env`(저장소 밖)에 있고 `config.py`가 `load_dotenv(override=False)`로 읽는다. 키를 코드, 응답, 로그 어디에도 남기지 않는다.
- YouTube 키는 **URL 쿼리**에 실린다. 그래서 다음을 지킨다.
  - `httpx`/`httpcore` 로거는 WARNING 이상만 남긴다. INFO에서 요청 URL을 찍는다.
  - httpx 예외에 `str(exc)`, `repr(exc)`, `log.exception`을 쓰지 않는다. 메시지와 traceback에 URL이 들어 있다. 로그에는 `type(e).__name__`만 남긴다.
  - 상류 응답 본문은 로그에만 남기고, 밖으로는 상태 코드만 보낸다.
- Bedrock 인증: `AWS_BEARER_TOKEN_BEDROCK`이 있으면 Bearer, 없으면 EC2 인스턴스 역할로 SigV4 서명한다. 지금은 `.env`에 Bearer가 **없고**, 인스턴스 역할을 쓴다.
- Bedrock 에러 본문은 `_redact()`를 거친 뒤 로그에 남긴다. 403 서명 불일치 본문에는 세션 토큰이 섞인다.
- 변경한 뒤 노출 검사: 키 값을 출력하지 말고 `grep -cF "$YT_API_KEY"`로 응답과 로그의 개수만 센다. 결과는 0이어야 한다.

## AWS 환경 제약

- 조직 SCP가 **ap-northeast-2의 Bedrock을 명시적으로 거부**한다. 인증 방식과 무관하다. `BEDROCK_REGION` 기본값은 `us-east-1`이다(`us-west-2`도 동작한다).
- 모델은 inference profile ID(`global.anthropic.claude-sonnet-4-6`)를 쓴다.
- Bedrock 호출은 SDK 없이 httpx REST로 한다. botocore는 SigV4 헤더를 만드는 데만 쓴다. 본문은 한 번만 직렬화해서 서명과 전송에 같은 바이트를 쓴다.

## 코드 규칙

- 스토어(`MemoryStore`)는 락이 없다. 스토어를 건드리는 코드는 모두 이벤트 루프에서 돌아야 한다. **라우트는 `async def`로만** 쓴다(`def`는 스레드풀에서 돈다).
- 스토어를 바꿀 때는 `store/base.py`의 `SnapshotStore` 계약을 지킨다. DynamoDB 대응은 그 docstring에 있다.
- `derive/`는 순수 함수만 둔다.
- 화면은 빌드 없는 단일 HTML이다. 요청은 **상대 경로**(`api/...`)로만 보낸다. API 문자열은 `textContent`로 넣는다. 브리핑 마크다운은 `md()`가 escape한 다음 허용된 태그만 만든다.
- 색은 `:root` CSS 변수로 정의하고, 다크 값은 `@media` 블록과 `[data-theme=dark]` 블록 두 곳에 둔다. 차트 카테고리 색(`--c-*`)은 칩 순서에 고정한다.
- 영상별 AI 태그(`app/llm/tagger.py`)는 수집 직후 백그라운드에서, **태그 없는 영상만** 한 번의 호출로 묶어 만든다. 영상 ID에 붙어서 재사용되고, 링버퍼에서 빠진 영상의 태그는 지운다. hover 미리보기와 터치 기기 카드 안의 한 줄 이유가 이 데이터를 쓴다.
- 태깅 모델은 `TAG_MODEL`(기본 Haiku 4.5)이다. **Haiku 5.5는 이 계정의 private marketplace 정책에 막혀 403**을 받는다(2026-10-13).
- 브리핑은 `maxTokens 800`이다. 한국어는 거의 1글자 1토큰이라, 프롬프트에 분량 상한(600자)을 둬서 세 섹션이 잘리지 않게 한다.

## 검증 (자동화 테스트 없음, curl로 확인)

```bash
curl -s localhost:8000/healthz
curl -s localhost:8000/api/trending | jq '.items | length'            # 30
curl -s "localhost:8000/api/trends?hours=1" | jq '.points | length'
curl -s -X POST "localhost:8000/api/brief?mode=now" | jq .cached        # 두 번째는 true
```

YouTube mostPopular 순위는 1분 단위로 거의 바뀌지 않는다. delta가 0이고 진입/이탈이 0인 것은 정상이다.
