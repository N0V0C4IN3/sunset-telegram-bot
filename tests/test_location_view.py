"""What the settings view says about the location the bot is holding."""

from app.bot.messages import location_saved_text, location_text, settings_text


def test_a_saved_location_is_shown_by_its_timezone_alone():
    shown = location_text("Europe/Kyiv")
    assert "Europe/Kyiv" in shown
    assert not any(character.isdigit() for character in shown), "no coordinates on screen"


def test_no_saved_location_says_so_rather_than_showing_nothing():
    assert location_text(None) == "Локація: ще не збережена"


def test_the_settings_view_always_reports_the_location_state():
    with_location = settings_text(70, 90, True, "Europe/Kyiv")
    without = settings_text(70, 90, True, None)
    assert "Локація" in with_location
    assert "Локація" in without
    assert "ще не збережена" in without


def test_the_settings_view_still_renders_when_no_location_is_passed():
    # Defaults keep every existing caller working.
    assert "Локація" in settings_text(70, 90, False)


def test_a_first_save_and_a_replacement_are_worded_differently():
    assert "збережено" in location_saved_text(replaced=False)
    assert "оновлено" in location_saved_text(replaced=True)
    assert location_saved_text(replaced=False) != location_saved_text(replaced=True)
