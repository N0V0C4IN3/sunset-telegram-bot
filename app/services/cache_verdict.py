"""The rules for serving a stored forecast, as a single decision.

`ForecastService` owns the I/O; this module owns the judgement. Keeping them
apart is what makes the whole decision — not just its leaves — reachable from a
test without a session.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.db.models import ForecastCache
from app.services.solar import SolarEvent
from app.services.sunsethue import PROVIDER_SUNSETHUE, latest_scheduled_run


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

    Ask which run is latest: serve `held` while it still is, re-ask Sunsethue
    once it is not. Runs are often late, and re-asking for each forecast before
    the new run lands would spend five credits for the same answer.
    """

    held: ForecastCache


@dataclass(frozen=True)
class Fetch:
    """Nothing servable is stored: ask a provider."""


CacheVerdict = Serve | RetryPreferred | Recheck | Fetch


def verdict_for(
    candidates: Sequence[ForecastCache],
    local_now: datetime,
    event: SolarEvent,
    preferred_available: bool,
    on_date: date | None = None,
) -> CacheVerdict:
    """Decide what to do with the rows held for a user.

    `candidates` are the stored forecasts that already passed the TTL, ordered
    soonest sunset first. `local_now` carries the user's timezone. `on_date`
    narrows the question to one day; without it the next upcoming `event` wins.
    A row for the other kind of Solar Event is never an answer, even on the same day.
    """
    timezone = local_now.tzinfo
    for cached in candidates:
        if cached.event != event:
            continue
        if on_date is not None and cached.forecast_date != on_date:
            continue
        if not _event_is_upcoming(cached, timezone, local_now):
            continue
        if cached.provider == PROVIDER_SUNSETHUE:
            verdict = _sunsethue_verdict(cached, local_now)
            if verdict is not None:
                return verdict
            continue
        if preferred_available:
            return RetryPreferred(cached)
        # Nobody better to ask, so the provisional row is all there is.
        return Serve(cached)
    return Fetch()


def _as_local_time(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _event_is_upcoming(cache: ForecastCache, timezone: ZoneInfo, local_now: datetime) -> bool:
    return _as_local_time(cache.event_at, timezone) > local_now


def _sunsethue_verdict(cache: ForecastCache, local_now: datetime) -> Serve | Recheck | None:
    """Serve, Recheck, or None for a row too old to serve.

    A row names the run it comes from and when Sunsethue expects the next one,
    and is good until then; with no update expected it is good until the event.
    A row stored before responses named their run is judged by the schedule.
    """
    if cache.run_at is None:
        return Serve(cache) if _sunsethue_cache_is_current(cache, local_now) else None
    if cache.next_update_at is None or local_now < as_utc(cache.next_update_at):
        return Serve(cache)
    return Recheck(cache)


def _sunsethue_cache_is_current(cache: ForecastCache, local_now: datetime) -> bool:
    """For a row that names no run: True while no scheduled run has landed since it was fetched."""
    return as_utc(cache.fetched_at) >= latest_scheduled_run(local_now)


def as_utc(value: datetime) -> datetime:
    """Stored timestamps are UTC; a naive one is read as such."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
