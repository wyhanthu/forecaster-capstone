"""SEC `companyfacts` JSON (+ `submissions` JSON) -> the fact bundle the screen reads.

The bundle has the same shape as the synthetic universe (sample_data.py): quarters, annual, maturities, filed dates.
All money is in $ millions. Nothing is invented: a figure that cannot be built from the reported tags is None, and the
ratios then report the input as missing (an unknown figure is never treated as zero).

Definitions (also written to bundle["data_notes"] so a reader of the report can see them):
  EBITDA          not an XBRL tag. EBIT + depreciation & amortization, where
                  EBIT = OperatingIncomeLoss, or (pre-tax income + interest expense) when that tag is absent, and
                  D&A comes from the cash-flow statement. Missing either part -> EBITDA is None.
  InterestExpense first available of InterestExpense, InterestExpenseNonoperating, InterestExpenseDebt, ...
  TotalDebt       DebtLongtermAndShorttermCombinedAmount; else LongTermDebt (+ short-term borrowings, commercial paper);
                  else LongTermDebtNoncurrent + current debt. Finance and operating leases are NOT included.
  Quarterly flows 3-month values as reported; Q2/Q3 and Q4 from year-to-date differences when no 3-month value exists.
  Maturities      LongTermDebtMaturitiesRepaymentsOfPrincipal* from the latest filing that reports them, keyed by calendar
                  year = balance-sheet year + n (year 1 = the 12 months after the balance-sheet date).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

PERIODIC = ("10-K", "10-Q", "10-KT", "10-QT")


class NoXbrlData(Exception):
    pass


@dataclass(frozen=True)
class Pt:
    value: float
    filed: str
    end: date


def _d(s: str) -> date:
    return date.fromisoformat(s)


# --------------------------------------------------------------------------- tag chains
EBIT_TAGS = ("OperatingIncomeLoss",)
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet")
COST_TAGS = ("CostsAndExpenses", "OperatingCostsAndExpenses")
PRETAX_TAGS = ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic")
DA_TAGS = ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAmortizationAndOther")
DA_PARTS = ("Depreciation", "AmortizationOfIntangibleAssets")
INTEREST_TAGS = ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense",
                 "InterestExpenseLongTermDebt")
PAID_TAGS = ("InterestPaidNet", "InterestPaid")                                  # cash interest: a fallback for companies that
NET_TAGS = ("InterestIncomeExpenseNonoperatingNet", "InterestIncomeExpenseNet")  # no longer report interest expense itself
CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash")
CFO_TAGS = ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")
CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements", "PaymentsToAcquireMachineryAndEquipment",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsToAcquireOtherProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets")
MATURITY_TAGS = (("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", 1),
                 ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo", 2),
                 ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree", 3),
                 ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour", 4),
                 ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive", 5),
                 ("LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive", 6))


# --------------------------------------------------------------------------- reading one tag
def _entries(facts: dict, tag: str) -> List[dict]:
    node = ((facts.get("facts") or {}).get("us-gaap") or {}).get(tag)
    if not node:
        return []
    return [e for e in (node.get("units") or {}).get("USD", [])
            if str(e.get("form", "")).split("/")[0] in PERIODIC and e.get("end")]


def _latest(entries: List[dict], key) -> Dict[tuple, Tuple[float, str]]:
    """Dedupe: the same period is repeated in later filings (comparatives, restatements): the newest filing wins."""
    out: Dict[tuple, Tuple[float, str, str]] = {}
    for e in entries:
        k = key(e)
        cur = (float(e["val"]), str(e.get("filed", "")), str(e.get("accn", "")))
        if k not in out or (cur[1], cur[2]) >= (out[k][1], out[k][2]):
            out[k] = cur
    return {k: (v[0], v[1]) for k, v in out.items()}


def _dur(facts: dict, tags) -> Tuple[Dict[tuple, Tuple[float, str]], Dict[str, int]]:
    """Reported durations (start, end) -> value, merged across the tags of one concept: for each period the first tag wins.

    Merging BEFORE the year-to-date arithmetic matters: a company that switched tag between its 10-K and its 10-Qs
    (DepreciationAndAmortization -> DepreciationDepletionAndAmortization) can still have its fourth quarter derived."""
    merged: Dict[tuple, Tuple[float, str]] = {}
    used: Dict[str, int] = {}
    for tag in tags:
        entries = [e for e in _entries(facts, tag) if e.get("start")]
        for k, v in _latest(entries, lambda e: (_d(e["start"]), _d(e["end"]))).items():
            if k not in merged:
                merged[k] = v
                used[tag] = used.get(tag, 0) + 1
    return merged, used


def _quarters_from(dur: Dict[tuple, Tuple[float, str]]) -> Dict[date, Pt]:
    out: Dict[date, Pt] = {}
    for (s, e), (v, f) in dur.items():
        if 80 <= (e - s).days <= 120:
            out[e] = Pt(v, f, e)
    ladders: Dict[date, Dict[int, Tuple[date, float, str]]] = defaultdict(dict)
    for (s, e), (v, f) in dur.items():
        days = (e - s).days
        rung = 1 if 80 <= days <= 120 else 2 if 170 <= days <= 200 else 3 if 260 <= days <= 290 else 4 if 340 <= days <= 380 else 0
        if rung:
            ladders[s][rung] = (e, v, f)
    for rungs in ladders.values():                                   # year-to-date differences: Q2 = 6M - 3M, Q4 = 12M - 9M
        for r in (2, 3, 4):
            if r in rungs and r - 1 in rungs:
                (e1, v1, f1), (e0, v0, f0) = rungs[r], rungs[r - 1]
                if 80 <= (e1 - e0).days <= 105 and e1 not in out:
                    out[e1] = Pt(v1 - v0, max(f1, f0), e1)
    return out


def _years_from(dur: Dict[tuple, Tuple[float, str]]) -> Dict[date, Pt]:
    return {e: Pt(v, f, e) for (s, e), (v, f) in dur.items() if 340 <= (e - s).days <= 380}


def quarterly(facts: dict, tag: str) -> Dict[date, Pt]:
    """Quarter-end -> value of the quarter, for a flow tag."""
    return _quarters_from(_dur(facts, (tag,))[0])


def annual(facts: dict, tag: str) -> Dict[date, Pt]:
    return _years_from(_dur(facts, (tag,))[0])


def instant(facts: dict, tag: str) -> Dict[date, Pt]:
    entries = [e for e in _entries(facts, tag) if not e.get("start")]
    return {k[0]: Pt(v, f, k[0]) for k, (v, f) in _latest(entries, lambda e: (_d(e["end"]),)).items()}


def chain(facts: dict, tags, reader) -> Tuple[Dict[date, Pt], Dict[str, int]]:
    """Merge several tags for one concept: each period takes the first tag in the list that reports it."""
    if reader is quarterly or reader is annual:
        dur, used = _dur(facts, tags)
        return (_quarters_from(dur) if reader is quarterly else _years_from(dur)), used
    out: Dict[date, Pt] = {}
    used = {}
    for tag in tags:
        for e, p in reader(facts, tag).items():
            if e not in out:
                out[e] = p
                used[tag] = used.get(tag, 0) + 1
    return out, used


def _add(a: Dict[date, Pt], b: Dict[date, Pt]) -> Dict[date, Pt]:
    return {e: Pt(a[e].value + b[e].value, max(a[e].filed, b[e].filed), e) for e in a if e in b}


def _da(facts: dict, reader) -> Tuple[Dict[date, Pt], str]:
    single, used = chain(facts, DA_TAGS, reader)
    if single:
        return single, max(used, key=used.get)
    parts = [reader(facts, t) for t in DA_PARTS]
    if all(parts):
        return _add(parts[0], parts[1]), "+".join(DA_PARTS)
    return {}, ""


def _interest(facts: dict, reader) -> Tuple[Dict[date, Pt], str]:
    """Interest expense: the reported expense; else cash interest paid; else net interest expense (never below zero).

    Per period the first basis available wins. Cash interest and net interest differ a little from the expense, so the basis
    actually used is written to the data notes."""
    gross, _ = chain(facts, INTEREST_TAGS, reader)
    paid, _ = chain(facts, PAID_TAGS, reader)
    net, _ = chain(facts, NET_TAGS, reader)
    out, n = dict(gross), {"expense": len(gross), "cash paid": 0, "net": 0}
    for e, p in paid.items():
        if e not in out:
            out[e] = Pt(abs(p.value), p.filed, e)
            n["cash paid"] += 1
    for e, p in net.items():
        if e not in out:
            out[e] = Pt(max(-p.value, 0.0), p.filed, e)               # negative net income = net expense; net income -> ~0
            n["net"] += 1
    basis = ", ".join(f"{k} {v}" for k, v in n.items() if v) or "none reported"
    return out, f"periods by basis: {basis}"


def _ebit(facts: dict, reader) -> Tuple[Dict[date, Pt], str]:
    direct, used = chain(facts, EBIT_TAGS, reader)
    basis = "OperatingIncomeLoss"
    pretax, _ = chain(facts, PRETAX_TAGS, reader)
    interest, _ = chain(facts, INTEREST_TAGS, reader)
    for e, p in _add(pretax, interest).items():
        if e not in direct:
            direct[e] = p
            basis = "OperatingIncomeLoss, else pre-tax income + interest expense"
    rev, _ = chain(facts, REVENUE_TAGS, reader)
    cost, _ = chain(facts, COST_TAGS, reader)
    for e in rev:
        if e not in direct and e in cost:
            direct[e] = Pt(rev[e].value - cost[e].value, max(rev[e].filed, cost[e].filed), e)
            basis += ", else revenue - total costs and expenses"
    for e, p in pretax.items():                      # pre-tax income is net of interest, so it understates EBIT: conservative
        if e not in direct:
            direct[e] = p
            basis += ", else pre-tax income alone (understates EBIT)"
    return direct, basis


# --------------------------------------------------------------------------- balance sheet
def _debt_series(facts: dict) -> Tuple[Dict[date, Pt], str]:
    combined, _ = chain(facts, ("DebtLongtermAndShorttermCombinedAmount",
                                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                                "DebtAndCapitalLeaseObligations"), instant)
    total_lt = instant(facts, "LongTermDebt")
    noncurrent, _ = chain(facts, ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"), instant)
    cur_lt, _ = chain(facts, ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"), instant)
    debt_cur = instant(facts, "DebtCurrent")
    short = instant(facts, "ShortTermBorrowings")
    paper = instant(facts, "CommercialPaper")
    loc, _ = chain(facts, ("LineOfCredit", "LongTermLineOfCredit"), instant)
    notes, _ = chain(facts, ("NotesPayable", "LongTermNotesPayable", "ConvertibleNotesPayable", "SeniorNotes", "SecuredDebt",
                             "UnsecuredDebt", "ConvertibleDebt", "SecuredLongTermDebt", "UnsecuredLongTermDebt",
                             "NonrecourseDebt", "DebtInstrumentCarryingAmount"), instant)
    out: Dict[date, Pt] = {}
    ends = set(combined) | set(total_lt) | set(noncurrent) | set(loc) | set(notes)
    for e in sorted(ends):
        if e in combined:
            out[e] = combined[e]
        elif e in total_lt:                                           # includes the current portion
            extra = sum(x[e].value for x in (short, paper) if e in x)
            out[e] = Pt(total_lt[e].value + extra, total_lt[e].filed, e)
        elif e in noncurrent:
            if e in debt_cur:
                cur = debt_cur[e].value
            else:
                cur = sum(x[e].value for x in (cur_lt, short, paper) if e in x)
            out[e] = Pt(noncurrent[e].value + cur, noncurrent[e].filed, e)
        else:                                                         # only borrowings lines / notes are reported
            parts = [x[e] for x in (loc, notes, cur_lt, short, paper) if e in x]
            if parts and sum(p.value for p in parts) > 0:             # a lone zero current portion is not "no debt"
                out[e] = Pt(sum(p.value for p in parts), max(p.filed for p in parts), e)
    return out, ("DebtLongtermAndShorttermCombinedAmount, else LongTermDebt (+ short-term borrowings, commercial paper), "
                 "else LongTermDebtNoncurrent or LongTermDebtAndCapitalLeaseObligations + current debt, else credit lines + notes "
                 "payable; operating leases excluded")


# --------------------------------------------------------------------------- sector
_SIC_RANGES = [
    (100, 999, "Consumer Staples"), (1000, 1099, "Materials"), (1200, 1299, "Energy"), (1300, 1399, "Energy"),
    (1400, 1499, "Materials"), (1500, 1799, "Industrials"), (2000, 2199, "Consumer Staples"),
    (2200, 2399, "Consumer Discretionary"), (2400, 2499, "Materials"), (2500, 2599, "Consumer Discretionary"),
    (2600, 2699, "Materials"), (2700, 2799, "Communication Services"), (2833, 2836, "Health Care"),
    (2800, 2899, "Materials"), (2900, 2999, "Energy"), (3000, 3099, "Materials"), (3100, 3199, "Consumer Discretionary"),
    (3200, 3399, "Materials"), (3400, 3499, "Industrials"), (3570, 3579, "Technology"), (3500, 3599, "Industrials"),
    (3630, 3639, "Consumer Discretionary"), (3660, 3699, "Technology"), (3600, 3699, "Industrials"),
    (3711, 3716, "Consumer Discretionary"), (3700, 3799, "Industrials"), (3841, 3845, "Health Care"),
    (3800, 3899, "Technology"), (3900, 3999, "Consumer Discretionary"), (4000, 4799, "Industrials"),
    (4800, 4899, "Communication Services"), (4950, 4959, "Industrials"), (4900, 4999, "Utilities"),
    (5122, 5122, "Health Care"), (5140, 5149, "Consumer Staples"), (5000, 5199, "Industrials"),
    (5400, 5499, "Consumer Staples"), (5912, 5912, "Consumer Staples"), (5200, 5999, "Consumer Discretionary"),
    (6000, 6499, "Financials"), (6500, 6599, "Real Estate"), (6700, 6799, "Financials"),
    (7000, 7099, "Consumer Discretionary"), (7370, 7379, "Technology"), (7300, 7399, "Industrials"),
    (7810, 7829, "Communication Services"), (7800, 7999, "Consumer Discretionary"), (8000, 8099, "Health Care"),
    (8200, 8299, "Consumer Discretionary"), (8700, 8799, "Industrials"),
]


def sector_from_sic(sic) -> str:
    try:
        n = int(sic)
    except (TypeError, ValueError):
        return "Unknown"
    for lo, hi, name in _SIC_RANGES:                    # the more specific range is listed before the wide one it sits in
        if lo <= n <= hi:
            return name
    return "Unknown"


# --------------------------------------------------------------------------- helpers
def qlabel(end: date) -> str:
    d = end - timedelta(days=21)            # 52/53-week years end up to three weeks after the calendar quarter
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def fy_of(end: date) -> int:
    return (end - timedelta(days=21)).year


def _m(x: Optional[Pt]) -> Optional[float]:
    return None if x is None else round(x.value / 1e6, 3)


def latest_periodic(subs: dict) -> Optional[dict]:
    """Newest 10-Q or 10-K (not amendments) from the submissions JSON: {form, filed, report, accn, doc}."""
    rec = ((subs.get("filings") or {}).get("recent")) or {}
    rows = list(zip(rec.get("form", []), rec.get("filingDate", []), rec.get("reportDate", []),
                    rec.get("accessionNumber", []), rec.get("primaryDocument", [])))
    best = None
    for form, filed, report, accn, doc in rows:
        if form in ("10-K", "10-Q", "10-KT", "10-QT") and (best is None or filed > best["filed"]):
            best = dict(form=form, filed=filed, report=report, accn=accn, doc=doc)
    return best


def filings_of(subs: dict, forms) -> List[dict]:
    rec = ((subs.get("filings") or {}).get("recent")) or {}
    n = len(rec.get("form", []))
    rows = []
    for i in range(n):
        if rec["form"][i] in forms:
            rows.append(dict(form=rec["form"][i], filed=rec["filingDate"][i],
                             report=(rec.get("reportDate") or [""] * n)[i], accn=rec["accessionNumber"][i],
                             doc=rec["primaryDocument"][i], items=(rec.get("items") or [""] * n)[i]))
    return sorted(rows, key=lambda r: r["filed"], reverse=True)


# --------------------------------------------------------------------------- the bundle
def build_bundle(facts: dict, subs: dict, ticker: str) -> dict:
    if not (facts.get("facts") or {}).get("us-gaap"):
        raise NoXbrlData(f"{ticker}: no us-gaap facts (not an XBRL filer in us-gaap?)")
    last = latest_periodic(subs)
    if not last:
        raise NoXbrlData(f"{ticker}: no 10-K or 10-Q in the recent filings")
    last_k = next(iter(filings_of(subs, ("10-K", "10-KT"))), None)
    cik = f"{int(facts.get('cik') or subs.get('cik')):010d}"
    notes: List[str] = []

    ebit_q, ebit_basis = _ebit(facts, quarterly)
    da_q, da_basis = _da(facts, quarterly)
    int_q, int_basis = _interest(facts, quarterly)
    ebit_a, _ = _ebit(facts, annual)
    da_a, _ = _da(facts, annual)
    int_a, _ = _interest(facts, annual)
    cfo_a, _ = chain(facts, CFO_TAGS, annual)
    capex_a, _ = chain(facts, CAPEX_TAGS, annual)
    debt, debt_basis = _debt_series(facts)
    cash, _ = chain(facts, CASH_TAGS, instant)
    ca, cl = instant(facts, "AssetsCurrent"), instant(facts, "LiabilitiesCurrent")
    da_absent = not da_q and not da_a                  # a company that reports no D&A at all (typical for small biotechs)
    notes.append(f"EBITDA = EBIT ({ebit_basis}) + D&A ({da_basis or 'NOT FOUND'}); not an XBRL tag"
                 + ("; no D&A is reported, so EBITDA = EBIT (conservative)" if da_absent else ""))
    notes.append(f"EBIT periods: {len(ebit_q)} quarterly / {len(ebit_a)} annual; D&A periods: {len(da_q)} / {len(da_a)}")
    notes.append(f"InterestExpense: {int_basis}")
    notes.append(f"TotalDebt = {debt_basis}")

    # ---- the quarters: the latest balance-sheet date reported by the newest 10-Q/10-K, then 4 earlier ones
    report = _d(last["report"]) if last.get("report") else None
    ends = sorted(set(ebit_q) | set(int_q) | set(debt) | set(ca))
    latest_end = None
    if report:
        near = [e for e in ends if abs((e - report).days) <= 10]
        latest_end = max(near) if near else None
    if latest_end is None:
        notes.append(f"XBRL has no period ending near {last.get('report')} yet (the newest filing may not be in companyfacts)")
    quarters: List[dict] = []
    da_gap_q = da_absent
    if latest_end:
        seq = [latest_end]
        while len(seq) < 5:
            prev = [e for e in ends if 80 <= (seq[-1] - e).days <= 120]
            if not prev:
                break
            seq.append(min(prev, key=lambda e: abs((seq[-1] - e).days - 91)))
        y0, q0 = divmod(int(qlabel(seq[0])[:4]) * 4 + int(qlabel(seq[0])[-1]) - 1, 4)

        def label(i: int) -> str:                      # consecutive quarters counting back from the latest: always unique
            yy, qq = divmod(y0 * 4 + q0 - i, 4)
            return f"{yy}Q{qq + 1}"
        da_gap_q = da_absent or any(e in ebit_q and e not in da_q for e in seq)
        if da_gap_q and not da_absent:
            notes.append("D&A is missing for some recent quarters: all quarterly EBITDA = EBIT (conservative)")
        for i in range(len(seq) - 1, -1, -1):
            e = seq[i]
            ebitda = (ebit_q[e] if da_gap_q else _add({e: ebit_q[e]}, {e: da_q[e]}).get(e)) if e in ebit_q and (da_gap_q or e in da_q) else None
            quarters.append(dict(period=label(i), end=e.isoformat(), ebitda=_m(ebitda), interest=_m(int_q.get(e)),
                                 debt=_m(debt.get(e)), cash=_m(cash.get(e)), ca=_m(ca.get(e)), cl=_m(cl.get(e))))

    # ---- fiscal years (10-K), the last three that end on or before the latest period
    fy_ends = sorted(e for e in set(ebit_a) | set(cfo_a) if latest_end is None or e <= latest_end)[-3:]
    annual_rows = []
    da_gap_a = da_absent or any(e in ebit_a and e not in da_a for e in fy_ends)
    if da_gap_a and not da_absent:
        notes.append("D&A is missing for some fiscal years: annual EBITDA = EBIT (conservative)")
    for e in fy_ends:
        ebitda = (ebit_a[e] if da_gap_a else _add({e: ebit_a[e]}, {e: da_a[e]}).get(e)) if e in ebit_a and (da_gap_a or e in da_a) else None
        annual_rows.append(dict(fy=fy_of(e), end=e.isoformat(), ebitda=_m(ebitda), interest=_m(int_a.get(e)),
                                debt=_m(debt.get(e)), cfo=_m(cfo_a.get(e)), capex=_m(capex_a.get(e))))

    # ---- debt maturities: latest balance-sheet date that reports at least years 1 and 2
    mats = {tag: instant(facts, tag) for tag, _n in MATURITY_TAGS}
    candidates = sorted(set(mats[MATURITY_TAGS[0][0]]) & set(mats[MATURITY_TAGS[1][0]]), reverse=True)
    maturities: Dict[int, float] = {}
    if candidates and latest_end is not None and (latest_end - candidates[0]).days > 400:
        notes.append(f"the newest maturity table is dated {candidates[0].isoformat()}: too old to use, maturities unknown")
        candidates = []
    if candidates:
        e = candidates[0]
        for tag, n in MATURITY_TAGS:
            if e in mats[tag]:
                maturities[e.year + n] = round(mats[tag][e].value / 1e6, 3)
        notes.append(f"maturities from the balance sheet dated {e.isoformat()}; year 1 = {e.year + 1}")
    else:
        notes.append("no LongTermDebtMaturitiesRepaymentsOfPrincipal* facts: maturities unknown")

    # ---- no borrowings at all: a company that has reported no debt item for two years, no maturity table and (almost) no interest
    if latest_end is not None:
        recent = [e for e in debt if (latest_end - e).days <= 730]
        interest_m = [abs(p.value) / 1e6 for e, p in int_q.items() if (latest_end - e).days <= 730]
        if not recent and not maturities and (not interest_m or max(interest_m) < 3.0):
            zero = Pt(0.0, last["filed"], latest_end)
            for q in quarters:
                if q["debt"] is None:
                    q["debt"] = 0.0
            for r in annual_rows:
                if r["debt"] is None:
                    r["debt"] = 0.0
            notes.append("no debt item, no maturity table and no material interest in two years: TotalDebt taken as 0")
            del zero

    return dict(
        cik=cik, ticker=ticker, name=subs.get("name") or facts.get("entityName") or ticker,
        sector=sector_from_sic(subs.get("sic")), sic=str(subs.get("sic") or ""), sic_desc=str(subs.get("sicDescription") or ""),
        sponsor=None,
        filed={"10-Q": last["filed"], "10-K": (last_k or last)["filed"]},
        latest_form=last["form"], latest_period_end=latest_end.isoformat() if latest_end else "",
        quarters=quarters, annual=annual_rows, maturities=maturities, data_notes=notes)
