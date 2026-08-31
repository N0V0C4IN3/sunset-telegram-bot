"""The Sunset Score is the product's whole opinion; these pin it.

score_sunset is a pure function of a dict, so every case here is a plain call.
The numbers are the ones the scoring module already encodes — these tests
describe the current behaviour so a change to it has to be deliberate.
"""

import pytest

from app.services.scoring import SunsetScore, score_sunset

# A sky with everything in its ideal band: mid/high cloud for colour, a clear
# horizon, no rain, good visibility, a steady forecast window.
IDEAL = {
    "cloud_cover": 50,
    "cloud_cover_low": 5,
    "cloud_cover_low_max": 10,
    "cloud_cover_mid": 35,
    "cloud_cover_high": 50,
    "cloud_cover_max": 60,
    "precipitation_probability": 0,
    "precipitation_probability_max": 5,
    "visibility": 20000,
    "relative_humidity_2m": 50,
    "sunset_window_consistency": 90,
}


def score(**overrides) -> int:
    return score_sunset({**IDEAL, **overrides}).score


def test_the_ideal_sky_scores_high():
    assert score() >= 85


def test_a_score_is_always_a_whole_number_between_zero_and_one_hundred():
    for weather in [IDEAL, {}, {"cloud_cover": 100, "relative_humidity_2m": 100, "visibility": 0}]:
        result = score_sunset(dict(weather))
        assert isinstance(result, SunsetScore)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100


def test_an_empty_forecast_still_produces_a_score():
    # Every field falls back to a default rather than raising.
    assert 0 <= score_sunset({}).score <= 100


# The five weighted factors, each moved on its own.


def test_a_horizon_choked_with_low_cloud_scores_below_an_open_one():
    assert score(cloud_cover_low=80, cloud_cover_low_max=85) < score(cloud_cover_low=5, cloud_cover_low_max=10)


def test_likely_rain_scores_below_dry():
    assert score(precipitation_probability=60, precipitation_probability_max=65) < score()


def test_poor_visibility_scores_below_clear_air():
    assert score(visibility=3200) < score(visibility=25000)


def test_high_humidity_scores_below_dry_air():
    assert score(relative_humidity_2m=95) < score(relative_humidity_2m=45)


def test_an_unstable_sunset_window_scores_below_a_steady_one():
    assert score(sunset_window_consistency=10) < score(sunset_window_consistency=95)


def test_a_bald_sky_with_no_cloud_to_catch_colour_scores_below_a_textured_one():
    assert score(cloud_cover=2, cloud_cover_high=2, cloud_cover_mid=2) < score()


# The hard caps. Each can only lower a score, never raise it.


@pytest.mark.parametrize(
    "label,overrides,ceiling",
    [
        ("heavy rain", {"precipitation_probability_max": 80}, 30),
        ("likely rain", {"precipitation_probability_max": 65}, 35),
        ("blanketed horizon", {"cloud_cover_low_max": 95}, 35),
        ("near-blanketed horizon", {"cloud_cover_low_max": 88}, 45),
        ("fog", {"visibility": 2000}, 35),
        ("haze", {"visibility": 2800}, 50),
        ("saturated air", {"relative_humidity_2m": 97}, 65),
        ("overcast with no high cloud", {"cloud_cover_max": 98, "cloud_cover_high": 20}, 55),
        ("empty sky", {"cloud_cover": 2, "cloud_cover_high": 2, "cloud_cover_mid": 2}, 70),
    ],
)
def test_each_cap_holds_the_score_at_or_below_its_ceiling(label, overrides, ceiling):
    assert score(**overrides) <= ceiling, f"{label} should cap at {ceiling}"


def test_a_cap_never_raises_a_score():
    # A sky that trips the fog cap scores no better than the same sky without it.
    assert score(visibility=2000) <= score(visibility=20000)


# Air quality is optional and must never be required.


def test_air_quality_is_optional():
    without = score()
    with_clean_air = score(air_quality={"pm10": 5, "pm2_5": 3})
    assert 0 <= without <= 100
    assert 0 <= with_clean_air <= 100


def test_dirty_air_scores_below_clean_air():
    assert score(air_quality={"pm10": 100, "pm2_5": 60}) < score(air_quality={"pm10": 5, "pm2_5": 3})


@pytest.mark.parametrize("overrides,ceiling", [({"pm2_5": 90}, 45), ({"pm10": 130}, 50)])
def test_heavy_particulates_cap_the_score(overrides, ceiling):
    assert score(air_quality=overrides) <= ceiling


def test_a_missing_air_quality_field_is_ignored_rather_than_treated_as_zero():
    assert score(air_quality={"pm10": None, "pm2_5": None}) == score(air_quality={})


# The description travels with the score.


def test_the_description_matches_the_band_the_score_lands_in():
    assert "дуже перспективно" in score_sunset(dict(IDEAL)).description
    grim = score_sunset({**IDEAL, "precipitation_probability_max": 90, "cloud_cover_low_max": 95})
    assert grim.score < 45
    assert "без великої драми" in grim.description


# A middling sky: nothing scores well enough to earn a reason of its own, which
# leaves room in the list for the air quality note.
UNREMARKABLE = {
    "cloud_cover": 50,
    "cloud_cover_low": 40,
    "cloud_cover_low_max": 45,
    "cloud_cover_mid": 35,
    "cloud_cover_high": 10,
    "cloud_cover_max": 60,
    "precipitation_probability": 30,
    "precipitation_probability_max": 35,
    "visibility": 6000,
    "relative_humidity_2m": 88,
    "sunset_window_consistency": 60,
}


def test_the_description_says_when_air_quality_was_part_of_the_calculation():
    described = score_sunset({**UNREMARKABLE, "air_quality": {"pm10": 5}}).description
    assert "якість повітря" in described


def test_the_air_quality_note_is_dropped_when_three_other_reasons_already_qualify():
    """Current behaviour, pinned rather than endorsed.

    The note is appended last and the list is then truncated to three reasons,
    so on a sky good enough to earn three of its own it never appears — which is
    most good forecasts. Changing that is a product decision, not a test fix.
    """
    described = score_sunset({**IDEAL, "air_quality": {"pm10": 5}}).description
    assert "якість повітря" not in described
