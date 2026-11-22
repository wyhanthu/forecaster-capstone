"""python -m forecaster.cli screen --top-n 8 --output report.md
   python -m forecaster.cli feedback add "no exposure to X" --keywords "x, y" --action review
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .industry_map import DEFAULT_FILE
from .config import ForecasterConfig
from .env import load_env
from .memory import FeedbackMemory
from .portfolio import build_portfolio
from .report import render
from .review import ReviewSession
from .workflow import Forecaster

FINANCE_SECTORS = ("Finance", "Real Estate")        # Nasdaq.com sectors dropped from the universe (unless --include-financials)


class _NoLLM:
    enabled = False


def _make_source(a, cfg):
    """(source, report note). source None = the synthetic universe; otherwise the live EDGAR client.

    Live runs need only `--mode regcap|clo --maturity YYYY-MM-DD` (plus the two SEC bulk files if you downloaded them):
    the universe is built from the bulk files, the rating target from the mode, the horizon from the maturity date.
    """
    import math
    from .config import LIVE_CALCS, LIVE_SOUGHT, LIVE_TOPICS
    from .edgar import RealEdgar, SecHttp, check_user_agent, read_tickers_file
    from .rating import ALIASES, MODE_LABEL, MODES, load_ratings
    from .universe import load_or_build
    if a.source == "offline" and not a.mode:
        return None, None
    http = SecHttp(check_user_agent(os.getenv("SEC_USER_AGENT")), Path(a.state_dir) / "edgar_cache")
    src = RealEdgar([], http, getattr(a, "facts_zip", None), getattr(a, "submissions_zip", None))
    cfg.required_topics, cfg.required_calcs, cfg.sought_topics = LIVE_TOPICS, LIVE_CALCS, LIVE_SOUGHT
    cfg.review_min_composite = 0.6
    cfg.keyword_hits_need_review = cfg.rule_adverse_blocks_stable = cfg.note_figures_need_review = False
    cfg.scan_risk_factors = False                  # live Item 1A is boilerplate ("we may not be able to comply ..."): LLM-read only
    lines = ["Live SEC filings (EDGAR)."]
    if a.mode:
        if not a.maturity:
            raise SystemExit("--mode needs --maturity YYYY-MM-DD (the date the collateral must last until)")
        years = (date.fromisoformat(a.maturity) - date.fromisoformat(cfg.as_of)).days / 365.25
        if years <= 0:
            raise SystemExit(f"--maturity {a.maturity} is not after the as-of date {cfg.as_of}")
        cfg.horizon_years = max(1, math.ceil(years))
        cfg.target_ratings = tuple(x.strip() for x in a.target_ratings.split(",")) if a.target_ratings else MODES[a.mode]
        cfg.target_ratings = tuple(dict.fromkeys(y for x in cfg.target_ratings for y in ALIASES.get(x, (x,))))   # BBB = BBB+, BBB
        if a.mode == "clo":
            cfg.category_quota = {"core": 0.8, "BB": 0.2}   # a CLO book is mainly BBB
            cfg.min_obligor_leverage = 0.5              # a CLO lives on coupon: a company with (almost) no debt pays none
        elif a.mode == "regcap":
            cfg.category_quota = {"core": 0.7, "top": 0.3}
            cfg.min_obligor_leverage = 0.2              # RegCap needs a real debt-paying obligor, but a lower bar than a CLO
        if a.min_ebitda is not None:
            cfg.min_ltm_ebitda = a.min_ebitda
        if a.min_leverage is not None:
            cfg.min_obligor_leverage = a.min_leverage
        if a.ratings_file:
            cfg.rating_overrides = load_ratings(a.ratings_file)
        lines.append(f"Mode: {MODE_LABEL[a.mode]} | maturity {a.maturity} ({years:.1f} years, horizon {cfg.horizon_years}) | as of {cfg.as_of}")
        lines.append("Rating categories are IMPLIED from leverage and interest coverage, because SEC filings carry no credit ratings; "
                     f"target: {', '.join(cfg.target_ratings)}."
                     + (f" A ratings file overrides {len(cfg.rating_overrides)} names." if cfg.rating_overrides else ""))
    tickers = [t.strip().upper() for t in (a.tickers or "").split(",") if t.strip()]
    if a.tickers_file:
        tickers += [t for t in read_tickers_file(a.tickers_file) if t not in tickers]
    if tickers:                                    # an explicit list overrides the automatic universe
        src._tickers = [t.upper().replace(".", "-") for t in tickers]
        lines.append(f"Universe: {len(tickers)} tickers given on the command line.")
    elif a.mode:
        if not getattr(a, "facts_zip", None) or not getattr(a, "submissions_zip", None):
            print("No bulk files given: reading every submissions record from SEC (cached afterwards; about 10 minutes).\n"
                  "Faster: download companyfacts.zip and submissions.zip once and pass --facts-zip / --submissions-zip.", file=sys.stderr)
        uni = load_or_build(Path(a.state_dir) / "universe", cfg.as_of, a.rebuild_universe, ciks=src.all_ciks(),
                            read_subs=src.read_subs, max_age_days=cfg.max_filing_age_days, include_financials=a.include_financials)
        tickers_ok = uni["tickers"]
        if not a.include_financials:                # the Nasdaq.com sector catches names the SIC code does not (e.g. MA, MCO)
            from .industry_map import load_industry_file
            imap = load_industry_file(cfg.industry_file or None)
            tickers_ok = {t: c for t, c in tickers_ok.items() if imap.get(t, ("", ""))[0] not in FINANCE_SECTORS}
            uni["funnel"]["not Finance / Real Estate (Nasdaq.com sector)"] = len(tickers_ok)
        src.set_universe(tickers_ok)
        uni = {**uni, "tickers": tickers_ok}
        lines.append("Universe: " + " > ".join(f"{k} {v:,}" for k, v in uni["funnel"].items()))
        print(f"Universe: {len(uni['tickers']):,} companies.", file=sys.stderr)
    else:
        raise SystemExit("--source edgar needs --mode regcap|clo, or --tickers / --tickers-file")
    return src, "\n".join(lines)


def _screen(a) -> int:
    live = a.source == "edgar" or bool(a.mode)
    # live runs default to the system date; the offline sample keeps a fixed date so its output is reproducible
    as_of = a.as_of if a.as_of != "today" else (date.today().isoformat() if live else "2026-11-01")
    cfg = ForecasterConfig(as_of=as_of, top_n=a.top_n or (100 if a.mode else 8), state_dir=a.state_dir, workers=a.workers)
    if a.mode:                                   # live runs: a CLO-style diversification limit per industry
        cfg.industry_cap_frac = a.industry_cap
    if getattr(a, "industry_file", None):
        cfg.industry_file = ",".join(a.industry_file)
    elif a.mode:
        cfg.industry_file = str(DEFAULT_FILE)          # live runs: the bundled Nasdaq.com industry map
    if a.sector_cap is not None:
        cfg.sector_cap_frac = a.sector_cap
    try:
        source, note = _make_source(a, cfg)
    except ValueError as exc:
        raise SystemExit(str(exc))
    fc = Forecaster(cfg, source=source, llm=_NoLLM() if a.no_llm else None)
    client = getattr(fc.llm, "client", None)
    print("LLM: off (--no-llm); running the rule-based path" if a.no_llm
          else client.status() if hasattr(client, "status") else "LLM: enabled", file=sys.stderr)
    tickers = source.tickers()[:a.limit] if (source is not None and getattr(a, "limit", None)) else None
    if live and a.pool_factor and a.pool_factor > 0:
        results = fc.screen_two_stage(tickers, pool_factor=a.pool_factor, progress=True)
    else:
        results = fc.screen_all(tickers, progress=source is not None)
    n_llm = fc.audit.count("llm_call")
    print(f"LLM calls made: {n_llm}" + ("" if n_llm or a.no_llm else
          "  (none: the run used the rule-based path; check OPENAI_API_KEY or OPENROUTER_API_KEY in .env)"), file=sys.stderr)
    if getattr(fc.llm, "errors", 0):
        print(f"LLM calls that FAILED: {fc.llm.errors} of {n_llm}; the rule-based path covered them. First error: "
              f"{fc.llm.first_error}", file=sys.stderr)
    if source is not None:
        http = source.http
        why = Counter(r.scorecard.skip_reason or r.scorecard.status for r in results)
        print(f"EDGAR: {len(results)} companies | {http.requests} requests to sec.gov, {http.cache_hits} served from cache | "
              + ", ".join(f"{k}: {v}" for k, v in sorted(why.items())), file=sys.stderr)
    port = build_portfolio(results, cfg)
    text = render(port, fc.index, cfg.as_of, note=note, compact=live, cfg=cfg)
    state = Path(a.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    (state / "last_run.json").write_text(json.dumps(       # what `review` works from
        {"as_of": cfg.as_of, "shortlist": port.tickers, "review_queue": [c.ticker for c in port.review_queue],
         "scorecards": {t: r.scorecard.__dict__ for t, r in port.results.items() if r.scorecard in port.shortlist
                        or r.scorecard in port.review_queue or not live}}, indent=2, default=str))
    if a.output:
        Path(a.output).write_text(text)
        Path(a.output).with_suffix(".json").write_text(json.dumps(
            {"shortlist": port.tickers, "counts": port.counts, "breaker": port.breaker,
             "scorecards": {t: r.scorecard.__dict__ for t, r in port.results.items()}}, indent=2, default=str))
        print(f"Wrote {a.output} (+ .json). Shortlist: {', '.join(port.tickers)}")
        if port.review_queue:
            print(f"{len(port.review_queue)} names need human review: python -m forecaster.cli review")
    else:
        print(text)
    return 0


def _feedback(a) -> int:
    fm = FeedbackMemory(Path(a.state_dir) / "feedback.json")
    if a.cmd == "add":
        c = fm.add(a.criterion, (a.keywords or "").split(","), a.action, a.reason or "", lesson=a.lesson or "",
                   source="manual", sector=a.sector or "")
        print(f"added {c.id}")
    elif a.cmd == "list":
        for c in fm.list():
            print(f"{c.id} [{c.action}{'' if c.active else ', off'}] {c.lesson or c.criterion} | keywords: "
                  f"{', '.join(c.keywords) or '-'} | sector: {c.sector or 'all'} | from: {c.source or '-'}")
    elif a.cmd == "edit":
        fm.edit(a.id, criterion=a.criterion, action=a.action, active=None if a.active is None else a.active == "on")
        print("updated")
    elif a.cmd == "delete":
        print("deleted" if fm.delete(a.id) else "not found")
    return 0


def _review(a) -> int:
    rs = ReviewSession(a.state_dir)
    if a.list:
        for c in rs.queue():
            print(f"{c['ticker']} [{c['status']}] " + "; ".join(c.get("review_reasons") or ["status requires review"]))
        return 0
    if a.ticker:
        if not a.decision:
            raise SystemExit("--decision is required with --ticker")
        res = rs.apply(a.ticker, a.decision, a.analyst, a.note, a.lesson, a.keywords.split(","), a.action,
                       "all" if a.all_sectors else "sector")
        print("override logged" if res["override"] == "yes" else "review confirmed",
              f"| lesson saved as {res['criterion_id']}" if res["criterion_id"] else "")
        return 0
    return rs.interactive(a.analyst)


def _graph(a) -> int:
    from .audit import AuditLog
    f = Forecaster(ForecasterConfig(state_dir=a.state_dir), audit=AuditLog(None))
    text = f.graph_mermaid()
    if not text:
        print("The workflow is running on the in-house graph; install langgraph (pip install -r requirements.txt) to draw it.")
        return 1
    print(text)
    return 0


def _llm_check(a) -> int:
    """Makes one tiny LLM call and prints package versions and, on failure, the full (key-free) traceback."""
    import platform
    import traceback
    from importlib import metadata
    from .llm import LLMClient, redact
    client = LLMClient()
    print(client.status())
    print(f"python {platform.python_version()}")
    for pkg in ("langchain-core", "langchain-openai", "langgraph", "openai", "httpx", "httpcore", "pydantic", "anyio", "tiktoken"):
        try:
            print(f"{pkg} {metadata.version(pkg)}")
        except metadata.PackageNotFoundError:
            print(f"{pkg} (not installed)")
    if not client.enabled:
        return 1
    import os
    print(f"provider {client.settings['name']} | model {client.model_name} | base url {client.settings['base'] or '(default)'}")
    try:
        from langchain_core.messages import HumanMessage
        reply = client._model.invoke([HumanMessage(content="Reply with the single word: ok")])
        print(f"OK: model replied {str(reply.content)[:60]!r} | usage {getattr(reply, 'usage_metadata', None)}")
        return 0
    except Exception:
        print("FAILED:\n" + redact(traceback.format_exc(), limit=4000))
        return 1


def _edgar_check(a) -> int:
    """For each ticker: what SEC returned, how it was mapped, and what the screen can compute. The way to see real data."""
    import traceback
    from .edgar import RealEdgar, SecHttp, check_user_agent
    from .notes import NOTE_TAGS, parse_notes
    from .ratios import FactSet, MissingDataError, core_ratios, note_ratios
    try:
        http = SecHttp(check_user_agent(os.getenv("SEC_USER_AGENT")), Path(a.state_dir) / "edgar_cache")
    except ValueError as exc:
        raise SystemExit(str(exc))
    src = RealEdgar([t.upper() for t in a.tickers], http, getattr(a, "facts_zip", None), getattr(a, "submissions_zip", None))
    code = 0
    for t in src.tickers():
        print(f"\n===== {t} " + "=" * 60)
        try:
            d = src.describe(t)
        except Exception:
            print("FAILED:\n" + traceback.format_exc(limit=6))
            code = 1
            continue
        b = d["bundle"]
        print(f"{b['name']} | CIK {b['cik']} | SIC {b['sic']} -> {b['sector']} | latest {b['latest_form']} filed {b['filed']['10-Q']}, "
              f"period end {b['latest_period_end'] or 'NOT FOUND IN XBRL'}")
        print("\nQuarters ($M):  period    EBITDA  interest      debt      cash        CA        CL")
        fmt = lambda v: "     --" if v is None else f"{v:9.1f}"
        for q in b["quarters"]:
            print(f"                {q['period']:8}" + "".join(fmt(q[k]) for k in ("ebitda", "interest", "debt", "cash", "ca", "cl")))
        print("Annual ($M):    FY        EBITDA  interest      debt       CFO     capex")
        for r in b["annual"]:
            print(f"                {r['fy']:<8}" + "".join(fmt(r[k]) for k in ("ebitda", "interest", "debt", "cfo", "capex")))
        print("Maturities ($M):", b["maturities"] or "NONE REPORTED")
        for n in b["data_notes"]:
            print("  note:", n)
        print("\nFiling text (section, form, filed, characters):")
        for sec, form, filed, n in d["chunks"]:
            print(f"  {sec:18} {form:5} {filed} {n:7,}")
        for form, info in d["parse"].items():
            print(f"  [{form}] lines {info.get('_lines')}, items found {info.get('_items')}, notes {info.get('_notes')}")
        print("8-K events after the last periodic filing:", d["events"] or "none")
        for w in d["warnings"]:
            print("  WARNING:", w)
        fs = FactSet(b["cik"], b)
        try:
            r = core_ratios(fs, 2, int(a.as_of[:4]) if a.as_of != "today" else date.today().year)
            print("\nRatios: " + ", ".join(f"{k} {v.value:.2f}" for k, v in r.items()))
        except MissingDataError as exc:
            print("\nRatios NOT computable, missing:", ", ".join(exc.missing))
        chunks = src.chunks_for_cik(b["cik"])
        found = parse_notes(chunks)
        print("Note figures read by the rule parser:", {k: v[0] for k, v in found.items()} or "none",
              "| left for the LLM:", [k for k in NOTE_TAGS if k not in found])
        print(f"Requests so far: {d['requests']} (cache hits {d['cache_hits']})")
    return code


def _source_args(p) -> None:
    p.add_argument("--source", choices=["offline", "edgar"], default="offline",
                   help="offline = the synthetic universe (default); edgar = live SEC filings")
    p.add_argument("--mode", choices=["regcap", "clo"], help="live run: RegCap = investment grade (lowest BBB), CLO = mainly BBB")
    p.add_argument("--maturity", help="YYYY-MM-DD: the date the collateral must last until (with --mode)")
    p.add_argument("--ratings-file", help="optional ticker,rating CSV; real ratings override the implied category")
    p.add_argument("--target-ratings", help="override the mode's categories, e.g. A,BBB,BB (BBB = BBB+, BBB and BBB-; BBB_plus = BBB+ only; A_or_better = AA and A)")
    p.add_argument("--include-financials", action="store_true", help="keep banks, insurers and REITs in the universe")
    p.add_argument("--rebuild-universe", action="store_true", help="ignore the cached universe and rebuild it from the bulk files")
    p.add_argument("--limit", type=int, help="trial run: screen only the first N companies of the universe")
    p.add_argument("--tickers", help="optional: screen only these tickers (comma-separated) instead of the automatic universe")
    p.add_argument("--tickers-file", help="optional: one ticker per line, instead of the automatic universe")
    p.add_argument("--facts-zip", help="SEC bulk companyfacts.zip: facts then cost no requests")
    p.add_argument("--submissions-zip", help="SEC bulk submissions.zip")


def _import_ratings(a) -> int:
    from .edgar import RealEdgar, SecHttp, check_user_agent
    from .ratings_import import latest_ratings, match, sec_names, write_ratings
    from .universe import load_or_build
    as_of = date.today().isoformat() if a.as_of == "today" else a.as_of
    http = SecHttp(check_user_agent(os.getenv("SEC_USER_AGENT")), Path(a.state_dir) / "edgar_cache")
    src = RealEdgar([], http, a.facts_zip, a.submissions_zip)
    uni = load_or_build(Path(a.state_dir) / "universe", as_of, False, ciks=src.all_ciks(), read_subs=src.read_subs,
                        max_age_days=150, include_financials=a.include_financials)
    ratings = latest_ratings(a.csv)
    matched, stat = match(ratings, sec_names(uni["tickers"], src.read_subs))
    write_ratings(a.output, matched)
    print("; ".join(f"{k}: {v:,}" for k, v in stat.items()), file=sys.stderr)
    print(f"Wrote {a.output} ({len(matched):,} tickers) and {Path(a.output).with_suffix('.detail.csv')} (dates and names, to eyeball the matches).")
    return 0


def _calibrate(a) -> int:
    from .calibrate import collect, report
    from .edgar import RealEdgar, SecHttp, check_user_agent
    from .rating import load_ratings
    from .ratios import FactSet, compute_ratio
    from .universe import load_or_build
    as_of = date.today().isoformat() if a.as_of == "today" else a.as_of
    http = SecHttp(check_user_agent(os.getenv("SEC_USER_AGENT")), Path(a.state_dir) / "edgar_cache")
    src = RealEdgar([], http, a.facts_zip, a.submissions_zip)
    uni = load_or_build(Path(a.state_dir) / "universe", as_of, False, ciks=src.all_ciks(), read_subs=src.read_subs,
                        max_age_days=150, include_financials=False)
    src.set_universe(uni["tickers"])
    ratings = {t: c for t, c in load_ratings(a.ratings).items() if t in uni["tickers"]}

    def metrics(t):
        try:
            fs = FactSet(src.facts(t)["cik"], src.facts(t))
            return compute_ratio(fs, "leverage_ltm").value, compute_ratio(fs, "coverage_ltm").value
        except Exception:                                    # missing data: the company is simply not in the sample
            return None
    print(f"{len(ratings)} rated tickers are in the universe; reading their filing numbers ...", file=sys.stderr)
    print(report(collect(ratings, metrics)))
    return 0


_NOISE = re.compile(r"Tax|Lessee|Lessor|Deferred|Contract|Comprehensive|Unrecognized|Allowance|Finite|Stock|Share|Accumulated|Equity|"
                    r"Pension|Derivative|Hedg|Fair|Securit|Related|Proceeds|Gross|Schedule|Reconcil|Segment|Revenue|Receivable|Impairment",
                    re.I)
_GAP_TAGS = {
    "TotalDebt": r"Debt|Borrow|Notes|Credit|Convertible|Lease",
    "InterestExpense": r"Interest",
    "EBITDA": r"OperatingIncome|IncomeLossFromContinuing|Depreciation|Amortization",
    "Capex": r"PaymentsToAcquire|CapitalExpend|PropertyPlant",
    "DebtMaturity": r"Maturit|Repayments",
}


def _xbrl_gaps(a) -> int:
    """Why do real companies fail with 'missing facts'? Counts by fact, plus the candidate us-gaap tags a few of them DO report."""
    from collections import defaultdict
    from .edgar import RealEdgar, SecHttp, check_user_agent
    from .ratios import FactSet, MissingDataError, core_ratios
    from .universe import load_or_build
    as_of = date.today().isoformat() if a.as_of == "today" else a.as_of
    http = SecHttp(check_user_agent(os.getenv("SEC_USER_AGENT")), Path(a.state_dir) / "edgar_cache")
    src = RealEdgar([], http, a.facts_zip, a.submissions_zip)
    uni = load_or_build(Path(a.state_dir) / "universe", as_of, False, ciks=src.all_ciks(), read_subs=src.read_subs,
                        max_age_days=150, include_financials=False)
    src.set_universe(uni["tickers"])
    tickers = [t.upper() for t in a.tickers] if a.tickers else src.tickers()[a.offset:a.offset + a.limit]
    cfg = ForecasterConfig(as_of=as_of)
    count, who, err = Counter(), defaultdict(list), Counter()
    for t in tickers:
        try:
            b = src.facts(t)
            core_ratios(FactSet(b["cik"], b), cfg.horizon_years, int(as_of[:4]))
            count["ok"] += 1
        except MissingDataError as exc:
            for fid in exc.missing:
                tag = fid.split(":")[2] if fid.count(":") >= 3 else fid
                count[tag] += 1
                who[tag].append(t)
        except Exception as exc:
            import traceback
            tb = traceback.extract_tb(exc.__traceback__)[-3:]
            err[f"{type(exc).__name__}: {str(exc)[:60]} @ " + " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(tb)) + f" [{t}]"] += 1
    print(f"\n{len(tickers)} companies (offset {a.offset}). Count of companies by missing fact (a company can miss several):")
    for k, v in count.most_common():
        print(f"  {v:4d}  {k}")
    for k, v in err.most_common():
        print(f"  {v:4d}  ERROR {k}")
    for tag, pat in _GAP_TAGS.items():
        for t in who.get(tag, [])[:a.samples]:
            facts = src._facts_zip.read(src._cik10(t)) if src._facts_zip else {}
            gaap = (facts.get("facts") or {}).get("us-gaap") or {}
            lines = []
            for name, node in gaap.items():
                if re.search(pat, name) and not _NOISE.search(name):
                    pts = [p for u in node.get("units", {}).values() for p in u]
                    if pts and max(p["end"] for p in pts) >= (date.fromisoformat(as_of).replace(year=date.fromisoformat(as_of).year - 2)).isoformat():
                        last = max(pts, key=lambda p: (p["end"], p.get("filed", "")))
                        lines.append(f"      {name}  last end {last['end']} form {last.get('form')} fp {last.get('fp')}")
            b = src.facts(t)
            fmt = lambda v: "--" if v is None else f"{v:.1f}"
            print(f"\n=== {t} ({b['name']}) missing {tag}. Quarters: " + " | ".join(
                f"{q['period']} ebitda {fmt(q['ebitda'])} int {fmt(q['interest'])} debt {fmt(q['debt'])}" for q in b["quarters"]))
            print("    Annual: " + " | ".join(f"FY{r['fy']} ebitda {fmt(r['ebitda'])} capex {fmt(r['capex'])}" for r in b["annual"][-3:])
                  + f" | maturities {b['maturities'] or 'none'}")
            for n in b["data_notes"][:4]:
                print("    note:", n)
            print(f"--- {t}: us-gaap tags matching /{pat}/ (noise removed) with data in the last two years ({len(lines)}):")
            print("\n".join(sorted(lines)[:a.max_tags]) or "      (none)")
    return 0


def main(argv=None) -> int:
    load_env()                                  # OPENAI_API_KEY etc. from .env (never overrides the real environment)
    ap = argparse.ArgumentParser(prog="forecaster")
    ap.add_argument("--state-dir", default="forecaster_state")
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("screen")
    s.add_argument("--as-of", default="today", help="analysis date (default: today; the offline sample uses 2026-11-01)")
    s.add_argument("--industry-cap", type=float, default=0.15,
                   help="live runs: max share of the list in one industry (Nasdaq.com taxonomy) (default 0.15; 0 = off)")
    s.add_argument("--industry-file", nargs="+",
                   help="CSV file(s) replacing the bundled Nasdaq.com industry/sector map (columns Symbol, Sector, Industry)")
    s.add_argument("--min-ebitda", type=float, default=None, help="optional size floor, LTM EBITDA in $ millions (default: none)")
    s.add_argument("--min-leverage", type=float, default=None, help="debt floor, Debt/EBITDA (clo default 0.5, regcap 0.2)")
    s.add_argument("--sector-cap", type=float, default=None, help="max share in one sector (default 0.4)")
    s.add_argument("--pool-factor", type=float, default=3.0,
                   help="live runs: read filings only for the best POOL_FACTOR x top-n names of the financial screen (0 = read all)")
    s.add_argument("--top-n", type=int, default=None, help="shortlist size (default 8 offline, 100 with --mode)")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--output")
    s.add_argument("--no-llm", action="store_true", help="ignore OPENAI_API_KEY and use the rule-based path")
    _source_args(s)
    s.set_defaults(fn=_screen)
    ir = sub.add_parser("import-ratings", help="turn a 17g-7 rating-history CSV (e.g. Egan-Jones) into a ticker,rating file")
    ir.add_argument("csv", help="the rating-history CSV")
    ir.add_argument("--output", default="ratings.csv")
    ir.add_argument("--as-of", default="today")
    ir.add_argument("--facts-zip")
    ir.add_argument("--submissions-zip")
    ir.add_argument("--include-financials", action="store_true")
    ir.set_defaults(fn=_import_ratings)
    cr = sub.add_parser("calibrate-ratings", help="compare the implied categories with a ticker,rating file and suggest better bands")
    cr.add_argument("ratings", help="ticker,rating file (from import-ratings)")
    cr.add_argument("--as-of", default="today")
    cr.add_argument("--facts-zip")
    cr.add_argument("--submissions-zip")
    cr.set_defaults(fn=_calibrate)
    xg = sub.add_parser("xbrl-gaps", help="diagnose 'missing facts': counts by fact and the tags those companies report instead")
    xg.add_argument("--as-of", default="today")
    xg.add_argument("--facts-zip")
    xg.add_argument("--submissions-zip")
    xg.add_argument("--limit", type=int, default=300)
    xg.add_argument("--offset", type=int, default=0)
    xg.add_argument("--samples", type=int, default=2, help="companies to list per missing fact")
    xg.add_argument("--max-tags", type=int, default=40)
    xg.add_argument("--tickers", nargs="*", help="diagnose just these tickers instead of a slice of the universe")
    xg.set_defaults(fn=_xbrl_gaps)
    ec = sub.add_parser("edgar-check", help="read real companies from SEC EDGAR and show how they were mapped")
    ec.add_argument("tickers", nargs="+")
    ec.add_argument("--as-of", default="today")
    ec.add_argument("--facts-zip", help="SEC bulk companyfacts.zip (no per-company request needed)")
    ec.add_argument("--submissions-zip", help="SEC bulk submissions.zip")
    ec.set_defaults(fn=_edgar_check)
    c = sub.add_parser("llm-check", help="one tiny LLM call; prints package versions and the full error if it fails")
    c.set_defaults(fn=_llm_check)
    g = sub.add_parser("graph", help="print the company workflow as a Mermaid diagram (needs langgraph)")
    g.set_defaults(fn=_graph)
    f = sub.add_parser("feedback")
    fs = f.add_subparsers(dest="cmd", required=True)
    add = fs.add_parser("add")
    add.add_argument("criterion")
    add.add_argument("--keywords", help="comma-separated words that signal it in filings (blank = advisory lesson only)")
    add.add_argument("--lesson", help="the experience in your own words (given to the LLM on later runs)")
    add.add_argument("--sector", help="only apply to this sector (blank = all)")
    add.add_argument("--action", choices=["review", "exclude"], default="review")
    add.add_argument("--reason")
    fs.add_parser("list")
    ed = fs.add_parser("edit")
    ed.add_argument("id")
    ed.add_argument("--criterion")
    ed.add_argument("--action", choices=["review", "exclude"])
    ed.add_argument("--active", choices=["on", "off"])
    de = fs.add_parser("delete")
    de.add_argument("id")
    f.set_defaults(fn=_feedback)
    r = sub.add_parser("review", help="human review of the last run; saves experience into memory")
    r.add_argument("--list", action="store_true", help="show the review queue and exit")
    r.add_argument("--ticker")
    r.add_argument("--decision", choices=list(ReviewSession.DECISIONS))
    r.add_argument("--analyst", default="analyst")
    r.add_argument("--note", default="")
    r.add_argument("--lesson", default="", help="what the model missed, in your words (saved to memory)")
    r.add_argument("--keywords", default="")
    r.add_argument("--action", choices=["review", "exclude"], default="review")
    r.add_argument("--all-sectors", action="store_true", help="apply the lesson to every sector, not just this one")
    r.set_defaults(fn=_review)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
