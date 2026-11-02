"""
Shared helper for hardened EDGAR HTTP requests, used by src/02a/02b/02c.

`requests`'s own `timeout` parameter does not guarantee a request finishes in
that time - it resets on each byte received, so a server that trickles data
slowly enough (or a connection that gets stuck in some non-standard state)
can hang far past the configured timeout. This happened in practice: a Stage
2a run sat completely stuck on one request for over an hour with zero
progress before being killed manually. `get_with_hard_timeout` uses a
SIGALRM-based wall-clock deadline that forcibly interrupts the request no
matter what's causing the hang, so one bad connection can't stall an entire
multi-thousand-filing run.

`get_user_agent` returns the User-Agent the SEC requires on every EDGAR
request (a name plus a contact email). It is read from the environment
variable SEC_USER_AGENT, or from a .env file in the repo root, so no personal
contact data is hard-coded in the scripts.
"""

import os
import signal
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_user_agent() -> str:
    """SEC User-Agent from SEC_USER_AGENT (environment first, then .env in the repo root)."""
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    env_path = REPO_ROOT / ".env"
    if not value and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, _, val = line.strip().partition("=")
            if key.strip() == "SEC_USER_AGENT":
                value = val.strip().strip('"').strip("'")
    if not value:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. The SEC requires an identifying User-Agent for EDGAR requests. "
            'Add a line like SEC_USER_AGENT="YourProject/1.0 (your email address)" to .env in the repo root '
            "(see .env.example) or export it in your shell."
        )
    return value


class HardTimeoutError(Exception):
    pass


def _alarm_handler(signum, frame):
    raise HardTimeoutError("hard wall-clock timeout exceeded")


def get_with_hard_timeout(session: requests.Session, url: str, hard_timeout_seconds: int = 25, **kwargs) -> requests.Response:
    """
    Same as session.get(), but guarantees the call cannot block past
    hard_timeout_seconds no matter what (Unix only - uses SIGALRM).
    """
    kwargs.setdefault("timeout", (10, 15))  # (connect, read) - requests' own timeout as a first line of defense
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(hard_timeout_seconds)
    try:
        return session.get(url, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
