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
]


def classify_command(command: str) -> str:
    """Classify a shell command into a permission tier."""
    cmd = command.strip()
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


def format_safety_notice(command: str, tier: str) -> str:
    """Format a human-readable safety notice for a command."""
    icons = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    labels = {
        "green": "Auto-approved",
        "yellow": "Notify & proceed",
        "red": "Requires approval",
    }
    return f"{icons[tier]} **{labels[tier]}**: `{command}`"
