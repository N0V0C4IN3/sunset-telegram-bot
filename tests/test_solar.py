from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.solar import SolarEvent, days_to_ask, event_time

KYIV = ZoneInfo("Europe/Kyiv")
KYIV_LAT, KYIV_LON = 50.45, 30.52


# The equation against published almanac times, within a couple of minutes.


@pytest.mark.parametrize(
    ("event", "on", "latitude", "longitude", "expected"),
    [
        (SolarEvent.SUNRISE, date(2024, 6, 21), 51.5074, -0.1278, datetime(2024, 6, 21, 3, 43, tzinfo=UTC)),
        (SolarEvent.SUNSET, date(2024, 6, 21), 51.5074, -0.1278, datetime(2024, 6, 21, 20, 21, tzinfo=UTC)),
        # Southern hemisphere, and a local day that starts on the previous UTC day.
        (SolarEvent.SUNRISE, date(2024, 12, 21), -33.8688, 151.2093, datetime(2024, 12, 20, 18, 41, tzinfo=UTC)),
        # West of Greenwich, where the local sunset lands on the next UTC day.
        (SolarEvent.SUNSET, date(2024, 3, 1), 34.05, -118.24, datetime(2024, 3, 2, 1, 49, tzinfo=UTC)),
    ],
)
def test_event_time_matches_the_almanac(event, on, latitude, longitude, expected):
    assert abs(event_time(event, on, latitude, longitude) - expected) < timedelta(minutes=2)


def test_polar_night_has_no_sunrise():
    assert event_time(SolarEvent.SUNRISE, date(2024, 12, 21), 69.65, 18.96) is None


# Which day to ask a provider about.


def todays(event: SolarEvent) -> datetime:
    return event_time(event, date(2026, 10, 8), KYIV_LAT, KYIV_LON).astimezone(KYIV)


def test_before_dawn_only_todays_sunrise_is_asked_for():
    local_now = todays(SolarEvent.SUNRISE) - timedelta(hours=2)
    assert days_to_ask(SolarEvent.SUNRISE, local_now, KYIV_LAT, KYIV_LON) == [date(2026, 10, 8)]


def test_after_dawn_only_tomorrows_sunrise_is_asked_for():
    local_now = todays(SolarEvent.SUNRISE) + timedelta(hours=1)
    assert days_to_ask(SolarEvent.SUNRISE, local_now, KYIV_LAT, KYIV_LON) == [date(2026, 10, 9)]


def test_after_dusk_only_tomorrows_sunset_is_asked_for():
    local_now = todays(SolarEvent.SUNSET) + timedelta(minutes=30)
    assert days_to_ask(SolarEvent.SUNSET, local_now, KYIV_LAT, KYIV_LON) == [date(2026, 10, 9)]


def test_too_close_to_call_asks_about_both_days():
    local_now = todays(SolarEvent.SUNSET) + timedelta(minutes=1)
    assert days_to_ask(SolarEvent.SUNSET, local_now, KYIV_LAT, KYIV_LON) == [date(2026, 10, 8), date(2026, 10, 9)]


def test_no_sunrise_today_asks_about_both_days():
    local_now = datetime(2024, 12, 21, 12, 0, tzinfo=ZoneInfo("Europe/Oslo"))
    assert days_to_ask(SolarEvent.SUNRISE, local_now, 69.65, 18.96) == [date(2024, 12, 21), date(2024, 12, 22)]
