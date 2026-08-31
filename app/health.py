"""Liveness signal for the notification scan.

The bot has no HTTP surface, and a wedged scan loop is indistinguishable from a
healthy one from outside: long polling keeps answering commands while nobody is
ever notified. The loop stamps a file after each completed pass and the
container healthcheck asserts that stamp is recent.

Deliberately reads its configuration from the environment rather than through
Settings: a healthcheck that fails because an unrelated setting is missing
reports the wrong thing.
"""

import os
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_PATH = "/tmp/notification-scan-heartbeat"
DEFAULT_SCAN_INTERVAL_MINUTES = 30

# A pass may be skipped for benign reasons, so allow two intervals plus a margin
# before calling the loop wedged.
MISSED_PASSES_ALLOWED = 2
GRACE_SECONDS = 300


def heartbeat_path() -> Path:
    return Path(os.environ.get("NOTIFICATION_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH))


def record_scan(path: Path | None = None, now: float | None = None) -> None:
    """Stamp a completed pass. Never raises: a health signal must not break the bot."""
    target = path or heartbeat_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(now or time.time()), encoding="utf-8")
    except OSError:
        pass


def seconds_since_last_scan(path: Path | None = None, now: float | None = None) -> float | None:
    """Age of the stamp, or None when there has never been one."""
    target = path or heartbeat_path()
    try:
        return (now or time.time()) - target.stat().st_mtime
    except OSError:
        return None


def max_age_seconds(scan_interval_minutes: int) -> float:
    return scan_interval_minutes * 60 * MISSED_PASSES_ALLOWED + GRACE_SECONDS


def is_healthy(scan_interval_minutes: int, path: Path | None = None, now: float | None = None) -> bool:
    age = seconds_since_last_scan(path, now)
    return age is not None and age <= max_age_seconds(scan_interval_minutes)


def _scan_interval_from_env() -> int:
    try:
        return int(os.environ.get("NOTIFICATION_SCAN_INTERVAL_MINUTES", DEFAULT_SCAN_INTERVAL_MINUTES))
    except ValueError:
        return DEFAULT_SCAN_INTERVAL_MINUTES


def main() -> int:
    return 0 if is_healthy(_scan_interval_from_env()) else 1


if __name__ == "__main__":
    sys.exit(main())
