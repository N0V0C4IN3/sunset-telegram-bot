"""The liveness stamp that tells a wedged scan loop from a healthy one."""

from app.health import (
    GRACE_SECONDS,
    MISSED_PASSES_ALLOWED,
    is_healthy,
    max_age_seconds,
    record_scan,
    seconds_since_last_scan,
)

NOW = 1_800_000_000.0
INTERVAL = 30


def stamp(tmp_path, age_seconds: float):
    """A heartbeat file that was written `age_seconds` ago."""
    import os

    path = tmp_path / "heartbeat"
    record_scan(path, now=NOW - age_seconds)
    os.utime(path, (NOW - age_seconds, NOW - age_seconds))
    return path


def test_a_bot_that_has_never_scanned_is_not_healthy(tmp_path):
    assert seconds_since_last_scan(tmp_path / "missing", now=NOW) is None
    assert is_healthy(INTERVAL, tmp_path / "missing", now=NOW) is False


def test_a_bot_that_just_scanned_is_healthy(tmp_path):
    assert is_healthy(INTERVAL, stamp(tmp_path, 0), now=NOW) is True


def test_a_bot_is_still_healthy_after_one_missed_pass(tmp_path):
    assert is_healthy(INTERVAL, stamp(tmp_path, INTERVAL * 60 + 60), now=NOW) is True


def test_a_loop_silent_well_past_its_allowance_is_unhealthy(tmp_path):
    silent_for = max_age_seconds(INTERVAL) + 60
    assert is_healthy(INTERVAL, stamp(tmp_path, silent_for), now=NOW) is False


def test_the_allowance_scales_with_the_configured_interval(tmp_path):
    assert max_age_seconds(30) == 30 * 60 * MISSED_PASSES_ALLOWED + GRACE_SECONDS
    assert max_age_seconds(60) > max_age_seconds(30)
    # A slow scan interval must not be reported unhealthy just for being slow.
    assert is_healthy(60, stamp(tmp_path, 45 * 60), now=NOW) is True


def test_recording_a_scan_never_raises_even_when_the_path_is_unwritable(tmp_path):
    # A health signal must never be able to break the loop it is reporting on.
    unwritable = tmp_path / "heartbeat" / "nested"
    (tmp_path / "heartbeat").write_text("i am a file, not a directory")
    record_scan(unwritable, now=NOW)


def test_a_recorded_scan_is_immediately_visible(tmp_path):
    path = tmp_path / "heartbeat"
    record_scan(path)
    age = seconds_since_last_scan(path)
    assert age is not None and age < 60
