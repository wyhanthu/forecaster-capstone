"""The company workflow runs on LangGraph (Checkpoint 4.1 names it as the controller). The in-house `StateGraph` below
has the same call shape (add_node / add_edge / add_conditional_edges / compile / invoke) and is the fallback when
langgraph is not installed, so the core project still runs with the standard library only."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

try:
    from langgraph.graph import END as LG_END, StateGraph as LangGraphStateGraph
    LANGGRAPH = True
except ImportError:  # pragma: no cover - optional dependency
    LangGraphStateGraph, LG_END, LANGGRAPH = None, "__end__", False   # type: ignore[assignment,misc]

END = LG_END      # "__end__" in both implementations


class FlowState(TypedDict, total=False):
    """Everything the nodes of one company's workflow pass to each other (LangGraph needs the keys declared)."""
    ticker: str
    bundle: Any
    cik: str
    fs: Any
    fallback_used: bool
    stm: Any
    rounds: int
    ratios: Any
    series: Any
    trend: Any
    stop: Any
    implied: Any
    rating_key: Any
    evidence: Any
    notes_missing: Any
    lesson_hits: Any
    tot: Any
    result: Any
    path: Annotated[List[str], operator.add]       # the nodes that ran, in order (an audit trail of the route)


class StateGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Callable[[dict], Optional[dict]]] = {}
        self.edges: Dict[str, str] = {}
        self.routers: Dict[str, Callable[[dict], str]] = {}
        self.entry: Optional[str] = None

    def add_node(self, name: str, fn: Callable[[dict], Optional[dict]]) -> None:
        self.nodes[name] = fn

    def set_entry_point(self, name: str) -> None:
        self.entry = name

    def add_edge(self, src: str, dst: str) -> None:
        self.edges[src] = dst

    def add_conditional_edges(self, src: str, router: Callable[[dict], str], path_map: Optional[dict] = None) -> None:
        self.routers[src] = router

    def compile(self) -> "StateGraph":
        return self

    def invoke(self, state: dict, config: Optional[dict] = None, max_steps: int = 60) -> dict:
        cur = self.entry
        state.setdefault("path", [])
        for _ in range(max_steps):
            if cur == END:
                return state
            state["path"].append(cur)
            state.update(self.nodes[cur](state) or {})
            cur = self.routers[cur](state) if cur in self.routers else self.edges.get(cur, END)
        raise RuntimeError("graph did not terminate (bounded loops are broken)")
