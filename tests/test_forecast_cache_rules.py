from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.forecast_service import _sunset_is_upcoming, _sunsethue_cache_is_current

KYIV = ZoneInfo("Europe/Kyiv")


@dataclass
class FakeCache:
    """Stands in for a ForecastCache row; only the read fields matter here."""

    sunset_at: datetime
    fetched_at: datetime
    provider: str = "open_meteo"
    weather_data: dict[str, Any] | None = None


def test_upcoming_sunset_is_servable():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=local_now + timedelta(hours=2), fetched_at=local_now)
    assert _sunset_is_upcoming(cache, KYIV, local_now) is True


def test_passed_sunset_is_not_servable():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=local_now - timedelta(minutes=1), fetched_at=local_now)
    assert _sunset_is_upcoming(cache, KYIV, local_now) is False


def test_naive_sunset_is_read_in_the_users_timezone():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=datetime(2026, 8, 27, 20, 0), fetched_at=local_now)
    assert _sunset_is_upcoming(cache, KYIV, local_now) is True


def test_sunsethue_row_is_current_between_model_updates():
    # 16:30 UTC is the latest update before 18:00 UTC; fetched after it.
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=2),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert _sunsethue_cache_is_current(cache, local_now) is True


def test_sunsethue_row_is_stale_once_a_model_update_lands():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=2),
        fetched_at=datetime(2026, 8, 27, 15, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert _sunsethue_cache_is_current(cache, local_now) is False


def test_naive_fetched_at_is_read_as_utc():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=2),
        fetched_at=datetime(2026, 8, 27, 17, 0),
        provider="sunsethue",
    )
    assert _sunsethue_cache_is_current(cache, local_now) is True


def test_before_the_first_update_of_the_day_yesterdays_last_update_applies():
    local_now = datetime(2026, 8, 27, 2, 0, tzinfo=UTC)
    fresh = FakeCache(
        sunset_at=local_now + timedelta(hours=16),
        fetched_at=datetime(2026, 8, 26, 23, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    stale = FakeCache(
        sunset_at=local_now + timedelta(hours=16),
        fetched_at=datetime(2026, 8, 26, 21, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert _sunsethue_cache_is_current(fresh, local_now) is True
    assert _sunsethue_cache_is_current(stale, local_now) is False
