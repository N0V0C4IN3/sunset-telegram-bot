import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.messages import format_forecast
from app.config import Settings
from app.db.repository import Repository
from app.services.forecast_service import ForecastService
from app.services.weather import OpenMeteoClient, WeatherError

logger = logging.getLogger(__name__)


async def notification_loop(
    bot: Bot,
    session_factory: async_sessionmaker,
    settings: Settings,
    weather_client: OpenMeteoClient,
) -> None:
    interval_seconds = settings.notification_scan_interval_minutes * 60
    while True:
        try:
            sent = await run_notification_scan(bot, session_factory, settings, weather_client)
            logger.info("notification_scan_completed sent=%s", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification_scan_failed")
        await asyncio.sleep(interval_seconds)


async def run_notification_scan(
    bot: Bot,
    session_factory: async_sessionmaker,
    settings: Settings,
    weather_client: OpenMeteoClient,
) -> int:
    sent = 0
    async with session_factory() as session:
        repo = Repository(session)
        users = await repo.subscribed_users()
        for user in users:
            user_settings = user.settings
            local_now = datetime.now(ZoneInfo(user.timezone))
            if user_settings.last_notified_for_date == local_now.date():
                continue
            try:
                forecast = await ForecastService(session, settings, weather_client).today_for_user(user)
            except WeatherError:
                logger.warning("forecast_unavailable_during_notification")
                continue

            notify_at = forecast.sunset_at - timedelta(minutes=user_settings.lead_time_minutes)
            scan_window_end = notify_at + timedelta(minutes=settings.notification_scan_interval_minutes)
            if not (notify_at <= local_now <= scan_window_end):
                continue
            if forecast.score < user_settings.threshold:
                await repo.mark_notified(user.id, local_now.date())
                continue

            await bot.send_message(user.id, format_forecast(forecast, user.timezone))
            await repo.mark_notified(user.id, local_now.date())
            sent += 1

        deleted = await repo.delete_old_forecasts(settings.forecast_cache_retention_days)
        await session.commit()
        if deleted:
            logger.info("old_forecasts_deleted count=%s", deleted)
    return sent
