"""Selective Tree-of-Thought (Checkpoints 4.1 / 5.1).

Only runs when the evidence is ambiguous (a cited one-time item, a multi-period trend, covenant /
going-concern / restatement language). Otherwise one linear "stable" branch is drafted and validated.

depth 1: one candidate interpretation per branch (stable / setback / deterioration)
depth 2: only if nothing is both valid and complete - repair branches by dropping unsupported claims
Beam width and branching are capped; pruned branches are written to the audit log.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .agents import Analyst, Evidence, ValidationContext, Validator
from .audit import AuditLog
from .config import ForecasterConfig
from .models import Branch
from .ratios import FactSet, Ratio

PRIORITY = {"deterioration": 0, "setback": 1, "stable": 2}     # conservative tie-break


@dataclass
class TotResult:
    outcome: str                      # final | uncertain | insufficient
    best: Optional[Branch] = None
    branches: List[Branch] = field(default_factory=list)
    used_tot: bool = False
    ambiguous_conflict: bool = False


BOILERPLATE_TAGS = ("covenant_pressure", "litigation", "off_balance_sheet", "adverse_development", "subsequent_event")


def is_ambiguous(ev: Evidence, multi_period: bool, shock: bool = False, boilerplate_counts: bool = True) -> bool:
    """Run the tree only when a plain reading is not enough: a one-off, a trend, risk language, or a sudden drop.

    Live filings are full of boilerplate that trips the keyword scan ("an event of default may accelerate ...", "adverse results in
    lawsuits may ..."); with `boilerplate_counts=False` those hits alone do not make a company ambiguous."""
    tags = ("temporary_event", "going_concern", "restatement", "industry_conditions") + (BOILERPLATE_TAGS if boilerplate_counts else ())
    return bool(multi_period or shock or any(ev.findings.get(t) for t in tags))


class TreeOfThought:
    def __init__(self, analyst: Analyst, validator: Validator, cfg: ForecasterConfig, audit: AuditLog):
        self.analyst, self.validator, self.cfg, self.audit = analyst, validator, cfg, audit

    def _mk(self, ticker, parent, depth, interp, strategy, claims) -> Branch:
        return Branch(f"{ticker}-d{depth}-{interp}-{strategy}", parent, depth, interp, strategy, claims)

    def run(self, ticker: str, ev: Evidence, fs: FactSet, ratios: Dict[str, Ratio], ctx: ValidationContext,
            beam: Optional[int] = None, max_depth: Optional[int] = None, info: Optional[dict] = None,
            shock: bool = False) -> TotResult:
        beam = beam or self.cfg.beam_width
        max_depth = max_depth or self.cfg.max_depth
        ambiguous = self.cfg.tot_enabled and is_ambiguous(ev, ctx.trend_multi_period, shock, self.cfg.rule_adverse_blocks_stable)
        info = dict(info or {}, ticker=ticker, ambiguous=ambiguous)
        cands = ["stable", "setback", "deterioration"][: self.cfg.branching] if ambiguous else ["stable"]

        branches: List[Branch] = []
        seen = set()
        for interp in cands:
            b = self._mk(ticker, None, 1, interp, "primary", self.analyst.draft(interp, ev, fs, ratios, info))
            self.validator.validate(b, ctx)
            branches.append(b)
            seen.add(b.signature())

        depth = 1
        while depth < max_depth and ambiguous and not any(b.valid and b.requirements_met for b in branches):
            depth += 1
            frontier = sorted([b for b in branches if b.depth == depth - 1],
                              key=lambda b: (-b.valid, -b.scores.mean, PRIORITY[b.interpretation]))[:beam]
            for parent in frontier:
                bad = {h.split(":")[0] for h in parent.hard_fails if ":" in h and not h.startswith("interp:")}
                if not bad:
                    continue
                kept = [c for c in parent.claims if c.claim_id not in bad]
                child = self._mk(ticker, parent.branch_id, depth, parent.interpretation, "repair", kept)
                if child.signature() in seen:
                    continue
                seen.add(child.signature())
                self.validator.validate(child, ctx)
                branches.append(child)

        valid = sorted([b for b in branches if b.valid], key=lambda b: (-round(b.scores.mean, 2), PRIORITY[b.interpretation]))
        best = valid[0] if valid else None
        unconfirmed = False
        if best is None and ambiguous:        # the model read the filing and found no support for ANY reading: do not drop the company
            soft = [b for b in branches if b.hard_fails and all(h == "interp:llm_rejects_hypothesis" for h in b.hard_fails)
                    and b.scores.mean >= self.cfg.valid_threshold]
            if soft:
                best = min(soft, key=lambda b: PRIORITY[b.interpretation])
                best.missing_items = list(best.missing_items) + ["interpretation:unconfirmed_by_the_text"]
                unconfirmed = True
        for b in branches:
            if b is best:
                continue
            b.pruned_reason = ("; ".join(b.hard_fails) or f"score {b.scores.mean:.2f} below threshold") if not b.valid \
                else "lower ranked than the selected branch"
            self.audit.log("tot_prune", ticker=ticker, branch=b.branch_id, reason=b.pruned_reason)
        conflict = bool(best and any(
            o.interpretation != best.interpretation and abs(o.scores.mean - best.scores.mean) < 0.05
            and "deterioration" in (o.interpretation, best.interpretation)
            and {o.interpretation, best.interpretation} != {"deterioration"} for o in valid[1:]))
        if best is None:
            outcome = "insufficient"
        else:
            outcome = "final" if best.requirements_met and not unconfirmed else "uncertain"
        self.audit.log("tot_result", ticker=ticker, outcome=outcome, used_tot=ambiguous,
                       best=best.branch_id if best else None, n_branches=len(branches))
        return TotResult(outcome, best, branches, ambiguous, conflict)
