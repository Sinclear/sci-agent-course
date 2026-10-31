"""Тесты песочницы и правки по образцу."""

from __future__ import annotations

import pytest

from sci_harness.tools import ToolError, Workspace, build_registry, specs


@pytest.fixture()
def ws(tmp_path):
    return Workspace(tmp_path)


def test_write_then_read_with_line_numbers(ws):
    ws.write_file("notes/a.md", "первая\nвторая\nтретья")
    out = ws.read_file("notes/a.md")
    assert "1\tпервая" in out
    assert "из 3" in out


def test_read_honours_limit_and_reports_remainder(ws):
    ws.write_file("big.txt", "\n".join(str(i) for i in range(50)))
    out = ws.read_file("big.txt", limit=10)
    assert "строки 1-10 из 50" in out
    assert "ещё 40 строк" in out


def test_rejects_absolute_path(ws):
    with pytest.raises(ToolError, match="абсолютные пути"):
        ws.read_file("/etc/passwd")


def test_rejects_traversal(ws):
    with pytest.raises(ToolError, match="вне рабочей папки"):
        ws.write_file("../escape.txt", "x")


def test_missing_file_is_an_error_not_empty_string(ws):
    with pytest.raises(ToolError, match="файла нет"):
        ws.read_file("nope.md")


def test_edit_requires_unique_pattern(ws):
    ws.write_file("a.py", "x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="встречается 2 раз"):
        ws.edit_file("a.py", "x = 1", "x = 2")


def test_edit_replaces_exactly_one(ws):
    ws.write_file("a.py", "def f():\n    return 1\n")
    ws.edit_file("a.py", "return 1", "return 2")
    assert "return 2" in ws.read_file("a.py")


def test_edit_with_absent_pattern_points_to_read(ws):
    ws.write_file("a.py", "alpha\n")
    with pytest.raises(ToolError, match="прочитай файл"):
        ws.edit_file("a.py", "beta", "gamma")


def test_truncate_keeps_head_and_tail(ws):
    text = "НАЧАЛО" + "x" * 30_000 + "КОНЕЦ"
    out = ws.truncate(text, keep_chars=2_000)
    assert out.startswith("НАЧАЛО")
    assert out.endswith("КОНЕЦ")
    assert "вырезано" in out


def test_pdf_extract_without_poppler_is_loud(ws, monkeypatch):
    monkeypatch.setattr("sci_harness.tools.pdftotext_available", lambda: False)
    with pytest.raises(ToolError, match="pdftotext"):
        ws.write_file("a.pdf", "%PDF-1.4")
        build_registry(ws)["pdf_extract"].fn("a.pdf")


def test_specs_hidden_are_filtered(ws):
    registry = build_registry(ws)
    only_read = {s["function"]["name"] for s in specs(registry, only_read_only=True)}
    assert "read_file" in only_read
    assert "write_file" not in only_read


def test_every_tool_has_schema(ws):
    for tool in build_registry(ws).values():
        assert tool.description, tool.name
        assert tool.parameters["type"] == "object"
