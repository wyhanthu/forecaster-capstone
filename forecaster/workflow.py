"""Company-level workflow (the inner loop) and the batch runner (the outer loop).

Per company, a deterministic orchestrator walks a state graph:

  load -> ratios -> prescreen -> events -> retrieve -> reason(ToT + Validator) -> finalize
                                              ^             |
                                              +--- revise --+   (bounded: max_revision_rounds)

Any node may set state["stop"] to leave early with a precise outcome (insufficient, prescreen_fail,
frozen by an event). The agents never route themselves; this orchestrator does.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from .agents import (
    Analyst, Evidence, LLMRetriever, Planner, ValidationContext, Validator,
)
from .audit import AuditLog
from .config import ForecasterConfig
from .industry_map import IndustryMap, load_industry_file
from .graph import END, LANGGRAPH, FlowState, LangGraphStateGraph, StateGraph
from .guardrails import FREEZE_EVENTS, check_feedback, confidence, is_stale
from .llm import LLMClient, LLMGate
from .memory import TREND_STRONG_COVERAGE, FeedbackMemory, ShortTermMemory, TrendLibrary
from .models import SECTION_DEBT, Branch, Claim, Scorecard, Status, industry_of
from .review import load_overrides
from .notes import NOTE_TAGS, llm_extract, parse_notes
from .rating import group_of, implied_category, passes_target
from .ratios import (
    FactSet, MissingDataError, compute_ratio, core_ratios, note_ratios, parse_debt_from_text, plausibility, series,
)
from .retrieval import HybridIndex
from .scoring import composite, wall_year
from .tools import BudgetExceeded, OfflineEdgar, ToolFailure, ToolRegistry
from .tot import TotResult, TreeOfThought


@dataclass
class CompanyResult:
    scorecard: Scorecard
    claims: List[Claim] = field(default_factory=list)
    branches: List[Branch] = field(default_factory=list)
    path: List[str] = field(default_factory=list)


class Forecaster:
    def __init__(self, cfg: Optional[ForecasterConfig] = None, source: Optional[OfflineEdgar] = None,
                 llm: Optional[LLMClient] = None, feedback: Optional[FeedbackMemory] = None,
                 trends: Optional[TrendLibrary] = None, audit: Optional[AuditLog] = None,
                 inject_failures: Optional[dict] = None):
        self.cfg = cfg or ForecasterConfig()
        state_dir = Path(self.cfg.state_dir)
        self.source = source or OfflineEdgar()
        self.index = self.source.make_index() if hasattr(self.source, "make_index") else HybridIndex(self.source.all_chunks())
        self.audit = audit or AuditLog(state_dir / "audit.jsonl")
        self.feedback = feedback if feedback is not None else FeedbackMemory(state_dir / "feedback.json")
        self.trends = trends if trends is not None else TrendLibrary(state_dir / "trends.json")
        self.industries = IndustryMap(load_industry_file(self.cfg.industry_file) if self.cfg.industry_file else {})
        self.registry = ToolRegistry(self.source, self.index, self.cfg, self.audit, inject_failures)
        self.llm = LLMGate(llm if llm is not None else LLMClient(), self.cfg, self.audit)
        self.overrides = load_overrides(state_dir)          # past human decisions, shown to the next reviewer
        self.planner, self.retriever = Planner(self.feedback), LLMRetriever(self.registry, self.cfg, self.llm)
        self.analyst, self.validator = Analyst(self.cfg, self.llm, self.index), Validator(self.cfg)
        self.tot = TreeOfThought(self.analyst, self.validator, self.cfg, self.audit)
        self.as_of = date.fromisoformat(self.cfg.as_of)
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        use_lg = LANGGRAPH and self.cfg.use_langgraph
        self.graph_backend = "langgraph" if use_lg else "in-house"
        g = LangGraphStateGraph(FlowState) if use_lg else StateGraph()

        def node(name, fn):
            if not use_lg:
                return fn                                  # the in-house graph records the route itself
            def run(state):
                return {**(fn(state) or {}), "path": [name]}   # LangGraph merges the partial update into the state
            return run

        for name, fn in (("load", self.n_load), ("ratios", self.n_ratios), ("prescreen", self.n_prescreen),
                         ("events", self.n_events), ("retrieve", self.n_retrieve), ("notes", self.n_notes), ("lessons", self.n_lessons),
                         ("reason", self.n_reason),
                         ("revise", self.n_revise), ("finalize", self.n_finalize)):
            g.add_node(name, node(name, fn))
        g.set_entry_point("load")

        def stop_or(src, nxt):
            g.add_conditional_edges(src, lambda s: "finalize" if s.get("stop") else nxt,
                                    {"finalize": "finalize", nxt: nxt})
        stop_or("load", "ratios")
        stop_or("ratios", "prescreen")
        stop_or("prescreen", "events")
        stop_or("events", "retrieve")
        g.add_edge("retrieve", "notes")
        g.add_edge("notes", "lessons")
        g.add_edge("lessons", "reason")
        g.add_conditional_edges("reason", self.route_after_reason, {"revise": "revise", "finalize": "finalize"})
        g.add_edge("revise", "retrieve")
        g.add_edge("finalize", END)
        return g.compile()

    def graph_mermaid(self) -> str:
        """The workflow as a Mermaid diagram (LangGraph draws it from the graph itself)."""
        if self.graph_backend != "langgraph":
            return ""
        return self.graph.get_graph().draw_mermaid()

    def route_after_reason(self, s: dict) -> str:
        tot: TotResult = s["tot"]
        improvable = bool(s["evidence"].missing) or bool(tot.best and tot.best.missing_items)
        if tot.outcome != "final" and s["rounds"] < self.cfg.max_revision_rounds and improvable:
            return "revise"
        return "finalize"

    # ------------------------------------------------------------------ nodes
    stage1 = False

    def n_load(self, s: dict) -> dict:
        t = s["ticker"]
        bundle = self.registry.mcp_call(t, "edgar_facts")
        fs = FactSet(bundle["cik"], bundle)
        fallback = False
        problems = plausibility(fs)
        if problems:
            self.audit.log("plausibility_flag", ticker=t, problems=problems)
            hits = self.registry.mcp_call(t, "filing_section", cik=bundle["cik"], sections=[SECTION_DEBT])
            for h in hits:                                   # re-extract the number from filing text
                value = parse_debt_from_text(h.chunk.text)
                if value is not None:
                    fs.override("TotalDebt", fs.q_periods[-1], value, h.chunk.chunk_id)
                    fallback = True
                    self.audit.log("fallback_extraction", ticker=t, chunk=h.chunk.chunk_id, value=value)
            problems = plausibility(fs)
            if problems:
                return {"stop": dict(status=Status.INSUFFICIENT.value, reason="implausible facts: " + "; ".join(problems),
                                     skip="implausible"), "bundle": bundle, "fs": fs}
        return {"bundle": bundle, "fs": fs, "cik": bundle["cik"], "fallback_used": fallback,
                "stm": ShortTermMemory(), "rounds": 0}

    def n_ratios(self, s: dict) -> dict:
        fs: FactSet = s["fs"]
        try:
            ratios = core_ratios(fs, self.cfg.horizon_years, self.as_of.year, with_liquidity=False)
        except MissingDataError as exc:
            return {"stop": dict(status=Status.INSUFFICIENT.value, reason=str(exc), skip="missing_facts",
                                 missing=exc.missing)}
        ser = series(fs)
        self.trends.ingest(s["cik"], ser)
        trend = self.trends.signal(s["cik"])
        s["stm"].figures.update({k: v.value for k, v in ratios.items()})
        return {"ratios": ratios, "series": ser, "trend": trend}

    def n_prescreen(self, s: dict) -> dict:
        t, r, cfg = s["ticker"], s["ratios"], self.cfg
        latest = s["bundle"]["filed"]["10-Q"]
        if is_stale(latest, cfg.as_of, cfg.max_filing_age_days):
            return {"stop": dict(status=Status.INSUFFICIENT.value, skip="stale",
                                 reason=f"latest 10-Q filed {latest} is older than {cfg.max_filing_age_days} days")}
        fails = []
        if r["leverage_ltm"].value > cfg.max_leverage:
            fails.append(f"leverage {r['leverage_ltm'].value:.1f}x > {cfg.max_leverage:.1f}x")
        if r["coverage_q"].value < cfg.min_coverage:
            fails.append(f"coverage {r['coverage_q'].value:.1f}x < {cfg.min_coverage:.1f}x")
        if r["current_ratio"].value < cfg.min_current_ratio:
            fails.append(f"current ratio {r['current_ratio'].value:.2f} < {cfg.min_current_ratio:.2f}")
        if fails:
            self.audit.log("prescreen_fail", ticker=t, reasons=fails)
            return {"stop": dict(status=Status.PRESCREEN_FAIL.value, reason="; ".join(fails), skip="prescreen")}
        update: dict = {}
        if cfg.target_ratings:                  # RegCap / CLO: stay inside the rating categories asked for
            given = cfg.rating_overrides.get(t)
            try:                                # last-four-quarters coverage is steadier; one quarter is the fallback
                cov = compute_ratio(s["fs"], "coverage_ltm").value
            except MissingDataError:
                cov = r["coverage_q"].value
            cat = given or implied_category(r["leverage_ltm"].value, cov)
            how = "rating file" if given else (f"implied: leverage {r['leverage_ltm'].value:.1f}x, coverage {cov:.1f}x")
            if not passes_target(cat, cfg.target_ratings, implied=not given):
                self.audit.log("prescreen_fail", ticker=t, reasons=[f"rating category {cat}"])
                return {"stop": dict(status=Status.PRESCREEN_FAIL.value, skip="rating",
                                     reason=f"rating category {cat} ({how}) is outside the target ({', '.join(cfg.target_ratings)})")}
            if cfg.min_ltm_ebitda > 0:                                   # size floor: LTM EBITDA in $ millions
                ltm = sum(f.value for f in s["fs"].need([("EBITDA", q) for q in s["fs"].q_periods[-4:]]))   # facts are in $ millions
                if ltm < cfg.min_ltm_ebitda:
                    self.audit.log("prescreen_fail", ticker=t, reasons=["too small"])
                    return {"stop": dict(status=Status.PRESCREEN_FAIL.value, skip="too_small",
                                         reason=f"LTM EBITDA ${ltm:,.0f}M < ${cfg.min_ltm_ebitda:,.0f}M: too small for this book")}
            if r["leverage_ltm"].value < cfg.min_obligor_leverage:       # (almost) no debt: nothing for a CLO or a RegCap book to hold
                self.audit.log("prescreen_fail", ticker=t, reasons=["almost no debt"])
                return {"stop": dict(status=Status.PRESCREEN_FAIL.value, skip="no_debt",
                                     reason=f"leverage {r['leverage_ltm'].value:.1f}x < {cfg.min_obligor_leverage:.1f}x: almost no debt")}
            update["implied"] = f"{cat} ({how})" if given else f"{group_of(cat)} group ({how}; the notch is not resolvable from ratios)"
            update["rating_key"] = cat if given else group_of(cat)
        # liquidity cover needs cash-flow and maturity data; it is asked for only once the company is inside the target
        try:
            liq = compute_ratio(s["fs"], "liquidity_cover", horizon=cfg.horizon_years, as_of_year=self.as_of.year)
        except MissingDataError as exc:
            return {"stop": dict(status=Status.INSUFFICIENT.value, reason=str(exc), skip="missing_facts", missing=exc.missing)}
        r["liquidity_cover"] = liq
        s["stm"].figures["liquidity_cover"] = liq.value
        if self.stage1:                         # two-stage run: stop here, rank on the financial score, read filings later
            comp, _parts = composite(r, s["series"]["coverage_q"], "stable")
            cat = update.get("rating_key", "")
            return {"stop": dict(status=Status.CANDIDATE.value, skip="candidate", composite=comp, category=cat,
                                 reason=f"passed the financial screen (score {comp:.2f}); filings not read yet"
                                        + (f"; rating category {update['implied']}" if update.get("implied") else "")),
                    **update}
        return update

    def n_events(self, s: dict) -> dict:
        t = s["ticker"]
        events = self.registry.mcp_call(t, "edgar_events", since=s["bundle"]["filed"]["10-Q"])
        hit = [e for e in events if e.type in FREEZE_EVENTS]
        if hit:
            self.audit.log("event_freeze", ticker=t, events=[e.type for e in hit])
            reason = "; ".join(f"{e.type} (8-K {e.filed})" for e in hit)
            return {"stop": dict(status=Status.UNCERTAIN.value, skip="event", reason="post-filing event: " + reason,
                                 cites=[e.chunk_id for e in hit], review="post-filing event: " + reason)}
        return {}

    def n_retrieve(self, s: dict) -> dict:
        plan = self.planner.plan(s["bundle"], self.cfg)
        new = self.retriever.gather(s["ticker"], s["cik"], plan, escalate=s["rounds"])
        ev: Evidence = s.get("evidence") or Evidence()
        ev.merge(new)
        return {"evidence": ev}

    def n_notes(self, s: dict) -> dict:
        """Read the 10-Q notes into cited facts and compute the ratios that need them.

        Credit facilities, leases, commitments and rate exposure decide future cash flow: they feed the adjusted liquidity
        cover and the covenant headroom. A figure the notes do not state stays unknown (never zero) and is flagged.
        """
        fs: FactSet = s["fs"]
        chunks = list(s["evidence"].context.values())
        found = parse_notes(chunks)
        missing = [t for t in NOTE_TAGS if t not in found]
        if missing and self.llm.enabled:
            found.update(llm_extract(self.llm, s["ticker"], chunks, missing))
        for tag, (value, cid) in found.items():
            fs.add_note_fact(tag, value, cid)
        nr = note_ratios(fs, self.cfg.horizon_years, self.as_of.year)
        ratios = dict(s["ratios"])
        ratios.update(nr["ratios"])
        gaps = sorted({m.split(":")[2] for m in nr["missing"]})
        self.audit.log("notes_read", ticker=s["ticker"], figures=sorted(found), missing=gaps)
        return {"ratios": ratios, "notes_missing": gaps}

    def n_lessons(self, s: dict) -> dict:
        """Check the desk's saved lessons against this filing (LLM reading; keyword lessons also run in _decide)."""
        if s.get("lesson_hits") is not None:
            return {}
        lessons = self.feedback.applicable(s["bundle"]["sector"])
        if not lessons or not self.llm.enabled:
            return {"lesson_hits": []}
        self.audit.log("lessons_supplied", ticker=s["ticker"], ids=[c.id for c in lessons])
        hits = self.analyst.check_lessons({"ticker": s["ticker"], "cik": s["cik"]}, lessons)
        return {"lesson_hits": hits}

    def n_reason(self, s: dict) -> dict:
        ctx = ValidationContext(s["ticker"], s["cik"], s["fs"], self.index, s["trend"].multi_period_decline, self.as_of)
        material = sum(q.get("ebitda") or 0.0 for q in s["bundle"]["quarters"][-4:]) >= self.cfg.escalation_min_ebitda
        beam, depth = None, None
        if s["rounds"] >= 1 and self.cfg.allow_escalation and material:
            beam, depth = self.cfg.escalation_beam_width, self.cfg.max_depth + 1    # escalate once
            self.audit.log("tot_escalation", ticker=s["ticker"])
        cov = s["series"]["coverage_q"]
        shock = (len(cov) >= 2 and cov[-2].value > 0 and (cov[-2].value - cov[-1].value) / cov[-2].value > self.cfg.shock_drop
                 and cov[-1].value < TREND_STRONG_COVERAGE)          # a drop from 40x to 28x is no credit event
        info = {"sector": s["bundle"]["sector"], "trend": s["trend"].details,
                "lessons": [{"id": c.id, "lesson": c.lesson or c.criterion}
                            for c in self.feedback.applicable(s["bundle"]["sector"])],
                "metrics": {k: round(v.value, 2) for k, v in s["ratios"].items()}}
        res = self.tot.run(s["ticker"], s["evidence"], s["fs"], s["ratios"], ctx, beam, depth, info, shock)
        return {"tot": res}

    def n_revise(self, s: dict) -> dict:
        self.audit.log("revision_round", ticker=s["ticker"], round=s["rounds"] + 1,
                       missing=s["evidence"].missing or s["tot"].best.missing_items)
        return {"rounds": s["rounds"] + 1}

    # ------------------------------------------------------------------ finalize
    def n_finalize(self, s: dict) -> dict:
        b = s.get("bundle") or {}
        sc = Scorecard(ticker=s["ticker"], cik=b.get("cik", ""), name=b.get("name", ""), sector=b.get("sector", ""),
                       industry=self.industries.industry(s["ticker"], b.get("sic")), group_sector=self.industries.sector(s["ticker"]), industry_desc=b.get("sic_desc", ""), sponsor=b.get("sponsor"), tool_calls=self.registry.calls(s["ticker"]),
                       revision_rounds=s.get("rounds", 0))
        claims: List[Claim] = []
        stop = s.get("stop")
        if s.get("ratios"):
            sc.metrics = {k: round(v.value, 3) for k, v in s["ratios"].items()}
        if stop:
            sc.status, sc.skip_reason = stop["status"], stop.get("skip", "")
            sc.rationale = [stop["reason"]]
            sc.composite = stop.get("composite", 0.0)
            if stop.get("category"):
                sc.flags.append("rating:" + stop["category"])
            sc.missing = stop.get("missing", [])
            if stop.get("review"):
                sc.review_reasons.append(stop["review"])
                sc.flags.append("event_freeze")
            sc.confidence = confidence(sc.status, 0.0, False, 0, False)
            self.audit.log("scorecard", ticker=sc.ticker, status=sc.status, reason=stop["reason"])
            return {"result": CompanyResult(sc, [], [], s["path"] + ["finalize"])}
        self._decide(s, sc)
        best = s["tot"].best
        claims = best.claims if best else []
        self.audit.log("scorecard", ticker=sc.ticker, status=sc.status, composite=sc.composite,
                       confidence=sc.confidence, review=sc.review_reasons, scope=sc.scope,
                       llm_calls=self.llm.calls(sc.ticker))
        return {"result": CompanyResult(sc, claims, s["tot"].branches, s["path"] + ["finalize"])}

    def _decide(self, s: dict, sc: Scorecard) -> None:
        cfg, ev, tot, r = self.cfg, s["evidence"], s["tot"], s["ratios"]
        best = tot.best
        sc.used_tot = tot.used_tot
        sc.interpretation = best.interpretation if best else ""
        if best and sc.interpretation != "stable":
            sc.scope = next((c.params.get("scope", "") for c in best.claims if c.kind == "judgment"), "")
            # industry-wide weakness only counts when the company's own filing text says so (a cited industry-conditions passage)
            sc.industry_evidenced = any(c.kind == "fact" and c.topic == "industry_conditions" and c.citations for c in best.claims)
        sc.wall_year = wall_year(s["bundle"]["maturities"], self.as_of.year, cfg.horizon_years, cfg.wall_share_threshold)
        comp, parts = composite(r, s["series"]["coverage_q"], sc.interpretation)
        sc.composite = comp
        sc.metrics.update({f"score_{k}": round(v, 3) for k, v in parts.items()})
        if s["trend"].details:
            sc.rationale.extend("Trend: " + d for d in s["trend"].details)
        if s.get("implied"):
            sc.rationale.append("Rating category: " + s["implied"])

        # ---- hard vetoes -------------------------------------------------------------
        liq = r.get("liquidity_cover_adj", r["liquidity_cover"])
        hr = r.get("covenant_headroom")
        veto = None
        gc = ev.findings.get("going_concern")
        if gc:
            veto = f"going-concern doubt cited ({gc[0][0].chunk_id})"
        elif hr is not None and hr.value < 0:
            veto = "covenant breached: actual leverage is above the limit stated in the credit-facility note"
        elif liq.value < cfg.min_liquidity_cover and not liq.params.get("proxy") and not liq.params.get("capex_unknown"):
            veto = (f"liquidity cover {liq.value:.2f} < {cfg.min_liquidity_cover:.2f}: cash, revolver and projected free cash "
                    f"flow do not cover maturities, leases, commitments, litigation and rate shock over {cfg.horizon_years} years")
        if liq.params.get("proxy"):
            sc.flags.append("liquidity:no_maturity_schedule")
        if liq.params.get("capex_unknown"):
            sc.flags.append("liquidity:capex_unknown")
        if veto:
            sc.status = Status.EXCLUDE.value
            sc.flags.append("veto:" + veto.split(" ")[0])
            sc.rationale.insert(0, "Hard veto: " + veto)
        elif best is None:
            sc.status = Status.INSUFFICIENT.value
            sc.skip_reason = "validation"
            sc.missing = list(ev.missing)
            sc.rationale.insert(0, "No branch passed the Validator" + (f"; missing {', '.join(ev.missing)}" if ev.missing else ""))
        elif tot.outcome == "uncertain":
            sc.status = Status.UNCERTAIN.value
            sc.missing = list(best.missing_items)
            sc.rationale.insert(0, "Evidence incomplete after retries: " + ", ".join(best.missing_items))
        elif comp < cfg.min_composite:
            sc.status = Status.EXCLUDE.value
            sc.rationale.insert(0, f"Composite {comp:.2f} below the {cfg.min_composite:.2f} floor")
        else:
            sc.status = Status.INCLUDE.value
            sc.rationale.insert(0, f"Composite {comp:.2f}; interpretation: {sc.interpretation}")

        # ---- analyst feedback memory --------------------------------------------------
        if best is not None or veto:
            texts = [c.text for c in self.index.company_chunks(s["cik"]) if c.form != "8-K"
                     and not self.index.is_stale(c)]
            fired = [(crit, f"matched '{kw}'") for crit, kw in check_feedback(
                self.feedback.applicable(sc.sector), texts)]
            seen = {c.id for c, _ in fired}
            fired += [(crit, f"LLM read: \u201c{quote}\u201d [{cid}]") for crit, cid, quote in (s.get("lesson_hits") or [])
                      if crit.id not in seen]
            for crit, why in fired:
                sc.lessons.append(crit.id)
                text = crit.lesson or crit.criterion
                if crit.action == "exclude" and sc.status == Status.INCLUDE.value:
                    sc.status = Status.EXCLUDE.value
                    sc.flags.append("veto:analyst_criterion")
                    sc.rationale.insert(0, f"Desk lesson {crit.id} ('{text}') applies: {why}")
                else:
                    sc.review_reasons.append(f"desk lesson {crit.id}: {text} ({why})")
                self.audit.log("criterion_applied", ticker=sc.ticker, criterion=crit.id, action=crit.action, why=why)
        for o in self.overrides:
            if o.get("ticker") == sc.ticker:
                sc.review_reasons.append(
                    f"analyst {o.get('analyst', '?')} previously overrode this name to '{o.get('decision')}' on {str(o.get('ts', ''))[:10]}"
                    + (f": {o['note']}" if o.get("note") else ""))

        # ---- flags, review triggers, confidence ------------------------------------------
        if sc.status in (Status.INCLUDE.value, Status.UNCERTAIN.value):
            if hr is not None and hr.value < cfg.covenant_headroom_min:
                sc.flags.append("covenant_tight")
                sc.review_reasons.append(f"covenant headroom only {hr.value * 100:.0f}% (credit-facility note)")
            for tag, label in (("litigation", "litigation"), ("off_balance_sheet", "off-balance-sheet exposure"),
                               ("adverse_development", "adverse development"), ("subsequent_event", "subsequent event")):
                for chunk, sent in ev.findings.get(tag, [])[:1]:
                    line = f"{label} in the notes: \u201c{sent[:200]}\u201d [{chunk.chunk_id}]"
                    if cfg.keyword_hits_need_review:
                        sc.review_reasons.append(line)
                    else:
                        sc.rationale.append("Keyword hit (not queued): " + line)
            if s.get("notes_missing"):
                sc.flags.append("notes_incomplete")
                if cfg.note_figures_need_review:
                    sc.review_reasons.append("10-Q note figures missing: " + ", ".join(s["notes_missing"]))
                else:
                    sc.rationale.append("10-Q note figures not found (optional): " + ", ".join(s["notes_missing"]))
        if s.get("fallback_used"):
            sc.flags.append("fallback_text_extraction")
            sc.review_reasons.append("a figure was re-extracted from filing text after an implausible XBRL value")
        if sc.interpretation == "deterioration" and sc.status == Status.INCLUDE.value:
            sc.review_reasons.append("multi-period deterioration signal")
        if sc.scope == "industry":
            sc.review_reasons.append("weakness attributed to industry-wide conditions, not only this company")
        if sc.interpretation == "setback":
            sc.review_reasons.append("one-time setback accepted on cited evidence: confirm the recovery plan")
        if tot.ambiguous_conflict:
            sc.review_reasons.append("branches disagreed on interpretation; the conservative one was chosen")
        if sc.status == Status.UNCERTAIN.value:
            sc.review_reasons.append("evidence incomplete")
        mean = best.scores.mean if best else 0.0
        sc.confidence = confidence(sc.status, mean, bool(s.get("fallback_used") or s.get("notes_missing")), s.get("rounds", 0),
                                 tot.ambiguous_conflict)

    # ------------------------------------------------------------------ public API
    def screen_company(self, ticker: str) -> CompanyResult:
        state = {"ticker": ticker}
        try:
            state = self.graph.invoke(state, config={"recursion_limit": 60})
            return state["result"]
        except (BudgetExceeded, ToolFailure) as exc:
            kind = "budget" if isinstance(exc, BudgetExceeded) else "tool_failure"
            self.audit.log("company_aborted", ticker=ticker, reason=kind, detail=str(exc))
            sc = Scorecard(ticker=ticker, status=Status.INSUFFICIENT.value, skip_reason=kind,
                           rationale=[f"{kind}: {exc}"], tool_calls=self.registry.calls(ticker))
            return CompanyResult(sc, [], [], state.get("path", []))
        except Exception as exc:        # one odd filing must never stop a run over thousands of companies
            import traceback
            self.audit.log("company_aborted", ticker=ticker, reason="error", detail=f"{type(exc).__name__}: {exc}",
                           where=" <- ".join(f"{f.name}:{f.lineno}" for f in reversed(traceback.extract_tb(exc.__traceback__)[-4:])))
            sc = Scorecard(ticker=ticker, status=Status.INSUFFICIENT.value, skip_reason="error",
                           rationale=[f"unexpected error ({type(exc).__name__}: {exc}); see audit.jsonl"],
                           tool_calls=self.registry.calls(ticker))
            return CompanyResult(sc, [], [], [])

    def screen_two_stage(self, tickers: Optional[List[str]] = None, pool_factor: float = 3.0, progress: bool = False):
        """Stage 1: ratios, rating gate and liquidity for everybody (no filing text). Stage 2: the full workflow, but only for
        the best `pool_factor x top_n` survivors, so reading the big documents (and paying the LLM) is limited to the pool."""
        import math
        import sys
        from collections import Counter as C
        self.stage1 = True
        try:
            first = self.screen_all(tickers, progress)
        finally:
            self.stage1 = False
        cands = [r for r in first if r.scorecard.status == Status.CANDIDATE.value]

        def cat_of(r):
            return group_of(next((f.split(":", 1)[1] for f in r.scorecard.flags if f.startswith("rating:")), ""))
        key = lambda r: (-r.scorecard.composite, r.scorecard.ticker)
        size = max(self.cfg.top_n, math.ceil(pool_factor * self.cfg.top_n))
        cap = max(1, math.floor(min(1.0, 1.5 * self.cfg.sector_cap_frac) * size))     # a sector may not fill the whole pool
        used, pool, taken = C(), [], set()

        def take(r) -> bool:
            if len(pool) < size and used[r.scorecard.sector] < cap and r.scorecard.ticker not in taken:
                pool.append(r.scorecard.ticker)
                taken.add(r.scorecard.ticker)
                used[r.scorecard.sector] += 1
                return True
            return False
        ranked = sorted(cands, key=key)
        for cat, share in self.cfg.category_quota.items():             # e.g. a CLO pool is mostly BBB names
            got = 0
            for r in (x for x in ranked if cat_of(x) == cat):
                if got >= math.ceil(share * size):
                    break
                got += take(r)
        for r in ranked:                                              # unfilled quota spills over to the best of the rest
            take(r)
        spare = [r for r in ranked if r.scorecard.ticker not in taken]
        print(f"Stage 1: {len(cands)} of {len(first)} passed the financial screen; reading filings for the best {len(pool)} "
              f"(pool = {pool_factor:g} x {self.cfg.top_n})", file=sys.stderr, flush=True)
        for r in spare:
            r.scorecard.skip_reason = "outside_pool"
            r.scorecard.rationale = [r.scorecard.rationale[0] + "; outside the reading pool"] if r.scorecard.rationale else []
        second = self.screen_all(pool, progress) if pool else []
        return [r for r in first if r.scorecard.status != Status.CANDIDATE.value or r.scorecard.ticker not in set(pool)] + second

    def screen_all(self, tickers: Optional[List[str]] = None, progress: bool = False) -> List[CompanyResult]:
        tickers = tickers or self.source.tickers()
        if not progress:
            with ThreadPoolExecutor(max_workers=self.cfg.workers) as ex:
                return list(ex.map(self.screen_company, tickers))
        import sys
        import threading
        import time
        from collections import Counter
        lock, seen, t0, last = threading.Lock(), Counter(), time.time(), [time.time()]
        http = getattr(self.source, "http", None)

        def one(t):
            r = self.screen_company(t)
            with lock:
                seen[r.scorecard.status] += 1
                done = sum(seen.values())
                if done == len(tickers) or done % 100 == 0 or time.time() - last[0] > 20:
                    last[0] = time.time()
                    req = f" | {http.requests:,} requests to sec.gov, {http.cache_hits:,} cached" if http else ""
                    print(f"  screened {done:,} / {len(tickers):,} in {int(time.time() - t0)}s | "
                          + ", ".join(f"{k}: {v}" for k, v in sorted(seen.items())) + req, file=sys.stderr, flush=True)
            return r
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as ex:
            return list(ex.map(one, tickers))

    def log_override(self, ticker: str, analyst: str, action: str, reason: str) -> None:
        """Human overrides are logged, never silently applied."""
        self.audit.log("override", ticker=ticker, analyst=analyst, action=action, reason=reason)
