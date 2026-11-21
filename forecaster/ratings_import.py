"""Turn a public rating-history file (SEC Rule 17g-7, e.g. Egan-Jones' corporate CSV) into a `ticker,rating` file.

The 17g-7 files carry issuer NAMES and no ticker or CIK, so names are matched to the names SEC has on file (current name and
former names). Only an exact match after normalizing legal suffixes and punctuation counts; a name that matches two tickers is
dropped, never guessed. Only the latest long-term rating of each issuer is kept, and withdrawn / default / not-rated symbols are
skipped. The file is delayed by 12 months by law, so these ratings are a cross-check on the implied category, not today's rating.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .rating import parse_rating

_SUFFIX = re.compile(r"\b(INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|LIMITED|LTD|LLC|LP|L P|PLC|NV|SA|AG|THE|DE|NEW)\b")


def norm_name(name: str) -> str:
    s = re.sub(r"[^A-Z0-9& ]", " ", (name or "").upper().replace("&AMP;", "&"))
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def latest_ratings(path: str) -> Dict[str, Tuple[str, str]]:
    """{issuer name: (rating symbol, action date)} from a 17g-7 CSV; latest long-term action; NR/WR/D and short-term skipped."""
    best: Dict[str, Tuple[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("rating_type_term") or "Long-term") != "Long-term":
                continue
            name, rating, when = (row.get("issuer_name") or "").strip(), (row.get("rating") or "").strip(), row.get("rating_action_date") or ""
            if not name or not rating:
                continue
            if name not in best or when >= best[name][1]:
                best[name] = (rating, when)
    return {n: v for n, v in best.items() if parse_rating(v[0]) and v[0].upper() not in ("NR", "WR", "D")}


def sec_names(ticker_to_cik: Dict[str, str], read_subs) -> Dict[str, List[str]]:
    """{normalized name: [tickers]} from each company's current and former names."""
    out: Dict[str, set] = defaultdict(set)
    for ticker, cik in ticker_to_cik.items():
        subs = read_subs(cik) or {}
        for n in [subs.get("name")] + [x.get("name") for x in subs.get("formerNames") or []]:
            if n and norm_name(n):
                out[norm_name(n)].add(ticker)
    return {k: sorted(v) for k, v in out.items()}


def match(ratings: Dict[str, Tuple[str, str]], names: Dict[str, List[str]]) -> Tuple[Dict[str, Tuple[str, str, str]], Dict[str, int]]:
    """{ticker: (rating, action date, issuer name)} plus counts of what happened to the rating file's issuers."""
    out: Dict[str, Tuple[str, str, str]] = {}
    stat = {"issuers with a usable rating": len(ratings), "matched to one SEC ticker": 0, "no SEC name match": 0, "ambiguous name": 0}
    for name, (rating, when) in ratings.items():
        tickers = names.get(norm_name(name), [])
        if not tickers:
            stat["no SEC name match"] += 1
        elif len(tickers) > 1:
            stat["ambiguous name"] += 1
        else:
            t = tickers[0]
            if t not in out or when > out[t][1]:
                out[t] = (rating, when, name)
            stat["matched to one SEC ticker"] += 1
    return out, stat


def write_ratings(path: str, matched: Dict[str, Tuple[str, str, str]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "rating"])
        for t in sorted(matched):
            w.writerow([t, matched[t][0]])
    Path(path).with_suffix(".detail.csv").write_text("ticker,rating,last_action_date,issuer_name\n" + "".join(
        f'{t},{r},{d},"{n}"\n' for t, (r, d, n) in sorted(matched.items())))
