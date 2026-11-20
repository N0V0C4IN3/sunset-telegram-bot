"""When the kernel saw a request's bytes acknowledged, for `telegram_call`.

aiohttp's `uploaded` mark is when the last chunk was handed to the transport, not
when Telegram had it: a card fits in the socket buffers, so a slow network and a
slow Telegram look the same from aiohttp. `SendWatch` polls the socket's
`TCP_INFO` while a call is in flight and reports when nothing written to it was
left unacknowledged, plus how many segments were retransmitted. An early `acked`
before a late `replied` is Telegram holding the reply; a late `acked` with
retransmissions is the link.

Linux only (the Pi). Elsewhere `read` returns None and a watch reports nothing.
"""

import asyncio
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Offsets into the kernel's `struct tcp_info` (include/uapi/linux/tcp.h).
_UNACKED = 24
_RTT = 68
_TOTAL_RETRANS = 100
_NOTSENT_BYTES = 144
_SIZE = _NOTSENT_BYTES + 4

POLL_INTERVAL = 0.02
# Longer than any API call is allowed to take, so a call that never reports
# back cannot leave a poller running.
MAX_WATCH = 120.0


@dataclass(frozen=True)
class TcpInfo:
    unacked: int  # segments sent and not yet acknowledged
    notsent_bytes: int  # bytes in the kernel's send buffer not yet sent
    total_retrans: int  # segments retransmitted over the connection's life
    rtt_us: int  # smoothed round-trip time

    @property
    def idle(self) -> bool:
        """Everything the kernel was given has been sent and acknowledged."""
        return self.unacked == 0 and self.notsent_bytes == 0


def parse(raw: bytes) -> TcpInfo | None:
    if len(raw) < _SIZE:
        return None

    def u32(offset: int) -> int:
        return struct.unpack_from("=I", raw, offset)[0]

    return TcpInfo(
        unacked=u32(_UNACKED),
        notsent_bytes=u32(_NOTSENT_BYTES),
        total_retrans=u32(_TOTAL_RETRANS),
        rtt_us=u32(_RTT),
    )


def read(sock: Any) -> TcpInfo | None:
    option = getattr(socket, "TCP_INFO", None)
    if sock is None or option is None:
        return None
    try:
        return parse(sock.getsockopt(socket.IPPROTO_TCP, option, 256))
    except (OSError, ValueError):
        return None


def buffered(transport: Any) -> int:
    """Bytes asyncio still holds above the kernel, TLS layer included."""
    total = 0
    inner = getattr(getattr(transport, "_ssl_protocol", None), "_transport", None)
    for layer in (transport, inner):
        if layer is None:
            continue
        try:
            total += layer.get_write_buffer_size()
        except (AttributeError, RuntimeError):
            pass
    return total


class SendWatch:
    """Samples one connection from headers sent until the call ends."""

    def __init__(
        self,
        transport: Any,
        interval: float = POLL_INTERVAL,
        read_info: Callable[[Any], TcpInfo | None] = read,
    ) -> None:
        self._transport = transport
        self._socket = transport.get_extra_info("socket")
        self._read = read_info
        self._first = read_info(self._socket)
        self._idle: list[float] = []
        self._task = asyncio.create_task(self._poll(interval)) if self._first else None

    async def _poll(self, interval: float) -> None:
        for _ in range(int(MAX_WATCH / interval)):
            self._sample()
            await asyncio.sleep(interval)

    def _sample(self) -> TcpInfo | None:
        info = self._read(self._socket)
        if info is not None and info.idle and buffered(self._transport) == 0:
            self._idle.append(time.perf_counter())
        return info

    def finish(self, written_at: float, ended_at: float) -> tuple[float | None, dict[str, int]]:
        """(`acked` time, counters) for a call whose body was last written at `written_at`.

        `acked` is the first idle sample after the body was written, so it is
        late by at most one poll interval, and never later than the call's end.
        """
        if self._task is None or self._first is None:
            return None, {}
        self._task.cancel()
        last = self._sample()
        acked = next((at for at in self._idle if at >= written_at), None)
        if acked is not None:
            acked = min(acked, ended_at)
        if last is None:
            return acked, {}
        return acked, {
            "retrans": last.total_retrans - self._first.total_retrans,
            "rtt": round(last.rtt_us / 1000),
        }
