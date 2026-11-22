"""Live-EDGAR client tests on fictional SEC-shaped data (no network): HTTP rules, XBRL mapping, filing text, end-to-end."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edgar_fixtures as fx  # noqa: E402

from forecaster import xbrl  # noqa: E402
from forecaster.cli import main  # noqa: E402
from forecaster.config import LIVE_CALCS, LIVE_SOUGHT, LIVE_TOPICS, ForecasterConfig  # noqa: E402
from forecaster.edgar import (  # noqa: E402
    EdgarError,
    EdgarNotFound,
    RealEdgar,
    SecHttp,
    check_user_agent,
    event_type,
    read_tickers_file,
)
from forecaster.evidence import real_topic_match, sentence_tags  # noqa: E402
from forecaster.filing_text import classify_note, extract_sections, find_items, html_to_lines, split_notes  # noqa: E402
from forecaster.models import (  # noqa: E402
    SECTION_BUSINESS,
    SECTION_DEBT,
    SECTION_MDA,
    SECTION_NOTE_CREDIT,
    SECTION_RISK,
    Status,
)
from forecaster.notes import real_parse  # noqa: E402
from forecaster.ratios import FactSet, MissingDataError, core_ratios  # noqa: E402
from forecaster.retrieval import ShardedIndex, focus  # noqa: E402
from forecaster.workflow import Forecaster  # noqa: E402

UA = "Test Analyst test.analyst@desk.org"


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make_http(sec, tmp_path=None, **kw):
    return SecHttp(UA, tmp_path, fetcher=sec.fetcher(), sleep=lambda s: None, **kw)


def build_sec():
    sec = fx.FakeSec()
    tenq, tenk = fx.tenq_html(), fx.tenk_html()
    sec.add_company("TSTA", 9000001, fx.make_facts(9000001), fx.make_subs(9000001, "TSTA"), tenq, tenk)
    sec.add_company("TSTB", 9000002, fx.make_facts(9000002, debt=(7000,) * 7, name="Testb Corp"),
                    fx.make_subs(9000002, "TSTB", "Testb Corp"), tenq, tenk)
    sec.add_company("TSTC", 9000003, fx.make_facts(9000003, name="Testc Inc"),
                    fx.make_subs(9000003, "TSTC", "Testc Inc", eight_k=("2026-10-30", "2.04,9.01")), tenq, tenk, fx.eightk_html())
    return sec


# --------------------------------------------------------------------------- HTTP rules
def test_user_agent_is_required_and_placeholder_rejected():
    assert check_user_agent(UA) == UA
    for bad in (None, "", "no contact", "Your Name your.email@example.com"):
        with pytest.raises(ValueError):
            check_user_agent(bad)


def test_rate_limit_holds_across_threads_and_cache_is_free(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    clock, stamps = Clock(), []

    def fetch(url, headers, timeout):
        stamps.append(clock.now())
        return 200, {}, b"{}"
    http = SecHttp(UA, tmp_path, min_interval=0.12, fetcher=fetch, sleep=clock.sleep, clock=clock.now)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda i: http.get(f"https://data.sec.gov/x/{i}"), range(40)))
    stamps.sort()
    assert http.requests == 40
    assert all(b - a >= 0.12 - 1e-9 for a, b in zip(stamps, stamps[1:])), "never faster than ~8 requests per second"
    http.get("https://data.sec.gov/x/3")                    # cached on disk: no request
    assert http.requests == 40 and http.cache_hits == 1


def test_retry_on_503_then_ok_and_errors_are_typed(tmp_path):
    sec = fx.FakeSec()
    sec.routes["https://data.sec.gov/ok"] = b"1"
    sec.fail_once["https://data.sec.gov/ok"] = 2
    http = make_http(sec, tmp_path)
    assert http.get("https://data.sec.gov/ok") == b"1" and http.requests == 3
    with pytest.raises(EdgarNotFound):
        http.get("https://data.sec.gov/missing")

    def forbidden(url, headers, timeout):
        return 403, {}, b""
    with pytest.raises(EdgarError, match="SEC_USER_AGENT"):
        SecHttp(UA, None, fetcher=forbidden, sleep=lambda s: None).get("https://data.sec.gov/z")


# --------------------------------------------------------------------------- filing text
def test_html_to_lines_drops_hidden_xbrl_and_joins_table_cells():
    lines = html_to_lines(fx.tenq_html())
    joined = "\n".join(lines)
    assert "HIDDEN-XBRL-CONTEXT" not in joined
    assert "Cash and cash equivalents | 400.0 | 380.0" in lines            # "$" cells dropped, cells joined
    assert "Net loss | (12.5)" in lines                                        # "(" | "12.5" | ")" repaired


def test_items_pick_the_body_not_the_table_of_contents():
    lines = html_to_lines(fx.tenq_html())
    items = find_items(lines)
    start, end = items["2"]
    assert "Liquidity and Capital Resources" in "\n".join(lines[start:end])   # the MD&A body, not the one-line ToC entry
    assert end - start > 3


def test_notes_are_split_and_classified_and_debt_feeds_two_sections():
    sec = extract_sections("10-Q", fx.tenq_html())
    assert [k for _t, k in sec["_notes"]] == ["basis", "debt", "leases", "commitments", "derivatives", "subsequent"]
    assert "total debt outstanding was $1,400.0 million" in sec[SECTION_DEBT]
    assert sec[SECTION_DEBT] == sec[SECTION_NOTE_CREDIT]
    assert "hypothetical 100 basis point" in sec["note_derivatives"]           # Item 3 market risk joins the derivatives note
    assert "Liquidity and Capital Resources" in sec[SECTION_MDA]
    assert SECTION_RISK not in sec                                             # 10-Q: business / risk come from the 10-K


def test_10k_gives_business_risk_and_mda():
    sec = extract_sections("10-K", fx.tenk_html())
    assert "industrial customers" in sec[SECTION_BUSINESS]
    assert any("substantial indebtedness" in p for p in sec[SECTION_RISK])
    assert "cash and cash equivalents of $380.0 million" in sec[SECTION_MDA]


def test_note_titles_and_numbered_chain():
    assert classify_note("Commitments and Contingencies") == "commitments"
    assert classify_note("Long-Term Debt") == "debt" and classify_note("Leases") == "leases"
    assert classify_note("Subsequent Events") == "subsequent" and classify_note("Segment Information") is None
    assert split_notes(["2025. Results were good", "Some text", "text"]) == []     # a stray number is not a note chain


# --------------------------------------------------------------------------- XBRL mapping
def test_bundle_quarters_ebitda_debt_and_derived_fourth_quarter():
    b = xbrl.build_bundle(fx.make_facts(9000001), fx.make_subs(9000001, "TSTA"), "TSTA")
    assert [q["period"] for q in b["quarters"]] == ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    q4 = b["quarters"][1]                                      # 2025Q4 exists only as FY2025 minus 9 months
    assert q4["ebitda"] == pytest.approx(205.0)                # EBIT 185 + D&A 20, both derived from YTD differences
    assert b["quarters"][-1]["ebitda"] == pytest.approx(220.0)
    assert b["quarters"][-1]["debt"] == pytest.approx(1400.0)  # noncurrent 1350 + current 50
    assert b["quarters"][-1]["interest"] == pytest.approx(30.0)
    assert [a["fy"] for a in b["annual"]] == [2023, 2024, 2025]
    assert b["annual"][-1]["ebitda"] == pytest.approx(790.0) and b["annual"][-1]["capex"] == pytest.approx(160.0)
    assert b["maturities"][2026] == 100.0 and b["maturities"][2030] == 700.0
    assert b["sector"] == "Industrials" and b["filed"] == {"10-Q": "2026-10-28", "10-K": "2026-02-20"}
    assert any("EBITDA = EBIT" in n for n in b["data_notes"])


def test_bundle_feeds_the_existing_ratios():
    b = xbrl.build_bundle(fx.make_facts(9000001), fx.make_subs(9000001, "TSTA"), "TSTA")
    r = core_ratios(FactSet(b["cik"], b), 2, 2026)
    assert r["leverage_ltm"].value == pytest.approx(1400.0 / (205 + 210 + 215 + 220))
    assert r["coverage_q"].value == pytest.approx(220 / 30)


def test_no_da_anywhere_gives_ebit_but_partial_da_stays_unknown():
    facts = fx.make_facts(9000001)
    del facts["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]               # e.g. a small biotech: no D&A reported
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert [q["ebitda"] for q in b["quarters"]][-1] == pytest.approx(220 - 20)           # = EBIT, conservative
    assert any("EBITDA = EBIT (conservative)" in n for n in b["data_notes"])
    facts = fx.make_facts(9000001)                                                       # D&A reported, but not for the latest quarter
    node = facts["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]["units"]["USD"]
    node[:] = [e for e in node if not str(e["end"]).startswith("2026-09")]
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert [q["ebitda"] for q in b["quarters"]][-1] == pytest.approx(220 - 20), "partly missing D&A: EBIT only, never mixed"
    assert any("D&A is missing for some recent quarters" in n for n in b["data_notes"])


def test_a_tag_switch_between_10k_and_10q_still_gives_every_quarter():
    facts = fx.make_facts(9000001)
    node = facts["facts"]["us-gaap"]["DepreciationDepletionAndAmortization"]["units"]["USD"]
    full_year = [e for e in node if 340 <= (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days <= 380]
    node[:] = [e for e in node if e not in full_year]
    facts["facts"]["us-gaap"]["DepreciationAndAmortization"] = {"units": {"USD": full_year}}   # the 10-K uses another tag
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert all(q["ebitda"] is not None for q in b["quarters"]), [q["ebitda"] for q in b["quarters"]]


def test_interest_falls_back_to_cash_paid_then_net_interest():
    facts = fx.make_facts(9000001)
    us = facts["facts"]["us-gaap"]
    exp = us.pop("InterestExpense")
    us["InterestPaidNet"] = exp
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["interest"] == pytest.approx(30.0)
    assert any("cash paid" in n for n in b["data_notes"])
    us.pop("InterestPaidNet")
    net = [dict(e, val=-e["val"]) for e in exp["units"]["USD"]]                         # net interest EXPENSE is negative income
    us["InterestIncomeExpenseNonoperatingNet"] = {"units": {"USD": net}}
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["interest"] == pytest.approx(30.0) and any(" net " in n or "net " in n for n in b["data_notes"])


def test_debt_is_found_under_the_other_common_tags():
    facts = fx.make_facts(9000001)
    us = facts["facts"]["us-gaap"]
    us["LongTermDebtAndCapitalLeaseObligations"] = us.pop("LongTermDebtNoncurrent")
    us["LongTermDebtAndCapitalLeaseObligationsCurrent"] = us.pop("LongTermDebtCurrent")
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["debt"] == pytest.approx(1400.0)


def test_a_company_with_no_borrowings_at_all_has_zero_debt_but_a_silent_one_with_interest_does_not():
    facts = fx.make_facts(9000001, interest=0.0)
    us = facts["facts"]["us-gaap"]
    for t in ("LongTermDebtNoncurrent", "LongTermDebtCurrent") + tuple(k for k in us if "Maturities" in k):
        us.pop(t)
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["debt"] == 0.0 and any("TotalDebt taken as 0" in n for n in b["data_notes"])
    facts = fx.make_facts(9000001, interest=30.0)                                       # pays interest but its debt tag is unknown
    us = facts["facts"]["us-gaap"]
    for t in ("LongTermDebtNoncurrent", "LongTermDebtCurrent") + tuple(k for k in us if "Maturities" in k):
        us.pop(t)
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["debt"] is None, "interest without any debt tag means unknown debt, not zero debt"


def test_52_53_week_calendars_do_not_collide_on_quarter_labels():
    ends = ["2026-01-03", "2026-04-18", "2026-07-11", "2026-10-03", "2027-01-02", "2026-07-18", "2026-10-10"]
    labels = [xbrl.qlabel(date.fromisoformat(e)) for e in ends[:5]]
    assert len(set(labels)) == 5, labels
    assert xbrl.qlabel(date.fromisoformat("2026-07-18")) != xbrl.qlabel(date.fromisoformat("2026-10-10"))


def test_a_company_without_quarters_is_missing_data_not_a_crash(tmp_path):
    b = RealEdgar(["TSTA"], make_http(build_sec(), tmp_path / "c")).facts("TSTA")
    fs = FactSet(b["cik"], dict(b, quarters=[]))
    from forecaster.ratios import compute_ratio
    for name in ("leverage_ltm", "coverage_q", "coverage_ltm", "current_ratio"):
        with pytest.raises(MissingDataError):
            compute_ratio(fs, name)


def test_missing_cash_flow_data_costs_a_company_only_after_the_rating_gate(tmp_path):
    def run(cik, tick, target, **fk):
        facts = fx.make_facts(cik, **fk)
        facts["facts"]["us-gaap"].pop("NetCashProvidedByUsedInOperatingActivities")        # no cash flow: liquidity cover unknown
        sec = build_sec()
        sec.add_company(tick, cik, facts, fx.make_subs(cik, tick), fx.tenq_html(), fx.tenk_html())
        fc = Forecaster(ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / tick), target_ratings=target),
                        source=RealEdgar([tick], make_http(sec, tmp_path / ("c" + tick))), llm=type("NoLLM", (), {"enabled": False})())
        return fc.screen_company(tick).scorecard
    outside = run(9100001, "TSTX", ("BBB", "BBB_plus"), debt=(3600,) * 7)                             # implied BB: out of the target first
    assert outside.skip_reason == "rating" and outside.status == "prescreen_fail"
    inside = run(9100002, "TSTY", ("BBB", "BBB_plus"))                                                # implied BBB: now the missing capex shows
    assert inside.skip_reason == "missing_facts" and any("OperatingCashFlow" in m for m in inside.rationale)


def test_unreported_maturities_give_a_flagged_harsh_proxy_never_a_veto_or_zero():
    from forecaster.ratios import compute_ratio
    facts = fx.make_facts(9000001)
    for tag in [t for t in facts["facts"]["us-gaap"] if "Maturities" in t]:
        del facts["facts"]["us-gaap"][tag]
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["maturities"] == {}
    liq = compute_ratio(FactSet(b["cik"], b), "liquidity_cover", horizon=2, as_of_year=2026)
    assert liq.params.get("proxy") is True and "TOTAL debt" in liq.formula
    assert liq.value == pytest.approx((400.0 + 2 * (560.0 - 160.0)) / 1400.0)      # (cash + 2 x FCF) / TOTAL debt
    r = core_ratios(FactSet(b["cik"], b), 2, 2026)                      # the other ratios do not need the schedule
    assert "leverage_ltm" in r and "liquidity_cover" in r


def test_a_maturity_table_older_than_a_year_is_not_used():
    facts = fx.make_facts(9000001)
    for tag, node in facts["facts"]["us-gaap"].items():
        if "Maturities" in tag:
            for e in node["units"]["USD"]:
                e["end"] = "2024-06-30"
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["maturities"] == {} and any("too old to use" in n for n in b["data_notes"])


def test_a_16_week_first_quarter_and_uneven_calendars_still_give_unique_consecutive_quarters():
    from datetime import timedelta
    # an auto-parts retailer: FY ends Jan 3; Q1 = 16 weeks (Apr 25), Q2 and Q3 = 12 weeks (Jul 18, Oct 10); Q4 ends Jan 2
    ends = [date(2025, 7, 19), date(2025, 10, 11), date(2026, 1, 3), date(2026, 4, 25), date(2026, 7, 18), date(2026, 10, 10)]

    def flow_e(start, end, v):
        return {"start": start.isoformat(), "end": end.isoformat(), "val": v * 1e6, "form": "10-Q",
                "filed": (end + timedelta(days=30)).isoformat(), "accn": f"a-{end}-{start}"}

    def inst_e(end, v):
        return {"end": end.isoformat(), "val": v * 1e6, "form": "10-Q", "filed": (end + timedelta(days=30)).isoformat(), "accn": f"i-{end}"}
    starts = [e - timedelta(days=84) for e in ends]
    starts[3] = ends[3] - timedelta(days=112)
    us = {"OperatingIncomeLoss": [flow_e(st, en, 50) for st, en in zip(starts, ends)],
          "DepreciationDepletionAndAmortization": [flow_e(st, en, 10) for st, en in zip(starts, ends)],
          "InterestExpense": [flow_e(st, en, 20) for st, en in zip(starts, ends)],
          "LongTermDebt": [inst_e(en, 1000) for en in ends], "AssetsCurrent": [inst_e(en, 900) for en in ends]}
    facts = {"cik": 9200001, "facts": {"us-gaap": {t: {"units": {"USD": v}} for t, v in us.items()}}}
    subs = fx.make_subs(9200001, "TSTR")
    subs["filings"]["recent"]["reportDate"] = ["2026-10-10"] * len(subs["filings"]["recent"]["form"])
    b = xbrl.build_bundle(facts, subs, "TSTR")
    labels = [q["period"] for q in b["quarters"]]
    assert len(labels) == 5 and len(set(labels)) == 5, labels
    nums = [int(x[:4]) * 4 + int(x[-1]) for x in labels]
    assert nums == list(range(nums[0], nums[0] + 5)), "labels must be consecutive quarters"
    assert all(q["ebitda"] == pytest.approx(60.0) for q in b["quarters"]), [q["ebitda"] for q in b["quarters"]]


def test_sector_from_sic_and_labels():
    assert xbrl.sector_from_sic("2834") == "Health Care" and xbrl.sector_from_sic(7372) == "Technology"
    assert xbrl.sector_from_sic("4911") == "Utilities" and xbrl.sector_from_sic("6021") == "Financials"
    assert xbrl.sector_from_sic(None) == "Unknown"
    assert xbrl.qlabel(date(2025, 12, 28)) == "2025Q4" and xbrl.qlabel(date(2026, 1, 3)) == "2025Q4"


# --------------------------------------------------------------------------- the source
def test_real_edgar_facts_chunks_events(tmp_path):
    sec = build_sec()
    src = RealEdgar(["TSTA", "TSTC"], make_http(sec, tmp_path))
    assert src.facts("TSTA")["quarters"][-1]["ebitda"] == pytest.approx(220.0)
    cik = src.facts("TSTA")["cik"]
    forms = {(c.section, c.form) for c in src.chunks_for_cik(cik)}
    assert (SECTION_DEBT, "10-Q") in forms and (SECTION_BUSINESS, "10-K") in forms and (SECTION_RISK, "10-K") in forms
    ev = src.events("TSTC", "2026-10-28")
    assert [(e.item, e.type) for e in ev] == [("2.04", "default")]
    assert src.events("TSTC", "2026-10-30") == []                            # only events AFTER the given date
    with pytest.raises(EdgarNotFound):
        src.facts("NOPE")


def test_event_types():
    assert event_type("4.02", "x") == "restatement" and event_type("1.03", "x") == "bankruptcy"
    assert event_type("2.04", "lenders granted a waiver") == "covenant_waiver"
    assert event_type("1.01", "Agreement and Plan of Merger") == "merger"
    assert event_type("1.01", "an amended credit agreement") == "other"
    assert event_type("8.01", "Moody's lowered its rating") == "downgrade"


def test_sharded_index_loads_a_company_once_and_serves_the_same_api(tmp_path):
    sec = build_sec()
    src = RealEdgar(["TSTA"], make_http(sec, tmp_path))
    cik = src.facts("TSTA")["cik"]
    calls = []
    idx = ShardedIndex(lambda c: calls.append(c) or src.chunks_for_cik(c))
    hits = idx.search("revolving credit facility availability", cik=cik, sections=[SECTION_NOTE_CREDIT], k=3)
    assert hits and idx.get(hits[0].chunk.chunk_id) is hits[0].chunk and not idx.is_stale(hits[0].chunk)
    idx.section_chunks(cik, SECTION_DEBT)
    assert calls == [cik]


def test_full_screen_on_live_shaped_data_reads_text_only_for_survivors(tmp_path):
    sec = build_sec()
    cfg = ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / "state"), scan_risk_factors=False,
                           required_topics=LIVE_TOPICS, required_calcs=LIVE_CALCS, sought_topics=LIVE_SOUGHT)
    src = RealEdgar(["TSTA", "TSTB", "TSTC"], make_http(sec, tmp_path / "cache"))
    fc = Forecaster(cfg, source=src, llm=type("NoLLM", (), {"enabled": False})())
    res = {r.scorecard.ticker: r.scorecard for r in fc.screen_all()}
    assert res["TSTB"].status == Status.PRESCREEN_FAIL.value                 # leverage 7000 / ~840 fails the XBRL pre-screen
    assert res["TSTC"].status == Status.UNCERTAIN.value and "event_freeze" in res["TSTC"].flags   # post-filing default
    a = res["TSTA"]
    assert a.status == Status.INCLUDE.value and a.metrics["leverage_ltm"] == pytest.approx(1400 / 850, abs=0.01)
    assert a.metrics["covenant_headroom"] == pytest.approx((4.5 - 3.2) / 4.5, abs=1e-3)       # read from the credit note's prose
    quotes = [c.quote for r in fc.screen_all() if r.scorecard.ticker == "TSTA" for c in r.claims if c.kind == "fact"]
    assert quotes and all("\n" not in q for q in quotes), "a quote is one sentence, never a heading plus a sentence"
    assert a.sector == "Industrials"
    docs = [u for u in sec.log if "/Archives/edgar/data/" in u]
    assert docs and all("/9000001/" in u or "/9000003/" in u for u in docs), "no filing text for a pre-screen failure"
    assert not any("/9000002/" in u for u in docs)
    assert {c.section for c in fc.index.company_chunks(a.cik)} >= {SECTION_MDA, SECTION_DEBT, SECTION_NOTE_CREDIT}


# --------------------------------------------------------------------------- real-text rules
def test_real_topic_patterns_and_going_concern_guard():
    assert real_topic_match("liquidity", "We had cash and cash equivalents of $400.0 million.")
    assert not real_topic_match("liquidity", "Cash and cash equivalents are held at several banks.")
    assert real_topic_match("credit", "We had availability under our revolving credit facility.")
    assert sentence_tags("Our recurring losses raise substantial doubt about our ability to continue as a going concern.") \
        == ["going_concern"]
    for boiler in ("Management evaluated whether there is substantial doubt about going concern and concluded there is none.",
                   "Any failure could raise substantial doubt about our ability to continue as a going concern.",
                   "There is no substantial doubt about our ability to continue as a going concern."):
        assert "going_concern" not in sentence_tags(boiler)


def test_real_note_sentences_are_read_with_units():
    t = ("As of September 30, 2026, we had $450.0 million of availability under our revolving credit facility. "
         "The credit agreement requires a net leverage ratio not to exceed 4.50 to 1.00. Our net leverage ratio was 3.2 to 1.00. "
         "The reasonably possible loss is up to $15.0 million. A 100 basis point increase would increase interest expense by $4.2 million.")
    assert real_parse("RevolverAvailability", t) == 450.0
    assert real_parse("CovenantMax", t) == 4.5 and real_parse("CovenantActual", t) == 3.2
    assert real_parse("LitigationPossibleLoss", t) == 15.0 and real_parse("RateShockInterest", t) == 4.2
    assert real_parse("RevolverAvailability", "We had $450 of availability under the revolver.") is None   # no unit: not guessed
    assert real_parse("LeaseObligations2Y", t) is None


def test_focus_keeps_the_relevant_sentences_of_a_long_note():
    long = " ".join(f"Unrelated filler sentence {i} about nothing." for i in range(400)) + \
        " The revolving credit facility had $450.0 million of availability."
    out = focus(long, ["revolving", "availability"], 400)
    assert "$450.0 million of availability" in out and len(out) <= 400
    assert focus("short text", ["x"], 400) == "short text"


# --------------------------------------------------------------------------- CLI
def test_cli_edgar_check_and_screen_with_the_fake_sec(tmp_path, monkeypatch, capsys):
    sec = build_sec()
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    state = str(tmp_path / "state")
    assert main(["--state-dir", state, "edgar-check", "TSTA", "--as-of", "2026-11-01"]) == 0
    out = capsys.readouterr().out
    assert "Quarters ($M)" in out and "2026Q3" in out and "debt_footnote" in out and "Ratios: leverage_ltm" in out
    assert "'RevolverAvailability': 450.0" in out and "'RateShockInterest': 4.2" in out   # rule parser read real-style prose
    tf = tmp_path / "universe.txt"
    tf.write_text("# my universe\nTSTA\nTSTB, comment\n")
    assert read_tickers_file(str(tf)) == ["TSTA", "TSTB"]
    rc = main(["--state-dir", state, "screen", "--source", "edgar", "--tickers-file", str(tf), "--as-of", "2026-11-01",
               "--no-llm", "--output", str(tmp_path / "r.md")])
    assert rc == 0 and json.loads((tmp_path / "r.json").read_text())["scorecards"]["TSTB"]["status"] == "prescreen_fail"
    assert "EDGAR: 2 companies" in capsys.readouterr().err


@pytest.fixture(autouse=True)
def _no_dotenv_no_network(monkeypatch):
    """Tests must not read the developer's real .env (it may hold a real SEC_USER_AGENT) or reach sec.gov."""
    import forecaster.cli as cli_mod
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(cli_mod, "load_env", lambda *a, **k: None)

    def _blocked(*a, **k):
        raise AssertionError("a test tried to reach the real network")
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", _blocked)


def test_cli_screen_edgar_needs_a_universe_and_an_email(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SystemExit):
        main(["--state-dir", str(tmp_path), "screen", "--source", "edgar", "--tickers", "TSTA"])
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    with pytest.raises(SystemExit):
        main(["--state-dir", str(tmp_path), "screen", "--source", "edgar"])


def test_default_required_evidence_is_strict_and_live_set_is_the_documented_relaxation(tmp_path):
    sec = build_sec()
    cfg = ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / "s"), scan_risk_factors=False)   # synthetic-universe defaults
    fc = Forecaster(cfg, source=RealEdgar(["TSTA"], make_http(sec, tmp_path / "c")), llm=type("NoLLM", (), {"enabled": False})())
    a = fc.screen_company("TSTA").scorecard
    assert a.status == Status.UNCERTAIN.value                  # maturities / lease tables are not sentences: evidence incomplete
    assert "fact:maturities" in a.missing
    assert set(LIVE_TOPICS) < set(cfg.required_topics) and set(LIVE_CALCS) < set(cfg.required_calcs)
    assert set(LIVE_SOUGHT) <= set(cfg.required_topics), "live runs still LOOK for these, they are just not required"


def test_llm_path_runs_on_live_shaped_data(tmp_path):
    """ReAct loop + LLM note extraction over lazily loaded real-shaped filings: no crash, quotes still verified."""
    from test_forecaster import FakeLLM
    from forecaster.llm import LLMGate
    sec = build_sec()
    cfg = ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / "s"), scan_risk_factors=False,
                           required_topics=LIVE_TOPICS, required_calcs=LIVE_CALCS, sought_topics=LIVE_SOUGHT)
    fc = Forecaster(cfg, source=RealEdgar(["TSTA"], make_http(sec, tmp_path / "c")), llm=FakeLLM())
    a = fc.screen_company("TSTA").scorecard
    assert a.status in (Status.INCLUDE.value, Status.UNCERTAIN.value) and a.cik == "0009000001"
    assert fc.audit.count("react_stop") >= 1


# --------------------------------------------------------------------------- RegCap / CLO mode, automatic universe
def _bulk(tmp_path):
    """Seven fictional companies in SEC's bulk-file layout; five are listed non-financials with a recent filing."""
    sec = build_sec()
    spec = [  # cik, ticker, facts kwargs, subs kwargs
        (9000001, "TSTA", {}, {}),                                                        # BBB: leverage 1.6x, coverage 7x
        (9000004, "TSTD", dict(debt=(500,) * 7, interest=15.0), {}),                      # A or better: 0.6x, 15x
        (9000005, "TSTE", dict(debt=(3600,) * 7), dict(exchanges=("Nasdaq",))),           # BB: 4.2x
        (9000006, "TSTF", {}, dict(sic="6021")),                                          # a bank: out of the universe
        (9000007, "TSTG", {}, dict(exchanges=("OTC",))),                                  # not NYSE / Nasdaq
        (9000008, "", {}, {}),                                                            # no ticker
        (9000009, "TSTI", {}, dict(stale=True)),                                          # last filing over a year old
    ]
    companies = []
    for cik, tick, fk, sk in spec:
        facts, subs = fx.make_facts(cik, name=f"Co {cik}", **fk), fx.make_subs(cik, tick, f"Co {cik}", **sk)
        companies.append((cik, facts, subs))
        sec.add_company(tick or f"X{cik}", cik, facts, subs, fx.tenq_html(), fx.tenk_html())
    fz, sz = fx.write_bulk_zips(tmp_path, companies)
    return sec, fz, sz


def test_rating_categories_and_symbols(tmp_path):
    from forecaster.rating import MODES, implied_category, load_ratings, parse_rating
    assert implied_category(1.6, 7.3) == "BBB_plus" and implied_category(0.35, 14.7) == "A" and implied_category(0.4, 20.0) == "AA_or_better"
    assert implied_category(3.6, 4.5) == "BBB" and implied_category(2.9, 6.5) == "BBB_plus"
    assert implied_category(4.2, 7.3) == "BB" and implied_category(2.0, 1.5) == "B" and implied_category(12, 9) == "CCC"
    assert implied_category(1.0, 3.5) == "BB"                    # the weaker of leverage and coverage decides
    assert [parse_rating(x) for x in ("BBB-", "Baa2", "A+", "BB+ (stable)", "B3", "Caa1", "AAA", "nonsense", "BBB+", "Baa1", "BBB")] == \
        ["BBB", "BBB", "A", "BB", "B", "CCC", "AA_or_better", None, "BBB_plus", "BBB_plus", "BBB"]
    f = tmp_path / "r.csv"
    f.write_text("ticker,rating\nTSTE,BBB-\nTSTA,Baa1\n")
    assert load_ratings(str(f)) == {"TSTE": "BBB", "TSTA": "BBB_plus"}
    f.write_text("TSTE,XYZ\n")
    with pytest.raises(ValueError):
        load_ratings(str(f))
    assert MODES["regcap"] == ("AA_or_better", "A", "BBB_plus") and MODES["clo"] == ("A", "BBB_plus", "BBB", "BB")
    assert "AA_or_better" not in MODES["clo"], "A is the ceiling for a CLO"


def test_universe_is_built_from_the_bulk_files_alone(tmp_path):
    from forecaster.universe import build_universe
    sec, fz, sz = _bulk(tmp_path)
    src = RealEdgar([], make_http(sec, tmp_path / "c"), fz, sz)
    assert len(src.all_ciks()) == 7
    uni = build_universe(src.all_ciks(), src.read_subs, "2026-11-01", progress=False)
    assert sorted(uni["tickers"]) == ["TSTA", "TSTD", "TSTE"]
    f = uni["funnel"]
    assert (f["companies with XBRL facts"], f["listed on NYSE/Nasdaq with a ticker"], f["sector kept"],
            f["latest filing recent enough"]) == (7, 5, 4, 3)
    fin = build_universe(src.all_ciks(), src.read_subs, "2026-11-01", include_financials=True, progress=False)
    assert "TSTF" in fin["tickers"]
    assert sec.log == [] or all("sec.gov/Archives" not in u for u in sec.log), "the bulk files need no request"


def _screen_args(tmp_path, fz, sz, *extra):
    return ["--state-dir", str(tmp_path / "state"), "screen", "--mode", extra[0], "--maturity", "2028-12-31",
            "--as-of", "2026-11-01", "--facts-zip", fz, "--submissions-zip", sz, "--no-llm",
            "--output", str(tmp_path / f"{extra[0]}.md"), *extra[1:]]


def test_regcap_and_clo_need_only_a_mode_and_a_maturity(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)

    assert main(_screen_args(tmp_path, fz, sz, "regcap", "--min-ebitda", "0", "--min-leverage", "0")) == 0   # tiny fixtures
    reg = json.loads((tmp_path / "regcap.json").read_text())
    assert reg["shortlist"] == ["TSTA", "TSTD"] or sorted(reg["shortlist"]) == ["TSTA", "TSTD"]
    assert reg["scorecards"]["TSTE"]["status"] == "prescreen_fail" and "BB" in reg["scorecards"]["TSTE"]["rationale"][0]
    text = (tmp_path / "regcap.md").read_text()
    assert "RegCap" in text and "IMPLIED" in text and "2028-12-31" in text and "Synthetic demo" not in text
    assert "horizon 3" in text                                                 # 2026-11-01 -> 2028-12-31 is 2.17 years -> 3

    state = tmp_path / "state" / "universe"
    assert list(state.glob("universe_2026-11-01.json")), "the universe is cached after the first run"
    assert main(_screen_args(tmp_path, fz, sz, "clo")) == 0
    clo = json.loads((tmp_path / "clo.json").read_text())
    assert sorted(clo["shortlist"]) == ["TSTA", "TSTD", "TSTE"]                # CLO: up to A, a little below BBB is fine
    assert main(_screen_args(tmp_path, fz, sz, "clo", "--target-ratings", "BBB")) == 0     # an IMPLIED rating gates by group:
    same_group = json.loads((tmp_path / "clo.json").read_text())                           # A and BBB+ are one group, not resolvable
    assert sorted(same_group["shortlist"]) == ["TSTA", "TSTD"] and same_group["scorecards"]["TSTE"]["status"] == "prescreen_fail"
    assert any("group" in r for r in same_group["scorecards"]["TSTA"]["rationale"])
    assert "10-Q note figures missing" not in " ".join(same_group["scorecards"]["TSTA"]["review_reasons"]), "optional in a live run"

    ratings = tmp_path / "ratings.csv"
    ratings.write_text("ticker,rating\nTSTE,Baa3\nTSTD,AA\n")                 # a real rating beats the implied category, notch-exact
    assert main(_screen_args(tmp_path, fz, sz, "clo", "--target-ratings", "BBB", "--ratings-file", str(ratings))) == 0
    assert sorted(json.loads((tmp_path / "clo.json").read_text())["shortlist"]) == ["TSTA", "TSTE"]
    err = capsys.readouterr().err
    assert "Universe: 3 companies" in err


def test_mode_needs_a_maturity_in_the_future(tmp_path, monkeypatch):
    sec, fz, sz = _bulk(tmp_path)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    base = ["--state-dir", str(tmp_path / "s"), "screen", "--mode", "clo", "--as-of", "2026-11-01", "--facts-zip", fz,
            "--submissions-zip", sz, "--no-llm"]
    with pytest.raises(SystemExit, match="--maturity"):
        main(base)
    with pytest.raises(SystemExit, match="not after"):
        main(base + ["--maturity", "2026-01-01"])


def test_import_ratings_matches_names_and_skips_unusable(tmp_path, monkeypatch):
    from forecaster.ratings_import import latest_ratings, match, norm_name, sec_names
    assert norm_name("Testa Holdings, Inc.") == norm_name("TESTA HOLDINGS CORPORATION") == "TESTA HOLDINGS"
    f = tmp_path / "ej.csv"
    f.write_text("issuer_name,rating,rating_action_date,rating_type_term\n"
                 '"Co 9000001, Inc.",BBB+,2024-01-01,Long-term\n"Co 9000001, Inc.",BBB-,2025-03-01,Long-term\n'
                 '"Co 9000001, Inc.",A1,2025-09-01,Short-term\n"Co 9000004",A-,2025-02-01,Long-term\n'
                 '"Co 9000005",NR,2025-02-01,Long-term\n"Unknown Corp",BB,2025-02-01,Long-term\n')
    rated = latest_ratings(str(f))
    assert rated["Co 9000001, Inc."] == ("BBB-", "2025-03-01") and "Co 9000005" not in rated        # latest long-term; NR skipped
    sec, fz, sz = _bulk(tmp_path)
    src = RealEdgar([], make_http(sec, tmp_path / "c"), fz, sz)
    got, stat = match(rated, sec_names({"TSTA": "0009000001", "TSTD": "0009000004"}, src.read_subs))
    assert {t: v[0] for t, v in got.items()} == {"TSTA": "BBB-", "TSTD": "A-"} and stat["no SEC name match"] == 1
    amb = match({"Dup Co": ("BB", "2025-01-01")}, {"DUP": ["AAA", "BBB"]})[1]
    assert amb["ambiguous name"] == 1


def test_calibration_compares_and_improves_bands():
    from forecaster.calibrate import collect, report, score, suggest
    rows = [("A1", "A", 1.0, 14.0), ("B1", "BBB", 2.5, 6.0), ("B2", "BBB", 3.0, 5.5), ("B3", "BBB", 4.4, 4.2),
            ("C1", "BB", 4.8, 3.5), ("C2", "BB", 5.5, 3.2), ("D1", "B", 6.5, 2.0), ("D2", "B", 6.8, 2.2), ("E1", "CCC", 9.0, 0.8)]
    exact, near = score(rows)
    assert 0 < exact < 1 and near >= exact
    sug = suggest(rows)
    assert score(rows, sug) >= (exact, near)
    txt = report(rows)
    assert "Calibration on 9 companies" in txt and "Suggested bands" in txt and "25% / median / 75%" in txt
    from forecaster.calibrate import ordered
    assert ordered(sug), "no category may collapse onto its neighbour"
    assert not ordered({"AA_or_better": (1, 15), "A": (2, 10), "BBB_plus": (7, 4), "BBB": (7, 4), "BB": (7, 4), "B": (7, 1.75)})
    assert collect({"X": "BBB", "Y": "BB", "Z": "B"}, lambda t: {"X": (2.0, 6.0), "Y": (-1.0, 3.0), "Z": None}[t]) == [("X", "BBB", 2.0, 6.0)]


def test_calibrate_command_runs_on_bulk_files(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    r = tmp_path / "r.csv"
    r.write_text("ticker,rating\nTSTA,BBB+\nTSTD,A\nTSTE,BB+\n")
    assert main(["--state-dir", str(tmp_path / "s"), "calibrate-ratings", str(r), "--as-of", "2026-11-01",
                 "--facts-zip", fz, "--submissions-zip", sz]) == 0
    out = capsys.readouterr().out
    assert "Calibration on 3 companies" in out and "same category 100%" in out


def test_ltm_coverage_is_the_four_quarter_ratio(tmp_path):
    from forecaster.ratios import FactSet, compute_ratio
    b = RealEdgar(["TSTA"], make_http(build_sec(), tmp_path / "c")).facts("TSTA")
    fs = FactSet(b["cik"], b)
    last4 = b["quarters"][-4:]
    want = sum(q["ebitda"] for q in last4) / sum(q["interest"] for q in last4)
    assert abs(compute_ratio(fs, "coverage_ltm").value - want) < 1e-9


def test_company_without_annual_data_is_missing_not_a_crash(tmp_path):
    from forecaster.ratios import FactSet, MissingDataError, compute_ratio
    b = RealEdgar(["TSTA"], make_http(build_sec(), tmp_path / "c")).facts("TSTA")
    b = dict(b, annual=[])                                       # e.g. a new listing with no 10-K in XBRL yet
    fs = FactSet(b["cik"], b)
    assert fs.fys == []
    with pytest.raises(MissingDataError):
        compute_ratio(fs, "liquidity_cover", horizon=2, as_of_year=2026)


def test_an_unexpected_error_in_one_company_does_not_stop_the_run(tmp_path, monkeypatch):
    sec, fz, sz = _bulk(tmp_path)
    src = RealEdgar(["TSTA", "TSTD"], make_http(sec, tmp_path / "c"), fz, sz)
    fc = Forecaster(ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / "s")), source=src, llm=type("NoLLM", (), {"enabled": False})())
    real = fc.registry.mcp_call

    def boom(ticker, tool, **kw):
        if ticker == "TSTA" and tool == "edgar_facts":
            raise KeyError("odd filing")
        return real(ticker, tool, **kw)
    monkeypatch.setattr(fc.registry, "mcp_call", boom)
    res = {r.scorecard.ticker: r.scorecard for r in fc.screen_all()}
    assert res["TSTA"].skip_reason == "error" and "KeyError" in res["TSTA"].rationale[0]
    assert res["TSTD"].status != "insufficient_data" or res["TSTD"].skip_reason != "error"


def test_live_run_prints_progress_and_honors_limit(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert main(_screen_args(tmp_path, fz, sz, "regcap", "--limit", "2")) == 0
    err = capsys.readouterr().err
    assert "screened 2 / 2" in err and "requests to sec.gov" in err
    assert len(json.loads((tmp_path / "regcap.json").read_text())["scorecards"]) == 2


def test_xbrl_gaps_command_reports_counts_and_candidate_tags(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert main(["--state-dir", str(tmp_path / "s"), "xbrl-gaps", "--as-of", "2026-11-01", "--facts-zip", fz,
                 "--submissions-zip", sz, "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "companies (offset 0)" in out and "ok" in out


def test_compute_ratio_accepts_recorded_params_of_proxy_liquidity():
    """Regression: the validator re-computes a claim from the params stored on the Ratio, including the proxy flag."""
    from forecaster.ratios import compute_ratio, RATIO_FUNCS
    import inspect
    assert "proxy" not in inspect.signature(RATIO_FUNCS["liquidity_cover"]).parameters
    src = inspect.getsource(compute_ratio)
    assert 'pop("proxy"' in src


def test_ebit_falls_back_to_revenue_minus_costs_then_to_pretax():
    facts = fx.make_facts(9000001)
    us = facts["facts"]["us-gaap"]
    ebit = us.pop("OperatingIncomeLoss")
    del us["DepreciationDepletionAndAmortization"]
    us["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"] = {"units": {"USD": ebit["units"]["USD"]}} \
        if isinstance(ebit, dict) else ebit
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["ebitda"] is not None
    assert any("EBIT periods:" in n for n in b["data_notes"])


def test_extra_debt_tags_and_lone_zero_current_portion():
    facts = fx.make_facts(9000001)
    us = facts["facts"]["us-gaap"]
    us["DebtAndCapitalLeaseObligations"] = us.pop("LongTermDebtNoncurrent")
    del us["LongTermDebtCurrent"]
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    assert b["quarters"][-1]["debt"] == pytest.approx(1400.0 - 50.0)


def test_two_stage_reads_filings_only_for_the_pool(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert main(_screen_args(tmp_path, fz, sz, "clo", "--top-n", "1", "--pool-factor", "1")) == 0
    sc = json.loads((tmp_path / "clo.json").read_text())["scorecards"]
    cand = {t: c for t, c in sc.items() if c["status"] == "candidate"}
    read = [t for t, c in sc.items() if c["status"] in ("include", "exclude", "uncertain", "insufficient_data")
            and c["skip_reason"] != "missing_facts"]
    assert len(read) == 1 and cand and all(c["skip_reason"] == "outside_pool" for c in cand.values())
    assert all(any(f.startswith("rating:") for f in c["flags"]) for c in cand.values())
    assert "Stage 1:" in capsys.readouterr().err
    assert main(_screen_args(tmp_path, fz, sz, "clo", "--pool-factor", "0")) == 0          # 0 = old one-stage behaviour
    assert not [1 for c in json.loads((tmp_path / "clo.json").read_text())["scorecards"].values() if c["status"] == "candidate"]


def test_missing_capex_tag_uses_operating_cash_flow_and_never_vetoes():
    from forecaster.ratios import compute_ratio
    facts = fx.make_facts(9000001)
    del facts["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]            # e.g. Airbnb reports no capex tag
    b = xbrl.build_bundle(facts, fx.make_subs(9000001, "TSTA"), "TSTA")
    fs = FactSet(b["cik"], b)
    liq = compute_ratio(fs, "liquidity_cover", horizon=2, as_of_year=2026)
    assert liq.params.get("capex_unknown") is True
    full = compute_ratio(FactSet(b["cik"], xbrl.build_bundle(fx.make_facts(9000001), fx.make_subs(9000001, "TSTA"), "TSTA")),
                         "liquidity_cover", horizon=2, as_of_year=2026)
    assert liq.value >= full.value and "capex_unknown" not in full.params
    assert compute_ratio(fs, "liquidity_cover", **liq.params).value == liq.value           # the validator can re-compute it


def test_trend_signals_need_to_be_material():
    from forecaster.memory import TrendLibrary
    tm = TrendLibrary(None)
    tm.data = {"x": {"coverage": {"2025Q4": 19.9, "2026Q1": 19.2, "2026Q2": 18.8}, "leverage": {"FY2023": 0.0, "FY2024": 0.0, "FY2025": 0.1},
                     "fcf": {"FY2023": 136.0, "FY2024": 129.0, "FY2025": 107.0}},
               "y": {"coverage": {"2025Q4": 6.0, "2026Q1": 5.0, "2026Q2": 4.0}, "leverage": {"FY2023": 2.0, "FY2024": 2.6, "FY2025": 3.2},
                     "fcf": {"FY2023": 100.0, "FY2024": 80.0, "FY2025": 60.0}}}
    quiet = tm.signal("x")
    assert not quiet.coverage_decline_2q and not quiet.leverage_rise_3y and quiet.fcf_decline_3y   # FCF -21% is still material
    loud = tm.signal("y")
    assert loud.coverage_decline_2q and loud.leverage_rise_3y and loud.fcf_decline_3y


def test_no_debt_floor_applies_to_clo_only_not_to_regcap(tmp_path, monkeypatch):
    facts = fx.make_facts(9100010, debt=(10,) * 7)                                           # 0.01x: almost no debt
    sec = build_sec()
    sec.add_company("TSTZ", 9100010, facts, fx.make_subs(9100010, "TSTZ"), fx.tenq_html(), fx.tenk_html())

    def run(floor):
        cfg = ForecasterConfig(as_of="2026-11-01", state_dir=str(tmp_path / f"s{floor}"), target_ratings=("A", "BBB_plus", "BBB"),
                               min_obligor_leverage=floor)
        fc = Forecaster(cfg, source=RealEdgar(["TSTZ"], make_http(sec, tmp_path / f"c{floor}")), llm=type("NoLLM", (), {"enabled": False})())
        return fc.screen_company("TSTZ").scorecard
    assert run(0.5).skip_reason == "no_debt"
    assert run(0.0).skip_reason != "no_debt"


def test_boilerplate_hits_do_not_make_a_live_company_ambiguous():
    from types import SimpleNamespace
    from forecaster.tot import is_ambiguous
    ev = SimpleNamespace(findings={"litigation": [("c", "lawsuits may ...")], "adverse_development": [("c", "default may accelerate")]})
    assert is_ambiguous(ev, False, False, boilerplate_counts=True)
    assert not is_ambiguous(ev, False, False, boilerplate_counts=False)
    assert is_ambiguous(SimpleNamespace(findings={"going_concern": [("c", "substantial doubt")]}), False, False, boilerplate_counts=False)
    assert is_ambiguous(SimpleNamespace(findings={}), True, False, boilerplate_counts=False), "a multi-period trend still counts"


def test_provider_settings_openai_openrouter_and_key_redaction(monkeypatch):
    from forecaster.llm import LLMClient, OPENROUTER_URL, provider_settings, redact
    for v in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    assert provider_settings()["name"] == "openai" and provider_settings()["model"] == "gpt-4.1-mini"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-secretsecret")
    s = provider_settings()                                                    # only an OpenRouter key: OpenRouter is chosen
    assert s["name"] == "openrouter" and s["base"] == OPENROUTER_URL and s["extra"]["extra_body"]["provider"]["require_parameters"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    assert provider_settings()["name"] == "openai", "both keys and no LLM_PROVIDER: the existing OpenAI setup wins"
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
    c = LLMClient()
    assert c.enabled and c.model_name == "anthropic/claude-sonnet-4.5" and "openrouter" in c.status()
    assert "openrouter.ai" in str(c._model.openai_api_base)
    out = redact("401 for key sk-or-v1-secretsecret and sk-openai-secret")
    assert "secretsecret" not in out and "sk-openai-secret" not in out
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert "OPENROUTER_API_KEY is not set" in LLMClient().unavailable_reason


def test_review_queue_holds_only_the_list_and_open_questions_that_could_reach_it():
    from forecaster.models import Scorecard
    from forecaster.portfolio import build_portfolio
    from forecaster.workflow import CompanyResult

    def card(t, status, comp, reasons=(), sector="S"):
        return CompanyResult(Scorecard(ticker=t, status=status, composite=comp, sector=sector, review_reasons=list(reasons)), [], [], [])
    cfg = ForecasterConfig(top_n=1, review_min_composite=0.6)
    res = [card("A", "include", 0.95, ["trend"]), card("B", "include", 0.90, ["trend"]),       # B is good but not on the list
           card("C", "uncertain", 0.80, ["evidence incomplete"]), card("D", "uncertain", 0.40, ["evidence incomplete"]),
           card("E", "uncertain", 0.40, ["evidence incomplete", "litigation"])]
    p = build_portfolio(res, cfg)
    assert [c.ticker for c in p.shortlist] == ["A"]
    assert [c.ticker for c in p.review_queue] == ["A", "C", "E"]


def test_industry_cap_and_industry_wide_weakness_apply_to_the_industry_not_the_whole_sector():
    from forecaster.models import Scorecard
    from forecaster.portfolio import build_portfolio
    from forecaster.report import concentration
    from forecaster.workflow import CompanyResult

    def card(t, ind, comp, sector="Industrials", **kw):
        return CompanyResult(Scorecard(ticker=t, status="include", composite=comp, sector=sector, industry=ind, **kw), [], [], [])
    res = [card(f"E{i}", "SIC 131", 0.99 - i * 0.01, "Energy") for i in range(6)]                     # six oil & gas names
    res += [card(f"I{i}", f"SIC 35{i}", 0.80 - i * 0.01) for i in range(6)]                           # six different industrial industries
    res += [card("W", "SIC 367", 0.70, interpretation="deterioration", scope="industry"), card("W2", "SIC 367", 0.69)]
    cfg = ForecasterConfig(top_n=10, industry_cap_frac=0.15, sector_cap_frac=1.0)
    p = build_portfolio(res, cfg)
    tick = [c.ticker for c in p.shortlist]
    assert sum(t.startswith("E") for t in tick) == 1, "15% of 10 names = 1 per industry"
    assert all(f"I{i}" in tick for i in range(6)), "one weak industry does not cap the rest of the sector"
    assert "W" in tick and "W2" not in tick, "the industry flagged industry-wide keeps its best name only"
    table = "\n".join(concentration(p, cfg))
    assert "| industry | SIC 131 | 1 | 12% | 1 | yes |" in table
    assert "NO" not in table


def test_openrouter_provider_pin(monkeypatch):
    from forecaster.llm import provider_settings
    for v in ("LLM_PROVIDER", "OPENROUTER_PROVIDER", "OPENAI_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert provider_settings()["extra"]["extra_body"]["provider"] == {"require_parameters": True}
    monkeypatch.setenv("OPENROUTER_PROVIDER", "alibaba")
    assert provider_settings()["extra"]["extra_body"]["provider"] == {
        "require_parameters": True, "only": ["alibaba"], "allow_fallbacks": False}
    monkeypatch.delenv("OPENROUTER_PROVIDER")
    monkeypatch.setenv("LLM_PROVIDER", "alibaba")
    s = provider_settings()
    assert s["name"] == "openrouter" and s["extra"]["extra_body"]["provider"]["only"] == ["alibaba"]


def test_industry_map_default_and_fallback():
    from forecaster.industry_map import IndustryMap
    m = IndustryMap()
    assert m.industry("AOS", 3630) == "Consumer Electronics/Appliances"      # bundled Nasdaq.com file
    assert m.sector("APA")
    assert m.industry("NOT-A-TICKER", 3674) == "SIC 367"                     # not in the file: SEC SIC group
    assert m.industry("NOT-A-TICKER", None) == ""


def test_impaired_industry_needs_filing_evidence():
    from forecaster.models import Scorecard, Status
    from forecaster.portfolio import build_portfolio
    from forecaster.config import ForecasterConfig
    from forecaster.workflow import CompanyResult
    def card(t, evid):
        return CompanyResult(Scorecard(t, sector="Energy", industry="Oil, Gas & Consumable Fuels", status=Status.INCLUDE.value,
                                       composite=0.9, scope="industry", interpretation="deterioration", industry_evidenced=evid), [], [], [])
    cfg = ForecasterConfig(top_n=5)
    out = build_portfolio([card("A", False), card("B", False)], cfg)
    assert len(out.shortlist) == 2                                # an unevidenced industry claim does not block peers
    out = build_portfolio([card("A", True), card("B", True)], cfg)
    assert len(out.shortlist) == 1


def test_industry_file_screener_format(tmp_path):
    from forecaster.industry_map import IndustryMap, load_industry_file
    f = tmp_path / "n.csv"
    f.write_text("Symbol,Name,Sector,Industry\nBRK/B,x,Finance,Property-Casualty Insurers\nAOS,y,Industrials,Industrial Machinery/Components\nZZ,z,,\n")
    m = load_industry_file(str(f))
    assert m == {"BRK-B": ("Finance", "Property-Casualty Insurers"), "AOS": ("Industrials", "Industrial Machinery/Components")}
    im = IndustryMap(m)
    assert im.industry("BRK.B", 6331) == "Property-Casualty Insurers" and im.sector("AOS") == "Industrials"
    assert im.industry("ZZ", 3674) == "SIC 367"                              # blank in the file: SIC fallback


def test_regcap_size_and_debt_floor(tmp_path, monkeypatch, capsys):
    sec, fz, sz = _bulk(tmp_path)
    import forecaster.edgar as edgar_mod
    monkeypatch.setattr(edgar_mod, "_urllib_fetch", sec.fetcher())
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    assert main(_screen_args(tmp_path, fz, sz, "regcap", "--min-ebitda", "1000000")) == 0   # opt-in floor, set above every fixture
    reg = json.loads((tmp_path / "regcap.json").read_text())
    assert reg["shortlist"] == [] and reg["scorecards"]["TSTA"]["skip_reason"] == "too_small"


def test_download_data_uses_env_user_agent(tmp_path, monkeypatch, capsys):
    """download-data sends SEC_USER_AGENT, writes both files, and skips files that already exist."""
    import io
    import forecaster.download as dl
    seen = []

    class Resp(io.BytesIO):
        headers = {"Content-Length": "5"}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=0):
        seen.append((req.full_url, req.get_header("User-agent")))
        return Resp(b"12345")

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_open)
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@firm.com")
    assert main(["download-data", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "companyfacts.zip").read_bytes() == b"12345" and (tmp_path / "submissions.zip").exists()
    assert not list(tmp_path.glob("*.part"))
    assert {ua for _, ua in seen} == {"Test User test@firm.com"} and len(seen) == 2
    assert main(["download-data", "--dir", str(tmp_path)]) == 0
    assert len(seen) == 2                       # nothing downloaded again
    monkeypatch.setenv("SEC_USER_AGENT", "Your Name your.email@example.com")
    with pytest.raises(SystemExit):
        main(["download-data", "--dir", str(tmp_path), "--force"])


def test_screen_finds_bulk_files_in_data_dir(tmp_path, monkeypatch):
    """Without --facts-zip / --submissions-zip the files saved by download-data in ./data are used."""
    from types import SimpleNamespace
    from forecaster.cli import _default_zips
    monkeypatch.chdir(tmp_path)
    a = SimpleNamespace(facts_zip=None, submissions_zip=None)
    _default_zips(a)
    assert a.facts_zip is None                  # nothing downloaded yet
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "companyfacts.zip").write_bytes(b"x")
    (tmp_path / "data" / "submissions.zip").write_bytes(b"x")
    _default_zips(a)
    assert a.facts_zip.endswith("companyfacts.zip") and a.submissions_zip.endswith("submissions.zip")
    b = SimpleNamespace(facts_zip="other.zip", submissions_zip=None)
    _default_zips(b)
    assert b.facts_zip == "other.zip"           # an explicit path wins
