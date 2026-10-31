"""Мок-модель и фейковый транспорт: уроки работают без ключа и без сети.

Это не «заглушка ради галочки». Урок должен быть воспроизводимым:
читатель запускает файл и видит ровно тот же вывод, что и автор.
Живой прогон включается переменной окружения — см. `live()` внизу.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# запуск «python3 examples/урок.py» кладёт в sys.path только examples/;
# добавляем корень курса, чтобы sci_harness импортировался без установки
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sci_harness.llm import Client, Reply, ToolCall, Usage  # noqa: E402


def load_sources(folder: str | Path) -> dict[str, str]:
    """Все .txt и .md из папки как источники: имя → текст."""
    out: dict[str, str] = {}
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() in {".txt", ".md"}:
            out[path.name] = path.read_text(encoding="utf-8")
    return out


@dataclass
class Step:
    """Один ход мок-модели: либо текст, либо вызовы инструментов."""

    text: str = ""
    calls: list[tuple[str, dict[str, Any]]] = None  # type: ignore[assignment]

    def reply(self) -> Reply:
        return Reply(
            text=self.text,
            tool_calls=[
                ToolCall(id=f"call_{i}", name=n, args=a) for i, (n, a) in enumerate(self.calls or [])
            ],
            usage=Usage(input_tokens=800, output_tokens=60, cost=0.0009),
        )


class FakeModel:
    """Скрипт вместо модели. Держит историю запросов — её видно в уроках."""

    def __init__(self, steps: list[Step]) -> None:
        self.steps = list(steps)
        self.usage = Usage()
        self.turns: list[list[dict[str, Any]]] = []
        self.systems: list[str | None] = []

    def chat(self, messages, tools=None, max_tokens=2048, temperature=0.0, system=None, on_text=None):
        self.turns.append(list(messages))
        self.systems.append(system)
        reply = self.steps.pop(0).reply() if self.steps else Reply(text="(сценарий исчерпан)")
        self.usage.add(reply.usage)
        return reply


def fake_transport(chunks: list[dict[str, Any]]):
    """SSE-поток в формате OpenAI: проходит через настоящий разбор клиента."""

    def send(url: str, headers: dict[str, str], body: bytes, timeout: float) -> Iterator[bytes]:
        for chunk in chunks:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return send


def live_client() -> Client | None:
    """Живой клиент, если заданы SCI_PROVIDER и SCI_API_KEY (или локальная Ollama)."""
    provider = os.environ.get("SCI_PROVIDER", "")
    key = os.environ.get("SCI_API_KEY", "")
    if provider == "ollama" or (provider and key):
        return Client()
    return None


def banner(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")
