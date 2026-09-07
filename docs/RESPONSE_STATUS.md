# Response status — transport contract

`agent.respond()` returns a `Reply`, a `str` whose value is **only raw model text**.
Its `.status` is a per-turn snapshot: Compass heading, actual response model, latest
call's input-context count and known capacity. `json.dumps(reply)` remains raw text.
Streaming tokens remain raw; the final `done` value carries the receipt.

Human-facing adapters MUST render `present(reply)` (Markdown/plain transport) or
render `status_of(reply)` separately. HTTP exposes **raw** `response`/`text`/`v` plus
`display_response`/`display_text`/`display_v` and `status`. History similarly keeps
raw `text` plus `display_text`. Agent-to-agent callers consume the raw field only.
Never feed a display field into a model, journal, prompt trace or memory miner.
Do not generate the strip in an LLM instruction. No provider calls or discovery
requests are made for this feature; it adds zero inference tokens.

Usage is the latest call, not the accumulated turn or daily usage. Gemini's input
already includes cached tokens; other normalized providers add cache-read/write
once. Missing usage and unknown model capacity remain unavailable, not zero or a
fallback 200k denominator. Model response IDs take precedence over configured IDs.
Concurrent turns use ContextVar state, never a shared last_usage read at delivery.

`debug/response-status.json` holds at most 2000 hash-to-receipt entries, outside
config/memory and private mind export, for same-device history restoration. It
contains no answer text. Old messages with no receipt (or ambiguous identical text)
show unavailable metadata rather than today's values. Moving the mind without its
diagnostics means those historic badges are unavailable; the memory itself is intact.

Apply the adapter contract to every new transport. Backend tests cover raw history,
concurrency, fallback, missing usage, cache accounting and reload receipts. Existing
human-facing transports include Tower, Discord, scheduled messages and, where shipped,
Edge streaming/history/push. Errors and ordinary UI controls are not model answers.
