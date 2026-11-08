"""Тесты capstone: локатор и контрольная подмена числа.

Эти две функции — то, что делает отчёт проверяемым. Если ломается
локатор, читатель не найдёт цитату; если ломается контрольная подмена,
детектор перестаёт ловить подмену цифр и «подтверждает» всё подряд.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from capstone.mini_review import locator_for, negative_control  # noqa: E402
from sci_harness.provenance import Claim, find_quote  # noqa: E402

TEXT = "Введение\nТеория\nМощность 88 Вт при 12 токенах/с.\nВыводы\n"


def test_locator_by_line() -> None:
    assert locator_for(TEXT, "Мощность 88 Вт") == "строка 3"


def test_locator_by_page() -> None:
    pdf_like = "страница один\n\fстраница два\n\fМощность 88 Вт"
    assert locator_for(pdf_like, "Мощность 88 Вт") == "с. 3"


def test_locator_missing_quote_is_empty() -> None:
    assert locator_for(TEXT, "такого текста нет") == ""


def test_control_bumps_all_numbers() -> None:
    claim = Claim(text="мощность", source="s.txt", quote="Мощность 88.5 Вт при 12 токенах/с.")
    control = negative_control(claim)
    assert "88.5" not in control.quote
    assert "101.8" in control.quote  # 88.5 * 1.15
    assert "13.8" in control.quote  # 12 * 1.15
    assert "подмена чисел" in control.text


def test_control_is_rejected_by_checker() -> None:
    claim = Claim(text="мощность", source="s.txt", quote="Мощность 88 Вт при 12 токенах/с.")
    assert find_quote(claim.quote, TEXT).found is True
    assert find_quote(negative_control(claim).quote, TEXT).found is False


def test_missing_folder_exits_2(tmp_path, monkeypatch) -> None:
    """Нет папки — понятное сообщение и код 2, а не traceback."""
    monkeypatch.setattr(sys, "argv", ["mini_review.py", "--sources", str(tmp_path / "nope")])
    from capstone.mini_review import main

    assert main() == 2


def test_folder_without_text_exits_2(tmp_path, monkeypatch) -> None:
    """Папка есть, читаемого текста нет — тоже 2, без пустого отчёта."""
    (tmp_path / "notes.bin").write_bytes(b"\x00\x01")
    monkeypatch.setattr(sys, "argv", ["mini_review.py", "--sources", str(tmp_path)])
    from capstone.mini_review import main

    assert main() == 2


def test_offline_report_is_written(tmp_path, monkeypatch) -> None:
    """Офлайн-режим на реальном источнике: отчёт создаётся и содержит метрики."""
    out = tmp_path / "review.md"
    sources = ROOT / "examples/sources"
    monkeypatch.setattr(sys, "argv", ["mini_review.py", "--sources", str(sources), "--out", str(out)])
    from capstone.mini_review import main

    assert main() == 0
    text = out.read_text(encoding="utf-8")
    assert "## Проверенные утверждения" in text
    assert "## Метрики" in text
    assert "контроль: подмена чисел" in text
