from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.bot.keyboards import main_keyboard, settings_keyboard
from app.bot.messages import format_forecast, score_bar
from app.services.weather import ForecastResult

KYIV = ZoneInfo("Europe/Kyiv")


def a_forecast(score: int = 72, provider: str = "sunsethue", days_ahead: int = 0) -> ForecastResult:
    sunset_at = datetime.now(KYIV).replace(hour=20, minute=14, second=0, microsecond=0)
    sunset_at += timedelta(days=days_ahead)
    return ForecastResult(
        provider=provider,
        forecast_date=sunset_at.date(),
        sunset_at=sunset_at,
        score=score,
        description="Варто вийти й перевірити: хмари мають добрий баланс для кольору.",
        weather_data={},
    )


def callback_data(keyboard) -> set[str]:
    return {button.callback_data for row in keyboard.inline_keyboard for button in row}


def test_score_bar_fills_proportionally():
    assert score_bar(0) == "░░░░░░░░░░"
    assert score_bar(100) == "██████████"
    assert score_bar(50) == "█████░░░░░"


def test_score_bar_never_overflows_its_width():
    assert len(score_bar(120)) == 10
    assert len(score_bar(-5)) == 10


def test_forecast_shows_the_band_next_to_the_number():
    text = format_forecast(a_forecast(score=72), "Europe/Kyiv")
    assert "███████░░░ 72%" in text
    assert "сьогодні" in text
    assert "20:14" in text


def test_a_settled_forecast_carries_no_provisional_note():
    text = format_forecast(a_forecast(), "Europe/Kyiv", provisional=False)
    assert "⏳" not in text


def test_a_provisional_forecast_explains_itself():
    text = format_forecast(a_forecast(provider="open_meteo"), "Europe/Kyiv", provisional=True)
    assert "⏳" in text
    assert "Sunsethue" in text


def test_tomorrow_is_labelled_as_tomorrow():
    text = format_forecast(a_forecast(days_ahead=1), "Europe/Kyiv")
    assert "завтра" in text


def test_the_next_day_button_appears_only_when_asked_for():
    assert "tomorrow" in callback_data(main_keyboard(False, show_next_day=True))
    assert "tomorrow" not in callback_data(main_keyboard(False, show_next_day=False))


def test_the_settings_view_is_the_same_keyboard_with_the_tuning_row():
    plain = callback_data(main_keyboard(True))
    opened = callback_data(settings_keyboard(True))
    assert plain < opened
    assert {"set_threshold", "set_lead_time"} == opened - plain


def test_both_views_offer_the_subscription_toggle_in_one_direction_only():
    assert "unsubscribe" in callback_data(main_keyboard(True))
    assert "subscribe" not in callback_data(main_keyboard(True))
    assert "subscribe" in callback_data(main_keyboard(False))
