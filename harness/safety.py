"""Permission tiers and safety guardrails for the agent."""

import re

# Green: auto-execute without asking
# Yellow: notify user, proceed unless vetoed within timeout
# Red: require explicit approval before executing

GREEN_PATTERNS = [
    r"^ls\b",
    r"^cat\b",
    r"^head\b",
    r"^tail\b",
    r"^grep\b",
    r"^find\b",
    r"^pwd$",
    r"^echo\b",
    r"^date$",
    r"^whoami$",
    r"^git\s+(status|log|diff|branch|show|remote)",
    r"^git\s+fetch\b",
    r"^aws\s+(s3\s+ls|dynamodb\s+describe|ec2\s+describe|sts\s+get|cloudformation\s+describe|cloudformation\s+list|ce\s+get)",
    r"^python3?\s+.*\.(py)\s*$",
    r"^pip\s+(list|show|freeze)",
    r"^sam\s+(validate|build)",
    r"^df\b",
    r"^free\b",
    r"^uptime$",
    r"^wc\b",
    r"^sort\b",
    r"^du\b",
]

YELLOW_PATTERNS = [
    r"^git\s+(add|commit|push|pull|merge|checkout|switch)",
    r"^sam\s+deploy",
    r"^aws\s+s3\s+(cp|mv|sync)",
    r"^aws\s+dynamodb\s+(put-item|update-item|batch-write)",
    r"^aws\s+lambda\s+update",
    r"^pip\s+install",
    r"^sudo\s+systemctl\b",
]

RED_PATTERNS = [
    r"^rm\s",
    r"^sudo\s+rm\b",
    r"^aws\s+iam\b",
    r"^aws\s+cognito",
    r"^aws\s+secretsmanager\s+(put|update|delete)",
    r"^aws\s+cloudformation\s+(create|update|delete)",
    r"^aws\s+ec2\s+(terminate|stop|run|modify)",
    r"^aws\s+s3\s+rb\b",
    r"^aws\s+dynamodb\s+(create|delete)-table",
    r"^git\s+(push\s+--force|reset\s+--hard)",
    r"^shutdown\b",
    r"^reboot\b",
    r"curl.*\|\s*(bash|sh)",
    # Destructive primitives that previously slipped classification
    r"^find\b.*\s-delete\b",
    r"^xargs\b.*\brm\b",
    r"^(sudo\s+)?mkfs",
    r"^(sudo\s+)?dd\b.*\bof=/dev/",
    r"^(sudo\s+)?shred\b",
    r"^(sudo\s+)?truncate\b",
    r"^(sudo\s+)?chmod\s+.*-R.*\s+/\s*$",
    r"^git\s+clean\b.*\s-\w*f",
]

# Shell control operators that chain commands: ; && || | & and newlines.
# Splitting on the single-char class also consumes the doubled forms.
_CONTROL_SPLIT = re.compile(r"[;|&\n]+")

# Command substitution hides an inner command from pattern matching —
# opaque by construction, so it floors the tier at yellow.
_SUBSTITUTION = re.compile(r"\$\(|`")

_SEVERITY = {"green": 0, "yellow": 1, "red": 2}


def _classify_single(cmd: str) -> str:
    """Classify one command segment against the tier patterns."""
    for pattern in RED_PATTERNS:
        if re.search(pattern, cmd):
            return "red"
    for pattern in YELLOW_PATTERNS:
        if re.search(pattern, cmd):
            return "yellow"
    for pattern in GREEN_PATTERNS:
        if re.search(pattern, cmd):
            return "green"
    # Unknown commands default to yellow
    return "yellow"


def classify_command(command: str) -> str:
    """Classify a shell command into a permission tier.

    Chained commands (``ls; rm -rf ~``) are split on shell control
    operators and every segment is classified independently; the overall
    tier is the MAX severity found. The whole string is also classified
    unsplit, so pipe-aware patterns (``curl ... | sh``) keep matching.
    Command substitution (``$(...)`` or backticks) floors the tier at
    yellow — the inner command is opaque to pattern matching.

    Splitting is textual, not a full shell parse: an operator inside
    quotes still splits, which can only raise the tier, never lower it.
    """
    cmd = command.strip()
    if not cmd:
        return "yellow"
    tiers = [_classify_single(cmd)]
    for segment in _CONTROL_SPLIT.split(cmd):
        segment = segment.strip()
        if segment:
            tiers.append(_classify_single(segment))
    if _SUBSTITUTION.search(cmd):
        tiers.append("yellow")
    return max(tiers, key=_SEVERITY.__getitem__)


def format_safety_notice(command: str, tier: str) -> str:
    """Format a human-readable safety notice for a command."""
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    labels = {
        "green": "Auto-approved",
        "yellow": "Notify & proceed",
        "red": "Requires approval",
    }
    return f"{icons[tier]} **{labels[tier]}**: `{command}`"
