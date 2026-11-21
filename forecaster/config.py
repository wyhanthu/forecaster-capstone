from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

# What a company must be shown to have before it can be called "final" (else: uncertain, human review).
DEFAULT_TOPICS = ("leverage", "coverage", "maturities", "liquidity",
                  "credit", "leases", "commitments", "derivatives", "subsequent", "basis")
DEFAULT_CALCS = ("leverage_ltm", "coverage_q", "current_ratio", "liquidity_cover", "liquidity_cover_adj", "covenant_headroom")
# Live filings state maturities and lease schedules in TABLES, not sentences, and rarely give the two-year figures the
# note-based ratios need. Those stay optional (used and flagged when found, never required) so a clean company can be final.
# Leverage, coverage and the other numbers come from XBRL (each carries its fact id), so no sentence has to confirm them. What only the
# text can give is the liquidity discussion and the credit-agreement note; events after the quarter come from the 8-K node.
LIVE_TOPICS = ()                      # nothing in the text is REQUIRED for a decision ...
LIVE_SOUGHT = ("liquidity", "credit")  # ... but these are looked for, and quoted in the memo when found
LIVE_CALCS = ("leverage_ltm", "coverage_q", "current_ratio", "liquidity_cover")


@dataclass
class ForecasterConfig:
    """All tunable limits in one place (Checkpoints 3.1, 4.1, 5.1, 6.1)."""

    # --- screening question -------------------------------------------------
    as_of: str = "2026-11-01"
    horizon_years: int = 2          # "Y" years until the ABS matures
    top_n: int = 8                  # "X" obligors in the final shortlist
    max_filing_age_days: int = 150  # older latest filing => stale => insufficient data

    # --- stage 1: programmatic pre-screen (no LLM, no retrieval) -------------
    max_leverage: float = 7.0
    min_coverage: float = 1.5
    min_current_ratio: float = 0.7

    # --- retrieval (Checkpoint 3.1): top 5 candidates -> re-rank -> 2-3 cited
    retrieve_k: int = 5
    keep_min: int = 2
    keep_max: int = 3
    mmr_lambda: float = 0.7
    react_max_steps: int = 3        # hybrid -> keyword -> full section, then hard stop

    # --- budgets and retries (Checkpoint 6.1) -------------------------------
    max_tool_calls_per_company: int = 120
    tool_retries: int = 1           # retry once, then report insufficient data
    max_revision_rounds: int = 2

    # --- tree of thought (Checkpoints 4.1 / 5.1) ----------------------------
    tot_enabled: bool = True
    beam_width: int = 3
    branching: int = 3
    max_depth: int = 2
    allow_escalation: bool = True   # once: beam 5 / depth 3 for material companies
    escalation_beam_width: int = 5
    escalation_min_ebitda: float = 400.0   # $ millions; "material company"

    # --- evidence scan
    scan_risk_factors: bool = True   # rule-scan Item 1A for going-concern / covenant words (off for live filings: boilerplate)

    # --- LLM layer (all optional; deterministic fallback when disabled or over budget)
    llm_max_calls_per_company: int = 20
    llm_max_steps: int = 5           # max Thought -> Action -> Observation rounds in the LLM retriever
    react_safety_net: bool = True    # topics still missing after the loop go through the rule-based fallback chain
    llm_context_chars: int = 6000    # filing text handed to the model per read
    shock_drop: float = 0.25         # quarter-on-quarter coverage drop that makes a company 'ambiguous'

    # --- decision rules ------------------------------------------------------
    min_composite: float = 0.15
    valid_threshold: float = 0.8     # Validator: equal-weight mean of the three scores
    min_liquidity_cover: float = 1.0
    covenant_headroom_min: float = 0.15  # below this the covenant is 'tight' (analyst review); below 0 it is breached

    # --- portfolio guardrails ------------------------------------------------
    sector_cap_frac: float = 0.4
    industry_cap_frac: float = 0.0       # max share per industry (Nasdaq.com taxonomy) (live runs: 0.15); 0 = no industry cap
    industry_file: str = ""              # CSV(s) with the Nasdaq.com industry/sector map; live runs default to the bundled file, offline samples use none
    shared_driver_cap: int = 2           # max names per sponsor
    maturity_year_cap_frac: float = 0.5  # max share of the shortlist with the same maturity wall year
    wall_share_threshold: float = 0.15   # a year holding >= 15% of debt is a "wall"

    # --- circuit breaker -----------------------------------------------------
    breaker_min_companies: int = 6
    breaker_veto_rate: float = 0.5
    breaker_failure_rate: float = 0.5

    # --- rating target (empty = no rating gate). Categories: AA_or_better, A, BBB_plus, BBB, BB, B, CCC (see rating.py)
    target_ratings: Tuple[str, ...] = ()
    category_quota: Dict[str, float] = field(default_factory=dict)  # share of the stage-2 reading pool per rating category
    min_ltm_ebitda: float = 0.0          # $ millions, last four quarters; 0 = no size floor (opt-in with --min-ebitda)
    min_obligor_leverage: float = 0.0   # CLO sets 0.5: below this Debt/EBITDA there is no coupon to earn. RegCap needs none
    rating_overrides: Dict[str, str] = field(default_factory=dict)   # ticker -> category, from a ratings file

    # --- required evidence (live filings use LIVE_TOPICS / LIVE_CALCS)
    keyword_hits_need_review: bool = True     # live: keyword hits in the notes are mostly boilerplate: shown in the memo, not queued
    rule_adverse_blocks_stable: bool = True   # live: a keyword hit alone does not contradict "stable" (the LLM and the numbers judge)
    note_figures_need_review: bool = True     # live: absent covenant / lease / commitment figures are normal: flagged, not queued
    sought_topics: Tuple[str, ...] = ()       # looked for by the retriever even when not required (live runs)
    review_min_composite: float = 0.0         # an 'uncertain' name scoring below this cannot reach the list: no review (live: 0.6)
    required_topics: Tuple[str, ...] = DEFAULT_TOPICS
    required_calcs: Tuple[str, ...] = DEFAULT_CALCS

    @property
    def required_items(self) -> Tuple[str, ...]:
        return tuple(f"fact:{t}" for t in self.required_topics) + tuple(f"calc:{r}" for r in self.required_calcs)

    # --- runtime -------------------------------------------------------------
    workers: int = 4
    use_langgraph: bool = True      # run the company workflow on LangGraph; falls back to the in-house graph if not installed
    state_dir: str = "forecaster_state"
