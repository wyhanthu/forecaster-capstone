from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Status(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    UNCERTAIN = "uncertain"
    INSUFFICIENT = "insufficient_data"
    PRESCREEN_FAIL = "prescreen_fail"
    CANDIDATE = "candidate"      # passed the financial screen; filings not read (stage 1 of a two-stage run)


SECTION_BUSINESS = "item1"
SECTION_RISK = "item1a"
SECTION_MDA = "item7"
SECTION_DEBT = "debt_footnote"
SECTION_8K = "8-K"
# 10-Q notes to the financial statements, one chunk per note (title kept as the section name)
SECTION_NOTE_CREDIT = "note_credit"            # credit facilities, covenants, revolver availability
SECTION_NOTE_LEASES = "note_leases"            # leases and arrangements outside the balance sheet
SECTION_NOTE_COMMIT = "note_commitments"       # commitments, guarantees, litigation / contingencies
SECTION_NOTE_DERIV = "note_derivatives"        # derivatives, floating-rate debt, rate sensitivity
SECTION_NOTE_SUBSEQ = "note_subsequent"        # subsequent events
SECTION_NOTE_BASIS = "note_basis"              # basis of presentation: going concern, restatement
NOTE_SECTIONS = (SECTION_DEBT, SECTION_NOTE_CREDIT, SECTION_NOTE_LEASES, SECTION_NOTE_COMMIT, SECTION_NOTE_DERIV,
                 SECTION_NOTE_SUBSEQ, SECTION_NOTE_BASIS)

SECTION_LABELS = {
    SECTION_BUSINESS: "Item 1 (Business)",
    SECTION_RISK: "Item 1A (Risk Factors)",
    SECTION_MDA: "Item 7 / MD&A (Liquidity)",
    SECTION_DEBT: "Debt & maturity footnote",
    SECTION_8K: "8-K event disclosure",
    SECTION_NOTE_CREDIT: "Note: Credit facilities & covenants",
    SECTION_NOTE_LEASES: "Note: Leases & off-balance-sheet arrangements",
    SECTION_NOTE_COMMIT: "Note: Commitments & contingencies",
    SECTION_NOTE_DERIV: "Note: Derivatives & interest-rate exposure",
    SECTION_NOTE_SUBSEQ: "Note: Subsequent events",
    SECTION_NOTE_BASIS: "Note: Basis of presentation (going concern / restatement)",
}

INTERPRETATIONS = ("setback", "deterioration", "stable")


@dataclass(frozen=True)
class Chunk:
    """A structure-aware filing chunk with the metadata the retriever filters on."""

    chunk_id: str
    cik: str
    ticker: str
    form: str
    filed: str
    section: str
    text: str
    superseded_by: Optional[str] = None

    def label(self) -> str:
        return f"{self.form} filed {self.filed}, {SECTION_LABELS.get(self.section, self.section)}"


@dataclass
class Event:
    """A post-filing 8-K style event."""

    cik: str
    ticker: str
    filed: str
    item: str
    type: str
    text: str

    @property
    def chunk_id(self) -> str:
        return f"{self.ticker.lower()}-8-k-{self.filed}-{self.item}"


@dataclass
class Claim:
    """One statement in a memo.

    kind = "fact"      -> verbatim filing sentence (quote) + chunk citation
    kind = "calc"      -> computed ratio; citations are the XBRL fact ids it used
    kind = "judgment"  -> labeled model opinion; never contains numbers
    """

    kind: str
    text: str
    topic: str = ""
    citations: List[str] = field(default_factory=list)
    quote: str = ""
    ratio: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    value: Optional[float] = None
    time_sensitive: bool = False

    @property
    def claim_id(self) -> str:
        raw = f"{self.kind}|{self.topic}|{self.text}|{sorted(self.citations)}|{self.ratio}|{sorted(self.params.items())}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]


@dataclass
class Scores:
    citation_coverage: float = 0.0
    evidence_completeness: float = 0.0
    recency: float = 0.0

    @property
    def mean(self) -> float:
        return (self.citation_coverage + self.evidence_completeness + self.recency) / 3.0


@dataclass
class Branch:
    """A node in the thought tree: one interpretation, its claims, and the Validator's verdict."""

    branch_id: str
    parent_id: Optional[str]
    depth: int
    interpretation: str
    strategy: str
    claims: List[Claim] = field(default_factory=list)
    scores: Scores = field(default_factory=Scores)
    valid: bool = False
    requirements_met: bool = False
    hard_fails: List[str] = field(default_factory=list)
    pruned_reason: str = ""
    missing_items: List[str] = field(default_factory=list)

    def cited_ids(self) -> set:
        out = set()
        for c in self.claims:
            out.update(c.citations)
        return out

    def signature(self) -> tuple:
        return (self.interpretation, frozenset(c.claim_id for c in self.claims))


def industry_of(sic) -> str:
    s = str(sic or "").strip()
    return f"SIC {s[:3]}" if len(s) >= 3 and s[:3].isdigit() else ""


@dataclass
class Scorecard:
    ticker: str
    cik: str = ""
    name: str = ""
    sector: str = ""
    industry: str = ""          # Nasdaq.com industry (industry_map.py); "SIC xxx" (first three SIC digits) when the ticker is not in the file
    group_sector: str = ""      # Nasdaq.com sector, used only for the sector concentration cap; empty = use `sector`
    industry_desc: str = ""
    sponsor: Optional[str] = None
    status: str = Status.INSUFFICIENT.value
    composite: float = 0.0
    confidence: str = "low"
    interpretation: str = ""
    scope: str = ""                 # company | industry | unknown (only for non-stable cases)
    industry_evidenced: bool = False   # the filing itself contains an industry-wide-conditions passage that backs scope == industry
    wall_year: Optional[int] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    rationale: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    review_reasons: List[str] = field(default_factory=list)
    used_tot: bool = False
    revision_rounds: int = 0
    tool_calls: int = 0
    skip_reason: str = ""
    missing: List[str] = field(default_factory=list)
    lessons: List[str] = field(default_factory=list)       # ids of desk lessons that fired on this company

    @property
    def auto_release(self) -> bool:
        return self.status == Status.INCLUDE.value and not self.review_reasons
