# Extensions

An extension is a mind's own addition to the engine: text added to the prompt, routines on a
schedule, and (for code extensions) tools and hooks. Extensions live beside the memory, in
`<data root>/extensions/<name>/`, where the data root is the parent of the memory directory.
Nothing an extension contributes takes effect until it has been approved on this instance.

## Layout

```
extensions/
  trust.json            approvals on THIS instance (never copy it to another machine)
  placement.json        which instance is home for each once-per-mind routine
  <name>/
    extension.json      the manifest
    layers/*.md         prompt text
    routines.json       scheduled prompts
    extension.py        code extensions only: def register(ctx)
    data/               the extension's shared state (travels with the mind)
    local/              this machine's state only (never copied, never hashed)
```

## Manifest

```json
{
  "name": "rhythms", "version": "1.0.0", "title": "Rhythms", "description": "...",
  "kind": "declarative",
  "platforms": ["windows", "macos", "linux"],
  "contributes": {
    "layers":   [{"file": "layers/evening.md", "title": "Evening", "platforms": ["linux"]}],
    "routines": ["council"],
    "tools":    [],
    "hooks":    []
  },
  "permissions": [],
  "keyring_slots": [],
  "requires": []
}
```

- `name` matches the folder (`^[a-z0-9][a-z0-9-]{0,47}$`). `kind` is `declarative` or `code`.
- `platforms` (optional, default all three): on any other OS the extension is valid but
  inactive (`not_for_this_body`). Layers and routines may narrow it further.
- A declarative extension may not declare tools or hooks and may not contain `extension.py`.
- `requires`: Python modules that must be importable, or the extension is marked failed.
- `permissions`: today only `palace_write` (lets `ctx.palace_add` file memories).

## Routines

`routines.json` is a list of `{"id", "at": "HH:MM", "days": "daily"|"workdays", "prompt",
"scope": "mind"|"body", "platforms"}`. A routine fires as a turn on its own channel
(`ext:<name>`) with the prompt prefixed by `[SYSTEM:ROUTINE:<name>:<id>]`.

One mind may run on several instances. A `body` routine fires on every instance where the
extension is enabled. A `mind` routine (the default) fires on exactly one: its home, recorded
in `placement.json`. The first instance to approve the extension that can run the routine
claims it; any instance can take it over (`POST /api/extensions/<name>/routines/<id>/home`).
Known limit: two offline instances that both claim a home before they share `placement.json`
will both fire until they do. Routines added while running take effect at the next start.

## Instance identity

Each instance has a `body.json` in its data root: `{body_id, name, os, machine, root}`. A copy
of the data folder on another machine, or in another folder on the same machine, becomes a new
instance. `bodies.json` lists every instance of the mind. The prompt names the instance the mind
is running on and the others it lives on. Rename: `POST /api/body/name {"name": "desk"}`.

## Approval

- **Declarative:** approval sticks when the text changes (it is text the mind reads).
- **Code:** approval is pinned to a hash of every file in the extension except `data/`,
  `local/`, `__pycache__/` and `*.pyc`. Change one byte and it waits for approval again. The hash
  is re-checked immediately before the code is imported.
- `GET /api/extensions` lists every extension with its state (`enabled`, `awaiting_approval`,
  `disabled`, `failed`, `not_for_this_body`), error, prompt cost and routines.
  `POST /api/extensions/<name>/approve` and `/disable` change it and reload the runtime at once.

**Code extensions run with the same power as the engine itself. Approve only code you trust.**
The manifest's declarations are a contract the runtime enforces on the `ctx` API; they are not a
sandbox.

## Code extensions

```python
def register(ctx):
    def greet(inputs):
        return f"hello from {ctx.body['name']} ({ctx.body['os']})"
    ctx.tool("greet", {"description": "...", "input_schema": {"type": "object", "properties": {}}},
             greet, tier="green")
    ctx.hook("on_turn_end", lambda summary: ctx.log.info(summary["channel_id"]))
```

`ctx` offers: `name`, `body`, `data_dir`, `local_dir`, `log`, `tool(name, definition, handler,
tier)`, `hook(event, fn)`, `secret(slot)` (declared keyring slots only), `daily_log(text)`,
`palace_search(query, k)` and, with `palace_write`, `async palace_add(...)`.

- Registration is all-or-nothing: an exception in import or `register` loads nothing.
- Tools must be declared, may not shadow a built-in or another extension's tool, and pass through
  the same safety gate as built-in tools: a `red` tool asks for approval first.
- Tool calls time out after 120 s and return an error text instead of raising.
- Hooks: `on_boot`, `on_turn_end(summary)`, `on_goodnight`, `on_termination`. Each runs with a
  time limit (2 s for `on_turn_end` and `on_termination`, 10 s otherwise); three timeouts in a row
  disable the extension, with the reason shown.
