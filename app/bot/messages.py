from zoneinfo import ZoneInfo

from app.services.weather import ForecastResult


def format_forecast(result: ForecastResult, timezone: str) -> str:
    sunset_at = result.sunset_at
    if sunset_at.tzinfo is None:
        sunset_at = sunset_at.replace(tzinfo=ZoneInfo(timezone))
    else:
        sunset_at = sunset_at.astimezone(ZoneInfo(timezone))
    sunset_time = sunset_at.strftime("%H:%M")
    return (
        f"🌅 Захід сонця сьогодні: {result.score}/100\n"
        f"Час заходу: {sunset_time}\n\n"
        f"{result.description}"
    )


def settings_text(threshold: int, lead_time: int, subscribed: bool) -> str:
    notification_state = "увімкнено" if subscribed else "вимкнено"
    return (
        "⚙️ Налаштування\n\n"
        f"Сповіщення: {notification_state}\n"
        f"Поріг балів: {threshold}/100\n"
        f"Нагадати до заходу сонця: за {lead_time} хв"
    )


def score_info_text() -> str:
    return (
        "ℹ️ Як рахується бал заходу сонця\n\n"
        "Бот дивиться прогноз біля часу заходу сонця і ставить оцінку від 0 до 100.\n\n"
        "Що додає балів:\n"
        "• трохи високих хмар, які можуть гарно підсвітитися\n"
        "• корисна середня хмарність\n"
        "• менше низьких хмар біля горизонту\n"
        "• низький шанс дощу\n"
        "• добра видимість\n\n"
        "Що знижує оцінку:\n"
        "• щільні низькі хмари\n"
        "• високий шанс дощу\n"
        "• погана видимість, серпанок або ризик туману\n"
        "• дуже висока вологість\n\n"
        "Це прогноз, а не магічна кришталева куля. Але куля старається."
    )
