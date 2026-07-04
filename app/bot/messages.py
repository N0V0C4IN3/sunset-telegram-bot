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
        "Бот дивиться не одну годину, а вікно навколо заходу сонця: приблизно дві години до заходу і одну годину після. Найбільшу вагу має час безпосередньо перед заходом.\n\n"
        "Оцінка складається з п'яти частин:\n"
        "• 30% — баланс хмар: високі й середні хмари можуть дати колір\n"
        "• 25% — відкритість горизонту: багато низьких хмар сильно шкодить\n"
        "• 20% — ризик дощу або опадів\n"
        "• 15% — прозорість атмосфери: видимість, вологість, PM2.5, PM10, пил та аерозолі\n"
        "• 10% — стабільність прогнозу у вікні заходу\n\n"
        "Є також жорсткі обмеження: сильний дощ, дуже низька хмарність, погана видимість або брудне повітря можуть обмежити максимум балів, навіть якщо інші фактори гарні.\n\n"
        "Це все ще прогноз, не контракт із небом. Небо іноді читає документацію вибірково."
    )
