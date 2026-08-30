"""Dual translation provider (Claude + OpenAI) with a single interface.

Modernized replacement for the old `ai_translator.AITranslator`:
- Claude parsing is block-safe (`_response_text` skips thinking/tool_use blocks,
  so a thinking-first response never breaks it).
- No `temperature` on Claude — current models (Opus 4.7/4.8/5, Sonnet 5) reject
  it with a 400, and translation doesn't need it. OpenAI keeps a low temperature
  for non-reasoning models; the o-series (reasoning) drops it and uses
  `max_completion_tokens`.
- `max_tokens` defaults high enough that a full batch of segments isn't
  truncated.
"""
from __future__ import annotations

import openai
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential


def _response_text(response) -> str:
    """Concatenate every text block in a Claude Messages response, skipping
    non-text blocks (thinking, tool_use)."""
    parts = []
    for block in (getattr(response, "content", None) or []):
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


def _is_openai_reasoning(model: str) -> bool:
    """o1/o3/o4-family reasoning models: no temperature, use max_completion_tokens."""
    m = (model or "").lower()
    return m.startswith(("o1", "o3", "o4")) or m.startswith("gpt-5")


def normalize_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p in ("anthropic", "claude"):
        return "Anthropic"
    if p in ("openai", "gpt"):
        return "OpenAI"
    raise ValueError(f"Unknown provider: {provider!r}")


class TranslationProvider:
    """One translate_batch(prompt) -> (text, total_tokens) interface for both SDKs."""

    def __init__(self, provider: str, api_key: str, model: str, max_tokens: int = 8192):
        self.provider = normalize_provider(provider)
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

        if self.provider == "OpenAI":
            self.client = openai.OpenAI(api_key=api_key)
        else:
            self.client = anthropic.Anthropic(api_key=api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def translate_batch(self, prompt: str):
        """Send one prompt, return (text, total_tokens). Retries transient errors."""
        if self.provider == "OpenAI":
            return self._openai(prompt)
        return self._anthropic(prompt)

    def _openai(self, prompt: str):
        messages = [
            {"role": "system", "content": "You are a professional translation engine. Follow the instructions exactly and return only what is asked."},
            {"role": "user", "content": prompt},
        ]
        kwargs = {"model": self.model, "messages": messages}
        if _is_openai_reasoning(self.model):
            kwargs["max_completion_tokens"] = self.max_tokens
        else:
            kwargs["max_tokens"] = self.max_tokens
            kwargs["temperature"] = 0.1
        response = self.client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content or ""
        tokens = getattr(response.usage, "total_tokens", 0) if response.usage else 0
        return text, tokens

    def _anthropic(self, prompt: str):
        # No temperature — current Claude models reject it (400).
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = _response_text(response)
        usage = response.usage
        tokens = (usage.input_tokens + usage.output_tokens) if usage else 0
        return text, tokens
