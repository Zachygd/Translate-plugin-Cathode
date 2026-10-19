#!/usr/bin/env python3
"""Cathode Translate - local on-screen OCR translation for KDE Plasma.

Part of the Cathode theme project: https://github.com/Zachygd/Translate-plugin-Cathode

Grab any area of the screen, and a local vision model running in Ollama
reads and translates it. Nothing leaves your computer.

Tray icon:   teal screen + green LED = model loaded
             black screen + dim LED  = service running, nothing loaded
             black screen + red LED  = service off
Hover:       loaded model, VRAM, time until unload, GPU stats
Left-click:  translate a screen region
Right-click: translate, show window, load/unload model, start/stop service

Usage:
  cathode-translate              start the tray app
  cathode-translate --translate  start a capture (bind this to a hotkey)
  cathode-translate --show       show the translation window
If the app is already running, --translate / --show are passed to it.

Settings: ~/.config/cathode-translate/config.ini
"""
import base64
import configparser
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

APP_ID = "cathode-translate"
CONFIG_FILE = os.path.expanduser(f"~/.config/{APP_ID}/config.ini")
DATA_DIR = os.path.expanduser(f"~/.local/share/{APP_ID}")
HIST_FILE = os.path.join(DATA_DIR, "history.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
SOCK = os.path.join(RUNTIME, f"{APP_ID}.sock")
SHOT = os.path.join(RUNTIME, f"{APP_ID}-shot.png")   # RAM-backed, overwritten
POLL_MS = 3000
HIST_MAX = 200

DEFAULTS = {
    "model": "qwen3-vl:4b-instruct",
    "target_language": "English",
    "keep_alive": "1h",
    "max_image_side": "1280",
    "ollama_url": "http://localhost:11434",
    "ollama_service": "ollama",
    "prompt": "",
}


def load_config():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["translate"] = DEFAULTS
    try:
        cfg.read(CONFIG_FILE)
    except configparser.Error:
        pass
    return cfg["translate"]


_conf = load_config()
API = _conf.get("ollama_url").rstrip("/")
MODEL = _conf.get("model")
KEEP_ALIVE = _conf.get("keep_alive")
SERVICE = _conf.get("ollama_service")
TARGET = _conf.get("target_language")
try:
    MAX_SIDE = int(_conf.get("max_image_side"))   # shrink big grabs for speed
except ValueError:
    MAX_SIDE = 1280
PROMPT = _conf.get("prompt").strip() or (
    f"Translate all text in this image into {TARGET}. "
    f"Output only the {TARGET} translation, nothing else.")


def migrate_old_data():
    """Bring over history from the pre-release 'ollama-tray' version."""
    old = os.path.expanduser("~/.local/share/ollama-tray/history.json")
    if os.path.exists(old) and not os.path.exists(HIST_FILE):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.replace(old, HIST_FILE)
        except OSError:
            pass


def send_to_running(cmd):
    """Pass a command to an already-running tray. Returns True if one exists."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(SOCK)
        s.sendall(cmd.encode() + b"\n")
        s.close()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    _cmd = "translate" if "--translate" in sys.argv else (
        "show" if "--show" in sys.argv else "ping")
    if send_to_running(_cmd):
        sys.exit(0)
    # Run under XWayland so the popup can stay on top and be positioned/moved.
    os.environ["QT_QPA_PLATFORM"] = os.environ.get("OLLAMA_TRAY_PLATFORM", "xcb")

from PyQt6.QtCore import (QBuffer, QByteArray, QEvent, QFileSystemWatcher,
                          QIODevice, QProcess, QSettings, QSize, Qt, QTimer,
                          QUrl)
from PyQt6.QtGui import (QAction, QColor, QGuiApplication, QIcon,
                         QImage, QLinearGradient, QPainter, QPalette, QPixmap)
from PyQt6.QtNetwork import (QLocalServer, QNetworkAccessManager,
                             QNetworkReply, QNetworkRequest)
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMenu, QPlainTextEdit,
                             QPushButton, QSizeGrip, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

# Classic Windows 98 palette
FACE, LIGHT, HILITE = "#c0c0c0", "#dfdfdf", "#ffffff"
SHADOW, DARK = "#808080", "#000000"
TEAL, NAVY = "#008080", "#000080"


# ---------------------------------------------------------------- tray icon
def draw_icon(state, scale):
    """Draw a 16x16 pixel-art CRT monitor, scaled by an integer factor."""
    pm = QPixmap(16 * scale, 16 * scale)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.scale(scale, scale)

    def px(x, y, w, h, c):
        p.fillRect(x, y, w, h, QColor(c))

    px(1, 1, 14, 11, FACE)
    px(1, 1, 14, 1, HILITE)
    px(1, 1, 1, 11, HILITE)
    px(2, 10, 12, 1, SHADOW)
    px(13, 2, 1, 9, SHADOW)
    px(1, 11, 14, 1, DARK)
    px(14, 1, 1, 11, DARK)

    px(3, 3, 10, 1, SHADOW)
    px(3, 3, 1, 7, SHADOW)
    px(3, 9, 10, 1, HILITE)
    px(12, 3, 1, 7, HILITE)
    if state == "loaded":
        px(4, 4, 8, 5, TEAL)
        px(4, 4, 8, 1, NAVY)
        px(10, 4, 1, 1, FACE)
        px(5, 6, 4, 1, HILITE)
        px(5, 7, 3, 1, HILITE)
    else:
        px(4, 4, 8, 5, DARK)
        if state == "idle":
            px(5, 7, 2, 1, "#00a000")

    px(6, 12, 4, 1, SHADOW)
    px(3, 13, 10, 2, FACE)
    px(3, 13, 10, 1, LIGHT)
    px(3, 15, 10, 1, DARK)
    px(12, 13, 1, 3, DARK)

    led = {"loaded": "#00ff00", "idle": "#007000", "off": "#ff0000"}[state]
    px(9, 14, 2, 1, led)
    p.end()
    return pm


def make_icon(state):
    icon = QIcon()
    for s in (1, 2, 3, 4):
        icon.addPixmap(draw_icon(state, s))
    return icon


def get_json(path):
    with urllib.request.urlopen(API + path, timeout=1) as r:
        return json.loads(r.read() or b"{}")


def gpu_stats():
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2).stdout.strip()
        used, total, temp, util = [v.strip() for v in out.split(",")]
        return (f"GPU: {int(used) / 1024:.1f} / {int(total) / 1024:.1f} GB used, "
                f"{temp}°C, {util}% busy")
    except Exception:
        return None


def time_left(expires):
    m = re.match(r"(.*T\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)$", expires or "")
    if not m:
        return None
    frac = (m.group(2) or ".0")[:7]
    tz = "+00:00" if m.group(3) == "Z" else m.group(3)
    try:
        t = datetime.fromisoformat(m.group(1) + frac + tz)
    except ValueError:
        return None
    mins = int((t - datetime.now(timezone.utc)).total_seconds() // 60)
    if mins > 100000:
        return "stays loaded"
    if mins < 1:
        return "unloads in <1 min"
    return f"unloads in {mins} min"


def run_bg(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def fmt_time(iso, with_date=False):
    t = datetime.fromisoformat(iso)
    s = t.strftime("%I:%M:%S %p").lstrip("0")
    if with_date:
        s = t.strftime("%b %d, ") + s
    return s


# ------------------------------------------------------------ 98 popup window
KDEGLOBALS = os.path.expanduser("~/.config/kdeglobals")


def _kde_color(cfg, section, key):
    try:
        r, g, b = [int(v) for v in cfg[section][key].split(",")[:3]]
        return QColor(r, g, b)
    except Exception:
        return None


def theme_colors():
    """Colors from the active KDE color scheme (e.g. a Cathode variant)."""
    pal = QApplication.palette()
    R = QPalette.ColorRole
    c = {
        "face": pal.color(R.Window), "text": pal.color(R.WindowText),
        "btn": pal.color(R.Button), "btntext": pal.color(R.ButtonText),
        "hilite": pal.color(R.Light), "light": pal.color(R.Midlight),
        "shadow": pal.color(R.Dark), "dark": pal.color(R.Shadow),
        "base": pal.color(R.Base), "basetext": pal.color(R.Text),
        "sel": pal.color(R.Highlight), "seltext": pal.color(R.HighlightedText),
        "disabled": pal.color(QPalette.ColorGroup.Disabled, R.ButtonText),
    }
    # Title bar: KDE's window-decoration colors, with the gradient "blend"
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.optionxform = str
    try:
        cfg.read(KDEGLOBALS)
    except Exception:
        pass
    c["title1"] = _kde_color(cfg, "WM", "activeBackground") or c["sel"]
    c["title2"] = (_kde_color(cfg, "WM", "activeBlend")
                   or c["title1"].lighter(150))
    c["titletext"] = _kde_color(cfg, "WM", "activeForeground") or c["seltext"]
    return c


def build_style(c):
    h = {k: v.name() for k, v in c.items()}
    out = (f"border-top:1px solid {h['hilite']}; border-left:1px solid {h['hilite']};"
           f"border-right:1px solid {h['dark']}; border-bottom:1px solid {h['dark']};")
    inn = (f"border-top:1px solid {h['shadow']}; border-left:1px solid {h['shadow']};"
           f"border-right:1px solid {h['hilite']}; border-bottom:1px solid {h['hilite']};")
    return f"""
QWidget#popup {{ background:{h['face']}; }}
QLabel {{ color:{h['text']}; background:transparent; }}
QLabel#status {{ padding:1px 2px; }}
QLabel#title {{ color:{h['titletext']}; font-weight:bold; }}
QPushButton {{ background:{h['btn']}; color:{h['btntext']}; {out}
               padding:2px 10px; min-width:44px; }}
QPushButton:pressed, QPushButton:checked {{ {inn} background:{h['light']}; }}
QPushButton:disabled {{ color:{h['disabled']}; }}
QPushButton#close {{ min-width:0; padding:0; font-weight:bold; }}
QPlainTextEdit, QListWidget {{ background:{h['base']}; color:{h['basetext']}; {inn}
               selection-background-color:{h['sel']}; selection-color:{h['seltext']}; }}
QListWidget::item {{ color:{h['basetext']}; padding:1px 2px; }}
QListWidget::item:selected {{ background:{h['sel']}; color:{h['seltext']}; }}
QScrollBar:vertical {{ background:{h['light']}; width:14px; margin:0; }}
QScrollBar::handle:vertical {{ background:{h['btn']}; {out} min-height:20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:{h['light']}; }}
"""


class TitleBar(QWidget):
    """Navy gradient title bar; dragging it moves the window."""

    def __init__(self, popup):
        super().__init__(popup)
        self.popup = popup
        self.setFixedHeight(20)
        self._drag = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 2, 2, 2)
        lay.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(draw_icon("loaded", 1))
        title = QLabel("Translation")
        title.setObjectName("title")
        close = QPushButton("x")
        close.setObjectName("close")
        close.setFixedSize(16, 14)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(popup.hide)
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addStretch()
        lay.addWidget(close)

    def paintEvent(self, e):
        p = QPainter(self)
        c = self.popup.colors
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, c["title1"])
        g.setColorAt(1, c["title2"])
        p.fillRect(self.rect(), g)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.globalPosition().toPoint()
                          - self.popup.frameGeometry().topLeft())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.popup.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.popup.save_geometry()


class Popup(QWidget):
    HIST_H = 150

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("popup")
        self.colors = theme_colors()
        self.setStyleSheet(build_style(self.colors))
        self.setWindowTitle("Translation")
        self.settings = QSettings(APP_ID, "popup")
        self.history = self.load_history()
        self.latest = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self))

        body = QVBoxLayout()
        body.setContentsMargins(4, 4, 4, 2)
        body.setSpacing(4)
        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        body.addWidget(self.status)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        f = self.text.font()
        f.setPointSize(f.pointSize() + 1)
        self.text.setFont(f)
        body.addWidget(self.text, 1)

        self.hist_list = QListWidget()
        self.hist_list.setFixedHeight(self.HIST_H - 8)
        self.hist_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.hist_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.hist_list.itemClicked.connect(self.show_entry)
        self.hist_list.hide()
        body.addWidget(self.hist_list)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.b_copy = QPushButton("Copy")
        self.b_copy.clicked.connect(self.copy)
        self.b_latest = QPushButton("Latest")
        self.b_latest.clicked.connect(self.show_latest)
        self.b_hist = QPushButton("History")
        self.b_hist.setCheckable(True)
        self.b_hist.toggled.connect(self.toggle_history)
        self.b_clear = QPushButton("Clear")
        self.b_clear.clicked.connect(self.clear_history)
        self.b_clear.hide()
        for b in (self.b_copy, self.b_latest, self.b_hist, self.b_clear):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(b)
        row.addStretch()
        row.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom)
        body.addLayout(row)
        outer.addLayout(body)

        self.resize(400, 180)
        self.setMinimumSize(260, 120)
        self.restore_geometry()
        self.refresh_history_list()

        # Follow theme changes live (switching Cathode variants, light/dark)
        self._theme_timer = QTimer(self)
        self._theme_timer.setSingleShot(True)
        self._theme_timer.setInterval(400)
        self._theme_timer.timeout.connect(self.apply_theme)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(os.path.dirname(KDEGLOBALS))
        if os.path.exists(KDEGLOBALS):
            self.watcher.addPath(KDEGLOBALS)
        self.watcher.fileChanged.connect(lambda _p: self._theme_timer.start())
        self.watcher.directoryChanged.connect(lambda _p: self._theme_timer.start())

        if self.history:
            self.latest = self.history[0]
            self.show_entry_data(self.latest, latest=True)

    # -- theme
    def apply_theme(self):
        if os.path.exists(KDEGLOBALS) and KDEGLOBALS not in self.watcher.files():
            self.watcher.addPath(KDEGLOBALS)   # KDE replaces the file on save
        new = theme_colors()
        if {k: v.name() for k, v in new.items()} == \
                {k: v.name() for k, v in self.colors.items()}:
            return
        self.colors = new
        self.setStyleSheet(build_style(new))
        self.update()
        for w in self.findChildren(QWidget):
            w.update()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.ApplicationPaletteChange:
            self._theme_timer.start()
        super().changeEvent(e)

    # -- drawing / position
    def paintEvent(self, e):
        c = self.colors
        p = QPainter(self)
        r = self.rect()
        w, h = r.width(), r.height()
        p.fillRect(r, c["face"])
        p.fillRect(0, 0, w, 1, c["light"])
        p.fillRect(0, 0, 1, h, c["light"])
        p.fillRect(1, 1, w - 2, 1, c["hilite"])
        p.fillRect(1, 1, 1, h - 2, c["hilite"])
        p.fillRect(0, h - 1, w, 1, c["dark"])
        p.fillRect(w - 1, 0, 1, h, c["dark"])
        p.fillRect(1, h - 2, w - 2, 1, c["shadow"])
        p.fillRect(w - 2, 1, 1, h - 2, c["shadow"])

    def restore_geometry(self):
        pos = self.settings.value("pos")
        size = self.settings.value("size")
        if size is not None:
            self.resize(size)
        if pos is not None and any(s.availableGeometry().contains(pos)
                                   for s in QGuiApplication.screens()):
            self.move(pos)
        else:
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.width() - 16, g.top() + 16)

    def save_geometry(self):
        self.settings.setValue("pos", self.pos())
        h = self.height() - (self.HIST_H if self.hist_list.isVisible() else 0)
        self.settings.setValue("size", QSize(self.width(), h))

    def hideEvent(self, e):
        self.save_geometry()
        super().hideEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()

    # -- translation states
    def begin(self):
        self.b_hist.setChecked(False)
        self.status.setText("Translating...")
        self.text.setPlainText("")
        self.b_copy.setEnabled(False)
        self.show()
        self.raise_()

    def append(self, chunk):
        if self.status.text() == "Translating...":
            self.status.setText("Translating... (receiving)")
        c = self.text.textCursor()
        c.movePosition(c.MoveOperation.End)
        c.insertText(chunk)
        self.text.setTextCursor(c)
        self.text.ensureCursorVisible()

    def finish(self, text):
        entry = {"time": datetime.now().isoformat(timespec="seconds"),
                 "text": text}
        self.history.insert(0, entry)
        del self.history[HIST_MAX:]
        self.save_history()
        self.refresh_history_list()
        self.latest = entry
        self.show_entry_data(entry, latest=True)

    def fail(self, msg):
        self.status.setText(msg)
        self.b_copy.setEnabled(bool(self.text.toPlainText()))
        self.show()

    # -- viewing
    def show_entry_data(self, entry, latest=False):
        when = fmt_time(entry["time"], with_date=not latest)
        self.status.setText(f"Translated at {when}" if latest
                            else f"History: {when}")
        self.text.setPlainText(entry["text"])
        self.text.moveCursor(self.text.textCursor().MoveOperation.Start)
        self.b_copy.setEnabled(True)

    def show_entry(self, item):
        self.show_entry_data(item.data(Qt.ItemDataRole.UserRole))

    def show_latest(self):
        if self.latest:
            self.show_entry_data(self.latest, latest=True)
        self.show()
        self.raise_()

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())
        old = self.status.text()
        self.status.setText("Copied to clipboard")
        QTimer.singleShot(1200, lambda: self.status.text() == "Copied to clipboard"
                          and self.status.setText(old))

    # -- history
    def toggle_history(self, on):
        if on == self.hist_list.isVisible():
            return
        self.hist_list.setVisible(on)
        self.b_clear.setVisible(on)
        self.resize(self.width(), self.height() + (self.HIST_H if on else -self.HIST_H))

    def refresh_history_list(self):
        self.hist_list.clear()
        for e in self.history:
            preview = " ".join(e["text"].split())
            if len(preview) > 70:
                preview = preview[:70] + "..."
            item = QListWidgetItem(f"{fmt_time(e['time'], with_date=True)}   {preview}")
            item.setData(Qt.ItemDataRole.UserRole, e)
            item.setToolTip(e["text"])
            self.hist_list.addItem(item)
        self.b_hist.setEnabled(bool(self.history))

    def clear_history(self):
        self.history = []
        self.save_history()
        self.refresh_history_list()
        self.b_hist.setChecked(False)

    def load_history(self):
        try:
            with open(HIST_FILE) as f:
                return json.load(f)[:HIST_MAX]
        except Exception:
            return []

    def save_history(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = HIST_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=1)
            os.replace(tmp, HIST_FILE)
        except OSError:
            pass


# -------------------------------------------------------------- translator
class Translator:
    """Capture with Spectacle, send to Ollama, stream into the popup."""

    def __init__(self, popup):
        self.popup = popup
        self.nam = QNetworkAccessManager()
        self.proc = None
        self.reply = None
        self.buf = b""
        self.text = ""
        self.error = None
        self.was_visible = False

    def start(self):
        if self.proc is not None:
            return                          # a capture is already on screen
        if self.reply is not None:
            self.reply.abort()              # new capture replaces old request
        self.was_visible = self.popup.isVisible()
        if self.was_visible:
            self.popup.hide()               # keep it out of the screenshot
        QTimer.singleShot(200 if self.was_visible else 0, self.capture)

    def capture(self):
        try:
            os.remove(SHOT)
        except FileNotFoundError:
            pass
        self.proc = QProcess()
        self.proc.finished.connect(self.captured)
        self.proc.errorOccurred.connect(lambda _e: self.captured())
        self.proc.start("spectacle", ["-b", "-n", "-r", "-o", SHOT])

    def captured(self, *_):
        if self.proc is None:
            return
        self.proc = None
        img = QImage(SHOT)
        if img.isNull():                    # cancelled with Esc
            if self.was_visible:
                self.popup.show()
            return
        if max(img.width(), img.height()) > MAX_SIDE:
            img = img.scaled(MAX_SIDE, MAX_SIDE,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        b64 = base64.b64encode(bytes(ba)).decode()

        body = json.dumps({
            "model": MODEL, "stream": True, "think": False,
            "keep_alive": KEEP_ALIVE, "images": [b64], "prompt": PROMPT,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 512},
        }).encode()
        req = QNetworkRequest(QUrl(API + "/api/generate"))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,
                      "application/json")
        req.setTransferTimeout(120000)
        self.buf, self.text, self.error = b"", "", None
        self.popup.begin()
        reply = self.nam.post(req, body)
        self.reply = reply
        reply.readyRead.connect(lambda r=reply: self.on_data(r))
        reply.finished.connect(lambda r=reply: self.on_done(r))

    def on_data(self, reply):
        if reply is not self.reply:
            return
        self.buf += bytes(reply.readAll())
        *lines, self.buf = self.buf.split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if "error" in obj:
                self.error = obj["error"]
                continue
            chunk = obj.get("response", "")
            if chunk:
                if not self.text:
                    chunk = chunk.lstrip()
                self.text += chunk
                self.popup.append(chunk)

    def on_done(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self.on_data(reply)
        err = reply.error()
        self.reply = None
        reply.deleteLater()
        if err == QNetworkReply.NetworkError.OperationCanceledError:
            return
        text = self.text.strip()
        if self.error:
            self.popup.fail(f"Ollama error: {self.error}")
        elif not text and err != QNetworkReply.NetworkError.NoError:
            self.popup.fail("Can't reach Ollama - is the service running?")
        elif not text:
            self.popup.fail("No text found in that area.")
        else:
            self.popup.finish(text)


# -------------------------------------------------------------------- tray
class Tray:
    def __init__(self):
        self.popup = Popup()
        self.translator = Translator(self.popup)
        self.icons = {s: make_icon(s) for s in ("loaded", "idle", "off")}
        self.tray = QSystemTrayIcon(self.icons["off"])
        self.tray.setToolTip("Cathode Translate")
        self.state = None

        menu = QMenu()
        self.header = QAction("Cathode Translate")
        self.header.setEnabled(False)
        menu.addAction(self.header)
        menu.addSeparator()
        self.a_translate = menu.addAction("Translate screen region",
                                          self.translator.start)
        menu.addAction("Show translation window", self.popup.show_latest)
        menu.addSeparator()
        self.a_load = menu.addAction(f"Load {MODEL}", self.load)
        self.a_unload = menu.addAction("Unload model (free VRAM)", self.unload)
        menu.addSeparator()
        self.a_start = menu.addAction("Start Ollama service", self.start)
        self.a_stop = menu.addAction("Stop Ollama service", self.stop)
        menu.addSeparator()
        menu.addAction("Quit indicator", QApplication.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.clicked)

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()
        self.tray.show()

    def command(self, cmd):
        if cmd == "translate":
            self.translator.start()
        elif cmd == "show":
            self.popup.show_latest()

    def refresh(self):
        try:
            models = get_json("/api/ps").get("models", [])
            state = "loaded" if models else "idle"
        except Exception:
            models, state = [], "off"

        if state != self.state:
            self.tray.setIcon(self.icons[state])
            self.state = state

        if state == "off":
            lines = ["Cathode Translate: Ollama service off"]
        elif state == "idle":
            lines = ["Cathode Translate: ready, no model loaded",
                     "GPU memory is free"]
        else:
            lines = ["Cathode Translate: model loaded"]
            for m in models:
                size, vram = m.get("size", 0), m.get("size_vram", 0)
                pct = round(100 * vram / size) if size else 0
                lines.append(m.get("name", "?"))
                lines.append(f"VRAM: {vram / 1e9:.1f} GB ({pct}% on GPU)")
                left = time_left(m.get("expires_at"))
                if left:
                    lines.append(left.capitalize())
        g = gpu_stats()
        if g:
            lines.append(g)

        self.tray.setToolTip("\n".join(lines))
        self.header.setText(lines[0])
        self.a_translate.setEnabled(state != "off")
        self.a_load.setEnabled(state == "idle")
        self.a_unload.setEnabled(state == "loaded")
        self.a_start.setEnabled(state == "off")
        self.a_stop.setEnabled(state != "off")

    def clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.translator.start()

    def load(self):
        body = json.dumps({"model": MODEL, "keep_alive": KEEP_ALIVE})
        run_bg(["curl", "-s", f"{API}/api/generate", "-d", body])

    def unload(self):
        body = json.dumps({"model": MODEL, "keep_alive": 0})
        run_bg(["curl", "-s", f"{API}/api/generate", "-d", body])

    def start(self):
        run_bg(["systemctl", "start", SERVICE])

    def stop(self):
        run_bg(["systemctl", "stop", SERVICE])


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Cathode Translate")
    app.setDesktopFileName(APP_ID)
    migrate_old_data()
    tray = Tray()

    server = QLocalServer()
    QLocalServer.removeServer(SOCK)
    server.listen(SOCK)

    def on_conn():
        conn = server.nextPendingConnection()

        def read():
            for line in bytes(conn.readAll()).decode(errors="ignore").split():
                tray.command(line.strip())
        conn.readyRead.connect(read)
        conn.disconnected.connect(conn.deleteLater)
    server.newConnection.connect(on_conn)

    if _cmd == "translate":
        QTimer.singleShot(300, tray.translator.start)
    elif _cmd == "show":
        tray.popup.show_latest()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
