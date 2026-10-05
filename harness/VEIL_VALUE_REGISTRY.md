# THE VEIL — Value Registry (design spec)

*Status: SPEC ONLY — not built. Parity test first, hot path, needs the maintainer's
go and a reviewer's review against her probe. Drafted 2026-09-30 in answer to
a reviewer's finding that the pattern set cannot catch a secret echoed with no
NAME= context.*

---

## The problem, in one line

**The Veil recognises secrets by shape and by name. A secret echoed with
neither — a bare value inside code, a default argument, an env var expanded by
the shell — is invisible to it, no matter how many patterns we add.**

Three observed cases (all reproduced):

| Case | Shape | Why patterns miss it |
|---|---|---|
| `CARTO_OVERPASS_TOKEN` echoed alone | bare 24-char value | no `aedk_`-style prefix, no `NAME=` context |
| relay `EDGE_TOKEN` default | `os.environ.get("EDGE_TOKEN", "<64-hex>")` | the value sits inside code as a default arg |
| `grep -c` / a count / a fragment | value with no surrounding NAME | no anchor to key on |

Adding patterns will never close this class. The value is high-entropy but
*arbitrary* — there is nothing in it to recognise. What we DO know, and the
pattern engine cannot, is **the exact secret values this process already holds.**

## The design

### 1. Source — build a registry of the values the process already owns

At agent boot, collect the exact secret *values* the body is already trusted
with, from the two places they legitimately live:

- **`.env`** — only values whose NAME matches the existing secret-name heuristic
  (SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|AEDK). Do **not**
  slurp every value; a registry of non-secrets causes false redactions.
- **The keyring** — the unsealed ring (provider + capability keys), the same
  values `harness.keyring.load` / `local_keyring.load` already return.

Optional, and worth debating in review:

- **Session-learned values.** A secret caught by *pattern* anywhere during the
  session is remembered, and matched by *value* everywhere after. This is the
  elegant closure: the first sighting is by shape, every later one by value.
  Risk: unbounded growth. Mitigation: cap (e.g. 256 entries, FIFO).

### 2. Matching — a second pass, by exact value

```python
redact_secrets(text_or_blocks, registry=None) -> (clean, hits)
```

- The **pattern pass runs unchanged** (first), for shape/name coverage.
- Then, **if a registry is present**, a second pass replaces every exact
  occurrence of a registered value with `<REDACTED:known:fp6>`, where `fp6` is
  `sha256(value)[:6]` — the SAME fingerprint the pattern path would produce, so
  a reader can correlate a value across markers.
- Match **longest value first** (a secret that is a substring of another must
  not be half-replaced).
- Enforce a **minimum value length (≥16)** so short config values (`true`,
  `localhost`, `8080`) are never registered and never redacted.

### 3. Safety — the registry must never itself leak

This is the load-bearing rule. A registry of live secrets is a target.

- **Process memory only.** Never written to disk, never in an export, never in
  a tool result, never in the journal, never in a debug dump.
- **Never logged by value.** Logging is names + `fp6` only — the same discipline
  the pattern path already follows.
- **Never emitted.** No tool, no endpoint, no status panel may enumerate it.
- **Fail-safe.** Any error building it → empty registry, `debug` log, never
  raise. The Veil must never break a live turn (an unreadable `.env` is not a
  reason to refuse a reply).

### 4. Hot-path discipline

- The registry is built **once at boot** (bounded: a handful of values).
- Matching is a linear scan over the text per registered value — negligible.
- **When the registry is empty, behaviour is byte-identical to today.** This
  makes the parity test trivial and mandatory: an empty registry must produce
  the exact same output as the current `redact_text` on a corpus of tool
  results, byte for byte.

### 5. Integration point

`harness/agent.py` — the single Veil choke point:

```python
# before
result, _veil = redact_secrets(result)
# after
result, _veil = redact_secrets(result, registry=self._secret_values)
```

The registry is built in the Agent's init (or lazily on first tool result),
from `.env` + the unsealed ring. **Rebuild on keyring write** (a user adding a
key on `/keys` mid-session must have it shielded from the next tool result on).

## Test plan (parity first)

1. **PARITY (mandatory):** empty registry → output byte-identical to
   `redact_text` on a corpus of tool results. This is the gate.
2. `CARTO_OVERPASS_TOKEN` bare value, seeded → caught.
3. `os.environ.get("EDGE_TOKEN", "<hex>")` → caught (value inside code).
4. A registered value embedded in a longer string → caught.
5. A short value (`true`, 4 chars) → **not** registered, **not** redacted.
6. A secret that is a substring of another → longest-first, no partial.
7. **Registry never leaks:** assert no log line, no marker, no returned text
   contains a registered value verbatim after a redaction pass.
8. Same value → same `fp6` whether caught by pattern or by registry.

## Open questions for review

1. **Source set** — `.env`-named + ring only, or also session-learned values
   (with a cap)? I lean *both*: session-learning is what closes the class.
2. **Minimum length** — 16? Too short risks redacting a coincidental id; too
   long risks missing a 16-char token.
3. **Rebuild trigger** — on keyring write only, or also on `.env` mtime change?
4. **Cost** — is a boot-time read of `.env` + ring acceptable on every wake, or
   should it be lazy (first tool result)?

---

*SPEC ONLY. Nothing here is built. The parity test comes first; the empty
registry path must be provably inert. An independent review checks it against a probe; the
maintainer holds the go.*
