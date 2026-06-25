"""iCloud photo access via pyicloud."""

import os

from flask import Blueprint, Response, jsonify, request, send_file
from photofetch.services.icloud_service import ICloudService

bp = Blueprint("icloud", __name__)


@bp.route("/login", methods=["POST"])
def login():
    """Authenticate with Apple ID."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid JSON body"}), 400
    email = data.get("email")
    password = data.get("password")
    if not email or not password:
        return jsonify({"error": "email and password required"}), 400
    try:
        result = ICloudService.login(email, password)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/verify-2fa", methods=["POST"])
def verify_2fa():
    """Submit 2FA verification code."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid JSON body"}), 400
    code = data.get("code")
    if not code:
        return jsonify({"error": "code required"}), 400
    result = ICloudService.verify_2fa(code)
    return jsonify(result)


@bp.route("/photos")
def list_photos():
    """List photos from iCloud library with pagination."""
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 100, type=int)
    limit = min(limit, 500)
    try:
        photos = ICloudService.list_photos(offset=offset, limit=limit)
        total = ICloudService.total_count()
        return jsonify({"photos": photos, "total": total, "offset": offset})
    except Exception as e:
        return jsonify({"error": str(e), "photos": []}), 500


@bp.route("/thumbnail")
def thumbnail():
    """Get JPEG thumbnail for an iCloud photo."""
    photo_id = request.args.get("id")
    if not photo_id:
        return jsonify({"error": "id parameter required"}), 400
    try:
        data = ICloudService.get_thumbnail(photo_id)
        return Response(data, mimetype="image/jpeg")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return Response(b"", status=404)


@bp.route("/download")
def download_photo():
    """Download a photo from iCloud by ID."""
    photo_id = request.args.get("id")
    if not photo_id:
        return jsonify({"error": "id parameter required"}), 400
    try:
        local_path = ICloudService.download(photo_id)
        resp = send_file(local_path, as_attachment=True, download_name=local_path.name)

        @resp.call_on_close
        def _cleanup():
            os.unlink(local_path)

        return resp
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
