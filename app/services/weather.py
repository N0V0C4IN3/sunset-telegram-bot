import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.services.scoring import SunsetScore, score_sunset
from app.services.solar import SolarEvent

PROVIDER_OPEN_METEO = "open_meteo"


class WeatherError(RuntimeError):
    pass


@dataclass(frozen=True)
class ForecastResult:
    provider: str
    forecast_date: date
    event_at: datetime
    score: int
    description: str
    weather_data: dict
    event: SolarEvent = SolarEvent.SUNSET


class OpenMeteoClient:
    forecast_url = "https://api.open-meteo.com/v1/forecast"
    air_quality_url = "https://air-quality-api.open-meteo.com/v1/air-quality"

    async def forecast_for_today(
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        on_date: date | None = None,
        event: SolarEvent = SolarEvent.SUNSET,
    ) -> ForecastResult:
        """The next upcoming `event`, or the one on `on_date` when asked for a day.

        The 2-day payload already covers both days and both events, so naming a
        day or a sunrise costs no extra call.
        """
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        async with httpx.AsyncClient(timeout=15) as client:
            weather_task = self._fetch_weather(client, latitude, longitude, timezone)
            air_quality_task = self._fetch_air_quality(client, latitude, longitude, timezone)
            weather_payload, air_quality_payload = await asyncio.gather(weather_task, air_quality_task)

        try:
            daily = weather_payload["daily"]
            event_at = self._relevant_event(daily[event], now, tz, on_date)
            hourly = weather_payload["hourly"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherError("Open-Meteo response did not include expected fields") from exc

        weather_window = self._weather_near_event(hourly, event_at, event)
        if air_quality_payload:
            weather_window["air_quality"] = self._air_quality_near_event(
                air_quality_payload.get("hourly", {}), event_at, event
            )

        scored: SunsetScore = score_sunset(weather_window, event)
        return ForecastResult(
            provider=PROVIDER_OPEN_METEO,
            forecast_date=event_at.date(),
            event_at=event_at,
            score=scored.score,
            description=scored.description,
            weather_data=weather_window,
            event=event,
        )

    async def _fetch_weather(self, client: httpx.AsyncClient, latitude: float, longitude: float, timezone: str) -> dict:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
            "forecast_days": 2,
            "daily": "sunrise,sunset",
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
            "forecast_days": 2,
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

    def _relevant_event(
        self,
        times: list[str],
        now: datetime,
        timezone: ZoneInfo,
        on_date: date | None = None,
    ) -> datetime:
        candidates = [
            datetime.fromisoformat(value).replace(tzinfo=timezone)
            for value in times
        ]
        if on_date is not None:
            for event_at in candidates:
                if event_at.date() == on_date:
                    return event_at
            raise WeatherError("Open-Meteo response did not include the requested day")
        for event_at in candidates:
            if event_at > now:
                return event_at
        if candidates:
            return candidates[-1]
        raise WeatherError("Open-Meteo response did not include event times")

    def _weather_near_event(self, hourly: dict, event_at: datetime, event: SolarEvent) -> dict:
        samples = self._samples_near_event(hourly, event_at, event)
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
        # The keys keep their sunset names: they are the scoring input, and the
        # harvested autoresearch corpus is keyed on them.
        weather["sunset_window_consistency"] = self._consistency_score(hourly, samples)
        weather["sunset_window_hours"] = [
            {
                "time": sample["time"].isoformat(timespec="minutes"),
                "weight": sample["weight"],
            }
            for sample in samples
        ]
        return weather

    def _air_quality_near_event(self, hourly: dict, event_at: datetime, event: SolarEvent) -> dict:
        if not hourly:
            return {}
        try:
            samples = self._samples_near_event(hourly, event_at, event)
        except WeatherError:
            return {}
        fields = ["pm10", "pm2_5", "aerosol_optical_depth", "dust", "european_aqi", "us_aqi"]
        return {field: self._weighted_average(hourly, field, samples) for field in fields}

    def _samples_near_event(self, hourly: dict, event_at: datetime, event: SolarEvent) -> list[dict]:
        """Hourly samples around the event, weighted by `_event_weight`.

        The sunset window runs from 2h before to 1h after. A sunrise mirrors it in
        time: 1h before to 2h after. The mirror is a starting point, not a tuned
        result — the weights were fitted on sunsets only.
        """
        hours_before, hours_after = (1, 2) if event is SolarEvent.SUNRISE else (2, 1)
        times = hourly.get("time", [])
        if not times:
            raise WeatherError("Open-Meteo response did not include hourly times")

        parsed_times = [datetime.fromisoformat(value).replace(tzinfo=event_at.tzinfo) for value in times]
        start = event_at - timedelta(hours=hours_before)
        end = event_at + timedelta(hours=hours_after)
        samples = [
            {"index": index, "time": value, "weight": self._event_weight(value, event_at, event)}
            for index, value in enumerate(parsed_times)
            if start <= value <= end
        ]
        if not samples:
            nearest_index = min(range(len(parsed_times)), key=lambda index: abs(parsed_times[index] - event_at))
            samples = [
                {
                    "index": nearest_index,
                    "time": parsed_times[nearest_index],
                    "weight": 1.0,
                }
            ]

        total_weight = sum(sample["weight"] for sample in samples)
        return [{**sample, "weight": sample["weight"] / total_weight} for sample in samples]

    def _event_weight(self, sample_time: datetime, event_at: datetime, event: SolarEvent) -> float:
        delta_hours = (sample_time - event_at).total_seconds() / 3600
        if event is SolarEvent.SUNRISE:
            delta_hours = -delta_hours
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
