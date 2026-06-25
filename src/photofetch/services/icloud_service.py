"""iCloud service — photo access via pyicloud."""

import tempfile
import threading
from pathlib import Path

MAX_BATCH_SIZE = 200
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}


class ICloudService:
    _lock = threading.Lock()
    _api = None
    _photo_cache: list | None = None

    @classmethod
    def login(cls, email: str, password: str) -> dict:
        from pyicloud import PyiCloudService

        api = PyiCloudService(email, password)
        with cls._lock:
            cls._api = api
            cls._photo_cache = None
        if cls._api.requires_2fa:
            return {"status": "2fa_required"}
        return {"status": "ok"}

    @classmethod
    def verify_2fa(cls, code: str) -> dict:
        with cls._lock:
            if not cls._api:
                return {"error": "not logged in"}
            result = cls._api.validate_2fa_code(code)
        if result:
            return {"status": "ok"}
        return {"error": "invalid code"}

    @classmethod
    def _get_photos(cls) -> list:
        with cls._lock:
            if not cls._api:
                raise RuntimeError("Not logged in")
            if cls._photo_cache is not None:
                return cls._photo_cache

        # Fetch outside lock to avoid blocking other threads
        photos = list(cls._api.photos.all)
        with cls._lock:
            cls._photo_cache = photos
        return photos

    @classmethod
    def _get_photo_by_id(cls, photo_id: str):
        """Get a photo by ID with bounds checking."""
        idx = int(photo_id)
        photos = cls._get_photos()
        if idx < 0 or idx >= len(photos):
            raise ValueError(f"Invalid photo ID: {photo_id}")
        return photos[idx]

    @classmethod
    def list_photos(cls, offset: int = 0, limit: int = 100) -> list[dict]:
        if not cls._api:
            return []
        all_photos = cls._get_photos()
        page = all_photos[offset:offset + limit]
        photos = []
        for i, photo in enumerate(page, start=offset):
            photos.append({
                "id": str(i),
                "filename": photo.filename,
                "date": str(photo.asset_date) if photo.asset_date else "",
                "size": photo.size or 0,
            })
        return photos

    @classmethod
    def total_count(cls) -> int:
        if not cls._api:
            return 0
        return len(cls._get_photos())

    @classmethod
    def get_thumbnail(cls, photo_id: str) -> bytes:
        """Download server-side thumbnail."""
        photo = cls._get_photo_by_id(photo_id)

        ext = Path(photo.filename).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            version = "thumb_image"
        else:
            version = "thumb"

        data = photo.download(version)
        if data is None:
            data = photo.download("medium" if ext not in VIDEO_EXTENSIONS else "medium_image")
        if data is None:
            raise ValueError("No thumbnail available")
        return data

    @classmethod
    def download(cls, photo_id: str) -> Path:
        photo = cls._get_photo_by_id(photo_id)
        data = photo.download()
        if data is None:
            raise RuntimeError("Download failed")
        suffix = Path(photo.filename).suffix
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    @classmethod
    def download_multiple(cls, photo_ids: list[str], dest_dir: Path) -> list[Path]:
        """Download multiple iCloud photos to a destination directory."""
        if len(photo_ids) > MAX_BATCH_SIZE:
            raise ValueError(f"Batch size exceeds maximum of {MAX_BATCH_SIZE}")
        downloaded = []
        for pid in photo_ids:
            photo = cls._get_photo_by_id(pid)
            data = photo.download()
            if data is None:
                continue
            local_path = dest_dir / photo.filename
            local_path.write_bytes(data)
            downloaded.append(local_path)
        return downloaded
