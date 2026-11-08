"""Capstone: обзор литературы из папки источников → проверяемый отчёт.

Запуск:
    python3 capstone/mini_review.py --sources examples/sources --out review.md
    python3 capstone/mini_review.py --sources ~/storage/downloads/papers --live --docx

Что делает:
1. Собирает тексты (.txt/.md напрямую, .pdf через pdftotext постранично).
2. Предлагает утверждения: в живом режиме — модель, в офлайн — эвристика
   «предложение с числом» (базовая линия, с которой надо сравнивать модель).
3. Проверяет каждое утверждение дословной цитатой по тексту источника.
4. Пишет отчёт: подтверждённое, отклонённое, метрики.
5. По флагу --docx собирает ГОСТ-документ скиллом gost-lab-report.

Честное ограничение офлайн-режима: эвристика берёт предложение целиком,
поэтому её цитаты подтверждаются по построению. Это проверка пайплайна,
а не модели. Для сравнения с моделью нужен --live.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sci_harness.llm import Client  # noqa: E402
from sci_harness.provenance import Claim, find_quote  # noqa: E402

SKILL = Path.home() / ".pi/agent/skills/gost-lab-report"
VENV_PY = Path.home() / ".local/share/gost-docx-venv/bin/python"
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![\d.,])")


@dataclass
class Evidence:
    name: str
    text: str


def read_sources(folder: Path) -> list[Evidence]:
    out: list[Evidence] = []
    for path in sorted(folder.iterdir()):
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            out.append(Evidence(path.name, path.read_text(encoding="utf-8")))
        elif suffix == ".pdf":
            text = _pdf_text(path)
            if text is None:
                continue
            if not text.strip():
                print(f"  ! {path.name}: текст не извлечён, файл пропущен", file=sys.stderr)
                continue
            out.append(Evidence(path.name, text))
        else:
            print(f"  · {path.name}: не .txt/.md/.pdf, пропущен", file=sys.stderr)
    return out


def _pdf_text(path: Path) -> str | None:
    """Текст PDF или None, если файл не читается (сообщение уже напечатано).

    Пустая строка отличается от None: сканированный PDF без текстового слоя
    — это не ошибка, а повод взять OCR (см. скилл document-analysis).
    """
    if not shutil.which("pdftotext"):
        print(f"  ! {path.name}: нет pdftotext — файл пропущен", file=sys.stderr)
        return None
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["pdftotext вернул ошибку"])[-1]
        print(f"  ! {path.name}: {reason[:120]} — файл пропущен", file=sys.stderr)
        return None
    return result.stdout


def locator_for(text: str, quote: str) -> str:
    """Локатор из смещения: в PDF-тексте это страница, в txt — номер строки.

    Проще, чем просить локатор у модели, и надёжнее: модель нумерует
    страницы по ощущению, а здесь номер вычислен из самого файла.
    """
    index = text.find(quote)
    if index < 0:  # цитату переносили между строками — ищем по началу
        index = text.find(quote[:40])
    if index < 0:
        return ""
    if "\f" in text:
        return f"с. {text[:index].count(chr(12)) + 1}"
    return f"строка {text[:index].count(chr(10)) + 1}"


def heuristic_claims(evidence: Evidence, limit: int = 6) -> list[Claim]:
    """Базовая линия: предложения с числами. Никакой модели.

    Полезно как контроль: если модель не лучше этой эвристики на вашей
    задаче, платить за неё нечем оправдать.
    """
    claims: list[Claim] = []
    for sentence in SENTENCE.split(evidence.text):
        sentence = sentence.strip().replace("\n", " ")
        if len(sentence) < 30 or not re.search(r"\d", sentence):
            continue
        claims.append(
            Claim(
                text=sentence,
                source=evidence.name,
                locator=locator_for(evidence.text, sentence),
                quote=sentence,
            )
        )
        if len(claims) >= limit:
            break
    return claims


def live_claims(client: Client, evidence: Evidence, topic: str, limit: int = 6) -> list[Claim]:
    """Модель предлагает утверждения с дословной цитатой и локатором."""
    prompt = (
        f"Тема: {topic}\n\nВыдели до {limit} проверяемых утверждений с числами.\n"
        "Верни ТОЛЬКО JSON-массив объектов: "
        '{"text": "...", "quote": "дословная цитата из текста", "locator": "страница/раздел"}.\n'
        "Цитата обязана быть дословной копией фрагмента текста.\n\n"
        f"ТЕКСТ:\n{evidence.text[:12000]}"
    )
    reply = client.chat([{"role": "user", "content": prompt}], max_tokens=1200)
    import json

    raw = reply.text.strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    return [
        Claim(
            text=str(item.get("text", "")),
            source=evidence.name,
            locator=str(item.get("locator", "")),
            quote=str(item.get("quote", "")),
        )
        for item in items
        if item.get("text") and item.get("quote")
    ]


def negative_control(claim: Claim) -> Claim:
    """Подмена всех чисел в цитате: если проверка это пропустит, она бесполезна.

    Подменяются все числа, а не одно: так контроль ловит детектор,
    который сверяет только первый/последний токен или обрезает цитату.
    """
    nums = NUMBER.findall(claim.quote)
    if not nums:
        return Claim(
            text=f"{claim.text} (пересказ)", source=claim.source, quote=claim.quote + " (так указано)"
        )

    def bump(match: re.Match[str]) -> str:
        try:
            value = float(match.group(0).replace(",", "."))
        except ValueError:
            return match.group(0)
        return f"{value * 1.15:.1f}"

    return Claim(
        text=f"{claim.text} — [контроль: подмена чисел {', '.join(dict.fromkeys(nums))}]",
        source=claim.source,
        quote=NUMBER.sub(bump, claim.quote),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    default_sources = Path(__file__).resolve().parents[1] / "examples/sources"
    parser.add_argument("--sources", default=str(default_sources), help="папка с .txt/.md/.pdf")
    parser.add_argument("--out", default="review.md")
    parser.add_argument("--topic", default="Обзор литературы")
    parser.add_argument("--live", action="store_true", help="использовать модель вместо эвристики")
    parser.add_argument("--docx", action="store_true", help="собрать ГОСТ-документ")
    args = parser.parse_args()

    folder = Path(args.sources).expanduser()
    if not folder.is_dir():
        print(f"папки нет: {folder}\nукажите --sources <папка с .txt/.md/.pdf>", file=sys.stderr)
        return 2
    evidence = [item for item in read_sources(folder) if item.text.strip()]
    if not evidence:
        print(
            f"в {folder} нет читаемого текста (.txt/.md/.pdf с извлекаемым содержимым)",
            file=sys.stderr,
        )
        return 2
    print(f"источников: {len(evidence)}: {', '.join(e.name for e in evidence)}")

    client = Client() if args.live else None
    claims: list[Claim] = []
    for item in evidence:
        try:
            proposed = live_claims(client, item, args.topic) if client else heuristic_claims(item)
        except OSError as exc:  # URLError — подкласс OSError: нет ключа, сети или сервера
            print(
                f"модель недоступна: {exc}\n"
                "Проверьте ключ и сеть либо запустите без --live (офлайн-эвристика).",
                file=sys.stderr,
            )
            return 3
        for claim in proposed:
            if not claim.locator:
                claim.locator = locator_for(item.text, claim.quote)
            match = find_quote(claim.quote, item.text)
            claim.verified, claim.score = match.found, match.score
            claims.append(claim)
        # контроль: та же цитата с подменённым числом обязана быть отвергнута
        for claim in list(proposed):
            control = negative_control(claim)
            match = find_quote(control.quote, item.text)
            control.verified, control.score = match.found, match.score
            claims.append(control)

    verified = [c for c in claims if c.verified]
    rejected = [c for c in claims if not c.verified]
    shared = round(len(rejected) / len(claims), 3) if claims else 0.0

    lines = [
        f"# {args.topic}",
        "",
        f"Источников: {len(evidence)}. Утверждений предложено: {len(claims)}.",
        "",
        "## Проверенные утверждения",
        "",
        "| id | утверждение | источник | локатор | совпадение |",
        "|---|---|---|---|---|",
    ]
    for claim in verified:
        lines.append(
            f"| {claim.id} | {claim.text} | {claim.source} | {claim.locator or '—'} | {claim.score:.2f} |"
        )
    lines += ["", "## Отклонённые (не прошли проверку цитаты)", ""]
    if rejected:
        for claim in rejected:
            lines.append(f"- {claim.text} — источник {claim.source}, совпадение {claim.score:.2f}")
    else:
        lines.append("- нет")
    lines += [
        "",
        "## Метрики",
        "",
        f"- утверждений: {len(claims)}",
        f"- подтверждено: {len(verified)}",
        f"- отклонено: {len(rejected)} ({shared:.1%})",
        "- отклонённые — это в первую очередь контрольные подмены чисел:",
        "  детектор обязан их ловить, иначе он подтвердит что угодно.",
        "",
        "## Как читать",
        "",
        "Каждая строка таблицы — утверждение с дословной цитатой из файла.",
        "Ссылку можно проверить, открыв источник на указанном месте.",
        "Это и есть разница между «агент написал обзор» и «агент собрал",
        "проверяемый черновик обзора».",
        "",
    ]
    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"отчёт: {out} ({len(verified)} подтверждено, {len(rejected)} отклонено)")

    if args.docx:
        if not (SKILL.exists() and VENV_PY.exists() and shutil.which("pandoc")):
            print("docx пропущен: нет скилла/venv/pandoc — см. docs/05-приёмка-гост.md")
            return 0
        docx = out.with_suffix(".docx")
        build = subprocess.run(
            [str(VENV_PY), str(SKILL / "scripts/build.py"), str(out), str(docx)],
            capture_output=True,
            text=True,
            check=False,
        )
        print(build.stdout[-300:] or build.stderr[-300:])
        verify = subprocess.run(
            [str(VENV_PY), str(SKILL / "scripts/verify.py"), str(docx)],
            capture_output=True,
            text=True,
            check=False,
        )
        tail = verify.stdout.strip().splitlines()[-1] if verify.stdout.strip() else "нет вывода"
        print(f"verify: {tail} (код {verify.returncode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
