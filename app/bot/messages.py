from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.weather import ForecastResult


def format_forecast(result: ForecastResult, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    sunset_at = result.sunset_at
    if sunset_at.tzinfo is None:
        sunset_at = sunset_at.replace(tzinfo=tz)
    else:
        sunset_at = sunset_at.astimezone(tz)

    today = datetime.now(tz).date()
    if result.forecast_date == today:
        date_label = "сьогодні"
    elif (result.forecast_date - today).days == 1:
        date_label = "завтра"
    else:
        date_label = result.forecast_date.strftime("%d.%m.%Y")

    sunset_time = sunset_at.strftime("%H:%M")
    return (
        f"🌅 Захід сонця {date_label} ({result.forecast_date:%d.%m.%Y}): {result.score}/100\n"
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
        "Бот дивиться не одну годину, а вікно навколо заходу сонця: приблизно дві години до заходу і одну годину після. Найбільшу вагу має час безпосередньо перед заходом.\n\n"
        "Якщо сьогоднішній захід уже минув, бот автоматично показує прогноз на наступний день і додає дату в повідомлення.\n\n"
        "Оцінка складається з п'яти частин:\n"
        "• 30% — баланс хмар: високі й середні хмари можуть дати колір\n"
        "• 25% — відкритість горизонту: багато низьких хмар сильно шкодить\n"
        "• 20% — ризик дощу або опадів\n"
        "• 15% — прозорість атмосфери: видимість, вологість, PM2.5, PM10, пил та аерозолі\n"
        "• 10% — стабільність прогнозу у вікні заходу\n\n"
        "Є також жорсткі обмеження: сильний дощ, дуже низька хмарність, погана видимість або брудне повітря можуть обмежити максимум балів, навіть якщо інші фактори гарні.\n\n"
        "Це все ще прогноз, не контракт із небом. Небо іноді читає документацію вибірково."
    )
