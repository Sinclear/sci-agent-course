"""Встроенная сборка и приёмка docx — на случай, когда внешнего скилла нет.

Полная приёмка курса — внешний скилл `gost-lab-report` (18 проверок, шаблон
кафедры, обновление полей). Он не входит в репозиторий, поэтому здесь есть
учебный минимум: сборка из markdown и шесть машинных проверок оформления.

Граница честная: `gost/build.py` и `gost/check.py` проверяют то, что сами же
и задают (поля, шрифт, интервал, нумерация, заголовки, подписи). Это не
замена скиллу и не «18/18» — это возможность пройти модуль 4 без него.

Импорт ленивый: `python -m gost.check` не должен тянуть сборку.
"""

from __future__ import annotations

from typing import Any

__all__ = ["NOT_CHECKED", "Check", "build_markdown", "check_document"]


def __getattr__(name: str) -> Any:
    if name in {"Check", "NOT_CHECKED", "check_document"}:
        from . import check

        return getattr(check, name)
    if name == "build_markdown":
        from .build import build_markdown

        return build_markdown
    raise AttributeError(f"module 'gost' has no attribute {name!r}")
