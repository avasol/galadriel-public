# Aedelgard — Windows Code Signing (EV) — Acquisition Dossier

*The wall: Windows 11 **Smart App Control (SAC)** hard-blocks our unsigned MSI with
no "Run anyway." SmartScreen (the softer "Unknown Publisher" scare) has an override;
SAC does not. Only a signed binary with **reputation** clears SAC. **OV builds
reputation over time; EV carries it from day one.** For a product shipped to
strangers, EV is the buy.*

---

## The entity (everything a CA will ask)

| Field | Value |
|---|---|
| Legal entity | **Millenion AB** |
| Org.nr | **556887-8697** |
| Country | Sweden |
| Contact | info@millenion.se |
| Brand | Millenion Labs / Aedelgard |
| Authorized signatory | Thomas Avasol (verify via BankID) |

CAs validate the entity automatically against **Bolagsverket** (org.nr lookup) and
the signatory via **BankID** when supported. For a Swedish AB this collapses EV
validation from ~2 weeks of notarized paperwork to a ~15-minute BankID session —
*the reason to prefer a Nordic-fluent CA.*

---

## The decision that matters MORE than price: how do we sign in CI?

Since June 2023 the CA/Browser Forum requires the EV private key to live in a
**FIPS-140-2 HSM** — a shipped USB token OR a **cloud-HSM signing service**.

Our MSI is built + signed on a **GitHub Actions `windows-latest` runner**. You
**cannot plug a USB token into a cloud runner.** Therefore:

> **REQUIRED: a cloud-HSM signing offering the runner can call via API/agent.**
> A USB-token-only EV cert does NOT fit our pipeline without a self-hosted
> Windows signing box with the token attached (avoid — fragile).

Cloud-signing products by CA:
- **SSL.com → eSigner** (sign from CI via API; cleanest fit; ~$250–400/yr EV)
- **DigiCert → KeyLocker** (purpose-built for CI; ~$500–700/yr; top reputation)
- **Sectigo** (via partners SignPath / Garantir)
- **GlobalSign → DSS** (strong Nordic/BankID story — get a quote)

**Recommended to quote first: SSL.com (eSigner) and GlobalSign.**

---

## Two questions to ask the CA's sales chat (5 min, do not skip)

1. *"For a Swedish AB, do you support **BankID** for the authorized-representative
   verification, and **Bolagsverket** for org validation?"*
2. *"Do you offer **cloud-HSM signing callable from a CI runner** (no USB token)?
   What is the product name and per-sign / annual cost?"*

A "yes/yes" with a reasonable price = buy it.

---

## How the cert wires into our pipeline (already built — no code change needed)

`packaging/windows/build_msi.ps1` already signs **automatically** when these
GitHub secrets exist. The mechanism differs by signing model:

### Model A — cert file (.pfx) [OV, or EV via cloud that exports a usable cert]
- `WINDOWS_CERT_BASE64` — base64 of the `.pfx`
- `WINDOWS_CERT_PASSWORD` — its password
- build_msi.ps1 decodes → `signtool sign /f cert.pfx /p <pwd> /fd sha256 /tr <timestamp-url> /td sha256`

### Model B — cloud-HSM (SSL.com eSigner / DigiCert KeyLocker) [EV in CI]
- Uses the CA's signing **agent/CLI** invoked by signtool, not a local .pfx.
- Secrets become CA-specific (e.g. eSigner: `ES_USERNAME`, `ES_PASSWORD`,
  `ES_CREDENTIAL_ID`, `ES_TOTP_SECRET`).
- **build_msi.ps1 needs a small branch for this path** — TODO when the CA is
  chosen (the exact signtool/agent invocation is documented per CA).

> ACTION when cert acquired: confirm which model, drop the secrets into the
> `avasol/galadriel-public` repo settings, and (Model B only) add the CA's
> signing branch to build_msi.ps1. Then every build self-signs and clears SAC.

---

## Timestamping (don't forget)

Always sign with a timestamp URL (`/tr`) so signatures stay valid after the cert
expires. Each CA publishes its RFC-3161 timestamp endpoint.

---

## Interim, for Lord Isildur's own box only (NOT for end users)

SAC is a one-way toggle (off → can't re-enable without Windows reset). Acceptable
on a trusted dev box; never recommended to a customer. The whole point of the EV
cert is that the customer never has to touch SAC.

---

*Filed by Galadriel, 2026-06-24. The pipeline is cert-ready; only the purchase +
(Model B) one signtool branch remain.*
