"""Gemini generateContent를 SDK 없이 httpx REST로 호출한다.

키는 x-goog-api-key 헤더로만 보낸다. URL 쿼리(?key=)는 로그와 예외 메시지에 남을 수 있다.
실패는 상태 코드와 reason만 로그에 남긴다(본문은 public 로그에 남기지 않는다).
"""
import logging

import httpx

from app.config import Settings
from app.upstream import error_summary

log = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
class LLMError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(f"llm error: {status}")
        self.status = status


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._http = httpx.AsyncClient(timeout=60)

    async def generate(self, system: str, user: str, *, max_tokens: int,
                       temperature: float = 0.5, json_mode: bool = False,
                       response_schema: dict | None = None, model: str | None = None,
                       thinking_budget: int | None = 0) -> str:
        config: dict = {"maxOutputTokens": max_tokens, "temperature": temperature}
        if thinking_budget is not None:
            # thinking 토큰이 출력 한도를 잡아먹지 않게 끈다 (lite 계열은 이 필드를 받지 않는다)
            config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        if json_mode or response_schema:
            config["responseMimeType"] = "application/json"
        if response_schema:
            # 구조화 출력: 형식을 프롬프트가 아니라 API가 강제한다
            config["responseSchema"] = response_schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            r = await self._http.post(
                URL.format(model=model or self._s.gemini_model),
                json=body,
                headers={"x-goog-api-key": self._s.gemini_api_key},
            )
        except httpx.TimeoutException:
            raise LLMError("timeout") from None
        except httpx.HTTPError as e:
            log.warning("gemini network error: %s", type(e).__name__)
            raise LLMError("network") from None
        if r.status_code != 200:
            log.warning("gemini status=%s %s", r.status_code, error_summary(r))
            raise LLMError(str(r.status_code))
        try:
            cand = r.json()["candidates"][0]
            if cand.get("finishReason") == "MAX_TOKENS":
                log.warning("gemini output truncated at maxOutputTokens=%s", max_tokens)
            text = "".join(p.get("text", "") for p in cand["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError, ValueError):
            raise LLMError("parse") from None
        if not text:
            raise LLMError("parse")
        return text

    async def aclose(self) -> None:
        await self._http.aclose()
