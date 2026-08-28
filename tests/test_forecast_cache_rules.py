from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.cache_verdict import (
    Fetch,
    RetryPreferred,
    Serve,
    _sunset_is_upcoming,
    _sunsethue_cache_is_current,
    verdict_for,
)

KYIV = ZoneInfo("Europe/Kyiv")


@dataclass
class FakeCache:
    """Stands in for a ForecastCache row; only the read fields matter here."""

    sunset_at: datetime
    fetched_at: datetime
    provider: str = "open_meteo"
    weather_data: dict[str, Any] | None = None

    @property
    def forecast_date(self):
        return self.sunset_at.date()


# The verdict: the whole decision, over the rows held for one user.


def test_nothing_held_means_fetch():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    assert verdict_for([], local_now, preferred_available=True) == Fetch()


def test_a_passed_sunset_is_not_servable():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    passed = FakeCache(sunset_at=local_now - timedelta(minutes=1), fetched_at=local_now, provider="sunsethue")
    assert verdict_for([passed], local_now, preferred_available=True) == Fetch()


def test_a_current_sunsethue_row_is_served():
    # 16:30 UTC is the latest update before 18:00 UTC; this row was fetched after it.
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=1),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([cache], local_now, preferred_available=True) == Serve(cache)


def test_a_sunsethue_row_is_dropped_once_a_model_update_lands():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=1),
        fetched_at=datetime(2026, 8, 27, 15, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([cache], local_now, preferred_available=True) == Fetch()


def test_a_provisional_row_sends_us_back_to_the_preferred_provider():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=local_now + timedelta(hours=2), fetched_at=local_now)
    assert verdict_for([cache], local_now, preferred_available=True) == RetryPreferred(cache)


def test_a_provisional_row_is_served_when_there_is_nobody_better_to_ask():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=local_now + timedelta(hours=2), fetched_at=local_now)
    assert verdict_for([cache], local_now, preferred_available=False) == Serve(cache)


def test_tomorrows_row_is_used_once_todays_sunset_has_passed():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    today = FakeCache(
        sunset_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        sunset_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([today, tomorrow], local_now, preferred_available=True) == Serve(tomorrow)


def test_a_stale_sunsethue_row_does_not_hide_tomorrows_provisional_row():
    # 10:30 UTC is the latest update before 16:00 UTC; today's row predates it.
    local_now = datetime(2026, 8, 27, 19, 0, tzinfo=KYIV)
    today = FakeCache(
        sunset_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        sunset_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
    )
    assert verdict_for([today, tomorrow], local_now, preferred_available=True) == RetryPreferred(tomorrow)


def test_a_naive_sunset_is_read_in_the_users_timezone():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=datetime(2026, 8, 27, 20, 0), fetched_at=local_now)
    assert verdict_for([cache], local_now, preferred_available=False) == Serve(cache)


# Naming a day: what the Завтра button asks for.


def test_naming_a_day_skips_the_row_for_the_other_day():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    today = FakeCache(
        sunset_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        sunset_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    candidates = [today, tomorrow]
    verdict = verdict_for(candidates, local_now, preferred_available=True, on_date=tomorrow.forecast_date)
    assert verdict == Serve(tomorrow)


def test_naming_a_day_we_hold_nothing_for_means_fetch():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    today = FakeCache(
        sunset_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    verdict = verdict_for([today], local_now, preferred_available=True, on_date=date(2026, 8, 28))
    assert verdict == Fetch()


def test_a_named_day_still_re_asks_the_preferred_provider_for_a_provisional_row():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    tomorrow = FakeCache(
        sunset_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
    )
    verdict = verdict_for([tomorrow], local_now, preferred_available=True, on_date=tomorrow.forecast_date)
    assert verdict == RetryPreferred(tomorrow)


# The Sunsethue model-update clock, where the calendar edges are easiest to state.


def test_sunsethue_row_is_current_between_model_updates():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    cache = FakeCache(
        sunset_at=local_now + timedelta(hours=2),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert _sunsethue_cache_is_current(cache, local_now) is True


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


def test_passed_sunset_is_not_upcoming():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(sunset_at=local_now - timedelta(minutes=1), fetched_at=local_now)
    assert _sunset_is_upcoming(cache, KYIV, local_now) is False
