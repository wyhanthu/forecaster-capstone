"""Markdown report. Rule: no citation, no number. Fact and calc claims without a valid citation are not rendered."""
from __future__ import annotations

import math
from collections import Counter
from datetime import date
from typing import Dict, List, Optional

from .models import SECTION_LABELS, Scorecard, Status
from .portfolio import PortfolioResult
from .retrieval import HybridIndex


def _src(cid: str, index: HybridIndex) -> str:
    if cid.startswith("xbrl:"):
        return f"XBRL fact `{cid}`"
    ch = index.get(cid)
    return f"{ch.form} filed {ch.filed}, {SECTION_LABELS.get(ch.section, ch.section)} (`{cid}`)" if ch else f"`{cid}`"


def company_memo(sc: Scorecard, claims, index: HybridIndex) -> str:
    refs: List[str] = []

    def ref(cid: str) -> int:
        if cid not in refs:
            refs.append(cid)
        return refs.index(cid) + 1

    lines = [f"### {sc.ticker} - {sc.name} ({sc.sector})",
             f"Score {sc.composite:.2f} | confidence {sc.confidence} | interpretation: {sc.interpretation or 'n/a'}"
             + (f" | scope: {sc.scope}" if sc.scope else "")
             + (f" | maturity wall {sc.wall_year}" if sc.wall_year else ""), ""]
    for label, kinds in (("Filing facts (verbatim)", ["fact"]), ("Computed ratios", ["calc"])):
        rows = []
        for c in claims:
            if c.kind in kinds:
                if not c.citations:
                    continue
                marks = "".join(f"[{ref(i)}]" for i in c.citations)
                rows.append(f"- {c.text} {marks}")
        if rows:
            lines += [f"**{label}**", *rows, ""]
    for c in claims:
        if c.kind == "judgment":
            lines += [f"*{c.text}* (model judgment, not a filing fact)", ""]
    if sc.lessons:
        lines += ["**Desk lessons applied:** " + ", ".join(sc.lessons), ""]
    if sc.review_reasons:
        lines += ["**Needs analyst review:** " + "; ".join(sc.review_reasons), ""]
    lines += [f"[{i + 1}] {_src(cid, index)}" for i, cid in enumerate(refs)]
    return "\n".join(lines) + "\n"


def concentration(p: PortfolioResult, cfg=None) -> List[str]:
    """The portfolio guardrails, checked on the finished list: share per sector and per industry against its cap."""
    n = len(p.shortlist)
    if not n:
        return []
    top_n = cfg.top_n if cfg else n
    caps = {"sector": max(1, math.floor(cfg.sector_cap_frac * top_n)) if cfg else None,
            "industry": max(1, math.floor(cfg.industry_cap_frac * top_n)) if cfg and cfg.industry_cap_frac > 0 else None}
    out = ["", f"**Concentration check** ({n} names; caps are on a list of {top_n})", "",
           "| Group | Name | Names | Share | Cap | OK |", "|---|---|---|---|---|---|"]
    for kind, key in (("sector", lambda c: c.group_sector or c.sector), ("industry", lambda c: c.industry or c.sector)):
        cnt = Counter(key(c) for c in p.shortlist)
        desc = {}
        for c in p.shortlist:
            desc.setdefault(key(c), c.industry_desc if kind == "industry" and key(c).startswith("SIC") else "")
        for name, k in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:15 if kind == "industry" else 99]:
            cap = caps[kind]
            ok = "n/a" if cap is None else ("yes" if k <= cap else "NO")
            label = f"{name} ({desc[name]})" if desc.get(name) else name
            out.append(f"| {kind} | {label} | {k} | {100 * k / n:.0f}% | {cap if cap is not None else '-'} | {ok} |")
    return out


def render(p: PortfolioResult, index: HybridIndex, as_of: str, note: Optional[str] = None, compact: bool = False, cfg=None) -> str:
    """`note` replaces the synthetic-data banner (live runs); `compact` summarizes the thousands of names that never got
    past the pre-screen or lacked data instead of listing each one."""
    counts = ", ".join(f"{k}: {v}" for k, v in sorted(p.counts.items()))
    title_date = date.today().isoformat() if compact else as_of   # live reports carry the generation date
    out = [f"# Forecaster screening report ({title_date})", "",
           note or "Synthetic demo universe: all companies and numbers are fictional.",
           f"Funnel outcome: {counts}", ""]
    if p.breaker:
        out += [f"> **CIRCUIT BREAKER TRIPPED:** {p.breaker}. Nothing below is auto-released; all names go to review.", ""]
    out += ["## Shortlist", "", "| # | Ticker | Sector | Score | Confidence | Interp. | Auto-release |", "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(p.shortlist, 1):
        auto = "yes" if (c.auto_release and not p.breaker) else "review"
        out.append(f"| {i} | {c.ticker} | {c.sector} | {c.composite:.2f} | {c.confidence} | {c.interpretation} | {auto} |")
    out += concentration(p, cfg)
    if p.skipped:
        out += ["", "Skipped by portfolio guardrails: " + "; ".join(f"{t} ({r})" for t, r in p.skipped)]
    out += ["", "## Company memos", ""]
    for c in p.shortlist:
        r = p.results[c.ticker]
        out.append(company_memo(c, r.claims, index))
    out += ["## Not selected", ""]
    bulk = Counter()
    for t, r in sorted(p.results.items()):
        c = r.scorecard
        if c in p.shortlist:
            continue
        if compact and c.status in (Status.PRESCREEN_FAIL.value, Status.INSUFFICIENT.value, Status.CANDIDATE.value):
            bulk[(c.status, c.skip_reason or "other")] += 1
            continue
        out.append(f"- **{t}** [{c.status}]: {c.rationale[0] if c.rationale else ''}")
    if bulk:
        out += ["", "Removed before any filing text was read (counts):"]
        out += [f"- {n:,} x {status} / {why}" for (status, why), n in sorted(bulk.items(), key=lambda kv: -kv[1])]
    if p.review_queue:
        out += ["", "## Human review queue", ""]
        out += [f"- **{c.ticker}** ({c.status}): " + "; ".join(c.review_reasons or ['status requires review']) for c in p.review_queue]
    return "\n".join(out) + "\n"
