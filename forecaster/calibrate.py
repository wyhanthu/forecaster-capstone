"""Compare the implied rating categories (rating.py) with real ratings, and suggest better bands.

The real ratings are only a yardstick: the screen keeps using the implied category. For every company that has both a real
rating and the numbers, compute Debt / LTM EBITDA and last-quarter coverage from the filings, classify it with the current
bands, and report how often it lands in the same / a neighbouring category. A coordinate search over round-number thresholds
then proposes bands that agree more often. Caveat printed with the result: the real ratings are about a year older than the
filings (17g-7 files are delayed 12 months), and a rating also reflects business risk that two ratios cannot see.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .rating import BANDS, CATEGORIES, implied_category

Row = Tuple[str, str, float, float]          # ticker, real category, leverage, coverage
LEV_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 10.0]
COV_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]


def collect(ratings: Dict[str, str], metrics: Callable[[str], Optional[Tuple[float, float]]]) -> List[Row]:
    rows = []
    for t, cat in sorted(ratings.items()):
        m = metrics(t)
        if m and m[0] is not None and m[1] is not None and m[0] > 0:        # negative EBITDA: leverage is meaningless
            rows.append((t, cat, m[0], m[1]))
    return rows


def score(rows: Sequence[Row], bands=BANDS) -> Tuple[float, float]:
    """(share in exactly the same category, share within one category)."""
    if not rows:
        return 0.0, 0.0
    idx = {c: i for i, c in enumerate(CATEGORIES)}
    exact = near = 0
    for _, real, lev, cov in rows:
        d = abs(idx[implied_category(lev, cov, bands)] - idx[real])
        exact += d == 0
        near += d <= 1
    return exact / len(rows), near / len(rows)


def confusion(rows: Sequence[Row], bands=BANDS) -> Dict[Tuple[str, str], int]:
    return dict(Counter((real, implied_category(lev, cov, bands)) for _, real, lev, cov in rows))


MIN_LEV_GAP, MIN_COV_GAP = 0.5, 1.0          # neighbouring categories must differ by at least this much (no vanishing categories)


def ordered(bands) -> bool:
    cats = CATEGORIES[:-1]
    lev = [bands[c][0] for c in cats]
    cov = [bands[c][1] for c in cats]
    return all(b - a >= MIN_LEV_GAP for a, b in zip(lev, lev[1:])) and all(a - b >= MIN_COV_GAP for a, b in zip(cov, cov[1:]))


def suggest(rows: Sequence[Row], passes: int = 3) -> Dict[str, Tuple[float, float]]:
    """Coordinate search: move one threshold at a time to the grid value that agrees most (exact first, then within one)."""
    bands = dict(BANDS)
    best = score(rows, bands)
    for _ in range(passes):
        for cat in CATEGORIES[:-1]:
            for pos, grid in ((0, LEV_GRID), (1, COV_GRID)):
                for v in grid:
                    trial = dict(bands)
                    trial[cat] = (v, bands[cat][1]) if pos == 0 else (bands[cat][0], v)
                    if not ordered(trial):             # better categories need clearly lower leverage and higher coverage
                        continue
                    s = score(rows, trial)
                    if s > best:
                        best, bands = s, trial
    return bands


def _q(xs: Sequence[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)


def quartiles(rows: Sequence[Row]) -> Dict[str, Tuple[int, Tuple[float, float, float], Tuple[float, float, float]]]:
    """Per real category: companies, (p25, median, p75) of leverage, (p25, median, p75) of coverage."""
    out = {}
    for c in CATEGORIES:
        sel = [r for r in rows if r[1] == c]
        if sel:
            out[c] = (len(sel), tuple(_q([r[2] for r in sel], p) for p in (.25, .5, .75)),
                      tuple(_q([r[3] for r in sel], p) for p in (.25, .5, .75)))
    return out


def report(rows: Sequence[Row]) -> str:
    cur, sug = BANDS, suggest(rows)
    e0, n0 = score(rows, cur)
    e1, n1 = score(rows, sug)
    cm = confusion(rows, cur)
    lines = [f"Calibration on {len(rows)} companies that have both a real rating and filing numbers.", "",
             "Real rating category: companies | leverage 25% / median / 75% | LTM coverage 25% / median / 75%"]
    lines += [f"  {c:12s} {n:4d} | " + " / ".join(f"{v:4.1f}x" for v in lev) + " | " + " / ".join(f"{v:4.1f}x" for v in cov)
              for c, (n, lev, cov) in quartiles(rows).items()]
    lines += ["  (a category with fewer than 15 companies is too thin to fit a threshold on)"]
    lines += ["", "Rows = real category, columns = implied by the current bands (" + ", ".join(CATEGORIES) + ")"]
    for real in CATEGORIES:
        lines.append(f"  {real:12s} " + " ".join(f"{cm.get((real, c), 0):5d}" for c in CATEGORIES))
    lines += ["", f"Current bands : same category {e0:.0%}, within one category {n0:.0%}",
              f"Suggested     : same category {e1:.0%}, within one category {n1:.0%}   (fitted on this same data: optimistic)",
              "Suggested bands (leverage below / coverage at least):"]
    lines += [f"  {c:12s} ({sug[c][0]:g}, {sug[c][1]:g})" for c in CATEGORIES[:-1]]
    lines += ["", "Caveats: real ratings are about a year older than the filings, rating also reflects business risk that two "
                  "ratios cannot see, and the fit uses the same companies it is judged on. Treat the suggestion as a guide: round "
                  "numbers, change BANDS in rating.py only if the improvement is clear."]
    return "\n".join(lines)
