from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(
    subscribed: bool = False,
    *,
    show_next_day: bool = False,
    settings_open: bool = False,
) -> InlineKeyboardMarkup:
    """The one keyboard.

    `show_next_day` adds the Завтра button, and is only true while today's sunset
    is still ahead — after it, Сьогодні already serves tomorrow and there is no
    second day inside the forecast window to offer.
    """
    days = [InlineKeyboardButton(text="🌅 Сьогодні", callback_data="today")]
    if show_next_day:
        days.append(InlineKeyboardButton(text="🌇 Завтра", callback_data="tomorrow"))

    rows = [days]
    if settings_open:
        rows.append(
            [
                InlineKeyboardButton(text="🎚 Поріг прогнозу", callback_data="set_threshold"),
                InlineKeyboardButton(text="⏰ Час завчасно", callback_data="set_lead_time"),
            ]
        )
        rows.append([InlineKeyboardButton(text="📍 Змінити локацію", callback_data="change_location")])
    rows.append(
        [
            InlineKeyboardButton(
                text="🔕 Вимкнути сповіщення" if subscribed else "🔔 Увімкнути сповіщення",
                callback_data="unsubscribe" if subscribed else "subscribe",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
            InlineKeyboardButton(text="ℹ️ Як рахується бал", callback_data="score_info"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Поділитися локацією", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def settings_keyboard(subscribed: bool, *, show_next_day: bool = False) -> InlineKeyboardMarkup:
    return main_keyboard(subscribed, show_next_day=show_next_day, settings_open=True)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Скасувати", callback_data="cancel_input")]])
