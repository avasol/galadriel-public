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

- **The local body** is **operator-blind by construction.** Plaintext and prompts
  never leave the user's machine; the cloud can back it up and relay it, but only
  ever as ciphertext sealed by a key derived from the user's own Aedelgard key.
  We cannot read it — not as policy, but as physics.
- **The hosted cloud broker** is **operator-blind at rest, but not in-flight.**
  Memory and model keys are envelope-encrypted at rest and per-tenant isolated,
  but during a request the broker decrypts what it needs *in memory* to run
  inference. We do not log prompts, do not train on user data, and do not browse
  it — but a compromise of the running broker process could, in principle, see
  in-flight plaintext. We say so on the same page, in the same voice. If you find
  a way to make the at-rest guarantee false, that is a serious finding.

If a claim anywhere in the product contradicts
[PRIVACY_STATEMENT.md](https://aedelgard.com/privacy), the claim is the bug —
tell us.

## Artifact integrity

Desktop body releases publish SHA256 checksums at
[aedelgard.com/checksums](https://aedelgard.com/checksums) and on each GitHub
release. Verify before you install a key-handling app.

---

*Aedelgard is a service of Millenion AB (org.nr 556887-8697), Sweden.*
