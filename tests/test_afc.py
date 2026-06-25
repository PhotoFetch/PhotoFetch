"""Tests for AFC protocol, usbmux, lockdown, and USB service methods."""

import io
import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from photofetch.afc.protocol import (
    AfcClient, AFC_MAGIC, AFC_HEADER_SIZE,
    AFC_OP_LIST_DIR, AFC_OP_GET_FILE_INFO, AFC_OP_FILE_OPEN,
    AFC_OP_FILE_OPEN_RES, AFC_OP_FILE_READ, AFC_OP_FILE_CLOSE,
    AFC_OP_STATUS, AFC_FOPEN_RDONLY,
)


# === AFC Protocol Tests ===

class FakeSocket:
    """Fake socket for testing AFC protocol."""

    def __init__(self):
        self._send_buf = b""
        self._recv_buf = b""

    def sendall(self, data):
        self._send_buf += data

    def recv(self, n):
        chunk = self._recv_buf[:n]
        self._recv_buf = self._recv_buf[n:]
        return chunk

    def queue_response(self, operation, header_payload=b"", data=b""):
        entire_length = AFC_HEADER_SIZE + len(header_payload) + len(data)
        this_length = AFC_HEADER_SIZE + len(header_payload)
        header = struct.pack("<8sQQQQ", AFC_MAGIC, entire_length, this_length, 0, operation)
        self._recv_buf += header + header_payload + data


class TestAfcProtocolEncoding:
    def test_send_packet_format(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        afc._send(AFC_OP_LIST_DIR, b"/DCIM/\x00")

        data = sock._send_buf
        magic = data[:8]
        assert magic == AFC_MAGIC
        entire_len, this_len, pkt_num, op = struct.unpack("<QQQQ", data[8:40])
        assert entire_len == AFC_HEADER_SIZE + 7  # "/DCIM/\x00" = 7 bytes
        assert this_len == AFC_HEADER_SIZE + 7
        assert pkt_num == 1
        assert op == AFC_OP_LIST_DIR

    def test_recv_packet_format(self):
        sock = FakeSocket()
        sock.queue_response(AFC_OP_LIST_DIR, data=b"file1.jpg\x00file2.jpg\x00")
        afc = AfcClient(sock)
        op, hdr, data = afc._recv()
        assert op == AFC_OP_LIST_DIR
        assert data == b"file1.jpg\x00file2.jpg\x00"

    def test_recv_bad_magic_raises(self):
        sock = FakeSocket()
        bad_header = struct.pack("<8sQQQQ", b"BADMAGIC", 40, 40, 0, 0)
        sock._recv_buf = bad_header
        afc = AfcClient(sock)
        with pytest.raises(RuntimeError, match="Bad AFC magic"):
            afc._recv()


class TestAfcListdir:
    def test_listdir_parses_response(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        sock.queue_response(AFC_OP_LIST_DIR, data=b".\x00..\x00IMG_0001.JPG\x00IMG_0002.JPG\x00")
        result = afc.listdir("/DCIM/100APPLE/")
        assert result == ["IMG_0001.JPG", "IMG_0002.JPG"]

    def test_listdir_empty_dir(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        sock.queue_response(AFC_OP_LIST_DIR, data=b".\x00..\x00")
        result = afc.listdir("/DCIM/")
        assert result == []

    def test_listdir_error_status(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 8))
        with pytest.raises(RuntimeError, match="listdir failed"):
            afc.listdir("/nonexistent/")


class TestAfcStat:
    def test_stat_parses_key_values(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        payload = b"st_size\x001024\x00st_ifmt\x00S_IFREG\x00st_nlink\x001\x00"
        sock.queue_response(AFC_OP_GET_FILE_INFO, data=payload)
        result = afc.stat("/DCIM/100APPLE/IMG_0001.JPG")
        assert result["st_size"] == 1024
        assert result["st_ifmt"] == "S_IFREG"
        assert result["st_nlink"] == 1

    def test_stat_parses_timestamps(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        # 1718614995000000000 ns = 2024-06-17 ~10:03 UTC
        payload = b"st_birthtime\x001718614995000000000\x00"
        sock.queue_response(AFC_OP_GET_FILE_INFO, data=payload)
        result = afc.stat("/DCIM/100APPLE/IMG_0001.JPG")
        assert result["st_birthtime"].year >= 2024

    def test_stat_error(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 4))
        with pytest.raises(RuntimeError, match="stat failed"):
            afc.stat("/bad/path")


class TestAfcReadFile:
    def test_read_file_single_chunk(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        # Open response
        handle = struct.pack("<Q", 42)
        sock.queue_response(AFC_OP_FILE_OPEN_RES, header_payload=handle)
        # Read response with data
        sock.queue_response(AFC_OP_FILE_READ, data=b"file contents here")
        # EOF (status)
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 0))
        # Close response
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 0))

        result = afc.read_file("/DCIM/100APPLE/IMG_0001.JPG")
        assert result == b"file contents here"

    def test_read_file_multiple_chunks(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        handle = struct.pack("<Q", 7)
        sock.queue_response(AFC_OP_FILE_OPEN_RES, header_payload=handle)
        sock.queue_response(AFC_OP_FILE_READ, data=b"chunk1")
        sock.queue_response(AFC_OP_FILE_READ, data=b"chunk2")
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 0))
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 0))

        result = afc.read_file("/test")
        assert result == b"chunk1chunk2"

    def test_read_file_open_error(self):
        sock = FakeSocket()
        afc = AfcClient(sock)
        sock.queue_response(AFC_OP_STATUS, header_payload=struct.pack("<Q", 2))
        with pytest.raises(RuntimeError, match="open failed"):
            afc.read_file("/nonexistent")


# === usbmux Tests ===

class TestUsbmux:
    @patch("photofetch.afc.usbmux.UsbmuxConnection")
    def test_list_devices(self, mock_conn_cls):
        from photofetch.afc.usbmux import list_devices
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.recv_plist.return_value = {
            "DeviceList": [
                {"Properties": {"DeviceID": 1, "SerialNumber": "ABC123"}},
            ]
        }
        devices = list_devices()
        assert len(devices) == 1
        assert devices[0]["SerialNumber"] == "ABC123"

    @patch("photofetch.afc.usbmux.UsbmuxConnection")
    def test_list_devices_empty(self, mock_conn_cls):
        from photofetch.afc.usbmux import list_devices
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.recv_plist.return_value = {"DeviceList": []}
        assert list_devices() == []

    @patch("photofetch.afc.usbmux.UsbmuxConnection")
    def test_connect_to_port_success(self, mock_conn_cls):
        from photofetch.afc.usbmux import connect_to_port
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.recv_plist.return_value = {"Number": 0}
        mock_sock = MagicMock()
        mock_conn.get_socket.return_value = mock_sock
        result = connect_to_port(1, 62078)
        assert result == mock_sock

    @patch("photofetch.afc.usbmux.UsbmuxConnection")
    def test_connect_to_port_refused(self, mock_conn_cls):
        from photofetch.afc.usbmux import connect_to_port
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.recv_plist.return_value = {"Number": 3}
        with pytest.raises(ConnectionError):
            connect_to_port(1, 22)


# === Lockdown Tests ===

class TestLockdown:
    def _make_lockdown(self):
        from photofetch.afc.lockdown import LockdownClient
        sock = MagicMock()
        with patch("photofetch.afc.lockdown._find_pair_record") as mock_pr:
            mock_pr.return_value = {
                "HostID": "test-host-id",
                "SystemBUID": "test-buid",
                "HostCertificate": b"cert",
                "HostPrivateKey": b"key",
                "RootCertificate": b"root",
            }
            ld = LockdownClient(sock, "FAKE-UDID")
        return ld, sock

    def test_query_type(self):
        ld, sock = self._make_lockdown()
        # Mock recv to return a plist response
        import plistlib
        resp = plistlib.dumps({"Type": "com.apple.mobile.lockdown"})
        sock.recv = MagicMock(side_effect=lambda n: (struct.pack(">I", len(resp)) + resp)[:n] if n <= 4 else resp)
        # Simplified: just test send is called
        ld._send = MagicMock()
        ld._recv = MagicMock(return_value={"Type": "com.apple.mobile.lockdown"})
        assert ld.query_type() == "com.apple.mobile.lockdown"

    def test_start_session_success(self):
        ld, sock = self._make_lockdown()
        ld._send = MagicMock()
        ld._recv = MagicMock(return_value={"SessionID": "abc-123", "EnableSessionSSL": False})
        assert ld.start_session() is True

    def test_start_session_failure(self):
        ld, sock = self._make_lockdown()
        ld._send = MagicMock()
        ld._recv = MagicMock(return_value={"Error": "InvalidHostID"})
        assert ld.start_session() is False

    def test_start_service(self):
        ld, sock = self._make_lockdown()
        ld._send = MagicMock()
        ld._recv = MagicMock(return_value={"Port": 12345, "EnableServiceSSL": False})
        port, use_ssl = ld.start_service("com.apple.afc")
        assert port == 12345
        assert use_ssl is False

    def test_start_service_error(self):
        ld, sock = self._make_lockdown()
        ld._send = MagicMock()
        ld._recv = MagicMock(return_value={"Error": "ServiceNotFound"})
        with pytest.raises(RuntimeError, match="StartService failed"):
            ld.start_service("com.apple.bad")


# === USB Service Tests ===

class TestUsbServiceMethods:
    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_thumbnail_image(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        # Create a minimal valid JPEG
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(buf, format="JPEG")
        mock_afc = MagicMock()
        mock_afc.read_file.return_value = buf.getvalue()
        mock_afc_fn.return_value = mock_afc

        svc = UsbService()
        thumb = svc.get_thumbnail("/DCIM/100APPLE/IMG_0001.JPG")
        assert thumb[:2] == b"\xff\xd8"  # JPEG magic

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_gps_with_data(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        from PIL import Image
        import piexif

        # Create JPEG with GPS EXIF
        img = Image.new("RGB", (10, 10))
        exif_dict = {"GPS": {
            piexif.GPSIFD.GPSLatitudeRef: "N",
            piexif.GPSIFD.GPSLatitude: ((47, 1), (28, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: "E",
            piexif.GPSIFD.GPSLongitude: ((19, 1), (4, 1), (0, 1)),
        }}
        exif_bytes = piexif.dump(exif_dict)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif_bytes)

        mock_afc = MagicMock()
        mock_afc.read_file.return_value = buf.getvalue()
        mock_afc_fn.return_value = mock_afc

        svc = UsbService()
        gps = svc.get_gps("/DCIM/100APPLE/IMG_0001.JPG")
        assert gps["lat"] > 47
        assert gps["lon"] > 19

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_gps_no_data(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="JPEG")
        mock_afc = MagicMock()
        mock_afc.read_file.return_value = buf.getvalue()
        mock_afc_fn.return_value = mock_afc

        svc = UsbService()
        assert svc.get_gps("/DCIM/100APPLE/IMG_0001.JPG") == {}

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_gps_video_skipped(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        svc = UsbService()
        assert svc.get_gps("/DCIM/100APPLE/VID_0001.MOV") == {}
        mock_afc_fn.assert_not_called()

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_preview_jpeg_passthrough(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        mock_afc = MagicMock()
        mock_afc.read_file.return_value = b"\xff\xd8raw jpeg data"
        mock_afc_fn.return_value = mock_afc

        svc = UsbService()
        result = svc.get_preview("/DCIM/100APPLE/IMG_0001.JPG")
        assert result == b"\xff\xd8raw jpeg data"

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_get_preview_video_raises(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService
        svc = UsbService()
        with pytest.raises(ValueError, match="Preview not available"):
            svc.get_preview("/DCIM/100APPLE/VID_0001.MOV")

    @patch("photofetch.services.usb_service._get_pooled_afc")
    def test_fetch_raw(self, mock_afc_fn):
        from photofetch.services.usb_service import UsbService

        mock_afc = MagicMock()
        mock_afc.read_file.return_value = b"photo data"
        mock_afc_fn.return_value = mock_afc

        svc = UsbService()
        data = svc.fetch_raw("/DCIM/100APPLE/IMG_0001.JPG")
        assert data == b"photo data"
        mock_afc.read_file.assert_called_once_with("/DCIM/100APPLE/IMG_0001.JPG")


# === Batch disk space test ===

class TestBatchDiskSpace:
    @patch("photofetch.routes.batch._is_under_home", return_value=True)
    @patch("photofetch.routes.batch.shutil.disk_usage")
    def test_insufficient_space(self, mock_usage, mock_home, client):
        mock_usage.return_value = MagicMock(free=100)  # 100 bytes free
        res = client.post("/api/batch/download", json={
            "source": "usb",
            "items": ["/DCIM/100APPLE/IMG_0001.HEIC"],
            "folder": str(Path.home()),
            "total_size": 5000000,  # 5 MB needed
        })
        assert res.status_code == 400
        assert "Not enough space" in res.get_json()["error"]


@pytest.fixture
def client():
    from photofetch.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
