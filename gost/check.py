"""Приёмка docx по ГОСТ 7.32: шесть машинных проверок вместо восемнадцати.

Зачем этот модуль. Полная приёмка курса — внешний скилл `gost-lab-report`
(18 проверок, pandoc + python-docx). Он НЕ входит в репозиторий, и без него
модуль 4 нечем проверить: остаётся «на глаз». Здесь — шесть проверок, которые
ловят самые частые провалы и работают из коробки:

1. поля страницы 30/15/20/20 мм;
2. основной шрифт Times New Roman 14 pt;
3. интервал 1.5, отступ первой строки 1.25 см, интервалы до/после 0;
4. нумерация страниц: поле PAGE в колонтитуле;
5. структурные заголовки оформлены стилем, а не «жирным вручную»;
6. подписи «Таблица N — …» / «Рисунок N — …», а не «Рис.» и не пустые.

Честная граница: здесь нет проверки полей подписей (только наличие), формул,
оглавления и списка источников. Шесть проверок — это «документ не развалился
по оформлению», а не «документ готов к сдаче».

Запуск:
    python3 -m gost.check report.docx          # код 0, если нет FAIL
    python3 -m gost.check report.docx --quiet  # только итог
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

# --- требования, которые проверяются ---------------------------------------

PAGE_MARGINS_MM: dict[str, float] = {"left": 30.0, "right": 15.0, "top": 20.0, "bottom": 20.0}
MARGIN_TOLERANCE_MM = 2.0
FONT_NAME = "Times New Roman"
FONT_SIZE_PT = 14.0
LINE_SPACING = 1.5
FIRST_LINE_CM = 1.25

HEADING_STYLE_NAMES = {"heading 1", "heading 2", "heading 3", "заголовок 1", "заголовок 2", "заголовок 3"}
TABLE_CAPTION = re.compile(r"^Таблица\s+\d+(\s*[—–-]\s*\S|\s*$)")
FIGURE_CAPTION = re.compile(r"^Рисунок\s+\d+(\s*[—–-]\s*\S|\s*$)")
BAD_FIGURE = re.compile(r"^Рис\.\s*\d")

PASS, FAIL, SKIP = "PASS", "FAIL", "н/п"


@dataclass
class Check:
    """Одна проверка: что смотрели, чем кончилось, почему."""

    name: str
    status: str
    detail: str

    def line(self, n: int) -> str:
        return f"[{n}] {self.name:<28} {self.status:<5} {self.detail}"


def _style_name(style) -> str:
    return (style.name or "").casefold()


def _has_page_field(doc) -> bool:
    """Поле PAGE в любом колонтитуле — так работает нумерация страниц."""
    for section in doc.sections:
        for part in (section.footer, section.header, section.first_page_footer):
            for paragraph in part.paragraphs:
                for node in paragraph._p.iter(qn("w:instrText")):
                    if "PAGE" in (node.text or "").upper():
                        return True
                for node in paragraph._p.iter(qn("w:fldSimple")):
                    if "PAGE" in (node.get(qn("w:instr")) or "").upper():
                        return True
    return False


def check_margins(doc) -> Check:
    section = doc.sections[0]
    got = {
        "left": round(section.left_margin.mm, 1),
        "right": round(section.right_margin.mm, 1),
        "top": round(section.top_margin.mm, 1),
        "bottom": round(section.bottom_margin.mm, 1),
    }
    bad = [f"{side} {got[side]:.0f} вместо {want:.0f}" for side, want in PAGE_MARGINS_MM.items()
           if abs(got[side] - want) > MARGIN_TOLERANCE_MM]
    detail = "30/15/20/20 мм" if not bad else "; ".join(bad) + " мм"
    return Check("поля страницы", FAIL if bad else PASS, detail)


def check_font(doc) -> Check:
    style = doc.styles["Normal"]
    name = style.font.name
    size = style.font.size.pt if style.font.size is not None else None
    problems = []
    if (name or "") != FONT_NAME:
        problems.append(f"шрифт {name or 'не задан'}")
    if size is None or abs(size - FONT_SIZE_PT) > 0.01:
        problems.append(f"кегль {f'{size:g}' if size else 'не задан'}")
    detail = f"{FONT_NAME} {FONT_SIZE_PT:g}" if not problems else ", ".join(problems)
    return Check("основной шрифт", FAIL if problems else PASS, detail)


def check_paragraph(doc) -> Check:
    fmt = doc.styles["Normal"].paragraph_format
    line = fmt.line_spacing
    indent = fmt.first_line_indent.cm if fmt.first_line_indent is not None else None
    before = fmt.space_before.pt if fmt.space_before is not None else 0.0
    after = fmt.space_after.pt if fmt.space_after is not None else 0.0
    problems = []
    if line is None or abs(float(line) - LINE_SPACING) > 0.01:
        problems.append(f"интервал {line if line is not None else 'не задан'}")
    if indent is None or abs(indent - FIRST_LINE_CM) > 0.05:
        problems.append(f"отступ {f'{indent:.2f} см' if indent is not None else 'не задан'}")
    if before or after:
        problems.append(f"интервалы до/после {before:g}/{after:g} пт")
    detail = "интервал 1.5, отступ 1.25 см, до/после 0" if not problems else ", ".join(problems)
    return Check("интервал и отступ", FAIL if problems else PASS, detail)


def check_page_numbers(doc) -> Check:
    if _has_page_field(doc):
        return Check("нумерация страниц", PASS, "поле PAGE в колонтитуле")
    return Check("нумерация страниц", FAIL, "нет поля PAGE: Word не пронумерует страницы")


def check_headings(doc) -> Check:
    headings = [p for p in doc.paragraphs if _style_name(p.style) in HEADING_STYLE_NAMES]
    if headings:
        return Check("заголовки стилями", PASS, f"стилевых заголовков: {len(headings)}")
    # заголовок «на вид» есть, а стиля нет — типичный провал ручной вёрстки
    manual = [
        p.text.strip()
        for p in doc.paragraphs
        if p.text.strip() and p.text.strip().isupper() and len(p.text.strip()) < 80
    ]
    if manual:
        return Check(
            "заголовки стилями",
            FAIL,
            f"прописными без стиля: «{manual[0][:40]}» — нужен стиль «Заголовок 1»",
        )
    return Check("заголовки стилями", FAIL, "ни одного абзаца со стилем заголовка")


def check_captions(doc) -> Check:
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    bad_figures = [t for t in texts if BAD_FIGURE.match(t)]
    tables = len(doc.tables)
    figures = len(doc.inline_shapes)
    table_caps = [t for t in texts if TABLE_CAPTION.match(t)]
    figure_caps = [t for t in texts if FIGURE_CAPTION.match(t)]

    if not tables and not figures:
        return Check("подписи таблиц и рисунков", SKIP, "в документе нет таблиц и рисунков")

    problems = []
    if tables and len(table_caps) < tables:
        problems.append(f"таблиц {tables}, подписей «Таблица N — …» {len(table_caps)}")
    if figures and len(figure_caps) < figures:
        problems.append(f"рисунков {figures}, подписей «Рисунок N — …» {len(figure_caps)}")
    if bad_figures:
        problems.append(f"«{bad_figures[0][:30]}» вместо «Рисунок N — …»")
    detail = (
        f"таблиц {tables}, рисунков {figures}, подписей {len(table_caps) + len(figure_caps)}"
        if not problems
        else "; ".join(problems)
    )
    return Check("подписи таблиц и рисунков", FAIL if problems else PASS, detail)


def check_document(path: str | Path) -> list[Check]:
    """Все шесть проверок. Порядок фиксирован: по нему же печатается отчёт."""
    doc = Document(str(path))
    return [
        check_margins(doc),
        check_font(doc),
        check_paragraph(doc),
        check_page_numbers(doc),
        check_headings(doc),
        check_captions(doc),
    ]


NOT_CHECKED = (
    "не проверяются: содержание (оглавление), поля подписей, формулы, "
    "список источников, титульный лист по бланку кафедры"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Приёмка docx по ГОСТ 7.32 (6 проверок)")
    parser.add_argument("docx", help="файл .docx")
    parser.add_argument("--quiet", action="store_true", help="печатать только итог")
    args = parser.parse_args(argv)

    path = Path(args.docx)
    if not path.exists():
        print(f"файла нет: {path}", file=sys.stderr)
        return 2
    try:
        checks = check_document(path)
    except Exception as exc:  # битый docx или не docx вовсе
        print(f"не удалось прочитать {path}: {exc}", file=sys.stderr)
        return 2

    failed = [c for c in checks if c.status == FAIL]
    passed = [c for c in checks if c.status == PASS]
    skipped = [c for c in checks if c.status == SKIP]

    if not args.quiet:
        print(f"приёмка ГОСТ 7.32 (встроенная, 6 проверок): {path.name}")
        print("-" * 78)
        for i, check in enumerate(checks, 1):
            print(check.line(i))
        print("-" * 78)
    verdict = "ПРИНЯТО" if not failed else "НЕ ПРИНЯТО"
    print(
        f"{verdict}: {len(passed)}/{len(checks)} PASS"
        + (f", {len(skipped)} н/п" if skipped else "")
        + (f", провалов {len(failed)}" if failed else "")
    )
    if failed and not args.quiet:
        print("\nЧто чинить: " + "; ".join(c.name for c in failed))
    if not args.quiet:
        print(f"\n{NOT_CHECKED}.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
