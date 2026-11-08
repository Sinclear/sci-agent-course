"""Практикум: научный агент на базе pi.

Код намеренно без внешних зависимостей: всё, что нужно для цикла
агента и проверки утверждений, — это стандартная библиотека.
Внешние пакеты появляются только на шаге сборки документа.
"""

from .context import Budget, BudgetExceeded, ContextOverflow, lost_facts, pinned_claims, rule_summarizer
from .llm import Client, Reply, ToolCall, Usage, estimate_tokens
from .loop import Runner, RunResult, cite_tool, write_run
from .provenance import Claim, Journal, find_quote, locator_for, numbers
from .tools import Tool, ToolError, Workspace, build_registry, clip_result, specs

__all__ = [
    "Budget",
    "BudgetExceeded",
    "Claim",
    "Client",
    "ContextOverflow",
    "Journal",
    "Reply",
    "RunResult",
    "Runner",
    "Tool",
    "ToolCall",
    "ToolError",
    "Usage",
    "Workspace",
    "build_registry",
    "cite_tool",
    "clip_result",
    "estimate_tokens",
    "find_quote",
    "locator_for",
    "lost_facts",
    "numbers",
    "pinned_claims",
    "rule_summarizer",
    "specs",
    "write_run",
]
