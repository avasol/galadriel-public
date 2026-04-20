"""Translate Anthropic API exceptions into short, human-readable Discord messages.

Returns None if the exception isn't a recognized Anthropic API error — callers
should fall back to their existing generic message in that case.
"""

import anthropic


def humanize_anthropic_error(exc: Exception) -> str | None:
    if isinstance(exc, anthropic.APITimeoutError):
        return (
            "⏳ The request to Anthropic timed out before I could answer. "
            "Nothing was wrong with your message — try again in a moment."
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return (
            "🔌 I couldn't reach the Anthropic API. "
            "Likely a network hiccup on my side — try again shortly."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return (
            "🐌 We're going a bit too fast for Anthropic's rate limits. "
            "Give it 30–60 seconds and I'll be ready again."
        )
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "🔑 My API key was rejected. `ANTHROPIC_API_KEY` needs attention — "
            "I can't recover from this on my own."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return (
            "🚫 Anthropic denied the request — usually a workspace/org "
            "permissions issue, or the model isn't enabled on this key."
        )
    if isinstance(exc, anthropic.NotFoundError):
        return (
            "❓ Anthropic says that resource doesn't exist. The model name may "
            "have been retired — check `AGENT_MODEL` in the environment."
        )
    if _is_overloaded(exc):
        return (
            "🌋 Claude's API is overloaded right now. "
            "Not your fault, not mine — just the servers. Retry in a minute."
        )
    if isinstance(exc, anthropic.InternalServerError):
        return (
            f"💥 Anthropic's servers threw HTTP {getattr(exc, 'status_code', '5xx')}. "
            "Already logged — try again in a moment."
        )
    if isinstance(exc, anthropic.BadRequestError):
        detail = _extract_api_message(exc)
        return (
            f"⚠️ The API rejected my request as malformed:\n`{detail}`\n"
            "If this repeats, `/compact` or `/new` usually clears it."
        )
    if isinstance(exc, anthropic.APIStatusError):
        return (
            f"⚠️ Anthropic returned HTTP {getattr(exc, 'status_code', '?')}: "
            f"`{_extract_api_message(exc)}`"
        )
    return None


def _is_overloaded(exc: Exception) -> bool:
    overloaded_cls = getattr(anthropic, "OverloadedError", None)
    if overloaded_cls is not None and isinstance(exc, overloaded_cls):
        return True
    if isinstance(exc, anthropic.APIStatusError) and getattr(exc, "status_code", None) == 529:
        return True
    return False


def _extract_api_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or body
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
    return str(exc)[:300]
