"""Download SEC's two public bulk files with the contact in SEC_USER_AGENT (.env), so no curl command is needed."""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Callable, Dict

SEC_FILES: Dict[str, str] = {
    "companyfacts.zip": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
    "submissions.zip": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
}
CHUNK = 1 << 20


def download_file(url: str, dest: Path, user_agent: str, say: Callable[[str], None] = print,
                  opener=None) -> int:
    """Stream `url` to `dest` (written as dest.part, then renamed, so a stopped download never looks complete)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    done, next_report = 0, 100 << 20
    opener = opener or urllib.request.urlopen
    with opener(req, timeout=60) as resp, open(part, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            block = resp.read(CHUNK)
            if not block:
                break
            out.write(block)
            done += len(block)
            if done >= next_report:
                say(f"  {dest.name}: {done / 1e6:,.0f} MB" + (f" of {total / 1e6:,.0f} MB" if total else ""))
                next_report += 100 << 20
    part.replace(dest)
    return done


def download_all(directory: Path, user_agent: str, force: bool = False, say: Callable[[str], None] = print,
                 opener=None) -> Dict[str, Path]:
    got: Dict[str, Path] = {}
    for name, url in SEC_FILES.items():
        dest = directory / name
        if dest.exists() and not force:
            say(f"{dest} already exists (use --force to download again)")
        else:
            say(f"Downloading {name} from SEC ...")
            size = download_file(url, dest, user_agent, say, opener)
            say(f"  saved {dest} ({size / 1e6:,.0f} MB)")
        got[name] = dest
    return got
