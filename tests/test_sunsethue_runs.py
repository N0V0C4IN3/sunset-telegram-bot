"""Sunsethue names the run behind each forecast; the cache follows runs, not the clock."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db.models import ForecastCache
from app.services.forecast_service import ForecastService
from app.services.solar import SolarEvent
from app.services.sunsethue import RUN_INFO_RECHECK, SunsethueClient, SunsethueError, _RunInfo
from app.services.weather import ForecastResult

RUN_06Z = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
RUN_12Z = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _event_payload(run: str | None = "2026-11-26T12:00:00.000Z", next_update: str | None = "2026-11-26T23:00:00.000Z"):
    """The shape of /event from the API documentation."""
    payload = {
        "time": "2026-11-26T17:31:32.509Z",
        "location": {"latitude": 50.45, "longitude": 30.52},
        "grid_location": {"latitude": 50.25, "longitude": 30.5},
        "data": {
            "type": "sunset",
            "model_data": True,
            "quality": 0.42,
            "cloud_cover": 0.5,
            "quality_text": "Fair",
            "time": "2026-11-26T14:01:00.000Z",
            "direction": 242.4,
            "magics": {"golden_hour": ["2026-11-26T13:44:00.000Z", "2026-11-26T14:21:00.000Z"]},
        },
    }
    if run is not ...:
        payload["run"] = run
    if next_update is not ...:
        payload["next_expected_update"] = next_update
    return payload


# Reading the run from a forecast.


def test_a_forecast_carries_its_run_and_next_expected_update():
    result = SunsethueClient("key")._parse_event(_event_payload(), "Europe/Kyiv", SolarEvent.SUNSET)

    assert result.run_at == RUN_12Z
    assert result.next_update_at == datetime(2026, 11, 26, 23, 0, tzinfo=UTC)


def test_no_update_expected_reads_as_none():
    result = SunsethueClient("key")._parse_event(_event_payload(next_update=None), "Europe/Kyiv", SolarEvent.SUNSET)

    assert result.run_at == RUN_12Z
    assert result.next_update_at is None


def test_a_response_without_run_fields_is_still_a_forecast():
    result = SunsethueClient("key")._parse_event(_event_payload(run=..., next_update=...), "Europe/Kyiv", SolarEvent.SUNSET)

    assert result.score == 42
    assert result.run_at is None and result.next_update_at is None


def test_an_unreadable_run_is_dropped_rather_than_failing_the_forecast():
    result = SunsethueClient("key")._parse_event(_event_payload(run="soon"), "Europe/Kyiv", SolarEvent.SUNSET)

    assert result.score == 42
    assert result.run_at is None


# /run-info: one credit, shared by every caller.


def _counting_run_info(client: SunsethueClient, monkeypatch, payload: dict) -> list[str]:
    calls: list[str] = []

    async def get(http, url, params):
        calls.append(url)
        return payload

    monkeypatch.setattr(client, "_get", get)
    return calls


@pytest.mark.asyncio
async def test_run_info_is_held_until_the_next_run_is_due(monkeypatch):
    client = SunsethueClient("key")
    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    calls = _counting_run_info(client, monkeypatch, {"last_run": "2026-11-26T12:00:00.000Z", "next_run_time": due})

    assert await client.latest_run() == RUN_12Z
    assert await client.latest_run() == RUN_12Z
    assert calls == [client.run_info_url]


@pytest.mark.asyncio
async def test_while_the_next_run_is_overdue_run_info_is_asked_again_after_the_recheck_interval(monkeypatch):
    client = SunsethueClient("key")
    overdue = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    calls = _counting_run_info(client, monkeypatch, {"last_run": "2026-11-26T12:00:00.000Z", "next_run_time": overdue})

    await client.latest_run()
    assert client._run_info.recheck_at <= datetime.now(UTC) + RUN_INFO_RECHECK
    await client.latest_run()
    assert len(calls) == 1

    client._run_info = _RunInfo(RUN_12Z, recheck_at=datetime.now(UTC) - timedelta(seconds=1))
    await client.latest_run()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_failed_run_info_is_unknown_and_counts_against_the_breaker(monkeypatch):
    client = SunsethueClient("key")

    async def get(http, url, params):
        raise SunsethueError("down")

    monkeypatch.setattr(client, "_get", get)

    assert await client.latest_run() is None
    assert client._breaker._consecutive_failures == 1


@pytest.mark.asyncio
async def test_run_info_without_a_key_asks_nobody(monkeypatch):
    client = SunsethueClient("")
    calls = _counting_run_info(client, monkeypatch, {})

    assert await client.latest_run() is None
    assert calls == []


# Executing a Recheck verdict.


class _Repo:
    def __init__(self, rows):
        self.rows, self.stored = rows, []

    def decrypt_location(self, user):
        return (50.45, 30.52)

    async def get_cached_forecasts(self, user_id, forecast_dates, ttl_minutes):
        return self.rows

    async def upsert_forecast(self, **row):
        self.stored.append(row)


class _Sunsethue:
    is_configured = True

    def __init__(self, latest: datetime | None, answer: ForecastResult | None = None):
        self.latest, self.answer = latest, answer
        self.forecasts_asked = 0

    async def latest_run(self):
        return self.latest

    async def forecast_for_today(self, *args, **kwargs):
        self.forecasts_asked += 1
        if self.answer is None:
            raise SunsethueError("declined")
        return self.answer


class _NoOpenMeteo:
    async def forecast_for_today(self, *args, **kwargs):
        raise AssertionError("a held Sunsethue forecast must not be swapped for Open-Meteo")


def _held_row() -> ForecastCache:
    now = datetime.now(UTC)
    return ForecastCache(
        user_id=1,
        forecast_date=(now + timedelta(hours=3)).date(),
        event="sunset",
        provider="sunsethue",
        fetched_at=now - timedelta(hours=6),
        event_at=now + timedelta(hours=3),
        score=40,
        description="held",
        weather_data={},
        run_at=RUN_06Z,
        next_update_at=now - timedelta(minutes=5),
    )


def _fresh_answer() -> ForecastResult:
    now = datetime.now(UTC)
    return ForecastResult(
        provider="sunsethue",
        event=SolarEvent.SUNSET,
        forecast_date=(now + timedelta(hours=3)).date(),
        event_at=now + timedelta(hours=3),
        score=70,
        description="fresh",
        weather_data={},
        run_at=RUN_12Z,
        next_update_at=now + timedelta(hours=6),
    )


async def _today(repo: _Repo, sunsethue: _Sunsethue) -> ForecastResult:
    service = ForecastService(
        session=None,
        settings=SimpleNamespace(forecast_cache_ttl_minutes=300),
        weather_client=_NoOpenMeteo(),
        sunsethue_client=sunsethue,
        repository=repo,
    )
    result = await service.today_for_user(SimpleNamespace(id=1, timezone="Europe/Kyiv"), SolarEvent.SUNSET)
    assert service.verdict == "Recheck"
    return result


@pytest.mark.asyncio
async def test_a_late_run_keeps_the_held_forecast_without_spending_a_forecast():
    repo, sunsethue = _Repo([_held_row()]), _Sunsethue(latest=RUN_06Z, answer=_fresh_answer())

    result = await _today(repo, sunsethue)

    assert result.description == "held"
    assert sunsethue.forecasts_asked == 0
    assert repo.stored == []


@pytest.mark.asyncio
async def test_a_newer_run_is_fetched_and_stored_with_its_run():
    repo, sunsethue = _Repo([_held_row()]), _Sunsethue(latest=RUN_12Z, answer=_fresh_answer())

    result = await _today(repo, sunsethue)

    assert result.description == "fresh"
    assert sunsethue.forecasts_asked == 1
    (stored,) = repo.stored
    assert stored["run_at"] == RUN_12Z and stored["next_update_at"] == result.next_update_at


@pytest.mark.asyncio
async def test_when_run_info_cannot_say_sunsethue_is_asked_directly():
    repo, sunsethue = _Repo([_held_row()]), _Sunsethue(latest=None, answer=_fresh_answer())

    assert (await _today(repo, sunsethue)).description == "fresh"


@pytest.mark.asyncio
async def test_if_sunsethue_declines_the_held_forecast_beats_open_meteo():
    repo, sunsethue = _Repo([_held_row()]), _Sunsethue(latest=RUN_12Z, answer=None)

    result = await _today(repo, sunsethue)

    assert result.description == "held"
    assert repo.stored == []
