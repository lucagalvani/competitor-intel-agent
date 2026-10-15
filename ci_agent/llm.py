"""Thin wrapper around the Claude API.

Three kinds of calls are used by the agent:
  - text():     plain prompt -> markdown answer (battle cards, digests, Q&A)
  - json():     prompt -> JSON that matches a schema (change classification)
  - research(): prompt -> answer, with Claude's built-in web search tool
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from .config import LLMConfig

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side `fallbacks` parameter
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
MAX_TOKENS = 32000


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: LLMConfig, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()

    # ── public helpers ──────────────────────────────────────────

    def text(self, system: str, prompt: str) -> str:
        msg = self._call(system, [{"role": "user", "content": prompt}])
        return _join_text(msg)

    def json(self, system: str, prompt: str, schema: dict) -> dict:
        msg = self._call(
            system,
            [{"role": "user", "content": prompt}],
            output_format={"type": "json_schema", "schema": schema},
        )
        try:
            return json.loads(_join_text(msg))
        except json.JSONDecodeError as e:
            raise LLMError(f"Claude returned invalid JSON: {e}") from e

    def research(self, system: str, prompt: str) -> str:
        """Let Claude search the web, then return its final written answer."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for _ in range(5):
            msg = self._call(system, messages, tools=[WEB_SEARCH_TOOL])
            # Long searches can pause mid-turn; send the partial turn back to let it continue
            if msg.stop_reason != "pause_turn":
                return _join_text(msg)
            messages = [messages[0], {"role": "assistant", "content": msg.content}]
        raise LLMError("web research did not finish after 5 continuation rounds")

    # ── the single place that talks to the API ──────────────────

    def _call(self, system: str, messages: list, tools: list | None = None, output_format: dict | None = None):
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
            "output_config": {"effort": self.cfg.effort},
        }
        if output_format:
            kwargs["output_config"]["format"] = output_format
        if tools:
            kwargs["tools"] = tools
        if self.cfg.fallbacks and self.cfg.model in FALLBACK_MODELS:
            # If the model declines a request, the API retries it on a fallback model
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            # Streaming avoids HTTP timeouts on long answers; we just wait for the final message
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Claude API authentication failed: set ANTHROPIC_API_KEY") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API rate limit hit; try again later or lower the number of sources") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"could not reach the Claude API: {e}") from e

        if msg.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        if msg.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off (max_tokens reached)")
        return msg


def _join_text(msg) -> str:
    return "".join(block.text for block in msg.content if block.type == "text").strip()
