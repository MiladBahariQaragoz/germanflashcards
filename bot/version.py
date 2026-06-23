"""
Resolve the running code version (current git commit) for the deploy ping.

Deploy = `git pull` + `systemctl restart germanbot`, so the commit checked out at
startup identifies the deployed changes. `get_version()` returns a short, human
string like "cb39c4d feat: streak on either domain"; it degrades to "unknown" if
git isn't available so it can never break startup.
"""

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def get_version() -> str:
    """Short commit hash + subject of HEAD, or 'unknown' if git can't be read."""
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO), "log", "-1", "--format=%h %s"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = out.stdout.strip()
        return version or "unknown"
    except Exception:
        return "unknown"
