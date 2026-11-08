"""Контекст: бюджет, сжатие, и проверка «что пережило сжатие».

Главный тезис модуля 3: цифра из середины статьи теряется не потому,
что модель глупая, а потому что сжатие — это пересказ пересказа.
Лечится не «лучшим промптом», а вынесением факта из транскрипта
в структуру: журнал утверждений (provenance.Journal) не сжимается
никогда, потому что он не часть диалога.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm import estimate_tokens
from .provenance import Journal


class ContextOverflow(RuntimeError):
    """Запрос не влезет в окно модели. Не «попробуем и получим 400», а заранее."""


class BudgetExceeded(RuntimeError):
    """Кончился денежный лимит прогона."""


@dataclass
class Budget:
    window: int = 32_000
    cost_limit: float = 1.0
    reserve: int = 2_000

    def used(self, messages: list[dict[str, Any]]) -> int:
        return sum(estimate_tokens(json.dumps(m, ensure_ascii=False)) for m in messages)

    def check(self, messages: list[dict[str, Any]], cost: float = 0.0) -> None:
        used = self.used(messages)
        if used + self.reserve > self.window:
            raise ContextOverflow(
                f"запрос ~{used} ток + резерв {self.reserve} > окна {self.window}. "
                f"Сожми историю или сократи инструмент-результаты."
            )
        if cost >= self.cost_limit:
            raise BudgetExceeded(f"потрачено ${cost:.4f} при лимите ${self.cost_limit:.2f}")


def pinned_claims(journal: Journal, limit: int = 40) -> str:
    """Блок «проверенные факты» для вставки в системный промпт.

    Это не украшение: после сжатия модель обязана видеть те же числа,
    которыми уже пользовалась, иначе она их пере-выведет (и соврёт).
    """
    claims = [c for c in journal.claims() if c.verified][:limit]
    if not claims:
        return ""
    rows = [f"- {c.text} [{c.source}{':' + c.locator if c.locator else ''}] (id {c.id})" for c in claims]
    return "Проверенные факты (не пересчитывай их заново, ссылайся):\n" + "\n".join(rows)


def rule_summarizer(messages: list[dict[str, Any]], keep_last: int = 4) -> list[dict[str, Any]]:
    """Детерминированное сжатие без модели — чтобы урок был воспроизводим.

    Оставляем системный промпт, краткую «хронику» середины и последние
    ходы. Хроника — заголовки инструментов и первые 120 символов
    текста. Именно здесь и теряются числа: 120 символов хватает на
    «извлекли текст», но не на «6 Вт в простое».

    В docs/03 предлагается заменить на модель и сравнить потерю.
    """
    if len(messages) <= keep_last + 1:
        return messages
    split = len(messages) - keep_last
    # пары tool-call/tool-result не разрываем: если хвост начинается с
    # результата инструмента, забираем и сообщение ассистента с вызовом
    while split > 1 and messages[split].get("role") == "tool":
        split -= 1
    head, middle, tail = messages[:1], messages[1:split], messages[split:]
    lines: list[str] = []
    for msg in middle:
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or ():
                lines.append(f"- вызван {call['function']['name']} с {call['function']['arguments'][:120]}")
            if msg.get("content"):
                lines.append(f"- сказано: {msg['content'][:120]}")
        elif role == "tool":
            lines.append(f"- результат {msg.get('tool_call_id', '?')}: {msg.get('content', '')[:120]}")
        else:
            lines.append(f"- пользователь: {msg.get('content', '')[:120]}")
    chronicle = {"role": "user", "content": "Хроника предыдущих шагов (сжато):\n" + "\n".join(lines)}
    return [*head, chronicle, *tail]


def lost_facts(claim_ids: list[str], messages: list[dict[str, Any]]) -> list[str]:
    """Какие id утверждений исчезли из диалога после сжатия.

    Полезно как упражнение и как проверка в evals: если журнал
    вынесен наружу, этот список может быть любым — факты всё равно
    доступны из файла. Если журнала нет, список = потерянные данные.
    """
    blob = json.dumps(messages, ensure_ascii=False)
    return [cid for cid in claim_ids if cid not in blob]
