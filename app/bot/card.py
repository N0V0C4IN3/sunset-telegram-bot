"""Render the forecast score as an image.

The score used to be drawn as a text bar of block characters, which fell out of
the UI font into a symbol font on Android and rasterised badly at phone DPI.
Drawing it ourselves makes the card identical on every client.

Pillow ships no Cyrillic, so the fonts here are vendored next to this module.
"""

import asyncio
import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from app.bot.messages import day_label, local_event_time
from app.services.solar import WORDS, SolarEvent
from app.services.weather import ForecastResult

FONTS = Path(__file__).parent / "assets" / "fonts"
FONT_BOLD = str(FONTS / "DejaVuSans-Bold.ttf")
FONT_REGULAR = str(FONTS / "DejaVuSans.ttf")

WIDTH, HEIGHT = 1024, 536

# The glow is blurred, which is the expensive part, so it is built at a fraction
# of the final size and scaled up. The blur hides the resampling.
GLOW_SCALE = 4

# Sky anchors, top of frame to bottom, five stops each so they interpolate.
# Deliberately continuous in the score: banding these made 44 and 45 look like
# different weather, which is not what a one point difference means.
Anchors = list[tuple[int, list[tuple[int, int, int]]]]


@dataclass(frozen=True)
class Sky:
    """Everything about the card that depends on the Solar Event."""

    anchors: Anchors
    # Where the sun sits, as fractions of the frame, and its tint.
    sun_across: float
    sun_down: float
    sun_tint: tuple[float, float, float]


SKIES = {
    # Warm dusk, the sun low under the gauge.
    SolarEvent.SUNSET: Sky(
        anchors=[
            (0, [(38, 44, 66), (58, 64, 88), (86, 88, 104), (118, 112, 118), (142, 132, 132)]),
            (45, [(44, 46, 78), (86, 62, 98), (140, 88, 104), (186, 124, 106), (210, 152, 112)]),
            (70, [(38, 44, 92), (100, 58, 112), (176, 84, 100), (226, 130, 92), (246, 168, 96)]),
            (100, [(30, 36, 96), (110, 50, 120), (200, 74, 92), (240, 132, 72), (255, 206, 120)]),
        ],
        sun_across=0.74,
        sun_down=0.80,
        sun_tint=(1.0, 0.74, 0.42),
    ),
    # Dawn: cooler — lavender overhead, rose, pale gold low down; the sun low in
    # the middle, clear of the gauge.
    SolarEvent.SUNRISE: Sky(
        anchors=[
            (0, [(40, 48, 70), (62, 70, 94), (92, 98, 116), (124, 124, 134), (150, 146, 150)]),
            (45, [(46, 56, 96), (92, 86, 132), (156, 116, 146), (204, 150, 146), (226, 184, 150)]),
            (70, [(40, 60, 116), (104, 92, 156), (186, 118, 156), (238, 160, 140), (252, 204, 150)]),
            (100, [(34, 62, 132), (110, 96, 172), (214, 120, 158), (252, 168, 128), (255, 228, 160)]),
        ],
        sun_across=0.50,
        sun_down=0.92,
        sun_tint=(1.0, 0.82, 0.62),
    ),
}

SCORE_AT = (70, HEIGHT // 2 - 40)
DAY_AT = (78, HEIGHT // 2 + 78)
TIME_AT = (78, HEIGHT // 2 + 134)

# A soft dark blur under the type, so it holds on the brightest sky: the lower
# frame is where both palettes are lightest, and where the day and time lines sit.
SHADOW = (40, 30, 50)
SHADOW_BLUR = 6


@lru_cache(maxsize=8)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def palette(score: int, event: SolarEvent) -> list[tuple[int, int, int]]:
    """Blend the two sky anchors either side of `score`."""
    anchors = SKIES[event].anchors
    score = max(0, min(100, score))
    for (low_score, low), (high_score, high) in zip(anchors, anchors[1:]):
        if low_score <= score <= high_score:
            weight = (score - low_score) / (high_score - low_score)
            return [
                tuple(round(a + (b - a) * weight) for a, b in zip(low_stop, high_stop))
                for low_stop, high_stop in zip(low, high)
            ]
    return anchors[-1][1]


def _gradient(score: int, event: SolarEvent) -> Image.Image:
    stops = palette(score, event)
    strip = Image.new("RGB", (1, len(stops)))
    for index, colour in enumerate(stops):
        strip.putpixel((0, index), colour)
    return strip.resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC)


def _with_glow(base: Image.Image, score: int, event: SolarEvent) -> Image.Image:
    """Screen a soft sun low in the frame; brighter the better the score."""
    small = (WIDTH // GLOW_SCALE, HEIGHT // GLOW_SCALE)
    glow = Image.new("RGB", small, (0, 0, 0))
    draw = ImageDraw.Draw(glow)
    sky = SKIES[event]
    centre_x, centre_y = int(small[0] * sky.sun_across), int(small[1] * sky.sun_down)
    radius = int(small[1] * (0.14 + score / 100 * 0.12))
    level = 70 + int(score / 100 * 170)
    draw.ellipse(
        [centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius],
        fill=tuple(int(level * channel) for channel in sky.sun_tint),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=small[1] * 0.11))
    return ImageChops.screen(base, glow.resize(base.size, Image.Resampling.BICUBIC))


def _gauge(image: Image.Image, score: int) -> Image.Image:
    """The arc, drawn on its own layer so the track keeps its transparency.

    Painting a translucent fill straight onto an RGB image drops the alpha
    silently, which made the unfilled track read as a full ring.
    """
    mask = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(mask)
    centre_x, centre_y, radius = int(WIDTH * 0.78), HEIGHT // 2, 150
    box = [centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius]
    draw.arc(box, start=135, end=405, width=26, fill=70)
    if score > 0:
        draw.arc(box, start=135, end=135 + int(270 * score / 100), width=26, fill=255)
    image.paste((255, 255, 255), (0, 0), mask)
    return image


def _text(
    image: Image.Image,
    position: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    shadow: int,
) -> None:
    """Draw one line of type over its own shadow, `shadow` being its darkness.

    Only the box around the line is blurred: blurring the whole frame per card
    cost more than everything else in a render put together.
    """
    left, top, right, bottom = ImageDraw.Draw(image).textbbox(position, text, font=font, anchor="lm")
    pad = SHADOW_BLUR * 3
    box = (max(left - pad, 0), max(top - pad, 0), min(right + pad, WIDTH), min(bottom + pad, HEIGHT))
    mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(mask).text(
        (position[0] - box[0], position[1] - box[1]), text, font=font, fill=shadow, anchor="lm"
    )
    image.paste(SHADOW, box, mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)))
    ImageDraw.Draw(image).text(position, text, font=font, fill=fill, anchor="lm")


# Everything above the day and time is a pure function of the integer score and
# the Solar Event, and the bot renders thousands of cards per process. A full
# 1024x536 RGB frame is ~1.6 MB, so this is deliberately small: it is here to
# catch the subscribers who share a score within a scan pass — sunsets, since
# only sunsets are notified — not to hold all 202 combinations.
@lru_cache(maxsize=12)
def _background(score: int, event: SolarEvent) -> Image.Image:
    image = _gauge(_with_glow(_gradient(score, event), score, event), score)
    # The score reading depends on nothing else, so it belongs in the cached
    # frame; only the day and the event time are per-subscriber.
    _text(image, SCORE_AT, f"{score}%", _font(FONT_BOLD, 170), (255, 255, 255), shadow=110)
    return image


def render_card(score: int, day_label: str, event_time: str, event: SolarEvent) -> bytes:
    """A PNG of the score card, ready for sendPhoto."""
    score = max(0, min(100, score))
    # Copied because the cached background must never be drawn on.
    image = _background(score, event).copy()

    _text(image, DAY_AT, day_label, _font(FONT_REGULAR, 44), (248, 244, 240), shadow=150)
    time_line = f"{WORDS[event].noun} о {event_time}"
    _text(image, TIME_AT, time_line, _font(FONT_REGULAR, 36), (250, 246, 242), shadow=170)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


async def render_forecast_card(result: ForecastResult, timezone: str) -> bytes:
    """Render off the event loop: Pillow is CPU-bound and this runs on a Pi."""
    return await asyncio.to_thread(
        render_card,
        result.score,
        day_label(result, timezone),
        local_event_time(result, timezone),
        result.event,
    )
