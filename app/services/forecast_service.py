import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ForecastCache, User
from app.db.repository import Repository
from app.services.sunsethue import SunsethueClient, SunsethueError
from app.services.weather import ForecastResult, OpenMeteoClient

logger = logging.getLogger(__name__)

SUNSETHUE_MODEL_AVAILABLE_AT_UTC = (
    time(4, 30, tzinfo=UTC),
    time(10, 30, tzinfo=UTC),
    time(16, 30, tzinfo=UTC),
    time(22, 30, tzinfo=UTC),
)


class ForecastService:
    def __init__(self, session: AsyncSession, settings: Settings, weather_client: OpenMeteoClient) -> None:
        self.repo = Repository(session)
        self.settings = settings
        self.weather_client = weather_client

    async def today_for_user(self, user: User) -> ForecastResult:
        location = self.repo.decrypt_location(user)
        if location is None:
            raise ValueError("User has no location")
        latitude, longitude = location

        timezone = ZoneInfo(user.timezone)
        local_now = datetime.now(timezone)
        local_today = local_now.date()
        for forecast_date in [local_today, local_today + timedelta(days=1)]:
            cached = await self.repo.get_cached_forecast(
                user.id,
                forecast_date,
                self.settings.forecast_cache_ttl_minutes,
            )
            if cached and _cache_is_usable(cached, timezone, local_now):
                return _from_cache(cached)

        result = await self._fetch_forecast(latitude, longitude, user.timezone)
        await self.repo.upsert_forecast(
            user_id=user.id,
            forecast_date=result.forecast_date,
            sunset_at=result.sunset_at,
            score=result.score,
            description=result.description,
            weather_data=result.weather_data,
        )
        return result

    async def _fetch_forecast(self, latitude: float, longitude: float, timezone: str) -> ForecastResult:
        if not self.settings.sunsethue_api_key and not self.settings.sunsethue_fallback_api_key:
            return await self.weather_client.forecast_for_today(
                latitude,
                longitude,
                timezone,
            )

        try:
            return await SunsethueClient(
                self.settings.sunsethue_api_key,
                self.settings.sunsethue_fallback_api_key,
            ).forecast_for_today(
                latitude,
                longitude,
                timezone,
            )
        except SunsethueError as exc:
            logger.warning("sunsethue_forecast_unavailable fallback=open_meteo reason=%s", exc)

        return await self.weather_client.forecast_for_today(
            latitude,
            longitude,
            timezone,
        )


def _from_cache(cache: ForecastCache) -> ForecastResult:
    return ForecastResult(
        forecast_date=cache.forecast_date,
        sunset_at=cache.sunset_at,
        score=cache.score,
        description=cache.description,
        weather_data=cache.weather_data,
    )


def _as_local_time(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _cache_is_usable(cache: ForecastCache, timezone: ZoneInfo, local_now: datetime) -> bool:
    if _as_local_time(cache.sunset_at, timezone) <= local_now:
        return False
    if cache.weather_data.get("provider") != "sunsethue":
        return True

    fetched_at = cache.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    fetched_at_utc = fetched_at.astimezone(UTC)
    now_utc = local_now.astimezone(UTC)
    latest_model_update = _latest_sunsethue_model_update(now_utc)
    return latest_model_update is None or fetched_at_utc >= latest_model_update


def _latest_sunsethue_model_update(now_utc: datetime) -> datetime | None:
    today_updates = [
        datetime.combine(now_utc.date(), update_time)
        for update_time in SUNSETHUE_MODEL_AVAILABLE_AT_UTC
        if datetime.combine(now_utc.date(), update_time) <= now_utc
    ]
    if today_updates:
        return max(today_updates)

    yesterday = now_utc.date() - timedelta(days=1)
    return datetime.combine(yesterday, SUNSETHUE_MODEL_AVAILABLE_AT_UTC[-1])
