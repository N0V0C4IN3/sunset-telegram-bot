import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.card import render_forecast_card
from app.bot.keyboards import cancel_keyboard, location_keyboard, main_keyboard, settings_keyboard
from app.bot.messages import format_forecast, location_saved_text, score_info_text, settings_text
from app.config import Settings
from app.db.repository import Repository
from app.services.forecast_service import ForecastService, is_provisional
from app.services.sunsethue import SunsethueClient
from app.services.timezone import timezone_for_coordinates
from app.services.weather import OpenMeteoClient, WeatherError

logger = logging.getLogger(__name__)
router = Router()


@dataclass(frozen=True)
class HandlerContext:
    """Everything the handlers need from the outside world.

    aiogram DI is deliberately not used here (see CLAUDE.md); the handlers resolve
    their dependencies through one module-level slot instead of four loose globals,
    so a test can substitute the whole set at once via `handler_context`.
    """

    session_factory: async_sessionmaker
    settings: Settings
    weather: OpenMeteoClient
    sunsethue: SunsethueClient


_context: HandlerContext | None = None


def setup_router(
    session_factory: async_sessionmaker,
    settings: Settings,
    weather_client: OpenMeteoClient,
    sunsethue_client: SunsethueClient,
) -> Router:
    global _context
    _context = HandlerContext(session_factory, settings, weather_client, sunsethue_client)
    return router


def current_context() -> HandlerContext:
    if _context is None:
        raise RuntimeError("Handlers are not configured")
    return _context


@contextmanager
def handler_context(context: HandlerContext) -> Iterator[HandlerContext]:
    """Run a block with these dependencies, restoring whatever was there before.

    This is the seam the handler tests use. It still swaps a module-level slot —
    it does not eliminate it — so it is not safe to run two different contexts
    concurrently in one process.
    """
    global _context
    previous = _context
    _context = context
    try:
        yield context
    finally:
        _context = previous


def sessions() -> async_sessionmaker:
    return current_context().session_factory


def open_session():
    return sessions()()


def app_settings() -> Settings:
    return current_context().settings


def weather_client() -> OpenMeteoClient:
    return current_context().weather


def sunsethue_client() -> SunsethueClient:
    return current_context().sunsethue


async def answer_callback(callback: CallbackQuery, text: str | None = None) -> None:
    try:
        await callback.answer(text)
    except TelegramBadRequest as exc:
        message = str(exc)
        if "query is too old" not in message and "query ID is invalid" not in message:
            raise
        logger.info("stale_callback_ignored")


def from_photo(callback: CallbackQuery) -> bool:
    """True when the callback came from a card, which cannot become text."""
    return bool(callback.message and callback.message.photo)


async def send_or_edit(
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    replace: bool = False,
) -> None:
    """Edit the message a callback came from; send a fresh one for commands.

    Editing keeps the chat from filling with stacked keyboards. Telegram rejects
    an edit that changes nothing, which is a no-op here rather than an error.

    `replace` is for the one transition that cannot be an edit: editMessageText
    only accepts text, rich and game messages, so a card has to be deleted and a
    text message sent in its place.
    """
    if message_id is not None and replace:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.info("card_delete_failed")
        message_id = None

    if message_id is not None:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            logger.info("message_edit_failed falling_back=send")
    await bot.send_message(chat_id, text, reply_markup=reply_markup)


async def send_or_edit_card(
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    png: bytes,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Show the score card, editing in place when we came from a callback.

    editMessageMedia replaces a text message with media as well as swapping one
    photo for another, so both /settings -> card and card -> card edit cleanly.
    """
    photo = BufferedInputFile(png, filename="sunset.png")
    if message_id is not None:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            logger.info("card_edit_failed falling_back=send")
    await bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup)


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
        existing = await repo.get_or_create_user(
            message.from_user.id,
            settings.default_notification_threshold,
            settings.default_notification_lead_time_minutes,
        )
        # Read before the save: afterwards there is always a location, so this is
        # the only moment that can tell a first save from a replacement.
        replaced = existing.latitude_encrypted is not None and existing.longitude_encrypted is not None
        await repo.save_location(message.from_user.id, latitude, longitude, timezone)
        user = await repo.get_user_with_settings(message.from_user.id)
        subscribed = user.settings.subscribed
        await session.commit()

    await message.answer(
        location_saved_text(replaced=replaced),
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
    await send_today(callback.bot, callback.message.chat.id, callback.from_user.id, callback.message.message_id)


@router.callback_query(F.data == "tomorrow")
async def tomorrow_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await send_today(
        callback.bot,
        callback.message.chat.id,
        callback.from_user.id,
        callback.message.message_id,
        next_day=True,
    )


@router.callback_query(F.data == "settings")
async def settings_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await show_settings(
        callback.bot, callback.message.chat.id, callback.from_user.id, callback.message.message_id, from_photo(callback)
    )


@router.callback_query(F.data == "subscribe")
async def subscribe_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_subscription(
        callback.bot, callback.message.chat.id, callback.from_user.id, True, callback.message.message_id, from_photo(callback)
    )


@router.callback_query(F.data == "unsubscribe")
async def unsubscribe_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_subscription(
        callback.bot, callback.message.chat.id, callback.from_user.id, False, callback.message.message_id, from_photo(callback)
    )


@router.callback_query(F.data == "score_info")
async def score_info_callback(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    subscribed = await user_is_subscribed(callback.from_user.id)
    await send_or_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        score_info_text(),
        main_keyboard(subscribed),
        replace=from_photo(callback),
    )


@router.callback_query(F.data == "set_threshold")
async def set_threshold(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_pending(
        callback.bot, callback.message.chat.id, callback.from_user.id, "threshold", callback.message.message_id, from_photo(callback)
    )


@router.callback_query(F.data == "set_lead_time")
async def set_lead_time(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    await set_pending(
        callback.bot, callback.message.chat.id, callback.from_user.id, "lead_time", callback.message.message_id, from_photo(callback)
    )


@router.callback_query(F.data == "change_location")
async def change_location(callback: CallbackQuery) -> None:
    await answer_callback(callback)
    # The location prompt is a reply keyboard, which cannot ride on an edit, so
    # this always sends a new message instead of replacing the settings one.
    await callback.bot.send_message(
        callback.message.chat.id,
        "📍 Поділіться новою локацією, і я перерахую прогноз для неї.",
        reply_markup=location_keyboard(),
    )


@router.callback_query(F.data == "cancel_input")
async def cancel_input(callback: CallbackQuery) -> None:
    await answer_callback(callback, "Скасовано")
    async with open_session() as session:
        repo = Repository(session)
        await repo.set_pending_input(callback.from_user.id, None)
        user = await repo.get_user_with_settings(callback.from_user.id)
        subscribed = bool(user and user.settings and user.settings.subscribed)
        await session.commit()
    await send_or_edit(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        "Скасовано.",
        main_keyboard(subscribed),
        replace=from_photo(callback),
    )


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
                await message.answer("Поріг має бути від 0% до 100%.", reply_markup=cancel_keyboard())
                return
            await repo.update_threshold(message.from_user.id, value)
            await session.commit()
            await message.answer(f"Поріг прогнозу оновлено: {value}%.")
        elif pending == "lead_time":
            if not 15 <= value <= 180:
                await message.answer("Час завчасного сповіщення має бути від 15 до 180 хвилин.", reply_markup=cancel_keyboard())
                return
            await repo.update_lead_time(message.from_user.id, value)
            await session.commit()
            await message.answer(f"Сповіщення тепер приходитиме за {value} хв до заходу сонця.")

    await show_settings(message.bot, message.chat.id, message.from_user.id)


async def send_today(
    bot: Bot,
    chat_id: int,
    user_id: int,
    message_id: int | None = None,
    next_day: bool = False,
) -> None:
    settings = app_settings()
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_user_with_settings(user_id)
        if user is None or user.latitude_encrypted is None or user.longitude_encrypted is None:
            await bot.send_message(chat_id, "Спочатку поділіться локацією, інакше я вгадуватиму по кавовій гущі.", reply_markup=location_keyboard())
            return
        subscribed = user.settings.subscribed
        timezone = user.timezone
        local_today = datetime.now(ZoneInfo(timezone)).date()
        on_date = local_today + timedelta(days=1) if next_day else None
        try:
            result = await ForecastService(
                session,
                settings,
                weather_client(),
                sunsethue_client(),
            ).today_for_user(user, on_date)
            await session.commit()
        except WeatherError:
            logger.warning("forecast_unavailable")
            await send_or_edit(
                bot, chat_id, message_id, "Прогноз погоди тимчасово недоступний. Спробуйте трохи пізніше."
            )
            return

    # Offer Завтра only while today's sunset is still ahead; once it passes,
    # Сьогодні already serves tomorrow and there is no further day to show.
    show_next_day = result.forecast_date == local_today
    caption = format_forecast(result, timezone, provisional=is_provisional(result, sunsethue_client()))
    png = await render_forecast_card(result, timezone)
    await send_or_edit_card(
        bot,
        chat_id,
        message_id,
        png,
        caption,
        main_keyboard(subscribed, show_next_day=show_next_day),
    )


async def set_subscription(
    bot: Bot,
    chat_id: int,
    user_id: int,
    subscribed: bool,
    message_id: int | None = None,
    replace: bool = False,
) -> None:
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
        await send_or_edit(bot, chat_id, message_id, text, main_keyboard(subscribed), replace=replace)


async def show_settings(
    bot: Bot, chat_id: int, user_id: int, message_id: int | None = None, replace: bool = False
) -> None:
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
        # Decrypted for display only; it is never logged.
        location = repo.decrypt_location(user)
        timezone = user.timezone
        await session.commit()

    await send_or_edit(
        bot,
        chat_id,
        message_id,
        settings_text(threshold, lead_time, subscribed, location, timezone),
        settings_keyboard(subscribed),
        replace=replace,
    )


async def set_pending(
    bot: Bot, chat_id: int, user_id: int, pending: str, message_id: int | None = None, replace: bool = False
) -> None:
    async with open_session() as session:
        repo = Repository(session)
        await repo.set_pending_input(user_id, pending)
        await session.commit()

    if pending == "threshold":
        text = "Введіть поріг прогнозу від 0% до 100%."
    else:
        text = "Введіть, за скільки хвилин до заходу сонця нагадати: від 15 до 180."
    await send_or_edit(bot, chat_id, message_id, text, cancel_keyboard(), replace=replace)


async def user_is_subscribed(user_id: int) -> bool:
    async with open_session() as session:
        repo = Repository(session)
        user = await repo.get_user_with_settings(user_id)
        return bool(user and user.settings and user.settings.subscribed)
