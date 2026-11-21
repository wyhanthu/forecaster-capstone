"""Outer loop: rank survivors and build a diversified shortlist (Checkpoint 1.1 funnel, 6.1 caps)."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import ForecasterConfig
from .guardrails import circuit_breaker
from .models import Scorecard, Status
from .workflow import CompanyResult


@dataclass
class PortfolioResult:
    shortlist: List[Scorecard] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)     # (ticker, reason)
    review_queue: List[Scorecard] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    breaker: Optional[str] = None
    results: Dict[str, CompanyResult] = field(default_factory=dict)

    @property
    def tickers(self) -> List[str]:
        return [c.ticker for c in self.shortlist]


def build_portfolio(results: List[CompanyResult], cfg: ForecasterConfig) -> PortfolioResult:
    cards = [r.scorecard for r in results]
    out = PortfolioResult(results={r.scorecard.ticker: r for r in results})
    out.counts = dict(Counter(c.status for c in cards))
    out.breaker = circuit_breaker(cards, cfg)

    caps = dict(sector=max(1, math.floor(cfg.sector_cap_frac * cfg.top_n)),
                industry=max(1, math.floor(cfg.industry_cap_frac * cfg.top_n)) if cfg.industry_cap_frac > 0 else 10 ** 9,
                sponsor=cfg.shared_driver_cap,
                wall=max(1, math.floor(cfg.maturity_year_cap_frac * cfg.top_n)))
    used = dict(sector=Counter(), industry=Counter(), sponsor=Counter(), wall=Counter())
    ind = lambda c: c.industry or c.sector                 # a company without a SIC group falls back to its sector

    # a weakness the LLM attributes to industry-wide conditions impairs that INDUSTRY (the industry, not the whole sector) and only when the company's own filing says the industry is weak: one name
    impaired = {ind(c): c.ticker for c in cards if c.scope == "industry" and c.industry_evidenced and c.interpretation == "deterioration"
                and c.status in (Status.INCLUDE.value, Status.UNCERTAIN.value)}
    eligible = sorted((c for c in cards if c.status == Status.INCLUDE.value), key=lambda c: (-c.composite, c.ticker))
    for c in eligible:
        if len(out.shortlist) >= cfg.top_n:
            out.skipped.append((c.ticker, "shortlist full"))
            continue
        if ind(c) in impaired and used["industry"][ind(c)] >= 1:
            out.skipped.append((c.ticker, f"industry {ind(c)} flagged industry-wide weakness via {impaired[ind(c)]} (max 1)"))
            continue
        if used["industry"][ind(c)] >= caps["industry"]:
            out.skipped.append((c.ticker, f"industry cap ({ind(c)}: max {caps['industry']})"))
            continue
        if used["sector"][c.group_sector or c.sector] >= caps["sector"]:
            out.skipped.append((c.ticker, f"sector cap ({c.group_sector or c.sector}: max {caps['sector']})"))
            continue
        if c.sponsor and used["sponsor"][c.sponsor] >= caps["sponsor"]:
            out.skipped.append((c.ticker, f"sponsor cap ({c.sponsor}: max {caps['sponsor']})"))
            continue
        if c.wall_year and used["wall"][c.wall_year] >= caps["wall"]:
            out.skipped.append((c.ticker, f"maturity-wall cap ({c.wall_year}: max {caps['wall']})"))
            continue
        out.shortlist.append(c)
        used["sector"][c.group_sector or c.sector] += 1
        used["industry"][ind(c)] += 1
        if c.sponsor:
            used["sponsor"][c.sponsor] += 1
        if c.wall_year:
            used["wall"][c.wall_year] += 1

    for c in cards:
        if ind(c) in impaired and c.ticker != impaired[ind(c)] and c.status == Status.INCLUDE.value:
            c.review_reasons.append(f"industry {ind(c)} flagged industry-wide weakness via {impaired[ind(c)]}")
    listed = {c.ticker for c in out.shortlist}

    def worth_review(c: Scorecard) -> bool:
        """A human looks at what is on the list, and at open questions that could still put a name on it. A name that is
        not selected (or is excluded) needs nobody's attention: it stays visible under 'Not selected'."""
        if c.ticker in listed:
            return bool(c.review_reasons)
        if c.status == Status.UNCERTAIN.value:
            return c.composite >= cfg.review_min_composite or bool(set(c.review_reasons) - {"evidence incomplete"})
        return bool(c.review_reasons) and cfg.review_min_composite == 0.0        # offline demo keeps the old behaviour
    review = [c for c in cards if worth_review(c)]
    if out.breaker:                      # systemic problem: nothing is auto-released
        review = list({c.ticker: c for c in review + out.shortlist}.values())
    out.review_queue = sorted(review, key=lambda c: c.ticker)
    return out
