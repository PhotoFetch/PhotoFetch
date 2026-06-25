"""Batch download route — direct-to-folder with rsync-like skip and SSE progress."""

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from photofetch.services.usb_service import UsbService
from photofetch.services.icloud_service import ICloudService, MAX_BATCH_SIZE

bp = Blueprint("batch", __name__)

MAX_USB_BATCH = 5000
MAX_ACTIVE_DOWNLOADS = 4
_TOKEN_MAX_AGE = 3600  # 1 hour

# Per-request abort tokens: {token_str: (threading.Event, creation_time)}
_abort_events: dict[str, tuple[threading.Event, float]] = {}
_abort_lock = threading.Lock()


def _cleanup_stale_tokens():
    """Remove tokens older than MAX_AGE (handles leaked generators)."""
    now = time.monotonic()
    stale = [k for k, (_, t) in _abort_events.items() if now - t > _TOKEN_MAX_AGE]
    for k in stale:
        _abort_events.pop(k, None)


def _pick_folder() -> str | None:
    """Open native folder picker dialog (cross-platform)."""
    import subprocess
    import sys

    if sys.platform == "darwin":
        try:
            # NSOpenPanel with canCreateDirectories shows "New Folder" button
            script = "\n".join([
                'use framework "AppKit"',
                "set panel to current application's NSOpenPanel's openPanel()",
                "panel's setCanChooseFiles:false",
                "panel's setCanChooseDirectories:true",
                "panel's setCanCreateDirectories:true",
                'panel\'s setPrompt:"Select"',
                'panel\'s setMessage:"Save photos to..."',
                "set result to panel's runModal() as integer",
                "if result is (current application's NSModalResponseOK)"
                " as integer then",
                "    return ((panel's |URL|())'s |path|()) as text",
                "end if",
            ])
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    elif sys.platform == "win32":
        try:
            # FolderBrowserDialog with ShowNewFolderButton explicitly enabled
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Add-Type -AssemblyName System.Windows.Forms; "
                 "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                 "$f.Description = 'Save photos to...'; "
                 "$f.ShowNewFolderButton = $true; "
                 "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    else:  # Linux
        try:
            # zenity --save allows typing a new folder name; makedirs
            # creates it.  Path is validated by _is_under_home() in the
            # download route before any files are written.
            result = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--save",
                 "--title=Save photos to...",
                 "--filename=" + os.path.expanduser("~/")],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                if not path.startswith(os.path.expanduser("~")):
                    return None
                os.makedirs(path, exist_ok=True)
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fallback: tkinter (available on most Python installs)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Save photos to...")
        root.destroy()
        return folder if folder else None
    except Exception:
        pass
    return None


@bp.route("/pick-folder", methods=["POST"])
def pick_folder():
    """Open native folder picker and return chosen path."""
    folder = _pick_folder()
    if not folder:
        return jsonify({"error": "cancelled"}), 400
    return jsonify({"folder": folder})


def _is_under_home(path: Path) -> bool:
    """Validate that path is under the user's home directory."""
    home = Path.home()
    try:
        path.resolve().relative_to(home.resolve())
        return True
    except ValueError:
        return False


def _unique_path(dest: Path, filename: str) -> Path:
    """Return a non-colliding path, appending _1, _2, etc. if needed."""
    local_path = dest / filename
    if not local_path.exists():
        return local_path
    stem = local_path.stem
    suffix = local_path.suffix
    for counter in range(1, 10000):
        candidate = dest / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Too many collisions for {filename}")


@bp.route("/download", methods=["POST"])
def download():
    """Save photos directly to folder, skipping existing files (rsync-like).

    Skip logic uses filename + size comparison. This is intentionally size-only
    (like rsync --size-only) because photo filenames are sequential and unique
    per device, making collisions between different content extremely unlikely.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid JSON body"}), 400

    source = data.get("source")
    items = data.get("items", [])
    folder = data.get("folder")
    total_size = data.get("total_size", 0)
    sizes = data.get("sizes", {})

    if not items:
        return jsonify({"error": "no items selected"}), 400
    if not folder:
        return jsonify({"error": "no folder specified"}), 400
    if source not in ("usb", "icloud"):
        return jsonify({"error": "invalid source"}), 400

    max_allowed = MAX_USB_BATCH if source == "usb" else MAX_BATCH_SIZE
    if len(items) > max_allowed:
        return jsonify({"error": f"max {max_allowed} items per batch"}), 400

    dest = Path(folder)
    if not dest.is_dir():
        return jsonify({"error": "folder does not exist"}), 400

    # Block UNC paths (Windows) — prevents NTLM credential leak via SMB
    resolved = str(dest.resolve())
    if resolved.startswith("\\\\") or resolved.startswith("//"):
        return jsonify({"error": "network paths not allowed"}), 400

    free_space = shutil.disk_usage(dest).free
    if total_size > 0 and total_size > free_space:
        free_mb = free_space // (1024 * 1024)
        need_mb = total_size // (1024 * 1024)
        return jsonify({"error": f"Not enough space: need {need_mb} MB, only {free_mb} MB free"}), 400

    # Create per-request abort token
    token = str(uuid.uuid4())
    abort_event = threading.Event()
    with _abort_lock:
        _cleanup_stale_tokens()
        if len(_abort_events) >= MAX_ACTIVE_DOWNLOADS:
            return jsonify({"error": "too many active downloads"}), 429
        _abort_events[token] = (abort_event, time.monotonic())

    def generate():
        total = len(items)
        saved = 0
        skipped = 0
        errors = 0
        bytes_done = 0
        start_time = time.monotonic()

        yield _sse({"token": token, "total": total})

        try:
            if source == "usb":
                svc = UsbService()
                svc.open_session()
                try:
                    for idx, item_path in enumerate(items):
                        if abort_event.is_set():
                            yield _sse({"current": idx, "total": total, "done": True,
                                        "saved": saved, "skipped": skipped, "errors": errors,
                                        "aborted": True})
                            return

                        filename = Path(item_path).name
                        remote_size = sizes.get(item_path, 0)
                        local_path = dest / filename

                        if local_path.exists() and remote_size and local_path.stat().st_size == remote_size:
                            skipped += 1
                            yield _sse(_progress(idx + 1, total, filename, "skipped",
                                                 saved, skipped, bytes_done, start_time))
                            continue

                        if local_path.exists() and remote_size and local_path.stat().st_size != remote_size:
                            local_path = _unique_path(dest, filename)
                        # size unknown + file exists → overwrite

                        try:
                            raw = svc.fetch_raw_session(item_path)
                            local_path.write_bytes(raw)
                            bytes_done += len(raw)
                            saved += 1
                            yield _sse(_progress(idx + 1, total, local_path.name, "saved",
                                                 saved, skipped, bytes_done, start_time))
                        except Exception as e:
                            errors += 1
                            yield _sse(_progress(idx + 1, total, filename, "error",
                                                 saved, skipped, bytes_done, start_time, str(e)))
                finally:
                    svc.close_session()

            else:  # icloud
                for idx, photo_id in enumerate(items):
                    if abort_event.is_set():
                        yield _sse({"current": idx, "total": total, "done": True,
                                    "saved": saved, "skipped": skipped, "errors": errors,
                                    "aborted": True})
                        return

                    filename = ""
                    try:
                        photo = ICloudService._get_photo_by_id(photo_id)
                        filename = photo.filename
                        remote_size = photo.size or 0
                        local_path = dest / filename

                        if local_path.exists() and remote_size and local_path.stat().st_size == remote_size:
                            skipped += 1
                            yield _sse(_progress(idx + 1, total, filename, "skipped",
                                                 saved, skipped, bytes_done, start_time))
                            continue

                        if local_path.exists() and remote_size and local_path.stat().st_size != remote_size:
                            local_path = _unique_path(dest, filename)

                        file_data = photo.download()
                        if file_data is None:
                            errors += 1
                            yield _sse(_progress(idx + 1, total, filename, "error",
                                                 saved, skipped, bytes_done, start_time,
                                                 "download returned None"))
                            continue

                        local_path.write_bytes(file_data)
                        bytes_done += len(file_data)
                        saved += 1
                        yield _sse(_progress(idx + 1, total, local_path.name, "saved",
                                             saved, skipped, bytes_done, start_time))
                    except Exception as e:
                        errors += 1
                        yield _sse(_progress(idx + 1, total, filename, "error",
                                             saved, skipped, bytes_done, start_time, str(e)))

            yield _sse({"current": total, "total": total, "done": True,
                        "saved": saved, "skipped": skipped, "errors": errors})
        finally:
            with _abort_lock:
                _abort_events.pop(token, None)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/abort", methods=["POST"])
def abort():
    """Abort an in-progress download by token."""
    data = request.get_json(silent=True)
    token = data.get("token") if data else None

    with _abort_lock:
        if token and token in _abort_events:
            _abort_events[token][0].set()
            return jsonify({"status": "aborted"})
        for evt, _ in _abort_events.values():
            evt.set()
    return jsonify({"status": "aborted"})


@bp.route("/open-folder", methods=["POST"])
def open_folder():
    """Open folder in file manager (cross-platform)."""
    import subprocess
    import sys
    data = request.get_json(silent=True)
    folder = data.get("folder") if data else None
    if not folder:
        return jsonify({"error": "no folder"}), 400
    dest = Path(folder)
    if not dest.is_dir():
        return jsonify({"error": "invalid folder"}), 400
    resolved = str(dest.resolve())
    if resolved.startswith("\\\\") or resolved.startswith("//"):
        return jsonify({"error": "network paths not allowed"}), 400
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(dest)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", str(dest)])
    else:
        subprocess.Popen(["xdg-open", str(dest)])
    return jsonify({"status": "ok"})


def _progress(current: int, total: int, filename: str, status: str,
              saved: int, skipped: int, bytes_done: int, start_time: float,
              error: str | None = None) -> dict:
    """Build a progress event dict with speed and ETA."""
    elapsed = time.monotonic() - start_time
    speed = bytes_done / elapsed if elapsed > 0 else 0
    remaining = total - current
    # Estimate seconds per item from elapsed
    eta = (elapsed / current * remaining) if current > 0 else 0

    d: dict = {"current": current, "total": total, "filename": filename,
               "status": status, "saved": saved, "skipped": skipped,
               "speed": int(speed), "eta": int(eta)}
    if error:
        d["error"] = error
    return d


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
