"""Tests use dependency injection like the teacher's sample (dummy sources, injected failures)."""
import json
from copy import deepcopy

import pytest

from forecaster.agents import Analyst, Evidence, ValidationContext, Validator
from forecaster.audit import AuditLog
from forecaster.cli import main as cli_main
from forecaster.config import ForecasterConfig
from forecaster.graph import END, StateGraph
from forecaster.memory import FeedbackMemory, TrendLibrary
from forecaster.models import Branch, Claim, Scorecard, Status
from forecaster.portfolio import build_portfolio
from forecaster.ratios import FactSet, MissingDataError, compute_ratio, core_ratios, series
from forecaster.report import company_memo, render
from forecaster.retrieval import HybridIndex
from forecaster.sample_data import build_universe
from forecaster.tools import BudgetExceeded, OfflineEdgar, ToolFailure, ToolRegistry
from forecaster.evidence import split_sentences
from forecaster.workflow import Forecaster


class NoLLM:
    enabled = False


def mk(cfg, **kw):
    kw.setdefault("llm", NoLLM())      # tests never touch the network, whatever OPENAI_API_KEY is set to
    return Forecaster(cfg, **kw)


@pytest.fixture()
def cfg(tmp_path):
    return ForecasterConfig(state_dir=str(tmp_path), workers=2)


@pytest.fixture()
def fc(cfg):
    return mk(cfg)


# ------------------------------------------------------------------ ratios
def test_ratios_are_recomputable_and_cited():
    u = build_universe()["ALPH"]
    fs = FactSet(u["cik"], u)
    r = compute_ratio(fs, "leverage_ltm")
    assert r.value == pytest.approx(1420 / (205 + 210 + 215 + 220))
    assert all(i.startswith("xbrl:") and i in fs.facts for i in r.inputs)
    assert compute_ratio(fs, "fcf_fy").value == 400


def test_missing_input_raises_instead_of_guessing():
    u = deepcopy(build_universe()["ALPH"])
    u["quarters"][-1]["ebitda"] = None
    fs = FactSet(u["cik"], u)
    with pytest.raises(MissingDataError):
        core_ratios(fs, 2, 2026)


# ------------------------------------------------------------------ retrieval
def test_stale_and_superseded_chunks_are_flagged():
    src = OfflineEdgar()
    idx = HybridIndex(src.all_chunks())
    stale = [c for c in idx.chunks.values() if idx.is_stale(c)]
    assert {c.ticker for c in stale} == {"ALPH", "BRCK"}


def test_retriever_ignores_stale_and_finds_latest(fc):
    plan = fc.planner.plan({}, fc.cfg)
    ev = fc.retriever.gather("BRCK", fc.source.universe["BRCK"]["cik"], plan)
    assert not ev.missing
    chunk, sentence = ev.topics["coverage"]
    assert "0.9x" not in sentence and chunk.superseded_by is None


def test_entity_mismatch_going_concern_is_ignored(fc):
    res = fc.screen_company("LUMN")
    assert res.scorecard.status == Status.INCLUDE.value
    assert fc.audit.count("entity_mismatch_ignored", ticker="LUMN") >= 1


# ------------------------------------------------------------------ validator
def _ctx(fc, t):
    b = fc.source.universe[t]
    return ValidationContext(t, b["cik"], FactSet(b["cik"], b), fc.index, False, fc.as_of)


def test_validator_hard_fails(fc):
    ctx = _ctx(fc, "ALPH")
    good = Claim("fact", "x", topic="leverage", citations=["alph-10-q-2026-10-28-debt_footnote"], quote="Total debt outstanding")
    wrong = Claim("fact", "Total debt outstanding", topic="coverage", citations=["brck-10-q-2026-10-28-debt_footnote"],
                  quote="Total debt outstanding")
    fake = Claim("fact", "made up", topic="maturities", citations=["alph-10-q-2026-10-28-debt_footnote"], quote="made up")
    nocite = Claim("fact", "n", topic="liquidity")
    digit = Claim("judgment", "Leverage is 3x.", topic="interpretation")
    b = Branch("t", None, 1, "stable", "primary", [good, wrong, fake, nocite, digit])
    Validator(fc.cfg).validate(b, ctx)
    reasons = " ".join(b.hard_fails)
    for r in ("wrong_cik", "quote_not_verbatim", "cite_missing", "number_in_judgment"):
        assert r in reasons
    assert not b.valid


def test_calc_claim_is_recomputed(fc):
    ctx = _ctx(fc, "ALPH")
    r = compute_ratio(ctx.fs, "coverage_q")
    c = Claim("calc", "bad", topic="coverage_q", citations=r.inputs, ratio="coverage_q", params=dict(r.params), value=r.value + 1)
    b = Validator(fc.cfg).validate(Branch("t", None, 1, "stable", "p", [c]), ctx)
    assert any("calc_mismatch" in h for h in b.hard_fails)


# ------------------------------------------------------------------ company outcomes
@pytest.mark.parametrize("ticker,status", [
    ("ALPH", "include"), ("CYAN", "include"), ("FLUX", "prescreen_fail"), ("GLEN", "exclude"),
    ("HRBR", "uncertain"), ("IRON", "exclude"), ("KITE", "insufficient_data"), ("MRDN", "include"),
])
def test_company_status(fc, ticker, status):
    assert fc.screen_company(ticker).scorecard.status == status


def test_setback_needs_cited_cause_and_recovery(fc):
    sc = fc.screen_company("EMBR").scorecard
    assert sc.interpretation == "setback" and sc.used_tot
    assert any("recovery" in r for r in sc.review_reasons)


def test_deterioration_detected_from_trend_library(fc):
    res = fc.screen_company("DRFT")
    sc = res.scorecard
    assert sc.interpretation == "deterioration" and sc.status == "include"
    assert any("coverage fell" in r for r in sc.rationale)
    assert sc.review_reasons          # never auto-released
    pruned = [b for b in res.branches if b.pruned_reason]
    assert pruned and fc.audit.count("tot_prune", ticker="DRFT") == len(pruned)


def test_event_freeze_and_going_concern_veto(fc):
    hr = fc.screen_company("HRBR").scorecard
    assert "event_freeze" in hr.flags and not hr.auto_release
    gl = fc.screen_company("GLEN").scorecard
    assert gl.status == "exclude" and "going-concern" in gl.rationale[0]


def test_units_error_is_repaired_by_text_fallback(fc):
    sc = fc.screen_company("MRDN").scorecard
    assert sc.metrics["leverage_ltm"] == pytest.approx(1350 / (142 + 145 + 147 + 150), abs=0.01)
    assert "fallback_text_extraction" in sc.flags and sc.confidence != "high"


def test_stale_filing_is_insufficient_not_guessed(fc):
    sc = fc.screen_company("KITE").scorecard
    assert sc.status == "insufficient_data" and sc.skip_reason == "stale"


# ------------------------------------------------------------------ tools: budget, retry, failure
def test_retry_once_then_insufficient(cfg):
    f = mk(cfg, inject_failures={("ALPH", "edgar_facts"): 1})
    assert f.screen_company("ALPH").scorecard.status == "include"       # one transient failure is retried
    f2 = mk(cfg, inject_failures={("ALPH", "edgar_facts"): 2})
    sc = f2.screen_company("ALPH").scorecard
    assert sc.status == "insufficient_data" and sc.skip_reason == "tool_failure"


def test_call_budget_is_enforced(tmp_path):
    f = mk(ForecasterConfig(state_dir=str(tmp_path), max_tool_calls_per_company=3))
    sc = f.screen_company("ALPH").scorecard
    assert sc.skip_reason == "budget" and sc.status == "insufficient_data"


def test_unknown_tool_rejected(fc):
    with pytest.raises(ValueError):
        fc.registry.mcp_call("ALPH", "delete_everything")


# ------------------------------------------------------------------ memory
def test_trend_library_needs_quarters_to_line_up(tmp_path):
    lib = TrendLibrary(tmp_path / "t.json")
    u = build_universe()["DRFT"]
    lib.ingest(u["cik"], series(FactSet(u["cik"], u)))
    sig = lib.signal(u["cik"])
    assert sig.coverage_decline_2q and sig.leverage_rise_3y and sig.fcf_decline_3y
    assert TrendLibrary(tmp_path / "t.json").signal(u["cik"]).multi_period_decline      # persisted


def test_feedback_criteria_are_editable_and_checked_against_filings(cfg, tmp_path):
    fm = FeedbackMemory(tmp_path / "fb.json")
    c = fm.add("no exposure to commercial customers in Europe", ["Europe"], "exclude")
    f = mk(cfg, feedback=fm)
    assert f.screen_company("CYAN").scorecard.status == "exclude"
    fm.edit(c.id, action="review")
    sc = mk(cfg, feedback=fm).screen_company("CYAN").scorecard
    assert sc.status == "include" and any(c.id in r for r in sc.review_reasons)
    fm.add("irrelevant", ["zzz-not-in-any-filing"], "exclude")
    assert fm.delete(c.id)
    assert mk(cfg, feedback=fm).screen_company("CYAN").scorecard.review_reasons == []


# ------------------------------------------------------------------ portfolio
def test_portfolio_caps_and_funnel(fc, cfg):
    p = build_portfolio(fc.screen_all(), cfg)
    assert p.breaker is None and len(p.shortlist) == 6
    reasons = dict(p.skipped)
    assert any("sponsor cap" in r for r in reasons.values())
    assert all("industry-wide" in reasons[t] for t in ("DRFT", "MRDN"))      # Industrials flagged: max 1 name
    sectors = [c.sector for c in p.shortlist]
    assert max(sectors.count(s) for s in set(sectors)) <= 3
    assert p.counts["prescreen_fail"] == 1 and p.counts["insufficient_data"] == 1


def test_circuit_breaker_trips_when_source_is_down(tmp_path):
    cfg = ForecasterConfig(state_dir=str(tmp_path))
    fails = {(t, "edgar_facts"): 5 for t in build_universe()}
    p = build_portfolio(mk(cfg, inject_failures=fails).screen_all(), cfg)
    assert p.breaker and not p.shortlist


def test_shortlist_is_deterministic(cfg, tmp_path):
    a = build_portfolio(mk(cfg).screen_all(), cfg).tickers
    b = build_portfolio(mk(ForecasterConfig(state_dir=str(tmp_path / "x"), workers=1)).screen_all(), cfg).tickers
    assert a == b


# ------------------------------------------------------------------ report + graph + cli
def test_report_never_renders_uncited_numbers(fc, cfg):
    p = build_portfolio(fc.screen_all(), cfg)
    text = render(p, fc.index, cfg.as_of)
    assert "Shortlist" in text and "Human review queue" in text
    memo = company_memo(Scorecard("T", name="n"), [Claim("fact", "Debt was $5 million", topic="leverage")], fc.index)
    assert "$5 million" not in memo


def test_graph_loops_are_bounded():
    g = StateGraph()
    g.add_node("a", lambda s: {})
    g.set_entry_point("a")
    g.add_conditional_edges("a", lambda s: "a")
    with pytest.raises(RuntimeError):
        g.invoke({}, max_steps=5)


def test_cli_screen_and_feedback(tmp_path, capsys):
    out = tmp_path / "r.md"
    assert cli_main(["--state-dir", str(tmp_path / "s"), "screen", "--no-llm", "--output", str(out)]) == 0
    assert out.exists() and json.loads(out.with_suffix(".json").read_text())["shortlist"]
    assert cli_main(["--state-dir", str(tmp_path / "s"), "feedback", "add", "no asbestos", "--keywords", "asbestos"]) == 0
    assert cli_main(["--state-dir", str(tmp_path / "s"), "feedback", "list"]) == 0
    assert "no asbestos" in capsys.readouterr().out


# ====================================================================== LLM layer (scripted fake model)
class FakeLLM:
    """Stands in for the OpenAI client.

    mode "scripted": a 2-round ReAct run (MD&A first, then the debt footnote, plus an unknown tool and a foreign CIK)
    mode "lazy":     says DONE immediately, then behaves like "scripted" once pushed back by the Observation
    mode "stuck":    keeps searching the wrong section forever
    `reader` answers the filing-reading JSON calls.
    """
    enabled = True

    def __init__(self, reader=None, mode="scripted"):
        self.reader, self.mode, self.messages_seen = reader, mode, []

    @staticmethod
    def _reply(content, calls):
        am = {"role": "assistant", "content": content}
        if calls:
            am["tool_calls"] = [{"id": c["id"], "type": "function",
                                 "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]
        return {"content": content, "tool_calls": calls, "assistant_message": am}

    def chat(self, messages, tools=None, json_mode=False):
        if json_mode:
            payload = json.loads(messages[1]["content"])
            out = self.reader(payload) if self.reader else {"supports_hypothesis": None}
            return self._reply(json.dumps(out), [])
        self.messages_seen.append([dict(m) for m in messages])
        n = sum(1 for m in messages if m["role"] == "assistant")
        if self.mode == "stuck":
            return self._reply("Thought: try the business section again.", [
                {"id": f"s{n}", "name": "filing_search", "arguments": {"query": "company overview", "sections": ["item1"]}}])
        if self.mode == "lazy":
            if n == 0:
                return self._reply("DONE", [])
            n -= 1
        if n == 0:
            return self._reply("Thought: start with coverage and liquidity in MD&A.", [
                {"id": "c1", "name": "filing_search",
                 "arguments": {"query": "interest coverage", "cik": "9999999999", "sections": ["item7"], "keywords": ["interest coverage"]}}])
        if n == 1:
            return self._reply("Thought: leverage, maturities and the 10-Q notes are missing, so read the debt note and the notes.", [
                {"id": "c2", "name": "filing_section", "arguments": {"sections": [
                    "debt_footnote", "note_credit", "note_leases", "note_commitments", "note_derivatives",
                    "note_subsequent", "note_basis"]}},
                {"id": "c3", "name": "drop_database", "arguments": {}}])
        return self._reply("DONE", [])


def sentence(payload, needle):
    for p in payload["passages"]:
        for s_ in split_sentences(p["text"]):
            if needle.lower() in s_.lower():
                return p["chunk_id"], s_
    raise AssertionError(needle)


def reader_industry(payload):
    h = payload["hypothesis"]
    if h != "deterioration":
        return {"supports_hypothesis": False, "scope": "unknown", "evidence": [], "rationale": "Judgment: not supported."}
    try:
        cid, q = sentence(payload, "industry-wide")
    except AssertionError:                    # this company's filings say nothing about the sector
        return {"supports_hypothesis": False, "scope": "company", "evidence": [], "rationale": "Judgment: no."}
    return {"supports_hypothesis": True, "scope": "industry",
            "evidence": [{"chunk_id": cid, "quote": q, "label": "industry_conditions"}],
            "rationale": "Judgment: the weakness tracks sector-wide freight demand rather than a company-specific failure."}


def _steps(f, t):
    return [e for e in f.audit.for_ticker(t) if e["event"] == "react_step"]


def test_react_loop_thought_action_observation(cfg):
    llm = FakeLLM()
    f = mk(cfg, llm=llm)
    res = f.screen_company("ALPH")
    assert res.scorecard.status == "include"
    steps = _steps(f, "ALPH")
    assert len(steps) == 2 and f.audit.count("llm_call", ticker="ALPH", purpose="retrieve") == 2
    assert steps[0]["thought"].startswith("Thought: start with coverage")
    assert set(steps[0]["missing"]) == {"leverage", "maturities", "credit", "leases", "commitments", "derivatives",
                                        "subsequent", "basis"}              # round 1 only found coverage + liquidity
    assert steps[1]["missing"] == [] and f.audit.count("react_stop", ticker="ALPH", reason="all_found") == 1
    # the model really saw an Observation naming what was still missing before choosing its second action
    second_call = llm.messages_seen[1]
    assert second_call[-1]["role"] == "user" and second_call[-1]["content"].startswith("Observation:")
    assert "leverage" in second_call[-1]["content"] and "covenant" not in second_call[-1]["content"].lower()
    assert "credit" in second_call[-1]["content"] and "subsequent" in second_call[-1]["content"]
    assert f.audit.count("react_safety_net", ticker="ALPH") == 0            # the loop finished the job itself
    names = [e["tool"] for e in f.audit.for_ticker("ALPH") if e["event"] == "tool_call"]
    assert "filing_search" in names and "filing_section" in names and "drop_database" not in names
    # a foreign CIK requested by the model is ignored: nothing cited belongs to another company
    cik = f.source.universe["ALPH"]["cik"]
    assert all(f.index.get(c.citations[0]).cik == cik for c in res.claims if c.kind == "fact")


def test_react_premature_done_is_pushed_back(cfg):
    llm = FakeLLM(mode="lazy")
    f = mk(cfg, llm=llm)
    assert f.screen_company("ALPH").scorecard.status == "include"
    steps = _steps(f, "ALPH")
    assert steps[0]["actions"] == [] and len(steps[0]["missing"]) == 10       # DONE with nothing found
    assert "You replied DONE but evidence is still missing" in llm.messages_seen[1][-1]["content"]
    assert steps[-1]["missing"] == []


def test_react_hard_stop_then_safety_net(cfg):
    f = mk(cfg, llm=FakeLLM(mode="stuck"))
    sc = f.screen_company("ALPH").scorecard
    assert len(_steps(f, "ALPH")) == cfg.llm_max_steps
    assert f.audit.count("react_stop", ticker="ALPH", reason="max_steps") == 1
    assert f.audit.count("react_safety_net", ticker="ALPH") == 10 and sc.status == "include"
    # and with the safety net off, the company ends as insufficient data instead of being guessed
    cfg2 = ForecasterConfig(state_dir=cfg.state_dir + "/b", react_safety_net=False)
    sc2 = mk(cfg2, llm=FakeLLM(mode="stuck")).screen_company("ALPH").scorecard
    assert sc2.status == "insufficient_data" and len(sc2.missing) == 10 and "basis" in sc2.missing


def test_llm_reads_filing_and_attributes_weakness_to_the_industry(cfg):
    f = mk(cfg, llm=FakeLLM(reader_industry))
    res = f.screen_company("DRFT")
    sc = res.scorecard
    assert sc.interpretation == "deterioration" and sc.scope == "industry"
    assert any("sector-wide freight demand" in c.text for c in res.claims if c.kind == "judgment")
    assert any(c.topic == "industry_conditions" and c.quote.startswith("Industry-wide") for c in res.claims)
    assert any("industry-wide" in r for r in sc.review_reasons)


def test_model_cannot_invent_quotes_or_numbers(cfg):
    def liar(payload):
        cid = payload["passages"][0]["chunk_id"]
        return {"supports_hypothesis": True, "scope": "company",
                "evidence": [{"chunk_id": cid, "quote": "Management guaranteed a full recovery.", "label": "recovery"}],
                "rationale": "Judgment: leverage will fall to 2.0x next year."}
    f = mk(cfg, llm=FakeLLM(liar))
    res = f.screen_company("EMBR")
    assert f.audit.count("llm_quote_rejected", ticker="EMBR") >= 1
    judg = [c for c in res.claims if c.kind == "judgment"][0]
    assert not any(ch.isdigit() for ch in judg.text)           # digit-bearing rationale replaced by the template
    assert all("guaranteed" not in c.text for c in res.claims)


def test_llm_that_rejects_a_hypothesis_vetoes_the_branch(cfg):
    def no_setback(payload):
        return {"supports_hypothesis": payload["hypothesis"] != "setback", "scope": "company", "evidence": [],
                "rationale": "Judgment: a plain reading."}
    res = mk(cfg, llm=FakeLLM(no_setback)).screen_company("EMBR")
    assert res.scorecard.interpretation != "setback"
    assert any("llm_rejects_hypothesis" in h for b in res.branches if b.interpretation == "setback" for h in b.hard_fails)


def test_llm_call_budget_degrades_to_rules(tmp_path):
    cfg1 = ForecasterConfig(state_dir=str(tmp_path), llm_max_calls_per_company=1)
    f = mk(cfg1, llm=FakeLLM(reader_industry))
    sc = f.screen_company("DRFT").scorecard
    assert f.audit.count("llm_budget_exceeded", ticker="DRFT") >= 1
    assert sc.status == "include" and sc.interpretation == "deterioration"     # rules took over


def test_industry_weakness_caps_the_sector(cfg):
    f = mk(cfg, llm=FakeLLM(reader_industry))
    p = build_portfolio(f.screen_all(), cfg)
    assert "ALPH" in p.tickers and "DRFT" not in p.tickers and "MRDN" not in p.tickers
    assert any(c.ticker == "ALPH" and any("industry-wide" in r for r in c.review_reasons) for c in p.review_queue)


# ====================================================================== human review -> experience memory -> next run
def _cli(tmp_path, *args):
    return cli_main(["--state-dir", str(tmp_path / "s"), *args])


def test_review_saves_experience_and_the_next_run_uses_it(tmp_path, capsys):
    assert _cli(tmp_path, "screen", "--no-llm", "--output", str(tmp_path / "r1.md")) == 0
    capsys.readouterr()
    assert _cli(tmp_path, "review", "--list") == 0
    assert "DRFT" in capsys.readouterr().out                       # deterioration names are queued for the human
    assert _cli(tmp_path, "review", "--ticker", "DRFT", "--decision", "exclude", "--analyst", "yuhan",
                "--note", "spot-market freight is too cyclical for 2 years",
                "--lesson", "Carriers selling into spot freight markets are too cyclical for a two-year pool",
                "--keywords", "freight", "--action", "exclude") == 0
    over = [json.loads(x) for x in (tmp_path / "s" / "overrides.jsonl").read_text().splitlines()]
    assert over[0]["event"] == "override" and over[0]["model_status"] == "include" and over[0]["analyst"] == "yuhan"
    fb = json.loads((tmp_path / "s" / "feedback.json").read_text())
    assert fb[0]["source"].startswith("DRFT exclude") and fb[0]["sector"] == "Industrials" and "spot freight" in fb[0]["lesson"]

    # next run: the saved experience changes the outcome, and the reviewer sees the history
    cfg = ForecasterConfig(state_dir=str(tmp_path / "s"))
    f = mk(cfg)
    sc = f.screen_company("DRFT").scorecard
    assert sc.status == "exclude" and sc.lessons == [fb[0]["id"]]
    assert "Desk lesson" in sc.rationale[0]
    assert any("previously overrode this name to 'exclude'" in r for r in sc.review_reasons)
    assert f.screen_company("CYAN").scorecard.lessons == []         # other sectors / filings without the signal: untouched
    assert (tmp_path / "s" / "overrides.jsonl").exists()            # the run log is rewritten, the human log is not
    assert _cli(tmp_path, "screen", "--no-llm", "--output", str(tmp_path / "r2.md")) == 0
    assert (tmp_path / "s" / "overrides.jsonl").read_text().count("override") >= 1


def test_lessons_are_given_to_the_llm(tmp_path):
    cfg = ForecasterConfig(state_dir=str(tmp_path))
    fm = FeedbackMemory(tmp_path / "fb.json")
    fm.add("customer concentration", [], "review", lesson="Check for customers above 20 percent of revenue",
           sector="Industrials")
    seen = []

    def reader(payload):
        seen.append(payload.get("analyst_experience"))
        return {"supports_hypothesis": True, "scope": "company", "evidence": [], "rationale": "Judgment: ok."}
    llm = FakeLLM(reader)
    f = mk(cfg, llm=llm, feedback=fm)
    f.screen_company("ALPH")
    assert "customers above 20 percent" in llm.messages_seen[0][1]["content"]     # the ReAct retriever is told
    f.screen_company("DRFT")
    assert any(e and "customers above 20 percent" in e[0]["lesson"] for e in seen)  # the reading Analyst is told
    llm2 = FakeLLM()
    mk(cfg, llm=llm2, feedback=fm).screen_company("CYAN")                          # Technology: lesson does not apply
    assert "customers above 20 percent" not in llm2.messages_seen[0][1]["content"]


def _lesson_reader(fake_quote=False):
    def reader(payload):
        if "lessons" not in payload:
            return {"supports_hypothesis": True, "scope": "company", "evidence": [], "rationale": "Judgment: ok."}
        out = []
        for l in payload["lessons"]:
            try:
                cid, q = sentence(payload, "freight demand")
                out.append({"id": l["id"], "applies": True, "chunk_id": cid,
                            "quote": "Freight demand collapsed overnight." if fake_quote else q})
            except AssertionError:
                out.append({"id": l["id"], "applies": False, "chunk_id": "", "quote": ""})
        return {"lessons": out}
    return reader


def test_llm_applies_a_lesson_only_with_a_verbatim_quote(tmp_path):
    cfg = ForecasterConfig(state_dir=str(tmp_path))
    fm = FeedbackMemory(tmp_path / "fb.json")
    c = fm.add("weak freight end markets", [], "review", lesson="Be wary of exposure to weak freight end markets",
               sector="Industrials")
    sc = mk(cfg, llm=FakeLLM(_lesson_reader()), feedback=fm).screen_company("DRFT").scorecard
    assert sc.lessons == [c.id]
    assert any("LLM read" in r and "freight demand" in r.lower() for r in sc.review_reasons)
    f2 = mk(cfg, llm=FakeLLM(_lesson_reader(fake_quote=True)), feedback=fm)
    sc2 = f2.screen_company("DRFT").scorecard
    assert sc2.lessons == [] and f2.audit.count("llm_quote_rejected", ticker="DRFT") >= 1
    fm.edit(c.id, action="exclude")                                   # the analyst can tighten it later
    assert mk(cfg, llm=FakeLLM(_lesson_reader()), feedback=fm).screen_company("DRFT").scorecard.status == "exclude"


def test_interactive_review_and_lesson_editing(tmp_path, capsys):
    _cli(tmp_path, "screen", "--no-llm", "--output", str(tmp_path / "r.md"))
    answers = {"decision": iter(["c", "s", "s", "s", "s", "s", "s", "s"]), "note": iter(["agree, flagged for the right reason"])}

    def fake_input(prompt):
        key = prompt.split()[0]
        return next(answers[key]) if key in answers else ""
    from forecaster.review import ReviewSession
    assert ReviewSession(tmp_path / "s").interactive("yuhan", input_fn=fake_input, print_fn=lambda *_: None) == 0
    log = [json.loads(x) for x in (tmp_path / "s" / "overrides.jsonl").read_text().splitlines()]
    assert log[0]["event"] == "review_confirmed" and log[0]["analyst"] == "yuhan"
    assert _cli(tmp_path, "feedback", "add", "x", "--lesson", "watch pension deficits", "--sector", "Utilities") == 0
    assert _cli(tmp_path, "feedback", "list") == 0
    assert "watch pension deficits" in capsys.readouterr().out


# ====================================================================== the six 10-Q notes
from dataclasses import replace


def universe_with(ticker, section, fn=None, drop=False):
    """A copy of the demo universe where one note of one company is rewritten (or removed)."""
    u = build_universe()
    chunks = []
    for c in u[ticker]["chunks"]:
        if c.section == section and not c.chunk_id.endswith("-old"):
            if drop:
                continue
            c = replace(c, text=fn(c.text))
        chunks.append(c)
    u[ticker]["chunks"] = chunks
    return OfflineEdgar(u)


def test_notes_become_cited_cash_flow_facts(fc):
    res = fc.screen_company("ALPH")
    sc = res.scorecard
    assert sc.status == "include" and sc.metrics["liquidity_cover_adj"] > 0 and sc.metrics["covenant_headroom"] > 0
    adj = [c for c in res.claims if c.ratio == "liquidity_cover_adj"][0]
    assert any(i.startswith("note:") for i in adj.citations)                           # figures read from the notes
    assert "alph-10-q-2026-10-28-note_credit" in adj.citations                          # ... and the note they came from
    assert not any(i.endswith("-old") for i in adj.citations)                           # the older credit note lost


def test_leases_and_commitments_can_turn_a_comfortable_company_into_an_exclusion(cfg):
    base = mk(cfg).screen_company("ALPH").scorecard
    assert base.status == "include" and base.metrics["liquidity_cover"] > 1
    src = universe_with("ALPH", "note_leases",
                        lambda t: t.replace("total $", "total $5000.0 million; previously $").replace("previously $", "previously $", 1))
    sc = mk(cfg, source=src).screen_company("ALPH").scorecard
    assert sc.status == "exclude" and "leases, commitments" in sc.rationale[0]
    assert sc.metrics["liquidity_cover"] > 1 > sc.metrics["liquidity_cover_adj"]       # only the notes reveal it


def test_covenant_headroom_flags_and_breach_vetoes(cfg, fc):
    tight = fc.screen_company("DRFT").scorecard
    assert tight.status == "include" and "covenant_tight" in tight.flags
    assert any("covenant headroom only" in r for r in tight.review_reasons)
    import re
    src = universe_with("CYAN", "note_credit", lambda t: re.sub(r"actual net leverage was [\d.]+x", "actual net leverage was 9.9x", t))
    sc = mk(cfg, source=src).screen_company("CYAN").scorecard
    assert sc.status == "exclude" and "covenant breached" in sc.rationale[0]


def test_missing_note_is_unknown_not_zero(cfg):
    src = universe_with("CYAN", "note_leases", drop=True)
    sc = mk(cfg, source=src).screen_company("CYAN").scorecard
    assert sc.status == "uncertain" and "fact:leases" in sc.missing and sc.confidence == "low"
    assert "liquidity_cover_adj" not in sc.metrics and "notes_incomplete" in sc.flags


def test_subsequent_events_and_off_balance_sheet_reach_the_reviewer(fc):
    nova = fc.screen_company("NOVA").scorecard
    assert nova.status == "include" and any("subsequent event in the notes" in r and "repaid" in r for r in nova.review_reasons)
    jade = fc.screen_company("JADE").scorecard
    assert any("off-balance-sheet exposure" in r and "unconsolidated joint venture" in r for r in jade.review_reasons)
    assert not any("subsequent event" in r for r in fc.screen_company("CYAN").scorecard.review_reasons)   # "no subsequent events"


def test_restatement_in_the_basis_note_changes_the_reading(cfg):
    src = universe_with("ALPH", "note_basis", lambda t: t + " The Company restated its previously issued financial "
                                                           "statements for the quarter ended 2026Q1.")
    res = mk(cfg, source=src).screen_company("ALPH")
    assert res.scorecard.interpretation == "deterioration"                    # a restatement cannot be read as "stable"
    assert any(c.topic == "restatement" and c.citations[0].endswith("note_basis") for c in res.claims)


def test_going_concern_in_the_basis_note_vetoes(fc):
    sc = fc.screen_company("GLEN").scorecard
    assert sc.status == "exclude" and "going-concern" in sc.rationale[0]


def test_llm_note_extraction_needs_a_verbatim_quote_that_contains_the_number(cfg):
    odd = lambda t: t.replace("lease obligations due within the next two years total", "lease obligations over the next two years amount to")
    src = universe_with("ALPH", "note_leases", odd)
    assert mk(cfg, source=src).screen_company("ALPH").scorecard.status == "uncertain"          # parser cannot read it

    def reader(payload):
        if "wanted" not in payload:
            return {"supports_hypothesis": True, "scope": "company", "evidence": [], "rationale": "Judgment: ok."}
        p = [x for x in payload["passages"] if x["section"] == "note_leases"][0]
        quote = p["text"].split(". ")[0] + "."
        return {"figures": [{"tag": "LeaseObligations2Y", "value": 71.0, "chunk_id": p["chunk_id"],
                             "quote": quote}]}
    sc = mk(cfg, source=src, llm=FakeLLM(reader)).screen_company("ALPH").scorecard
    assert sc.status == "include" and "liquidity_cover_adj" in sc.metrics

    def liar(payload):
        out = reader(payload)
        if "figures" in out:
            out["figures"][0]["quote"] = "Lease obligations are negligible."
        return out
    f = mk(cfg, source=src, llm=FakeLLM(liar))
    assert f.screen_company("ALPH").scorecard.status == "uncertain"
    assert f.audit.count("llm_quote_rejected", ticker="ALPH") >= 1
