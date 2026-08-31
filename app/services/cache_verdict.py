"""The rules for serving a stored forecast, as a single decision.

`ForecastService` owns the I/O; this module owns the judgement. Keeping them
apart is what makes the whole decision — not just its leaves — reachable from a
test without a session.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db.models import ForecastCache
from app.services.sunsethue import PROVIDER_SUNSETHUE

SUNSETHUE_MODEL_AVAILABLE_AT_UTC = (
    time(4, 30, tzinfo=UTC),
    time(10, 30, tzinfo=UTC),
    time(16, 30, tzinfo=UTC),
    time(22, 30, tzinfo=UTC),
)


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
class Fetch:
    """Nothing servable is stored: ask a provider."""


CacheVerdict = Serve | RetryPreferred | Fetch


def verdict_for(
    candidates: Sequence[ForecastCache],
    local_now: datetime,
    preferred_available: bool,
    on_date: date | None = None,
) -> CacheVerdict:
    """Decide what to do with the rows held for a user.

    `candidates` are the stored forecasts that already passed the TTL, ordered
    soonest sunset first. `local_now` carries the user's timezone. `on_date`
    narrows the question to one day; without it the next upcoming sunset wins.
    """
    timezone = local_now.tzinfo
    for cached in candidates:
        if on_date is not None and cached.forecast_date != on_date:
            continue
        if not _sunset_is_upcoming(cached, timezone, local_now):
            continue
        if cached.provider == PROVIDER_SUNSETHUE:
            if _sunsethue_cache_is_current(cached, local_now):
                return Serve(cached)
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


def _sunset_is_upcoming(cache: ForecastCache, timezone: ZoneInfo, local_now: datetime) -> bool:
    return _as_local_time(cache.sunset_at, timezone) > local_now


def _sunsethue_cache_is_current(cache: ForecastCache, local_now: datetime) -> bool:
    """True while no Sunsethue model update has landed since this row was fetched."""
    fetched_at = cache.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    fetched_at_utc = fetched_at.astimezone(UTC)
    now_utc = local_now.astimezone(UTC)
    latest_model_update = _latest_sunsethue_model_update(now_utc)
    return latest_model_update is None or fetched_at_utc >= latest_model_update


def _latest_sunsethue_model_update(now_utc: datetime) -> datetime | None:
    today_updates = [
        datetime.combine(now_utc.date(), update_time)
        for update_time in SUNSETHUE_MODEL_AVAILABLE_AT_UTC
        if datetime.combine(now_utc.date(), update_time) <= now_utc
    ]
    if today_updates:
        return max(today_updates)

    yesterday = now_utc.date() - timedelta(days=1)
    return datetime.combine(yesterday, SUNSETHUE_MODEL_AVAILABLE_AT_UTC[-1])
