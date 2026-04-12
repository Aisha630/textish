"""
Wire protocol shared between textish and Textual's WebDriver.

Packet layout:
    [ 1 byte: type ] [ 4 bytes: big-endian payload length ] [ N bytes: payload ]

Known type bytes (defined by Textual's WebDriver):
    b"D"  — display data to write directly to the terminal
    b"M"  — JSON meta message (e.g. resize, quit, exit)
"""

import asyncio
import struct


def encode_packet(type_byte: bytes, data: bytes) -> bytes:
    """Encode a single packet in the WebDriver wire format.

    Args:
        type_byte: Exactly one byte identifying the packet type (e.g. ``b"D"``).
        data:      Raw payload bytes.

    Returns:
        The complete framed packet: type byte + 4-byte big-endian length + payload.

    Raises:
        TypeError:  If either argument is not ``bytes``.
        ValueError: If ``type_byte`` is not exactly one byte.
    """
    if not isinstance(type_byte, bytes):
        raise TypeError(f"type_byte must be bytes, got {type(type_byte).__name__}")
    if not isinstance(data, bytes):
        raise TypeError(f"data must be bytes, got {type(data).__name__}")
    if len(type_byte) != 1:
        raise ValueError(f"type_byte must be exactly 1 byte, got {len(type_byte)}")

    return type_byte + struct.pack(">I", len(data)) + data


async def read_packet(reader: asyncio.StreamReader) -> tuple[bytes, bytes] | None:
    """Read one packet from *reader* and return ``(type_byte, payload)``.

    Blocks until a full packet is available. Returns ``None`` when the stream
    is closed or truncated mid-packet (i.e. the subprocess exited).
    """
    try:
        type_byte = await reader.readexactly(1)
        length = struct.unpack(">I", await reader.readexactly(4))[0]
        payload = await reader.readexactly(length)
        return type_byte, payload
    except asyncio.IncompleteReadError:
        return None
