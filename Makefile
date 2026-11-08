# Если рядом есть .venv (см. make setup), работаем его интерпретатором.
VENV_PY := $(wildcard .venv/bin/python)
PY ?= $(if $(VENV_PY),$(VENV_PY),python3)

.PHONY: help setup test lessons evals capstone check docx clean

help:
	@echo "make setup     — создать .venv и поставить pytest, ruff, mypy"
	@echo "make test      — тесты (pytest, без ключей и сети)"
	@echo "make lessons   — прогнать все уроки 00…06"
	@echo "make evals     — 15 кейсов, precision/recall"
	@echo "make capstone  — обзор литературы из examples/sources"
	@echo "make docx      — capstone + сборка ГОСТ-docx + verify"
	@echo "make check     — ruff + mypy (+ pyright, если установлен)"

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q pytest ruff mypy
	@echo "setup: готово — make test подхватит .venv"

test:
	$(PY) -m pytest -q

lessons:
	@fail=0; for f in examples/0*.py; do \
	  printf "%-32s" "$$f"; \
	  if $(PY) "$$f" >/dev/null 2>&1; then echo OK; else echo FAIL; fail=1; fi; \
	done; test $$fail -eq 0 || (echo "есть упавшие уроки"; exit 1)

evals:
	$(PY) -m evals.run_evals

capstone:
	$(PY) capstone/mini_review.py --out review.md

docx:
	$(PY) capstone/mini_review.py --out review.md --docx

check:
	$(PY) -m ruff check .
	$(PY) -m mypy sci_harness capstone evals
	@if command -v pyright >/dev/null 2>&1; then pyright; else echo "pyright не установлен — шаг пропущен"; fi

clean:  ## убрать артефакты прогонов и кеши
	rm -f review.md review.docx trace.jsonl
	rm -rf .pytest_cache .ruff_cache .mypy_cache .sci
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "clean: готово"
