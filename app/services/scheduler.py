import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.card import render_forecast_card
from app.bot.messages import format_forecast
from app.config import Settings
from app.db.models import User
from app.health import record_scan
from app.db.repository import Repository
from app.services.forecast_service import ForecastService, is_provisional
from app.services.sunsethue import SunsethueClient
from app.services.weather import ForecastResult, OpenMeteoClient, WeatherError

logger = logging.getLogger(__name__)


async def notification_loop(
    bot: Bot,
    session_factory: async_sessionmaker,
    settings: Settings,
    weather_client: OpenMeteoClient,
    sunsethue_client: SunsethueClient,
) -> None:
    interval_seconds = settings.notification_scan_interval_minutes * 60
    while True:
        try:
            sent = await run_notification_scan(
                bot,
                session_factory,
                settings,
                weather_client,
                sunsethue_client,
            )
            # Stamped only on a completed pass: the container healthcheck reads
            # this to tell a wedged loop from a quiet one.
            record_scan()
            logger.info("notification_scan_completed sent=%s", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification_scan_failed")
        await asyncio.sleep(interval_seconds)


async def notify_subscriber(
    bot: Bot,
    repo: Repository,
    user: User,
    settings: Settings,
    sunsethue_client: SunsethueClient,
    forecast: ForecastResult,
    local_now: datetime,
) -> bool:
    """Act on one subscriber's forecast. True when a notification was sent.

    Split out so the caller can put an error boundary around a single subscriber:
    a send that fails for one must not end the pass for the rest.
    """
    user_settings = user.settings
    notify_at = forecast.sunset_at - timedelta(minutes=user_settings.lead_time_minutes)
    scan_window_end = notify_at + timedelta(minutes=settings.notification_scan_interval_minutes)
    if not (notify_at <= local_now <= scan_window_end):
        return False

    provisional = is_provisional(forecast, sunsethue_client)
    if forecast.score < user_settings.threshold:
        # A provisional score must not consume the day: Sunsethue may yet
        # recover and report a sunset worth sending.
        if not provisional:
            await repo.mark_notified(user.id, local_now.date())
        return False

    caption = format_forecast(forecast, user.timezone, provisional=provisional)
    png = await render_forecast_card(forecast, user.timezone)
    await bot.send_photo(
        user.id,
        BufferedInputFile(png, filename="sunset.png"),
        caption=caption,
    )
    await repo.mark_notified(user.id, local_now.date())
    return True


async def run_notification_scan(
    bot: Bot,
    session_factory: async_sessionmaker,
    settings: Settings,
    weather_client: OpenMeteoClient,
    sunsethue_client: SunsethueClient,
) -> int:
    sent = 0
    async with session_factory() as session:
        repo = Repository(session)
        users = await repo.subscribed_users()
        for user in users:
            local_now = datetime.now(ZoneInfo(user.timezone))
            if user.settings.last_notified_for_date == local_now.date():
                continue
            try:
                forecast = await ForecastService(
                    session,
                    settings,
                    weather_client,
                    sunsethue_client,
                    repository=repo,
                ).today_for_user(user)
            except WeatherError:
                logger.warning("forecast_unavailable_during_notification")
                continue
            except asyncio.CancelledError:
                raise

            try:
                if await notify_subscriber(
                    bot, repo, user, settings, sunsethue_client, forecast, local_now
                ):
                    sent += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One subscriber blocking the bot must not end the pass. Roll back
                # only this subscriber's uncommitted work; everything already
                # committed below stands.
                await session.rollback()
                logger.warning("notification_failed_for_subscriber error=%s", type(exc).__name__)
                continue

            # Commit per subscriber, not once at the end: a failure later in the
            # pass must not roll back a Notification Decision already taken, or
            # that subscriber becomes eligible again and is notified twice for
            # the same sunset.
            await session.commit()

        deleted = await repo.delete_old_forecasts(settings.forecast_cache_retention_days)
        await session.commit()
        if deleted:
            logger.info("old_forecasts_deleted count=%s", deleted)
    return sent
