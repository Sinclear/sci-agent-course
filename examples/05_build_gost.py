"""Урок 5. Сборка артефакта: markdown → ГОСТ-docx → проверка.

Запуск:  python3 examples/05_build_gost.py

Два пути, и оба честные:

* есть скилл `gost-lab-report` (pandoc + venv) — собираем и проверяем им,
  18 проверок, шаблон кафедры;
* скилла нет — собираем встроенным `gost/build.py` и проверяем встроенным
  `gost/check.py`, 6 проверок. Этого хватает, чтобы пройти модуль 4, но это
  не «18/18»: разница печатается в конце урока.

Нужен python-docx: `.venv/bin/pip install python-docx` (make setup ставит его).
Важная деталь Termux: /tmp не писабельный, временные файлы — в $TMPDIR.
"""

from __future__ import annotations

import importlib.util
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

Таблица 1 — Энергопотребление и скорость генерации

| Модель | Мощность, Вт | Токенов/с |
|---|---|---|
| 8B | 88 | 12 |
| 14B | 92 | 6.5 |
| 32B | 95 | 2.4 |

# Выводы

До 8B процессор достаточен; с 14B ограничение — пропускная способность памяти.
"""


def skill_status() -> tuple[bool, str]:
    if not SKILL.exists():
        return False, "нет скилла gost-lab-report"
    if not VENV_PY.exists():
        return False, "нет venv с python-docx"
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False, "нет pandoc"
    return True, f"pandoc {pandoc}"


def builtin_available() -> bool:
    return importlib.util.find_spec("docx") is not None


banner("1. Проверка окружения")
skill_ok, skill_info = skill_status()
print(f"скилл: {SKILL} — {'есть' if SKILL.exists() else 'нет'}")
print(f"venv:  {VENV_PY} — {'есть' if VENV_PY.exists() else 'нет'}")
print(f"docx:  python-docx — {'есть' if builtin_available() else 'нет'}")
print(f"итог:  {'внешний скилл, 18 проверок' if skill_ok else 'встроенная сборка, 6 проверок'}")

if not skill_ok and not builtin_available():
    banner("Установка (выполнить один раз)")
    print("# встроенный путь — достаточно для модуля 4:")
    print("python3 -m venv .venv && .venv/bin/pip install python-docx")
    print("# эталонный путь (18 проверок, шаблон кафедры):")
    print("git clone --depth 1 https://github.com/Bbar0n234/gost-report-skill.git $TMPDIR/gostsrc")
    print("cp -a $TMPDIR/gostsrc/gost-lab-report ~/.pi/agent/skills/")
    print("PIP_USER=0 ~/.local/share/gost-docx-venv/bin/pip install python-docx docxcompose")
    print("pkg install pandoc          # Termux; apt install pandoc — на Debian")
    raise SystemExit(0)

work = Path(tempfile.mkdtemp(prefix="gost-"))
md = work / "report.md"
md.write_text(REPORT, encoding="utf-8")
docx = work / "report.docx"

if skill_ok:
    banner(f"2. Сборка скиллом ({skill_info})")
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
    code = verify.returncode
else:
    from gost.build import build_markdown
    from gost.check import check_document

    banner("2. Сборка встроенными средствами (gost/build.py)")
    build_markdown(md, docx)
    print(f"собрано: {docx} ({docx.stat().st_size} байт)")

    banner("3. Встроенная приёмка ГОСТ: 6 проверок вместо 18")
    checks = check_document(docx)
    for i, check in enumerate(checks, 1):
        print(check.line(i))
    failed = [c for c in checks if c.status == "FAIL"]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} PASS")
    print("Это не «18/18»: проверяются поля, шрифт, интервал, нумерация, заголовки,")
    print("подписи. Оглавление, формулы и список источников — за глазами и за скиллом.")
    code = 0 if not failed else 1

banner("4. Что осталось человеку")
print("- СОДЕРЖАНИЕ: без LibreOffice обновляется в Word (Ctrl+A, F9).")
print("- Титульник: проверить, что вуз и тип работы совпадают с бланком кафедры.")
print("- Таблицы и рисунки: проверка смотрит формат подписи, но не её смысл.")
print("- Числа: только из журнала утверждений, а не из этого шаблона.")
print(f"\nфайлы: {work}")
print("TMPDIR:", os.environ.get("TMPDIR", "/tmp"))
raise SystemExit(code)
