"""Coalesced frame capture and compact CUAF binary WebSocket framing."""

from __future__ import annotations

import asyncio
import enum
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CUAF_MAGIC = b"CUAF"
DESKTOP_STREAM_ID = "desktop"
_HEADER = struct.Struct(">4sBBQIIQ")
_VERSION = 1


class FrameCodec(enum.IntEnum):
    JPEG = 1
    WEBP = 2
    PNG = 3


@dataclass(frozen=True)
class BinaryFrame:
    sequence: int
    width: int
    height: int
    timestamp_ms: int
    codec: FrameCodec
    payload: bytes


def pack_cuaf_frame(
    sequence: int, width: int, height: int, timestamp_ms: int, codec: FrameCodec, payload: bytes
) -> bytes:
    if sequence < 0 or width <= 0 or height <= 0 or timestamp_ms < 0:
        raise ValueError("Invalid frame metadata")
    return (
        _HEADER.pack(CUAF_MAGIC, _VERSION, int(codec), sequence, width, height, timestamp_ms)
        + payload
    )


def unpack_cuaf_frame(data: bytes) -> BinaryFrame:
    if len(data) < _HEADER.size:
        raise ValueError("Truncated CUAF frame")
    magic, version, codec, sequence, width, height, timestamp_ms = _HEADER.unpack_from(data)
    if magic != CUAF_MAGIC or version != _VERSION:
        raise ValueError("Unsupported CUAF frame")
    return BinaryFrame(
        sequence, width, height, timestamp_ms, FrameCodec(codec), data[_HEADER.size :]
    )


class FrameBroker:
    """Allows concurrent consumers to share the same in-flight capture."""

    def __init__(
        self, capture: Callable[[], Awaitable[tuple[bytes, int, int, FrameCodec]]]
    ) -> None:
        self._capture = capture
        self._inflight: asyncio.Future[BinaryFrame] | None = None
        self._lock = asyncio.Lock()
        self._sequence = 0

    async def capture(self) -> BinaryFrame:
        async with self._lock:
            if self._inflight is None or self._inflight.done():
                self._sequence += 1
                sequence = self._sequence

                async def _perform() -> BinaryFrame:
                    payload, width, height, codec = await self._capture()
                    return BinaryFrame(
                        sequence, width, height, int(time.time() * 1000), codec, payload
                    )

                self._inflight = asyncio.ensure_future(_perform())
            task = self._inflight
        return await asyncio.shield(task)
