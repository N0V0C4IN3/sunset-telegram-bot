import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    forecast_url = "https://api.open-meteo.com/v1/forecast"
    air_quality_url = "https://air-quality-api.open-meteo.com/v1/air-quality"

    async def forecast_for_today(self, latitude: float, longitude: float, timezone: str) -> ForecastResult:
        tz = ZoneInfo(timezone)
        today = datetime.now(tz).date()
        async with httpx.AsyncClient(timeout=15) as client:
            weather_task = self._fetch_weather(client, latitude, longitude, timezone)
            air_quality_task = self._fetch_air_quality(client, latitude, longitude, timezone)
            weather_payload, air_quality_payload = await asyncio.gather(weather_task, air_quality_task)

        try:
            sunset_raw = weather_payload["daily"]["sunset"][0]
            sunset_at = datetime.fromisoformat(sunset_raw).replace(tzinfo=tz)
            hourly = weather_payload["hourly"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherError("Open-Meteo response did not include expected fields") from exc

        weather_window = self._weather_near_sunset(hourly, sunset_at)
        if air_quality_payload:
            weather_window["air_quality"] = self._air_quality_near_sunset(air_quality_payload.get("hourly", {}), sunset_at)

        scored: SunsetScore = score_sunset(weather_window)
        return ForecastResult(
            forecast_date=today,
            sunset_at=sunset_at,
            score=scored.score,
            description=scored.description,
            weather_data=weather_window,
        )

    async def _fetch_weather(self, client: httpx.AsyncClient, latitude: float, longitude: float, timezone: str) -> dict:
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
                    "weather_code",
                ]
            ),
        }
        response = await client.get(self.forecast_url, params=params)
        if response.status_code >= 400:
            raise WeatherError(f"Open-Meteo returned HTTP {response.status_code}")
        return response.json()

    async def _fetch_air_quality(self, client: httpx.AsyncClient, latitude: float, longitude: float, timezone: str) -> dict:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "forecast_days": 1,
            "hourly": ",".join(
                [
                    "pm10",
                    "pm2_5",
                    "aerosol_optical_depth",
                    "dust",
                    "european_aqi",
                    "us_aqi",
                ]
            ),
        }
        try:
            response = await client.get(self.air_quality_url, params=params)
            if response.status_code >= 400:
                return {}
            return response.json()
        except httpx.HTTPError:
            return {}

    def _weather_near_sunset(self, hourly: dict, sunset_at: datetime) -> dict:
        samples = self._samples_near_sunset(hourly, sunset_at, hours_before=2, hours_after=1)
        fields = [
            "cloud_cover",
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "precipitation_probability",
            "visibility",
            "relative_humidity_2m",
            "weather_code",
        ]
        weather = {field: self._weighted_average(hourly, field, samples) for field in fields}
        weather["cloud_cover_low_max"] = self._max_value(hourly, "cloud_cover_low", samples)
        weather["cloud_cover_max"] = self._max_value(hourly, "cloud_cover", samples)
        weather["precipitation_probability_max"] = self._max_value(hourly, "precipitation_probability", samples)
        weather["sunset_window_consistency"] = self._consistency_score(hourly, samples)
        weather["sunset_window_hours"] = [
            {
                "time": sample["time"].isoformat(timespec="minutes"),
                "weight": sample["weight"],
            }
            for sample in samples
        ]
        return weather

    def _air_quality_near_sunset(self, hourly: dict, sunset_at: datetime) -> dict:
        if not hourly:
            return {}
        try:
            samples = self._samples_near_sunset(hourly, sunset_at, hours_before=2, hours_after=1)
        except WeatherError:
            return {}
        fields = ["pm10", "pm2_5", "aerosol_optical_depth", "dust", "european_aqi", "us_aqi"]
        return {field: self._weighted_average(hourly, field, samples) for field in fields}

    def _samples_near_sunset(
        self,
        hourly: dict,
        sunset_at: datetime,
        hours_before: int,
        hours_after: int,
    ) -> list[dict]:
        times = hourly.get("time", [])
        if not times:
            raise WeatherError("Open-Meteo response did not include hourly times")

        parsed_times = [datetime.fromisoformat(value).replace(tzinfo=sunset_at.tzinfo) for value in times]
        start = sunset_at - timedelta(hours=hours_before)
        end = sunset_at + timedelta(hours=hours_after)
        samples = [
            {"index": index, "time": value, "weight": self._sunset_weight(value, sunset_at)}
            for index, value in enumerate(parsed_times)
            if start <= value <= end
        ]
        if not samples:
            nearest_index = min(range(len(parsed_times)), key=lambda index: abs(parsed_times[index] - sunset_at))
            samples = [
                {
                    "index": nearest_index,
                    "time": parsed_times[nearest_index],
                    "weight": 1.0,
                }
            ]

        total_weight = sum(sample["weight"] for sample in samples)
        return [{**sample, "weight": sample["weight"] / total_weight} for sample in samples]

    def _sunset_weight(self, sample_time: datetime, sunset_at: datetime) -> float:
        delta_hours = (sample_time - sunset_at).total_seconds() / 3600
        if -1 <= delta_hours <= 0.25:
            return 1.0
        if -2 <= delta_hours < -1:
            return 0.65
        if 0.25 < delta_hours <= 1:
            return 0.5
        return 0.25

    def _weighted_average(self, hourly: dict, field: str, samples: list[dict]) -> float | None:
        values = hourly.get(field) or []
        weighted_values = [
            (float(values[sample["index"]]), sample["weight"])
            for sample in samples
            if sample["index"] < len(values) and values[sample["index"]] is not None
        ]
        if not weighted_values:
            return None
        total_weight = sum(weight for _, weight in weighted_values)
        return sum(value * weight for value, weight in weighted_values) / total_weight

    def _max_value(self, hourly: dict, field: str, samples: list[dict]) -> float | None:
        values = hourly.get(field) or []
        selected = [
            float(values[sample["index"]])
            for sample in samples
            if sample["index"] < len(values) and values[sample["index"]] is not None
        ]
        return max(selected) if selected else None

    def _consistency_score(self, hourly: dict, samples: list[dict]) -> float:
        spreads = []
        for field in ["cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "precipitation_probability"]:
            values = hourly.get(field) or []
            selected = [
                float(values[sample["index"]])
                for sample in samples
                if sample["index"] < len(values) and values[sample["index"]] is not None
            ]
            if len(selected) >= 2:
                spreads.append(max(selected) - min(selected))
        if not spreads:
            return 65
        average_spread = sum(spreads) / len(spreads)
        return max(0, min(100, 100 - average_spread * 1.4))
