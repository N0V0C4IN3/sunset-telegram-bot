import logging

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.bot.tracing import TracedAiohttpSession, card_phases, method_name, summarize

TOKEN = "123456:SECRET-token-value"


def test_method_name_drops_the_token():
    assert method_name(f"/bot{TOKEN}/editMessageMedia") == "editMessageMedia"


@pytest.mark.parametrize(
    "path",
    [f"/bot{TOKEN}/", f"/bot{TOKEN}", f"/file/bot{TOKEN}/photos/file_1.jpg", "/"],
)
def test_a_path_that_is_not_a_method_call_logs_as_unknown(path):
    assert method_name(path) == "unknown"


def test_summary_lists_phases_in_time_order_from_the_start():
    marks = [("start", 10.0), ("sent", 10.002), ("reused", 10.001), ("replied", 12.5)]

    assert summarize("sendPhoto", marks) == "telegram_call method=sendPhoto reused=1 sent=2 replied=2500"


def test_summary_names_the_error():
    marks = [("start", 0.0), ("failed", 5.0)]

    line = summarize("sendPhoto", marks, TimeoutError())

    assert line.endswith("failed=5000 error=TimeoutError")


async def _traced_requests(caplog, *methods: str) -> list[str]:
    async def ok(request: web.Request) -> web.Response:
        await request.read()
        return web.json_response({"ok": True, "result": True})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", ok)
    server = TestServer(app)
    await server.start_server()
    session = TracedAiohttpSession()
    try:
        client = await session.create_session()
        with caplog.at_level(logging.INFO, logger="app.bot.tracing"):
            for method in methods:
                url = server.make_url(f"/bot{TOKEN}/{method}")
                async with client.post(url, data=b"x" * 200_000) as response:
                    await response.read()
    finally:
        await session.close()
        await server.close()
    return [record.getMessage() for record in caplog.records]


@pytest.mark.asyncio
async def test_a_real_request_is_logged_by_phase_without_the_token(caplog):
    lines = await _traced_requests(caplog, "editMessageMedia")

    assert len(lines) == 1
    assert lines[0].startswith("telegram_call method=editMessageMedia ")
    for phase in ("connected=", "sent=", "uploaded=", "replied="):
        assert phase in lines[0]
    assert "SECRET" not in lines[0]


@pytest.mark.asyncio
async def test_the_long_poll_is_not_logged(caplog):
    assert await _traced_requests(caplog, "getUpdates") == []


class _RecordedSession:
    """Stands in for aiohttp's ClientSession and keeps what it was built with."""

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs, self.closed = args, kwargs, False

    async def close(self):
        self.closed = True


def _construction(session: _RecordedSession) -> dict:
    kwargs = dict(session.kwargs)
    kwargs.pop("trace_configs", None)
    kwargs["connector"] = type(kwargs["connector"])
    return {"args": session.args, **kwargs}


@pytest.mark.asyncio
async def test_the_traced_session_is_built_like_aiograms_own(monkeypatch):
    import aiogram.client.session.aiohttp as aiogram_session

    import app.bot.tracing as tracing

    monkeypatch.setattr(aiogram_session, "ClientSession", _RecordedSession)
    monkeypatch.setattr(tracing, "ClientSession", _RecordedSession)
    traced, plain = TracedAiohttpSession(), AiohttpSession()

    ours, theirs = await traced.create_session(), await plain.create_session()
    try:
        assert _construction(ours) == _construction(theirs)
        assert len(ours.kwargs["trace_configs"]) == 1
    finally:
        for session in (ours, theirs):
            await session.kwargs["connector"].close()


def test_a_card_logs_its_notes_and_stages(caplog):
    with caplog.at_level(logging.INFO, logger="app.bot.tracing"):
        with card_phases(event="sunrise", edit=True) as card:
            card.note(verdict="Fetch")
            card.lap("forecast")

    (line,) = [record.getMessage() for record in caplog.records]
    assert line.startswith("card_timing event=sunrise edit=True verdict=Fetch forecast=")
    assert " done=" in line and "error=" not in line


def test_a_failed_card_is_still_logged_with_its_error(caplog):
    with caplog.at_level(logging.INFO, logger="app.bot.tracing"):
        with pytest.raises(TimeoutError):
            with card_phases(event="sunset", edit=True) as card:
                card.lap("render")
                raise TimeoutError

    (line,) = [record.getMessage() for record in caplog.records]
    assert line.startswith("card_timing event=sunset edit=True render=")
    assert line.endswith("error=TimeoutError")
