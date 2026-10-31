"""Урок 0. Что агент умеет и чего не умеет — в цифрах, а не на словах.

Запуск:  python3 examples/00_boundaries.py
Живой режим: SCI_PROVIDER=ollama SCI_MODEL=qwen3:8b python3 examples/00_boundaries.py
"""

from __future__ import annotations

from typing import Any

from _mock import banner, fake_transport

from sci_harness.llm import Client, estimate_tokens

NAIVE = "Число ошибок снижается" * 20

banner("1. Сколько это токенов на самом деле")
print(f"символов: {len(NAIVE)}")
print(f"наивная оценка len/4: {len(NAIVE) // 4} токенов")
print(f"с поправкой на кириллицу: {estimate_tokens(NAIVE)} токенов")
print("Разница в 2 раза — это разница между «влезло» и «400 от провайдера»,")
print("если считать бюджет по английской привычке.")

banner("2. Модель не помнит прошлый запрос")
chunks: list[dict[str, Any]] = [
    {"choices": [{"delta": {"content": "Здесь нет никакой памяти:"}}]},
    {"choices": [{"delta": {"content": " весь контекст придёт в запросе заново."}}]},
    {"usage": {"prompt_tokens": 42, "completion_tokens": 11}},
]
client = Client(provider="openai", model="gpt-5-mini", api_key="demo", transport=fake_transport(chunks))
reply = client.chat([{"role": "user", "content": "Что ты помнишь о прошлом сообщении?"}])
print(f"ответ: {reply.text}")
print(
    f"токенов: {reply.usage.input_tokens} вход + {reply.usage.output_tokens} выход, ${reply.usage.cost:.5f}"
)

banner("3. Инструмент, которого нет — это не ответ, а ошибка")

print("Если инструмента нет в реестре, цикл вернёт модели текст ошибки,")
print("а не выдумает результат. Это единственная защита от «я поискал в интернете».")
print("Проверка: tests/test_loop.py::test_unknown_tool_returns_error_not_crash")

banner("4. Чего агент НЕ делает (запишите под свою задачу)")
for line in [
    "не проверяет эксперимент за вас — только цитату из текста;",
    "не видит статью, если вы её не передали (нет «доступа к интернету по умолчанию»);",
    "не помнит вчерашний разговор, если вы не сохранили журнал;",
    "не заменяет чтение методики: он ускоряет сверку, а не понимание.",
]:
    print(f"  - {line}")

if (live := __import__("_mock").live_client()) is not None:
    banner("5. Живой прогон")
    answer = live.chat([{"role": "user", "content": "Сколько будет 12*12? Ответь числом."}])
    print(answer.text.strip())
    print(live.usage)
