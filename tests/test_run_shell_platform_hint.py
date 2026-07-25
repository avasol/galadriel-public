"""run_shell tool description — platform-honesty guard.

The tool schema (harness/tools.py TOOL_DEFINITIONS) used to hardcode
"Execute a shell command on the EC2 instance." — true on this box, false on
every Aedelgard body running on a customer's own Windows/macOS/Linux machine.
Because the tools list is resent on EVERY API call, that lie reasserted
itself every single turn: the model reached for bash syntax, discovered the
truth by trial and error, and had to rediscover it again next turn since
nothing durable recorded the correction.

Fixed: `_platform_shell_hint()` computes the real host's shell syntax via
`platform.system()` at import time and the run_shell description embeds it
directly. This guards two invariants:
  1. The hardcoded "EC2 instance" claim never returns.
  2. `_platform_shell_hint()` names the right shell for each of the three
     platforms the body actually ships on.

Run: python -m pytest tests/test_run_shell_platform_hint.py -q
(no network, no API spend.)
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import harness.tools as tools_mod  # noqa: E402


def test_run_shell_description_never_claims_ec2():
    d = next(t for t in tools_mod.TOOL_DEFINITIONS if t["name"] == "run_shell")
    assert "EC2 instance" not in d["description"]
    assert "Execute a shell command." in d["description"]


def test_windows_hint_names_powershell_and_cmd():
    with patch.object(tools_mod.platform, "system", return_value="Windows"):
        hint = tools_mod._platform_shell_hint()
    assert "cmd.exe" in hint
    assert "PowerShell" in hint


def test_macos_hint_names_bash_zsh():
    with patch.object(tools_mod.platform, "system", return_value="Darwin"):
        hint = tools_mod._platform_shell_hint()
    assert "macOS" in hint
    assert "bash" in hint or "zsh" in hint


def test_linux_hint_names_bash():
    with patch.object(tools_mod.platform, "system", return_value="Linux"):
        hint = tools_mod._platform_shell_hint()
    assert "Linux" in hint
    assert "bash" in hint


if __name__ == "__main__":
    test_run_shell_description_never_claims_ec2()
    test_windows_hint_names_powershell_and_cmd()
    test_macos_hint_names_bash_zsh()
    test_linux_hint_names_bash()
    print("OK")
