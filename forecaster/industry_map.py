"""Industry and sector of each company for the diversification caps, from public data only.

Source: the Nasdaq.com stock-screener downloads for NASDAQ, NYSE and NYSE American (Sector and Industry columns), stored without the price
columns in `forecaster/data/industry_map.csv` (downloaded 2026-11-20; US, Canada and Europe listings). This is Nasdaq's own taxonomy (13 sectors, about 140
industries), not GICS. A company missing from the file, or with a blank industry (ETFs, very new listings, NYSE American
names), falls back to its SEC SIC group ("SIC xxx", the first three digits of the SIC code in the SEC submissions file).
To refresh the file, download both screener CSVs again and pass them with `--industry-file`."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Optional, Tuple

DEFAULT_FILE = Path(__file__).parent / "data" / "industry_map.csv"


def norm_ticker(t: str) -> str:
    return (t or "").strip().upper().replace("/", "-").replace(".", "-").replace(" ", "-")


def load_industry_file(paths=None) -> Dict[str, Tuple[str, str]]:
    """{ticker: (sector, industry)} from one or more CSVs (a list or a comma-separated string). Columns: ticker or Symbol,
    industry or Industry, and optionally sector or Sector; other columns are ignored, so the raw Nasdaq.com exports work.
    The first file that has a ticker wins. With no paths the bundled file is used."""
    if not paths:
        paths = [DEFAULT_FILE]
    if isinstance(paths, (str, Path)):
        paths = [p for p in str(paths).split(",") if p.strip()]
    out: Dict[str, Tuple[str, str]] = {}
    for path in paths:
        with open(Path(str(path).strip()).expanduser(), newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                t, ind = norm_ticker(row.get("ticker") or row.get("symbol") or ""), row.get("industry", "")
                if t and ind:
                    out.setdefault(t, (row.get("sector", ""), ind))
    return out


class IndustryMap:
    def __init__(self, table: Optional[Dict[str, Tuple[str, str]]] = None) -> None:
        self.table = table if table is not None else load_industry_file()

    def industry(self, ticker: str, sic) -> str:
        hit = self.table.get(norm_ticker(ticker))
        if hit:
            return hit[1]
        s = str(sic or "").strip()
        return f"SIC {s[:3]}" if len(s) >= 3 and s[:3].isdigit() else ""

    def sector(self, ticker: str) -> str:
        return (self.table.get(norm_ticker(ticker)) or ("", ""))[0]
