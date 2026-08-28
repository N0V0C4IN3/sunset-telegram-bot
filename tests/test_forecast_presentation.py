import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

from app.bot.card import HEIGHT, WIDTH, palette, render_card
from app.bot.keyboards import main_keyboard, settings_keyboard
from app.bot.messages import day_label, format_forecast, local_sunset_time
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


# The caption. It has to stand on its own: the card is not readable by a screen
# reader and the chat list previews only the text.


def test_caption_carries_the_score_and_the_day():
    text = format_forecast(a_forecast(score=72), "Europe/Kyiv")
    assert "72%" in text
    assert "сьогодні" in text


def test_caption_has_no_text_bar_left_in_it():
    text = format_forecast(a_forecast(score=72), "Europe/Kyiv")
    for glyph in "█░●○▰▱":
        assert glyph not in text


def test_a_settled_forecast_carries_no_provisional_note():
    assert "⏳" not in format_forecast(a_forecast(), "Europe/Kyiv", provisional=False)


def test_a_provisional_forecast_explains_itself():
    text = format_forecast(a_forecast(provider="open_meteo"), "Europe/Kyiv", provisional=True)
    assert "⏳" in text
    assert "Sunsethue" in text


def test_tomorrow_is_labelled_as_tomorrow():
    assert day_label(a_forecast(days_ahead=1), "Europe/Kyiv") == "завтра"
    assert "завтра" in format_forecast(a_forecast(days_ahead=1), "Europe/Kyiv")


def test_sunset_time_is_rendered_in_the_users_zone():
    assert local_sunset_time(a_forecast(), "Europe/Kyiv") == "20:14"


def test_a_naive_sunset_is_read_as_local():
    result = ForecastResult(
        provider="open_meteo",
        forecast_date=datetime(2026, 8, 28).date(),
        sunset_at=datetime(2026, 8, 28, 20, 14),
        score=50,
        description="x",
        weather_data={},
    )
    assert local_sunset_time(result, "Europe/Kyiv") == "20:14"


# The card.


def test_the_card_is_a_png_of_the_expected_size():
    image = Image.open(io.BytesIO(render_card(72, "сьогодні", "20:14")))
    assert image.format == "PNG"
    assert image.size == (WIDTH, HEIGHT)


def test_the_card_renders_at_both_extremes():
    for score in (0, 100):
        assert Image.open(io.BytesIO(render_card(score, "сьогодні", "20:14"))).size == (WIDTH, HEIGHT)


def test_the_card_clamps_a_score_outside_the_scale():
    assert render_card(140, "сьогодні", "20:14") == render_card(100, "сьогодні", "20:14")
    assert render_card(-10, "сьогодні", "20:14") == render_card(0, "сьогодні", "20:14")


def test_a_better_score_makes_a_warmer_sky():
    """The gradient carries the verdict before the number is read."""
    poor_red = palette(10)[3][0]
    great_red = palette(95)[3][0]
    assert great_red > poor_red


def test_the_palette_is_continuous_across_the_old_band_edges():
    """Banding these made 44 and 45 look like different weather."""
    for edge in (45, 65, 70, 80):
        before, after = palette(edge - 1), palette(edge)
        for low, high in zip(before, after):
            assert max(abs(a - b) for a, b in zip(low, high)) <= 3


def test_the_card_is_deterministic():
    assert render_card(63, "завтра", "20:41") == render_card(63, "завтра", "20:41")


# The keyboard.


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
