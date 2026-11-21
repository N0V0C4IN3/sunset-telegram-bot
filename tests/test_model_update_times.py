from datetime import UTC, datetime

from app.bot.messages import model_update_times, score_info_text

WINTER = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def test_updates_are_shown_on_the_users_clock_in_clock_order():
    assert model_update_times("Europe/Kyiv", WINTER) == ["00:30", "06:30", "12:30", "18:30"]


def test_daylight_saving_moves_them_with_the_users_clock():
    assert model_update_times("Europe/Kyiv", SUMMER) == ["01:30", "07:30", "13:30", "19:30"]


def test_a_half_hour_zone_keeps_its_minutes():
    assert model_update_times("Asia/Kolkata", WINTER) == ["04:00", "10:00", "16:00", "22:00"]


def test_from_25_november_they_are_half_an_hour_later():
    after_change = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
    assert model_update_times("Europe/Kyiv", after_change) == ["01:00", "07:00", "13:00", "19:00"]
    assert model_update_times(None, after_change) == ["05:00", "11:00", "17:00", "23:00"]


def test_without_a_location_they_are_given_in_utc():
    assert model_update_times(None, WINTER) == ["04:30", "10:30", "16:30", "22:30"]


def test_the_info_text_names_the_users_zone_and_times():
    text = score_info_text("Europe/Kyiv", WINTER)

    assert "(Europe/Kyiv)" in text
    assert "00:30, 06:30, 12:30 і 18:30" in text


def test_the_info_text_without_a_location_says_utc_and_how_to_get_local_times():
    text = score_info_text(None, WINTER)

    assert "04:30, 10:30, 16:30 і 22:30 за UTC" in text
    assert "збережіть локацію" in text
