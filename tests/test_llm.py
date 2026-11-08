"""Тесты клиента: разбор трёх протоколов, повторы, битый JSON.

Ни один тест не ходит в сеть: транспорт подменяется функцией,
которая отдаёт заранее заготовленный SSE-поток.
"""

from __future__ import annotations

import json

import pytest

from sci_harness.llm import Client, LLMError, RateLimited, Usage, estimate_tokens, parse_args


def sse(events: list[dict]) -> list[bytes]:
    return [f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode() for e in events] + [b"data: [DONE]\n\n"]


def transport(events: list[dict]):
    def send(url, headers, body, timeout):
        yield from sse(events)

    return send


def flaky(fail_times: int, events: list[dict]):
    state = {"calls": 0}

    def send(url, headers, body, timeout):
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise RateLimited("HTTP 429: slow down")
        yield from sse(events)

    return send, state


def test_openai_collects_text_and_split_tool_call():
    events = [
        {"choices": [{"delta": {"content": "Сейчас "}}]},
        {"choices": [{"delta": {"content": "посмотрю."}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "read_file", "arguments": '{"pa'},
                            }
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "a.txt"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    ]
    client = Client(provider="openai", model="gpt-5-mini", api_key="k", transport=transport(events))
    reply = client.chat([{"role": "user", "content": "прочитай a.txt"}])
    assert reply.text == "Сейчас посмотрю."
    assert [c.name for c in reply.tool_calls] == ["read_file"]
    assert reply.tool_calls[0].args == {"path": "a.txt"}
    assert reply.usage.input_tokens == 100
    assert reply.usage.cost > 0  # gpt-5-mini есть в таблице цен


def test_streaming_callback_gets_pieces():
    pieces: list[str] = []
    events = [{"choices": [{"delta": {"content": "раз"}}]}, {"choices": [{"delta": {"content": "два"}}]}]
    client = Client(provider="openai", model="m", api_key="k", transport=transport(events))
    client.chat([{"role": "user", "content": "x"}], on_text=pieces.append)
    assert pieces == ["раз", "два"]


def test_anthropic_tool_use_and_system_split():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 50}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Так."}},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tu_1", "name": "cite_check"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"quote": "6 Вт"}'},
        },
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}},
    ]
    captured: dict = {}

    def send(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body)
        yield from sse(events)

    client = Client(provider="anthropic", model="claude-sonnet-4-5", api_key="k", transport=send)
    reply = client.chat([{"role": "user", "content": "найди цифру"}], system="ты научный ассистент")
    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "k"
    assert captured["body"]["system"] == "ты научный ассистент"
    assert reply.tool_calls[0].args == {"quote": "6 Вт"}
    assert reply.usage.input_tokens == 50 and reply.usage.output_tokens == 30


def test_ollama_arguments_as_object():
    events = [
        {"message": {"content": ""}, "done": False},
        {
            "message": {"tool_calls": [{"function": {"name": "list_dir", "arguments": {"path": "."}}}]},
            "done": False,
        },
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
        },
    ]
    client = Client(provider="ollama", model="qwen3:8b", transport=transport(events))
    reply = client.chat([{"role": "user", "content": "x"}])
    assert reply.tool_calls[0].args == {"path": "."}
    assert reply.finish_reason == "stop"


def test_retries_on_429_then_succeeds():
    events = [{"choices": [{"delta": {"content": "ok"}}]}]
    send, state = flaky(2, events)
    client = Client(provider="openai", model="m", api_key="k", transport=send, backoff=0.0)
    reply = client.chat([{"role": "user", "content": "x"}])
    assert reply.text == "ok"
    assert state["calls"] == 3


def test_gives_up_after_retries():
    send, _ = flaky(10, [])
    client = Client(provider="openai", model="m", api_key="k", transport=send, backoff=0.0, max_retries=2)
    with pytest.raises(RateLimited):
        client.chat([{"role": "user", "content": "x"}])


def test_non_retryable_error_raised_immediately():
    def send(url, headers, body, timeout):
        raise LLMError("HTTP 401: bad key")
        yield b""  # pragma: no cover

    client = Client(provider="openai", model="m", api_key="k", transport=send, max_retries=3)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])


def test_parse_args_marks_truncated_json():
    assert parse_args('{"a": 1}') == {"a": 1}
    broken = parse_args('{"path": "гл')
    assert "__invalid_json__" in broken
    assert broken["__error__"]  # текст ошибки — чтобы модель поняла, что не так


def test_cyrillic_token_estimate_is_above_naive():
    text = "Число ошибок снижается" * 10
    naive = len(text) // 4
    assert estimate_tokens(text) > naive * 1.5


def test_usage_price_known_flag() -> None:
    """Модель вне PRICES: cost=0, но priced=False — иначе «$0.0000» врёт."""
    events: list[dict[str, object]] = [
        {"choices": [{"delta": {"content": "ok"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
    ]

    known = Client(provider="openai", model="gpt-5", api_key="k", transport=transport(events)).chat(
        [{"role": "user", "content": "hi"}]
    )
    assert known.usage.priced is True
    assert known.usage.cost > 0

    unknown = Client(provider="openai", model="gemma4:31b", api_key="k", transport=transport(events)).chat(
        [{"role": "user", "content": "hi"}]
    )
    assert unknown.usage.cost == 0.0
    assert unknown.usage.priced is False


def test_usage_add_keeps_priced() -> None:
    total = Usage()
    total.add(Usage(input_tokens=10, cost=0.5, priced=True))
    total.add(Usage(input_tokens=5))
    assert total.priced is True
    assert total.input_tokens == 15
