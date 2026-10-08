"""Thin wrapper over the Anthropic SDK: per-model request shaping, JSON calls, usage/cost metering."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-opus-5-5"

# USD per 1M tokens: (input, output, cache_read). Cache writes are billed at 1.25x input.
PRICING = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}

# effort: accepts output_config.effort. fallbacks: accepts server-side `fallbacks: "default"`.
MODEL_CAPS = {
    "claude-fable-5-1": {"effort": True, "fallbacks": True},
    "claude-opus-5-5": {"effort": True, "fallbacks": True},
    "claude-opus-5": {"effort": True, "fallbacks": True},
    "claude-sonnet-5-5": {"effort": True, "fallbacks": True},
    "claude-sonnet-5": {"effort": True, "fallbacks": False},
    "claude-haiku-4-5": {"effort": False, "fallbacks": False},
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMRefusal(RuntimeError):
    pass


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage, model: str) -> dict:
        d = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        p_in, p_out, p_cr = PRICING.get(model, (0.0, 0.0, 0.0))
        d["cost_usd"] = (d["input_tokens"] * p_in + d["output_tokens"] * p_out
                         + d["cache_read_input_tokens"] * p_cr
                         + d["cache_creation_input_tokens"] * p_in * 1.25) / 1e6
        with self._lock:
            self.calls += 1
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                setattr(self, k, getattr(self, k) + d[k])
            self.cost_usd += d["cost_usd"]
        return d

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("calls", "input_tokens", "output_tokens",
                                               "cache_read_input_tokens", "cache_creation_input_tokens", "cost_usd")}


class ClaudeClient:
    """One client per model/effort configuration. Thread-safe (the SDK client is)."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str | None = None, max_tokens: int = 8000,
                 use_fallbacks: bool = True, max_retries: int = 6, sdk_client=None) -> None:
        if sdk_client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise SystemExit('The Claude backend needs the Anthropic SDK: pip install -e ".[claude]" '
                                 "(or use --provider ollama for a free model)") from exc
            sdk_client = anthropic.Anthropic(max_retries=max_retries)
        self.sdk = sdk_client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        caps = MODEL_CAPS.get(model, {"effort": True, "fallbacks": False})
        self.supports_effort = caps["effort"]
        self.use_fallbacks = use_fallbacks and caps["fallbacks"]
        self.usage = UsageMeter()

    def create(self, **params):
        params.setdefault("max_tokens", self.max_tokens)
        params["model"] = self.model
        if self.effort and self.supports_effort:
            params["output_config"] = {**params.get("output_config", {}), "effort": self.effort}
        if self.use_fallbacks:
            resp = self.sdk.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            resp = self.sdk.messages.create(**params)
        resp_usage = self.usage.add(resp.usage, self.model)
        return resp, resp_usage

    def json_call(self, system: str, prompt: str, schema: dict, max_tokens: int = 16000) -> tuple[dict, dict]:
        """Structured-output call; returns (parsed_json, usage)."""
        resp, usage = self.create(
            system=system,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise LLMRefusal(getattr(resp, "stop_details", None))
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text), usage
