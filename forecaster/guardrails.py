"""Safety layer (Checkpoint 6.1): event freeze, confidence caps, analyst-criteria checks,
human-review triggers and the batch circuit breaker. Everything here is deterministic."""
from __future__ import annotations

from datetime import date
from typing import Iterable, List, Optional, Tuple

from .config import ForecasterConfig
from .memory import Criterion
from .models import Scorecard, Status

FREEZE_EVENTS = {"covenant_waiver", "restatement", "downgrade", "merger", "default", "bankruptcy"}


def is_stale(latest_filed: str, as_of: str, max_age_days: int) -> bool:
    return (date.fromisoformat(as_of) - date.fromisoformat(latest_filed)).days > max_age_days


def confidence(status: str, mean_score: float, fallback_used: bool, rounds: int, ambiguous: bool) -> str:
    """Caps: low if not a clean result; never 'high' after fallback, revision or an ambiguous interpretation."""
    if status in (Status.PRESCREEN_FAIL.value, Status.CANDIDATE.value):
        return "n/a"
    if status in (Status.UNCERTAIN.value, Status.INSUFFICIENT.value):
        return "low"
    if fallback_used or rounds > 0 or ambiguous:
        return "medium"
    return "high" if mean_score >= 0.95 else "medium"


def check_feedback(criteria: Iterable[Criterion], texts: Iterable[str]) -> List[Tuple[Criterion, str]]:
    """A criterion fires only if its keywords appear in the company's *current* filing text."""
    hits: List[Tuple[Criterion, str]] = []
    corpus = " ".join(texts).lower()
    for c in criteria:
        found = [k for k in c.keywords if k.lower() in corpus]
        if found:
            hits.append((c, found[0]))
    return hits


def circuit_breaker(cards: List[Scorecard], cfg: ForecasterConfig) -> Optional[str]:
    """Return a reason string if the batch should be treated as systemically unreliable, else None."""
    if len(cards) < cfg.breaker_min_companies:
        return None
    analyzed = [c for c in cards if c.status in (Status.INCLUDE.value, Status.EXCLUDE.value, Status.UNCERTAIN.value)]
    failures = [c for c in cards if c.skip_reason in ("tool_failure", "budget")]
    if len(failures) / len(cards) > cfg.breaker_failure_rate:
        return f"{len(failures)} of {len(cards)} companies failed on tools: data source looks unavailable"
    vetoes = [c for c in analyzed if c.status == Status.EXCLUDE.value and any(f.startswith("veto:") for f in c.flags)]
    if analyzed and len(vetoes) / len(analyzed) > cfg.breaker_veto_rate:
        return f"{len(vetoes)} of {len(analyzed)} analyzed companies hit hard vetoes: check the data before trusting results"
    return None
