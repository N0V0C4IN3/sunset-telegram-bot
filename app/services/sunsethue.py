import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.services.solar import WORDS, SolarEvent, days_to_ask
from app.services.weather import ForecastResult, WeatherError

logger = logging.getLogger(__name__)

PROVIDER_SUNSETHUE = "sunsethue"

# Sunsethue behaves the same on every deployment, so these stay constants rather
# than settings. Quota is separated from unreachability on purpose: a daily
# allowance recovers at the provider's own boundary, not after a short wait.
BREAKER_FAILURE_THRESHOLD = 5
BREAKER_COOLDOWN = timedelta(minutes=15)

# /run-info costs a credit, so its answer is shared by every caller until the
# next run is due, and re-asked this often while that run is overdue.
RUN_INFO_RECHECK = timedelta(minutes=10)

# When each day's four runs are published (UTC), only to tell users when scores
# can change: the cache goes by the run each response names instead. Sunsethue
# moved every run 30 minutes later from the 12z run of 25 Nov 2026; once that
# day is past, RUN_TIMES_BEFORE_CHANGE and the switch can be deleted.
RUN_SCHEDULE_CHANGED_AT = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
RUN_TIMES_BEFORE_CHANGE = (
    time(4, 30, tzinfo=UTC),
    time(10, 30, tzinfo=UTC),
    time(16, 30, tzinfo=UTC),
    time(22, 30, tzinfo=UTC),
)
RUN_TIMES = (
    time(5, 0, tzinfo=UTC),
    time(11, 0, tzinfo=UTC),
    time(17, 0, tzinfo=UTC),
    time(23, 0, tzinfo=UTC),
)


def run_times(at: datetime) -> tuple[time, ...]:
    """The publication times in force at `at`."""
    return RUN_TIMES if at >= RUN_SCHEDULE_CHANGED_AT else RUN_TIMES_BEFORE_CHANGE


class SunsethueError(WeatherError):
    pass


class SunsethueQuotaError(SunsethueError):
    pass


@dataclass(frozen=True)
class _RunInfo:
    last_run: datetime
    recheck_at: datetime


class _Breaker:
    """Tracks whether Sunsethue is worth asking, shared across every user."""

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._blocked_until: datetime | None = None

    def allows(self, now: datetime) -> bool:
        if self._blocked_until is None:
            return True
        if now < self._blocked_until:
            return False
        # Cooldown lapsed. The failure count deliberately survives, so the single
        # probe this permits re-trips the breaker if it fails.
        self._blocked_until = None
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._blocked_until = None

    def record_failure(self, now: datetime) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= BREAKER_FAILURE_THRESHOLD:
            self._blocked_until = now + BREAKER_COOLDOWN

    def record_quota_exhaustion(self, now: datetime) -> None:
        self._consecutive_failures = 0
        self._blocked_until = _next_utc_midnight(now)


class SunsethueClient:
    event_url = "https://api.sunsethue.com/event"
    run_info_url = "https://api.sunsethue.com/run-info"

    def __init__(self, api_key: str, fallback_api_key: str = "") -> None:
        self.api_keys = [key for key in [api_key, fallback_api_key] if key]
        self._breaker = _Breaker()
        self._run_info: _RunInfo | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_keys)

    async def forecast_for_today(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        event: SolarEvent,
        on_date: date | None = None,
    ) -> ForecastResult:
        if not self.api_keys:
            raise SunsethueError("Sunsethue API key is not configured")
        if not self._breaker.allows(datetime.now(UTC)):
            raise SunsethueError("Sunsethue is cooling down after repeated failures")

        try:
            result = await self._forecast_for_today(latitude, longitude, timezone, event, on_date)
        except SunsethueQuotaError:
            self._breaker.record_quota_exhaustion(datetime.now(UTC))
            raise
        except SunsethueError:
            self._breaker.record_failure(datetime.now(UTC))
            raise
        self._breaker.record_success()
        self._hold_served_run(result.run_at)
        return result

    def _hold_served_run(self, served_run: datetime | None) -> None:
        """Never claim a newer run than /event actually serves.

        /run-info can name a run before /event serves it. Left alone, every
        recheck in that gap would see a newer run, re-ask, get the old one back,
        and spend five credits each time. Holding the served run for
        RUN_INFO_RECHECK costs one /run-info credit per recheck instead.
        """
        held = self._run_info
        if served_run is None or held is None or served_run >= held.last_run:
            return
        recheck_at = min(held.recheck_at, datetime.now(UTC) + RUN_INFO_RECHECK)
        self._run_info = _RunInfo(served_run, recheck_at)

    async def latest_run(self) -> datetime | None:
        """The run Sunsethue's newest data comes from, or None when it cannot say.

        One credit per call, behind the same breaker as forecasts. The answer is
        held for every caller until the next run is due, or for RUN_INFO_RECHECK
        while that run is overdue, so a delayed run costs a credit per recheck
        rather than five per cached forecast.
        """
        now = datetime.now(UTC)
        if self._run_info is not None and now < self._run_info.recheck_at:
            return self._run_info.last_run
        if not self.api_keys or not self._breaker.allows(now):
            return None

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                payload = await self._get(client, self.run_info_url, {})
            last_run = _parse_utc_datetime(str(payload["last_run"]))
            next_run_time = _optional_utc_datetime(payload.get("next_run_time"))
        except SunsethueQuotaError:
            self._breaker.record_quota_exhaustion(now)
            logger.info("sunsethue_run_info_unavailable reason=quota")
            return None
        except (SunsethueError, KeyError, ValueError) as exc:
            self._breaker.record_failure(now)
            logger.info("sunsethue_run_info_unavailable reason=%s", type(exc).__name__)
            return None

        self._breaker.record_success()
        due = next_run_time is not None and next_run_time > now
        self._run_info = _RunInfo(last_run, next_run_time if due else now + RUN_INFO_RECHECK)
        return last_run

    async def _forecast_for_today(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        event: SolarEvent,
        on_date: date | None = None,
    ) -> ForecastResult:
        tz = ZoneInfo(timezone)
        local_now = datetime.now(tz)

        async with httpx.AsyncClient(timeout=15) as client:
            if on_date is not None:
                payload = await self._fetch_event(client, latitude, longitude, on_date, event)
                return self._parse_event(payload, timezone, event)

            # Every event costs credits, so the day is chosen locally rather than
            # by asking about today and discarding it once it turns out to be past.
            # Tomorrow stays behind it as a fallback: if Sunsethue places today's
            # event earlier than the equation does, the answer comes back already
            # past, and that is a disagreement to absorb, not a provider failure.
            days = days_to_ask(event, local_now, latitude, longitude)
            if days[-1] == local_now.date():
                days.append(local_now.date() + timedelta(days=1))
            for forecast_date in days:
                payload = await self._fetch_event(client, latitude, longitude, forecast_date, event)
                result = self._parse_event(payload, timezone, event)
                if result.event_at.astimezone(tz) > local_now:
                    return result

        raise SunsethueError(f"Sunsethue response did not include an upcoming {event}")

    async def _fetch_event(
        self,
        client: httpx.AsyncClient,
        latitude: float,
        longitude: float,
        forecast_date: date,
        event: SolarEvent,
    ) -> dict:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "date": forecast_date.isoformat(),
            "type": str(event),
            "forecast": "true",
        }
        return await self._get(client, self.event_url, params)

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict) -> dict:
        """GET with the first key that still has quota."""
        quota_errors = 0
        for api_key in self.api_keys:
            try:
                return await self._get_with_key(client, url, params, api_key)
            except SunsethueQuotaError:
                quota_errors += 1
                continue

        if quota_errors:
            raise SunsethueQuotaError("All Sunsethue API keys exceeded their quota")
        raise SunsethueError("Sunsethue API key is not configured")

    async def _get_with_key(self, client: httpx.AsyncClient, url: str, params: dict, api_key: str) -> dict:
        try:
            response = await client.get(url, params=params, headers={"x-api-key": api_key})
        except httpx.HTTPError as exc:
            raise SunsethueError("Sunsethue request failed") from exc

        payload = _response_json(response)
        if response.status_code >= 400:
            if _is_quota_error(payload):
                raise SunsethueQuotaError("Sunsethue API key exceeded its quota")
            message = payload.get("message") if isinstance(payload, dict) else None
            detail = f": {message}" if message else ""
            raise SunsethueError(f"Sunsethue returned HTTP {response.status_code}{detail}")
        return payload

    def _parse_event(self, payload: dict, timezone: str, event: SolarEvent) -> ForecastResult:
        try:
            data = payload["data"]
            if data["type"] != event:
                raise SunsethueError(f"Sunsethue response was not for {event}")
            if not data.get("model_data"):
                raise SunsethueError("Sunsethue response did not include forecast model data")
            quality = float(data["quality"])
            event_at = _parse_utc_datetime(data["time"])
            score = round(max(0, min(1, quality)) * 100)
            quality_text = str(data.get("quality_text", "")).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise SunsethueError("Sunsethue response did not include expected fields") from exc

        weather_data = {
            "provider": "sunsethue",
            "model_data": data.get("model_data"),
            "quality": quality,
            "quality_text": quality_text,
            "cloud_cover": data.get("cloud_cover"),
            "direction": data.get("direction"),
            "magics": data.get("magics"),
            "location": payload.get("location"),
            "grid_location": payload.get("grid_location"),
            "fetched_at": payload.get("time"),
        }
        return ForecastResult(
            provider=PROVIDER_SUNSETHUE,
            forecast_date=event_at.astimezone(ZoneInfo(timezone)).date(),
            event_at=event_at,
            score=score,
            description=_description_for_quality(quality_text, data, timezone, event),
            weather_data=weather_data,
            event=event,
            run_at=_optional_utc_datetime(payload.get("run")),
            next_update_at=_optional_utc_datetime(payload.get("next_expected_update")),
        )


def _next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time(0, tzinfo=UTC))


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_utc_datetime(value: object) -> datetime | None:
    """A UTC timestamp the forecast can do without: unreadable reads as absent."""
    if not isinstance(value, str):
        return None
    try:
        return _parse_utc_datetime(value)
    except ValueError:
        return None


def _response_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SunsethueError("Sunsethue response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SunsethueError("Sunsethue response was not a JSON object")
    return payload


def _is_quota_error(payload: dict) -> bool:
    code = payload.get("code")
    message = str(payload.get("message", "")).lower()
    return code == 204 or ("quota" in message and ("exceeded" in message or "daily" in message))


def _description_for_quality(quality_text: str, data: dict, timezone: str, event: SolarEvent) -> str:
    words = WORDS[event]
    openers = {
        "excellent": f"Sunsethue дає дуже високий шанс на виразний {words.noun}",
        "great": f"Sunsethue дає високий шанс на красивий {words.noun}",
        "good": f"Sunsethue очікує добрі умови для {words.of_noun}",
        "fair": "Sunsethue бачить змішані, але не безнадійні умови",
        "poor": "Sunsethue очікує слабкі умови для кольору",
    }
    opener = openers.get(quality_text, f"Sunsethue оцінив {words.noun} сонця")
    details: list[str] = []

    cloud_cover = data.get("cloud_cover")
    if cloud_cover is not None:
        try:
            details.append(_cloud_cover_text(float(cloud_cover)))
        except (TypeError, ValueError):
            pass

    direction = data.get("direction")
    if direction is not None:
        try:
            details.append(_direction_text(float(direction), event))
        except (TypeError, ValueError):
            pass

    magics = data.get("magics") or {}
    golden_hour = magics.get("golden_hour")
    golden_hour_text = _golden_hour_text(golden_hour, timezone)
    if golden_hour_text:
        details.append(golden_hour_text)

    if not details:
        return f"{opener}."
    return f"{opener}: {', '.join(details[:3])}."


def _cloud_cover_text(cloud_cover: float) -> str:
    percent = round(max(0, min(1, cloud_cover)) * 100)
    if percent < 15:
        return f"хмар майже немає ({percent}%), кольори можуть бути спокійніші"
    if percent < 35:
        return f"хмар небагато ({percent}%), горизонт має бути досить відкритий"
    if percent < 70:
        return f"хмарність збалансована ({percent}%), є матеріал для кольору"
    if percent < 90:
        return f"хмар багато ({percent}%), ефект залежатиме від просвітів біля горизонту"
    return f"небо майже затягнуте ({percent}%), шанс на яскравий колір нижчий"


def _direction_text(direction: float, event: SolarEvent) -> str:
    degrees = round(direction) % 360
    names = [
        "північ",
        "північний схід",
        "схід",
        "південний схід",
        "південь",
        "південний захід",
        "захід",
        "північний захід",
    ]
    index = round(degrees / 45) % len(names)
    return f"сонце {WORDS[event].verb} у напрямку {names[index]} ({degrees}°)"


def _golden_hour_text(golden_hour: object, timezone: str) -> str | None:
    if not isinstance(golden_hour, list) or len(golden_hour) != 2:
        return None
    try:
        tz = ZoneInfo(timezone)
        start = _parse_utc_datetime(str(golden_hour[0])).astimezone(tz).strftime("%H:%M")
        end = _parse_utc_datetime(str(golden_hour[1])).astimezone(tz).strftime("%H:%M")
    except (ValueError, TypeError):
        return None
    return f"золота година приблизно {start}-{end}"
