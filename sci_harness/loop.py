"""Цикл агента: запрос → вызовы инструментов → наблюдения → снова запрос.

Отличия от «учебного минимума», которые здесь принципиальны:

* Вызовы инструментов одного шага идут **параллельно** (3 поиска × 8 с
  вместо 24 с), но результаты кладутся в порядке `tool_calls` — модель
  опирается на порядок.
* У каждого инструмента свой таймаут. Python не умеет убивать поток,
  поэтому таймаут = «перестаём ждать», о чём честно сообщаем модели.
* Повторный вызов с теми же аргументами не выполняется второй раз:
  агент с мемо не платит трижды за один и тот же запрос.
* Обрезанный ответ модели (`finish_reason=max_tokens`) не превращается
  в мусорный вызов — делается один повтор с увеличенным лимитом.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .context import Budget, pinned_claims
from .llm import Reply, ToolCall, Usage
from .provenance import Claim, Journal, find_quote
from .tools import Tool, ToolError, specs


class ChatClient(Protocol):
    """Всё, что циклу нужно от модели. Позволяет подставить скрипт в тестах."""

    usage: Usage

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = ...,
        max_tokens: int = ...,
        temperature: float = ...,
        system: str | None = ...,
        on_text: Callable[[str], None] | None = ...,
    ) -> Reply: ...


@dataclass
class Step:
    n: int
    text: str
    calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class RunResult:
    answer: str
    steps: list[Step]
    usage_cost: float
    usage_input: int
    usage_output: int
    stopped: str

    def summary(self) -> str:
        return (
            f"шагов: {len(self.steps)}, токенов: {self.usage_input}+{self.usage_output}, "
            f"${self.usage_cost:.4f}, остановка: {self.stopped}"
        )


class Runner:
    def __init__(
        self,
        client: ChatClient,
        registry: dict[str, Tool],
        journal: Journal,
        budget: Budget | None = None,
        tool_timeout: float = 30.0,
        max_steps: int = 12,
        workers: int = 4,
        system: str = "",
        on_step: Callable[[Step], None] | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.journal = journal
        self.budget = budget or Budget()
        self.tool_timeout = tool_timeout
        self.max_steps = max_steps
        self.workers = workers
        self.system = system
        self.on_step = on_step
        self._memo: dict[tuple[str, str], tuple[int, str]] = {}

    # --- основной цикл --------------------------------------------------

    def run(self, question: str, max_tokens: int = 2048) -> RunResult:
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        steps: list[Step] = []
        stopped = "max_steps"
        for n in range(1, self.max_steps + 1):
            self.budget.check(messages, self.client.usage.cost)
            started = time.monotonic()
            reply = self._ask(messages, max_tokens)
            messages.append(reply.as_message())
            step = Step(n=n, text=reply.text, seconds=time.monotonic() - started)
            if not reply.tool_calls:
                steps.append(step)
                if self.on_step:
                    self.on_step(step)
                return self._result(reply.text, steps, "ответ")
            observations = self._execute(reply.tool_calls, step)
            for call, result in zip(reply.tool_calls, observations, strict=True):
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            steps.append(step)
            if self.on_step:
                self.on_step(step)
        return self._result("", steps, stopped)

    def _ask(self, messages: list[dict[str, Any]], max_tokens: int) -> Reply:
        """Запрос с обработкой обрезанного ответа (max_tokens)."""
        system = self._system()
        reply = self.client.chat(
            messages,
            tools=specs(self.registry),
            max_tokens=max_tokens,
            system=system or None,
        )
        for _ in range(2):
            truncated = reply.finish_reason in {"length", "max_tokens", "MAX_TOKENS"}
            broken = any("__invalid_json__" in c.args for c in reply.tool_calls)
            if not (truncated and broken):
                break
            max_tokens = min(max_tokens * 2, 8192)
            reply = self.client.chat(
                messages, tools=specs(self.registry), max_tokens=max_tokens, system=system or None
            )
        return reply

    def _system(self) -> str:
        base = self.system
        claims = pinned_claims(self.journal)
        return f"{base}\n\n{claims}" if claims else base

    # --- инструменты ----------------------------------------------------

    def _execute(self, calls: list[ToolCall], step: Step) -> list[str]:
        """Параллельно, с мемо, таймаутом и обрезкой результата."""
        results: list[str] = [""] * len(calls)
        todo: list[tuple[int, ToolCall]] = []
        for idx, call in enumerate(calls):
            key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))
            if key in self._memo:
                cached_step, cached = self._memo[key]
                results[idx] = (
                    f"[повтор: тот же вызов уже был на шаге {cached_step}, результат взят из кэша]\n{cached}"
                )
                step.calls.append((call.name, call.args, "cached"))
                continue
            if "__invalid_json__" in call.args:
                results[idx] = (
                    f"ошибка: аргументы вызова {call.name} не разобрались как JSON "
                    f"({call.args.get('__error__', '')}). Пришли корректный JSON."
                )
                step.calls.append((call.name, call.args, "bad_args"))
                continue
            todo.append((idx, call))

        with ThreadPoolExecutor(max_workers=min(self.workers, max(len(todo), 1))) as pool:
            futures = {idx: pool.submit(self._call_tool, call) for idx, call in todo}
            for idx, call in todo:
                try:
                    result = futures[idx].result(timeout=self.tool_timeout)
                    status = "ok"
                except FutureTimeout:
                    result = (
                        f"таймаут {self.tool_timeout:.0f} с: инструмент {call.name} не ответил. "
                        f"Не повторяй его с теми же аргументами — сократи объём."
                    )
                    status = "timeout"
                except ToolError as exc:
                    result = f"ошибка инструмента: {exc}"
                    status = "error"
                results[idx] = result
                step.calls.append((call.name, call.args, status))
                key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False))
                self._memo[key] = (step.n, result)
        return results

    def _call_tool(self, call: ToolCall) -> str:
        tool = self.registry.get(call.name)
        if tool is None:
            raise ToolError(f"нет такого инструмента: {call.name}. Доступны: {', '.join(self.registry)}")
        if tool.needs_approval:
            raise ToolError(f"{call.name} изменяет файлы: в headless-режиме требуется явное разрешение")
        try:
            value = tool.fn(**call.args)
        except TypeError as exc:
            raise ToolError(f"неверные аргументы для {call.name}: {exc}") from exc
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text

    def _result(self, answer: str, steps: list[Step], stopped: str) -> RunResult:
        return RunResult(
            answer=answer,
            steps=steps,
            usage_cost=self.client.usage.cost,
            usage_input=self.client.usage.input_tokens,
            usage_output=self.client.usage.output_tokens,
            stopped=stopped,
        )


def cite_tool(journal: Journal, sources: dict[str, str]) -> Tool:
    """Инструмент проверки цитаты — сердце курса.

    Модель обязана предъявить дословную цитату и локатор. Инструмент
    ищет цитату в тексте источника; при совпадении пишет утверждение
    в журнал. Соврать в цитате нельзя: она проверяется по файлу.
    """

    def cite_check(text: str, source: str, quote: str, locator: str = "") -> str:
        if source not in sources:
            raise ToolError(f"неизвестный источник {source}. Доступны: {', '.join(sources)}")
        match = find_quote(quote, sources[source])
        claim = Claim(
            text=text, source=source, locator=locator, quote=quote, verified=match.found, score=match.score
        )
        journal.add(claim)
        if match.found:
            return f"принято: id {claim.id}, совпадение {match.score:.2f} ({match.reason})"
        return (
            f"НЕ НАЙДЕНО (id {claim.id}, совпадение {match.score:.2f}, причина: {match.reason}). "
            f"Ближайший фрагмент источника: «{match.snippet[:200]}». "
            f"Либо цитируй точнее, либо откажись от утверждения."
        )

    return Tool(
        name="cite_check",
        description="Записать утверждение с дословной цитатой и локатором. Инструмент сверяет "
        "цитату с текстом источника. Без успешного cite_check утверждение считается непроверенным.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "утверждение своими словами"},
                "source": {"type": "string", "description": "файл или URL источника"},
                "quote": {"type": "string", "description": "дословная цитата из источника"},
                "locator": {"type": "string", "description": "страница/раздел/таблица"},
            },
            "required": ["text", "source", "quote"],
        },
        fn=cite_check,
        read_only=False,
    )


def write_run(path: str | Path, result: RunResult) -> Path:
    """Трейс прогона: то, чего не хватало в исходном курсе."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": result.summary(),
        "stopped": result.stopped,
        "steps": [
            {"n": s.n, "text": s.text, "seconds": round(s.seconds, 2), "calls": s.calls} for s in result.steps
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
