"""Regenerate the README's images from the bot's own renderer.

    .venv\\Scripts\\python.exe .github/readme/make_images.py

Writes example cards and the score-to-sky strip next to this file. Run it again
after changing the card's palette or layout so the README shows what users see.
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
# Run as a plain script from anywhere: the repo root holds the `app` package.
sys.path.insert(0, str(HERE.parents[1]))

from app.bot.card import FONT_REGULAR, palette, render_card  # noqa: E402
from app.services.solar import SolarEvent  # noqa: E402

# Real-looking Kyiv times for early November.
CARDS = [
    ("card-sunset-high.jpg", 86, "сьогодні", "16:24", SolarEvent.SUNSET),
    ("card-sunrise-mid.jpg", 52, "завтра", "06:58", SolarEvent.SUNRISE),
    ("card-sunset-low.jpg", 14, "завтра", "16:22", SolarEvent.SUNSET),
]


def save_jpeg(image_bytes: bytes, name: str) -> None:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image.save(HERE / name, "JPEG", quality=85, optimize=True, progressive=True)


def sky_column(stops: list[tuple[int, int, int]], height: int) -> list[tuple[int, int, int]]:
    """The five sky stops, top to bottom, blended into `height` pixels."""
    pixels = []
    for y in range(height):
        position = y / (height - 1) * (len(stops) - 1)
        low = int(position)
        high = min(low + 1, len(stops) - 1)
        weight = position - low
        pixels.append(tuple(round(a + (b - a) * weight) for a, b in zip(stops[low], stops[high])))
    return pixels


def score_strip() -> Image.Image:
    """Every score from 0 to 100 as a column of its sky, one band per Solar Event."""
    column, band, gap, left, top, bottom = 7, 120, 34, 110, 16, 40
    width = left + 101 * column + 24
    height = top + 2 * band + gap + bottom
    image = Image.new("RGB", (width, height), (24, 22, 32))
    draw = ImageDraw.Draw(image)
    label = ImageFont.truetype(FONT_REGULAR, 20)
    tick = ImageFont.truetype(FONT_REGULAR, 16)

    for row, (event, name) in enumerate([(SolarEvent.SUNSET, "Sunset"), (SolarEvent.SUNRISE, "Sunrise")]):
        y0 = top + row * (band + gap)
        for score in range(101):
            x0 = left + score * column
            for y, colour in enumerate(sky_column(palette(score, event), band)):
                draw.line([(x0, y0 + y), (x0 + column - 1, y0 + y)], fill=colour)
        draw.text((left - 14, y0 + band / 2), name, font=label, fill=(236, 233, 243), anchor="rm")

    axis_y = top + 2 * band + gap + 8
    for score in (0, 25, 50, 75, 100):
        x = left + score * column + column / 2
        draw.line([(x, axis_y - 6), (x, axis_y)], fill=(163, 157, 179), width=2)
        draw.text((x, axis_y + 4), str(score), font=tick, fill=(236, 233, 243), anchor="mt")
    return image


def main() -> None:
    for name, score, day, time, event in CARDS:
        save_jpeg(render_card(score, day, time, event), name)
    score_strip().save(HERE / "score-to-sky.png", optimize=True)
    for path in sorted(HERE.glob("*.jpg")) + [HERE / "score-to-sky.png"]:
        print(f"{path.name}: {path.stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main()
