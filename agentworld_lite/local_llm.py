"""Local open-weight models through Ollama (free, runs on your own GPU/CPU).

Same roles as the Claude path:
  OllamaClient.json_call(...)  -> drop-in for ClaudeClient.json_call (CCE judge, failure taxonomy)
  OllamaAgent                  -> drop-in for LLMAgent (one tool call per turn)

Uses Ollama's native REST API (POST /api/chat, GET /api/tags) with the standard library only.
Small models sometimes write the tool call as JSON text instead of a structured call; that text is
parsed as a fallback and the decision is tagged note="text_tool_call".

Cloud models (names ending in ":cloud" or "-cloud") run on Ollama's servers but are still called
through the local server once you have run `ollama signin`. They are not listed by /api/tags and do
not support structured outputs, so json_call falls back to schema-in-prompt + JSON extraction.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from types import SimpleNamespace

from .agents import NUDGE, SYSTEM_PROMPT, BaseAgent, Decision, TurnInput
from .engine import TOOL_NAMES
from .llm import UsageMeter
from .tools import tool_specs

DEFAULT_HOST = "http://127.0.0.1:11434"  # "localhost" adds ~2s per call on Windows (IPv6 fallback)
DEFAULT_NUM_CTX = 8192  # prompts are ~3k tokens + ~1.5k of tool schemas; many models default lower


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, model: str, host: str = DEFAULT_HOST, num_ctx: int = DEFAULT_NUM_CTX,
                 temperature: float = 0.2, timeout: float = 600.0, think: bool | None = None) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout = timeout
        self.think = think
        self.usage = UsageMeter()

    # -- transport (overridden in tests)
    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.host + path, data=data, method="POST" if payload is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise OllamaError(f"Ollama returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.host} ({exc.reason}). Is the Ollama app running?") from exc

    @property
    def is_cloud(self) -> bool:
        return self.model.endswith(":cloud") or self.model.endswith("-cloud")

    def check(self) -> str:
        """Verify the server is up and the model is pulled; returns the model's parameter size."""
        tags = self._request("/api/tags")  # also proves the local server is reachable
        if self.is_cloud:
            return "cloud model; requires `ollama signin`"
        names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
        wanted = {self.model, f"{self.model}:latest"}
        if not names & wanted:
            have = ", ".join(sorted(n for n in names if n)) or "none"
            raise OllamaError(f"Model '{self.model}' is not pulled. Run: ollama pull {self.model}  (installed: {have})")
        info = next(m for m in tags["models"] if m.get("name") in wanted or m.get("model") in wanted)
        return (info.get("details") or {}).get("parameter_size", "?")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, fmt: dict | str | None = None) -> tuple[dict, dict]:
        payload: dict = {
            "model": self.model, "messages": messages, "stream": False,
            "options": {"num_ctx": self.num_ctx, "temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        if fmt is not None:
            payload["format"] = fmt
        if self.think is not None:
            payload["think"] = self.think
        resp = self._request("/api/chat", payload)
        usage = self.usage.add(SimpleNamespace(input_tokens=resp.get("prompt_eval_count", 0),
                                               output_tokens=resp.get("eval_count", 0)), self.model)
        return resp, usage

    def json_call(self, system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> tuple[dict, dict]:
        """Structured output via `format`; for cloud models (no `format` support) or a malformed reply,
        retry once with the schema in the prompt and extract the JSON object from the text."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if not self.is_cloud:
            try:
                resp, usage = self.chat(messages, fmt=schema)
                return json.loads((resp.get("message") or {}).get("content", "")), usage
            except (OllamaError, json.JSONDecodeError):
                pass
        messages[1]["content"] = (prompt + "\n\nRespond with ONLY a JSON object that matches this JSON schema, "
                                  "no other text:\n" + json.dumps(schema))
        resp, usage = self.chat(messages)
        text = (resp.get("message") or {}).get("content", "")
        m = _JSON_OBJ.search(text)
        try:
            return json.loads(m.group(0) if m else text), usage
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Model did not return valid JSON: {text[:200]!r}") from exc


def to_ollama_tools(specs: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                              "parameters": s["input_schema"]}} for s in specs]


_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def parse_text_tool_call(text: str) -> tuple[str, dict] | None:
    """Fallback for models that print {"name": ..., "arguments": {...}} instead of a structured call."""
    m = _JSON_OBJ.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or (obj.get("function") or {}).get("name")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if name in TOOL_NAMES and isinstance(args, dict):
        return name, args
    return None


class OllamaAgent(BaseAgent):
    kind = "ollama"

    def __init__(self, name: str, client: OllamaClient) -> None:
        super().__init__(name)
        self.client = client

    def act(self, turn: TurnInput) -> Decision:
        tools = to_ollama_tools(tool_specs(turn.allowed_tools, strict=False))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": turn.context + "\n\n" + turn.observation},
        ]
        usage_total: dict = {}
        text = ""
        for attempt in range(2):
            resp, usage = self.client.chat(messages, tools=tools)
            for k, v in usage.items():
                usage_total[k] = usage_total.get(k, 0) + v
            msg = resp.get("message") or {}
            text = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            if calls:
                fn = calls[0].get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                note = "" if attempt == 0 else "needed_nudge"
                return Decision(fn.get("name", "wait"), dict(args), reasoning=text, usage=usage_total, note=note)
            parsed = parse_text_tool_call(text)
            if parsed:
                return Decision(parsed[0], parsed[1], reasoning=text, usage=usage_total, note="text_tool_call")
            messages = messages + [{"role": "assistant", "content": text}, {"role": "user", "content": NUDGE}]
        return Decision("wait", {"reason": "no tool call"}, reasoning=text, note="no_tool_call", usage=usage_total)
