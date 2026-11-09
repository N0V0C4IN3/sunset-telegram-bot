"""Phase timing for every Telegram API call, to find where slow cards stall.

Diagnostics for the card-latency investigation: a card that takes 5-30 s on the
Pi spends that time after the forecast is ready, but the stall would not
reproduce outside the live bot. Each call made through `TracedAiohttpSession`
logs one line naming the phases it went through: DNS, a reused or new
connection, request sent, and the wait for Telegram's reply.

Logs stay data-free: only the API method name is taken from the URL, which
also carries the bot token, and nothing else from the request is recorded.
"""

import logging
import time
from types import SimpleNamespace

from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientSession, TraceConfig
from aiohttp.hdrs import USER_AGENT
from aiohttp.http import SERVER_SOFTWARE
from aiogram import __version__ as aiogram_version

logger = logging.getLogger(__name__)

# The long poll waits up to 30 s by design and runs continuously; tracing it
# would only bury the calls that matter.
UNTRACED_METHODS = frozenset({"getUpdates"})


def method_name(path: str) -> str:
    """The API method from a /bot<token>/<method> path, without the token."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def summarize(method: str, marks: list[tuple[str, float]], error: BaseException | None = None) -> str:
    """One log line from the (phase, timestamp) marks of a single request."""
    start = marks[0][1]
    phases = " ".join(f"{name}={round(1000 * (at - start))}" for name, at in marks[1:])
    line = f"telegram_call method={method} {phases}"
    if error is not None:
        line += f" error={type(error).__name__}"
    return line


def _trace_config() -> TraceConfig:
    config = TraceConfig()

    def mark(phase: str):
        async def record(session, context: SimpleNamespace, params) -> None:
            if not hasattr(context, "marks"):
                context.marks = []
            context.marks.append((phase, time.perf_counter()))

        return record

    async def finish(session, context: SimpleNamespace, params) -> None:
        context.marks.append(("total", time.perf_counter()))
        method = method_name(params.url.path)
        if method in UNTRACED_METHODS:
            return
        logger.info(summarize(method, context.marks, getattr(params, "exception", None)))

    config.on_request_start.append(mark("start"))
    config.on_dns_cache_hit.append(mark("dns_cached"))
    config.on_dns_resolvehost_end.append(mark("dns"))
    config.on_connection_queued_end.append(mark("queued"))
    config.on_connection_reuseconn.append(mark("reused"))
    config.on_connection_create_end.append(mark("connected"))
    config.on_request_headers_sent.append(mark("sent"))
    config.on_request_end.append(finish)
    config.on_request_exception.append(finish)
    return config


class TracedAiohttpSession(AiohttpSession):
    """aiogram's session with phase tracing attached.

    Mirrors `AiohttpSession.create_session`, whose ClientSession offers no hook
    for trace configs; keep the two in step when upgrading aiogram.
    """

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
