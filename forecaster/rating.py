"""Rating categories for the RegCap / CLO target.

SEC filings carry NO credit ratings (S&P, Moody's and Fitch are not in EDGAR). So by default the category is IMPLIED from two
numbers that are in the filings: Debt / LTM EBITDA and last-four-quarter interest coverage, using round bands calibrated on
real ratings (see BANDS). It is a proxy for the financial side of a rating (it knows nothing about business risk, size or ownership), meant to
keep the screen in the right neighbourhood; it is not a rating and the report says so. Real ratings, when you have them,
override it: `--ratings-file ratings.csv` (ticker,rating; S&P/Fitch or Moody's symbols).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

CATEGORIES = ("AA_or_better", "A", "BBB_plus", "BBB", "BB", "B", "CCC")   # best to worst; A = A+/A/A-, BBB_plus = BBB+ only, BBB = BBB and BBB-
# category: (leverage must be below, LTM interest coverage must be at least)
# Calibrated 2026-11 against 225 companies with an Egan-Jones rating (17g-7 file; ratings about a year older than the filings).
# The two ratios CANNOT separate A, BBB+ and BBB (median leverage 2.2x / 2.5x / 2.7x, coverage 11.3x / 9.9x / 8.8x): exact category
# 32%, within one 61%. They do separate AA-and-better, the investment-grade core, BB and the weaker names. So an IMPLIED rating
# gates by GROUP (see GROUPS); the notch (BBB+ vs BBB-) is judged only from a real rating (--ratings-file).
BANDS: Dict[str, Tuple[float, float]] = {
    "AA_or_better": (0.5, 15.0),
    "A": (2.5, 8.0),
    "BBB_plus": (3.0, 6.0),
    "BBB": (4.0, 4.0),
    "BB": (5.0, 2.0),
    "B": (10.0, 1.0),
    "CCC": (float("inf"), 0.0),
}
MODES = {"regcap": ("AA_or_better", "A", "BBB_plus"),         # regulatory capital: BBB+ or better, anything better is fine
         "clo": ("A", "BBB_plus", "BBB", "BB")}               # CLO collateral: A (the ceiling) down to BB, mainly BBB; no AA or AAA (coupon too small)
GROUPS = {"AA_or_better": "top", "A": "core", "BBB_plus": "core", "BBB": "core", "BB": "BB", "B": "B", "CCC": "CCC"}


def group_of(category: str) -> str:
    return GROUPS.get(category, category)


def passes_target(category: str, targets, implied: bool) -> bool:
    """A real rating must be one of the target categories. An implied one only has to fall in the same GROUP as a target category,
    because the two ratios cannot resolve the notch inside the investment-grade core."""
    if not implied:
        return category in targets
    return group_of(category) in {group_of(t) for t in targets}


ALIASES = {"A_or_better": ("AA_or_better", "A"), "BBB": ("BBB_plus", "BBB")}   # --target-ratings shorthands (BBB = the BBB family)
MODE_LABEL = {"regcap": "RegCap (BBB+ or better)", "clo": "CLO (mainly BBB; up to A, some BB)"}


def implied_category(leverage: float, coverage: float, bands: Dict[str, Tuple[float, float]] = BANDS) -> str:
    """The worse of the leverage category and the coverage category."""
    by_lev = next(i for i, c in enumerate(CATEGORIES) if leverage < bands[c][0] or c == "CCC")
    by_cov = next(i for i, c in enumerate(CATEGORIES) if coverage >= bands[c][1] or c == "CCC")
    return CATEGORIES[max(by_lev, by_cov)]


_SP = re.compile(r"^(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)\s*[+-]?$")
_MOODY = re.compile(r"^(Aaa|Aa|A|Baa|Ba|B|Caa|Ca|C)\s*[123]?$", re.I)


def parse_rating(text: str) -> Optional[str]:
    """'BBB-', 'Baa2', 'BBB (stable)' -> a category; None when the symbol is not recognised."""
    token = (text or "").strip().split()[0] if (text or "").strip() else ""
    up = token.upper().replace("*", "")
    if _SP.match(up):
        base = up.rstrip("+-")
        if base == "BBB" and up.endswith("+"):
            return "BBB_plus"
        return {"AAA": "AA_or_better", "AA": "AA_or_better", "A": "A", "BBB": "BBB", "BB": "BB", "B": "B"}.get(base, "CCC")
    m = _MOODY.match(token)
    if m:
        if m.group(1).upper() == "BAA" and token.strip()[-1] == "1":
            return "BBB_plus"
        return {"AAA": "AA_or_better", "AA": "AA_or_better", "A": "A", "BAA": "BBB", "BA": "BB", "B": "B"}.get(m.group(1).upper(), "CCC")
    return None


def load_ratings(path: str) -> Dict[str, str]:
    """ticker,rating per line (header allowed) -> {ticker: category}. Unrecognised symbols are reported, not guessed."""
    out: Dict[str, str] = {}
    bad = []
    for row in csv.reader(Path(path).read_text().splitlines()):
        if len(row) < 2 or not row[0].strip() or row[0].strip().startswith("#"):
            continue
        cat = parse_rating(row[1])
        if cat:
            out[row[0].strip().upper().replace(".", "-")] = cat
        elif row[0].strip().lower() not in ("ticker", "symbol"):
            bad.append(f"{row[0]}:{row[1]}")
    if bad:
        raise ValueError("unrecognised rating symbols in " + path + ": " + ", ".join(bad[:10]))
    return out
