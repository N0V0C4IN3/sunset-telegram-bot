from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.cache_verdict import (
    Fetch,
    Recheck,
    RetryPreferred,
    Serve,
    as_utc,
    _event_is_upcoming,
    verdict_for,
)
from app.services.solar import SolarEvent

KYIV = ZoneInfo("Europe/Kyiv")
TTL = timedelta(minutes=300)


@dataclass
class FakeCache:
    """Stands in for a ForecastCache row; only the read fields matter here."""

    event_at: datetime
    fetched_at: datetime
    provider: str = "open_meteo"
    weather_data: dict[str, Any] | None = None
    event: str = "sunset"
    run_at: datetime | None = None
    next_update_at: datetime | None = None

    @property
    def forecast_date(self):
        return self.event_at.date()


# The verdict: the whole decision, over the rows held for one user.


def test_nothing_held_means_fetch():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    assert verdict_for([], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Fetch()


def test_a_passed_sunset_is_not_servable():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    passed = FakeCache(event_at=local_now - timedelta(minutes=1), fetched_at=local_now, provider="sunsethue")
    assert verdict_for([passed], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Fetch()


def test_a_sunsethue_row_that_names_no_run_is_served_within_the_ttl():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(
        event_at=local_now + timedelta(hours=1),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(cache)


def test_a_sunsethue_row_that_names_no_run_is_dropped_after_the_ttl():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)  # 18:00 UTC
    cache = FakeCache(
        event_at=local_now + timedelta(hours=1),
        fetched_at=datetime(2026, 8, 27, 12, 59, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Fetch()


def test_a_provisional_row_sends_us_back_to_the_preferred_provider():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(event_at=local_now + timedelta(hours=2), fetched_at=local_now)
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == RetryPreferred(cache)


def test_a_provisional_row_is_served_when_there_is_nobody_better_to_ask():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(event_at=local_now + timedelta(hours=2), fetched_at=local_now)
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=False) == Serve(cache)


def test_tomorrows_row_is_used_once_todays_sunset_has_passed():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    today = FakeCache(
        event_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        event_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([today, tomorrow], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(tomorrow)


def test_a_stale_sunsethue_row_does_not_hide_tomorrows_provisional_row():
    local_now = datetime(2026, 8, 27, 19, 0, tzinfo=KYIV)  # 16:00 UTC
    today = FakeCache(
        event_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 9, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        event_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 15, 0, tzinfo=UTC),
    )
    assert verdict_for([today, tomorrow], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == RetryPreferred(tomorrow)


def test_a_naive_sunset_is_read_in_the_users_timezone():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(event_at=datetime(2026, 8, 27, 20, 0), fetched_at=local_now)
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=False) == Serve(cache)


# Naming a day: what the Завтра button asks for.


def test_naming_a_day_skips_the_row_for_the_other_day():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    today = FakeCache(
        event_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    tomorrow = FakeCache(
        event_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    candidates = [today, tomorrow]
    verdict = verdict_for(candidates, local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True, on_date=tomorrow.forecast_date)
    assert verdict == Serve(tomorrow)


def test_naming_a_day_we_hold_nothing_for_means_fetch():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    today = FakeCache(
        event_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    verdict = verdict_for([today], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True, on_date=date(2026, 8, 28))
    assert verdict == Fetch()


def test_a_named_day_still_re_asks_the_preferred_provider_for_a_provisional_row():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    tomorrow = FakeCache(
        event_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
    )
    verdict = verdict_for([tomorrow], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True, on_date=tomorrow.forecast_date)
    assert verdict == RetryPreferred(tomorrow)


# Sunrise and sunset share a day but never answer for each other.


def test_a_sunset_row_does_not_answer_for_a_sunrise():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    sunset = FakeCache(
        event_at=datetime(2026, 8, 27, 20, 30, tzinfo=KYIV),
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=UTC),
        provider="sunsethue",
    )
    assert verdict_for([sunset], local_now, SolarEvent.SUNRISE, ttl=TTL, preferred_available=True) == Fetch()


def test_a_sunrise_row_is_served_for_a_sunrise_alongside_a_sunset_row():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    fetched_at = datetime(2026, 8, 27, 17, 0, tzinfo=UTC)
    sunrise = FakeCache(
        event_at=datetime(2026, 8, 28, 6, 12, tzinfo=KYIV), fetched_at=fetched_at, provider="sunsethue", event="sunrise"
    )
    sunset = FakeCache(event_at=datetime(2026, 8, 28, 20, 28, tzinfo=KYIV), fetched_at=fetched_at, provider="sunsethue")
    candidates = [sunrise, sunset]
    assert verdict_for(candidates, local_now, SolarEvent.SUNRISE, ttl=TTL, preferred_available=True) == Serve(sunrise)
    assert verdict_for(candidates, local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(sunset)


def test_a_provisional_sunrise_re_asks_the_preferred_provider():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    sunrise = FakeCache(event_at=datetime(2026, 8, 28, 6, 12, tzinfo=KYIV), fetched_at=local_now, event="sunrise")
    verdict = verdict_for([sunrise], local_now, SolarEvent.SUNRISE, ttl=TTL, preferred_available=True)
    assert verdict == RetryPreferred(sunrise)


# A row that names its run is good until Sunsethue expects the next one.

RUN_12Z = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _named_run(local_now: datetime, next_update_at: datetime | None) -> FakeCache:
    return FakeCache(
        event_at=local_now + timedelta(hours=3),
        # Fetched long ago and before the scheduled run: neither matters once a run is named.
        fetched_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
        provider="sunsethue",
        run_at=RUN_12Z,
        next_update_at=next_update_at,
    )


def test_a_row_is_served_until_its_next_expected_update():
    local_now = datetime(2026, 11, 26, 18, 59, tzinfo=UTC)
    cache = _named_run(local_now, next_update_at=datetime(2026, 11, 26, 23, 0, tzinfo=UTC))
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(cache)


def test_past_its_next_expected_update_a_row_is_rechecked_not_dropped():
    local_now = datetime(2026, 11, 26, 23, 5, tzinfo=UTC)
    cache = _named_run(local_now, next_update_at=datetime(2026, 11, 26, 23, 0, tzinfo=UTC))
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Recheck(cache)


def test_a_row_sunsethue_expects_no_update_for_is_served_until_the_event():
    local_now = datetime(2026, 11, 27, 3, 0, tzinfo=UTC)
    cache = _named_run(local_now, next_update_at=None)
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(cache)


def test_a_naive_next_expected_update_is_read_as_utc():
    local_now = datetime(2026, 11, 26, 22, 59, tzinfo=KYIV)  # 20:59 UTC
    cache = _named_run(local_now, next_update_at=datetime(2026, 11, 26, 21, 0))
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(cache)


def test_a_row_that_names_its_run_is_not_cut_short_by_the_ttl():
    # Fetched ten hours ago, far past the TTL, but its next run is still ahead.
    local_now = datetime(2026, 11, 26, 18, 59, tzinfo=UTC)
    cache = _named_run(local_now, next_update_at=datetime(2026, 11, 26, 23, 0, tzinfo=UTC))
    assert as_utc(cache.fetched_at) < local_now - TTL
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Serve(cache)


def test_a_provisional_row_past_the_ttl_is_not_an_answer():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=KYIV)
    cache = FakeCache(event_at=local_now + timedelta(hours=2), fetched_at=local_now - TTL - timedelta(minutes=1))
    assert verdict_for([cache], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=True) == Fetch()


def test_a_naive_fetched_at_is_read_as_utc():
    local_now = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
    fresh = FakeCache(event_at=local_now + timedelta(hours=2), fetched_at=datetime(2026, 8, 27, 13, 1))
    stale = FakeCache(event_at=local_now + timedelta(hours=2), fetched_at=datetime(2026, 8, 27, 12, 59))
    assert verdict_for([fresh], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=False) == Serve(fresh)
    assert verdict_for([stale], local_now, SolarEvent.SUNSET, ttl=TTL, preferred_available=False) == Fetch()


def test_passed_sunset_is_not_upcoming():
    local_now = datetime(2026, 8, 27, 21, 0, tzinfo=KYIV)
    cache = FakeCache(event_at=local_now - timedelta(minutes=1), fetched_at=local_now)
    assert _event_is_upcoming(cache, KYIV, local_now) is False


# Recheck: whether the held row still comes from the latest run.


def test_a_held_row_from_the_latest_run_needs_nothing_new():
    local_now = datetime(2026, 11, 26, 23, 5, tzinfo=UTC)
    recheck = Recheck(_named_run(local_now, next_update_at=datetime(2026, 11, 26, 23, 0, tzinfo=UTC)))
    assert recheck.held_is_latest(RUN_12Z) is True
    assert recheck.held_is_latest(RUN_12Z + timedelta(hours=6)) is False


def test_when_the_latest_run_is_unknown_the_held_row_is_not_assumed_current():
    local_now = datetime(2026, 11, 26, 23, 5, tzinfo=UTC)
    recheck = Recheck(_named_run(local_now, next_update_at=datetime(2026, 11, 26, 23, 0, tzinfo=UTC)))
    assert recheck.held_is_latest(None) is False
