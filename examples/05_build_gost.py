"""Урок 5. Сборка артефакта: markdown → ГОСТ-docx → проверка.

Запуск:  python3 examples/05_build_gost.py

Нужен установленный скилл gost-lab-report и venv с python-docx
(см. docs/04-приёмка-гост.md). Если их нет — скрипт печатает команду
установки и НЕ падает: артефакт собирается на другой машине.

Важная деталь Termux: /tmp не писабельный, временные файлы — в $TMPDIR.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from _mock import banner

SKILL = Path.home() / ".pi/agent/skills/gost-lab-report"
VENV_PY = Path.home() / ".local/share/gost-docx-venv/bin/python"

REPORT = """---
title: "Влияние планировщика на энергопотребление при LLM-инференсе"
teacher_title: "канд. техн. наук, доцент"
teacher_name: "И.И. Петров"
lab_number: "1"
lab_title: "Измерение энергопотребления"
discipline: "Архитектура вычислительных систем"
group: "1234"
student_name: "А.А. Иванов"
department: "Кафедра ИВТ"
---

# Введение

Цель работы — сопоставить энергопотребление и скорость генерации
для моделей разного размера на CPU.

# Методика

Замеры ваттметром, интервал 1 с, три повтора по 60 с.

# Результаты

| Модель | Мощность, Вт | Токенов/с |
|---|---|---|
| 8B | 88 | 12 |
| 14B | 92 | 6.5 |
| 32B | 95 | 2.4 |

# Выводы

До 8B процессор достаточен; с 14B ограничение — пропускная способность памяти.
"""


def tool_status() -> tuple[bool, str]:
    if not SKILL.exists():
        return False, "нет скилла gost-lab-report"
    if not VENV_PY.exists():
        return False, "нет venv с python-docx"
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False, "нет pandoc"
    return True, f"pandoc {pandoc}"


banner("1. Проверка окружения")
ok, info = tool_status()
print(f"skill: {SKILL} — {'есть' if SKILL.exists() else 'нет'}")
print(f"venv:  {VENV_PY} — {'есть' if VENV_PY.exists() else 'нет'}")
print(f"итог:  {info}")

if not ok:
    banner("Установка (выполнить один раз)")
    print("git clone --depth 1 https://github.com/Bbar0n234/gost-report-skill.git $TMPDIR/gostsrc")
    print("cp -a $TMPDIR/gostsrc/gost-lab-report ~/.pi/agent/skills/")
    print("python3 -m venv ~/.local/share/gost-docx-venv")
    print("PIP_USER=0 ~/.local/share/gost-docx-venv/bin/pip install python-docx docxcompose")
    print("pkg install pandoc          # Termux; apt install pandoc — на Debian")
    raise SystemExit(0)

banner("2. Сборка")
work = Path(tempfile.mkdtemp(prefix="gost-"))
md = work / "report.md"
md.write_text(REPORT, encoding="utf-8")
docx = work / "report.docx"

build = subprocess.run(
    [str(VENV_PY), str(SKILL / "scripts/build.py"), str(md), str(docx)],
    capture_output=True,
    text=True,
    check=False,
)
print(build.stdout[-600:] or build.stderr[-600:])

banner("3. Детерминированная проверка ГОСТ (18 проверок, без Word)")
verify = subprocess.run(
    [str(VENV_PY), str(SKILL / "scripts/verify.py"), str(docx)],
    capture_output=True,
    text=True,
    check=False,
)
print(verify.stdout[-1500:])
print(f"код возврата: {verify.returncode}")

banner("4. Что осталось человеку")
print("- СОДЕРЖАНИЕ: без LibreOffice обновляется в Word (Ctrl+A, F9).")
print("- Титульник: проверить, что вуз и тип работы совпадают с бланком кафедры.")
print("- Таблицы и рисунки: verify проверяет стили, но не смысл подписей.")
print("- Числа: только из журнала утверждений, а не из этого шаблона.")
print(f"\nфайлы: {work}")
print("TMPDIR:", os.environ.get("TMPDIR", "/tmp"))
