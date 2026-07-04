from timezonefinder import TimezoneFinder

_finder = TimezoneFinder()


def timezone_for_coordinates(latitude: float, longitude: float) -> str:
    return _finder.timezone_at(lat=latitude, lng=longitude) or "UTC"
