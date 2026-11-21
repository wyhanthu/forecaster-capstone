from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .config import ForecasterConfig
from .ratios import Ratio


def lin(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def composite(ratios: Dict[str, Ratio], coverage_series: List[Ratio], interpretation: str) -> Tuple[float, Dict[str, float]]:
    """Transparent 0-1 score: mean of leverage, coverage and liquidity sub-scores, minus a deterioration penalty.

    For a cited one-time setback the coverage sub-score uses the 4-quarter average instead of the latest quarter.
    """
    cov = ratios["coverage_q"].value
    if interpretation == "setback" and len(coverage_series) >= 4:
        cov = sum(r.value for r in coverage_series[-4:]) / 4
    parts = {
        "leverage": lin(7.0 - ratios["leverage_ltm"].value, 0.0, 6.0),
        "coverage": lin(cov, 1.5, 10.0),
        # once the 10-Q notes are read, liquidity is judged after leases, commitments, litigation and rate shock
        "liquidity": lin(ratios.get("liquidity_cover_adj", ratios["liquidity_cover"]).value, 0.5, 4.0),
    }
    score = sum(parts.values()) / 3.0
    hr = ratios.get("covenant_headroom")
    if hr is not None and hr.value < 0.15:
        score -= 0.05                 # a tight covenant leaves little room for a bad quarter
    if interpretation == "deterioration":
        score -= 0.1
    return max(0.0, round(score, 4)), parts


def wall_year(maturities: Dict[int, float], as_of_year: int, horizon: int, threshold: float) -> Optional[int]:
    total = sum(maturities.values())
    if total <= 0:
        return None
    best: Optional[Tuple[float, int]] = None
    for y in range(as_of_year, as_of_year + horizon + 1):
        share = maturities.get(y, 0.0) / total
        if share >= threshold and (best is None or share > best[0]):
            best = (share, y)
    return best[1] if best else None
