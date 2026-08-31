from datetime import UTC, datetime, timedelta

from app.services.sunsethue import BREAKER_COOLDOWN, BREAKER_FAILURE_THRESHOLD, _Breaker

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def fail_times(breaker: _Breaker, count: int, now: datetime = NOW) -> None:
    for _ in range(count):
        breaker.record_failure(now)


def test_allows_calls_before_any_failure():
    assert _Breaker().allows(NOW) is True


def test_stays_open_below_the_failure_threshold():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.allows(NOW) is True


def test_trips_on_the_threshold_failure():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD)
    assert breaker.allows(NOW) is False


def test_success_resets_the_failure_count():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD - 1)
    breaker.record_success()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.allows(NOW) is True


def test_permits_a_probe_once_the_cooldown_lapses():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD)
    assert breaker.allows(NOW + BREAKER_COOLDOWN - timedelta(seconds=1)) is False
    assert breaker.allows(NOW + BREAKER_COOLDOWN) is True


def test_a_failed_probe_retrips_immediately():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD)
    probe_at = NOW + BREAKER_COOLDOWN
    assert breaker.allows(probe_at) is True
    breaker.record_failure(probe_at)
    assert breaker.allows(probe_at) is False


def test_a_successful_probe_reopens_fully():
    breaker = _Breaker()
    fail_times(breaker, BREAKER_FAILURE_THRESHOLD)
    probe_at = NOW + BREAKER_COOLDOWN
    assert breaker.allows(probe_at) is True
    breaker.record_success()
    breaker.record_failure(probe_at)
    assert breaker.allows(probe_at) is True


def test_quota_exhaustion_blocks_until_the_next_utc_day():
    breaker = _Breaker()
    breaker.record_quota_exhaustion(NOW)
    assert breaker.allows(NOW + BREAKER_COOLDOWN) is False
    assert breaker.allows(datetime(2026, 8, 27, 23, 59, tzinfo=UTC)) is False
    assert breaker.allows(datetime(2026, 8, 28, 0, 0, tzinfo=UTC)) is True


def test_quota_exhaustion_does_not_need_the_failure_threshold():
    breaker = _Breaker()
    breaker.record_quota_exhaustion(NOW)
    assert breaker.allows(NOW) is False
