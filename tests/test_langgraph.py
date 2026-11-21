"""The company workflow runs on LangGraph. It must behave exactly like the in-house graph it replaced."""
import dataclasses

import pytest

pytest.importorskip("langgraph")
from forecaster.config import ForecasterConfig                     # noqa: E402
from forecaster.cli import main as cli_main                        # noqa: E402
from test_forecaster import mk                                     # noqa: E402


def _run(tmp_path, name, use_langgraph, **kw):
    cfg = ForecasterConfig(state_dir=str(tmp_path / name), workers=1, use_langgraph=use_langgraph)
    f = mk(cfg, **kw)
    return f, {t: dataclasses.asdict(f.screen_company(t).scorecard) for t in sorted(f.source.tickers())}


def test_langgraph_is_the_default_backend(tmp_path):
    f = mk(ForecasterConfig(state_dir=str(tmp_path)))
    assert f.graph_backend == "langgraph"
    assert mk(ForecasterConfig(state_dir=str(tmp_path / "b"), use_langgraph=False)).graph_backend == "in-house"


def test_langgraph_gives_the_same_scorecards_as_the_in_house_graph(tmp_path):
    f_lg, lg = _run(tmp_path, "lg", True)
    f_ih, ih = _run(tmp_path, "ih", False)
    assert len(lg) == 14 and lg == ih          # every company: same status, scores, claims, flags, review reasons


def test_langgraph_follows_the_same_route_including_the_revision_loop(tmp_path):
    f_lg, _ = _run(tmp_path, "lg", True)
    f_ih, _ = _run(tmp_path, "ih", False)
    for t in f_lg.source.tickers():
        a = [e["event"] for e in f_lg.audit.for_ticker(t)]
        b = [e["event"] for e in f_ih.audit.for_ticker(t)]
        assert a == b, t


def test_early_stops_and_tool_failures_still_end_cleanly_under_langgraph(tmp_path):
    f, out = _run(tmp_path, "lg", True, inject_failures={("ALPH", "edgar_facts"): 99})
    assert out["ALPH"]["status"] == "insufficient_data" and out["ALPH"]["skip_reason"] == "tool_failure"
    assert any(s["status"] == "prescreen_fail" for s in out.values())         # a stop before retrieval


def test_the_workflow_can_be_drawn(tmp_path, capsys):
    assert cli_main(["--state-dir", str(tmp_path), "graph"]) == 0
    text = capsys.readouterr().out
    for node in ("load", "prescreen", "retrieve", "notes", "reason", "revise", "finalize"):
        assert node in text
