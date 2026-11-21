"""The three memory layers from Checkpoint 2.1.

* ShortTermMemory   - one company's working notebook, discarded when the run ends
* TrendLibrary      - quarterly snapshots per obligor; deterioration only shows up once quarters line up
* FeedbackMemory    - analyst review criteria; explicit, inspectable, editable, deletable
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------- short-term
TREND_MIN_DROP = 0.15           # coverage or FCF must fall at least 15% over the window to count
TREND_STRONG_COVERAGE = 8.0     # at or above this coverage a decline is not a credit signal
TREND_WEAK_LEVERAGE = 1.5       # leverage below this is not worth flagging as "rising"
TREND_MIN_LEV_RISE = 0.5        # and it must have risen by at least half a turn

@dataclass
class ShortTermMemory:
    figures: Dict[str, float] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)

    def clear(self) -> None:
        self.figures.clear()
        self.flags.clear()
        self.trace.clear()


# ---------------------------------------------------------------- trend library
@dataclass
class TrendSignal:
    coverage_decline_2q: bool = False
    leverage_rise_3y: bool = False
    fcf_decline_3y: bool = False
    details: List[str] = field(default_factory=list)

    @property
    def multi_period_decline(self) -> bool:
        return self.coverage_decline_2q or self.leverage_rise_3y or self.fcf_decline_3y


class TrendLibrary:
    """Persisted as JSON: {cik: {"coverage": {period: x}, "leverage": {FYyyyy: x}, "fcf": {FYyyyy: x}}}."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.data: Dict[str, Dict[str, Dict[str, float]]] = {}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text())

    def ingest(self, cik: str, series: dict) -> None:
        with self._lock:
            rec = self.data.setdefault(cik, {"coverage": {}, "leverage": {}, "fcf": {}})
            for r in series.get("coverage_q", []):
                rec["coverage"][r.params["period"]] = round(r.value, 4)
            for r in series.get("leverage_fy", []):
                rec["leverage"][f"FY{r.params['fy']}"] = round(r.value, 4)
            for r in series.get("fcf_fy", []):
                rec["fcf"][f"FY{r.params['fy']}"] = round(r.value, 4)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    def signal(self, cik: str) -> TrendSignal:                      # thresholds: module constants above
        with self._lock:
            rec = self.data.get(cik)
        sig = TrendSignal()
        if not rec:
            return sig
        cov = [v for _, v in sorted(rec["coverage"].items())]
        lev = [v for _, v in sorted(rec["leverage"].items())]
        fcf = [v for _, v in sorted(rec["fcf"].items())]
        # a trend counts only if it is material AND the company is not strong at the latest point: a coverage slide from 19.9x
        # to 18.8x, or leverage from 0.0x to 0.1x, is noise, not deterioration
        if len(cov) >= 3 and cov[-3] > cov[-2] > cov[-1] and cov[-1] < TREND_STRONG_COVERAGE and cov[-1] <= (1 - TREND_MIN_DROP) * cov[-3]:
            sig.coverage_decline_2q = True
            sig.details.append("interest coverage fell two consecutive quarters: " + " -> ".join(f"{v:.1f}x" for v in cov[-3:]))
        if len(lev) >= 3 and lev[-3] < lev[-2] < lev[-1] and lev[-1] >= TREND_WEAK_LEVERAGE and lev[-1] - lev[-3] >= TREND_MIN_LEV_RISE:
            sig.leverage_rise_3y = True
            sig.details.append("leverage rose three fiscal years running: " + " -> ".join(f"{v:.1f}x" for v in lev[-3:]))
        if len(fcf) >= 3 and fcf[-3] > fcf[-2] > fcf[-1] and fcf[-1] <= (1 - TREND_MIN_DROP) * fcf[-3]:
            sig.fcf_decline_3y = True
            sig.details.append("free cash flow fell three fiscal years running: " + " -> ".join(f"{v:,.0f}" for v in fcf[-3:]))
        return sig


# ---------------------------------------------------------------- analyst feedback
@dataclass
class Criterion:
    id: str
    criterion: str
    keywords: List[str]
    action: str = "review"       # "review" or "exclude"
    reason: str = ""
    created: str = ""
    active: bool = True
    lesson: str = ""          # the analyst's experience in their own words (what the model had not thought of)
    source: str = ""          # where it came from, e.g. "DRFT exclude 2026-11-19"
    sector: str = ""          # "" = applies to every sector


class FeedbackMemory:
    """Desk-specific screening policy learned from analyst rejections.

    A criterion only changes a recommendation when current filings actually contain its
    keywords (Checkpoint 2.1: "checked against current disclosures before it affects a recommendation").
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.items: Dict[str, Criterion] = {}
        if self.path and self.path.exists():
            for raw in json.loads(self.path.read_text()):
                c = Criterion(**raw)
                self.items[c.id] = c

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(c) for c in self.items.values()], indent=2))

    def add(self, criterion: str, keywords: List[str], action: str = "review", reason: str = "",
            lesson: str = "", source: str = "", sector: str = "") -> Criterion:
        """keywords may be empty: the lesson is then advisory (shown to the LLM, never matched by string)."""
        if action not in ("review", "exclude"):
            raise ValueError("action must be 'review' or 'exclude'")
        c = Criterion(uuid.uuid4().hex[:6], criterion, [k.strip() for k in keywords if k.strip()], action, reason,
                      datetime.now(timezone.utc).isoformat(timespec="seconds"), True, lesson, source, sector)
        with self._lock:
            self.items[c.id] = c
            self._save()
        return c

    def edit(self, cid: str, **changes) -> Criterion:
        with self._lock:
            c = self.items[cid]
            for k, v in changes.items():
                if v is not None and hasattr(c, k):
                    setattr(c, k, v)
            self._save()
            return c

    def delete(self, cid: str) -> bool:
        with self._lock:
            existed = self.items.pop(cid, None) is not None
            self._save()
            return existed

    def active(self) -> List[Criterion]:
        return [c for c in self.items.values() if c.active]

    def applicable(self, sector: str) -> List[Criterion]:
        return [c for c in self.active() if not c.sector or c.sector == sector]

    def list(self) -> List[Criterion]:
        return list(self.items.values())
