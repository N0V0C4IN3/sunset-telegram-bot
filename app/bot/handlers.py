import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.keyboards import cancel_keyboard, location_keyboard, main_keyboard, settings_keyboard
from app.bot.messages import format_forecast, score_info_text, settings_text
from app.config import Settings
from app.db.repository import Repository
from app.services.forecast_service import ForecastService
from app.services.timezone import timezone_for_coordinates
from app.services.weather import OpenMeteoClient, WeatherError

logger = logging.getLogger(__name__)
router = Router()

_session_factory: async_sessionmaker | None = None
_settings: Settings | None = None
_weather_client: OpenMeteoClient | None = None


def setup_router(session_factory: async_sessionmaker, settings: Settings, weather_client: OpenMeteoClient) -> Router:
    global _session_factory, _settings, _weather_client
    _session_factory = session_factory
    _settings = settings
    _weather_client = weather_client
    return router


def sessions() -> async_sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Session factory is not configured")
    return _session_factory


def open_session():
    return sessions()()


def app_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("Settings are not configured")
    return _settings


def weather_client() -> OpenMeteoClient:
    if _weather_client is None:
        raise RuntimeError("Weather client is not configured")
    return _weather_client


async def answer_callback(callback: CallbackQuery, text: str | None = None) -> None:
    try:
        await callback.answer(text)
    except TelegramBadRequest as exc:
        message = str(exc)
        if "query is too old" not in message and "query ID is invalid" not in message:
            raise
        logger.info("stale_callback_ignored")


@router.message(Command("start"))
async def start(message: Message) -> None:
    settings = app_settings()
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(
            message.from_user.id,
            settings.default_notification_threshold,
            settings.default_notification_lead_time_minutes,
        )
        has_location = user.latitude_encrypted is not None and user.longitude_encrypted is not None
        subscribed = user.settings.subscribed
        await session.commit()

    await message.answer(
        "🌅 Я можу оцінити, чи варто сьогодні ловити захід сонця.\n\n"
        "Поділіться локацією один раз, щоб я знав місцеву погоду і час заходу.",
        reply_markup=location_keyboard(),
    )
    if has_location:
        await message.answer("Можна вже перевірити прогноз на сьогодні.", reply_markup=main_keyboard(subscribed))


@router.message(Command("location"))
async def request_location(message: Message) -> None:
    await message.answer("📍 Поділіться локацією один раз, щоб я оновив прогноз заходу сонця.", reply_markup=location_keyboard())


@router.message(F.location)
async def save_location(message: Message) -> None:
    settings = app_settings()
    latitude = message.location.latitude
    longitude = message.location.longitude
    timezone = timezone_for_coordinates(latitude, longitude)
    async with open_session() as session:
        repo = Repository(session)
        await repo.get_or_create_user(
            message.from_user.id,
            settings.default_notification_threshold,
            settings.default_notification_lead_time_minutes,
        )
        await repo.save_location(message.from_user.id, latitude, longitude, timezone)
        user = await repo.get_user_with_settings(message.from_user.id)
        subscribed = user.settings.subscribed
        await session.commit()

    await message.answer(
        "Локацію збережено. Тепер працюю з вашим місцевим часом заходу сонця.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Що робимо далі?", reply_markup=main_keyboard(subscribed))


@router.message(Command("today"))
async def today_command(message: Message) -> None:
    await send_today(message.bot, message.chat.id, message.from_user.id)


@router.message(Command("subscribe"))
async def subscribe_command(message: Message) -> None:
    await set_subscription(message.bot, message.chat.id, message.from_user.id, True)


@router.message(Command("unsubscribe"))
async def unsubscribe_command(message: Message) -> None:
    await set_subscription(message.bot, message.chat.id, message.from_user.id, False)


@router.message(Command("settings"))
async def settings_command(message: Message) -> None:
    await show_settings(message.bot, message.chat.id, message.from_user.id)


@router.callback_query(F.data == "today")
async def today_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await send_today(callback.bot, callback.message.chat.id, callback.from_user.id)


@router.callback_query(F.data == "settings")
async def settings_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await show_settings(callback.bot, callback.message.chat.id, callback.from_user.id)


@router.callback_query(F.data == "subscribe")
async def subscribe_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_subscription(callback.bot, callback.message.chat.id, callback.from_user.id, True)


@router.callback_query(F.data == "unsubscribe")
async def unsubscribe_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_subscription(callback.bot, callback.message.chat.id, callback.from_user.id, False)


@router.callback_query(F.data == "score_info")
async def score_info_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await callback.message.answer(score_info_text(), reply_markup=main_keyboard())


@router.callback_query(F.data == "set_threshold")
async def set_threshold(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_pending(callback.bot, callback.message.chat.id, callback.from_user.id, "threshold")


@router.callback_query(F.data == "set_lead_time")
async def set_lead_time(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_pending(callback.bot, callback.message.chat.id, callback.from_user.id, "lead_time")


@router.callback_query(F.data == "cancel_input")
async def cancel_input(callback: CallbackQuery) -> None:
    await answer_callback(callback, "Скасовано")
    async with open_session() as session:
        repo = Repository(session)
        await repo.set_pending_input(callback.from_user.id, None)
        await session.commit()
    await callback.message.answer("Скасовано.", reply_markup=main_keyboard())


@router.message(F.text)
async def text_input(message: Message) -> None:
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_user_with_settings(message.from_user.id)
        if user is None or user.settings is None or user.settings.pending_input is None:
            await message.answer("Скористайтесь /today, /settings або поділіться локацією.")
            return

        pending = user.settings.pending_input
        raw_value = message.text.strip()
        try:
            value = int(raw_value)
        except ValueError:
            await message.answer("Введіть ціле число. Дроби залишимо для кулінарії.", reply_markup=cancel_keyboard())
            return

        if pending == "threshold":
            if not 0 <= value <= 100:
                await message.answer("Поріг має бути від 0 до 100.", reply_markup=cancel_keyboard())
                return
            await repo.update_threshold(message.from_user.id, value)
            await session.commit()
            await message.answer(f"Поріг балів оновлено: {value}/100.")
        elif pending == "lead_time":
            if not 15 <= value <= 180:
                await message.answer("Час завчасного сповіщення має бути від 15 до 180 хвилин.", reply_markup=cancel_keyboard())
                return
            await repo.update_lead_time(message.from_user.id, value)
            await session.commit()
            await message.answer(f"Сповіщення тепер приходитиме за {value} хв до заходу сонця.")

    await show_settings(message.bot, message.chat.id, message.from_user.id)


async def send_today(bot: Bot, chat_id: int, user_id: int) -> None:
    settings = app_settings()
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_user_with_settings(user_id)
        if user is None or user.latitude_encrypted is None or user.longitude_encrypted is None:
            await bot.send_message(chat_id, "Спочатку поділіться локацією, інакше я вгадуватиму по кавовій гущі.", reply_markup=location_keyboard())
            return
        subscribed = user.settings.subscribed
        timezone = user.timezone
        try:
            result = await ForecastService(session, settings, weather_client()).today_for_user(user)
            await session.commit()
        except WeatherError:
            logger.warning("forecast_unavailable")
            await bot.send_message(chat_id, "Прогноз погоди тимчасово недоступний. Спробуйте трохи пізніше.")
            return
    await bot.send_message(chat_id, format_forecast(result, timezone), reply_markup=main_keyboard(subscribed))


async def set_subscription(bot: Bot, chat_id: int, user_id: int, subscribed: bool) -> None:
    settings = app_settings()
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(
            user_id,
            settings.default_notification_threshold,
            settings.default_notification_lead_time_minutes,
        )
        has_location = user.latitude_encrypted is not None and user.longitude_encrypted is not None
        await repo.set_subscribed(user_id, subscribed)
        await session.commit()

    if subscribed and not has_location:
        await bot.send_message(
            chat_id,
            "Сповіщення увімкнено. Поділіться локацією, щоб я знав, коли у вас захід сонця.",
            reply_markup=location_keyboard(),
        )
    else:
        text = "Сповіщення увімкнено." if subscribed else "Сповіщення вимкнено."
        await bot.send_message(chat_id, text, reply_markup=main_keyboard(subscribed))


async def show_settings(bot: Bot, chat_id: int, user_id: int) -> None:
    settings = app_settings()
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(
            user_id,
            settings.default_notification_threshold,
            settings.default_notification_lead_time_minutes,
        )
        threshold = user.settings.threshold
        lead_time = user.settings.lead_time_minutes
        subscribed = user.settings.subscribed
        await session.commit()

    await bot.send_message(
        chat_id,
        settings_text(threshold, lead_time, subscribed),
        reply_markup=settings_keyboard(subscribed),
    )


async def set_pending(bot: Bot, chat_id: int, user_id: int, pending: str) -> None:
    async with open_session() as session:
        repo = Repository(session)
        await repo.set_pending_input(user_id, pending)
        await session.commit()

    if pending == "threshold":
        text = "Введіть поріг балів від 0 до 100."
    else:
        text = "Введіть, за скільки хвилин до заходу сонця нагадати: від 15 до 180."
    await bot.send_message(chat_id, text, reply_markup=cancel_keyboard())
