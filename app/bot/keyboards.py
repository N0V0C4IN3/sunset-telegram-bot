from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_keyboard(subscribed: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌅 Сьогодні", callback_data="today"),
                InlineKeyboardButton(text="⚙️ Налаштування", callback_data="settings"),
            ],
            [InlineKeyboardButton(text="ℹ️ Як рахується бал", callback_data="score_info")],
            [
                InlineKeyboardButton(
                    text="🔕 Вимкнути сповіщення" if subscribed else "🔔 Увімкнути сповіщення",
                    callback_data="unsubscribe" if subscribed else "subscribe",
                )
            ],
        ]
    )


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Поділитися локацією", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def settings_keyboard(subscribed: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎚 Поріг балів", callback_data="set_threshold"),
                InlineKeyboardButton(text="⏰ Час завчасно", callback_data="set_lead_time"),
            ],
            [
                InlineKeyboardButton(
                    text="🔕 Вимкнути сповіщення" if subscribed else "🔔 Увімкнути сповіщення",
                    callback_data="unsubscribe" if subscribed else "subscribe",
                )
            ],
            [InlineKeyboardButton(text="🌅 Сьогодні", callback_data="today")],
            [InlineKeyboardButton(text="ℹ️ Як рахується бал", callback_data="score_info")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Скасувати", callback_data="cancel_input")]])
