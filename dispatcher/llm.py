"""LLM providers for the dispatcher: Anthropic Claude (SDK) or Google Gemini (REST, free tier works).

LLM_PROVIDER=anthropic|gemini picks one explicitly. Without it, the provider follows whichever key is set:
ANTHROPIC_API_KEY first, then GEMINI_API_KEY. With no key, the rule parser is used.
"""

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

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = os.environ.get("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta")

_client = None
_gemini_http = None


def provider() -> str | None:
    if os.environ.get("LLM_DISABLED") == "1":
        return None
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit in ("anthropic", "claude"):
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None
    if explicit in ("gemini", "google"):
        return "gemini" if os.environ.get("GEMINI_API_KEY") else None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def llm_available() -> bool:
    return provider() is not None


def model_name() -> str | None:
    return {"anthropic": DISPATCH_MODEL, "gemini": GEMINI_MODEL}.get(provider() or "")


def gemini_http():
    global _gemini_http
    if _gemini_http is None:
        import httpx

        _gemini_http = httpx.Client(timeout=120)
    return _gemini_http


def gemini_post(url: str, payload: dict, retries: int = 6):
    """POST to Gemini, waiting and retrying on 429/5xx (the free tier allows only a few requests per minute)."""
    import re
    import time

    for attempt in range(retries + 1):
        resp = gemini_http().post(url, headers={"x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")}, json=payload)
        if resp.status_code not in (429, 500, 503) or attempt == retries:
            return resp
        hint = re.search(r'"retryDelay":\s*"(\d+)', resp.text)
        wait = int(hint.group(1)) + 1 if hint else min(60, 5 * 2 ** attempt)
        print(f"[gemini] {resp.status_code}, waiting {wait} s (attempt {attempt + 1}/{retries})", flush=True)
        time.sleep(wait)
    return resp


def client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    if model.startswith("gemini"):
        return 0.0  # free tier; set your own price here if you use a paid Gemini plan
    pin, pout, pcache = PRICING.get(model, PRICING["claude-opus-5-5"])
    return (input_tokens * pin + output_tokens * pout + cache_read * pcache) / 1_000_000
