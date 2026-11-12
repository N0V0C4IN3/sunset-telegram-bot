"""Show a forecast card, reusing the photo when Telegram already holds it.

An edit that uploads a new image is the slow call. Probing from the Pi showed
Telegram acknowledging the whole upload within 0.3 s and then holding the reply
for up to 18 s, while an edit naming a file_id it already held answered in
0.15-0.2 s every time. Every card with the same `CardFace` is the same picture,
so the file_id Telegram returns for the first upload serves every later showing
of it, in any chat: a second press, the other user with the same sunset, the
button under a notification.

The ids live in process memory only. A restart costs one upload per card, and a
redeploy that changes the design can never be served an old picture.
"""

import logging
from collections import OrderedDict

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputFile, InputMediaPhoto, Message

from app.bot.card import CARD_FILENAME, CardFace, render_face
from app.bot.tracing import CardPhases

logger = logging.getLogger(__name__)


class UploadedCards:
    """file_ids of card photos Telegram holds, by what is drawn on them.

    A card's day and time lines change it, so a subscriber's card turns over
    about daily; the cap only has to outlast a day of everyone's cards, and the
    least recently shown goes first.
    """

    def __init__(self, capacity: int = 512) -> None:
        self._capacity = capacity
        self._ids: OrderedDict[CardFace, str] = OrderedDict()

    def get(self, face: CardFace) -> str | None:
        file_id = self._ids.get(face)
        if file_id is not None:
            self._ids.move_to_end(face)
        return file_id

    def remember(self, face: CardFace, sent: Message | bool | None) -> None:
        """Keep the file_id from what sendPhoto/editMessageMedia returned.

        Edits of inline messages return True instead of the message, and a
        "not modified" edit returns nothing: neither names a photo.
        """
        photo = getattr(sent, "photo", None)
        if not photo:
            return
        self._ids[face] = photo[-1].file_id
        self._ids.move_to_end(face)
        while len(self._ids) > self._capacity:
            self._ids.popitem(last=False)

    def forget(self, face: CardFace) -> None:
        self._ids.pop(face, None)


uploaded_cards = UploadedCards()


async def show_card(
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    face: CardFace,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    phases: CardPhases | None = None,
    cards: UploadedCards = uploaded_cards,
) -> None:
    """Edit `message_id` into the card, or send it when there is none.

    A held file_id is tried first; if Telegram rejects it, it is forgotten and
    the card is rendered and uploaded as if it had never been seen.
    """
    file_id = cards.get(face)
    if file_id is not None:
        try:
            await _put_photo(bot, chat_id, message_id, file_id, caption, reply_markup)
            _note(phases, photo="reused")
            return
        except TelegramBadRequest:
            cards.forget(face)
            logger.info("card_file_id_rejected falling_back=upload")

    image = await render_face(face)
    if phases is not None:
        phases.lap("render")
    upload = BufferedInputFile(image, filename=CARD_FILENAME)
    sent = await _put_photo(bot, chat_id, message_id, upload, caption, reply_markup)
    cards.remember(face, sent)
    _note(phases, photo="uploaded")


async def _put_photo(
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    photo: InputFile | str,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> Message | bool | None:
    """Edit in place when we came from a callback, else send.

    editMessageMedia replaces a text message with media as well as swapping one
    photo for another, so both /settings -> card and card -> card edit cleanly.
    "Not modified" means the card is already showing, which is success.
    """
    if message_id is not None:
        try:
            return await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return None
            logger.info("card_edit_failed falling_back=send")
    return await bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup)


def _note(phases: CardPhases | None, **fields: object) -> None:
    if phases is not None:
        phases.note(**fields)
