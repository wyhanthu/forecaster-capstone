"""Fictional SEC-shaped data for tests: companyfacts JSON, submissions JSON, 10-Q / 10-K / 8-K HTML.

Every company and number here is invented. The shapes follow what SEC returns (units/USD entries with start, end, val,
accn, form, filed; filings.recent parallel arrays; inline-XBRL pages with a hidden ix:header and table-based layout).
"""
from __future__ import annotations

import calendar
import json
from datetime import date
from typing import Dict, List, Optional

QUARTERS = [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2), (2026, 3)]
M = 1_000_000


def qend(y: int, q: int) -> date:
    return date(y, 3 * q, calendar.monthrange(y, 3 * q)[1])


FILED = {(2025, 1): "2025-05-06", (2025, 2): "2025-08-05", (2025, 3): "2025-10-29",
         (2026, 1): "2026-05-06", (2026, 2): "2026-08-05", (2026, 3): "2026-10-28"}
K_FILED = {2023: "2024-02-21", 2024: "2025-02-20", 2025: "2026-02-20"}


def _e(start: Optional[date], end: date, val: float, form: str, filed: str, accn: str = "0000000000-00-000000") -> dict:
    d = {"end": end.isoformat(), "val": int(round(val * M)), "accn": accn, "fy": end.year, "fp": "Q", "form": form, "filed": filed}
    if start:
        d["start"] = start.isoformat()
    return d


def flow(qvals: Dict[tuple, float], direct_3m: bool, fy_prior: Dict[int, float]) -> List[dict]:
    """A flow tag the way filers report it: 3-month values (if direct_3m), year-to-date values, and the 12-month total."""
    out = []
    for y in (2025, 2026):
        cum = 0.0
        for q in (1, 2, 3):
            v = qvals[(y, q)]
            cum += v
            if direct_3m:
                out.append(_e(date(y, 3 * q - 2, 1), qend(y, q), v, "10-Q", FILED[(y, q)]))
            out.append(_e(date(y, 1, 1), qend(y, q), cum, "10-Q", FILED[(y, q)]))
        if (y, 4) in qvals:
            out.append(_e(date(y, 1, 1), date(y, 12, 31), cum + qvals[(y, 4)], "10-K", K_FILED[y]))
    for y, v in fy_prior.items():
        out.append(_e(date(y, 1, 1), date(y, 12, 31), v, "10-K", K_FILED[y]))
    return out


def inst(vals: Dict[tuple, float], form_for=lambda k: "10-Q" if k[1] != 4 else "10-K") -> List[dict]:
    return [_e(None, qend(*k), v, form_for(k), FILED.get(k) or K_FILED[k[0]]) for k, v in vals.items()]


def make_facts(cik: int, ebitda=(190, 195, 200, 205, 210, 215, 220), debt=(1520, 1500, 1480, 1460, 1440, 1420, 1400),
               interest=30.0, da=20.0, cash=400.0, ca=900.0, cl=600.0, name="Testa Holdings") -> dict:
    ebit = {k: e - da for k, e in zip(QUARTERS, ebitda)}
    dep = {k: da for k in QUARTERS}
    intr = {k: interest for k in QUARTERS}
    cur = 50.0
    us = {
        "OperatingIncomeLoss": flow(ebit, True, {2023: 640.0, 2024: 680.0}),
        "DepreciationDepletionAndAmortization": flow(dep, False, {2023: 80.0, 2024: 80.0}),   # cash-flow tag: YTD only
        "InterestExpense": flow(intr, True, {2023: 120.0, 2024: 120.0}),
        "NetCashProvidedByUsedInOperatingActivities": flow({k: 140.0 for k in QUARTERS}, False, {2023: 500.0, 2024: 540.0}),
        "PaymentsToAcquirePropertyPlantAndEquipment": flow({k: 40.0 for k in QUARTERS}, False, {2023: 150.0, 2024: 150.0}),
        "LongTermDebtNoncurrent": inst({k: d - cur for k, d in zip(QUARTERS, debt)}),
        "LongTermDebtCurrent": inst({k: cur for k in QUARTERS}),
        "CashAndCashEquivalentsAtCarryingValue": inst({k: cash for k in QUARTERS}),
        "AssetsCurrent": inst({k: ca for k in QUARTERS}),
        "LiabilitiesCurrent": inst({k: cl for k in QUARTERS}),
    }
    for (tag, _n), v in zip((("InNextTwelveMonths", 1), ("InYearTwo", 2), ("InYearThree", 3), ("InYearFour", 4),
                             ("InYearFive", 5)), (100, 150, 200, 300, 700)):
        us[f"LongTermDebtMaturitiesRepaymentsOfPrincipal{tag}"] = [_e(None, date(2025, 12, 31), v, "10-K", K_FILED[2025])]
    us["LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive"] = [_e(None, date(2025, 12, 31), 0, "10-K", K_FILED[2025])]
    return {"cik": cik, "entityName": name, "facts": {"us-gaap": {t: {"units": {"USD": v}} for t, v in us.items()}}}


def make_subs(cik: int, ticker: str, name: str = "Testa Holdings", sic: str = "3490", eight_k: Optional[tuple] = None,
              exchanges=("NYSE",), stale: bool = False) -> dict:
    rows = [("10-Q", "2026-10-28", "2026-09-30", f"{cik:010d}-26-000010", f"{ticker.lower()}-20260930.htm", ""),
            ("10-Q", "2026-08-05", "2026-06-30", f"{cik:010d}-26-000007", f"{ticker.lower()}-20260630.htm", ""),
            ("10-K", "2026-02-20", "2025-12-31", f"{cik:010d}-26-000003", f"{ticker.lower()}-20251231.htm", "")]
    if eight_k:
        rows.insert(0, ("8-K", eight_k[0], eight_k[0], f"{cik:010d}-26-000012", f"{ticker.lower()}-8k.htm", eight_k[1]))
    if stale:                                              # the latest periodic filing is more than a year old
        rows = [(f, "2025-03-01" if f != "8-K" else d, r, a, doc, i) for f, d, r, a, doc, i in rows]
    cols = list(zip(*rows))
    keys = ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "items")
    return {"cik": str(cik), "name": name, "sic": sic, "tickers": [ticker] if ticker else [], "exchanges": list(exchanges),
            "filings": {"recent": dict(zip(keys, map(list, cols)))}}


# --------------------------------------------------------------------------- HTML
def _toc() -> str:
    rows = "".join(f"<tr><td>{i}</td><td>{t}</td><td>{n}</td></tr>" for i, t, n in (
        ("Item 1.", "Financial Statements", 3), ("Item 2.", "Management&#8217;s Discussion and Analysis", 15),
        ("Item 3.", "Quantitative and Qualitative Disclosures About Market Risk", 22), ("Item 4.", "Controls and Procedures", 23)))
    return f"<table>{rows}</table>"


def tenq_html(name: str = "Testa Holdings", availability: str = "$450.0 million") -> str:
    return f"""<html><head><title>{name} 10-Q</title></head><body>
<div style="display:none"><ix:header><ix:hidden>HIDDEN-XBRL-CONTEXT should never appear</ix:hidden></ix:header></div>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p><p>FORM 10-Q</p><p>{name}</p>
{_toc()}
<p>PART I. FINANCIAL INFORMATION</p>
<table><tr><td>Item 1.</td><td>Financial Statements</td></tr></table>
<p>Condensed Consolidated Balance Sheets (Unaudited)</p>
<table><tr><td>Cash and cash equivalents</td><td>$</td><td>400.0</td><td>$</td><td>380.0</td></tr>
<tr><td>Net loss</td><td>(</td><td>12.5</td><td>)</td></tr></table>
<p>Notes to Condensed Consolidated Financial Statements (Unaudited)</p>
<p>Note 1 &#8212; Basis of Presentation</p>
<p>The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. Management evaluated whether
there is substantial doubt about the Company&#8217;s ability to continue as a going concern and concluded there is none.</p>
<p>Note 2 &#8212; Debt</p>
<p>As of September 30, 2026, total debt outstanding was $1,400.0 million. As of September 30, 2026, we had {availability} of
availability under our revolving credit facility. The credit agreement requires a net leverage ratio not to exceed 4.50 to 1.00.
As of September 30, 2026, our net leverage ratio was 3.2 to 1.00.</p>
<table><tr><td>2027</td><td>150.0</td></tr><tr><td>2028</td><td>200.0</td></tr></table>
<p>Note 3 &#8212; Leases</p>
<p>Operating lease payments due within the next two years total $70.0 million in the table below.</p>
<p>Note 4 &#8212; Commitments and Contingencies</p>
<p>Purchase commitments due within the next two years were $42.0 million. We estimate the reasonably possible loss in excess
of amounts accrued at up to $0.0 million to $15.0 million.</p>
<p>Note 5 &#8212; Derivatives and Hedging</p>
<p>Floating-rate borrowings represented 30% of total debt after hedging.</p>
<p>Note 6 &#8212; Subsequent Events</p>
<p>Management evaluated events through the filing date and identified no material subsequent events.</p>
<table><tr><td>Item 2.</td><td>Management&#8217;s Discussion and Analysis of Financial Condition and Results of Operations</td></tr></table>
<p>Overview. Revenue grew modestly in the quarter on stable demand.</p>
<p>Liquidity and Capital Resources</p>
<p>As of September 30, 2026, we had cash and cash equivalents of $400.0 million. Interest expense was $30.0 million for the
three months ended September 30, 2026. Scheduled principal maturities of long-term debt are $100.0 million in 2026.</p>
<table><tr><td>Item 3.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td></tr></table>
<p>A hypothetical 100 basis point increase in interest rates would increase our annual interest expense by approximately
$4.2 million.</p>
<table><tr><td>Item 4.</td><td>Controls and Procedures</td></tr></table>
<p>Our disclosure controls were effective.</p>
<p>PART II. OTHER INFORMATION</p>
<table><tr><td>Item 1.</td><td>Legal Proceedings</td></tr></table><p>None.</p>
<table><tr><td>Item 1A.</td><td>Risk Factors</td></tr></table><p>No material changes.</p>
</body></html>"""


def tenk_html(name: str = "Testa Holdings") -> str:
    return f"""<html><body><div style="display:none"><ix:header>HIDDEN</ix:header></div>
<table><tr><td>Item 1.</td><td>Business</td><td>3</td></tr><tr><td>Item 1A.</td><td>Risk Factors</td><td>9</td></tr>
<tr><td>Item 7.</td><td>MD&amp;A</td><td>30</td></tr><tr><td>Item 8.</td><td>Financial Statements</td><td>40</td></tr></table>
<p>Item 1. Business</p><p>{name} makes components for industrial customers in North America and Europe.</p>
<p>Item 1A. Risk Factors</p><p>Our substantial indebtedness could adversely affect our financial condition. We may not be able to
comply with the covenants in our credit agreement. Any failure could raise substantial doubt about our ability to continue as a
going concern.</p><p>Demand for our products varies with economic conditions.</p>
<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>
<p>Item 7. Management&#8217;s Discussion and Analysis</p><p>Results were stable. We had cash and cash equivalents of $380.0 million.</p>
<p>Item 8. Financial Statements and Supplementary Data</p>
<p>Note 1. Organization</p><p>{name} was founded in 1990.</p><p>Note 2. Debt</p><p>Total debt outstanding was $1,460.0 million.</p>
<p>Note 3. Leases</p><p>Lease payments are $60.0 million.</p><p>Item 9. Changes in and Disagreements</p><p>None.</p>
</body></html>"""


def eightk_html(item: str = "2.04", extra: str = "The lenders have accelerated the revolving credit facility.") -> str:
    return f"""<html><body><p>FORM 8-K</p><p>Item {item}. Triggering Events That Accelerate or Increase a Direct Financial Obligation</p>
<p>{extra}</p><p>Item 9.01. Financial Statements and Exhibits</p><p>None.</p><p>SIGNATURES</p></body></html>"""


# --------------------------------------------------------------------------- a fake SEC
class FakeSec:
    """A dict of url -> bytes served through the Fetcher interface; records every request."""

    def __init__(self):
        self.routes: Dict[str, bytes] = {}
        self.log: List[str] = []
        self.fail_once: Dict[str, int] = {}

    def add_company(self, ticker: str, cik: int, facts: dict, subs: dict, tenq: str, tenk: str, eightk: str = ""):
        from forecaster.edgar import DOC_URL, FACTS_URL, SUBS_URL
        self.routes[FACTS_URL.format(cik=f"{cik:010d}")] = json.dumps(facts).encode()
        self.routes[SUBS_URL.format(cik=f"{cik:010d}")] = json.dumps(subs).encode()
        rec = subs["filings"]["recent"]
        for form, accn, doc in zip(rec["form"], rec["accessionNumber"], rec["primaryDocument"]):
            body = {"10-Q": tenq, "10-K": tenk, "8-K": eightk}[form]
            self.routes[DOC_URL.format(cik_int=cik, accn=accn.replace("-", ""), doc=doc)] = body.encode()
        tick = json.loads(self.routes.get("tickers", b"{}")) if "tickers" in self.routes else {}
        tick[str(len(tick))] = {"cik_str": cik, "ticker": ticker, "title": ticker}
        self.routes["tickers"] = json.dumps(tick).encode()

    def fetcher(self):
        from forecaster.edgar import TICKERS_URL

        def fetch(url, headers, timeout):
            self.log.append(url)
            assert headers.get("User-Agent") and "@" in headers["User-Agent"], "SEC requires a User-Agent with contact info"
            if self.fail_once.get(url, 0) > 0:
                self.fail_once[url] -= 1
                return 503, {}, b""
            body = self.routes.get("tickers") if url == TICKERS_URL else self.routes.get(url)
            return (200, {}, body) if body is not None else (404, {}, b"")
        return fetch


def write_bulk_zips(folder, companies) -> tuple:
    """companies: [(cik, facts, subs)] -> (companyfacts.zip, submissions.zip) the way SEC ships them (CIK##########.json)."""
    import zipfile
    fz, sz = str(folder / "companyfacts.zip"), str(folder / "submissions.zip")
    with zipfile.ZipFile(fz, "w") as f, zipfile.ZipFile(sz, "w") as sb:
        for cik, facts, subs in companies:
            f.writestr(f"CIK{cik:010d}.json", json.dumps(facts))
            sb.writestr(f"CIK{cik:010d}.json", json.dumps(subs))
        sb.writestr("CIK0000999999-submissions-001.json", "{}")        # SEC's overflow files are not companies
    return fz, sz
