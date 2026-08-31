from dataclasses import dataclass


@dataclass(frozen=True)
class SunsetScore:
    score: int
    description: str


def score_sunset(weather: dict) -> SunsetScore:
    cloud = _value(weather, "cloud_cover", 50)
    low = _value(weather, "cloud_cover_low", cloud)
    low_max = _value(weather, "cloud_cover_low_max", low)
    mid = _value(weather, "cloud_cover_mid", cloud)
    high = _value(weather, "cloud_cover_high", cloud)
    total_max = _value(weather, "cloud_cover_max", cloud)
    precipitation = _value(weather, "precipitation_probability", 0)
    precipitation_max = _value(weather, "precipitation_probability_max", precipitation)
    visibility = _value(weather, "visibility", 10000)
    humidity = _value(weather, "relative_humidity_2m", 60)
    consistency = _value(weather, "sunset_window_consistency", 65)
    air_quality = weather.get("air_quality") or {}

    cloud_composition = (
        _range_score(high, ideal_min=25, ideal_max=75, hard_min=5, hard_max=95) * 0.45
        + _range_score(mid, ideal_min=15, ideal_max=60, hard_min=0, hard_max=90) * 0.35
        + _range_score(cloud, ideal_min=25, ideal_max=80, hard_min=0, hard_max=100) * 0.20
    )
    horizon_openness = _inverse_score(low * 0.65 + low_max * 0.35, ideal_max=25, hard_max=90)
    precipitation_score = _inverse_score(precipitation * 0.65 + precipitation_max * 0.35, ideal_max=15, hard_max=70)
    clarity_score = _clarity_score(visibility, humidity, air_quality)
    consistency_score = max(0, min(100, consistency))

    raw_score = (
        cloud_composition * 0.30
        + horizon_openness * 0.25
        + precipitation_score * 0.20
        + clarity_score * 0.15
        + consistency_score * 0.10
    )

    score = _apply_caps(
        raw_score,
        low=low,
        low_max=low_max,
        cloud=cloud,
        total_max=total_max,
        mid=mid,
        high=high,
        precipitation=precipitation,
        precipitation_max=precipitation_max,
        visibility=visibility,
        humidity=humidity,
        air_quality=air_quality,
    )
    description = _build_description(
        score,
        cloud_composition=cloud_composition,
        horizon_openness=horizon_openness,
        precipitation_score=precipitation_score,
        clarity_score=clarity_score,
        consistency_score=consistency_score,
        weather=weather,
    )
    return SunsetScore(score=round(score), description=description)


def _clarity_score(visibility: float, humidity: float, air_quality: dict) -> float:
    visibility_score = _range_score(visibility, ideal_min=10000, ideal_max=30000, hard_min=2500, hard_max=40000)
    humidity_score = _inverse_score(humidity, ideal_max=75, hard_max=98)

    pm10 = _optional_value(air_quality, "pm10")
    pm25 = _optional_value(air_quality, "pm2_5")
    aerosol = _optional_value(air_quality, "aerosol_optical_depth")
    dust = _optional_value(air_quality, "dust")

    air_scores = []
    if pm10 is not None:
        air_scores.append(_inverse_score(pm10, ideal_max=25, hard_max=120))
    if pm25 is not None:
        air_scores.append(_inverse_score(pm25, ideal_max=12, hard_max=75))
    if aerosol is not None:
        air_scores.append(_inverse_score(aerosol, ideal_max=0.15, hard_max=0.9))
    if dust is not None:
        air_scores.append(_inverse_score(dust, ideal_max=20, hard_max=150))

    air_score = sum(air_scores) / len(air_scores) if air_scores else 70
    return visibility_score * 0.45 + humidity_score * 0.25 + air_score * 0.30


def _apply_caps(
    score: float,
    *,
    low: float,
    low_max: float,
    cloud: float,
    total_max: float,
    mid: float,
    high: float,
    precipitation: float,
    precipitation_max: float,
    visibility: float,
    humidity: float,
    air_quality: dict,
) -> float:
    capped = score
    if precipitation_max > 70:
        capped = min(capped, 30)
    elif precipitation_max > 60:
        capped = min(capped, 35)
    elif precipitation > 45:
        capped = min(capped, 50)

    if low_max > 90:
        capped = min(capped, 35)
    elif low_max > 85:
        capped = min(capped, 45)
    elif low > 70:
        capped = min(capped, 55)

    if visibility < 2500:
        capped = min(capped, 35)
    elif visibility < 3000:
        capped = min(capped, 50)

    pm25 = _optional_value(air_quality, "pm2_5")
    pm10 = _optional_value(air_quality, "pm10")
    if pm25 is not None and pm25 > 75:
        capped = min(capped, 45)
    if pm10 is not None and pm10 > 120:
        capped = min(capped, 50)

    if total_max > 95 and high < 40:
        capped = min(capped, 55)
    if cloud < 8 and high < 10 and mid < 10:
        capped = min(capped, 70)
    if humidity > 96:
        capped = min(capped, 65)
    return max(0, min(100, capped))


def _build_description(
    score: float,
    *,
    cloud_composition: float,
    horizon_openness: float,
    precipitation_score: float,
    clarity_score: float,
    consistency_score: float,
    weather: dict,
) -> str:
    if score >= 80:
        opener = "Виглядає дуже перспективно"
    elif score >= 65:
        opener = "Варто вийти й перевірити"
    elif score >= 45:
        opener = "Є шанс на непоганий захід"
    else:
        opener = "Схоже, сьогодні без великої драми на небі"

    reasons: list[str] = []
    cautions: list[str] = []

    if cloud_composition >= 75:
        reasons.append("хмари мають добрий баланс для кольору")
    elif cloud_composition < 40:
        cautions.append("хмарний малюнок не дуже вдалий")

    if horizon_openness >= 75:
        reasons.append("низьких хмар біля горизонту небагато")
    elif horizon_openness < 45:
        cautions.append("низькі хмари можуть перекрити сонце")

    if precipitation_score >= 80:
        reasons.append("ризик дощу низький")
    elif precipitation_score < 45:
        cautions.append("опади можуть зіпсувати картину")

    if clarity_score >= 75:
        reasons.append("видимість і якість повітря виглядають пристойно")
    elif clarity_score < 45:
        cautions.append("серпанок, вологість або забруднення можуть приглушити кольори")

    if consistency_score >= 75:
        reasons.append("прогноз у вікні заходу досить стабільний")
    elif consistency_score < 45:
        cautions.append("прогноз швидко змінюється біля заходу")

    air_quality = weather.get("air_quality") or {}
    if any(_optional_value(air_quality, field) is not None for field in ["pm10", "pm2_5", "aerosol_optical_depth", "dust"]):
        reasons.append("у розрахунку враховано якість повітря")

    details = reasons[:3] + cautions[:2]
    if not details:
        details = ["сигнали прогнозу змішані"]
    return f"{opener}: {', '.join(details)}."


def _range_score(value: float, *, ideal_min: float, ideal_max: float, hard_min: float, hard_max: float) -> float:
    if value <= hard_min or value >= hard_max:
        return 0
    if ideal_min <= value <= ideal_max:
        return 100
    if value < ideal_min:
        return (value - hard_min) / (ideal_min - hard_min) * 100
    return (hard_max - value) / (hard_max - ideal_max) * 100


def _inverse_score(value: float, *, ideal_max: float, hard_max: float) -> float:
    if value <= ideal_max:
        return 100
    if value >= hard_max:
        return 0
    return (hard_max - value) / (hard_max - ideal_max) * 100


def _value(weather: dict, key: str, default: float) -> float:
    value = weather.get(key, default)
    return default if value is None else float(value)


def _optional_value(values: dict, key: str) -> float | None:
    value = values.get(key)
    return None if value is None else float(value)
