"""Every view that carries the day buttons offers Завтра by the same rule.

The settings view used to build its keyboard without asking, so Завтра was
missing there even while today's sunset was still ahead.
"""

from dataclasses import dataclass, field

import pytest

from app.bot import handlers

KYIV_LOCATION = (50.45, 30.52)


@dataclass
class FakeUserSettings:
    threshold: int = 70
    lead_time_minutes: int = 90
    subscribed: bool = True
    pending_input: str | None = None


@dataclass
class FakeUser:
    id: int = 1
    timezone: str = "Europe/Kyiv"
    latitude_encrypted: str | None = "ciphertext"
    longitude_encrypted: str | None = "ciphertext"
    settings: FakeUserSettings = field(default_factory=FakeUserSettings)


class FakeRepo:
    def __init__(self, user: FakeUser):
        self.user = user

    async def get_or_create_user(self, user_id, threshold, lead_time):
        return self.user

    async def get_user_with_settings(self, user_id):
        return self.user

    async def set_subscribed(self, user_id, subscribed):
        self.user.settings.subscribed = subscribed

    async def set_pending_input(self, user_id, pending):
        self.user.settings.pending_input = pending

    def decrypt_location(self, user):
        return None if user.latitude_encrypted is None else KYIV_LOCATION


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


class FakeBot:
    def __init__(self):
        self.markups = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.markups.append(reply_markup)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        self.markups.append(reply_markup)


def callbacks(markup) -> set[str]:
    return {button.callback_data for row in markup.inline_keyboard for button in row}


@pytest.fixture
def views(monkeypatch):
    def build(user: FakeUser, sunset_ahead: bool):
        monkeypatch.setattr(handlers, "Repository", lambda _session: FakeRepo(user))
        monkeypatch.setattr(handlers, "next_day_available", lambda _location, _now: sunset_ahead)
        context = handlers.HandlerContext(
            session_factory=FakeSession,
            settings=type("Settings", (), {"default_notification_threshold": 70,
                                            "default_notification_lead_time_minutes": 90})(),
            weather=None,
            sunsethue=None,
        )
        return context

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("sunset_ahead", [True, False])
async def test_the_settings_view_offers_tomorrow_by_the_card_rule(views, sunset_ahead):
    bot = FakeBot()
    with handlers.handler_context(views(FakeUser(), sunset_ahead)):
        await handlers.show_settings(bot, chat_id=1, user_id=1)
    assert ("tomorrow" in callbacks(bot.markups[-1])) is sunset_ahead


@pytest.mark.asyncio
async def test_the_subscription_toggle_keeps_tomorrow(views):
    bot = FakeBot()
    with handlers.handler_context(views(FakeUser(), sunset_ahead=True)):
        await handlers.set_subscription(bot, chat_id=1, user_id=1, subscribed=False)
    assert "tomorrow" in callbacks(bot.markups[-1])


@pytest.mark.asyncio
async def test_without_a_location_there_is_no_tomorrow_to_offer(views):
    bot = FakeBot()
    user = FakeUser(latitude_encrypted=None, longitude_encrypted=None)
    with handlers.handler_context(views(user, sunset_ahead=True)):
        await handlers.show_settings(bot, chat_id=1, user_id=1)
    assert "tomorrow" not in callbacks(bot.markups[-1])
