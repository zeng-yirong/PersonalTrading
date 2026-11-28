"""Minimal async client for the DeepSeek-compatible chat API.

Deliberately thinner than the ``openai`` SDK: one POST, JSON mode on demand,
retry on transient failures, and exact token accounting from ``usage`` so the
multi-agent run can report what it cost.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import settings

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "calls": self.calls,
        }


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """Pull the first JSON object out of a model reply."""
    candidate = text.strip()
    fenced = _JSON_FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(candidate)):
            char = candidate[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(candidate[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else None
        start = candidate.find("{", start + 1)
    return None


@dataclass
class ChatClient:
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: float = 0.0
    temperature: Optional[float] = None   # replay sets 0.0; normal runs use settings
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        self.model = self.model or settings.agent_model or settings.deepseek_model
        self.api_key = self.api_key or settings.deepseek_api_key
        self.base_url = (self.base_url or settings.deepseek_base_url).rstrip("/")
        self.timeout = self.timeout or settings.agent_timeout

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "未配置 PERSONALTRADING_DEEPSEEK_API_KEY"
        return True, ""

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        retries: int = 3,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                (self.temperature if self.temperature is not None else settings.agent_temperature)
                if temperature is None
                else temperature
            ),
            "max_tokens": max_tokens or settings.agent_max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error = ""
        for attempt in range(1, retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(self.url, headers=headers, json=payload)
                if response.status_code >= 400:
                    detail = response.text[:300]
                    # 429/5xx are worth retrying; 4xx auth errors are not.
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                        last_error = f"HTTP {response.status_code}: {detail}"
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    raise LLMError(f"模型接口返回 {response.status_code}: {detail}")
                body = response.json()
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001 - network layer
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                raise LLMError(f"模型调用失败：{last_error}") from exc

            usage = body.get("usage") or {}
            self.usage.add(
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
            choices = body.get("choices") or []
            message = (choices[0].get("message") if choices else {}) or {}
            content = (message.get("content") or "").strip()
            if not content:
                content = (message.get("reasoning_content") or "").strip()
            if not content:
                raise LLMError("模型返回了空内容")
            return content
        raise LLMError(f"模型调用失败：{last_error}")

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        text = await self.complete(
            system, user, json_mode=True, max_tokens=max_tokens, temperature=temperature
        )
        parsed = _extract_json(text)
        if parsed is None:
            # One repair round: cheap insurance against a stray prose prefix, and
            # against reasoning models that emit their thinking before the JSON.
            repair = await self.complete(
                "把下面的内容原样转换成合法 JSON 对象，只输出 JSON，不要解释。",
                text[:6000],
                json_mode=True,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            parsed = _extract_json(repair)
        if parsed is None:
            raise LLMError(f"模型输出不是合法 JSON：{text[:200]}")
        return parsed
