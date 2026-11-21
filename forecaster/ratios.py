"""Pure computation tools: XBRL fact registry and textbook credit ratios.

No LLM is involved here. Every ratio returns its formula and the ids of the XBRL facts it
used, so a calc claim in a memo can be recomputed and traced ("cited inputs").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CAP = 99.0


class MissingDataError(Exception):
    def __init__(self, missing: List[str]):
        super().__init__("missing facts: " + ", ".join(missing))
        self.missing = missing


@dataclass(frozen=True)
class Fact:
    fact_id: str
    tag: str
    period: str
    value: float
    filed: str
    source: str = "xbrl"        # "xbrl", "fallback_text" (re-extracted debt) or "note_text" (read from a 10-Q note)
    chunk_id: str = ""          # set when a fallback re-extraction supplied the value


@dataclass
class Ratio:
    name: str
    value: float
    formula: str
    inputs: List[str]
    params: Dict = field(default_factory=dict)
    projection: bool = False


class FactSet:
    """Facts for one company, keyed by id: xbrl:<cik>:<tag>:<period>."""

    QMAP = {"ebitda": "EBITDA", "interest": "InterestExpense", "debt": "TotalDebt",
            "cash": "Cash", "ca": "CurrentAssets", "cl": "CurrentLiabilities"}
    AMAP = {"ebitda": "EBITDA", "interest": "InterestExpense", "debt": "TotalDebt",
            "cfo": "OperatingCashFlow", "capex": "Capex"}

    def __init__(self, cik: str, bundle: dict):
        self.cik = cik
        self.bundle = bundle
        self.facts: Dict[str, Fact] = {}
        self.q_periods: List[str] = [q["period"] for q in bundle["quarters"]]
        self.fys: List[int] = [a["fy"] for a in bundle["annual"]]
        self.filed = dict(bundle["filed"])
        for q in bundle["quarters"]:
            for key, tag in self.QMAP.items():
                if q.get(key) is not None:
                    self._add(tag, q["period"], q[key], self.filed.get("10-Q", ""))
        for a in bundle["annual"]:
            for key, tag in self.AMAP.items():
                if a.get(key) is not None:
                    self._add(tag, f"FY{a['fy']}", a[key], self.filed.get("10-K", ""))
        for year, amount in bundle["maturities"].items():
            self._add("DebtMaturity", str(year), amount, self.filed.get("10-Q", ""))

    def fid(self, tag: str, period: str) -> str:
        return f"xbrl:{self.cik}:{tag}:{period}"

    def _add(self, tag: str, period: str, value: float, filed: str) -> None:
        fid = self.fid(tag, period)
        self.facts[fid] = Fact(fid, tag, period, float(value), filed)

    def get(self, tag: str, period: str) -> Optional[Fact]:
        return self.facts.get(self.fid(tag, period))

    def need(self, pairs) -> List[Fact]:
        found, missing = [], []
        for tag, period in pairs:
            f = self.get(tag, period)
            (found if f else missing).append(f if f else self.fid(tag, period))
        if missing:
            raise MissingDataError(missing)
        return found

    # ---- numbers read from the 10-Q notes: same traceability as XBRL, citing the note chunk they came from
    def note_fid(self, tag: str) -> str:
        return f"note:{self.cik}:{tag}:{self.q_periods[-1]}"

    def add_note_fact(self, tag: str, value: float, chunk_id: str) -> None:
        fid = self.note_fid(tag)
        self.facts[fid] = Fact(fid, tag, self.q_periods[-1], float(value), self.filed.get("10-Q", ""),
                               source="note_text", chunk_id=chunk_id)

    def need_notes(self, tags) -> List[Fact]:
        found, missing = [], []
        for t in tags:
            f = self.facts.get(self.note_fid(t))
            (found if f else missing).append(f if f else self.note_fid(t))
        if missing:
            raise MissingDataError(missing)
        return found

    def override(self, tag: str, period: str, value: float, chunk_id: str) -> None:
        fid = self.fid(tag, period)
        self.facts[fid] = Fact(fid, tag, period, float(value), self.filed.get("10-Q", ""),
                               source="fallback_text", chunk_id=chunk_id)

    def fallback_chunks(self, fact_ids: List[str]) -> List[str]:
        return sorted({self.facts[i].chunk_id for i in fact_ids if i in self.facts and self.facts[i].chunk_id})


def _last_q(fs: "FactSet") -> str:
    """Latest quarter with data. A company whose newest filing is not in XBRL yet has none: that is missing data."""
    if not fs.q_periods:
        raise MissingDataError([fs.fid("Quarter", "no quarterly data")])
    return fs.q_periods[-1]


def _last_fy(fs: "FactSet") -> int:
    """Latest fiscal year with data. A company with no annual figures (a recent listing, a shell) is missing data, not an error."""
    if not fs.fys:
        raise MissingDataError([fs.fid("Annual", "no fiscal-year data")])
    return fs.fys[-1]


def _need_schedule(fs: "FactSet") -> None:
    """A debt-maturity schedule that was never reported is unknown, not zero (zero would read as unlimited liquidity)."""
    if any(f.tag == "DebtMaturity" for f in fs.facts.values()):
        return
    debt = fs.get("TotalDebt", _last_q(fs))
    if debt is not None and debt.value > 0:
        raise MissingDataError([fs.fid("DebtMaturity", "schedule")])


def _div(a: float, b: float) -> float:
    if b <= 0:
        return CAP
    return min(a / b, CAP)


# ---------------------------------------------------------------- ratio definitions
def r_leverage_ltm(fs: FactSet) -> Ratio:
    p = _last_q(fs)
    last4 = fs.q_periods[-4:]
    facts = fs.need([("TotalDebt", p)] + [("EBITDA", q) for q in last4])
    debt, ebitda = facts[0], facts[1:]
    return Ratio("leverage_ltm", _div(debt.value, sum(f.value for f in ebitda)),
                 "Total debt / LTM EBITDA", [f.fact_id for f in facts])


def r_coverage_q(fs: FactSet, period: Optional[str] = None) -> Ratio:
    p = period or _last_q(fs)
    e, i = fs.need([("EBITDA", p), ("InterestExpense", p)])
    return Ratio("coverage_q", _div(e.value, i.value), "EBITDA / interest expense (quarter)",
                 [e.fact_id, i.fact_id], {"period": p})


def r_coverage_ltm(fs: FactSet) -> Ratio:
    """EBITDA / interest expense over the last four quarters: steadier than one quarter, which swings with timing."""
    _last_q(fs)
    last4 = fs.q_periods[-4:]
    facts = fs.need([("EBITDA", q) for q in last4] + [("InterestExpense", q) for q in last4])
    e, i = facts[:len(last4)], facts[len(last4):]
    return Ratio("coverage_ltm", _div(sum(f.value for f in e), sum(f.value for f in i)),
                 "LTM EBITDA / LTM interest expense", [f.fact_id for f in facts])


def r_current_ratio(fs: FactSet) -> Ratio:
    p = _last_q(fs)
    ca, cl = fs.need([("CurrentAssets", p), ("CurrentLiabilities", p)])
    return Ratio("current_ratio", _div(ca.value, cl.value), "Current assets / current liabilities",
                 [ca.fact_id, cl.fact_id])


def r_fcf_fy(fs: FactSet, fy: Optional[int] = None) -> Ratio:
    y = fy or _last_fy(fs)
    cfo, capex = fs.need([("OperatingCashFlow", f"FY{y}"), ("Capex", f"FY{y}")])
    return Ratio("fcf_fy", cfo.value - capex.value, "Operating cash flow - capex ($ millions)",
                 [cfo.fact_id, capex.fact_id], {"fy": y})


def r_leverage_fy(fs: FactSet, fy: Optional[int] = None) -> Ratio:
    y = fy or _last_fy(fs)
    d, e = fs.need([("TotalDebt", f"FY{y}"), ("EBITDA", f"FY{y}")])
    return Ratio("leverage_fy", _div(d.value, e.value), "Total debt / EBITDA (fiscal year)",
                 [d.fact_id, e.fact_id], {"fy": y})


def _cfo_capex(fs: FactSet, fy) -> tuple:
    """(operating cash flow, capex or None). Some filers (Airbnb) report no capex tag at all; then free cash flow = operating
    cash flow, and the ratio carries `capex_unknown` so it is flagged and never used for a veto."""
    cfo = fs.need([("OperatingCashFlow", f"FY{fy}")])[0]
    return cfo, fs.get("Capex", f"FY{fy}")


def r_liquidity_cover(fs: FactSet, horizon: int = 2, as_of_year: int = 2026) -> Ratio:
    """(cash + horizon x projected FCF) / debt maturing within the horizon.

    The FCF projection is flat at the latest fiscal year and is labeled a projection. When the company reports debt but no
    maturity schedule in XBRL, ALL of its debt is counted as due: a deliberately harsh proxy (it is flagged, shown in the
    formula, and never used to veto a company, because a missing table says nothing about when the debt falls due).
    """
    pq = _last_q(fs)
    fy = _last_fy(fs)
    cash = fs.need([("Cash", pq)])[0]
    cfo, capex = _cfo_capex(fs, fy)
    years = [str(y) for y in range(as_of_year, as_of_year + horizon + 1)]
    fcf = max(cfo.value - (capex.value if capex else 0.0), 0.0)
    ids = [f.fact_id for f in (cash, cfo, capex) if f]
    unk = {"capex_unknown": True} if capex is None else {}
    if not any(f.tag == "DebtMaturity" for f in fs.facts.values()):
        debt = fs.get("TotalDebt", pq)
        if debt is not None and debt.value > 0:
            return Ratio("liquidity_cover", min((cash.value + horizon * fcf) / debt.value, CAP),
                         f"(Cash + {horizon} x max(FCF,0)) / TOTAL debt (no maturity schedule in XBRL: all debt counted as due)",
                         ids + [debt.fact_id],
                         {"horizon": horizon, "as_of_year": as_of_year, "proxy": True, **unk}, projection=True)
    mats = [fs.get("DebtMaturity", y) for y in years if fs.get("DebtMaturity", y)]
    due = sum(m.value for m in mats)
    value = CAP if due <= 0 else min((cash.value + horizon * fcf) / due, CAP)
    return Ratio("liquidity_cover", value,
                 f"(Cash + {horizon} x max(FCF,0)) / debt maturing {years[0]}-{years[-1]}",
                 ids + [m.fact_id for m in mats],
                 {"horizon": horizon, "as_of_year": as_of_year, **unk}, projection=True)


NOTE_USES = ("RevolverAvailability", "LeaseObligations2Y", "Commitments2Y", "LitigationPossibleLoss", "RateShockInterest")


def r_liquidity_cover_adj(fs: FactSet, horizon: int = 2, as_of_year: int = 2026) -> Ratio:
    """Liquidity cover after the 10-Q notes are counted.

    (cash + undrawn revolver + horizon x projected FCF)
      / (debt maturing in the horizon + lease obligations + other commitments + possible litigation loss
         + horizon x the interest cost of a 100bp rate rise on floating debt)
    """
    pq, fy = _last_q(fs), _last_fy(fs)
    cash = fs.need([("Cash", pq)])[0]
    cfo, capex = _cfo_capex(fs, fy)
    rev, lease, commit, lit, shock = fs.need_notes(NOTE_USES)
    _need_schedule(fs)
    years = [str(y) for y in range(as_of_year, as_of_year + horizon + 1)]
    mats = [fs.get("DebtMaturity", y) for y in years if fs.get("DebtMaturity", y)]
    due = sum(m.value for m in mats)
    fcf = max(cfo.value - (capex.value if capex else 0.0), 0.0)
    uses = due + lease.value + commit.value + lit.value + horizon * shock.value
    value = CAP if uses <= 0 else min((cash.value + rev.value + horizon * fcf) / uses, CAP)
    return Ratio("liquidity_cover_adj", value,
                 f"(Cash + revolver + {horizon} x max(FCF,0)) / (maturities {years[0]}-{years[-1]} + leases + commitments "
                 f"+ litigation + {horizon} x rate shock)",
                 [f.fact_id for f in (cash, cfo, capex) if f] + [m.fact_id for m in mats]
                 + [f.fact_id for f in (rev, lease, commit, lit, shock)],
                 {"horizon": horizon, "as_of_year": as_of_year, **({"capex_unknown": True} if capex is None else {})},
                 projection=True)


def r_covenant_headroom(fs: FactSet) -> Ratio:
    """(covenant limit - actual) / limit, both as stated in the credit-facility note."""
    cmax, cact = fs.need_notes(("CovenantMax", "CovenantActual"))
    return Ratio("covenant_headroom", (cmax.value - cact.value) / cmax.value,
                 "(Covenant limit - actual) / covenant limit", [cmax.fact_id, cact.fact_id])


RATIO_FUNCS = {
    "leverage_ltm": r_leverage_ltm,
    "coverage_q": r_coverage_q,
    "coverage_ltm": r_coverage_ltm,
    "current_ratio": r_current_ratio,
    "fcf_fy": r_fcf_fy,
    "leverage_fy": r_leverage_fy,
    "liquidity_cover": r_liquidity_cover,
    "liquidity_cover_adj": r_liquidity_cover_adj,
    "covenant_headroom": r_covenant_headroom,
}


def compute_ratio(fs: FactSet, name: str, **params) -> Ratio:
    params.pop("proxy", None)  # informational flags recorded on a Ratio; the function re-derives them itself
    params.pop("capex_unknown", None)
    return RATIO_FUNCS[name](fs, **params)


def core_ratios(fs: FactSet, horizon: int, as_of_year: int, with_liquidity: bool = True) -> Dict[str, Ratio]:
    """The headline ratios; raises MissingDataError if any input is absent. `with_liquidity=False` leaves out liquidity cover
    (it needs cash-flow and maturity data), so the pre-screen can drop a company on leverage, coverage or rating first."""
    missing: List[str] = []
    out: Dict[str, Ratio] = {}
    wanted = [("leverage_ltm", {}), ("coverage_q", {}), ("current_ratio", {})]
    if with_liquidity:
        wanted.append(("liquidity_cover", {"horizon": horizon, "as_of_year": as_of_year}))
    for name, kwargs in wanted:
        try:
            out[name] = compute_ratio(fs, name, **kwargs)
        except MissingDataError as exc:
            missing.extend(exc.missing)
    if missing:
        raise MissingDataError(sorted(set(missing)))
    return out


def note_ratios(fs: FactSet, horizon: int, as_of_year: int) -> Dict[str, object]:
    """Ratios that need numbers from the 10-Q notes. Returns {"ratios": {...}, "missing": [names]}; unknown is never zero."""
    out: Dict[str, Ratio] = {}
    missing: List[str] = []
    for name, kwargs in (("liquidity_cover_adj", {"horizon": horizon, "as_of_year": as_of_year}), ("covenant_headroom", {})):
        try:
            out[name] = compute_ratio(fs, name, **kwargs)
        except MissingDataError as exc:
            missing.extend(exc.missing)
    return {"ratios": out, "missing": sorted(set(missing))}


def series(fs: FactSet) -> Dict[str, List[Ratio]]:
    """Quarterly coverage, annual leverage and annual FCF (the inputs to trend detection)."""
    out: Dict[str, List[Ratio]] = {"coverage_q": [], "leverage_fy": [], "fcf_fy": []}
    for p in fs.q_periods:
        try:
            out["coverage_q"].append(compute_ratio(fs, "coverage_q", period=p))
        except MissingDataError:
            pass
    for y in fs.fys:
        for name, key in (("leverage_fy", "leverage_fy"), ("fcf_fy", "fcf_fy")):
            try:
                out[key].append(compute_ratio(fs, name, fy=y))
            except MissingDataError:
                pass
    return out


# ---------------------------------------------------------------- plausibility + fallback
def plausibility(fs: FactSet) -> List[str]:
    """Flag implausible numbers (the fast feedback channel: re-extract before anything is reported)."""
    problems: List[str] = []
    debts = [fs.get("TotalDebt", p) for p in fs.q_periods]
    for prev, cur in zip(debts, debts[1:]):
        if prev and cur and prev.value > 0:
            ratio = cur.value / prev.value
            if ratio < 0.1 or ratio > 10:
                problems.append(f"TotalDebt moved {ratio:.3f}x between {prev.period} and {cur.period} (units?)")
    for f in fs.facts.values():
        if f.tag == "TotalDebt" and f.value < 0:
            problems.append(f"negative debt in {f.fact_id}")
    try:
        cov = compute_ratio(fs, "coverage_q").value
        if cov > 500:
            problems.append(f"coverage {cov:.0f}x is implausible")
    except MissingDataError:
        pass
    return problems


DEBT_SENTENCE = re.compile(r"Total debt outstanding was \$([\d,\.]+) million")


def parse_debt_from_text(text: str) -> Optional[float]:
    m = DEBT_SENTENCE.search(text)
    return float(m.group(1).replace(",", "")) if m else None
