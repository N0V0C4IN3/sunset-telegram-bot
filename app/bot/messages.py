from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.weather import ForecastResult


def format_forecast(result: ForecastResult, timezone: str, provisional: bool = False) -> str:
    date_label = day_label(result, timezone)

    # The card image already carries the score, the day and the sunset time.
    # The caption repeats them anyway: it is what a screen reader announces and
    # what the chat list previews, so it has to stand on its own.
    lines = [
        f"🌅 Захід сонця {date_label} ({result.forecast_date:%d.%m.%Y}): {result.score}%",
        "",
        result.description,
    ]
    if provisional:
        lines.append("")
        lines.append(
            "⏳ Це попередня оцінка Open-Meteo: Sunsethue зараз не відповідає. "
            "Загляньте пізніше — бал може змінитися."
        )
    return "\n".join(lines)


def day_label(result: ForecastResult, timezone: str) -> str:
    """The word the card prints under the score."""
    today = datetime.now(ZoneInfo(timezone)).date()
    if result.forecast_date == today:
        return "сьогодні"
    if (result.forecast_date - today).days == 1:
        return "завтра"
    return result.forecast_date.strftime("%d.%m.%Y")


def local_sunset_time(result: ForecastResult, timezone: str) -> str:
    """HH:MM in the user's zone. Open-Meteo returns naive local times, Sunsethue
    returns UTC, so the two need different conversions."""
    tz = ZoneInfo(timezone)
    sunset_at = result.sunset_at
    if sunset_at.tzinfo is None:
        sunset_at = sunset_at.replace(tzinfo=tz)
    else:
        sunset_at = sunset_at.astimezone(tz)
    return sunset_at.strftime("%H:%M")


def location_text(location: tuple[float, float] | None, timezone: str | None) -> str:
    """One line describing the location currently held, for the settings view."""
    if location is None:
        return "Локація: ще не збережена"
    latitude, longitude = location
    return f"Локація: {latitude:.3f}, {longitude:.3f} ({timezone})"


def location_saved_text(*, replaced: bool) -> str:
    """A first save and a replacement are different events; say which happened."""
    if replaced:
        return "Локацію оновлено. Тепер працюю з вашим місцевим часом заходу сонця."
    return "Локацію збережено. Тепер працюю з вашим місцевим часом заходу сонця."


def settings_text(
    threshold: int,
    lead_time: int,
    subscribed: bool,
    location: tuple[float, float] | None = None,
    timezone: str | None = None,
) -> str:
    notification_state = "увімкнено" if subscribed else "вимкнено"
    return (
        "⚙️ Налаштування\n\n"
        f"Сповіщення: {notification_state}\n"
        f"Поріг прогнозу: {threshold}%\n"
        f"Нагадати до заходу сонця: за {lead_time} хв\n"
        f"{location_text(location, timezone)}"
    )


def score_info_text() -> str:
    return (
        "ℹ️ Як рахується прогноз заходу сонця\n\n"
        "Якщо налаштовано ключ Sunsethue, бот спершу бере прогноз Sunsethue: якість заходу у відсотках, час заходу, хмарність, напрямок сонця і золоту годину. Результат кешується, але оновлюється після наступного очікуваного оновлення моделі Sunsethue.\n\n"
        "Якщо Sunsethue недоступний, не має модельних даних для локації або ключ не налаштовано, бот автоматично переходить на Open-Meteo і рахує прогноз локально.\n\n"
        "Коли Open-Meteo підмінив Sunsethue через збій, така оцінка вважається тимчасовою. При наступному запиті бот знову спробує Sunsethue: якщо той уже ожив, ви побачите його прогноз, якщо ні — збережену оцінку Open-Meteo. Тому бал може змінитися між двома натисканнями, і це не тому, що бот передумав.\n\n"
        "Якщо Sunsethue мовчить багато разів поспіль, бот ненадовго перестає його смикати, щоб не змушувати вас чекати даремно. А якщо вичерпано денний ліміт запитів, чекає вже до наступної доби.\n\n"
        "Локальний розрахунок Open-Meteo дивиться не одну годину, а вікно навколо заходу сонця: приблизно дві години до заходу і одну годину після. Найбільшу вагу має час безпосередньо перед заходом.\n\n"
        "Якщо сьогоднішній захід уже минув, бот автоматично показує прогноз на наступний день і додає дату в повідомлення. Поки сьогоднішній захід ще попереду, кнопка «Завтра» показує наступний день; після заходу вона зникає, бо «Сьогодні» вже показує саме його.\n\n"
        "Open-Meteo оцінка складається з п'яти частин:\n"
        "• 30% — баланс хмар: високі й середні хмари можуть дати колір\n"
        "• 25% — відкритість горизонту: багато низьких хмар сильно шкодить\n"
        "• 20% — ризик дощу або опадів\n"
        "• 15% — прозорість атмосфери: видимість, вологість, PM2.5, PM10, пил та аерозолі\n"
        "• 10% — стабільність прогнозу у вікні заходу\n\n"
        "Є також жорсткі обмеження: сильний дощ, дуже низька хмарність, погана видимість або брудне повітря можуть обмежити максимальний відсоток, навіть якщо інші фактори гарні.\n\n"
        "Це все ще прогноз, не контракт із небом. Небо іноді читає документацію вибірково."
    )
