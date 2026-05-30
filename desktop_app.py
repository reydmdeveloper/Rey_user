"""
═══════════════════════════════════════════════════════════════════════════
 REYDM Desktop — PyQt6 native shell for the REYDM Flask platform
═══════════════════════════════════════════════════════════════════════════

 This wraps the existing Flask + MySQL application in a native desktop window
 using PyQt6 + QtWebEngine. The Flask app is served locally by a fast,
 production-grade WSGI server (waitress) running in a background thread, and
 rendered in a real native window — no external browser required.

 Why this design:
   • Keeps 100% of the existing features (all 9 tools, auth, scheduler).
   • Fast: everything is served from 127.0.0.1, no network round-trips.
   • Efficient: single process, single thread for the server, no dev-server
     overhead, no auto-reloader.
   • Packageable: works cleanly with PyInstaller into a standalone executable.

 Rendering / scrolling performance:
   • Hardware GPU acceleration is ENABLED by default for smooth, high-FPS
     scrolling. If a machine has broken GPU drivers and shows a blank window,
     set REYDM_SOFTWARE_RENDER=1 to fall back to software rendering.

 Run:
     python desktop_app.py
═══════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import socket
import threading
import time
from urllib.request import urlopen
from urllib.error import URLError


# ─── Resolve base directory (works both in dev and inside PyInstaller) ──────
def resource_base():
    """Return the directory that holds app.py / templates / static.

    When frozen by PyInstaller (one-file mode) resources are unpacked to a
    temporary folder exposed as sys._MEIPASS. In dev it's just this file's dir.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = resource_base()
# Make sure Python can import app.py and that relative paths resolve.
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)


# Load .env if present (so DB / SMTP creds are picked up). Optional dependency.
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                # Don't override anything already set in the real environment.
                os.environ.setdefault(key, val)
    except Exception as exc:  # noqa: BLE001
        print(f"[REYDM] Could not read .env: {exc}")


load_env()

# ─── Configuration ──────────────────────────────────────────────────────────
APP_TITLE = "REYDM — REY Datamind Platform"
HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("APP_PORT", "5000"))


def find_free_port(preferred: int) -> int:
    """Use the preferred port if free, otherwise grab any free port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind((HOST, 0))
        port = s2.getsockname()[1]
        s2.close()
        return port


# ═══════════════════════════════════════════════════════════════════════════
# Flask server (background thread, production WSGI via waitress)
# ═══════════════════════════════════════════════════════════════════════════
class FlaskServerThread(threading.Thread):
    """Runs the existing Flask `app` with waitress in a daemon thread."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self._started = threading.Event()
        self.error = None

    def run(self):
        try:
            # Importing app triggers init_db() + reminder scheduler (see app.py
            # else-branch), so the desktop app gets the same startup behaviour
            # as the production server.
            from app import app as flask_app  # noqa: WPS433 (local import on purpose)

            try:
                from waitress import serve
                self._started.set()
                # threads=8 keeps API endpoints + page loads responsive.
                serve(
                    flask_app,
                    host=HOST,
                    port=self.port,
                    threads=8,
                    _quiet=True,
                )
            except ImportError:
                # Fallback: Flask's built-in server (no reloader, threaded).
                print("[REYDM] waitress not found, falling back to Flask server.")
                self._started.set()
                flask_app.run(
                    host=HOST,
                    port=self.port,
                    debug=False,
                    use_reloader=False,
                    threaded=True,
                )
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self._started.set()
            print(f"[REYDM] Server failed to start: {exc}")

    def wait_until_ready(self, timeout: float = 30.0) -> bool:
        """Block until the HTTP server actually answers, or timeout."""
        self._started.wait(timeout=timeout)
        if self.error:
            return False
        deadline = time.time() + timeout
        url = f"http://{HOST}:{self.port}/login"
        while time.time() < deadline:
            try:
                with urlopen(url, timeout=2) as resp:
                    if resp.status < 500:
                        return True
            except URLError:
                time.sleep(0.25)
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# PyQt6 window
# ═══════════════════════════════════════════════════════════════════════════
def build_window(start_url: str):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QIcon, QAction, QKeySequence
    from PyQt6.QtWidgets import QMainWindow, QMessageBox
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineSettings,
        QWebEngineDownloadRequest,
    )

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(APP_TITLE)
            self.resize(1280, 820)
            self.setMinimumSize(960, 600)

            icon_path = os.path.join(BASE_DIR, "static", "Images", "REYDM_LOGO.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))

            # Web view
            self.view = QWebEngineView(self)
            self.setCentralWidget(self.view)

            # ── Performance / scrolling settings ────────────────────────────
            # These directly improve scroll smoothness and overall FPS.
            s = self.view.settings()
            try:
                # Animate scrolling instead of jumping — feels smoother and the
                # compositor can keep up at higher frame rates.
                s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
                # Keep accelerated 2D canvas / WebGL on (GPU compositing path).
                s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
                s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
                # Ensure scrollbars are shown.
                s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
            except Exception:  # noqa: BLE001
                # Attribute names are stable in 6.7, but guard just in case.
                pass

            # Handle file downloads (CSV exports, unlocked PDFs, etc.)
            profile = self.view.page().profile()
            profile.downloadRequested.connect(self._on_download)

            self.view.load(QUrl(start_url))

            self._build_shortcuts()

        def _build_shortcuts(self):
            # Reload (F5 / Ctrl+R)
            reload_act = QAction(self)
            reload_act.setShortcuts([QKeySequence("F5"), QKeySequence("Ctrl+R")])
            reload_act.triggered.connect(self.view.reload)
            self.addAction(reload_act)

            # Hard Reload (Ctrl+F5 / Ctrl+Shift+R)
            from PyQt6.QtWebEngineCore import QWebEnginePage
            hard_reload_act = QAction(self)
            hard_reload_act.setShortcuts([QKeySequence("Ctrl+F5"), QKeySequence("Ctrl+Shift+R")])
            hard_reload_act.triggered.connect(
                lambda: self.view.triggerPageAction(QWebEnginePage.WebAction.ReloadAndBypassCache)
            )
            self.addAction(hard_reload_act)

            # Back / Forward
            back_act = QAction(self)
            back_act.setShortcut(QKeySequence("Alt+Left"))
            back_act.triggered.connect(self.view.back)
            self.addAction(back_act)

            fwd_act = QAction(self)
            fwd_act.setShortcut(QKeySequence("Alt+Right"))
            fwd_act.triggered.connect(self.view.forward)
            self.addAction(fwd_act)

            # Zoom
            zoom_in = QAction(self)
            zoom_in.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
            zoom_in.triggered.connect(
                lambda: self.view.setZoomFactor(self.view.zoomFactor() + 0.1)
            )
            self.addAction(zoom_in)

            zoom_out = QAction(self)
            zoom_out.setShortcut(QKeySequence("Ctrl+-"))
            zoom_out.triggered.connect(
                lambda: self.view.setZoomFactor(max(0.4, self.view.zoomFactor() - 0.1))
            )
            self.addAction(zoom_out)

            zoom_reset = QAction(self)
            zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
            zoom_reset.triggered.connect(lambda: self.view.setZoomFactor(1.0))
            self.addAction(zoom_reset)

        def _on_download(self, item: "QWebEngineDownloadRequest"):
            from PyQt6.QtWidgets import QFileDialog

            suggested = item.downloadFileName() or "download"
            target, _ = QFileDialog.getSaveFileName(
                self, "Save File", os.path.join(os.path.expanduser("~"), suggested)
            )
            if not target:
                item.cancel()
                return
            item.setDownloadDirectory(os.path.dirname(target))
            item.setDownloadFileName(os.path.basename(target))
            item.accept()
            item.isFinishedChanged.connect(
                lambda: self._download_done(item, target)
            )

        def _download_done(self, item, target):
            if item.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                QMessageBox.information(self, "Download complete", f"Saved to:\n{target}")

    return MainWindow()


# ═══════════════════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════════════════
def main():
    # ── Rendering setup (must happen BEFORE QApplication is created) ─────────
    #
    # SCROLLING / FPS:
    #   Hardware GPU acceleration is ON by default. This is what gives smooth,
    #   high-FPS scrolling. The previous default disabled the GPU, which forced
    #   slow CPU-only software compositing and caused scroll lag.
    #
    #   The flags below explicitly enable the GPU compositing + rasterization
    #   path and smooth-scrolling inside Chromium.
    #
    #   If a machine has broken GPU drivers and shows a BLANK window, run with
    #   the environment variable REYDM_SOFTWARE_RENDER=1 to fall back to the
    #   software renderer (slower scrolling, but always displays).
    software_render = os.environ.get("REYDM_SOFTWARE_RENDER", "0") == "1"
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")

    if software_render:
        flags = "--disable-gpu --no-sandbox"
    else:
        # GPU-accelerated, smooth-scrolling configuration.
        flags = (
            "--ignore-gpu-blocklist "          # use the GPU even if Chromium is unsure
            "--enable-gpu-rasterization "      # rasterize on the GPU
            "--enable-zero-copy "              # fewer CPU<->GPU copies while scrolling
            "--enable-smooth-scrolling "       # animated, high-FPS scrolling
            "--num-raster-threads=4 "          # parallel raster for big pages
            "--no-sandbox"                     # avoids startup failure on locked-down PCs
        )

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (existing + " " + flags).strip()

    from PyQt6.QtCore import QCoreApplication, Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox

    # Required for QtWebEngine's GPU compositing; must be set before QApplication.
    try:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception:  # noqa: BLE001
        pass

    port = find_free_port(DEFAULT_PORT)

    server = FlaskServerThread(port)
    server.start()

    app = QApplication(sys.argv)
    app.setApplicationName("REYDM")

    ready = server.wait_until_ready(timeout=40)
    if not ready:
        msg = (
            "REYDM could not start its local server.\n\n"
            "Most often this is a database connection problem — check your\n"
            "internet connection and the DB settings in your .env file."
        )
        if server.error:
            msg += f"\n\nDetails: {server.error}"
        QMessageBox.critical(None, "REYDM — Startup Error", msg)
        return 1

    start_url = f"http://{HOST}:{port}/"
    window = build_window(start_url)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())