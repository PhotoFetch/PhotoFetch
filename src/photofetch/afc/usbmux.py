"""usbmuxd client — connects to the local usbmuxd daemon.

Modern usbmuxd (since iOS 5+) uses a plist-based protocol rather than
the binary protocol shown on the wiki. We implement the plist variant.
"""

import plistlib
import socket
import struct
import sys
from pathlib import Path


USBMUXD_SOCKET_PATH = "/var/run/usbmuxd"
USBMUXD_SOCKET_PATH_ALT = "/var/run/apple/usbmuxd"
USBMUXD_WIN_HOST = "127.0.0.1"
USBMUXD_WIN_PORT = 27015


def _find_socket_path() -> str:
    """Find the usbmuxd socket path."""
    for p in (USBMUXD_SOCKET_PATH, USBMUXD_SOCKET_PATH_ALT):
        if Path(p).exists():
            return p
    if sys.platform == "darwin":
        return "/var/run/usbmuxd"
    raise FileNotFoundError("usbmuxd socket not found")


class UsbmuxConnection:
    """Low-level connection to usbmuxd daemon."""

    def __init__(self):
        if sys.platform == "win32":
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((USBMUXD_WIN_HOST, USBMUXD_WIN_PORT))
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(_find_socket_path())
        self._tag = 0

    def _next_tag(self) -> int:
        self._tag += 1
        return self._tag

    def send_plist(self, payload: dict) -> None:
        """Send a plist message to usbmuxd."""
        payload.setdefault("ClientVersionString", "photofetch")
        payload.setdefault("ProgName", "photofetch")
        xml = plistlib.dumps(payload)
        # Plist protocol: 4-byte LE length + 4-byte LE version(1) + 4-byte LE type(8=plist) + 4-byte LE tag
        tag = self._next_tag()
        header = struct.pack("<IIII", len(xml) + 16, 1, 8, tag)
        self._sock.sendall(header + xml)

    def recv_plist(self) -> dict:
        """Receive a plist message from usbmuxd."""
        header = self._recvall(16)
        length, version, msg_type, tag = struct.unpack("<IIII", header)
        payload_len = length - 16
        if payload_len <= 0:
            return {}
        data = self._recvall(payload_len)
        return plistlib.loads(data)

    def _recvall(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("usbmuxd connection closed")
            buf += chunk
        return buf

    def get_socket(self) -> socket.socket:
        """Return the underlying socket (for TLS wrapping)."""
        return self._sock

    def close(self):
        self._sock.close()


def list_devices() -> list[dict]:
    """List connected iOS devices."""
    conn = UsbmuxConnection()
    conn.send_plist({"MessageType": "ListDevices"})
    resp = conn.recv_plist()
    conn.close()
    devices = resp.get("DeviceList", [])
    return [d["Properties"] for d in devices if "Properties" in d]


def connect_to_port(device_id: int, port: int) -> socket.socket:
    """Connect to a TCP port on the device via usbmuxd.

    Returns the raw socket after successful connection (ready for I/O).
    Port must be in network byte order (big-endian) in the protocol,
    but we accept host-order here and convert internally.
    """
    conn = UsbmuxConnection()
    conn.send_plist({
        "MessageType": "Connect",
        "DeviceID": device_id,
        "PortNumber": socket.htons(port),
    })
    resp = conn.recv_plist()
    if resp.get("Number", -1) != 0:
        conn.close()
        raise ConnectionError(f"Failed to connect to port {port}: {resp}")
    # After successful connect, the socket is now a raw TCP tunnel to the device
    return conn.get_socket()
