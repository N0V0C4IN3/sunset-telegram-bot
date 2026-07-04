from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import ForecastCache, User, UserSettings
from app.services.location_crypto import LocationCrypto


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.location_crypto = LocationCrypto(get_settings())

    async def get_or_create_user(self, user_id: int, threshold: int, lead_time: int) -> User:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user is None:
            user = User(id=user_id)
            user.settings = UserSettings(
                threshold=threshold,
                lead_time_minutes=lead_time,
            )
            self.session.add(user)
            await self.session.flush()
        elif user.settings is None:
            user.settings = UserSettings(
                user_id=user_id,
                threshold=threshold,
                lead_time_minutes=lead_time,
            )
            await self.session.flush()
        return user

    async def save_location(self, user_id: int, latitude: float, longitude: float, timezone: str) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user is None:
            user = User(id=user_id)
            self.session.add(user)
        user.latitude_encrypted = self.location_crypto.encrypt_coordinate(latitude)
        user.longitude_encrypted = self.location_crypto.encrypt_coordinate(longitude)
        user.timezone = timezone
        user.updated_at = datetime.now(UTC)
        if user.settings is None:
            user.settings = UserSettings(user_id=user_id)
        await self.session.execute(delete(ForecastCache).where(ForecastCache.user_id == user_id))
        await self.session.flush()

    async def get_user_with_settings(self, user_id: int) -> User | None:
        result = await self.session.execute(select(User).options(selectinload(User.settings)).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def set_subscribed(self, user_id: int, subscribed: bool) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user and user.settings:
            user.settings.subscribed = subscribed
            user.settings.updated_at = datetime.now(UTC)

    async def set_pending_input(self, user_id: int, pending_input: str | None) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user and user.settings:
            user.settings.pending_input = pending_input
            user.settings.updated_at = datetime.now(UTC)

    async def update_threshold(self, user_id: int, threshold: int) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user and user.settings:
            user.settings.threshold = threshold
            user.settings.pending_input = None
            user.settings.updated_at = datetime.now(UTC)

    async def update_lead_time(self, user_id: int, lead_time_minutes: int) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user and user.settings:
            user.settings.lead_time_minutes = lead_time_minutes
            user.settings.pending_input = None
            user.settings.updated_at = datetime.now(UTC)

    async def get_cached_forecast(self, user_id: int, forecast_date: date, ttl_minutes: int) -> ForecastCache | None:
        cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
        result = await self.session.execute(
            select(ForecastCache).where(
                ForecastCache.user_id == user_id,
                ForecastCache.forecast_date == forecast_date,
                ForecastCache.fetched_at >= cutoff,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_forecast(
        self,
        user_id: int,
        forecast_date: date,
        sunset_at: datetime,
        score: int,
        description: str,
        weather_data: dict,
    ) -> None:
        statement = insert(ForecastCache).values(
            user_id=user_id,
            forecast_date=forecast_date,
            fetched_at=datetime.now(UTC),
            sunset_at=sunset_at,
            score=score,
            description=description,
            weather_data=weather_data,
        )
        update_values = {
            "fetched_at": statement.excluded.fetched_at,
            "sunset_at": statement.excluded.sunset_at,
            "score": statement.excluded.score,
            "description": statement.excluded.description,
            "weather_data": statement.excluded.weather_data,
        }
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_forecast_cache_user_date",
                set_=update_values,
            )
        )

    async def subscribed_users(self) -> list[User]:
        result = await self.session.execute(
            select(User).options(selectinload(User.settings)).join(UserSettings).where(
                UserSettings.subscribed.is_(True),
                User.latitude_encrypted.is_not(None),
                User.longitude_encrypted.is_not(None),
            )
        )
        return list(result.scalars().all())

    def decrypt_location(self, user: User) -> tuple[float, float] | None:
        if user.latitude_encrypted is None or user.longitude_encrypted is None:
            return None
        return (
            self.location_crypto.decrypt_coordinate(user.latitude_encrypted),
            self.location_crypto.decrypt_coordinate(user.longitude_encrypted),
        )

    async def mark_notified(self, user_id: int, notified_date: date) -> None:
        user = await self.session.get(User, user_id, options=(selectinload(User.settings),))
        if user and user.settings:
            user.settings.last_notified_for_date = notified_date
            user.settings.updated_at = datetime.now(UTC)

    async def delete_old_forecasts(self, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        result = await self.session.execute(delete(ForecastCache).where(ForecastCache.fetched_at < cutoff))
        return result.rowcount or 0
