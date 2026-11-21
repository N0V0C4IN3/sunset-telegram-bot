"""The rules for serving a stored forecast, as a single decision.

`ForecastService` owns the I/O; this module owns the judgement, freshness
included. Keeping them apart is what makes the whole decision — not just its
leaves — reachable from a test without a session.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.models import ForecastCache
from app.services.solar import SolarEvent
from app.services.sunsethue import PROVIDER_SUNSETHUE


@dataclass(frozen=True)
class Serve:
    """The stored forecast is settled: serve it without asking anyone."""

    cached: ForecastCache


@dataclass(frozen=True)
class RetryPreferred:
    """A provisional forecast is held.

    Re-ask the preferred provider first; serve `held` if it declines again,
    rather than spending a second call on the other provider.
    """

    held: ForecastCache


@dataclass(frozen=True)
class Recheck:
    """Sunsethue expected a newer run than `held` comes from by now.

    Ask which run is latest: serve `held` while it still is, otherwise treat it
    like a provisional row. Runs are often late, and re-asking for each forecast
    before the new run lands would spend five credits for the same answer.
    """

    held: ForecastCache

    def held_is_latest(self, latest_run: datetime | None) -> bool:
        """True when the latest run is known and `held` already comes from it."""
        return latest_run is not None and latest_run <= as_utc(self.held.run_at)


@dataclass(frozen=True)
class Fetch:
    """Nothing servable is stored: ask a provider."""


CacheVerdict = Serve | RetryPreferred | Recheck | Fetch


def verdict_for(
    candidates: Sequence[ForecastCache],
    local_now: datetime,
    event: SolarEvent,
    preferred_available: bool,
    ttl: timedelta,
    on_date: date | None = None,
) -> CacheVerdict:
    """Decide what to do with the rows held for a user.

    `candidates` are the stored forecasts for the days in question, soonest
    first. `local_now` carries the user's timezone. `on_date` narrows the
    question to one day; without it the next upcoming `event` wins. A row for
    the other kind of Solar Event is never an answer, even on the same day.

    A Sunsethue row that names its run is fresh until Sunsethue expects the
    next one, and until the event when it expects none. Every other row is
    fresh for `ttl` after it was fetched.
    """
    timezone = local_now.tzinfo
    for cached in candidates:
        if cached.event != event:
            continue
        if on_date is not None and cached.forecast_date != on_date:
            continue
        if not _event_is_upcoming(cached, timezone, local_now):
            continue
        if cached.provider == PROVIDER_SUNSETHUE and cached.run_at is not None:
            if cached.next_update_at is None or local_now < as_utc(cached.next_update_at):
                return Serve(cached)
            return Recheck(cached)
        if as_utc(cached.fetched_at) < local_now - ttl:
            continue
        if cached.provider == PROVIDER_SUNSETHUE or not preferred_available:
            # A Sunsethue row that names no run, or a provisional row with
            # nobody better to ask: either way, it is all there is.
            return Serve(cached)
        return RetryPreferred(cached)
    return Fetch()


def _as_local_time(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _event_is_upcoming(cache: ForecastCache, timezone: ZoneInfo, local_now: datetime) -> bool:
    return _as_local_time(cache.event_at, timezone) > local_now


def as_utc(value: datetime) -> datetime:
    """Stored timestamps are UTC; a naive one is read as such."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
