from dataclasses import dataclass


@dataclass(frozen=True)
class SunsetScore:
    score: int
    description: str


def score_sunset(weather: dict) -> SunsetScore:
    cloud = _value(weather, "cloud_cover", 50)
    low = _value(weather, "cloud_cover_low", cloud)
    mid = _value(weather, "cloud_cover_mid", cloud)
    high = _value(weather, "cloud_cover_high", cloud)
    precipitation = _value(weather, "precipitation_probability", 0)
    visibility = _value(weather, "visibility", 10000)
    humidity = _value(weather, "relative_humidity_2m", 60)

    score = 55
    reasons: list[str] = []
    cautions: list[str] = []

    if 20 <= high <= 80:
        score += 16
        reasons.append("високі хмари можуть гарно зловити колір")
    elif high > 85:
        score -= 8
        cautions.append("високі хмари можуть бути занадто щільними")
    else:
        score -= 4

    if 10 <= mid <= 65:
        score += 10
        reasons.append("середня хмарність виглядає корисною для кольору")
    elif mid > 80:
        score -= 10
        cautions.append("середні хмари можуть приглушити світло")

    if low <= 35:
        score += 12
        reasons.append("низьких хмар біля горизонту небагато")
    elif low <= 65:
        score -= 6
        cautions.append("частина низьких хмар може пом'якшити картинку")
    else:
        score -= 22
        cautions.append("низькі хмари можуть закрити захід сонця")

    if precipitation <= 15:
        score += 10
        reasons.append("ризик дощу низький")
    elif precipitation <= 40:
        score -= 8
        cautions.append("можливі короткі опади")
    else:
        score -= 25
        cautions.append("ризик дощу високий")

    if visibility >= 10000:
        score += 8
        reasons.append("видимість виглядає доброю")
    elif visibility < 5000:
        score -= 12
        cautions.append("серпанок або погана видимість можуть приглушити кольори")

    if humidity > 90:
        score -= 8
        cautions.append("висока вологість може означати серпанок або туман")

    score = max(0, min(100, score))
    description = _build_description(score, reasons, cautions)
    return SunsetScore(score=score, description=description)


def _value(weather: dict, key: str, default: float) -> float:
    value = weather.get(key, default)
    return default if value is None else float(value)


def _build_description(score: int, reasons: list[str], cautions: list[str]) -> str:
    if score >= 80:
        opener = "Виглядає багатообіцяюче"
    elif score >= 60:
        opener = "Варто глянути"
    elif score >= 40:
        opener = "Може бути скромно, але не без шансів"
    else:
        opener = "Схоже, сильного заходу сонця не буде"

    details = reasons[:3] + cautions[:2]
    if not details:
        details = ["сигнали прогнозу змішані"]
    sentence = ", ".join(details)
    return f"{opener}: {sentence}."
