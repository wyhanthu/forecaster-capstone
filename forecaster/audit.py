"""Structured audit trail (teacher's Virtual Lab 6.2): every tool call, veto, pruned branch and release decision."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class AuditLog:
    def __init__(self, path: Optional[Path] = None, append: bool = False):
        self.path = Path(path) if path else None
        self.events: List[dict] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not append or not self.path.exists():
                self.path.write_text("")

    def log(self, event: str, **fields) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
        with self._lock:
            self.events.append(rec)
            if self.path:
                with self.path.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")

    def for_ticker(self, ticker: str) -> List[dict]:
        return [e for e in self.events if e.get("ticker") == ticker]

    def count(self, event: str, **match) -> int:
        return sum(1 for e in self.events if e["event"] == event and all(e.get(k) == v for k, v in match.items()))
