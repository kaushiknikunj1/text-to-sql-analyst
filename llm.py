"""Minimal LLM layer with a deterministic fallback.

Provider order:
  1. Gemini      - set GEMINI_API_KEY     (REST, no SDK needed)
  2. Claude      - set ANTHROPIC_API_KEY  (official `anthropic` SDK)
  3. None        - caller uses its own template; the demo still runs end to end

Set LLM_PROVIDER=gemini|anthropic|none to force one.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")


def provider() -> str:
    forced = os.environ.get("LLM_PROVIDER", "").lower()
    if forced in ("gemini", "anthropic", "none"):
        return forced
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "none"


def complete(system: str, user: str, max_tokens: int = 700) -> str | None:
    """Return model text, or None when no provider is configured."""
    p = provider()
    if p == "gemini":
        return _gemini(system, user, max_tokens)
    if p == "anthropic":
        return _anthropic(system, user, max_tokens)
    return None


def _gemini(system: str, user: str, max_tokens: int) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}")
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic  # official SDK; reads ANTHROPIC_API_KEY

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},   # routes a policy decline to a fallback model
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Number pinning: the model may only use numbers we handed it.
# ---------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUM.findall(text):
        s = m.replace(",", "")
        try:
            v = float(s)
        except ValueError:
            continue
        out.add(_norm(v))
    return out


def _norm(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def allowed_numbers(evidence: dict) -> set[str]:
    """Every number in the evidence dict, plus its rounded forms."""
    allowed = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            for v in (x, round(x), round(x, 1), round(x, 2)):
                allowed.add(_norm(float(v)))
            if abs(x) < 1.5:  # a rate: also allow the % form
                for v in (x * 100, round(x * 100), round(x * 100, 1)):
                    allowed.add(_norm(float(v)))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            allowed.update(numbers_in(x))

    walk(evidence)
    return allowed


def unpinned(text: str, evidence: dict) -> set[str]:
    """Numbers the model wrote that are not in the evidence. Empty set = clean."""
    return numbers_in(text) - allowed_numbers(evidence)
