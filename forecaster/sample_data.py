"""Synthetic EDGAR-like universe for offline runs and tests.

IMPORTANT: every company below is fictional and every number is invented. The shapes
(XBRL-style quarterly/annual facts + section text + 8-K events) match what the live
pipeline would produce, and each company is built to exercise one behaviour from the design:

  ALPH  healthy; also has an outdated MD&A chunk (stale-chunk filter)
  BRCK  healthy; has a superseded MD&A chunk (amendment filter); Cedar Partners sponsor
  CYAN  healthy, low leverage
  DRFT  multi-period deterioration (coverage falling, leverage rising, FCF falling), blamed on sector-wide demand
  EMBR  one-time earnings dip with a cited recovery plan  -> "setback"
  FLUX  fails the XBRL pre-screen (leverage too high)
  GLEN  going-concern doubt -> hard exclude
  HRBR  covenant-waiver 8-K after the last 10-Q -> freeze
  IRON  maturity wall inside the horizon -> liquidity veto
  JADE  healthy; Cedar Partners sponsor
  KITE  last 10-Q is too old -> insufficient data
  LUMN  "going concern" text is about a divested subsidiary -> must be ignored
  MRDN  latest TotalDebt fact has a units error -> re-extracted from the footnote text
  NOVA  healthy; Cedar Partners sponsor

Each company also has the six 10-Q notes the screen reads (credit & covenants, leases / off-balance-sheet,
commitments & contingencies, derivatives & rate exposure, subsequent events, basis of presentation).
Overrides in NOTE_OVERRIDES give DRFT a tight covenant, GLEN going-concern + a class action, IRON an almost empty
revolver, JADE an off-balance-sheet guarantee, LUMN heavy floating-rate debt, EMBR / NOVA a subsequent event.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import (
    SECTION_8K,
    SECTION_BUSINESS,
    SECTION_DEBT,
    SECTION_MDA,
    SECTION_NOTE_BASIS,
    SECTION_NOTE_COMMIT,
    SECTION_NOTE_CREDIT,
    SECTION_NOTE_DERIV,
    SECTION_NOTE_LEASES,
    SECTION_NOTE_SUBSEQ,
    SECTION_RISK,
    Chunk,
    Event,
)

PERIODS = ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]
FYS = [2023, 2024, 2025]
Q_FILED = "2026-10-28"
K_FILED = "2026-02-20"

BASE_RISK = ("Our substantial indebtedness could adversely affect our financial condition and our ability to "
             "refinance maturing debt. Demand for our products can vary with economic conditions.")

# ticker: (name, sector, sponsor, ebitda_q, interest_q, debt_q, cash, ca, cl, annual, maturities)
# annual = [(ebitda, interest, debt, cfo, capex)] for FY2023..FY2025
SPECS: Dict[str, dict] = {
    "ALPH": dict(name="Alpine Components Inc.", sector="Industrials", sponsor=None,
                 e=[200, 205, 210, 215, 220], i=[30] * 5, d=[1500, 1480, 1460, 1440, 1420],
                 cash=400, ca=900, cl=600,
                 a=[(760, 120, 1600, 500, 150), (800, 120, 1550, 540, 150), (830, 120, 1480, 560, 160)],
                 m={2026: 100, 2027: 150, 2028: 200, 2029: 300, 2030: 700}),
    "BRCK": dict(name="Brickyard Foods Corp.", sector="Consumer Staples", sponsor="Cedar Partners",
                 e=[120, 122, 125, 128, 130], i=[20] * 5, d=[1000, 990, 985, 975, 960],
                 cash=250, ca=600, cl=420,
                 a=[(470, 80, 1100, 300, 90), (485, 80, 1050, 310, 90), (495, 80, 1010, 320, 95)],
                 m={2026: 60, 2027: 120, 2028: 150, 2029: 250, 2030: 380}),
    "CYAN": dict(name="Cyan Software Ltd.", sector="Technology", sponsor=None,
                 e=[300, 310, 320, 330, 340], i=[10] * 5, d=[800, 790, 780, 770, 760],
                 cash=900, ca=1500, cl=700,
                 a=[(1100, 40, 900, 800, 100), (1180, 40, 850, 860, 100), (1260, 40, 800, 920, 110)],
                 m={2026: 50, 2027: 60, 2028: 100, 2029: 200, 2030: 350}),
    "DRFT": dict(name="Drift Logistics Co.", sector="Industrials", sponsor=None,
                 e=[150, 145, 135, 125, 115], i=[30, 32, 34, 36, 38], d=[1800, 1850, 1900, 1950, 2000],
                 cash=800, ca=1000, cl=450,
                 a=[(600, 120, 1500, 380, 80), (570, 130, 1700, 340, 80), (540, 140, 1900, 300, 80)],
                 m={2026: 200, 2027: 300, 2028: 400, 2029: 500, 2030: 600}),
    "EMBR": dict(name="Ember Energy Partners", sector="Energy", sponsor=None,
                 e=[170, 175, 180, 190, 60], i=[25] * 5, d=[1200, 1200, 1190, 1190, 1200],
                 cash=500, ca=900, cl=600,
                 a=[(640, 100, 1300, 450, 200), (680, 100, 1250, 470, 200), (715, 100, 1200, 480, 200)],
                 m={2026: 100, 2027: 150, 2028: 200, 2029: 350, 2030: 400}),
    "FLUX": dict(name="Flux Retail Group", sector="Consumer Discretionary", sponsor=None,
                 e=[100, 100, 100, 100, 100], i=[40] * 5, d=[3600] * 5,
                 cash=200, ca=500, cl=450,
                 a=[(400, 160, 3500, 150, 90), (400, 160, 3550, 140, 90), (400, 160, 3600, 130, 90)],
                 m={2026: 300, 2027: 400, 2028: 600, 2029: 800, 2030: 1000}),
    "GLEN": dict(name="Glenview Health Systems", sector="Health Care", sponsor=None,
                 e=[90, 85, 80, 80, 75], i=[20] * 5, d=[900, 910, 920, 930, 940],
                 cash=150, ca=400, cl=350,
                 a=[(380, 80, 850, 180, 100), (350, 80, 880, 160, 100), (325, 80, 920, 120, 100)],
                 m={2026: 100, 2027: 120, 2028: 200, 2029: 250, 2030: 270}),
    "HRBR": dict(name="Harbor Media Holdings", sector="Communication Services", sponsor=None,
                 e=[140, 142, 145, 148, 150], i=[28] * 5, d=[1300, 1290, 1280, 1270, 1260],
                 cash=350, ca=700, cl=500,
                 a=[(540, 112, 1350, 330, 110), (560, 112, 1320, 340, 110), (585, 112, 1285, 350, 110)],
                 m={2026: 90, 2027: 130, 2028: 200, 2029: 350, 2030: 490}),
    "IRON": dict(name="Ironwood Materials Inc.", sector="Materials", sponsor=None,
                 e=[110, 112, 115, 115, 118], i=[22] * 5, d=[1500, 1500, 1500, 1500, 1500],
                 cash=250, ca=500, cl=420,
                 a=[(440, 88, 1500, 200, 120), (450, 88, 1500, 200, 120), (460, 88, 1500, 200, 120)],
                 m={2026: 300, 2027: 900, 2028: 300, 2029: 300, 2030: 200}),
    "JADE": dict(name="Jade Chemicals Corp.", sector="Materials", sponsor="Cedar Partners",
                 e=[160, 158, 162, 165, 168], i=[26] * 5, d=[1400, 1390, 1380, 1370, 1360],
                 cash=380, ca=850, cl=550,
                 a=[(620, 104, 1500, 390, 140), (630, 104, 1450, 400, 140), (645, 104, 1400, 410, 140)],
                 m={2026: 80, 2027: 140, 2028: 190, 2029: 400, 2030: 550}),
    "KITE": dict(name="Kite Telecom Inc.", sector="Communication Services", sponsor=None,
                 e=[130, 130, 132, 133, 135], i=[25] * 5, d=[1100, 1100, 1090, 1085, 1080],
                 cash=300, ca=600, cl=450,
                 a=[(500, 100, 1150, 320, 120), (510, 100, 1120, 325, 120), (520, 100, 1100, 330, 120)],
                 m={2026: 80, 2027: 100, 2028: 150, 2029: 300, 2030: 450}, q_filed="2026-03-15"),
    "LUMN": dict(name="Lumina Utilities Co.", sector="Utilities", sponsor=None,
                 e=[180, 182, 185, 187, 190], i=[35] * 5, d=[2000, 1990, 1980, 1970, 1960],
                 cash=300, ca=800, cl=650,
                 a=[(700, 140, 2100, 420, 250), (715, 140, 2050, 430, 250), (730, 140, 2000, 440, 250)],
                 m={2026: 100, 2027: 150, 2028: 250, 2029: 450, 2030: 1000}),
    "MRDN": dict(name="Meridian Industrial Corp.", sector="Industrials", sponsor=None,
                 e=[140, 142, 145, 147, 150], i=[24] * 5, d=[1340, 1345, 1350, 1348, 1350],
                 cash=320, ca=780, cl=520, unit_error=True,
                 a=[(560, 96, 1350, 340, 110), (570, 96, 1345, 345, 110), (580, 96, 1350, 350, 115)],
                 m={2026: 70, 2027: 130, 2028: 180, 2029: 320, 2030: 650}),
    "NOVA": dict(name="Nova Semiconductor Inc.", sector="Technology", sponsor="Cedar Partners",
                 e=[250, 255, 262, 270, 278], i=[15] * 5, d=[1000, 990, 985, 980, 975],
                 cash=700, ca=1300, cl=650,
                 a=[(950, 60, 1100, 650, 200), (1010, 60, 1050, 700, 200), (1075, 60, 1000, 740, 210)],
                 m={2026: 60, 2027: 90, 2028: 160, 2029: 300, 2030: 365}),
}

SCENARIO_RISK = {
    "GLEN": "Our recurring losses and negative operating cash flows raise substantial doubt about our ability to "
            "continue as a going concern.",
    "LUMN": "Our former subsidiary Lumina Retail, which we divested in 2024, disclosed substantial doubt about its "
            "ability to continue as a going concern prior to its sale.",
}
SCENARIO_MDA = {
    "DRFT": "Industry-wide freight demand has weakened, and our shipment volumes declined in line with the sector.",
    "EMBR": "Latest-quarter EBITDA included a one-time impairment charge related to the retirement of a processing "
            "facility. Management expects to recover the earnings impact as replacement capacity comes online.",
}


# Per-company overrides of the 10-Q notes (everything else is derived from the company's size).
#   rev = undrawn revolver ($M)   cmax/cact = net-leverage covenant limit / actual (x)   lease/commit = 2-year outflows ($M)
#   lit = reasonably possible litigation loss ($M)   float = floating-rate share of debt   shock = interest cost of +100bp ($M)
NOTE_OVERRIDES = {
    "DRFT": dict(cmax=4.4, cact=3.9),                      # covenant headroom is thin
    "GLEN": dict(lit=60.0, extra_commit="The Company is a defendant in a putative class action brought by former patients.",
                 basis_extra="Management has concluded there is substantial doubt about the Company's ability to "
                             "continue as a going concern."),
    "IRON": dict(rev=20.0, lease=90.0, commit=120.0),      # little liquidity left once the notes are counted
    "JADE": dict(commit=160.0, commit_extra=", including $120.0 million of guarantees of an unconsolidated joint venture"),
    "LUMN": dict(float_=0.55, lease=180.0),
    "EMBR": dict(subseq="Subsequent to quarter end, the Company began commissioning replacement processing capacity."),
    "NOVA": dict(subseq="Subsequent to quarter end, the Company repaid $200.0 million of outstanding term loans."),
}


def _notes(ticker: str, s: dict, true_debt: float) -> Dict[str, str]:
    o = NOTE_OVERRIDES.get(ticker, {})
    P = PERIODS[-1]
    ltm = sum(s["e"][-4:])
    cact = o.get("cact", round(true_debt / ltm, 1))
    cmax = o.get("cmax", round(max(4.5, cact + 2.0), 1))
    rev = o.get("rev", round(0.5 * s["cash"], 1))
    lease = o.get("lease", round(0.05 * true_debt, 1))
    commit = o.get("commit", round(0.03 * true_debt, 1))
    lit = o.get("lit", 0.0)
    fl = o.get("float_", 0.30)
    shock = round(0.01 * fl * true_debt, 1)
    return {
        SECTION_NOTE_CREDIT: f"Undrawn availability under the revolving credit facility was ${rev:,.1f} million as of {P}, "
                             f"and the facility matures in 2029. The credit agreement requires net leverage not to exceed "
                             f"{cmax:.1f}x; actual net leverage was {cact:.1f}x as of {P}.",
        SECTION_NOTE_LEASES: f"Operating lease obligations due within the next two years total ${lease:,.1f} million. "
                             f"The Company has not entered into any financing arrangements outside the consolidated balance sheet.",
        SECTION_NOTE_COMMIT: f"Purchase and other commitments due within the next two years total ${commit:,.1f} million"
                             f"{o.get('commit_extra', '')}. Reasonably possible litigation losses in excess of accruals were "
                             f"estimated at ${lit:,.1f} million." + (" " + o["extra_commit"] if o.get("extra_commit") else ""),
        SECTION_NOTE_DERIV: f"Floating-rate debt represented {fl * 100:.0f}% of total debt after hedging. A 100 basis point "
                            f"increase in interest rates would increase annual interest expense by ${shock:,.1f} million.",
        SECTION_NOTE_SUBSEQ: o.get("subseq", "Management evaluated events through the filing date and identified no "
                                             "material subsequent events."),
        SECTION_NOTE_BASIS: "The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. "
                            "Management evaluated the Company's ability to meet its obligations for twelve months from "
                            "issuance and identified no adverse conditions." + (" " + o["basis_extra"] if o.get("basis_extra") else ""),
    }


def _cik(i: int) -> str:
    return f"{1000000 + i:010d}"


def _cid(ticker: str, form: str, filed: str, section: str, tag: str = "") -> str:
    return f"{ticker.lower()}-{form.lower().replace('/', '')}-{filed}-{section}{tag}"


def _build(i: int, ticker: str, s: dict) -> dict:
    cik = _cik(i)
    q_filed = s.get("q_filed", Q_FILED)
    quarters = []
    for p, e, intr, d in zip(PERIODS, s["e"], s["i"], s["d"]):
        quarters.append(dict(period=p, ebitda=e, interest=intr, debt=d, cash=s["cash"], ca=s["ca"], cl=s["cl"]))
    if s.get("unit_error"):
        quarters[-1]["debt"] = quarters[-1]["debt"] * 1000   # the XBRL value that looks 1000x too large
    annual = [dict(fy=fy, ebitda=a[0], interest=a[1], debt=a[2], cfo=a[3], capex=a[4]) for fy, a in zip(FYS, s["a"])]
    true_debt = s["d"][-1]
    cov = s["e"][-1] / s["i"][-1]
    mats = ", ".join(f"{y} ${v:,.1f} million" for y, v in sorted(s["m"].items()))

    chunks: List[Chunk] = []

    def add(form, filed, section, text, superseded_by=None, tag=""):
        chunks.append(Chunk(_cid(ticker, form, filed, section, tag), cik, ticker, form, filed, section, text, superseded_by))

    add("10-K", K_FILED, SECTION_BUSINESS,
        f"{s['name']} is a {s['sector'].lower()} company that serves commercial customers in North America and Europe.")
    add("10-K", K_FILED, SECTION_RISK, BASE_RISK + (" " + SCENARIO_RISK[ticker] if ticker in SCENARIO_RISK else ""))
    mda = (f"Interest coverage, measured as EBITDA divided by interest expense, was {cov:.1f}x for the quarter "
           f"ended {PERIODS[-1]}. We had cash and cash equivalents of ${s['cash']:,.1f} million as of {PERIODS[-1]}.")
    if ticker in SCENARIO_MDA:
        mda += " " + SCENARIO_MDA[ticker]
    add("10-Q", q_filed, SECTION_MDA, mda)
    add("10-Q", q_filed, SECTION_DEBT,
        f"Total debt outstanding was ${true_debt:,.1f} million as of {PERIODS[-1]}. Scheduled maturities: {mats}.")

    for section, text in _notes(ticker, s, true_debt).items():
        add("10-Q", q_filed, section, text)

    if ticker == "ALPH":   # an older MD&A and an older credit note that must lose to the latest filing
        add("10-Q", "2026-05-06", SECTION_NOTE_CREDIT,
            "Undrawn availability under the revolving credit facility was $10.0 million as of 2026Q1, and the facility "
            "matures in 2029. The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 4.4x "
            "as of 2026Q1.", tag="-old")
        add("10-Q", "2026-05-06", SECTION_MDA,
            "Interest coverage, measured as EBITDA divided by interest expense, was 6.2x for the quarter ended "
            "2026Q1. We had cash and cash equivalents of $310.0 million as of 2026Q1.", tag="-old")
    if ticker == "BRCK":   # a superseded draft of the same filing
        add("10-Q", q_filed, SECTION_MDA,
            "Interest coverage, measured as EBITDA divided by interest expense, was 0.9x for the quarter ended "
            "2026Q3. We had cash and cash equivalents of $90.0 million as of 2026Q3.",
            superseded_by=_cid(ticker, "10-Q", q_filed, SECTION_MDA), tag="-draft")

    events: List[Event] = []
    if ticker == "HRBR":
        events.append(Event(cik, ticker, "2026-10-30", "2.04", "covenant_waiver",
                            "The company obtained a waiver of its maximum leverage covenant from its revolving lenders."))
    return dict(cik=cik, ticker=ticker, name=s["name"], sector=s["sector"], sponsor=s["sponsor"],
                filed={"10-Q": q_filed, "10-K": K_FILED}, quarters=quarters, annual=annual,
                maturities=dict(s["m"]), chunks=chunks, events=events)


def build_universe() -> Dict[str, dict]:
    uni = {t: _build(i, t, s) for i, (t, s) in enumerate(SPECS.items(), start=1)}
    for b in uni.values():     # 8-K events are searchable chunks too, so memos can cite them
        for e in b["events"]:
            b["chunks"].append(Chunk(e.chunk_id, e.cik, e.ticker, "8-K", e.filed, SECTION_8K, e.text))
    return uni
