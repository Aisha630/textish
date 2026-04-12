## the textual webdriver using this packet exchnage protocol 
# [ 1 byte: type ] [ 4 bytes: big-endian size ] [ N bytes: payload ]

import asyncio


def encode_packet(type_byte: bytes, data: bytes) -> bytes:
    """Encodes a packet with the given type and data."""
    if not isinstance(type_byte, bytes):
        raise TypeError("Type byte must be of type bytes.")
    if not isinstance(data, bytes):
        raise TypeError("Data must be of type bytes.")

    if len(type_byte) != 1:
        raise ValueError("Type byte must be exactly 1 byte long.")

    data_size = len(data)
    data_size_bytes = data_size.to_bytes(
        4, byteorder="big"
    )  # convert data size to 4 bytes big-endian as that is the protocol we are using
    return type_byte + data_size_bytes + data


async def read_packet(reader: asyncio.StreamReader) -> tuple[bytes, bytes] | None:
    """Reads a packet from the given StreamReader.

    Returns a tuple of (type_byte, payload) both in byte format,
    or None if the connection is closed.
    """

    try:
        type_byte = await reader.readexactly(1)
        data_size_bytes = await reader.readexactly(4)
        data_size = int.from_bytes(data_size_bytes, byteorder="big")
        payload = await reader.readexactly(data_size)
        return type_byte, payload
    except asyncio.IncompleteReadError:
        return None  # connection closed before we could read a full packet
