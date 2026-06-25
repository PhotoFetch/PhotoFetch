"""USB service — iPhone photo access using our own AFC client (MIT-licensed)."""

import io
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

PHOTO_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".mov", ".mp4", ".dng"}
VIDEO_EXTENSIONS = {".mov", ".mp4"}
DCIM_PREFIX = "/DCIM/"

_cache_lock = threading.Lock()
_thumb_cache: dict[str, bytes] = {}
_afc_pool: list = []
_afc_pool_lock = threading.Lock()
_AFC_POOL_SIZE = 4


def _get_pooled_afc():
    """Get an AFC connection from the pool, creating one if needed."""
    with _afc_pool_lock:
        if _afc_pool:
            return _afc_pool.pop()
    return _new_afc()


def _return_afc(afc):
    """Return an AFC connection to the pool."""
    with _afc_pool_lock:
        if len(_afc_pool) < _AFC_POOL_SIZE:
            _afc_pool.append(afc)
        else:
            try:
                afc.close()
            except Exception:
                pass


def _read_file(remote_path: str) -> bytes:
    """Read a file from the device using a pooled AFC connection."""
    afc = _get_pooled_afc()
    try:
        data = afc.read_file(remote_path)
        _return_afc(afc)
        return data
    except Exception:
        try:
            afc.close()
        except Exception:
            pass
        try:
            afc = _new_afc()
            data = afc.read_file(remote_path)
            _return_afc(afc)
            return data
        except Exception as e:
            raise ConnectionError(f"Device not accessible: {e}") from e


def _validate_dcim_path(path: str) -> str:
    normalized = Path(path).as_posix()
    if not normalized.startswith(DCIM_PREFIX):
        raise ValueError(f"Path must start with {DCIM_PREFIX}")
    if any(part == ".." for part in Path(normalized).parts):
        raise ValueError("Path traversal not allowed")
    return normalized


def _new_afc():
    from photofetch.afc.client import connect
    return connect()


def _video_thumbnail(file_path: str) -> bytes:
    out_path = file_path + ".thumb.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-i", file_path, "-vframes", "1", "-vf",
             "scale=320:320:force_original_aspect_ratio=decrease",
             "-y", out_path],
            capture_output=True, timeout=10,
        )
        if Path(out_path).exists():
            return Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)
    return b""


class UsbService:
    _photo_cache: list[dict] | None = None

    @classmethod
    def clear_cache(cls):
        with _cache_lock:
            cls._photo_cache = None
            _thumb_cache.clear()
        # Drain stale pool connections
        with _afc_pool_lock:
            while _afc_pool:
                afc = _afc_pool.pop()
                try:
                    afc.close()
                except Exception:
                    pass

    def is_connected(self) -> bool:
        try:
            from photofetch.afc.usbmux import list_devices
            return len(list_devices()) > 0
        except Exception:
            return False

    def list_photos(self) -> list[dict]:
        with _cache_lock:
            if UsbService._photo_cache is not None:
                return UsbService._photo_cache

        afc = _get_pooled_afc()
        photos = []
        try:
            folders = afc.listdir("/DCIM/")
            for folder in sorted(folders):
                if folder.startswith("."):
                    continue
                files = afc.listdir(f"/DCIM/{folder}/")
                for filename in sorted(files):
                    if filename.startswith("."):
                        continue
                    if Path(filename).suffix.lower() not in PHOTO_EXTENSIONS:
                        continue
                    path = f"/DCIM/{folder}/{filename}"
                    stat = afc.stat(path)
                    btime = stat.get("st_birthtime") or stat.get("st_mtime")
                    photos.append({
                        "path": path,
                        "filename": filename,
                        "size": stat.get("st_size", 0),
                        "date": btime.isoformat() if btime else "",
                    })
            _return_afc(afc)
        except Exception:
            try:
                afc.close()
            except Exception:
                pass
            raise
        with _cache_lock:
            UsbService._photo_cache = photos
        return photos

    def get_thumbnail(self, remote_path: str) -> bytes:
        remote_path = _validate_dcim_path(remote_path)

        cached = _thumb_cache.get(remote_path)
        if cached:
            return cached

        # Fast path: iOS pre-rendered thumbnail from /PhotoData/Thumbnails/V2/
        thumb_dir = f"/PhotoData/Thumbnails/V2{remote_path}/"
        try:
            afc = _get_pooled_afc()
            try:
                files = afc.listdir(thumb_dir)
                if files:
                    data = afc.read_file(f"{thumb_dir}{files[0]}")
                    _return_afc(afc)
                    _thumb_cache[remote_path] = data
                    return data
                _return_afc(afc)
            except Exception:
                try:
                    afc.close()
                except Exception:
                    pass
        except Exception:
            pass

        # Fallback: download full file
        data = _read_file(remote_path)

        is_video = Path(remote_path).suffix.lower() in VIDEO_EXTENSIONS
        if is_video:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            del data
            try:
                thumb = _video_thumbnail(tmp_path)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            if thumb:
                _thumb_cache[remote_path] = thumb
                return thumb
            raise ValueError("Could not generate video thumbnail")

        # Try EXIF thumbnail
        try:
            import piexif
            exif_dict = piexif.load(data)
            if "thumbnail" in exif_dict and exif_dict["thumbnail"]:
                _thumb_cache[remote_path] = exif_dict["thumbnail"]
                return exif_dict["thumbnail"]
        except Exception:
            pass

        # Last resort: resize full image
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((160, 120))
        if img.width < 160:
            padded = Image.new("RGB", (160, 120), (0, 0, 0))
            padded.paste(img, ((160 - img.width) // 2, (120 - img.height) // 2))
            img = padded
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        result = buf.getvalue()
        _thumb_cache[remote_path] = result
        return result

    def get_gps(self, remote_path: str) -> dict:
        remote_path = _validate_dcim_path(remote_path)
        if Path(remote_path).suffix.lower() in VIDEO_EXTENSIONS:
            return {}

        data = _read_file(remote_path)

        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        if not exif:
            return {}
        gps_ifd = exif.get_ifd(0x8825)
        if not gps_ifd:
            return {}

        def to_degrees(val):
            d, m, s = val
            return float(d) + float(m) / 60 + float(s) / 3600

        lat = to_degrees(gps_ifd.get(2, (0, 0, 0)))
        lon = to_degrees(gps_ifd.get(4, (0, 0, 0)))
        if gps_ifd.get(1) == "S":
            lat = -lat
        if gps_ifd.get(3) == "W":
            lon = -lon
        if lat == 0 and lon == 0:
            return {}
        return {"lat": lat, "lon": lon}

    def get_preview(self, remote_path: str) -> bytes:
        remote_path = _validate_dcim_path(remote_path)
        if Path(remote_path).suffix.lower() in VIDEO_EXTENSIONS:
            raise ValueError("Preview not available for videos")

        data = _read_file(remote_path)

        ext = Path(remote_path).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png"):
            return data

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def download(self, remote_path: str) -> Path:
        remote_path = _validate_dcim_path(remote_path)
        data = _read_file(remote_path)
        suffix = Path(remote_path).suffix
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    def fetch_raw(self, remote_path: str) -> bytes:
        """Download raw file bytes from device."""
        remote_path = _validate_dcim_path(remote_path)
        data = _read_file(remote_path)
        return data

    def open_session(self):
        """Open a persistent AFC connection for batch operations."""
        self._session = _new_afc()
        return self

    def close_session(self):
        """Close the persistent AFC connection."""
        if hasattr(self, "_session") and self._session:
            self._session.close()
            self._session = None

    def fetch_raw_session(self, remote_path: str) -> bytes:
        """Download raw file bytes using persistent connection (faster for batches)."""
        remote_path = _validate_dcim_path(remote_path)
        if not hasattr(self, "_session") or not self._session:
            return self.fetch_raw(remote_path)
        try:
            return self._session.read_file(remote_path)
        except Exception:
            # Reconnect on failure
            try:
                self._session.close()
            except Exception:
                pass
            self._session = _new_afc()
            return self._session.read_file(remote_path)
