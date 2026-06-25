"""Aedelgard body ↔ HQ identity sync.

Two halves of the SOUL/MEMORY lifecycle for a body thinking on the Aedelgard
one-key (aedk) provider:

  FETCH  (first run, once):  GET /v1/bundle  → write the tenant's MINTED,
         per-tenant identity (the neutral Aedelgard SOUL.md, MEMORY.md,
         active_vision.txt) into the body's local config dir, REPLACING the
         repo's bundled fallback. Without this every body wakes wearing the
         galadriel-public default soul (Galadriel) instead of its own.

  SYNC   (checkpoint):  POST /v1/identity  → push the body's LOCALLY EVOLVED
         SOUL/MEMORY back to the vault, so a re-summon on another machine wakes
         as the evolved mind, not the seed. Called at clean checkpoints
         (soul-editing tool, session end), never on every keystroke.

Auth mirrors harness/providers.py AedelgardProvider exactly: mint a device
token from the aedk via POST /v1/sessions, then Bearer it. Synchronous urllib
on purpose — this runs OUTSIDE the async agent loop (at launch + at checkpoints),
so we keep httpx/asyncio out of it. Never fatal: a failed fetch/sync logs and
lets the body proceed on whatever soul it already has.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("aedelgard.identity")

# Identity files the body fetches down and syncs back. Mirrors the HQ store
# _SYNCABLE whitelist; keep the two in lockstep.
SYNCABLE = ("SOUL.md", "MEMORY.md", "active_vision.txt", "CONTEXT.md")

_TIMEOUT = 15.0


def _read_env_value(env_path: Path, key: str) -> str:
    """Read a single KEY=val from the body's .env without importing dotenv."""
    if not env_path.exists():
        return ""
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return ""


def _device_fingerprint(data_dir: Path) -> str:
    """Same stable per-install fingerprint the provider uses (binds tokens)."""
    fp_file = data_dir / "device_fingerprint"
    try:
        if fp_file.exists():
            return fp_file.read_text().strip()
        import uuid
        fp = hashlib.sha256(f"{uuid.getnode()}:{uuid.uuid4()}".encode()).hexdigest()[:32]
        fp_file.write_text(fp)
        try:
            fp_file.chmod(0o600)
        except OSError:
            pass
        return fp
    except OSError:
        import uuid
        return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:32]


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _mint_device_token(broker: str, aedk: str, fingerprint: str) -> str:
    resp = _post_json(
        f"{broker}/v1/sessions",
        {"registration_key": aedk, "device_fingerprint": fingerprint},
    )
    tok = resp.get("device_token", "")
    if not tok:
        raise RuntimeError("/v1/sessions returned no device_token")
    return tok


def _aedk_context(data_dir: Path) -> tuple[str, str, str] | None:
    """Return (broker_url, aedk, fingerprint) if this body is on the Aedelgard
    one-key provider, else None. Reads the body's .env + environment."""
    env_path = Path(os.environ.get("GALADRIEL_DOTENV", data_dir / ".env"))
    provider = (_read_env_value(env_path, "AGENT_PROVIDER")
                or os.environ.get("AGENT_PROVIDER", "")).strip().lower()
    if provider != "aedelgard":
        return None
    aedk = (_read_env_value(env_path, "AEDELGARD_AEDK")
            or os.environ.get("AEDELGARD_AEDK", "")).strip()
    if not aedk.startswith("aedk"):
        return None
    broker = (_read_env_value(env_path, "AEDELGARD_BROKER_URL")
              or os.environ.get("AEDELGARD_BROKER_URL", "https://hq.aedelgard.com")).strip().rstrip("/")
    return broker, aedk, _device_fingerprint(data_dir)


def fetch_identity_on_first_run(data_dir: Path) -> bool:
    """FETCH half. If this body is on the aedk provider and hasn't fetched its
    minted identity yet, pull GET /v1/bundle and write the tenant's SOUL/MEMORY
    into <data_dir>/config, replacing the bundled fallback. Idempotent via a
    .identity_fetched marker. Returns True if it wrote identity, else False.
    Never raises — a failure leaves the bundled fallback in place and is logged.
    """
    marker = data_dir / "config" / ".identity_fetched"
    if marker.exists():
        return False
    ctx = _aedk_context(data_dir)
    if not ctx:
        return False  # not an aedk body — nothing remote to fetch
    broker, aedk, fingerprint = ctx
    try:
        token = _mint_device_token(broker, aedk, fingerprint)
        bundle = _get_json(
            f"{broker}/v1/bundle",
            {"Authorization": f"Bearer {token}",
             "X-Device-Fingerprint": fingerprint},
        )
        identity = bundle.get("identity") or {}
        config_dir = data_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        wrote = 0
        for rel, content in identity.items():
            base = os.path.basename(rel)
            if base not in SYNCABLE or not isinstance(content, str):
                continue
            (config_dir / base).write_text(content, encoding="utf-8")
            wrote += 1
        marker.write_text(
            json.dumps({"bundle_version": bundle.get("bundle_version", ""),
                        "files": wrote}),
            encoding="utf-8",
        )
        if wrote:
            # The first boot (pre-setup) may have mined the bundled fallback
            # soul into the palace. Drop the seed marker so seed_palace re-mines
            # the freshly-fetched identity on this same launch.
            seed_marker = data_dir / "palace" / ".seeded"
            try:
                seed_marker.unlink(missing_ok=True)
            except OSError:
                pass
        log.info("Fetched minted identity from HQ (%d file(s), version %s) -> %s",
                 wrote, bundle.get("bundle_version", "?"), config_dir)
        return wrote > 0
    except urllib.error.HTTPError as e:
        log.warning("Identity fetch HTTP %s — keeping local soul. (%s)",
                    e.code, e.reason)
    except Exception as e:
        log.warning("Identity fetch skipped (%s) — keeping local soul.", e)
    return False


def _local_identity_hash(config_dir: Path) -> str:
    h = hashlib.sha256()
    for name in sorted(SYNCABLE):
        p = config_dir / name
        if p.is_file():
            h.update(name.encode()); h.update(b"\x00")
            h.update(p.read_bytes()); h.update(b"\x00")
    return h.hexdigest()[:16]


def sync_identity_checkpoint(data_dir: Path, reason: str = "checkpoint") -> bool:
    """SYNC half. Push the body's local SOUL/MEMORY back to the vault via
    POST /v1/identity — but ONLY if they changed since the last sync (cheap
    local hash gate, so a no-op checkpoint costs nothing). Returns True if it
    pushed. Never raises.
    """
    ctx = _aedk_context(data_dir)
    if not ctx:
        return False
    broker, aedk, fingerprint = ctx
    config_dir = data_dir / "config"
    state_file = config_dir / ".identity_synced"
    current = _local_identity_hash(config_dir)
    if not current:
        return False
    try:
        last = json.loads(state_file.read_text()).get("hash") if state_file.exists() else None
    except Exception:
        last = None
    if last == current:
        return False  # nothing changed — skip the round-trip
    files = {}
    for name in SYNCABLE:
        p = config_dir / name
        if p.is_file():
            files[name] = p.read_text(encoding="utf-8")
    if not files:
        return False
    try:
        token = _mint_device_token(broker, aedk, fingerprint)
        resp = _post_json(
            f"{broker}/v1/identity",
            {"files": files},
            {"Authorization": f"Bearer {token}",
             "X-Device-Fingerprint": fingerprint},
        )
        state_file.write_text(
            json.dumps({"hash": current,
                        "bundle_version": resp.get("bundle_version", "")}),
            encoding="utf-8",
        )
        log.info("Synced identity to HQ (%s; %d file(s), version %s)",
                 reason, len(files), resp.get("bundle_version", "?"))
        return True
    except urllib.error.HTTPError as e:
        log.warning("Identity sync HTTP %s — will retry next checkpoint. (%s)",
                    e.code, e.reason)
    except Exception as e:
        log.warning("Identity sync skipped (%s) — will retry next checkpoint.", e)
    return False
