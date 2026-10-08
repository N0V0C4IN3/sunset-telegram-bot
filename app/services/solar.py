"""Solar Events, and where they fall without asking anyone.

Sunsethue charges per event, so asking it for a sunrise that has already happened
spends credits on nothing. This works out locally which day's event is still
ahead, using the standard sunrise equation (good to about a minute away from the
poles), so a provider is only asked for the day that matters.
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum


class SolarEvent(StrEnum):
    SUNRISE = "sunrise"
    SUNSET = "sunset"


@dataclass(frozen=True)
class EventWords:
    """How each Solar Event is named in Ukrainian copy."""

    noun: str  # "красивий захід"
    of_noun: str  # "умови для заходу"
    verb: str  # "сонце сідає"


WORDS = {
    SolarEvent.SUNSET: EventWords(noun="захід", of_noun="заходу", verb="сідає"),
    SolarEvent.SUNRISE: EventWords(noun="схід", of_noun="сходу", verb="сходить"),
}


# Wider than the equation's error, so a borderline call asks about both days
# rather than guessing wrong and skipping the event the user wanted.
UNCERTAINTY = timedelta(minutes=2)

_J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)
_REFRACTION_AND_DISC = math.radians(-0.833)
_OBLIQUITY = math.radians(23.4397)


def event_time(event: SolarEvent, on: date, latitude: float, longitude: float) -> datetime | None:
    """The UTC time of `event` on the local day `on`, or None in polar day or night."""
    days = (on - _J2000.date()).days
    mean_solar_noon = days - longitude / 360
    anomaly = math.radians((357.5291 + 0.98560028 * mean_solar_noon) % 360)
    centre = (
        1.9148 * math.sin(anomaly) + 0.02 * math.sin(2 * anomaly) + 0.0003 * math.sin(3 * anomaly)
    )
    ecliptic_longitude = math.radians((math.degrees(anomaly) + centre + 180 + 102.9372) % 360)
    transit = mean_solar_noon + 0.0053 * math.sin(anomaly) - 0.0069 * math.sin(2 * ecliptic_longitude)
    declination = math.asin(math.sin(ecliptic_longitude) * math.sin(_OBLIQUITY))

    phi = math.radians(latitude)
    cos_hour_angle = (math.sin(_REFRACTION_AND_DISC) - math.sin(phi) * math.sin(declination)) / (
        math.cos(phi) * math.cos(declination)
    )
    if not -1 <= cos_hour_angle <= 1:
        return None
    half_day = math.degrees(math.acos(cos_hour_angle)) / 360
    offset = -half_day if event is SolarEvent.SUNRISE else half_day
    return _J2000 + timedelta(days=transit + offset)


def days_to_ask(event: SolarEvent, local_now: datetime, latitude: float, longitude: float) -> list[date]:
    """The local days whose `event` could be the next one, soonest first.

    One day when the answer is clear, both when today's event is too close to
    call or the sun does not rise or set at all today.
    """
    today = local_now.date()
    tomorrow = today + timedelta(days=1)
    at = event_time(event, today, latitude, longitude)
    if at is None:
        return [today, tomorrow]
    if at - UNCERTAINTY > local_now:
        return [today]
    if at + UNCERTAINTY < local_now:
        return [tomorrow]
    return [today, tomorrow]
