import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.bot.handlers import setup_router
from app.config import get_settings
from app.db.session import create_session_factory
from app.logging_config import configure_logging
from app.services.scheduler import notification_loop
from app.services.weather import OpenMeteoClient

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if len(settings.location_encryption_key) < 16:
        raise RuntimeError("LOCATION_ENCRYPTION_KEY must be set to a stable secret with at least 16 characters")

    session_factory = create_session_factory(settings)
    weather_client = OpenMeteoClient()
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(setup_router(session_factory, settings, weather_client))

    scheduler_task = asyncio.create_task(notification_loop(bot, session_factory, settings, weather_client))
    logger.info("bot_starting")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler_task.cancel()
        await bot.session.close()
        logger.info("bot_stopped")


if __name__ == "__main__":
    asyncio.run(main())
