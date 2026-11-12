from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageMedia
from aiogram.types import InputMediaPhoto
from aiogram.types import BufferedInputFile

from app.bot import card_photos
from app.bot.card import CardFace, card_face
from app.bot.card_photos import UploadedCards, show_card
from app.services.solar import SolarEvent
from app.services.weather import ForecastResult

ZONE = "Europe/Kyiv"


def face(time: str = "16:21", score: int = 72) -> CardFace:
    return CardFace(score=score, day="сьогодні", time=time, event=SolarEvent.SUNSET)


def a_forecast(score: int = 72, days_ahead: int = 0) -> ForecastResult:
    now = datetime.now(ZoneInfo(ZONE))
    event_at = (now + timedelta(days=days_ahead)).replace(hour=16, minute=21, second=0, microsecond=0)
    return ForecastResult(
        provider="sunsethue",
        event=SolarEvent.SUNSET,
        forecast_date=event_at.date(),
        event_at=event_at,
        score=score,
        description="",
        weather_data={},
    )


def bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=EditMessageMedia(media=InputMediaPhoto(media="x")), message=message)


def a_photo_message(file_id: str) -> SimpleNamespace:
    # Telegram lists every size it made; any of them names the photo.
    return SimpleNamespace(photo=[SimpleNamespace(file_id=f"{file_id}-small"), SimpleNamespace(file_id=file_id)])


class FakeBot:
    """Records what each call carried: an upload, or a file_id Telegram holds."""

    def __init__(self, edit_errors: list[str] | None = None, send_errors: list[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.edit_errors = list(edit_errors or [])
        self.send_errors = list(send_errors or [])
        self.uploads = 0

    def _carried(self, photo) -> str:
        if isinstance(photo, BufferedInputFile):
            self.uploads += 1
            return "upload"
        return photo

    async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
        self.calls.append(("edit", self._carried(media.media)))
        if self.edit_errors:
            raise bad_request(self.edit_errors.pop(0))
        return a_photo_message(f"id-{self.uploads}")

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.calls.append(("send", self._carried(photo)))
        if self.send_errors:
            raise bad_request(self.send_errors.pop(0))
        return a_photo_message(f"id-{self.uploads}")


@pytest.fixture
def renders(monkeypatch) -> list[CardFace]:
    rendered: list[CardFace] = []

    async def render(face):
        rendered.append(face)
        return b"jpeg"

    monkeypatch.setattr(card_photos, "render_face", render)
    return rendered


@pytest.mark.asyncio
async def test_a_card_seen_for_the_first_time_is_uploaded_and_remembered(renders):
    bot, cards = FakeBot(), UploadedCards()

    await show_card(bot, 1, 7, face(), "caption", cards=cards)

    assert bot.calls == [("edit", "upload")]
    assert cards.get(face()) == "id-1"


@pytest.mark.asyncio
async def test_the_same_card_again_points_at_the_photo_telegram_already_has(renders):
    bot, cards = FakeBot(), UploadedCards()

    await show_card(bot, 1, 7, face(), "caption", cards=cards)
    await show_card(bot, 2, 9, face(), "caption", cards=cards)

    assert bot.calls == [("edit", "upload"), ("edit", "id-1")]
    assert renders == [face()], "a reused card is not rendered again"


@pytest.mark.asyncio
async def test_a_card_with_anything_drawn_differently_is_uploaded(renders):
    bot, cards = FakeBot(), UploadedCards()

    await show_card(bot, 1, 7, face(time="16:21"), "caption", cards=cards)
    await show_card(bot, 1, 7, face(time="16:22"), "caption", cards=cards)

    assert bot.calls == [("edit", "upload"), ("edit", "upload")]


@pytest.mark.asyncio
async def test_a_notification_is_sent_and_feeds_the_buttons_that_follow(renders):
    bot, cards = FakeBot(), UploadedCards()

    await show_card(bot, 1, None, face(), "caption", cards=cards)
    await show_card(bot, 1, 7, face(), "caption", cards=cards)

    assert bot.calls == [("send", "upload"), ("edit", "id-1")]


@pytest.mark.asyncio
async def test_pressing_the_button_for_the_card_already_shown_changes_nothing(renders):
    cards = UploadedCards()
    cards.remember(face(), a_photo_message("held"))
    bot = FakeBot(edit_errors=["Bad Request: message is not modified"])

    await show_card(bot, 1, 7, face(), "caption", cards=cards)

    assert bot.calls == [("edit", "held")]
    assert cards.get(face()) == "held"


@pytest.mark.asyncio
async def test_a_file_id_telegram_rejects_is_forgotten_and_the_card_uploaded(renders):
    cards = UploadedCards()
    cards.remember(face(), a_photo_message("stale"))
    bot = FakeBot(
        edit_errors=["Bad Request: wrong file identifier/HTTP URL specified"],
        send_errors=["Bad Request: wrong file identifier/HTTP URL specified"],
    )

    await show_card(bot, 1, 7, face(), "caption", cards=cards)

    assert bot.calls == [("edit", "stale"), ("send", "stale"), ("edit", "upload")]
    assert cards.get(face()) == "id-1"


@pytest.mark.asyncio
async def test_a_message_that_cannot_be_edited_still_gets_the_held_photo(renders):
    cards = UploadedCards()
    cards.remember(face(), a_photo_message("held"))
    bot = FakeBot(edit_errors=["Bad Request: message to edit not found"])

    await show_card(bot, 1, 7, face(), "caption", cards=cards)

    assert bot.calls == [("edit", "held"), ("send", "held")]
    assert renders == []


def test_the_least_recently_used_card_makes_room():
    cards = UploadedCards(capacity=2)
    cards.remember(face("16:01"), a_photo_message("a"))
    cards.remember(face("16:02"), a_photo_message("b"))
    cards.get(face("16:01"))
    cards.remember(face("16:03"), a_photo_message("c"))

    assert cards.get(face("16:01")) == "a"
    assert cards.get(face("16:02")) is None
    assert cards.get(face("16:03")) == "c"


def test_an_edit_that_returns_no_message_is_not_remembered():
    cards = UploadedCards()
    cards.remember(face(), True)  # what editMessageMedia returns for inline messages

    assert cards.get(face()) is None


def test_the_face_is_what_the_card_prints():
    assert card_face(a_forecast(72), ZONE) == card_face(a_forecast(72), ZONE)
    assert card_face(a_forecast(72), ZONE) != card_face(a_forecast(73), ZONE)
    assert card_face(a_forecast(72), ZONE).day == "сьогодні"
    assert card_face(a_forecast(72, days_ahead=1), ZONE).day == "завтра"
    assert card_face(a_forecast(72), ZONE).time == "16:21"


def test_the_face_clamps_the_score_like_the_render_does():
    assert card_face(a_forecast(140), ZONE) == card_face(a_forecast(100), ZONE)
