import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ForecastCache, User
from app.db.repository import Repository
from app.services.cache_verdict import Fetch, RetryPreferred, Serve, verdict_for
from app.services.sunsethue import PROVIDER_SUNSETHUE, SunsethueClient, SunsethueError
from app.services.weather import ForecastResult, OpenMeteoClient

logger = logging.getLogger(__name__)


class ForecastService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        weather_client: OpenMeteoClient,
        sunsethue_client: SunsethueClient,
        repository: Repository | None = None,
    ) -> None:
        self.repo = repository or Repository(session)
        self.settings = settings
        self.weather_client = weather_client
        self.sunsethue_client = sunsethue_client

    async def today_for_user(self, user: User, on_date: date | None = None) -> ForecastResult:
        """The user's next sunset, or the one on `on_date` when a day is named."""
        location = self.repo.decrypt_location(user)
        if location is None:
            raise ValueError("User has no location")
        latitude, longitude = location

        local_now = datetime.now(ZoneInfo(user.timezone))
        local_today = local_now.date()
        candidates = await self.repo.get_cached_forecasts(
            user.id,
            [local_today, local_today + timedelta(days=1)],
            self.settings.forecast_cache_ttl_minutes,
        )

        match verdict_for(candidates, local_now, self.sunsethue_client.is_configured, on_date):
            case Serve(cached):
                return _from_cache(cached)
            case RetryPreferred(held):
                upgraded = await self._retry_preferred_provider(latitude, longitude, user.timezone, on_date)
                if upgraded is None:
                    return _from_cache(held)
                await self._store(user.id, upgraded)
                return upgraded
            case Fetch():
                result = await self._fetch_forecast(latitude, longitude, user.timezone, on_date)
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
        on_date: date | None = None,
    ) -> ForecastResult | None:
        """Re-ask Sunsethue for a forecast we currently hold provisionally.

        Returns None when Sunsethue still cannot answer, in which case the caller
        serves the held forecast rather than spending another Open-Meteo call.
        """
        if not self.sunsethue_client.is_configured:
            return None
        try:
            return await self.sunsethue_client.forecast_for_today(latitude, longitude, timezone, on_date)
        except SunsethueError as exc:
            logger.info("sunsethue_retry_declined reason=%s", exc)
            return None

    async def _fetch_forecast(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        on_date: date | None = None,
    ) -> ForecastResult:
        if self.sunsethue_client.is_configured:
            try:
                return await self.sunsethue_client.forecast_for_today(latitude, longitude, timezone, on_date)
            except SunsethueError as exc:
                logger.warning("sunsethue_forecast_unavailable fallback=open_meteo reason=%s", exc)

        return await self.weather_client.forecast_for_today(
            latitude,
            longitude,
            timezone,
            on_date,
        )


def is_provisional(result: ForecastResult, sunsethue_client: SunsethueClient) -> bool:
    """True when Sunsethue could have answered but something else did."""
    return sunsethue_client.is_configured and result.provider != PROVIDER_SUNSETHUE


def _from_cache(cache: ForecastCache) -> ForecastResult:
    return ForecastResult(
        provider=cache.provider,
        forecast_date=cache.forecast_date,
        sunset_at=cache.sunset_at,
        score=cache.score,
        description=cache.description,
        weather_data=cache.weather_data,
    )
