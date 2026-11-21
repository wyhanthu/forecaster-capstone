"""Human review and experience memory (Checkpoint 2.1 / 6.1).

The human reviews the final result. When the model missed something, the analyst records the experience once;
it is stored in the analyst-feedback memory (inspectable, editable, deletable) and the next run carries it:
  * its text is handed to the Retriever and the Analyst (LLM) as desk experience,
  * its keywords are matched against current filings,
  * an LLM reading must quote the filing to make it fire, and every override stays in an append-only log.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .audit import AuditLog
from .memory import FeedbackMemory


def load_overrides(state_dir) -> List[dict]:
    p = Path(state_dir) / "overrides.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "override":
            out.append(rec)
    return out


class ReviewSession:
    DECISIONS = ("confirm", "include", "exclude")

    def __init__(self, state_dir):
        self.dir = Path(state_dir)
        self.last_run_path = self.dir / "last_run.json"
        self.feedback = FeedbackMemory(self.dir / "feedback.json")
        self.log = AuditLog(self.dir / "overrides.jsonl", append=True)     # permanent, never truncated

    # ------------------------------------------------------------------ the last run
    def last_run(self) -> dict:
        if not self.last_run_path.exists():
            raise FileNotFoundError("no previous run found: run `screen` first")
        return json.loads(self.last_run_path.read_text())

    def queue(self) -> List[dict]:
        run = self.last_run()
        cards = run["scorecards"]
        return [cards[t] for t in run["review_queue"] if t in cards]

    # ------------------------------------------------------------------ record a decision (+ optional lesson)
    def apply(self, ticker: str, decision: str, analyst: str = "analyst", note: str = "", lesson: str = "",
              keywords: Sequence[str] = (), action: str = "review", lesson_scope: str = "sector") -> Dict[str, Optional[str]]:
        if decision not in self.DECISIONS:
            raise ValueError(f"decision must be one of {self.DECISIONS}")
        card = self.last_run()["scorecards"].get(ticker)
        if card is None:
            raise KeyError(f"{ticker} was not in the last run")
        model_status = card["status"]
        differs = decision != "confirm" and not (
            (decision == "include" and model_status == "include") or (decision == "exclude" and model_status != "include"))
        self.log.log("override" if differs else "review_confirmed", ticker=ticker, decision=decision, analyst=analyst,
                     note=note, model_status=model_status, model_interpretation=card.get("interpretation", ""),
                     review_reasons=card.get("review_reasons", []))
        crit_id = None
        if lesson.strip():
            c = self.feedback.add(lesson.strip()[:80], list(keywords), action, note,
                                  lesson=lesson.strip(), source=f"{ticker} {decision} {date.today().isoformat()}",
                                  sector=card.get("sector", "") if lesson_scope == "sector" else "")
            crit_id = c.id
            self.log.log("lesson_saved", ticker=ticker, criterion=c.id, analyst=analyst)
        return {"override": "yes" if differs else "no", "criterion_id": crit_id}

    # ------------------------------------------------------------------ interactive loop
    def interactive(self, analyst: str = "analyst", input_fn: Callable[[str], str] = input,
                    print_fn: Callable[[str], None] = print) -> int:
        q = self.queue()
        if not q:
            print_fn("Nothing needs review.")
            return 0
        for card in q:
            print_fn(f"\n== {card['ticker']} ({card['sector']}) model: {card['status']}, "
                     f"{card.get('interpretation') or 'n/a'}, scope {card.get('scope') or 'n/a'}")
            for r in card.get("review_reasons") or ["status requires review"]:
                print_fn(f"   - {r}")
            d = input_fn("decision [c]onfirm / [i]nclude / [e]xclude / [s]kip: ").strip().lower()[:1]
            if d in ("", "s"):
                continue
            decision = {"c": "confirm", "i": "include", "e": "exclude"}.get(d)
            if decision is None:
                print_fn("   unknown choice, skipped")
                continue
            note = input_fn("note (why): ").strip()
            lesson = input_fn("what did the model miss? (experience for next runs, blank = none): ").strip()
            kws, action = [], "review"
            if lesson:
                kws = [k for k in input_fn("keywords that signal it in filings (comma-separated, blank = none): ").split(",") if k.strip()]
                action = "exclude" if input_fn("if it fires: [r]eview or [e]xclude? ").strip().lower().startswith("e") else "review"
            res = self.apply(card["ticker"], decision, analyst, note, lesson, kws, action)
            print_fn(f"   saved{' (lesson ' + res['criterion_id'] + ')' if res['criterion_id'] else ''}")
        return 0
