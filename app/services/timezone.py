from functools import lru_cache


@lru_cache(maxsize=1)
def _timezone_finder():
    """Built on first use, not at import.

    `timezonefinder` compiles from source where no wheel exists (Windows needs
    MSVC), so importing it at module scope makes every module that transitively
    reaches this one unimportable on a dev box without it — including the
    handlers, which then cannot be exercised in a test at all.
    """
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


def timezone_for_coordinates(latitude: float, longitude: float) -> str:
    return _timezone_finder().timezone_at(lat=latitude, lng=longitude) or "UTC"
