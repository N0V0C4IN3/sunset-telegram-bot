"""What the settings view says about the location the bot is holding."""

from app.bot.messages import location_saved_text, location_text, settings_text


def test_a_saved_location_is_shown_with_its_timezone():
    shown = location_text((50.4501, 30.5234), "Europe/Kyiv")
    assert "50.450" in shown
    assert "30.523" in shown
    assert "Europe/Kyiv" in shown


def test_no_saved_location_says_so_rather_than_showing_nothing():
    assert location_text(None, None) == "Локація: ще не збережена"


def test_the_settings_view_always_reports_the_location_state():
    with_location = settings_text(70, 90, True, (50.4501, 30.5234), "Europe/Kyiv")
    without = settings_text(70, 90, True, None, None)
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


def test_the_shown_location_is_rounded_rather_than_exact():
    # Three decimals is ~100m: enough to recognise your own town, coarser than
    # the six decimals actually stored.
    shown = location_text((50.4501234, 30.5234567), "Europe/Kyiv")
    assert "50.4501234" not in shown
    assert "30.5234567" not in shown
