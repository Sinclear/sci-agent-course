"""Клиент к модели: три протокола, потоковый разбор, повторы.

Один класс `Client` знает про OpenAI-совместимый API, Anthropic и Ollama.
Внутри — общая форма сообщений (как у OpenAI): её удобно писать руками,
а в Anthropic-формат её переводит сам клиент.

Транспорт (`send`) подменяется в тестах — поэтому сеть не нужна,
чтобы проверить разбор SSE и повторы.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TIMEOUT = 120.0
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}

# Цены за 1M токенов (вход/выход). Меняются — сверяй с провайдером.
# Если модели нет в таблице, стоимость считается нулевой: лучше ноль,
# чем выдуманное число.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0),
    "qwen3:8b": (0.0, 0.0),
    "llama3.1:8b": (0.0, 0.0),
}


class LLMError(RuntimeError):
    """Ошибка провайдера, которую бессмысленно повторять."""


class RateLimited(LLMError):
    """429/5xx: имеет смысл повторить с паузой."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    priced: bool = False  # модель есть в PRICES: иначе cost=0 значил бы «бесплатно»

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost += other.cost
        self.priced = self.priced or other.priced


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)},
                }
                for c in self.tool_calls
            ]
        return msg


Transport = Callable[[str, dict[str, str], bytes, float], Iterator[bytes]]


def _real_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> Iterator[bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            yield from resp
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        if exc.code in RETRY_STATUSES:
            raise RateLimited(f"HTTP {exc.code}: {detail}") from exc
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc


def parse_args(raw: str) -> dict[str, Any]:
    """Разбор аргументов вызова инструмента.

    Модель иногда присылает обрезанный или невалидный JSON. Молча
    подставлять {} нельзя: инструмент выполнится с пустыми аргументами
    (write_file без path — привет). Поэтому отдаём маркер, и цикл
    вернёт модели понятную ошибку.
    """
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"__invalid_json__": raw, "__error__": str(exc)}
    if not isinstance(value, dict):
        return {"__invalid_json__": raw, "__error__": "аргументы должны быть объектом"}
    return value


class Client:
    """Один клиент — один провайдер — одна модель."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff: float = 1.0,
    ) -> None:
        self.provider = provider or os.environ.get("SCI_PROVIDER", "ollama")
        self.model = model or os.environ.get("SCI_MODEL", "qwen3:8b")
        self.api_key = api_key or self._key_from_env()
        self.base_url = (base_url or os.environ.get("SCI_BASE_URL") or self._default_base()).rstrip("/")
        self.send = transport or _real_transport
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.usage = Usage()
        self.calls = 0

    def _key_from_env(self) -> str:
        for name in ("SCI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            if os.environ.get(name):
                return os.environ[name]
        return ""

    def _default_base(self) -> str:
        if self.provider == "anthropic":
            return "https://api.anthropic.com"
        if self.provider == "ollama":
            return "http://localhost:11434"
        return "https://api.openai.com"

    # --- публичный вход -------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        system: str | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Reply:
        """Запрос с разбором потока. Собирает текст, вызовы, расход."""
        payload, url, headers = self._request(messages, tools, max_tokens, temperature, system)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        lines = self._with_retries(url, headers, body)
        reply = self._parse(lines, on_text)
        self.calls += 1
        self.usage.add(reply.usage)
        return reply

    def _with_retries(self, url: str, headers: dict[str, str], body: bytes) -> Iterator[bytes]:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                yield from self.send(url, headers, body, self.timeout)
                return
            except RateLimited as exc:
                last = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff * (2**attempt))
        assert last is not None
        raise last

    # --- сборка запроса -------------------------------------------------

    def _request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
        system: str | None,
    ) -> tuple[dict[str, Any], str, dict[str, str]]:
        if self.provider == "anthropic":
            sys_text, conv = _split_system(messages, system)
            payload: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": conv,
                "stream": True,
            }
            if sys_text:
                payload["system"] = sys_text
            if tools:
                payload["tools"] = [
                    {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "input_schema": t["function"].get("parameters", {"type": "object"}),
                    }
                    for t in tools
                ]
            headers = {
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            return payload, f"{self.base_url}/v1/messages", headers

        if self.provider == "ollama":
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if tools:
                payload["tools"] = tools
            return payload, f"{self.base_url}/api/chat", {"content-type": "application/json"}

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["messages"] = [{"role": "system", "content": system}, *messages]
        if tools:
            payload["tools"] = tools
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.api_key}"}
        return payload, f"{self.base_url}/chat/completions", headers

    # --- разбор потока --------------------------------------------------

    def _parse(self, lines: Iterator[bytes], on_text: Callable[[str], None] | None) -> Reply:
        if self.provider == "anthropic":
            return self._parse_anthropic(lines, on_text)
        if self.provider == "ollama":
            return self._parse_ollama(lines, on_text)
        return self._parse_openai(lines, on_text)

    def _events(self, lines: Iterator[bytes]) -> Iterator[dict[str, Any]]:
        for line in lines:
            text = line.decode("utf-8", "replace").strip()
            if not text or text.startswith(":"):
                continue
            if text.startswith("data:"):
                text = text[5:].strip()
            if text == "[DONE]":
                return
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                continue

    def _parse_openai(self, lines: Iterator[bytes], on_text: Callable[[str], None] | None) -> Reply:
        reply = Reply()
        partial: dict[int, dict[str, Any]] = {}
        for event in self._events(lines):
            if usage := event.get("usage"):
                reply.usage.input_tokens = usage.get("prompt_tokens", 0)
                reply.usage.output_tokens = usage.get("completion_tokens", 0)
            for choice in event.get("choices") or ():
                delta = choice.get("delta") or {}
                if piece := delta.get("content"):
                    reply.text += piece
                    if on_text:
                        on_text(piece)
                for call in delta.get("tool_calls") or ():
                    slot = partial.setdefault(
                        call.get("index", 0),
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if call.get("id"):
                        slot["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    reply.finish_reason = choice["finish_reason"]
        self._finish_calls(reply, partial)
        self._price(reply)
        return reply

    def _parse_anthropic(self, lines: Iterator[bytes], on_text: Callable[[str], None] | None) -> Reply:
        reply = Reply()
        partial: dict[int, dict[str, Any]] = {}
        block_to_index: dict[int, int] = {}
        for event in self._events(lines):
            kind = event.get("type")
            if kind == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    idx = len(partial)
                    block_to_index[event.get("index", 0)] = idx
                    partial[idx] = {"id": block.get("id", ""), "name": block.get("name", ""), "arguments": ""}
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    reply.text += delta.get("text", "")
                    if on_text and delta.get("text"):
                        on_text(delta["text"])
                elif delta.get("type") == "input_json_delta":
                    slot_index = block_to_index.get(event.get("index", 0))
                    if slot_index is not None:
                        partial[slot_index]["arguments"] += delta.get("partial_json", "")
            elif kind == "message_delta":
                delta = event.get("delta") or {}
                if delta.get("stop_reason"):
                    reply.finish_reason = delta["stop_reason"]
                if usage := event.get("usage"):
                    reply.usage.output_tokens = usage.get("output_tokens", 0)
            elif kind == "message_start":
                if usage := (event.get("message") or {}).get("usage"):
                    reply.usage.input_tokens = usage.get("input_tokens", 0)
        self._finish_calls(reply, partial)
        self._price(reply)
        return reply

    def _parse_ollama(self, lines: Iterator[bytes], on_text: Callable[[str], None] | None) -> Reply:
        reply = Reply()
        partial: dict[int, dict[str, Any]] = {}
        for event in self._events(lines):
            message = event.get("message") or {}
            if piece := message.get("content"):
                reply.text += piece
                if on_text:
                    on_text(piece)
            for idx, call in enumerate(message.get("tool_calls") or ()):
                fn = call.get("function") or {}
                slot = partial.setdefault(idx, {"id": f"call_{idx}", "name": "", "arguments": ""})
                slot["name"] = fn.get("name", slot["name"])
                args = fn.get("arguments")
                slot["arguments"] += (
                    args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)
                )
            if event.get("done"):
                reply.finish_reason = event.get("done_reason", "stop")
                reply.usage.input_tokens = event.get("prompt_eval_count", 0)
                reply.usage.output_tokens = event.get("eval_count", 0)
        self._finish_calls(reply, partial)
        self._price(reply)
        return reply

    def _finish_calls(self, reply: Reply, partial: dict[int, dict[str, Any]]) -> None:
        for _, slot in sorted(partial.items()):
            if not slot["name"]:
                continue
            reply.tool_calls.append(
                ToolCall(
                    id=slot["id"] or f"call_{len(reply.tool_calls)}",
                    name=slot["name"],
                    args=parse_args(slot["arguments"]),
                )
            )

    def _price(self, reply: Reply) -> None:
        """Стоимость по таблице PRICES; модель не в таблице — cost остаётся 0, priced=False."""
        known = self.model in PRICES
        prompt, completion = PRICES.get(self.model, (0.0, 0.0))
        reply.usage.cost = (
            reply.usage.input_tokens / 1_000_000 * prompt + reply.usage.output_tokens / 1_000_000 * completion
        )
        reply.usage.priced = known


def _split_system(messages: list[dict[str, Any]], system: str | None) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic: системный промпт отдельным полем, роли только user/assistant."""
    sys_parts = [system] if system else []
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            sys_parts.append(msg.get("content", ""))
            continue
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": msg.get("content", ""),
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": parse_args(call["function"]["arguments"])
                        if isinstance(call["function"]["arguments"], str)
                        else call["function"]["arguments"],
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": role, "content": msg.get("content", "")})
    return "\n\n".join(p for p in sys_parts if p), out


def estimate_tokens(text: str) -> int:
    """Грузая оценка размера, но с поправкой на кириллицу.

    Наивное len/4 занижает русский текст почти вдвое: буква кириллицы
    в BPE-токенизаторах стоит ~2 байта и часто не слипается в пары.
    """
    cyrillic = len(re.findall(r"[а-яА-ЯёЁ]", text))
    other = max(len(text) - cyrillic, 0)
    return max(1, int(cyrillic / 2 + other / 4))
