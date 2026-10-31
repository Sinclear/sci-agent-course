"""Тесты цикла: параллельность, порядок, мемо, таймаут, обрезанный ответ."""

from __future__ import annotations

import json
import time

import pytest

from sci_harness.llm import Reply, ToolCall, Usage
from sci_harness.loop import Runner, cite_tool, write_run
from sci_harness.provenance import Claim, Journal
from sci_harness.tools import Tool, ToolError, Workspace, build_registry


class ScriptedClient:
    """Модель, которая отвечает по заранее заданному сценарию."""

    def __init__(self, replies: list[Reply]) -> None:
        self.replies = list(replies)
        self.usage = Usage()
        self.seen: list[list[dict]] = []
        self.systems: list[str | None] = []
        self.max_tokens_seen: list[int] = []

    def chat(self, messages, tools=None, max_tokens=2048, temperature=0.0, system=None, on_text=None):
        self.seen.append(list(messages))
        self.systems.append(system)
        self.max_tokens_seen.append(max_tokens)
        self.usage.add(Usage(input_tokens=10, output_tokens=5, cost=0.001))
        if not self.replies:
            return Reply(text="конец")
        return self.replies.pop(0)


def call(name: str, args: dict, cid: str = "c1") -> ToolCall:
    return ToolCall(id=cid, name=name, args=args)


@pytest.fixture()
def journal(tmp_path):
    return Journal(tmp_path / ".sci" / "claims.jsonl")


def test_stops_when_model_answers_without_tools(journal, tmp_path):
    client = ScriptedClient([Reply(text="ответ готов")])
    runner = Runner(client, build_registry(Workspace(tmp_path)), journal)
    result = runner.run("вопрос")
    assert result.answer == "ответ готов"
    assert result.stopped == "ответ"
    assert len(result.steps) == 1


def test_tool_results_appended_in_call_order(journal, tmp_path):
    slow = Tool(
        name="slow",
        description="спит",
        parameters={"type": "object", "properties": {}},
        fn=lambda: (time.sleep(0.2), "медленный")[1],
    )
    fast = Tool(
        name="fast",
        description="быстро",
        parameters={"type": "object", "properties": {}},
        fn=lambda: "быстрый",
    )
    registry = {"slow": slow, "fast": fast}
    client = ScriptedClient(
        [
            Reply(tool_calls=[call("slow", {}, "a"), call("fast", {}, "b")]),
            Reply(text="готово"),
        ]
    )
    Runner(client, registry, journal).run("вопрос")
    tool_messages = [m for m in client.seen[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["a", "b"]
    assert tool_messages[0]["content"] == "медленный"


def test_calls_run_in_parallel(journal):
    def sleepy(delay: float) -> str:
        time.sleep(delay)
        return f"спал {delay}"

    registry = {
        "s1": Tool("s1", "d", {"type": "object", "properties": {"delay": {"type": "number"}}}, sleepy),
    }
    client = ScriptedClient(
        [
            Reply(tool_calls=[call("s1", {"delay": 0.2}, "a"), call("s1", {"delay": 0.2}, "b")]),
            Reply(text="готово"),
        ]
    )
    started = time.monotonic()
    Runner(client, registry, journal).run("вопрос")
    assert time.monotonic() - started < 0.36  # последовательно было бы ≥ 0.4


def test_identical_call_is_not_repeated(journal):
    counter = {"n": 0}

    def count() -> str:
        counter["n"] += 1
        return f"вызов {counter['n']}"

    registry = {"count": Tool("count", "d", {"type": "object", "properties": {}}, count)}
    client = ScriptedClient(
        [
            Reply(tool_calls=[call("count", {}, "a")]),
            Reply(tool_calls=[call("count", {}, "b")]),
            Reply(text="готово"),
        ]
    )
    Runner(client, registry, journal).run("вопрос")
    assert counter["n"] == 1
    second = [m for m in client.seen[2] if m.get("tool_call_id") == "b"][0]
    assert "из кэша" in second["content"]


def test_timeout_is_reported_to_model(journal):
    registry = {
        "hang": Tool("hang", "d", {"type": "object", "properties": {}}, lambda: time.sleep(1.0) or "поздно"),
    }
    client = ScriptedClient([Reply(tool_calls=[call("hang", {}, "a")]), Reply(text="готово")])
    Runner(client, registry, journal, tool_timeout=0.05).run("вопрос")
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "таймаут" in tool_msg["content"]


def test_unknown_tool_returns_error_not_crash(journal):
    client = ScriptedClient([Reply(tool_calls=[call("nope", {}, "a")]), Reply(text="готово")])
    Runner(client, {}, journal).run("вопрос")
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "нет такого инструмента" in tool_msg["content"]


def test_write_tool_blocked_without_approval(journal, tmp_path):
    client = ScriptedClient(
        [Reply(tool_calls=[call("write_file", {"path": "a.txt", "content": "x"}, "a")]), Reply(text="ок")]
    )
    Runner(client, build_registry(Workspace(tmp_path)), journal).run("запиши")
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "требуется явное разрешение" in tool_msg["content"]
    assert not (tmp_path / "a.txt").exists()


def test_truncated_reply_retried_with_bigger_limit(journal, tmp_path):
    broken = Reply(
        text="",
        tool_calls=[
            ToolCall(id="c1", name="read_file", args={"__invalid_json__": '{"path": "a', "__error__": "x"})
        ],
        finish_reason="length",
    )
    client = ScriptedClient([broken, Reply(text="исправился")])
    Runner(client, build_registry(Workspace(tmp_path)), journal).run("вопрос")
    assert client.max_tokens_seen[:2] == [2048, 4096]
    assert client.seen[0] == client.seen[1]  # повтор идёт с теми же сообщениями


def test_bad_json_args_are_reported_without_execution(journal, tmp_path):
    bad = Reply(
        tool_calls=[
            ToolCall(
                id="c1", name="read_file", args={"__invalid_json__": '{"path": "a', "__error__": "Expecting"}
            )
        ]
    )
    client = ScriptedClient([bad, Reply(text="готово")])
    Runner(client, build_registry(Workspace(tmp_path)), journal).run("вопрос")
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "не разобрались как JSON" in tool_msg["content"]


def test_cite_tool_records_verified_claim(journal, tmp_path):
    sources = {"tab2.txt": "В простое процессор потребляет 6 Вт, под нагрузкой 65 Вт."}
    registry = {"cite_check": cite_tool(journal, sources)}
    client = ScriptedClient(
        [
            Reply(
                tool_calls=[
                    call(
                        "cite_check",
                        {"text": "6 Вт в простое", "source": "tab2.txt", "quote": "потребляет 6 Вт"},
                        "a",
                    )
                ]
            ),
            Reply(text="готово"),
        ]
    )
    Runner(client, registry, journal).run("проверь")
    assert journal.stats()["verified"] == 1


def test_cite_tool_rejects_altered_number(journal):
    sources = {"tab2.txt": "под нагрузкой 65 Вт"}
    registry = {"cite_check": cite_tool(journal, sources)}
    client = ScriptedClient(
        [
            Reply(
                tool_calls=[
                    call(
                        "cite_check",
                        {"text": "85 Вт", "source": "tab2.txt", "quote": "под нагрузкой 85 Вт"},
                        "a",
                    )
                ]
            ),
            Reply(text="готово"),
        ]
    )
    Runner(client, registry, journal).run("проверь")
    assert journal.stats()["verified"] == 0
    assert journal.stats()["unsourced_share"] == 1.0


def test_pinned_claims_injected_into_system_prompt(journal, tmp_path):
    journal.add(Claim(text="6 Вт", source="s", verified=True))
    client = ScriptedClient([Reply(text="ок")])
    Runner(client, {}, journal, system="база").run("вопрос")
    assert client.systems[0] is not None
    assert "Проверенные факты" in client.systems[0]
    assert "6 Вт" in client.systems[0]


def test_write_run_trace_has_calls(journal, tmp_path):
    client = ScriptedClient([Reply(tool_calls=[call("list_dir", {"path": "."}, "a")]), Reply(text="готово")])
    result = Runner(client, build_registry(Workspace(tmp_path)), journal).run("вопрос")
    path = write_run(tmp_path / "runs" / "trace.json", result)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["steps"][0]["calls"][0][0] == "list_dir"
    assert "шагов" in data["summary"]


def test_tool_error_from_tool_becomes_observation(journal, tmp_path):
    registry = {
        "boom": Tool(
            "boom",
            "d",
            {"type": "object", "properties": {}},
            lambda: (_ for _ in ()).throw(ToolError("нет доступа")),
        )
    }
    client = ScriptedClient([Reply(tool_calls=[call("boom", {}, "a")]), Reply(text="готово")])
    Runner(client, registry, journal).run("вопрос")
    tool_msg = [m for m in client.seen[1] if m["role"] == "tool"][0]
    assert "нет доступа" in tool_msg["content"]
