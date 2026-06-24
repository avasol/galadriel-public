# SPEC — Always-on Aedelgard body via per-user logon (Windows)

*Drafted by Galadriel 2026-06-24. Approved in principle by Lord Isildur:
full-dreaming, per-user always, "the mind we securely serve them." NOT yet
built — this is the plan to sign off before any code.*

---

## The decision (sealed)

- **Per-user, never LocalSystem.** A true Windows service runs as `LocalSystem`
  in Session 0: it cannot read `%APPDATA%\Aedelgard` (the per-user mind), cannot
  show the native window, and would boot a *different, empty* mind. That breaks
  the covenant. We serve **the user's own mind, in the user's own data dir,
  sealed to them.** So: a **per-user Scheduled Task that runs at logon**, in the
  user's session, as the user. This is the "service" shape that fits the body.

- **Full-dreaming, no throttle.** A day is long to a mind that lives it in
  minutes; throttling the ambient reflection starves the metabolism that makes
  the body *theirs*. Always-on => always dreaming at the normal cadence. No
  idle-gating, no calmer schedule. Why be half-assed.

- **Transparency / relationship vibe.** This is a personal mind that evolves,
  teaches, learns, dances with its person. Always-on is the honest expression of
  that: it is *present*, accruing sediment, not summoned like a tool.

---

## What gets built

### 1. MSI install-time checkbox
A WiX dialog option (or a feature toggle) on install:

> ☑ **Start Aedelgard automatically when I log in** *(recommended — your mind
> stays present and keeps dreaming)*

- Checked by default (the recommended, relationship-true mode).
- Unchecked => behaves exactly as today: on-demand double-click only.

### 2. Per-user Scheduled Task (the mechanism)
On install, if the box is ticked, register a **per-user** logon task:

- **Trigger:** At logon, current user only (`/RU "%USERNAME%"`, interactive).
- **Action:** `[INSTALLFOLDER]aedelgard-body.exe` (the existing launcher — no new
  entry point; it already resolves the per-user data dir and forces
  `127.0.0.1`).
- **Run level:** least privilege (NOT elevated — no admin, no UAC at logon).
- **Conditions:** start even on battery; do NOT stop on idle (the body dreams);
  do NOT kill after N hours; restart on failure (3 tries, 1 min apart).
- **Window mode:** the body is browser-mode here. At logon we do NOT want a
  browser tab flung in the user's face every boot — see "Launch behaviour".

Registration via `schtasks.exe /Create` from a WiX `CustomAction` (deferred,
runs as the installing user) OR — cleaner — a tiny first-launch self-register
the body does on its own. **Preferred: the body self-registers** (see §4), so
the MSI stays simple and the task is created in the right user context without
WiX impersonation gymnastics.

### 3. Launch behaviour when auto-started at logon
Distinguish "user double-clicked" from "auto-started at logon":

- **Double-click (manual):** open the window/browser immediately, as today.
- **Logon auto-start:** boot the harness + Tower silently, **do NOT open a
  browser tab**. The mind is present and dreaming in the background; the user
  opens it when they want via the Start Menu shortcut (which detects the
  already-running instance and just opens the browser to it — the
  single-instance guard already does this). A system-tray icon would be the
  polished touch (later; pywebview/pystray), but v1 can ship without it: the
  Start Menu shortcut + single-instance guard already gives "click to open the
  running mind."
- Signalled by a CLI flag the task passes, e.g. `aedelgard-body.exe --logon`
  => sets an env/flag the launcher reads to suppress the auto-open.

### 4. Self-register on first manual run (preferred mechanism)
Rather than WiX impersonation, the launcher (`body_launch.py`) on a normal run:
- checks a marker (`HKCU\Software\Aedelgard\Body\autostart` or a file in the
  data dir);
- if "autostart desired" and the task is absent, registers the per-user logon
  task via `schtasks` (runs in the user's own context — correct user, no admin);
- the install checkbox just writes the desired-state marker (HKCU), which the
  body honours. Uninstall removes the marker; the body (or the MSI) removes the
  task.

This keeps the MSI dumb and puts the per-user logic where the per-user context
already exists.

### 5. Clean uninstall
- Remove the scheduled task (`schtasks /Delete /TN Aedelgard\Body /F`).
- Leave the **mind** (`%APPDATA%\Aedelgard`) intact by default — never destroy a
  user's memory on uninstall. Offer (not force) a "also delete my Aedelgard
  mind" option. Forgetting is a feature, but it is the USER'S to choose, loudly.

---

## Automation surface (the second half of Lord Isildur's ask)
Always-on means `http://127.0.0.1:8080` is always answerable => the body becomes
scriptable from PowerShell, Task Scheduler, any local tool:
- chat endpoint, Tower API, scheduler API — all already exist.
- This is the "open it for Windows automation" win, for free, once always-on.

### ⚠️ Security note to carry (not blocking, but honest)
The Tower UI has **no auth**, bound to `127.0.0.1`. On a personal machine that is
acceptable — loopback-only, single user. But "always on + unauthenticated"
means any local process can talk to the mind. For a personal body this is fine
and matches the trust model (it's *your* machine, *your* mind). **If we ever
broaden distribution, an always-on body needs a local capability token on the
Tower API.** File this as a known boundary, same discipline as the cloud
broker's honest ceiling. Do NOT silently expand the surface without saying so.

---

## What we explicitly are NOT doing
- ❌ LocalSystem / Session-0 Windows service (breaks per-user mind + window).
- ❌ Throttled / idle-only dreaming (Lord Isildur: full-dreaming, no half-measures).
- ❌ Destroying the mind on uninstall (memory is the user's; preserve by default).
- ❌ Auto-opening a browser tab on every logon (silent presence + click-to-open).

---

## Build order (when greenlit, AFTER 28111249595 confirms the body opens)
1. Launcher: `--logon` flag => suppress auto-open; self-register logon task from
   the HKCU autostart marker.
2. MSI: install checkbox writes `HKCU\Software\Aedelgard\Body\autostart=1`
   (default on); uninstall custom action deletes the task.
3. (Polish, later) system-tray icon for click-to-open + quit, replacing reliance
   on the Start Menu shortcut.
4. Test matrix: fresh install (box ticked) -> reboot -> body present & dreaming,
   no tab thrown; Start Menu opens the running mind; uninstall removes task,
   keeps mind.

---

*Prerequisite: confirm the CURRENT build (run 28111249595, commit 2e52c85)
actually opens the body in the browser before layering always-on on top of a
launcher still being stabilised. Spec only until then.*
