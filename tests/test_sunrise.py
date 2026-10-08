"""Sunrise through both providers, and how it is worded and drawn.

Everything that does not depend on the kind of Solar Event is already covered by
the sunset tests; this pins down only where sunrise differs.
"""

import asyncio
import io
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from app.bot.card import palette, render_card
from app.bot.keyboards import main_keyboard
from app.bot.messages import format_forecast
from app.services.scoring import score_sunset
from app.services.solar import SolarEvent, days_to_ask, event_time
from app.services.sunsethue import SunsethueClient, SunsethueError
from app.services.weather import ForecastResult, OpenMeteoClient

KYIV = ZoneInfo("Europe/Kyiv")
KYIV_LAT, KYIV_LON = 50.45, 30.52


# Open-Meteo: the window is the sunset window mirrored in time.


@pytest.mark.parametrize(
    ("offset_minutes", "sunrise_weight", "sunset_weight"),
    [
        # Each sunrise weight is the sunset weight at the opposite offset.
        (-90, 0.25, 0.65),
        (-30, 0.5, 1.0),
        (30, 1.0, 0.5),
        (90, 0.65, 0.25),
    ],
)
def test_the_sunrise_weights_mirror_the_sunset_weights(offset_minutes, sunrise_weight, sunset_weight):
    client = OpenMeteoClient()
    event_at = datetime(2026, 10, 8, 7, 0, tzinfo=KYIV)
    sample = event_at + timedelta(minutes=offset_minutes)
    assert client._event_weight(sample, event_at, SolarEvent.SUNRISE) == sunrise_weight
    assert client._event_weight(sample, event_at, SolarEvent.SUNSET) == sunset_weight


def hourly_around(event_at: datetime) -> dict:
    times = [event_at.replace(minute=0) + timedelta(hours=offset) for offset in range(-4, 5)]
    return {"time": [moment.replace(tzinfo=None).isoformat(timespec="minutes") for moment in times]}


def test_the_sunrise_window_runs_from_an_hour_before_to_two_after():
    event_at = datetime(2026, 10, 8, 7, 0, tzinfo=KYIV)
    samples = OpenMeteoClient()._samples_near_event(hourly_around(event_at), event_at, SolarEvent.SUNRISE)
    hours = [sample["time"].hour for sample in samples]
    assert hours == [6, 7, 8, 9]


def test_the_sunset_window_is_unchanged():
    event_at = datetime(2026, 10, 8, 19, 0, tzinfo=KYIV)
    samples = OpenMeteoClient()._samples_near_event(hourly_around(event_at), event_at, SolarEvent.SUNSET)
    hours = [sample["time"].hour for sample in samples]
    assert hours == [17, 18, 19, 20]


def test_open_meteo_picks_the_sunrise_from_the_daily_payload(monkeypatch):
    now = datetime.now(KYIV)
    sunrise = (now + timedelta(days=1)).replace(hour=7, minute=10, second=0, microsecond=0)
    sunset = sunrise.replace(hour=18, minute=40)
    hourly = hourly_around(sunrise)
    hourly["cloud_cover"] = [40] * len(hourly["time"])
    payload = {
        "daily": {
            "sunrise": [sunrise.replace(tzinfo=None).isoformat(timespec="minutes")],
            "sunset": [sunset.replace(tzinfo=None).isoformat(timespec="minutes")],
        },
        "hourly": hourly,
    }

    async def weather(*_args):
        return payload

    async def air_quality(*_args):
        return {}

    client = OpenMeteoClient()
    monkeypatch.setattr(client, "_fetch_weather", weather)
    monkeypatch.setattr(client, "_fetch_air_quality", air_quality)
    result = asyncio.run(client.forecast_for_today(KYIV_LAT, KYIV_LON, "Europe/Kyiv", event=SolarEvent.SUNRISE))

    assert result.event is SolarEvent.SUNRISE
    assert result.event_at == sunrise
    assert result.provider == "open_meteo"


def test_the_open_meteo_description_names_the_sunrise():
    # A good sky whose forecast keeps shifting, so the wording mentions the event.
    shifting = {
        "cloud_cover": 50,
        "cloud_cover_low": 5,
        "cloud_cover_mid": 35,
        "cloud_cover_high": 50,
        "visibility": 20000,
        "relative_humidity_2m": 50,
        "sunset_window_consistency": 20,
    }
    sunrise = score_sunset(shifting, SolarEvent.SUNRISE)
    sunset = score_sunset(shifting, SolarEvent.SUNSET)
    assert "біля сходу" in sunrise.description
    assert "біля заходу" in sunset.description
    assert sunrise.score == sunset.score


# Sunsethue: asked for sunrise by name, and only for the day that matters.


def a_sunsethue_payload(event: SolarEvent, on: date) -> dict:
    at = event_time(event, on, KYIV_LAT, KYIV_LON)
    return {
        "time": datetime.now(UTC).isoformat(),
        "data": {
            "type": str(event),
            "model_data": True,
            "quality": 0.81,
            "quality_text": "great",
            "cloud_cover": 0.3,
            "direction": 95,
            "time": at.isoformat().replace("+00:00", "Z"),
            "magics": {},
        },
    }


class RecordingSunsethue(SunsethueClient):
    def __init__(self) -> None:
        super().__init__("key")
        self.asked: list[tuple[date, SolarEvent]] = []

    async def _fetch_event(self, client, latitude, longitude, forecast_date, event):
        self.asked.append((forecast_date, event))
        return a_sunsethue_payload(event, forecast_date)


def test_sunsethue_is_asked_only_about_the_day_the_calculation_picks():
    client = RecordingSunsethue()
    expected = days_to_ask(SolarEvent.SUNRISE, datetime.now(KYIV), KYIV_LAT, KYIV_LON)
    result = asyncio.run(client.forecast_for_today(KYIV_LAT, KYIV_LON, "Europe/Kyiv", event=SolarEvent.SUNRISE))

    asked_days = [day for day, _ in client.asked]
    # Both days are only asked about inside the two-minute band around sunrise.
    assert asked_days == expected[: len(asked_days)]
    assert all(event is SolarEvent.SUNRISE for _, event in client.asked)
    assert result.event is SolarEvent.SUNRISE
    assert result.event_at > datetime.now(UTC)


def test_a_sunsethue_sunrise_is_worded_as_a_sunrise():
    result = SunsethueClient("key")._parse_event(
        a_sunsethue_payload(SolarEvent.SUNRISE, date(2026, 10, 9)), "Europe/Kyiv", SolarEvent.SUNRISE
    )
    assert result.event is SolarEvent.SUNRISE
    assert result.score == 81
    assert "красивий схід" in result.description
    assert "сонце сходить у напрямку схід" in result.description


def test_a_sunset_answer_is_rejected_when_a_sunrise_was_asked_for():
    with pytest.raises(SunsethueError):
        SunsethueClient("key")._parse_event(
            a_sunsethue_payload(SolarEvent.SUNSET, date(2026, 10, 9)), "Europe/Kyiv", SolarEvent.SUNRISE
        )


# Presentation.


def a_sunrise(days_ahead: int = 1) -> ForecastResult:
    event_at = datetime.now(KYIV).replace(hour=7, minute=12, second=0, microsecond=0) + timedelta(days=days_ahead)
    return ForecastResult(
        provider="sunsethue",
        forecast_date=event_at.date(),
        event_at=event_at,
        score=64,
        description="x",
        weather_data={},
        event=SolarEvent.SUNRISE,
    )


def test_the_caption_names_the_sunrise():
    text = format_forecast(a_sunrise(), "Europe/Kyiv")
    assert text.startswith("🌄 Схід сонця завтра")
    assert "64%" in text


def test_the_sunrise_card_has_its_own_sky():
    assert palette(64, SolarEvent.SUNRISE) != palette(64, SolarEvent.SUNSET)


def test_the_sunrise_card_renders_and_differs_from_the_sunset_card():
    sunrise = render_card(64, "завтра", "07:12", SolarEvent.SUNRISE)
    sunset = render_card(64, "завтра", "07:12", SolarEvent.SUNSET)
    assert Image.open(io.BytesIO(sunrise)).size == Image.open(io.BytesIO(sunset)).size
    assert sunrise != sunset


def test_the_sunrise_button_is_always_offered():
    for show_next_day in (True, False):
        keyboard = main_keyboard(False, show_next_day=show_next_day)
        assert "sunrise" in {button.callback_data for row in keyboard.inline_keyboard for button in row}
