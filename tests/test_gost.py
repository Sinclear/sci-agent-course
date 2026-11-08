"""Тесты встроенной приёмки и сборки docx (6 проверок вместо 18).

Зачем эти тесты: приёмка, которая всегда говорит PASS, бесполезна. Здесь
на каждый тип провала есть кейс, который обязан провалиться, — и один, где
документ собирается и проходит все шесть проверок.
"""

from __future__ import annotations

import pytest

docx = pytest.importorskip("docx", reason="нужен python-docx: .venv/bin/pip install python-docx")

from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Mm  # noqa: E402

from gost.build import build_markdown  # noqa: E402
from gost.check import FAIL, SKIP, check_document, main  # noqa: E402

DRAFT = """---
title: "Проверка приёмки"
teacher_title: "канд. техн. наук, доцент"
teacher_name: "И.И. Петров"
lab_number: "1"
lab_title: "Измерения"
discipline: "Архитектура ВС"
group: "1234"
student_name: "А.А. Иванов"
department: "Кафедра ИВТ"
---

# Введение

Цель работы — проверить, что приёмка ловит провалы оформления, а не только
хвалит собственную сборку.

# Результаты

Таблица 1 — Энергопотребление

| Модель | Мощность, Вт |
|---|---|
| 8B | 88 |
| 32B | 95 |

# Выводы

Документ проходит приёмку.
"""


@pytest.fixture()
def built(tmp_path):
    source = tmp_path / "draft.md"
    source.write_text(DRAFT, encoding="utf-8")
    target = tmp_path / "report.docx"
    build_markdown(source, target)
    return target


def _by_name(checks, name):
    return next(c for c in checks if c.name == name)


def test_built_document_passes_all_checks(built):
    checks = check_document(built)
    assert [c.status for c in checks] == ["PASS"] * 6, [c.line(i) for i, c in enumerate(checks, 1)]
    assert main([str(built), "--quiet"]) == 0


def test_wrong_margins_fail(built):
    document = docx.Document(str(built))
    document.sections[0].left_margin = Mm(10)
    broken = built.with_name("margins.docx")
    document.save(str(broken))
    check = _by_name(check_document(broken), "поля страницы")
    assert check.status == FAIL and "left 10" in check.detail
    assert main([str(broken), "--quiet"]) == 1


def test_wrong_font_fails(built):
    document = docx.Document(str(built))
    document.styles["Normal"].font.name = "Arial"
    broken = built.with_name("font.docx")
    document.save(str(broken))
    assert _by_name(check_document(broken), "основной шрифт").status == FAIL


def test_wrong_spacing_fails(built):
    document = docx.Document(str(built))
    fmt = document.styles["Normal"].paragraph_format
    fmt.line_spacing = 1.0
    fmt.first_line_indent = None
    broken = built.with_name("spacing.docx")
    document.save(str(broken))
    check = _by_name(check_document(broken), "интервал и отступ")
    assert check.status == FAIL and "интервал 1" in check.detail


def test_missing_page_field_fails(built):
    document = docx.Document(str(built))
    footer = document.sections[0].footer
    for paragraph in list(footer.paragraphs):
        paragraph._p.getparent().remove(paragraph._p)
    broken = built.with_name("nopage.docx")
    document.save(str(broken))
    assert _by_name(check_document(broken), "нумерация страниц").status == FAIL


def test_heading_without_style_fails(built):
    document = docx.Document(str(built))
    for paragraph in document.paragraphs:
        if paragraph.style.name.startswith("Heading"):
            paragraph.style = document.styles["Normal"]
    broken = built.with_name("noheadings.docx")
    document.save(str(broken))
    assert _by_name(check_document(broken), "заголовки стилями").status == FAIL


def test_table_without_caption_fails(built):
    document = docx.Document(str(built))
    for paragraph in document.paragraphs:
        if paragraph.text.startswith("Таблица 1"):
            paragraph.text = "Таблица без номера и тире"
    broken = built.with_name("nocaption.docx")
    document.save(str(broken))
    check = _by_name(check_document(broken), "подписи таблиц и рисунков")
    assert check.status == FAIL and "таблиц 1" in check.detail


def test_short_figure_caption_fails(built):
    document = docx.Document(str(built))
    for paragraph in document.paragraphs:
        if paragraph.text.startswith("Таблица 1"):
            paragraph.text = "Рис. 1 — так подписывать нельзя"
    broken = built.with_name("badfigure.docx")
    document.save(str(broken))
    check = _by_name(check_document(broken), "подписи таблиц и рисунков")
    assert check.status == FAIL and "Рис." in check.detail


def test_no_tables_or_figures_is_not_a_failure(tmp_path):
    source = tmp_path / "plain.md"
    source.write_text("# Введение\n\nПросто текст без таблиц.\n", encoding="utf-8")
    target = tmp_path / "plain.docx"
    build_markdown(source, target)
    checks = check_document(target)
    assert _by_name(checks, "подписи таблиц и рисунков").status == SKIP
    assert not [c for c in checks if c.status == FAIL]
    assert main([str(target), "--quiet"]) == 0


def test_missing_file_exits_2(tmp_path):
    assert main([str(tmp_path / "nope.docx"), "--quiet"]) == 2


def test_broken_docx_exits_2(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    assert main([str(bad), "--quiet"]) == 2


def test_paragraph_alignment_is_justified(built):
    """Сборка не только «проходит проверку», но и выравнивает по ширине."""
    document = docx.Document(str(built))
    assert document.styles["Normal"].paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY


def test_fonts_have_no_theme_override(built):
    """Атрибуты темы перекрывают явный шрифт: заголовки уезжали в Calibri/Carlito."""
    from docx.oxml.ns import qn

    document = docx.Document(str(built))
    theme_attrs = ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme")
    for name in ("Normal", "Heading 1", "Heading 2"):
        rfonts = document.styles[name].element.rPr.find(qn("w:rFonts"))
        for attr in theme_attrs:
            assert rfonts.get(qn(attr)) is None, f"{name}: остался {attr}"
        assert rfonts.get(qn("w:ascii")) == "Times New Roman", name


def test_document_language_is_russian(built):
    """Язык проверки орфографии: с en-US из шаблона Word подчёркивает весь текст."""
    from docx.oxml.ns import qn

    document = docx.Document(str(built))
    styles_el = document.styles.element

    rpr_default = styles_el.find(qn("w:docDefaults") + "/" + qn("w:rPrDefault"))
    lang = rpr_default.find(qn("w:rPr")).find(qn("w:lang"))
    assert lang.get(qn("w:val")) == "ru-RU"
    assert lang.get(qn("w:eastAsia")) == "ru-RU"

    for name in ("Normal", "Heading 1", "Heading 2"):
        style_lang = document.styles[name].element.rPr.find(qn("w:lang"))
        assert style_lang is not None, f"{name}: язык не задан"
        assert style_lang.get(qn("w:val")) == "ru-RU", name

    xml = styles_el.xml
    assert "en-US" not in xml, "в стилях остался английский язык"

    theme_lang = document.settings.element.find(qn("w:themeFontLang"))
    assert theme_lang is not None, "язык шрифтов темы не задан"
    assert theme_lang.get(qn("w:val")) == "ru-RU"
    assert "en-US" not in document.settings.element.xml, "английский в settings.xml"
