# Security Policy

Aedelgard is a memory you own. Its whole value rests on trust, so we treat
security reports as first-class work, not a compliance afterthought.

## Reporting a vulnerability

Please report privately, before public disclosure:

- **Email:** security@aedelgard.com
- Encrypt if you wish — ask in a first, contentless email and we'll exchange a key.

Include, where you can: what you found, how to reproduce it, the affected
component (engine / cloud broker / desktop body), and its impact. A minimal
proof-of-concept helps us move faster.

**What to expect**

| Stage | Target |
| --- | --- |
| Acknowledgement of your report | within 3 business days |
| Initial assessment + severity | within 7 business days |
| Fix or mitigation plan | communicated as soon as it's understood |

We will keep you updated, credit you if you wish (or honour a request to stay
anonymous), and we will not pursue good-faith researchers who follow this policy.

## Scope

In scope:

- **The open engine** (`avasol/galadriel-public`) — the memory palace, temporal
  knowledge graph, agent loop, and desktop **body**.
- **The cloud broker** (`hq.aedelgard.com`) — tenant isolation, the user-key
  vault, device-token auth, and the palace sync endpoints.
- **The site** (`aedelgard.com`) — auth flows, key handling, checkout.

Especially wanted: tenant-isolation escapes (one tenant reaching another's
palace), device-token forgery, vault-at-rest weaknesses, and anything that
would let plaintext leave a user's machine when they run the local body.

Out of scope: volumetric DoS, reports requiring a rooted/compromised host,
social engineering, and findings in third-party processors (Anthropic, Stripe,
AWS) — report those to the processor.

## The honest trust boundary (so your testing targets the real thing)

We state this plainly on [aedelgard.com/architecture](https://aedelgard.com/architecture)
and repeat it here because it is the truth your threat model should assume:

- **The local body** is **operator-blind by construction** — *Aedelgard* cannot
  read it. Memory lives on the user's disk; the cloud can back it up and relay it,
  but only ever as ciphertext sealed by a key derived from the user's own Aedelgard
  key. Prompts, however, go wherever the *thinking* happens: with your own
  provider key (Anthropic, Google, OpenAI) every turn's full prompt is sent to
  that vendor under their terms — we are not in that path, but they are. Only
  with a **local model** does plaintext never leave the machine at all. The
  "not as policy, but as physics" claim is true of *our* blindness in every body
  mode, and of *everyone's* blindness only in local-model mode.
- **The hosted cloud broker** does not run inference, does not accept provider keys,
  and does not decrypt memory in-flight. It serves strictly as a device-token
  authentication door and a blind relay/backup vault for client-sealed AES-256-GCM
  ciphertext. The vault key is derived client-side via HKDF-SHA256 from the user's
  Aedelgard key; the broker stores only a one-way verification hash of the key
  and never holds the plaintext vault key or HKDF derivation parameters. Even
  under full server compromise or live operator memory dumps, the broker holds only
  opaque ciphertext.

> **Architectural transition note (updated 8 September 2026):**
> Earlier development phases evaluated a hosted broker-inference mode where the
> broker accepted provider credentials and decrypted memory during request execution.
> That architecture was formally retired in September 2026 in favour of strictly
> direct client-to-provider inference (or local models). The broker no longer accepts
> provider keys, does not decrypt context in-flight, and operates exclusively as an
> encrypted sync and relay service.

If a claim anywhere in the product contradicts
[PRIVACY_STATEMENT.md](https://aedelgard.com/privacy), the claim is the bug —
tell us.

## Artifact integrity

Desktop body releases publish SHA256 checksums at
[aedelgard.com/checksums](https://aedelgard.com/checksums) and on each GitHub
release. Verify before you install a key-handling app.

---

*Aedelgard is a service of Millenion AB (org.nr 556887-8697), Sweden.*
