import asyncio
import struct
import time

import pytest

from app.bot import tcp_info
from app.bot.tcp_info import SendWatch, TcpInfo, parse


def _raw(unacked=0, notsent=0, total_retrans=0, rtt_us=0, size=232) -> bytes:
    raw = bytearray(232)
    struct.pack_into("=I", raw, 24, unacked)
    struct.pack_into("=I", raw, 68, rtt_us)
    struct.pack_into("=I", raw, 100, total_retrans)
    struct.pack_into("=I", raw, 144, notsent)
    return bytes(raw[:size])


def test_parse_reads_the_kernel_fields():
    info = parse(_raw(unacked=3, notsent=10, total_retrans=7, rtt_us=41_500))

    assert info == TcpInfo(unacked=3, notsent_bytes=10, total_retrans=7, rtt_us=41_500)
    assert not info.idle


def test_a_struct_from_a_kernel_too_old_for_notsent_bytes_is_ignored():
    assert parse(_raw(size=104)) is None


class _Transport:
    def __init__(self, buffered: int = 0):
        self.buffered = buffered

    def get_extra_info(self, name):
        return object() if name == "socket" else None

    def get_write_buffer_size(self):
        return self.buffered


class _Kernel:
    """Scripted TCP_INFO: busy until `idle_after` reads, then acknowledged."""

    def __init__(self, idle_after: int, retrans_during: int = 0):
        self.reads, self.idle_after, self.retrans_during = 0, idle_after, retrans_during

    def __call__(self, sock):
        self.reads += 1
        busy = self.reads <= self.idle_after
        return TcpInfo(
            unacked=2 if busy else 0,
            notsent_bytes=0,
            total_retrans=5 + (0 if self.reads == 1 else self.retrans_during),
            rtt_us=40_000,
        )


@pytest.mark.asyncio
async def test_acked_is_when_the_kernel_went_idle_after_the_body_was_written():
    written = time.perf_counter()
    watch = SendWatch(_Transport(), interval=0.001, read_info=_Kernel(idle_after=5, retrans_during=2))
    await asyncio.sleep(0.05)

    ended = time.perf_counter()
    acked, counters = watch.finish(written, ended)

    assert acked is not None and written <= acked <= ended
    assert counters == {"retrans": 2, "rtt": 40}


@pytest.mark.asyncio
async def test_bytes_asyncio_still_holds_are_not_acknowledged():
    written = time.perf_counter()
    watch = SendWatch(_Transport(buffered=1), interval=0.001, read_info=_Kernel(idle_after=0))
    await asyncio.sleep(0.02)

    acked, _ = watch.finish(written, time.perf_counter())

    assert acked is None


@pytest.mark.asyncio
async def test_idle_before_the_body_was_written_does_not_count():
    watch = SendWatch(_Transport(), interval=0.001, read_info=_Kernel(idle_after=0))
    await asyncio.sleep(0.02)
    written = time.perf_counter()

    acked, _ = watch.finish(written, written + 1)

    assert acked is not None and acked >= written


@pytest.mark.asyncio
async def test_without_tcp_info_a_watch_reports_nothing():
    watch = SendWatch(_Transport(), read_info=lambda sock: None)

    assert watch.finish(0.0, 1.0) == (None, {})


def test_read_without_a_socket_is_none():
    assert tcp_info.read(None) is None
