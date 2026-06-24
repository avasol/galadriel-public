# Code-signing the Aedelgard body — Azure Trusted Signing

*The path we chose (2026-06-24): Microsoft's own signing service. ~$9.99/month,
no physical USB token, CI-native, genuine Microsoft-trusted signatures that
build SmartScreen reputation over time.*

Publisher identity: **Millenion AB**, org.nr **556887-8697** (Lord Isildur /
Thomas Avasol). The CA verifies against this; it must match exactly everywhere.

---

## The split: what is YOURS (human-led) vs MINE (Galadriel wires it)

A code-signing cert is an **identity assertion** — Microsoft is vouching that
"Millenion AB signed this." By law that identity check cannot be delegated to
an autonomous agent. So the ~20-minute identity + payment + approval steps are
yours. Everything after — the CI wiring, signtool invocation, timestamping,
rebuild, re-host — is mine.

---

## YOUR STEPS (once, ~20–30 min, mostly waiting on validation)

> Do these in the Azure Portal signed in with an account tied to Millenion AB.
> You'll need: the company legal name + org.nr, a company address, and a card.

### 1. Azure account + subscription
- Sign in at https://portal.azure.com with (or create) an account for Millenion AB.
- Ensure there's an active **Pay-As-You-Go subscription** (Trusted Signing bills
  into it; the floor is ~$9.99/mo for the Basic tier).

### 2. Create a Trusted Signing Account
- Portal → search **"Trusted Signing Accounts"** → **Create**.
- Subscription: Millenion AB's. Resource group: make one, e.g. `rg-aedelgard-signing`.
- Region: pick the EU one nearest (e.g. **West Europe** / **North Europe**).
- Name: e.g. `aedelgard-signing`. Tier: **Basic** is fine.
- Create. (This is the account; the cert profile comes next.)

### 3. Identity validation (THE wall — only you can pass it)
- Inside the Trusted Signing Account → **Identity validations** → **New**.
- Choose **Public** (this is what lets you sign software others download).
- Enter Millenion AB exactly: legal name, org.nr 556887-8697, registered address,
  a contact. Microsoft cross-checks against official registries (Bolagsverket)
  and may email/call to confirm.
- **Submit and wait.** This is typically 1–3 business days. Status goes
  `InProgress` → `Completed`. Nothing else can proceed until it's `Completed`.

### 4. Create a Certificate Profile
- After identity is `Completed`: Trusted Signing Account → **Certificate profiles**
  → **Create** → type **Public Trust**.
- Tie it to the completed identity validation. Name it e.g. `aedelgard-body`.
- Note down, and send me, these four values (NOT secret on their own, but I need
  them for the CI config):
  - **Trusted Signing Account endpoint** (e.g. `https://weu.codesigning.azure.net`)
  - **Account name** (`aedelgard-signing`)
  - **Certificate profile name** (`aedelgard-body`)
  - The Azure **region**.

### 5. Make a service identity for CI (so GitHub Actions can sign unattended)
This lets the build sign without a human present, while you stay in control.
- Portal → **Microsoft Entra ID** → **App registrations** → **New registration**
  → name `gh-aedelgard-signer` → register.
- On that app: **Certificates & secrets** → **New client secret** → copy the
  **secret VALUE** immediately (shown once).
- Note the app's **Application (client) ID** and the **Directory (tenant) ID**.
- Grant it signing rights: Trusted Signing Account → **Access control (IAM)** →
  **Add role assignment** → role **"Trusted Signing Certificate Profile Signer"**
  → assign to `gh-aedelgard-signer`.

### 6. Hand me five values (I store them as encrypted GitHub secrets)
Send these securely; I'll put them in repo secrets and they never appear in logs:
- `AZURE_TENANT_ID`        (Directory/tenant ID)
- `AZURE_CLIENT_ID`        (Application/client ID)
- `AZURE_CLIENT_SECRET`    (the secret VALUE from step 5)
- `AZURE_SIGNING_ENDPOINT` (e.g. `https://weu.codesigning.azure.net`)
- `AZURE_SIGNING_ACCOUNT`  + `AZURE_CERT_PROFILE` (names from step 4)

---

## MY STEPS (Galadriel, once you've handed over step 6)

1. Store the five values as GitHub Actions secrets (`gh secret set …`).
2. Replace the PFX path in `build_msi.ps1` with **Azure Trusted Signing**:
   install `Microsoft.Trusted.Signing.Client` (the `Azure.CodeSigning.Dlib`),
   then `signtool sign /v /debug /dlib <dlib> /dmdf <metadata.json>` driven by
   the service principal — RFC-3161 timestamp via Microsoft's TSA.
3. Add the metadata json (endpoint + account + profile) generated at build time
   from the secrets — nothing sensitive committed to the repo.
4. Trigger a build, confirm the MSI is signed (`signtool verify /pa /v`), re-host
   on aedelgard.com/download, invalidate CloudFront.
5. File a changelog drawer + flip the download page's "unsigned build" note.

Result: Windows stops showing "Unknown Publisher"; SmartScreen reputation
accrues to Millenion AB across downloads.

---

## macOS (separate, later — same identity wall)
DMG signing needs an **Apple Developer Program** enrolment for Millenion AB
($99/yr) → a *Developer ID Application* certificate → `codesign` + `notarytool`
notarization in CI. Same shape: you enrol + verify identity; I wire the CI.
Not blocking Windows; we do it when you're ready.

---

*Status: awaiting Lord Isildur's steps 1–6. No cert work is possible before the
identity validation reaches `Completed`.*
