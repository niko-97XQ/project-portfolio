from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelTransportError(RuntimeError):
    pass


SUPPORTED_MODELS = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V4-Pro",
}


class JsonHttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


class UrlLibJsonTransport:
    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request = Request(
            url,
            method="POST",
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # Do not include request headers or API keys in errors.
            body = error.read().decode("utf-8", errors="replace")[:500]
            raise ModelTransportError(f"DeepSeek HTTP {error.code}: {body}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ModelTransportError(f"DeepSeek request failed: {type(error).__name__}") from error


@dataclass(frozen=True, slots=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class DeepSeekChatModel:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: int = 20,
        transport: JsonHttpTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key is required")
        if model not in SUPPORTED_MODELS:
            raise ValueError("unsupported DeepSeek model")
        self.api_key = api_key.strip()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrlLibJsonTransport()
        self.last_usage = ModelUsage()

    def generate(self, system_prompt: str, input_data: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(input_data, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.55,
            "max_tokens": 1200,
            "stream": False,
        }
        if "siliconflow.cn" in self.base_url:
            payload["enable_thinking"] = False
        else:
            payload["thinking"] = {"type": "disabled"}
        response = self.transport.post_json(
            f"{self.base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout_seconds,
        )
        self.last_usage = _usage(response)
        return _extract_json_content(response)


def _extract_json_content(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelTransportError("DeepSeek response has no choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ModelTransportError("DeepSeek choice is invalid")
    finish_reason = str(choice.get("finish_reason") or "")
    if finish_reason != "stop":
        raise ModelTransportError(f"DeepSeek response did not finish normally: {finish_reason}")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ModelTransportError("DeepSeek response message is invalid")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelTransportError("DeepSeek returned empty content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ModelTransportError("DeepSeek content is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ModelTransportError("DeepSeek JSON output must be an object")
    return parsed


def _usage(response: dict[str, Any]) -> ModelUsage:
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return ModelUsage()
    return ModelUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
    )
