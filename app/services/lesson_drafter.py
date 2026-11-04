"""Asks Claude for the empty parts of a lesson plan, as JSON that matches a schema."""

import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# A draft is a few hundred words; adaptive thinking needs room on top of that.
MAX_TOKENS = 16000
# The phone waits 80 seconds for a draft. No retries: a retried timeout would
# outlast the phone, and the teacher can just tap again.
TIMEOUT_SECONDS = 70.0

_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(timeout=TIMEOUT_SECONDS, max_retries=0)
    return _client


@dataclass
class Drafted:
    parts: dict[str, str]
    input_tokens: int
    output_tokens: int


class DraftError(Exception):
    """A draft that didn't happen, with the message for the teacher and how to log it."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        outcome: str = "failed",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.outcome = outcome
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


NOT_SET_UP = "AI drafting isn't set up on the server yet. Ask your school's admin."
BUSY = "The AI service is busy or unreachable. Try again in a minute."


async def draft_parts(model: str, system: str, prompt: str, schema: dict[str, object]) -> Drafted:
    try:
        message = await _anthropic().beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=system,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        logger.error("lesson drafts: the AI service rejected our credentials: %s", exc)
        raise DraftError(NOT_SET_UP) from exc
    except anthropic.BadRequestError as exc:
        # Usually billing (no credit) or the key's workspace; the log has the reason.
        logger.error("lesson drafts: request rejected: %s", exc)
        raise DraftError(NOT_SET_UP) from exc
    except anthropic.RateLimitError as exc:
        logger.warning("lesson drafts: rate limited: %s", exc)
        raise DraftError(BUSY) from exc
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        logger.warning("lesson drafts: AI service unavailable: %s", exc)
        raise DraftError(BUSY) from exc
    except (anthropic.AnthropicError, TypeError) as exc:
        # The SDK raises TypeError when no credentials are configured at all.
        logger.error("lesson drafts: AI client error: %s", exc)
        raise DraftError(NOT_SET_UP) from exc

    tokens_in = message.usage.input_tokens or 0
    tokens_out = message.usage.output_tokens or 0
    if message.stop_reason == "refusal":
        raise DraftError(
            "This lesson couldn't be drafted with AI. Write it yourself.",
            status_code=422,
            outcome="refused",
            input_tokens=tokens_in,
            output_tokens=tokens_out,
        )
    if message.stop_reason == "max_tokens":
        logger.warning("lesson drafts: hit max_tokens (%s out)", tokens_out)
        raise DraftError(BUSY, input_tokens=tokens_in, output_tokens=tokens_out)

    text = "".join(block.text for block in message.content if block.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("lesson drafts: reply wasn't JSON: %.200s", text)
        raise DraftError(BUSY, input_tokens=tokens_in, output_tokens=tokens_out) from exc
    if not isinstance(data, dict):
        raise DraftError(BUSY, input_tokens=tokens_in, output_tokens=tokens_out)
    return Drafted(
        parts={k: v for k, v in data.items() if isinstance(v, str)},
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )
