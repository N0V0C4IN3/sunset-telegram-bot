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
        "Між оновленнями прогноз Sunsethue не змінюється: бот показує збережений."
    )


def score_info_text(timezone: str | None = None, now: datetime | None = None) -> str:
    """What a user needs to read a score: where it comes from and when it can change.

    Deliberately Sunsethue only. A provisional Open-Meteo card explains itself
    in its own caption (`format_forecast`), so the fallback is not described here.
    """
    now = now or datetime.now(UTC)
    return (
        "ℹ️ Як рахується прогноз\n\n"
        "Прогноз дає Sunsethue: якість заходу чи світанку у відсотках і точний час події з урахуванням хмар, напрямку сонця та золотої години.\n\n"
        f"{model_updates_text(timezone, now)}\n\n"
        "Поки сьогоднішній захід попереду, «Завтра» показує наступний день; після заходу «Сьогодні» вже показує завтрашній. "
        "«Світанок» показує найближчий схід сонця. Сповіщення бот надсилає лише про захід."
    )
