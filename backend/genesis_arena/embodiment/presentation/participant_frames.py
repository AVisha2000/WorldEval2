"""In-memory, pixel-only participant camera frames for the local dashboard."""

from __future__ import annotations

import asyncio
import hashlib
import struct
import zlib
from collections import deque
from dataclasses import dataclass
from io import BytesIO
from typing import Generic, TypeVar

from PIL import Image, UnidentifiedImageError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
_RGBA_SCANLINE_BYTES = FRAME_WIDTH * 4 + 1
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_SnapshotT = TypeVar("_SnapshotT")


class _NewestAsyncQueue(Generic[_SnapshotT]):
    """A loop-lazy, depth-one queue for browser preview fan-out.

    Python 3.9 binds ``asyncio.Queue`` to the current event loop during
    construction. Preview subscriptions are also inspected synchronously by
    diagnostics and tests, where no loop may exist. This tiny queue preserves
    the used asyncio queue interface while creating wait primitives only from
    inside ``get()``, where a running loop is guaranteed.
    """

    maxsize = 1

    def __init__(self) -> None:
        self._items: deque[_SnapshotT] = deque()
        self._waiters: set[asyncio.Event] = set()

    def empty(self) -> bool:
        return not self._items

    def full(self) -> bool:
        return bool(self._items)

    def qsize(self) -> int:
        return len(self._items)

    def get_nowait(self) -> _SnapshotT:
        if not self._items:
            raise asyncio.QueueEmpty
        return self._items.popleft()

    def put_nowait(self, value: _SnapshotT) -> None:
        if self._items:
            raise asyncio.QueueFull
        self._items.append(value)
        for waiter in tuple(self._waiters):
            waiter.set()

    async def get(self) -> _SnapshotT:
        while not self._items:
            waiter = asyncio.Event()
            self._waiters.add(waiter)
            try:
                if not self._items:
                    await waiter.wait()
            finally:
                self._waiters.discard(waiter)
        return self.get_nowait()


@dataclass(frozen=True)
class ParticipantFrameSnapshot:
    observation_seq: int
    png: bytes
    sha256: str


class ParticipantFrameStore:
    """Retain only the newest sanitized active-participant frame in memory."""

    def __init__(self) -> None:
        self._snapshot: ParticipantFrameSnapshot | None = None

    def publish(self, participant_id: str, observation_seq: int, png: bytes) -> None:
        self.publish_sanitized(participant_id, observation_seq, sanitize_participant_png(png))

    def publish_sanitized(self, participant_id: str, observation_seq: int, png: bytes) -> None:
        """Publish pixels already sanitized off the authority event loop."""
        if participant_id != "participant_0":
            raise ValueError("only the active solo participant frame may be published")
        if (
            isinstance(observation_seq, bool)
            or not isinstance(observation_seq, int)
            or observation_seq < 0
        ):
            raise ValueError("observation sequence is invalid")
        if self._snapshot is not None and observation_seq < self._snapshot.observation_seq:
            raise ValueError("participant frame sequence moved backwards")
        if not isinstance(png, bytes):
            raise TypeError("participant frame pixels must be immutable bytes")
        self._snapshot = ParticipantFrameSnapshot(
            observation_seq=observation_seq,
            png=png,
            sha256=hashlib.sha256(png).hexdigest(),
        )

    def snapshot(self) -> ParticipantFrameSnapshot | None:
        return self._snapshot

    def close(self) -> None:
        self._snapshot = None


class ParticipantPreviewHub:
    """Newest-frame-only local fan-out; it carries participant pixels only."""

    def __init__(self) -> None:
        self._subscribers: dict[int, _NewestAsyncQueue[ParticipantFrameSnapshot]] = {}
        self._next_id = 0

    def publish(self, snapshot: ParticipantFrameSnapshot) -> None:
        for queue in tuple(self._subscribers.values()):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(snapshot)

    def subscribe(self) -> tuple[int, asyncio.Queue[ParticipantFrameSnapshot]]:
        token = self._next_id
        self._next_id += 1
        queue = _NewestAsyncQueue[ParticipantFrameSnapshot]()
        self._subscribers[token] = queue
        return token, queue

    def unsubscribe(self, token: int) -> None:
        self._subscribers.pop(token, None)

    def close(self) -> None:
        self._subscribers.clear()


@dataclass(frozen=True)
class ParticipantLivePreviewSnapshot:
    """One presentation-only JPEG; deliberately carries no semantic side channel."""

    sequence: int
    jpeg: bytes


class ParticipantLivePreviewStore:
    """Retain only the newest metadata-free participant JPEG in process memory."""

    def __init__(self) -> None:
        self._snapshot: ParticipantLivePreviewSnapshot | None = None

    def publish(self, participant_id: str, sequence: int, jpeg: bytes) -> None:
        if participant_id != "participant_0":
            raise ValueError("only the active solo participant preview may be published")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("participant preview sequence is invalid")
        if self._snapshot is not None and sequence <= self._snapshot.sequence:
            raise ValueError("participant preview sequence is not newer")
        self._snapshot = ParticipantLivePreviewSnapshot(
            sequence=sequence,
            jpeg=sanitize_participant_jpeg(jpeg),
        )

    def publish_sanitized(self, participant_id: str, sequence: int, jpeg: bytes) -> None:
        """Publish a JPEG already sanitized off the authority event loop."""

        if participant_id != "participant_0":
            raise ValueError("only the active solo participant preview may be published")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("participant preview sequence is invalid")
        if self._snapshot is not None and sequence <= self._snapshot.sequence:
            raise ValueError("participant preview sequence is not newer")
        if not isinstance(jpeg, bytes):
            raise TypeError("participant preview pixels must be immutable bytes")
        self._snapshot = ParticipantLivePreviewSnapshot(sequence=sequence, jpeg=jpeg)

    def snapshot(self) -> ParticipantLivePreviewSnapshot | None:
        return self._snapshot

    def close(self) -> None:
        self._snapshot = None


class ParticipantLivePreviewHub:
    """Newest-only JPEG fan-out with a hard queue depth of one per browser."""

    def __init__(self) -> None:
        self._subscribers: dict[int, _NewestAsyncQueue[ParticipantLivePreviewSnapshot]] = {}
        self._next_id = 0

    def publish(self, snapshot: ParticipantLivePreviewSnapshot) -> None:
        for queue in tuple(self._subscribers.values()):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(snapshot)

    def subscribe(self) -> tuple[int, asyncio.Queue[ParticipantLivePreviewSnapshot]]:
        token = self._next_id
        self._next_id += 1
        queue = _NewestAsyncQueue[ParticipantLivePreviewSnapshot]()
        self._subscribers[token] = queue
        return token, queue

    def unsubscribe(self, token: int) -> None:
        self._subscribers.pop(token, None)

    def close(self) -> None:
        self._subscribers.clear()


def sanitize_participant_jpeg(value: bytes) -> bytes:
    """Decode participant pixels and deterministically create a fresh metadata-free JPEG.

    No source coding table or entropy byte is copied to the browser result. The structural pass
    first rejects trailing data and unsupported coding modes, Pillow then fully decodes one fixed-
    size frame, and a new RGB image is encoded with fixed baseline settings. Rebuilding from pixels
    is required because JPEG DQT/DHT/entropy segments can themselves carry arbitrary byte strings.
    """

    if not isinstance(value, bytes) or not 128 <= len(value) <= _MAX_SOURCE_BYTES:
        raise ValueError("participant preview JPEG size is invalid")
    structurally_sanitized = _strip_baseline_jpeg_metadata(value)
    try:
        with Image.open(BytesIO(structurally_sanitized)) as source:
            if (
                source.format != "JPEG"
                or getattr(source, "n_frames", 1) != 1
                or source.size != (FRAME_WIDTH, FRAME_HEIGHT)
            ):
                raise ValueError("participant preview JPEG image is unsupported")
            source.load()
            rgb = source.convert("RGB")
            pixels = rgb.tobytes()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("participant preview JPEG pixels are invalid") from error

    rebuilt_image = Image.frombytes("RGB", (FRAME_WIDTH, FRAME_HEIGHT), pixels)
    encoded = BytesIO()
    rebuilt_image.save(
        encoded,
        format="JPEG",
        quality=82,
        subsampling="4:2:0",
        optimize=False,
        progressive=False,
    )
    rebuilt = _strip_baseline_jpeg_metadata(encoded.getvalue())
    try:
        with Image.open(BytesIO(rebuilt)) as verified:
            if (
                verified.format != "JPEG"
                or getattr(verified, "n_frames", 1) != 1
                or verified.size != (FRAME_WIDTH, FRAME_HEIGHT)
                or verified.mode != "RGB"
            ):
                raise ValueError("sanitized participant preview JPEG is invalid")
            verified.load()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("sanitized participant preview JPEG is invalid") from error
    return rebuilt


def _strip_baseline_jpeg_metadata(value: bytes) -> bytes:
    """Validate baseline structure and strip APP/COM without claiming pixel sanitization."""

    if value[:2] != b"\xff\xd8":
        raise ValueError("participant preview is not a JPEG")
    output = bytearray(value[:2])
    offset = 2
    found_frame = False
    found_scan = False
    while offset < len(value):
        if value[offset] != 0xFF:
            raise ValueError("participant preview JPEG marker is invalid")
        while offset < len(value) and value[offset] == 0xFF:
            offset += 1
        if offset >= len(value):
            raise ValueError("participant preview JPEG is truncated")
        marker = value[offset]
        offset += 1
        if marker == 0xD9:
            if offset != len(value) or not found_frame or not found_scan:
                raise ValueError("participant preview JPEG ending is invalid")
            output.extend(b"\xff\xd9")
            return bytes(output)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            raise ValueError("participant preview JPEG marker order is invalid")
        if offset + 2 > len(value):
            raise ValueError("participant preview JPEG segment is truncated")
        length = int.from_bytes(value[offset : offset + 2], "big")
        if length < 2 or offset + length > len(value):
            raise ValueError("participant preview JPEG segment length is invalid")
        segment = value[offset + 2 : offset + length]
        offset += length
        if marker == 0xC0:
            if (
                found_frame
                or len(segment) < 6
                or segment[0] != 8
                or int.from_bytes(segment[1:3], "big") != FRAME_HEIGHT
                or int.from_bytes(segment[3:5], "big") != FRAME_WIDTH
            ):
                raise ValueError("participant preview JPEG dimensions are invalid")
            found_frame = True
        if marker == 0xDA:
            if not found_frame:
                raise ValueError("participant preview JPEG scan precedes its frame")
            output.extend(b"\xff" + bytes((marker,)) + length.to_bytes(2, "big") + segment)
            found_scan = True
            scan_end = _jpeg_scan_end(value, offset)
            output.extend(value[offset:scan_end])
            offset = scan_end
            continue
        if marker == 0xFE or 0xE0 <= marker <= 0xEF:
            continue
        # Strictly retain only the segments emitted by Godot's baseline JPEG encoder. In
        # particular, reserved JPG extension markers (F0-FD), arithmetic coding, progressive
        # frames, and application-specific coding segments must never become a covert browser
        # payload just because they use a syntactically length-prefixed JPEG marker.
        if marker not in (0xC0, 0xC4, 0xDB, 0xDD):
            raise ValueError("participant preview JPEG coding marker is unsupported")
        output.extend(b"\xff" + bytes((marker,)) + length.to_bytes(2, "big") + segment)
    raise ValueError("participant preview JPEG is incomplete")


def _jpeg_scan_end(value: bytes, offset: int) -> int:
    while offset + 1 < len(value):
        marker = value.find(b"\xff", offset)
        if marker < 0 or marker + 1 >= len(value):
            break
        following = value[marker + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7:
            offset = marker + 2
            continue
        return marker
    raise ValueError("participant preview JPEG scan is truncated")


def sanitize_participant_png(value: bytes) -> bytes:
    """Rebuild a Godot RGBA PNG from decoded scanlines, discarding all metadata."""

    if not isinstance(value, bytes) or not 32 <= len(value) <= _MAX_SOURCE_BYTES:
        raise ValueError("participant frame PNG size is invalid")
    if not value.startswith(PNG_SIGNATURE):
        raise ValueError("participant frame is not a PNG")

    offset = len(PNG_SIGNATURE)
    ihdr: bytes | None = None
    compressed_parts: list[bytes] = []
    ended = False
    while offset < len(value):
        if offset + 12 > len(value):
            raise ValueError("participant frame PNG is truncated")
        length = struct.unpack(">I", value[offset : offset + 4])[0]
        chunk_type = value[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(value):
            raise ValueError("participant frame PNG chunk is truncated")
        data = value[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", value[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("participant frame PNG checksum is invalid")
        if chunk_type == b"IHDR":
            if ihdr is not None or offset != len(PNG_SIGNATURE) or length != 13:
                raise ValueError("participant frame PNG header is invalid")
            ihdr = data
        elif chunk_type == b"IDAT":
            if ihdr is None or ended:
                raise ValueError("participant frame PNG data order is invalid")
            compressed_parts.append(data)
        elif chunk_type == b"IEND":
            if length != 0 or ihdr is None or not compressed_parts:
                raise ValueError("participant frame PNG ending is invalid")
            ended = True
            offset = chunk_end
            break
        elif chunk_type[:1].isupper():
            raise ValueError("participant frame PNG contains an unsupported critical chunk")
        offset = chunk_end

    if not ended or offset != len(value) or ihdr is None:
        raise ValueError("participant frame PNG has trailing or incomplete data")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (
        (width, height) != (FRAME_WIDTH, FRAME_HEIGHT)
        or bit_depth != 8
        or color_type != 6
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise ValueError("participant frame PNG format is unsupported")

    decompressor = zlib.decompressobj()
    # Verify the compressed pixel stream, but retain its validated IDAT data instead of
    # recompressing 3.5 MiB of scanlines for every presentation frame.  Rebuilding the PNG from
    # only IHDR/IDAT/IEND still strips every source metadata chunk, while avoiding a synchronous
    # zlib deflate on the real-time preview path.
    compressed = b"".join(compressed_parts)
    scanlines = decompressor.decompress(compressed, _RGBA_SCANLINE_BYTES * FRAME_HEIGHT + 1)
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or len(scanlines) != _RGBA_SCANLINE_BYTES * FRAME_HEIGHT
    ):
        raise ValueError("participant frame PNG pixel stream is invalid")

    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", compressed),
            _chunk(b"IEND", b""),
        )
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


__all__ = [
    "FRAME_HEIGHT",
    "FRAME_WIDTH",
    "PNG_SIGNATURE",
    "ParticipantFrameSnapshot",
    "ParticipantFrameStore",
    "ParticipantLivePreviewHub",
    "ParticipantLivePreviewSnapshot",
    "ParticipantLivePreviewStore",
    "ParticipantPreviewHub",
    "sanitize_participant_jpeg",
    "sanitize_participant_png",
]
