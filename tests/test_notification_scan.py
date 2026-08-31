"""The notification pass must survive one subscriber failing.

A subscriber who has blocked the bot makes send_photo raise. Before, that ended
the pass for everyone after them and rolled back the Notification Decisions
already taken, so those subscribers were notified twice for the same sunset.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.scheduler import notify_subscriber, run_notification_scan
from app.services.weather import ForecastResult

TZ = "Europe/Kyiv"
SUNSET = datetime(2026, 8, 31, 20, 0, tzinfo=ZoneInfo(TZ))


@dataclass
class FakeSettings:
    notification_scan_interval_minutes: int = 30
    forecast_cache_retention_days: int = 7


@dataclass
class FakeUserSettings:
    threshold: int = 70
    lead_time_minutes: int = 90
    last_notified_for_date: date | None = None
    subscribed: bool = True


@dataclass
class FakeUser:
    id: int
    timezone: str = TZ
    settings: FakeUserSettings = field(default_factory=FakeUserSettings)


class FakeSunsethue:
    is_configured = True


class FakeRepo:
    def __init__(self, users):
        self.users = users
        self.marked: list[tuple[int, date]] = []
        self.uncommitted: list[tuple[int, date]] = []

    async def subscribed_users(self):
        return self.users

    async def mark_notified(self, user_id, notified_date):
        self.uncommitted.append((user_id, notified_date))

    async def delete_old_forecasts(self, retention_days):
        return 0


class FakeSession:
    """Commits and rolls back the repo's pending marks, like the real unit of work."""

    def __init__(self, repo):
        self.repo = repo
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1
        self.repo.marked.extend(self.repo.uncommitted)
        self.repo.uncommitted.clear()

    async def rollback(self):
        self.rollbacks += 1
        self.repo.uncommitted.clear()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeBot:
    def __init__(self, blocked_by: set[int] | None = None):
        self.blocked_by = blocked_by or set()
        self.sent_to: list[int] = []

    async def send_photo(self, chat_id, photo, caption=None):
        if chat_id in self.blocked_by:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent_to.append(chat_id)


def forecast(score: int) -> ForecastResult:
    return ForecastResult(
        provider="sunsethue",
        forecast_date=SUNSET.date(),
        sunset_at=SUNSET,
        score=score,
        description="",
        weather_data={},
    )


def inside_window(user: FakeUser) -> datetime:
    return SUNSET - timedelta(minutes=user.settings.lead_time_minutes)


# notify_subscriber: the per-subscriber unit.


@pytest.mark.asyncio
async def test_a_good_forecast_inside_the_window_is_sent_and_consumes_the_day():
    user, repo, bot = FakeUser(1), FakeRepo([]), FakeBot()
    sent = await notify_subscriber(
        bot, repo, user, FakeSettings(), FakeSunsethue(), forecast(90), inside_window(user)
    )
    assert sent is True
    assert bot.sent_to == [1]
    assert repo.uncommitted == [(1, SUNSET.date())]


@pytest.mark.asyncio
async def test_a_forecast_outside_the_window_is_neither_sent_nor_marked():
    user, repo, bot = FakeUser(1), FakeRepo([]), FakeBot()
    sent = await notify_subscriber(
        bot, repo, user, FakeSettings(), FakeSunsethue(), forecast(90), SUNSET - timedelta(hours=6)
    )
    assert sent is False
    assert bot.sent_to == []
    assert repo.uncommitted == []


@pytest.mark.asyncio
async def test_a_settled_score_below_threshold_consumes_the_day_without_sending():
    user, repo, bot = FakeUser(1), FakeRepo([]), FakeBot()
    sent = await notify_subscriber(
        bot, repo, user, FakeSettings(), FakeSunsethue(), forecast(10), inside_window(user)
    )
    assert sent is False
    assert bot.sent_to == []
    assert repo.uncommitted == [(1, SUNSET.date())]


@pytest.mark.asyncio
async def test_a_provisional_score_below_threshold_leaves_the_day_open():
    user, repo, bot = FakeUser(1), FakeRepo([]), FakeBot()
    provisional = ForecastResult(
        provider="open_meteo",
        forecast_date=SUNSET.date(),
        sunset_at=SUNSET,
        score=10,
        description="",
        weather_data={},
    )
    sent = await notify_subscriber(
        bot, repo, user, FakeSettings(), FakeSunsethue(), provisional, inside_window(user)
    )
    assert sent is False
    assert repo.uncommitted == []


# run_notification_scan: the pass as a whole.


@pytest.fixture
def scan(monkeypatch):
    """Runs a scan over the given users with fakes swapped in for the real collaborators."""

    def build(users, blocked_by=None, scores=None):
        repo = FakeRepo(users)
        session = FakeSession(repo)
        bot = FakeBot(blocked_by)

        class FakeForecastService:
            def __init__(self, *args, **kwargs):
                pass

            async def today_for_user(self, user, on_date=None):
                return forecast((scores or {}).get(user.id, 90))

        monkeypatch.setattr("app.services.scheduler.Repository", lambda _session: repo)
        monkeypatch.setattr("app.services.scheduler.ForecastService", FakeForecastService)
        monkeypatch.setattr(
            "app.services.scheduler.render_forecast_card",
            lambda *args, **kwargs: _immediate(b""),
        )
        monkeypatch.setattr("app.services.scheduler.format_forecast", lambda *a, **k: "")
        # Freeze "now" inside every subscriber's window.
        monkeypatch.setattr(
            "app.services.scheduler.datetime",
            _FrozenDatetime(SUNSET - timedelta(minutes=90)),
        )
        return bot, repo, session

    return build


async def _immediate(value):
    return value


class _FrozenDatetime(datetime):
    """datetime.now(tz) pinned; everything else behaves normally."""

    def __new__(cls, moment):
        instance = super().__new__(cls, 2026, 1, 1)
        instance._moment = moment
        return instance

    def now(self, tz=None):
        return self._moment.astimezone(tz) if tz else self._moment


@pytest.mark.asyncio
async def test_a_blocked_subscriber_does_not_end_the_pass(scan):
    users = [FakeUser(1), FakeUser(2), FakeUser(3)]
    bot, repo, session = scan(users, blocked_by={2})

    sent = await run_notification_scan(bot, lambda: session, FakeSettings(), None, FakeSunsethue())

    assert sent == 2
    assert bot.sent_to == [1, 3], "the subscriber after the blocked one must still be notified"


@pytest.mark.asyncio
async def test_a_blocked_subscriber_does_not_roll_back_an_earlier_decision(scan):
    users = [FakeUser(1), FakeUser(2), FakeUser(3)]
    bot, repo, session = scan(users, blocked_by={2})

    await run_notification_scan(bot, lambda: session, FakeSettings(), None, FakeSunsethue())

    marked = {user_id for user_id, _ in repo.marked}
    assert 1 in marked, "a decision taken before the failure must survive it"
    assert 3 in marked
    assert 2 not in marked, "the blocked subscriber's failed send must not be recorded as notified"


@pytest.mark.asyncio
async def test_every_subscriber_is_committed_as_they_are_decided(scan):
    users = [FakeUser(1), FakeUser(2), FakeUser(3)]
    bot, repo, session = scan(users)

    await run_notification_scan(bot, lambda: session, FakeSettings(), None, FakeSunsethue())

    assert session.commits >= len(users), "each subscriber commits, not one commit at the end"
    assert session.rollbacks == 0
