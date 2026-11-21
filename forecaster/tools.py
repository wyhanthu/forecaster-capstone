"""Read-only data source + tool registry (the MCP-style `mcp_call` pattern from the TA's Virtual Lab 5.1).

Every external read goes through ToolRegistry.mcp_call so that call budgets, retry-once and the
audit trail are enforced in one place (Checkpoint 6.1).
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Callable, Dict, List, Optional

from .audit import AuditLog
from .config import ForecasterConfig
from .models import Chunk, Event
from .retrieval import HybridIndex
from .sample_data import build_universe


class ToolError(Exception):
    """Transient tool failure (worth one retry)."""


class ToolFailure(Exception):
    """Tool failed after its retry; the caller reports insufficient data."""


class BudgetExceeded(Exception):
    pass


class OfflineEdgar:
    """Read-only EDGAR stand-in backed by the synthetic universe.

    A live client would expose the same four methods (companyfacts / submissions / filing text /
    8-K index). Nothing here can write or trade: the environment is read-only by construction.
    """

    def __init__(self, universe: Optional[Dict[str, dict]] = None):
        self.universe = universe or build_universe()

    def tickers(self) -> List[str]:
        return sorted(self.universe)

    def facts(self, ticker: str) -> dict:
        b = self.universe[ticker]
        return {k: b[k] for k in ("cik", "ticker", "name", "sector", "sponsor", "filed", "quarters", "annual", "maturities")}

    def all_chunks(self) -> List[Chunk]:
        return [c for b in self.universe.values() for c in b["chunks"]]

    def events(self, ticker: str, since: str) -> List[Event]:
        return [e for e in self.universe[ticker]["events"] if e.filed > since]


class ToolRegistry:
    def __init__(self, source: OfflineEdgar, index: HybridIndex, cfg: ForecasterConfig, audit: AuditLog,
                 inject_failures: Optional[Dict[tuple, int]] = None):
        self.source, self.index, self.cfg, self.audit = source, index, cfg, audit
        self._lock = threading.Lock()
        self._calls: Counter = Counter()
        self._fail = dict(inject_failures or {})   # {(ticker, tool): n_times_to_fail}, for tests
        self.tools: Dict[str, Callable[..., object]] = {
            "edgar_facts": lambda ticker: source.facts(ticker),
            "edgar_events": lambda ticker, since: source.events(ticker, since),
            "filing_search": lambda **kw: index.search(**kw),
            "filing_section": lambda **kw: index.full_section(**kw),
        }

    def calls(self, ticker: str) -> int:
        return self._calls[ticker]

    def mcp_call(self, ticker: str, tool: str, **kwargs):
        if tool not in self.tools:
            raise ValueError(f"unknown tool: {tool}")
        for attempt in range(1 + self.cfg.tool_retries):
            with self._lock:
                self._calls[ticker] += 1
                over = self._calls[ticker] > self.cfg.max_tool_calls_per_company
                inject = self._fail.get((ticker, tool), 0) > 0
                if inject:
                    self._fail[(ticker, tool)] -= 1
            if over:
                self.audit.log("budget_exceeded", ticker=ticker, tool=tool)
                raise BudgetExceeded(f"{ticker}: more than {self.cfg.max_tool_calls_per_company} tool calls")
            try:
                if inject:
                    raise ToolError(f"injected failure in {tool}")
                call_kwargs = dict(kwargs)
                if tool in ("edgar_facts", "edgar_events"):
                    call_kwargs["ticker"] = ticker
                out = self.tools[tool](**call_kwargs)
                self.audit.log("tool_call", ticker=ticker, tool=tool, attempt=attempt + 1, ok=True)
                return out
            except ToolError as exc:
                self.audit.log("tool_call", ticker=ticker, tool=tool, attempt=attempt + 1, ok=False, error=str(exc))
        raise ToolFailure(f"{ticker}: {tool} failed after {1 + self.cfg.tool_retries} attempts")
