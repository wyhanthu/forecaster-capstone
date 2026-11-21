"""The universe, built from SEC's bulk files: nobody types a ticker.

Start: every company in companyfacts.zip (the XBRL filers). Keep a company when
  1. its submissions record has a ticker listed on NYSE or Nasdaq (those are the obligors an investor can look through),
  2. its sector is not Financials / Real Estate (banks and REITs have no EBITDA or debt-service profile to screen; override
     with include_financials),
  3. its latest 10-K / 10-Q is not older than `max_age_days` at the as-of date (a stale filer cannot be screened anyway).
The rating target (RegCap / CLO) is applied afterwards, on the numbers, in the pre-screen. The result is cached in the state
folder, so only the first run reads the two zip files end to end.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .xbrl import latest_periodic, sector_from_sic

EXCLUDED_SECTORS = ("Financials", "Real Estate")
DEFAULT_EXCHANGES = ("NYSE", "Nasdaq")
_CIK = re.compile(r"CIK(\d{10})\.json$", re.I)


def ciks_in(names: Iterable[str]) -> List[str]:
    return sorted({m.group(1) for n in names if (m := _CIK.search(n))})


def build_universe(ciks: Iterable[str], read_subs: Callable[[str], Optional[dict]], as_of: str, max_age_days: int = 150,
                   exchanges=DEFAULT_EXCHANGES, include_financials: bool = False, progress: bool = True) -> Dict[str, object]:
    """{"tickers": {ticker: cik10}, "funnel": {...}} (ticker -> CIK)."""
    wanted = {e.lower() for e in exchanges}
    ciks = list(ciks)
    funnel = {"companies with XBRL facts": len(ciks), "submissions record found": 0, "listed on NYSE/Nasdaq with a ticker": 0,
              "sector kept": 0, "latest filing recent enough": 0}
    out: Dict[str, str] = {}
    asof = date.fromisoformat(as_of)
    for n, cik in enumerate(ciks, 1):
        if progress and n % 1000 == 0:
            print(f"  universe: {n:,} / {len(ciks):,} companies checked", file=sys.stderr)
        subs = read_subs(cik)
        if not subs:
            continue
        funnel["submissions record found"] += 1
        tickers = [t for t in (subs.get("tickers") or []) if t]
        if not tickers or not ({str(e).lower() for e in (subs.get("exchanges") or [])} & wanted):
            continue
        funnel["listed on NYSE/Nasdaq with a ticker"] += 1
        if not include_financials and sector_from_sic(subs.get("sic")) in EXCLUDED_SECTORS:
            continue
        funnel["sector kept"] += 1
        last = latest_periodic(subs)
        if not last or (asof - date.fromisoformat(last["filed"])).days > max_age_days:
            continue
        funnel["latest filing recent enough"] += 1
        ticker = tickers[0].upper().replace(".", "-")
        if ticker not in out:
            out[ticker] = cik
    return {"tickers": out, "funnel": funnel}


def load_or_build(cache: Path, as_of: str, rebuild: bool, **kw) -> Dict[str, object]:
    path = Path(cache) / f"universe_{as_of}{'_fin' if kw.get('include_financials') else ''}.json"
    if path.exists() and not rebuild:
        return json.loads(path.read_text())
    uni = build_universe(as_of=as_of, **kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(uni, indent=1))
    return uni
