"""Урок 2. Цикл агента: инструменты, проверка цитаты, журнал.

Запуск:  python3 examples/02_agent_loop.py

Сценарий: агент извлекает цифры из обзора и обязан каждую подтвердить
дословной цитатой. Вторая попытка — с подменённым числом: инструмент
её отвергает, утверждение остаётся непроверенным.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from _mock import FakeModel, Step, banner, load_sources

from sci_harness.loop import Runner, cite_tool, write_run
from sci_harness.provenance import Journal
from sci_harness.tools import Workspace, build_registry

HERE = Path(__file__).resolve().parent
sources = load_sources(HERE / "sources")

with tempfile.TemporaryDirectory() as tmp:
    work = Workspace(tmp)
    journal = Journal(Path(tmp) / ".sci" / "claims.jsonl")
    registry = {**build_registry(work), "cite_check": cite_tool(journal, sources)}

    model = FakeModel(
        [
            Step(calls=[("read_file", {"path": "sources/review_9700x.txt", "limit": 20})]),
            Step(
                calls=[
                    (
                        "cite_check",
                        {
                            "text": "8B-модель выдаёт 12 токенов/с при 88 Вт",
                            "source": "review_9700x.txt",
                            "quote": "8B модель: 88 Вт среднее, 12 токенов/с",
                            "locator": "раздел «Энергопотребление»",
                        },
                    ),
                ]
            ),
            Step(
                calls=[
                    (
                        "cite_check",
                        {
                            "text": "32B-модель ускоряется до 12 токенов/с",
                            "source": "review_9700x.txt",
                            "quote": "32B модель: 95 Вт, 12 токенов/с",
                            "locator": "раздел «Энергопотребление»",
                        },
                    ),
                ]
            ),
            Step(text="Собрал два факта: один подтверждён, второй — нет."),
        ]
    )

    runner = Runner(
        model, registry, journal, system="Ты ассистент-исследователь. Каждое число подтверждай cite_check."
    )
    result = runner.run("Какие числа энергопотребления подтверждены обзором?")

    banner("1. Что ответила модель")
    print(result.answer)

    banner("2. Что показали инструменты")
    for step in result.steps:
        for name, args, status in step.calls:
            print(f"шаг {step.n}: {name} [{status}] {str(args)[:80]}")

    banner("3. Журнал утверждений — он живёт в файле, а не в диалоге")
    for claim in journal.claims():
        mark = "✓" if claim.verified else "✗"
        print(f"{mark} {claim.id} score={claim.score:.2f} «{claim.text}»")

    banner("4. Метрика, которую можно защитить в статье")
    print(journal.stats())
    print("unsourced_share = доля утверждений без подтверждённой цитаты.")
    print("Это число печатается в трейсе и в отчёте — не «на глаз».")

    banner("5. Трейс прогона")
    path = write_run(Path(tmp) / "runs" / "trace.json", result)
    print(path.read_text(encoding="utf-8")[:400] + "...")

    print(f"\nИТОГ: {result.summary()}")
