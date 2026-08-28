from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.services.weather import ForecastResult, WeatherError

PROVIDER_SUNSETHUE = "sunsethue"

# Sunsethue behaves the same on every deployment, so these stay constants rather
# than settings. Quota is separated from unreachability on purpose: a daily
# allowance recovers at the provider's own boundary, not after a short wait.
BREAKER_FAILURE_THRESHOLD = 5
BREAKER_COOLDOWN = timedelta(minutes=15)


class SunsethueError(WeatherError):
    pass


class SunsethueQuotaError(SunsethueError):
    pass


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

    def __init__(self, api_key: str, fallback_api_key: str = "") -> None:
        self.api_keys = [key for key in [api_key, fallback_api_key] if key]
        self._breaker = _Breaker()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_keys)

    async def forecast_for_today(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        on_date: date | None = None,
    ) -> ForecastResult:
        if not self.api_keys:
            raise SunsethueError("Sunsethue API key is not configured")
        if not self._breaker.allows(datetime.now(UTC)):
            raise SunsethueError("Sunsethue is cooling down after repeated failures")

        try:
            result = await self._forecast_for_today(latitude, longitude, timezone, on_date)
        except SunsethueQuotaError:
            self._breaker.record_quota_exhaustion(datetime.now(UTC))
            raise
        except SunsethueError:
            self._breaker.record_failure(datetime.now(UTC))
            raise
        self._breaker.record_success()
        return result

    async def _forecast_for_today(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        on_date: date | None = None,
    ) -> ForecastResult:
        tz = ZoneInfo(timezone)
        local_now = datetime.now(tz)
        local_today = local_now.date()

        async with httpx.AsyncClient(timeout=15) as client:
            if on_date is not None:
                payload = await self._fetch_event(client, latitude, longitude, on_date)
                return self._parse_event(payload, timezone)

            for forecast_date in [local_today, local_today + timedelta(days=1)]:
                payload = await self._fetch_event(client, latitude, longitude, forecast_date)
                result = self._parse_event(payload, timezone)
                if result.sunset_at.astimezone(tz) > local_now:
                    return result

        raise SunsethueError("Sunsethue response did not include an upcoming sunset")

    async def _fetch_event(
        self,
        client: httpx.AsyncClient,
        latitude: float,
        longitude: float,
        forecast_date: date,
    ) -> dict:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "date": forecast_date.isoformat(),
            "type": "sunset",
            "forecast": "true",
        }
        quota_errors = 0
        for api_key in self.api_keys:
            try:
                return await self._fetch_event_with_key(client, params, api_key)
            except SunsethueQuotaError:
                quota_errors += 1
                continue

        if quota_errors:
            raise SunsethueQuotaError("All Sunsethue API keys exceeded their quota")
        raise SunsethueError("Sunsethue API key is not configured")

    async def _fetch_event_with_key(self, client: httpx.AsyncClient, params: dict, api_key: str) -> dict:
        try:
            response = await client.get(self.event_url, params=params, headers={"x-api-key": api_key})
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

    def _parse_event(self, payload: dict, timezone: str) -> ForecastResult:
        try:
            data = payload["data"]
            if data["type"] != "sunset":
                raise SunsethueError("Sunsethue response was not for sunset")
            if not data.get("model_data"):
                raise SunsethueError("Sunsethue response did not include forecast model data")
            quality = float(data["quality"])
            sunset_at = _parse_utc_datetime(data["time"])
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
            forecast_date=sunset_at.astimezone(ZoneInfo(timezone)).date(),
            sunset_at=sunset_at,
            score=score,
            description=_description_for_quality(quality_text, data, timezone),
            weather_data=weather_data,
        )


def _next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time(0, tzinfo=UTC))


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _description_for_quality(quality_text: str, data: dict, timezone: str) -> str:
    openers = {
        "excellent": "Sunsethue дає дуже високий шанс на виразний захід",
        "great": "Sunsethue дає високий шанс на красивий захід",
        "good": "Sunsethue очікує добрі умови для заходу",
        "fair": "Sunsethue бачить змішані, але не безнадійні умови",
        "poor": "Sunsethue очікує слабкі умови для кольору",
    }
    opener = openers.get(quality_text, "Sunsethue оцінив захід сонця")
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
            details.append(_direction_text(float(direction)))
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


def _direction_text(direction: float) -> str:
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
    return f"сонце сідає у напрямку {names[index]} ({degrees}°)"


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
