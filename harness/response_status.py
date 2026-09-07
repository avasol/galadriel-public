"""Presentation-only reply metadata. Never append this to model/journal content.

All transports must call present(reply), or render reply.status separately.
Reply is a plain-text-compatible str: serialization/replay sees ONLY the answer.
Per-turn ContextVar state prevents concurrent channels borrowing each other's usage.
No network requests, inference calls, catalogue discovery or prompt mutations.
"""
from contextvars import ContextVar
from functools import wraps
from datetime import datetime, timezone
import os
import sys
import json
import hashlib
from pathlib import Path

_turn = ContextVar('response_status_turn', default=None)


class Reply(str):
    def __new__(cls, text, status):
        obj = super().__new__(cls, text)
        obj.status = dict(status)
        return obj


def heading(agent):
    try:
        # The live corridor takes precedence over the legacy project stack.
        from . import compass
        current = compass.current()
        if current:
            return current.get('name') or current.get('slug') or current.get('corridor') or 'Unheaded'
    except (ImportError, AttributeError, TypeError, OSError, ValueError):
        pass
    try:
        return agent.memory._active_project_name() or 'Unheaded'
    except (AttributeError, TypeError):
        return 'Unheaded'


def snapshot(agent):
    return {'heading': heading(agent), 'model': getattr(agent, 'model', None),
            'model_source': 'configured', 'context_tokens': None,
            'context_window': None, 'measured_at': None, 'measurement': 'unavailable'}


def _window(agent, model):
    # Never use _resolve_context_window's guessed default, nor trigger discovery.
    module = sys.modules.get(type(agent).__module__)
    for name in ('_DISCOVERED_CONTEXT_WINDOWS', 'CONTEXT_WINDOW_OVERRIDES'):
        value = getattr(module, name, {}).get(str(model).lower())
        if isinstance(value, int) and value > 0:
            return value
    explicit = os.environ.get('AGENT_CONTEXT_WINDOW', '')
    if model == getattr(agent, 'model', None) and explicit.isdigit() and int(explicit) > 0:
        return int(explicit)
    return None


def record(agent, response):
    """Read the completed call's usage, not the shared agent.last_usage.

    Gemini promptTokenCount already includes cached tokens; Anthropic and the
    OpenAI normalized seam report uncached input separately. Never double count.
    """
    state = _turn.get()
    if state is None:
        return
    provider = getattr(agent, 'provider', None)
    provider = getattr(provider, '_last_used', None) or provider
    model = getattr(response, 'model', None)
    if not model:
        ladder = getattr(agent, 'provider', None)
        rungs = getattr(ladder, '_rungs', [])
        model = (rungs[ladder._active].model if rungs else None) or getattr(agent, 'model', None)
    state.update(heading=heading(agent), model=model, model_source='response' if getattr(response, 'model', None) else 'request',
                 context_window=_window(agent, model), context_tokens=None,
                 measured_at=None, measurement='unavailable')
    if getattr(response, "status_usage_available", True) is False:
        return
    usage = getattr(response, 'usage', None)
    inp = getattr(usage, 'input_tokens', None)
    if not isinstance(inp, int) or isinstance(inp, bool) or inp < 0:
        return
    tokens = inp
    if not str(model).lower().startswith('gemini') and getattr(provider, 'name', '') != 'gemini':
        for attr in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
            value = getattr(usage, attr, 0) or 0
            if isinstance(value, int) and value >= 0:
                tokens += value
    state.update(context_tokens=tokens, measurement='measured',
                 measured_at=datetime.now(timezone.utc).isoformat())


def status_of(reply, agent=None):
    if isinstance(reply, Reply):
        return dict(reply.status)
    if agent is not None:
        receipt = _receipts(agent).get(_digest(str(reply)))
        if receipt:
            return dict(receipt)
    # Historic text without a receipt must not acquire today's model or usage.
    return {'heading': '—', 'model': None, 'model_source': 'unavailable',
            'context_tokens': None, 'context_window': None,
            'measured_at': None, 'measurement': 'unavailable'}


def _label(value):
    # Metadata can contain user-authored headings. No mentions, markup or lines.
    text = ' '.join(str(value or '—').split())
    for char in '`*_[]<>@|':
        text = text.replace(char, '')
    return text[:90] or '—'


def strip(status):
    model = _label(status.get('model'))
    if status.get('model_source') == 'configured':
        model += ' (configured)'
    tokens = status.get('context_tokens')
    window = status.get('context_window')
    ctx = '—'
    if isinstance(tokens, int):
        ctx = f'{tokens:,} tok'
        if isinstance(window, int) and window > 0:
            ctx = f'{tokens:,} / {window:,} · {100*tokens/window:.1f}%'
    return f'🧭 **{_label(status.get("heading"))}**  ·  🧠 **{model}**  ·  ◔ **Ctx {ctx}**'


def present(reply, agent=None):
    """Only at an outbound presentation boundary, AFTER history/journaling."""
    if not reply or not str(reply).strip():
        return str(reply or '')
    return strip(status_of(reply, agent)) + '\n\n' + str(reply)


def with_status(fn):
    @wraps(fn)
    async def wrapped(self, *args, **kwargs):
        state = snapshot(self)
        token = _turn.set(state)
        try:
            text = await fn(self, *args, **kwargs)
            state['heading'] = heading(self)
            remember(self, text, state)
            return Reply(text, state)
        finally:
            _turn.reset(token)
    return wrapped


def current_status(agent):
    return dict(_turn.get() or snapshot(agent))


def with_stream_status(fn):
    @wraps(fn)
    async def wrapped(self, *args, **kwargs):
        state = snapshot(self)
        token = _turn.set(state)
        try:
            yield 'status', snapshot(self)
            async for kind, value in fn(self, *args, **kwargs):
                if kind == 'done':
                    state['heading'] = heading(self)
                    value = value if isinstance(value, Reply) else Reply(value, state)
                    remember(self, value, value.status)
                yield kind, value
        finally:
            _turn.reset(token)
    return wrapped


def _digest(text):
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()


def _receipt_path(agent):
    # Outside memory/ and config/: never mined, replayed or injected.
    return Path(agent.memory.memory_dir).parent / 'debug' / 'response-status.json'


def _receipts(agent):
    if not hasattr(agent, '_response_receipts'):
        try:
            agent._response_receipts = json.loads(_receipt_path(agent).read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError, TypeError):
            agent._response_receipts = {}
    return agent._response_receipts


def remember(agent, text, status):
    if not text:
        return
    records = _receipts(agent)
    key = _digest(str(text))
    # Identical historical answers can have distinct receipts; do not misattribute.
    if key in records and (records[key] is None or records[key] != status):
        records[key] = None
    else:
        records[key] = dict(status)
    while len(records) > 2000:
        records.pop(next(iter(records)))
    try:
        path = _receipt_path(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(records), encoding="utf-8")
        tmp.replace(path)
    except (OSError, AttributeError, TypeError):
        pass  # Presentation must never lose the answer on a read-only disk.
