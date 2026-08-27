import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ForecastCache, User
from app.db.repository import Repository
from app.services.sunsethue import PROVIDER_SUNSETHUE, SunsethueClient, SunsethueError
from app.services.weather import ForecastResult, OpenMeteoClient

logger = logging.getLogger(__name__)

SUNSETHUE_MODEL_AVAILABLE_AT_UTC = (
    time(4, 30, tzinfo=UTC),
    time(10, 30, tzinfo=UTC),
    time(16, 30, tzinfo=UTC),
    time(22, 30, tzinfo=UTC),
)


class ForecastService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        weather_client: OpenMeteoClient,
        sunsethue_client: SunsethueClient,
    ) -> None:
        self.repo = Repository(session)
        self.settings = settings
        self.weather_client = weather_client
        self.sunsethue_client = sunsethue_client

    async def today_for_user(self, user: User) -> ForecastResult:
        location = self.repo.decrypt_location(user)
        if location is None:
            raise ValueError("User has no location")
        latitude, longitude = location

        timezone = ZoneInfo(user.timezone)
        local_now = datetime.now(timezone)
        local_today = local_now.date()

        held: ForecastCache | None = None
        for forecast_date in [local_today, local_today + timedelta(days=1)]:
            cached = await self.repo.get_cached_forecast(
                user.id,
                forecast_date,
                self.settings.forecast_cache_ttl_minutes,
            )
            if cached is None or not _sunset_is_upcoming(cached, timezone, local_now):
                continue
            if cached.provider == PROVIDER_SUNSETHUE:
                if _sunsethue_cache_is_current(cached, local_now):
                    return _from_cache(cached)
                continue
            # Provisional: servable, but Sunsethue gets re-asked before we use it.
            held = cached
            break

        if held is not None:
            upgraded = await self._retry_preferred_provider(latitude, longitude, user.timezone)
            if upgraded is None:
                return _from_cache(held)
            await self._store(user.id, upgraded)
            return upgraded

        result = await self._fetch_forecast(latitude, longitude, user.timezone)
        await self._store(user.id, result)
        return result

    async def _store(self, user_id: int, result: ForecastResult) -> None:
        await self.repo.upsert_forecast(
            user_id=user_id,
            forecast_date=result.forecast_date,
            provider=result.provider,
            sunset_at=result.sunset_at,
            score=result.score,
            description=result.description,
            weather_data=result.weather_data,
        )

    async def _retry_preferred_provider(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> ForecastResult | None:
        """Re-ask Sunsethue for a forecast we currently hold provisionally.

        Returns None when Sunsethue still cannot answer, in which case the caller
        serves the held forecast rather than spending another Open-Meteo call.
        """
        if not self.sunsethue_client.is_configured:
            return None
        try:
            return await self.sunsethue_client.forecast_for_today(latitude, longitude, timezone)
        except SunsethueError as exc:
            logger.info("sunsethue_retry_declined reason=%s", exc)
            return None

    async def _fetch_forecast(self, latitude: float, longitude: float, timezone: str) -> ForecastResult:
        if self.sunsethue_client.is_configured:
            try:
                return await self.sunsethue_client.forecast_for_today(latitude, longitude, timezone)
            except SunsethueError as exc:
                logger.warning("sunsethue_forecast_unavailable fallback=open_meteo reason=%s", exc)

        return await self.weather_client.forecast_for_today(
            latitude,
            longitude,
            timezone,
        )


def _from_cache(cache: ForecastCache) -> ForecastResult:
    return ForecastResult(
        provider=cache.provider,
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


def _sunset_is_upcoming(cache: ForecastCache, timezone: ZoneInfo, local_now: datetime) -> bool:
    return _as_local_time(cache.sunset_at, timezone) > local_now


def _sunsethue_cache_is_current(cache: ForecastCache, local_now: datetime) -> bool:
    """True while no Sunsethue model update has landed since this row was fetched."""
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
