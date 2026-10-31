PY := python3

.PHONY: help test lessons evals capstone check docx

help:
	@echo "make test      — тесты (pytest, без ключей и сети)"
	@echo "make lessons   — прогнать все уроки 00…06"
	@echo "make evals     — 15 кейсов, precision/recall"
	@echo "make capstone  — обзор литературы из examples/sources"
	@echo "make docx      — capstone + сборка ГОСТ-docx + verify"
	@echo "make check     — ruff + mypy + pyright (если установлен check)"

test:
	$(PY) -m pytest -q

lessons:
	@for f in examples/0*.py; do printf "%-32s" "$$f"; $(PY) "$$f" >/dev/null && echo OK || echo FAIL; done

evals:
	$(PY) -m evals.run_evals

capstone:
	$(PY) capstone/mini_review.py --out review.md

docx:
	$(PY) capstone/mini_review.py --out review.md --docx

check:
	check .
