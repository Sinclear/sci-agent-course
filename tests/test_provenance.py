"""Тесты проверки цитат и журнала утверждений.

Здесь же — главный научный тест: подмена цифры в цитате обязана
проваливать проверку. Если однажды он позеленеет на подмене,
курс перестал работать.
"""

from __future__ import annotations

from sci_harness.provenance import Claim, Journal, find_quote

SOURCE = """Таблица 2. Энергопотребление стенда
В простое процессор потребляет 6 Вт, под нагрузкой — 65 Вт.
Температура при этом не превышает 78 °C.
Методика: измерение ваттметром, шаг 1 с, 10 повторов.
"""


def test_exact_quote_found():
    match = find_quote("процессор потребляет 6 Вт", SOURCE)
    assert match.found and match.score > 0.95


def test_typographic_differences_do_not_break_check():
    # модель перепечатала «65 Вт» с неразрывным пробелом и длинным тире
    match = find_quote("под нагрузкой — 65\u00a0Вт", SOURCE)
    assert match.found


def test_changed_number_is_rejected():
    match = find_quote("под нагрузкой — 85 Вт", SOURCE)
    assert not match.found


def test_fabricated_quote_is_rejected():
    match = find_quote("процессор потребляет 4 Вт благодаря новому техпроцессу", SOURCE)
    assert not match.found


def test_reordered_clause_scores_lower():
    faithful = find_quote("Температура не превышает 78 °C", SOURCE)
    reordered = find_quote("78 °C — предел температуры в стенде", SOURCE)
    assert faithful.score > reordered.score


def test_empty_inputs_are_not_verified():
    assert not find_quote("", SOURCE).found
    assert not find_quote("что-то", "").found


def test_journal_roundtrip(tmp_path):
    journal = Journal(tmp_path / ".sci" / "claims.jsonl")
    claim = Claim(text="6 Вт в простое", source="tab2.txt", locator="таблица 2", quote="6 Вт", verified=True)
    journal.add(claim)
    loaded = journal.claims()
    assert len(loaded) == 1
    assert loaded[0].id == claim.id
    assert loaded[0].locator == "таблица 2"


def test_journal_stats_count_unsourced(tmp_path):
    journal = Journal(tmp_path / "claims.jsonl")
    journal.add(Claim(text="a", source="s", verified=True))
    journal.add(Claim(text="b", source="s", verified=False))
    assert journal.stats() == {"claims": 2, "verified": 1, "unsourced_share": 0.5}


def test_journal_survives_broken_line(tmp_path):
    path = tmp_path / "claims.jsonl"
    path.write_text('{"text": "ok", "source": "s"}\nне json\n', encoding="utf-8")
    assert len(Journal(path).claims()) == 1


def test_claim_id_is_stable_and_short():
    a = Claim(text="t", source="s", quote="q")
    b = Claim(text="t", source="s", quote="q")
    assert a.id == b.id and len(a.id) == 10
