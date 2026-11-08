"""Тесты бюджета контекста и сжатия."""

from __future__ import annotations

import pytest

from sci_harness.context import Budget, ContextOverflow, lost_facts, pinned_claims, rule_summarizer
from sci_harness.provenance import Claim, Journal


def test_budget_raises_before_request():
    budget = Budget(window=200, reserve=50)
    messages = [{"role": "user", "content": "слово " * 100}]
    with pytest.raises(ContextOverflow, match="окна"):
        budget.check(messages)


def test_budget_allows_small_request():
    Budget(window=10_000).check([{"role": "user", "content": "короткий вопрос"}])


def test_budget_stops_on_cost():
    with pytest.raises(Exception, match="потрачено"):
        Budget(window=10_000, cost_limit=0.5).check([], cost=0.6)


def test_summarizer_keeps_head_and_tail_drops_middle_number():
    long_result = (
        "Методика: " + "измерение ваттметром, шаг 1 с, " * 10 + "В простое 6 Вт, под нагрузкой 65 Вт"
    )
    messages = [
        {"role": "system", "content": "ты ассистент"},
        {"role": "tool", "tool_call_id": "c1", "content": long_result},
        {"role": "assistant", "content": "записал"},
        {"role": "user", "content": "продолжай"},
        {"role": "assistant", "content": "готово"},
        {"role": "user", "content": "финальный вопрос"},
    ]
    compacted = rule_summarizer(messages, keep_last=2)
    assert compacted[0]["content"] == "ты ассистент"
    assert len(compacted) == 4
    # в хронику попадают первые 120 символов — цифра из конца результата испарилась
    assert "65 Вт" in str(messages)
    assert "65 Вт" not in str(compacted)


def test_lost_facts_finds_ids_gone_after_compaction():
    long_result = "шапка " * 40 + "id abc123 в конце результата"
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_call_id": "c", "content": long_result},
    ]
    assert lost_facts(["abc123"], messages) == []
    compacted = rule_summarizer([*messages, {"role": "user", "content": "дальше"}], keep_last=1)
    assert lost_facts(["abc123"], compacted) == ["abc123"]


def test_summarizer_does_not_split_tool_pairs():
    """Хвост не начинается с результата инструмента: пара не разрывается."""
    messages = [
        {"role": "system", "content": "s"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "результат"},
        {"role": "assistant", "content": "записал"},
        {"role": "user", "content": "дальше"},
    ]
    for keep_last in (1, 2):
        compacted = rule_summarizer(messages, keep_last=keep_last)
        for i, msg in enumerate(compacted):
            if msg.get("role") == "tool":
                assert any(
                    m.get("role") == "assistant" and m.get("tool_calls") for m in compacted[:i]
                ), f"пара разорвана при keep_last={keep_last}"


def test_pinned_claims_only_verified_and_compact(tmp_path):
    journal = Journal(tmp_path / "claims.jsonl")
    journal.add(Claim(text="6 Вт в простое", source="tab2.txt", locator="таблица 2", verified=True))
    journal.add(Claim(text="выдумка", source="tab2.txt", verified=False))
    block = pinned_claims(journal)
    assert "6 Вт в простое" in block
    assert "выдумка" not in block
    assert "tab2.txt:таблица 2" in block


def test_pinned_claims_empty_when_nothing_verified(tmp_path):
    assert pinned_claims(Journal(tmp_path / "c.jsonl")) == ""
