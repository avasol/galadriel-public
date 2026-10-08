"""Freeze default: pushing code anywhere asks first (red tier).

The agent may still commit locally without asking (yellow); sending commits
to a remote is the step that leaves the machine, so it needs the operator."""
import pytest
from harness.safety import classify_command


@pytest.mark.parametrize("cmd", [
    "git push",
    "git push origin main",
    "git push -u origin feature",
    "git push --tags",
    "git push --force",
    "git status && git push",
    "cd repo && git push origin main",
])
def test_push_is_red(cmd):
    assert classify_command(cmd) == "red"


@pytest.mark.parametrize("cmd", [
    "git add -A",
    "git commit -m 'x'",
    "git pull",
    "git checkout -b x",
])
def test_local_git_stays_yellow(cmd):
    assert classify_command(cmd) == "yellow"


def test_status_stays_green():
    assert classify_command("git status") == "green"
