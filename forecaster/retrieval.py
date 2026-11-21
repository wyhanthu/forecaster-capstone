"""Hybrid retrieval over structure-aware filing chunks (Checkpoint 3.1).

score = 0.5 * semantic (TF-IDF cosine, stand-in for embeddings)
      + 0.3 * keyword  (exact terms such as XBRL tag names)
      + 0.2 * section prior (a debt footnote outranks a passing mention in Item 1)
Metadata filters (CIK, section, filing date) are applied before ranking. Top-k candidates
are re-ranked with Maximal Marginal Relevance (as in the teacher's Virtual Lab 3.2) so the
2-3 passages handed to the Analyst are relevant and not redundant.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .models import SECTION_8K, Chunk

STOP = set("the a an of and or to in for on at by with as is was were be that this it its from are".split())


def focus(text: str, terms, limit: int) -> str:
    """The part of a long passage most relevant to `terms`, as whole sentences in their original order.

    Real filing notes run to tens of thousands of characters while the model only sees `limit` of them, so cutting
    at the start would usually drop the sentence that matters. Short passages are returned unchanged. Every kept
    sentence is a verbatim substring of `text`, so quotes taken from the result still pass the verbatim check.
    """
    if len(text) <= limit:
        return text
    want = {t for term in terms for t in tokenize(str(term))}
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    scored = []
    for i, sent in enumerate(sentences):
        toks = set(tokenize(sent))
        scored.append((len(toks & want) + (0.5 if re.search(r"\$\s?\d", sent) else 0.0), i, sent))
    keep, used = [], 0
    for score, i, sent in sorted(scored, key=lambda x: (-x[0], x[1])):
        if score <= 0 and keep:
            break
        if used + len(sent) + 1 > limit:
            continue
        keep.append((i, sent))
        used += len(sent) + 1
    if not keep:
        return text[:limit]
    return " ".join(sent for _, sent in sorted(keep))


def tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    sem: float = 0.0
    kw: float = 0.0
    prior: float = 0.0


class HybridIndex:
    def __init__(self, chunks: Iterable[Chunk]):
        self._lock = threading.Lock()
        self.chunks: Dict[str, Chunk] = {}
        self.vecs: Dict[str, Dict[str, float]] = {}
        self._by_key: Dict[tuple, List[str]] = defaultdict(list)
        chunks = list(chunks)
        docs = {c.chunk_id: Counter(tokenize(c.text)) for c in chunks}
        df: Counter = Counter()
        for tf in docs.values():
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        for c in chunks:
            self.chunks[c.chunk_id] = c
            self.vecs[c.chunk_id] = self._weigh(docs[c.chunk_id])
            self._by_key[(c.cik, c.section)].append(c.chunk_id)

    # ------------------------------------------------------------- vectors
    def _weigh(self, tf: Counter) -> Dict[str, float]:
        v = {t: (1 + math.log(n)) * self.idf.get(t, 1.0) for t, n in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def vec(self, text: str) -> Dict[str, float]:
        return self._weigh(Counter(tokenize(text)))

    @staticmethod
    def cos(a: Dict[str, float], b: Dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(t, 0.0) for t, x in a.items())

    # ------------------------------------------------------------- lookups
    def get(self, chunk_id: str) -> Optional[Chunk]:
        return self.chunks.get(chunk_id)

    def company_chunks(self, cik: str) -> List[Chunk]:
        return [c for c in self.chunks.values() if c.cik == cik]

    def section_chunks(self, cik: str, section: str) -> List[Chunk]:
        ids = self._by_key.get((cik, section), [])
        return sorted((self.chunks[i] for i in ids), key=lambda c: c.filed, reverse=True)

    def latest_filed(self, cik: str, section: str) -> Optional[str]:
        cs = self.section_chunks(cik, section)
        return cs[0].filed if cs else None

    def is_stale(self, chunk: Chunk) -> bool:
        latest = self.latest_filed(chunk.cik, chunk.section)
        return bool(chunk.superseded_by) or (latest is not None and chunk.filed < latest)

    # ------------------------------------------------------------- search
    def search(self, query: str, *, cik: Optional[str], sections: Optional[Sequence[str]] = None,
               filed_after: Optional[str] = None, keywords: Sequence[str] = (),
               pref_sections: Sequence[str] = (), k: int = 5, min_score: float = 0.0,
               semantic: bool = True, include_8k: bool = False) -> List[Hit]:
        qv = self.vec(query)
        hits: List[Hit] = []
        for c in self.chunks.values():
            if cik is not None and c.cik != cik:
                continue
            if sections and c.section not in sections:
                continue
            if c.section == SECTION_8K and not include_8k:
                continue
            if filed_after and c.filed < filed_after:
                continue
            sem = self.cos(qv, self.vecs[c.chunk_id]) if semantic else 0.0
            low = c.text.lower()
            kw = (sum(1 for w in keywords if w.lower() in low) / len(keywords)) if keywords else 0.0
            if pref_sections:
                prior = 1.0 if c.section == pref_sections[0] else 0.6 if c.section in pref_sections[1:] else 0.1
            else:
                prior = 0.5
            score = (0.5 * sem + 0.3 * kw + 0.2 * prior) if semantic else (kw + 0.01 * prior)
            if score >= min_score:
                hits.append(Hit(c, score, sem, kw, prior))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:k]

    def full_section(self, cik: str, sections: Sequence[str]) -> List[Hit]:
        out: List[Hit] = []
        for s in sections:
            for c in self.section_chunks(cik, s)[:1]:
                out.append(Hit(c, 1.0))
        return out

    # ------------------------------------------------------------- MMR re-rank
    def mmr(self, hits: List[Hit], n: int, lam: float = 0.7, keep_min: int = 2) -> List[Hit]:
        pool = list(hits)
        chosen: List[Hit] = []
        while pool and len(chosen) < n:
            best, best_val = None, -1e9
            for h in pool:
                div = max((self.cos(self.vecs[h.chunk.chunk_id], self.vecs[s.chunk.chunk_id]) for s in chosen), default=0.0)
                val = lam * h.score - (1 - lam) * div
                if val > best_val:
                    best, best_val = h, val
            if len(chosen) >= keep_min and best.score < 0.5 * chosen[0].score:
                break
            chosen.append(best)
            pool.remove(best)
        return chosen


class ShardedIndex:
    """One HybridIndex per company, built on first use.

    A live run covers thousands of companies but only the pre-screen survivors are ever read, and every search is
    already restricted to one CIK. So the filing text of a company is fetched and indexed the first time something
    asks for it, and the TF-IDF statistics are those of that company's own filings. Same API as HybridIndex.
    """

    def __init__(self, loader):
        self._loader = loader                       # cik -> List[Chunk]; may raise ToolError
        self._shards: Dict[str, HybridIndex] = {}
        self._owner: Dict[str, str] = {}            # chunk_id -> cik
        self._lock = threading.Lock()
        self._cik_locks: Dict[str, threading.Lock] = {}

    def shard(self, cik: str) -> HybridIndex:
        got = self._shards.get(cik)
        if got is not None:
            return got
        with self._lock:
            lock = self._cik_locks.setdefault(cik, threading.Lock())
        with lock:                                  # load each company once, even with several workers
            got = self._shards.get(cik)
            if got is None:
                got = HybridIndex(self._loader(cik))
                with self._lock:
                    self._shards[cik] = got
                    for cid in got.chunks:
                        self._owner[cid] = cik
        return got

    @property
    def loaded(self) -> List[str]:
        return sorted(self._shards)

    # ------------------------------------------------------------- same API as HybridIndex
    def get(self, chunk_id: str) -> Optional[Chunk]:
        cik = self._owner.get(chunk_id)
        return self._shards[cik].get(chunk_id) if cik else None

    def company_chunks(self, cik: str) -> List[Chunk]:
        return self.shard(cik).company_chunks(cik)

    def section_chunks(self, cik: str, section: str) -> List[Chunk]:
        return self.shard(cik).section_chunks(cik, section)

    def latest_filed(self, cik: str, section: str) -> Optional[str]:
        return self.shard(cik).latest_filed(cik, section)

    def is_stale(self, chunk: Chunk) -> bool:
        return self.shard(chunk.cik).is_stale(chunk)

    def search(self, query: str, *, cik: Optional[str], **kw) -> List[Hit]:
        if cik is not None:
            return self.shard(cik).search(query, cik=cik, **kw)
        hits = [h for sh in list(self._shards.values()) for h in sh.search(query, cik=None, **kw)]
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[: kw.get("k", 5)]

    def full_section(self, cik: str, sections: Sequence[str]) -> List[Hit]:
        return self.shard(cik).full_section(cik, sections)

    def mmr(self, hits: List[Hit], n: int, lam: float = 0.7, keep_min: int = 2) -> List[Hit]:
        if not hits:
            return []
        return self.shard(hits[0].chunk.cik).mmr(hits, n, lam, keep_min)
