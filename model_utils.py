"""Live model discovery for the dual-provider dropdown.

Claude: list the account's models via the Anthropic Models API, filtered to
current generation (Claude 3.x and older hidden). OpenAI: list models filtered
to chat/reasoning families relevant to translation. Both fall back to a small
curated current list when the API can't be reached.
"""
from __future__ import annotations

import re
from typing import List

import anthropic
import openai

# ---- Anthropic (Claude) ----
CLAUDE_FALLBACK = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"]
_CLAUDE_PREFERRED = ["claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-4-6", "claude-haiku-4-5"]
_CLAUDE_LEGACY = re.compile(r"^claude-(?:instant|[0-3])[.\-]")
_CLAUDE_LABELS = {
    "claude-sonnet-4-6": "Claude Sonnet 4.6 — best value (recommended)",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-opus-4-8": "Claude Opus 4.8 — most capable",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-haiku-4-5": "Claude Haiku 4.5 — fast & budget",
    "claude-fable-5": "Claude Fable 5",
}

# ---- OpenAI ----
OPENAI_FALLBACK = ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "o4-mini"]
_OPENAI_PREFERRED = ["gpt-4o", "gpt-4.1", "gpt-5", "gpt-4o-mini"]
_OPENAI_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4")
_OPENAI_NOISE = ("embedding", "whisper", "tts", "dall", "audio", "realtime",
                 "moderation", "image", "transcribe", "search", "instruct")


def is_current_claude(model_id: str) -> bool:
    return not _CLAUDE_LEGACY.match(model_id)


def list_claude_models(api_key: str) -> List[str]:
    if not api_key:
        return []
    try:
        client = anthropic.Anthropic(api_key=api_key)
        models = list(client.models.list())
        models.sort(key=lambda m: str(getattr(m, "created_at", "") or ""), reverse=True)
        return [m.id for m in models if getattr(m, "id", None) and is_current_claude(m.id)]
    except Exception:
        return []


def _openai_relevant(model_id: str) -> bool:
    mid = model_id.lower()
    if any(n in mid for n in _OPENAI_NOISE):
        return False
    return mid.startswith(_OPENAI_PREFIXES)


def list_openai_models(api_key: str) -> List[str]:
    if not api_key:
        return []
    try:
        client = openai.OpenAI(api_key=api_key)
        ids = [m.id for m in client.models.list().data if getattr(m, "id", None)]
        return sorted(mid for mid in ids if _openai_relevant(mid))
    except Exception:
        return []


def list_models(provider: str, api_key: str) -> List[str]:
    p = (provider or "").lower()
    if p in ("anthropic", "claude"):
        return list_claude_models(api_key) or CLAUDE_FALLBACK
    return list_openai_models(api_key) or OPENAI_FALLBACK


def default_model(provider: str, ids: List[str]) -> str:
    preferred = _CLAUDE_PREFERRED if (provider or "").lower() in ("anthropic", "claude") else _OPENAI_PREFERRED
    for pref in preferred:
        if pref in ids:
            return pref
    return ids[0] if ids else (CLAUDE_FALLBACK[0])


def display_name(model_id: str) -> str:
    return _CLAUDE_LABELS.get(model_id, model_id)
