"""Инструменты: песочница, файлы, правка по образцу, реестр.

Правила, которые здесь зашиты и разобраны в docs/02-механика.md:

1. Описание инструмента — это промпт. Модель выбирает инструмент по
   описанию, поэтому «читает файл» хуже, чем «читает файл построчно;
   если файл больше 2000 строк — сначала список заголовков».
2. Каждый инструмент живёт в песочнице `root` и не выходит за неё.
3. Результат инструмента ограничен по размеру — иначе контекст
   умирает на третьем шаге (полный текст сохраняется в файл).
4. Правка файла — по образцу, а не перезаписью целиком: модель,
   переписывающая файл на 500 строк, гарантированно что-то теряет.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RESULT_CHARS = 20_000
DEFAULT_READ_LINES = 400


class ToolError(RuntimeError):
    """Ошибка инструмента, которую надо вернуть модели текстом, а не падением."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    read_only: bool = True
    needs_approval: bool = False

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Workspace:
    """Песочница: всё, что делает агент с файлами, происходит внутри root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def safe(self, path: str) -> Path:
        raw = Path(path)
        if raw.is_absolute():
            raise ToolError(f"абсолютные пути запрещены: {path}")
        candidate = (self.root / raw).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"путь вне рабочей папки: {path}")
        return candidate

    def read_file(self, path: str, offset: int = 1, limit: int = DEFAULT_READ_LINES) -> str:
        target = self.safe(path)
        if not target.exists():
            raise ToolError(f"файла нет: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset - 1, 0)
        chunk = lines[start : start + limit]
        head = f"{path}: строки {start + 1}-{start + len(chunk)} из {len(lines)}"
        body = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk))
        tail = "" if start + limit >= len(lines) else f"\n... ещё {len(lines) - start - limit} строк"
        return f"{head}\n{body}{tail}"

    def write_file(self, path: str, content: str) -> str:
        target = self.safe(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"записано {len(content)} символов в {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        """Замена по образцу: точное совпадение, ровно одно вхождение.

        Если образец встречается дважды — это не «мелкая неприятность»,
        а признак того, что модель не видела контекст. Просим уточнить.
        """
        target = self.safe(path)
        if not target.exists():
            raise ToolError(f"файла нет: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ToolError("образец не найден; прочитай файл и возьми точный фрагмент")
        if count > 1:
            raise ToolError(f"образец встречается {count} раз; добавь контекста, чтобы он был уникален")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"заменено 1 вхождение в {path}"

    def list_dir(self, path: str = ".") -> str:
        target = self.safe(path)
        if not target.is_dir():
            raise ToolError(f"не папка: {path}")
        rows = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        return (
            "\n".join(f"{p.name}/" if p.is_dir() else f"{p.name}\t{p.stat().st_size} б" for p in rows)
            or "(пусто)"
        )

    def truncate(self, text: str, keep_chars: int = MAX_RESULT_CHARS) -> str:
        """Обрезаем середину, а не хвост: модель должна видеть и итог."""
        if len(text) <= keep_chars:
            return text
        half = keep_chars // 2
        return (
            f"{text[:half]}\n\n[... вырезано {len(text) - keep_chars} символов; "
            f"полный вывод сохранён в файле ...]\n\n{text[-half:]}"
        )


def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def pdf_extract(workspace: Workspace, path: str, first: int = 1, last: int = 0) -> str:
    """Текст PDF постранично. Без poppler — честная ошибка, а не пустая строка.

    Пустая строка опаснее ошибки: модель считает, что документ пуст,
    и начинает выдумывать содержание.
    """
    if not pdftotext_available():
        raise ToolError("нет pdftotext: pkg install poppler (Termux) / apt install poppler-utils")
    src = workspace.safe(path)
    if not src.exists():
        raise ToolError(f"файла нет: {path}")
    args = ["pdftotext", "-layout", "-enc", "UTF-8"]
    if last:
        args += ["-f", str(first), "-l", str(last)]
    with tempfile.NamedTemporaryFile("r+", encoding="utf-8", suffix=".txt") as out:
        result = subprocess.run([*args, str(src), out.name], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ToolError(f"pdftotext: {result.stderr.strip()[:300]}")
        text = out.read()
    if not text.strip():
        raise ToolError(f"текста не извлечено из {path}: вероятно скан, нужен OCR (tesseract, только eng)")
    pages = text.split("\f")
    parts = [
        f"=== {path}, страница {i + first} ===\n{page.strip()}"
        for i, page in enumerate(pages)
        if page.strip()
    ]
    return "\n\n".join(parts)


def table_extract(workspace: Workspace, path: str) -> str:
    """Попытка вытащить таблицу из layout-текста: строки с 2+ пробелами.

    Честное ограничение: это эвристика, а не разбор таблиц. Для чисел
    из статьи она работает в 80% случаев, для вложенных шапок — нет.
    В docs/02 сказано, как проверить её выход на своей статье.
    """
    text = pdf_extract(workspace, path)
    rows: list[str] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("  ") if c.strip()]
        if len(cells) >= 3:
            rows.append(" | ".join(cells))
    return "\n".join(rows) or "(таблиц не распознано — проверь вёрстку глазами)"


def build_registry(workspace: Workspace, extra: list[Tool] | None = None) -> dict[str, Tool]:
    tools = [
        Tool(
            name="list_dir",
            description="Показать содержимое папки с размерами файлов. Начни с этого.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "папка внутри рабочей"}},
            },
            fn=workspace.list_dir,
        ),
        Tool(
            name="read_file",
            description="Прочитать файл с номерами строк. Файл целиком не отдаётся: "
            "offset/limit, чтобы большие PDF-извлечения не съели контекст.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["path"],
            },
            fn=workspace.read_file,
        ),
        Tool(
            name="write_file",
            description="Создать или полностью перезаписать файл. Для правки существующего "
            "файла используй edit_file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            fn=workspace.write_file,
            read_only=False,
            needs_approval=True,
        ),
        Tool(
            name="edit_file",
            description="Заменить ровно одно вхождение фрагмента old на new. Образец должен "
            "быть уникален; при неточном совпадении инструмент вернёт ошибку — сначала прочитай файл.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "точный фрагмент из файла"},
                    "new": {"type": "string", "description": "чем заменить"},
                },
                "required": ["path", "old", "new"],
            },
            fn=workspace.edit_file,
            read_only=False,
            needs_approval=True,
        ),
        Tool(
            name="pdf_extract",
            description="Извлечь текст PDF постранично (нужен poppler). Указывай first/last, "
            "если страниц больше 10 — иначе результат будет обрезан.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "first": {"type": "integer", "minimum": 1},
                    "last": {"type": "integer", "minimum": 0},
                },
                "required": ["path"],
            },
            fn=lambda path, first=1, last=0: pdf_extract(workspace, path, first, last),
        ),
        Tool(
            name="table_extract",
            description="Вытащить строки таблицы из PDF (эвристика по выравниванию). "
            "Всегда проверяй результат глазами: вложенные шапки ломаются.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            fn=lambda path: table_extract(workspace, path),
        ),
    ]
    for tool in extra or []:
        tools.append(tool)
    return {t.name: t for t in tools}


def specs(registry: dict[str, Tool], only_read_only: bool = False) -> list[dict[str, Any]]:
    return [t.spec() for t in registry.values() if not (only_read_only and not t.read_only)]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
