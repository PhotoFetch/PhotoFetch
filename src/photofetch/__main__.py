"""PhotoFetch entry point — starts Flask in background, shows native window."""

import logging
import socket
import threading
import time
import webbrowser

from photofetch.app import create_app

HOST = "localhost"
PORT = 8080


def _start_server(app):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)


def _wait_for_server():
    """Block until Flask is accepting connections."""
    for _ in range(50):
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def main():
    app = create_app()
    threading.Thread(target=_start_server, args=(app,), daemon=True).start()
    _wait_for_server()

    url = f"http://{HOST}:{PORT}"

    try:
        import webview
        win = webview.create_window("PhotoFetch", url, width=1280, height=800)
        webview.start(private_mode=False, storage_path="/tmp/photofetch-webview")
    except Exception:
        print(f"PhotoFetch running at {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
