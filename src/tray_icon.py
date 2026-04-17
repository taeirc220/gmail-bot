"""
tray_icon.py — Windows system tray icon for Gmail Bot.

Provides a persistent tray icon (bottom-right system tray) that:
  - Shows bot status at a glance via icon colour (green/yellow/red)
  - Opens the dashboard on left-click or "Open Dashboard" menu item
  - Lets the user pause/resume polling without touching the terminal
  - Provides a clean "Quit" option that shuts down the bot gracefully

Uses pystray (tray icon framework) + Pillow (draws icon image in-process).
No .ico file required — icon is generated programmatically.

Must be run in its own thread (pystray's run loop is blocking).
Communicate status changes via set_status() from any thread.
"""

import logging
import threading
import webbrowser
from typing import Callable

from PIL import Image, ImageDraw

try:
    import pystray
    _PYSTRAY_AVAILABLE = True
except ImportError:
    _PYSTRAY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Status colours (Google Material palette)
_COLOURS = {
    "running": "#34a853",   # green
    "paused":  "#f29900",   # amber
    "error":   "#ea4335",   # red
}

_TITLES = {
    "running": "Gmailbot — Running",
    "paused":  "Gmailbot — Paused",
    "error":   "Gmailbot — Error",
}


def _make_icon_image(colour: str) -> "Image.Image":
    """Draw a 64×64 coloured circle on a transparent background."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=colour,
        outline="#ffffff",
        width=2,
    )
    return img


class TrayIcon:
    """
    Windows system tray icon.

    Usage (from main.py):
        pause_event = threading.Event()
        stop_event  = threading.Event()
        tray = TrayIcon(secret, port, pause_event, stop_event)
        threading.Thread(target=tray.start, daemon=True).start()
        # Later, to reflect an error:
        tray.set_status("error")
    """

    def __init__(
        self,
        secret: str,
        port: int,
        pause_event: threading.Event,
        stop_event: threading.Event,
    ) -> None:
        self._secret = secret
        self._port = port
        self._pause_event = pause_event
        self._stop_event = stop_event
        self._icon: "pystray.Icon | None" = None
        self._status = "running"
        self._window = None   # set later via set_window() once pywebview creates it

    def set_window(self, window) -> None:
        """Attach the pywebview window so the tray can show/hide/destroy it."""
        self._window = window

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Blocking — run in a dedicated daemon thread."""
        if not _PYSTRAY_AVAILABLE:
            logger.warning("pystray not available — tray icon disabled")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem(
                    "Gmailbot",
                    None,
                    enabled=False,          # status header — non-clickable
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Open Dashboard",
                    self._open_dashboard,
                    default=True,           # triggered on left-click too
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Pause Polling",
                    self._toggle_pause,
                    checked=lambda item: self._pause_event.is_set(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit Gmailbot", self._quit),
            )

            self._icon = pystray.Icon(
                name="GmailBot",
                icon=_make_icon_image(_COLOURS["running"]),
                title=_TITLES["running"],
                menu=menu,
            )

            logger.info("Tray icon starting")
            self._icon.run()
        except Exception as exc:
            logger.error("Tray icon failed to start: %s", exc)

    def set_status(self, status: str) -> None:
        """
        Update the tray icon colour and tooltip.
        status: 'running' | 'paused' | 'error'
        Safe to call from any thread.
        """
        if not _PYSTRAY_AVAILABLE or self._icon is None:
            return
        if status not in _COLOURS:
            return
        self._status = status
        try:
            self._icon.icon  = _make_icon_image(_COLOURS[status])
            self._icon.title = _TITLES[status]
        except Exception as exc:
            logger.debug("Could not update tray icon status: %s", exc)

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def _open_dashboard(self, icon=None, item=None) -> None:
        if self._window is not None:
            try:
                self._window.show()
                logger.info("Dashboard window shown from tray")
                return
            except Exception as exc:
                logger.debug("window.show() failed (%s) — falling back to browser", exc)
        url = f"http://localhost:{self._port}/?token={self._secret}"
        webbrowser.open(url)
        logger.info("Dashboard opened in browser from tray")

    def _toggle_pause(self, icon=None, item=None) -> None:
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.set_status("running")
            logger.info("Polling resumed via tray icon")
        else:
            self._pause_event.set()
            self.set_status("paused")
            logger.info("Polling paused via tray icon")

    def _quit(self, icon=None, item=None) -> None:
        logger.info("Quit requested via tray icon")
        self._stop_event.set()
        if self._window is not None:
            try:
                self._window.destroy()   # unblocks webview.start() on main thread
            except Exception as exc:
                logger.debug("window.destroy() failed: %s", exc)
        if self._icon is not None:
            self._icon.stop()
