"""Raw SEC filing (inline-XBRL HTML) -> plain text -> the sections the screen reads.

Output sections (see models.py):
    10-K : item1 (business), item1a (risk factors), item7 (MD&A), plus the notes found in Item 8
    10-Q : item7 (= Item 2 MD&A), plus the notes found in Part I Item 1 and the market-risk Item 3
and from the notes: debt_footnote, note_credit, note_leases, note_commitments, note_derivatives, note_subsequent,
note_basis (one chunk of text per section).

Filings are messy, so the rules are heuristics with a safe failure mode: a section that cannot be found is simply absent
(the Retriever then reports the topic as missing), never guessed. Use `python -m forecaster.cli edgar-check TICKER`
to see what was found for a real company.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from .models import (
    SECTION_BUSINESS,
    SECTION_DEBT,
    SECTION_MDA,
    SECTION_NOTE_BASIS,
    SECTION_NOTE_COMMIT,
    SECTION_NOTE_CREDIT,
    SECTION_NOTE_DERIV,
    SECTION_NOTE_LEASES,
    SECTION_NOTE_SUBSEQ,
    SECTION_RISK,
)

# --------------------------------------------------------------------------- HTML -> lines
_BLOCK = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "section", "article", "center", "pre", "hr",
          "blockquote", "dd", "dt", "body", "h1", "h2", "h3", "h4", "h5", "h6", "caption"}
_VOID = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr"}
_SKIP = {"script", "style", "head", "title", "ix:header"}
_HIDDEN = re.compile(r"display\s*:\s*none", re.I)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._stack: List[Tuple[str, bool]] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _VOID:
            if tag in _BLOCK and not self._hidden:
                self.parts.append("\n")
            return
        style = dict(attrs).get("style") or ""
        hide = tag in _SKIP or bool(_HIDDEN.search(style))
        self._stack.append((tag, hide))
        if hide:
            self._hidden += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self._stack) - 1, -1, -1):          # close the matching tag and anything left open inside it
            if self._stack[i][0] == tag:
                for _, hide in self._stack[i:]:
                    if hide:
                        self._hidden -= 1
                del self._stack[i:]
                break
        else:
            return
        if self._hidden:
            return
        if tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(re.sub(r"\s+", " ", data))      # a newline inside a <p> is just whitespace in HTML


def html_to_lines(html: str) -> List[str]:
    """One text line per paragraph or table row; table cells are joined with ' | '; hidden XBRL header dropped."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ").replace("​", "")
    lines: List[str] = []
    for raw in text.split("\n"):
        cells = [re.sub(r"[ \r\f\v]+", " ", c).strip() for c in raw.split("\t")]
        out: List[str] = []
        pending = ""
        for c in cells:
            if not c or c == "$":
                continue
            if c == "(":                                       # "(" | "1,234" | ")" are three cells in SEC tables
                pending = "("
            elif c in (")", "%", ")%") and out:
                out[-1] += c
            else:
                out.append(pending + c)
                pending = ""
        line = " | ".join(out).strip()
        if line:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------- Items
ITEM_RE = re.compile(r"^\s*(?:part\s+[iv]+[\s,\.\-–—:|]*)?item\s+(\d{1,2}[abc]?)[\s\.\:\-–—|]*(.{0,200})$", re.I)


def find_items(lines: List[str]) -> Dict[str, Tuple[int, int]]:
    """{item key: (first line, end line)} choosing, for each item, the occurrence with the longest body.

    The table of contents lists every item too, but its bodies are one line long, so the real section wins.
    """
    heads = [(i, m.group(1).lower()) for i, line in enumerate(lines) if len(line) < 260 and (m := ITEM_RE.match(line))]
    best: Dict[str, Tuple[int, int, int]] = {}
    for n, (i, key) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        size = sum(len(x) for x in lines[i + 1:end])
        if key not in best or size > best[key][2]:
            best[key] = (i, end, size)
    return {k: (v[0], v[1]) for k, v in best.items()}


def item_text(lines: List[str], span: Optional[Tuple[int, int]]) -> str:
    return "\n".join(lines[span[0] + 1:span[1]]) if span else ""


# --------------------------------------------------------------------------- Notes
_NOTE_WITH_WORD = re.compile(r"^\s*note\s*\(?(\d{1,2})\)?[\s\.\:\-–—|]*([A-Za-z][^|]{2,100})$", re.I)
_NOTE_BARE = re.compile(r"^\s*\(?(\d{1,2})[\.\)]\s*[-–—:|]?\s*([A-Z][A-Za-z0-9 ,&'’/\-–—()]{2,90})$")

NOTE_KINDS: List[Tuple[str, re.Pattern]] = [
    ("subsequent", re.compile(r"subsequent event", re.I)),
    ("basis", re.compile(r"basis of (?:presentation|preparation)|significant accounting|accounting policies|"
                         r"organization|nature of (?:the )?(?:business|operations)|description of (?:the )?business|"
                         r"going concern|principles of consolidation|general\b", re.I)),
    ("leases", re.compile(r"\blease", re.I)),
    ("debt", re.compile(r"\bdebt\b|borrowing|credit (?:facilit|agreement)|notes payable|financing arrangement|indebtedness|"
                        r"senior notes|term loan|revolv|long-term obligations", re.I)),
    ("commitments", re.compile(r"commitment|contingenc|litigation|legal|guarantee", re.I)),
    ("derivatives", re.compile(r"derivative|hedg|financial instruments|interest rate|risk management|fair value", re.I)),
]
KIND_SECTIONS = {
    "debt": (SECTION_DEBT, SECTION_NOTE_CREDIT),        # the same note feeds the maturity facts and the credit facts
    "leases": (SECTION_NOTE_LEASES,),
    "commitments": (SECTION_NOTE_COMMIT,),
    "derivatives": (SECTION_NOTE_DERIV,),
    "subsequent": (SECTION_NOTE_SUBSEQ,),
    "basis": (SECTION_NOTE_BASIS,),
}


def classify_note(title: str) -> Optional[str]:
    for kind, pat in NOTE_KINDS:
        if pat.search(title):
            return kind
    return None


def _note_candidates(lines: List[str]) -> List[Tuple[int, int, str]]:
    out = []
    for i, line in enumerate(lines):
        if len(line) > 130 or line.rstrip().endswith("."):
            continue
        m = _NOTE_WITH_WORD.match(line) or _NOTE_BARE.match(line)
        if m and len(m.group(2).split()) <= 14:
            out.append((i, int(m.group(1)), m.group(2).strip(" .:-–—|")))
    return out


def split_notes(lines: List[str]) -> List[Tuple[int, str, str]]:
    """[(first line, title, text)] for the numbered notes. A chain 1, 2, 3 ... of headings is required, which rejects the
    stray numbered lines that table rows and lists produce."""
    cands = _note_candidates(lines)
    best: List[Tuple[int, int, str]] = []
    for start in [c for c in cands if c[1] == 1]:
        chain, want = [start], 2
        for c in cands:
            if c[0] > chain[-1][0] and c[1] == want:
                chain.append(c)
                want += 1
        if len(chain) > len(best) or (len(chain) == len(best) and chain and chain[0][0] > best[0][0]):
            best = chain
    notes = []
    for n, (i, _num, title) in enumerate(best):
        end = best[n + 1][0] if n + 1 < len(best) else len(lines)
        notes.append((i, title, "\n".join(lines[i + 1:end])))
    return notes if len(notes) >= 3 else []


# --------------------------------------------------------------------------- caps
CAP_SECTION = 40_000      # one chunk per section: cap in characters
CAP_BUSINESS = 20_000
CAP_RISK_TOTAL = 160_000
RISK_PART = 8_000


def _cap(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[:cap].rsplit("\n", 1)[0] + "\n[truncated]"


def _mda_cap(text: str, cap: int = CAP_SECTION) -> str:
    """Keep the start of the MD&A and, when it is long, the Liquidity and Capital Resources part that drives this screen."""
    if len(text) <= cap:
        return text
    m = None
    for m in re.finditer(r"(?im)^\s*liquidity and capital resources\b.*$", text):
        pass                                                     # the last heading wins (the first is often a table of contents)
    if not m:
        return _cap(text, cap)
    head = text[: cap // 3].rsplit("\n", 1)[0]
    tail = text[m.start(): m.start() + cap - len(head) - 20]
    return head + "\n[...]\n" + tail.rsplit("\n", 1)[0]


# --------------------------------------------------------------------------- sections
def risk_parts(text: str) -> List[str]:
    """Item 1A is long: several chunks of about RISK_PART characters, split at paragraph boundaries."""
    parts, cur = [], ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) > RISK_PART:
            parts.append(cur)
            cur = ""
        cur += para + "\n"
        if sum(len(p) for p in parts) > CAP_RISK_TOTAL:
            break
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def extract_sections(form: str, html: str, want_text: bool = True, want_notes: bool = True) -> Dict[str, object]:
    """{section: text} (item1a maps to a list of parts) plus '_found' diagnostics. Missing sections are absent."""
    lines = html_to_lines(html)
    items = find_items(lines)
    is_k = form.upper().startswith("10-K")
    out: Dict[str, object] = {"_lines": len(lines), "_items": sorted(items)}

    if is_k and want_text:
        if "1" in items:
            out[SECTION_BUSINESS] = _cap(item_text(lines, items["1"]), CAP_BUSINESS)
        if "1a" in items:
            out[SECTION_RISK] = risk_parts(item_text(lines, items["1a"]))
    mda_key = "7" if is_k else "2"
    if want_notes and mda_key in items:
        out[SECTION_MDA] = _mda_cap(item_text(lines, items[mda_key]))

    if not want_notes:
        return out
    if is_k:                                                       # statements sit in Item 8, or in Item 15 (F-pages)
        fin = items.get("8")
        if not fin or sum(len(x) for x in lines[fin[0]:fin[1]]) < 5000:
            fin = items.get("15") or fin
        market = items.get("7a")
    else:
        fin, market = items.get("1"), items.get("3")
    fin_lines = lines[fin[0] + 1:fin[1]] if fin else []
    notes = split_notes(fin_lines)
    out["_notes"] = [(title, classify_note(title)) for _i, title, _t in notes]
    grouped: Dict[str, List[str]] = {}
    for _i, title, body in notes:
        kind = classify_note(title)
        if kind:
            grouped.setdefault(kind, []).append(f"{title}\n{body}")
    if market:
        grouped.setdefault("derivatives", []).append(item_text(lines, market))
    for kind, texts in grouped.items():
        joined = _cap("\n\n".join(texts), CAP_SECTION)
        for section in KIND_SECTIONS[kind]:
            out[section] = joined
    return out
