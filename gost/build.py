"""Сборка docx по ГОСТ 7.32 из простого markdown — учебный минимум.

Зачем. Эталонная сборка курса — внешний скилл `gost-lab-report`
(pandoc + python-docx + шаблон кафедры, 18 проверок). Здесь только то, что
нужно, чтобы модуль 4 проходил без скилла: лист A4, поля 30/15/20/20, Times
New Roman 14, интервал 1.5, отступ 1.25 см, нумерация страниц полем PAGE,
заголовки стилями, таблицы с рамкой и подписями из текста.

Что НЕ делает: титульный лист по бланку кафедры, оглавление, поля формул,
автоматические подписи (подпись берётся из markdown как есть — если её нет,
приёмка это покажет).

Запуск:
    python3 -m gost.build draft.md report.docx
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt

FONT = "Times New Roman"
SIZE = Pt(14)
LINE = 1.5
INDENT = Cm(1.25)
MARGINS_MM = {"left": 30, "right": 15, "top": 20, "bottom": 20}

STRUCTURAL = {
    "содержание",
    "введение",
    "заключение",
    "список использованных источников",
    "список литературы",
    "реферат",
    "приложение",
}

TABLE_ROW = re.compile(r"^\|(.+)\|$")
SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def _set_fonts(style, name: str) -> None:
    """Шрифт для всех письменностей: иначе Word подставит свой для кириллицы."""
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)


def _page_field(paragraph) -> None:
    """Поле PAGE в колонтитуле: номер подставляет Word, а не текст."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, end):
        run._r.append(node)


def _base_styles(doc) -> None:
    normal = doc.styles["Normal"]
    _set_fonts(normal, FONT)
    normal.font.size = SIZE
    fmt = normal.paragraph_format
    fmt.line_spacing = LINE
    fmt.first_line_indent = INDENT
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for level, after in ((1, 6), (2, 6)):
        style = doc.styles[f"Heading {level}"]
        _set_fonts(style, FONT)
        style.font.size = SIZE
        style.font.bold = True
        style.font.italic = False
        style.font.color.rgb = None
        pfmt = style.paragraph_format
        pfmt.first_line_indent = INDENT
        pfmt.space_before = Pt(12 if level == 1 else 6)
        pfmt.space_after = Pt(after)
        pfmt.line_spacing = LINE
        pfmt.keep_with_next = True
        pfmt.alignment = WD_ALIGN_PARAGRAPH.LEFT


def _page_setup(doc) -> None:
    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.left_margin = Mm(MARGINS_MM["left"])
    section.right_margin = Mm(MARGINS_MM["right"])
    section.top_margin = Mm(MARGINS_MM["top"])
    section.bottom_margin = Mm(MARGINS_MM["bottom"])
    # номер на титульном листе не ставится, но в нумерацию он входит
    section.different_first_page_header_footer = True
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.first_line_indent = Cm(0)
    _page_field(footer)


def _front_matter(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """YAML-шапка `--- ключ: значение ---` в начале файла (как в скилле)."""
    if not lines or lines[0].strip() != "---":
        return {}, lines
    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return meta, lines[i + 1 :]
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return {}, lines


def _title_page(doc, meta: dict[str, str]) -> None:
    if not meta:
        return

    def centered(text: str, *, bold: bool = False, before: int = 0, after: int = 0) -> None:
        p = doc.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        if bold:
            for run in p.runs:
                run.bold = True

    centered(meta.get("department", ""), after=12)
    centered(meta.get("title", ""), bold=True, before=24, after=12)
    if meta.get("lab_number"):
        centered(
            f"ОТЧЁТ по лабораторной работе № {meta['lab_number']}"
            + (f" «{meta['lab_title']}»" if meta.get("lab_title") else ""),
            after=12,
        )
    if meta.get("discipline"):
        centered(f"по дисциплине «{meta['discipline']}»", after=24)
    if meta.get("group"):
        centered(f"Группа {meta['group']}", after=12)

    right = doc.add_paragraph()
    right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    right.paragraph_format.first_line_indent = Cm(0)
    right.paragraph_format.space_before = Pt(24)
    if meta.get("student_name"):
        right.add_run(f"Выполнил: {meta['student_name']}\n")
    if meta.get("teacher_name"):
        right.add_run(f"Проверил: {meta['teacher_title']} {meta['teacher_name']}".strip())

    centered("2026", before=48)
    doc.paragraphs[-1].runs[0].add_break(WD_BREAK.PAGE)


def _add_table(doc, rows: list[list[str]]) -> None:
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            paragraph = table.cell(r, c).paragraphs[0]
            paragraph.text = cell
            paragraph.paragraph_format.first_line_indent = Cm(0)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if r == 0:
                for run in paragraph.runs:
                    run.bold = True
    doc.add_paragraph()


def _body(doc, lines: list[str]) -> None:
    buffer: list[str] = []
    table: list[list[str]] = []

    def flush_text() -> None:
        if buffer:
            doc.add_paragraph(" ".join(buffer).strip())
            buffer.clear()

    def flush_table() -> None:
        if table:
            _add_table(doc, table)
            table.clear()

    for raw in lines:
        line = raw.rstrip()
        if TABLE_ROW.match(line):
            if SEPARATOR.match(line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if buffer:
                flush_text()
            table.append(cells)
            continue
        flush_table()
        if not line.strip():
            flush_text()
        elif line.startswith("## "):
            flush_text()
            doc.add_paragraph(line[3:].strip(), style="Heading 2")
        elif line.startswith("# "):
            flush_text()
            title = line[2:].strip()
            if title.casefold() in STRUCTURAL:
                title = title.upper()
            doc.add_paragraph(title, style="Heading 1")
        else:
            buffer.append(line.strip())
    flush_text()
    flush_table()


def build_markdown(source: str | Path, target: str | Path) -> Path:
    """Собрать docx из markdown-файла. Возвращает путь к результату."""
    text = Path(source).read_text(encoding="utf-8")
    meta, lines = _front_matter(text.splitlines())

    doc = Document()
    _page_setup(doc)
    _base_styles(doc)
    _title_page(doc, meta)
    _body(doc, lines)

    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("использование: python3 -m gost.build draft.md report.docx", file=sys.stderr)
        return 2
    source, target = Path(argv[0]), Path(argv[1])
    if not source.exists():
        print(f"файла нет: {source}", file=sys.stderr)
        return 2
    out = build_markdown(source, target)
    print(f"собрано: {out} ({out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
