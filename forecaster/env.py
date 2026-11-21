"""Load OPENAI_API_KEY and friends from a .env file, so the key never has to be typed into the shell or the code.

Standard library only. Variables already set in the environment win over the file. The file is looked up in the current
directory, the project root and the folder above it (so a .env kept next to the project folder is found too)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:                                   # trailing comment on an unquoted value
            value = value.split(" #", 1)[0].strip()
        if key.strip():
            out[key.strip()] = value
    return out


def load_env(paths: Optional[Iterable[Path]] = None) -> Optional[Path]:
    """Sets variables from the first .env found. Returns that path, or None."""
    root = Path(__file__).resolve().parent.parent
    candidates = list(paths) if paths is not None else [Path.cwd() / ".env", root / ".env", root.parent / ".env"]
    for p in candidates:
        if p.is_file():
            for k, v in parse_env(p.read_text(encoding="utf-8")).items():
                os.environ.setdefault(k, v)
            return p
    return None
