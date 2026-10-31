"""Прогон evals: precision/recall детектора цитат на 15 размеченных кейсах.

Запуск:  python3 -m evals.run_evals

Смысл не в том, чтобы получить 1.00. Смысл — иметь число, которое
меняется, когда вы меняете порог, добавляете проверку чисел или
подкручиваете нормализацию. Без такого числа любое улучшение
«на глаз» — самообман.
"""

from __future__ import annotations

import sys

from evals.cases import CASES, SOURCES
from sci_harness.provenance import find_quote


def main() -> int:
    tp = fp = fn = tn = 0
    print(f"{'id':>3} {'ож':>3} {'факт':>5} {'score':>6}  причина")
    print("-" * 78)
    failures: list[str] = []
    for case in CASES:
        match = find_quote(case.quote, SOURCES[case.source])
        got = match.found
        if got and case.expect_found:
            tp += 1
        elif got and not case.expect_found:
            fp += 1
            failures.append(f"кейс {case.id}: принял ложную цитату ({case.why})")
        elif not got and case.expect_found:
            fn += 1
            failures.append(f"кейс {case.id}: отверг верную цитату ({case.why})")
        else:
            tn += 1
        mark = "ok " if got == case.expect_found else "ПЛОХО"
        print(
            f"{case.id:>3} {str(case.expect_found):>5} {str(got):>5} "
            f"{match.score:>6.2f}  {mark} {match.reason}"
        )

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / len(CASES)
    print("-" * 78)
    print(f"принято верных: {tp}, принято ложных: {fp}, отвергнуто верных: {fn}, отвергнуто ложных: {tn}")
    print(f"precision={precision:.3f}  recall={recall:.3f}  accuracy={accuracy:.3f}")
    if failures:
        print("\nРазбор:")
        for line in failures:
            print(f"  - {line}")
    else:
        print("\nВсе 15 кейсов совпали с разметкой.")
    return 0 if accuracy == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
