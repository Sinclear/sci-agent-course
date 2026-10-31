"""Журнал провенанса: каждое утверждение помнит, откуда оно взялось.

Идея одна и держит половину курса: **факт живёт вне транскрипта**.
Модель может «забыть» страницу после сжатия контекста — журнал нельзя.
Поэтому утверждение записывается в файл сразу, вместе с дословной
цитатой и локатором, и проверяется по тексту источника, а не по памяти.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

_WS = re.compile(r"\s+")
_DASHES = str.maketrans({"—": "-", "–": "-", "−": "-", "«": '"', "»": '"', "“": '"', "”": '"', "№": "N"})


def normalize(text: str) -> str:
    """Приводим текст к сравнимому виду.

    Это не косметика: PDF даёт «5 - 7 Вт» с неразрывным пробелом и
    мягким переносом, а модель цитирует «5–7 Вт». Без нормализации
    проверка цитаты провалится на типографике, а не на факте.
    """
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    return _WS.sub(" ", text).strip()


@dataclass
class Claim:
    """Одно проверяемое утверждение."""

    text: str
    source: str
    locator: str = ""
    quote: str = ""
    verified: bool = False
    score: float = 0.0
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            digest = hashlib.sha256(f"{self.source}|{self.quote}|{self.text}".encode()).hexdigest()
            self.id = digest[:10]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class Match:
    found: bool
    score: float
    snippet: str
    reason: str = ""


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> list[str]:
    """Числа в порядке появления — единственное, что нельзя «примерно» цитировать."""
    return _NUM.findall(normalize(text))


def find_quote(quote: str, source_text: str, threshold: float = 0.92) -> Match:
    """Есть ли цитата в тексте источника — с допуском на разметку PDF.

    Два уровня проверки, и второй важнее первого:

    1. Похожесть текста (difflib). Ловит переставленные слова,
       выброшенные вводные обороты, разницу в тире и пробелах.
    2. Совпадение чисел. Подмена «65 Вт» на «85 Вт» меняет один символ
       из двадцати — похожесть остаётся 0.97, то есть выше любого
       разумного порога. Поэтому числа сравниваются отдельно и
       строго: не сошлось — утверждение не принимается.
    """
    if not quote.strip() or not source_text.strip():
        return Match(found=False, score=0.0, snippet="", reason="пустой ввод")
    haystack = normalize(source_text)
    needle = normalize(quote)
    window = len(needle)
    if window == 0:
        return Match(found=False, score=0.0, snippet="", reason="пустая цитата")

    if needle in haystack:
        start = haystack.index(needle)
        snippet = haystack[max(start - 20, 0) : start + window + 20]
        return Match(found=True, score=1.0, snippet=snippet, reason="дословно")

    best_score, best_start = 0.0, 0
    step = max(1, window // 16)
    for start in range(0, max(len(haystack) - window, 0) + 1, step):
        chunk = haystack[start : start + window]
        ratio = difflib.SequenceMatcher(None, needle, chunk).ratio()
        if ratio > best_score:
            best_score, best_start = ratio, start
    snippet = haystack[max(best_start - 20, 0) : best_start + window + 20]

    if numbers(needle) != numbers(haystack[best_start : best_start + window]):
        return Match(
            found=False,
            score=round(best_score, 3),
            snippet=snippet,
            reason="числа в цитате не совпадают с источником",
        )
    if best_score < threshold:
        return Match(found=False, score=round(best_score, 3), snippet=snippet, reason="текст похож слабо")
    return Match(found=True, score=round(best_score, 3), snippet=snippet, reason="близкий пересказ")


class Journal:
    """Файл `.sci/claims.jsonl`: одна строка — одно утверждение.

    Формат JSONL выбран нарочно: его видно глазами, он дописывается
    атомарно построчно и не ломается при падении посреди записи.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, claim: Claim) -> Claim:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(claim.to_json() + "\n")
        return claim

    def claims(self) -> list[Claim]:
        if not self.path.exists():
            return []
        out: list[Claim] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Claim(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # битая строка не должна ронять чтение журнала
        return out

    def unverified(self) -> list[Claim]:
        return [c for c in self.claims() if not c.verified]

    def stats(self) -> dict[str, float]:
        claims = self.claims()
        total = len(claims)
        ok = sum(1 for c in claims if c.verified)
        return {
            "claims": total,
            "verified": ok,
            "unsourced_share": 0.0 if total == 0 else round((total - ok) / total, 3),
        }
