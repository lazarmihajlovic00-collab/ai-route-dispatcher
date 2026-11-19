"""Thin wrapper around the Anthropic SDK: availability, model choice, pricing."""

from __future__ import annotations

import os

# The dispatcher is a multi-step tool-using agent, so it runs on the strong model by default.
DISPATCH_MODEL = os.environ.get("DISPATCH_MODEL", "claude-opus-5-5")
DISPATCH_EFFORT = os.environ.get("DISPATCH_EFFORT", "medium")

# USD per 1M tokens: (input, output, cache read). Anthropic list prices, Nov 2026.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

_client = None


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("LLM_DISABLED") != "1"


def client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    pin, pout, pcache = PRICING.get(model, PRICING["claude-opus-5-5"])
    return (input_tokens * pin + output_tokens * pout + cache_read * pcache) / 1_000_000
