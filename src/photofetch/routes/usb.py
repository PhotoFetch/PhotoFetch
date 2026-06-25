"""USB photo access via pymobiledevice3 AFC."""

import os
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file
from photofetch.services.usb_service import UsbService

bp = Blueprint("usb", __name__)


@bp.route("/status")
def status():
    """Check if an iPhone is connected via USB."""
    svc = UsbService()
    connected = svc.is_connected()
    return jsonify({"connected": connected})


@bp.route("/photos")
def list_photos():
    """List all photos on connected device (cached after first scan)."""
    svc = UsbService()
    try:
        photos = svc.list_photos()
        return jsonify({"photos": photos})
    except Exception as e:
        code = str(e)
        if code not in ("NO_DEVICE", "NOT_PAIRED", "SESSION_FAILED"):
            code = "CONNECTION_LOST"
        return jsonify({"error": code, "errorCode": code, "photos": []}), 500


@bp.route("/refresh", methods=["POST"])
def refresh_cache():
    """Clear photo list cache and rescan."""
    UsbService.clear_cache()
    return jsonify({"status": "ok"})


@bp.route("/thumbnail")
def thumbnail():
    """Get JPEG thumbnail for a photo."""
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path parameter required"}), 400
    try:
        svc = UsbService()
        data = svc.get_thumbnail(path)
        return Response(data, mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return Response(b"", status=404)


@bp.route("/download")
def download_photo():
    """Download a single photo by path."""
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path parameter required"}), 400
    try:
        svc = UsbService()
        local_path = svc.download(path)
        original_name = Path(path).name
        resp = send_file(local_path, as_attachment=True, download_name=original_name)

        @resp.call_on_close
        def _cleanup():
            os.unlink(local_path)

        return resp
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/preview")
def preview_photo():
    """Get a browser-viewable version (HEIC converted to JPEG)."""
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path parameter required"}), 400
    try:
        svc = UsbService()
        data = svc.get_preview(path)
        return Response(data, mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return Response(b"", status=404)


@bp.route("/exif")
def get_exif():
    """Get EXIF GPS data for a photo."""
    path = request.args.get("path")
    if not path:
        return jsonify({"error": "path parameter required"}), 400
    try:
        svc = UsbService()
        gps = svc.get_gps(path)
        return jsonify(gps)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({})
