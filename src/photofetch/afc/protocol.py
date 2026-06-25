"""AFC (Apple File Conduit) binary protocol client.

AFC packet format:
  Header (40 bytes):
    - magic: "CFA6LPAA" (8 bytes)
    - entire_length: u64 LE (header + payload)
    - this_length: u64 LE (header + header_payload, excluding data)
    - packet_num: u64 LE
    - operation: u64 LE
  Followed by header_payload (null-terminated strings for paths, etc.)
  Followed by data payload

AFC operations (subset needed for file browsing):
  LIST_DIR = 0x03
  GET_FILE_INFO = 0x0a
  FILE_OPEN = 0x0d
  FILE_READ = 0x0f
  FILE_CLOSE = 0x14
"""

import struct
from pathlib import Path

AFC_MAGIC = b"CFA6LPAA"
AFC_HEADER_SIZE = 40

# Operations
AFC_OP_STATUS = 0x01
AFC_OP_LIST_DIR = 0x03
AFC_OP_GET_FILE_INFO = 0x0a
AFC_OP_FILE_OPEN = 0x0d
AFC_OP_FILE_OPEN_RES = 0x0e
AFC_OP_FILE_READ = 0x0f
AFC_OP_FILE_CLOSE = 0x14

# File open modes
AFC_FOPEN_RDONLY = 0x01


class AfcClient:
    """Minimal AFC client for reading files from iOS devices."""

    def __init__(self, sock):
        """Initialize with a connected socket to the AFC service port."""
        self._sock = sock
        self._packet_num = 0

    def _next_packet_num(self) -> int:
        self._packet_num += 1
        return self._packet_num

    def _send(self, operation: int, header_payload: bytes = b"", data: bytes = b""):
        """Send an AFC packet."""
        pkt_num = self._next_packet_num()
        entire_length = AFC_HEADER_SIZE + len(header_payload) + len(data)
        this_length = AFC_HEADER_SIZE + len(header_payload)
        header = struct.pack(
            "<8sQQQQ",
            AFC_MAGIC,
            entire_length,
            this_length,
            pkt_num,
            operation,
        )
        self._sock.sendall(header + header_payload + data)

    def _recv(self) -> tuple[int, bytes, bytes]:
        """Receive an AFC packet. Returns (operation, header_payload, data)."""
        raw_header = self._recvall(AFC_HEADER_SIZE)
        magic, entire_length, this_length, pkt_num, operation = struct.unpack(
            "<8sQQQQ", raw_header
        )
        if magic != AFC_MAGIC:
            raise RuntimeError(f"Bad AFC magic: {magic!r}")
        if entire_length > 100 * 1024 * 1024:
            raise RuntimeError(f"AFC response too large: {entire_length} bytes")
        header_payload_len = this_length - AFC_HEADER_SIZE
        data_len = entire_length - this_length
        header_payload = self._recvall(header_payload_len) if header_payload_len > 0 else b""
        data = self._recvall(data_len) if data_len > 0 else b""
        return operation, header_payload, data

    def _recvall(self, n: int) -> bytes:
        if n <= 0:
            return b""
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(min(n - len(buf), 65536))
            if not chunk:
                raise ConnectionError("AFC connection closed")
            buf += chunk
        return buf

    def listdir(self, path: str) -> list[str]:
        """List directory contents. Returns filenames (excludes . and ..)."""
        self._send(AFC_OP_LIST_DIR, path.encode() + b"\x00")
        op, hdr, data = self._recv()
        if op == AFC_OP_STATUS:
            code = struct.unpack("<Q", hdr[:8])[0] if len(hdr) >= 8 else -1
            if code != 0:
                raise RuntimeError(f"AFC listdir failed: status {code}")
            return []
        # Data is null-separated list of filenames
        names = data.split(b"\x00")
        return [n.decode() for n in names if n and n not in (b".", b"..")]

    def stat(self, path: str) -> dict:
        """Get file info. Returns dict with keys like st_size, st_mtime, etc."""
        self._send(AFC_OP_GET_FILE_INFO, path.encode() + b"\x00")
        op, hdr, data = self._recv()
        if op == AFC_OP_STATUS:
            code = struct.unpack("<Q", hdr[:8])[0] if len(hdr) >= 8 else -1
            raise RuntimeError(f"AFC stat failed: status {code}")
        # Data is alternating key\0value\0 pairs
        parts = data.split(b"\x00")
        info = {}
        for i in range(0, len(parts) - 1, 2):
            key = parts[i].decode()
            val = parts[i + 1].decode()
            if key in ("st_size", "st_blocks", "st_nlink"):
                info[key] = int(val)
            elif key in ("st_mtime", "st_birthtime"):
                # AFC timestamps are in nanoseconds since epoch
                from datetime import datetime
                info[key] = datetime.fromtimestamp(int(val) / 1_000_000_000)
            else:
                info[key] = val
        return info

    def read_file(self, path: str) -> bytes:
        """Read entire file contents."""
        # Open file
        payload = struct.pack("<Q", AFC_FOPEN_RDONLY) + path.encode() + b"\x00"
        self._send(AFC_OP_FILE_OPEN, payload)
        op, hdr, data = self._recv()
        if op == AFC_OP_STATUS:
            code = struct.unpack("<Q", hdr[:8])[0] if len(hdr) >= 8 else -1
            raise RuntimeError(f"AFC open failed: status {code}")
        handle = struct.unpack("<Q", hdr[:8])[0]

        # Read in chunks
        chunks = []
        while True:
            req = struct.pack("<QQ", handle, 1024 * 1024)  # 1 MB chunks
            self._send(AFC_OP_FILE_READ, req)
            op, hdr, data = self._recv()
            if op == AFC_OP_STATUS:
                break  # EOF or error
            if not data:
                break
            chunks.append(data)

        # Close
        self._send(AFC_OP_FILE_CLOSE, struct.pack("<Q", handle))
        self._recv()  # consume close response

        return b"".join(chunks)

    def close(self):
        self._sock.close()
