"""상류(Google API) 에러 응답을 로그용 한 줄로 줄인다.

public 저장소의 Actions 로그는 누구나 읽으므로 본문을 그대로 남기지 않는다.
Google 에러 본문에는 GCP 프로젝트 번호 같은 계정 정보가 들어갈 수 있다. 상태 문자열과 reason 코드만 남긴다.
"""
import httpx


def error_summary(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
    except ValueError:
        return "non-json body"
    reasons = sorted({d.get("reason") for d in err.get("details", []) if isinstance(d, dict) and d.get("reason")}
                     | {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict) and e.get("reason")})
    return f"{err.get('status', '?')} {','.join(reasons)}".strip()
