from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.cache_verdict import SUNSETHUE_MODEL_AVAILABLE_AT_UTC
from app.services.solar import SolarEvent
from app.services.weather import ForecastResult

HEADLINE = {SolarEvent.SUNSET: "🌅 Захід сонця", SolarEvent.SUNRISE: "🌄 Схід сонця"}


def format_forecast(result: ForecastResult, timezone: str, provisional: bool = False) -> str:
    date_label = day_label(result, timezone)

    # The card image already carries the score, the day and the event time.
    # The caption repeats them anyway: it is what a screen reader announces and
    # what the chat list previews, so it has to stand on its own.
    lines = [
        f"{HEADLINE[result.event]} {date_label} ({result.forecast_date:%d.%m.%Y}): {result.score}%",
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


def local_event_time(result: ForecastResult, timezone: str) -> str:
    """HH:MM in the user's zone. Open-Meteo returns naive local times, Sunsethue
    returns UTC, so the two need different conversions."""
    tz = ZoneInfo(timezone)
    event_at = result.event_at
    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=tz)
    else:
        event_at = event_at.astimezone(tz)
    return event_at.strftime("%H:%M")


def location_text(timezone: str | None) -> str:
    """One line describing the location held, for the settings view.

    Only the timezone derived from it is shown, never the coordinates: the
    timezone is enough to tell a stale location from a bad forecast.
    `None` means no location is saved.
    """
    if timezone is None:
        return "Локація: ще не збережена"
    return f"Локація: збережена, часовий пояс {timezone}"


def location_saved_text(*, replaced: bool) -> str:
    """A first save and a replacement are different events; say which happened."""
    if replaced:
        return "Локацію оновлено. Тепер працюю з вашим місцевим часом заходу сонця."
    return "Локацію збережено. Тепер працюю з вашим місцевим часом заходу сонця."


def settings_text(
    threshold: int,
    lead_time: int,
    subscribed: bool,
    timezone: str | None = None,
) -> str:
    notification_state = "увімкнено" if subscribed else "вимкнено"
    return (
        "⚙️ Налаштування\n\n"
        f"Сповіщення: {notification_state}\n"
        f"Поріг прогнозу: {threshold}%\n"
        f"Нагадати до заходу сонця: за {lead_time} хв\n"
        f"{location_text(timezone)}"
    )


def model_update_times(timezone: str | None, now: datetime) -> list[str]:
    """HH:MM of the day's Sunsethue model updates, in clock order.

    In the user's zone when one is saved, else UTC. Converted on today's date,
    so a daylight-saving change moves them the way the user's clock moves.
    """
    tz = ZoneInfo(timezone) if timezone else UTC
    today = now.astimezone(UTC).date()
    local = (datetime.combine(today, at).astimezone(tz) for at in SUNSETHUE_MODEL_AVAILABLE_AT_UTC)
    return sorted(moment.strftime("%H:%M") for moment in local)


def model_updates_text(timezone: str | None, now: datetime) -> str:
    """When a Sunsethue score can change, so a moved score is not a surprise."""
    times = model_update_times(timezone, now)
    listed = f"{', '.join(times[:-1])} і {times[-1]}"
    if timezone:
        when = f"за вашим часом ({timezone}) — приблизно о {listed}"
    else:
        when = f"приблизно о {listed} за UTC (збережіть локацію, і тут буде ваш місцевий час)"
    return (
        f"Модель Sunsethue оновлюється чотири рази на добу, {when}. "
        "Між оновленнями бот показує збережений прогноз, тож бал Sunsethue змінюється лише після одного з цих моментів."
    )


def score_info_text(timezone: str | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return (
        "ℹ️ Як рахується прогноз заходу сонця\n\n"
        "Якщо налаштовано ключ Sunsethue, бот спершу бере прогноз Sunsethue: якість заходу у відсотках, час заходу, хмарність, напрямок сонця і золоту годину. Результат кешується, але оновлюється після наступного очікуваного оновлення моделі Sunsethue.\n\n"
        f"{model_updates_text(timezone, now)}\n\n"
        "Якщо Sunsethue недоступний, не має модельних даних для локації або ключ не налаштовано, бот автоматично переходить на Open-Meteo і рахує прогноз локально.\n\n"
        "Коли Open-Meteo підмінив Sunsethue через збій, така оцінка вважається тимчасовою. При наступному запиті бот знову спробує Sunsethue: якщо той уже ожив, ви побачите його прогноз, якщо ні — збережену оцінку Open-Meteo. Тому бал може змінитися між двома натисканнями, і це не тому, що бот передумав.\n\n"
        "Якщо Sunsethue мовчить багато разів поспіль, бот ненадовго перестає його смикати, щоб не змушувати вас чекати даремно. А якщо вичерпано денний ліміт запитів, чекає вже до наступної доби.\n\n"
        "Локальний розрахунок Open-Meteo дивиться не одну годину, а вікно навколо заходу сонця: приблизно дві години до заходу і одну годину після. Найбільшу вагу має час безпосередньо перед заходом.\n\n"
        "Кнопка «Світанок» показує найближчий схід сонця: до світанку — сьогоднішній, після нього — завтрашній. Він оцінюється так само, за тією ж шкалою, а в розрахунку Open-Meteo вікно віддзеркалене: приблизно година до сходу і дві години після. Сповіщень про світанок бот не надсилає — лише про захід.\n\n"
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
