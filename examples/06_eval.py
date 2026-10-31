"""Урок 6. Evals: число вместо ощущения «стало лучше».

Запуск:  python3 examples/06_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _mock import banner  # noqa: E402

from evals.run_evals import main as run_evals  # noqa: E402

banner("Прогон 15 размеченных кейсов")
code = run_evals()

banner("Что делать с этими числами")
print("1. Поменяйте порог 0.92 на 0.85 — recall вырастет, precision упадёт.")
print("   Это выбор политики, а не «баг»: для статьи важнее precision.")
print("2. Уберите проверку чисел из find_quote и запустите снова —")
print("   кейсы 4, 5, 11, 15 (подмена цифры) начнут проходить как верные.")
print("   Именно так выглядит харнес без проверки фактов: всё «работает».")
print("3. Добавьте кейс из своей области и посмотрите, что сломается.")
print("   Один хороший кейс из вашей статьи полезнее сотни чужих.")

raise SystemExit(code)
