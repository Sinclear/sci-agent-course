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
