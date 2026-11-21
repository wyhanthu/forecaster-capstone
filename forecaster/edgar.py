"""Live SEC EDGAR client: the same four reads as OfflineEdgar, against the real archive.

    tickers()            the universe you give it (a list or a file); ticker -> CIK uses SEC's company_tickers.json
    facts(ticker)        XBRL `companyfacts` + `submissions` -> the fact bundle (xbrl.py)
    chunks_for_cik(cik)  latest 10-Q / 10-K text split into sections and notes (filing_text.py), plus recent 8-Ks
    events(ticker, since) 8-Ks filed after the latest 10-Q / 10-K

Read-only by construction: it only issues GET requests to sec.gov. SEC's fair-access rules are built in:
  * every request carries a User-Agent with a contact e-mail (SEC_USER_AGENT in .env),
  * at most ~8 requests per second across all threads (the limit is 10),
  * every response is cached on disk, so a re-run costs no requests (filings never change; facts expire after a few days).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .filing_text import extract_sections, html_to_lines
from .models import (
    SECTION_8K,
    SECTION_BUSINESS,
    SECTION_RISK,
    Chunk,
    Event,
)
from .retrieval import ShardedIndex
from .tools import ToolError
from .xbrl import NoXbrlData, build_bundle, filings_of, latest_periodic

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn}/{doc}"

FACTS_TTL_DAYS = 3
RELEVANT_8K_ITEMS = {"1.01", "1.02", "1.03", "2.04", "2.05", "2.06", "3.01", "4.02", "8.01"}


class EdgarError(ToolError):
    """A request to SEC failed (worth one retry by the tool registry)."""


class EdgarNotFound(EdgarError):
    """SEC has nothing for this ticker / filing."""


# --------------------------------------------------------------------------- HTTP
Fetcher = Callable[[str, Dict[str, str], float], Tuple[int, Dict[str, str], bytes]]


def _urllib_fetch(url: str, headers: Dict[str, str], timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b""


def check_user_agent(ua: Optional[str]) -> str:
    if not ua or "@" not in ua or "your.email" in ua or ua.lower().endswith("example.com"):
        raise ValueError("SEC requires a User-Agent that identifies you. Put this line in .env:\n"
                         "SEC_USER_AGENT=Your Name your.email@example.com")
    return ua.strip()


class SecHttp:
    """GET with a User-Agent, a global rate limit, retries with backoff, and a gzip'd disk cache."""

    def __init__(self, user_agent: str, cache_dir: Optional[Path] = None, min_interval: float = 0.12, timeout: float = 40.0,
                 retries: int = 4, fetcher: Optional[Fetcher] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.user_agent = check_user_agent(user_agent)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.min_interval, self.timeout, self.retries = min_interval, timeout, retries
        self._fetch, self._sleep, self._clock = fetcher or _urllib_fetch, sleep, clock
        self._lock = threading.Lock()
        self._next = 0.0
        self.requests = 0                        # network requests made (cache hits are free)
        self.cache_hits = 0

    # ---- rate limit: one shared schedule, so the limit holds across all worker threads
    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.min_interval
        if wait:
            self._sleep(wait)

    def _path(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.gz"

    def get(self, url: str, ttl_days: Optional[float] = None) -> bytes:
        """Body of `url`. ttl_days None = cache forever (filings never change)."""
        path = self._path(url)
        if path and path.exists() and (ttl_days is None or time.time() - path.stat().st_mtime < ttl_days * 86400):
            try:
                body = gzip.decompress(path.read_bytes())
                self.cache_hits += 1
                return body
            except (OSError, EOFError):                       # a half-written or corrupt cache file is just a miss
                path.unlink(missing_ok=True)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip", "Accept": "*/*"}
        last = "no response"
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                with self._lock:
                    self.requests += 1
                status, hdrs, body = self._fetch(url, headers, self.timeout)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                self._sleep(min(2 ** attempt, 20))
                continue
            if status == 200:
                if hdrs.get("content-encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
                    tmp.write_bytes(gzip.compress(body, 3))
                    os.replace(tmp, path)
                return body
            if status == 404:
                raise EdgarNotFound(f"404 {url}")
            if status == 403:
                raise EdgarError(f"403 from SEC for {url}. Check SEC_USER_AGENT (name + e-mail) and the request rate.")
            if status in (429, 500, 502, 503, 504):
                last = f"HTTP {status}"
                retry_after = hdrs.get("retry-after", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else min(2 ** attempt * 2, 30))
                continue
            raise EdgarError(f"HTTP {status} for {url}")
        raise EdgarError(f"gave up on {url} after {self.retries + 1} tries ({last})")

    def get_json(self, url: str, ttl_days: Optional[float] = None) -> dict:
        return json.loads(self.get(url, ttl_days))


class ZipStore:
    """Optional bulk file from SEC (companyfacts.zip / submissions.zip): lookups cost no requests at all."""

    def __init__(self, path: str):
        self._zip = zipfile.ZipFile(path)
        self._names = {n.lower(): n for n in self._zip.namelist()}
        self._lock = threading.Lock()

    def ciks(self) -> List[str]:
        from .universe import ciks_in
        return ciks_in(self._names.values())

    def read(self, cik10: str) -> Optional[dict]:
        name = self._names.get(f"cik{cik10}.json".lower())
        if not name:
            return None
        with self._lock:                     # only the file read is serialized; parsing the JSON runs outside the lock
            raw = self._zip.read(name)
        return json.loads(raw)


# --------------------------------------------------------------------------- events
def event_type(item: str, text: str) -> str:
    low = text.lower()
    if item == "4.02":
        return "restatement"
    if item == "1.03":
        return "bankruptcy"
    if item == "2.04":
        return "covenant_waiver" if "waiver" in low else "default"
    if item in ("1.01", "8.01") and "waiver" in low:
        return "covenant_waiver"
    if item in ("1.01", "2.01") and re.search(r"agreement and plan of merger|merger agreement|agreement of merger", low):
        return "merger"
    if item in ("8.01", "7.01") and re.search(r"downgrad|lowered[^.]{0,80}rating|rating[^.]{0,80}(?:lowered|downgrad)", low):
        return "downgrade"
    return {"2.06": "impairment", "3.01": "delisting", "1.02": "termination", "2.05": "exit_costs"}.get(item, "other")


def item_body(html: str, item: str, limit: int = 1500) -> str:
    lines = html_to_lines(html)
    for i, line in enumerate(lines):
        if re.match(rf"^\s*item\s+{re.escape(item)}\b", line, re.I):
            out, used = [], 0
            for x in lines[i + 1:]:
                if re.match(r"^\s*item\s+\d\.\d{2}\b", x, re.I) or re.match(r"^\s*signatures?\s*$", x, re.I):
                    break
                out.append(x)
                used += len(x)
                if used >= limit:
                    break
            return "\n".join(out)[:limit].strip()
    return ""


# --------------------------------------------------------------------------- the source
@dataclass
class _Company:
    ticker: str
    cik: str
    bundle: dict
    subs: dict
    last: dict
    last_k: Optional[dict]
    eight_ks: List[dict]
    chunks: Optional[List[Chunk]] = None
    events: Optional[List[Event]] = None
    warnings: List[str] = field(default_factory=list)
    parse: dict = field(default_factory=dict)


def read_tickers_file(path: str) -> List[str]:
    out: List[str] = []
    for line in Path(path).read_text().splitlines():
        t = line.split("#")[0].split(",")[0].strip().upper()
        if t and t not in out and t not in ("TICKER", "SYMBOL"):
            out.append(t)
    return out


class RealEdgar:
    def __init__(self, tickers: List[str], http: SecHttp, facts_zip: Optional[str] = None,
                 submissions_zip: Optional[str] = None, max_events: int = 6, cik_map: Optional[Dict[str, str]] = None):
        self._tickers = [t.upper().replace(".", "-") for t in tickers]
        self.http = http
        self.max_events = max_events
        self._facts_zip = ZipStore(facts_zip) if facts_zip else None
        self._subs_zip = ZipStore(submissions_zip) if submissions_zip else None
        self._lock = threading.Lock()
        self._tick_locks: Dict[str, threading.RLock] = {}
        self._cik_map: Optional[Dict[str, str]] = dict(cik_map) if cik_map else None
        self._co: Dict[str, _Company] = {}
        self._by_cik: Dict[str, str] = {}

    # ---- the OfflineEdgar interface -------------------------------------------------------------
    def tickers(self) -> List[str]:
        return list(self._tickers)

    def all_chunks(self) -> List[Chunk]:
        return []                                   # text is loaded per company, on demand (see make_index)

    def make_index(self) -> ShardedIndex:
        return ShardedIndex(self.chunks_for_cik)

    def facts(self, ticker: str) -> dict:
        return self._company(ticker).bundle

    def events(self, ticker: str, since: str) -> List[Event]:
        co = self._company(ticker)
        return [e for e in self._events(co) if e.filed > since]

    def chunks_for_cik(self, cik: str) -> List[Chunk]:
        ticker = self._by_cik.get(cik)
        if ticker is None:
            raise EdgarNotFound(f"unknown CIK {cik}: facts must be read before the filing text")
        co = self._company(ticker)
        with self._tick_lock(ticker):
            if co.chunks is None:
                try:
                    co.chunks = self._build_chunks(co)
                except ToolError:
                    raise
                except Exception as exc:               # a parsing bug on one filing must not stop the run
                    raise EdgarError(f"{ticker}: could not read the filing text ({type(exc).__name__}: {exc})") from exc
        return co.chunks

    def set_universe(self, ticker_to_cik: Dict[str, str]) -> None:
        self._tickers = sorted(ticker_to_cik)
        self._cik_map = dict(ticker_to_cik)

    def read_subs(self, cik10: str) -> Optional[dict]:
        """The submissions record of a company: from the bulk zip when there is one, else from SEC (cached)."""
        if self._subs_zip is not None:
            return self._subs_zip.read(cik10)
        try:
            return self.http.get_json(SUBS_URL.format(cik=cik10), ttl_days=1)
        except EdgarNotFound:
            return None

    def all_ciks(self) -> List[str]:
        """Every company with XBRL facts: the facts zip when there is one, else SEC's exchange-listed company file."""
        if self._facts_zip is not None:
            return self._facts_zip.ciks()
        data = self.http.get_json("https://www.sec.gov/files/company_tickers_exchange.json", ttl_days=7)
        fields = data["fields"]
        ic, ie = fields.index("cik"), fields.index("exchange")
        return sorted({f"{int(r[ic]):010d}" for r in data["data"] if str(r[ie]).lower() in ("nyse", "nasdaq")})

    # ---- loading --------------------------------------------------------------------------------
    def _tick_lock(self, ticker: str) -> "threading.RLock":
        with self._lock:                                 # re-entrant: loading a company's text also reads its 8-Ks
            return self._tick_locks.setdefault(ticker, threading.RLock())

    def _cik10(self, ticker: str) -> str:
        with self._lock:
            known = self._cik_map
        if known is None:
            data = self.http.get_json(TICKERS_URL, ttl_days=7)
            known = {v["ticker"].upper(): f"{int(v['cik_str']):010d}" for v in data.values()}
            with self._lock:
                self._cik_map = known
        if ticker not in known:
            raise EdgarNotFound(f"{ticker}: not in SEC's company_tickers.json")
        return known[ticker]

    def _json(self, zipstore: Optional[ZipStore], url: str, cik10: str, ttl: float) -> dict:
        if zipstore is not None:
            got = zipstore.read(cik10)
            if got is not None:
                return got
        return self.http.get_json(url, ttl_days=ttl)

    def _company(self, ticker: str) -> _Company:
        ticker = ticker.upper().replace(".", "-")
        with self._tick_lock(ticker):
            co = self._co.get(ticker)
            if co is not None:
                return co
            try:
                cik10 = self._cik10(ticker)
                subs = self._json(self._subs_zip, SUBS_URL.format(cik=cik10), cik10, 1)
                facts = self._json(self._facts_zip, FACTS_URL.format(cik=cik10), cik10, FACTS_TTL_DAYS)
                bundle = build_bundle(facts, subs, ticker)
            except NoXbrlData as exc:
                raise EdgarNotFound(str(exc)) from exc
            except ToolError:
                raise
            except Exception as exc:                    # keep one odd company from stopping a 2,000-company run
                raise EdgarError(f"{ticker}: could not build facts ({type(exc).__name__}: {exc})") from exc
            del facts
            last = latest_periodic(subs)
            ks = filings_of(subs, ("10-K", "10-KT"))
            co = _Company(ticker, cik10, bundle, subs, last, ks[0] if ks else None, filings_of(subs, ("8-K",)))
            with self._lock:
                self._co[ticker] = co
                self._by_cik[cik10] = ticker
            return co

    def _doc(self, co: _Company, filing: dict) -> str:
        url = DOC_URL.format(cik_int=int(co.cik), accn=filing["accn"].replace("-", ""), doc=filing["doc"])
        return self.http.get(url).decode("utf-8", errors="replace")

    # ---- events ---------------------------------------------------------------------------------
    def _events(self, co: _Company) -> List[Event]:
        with self._tick_lock(co.ticker):
            if co.events is not None:
                return co.events
            out: List[Event] = []
            since = co.last["filed"]
            fresh = [f for f in co.eight_ks if f["filed"] > since]
            todo = [f for f in fresh if RELEVANT_8K_ITEMS & {x.strip() for x in f.get("items", "").split(",")}]
            for f in todo[: self.max_events]:
                try:
                    html = self._doc(co, f)
                except ToolError as exc:
                    co.warnings.append(f"8-K {f['filed']} not read: {exc}")
                    continue
                for item in sorted(RELEVANT_8K_ITEMS & {x.strip() for x in f["items"].split(",")}):
                    text = item_body(html, item) or f"Form 8-K, Item {item}, filed {f['filed']}."
                    out.append(Event(co.cik, co.ticker, f["filed"], item, event_type(item, text), text))
            if len(todo) > self.max_events:
                co.warnings.append(f"{len(todo) - self.max_events} older relevant 8-Ks after the last 10-Q were not read")
            co.events = out
            return out

    # ---- filing text ----------------------------------------------------------------------------
    def _build_chunks(self, co: _Company) -> List[Chunk]:
        chunks: List[Chunk] = []

        def add(form: str, filed: str, section: str, text: str, tag: str = "") -> None:
            if text and text.strip():
                cid = f"{co.ticker.lower()}-{form.lower().replace('/', '')}-{filed}-{section}{tag}"
                chunks.append(Chunk(cid, co.cik, co.ticker, form, filed, section, text.strip()))

        last = co.last
        secs = extract_sections(last["form"], self._doc(co, last), want_text=True, want_notes=True)
        co.parse[last["form"]] = {k: secs[k] for k in ("_lines", "_items", "_notes") if k in secs}
        for section, body in secs.items():
            if section.startswith("_"):
                continue
            if section == SECTION_RISK:
                for n, part in enumerate(body, 1):
                    add(last["form"], last["filed"], section, part, f"-p{n}")
            else:
                add(last["form"], last["filed"], section, body)

        is_q = last["form"].upper().startswith("10-Q")
        if is_q and co.last_k:                      # business and risk factors come from the latest annual report
            try:
                ksecs = extract_sections("10-K", self._doc(co, co.last_k), want_text=True, want_notes=False)
                co.parse["10-K"] = {k: ksecs[k] for k in ("_lines", "_items") if k in ksecs}
                add("10-K", co.last_k["filed"], SECTION_BUSINESS, ksecs.get(SECTION_BUSINESS, ""))
                for n, part in enumerate(ksecs.get(SECTION_RISK, []), 1):
                    add("10-K", co.last_k["filed"], SECTION_RISK, part, f"-p{n}")
            except ToolError as exc:
                co.warnings.append(f"10-K not read ({exc}); no business / risk-factor text")

        for e in self._events(co):
            chunks.append(Chunk(e.chunk_id, e.cik, e.ticker, "8-K", e.filed, SECTION_8K, e.text))
        if not any(c.section != SECTION_8K for c in chunks):
            co.warnings.append("no section could be extracted from the filing (layout not recognized)")
        return chunks

    # ---- diagnostics (python -m forecaster.cli edgar-check TICKER) -----------------------------------
    def describe(self, ticker: str) -> dict:
        co = self._company(ticker)
        chunks = self.chunks_for_cik(co.cik)
        events = self._events(co)
        return dict(bundle=co.bundle, last=co.last, chunks=[(c.section, c.form, c.filed, len(c.text)) for c in chunks],
                    events=[(e.filed, e.item, e.type) for e in events], warnings=co.warnings, parse=co.parse,
                    requests=self.http.requests, cache_hits=self.http.cache_hits)
