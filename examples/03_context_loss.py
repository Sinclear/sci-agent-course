"""Урок 3. Куда пропадает цифра из середины статьи.

Запуск:  python3 examples/03_context_loss.py

Демонстрация в три шага:
1. Длинный результат инструмента попадает в историю.
2. История сжимается (rule_summarizer — детерминированно, без модели).
3. Число из конца результата исчезает из диалога.

Вывод, который и есть суть модуля: не «промпт получше», а вынести
факт в журнал до сжатия. Журнал не сжимается — он не часть транскрипта.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from _mock import banner

from sci_harness.context import Budget, lost_facts, pinned_claims, rule_summarizer
from sci_harness.provenance import Claim, Journal

long_result = (
    "Методика: замеры ваттметром, интервал 1 с, три повтора по 60 с. "
    + "Комментарий авторов к методике. " * 12
    + "В простое 6 Вт, под нагрузкой 88 Вт, 12 токенов/с."
)

messages = [
    {"role": "system", "content": "Ты ассистент-исследователь."},
    {"role": "tool", "tool_call_id": "c1", "content": long_result},
    {"role": "assistant", "content": "Извлёк методику и числа."},
    {"role": "user", "content": "Сравни с 14B-моделью."},
    {"role": "assistant", "content": "У 14B — 6.5 токенов/с."},
    {"role": "user", "content": "Теперь сведи всё в таблицу."},
]

banner("1. До сжатия: число в диалоге есть")
print(f"символов в истории: {sum(len(str(m)) for m in messages)}")
print(f"«12 токенов/с» в истории: {'12 токенов/с' in str(messages)}")

banner("2. После сжатия (в хронику попадает 120 символов на сообщение)")
compacted = rule_summarizer(messages, keep_last=2)
print(f"символов стало: {sum(len(str(m)) for m in compacted)}")
print(f"«12 токенов/с» в истории: {'12 токенов/с' in str(compacted)}")
print("\nХроника выглядит так:")
print(compacted[1]["content"][:300])

banner("3. Стоимость потери: бюджет в токенах")
budget = Budget(window=32_000)
print(f"до: ~{budget.used(messages)} токенов, после: ~{budget.used(compacted)} токенов")

banner("4. Лечение: журнал вынесен из диалога")
with tempfile.TemporaryDirectory() as tmp:
    journal = Journal(Path(tmp) / "claims.jsonl")
    claim = Claim(
        text="8B-модель: 88 Вт, 12 токенов/с",
        source="review_9700x.txt",
        locator="раздел «Энергопотребление»",
        quote="8B модель: 88 Вт среднее, 12 токенов/с",
        verified=True,
    )
    journal.add(claim)
    pinned = pinned_claims(journal)

    print("Даже если из диалога выпало всё, факт доступен:")
    print(pinned)
    print("\nЭтот блок уходит в системный промпт на каждом шаге.")
    print("Модель не пересчитывает число заново — она его цитирует.")

    banner("5. Проверка, которую стоит ставить в evals")
    ids = [c.id for c in journal.claims()]
    lost = lost_facts(ids, compacted)
    print(f"id утверждений в журнале: {ids}")
    print(f"из диалога после сжатия исчезли id: {lost}")
    print(f"«88 Вт» осталось в диалоге: {'88 Вт' in str(compacted)}")
    print(f"«88 Вт» осталось в журнале: {'88 Вт' in pinned}")
    print()
    print("Вот и вся проверка, которую стоит ставить в evals:")
    print("  факт исчез из диалога → это нормально, диалог сжимается;")
    print("  факт исчез из журнала  → это потеря данных, баг пайплайна.")
    print("Пока список исчезнувших id не пуст, а журнал цел — пайплайн здоров.")
