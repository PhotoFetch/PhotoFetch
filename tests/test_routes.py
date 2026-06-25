"""Comprehensive tests for PhotoFetch routes and services."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from photofetch.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# === USB Routes ===

@patch("photofetch.routes.usb.UsbService")
def test_usb_status_connected(mock_cls, client):
    mock_cls.return_value.is_connected.return_value = True
    res = client.get("/api/usb/status")
    assert res.status_code == 200
    assert res.get_json() == {"connected": True}


@patch("photofetch.routes.usb.UsbService")
def test_usb_status_disconnected(mock_cls, client):
    mock_cls.return_value.is_connected.return_value = False
    res = client.get("/api/usb/status")
    assert res.get_json() == {"connected": False}


@patch("photofetch.routes.usb.UsbService")
def test_usb_list_photos(mock_cls, client):
    mock_cls.return_value.list_photos.return_value = [
        {"path": "/DCIM/100APPLE/IMG_0001.HEIC", "filename": "IMG_0001.HEIC", "size": 2048, "date": ""},
    ]
    res = client.get("/api/usb/photos")
    data = res.get_json()
    assert len(data["photos"]) == 1
    assert data["photos"][0]["filename"] == "IMG_0001.HEIC"


@patch("photofetch.routes.usb.UsbService")
def test_usb_list_photos_error(mock_cls, client):
    mock_cls.return_value.list_photos.side_effect = RuntimeError("No device")
    res = client.get("/api/usb/photos")
    assert res.status_code == 500
    assert "error" in res.get_json()


@patch("photofetch.routes.usb.UsbService")
def test_usb_thumbnail(mock_cls, client):
    mock_cls.return_value.get_thumbnail.return_value = b"\xff\xd8\xff\xe0fake"
    res = client.get("/api/usb/thumbnail?path=/DCIM/100APPLE/IMG_0001.HEIC")
    assert res.status_code == 200
    assert res.content_type == "image/jpeg"
    assert "Cache-Control" in res.headers


def test_usb_thumbnail_missing_param(client):
    res = client.get("/api/usb/thumbnail")
    assert res.status_code == 400


@patch("photofetch.routes.usb.UsbService")
def test_usb_thumbnail_path_traversal(mock_cls, client):
    mock_cls.return_value.get_thumbnail.side_effect = ValueError("Path must start with /DCIM/")
    res = client.get("/api/usb/thumbnail?path=/etc/passwd")
    assert res.status_code == 400
    assert "error" in res.get_json()


@patch("photofetch.routes.usb.UsbService")
def test_usb_download(mock_cls, client):
    tmp = Path(tempfile.gettempdir()) / "test_dl.HEIC"
    tmp.write_bytes(b"fakedata")
    mock_cls.return_value.download.return_value = tmp
    res = client.get("/api/usb/download?path=/DCIM/100APPLE/IMG_0001.HEIC")
    assert res.status_code == 200
    assert res.headers.get("Content-Disposition") is not None
    assert "IMG_0001.HEIC" in res.headers["Content-Disposition"]


def test_usb_download_missing_param(client):
    res = client.get("/api/usb/download")
    assert res.status_code == 400


@patch("photofetch.routes.usb.UsbService")
def test_usb_download_path_traversal(mock_cls, client):
    mock_cls.return_value.download.side_effect = ValueError("Path must start with /DCIM/")
    res = client.get("/api/usb/download?path=/root/secret.txt")
    assert res.status_code == 400


@patch("photofetch.routes.usb.UsbService")
def test_usb_preview(mock_cls, client):
    mock_cls.return_value.get_preview.return_value = b"\xff\xd8jpeg"
    res = client.get("/api/usb/preview?path=/DCIM/100APPLE/IMG_0001.HEIC")
    assert res.status_code == 200
    assert res.content_type == "image/jpeg"


@patch("photofetch.routes.usb.UsbService")
def test_usb_exif(mock_cls, client):
    mock_cls.return_value.get_gps.return_value = {"lat": 47.5, "lon": 19.0}
    res = client.get("/api/usb/exif?path=/DCIM/100APPLE/IMG_0001.JPG")
    assert res.status_code == 200
    assert res.get_json()["lat"] == 47.5


@patch("photofetch.routes.usb.UsbService")
def test_usb_exif_no_gps(mock_cls, client):
    mock_cls.return_value.get_gps.return_value = {}
    res = client.get("/api/usb/exif?path=/DCIM/100APPLE/IMG_0001.JPG")
    assert res.get_json() == {}


# === iCloud Routes ===

@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_login_success(mock_cls, client):
    mock_cls.login.return_value = {"status": "ok"}
    res = client.post("/api/icloud/login", json={"email": "a@b.c", "password": "x"})
    assert res.get_json()["status"] == "ok"


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_login_2fa(mock_cls, client):
    mock_cls.login.return_value = {"status": "2fa_required"}
    res = client.post("/api/icloud/login", json={"email": "a@b.c", "password": "x"})
    assert res.get_json()["status"] == "2fa_required"


def test_icloud_login_missing_fields(client):
    res = client.post("/api/icloud/login", json={"email": "a@b.c"})
    assert res.status_code == 400


def test_icloud_login_invalid_json(client):
    res = client.post("/api/icloud/login", data="not json", content_type="text/plain")
    assert res.status_code == 400


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_verify_2fa(mock_cls, client):
    mock_cls.verify_2fa.return_value = {"status": "ok"}
    res = client.post("/api/icloud/verify-2fa", json={"code": "123456"})
    assert res.get_json()["status"] == "ok"


def test_icloud_verify_2fa_missing_code(client):
    res = client.post("/api/icloud/verify-2fa", json={})
    assert res.status_code == 400


def test_icloud_verify_2fa_invalid_json(client):
    res = client.post("/api/icloud/verify-2fa", data="bad", content_type="text/plain")
    assert res.status_code == 400


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_list_photos(mock_cls, client):
    mock_cls.list_photos.return_value = [
        {"id": "0", "filename": "IMG_0001.HEIC", "date": "2026-01-01", "size": 4096},
    ]
    mock_cls.total_count.return_value = 1
    res = client.get("/api/icloud/photos")
    data = res.get_json()
    assert len(data["photos"]) == 1
    assert data["total"] == 1


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_list_photos_pagination(mock_cls, client):
    mock_cls.list_photos.return_value = []
    mock_cls.total_count.return_value = 5000
    res = client.get("/api/icloud/photos?offset=100&limit=50")
    mock_cls.list_photos.assert_called_with(offset=100, limit=50)


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_list_photos_limit_cap(mock_cls, client):
    mock_cls.list_photos.return_value = []
    mock_cls.total_count.return_value = 0
    res = client.get("/api/icloud/photos?limit=9999")
    mock_cls.list_photos.assert_called_with(offset=0, limit=500)


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_thumbnail(mock_cls, client):
    mock_cls.get_thumbnail.return_value = b"\xff\xd8fake"
    res = client.get("/api/icloud/thumbnail?id=0")
    assert res.status_code == 200
    assert res.content_type == "image/jpeg"


def test_icloud_thumbnail_missing_id(client):
    res = client.get("/api/icloud/thumbnail")
    assert res.status_code == 400


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_thumbnail_invalid_id(mock_cls, client):
    mock_cls.get_thumbnail.side_effect = ValueError("Invalid photo ID: 999")
    res = client.get("/api/icloud/thumbnail?id=999")
    assert res.status_code == 400


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_download(mock_cls, client):
    tmp = Path(tempfile.gettempdir()) / "test_icloud.HEIC"
    tmp.write_bytes(b"fakedata")
    mock_cls.download.return_value = tmp
    res = client.get("/api/icloud/download?id=0")
    assert res.status_code == 200


def test_icloud_download_missing_id(client):
    res = client.get("/api/icloud/download")
    assert res.status_code == 400


@patch("photofetch.routes.icloud.ICloudService")
def test_icloud_download_invalid_id(mock_cls, client):
    mock_cls.download.side_effect = ValueError("Invalid photo ID: -1")
    res = client.get("/api/icloud/download?id=-1")
    assert res.status_code == 400


# === Batch Routes ===

@patch("photofetch.routes.batch._is_under_home", return_value=True)
@patch("photofetch.routes.batch.UsbService")
def test_batch_download_usb(mock_cls, _mock_home, client, tmp_path):
    mock_cls.return_value.fetch_raw_session.return_value = b"fakedata"
    res = client.post("/api/batch/download", json={
        "source": "usb",
        "items": ["/DCIM/100APPLE/IMG_0001.HEIC"],
        "folder": str(tmp_path),
        "sizes": {"/DCIM/100APPLE/IMG_0001.HEIC": 8},
        "total_size": 100,
    })
    assert res.status_code == 200
    data = res.get_data(as_text=True)
    assert '"saved": 1' in data
    assert (tmp_path / "IMG_0001.HEIC").exists()


@patch("photofetch.routes.batch._is_under_home", return_value=True)
@patch("photofetch.routes.batch.UsbService")
def test_batch_download_usb_skips_existing(mock_cls, _mock_home, client, tmp_path):
    (tmp_path / "IMG_0001.HEIC").write_bytes(b"fakedata")
    res = client.post("/api/batch/download", json={
        "source": "usb",
        "items": ["/DCIM/100APPLE/IMG_0001.HEIC"],
        "folder": str(tmp_path),
        "sizes": {"/DCIM/100APPLE/IMG_0001.HEIC": 8},
        "total_size": 0,
    })
    assert res.status_code == 200
    data = res.get_data(as_text=True)
    assert '"skipped": 1' in data
    mock_cls.return_value.fetch_raw_session.assert_not_called()


@patch("photofetch.routes.batch._is_under_home", return_value=True)
@patch("photofetch.routes.batch.UsbService")
def test_batch_download_usb_filename_collision(mock_cls, _mock_home, client, tmp_path):
    # Existing file with different size — should save as IMG_0001_1.HEIC
    (tmp_path / "IMG_0001.HEIC").write_bytes(b"old")
    mock_cls.return_value.fetch_raw_session.return_value = b"newdata!"
    res = client.post("/api/batch/download", json={
        "source": "usb",
        "items": ["/DCIM/100APPLE/IMG_0001.HEIC"],
        "folder": str(tmp_path),
        "sizes": {"/DCIM/100APPLE/IMG_0001.HEIC": 8},
        "total_size": 0,
    })
    assert res.status_code == 200
    data = res.get_data(as_text=True)
    assert '"saved": 1' in data
    assert (tmp_path / "IMG_0001_1.HEIC").exists()


@patch("photofetch.routes.batch._is_under_home", return_value=True)
@patch("photofetch.routes.batch.ICloudService")
def test_batch_download_icloud(mock_cls, _mock_home, client, tmp_path):
    photo = MagicMock()
    photo.download.return_value = b"fakedata1"
    photo.filename = "IMG_0001.HEIC"
    photo.size = 9
    mock_cls._get_photo_by_id.return_value = photo
    res = client.post("/api/batch/download", json={
        "source": "icloud",
        "items": ["0"],
        "folder": str(tmp_path),
        "total_size": 100,
    })
    assert res.status_code == 200
    data = res.get_data(as_text=True)
    assert '"saved": 1' in data
    assert (tmp_path / "IMG_0001.HEIC").exists()


def test_batch_download_no_items(client, tmp_path):
    res = client.post("/api/batch/download", json={"source": "usb", "items": [], "folder": str(tmp_path)})
    assert res.status_code == 400


def test_batch_download_no_folder(client):
    res = client.post("/api/batch/download", json={"source": "usb", "items": ["x"]})
    assert res.status_code == 400


def test_batch_download_invalid_source(client, tmp_path):
    res = client.post("/api/batch/download", json={"source": "bad", "items": ["x"], "folder": str(tmp_path)})
    assert res.status_code == 400


def test_batch_download_invalid_json(client):
    res = client.post("/api/batch/download", data="bad", content_type="text/plain")
    assert res.status_code == 400


@patch("photofetch.routes.batch._is_under_home", return_value=True)
def test_batch_download_too_many_items(_mock_home, client, tmp_path):
    res = client.post("/api/batch/download", json={
        "source": "usb",
        "items": [f"/DCIM/100APPLE/IMG_{i:04d}.HEIC" for i in range(5001)],
        "folder": str(tmp_path),
        "total_size": 0,
    })
    assert res.status_code == 400
    assert "max" in res.get_json()["error"]


def test_batch_download_folder_outside_home(client, tmp_path):
    # Folder outside home is now allowed (trust the folder picker)
    # Just verify it doesn't crash — /tmp is valid if it exists
    import tempfile
    d = tempfile.mkdtemp()
    res = client.post("/api/batch/download", json={
        "source": "usb",
        "items": ["/DCIM/100APPLE/IMG_0001.HEIC"],
        "folder": d,
        "total_size": 0,
    })
    # Should start streaming (200) not reject
    assert res.status_code == 200
    Path(d).rmdir()


@patch("photofetch.routes.batch._pick_folder")
def test_batch_pick_folder(mock_pick, client):
    mock_pick.return_value = "/Users/test/Photos"
    res = client.post("/api/batch/pick-folder")
    assert res.status_code == 200
    assert res.get_json()["folder"] == "/Users/test/Photos"


@patch("photofetch.routes.batch._pick_folder")
def test_batch_pick_folder_cancelled(mock_pick, client):
    mock_pick.return_value = None
    res = client.post("/api/batch/pick-folder")
    assert res.status_code == 400


def test_batch_abort(client):
    res = client.post("/api/batch/abort")
    assert res.status_code == 200
    assert res.get_json()["status"] == "aborted"


def test_batch_abort_with_token(client):
    res = client.post("/api/batch/abort", json={"token": "nonexistent-token"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "aborted"


@patch("photofetch.routes.batch._is_under_home", return_value=True)
def test_batch_open_folder(_mock_home, client, tmp_path):
    with patch("subprocess.Popen") as mock_popen:
        res = client.post("/api/batch/open-folder", json={"folder": str(tmp_path)})
        assert res.status_code == 200
        mock_popen.assert_called_once()


def test_batch_open_folder_invalid(client):
    res = client.post("/api/batch/open-folder", json={"folder": "/nonexistent"})
    assert res.status_code == 400


def test_batch_open_folder_outside_home(client, tmp_path):
    # External folders now allowed — just verify non-existent is rejected
    res = client.post("/api/batch/open-folder", json={"folder": "/nonexistent_xyz"})
    assert res.status_code == 400


def test_csrf_rejects_cross_origin(client):
    res = client.post("/api/batch/pick-folder",
                      headers={"Origin": "http://evil.com"})
    assert res.status_code == 403


def test_csrf_allows_localhost(client):
    with patch("photofetch.routes.batch._pick_folder", return_value=None):
        res = client.post("/api/batch/pick-folder",
                          headers={"Origin": "http://127.0.0.1:8080"})
        # 400 = cancelled (not 403), meaning CSRF passed
        assert res.status_code == 400
        assert "cancelled" in res.get_json()["error"]


# === USB Service Unit Tests ===

class TestUsbServiceValidation:
    def test_validate_dcim_path_valid(self):
        from photofetch.services.usb_service import _validate_dcim_path
        assert _validate_dcim_path("/DCIM/100APPLE/IMG_0001.HEIC") == "/DCIM/100APPLE/IMG_0001.HEIC"

    def test_validate_dcim_path_traversal(self):
        from photofetch.services.usb_service import _validate_dcim_path
        with pytest.raises(ValueError, match="traversal"):
            _validate_dcim_path("/DCIM/../etc/passwd")

    def test_validate_dcim_path_wrong_prefix(self):
        from photofetch.services.usb_service import _validate_dcim_path
        with pytest.raises(ValueError, match="must start with"):
            _validate_dcim_path("/etc/passwd")

    def test_validate_dcim_path_root(self):
        from photofetch.services.usb_service import _validate_dcim_path
        with pytest.raises(ValueError, match="must start with"):
            _validate_dcim_path("/root/keychain.db")


# === iCloud Service Unit Tests ===

class TestICloudServiceValidation:
    @patch("photofetch.services.icloud_service.ICloudService._get_photos")
    def test_get_photo_by_id_valid(self, mock_photos):
        from photofetch.services.icloud_service import ICloudService
        mock_photos.return_value = ["photo0", "photo1", "photo2"]
        result = ICloudService._get_photo_by_id("1")
        assert result == "photo1"

    @patch("photofetch.services.icloud_service.ICloudService._get_photos")
    def test_get_photo_by_id_negative(self, mock_photos):
        from photofetch.services.icloud_service import ICloudService
        mock_photos.return_value = ["photo0", "photo1"]
        with pytest.raises(ValueError, match="Invalid photo ID"):
            ICloudService._get_photo_by_id("-1")

    @patch("photofetch.services.icloud_service.ICloudService._get_photos")
    def test_get_photo_by_id_out_of_range(self, mock_photos):
        from photofetch.services.icloud_service import ICloudService
        mock_photos.return_value = ["photo0"]
        with pytest.raises(ValueError, match="Invalid photo ID"):
            ICloudService._get_photo_by_id("5")

    @patch("photofetch.services.icloud_service.ICloudService._get_photos")
    def test_list_photos_pagination(self, mock_photos):
        from photofetch.services.icloud_service import ICloudService
        photos = []
        for i in range(10):
            p = MagicMock()
            p.filename = f"IMG_{i:04d}.HEIC"
            p.asset_date = "2026-01-01"
            p.size = 1024
            photos.append(p)
        mock_photos.return_value = photos
        ICloudService._api = MagicMock()

        result = ICloudService.list_photos(offset=3, limit=2)
        assert len(result) == 2
        assert result[0]["id"] == "3"
        assert result[1]["id"] == "4"

        ICloudService._api = None
        ICloudService._photo_cache = None

    def test_download_multiple_batch_limit(self):
        from photofetch.services.icloud_service import ICloudService, MAX_BATCH_SIZE
        with pytest.raises(ValueError, match="maximum"):
            ICloudService.download_multiple(
                [str(i) for i in range(MAX_BATCH_SIZE + 1)],
                Path(tempfile.gettempdir()),
            )


# === Main page ===

def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"PhotoFetch" in res.data
    assert b"escapeHtml" in res.data
    assert b"i18n.js" in res.data
