from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.services.scoring import SunsetScore, score_sunset


class WeatherError(RuntimeError):
    pass


@dataclass(frozen=True)
class ForecastResult:
    forecast_date: date
    sunset_at: datetime
    score: int
    description: str
    weather_data: dict


class OpenMeteoClient:
    base_url = "https://api.open-meteo.com/v1/forecast"

    async def forecast_for_today(self, latitude: float, longitude: float, timezone: str) -> ForecastResult:
        tz = ZoneInfo(timezone)
        today = datetime.now(tz).date()
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "forecast_days": 1,
            "daily": "sunset",
            "hourly": ",".join(
                [
                    "cloud_cover",
                    "cloud_cover_low",
                    "cloud_cover_mid",
                    "cloud_cover_high",
                    "precipitation_probability",
                    "visibility",
                    "relative_humidity_2m",
                ]
            ),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self.base_url, params=params)
        if response.status_code >= 400:
            raise WeatherError(f"Open-Meteo returned HTTP {response.status_code}")

        payload = response.json()
        try:
            sunset_raw = payload["daily"]["sunset"][0]
            sunset_at = datetime.fromisoformat(sunset_raw).replace(tzinfo=tz)
            hourly = payload["hourly"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherError("Open-Meteo response did not include expected fields") from exc

        weather_window = self._weather_near_sunset(hourly, sunset_at)
        scored: SunsetScore = score_sunset(weather_window)
        return ForecastResult(
            forecast_date=today,
            sunset_at=sunset_at,
            score=scored.score,
            description=scored.description,
            weather_data=weather_window,
        )

    def _weather_near_sunset(self, hourly: dict, sunset_at: datetime) -> dict:
        times = hourly.get("time", [])
        if not times:
            raise WeatherError("Open-Meteo response did not include hourly times")

        sunset_hour = sunset_at.replace(minute=0, second=0, microsecond=0)
        parsed_times = [datetime.fromisoformat(value).replace(tzinfo=sunset_at.tzinfo) for value in times]
        best_index = min(range(len(parsed_times)), key=lambda index: abs(parsed_times[index] - sunset_hour))
        fields = [
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "precipitation_probability",
            "visibility",
            "relative_humidity_2m",
        ]
        weather = {}
        for field in fields:
            values = hourly.get(field) or []
            weather[field] = values[best_index] if best_index < len(values) else None
        return weather
