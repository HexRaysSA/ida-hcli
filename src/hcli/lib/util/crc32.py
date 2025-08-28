"""CRC32 calculation utilities."""


def crc32(data: bytes | str, seed: int = 0xFFFFFFFF) -> str:
    """
    Calculate CRC32 checksum for the given data.

    Args:
        data: The data to calculate CRC32 for (bytes or string)
        seed: The initial seed value

    Returns:
        The CRC32 checksum as a hexadecimal string
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    crc = seed ^ 0xFFFFFFFF
    poly = 0xEDB88320

    for byte in data:
        temp = (crc ^ byte) & 0xFF
        for _ in range(8):
            if temp & 1:
                temp = (temp >> 1) ^ poly
            else:
                temp = temp >> 1
        crc = (crc >> 8) ^ temp

    return number_to_hex(crc ^ 0xFFFFFFFF)


def number_to_hex(n: int) -> str:
    """Convert a number to a hexadecimal string."""
    return f"{n & 0xFFFFFFFF:08x}"


def hex_to_bytes(hex_str: str) -> bytes:
    """Convert a hexadecimal string to bytes."""
    if len(hex_str) == 0 or len(hex_str) % 2 != 0:
        raise ValueError(f'The string "{hex_str}" is not valid hex.')
    return bytes.fromhex(hex_str)


def bytes_to_hex(data: bytes) -> str:
    """Convert bytes to a hexadecimal string."""
    return data.hex()


class Crc32Stream:
    """CRC32 stream processing class for incremental calculation."""

    def __init__(self):
        self._poly = 0xEDB88320
        self._crc = 0
        self._bytes = [0] * 256
        self.reset()

    def reset(self) -> None:
        """Reset the state of the CRC32 stream."""
        self._crc = 0 ^ 0xFFFFFFFF

        for n in range(256):
            c = n
            for _ in range(8):
                if c & 1:
                    c = self._poly ^ (c >> 1)
                else:
                    c = c >> 1
            self._bytes[n] = c & 0xFFFFFFFF

    def append(self, data: bytes | str) -> str:
        """
        Append new data to the CRC32 stream and update the checksum.

        Args:
            data: The data to append (bytes or string)

        Returns:
            The updated CRC32 checksum as a hexadecimal string
        """
        if isinstance(data, str):
            data = data.encode("utf-8")

        crc = self._crc

        for byte in data:
            crc = (crc >> 8) ^ self._bytes[(crc ^ byte) & 0xFF]

        self._crc = crc
        return number_to_hex(crc ^ 0xFFFFFFFF)

    @property
    def crc32(self) -> str:
        """Get the current CRC32 checksum."""
        return number_to_hex(self._crc ^ 0xFFFFFFFF)
