"""Text patterns shared by the Retriever, Analyst and Validator.

Everything here is deterministic. The patterns decide which filing sentences count as
material findings (going concern, covenant pressure, restatement, temporary event),
which topic a sentence answers, and which sections may legitimately support a topic.
"""
from __future__ import annotations

import re
from typing import List, Set

from .models import (
    SECTION_8K,
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
    Chunk,
)

PATTERNS = {
    "going_concern": re.compile(r"going concern|substantial doubt", re.I),
    "covenant_pressure": re.compile(
        r"waiver|amendment|additional relief|may not be able to comply|unable to comply|covenant breach|breach of", re.I
    ),
    "restatement": re.compile(r"restat", re.I),
    "temporary_event": re.compile(r"one-time|non-recurring|temporary|transitory", re.I),
    "litigation": re.compile(r"class action|lawsuit|putative|defendant in", re.I),
    "off_balance_sheet": re.compile(r"unconsolidated|off-balance|variable interest entit|guarantees? (?:of )?\$", re.I),
    "adverse_development": re.compile(r"event of default|downgrad|lost (?:our|its) largest customer", re.I),
    "industry_conditions": re.compile(r"industry-wide|sector-wide|across (?:the|our) (?:industry|sector|end markets)", re.I),
}
NEG_SUBSEQ = re.compile(r"no (?:material )?subsequent events", re.I)
RECOVERY = re.compile(r"expects? to (?:repay|recover|reduce)|proceeds|deleverag|normalize", re.I)
ENTITY_RISK = re.compile(r"former subsidiary|divested subsidiary|prior to (?:its|the) (?:sale|divestiture)", re.I)
NUMBER = re.compile(r"\d[\d,]*\.?\d*")

# sentence that answers a topic (used to turn chunks into verbatim fact claims)
TOPIC_PATTERN = {
    "leverage": re.compile(r"Total debt outstanding", re.I),
    "coverage": re.compile(r"Interest coverage", re.I),
    "maturities": re.compile(r"Scheduled maturities", re.I),
    "liquidity": re.compile(r"cash and cash equivalents", re.I),
    # notes to the financial statements
    "credit": re.compile(r"credit agreement requires", re.I),
    "leases": re.compile(r"lease obligations", re.I),
    "commitments": re.compile(r"commitments due within the next two years", re.I),
    "derivatives": re.compile(r"Floating-rate debt", re.I),
    "subsequent": re.compile(r"subsequent events|Subsequent to", re.I),
    "basis": re.compile(r"prepared in accordance with U\.S\. GAAP", re.I),
}

# sections allowed to support a topic ("debt footnotes for leverage", Checkpoint 4.1)
ANY_SECTION = {SECTION_BUSINESS, SECTION_RISK, SECTION_MDA, SECTION_DEBT, SECTION_8K, SECTION_NOTE_CREDIT,
               SECTION_NOTE_LEASES, SECTION_NOTE_COMMIT, SECTION_NOTE_DERIV, SECTION_NOTE_SUBSEQ, SECTION_NOTE_BASIS}
TOPIC_SECTIONS = {
    "leverage": {SECTION_DEBT, SECTION_MDA},
    "coverage": {SECTION_MDA},
    "maturities": {SECTION_DEBT},
    "liquidity": {SECTION_MDA},
    "credit": {SECTION_NOTE_CREDIT},
    "leases": {SECTION_NOTE_LEASES},
    "commitments": {SECTION_NOTE_COMMIT},
    "derivatives": {SECTION_NOTE_DERIV},
    "subsequent": {SECTION_NOTE_SUBSEQ},
    "basis": {SECTION_NOTE_BASIS},
}

FINDING_TOPIC = {
    "going_concern": "going_concern",
    "covenant_pressure": "covenant_pressure",
    "restatement": "restatement",
    "temporary_event": "temporary_event",
}


def split_sentences(text: str) -> List[str]:
    protected = text.strip().replace("U.S.", "U\u00a7S\u00a7")           # "U.S. GAAP" is not a sentence break
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z$])|\n+", protected)   # a heading or table row is not part of the next sentence
    return [p.replace("U\u00a7S\u00a7", "U.S.").strip() for p in parts if p.strip()]


# A going-concern sentence only counts when it ASSERTS doubt. Real filings are full of the opposite: "management evaluated
# whether there is substantial doubt ... and concluded there is none", "could raise substantial doubt".
NOT_ASSERTED = re.compile(r"\b(?:no|not|neither|without|whether|if|could|might|would|may|alleviat\w*|evaluat\w*|assess\w*)\b", re.I)


def sentence_tags(sentence: str) -> List[str]:
    tags = [name for name, pat in PATTERNS.items() if pat.search(sentence)]
    if "going_concern" in tags and NOT_ASSERTED.search(sentence):
        tags.remove("going_concern")
    return tags


# Wording of real 10-K / 10-Q passages. The patterns above match the exact phrasing of the synthetic universe; these are
# the second pass, used only when the first finds no sentence for a topic, so the synthetic behavior is unchanged.
_DOLLAR = re.compile(r"\$\s?\d")
REAL_TOPIC = {
    "leverage": (re.compile(r"(?:total|aggregate)\s+(?:outstanding\s+)?(?:debt|indebtedness|borrowings)|outstanding\s+"
                            r"(?:debt|indebtedness|borrowings|principal)|(?:long-term|senior)\s+(?:debt|notes)", re.I), True),
    "coverage": (re.compile(r"interest coverage|coverage ratio|fixed charge|interest expense", re.I), True),
    "maturities": (re.compile(r"matur\w+|principal payments|scheduled (?:payments|repayments)", re.I), True),
    "liquidity": (re.compile(r"cash and cash equivalents|liquidity", re.I), True),
    "credit": (re.compile(r"(?:revolv\w+|credit (?:facility|agreement)|term loan).*(?:availab|capacity|covenant|leverage ratio|"
                          r"undrawn|unused)|(?:availab|capacity|covenant|undrawn|unused).*(?:revolv\w+|credit (?:facility|agreement))",
                          re.I), False),
    "leases": (re.compile(r"lease.*(?:obligation|payment|liabilit|commitment)", re.I), True),
    "commitments": (re.compile(r"commitment|contingenc|guarantee|purchase obligation|litigation|legal proceeding", re.I), False),
    "derivatives": (re.compile(r"floating|variable[- ]rate|interest rate (?:swap|risk|cap|exposure)|basis point|derivative|hedg", re.I), False),
    "subsequent": (re.compile(r"subsequent events?|subsequent to|after (?:the )?(?:quarter|period|balance sheet)|"
                              r"through the (?:date|issuance)", re.I), False),
    "basis": (re.compile(r"prepared in accordance|basis of presentation|generally accepted accounting|U\.S\. GAAP", re.I), False),
}


def real_topic_match(topic: str, sentence: str) -> bool:
    pat, needs_amount = REAL_TOPIC[topic]
    return bool(pat.search(sentence)) and (not needs_amount or bool(_DOLLAR.search(sentence)))


def chunk_tags(chunk: Chunk) -> Set[str]:
    tags = {name for name, pat in PATTERNS.items() if pat.search(chunk.text)}
    if chunk.form == "8-K":
        tags.add("event")
    return tags


def entity_risk(chunk: Chunk) -> bool:
    """True when a risk paragraph is about a divested/former subsidiary, not the current obligor."""
    return bool(ENTITY_RISK.search(chunk.text))


def has_quantified(sentence: str) -> bool:
    return "$" in sentence and bool(NUMBER.search(sentence))


def classify_event(text: str) -> str:
    if re.search(r"waiver", text, re.I):
        return "covenant_waiver"
    if re.search(r"lowered|downgrad", text, re.I):
        return "downgrade"
    if re.search(r"restat", text, re.I):
        return "restatement"
    if re.search(r"merger|acquir", text, re.I):
        return "merger"
    return "other"
