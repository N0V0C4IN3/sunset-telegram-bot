"""Phase timing for Telegram API calls and for card requests.

A card on the Pi sometimes takes 2-30 s, always after the forecast is ready, and
the stall does not reproduce outside the live bot. Two log lines locate it:

- `telegram_call`, one per Telegram API call made through `TracedAiohttpSession`
  (except the long poll). Marks are ms from the start of the call: `dns` or
  `dns_cached`, `reused` or `connected`, `sent` (headers written), `uploaded`
  (last body byte handed to the transport: it may still sit in socket buffers),
  `acked` (the kernel saw everything written acknowledged; Linux only, see
  `tcp_info`), then `replied` (the response headers arrived; the body read that
  follows is not counted) or `failed` with the error name. On Linux the line
  ends with `retrans` (segments retransmitted during the call) and `rtt` (ms).
- `card_timing`, one per card request from `card_phases`, written even when the
  card fails or returns early: the cache verdict, then cumulative marks per stage.

Permanent and deliberately low-volume: a few lines per button press. Logs stay
data-free: only the API method name is read from the URL, which also carries the
bot token, and anything that does not look like a method logs as `unknown`.
"""

import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from aiogram import __version__ as aiogram_version
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientSession, TCPConnector, TraceConfig
from aiohttp.hdrs import USER_AGENT
from aiohttp.http import SERVER_SOFTWARE

from app.bot.tcp_info import SendWatch

logger = logging.getLogger(__name__)

# The long poll waits up to 30 s by design and runs continuously; tracing it
# would only bury the calls that matter.
UNTRACED_METHODS = frozenset({"getUpdates"})

_API_PATH = re.compile(r"^/bot[^/]+/([A-Za-z]+)$")

Marks = list[tuple[str, float]]


def method_name(path: str) -> str:
    """The API method from a /bot<token>/<method> path, or `unknown`.

    Strict on purpose: a path of any other shape (no method, a file download)
    would otherwise put the token or a file path into the log.
    """
    match = _API_PATH.match(path)
    return match.group(1) if match else "unknown"


def phases(marks: Marks) -> str:
    """`name=ms` for each mark after the first, measured from the first."""
    start = marks[0][1]
    return " ".join(f"{name}={round(1000 * (at - start))}" for name, at in marks[1:])


def summarize(
    method: str,
    marks: Marks,
    error: BaseException | None = None,
    counters: dict[str, int] | None = None,
) -> str:
    """The `telegram_call` line for one request."""
    line = f"telegram_call method={method} {phases(sorted(marks, key=lambda mark: mark[1]))}"
    for name, value in (counters or {}).items():
        line += f" {name}={value}"
    if error is not None:
        line += f" error={type(error).__name__}"
    return line


# The transport of the request in flight. aiohttp gives trace callbacks no
# connection, so `WatchedConnector` sets this in the requesting task, where the
# headers-sent callback that follows can read it.
_transport: ContextVar[object | None] = ContextVar("telegram_transport", default=None)


class WatchedConnector(TCPConnector):
    """aiogram's connector, publishing each request's transport to the tracer."""

    async def connect(self, req, traces, timeout):
        connection = await super().connect(req, traces, timeout)
        _transport.set(connection.transport)
        return connection


def _trace_config() -> TraceConfig:
    config = TraceConfig()

    async def started(session, context: SimpleNamespace, params) -> None:
        context.marks = [("start", time.perf_counter())]
        context.uploaded = None
        context.watch = None

    async def headers_sent(session, context: SimpleNamespace, params) -> None:
        context.marks.append(("sent", time.perf_counter()))
        transport = _transport.get()
        if transport is not None and method_name(params.url.path) not in UNTRACED_METHODS:
            context.watch = SendWatch(transport)

    def mark(phase: str):
        async def record(session, context: SimpleNamespace, params) -> None:
            context.marks.append((phase, time.perf_counter()))

        return record

    async def chunk_sent(session, context: SimpleNamespace, params) -> None:
        # Fires per body chunk; only the last one matters.
        context.uploaded = time.perf_counter()

    def log(context: SimpleNamespace, url, outcome: str, error: BaseException | None = None) -> None:
        ended = time.perf_counter()
        method = method_name(url.path)
        if method in UNTRACED_METHODS:
            return
        marks = list(context.marks)
        if context.uploaded is not None:
            marks.append(("uploaded", context.uploaded))
        counters: dict[str, int] = {}
        if context.watch is not None:
            sent = next(at for name, at in marks if name == "sent")
            written = context.uploaded if context.uploaded is not None else sent
            acked, counters = context.watch.finish(written, ended)
            if acked is not None:
                marks.append(("acked", acked))
        marks.append((outcome, ended))
        logger.info(summarize(method, marks, error, counters))

    async def replied(session, context: SimpleNamespace, params) -> None:
        log(context, params.url, "replied")

    async def failed(session, context: SimpleNamespace, params) -> None:
        log(context, params.url, "failed", params.exception)

    config.on_request_start.append(started)
    config.on_dns_cache_hit.append(mark("dns_cached"))
    config.on_dns_resolvehost_end.append(mark("dns"))
    config.on_connection_queued_end.append(mark("queued"))
    config.on_connection_reuseconn.append(mark("reused"))
    config.on_connection_create_end.append(mark("connected"))
    config.on_request_headers_sent.append(headers_sent)
    config.on_request_chunk_sent.append(chunk_sent)
    config.on_request_end.append(replied)
    config.on_request_exception.append(failed)
    return config


class TracedAiohttpSession(AiohttpSession):
    """aiogram's session with phase tracing attached.

    Mirrors `AiohttpSession.create_session`, whose ClientSession offers no hook
    for trace configs. `test_tracing.py` checks the two still build the same
    session, so an aiogram upgrade that changes it fails the suite. The one
    deliberate difference is `WatchedConnector`, a subclass of aiogram's own.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self._connector_type is TCPConnector:
            self._connector_type = WatchedConnector

    async def create_session(self) -> ClientSession:
        if self._should_reset_connector:
            await self.close()

        if self._session is None or self._session.closed:
            self._session = ClientSession(
                connector=self._connector_type(**self._connector_init),
                headers={USER_AGENT: f"{SERVER_SOFTWARE} aiogram/{aiogram_version}"},
                trace_configs=[_trace_config()],
            )
            self._should_reset_connector = False

        return self._session


class CardPhases:
    """Marks and notes for one card request, logged once by `card_phases`."""

    def __init__(self) -> None:
        self.marks: Marks = [("start", time.perf_counter())]
        self.notes: dict[str, object] = {}

    def lap(self, phase: str) -> None:
        self.marks.append((phase, time.perf_counter()))

    def note(self, **fields: object) -> None:
        self.notes.update(fields)


@contextmanager
def card_phases(**fields: object) -> Iterator[CardPhases]:
    """Time a card request; log `card_timing` once, however the request ends."""
    card = CardPhases()
    card.note(**fields)
    error: BaseException | None = None
    try:
        yield card
    except BaseException as exc:
        error = exc
        raise
    finally:
        card.lap("done")
        notes = " ".join(f"{key}={value}" for key, value in card.notes.items())
        line = f"card_timing {notes} {phases(card.marks)}"
        if error is not None:
            line += f" error={type(error).__name__}"
        logger.info(line)
