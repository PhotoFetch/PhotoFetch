"""Lockdown client — handles pairing and service startup.

Communicates with lockdownd on port 62078. Messages are length-prefixed
plist XML over a plain socket, then TLS after StartSession.
"""

import plistlib
import socket
import ssl
import struct
import sys
from pathlib import Path


LOCKDOWN_PORT = 62078


def _pair_record_dirs() -> list[Path]:
    """Return candidate pair record directories for the current platform."""
    if sys.platform == "win32":
        return [
            Path(r"C:\ProgramData\Apple\Lockdown"),
            Path(r"C:\ProgramData\Apple\Lockdown\Backups"),
        ]
    elif sys.platform == "darwin":
        return [Path("/var/db/lockdown")]
    return [
        Path("/var/lib/lockdown"),
        Path(Path.home() / ".config" / "lockdown"),
    ]


def _find_pair_record(udid: str) -> dict:
    """Find the pairing record for a device by UDID."""
    for d in _pair_record_dirs():
        f = d / f"{udid}.plist"
        if f.exists():
            return plistlib.loads(f.read_bytes())
    searched = ", ".join(str(d) for d in _pair_record_dirs())
    raise FileNotFoundError(
        f"No pair record for {udid}. Trust the device first. "
        f"(Searched: {searched})"
    )


class LockdownClient:
    """Communicates with lockdownd to start services."""

    def __init__(self, sock: socket.socket, udid: str):
        self._sock = sock
        self._ssl_sock = None
        self._udid = udid
        self._pair_record = _find_pair_record(udid)
        self._session_id = None

    def _send(self, payload: dict):
        """Send a plist message (length-prefixed, big-endian)."""
        s = self._ssl_sock or self._sock
        xml = plistlib.dumps(payload)
        s.sendall(struct.pack(">I", len(xml)) + xml)

    def _recv(self) -> dict:
        """Receive a plist message."""
        s = self._ssl_sock or self._sock
        header = self._recvall(s, 4)
        length = struct.unpack(">I", header)[0]
        if length == 0:
            return {}
        data = self._recvall(s, length)
        return plistlib.loads(data)

    @staticmethod
    def _recvall(s, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("lockdown connection closed")
            buf += chunk
        return buf

    def query_type(self) -> str:
        """Verify we're talking to lockdownd."""
        self._send({"Label": "photofetch", "Request": "QueryType"})
        resp = self._recv()
        return resp.get("Type", "")

    def get_value(self, key: str = None, domain: str = None) -> dict:
        """Get a device value."""
        req = {"Label": "photofetch", "Request": "GetValue"}
        if key:
            req["Key"] = key
        if domain:
            req["Domain"] = domain
        self._send(req)
        return self._recv()

    def validate_pair(self) -> bool:
        """Validate an existing pairing."""
        self._send({
            "Label": "photofetch",
            "Request": "ValidatePair",
            "PairRecord": self._pair_record,
        })
        resp = self._recv()
        return resp.get("Result") == "Success"

    def start_session(self) -> bool:
        """Start a TLS session using the pair record."""
        host_id = self._pair_record.get("HostID", "")
        self._send({
            "Label": "photofetch",
            "Request": "StartSession",
            "HostID": host_id,
            "SystemBUID": self._pair_record.get("SystemBUID", host_id),
        })
        resp = self._recv()
        if resp.get("Error"):
            return False
        self._session_id = resp.get("SessionID")
        if resp.get("EnableSessionSSL"):
            self._enable_ssl()
        return self._session_id is not None

    def _enable_ssl(self):
        """Wrap the socket in TLS using pair record certificates."""
        import tempfile

        host_cert = self._pair_record["HostCertificate"]
        host_key = self._pair_record["HostPrivateKey"]

        cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
        key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
        cert_file.write(host_cert)
        cert_file.close()
        key_file.write(host_key)
        key_file.close()

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(cert_file.name, key_file.name)

        self._ssl_sock = ctx.wrap_socket(self._sock, server_hostname=None)

        Path(cert_file.name).unlink()
        Path(key_file.name).unlink()

    def start_service(self, service_name: str) -> tuple[int, bool]:
        """Start a service and return (port, use_ssl)."""
        self._send({
            "Label": "photofetch",
            "Request": "StartService",
            "Service": service_name,
        })
        resp = self._recv()
        if resp.get("Error"):
            raise RuntimeError(f"StartService failed: {resp['Error']}")
        port = resp["Port"]
        use_ssl = resp.get("EnableServiceSSL", False)
        return port, use_ssl

    def close(self):
        if self._ssl_sock:
            self._ssl_sock.close()
        else:
            self._sock.close()
