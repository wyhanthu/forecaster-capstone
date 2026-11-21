"""The LLM layer runs on LangChain. These tests drive the real LangChain objects (messages, bind_tools, prompt | model |
parser) with a fake chat model, so the whole path is exercised offline."""
import json
from typing import Any

import pytest

pytest.importorskip("langchain_core")
from langchain_core.language_models.chat_models import BaseChatModel          # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage   # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult                  # noqa: E402

from forecaster.llm import LLMClient, to_langchain                             # noqa: E402
from test_forecaster import FakeLLM, cfg, mk, reader_industry                  # noqa: E402,F401


def to_dicts(messages):
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            d = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                d["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in m.tool_calls]
            out.append(d)
    return out


class DelegatingChatModel(BaseChatModel):
    """A real LangChain chat model whose replies come from a scripted delegate (FakeLLM or a stub)."""
    delegate: Any

    @property
    def _llm_type(self) -> str:
        return "delegating-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        out = self.delegate.chat(to_dicts(messages), json_mode=bool(kwargs.get("response_format")))
        ai = AIMessage(content=out["content"],
                       tool_calls=[{"id": c["id"], "name": c["name"], "args": c["arguments"]} for c in out["tool_calls"]],
                       usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18})
        return ChatResult(generations=[ChatGeneration(message=ai)])


class Stub:
    def __init__(self, text="", calls=()):
        self.text, self.calls = text, list(calls)

    def chat(self, messages, tools=None, json_mode=False):
        return {"content": self.text, "tool_calls": self.calls}


def test_transcript_converts_to_langchain_messages():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "Thought", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "filing_search", "arguments": '{"query": "x"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"}]
    lc = to_langchain(msgs)
    assert [type(m).__name__ for m in lc] == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage"]
    assert lc[2].tool_calls[0]["name"] == "filing_search" and lc[2].tool_calls[0]["args"] == {"query": "x"}
    assert lc[3].tool_call_id == "c1"


def test_chat_returns_tool_calls_in_the_agents_format_with_token_usage():
    call = {"id": "t1", "name": "filing_search", "arguments": {"query": "coverage"}}
    client = LLMClient(model=DelegatingChatModel(delegate=Stub("Thought: look.", [call])))
    out = client.chat([{"role": "user", "content": "go"}], tools=[{"type": "function", "function": {"name": "filing_search"}}])
    assert out["content"] == "Thought: look." and out["tool_calls"] == [call]
    assert out["assistant_message"]["tool_calls"][0]["function"]["arguments"] == '{"query": "coverage"}'
    assert out["usage"] == {"input": 11, "output": 7}


def test_json_chain_parses_fenced_json_and_reports_garbage():
    ok = LLMClient(model=DelegatingChatModel(delegate=Stub('```json\n{"supports_hypothesis": true}\n```')))
    assert ok.run_json("sys {with braces}", "user {x}") == ({"supports_hypothesis": True}, "ok")
    bad = LLMClient(model=DelegatingChatModel(delegate=Stub("not json at all")))
    assert bad.run_json("s", "u") == (None, "bad_json")


def test_client_without_a_key_is_disabled_not_broken(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = LLMClient()
    assert not c.enabled and c.unavailable_reason == "OPENAI_API_KEY is not set"
    assert c.chat([{"role": "user", "content": "x"}])["content"] == ""


def test_full_screen_runs_through_langchain(cfg):
    llm = LLMClient(model=DelegatingChatModel(delegate=FakeLLM(reader_industry)))
    f = mk(cfg, llm=llm)
    res = f.screen_company("DRFT")
    assert res.scorecard.interpretation == "deterioration" and res.scorecard.scope == "industry"
    assert f.audit.count("llm_call", ticker="DRFT", purpose="retrieve") >= 1      # ReAct: LangChain chat + bind_tools
    assert f.audit.count("llm_call", ticker="DRFT", chain="json") >= 1            # reading: prompt | model | parser
    assert any(e.get("tokens") == {"input": 11, "output": 7} for e in f.audit.for_ticker("DRFT") if e["event"] == "llm_call")
    assert any(c.topic == "industry_conditions" and c.quote.startswith("Industry-wide") for c in res.claims)


class Boom:
    def chat(self, messages, tools=None, json_mode=False):
        raise RuntimeError("404 not found for key sk-abcdef123456789")


def test_a_failing_api_is_reported_not_swallowed(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdef123456789")
    client = LLMClient(model=DelegatingChatModel(delegate=Boom()))
    out = client.chat([{"role": "user", "content": "x"}])
    assert out["content"] == "" and out["error"].startswith("RuntimeError") and "abcdef" not in out["error"]
    f = mk(cfg, llm=client)
    res = f.screen_company("ALPH")                      # the screen still completes on the rule-based path
    assert res.scorecard.status
    assert f.llm.errors >= 1 and "RuntimeError" in f.llm.first_error
    assert f.audit.count("llm_error", ticker="ALPH") >= 1


def test_a_full_endpoint_url_is_trimmed_to_the_api_root():
    from forecaster.llm import clean_base_url
    assert clean_base_url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"
    assert clean_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert clean_base_url("") is None and clean_base_url(None) is None
