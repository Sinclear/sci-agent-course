"""Урок 1. Источники и инструменты: что реально видит модель.

Запуск:  python3 examples/01_sources.py

Тема урока: инструмент возвращает не «файл», а текст, который модель
сможет прочитать в бюджете контекста. Многострочный PDF без обрезки —
самый быстрый способ получить 400 от провайдера.
"""

from __future__ import annotations

from pathlib import Path

from _mock import banner, load_sources

from sci_harness.tools import Workspace, build_registry

HERE = Path(__file__).resolve().parent
ws = Workspace(HERE)
registry = build_registry(ws)

banner("1. Что лежит в папке — это инструмент, а не вы")
print(registry["list_dir"].fn("sources"))

banner("2. Чтение с номерами строк — чтобы модель могла цитировать точно")
print(registry["read_file"].fn("sources/review_9700x.txt", limit=12))

banner("3. Описание инструмента — это промпт")
print("Модель выбирает инструмент по описанию. Вот чем они отличаются:")
for name in ("read_file", "pdf_extract", "table_extract"):
    tool = registry[name]
    print(f"\n{name}: {tool.description}")

banner("4. Обрезка результата: без неё контекст умирает на третьем шаге")
long_text = "\n".join(f"строка {i}: {'данные ' * 20}" for i in range(400))
print(f"исходный результат: {len(long_text)} символов")
print(f"после обрезки: {registry['read_file'].fn} — обрезает не инструмент, а цикл;")
print(f"а Workspace.truncate оставляет края: {len(ws.truncate(long_text, 500))} символов")

banner("5. Источники, доступные для проверки цитат")
sources = load_sources(HERE / "sources")
for name, text in sources.items():
    print(f"{name}: {len(text)} символов, {len(text.splitlines())} строк")
print("\nИменно этот словарь попадает в cite_check — модель не может")
print("сослаться на источник, которого нет: инструмент вернёт ошибку.")
