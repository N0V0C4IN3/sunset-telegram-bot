from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ForecastCache, User
from app.db.repository import Repository
from app.services.weather import ForecastResult, OpenMeteoClient


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
            if cached and _as_local_time(cached.sunset_at, timezone) > local_now:
                return _from_cache(cached)

        result = await self.weather_client.forecast_for_today(
            latitude,
            longitude,
            user.timezone,
        )
        await self.repo.upsert_forecast(
            user_id=user.id,
            forecast_date=result.forecast_date,
            sunset_at=result.sunset_at,
            score=result.score,
            description=result.description,
            weather_data=result.weather_data,
        )
        return result


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
