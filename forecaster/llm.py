"""LLM access: a thin client (same env vars as the teacher's llm_writer.py) and a gate that adds
a per-company call budget and an audit log entry for every call (Checkpoint 6.1 / Virtual Lab 6.2).

Everything degrades gracefully: with no OPENAI_API_KEY (or after the budget is spent) the agents fall back to
their deterministic rules, so the project still runs offline.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from typing import Dict, List, Optional, Tuple

# The LLM layer is built on LangChain (Checkpoint 4.1: "LangChain supplies the Analyst's thought-generation chain ...
# and the Validator's evaluation chain"). It is optional: without langchain or without a key every agent falls back
# to its deterministic rules, so the project still runs offline.
try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    LANGCHAIN = True
except ImportError:  # pragma: no cover - optional dependency
    LANGCHAIN = False
try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - optional dependency
    ChatOpenAI = None  # type: ignore[assignment,misc]

from .audit import AuditLog
from .config import ForecasterConfig

EMPTY = {"content": "", "tool_calls": [], "assistant_message": {"role": "assistant", "content": ""}}
JSON_MODE = {"response_format": {"type": "json_object"}}


def clean_base_url(url: Optional[str]) -> Optional[str]:
    """The client appends /chat/completions itself, so a full endpoint URL is trimmed back to the API root."""
    u = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u or None


OPENROUTER_URL = "https://openrouter.ai/api/v1"


def provider_settings() -> dict:
    """Which OpenAI-compatible endpoint to call.

    LLM_PROVIDER=openrouter|openai picks it. Without it: OpenRouter when OPENROUTER_API_KEY is the only key set, else OpenAI
    (or whatever OPENAI_BASE_URL points at). OpenRouter model names look like `provider/model` (openrouter.ai/models); the
    model must support tool calling and JSON mode, and `require_parameters` keeps OpenRouter from routing a call to a provider
    that would silently ignore them."""
    want = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    or_key, oa_key = os.getenv("OPENROUTER_API_KEY"), os.getenv("OPENAI_API_KEY")
    # Upstream provider pinning: OPENROUTER_PROVIDER=alibaba (or a comma list). A LLM_PROVIDER value that is neither
    # "openrouter" nor "openai" is read the same way, as an upstream provider name, and implies OpenRouter.
    pin = os.getenv("OPENROUTER_PROVIDER") or (want if want not in ("", "openrouter", "openai") else "")
    only = [x.strip() for x in pin.split(",") if x.strip()]
    if want not in ("openrouter", "openai"):
        want = "openrouter" if (or_key and (only or not oa_key)) else "openai"
    if want == "openrouter":
        route = {"require_parameters": True}
        if only:
            # Pinned: only these upstream providers, never fall back to another (keeps the prompt cache hit rate high).
            route.update(only=only, allow_fallbacks=False)
        return dict(name="openrouter", key=or_key, key_var="OPENROUTER_API_KEY",
                    base=clean_base_url(os.getenv("OPENROUTER_BASE_URL")) or OPENROUTER_URL,
                    model=os.getenv("OPENROUTER_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-4.1-mini",
                    extra=dict(default_headers={"X-Title": "Forecaster"}, extra_body={"provider": route}))
    return dict(name="openai", key=oa_key, key_var="OPENAI_API_KEY", base=clean_base_url(os.getenv("OPENAI_BASE_URL")),
                model=os.getenv("OPENAI_MODEL") or "gpt-4.1-mini", extra={})


def redact(message: str, limit: int = 300) -> str:
    """Error text for the audit log and the console, with anything that looks like an API key removed."""
    for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        key = os.getenv(var) or ""
        if key:
            message = message.replace(key, "<key>")
    return re.sub(r"sk-[A-Za-z0-9_\-*]{4,}", "sk-***", message)[:limit]


def where(exc: BaseException, frames: int = 4) -> str:
    """The innermost few stack frames of an exception, as 'file.py:line in function' (paths shortened)."""
    import traceback
    tb = traceback.extract_tb(exc.__traceback__)[-frames:]
    return " <- ".join(f"{'/'.join(f.filename.replace(chr(92), '/').split('/')[-2:])}:{f.lineno} in {f.name}" for f in reversed(tb))


def _loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in content)
    return ""


def to_langchain(messages: List[dict]) -> list:
    """OpenAI-style message dicts (what the agents keep in their ReAct transcript) -> LangChain messages."""
    out: list = []
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role == "assistant":
            calls = [{"id": tc["id"], "name": tc["function"]["name"], "args": _loads(tc["function"].get("arguments"))}
                     for tc in m.get("tool_calls", [])]
            out.append(AIMessage(content=content, tool_calls=calls))
        elif role == "tool":
            out.append(ToolMessage(content=content, tool_call_id=m["tool_call_id"]))
        else:
            out.append(HumanMessage(content=content))
    return out


class LLMClient:
    """LangChain chat model with function calling.

    Env: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL (any OpenAI-compatible endpoint), or for OpenRouter OPENROUTER_API_KEY,
    OPENROUTER_MODEL and LLM_PROVIDER=openrouter (see provider_settings); optional OPENAI_TEMPERATURE
    (not sent unless set, because some models only accept their default). A ready-made LangChain chat
    model can be injected with `model=` (tests use this, so the whole LangChain path runs offline)."""

    def __init__(self, model=None) -> None:
        self.settings = provider_settings()
        self.model_name = self.settings["model"]
        self.unavailable_reason = ""
        self.last_error = ""
        self._model = None
        if not LANGCHAIN:
            self.unavailable_reason = "langchain-core is not installed"
        elif model is not None:
            self._model = model
        elif not self.settings["key"]:
            self.unavailable_reason = f"{self.settings['key_var']} is not set"
        elif ChatOpenAI is None:
            self.unavailable_reason = "langchain-openai is not installed"
        else:
            kwargs = {}
            if os.getenv("OPENAI_TEMPERATURE"):            # off by default: some models reject the parameter
                kwargs["temperature"] = float(os.getenv("OPENAI_TEMPERATURE"))
            # connection errors and 429/5xx are retried with backoff (the SDK does the waiting); with a pinned provider there is
            # no fallback route, so transient failures are the main way a call is lost
            kwargs["max_retries"] = int(os.getenv("LLM_MAX_RETRIES") or 6)
            kwargs["timeout"] = float(os.getenv("LLM_TIMEOUT") or 120)
            self._model = ChatOpenAI(model=self.model_name, api_key=self.settings["key"], base_url=self.settings["base"],
                                     **self.settings["extra"], **kwargs)

    @property
    def enabled(self) -> bool:
        return self._model is not None

    def status(self) -> str:
        if self.enabled:
            pin = ((self.settings.get("extra") or {}).get("extra_body") or {}).get("provider", {}).get("only")
            return (f"LLM: enabled ({self.settings['name']}, model {self.model_name}"
                    + (f", pinned to {','.join(pin)}, no fallback" if pin else "") + ")")
        return f"LLM: disabled ({self.unavailable_reason or 'no model'}); running the rule-based path"

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None, json_mode: bool = False) -> dict:
        """Returns {"content", "tool_calls": [{"id","name","arguments"(dict)}], "assistant_message", "usage"}."""
        if not self._model:
            return dict(EMPTY)
        model = self._model.bind_tools(tools) if tools else self._model
        if json_mode:
            model = model.bind(**JSON_MODE)
        try:
            msg = model.invoke(to_langchain(messages))
        except Exception as exc:  # network/API failure must never break a screen, but it must never be silent either
            self.last_error = f"{type(exc).__name__}: {redact(str(exc))} [at {where(exc)}]"
            return {**EMPTY, "error": self.last_error}
        calls, raw_calls = [], []
        for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
            cid = tc.get("id") or f"call_{i}"
            args = tc.get("args") or {}
            calls.append({"id": cid, "name": tc["name"], "arguments": args})
            raw_calls.append({"id": cid, "type": "function",
                              "function": {"name": tc["name"], "arguments": json.dumps(args)}})
        text = _text(msg.content)
        am: dict = {"role": "assistant", "content": text}
        if raw_calls:
            am["tool_calls"] = raw_calls
        usage = getattr(msg, "usage_metadata", None) or {}
        return {"content": text, "tool_calls": calls, "assistant_message": am,
                "usage": {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")} if usage else None}

    def run_json(self, system: str, user: str) -> Tuple[Optional[dict], str]:
        """A LangChain chain: prompt | chat model (JSON mode) | JsonOutputParser. Returns (data, status) with status
        "ok", "bad_json" or "error". The prompt takes the texts as variables, so braces in a filing never get
        interpreted as template fields."""
        if not self._model:
            return None, "error"
        prompt = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{user}")])
        chain = prompt | self._model.bind(**JSON_MODE) | JsonOutputParser()
        try:
            data = chain.invoke({"system": system, "user": user})
        except Exception as exc:
            bad = exc.__class__.__name__ == "OutputParserException"
            if not bad:
                self.last_error = f"{type(exc).__name__}: {redact(str(exc))} [at {where(exc)}]"
            return None, "bad_json" if bad else "error"
        return (data, "ok") if isinstance(data, dict) else (None, "bad_json")


class LLMGate:
    """Budget + logging wrapper. Agents only ever talk to this, never to the raw client."""

    def __init__(self, client, cfg: ForecasterConfig, audit: AuditLog):
        self.client, self.cfg, self.audit = client, cfg, audit
        self._n: Counter = Counter()
        self._lock = threading.Lock()
        self.errors = 0
        self.first_error = ""

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.client, "enabled", False))

    def _error(self, ticker: str, purpose: str, error: str) -> None:
        with self._lock:
            self.errors += 1
            self.first_error = self.first_error or error
        self.audit.log("llm_error", ticker=ticker, purpose=purpose, error=error)

    def calls(self, ticker: str) -> int:
        return self._n[ticker]

    def _take(self, ticker: str, purpose: str) -> bool:
        with self._lock:
            if self._n[ticker] >= self.cfg.llm_max_calls_per_company:
                self.audit.log("llm_budget_exceeded", ticker=ticker, purpose=purpose)
                return False
            self._n[ticker] += 1
            return True

    def chat(self, ticker: str, messages: List[dict], tools: Optional[List[dict]] = None,
             json_mode: bool = False, purpose: str = "") -> Optional[dict]:
        if not self.enabled or not self._take(ticker, purpose):
            return None
        out = self.client.chat(messages, tools=tools, json_mode=json_mode)
        if out.get("error"):
            self._error(ticker, purpose, out["error"])
        self.audit.log("llm_call", ticker=ticker, purpose=purpose, tool_calls=[c["name"] for c in out["tool_calls"]],
                       empty=not (out["content"] or out["tool_calls"]), tokens=out.get("usage"))
        return out

    def json(self, ticker: str, system: str, user: str, purpose: str) -> Optional[dict]:
        if hasattr(self.client, "run_json"):                    # the LangChain chain (prompt | model | parser)
            if not self.enabled or not self._take(ticker, purpose):
                return None
            data, status = self.client.run_json(system, user)
            if status == "error":
                self._error(ticker, purpose, getattr(self.client, "last_error", "") or "unknown error")
            self.audit.log("llm_call", ticker=ticker, purpose=purpose, tool_calls=[], empty=data is None, chain="json")
            if status == "bad_json":
                self.audit.log("llm_bad_json", ticker=ticker, purpose=purpose)
            return data
        out = self.chat(ticker, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        json_mode=True, purpose=purpose)
        if not out or not out["content"]:
            return None
        text = re.sub(r"^```(?:json)?|```$", "", out["content"].strip(), flags=re.M).strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except ValueError:
            self.audit.log("llm_bad_json", ticker=ticker, purpose=purpose)
            return None
