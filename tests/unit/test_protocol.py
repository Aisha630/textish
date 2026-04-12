import asyncio

import pytest

from textish import protocol


def test_encode_packet():
    type_byte = b"D"
    data = b"Hello, World!"
    packet = protocol.encode_packet(type_byte, data)
    assert packet == b"D\x00\x00\x00\rHello, World!"

    with pytest.raises(ValueError):
        protocol.encode_packet(b"AB", data)

    with pytest.raises(TypeError):
        protocol.encode_packet("D", data)

    with pytest.raises(TypeError):
        protocol.encode_packet(type_byte, "Hello, World!")


@pytest.mark.asyncio
async def test_read_packet():
    reader = asyncio.StreamReader()
    packet = protocol.encode_packet(b"D", b"Hello")
    reader.feed_data(packet)
    reader.feed_eof()

    result = await protocol.read_packet(reader)
    assert result == (b"D", b"Hello")


@pytest.mark.asyncio
async def test_read_packet_empty_payload():
    reader = asyncio.StreamReader()
    packet = protocol.encode_packet(b"X", b"")
    reader.feed_data(packet)
    reader.feed_eof()

    result = await protocol.read_packet(reader)
    assert result == (b"X", b"")


@pytest.mark.asyncio
async def test_read_packet_eof_returns_none():
    reader = asyncio.StreamReader()
    reader.feed_eof()

    result = await protocol.read_packet(reader)
    assert result is None


@pytest.mark.asyncio
async def test_read_packet_truncated_returns_none():
    reader = asyncio.StreamReader()
    reader.feed_data(b"D\x00\x00\x00\x0a")
    reader.feed_eof()

    result = await protocol.read_packet(reader)
    assert result is None


@pytest.mark.asyncio
async def test_read_multiple_packets():
    reader = asyncio.StreamReader()
    reader.feed_data(protocol.encode_packet(b"A", b"first"))
    reader.feed_data(protocol.encode_packet(b"B", b"second"))
    reader.feed_eof()

    assert await protocol.read_packet(reader) == (b"A", b"first")
    assert await protocol.read_packet(reader) == (b"B", b"second")
    assert await protocol.read_packet(reader) is None


def test_encode_packet_roundtrip():
    for type_byte, data in [(b"D", b"hello"), (b"\x00", b"\xff" * 255), (b"Z", b"")]:
        packet = protocol.encode_packet(type_byte, data)
        assert packet[0:1] == type_byte
        size = int.from_bytes(packet[1:5], "big")
        assert size == len(data)
        assert packet[5:] == data


def test_encode_packet_large_payload():
    data = b"x" * 100_000
    packet = protocol.encode_packet(b"L", data)
    assert len(packet) == 5 + 100_000
    assert int.from_bytes(packet[1:5], "big") == 100_000
