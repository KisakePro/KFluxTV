import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from datetime import datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests

try:
    import pychromecast
    import zeroconf
except Exception:  # Chromecast optionnel
    pychromecast = zeroconf = None
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QKeySequence, QLinearGradient,
    QFontMetrics, QPainter, QPalette, QPixmap, QPolygonF,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QCheckBox, QInputDialog, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
    QTabWidget, QTextBrowser, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

APP = "KFluxTV"
VERSION = "0.6"
REPO = "KisakePro/KFluxTV"
_ROAMING = os.getenv("APPDATA", os.path.expanduser("~"))
DATA_DIR = os.path.join(_ROAMING, "KFluxTV")
if not os.path.isdir(DATA_DIR) and os.path.isdir(os.path.join(_ROAMING, "LecteurIPTV")):
    try:  # reprise des comptes de l'ancienne version « Lecteur IPTV »
        shutil.copytree(os.path.join(_ROAMING, "LecteurIPTV"), DATA_DIR)
    except Exception:
        pass
CFG = os.path.join(DATA_DIR, "profiles.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KIND = Qt.UserRole + 1  # "cat", "ch", "movie", "series", "season", "ep"
KEY = Qt.UserRole + 2   # id (str)
DATA = Qt.UserRole + 3  # dict brut renvoyé par l'API
FAV_KIND = {"ch": "live", "movie": "vod", "series": "series"}
SECTIONS = {
    "live": dict(title="📺 Direct", cat="get_live_categories", lst="get_live_streams",
                 id="stream_id", pre="", kind="ch", tag="Chaîne"),
    "vod": dict(title="🎬 Films", cat="get_vod_categories", lst="get_vod_streams",
                id="stream_id", pre="vod:", kind="movie", tag="Film"),
    "series": dict(title="🍿 Séries", cat="get_series_categories", lst="get_series",
                   id="series_id", pre="series:", kind="series", tag="Série"),
}


def load_profiles():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_profiles(p):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


SETTINGS = os.path.join(os.path.dirname(CFG), "settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def parse_input(server, user, pwd):
    """Accepte une URL complète get.php?username=..&password=.. ou host + user + pass."""
    server = server.strip()
    if "://" not in server:
        server = "http://" + server
    u = urlparse(server)
    q = parse_qs(u.query)
    if "username" in q and "password" in q:
        user, pwd = q["username"][0], q["password"][0]
    return f"{u.scheme}://{u.netloc}", user.strip(), pwd.strip()


def api(p, action=None):
    params = {"username": p["user"], "password": p["pwd"]}
    if action:
        params["action"] = action
    r = requests.get(f"{p['server']}/player_api.php", params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


class Job(QThread):
    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:
            self.fail.emit(str(e))


def load_all(p):
    info = api(p)
    if isinstance(info, dict) and info.get("user_info", {}).get("auth") == 0:
        raise RuntimeError("Identifiants refusés par le serveur.")
    cats = api(p, "get_live_categories")
    streams = api(p, "get_live_streams")
    if not isinstance(cats, list) or not isinstance(streams, list):
        raise RuntimeError("Réponse serveur invalide.")
    return cats, streams


def fmt_ts(v):
    try:
        return datetime.fromtimestamp(int(v)).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "illimité" if v in (None, "", "null") else str(v)


class ProfileEditor(QDialog):
    def __init__(self, parent=None, profile=None):
        super().__init__(parent)
        self.setWindowTitle("Profil Xtream")
        self.setMinimumWidth(460)
        self.old = profile
        self.name = QLineEdit()
        self.server = QLineEdit()
        self.server.setPlaceholderText("http://serveur:port  (ou lien M3U complet)")
        self.user = QLineEdit()
        self.pwd = QLineEdit()
        if profile:
            self.name.setText(profile["name"])
            self.server.setText(profile["server"])
            self.user.setText(profile["user"])
            self.pwd.setText(profile["pwd"])
        f = QFormLayout()
        f.addRow("Nom", self.name)
        f.addRow("Serveur", self.server)
        f.addRow("Utilisateur", self.user)
        f.addRow("Mot de passe", self.pwd)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)

    def ok(self):
        base, user, pwd = parse_input(self.server.text(), self.user.text(), self.pwd.text())
        if not (self.server.text().strip() and user and pwd):
            QMessageBox.warning(self, APP, "Serveur, utilisateur et mot de passe requis.")
            return
        name = self.name.text().strip() or f"{user}@{urlparse(base).hostname}"
        p = {"name": name, "server": base, "user": user, "pwd": pwd,
             "hidden_cats": [], "hidden_chs": []}
        if self.old and (self.old["server"], self.old["user"]) == (base, user):
            p["hidden_cats"] = self.old.get("hidden_cats", [])
            p["hidden_chs"] = self.old.get("hidden_chs", [])
        self.result_profile = p
        self.accept()


class ProfilesDialog(QDialog):
    """Gestion des comptes : ajouter, modifier, supprimer, se connecter."""

    def __init__(self, parent=None, active=None):
        super().__init__(parent)
        self.setWindowTitle("Gestion des comptes")
        self.setMinimumSize(480, 340)
        self.profiles = load_profiles()
        self.active = active
        self.selected = None
        self.lst = QListWidget()
        self.lst.itemDoubleClicked.connect(lambda _: self.connect_sel())
        b_add = QPushButton("Ajouter…")
        b_edit = QPushButton("Modifier…")
        b_del = QPushButton("Supprimer")
        b_con = QPushButton("Se connecter")
        b_con.setDefault(True)
        b_close = QPushButton("Fermer")
        b_add.clicked.connect(self.add)
        b_edit.clicked.connect(self.edit)
        b_del.clicked.connect(self.delete)
        b_con.clicked.connect(self.connect_sel)
        b_close.clicked.connect(self.reject)
        side = QVBoxLayout()
        for b in (b_con, b_add, b_edit, b_del):
            side.addWidget(b)
        side.addStretch()
        side.addWidget(b_close)
        lay = QHBoxLayout(self)
        lay.addWidget(self.lst, 1)
        lay.addLayout(side)
        self.refresh()

    def refresh(self):
        self.lst.clear()
        for p in self.profiles:
            tag = "  ● connecté" if self.active == p["name"] else ""
            it = QListWidgetItem(f"{p['name']}   [{urlparse(p['server']).hostname}]{tag}")
            it.setData(Qt.UserRole, p["name"])
            self.lst.addItem(it)
        if self.lst.count():
            self.lst.setCurrentRow(0)

    def current(self):
        it = self.lst.currentItem()
        if not it:
            return None
        return next((p for p in self.profiles if p["name"] == it.data(Qt.UserRole)), None)

    def add(self):
        d = ProfileEditor(self)
        if d.exec():
            p = d.result_profile
            self.profiles = [x for x in self.profiles if x["name"] != p["name"]] + [p]
            save_profiles(self.profiles)
            self.refresh()

    def edit(self):
        cur = self.current()
        if not cur:
            return
        d = ProfileEditor(self, cur)
        if d.exec():
            p = d.result_profile
            self.profiles = [x for x in self.profiles if x["name"] not in (cur["name"], p["name"])] + [p]
            save_profiles(self.profiles)
            self.refresh()

    def delete(self):
        cur = self.current()
        if cur and QMessageBox.question(self, APP, f"Supprimer le compte « {cur['name']} » ?") == QMessageBox.Yes:
            self.profiles = [x for x in self.profiles if x["name"] != cur["name"]]
            save_profiles(self.profiles)
            self.refresh()

    def connect_sel(self):
        cur = self.current()
        if cur:
            self.selected = cur
            self.accept()


class HiddenDialog(QDialog):
    """Liste les catégories/chaînes masquées ; permet de les réafficher."""

    def __init__(self, parent, entries):
        super().__init__(parent)
        self.setWindowTitle("Éléments masqués")
        self.setMinimumSize(460, 420)
        self.lst = QListWidget()
        self.lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for kind, key, label in entries:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, (kind, key))
            self.lst.addItem(it)
        b_sel = QPushButton("Réafficher la sélection")
        b_all = QPushButton("Tout réafficher")
        b_close = QPushButton("Fermer")
        b_sel.clicked.connect(lambda: self.finish(self.lst.selectedItems()))
        b_all.clicked.connect(lambda: self.finish([self.lst.item(i) for i in range(self.lst.count())]))
        b_close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(b_sel)
        row.addWidget(b_all)
        row.addStretch()
        row.addWidget(b_close)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Sélectionne les éléments à réafficher :"))
        lay.addWidget(self.lst)
        lay.addLayout(row)
        self.restore = []

    def finish(self, items):
        self.restore = [i.data(Qt.UserRole) for i in items]
        self.accept()


STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog, QWidget#root { background: #14151a; }
QWidget { color: #e6e8ef; }
QMenuBar { background: #14151a; padding: 2px 6px; }
QMenuBar::item { padding: 6px 12px; border-radius: 6px; background: transparent; }
QMenuBar::item:selected { background: #2a2d3a; }
QMenu { background: #1b1d24; border: 1px solid #2c3040; padding: 6px; border-radius: 8px; }
QMenu::item { padding: 7px 26px 7px 22px; border-radius: 6px; }
QMenu::item:selected { background: #7c5cff; }
QMenu::separator { height: 1px; background: #2c3040; margin: 5px 8px; }
QStatusBar { background: #14151a; color: #8b90a0; }
QLabel#muted { color: #8b90a0; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLabel#cardtitle { font-size: 14px; font-weight: 600; }
QLineEdit { background: #1b1d24; border: 1px solid #2c3040; border-radius: 10px; padding: 8px 12px; selection-background-color: #7c5cff; }
QLineEdit:focus { border: 1px solid #7c5cff; }
QTabWidget::pane { border: none; margin-top: 6px; }
QTabBar { background: transparent; }
QTabBar::tab { background: #1b1d24; color: #8b90a0; padding: 8px 10px; margin-right: 3px; border-radius: 8px; }
QTabBar::tab:selected { background: #7c5cff; color: white; font-weight: 600; }
QTabBar::tab:hover:!selected { background: #2a2d3a; color: #e6e8ef; }
QTreeWidget, QListWidget { background: #1b1d24; border: none; border-radius: 12px; padding: 6px; outline: 0; }
QTreeWidget::item, QListWidget::item { padding: 6px 6px; border-radius: 6px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #2a2d3a; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #7c5cff; color: white; }
QPushButton { background: #2a2d3a; border: none; border-radius: 9px; padding: 8px 14px; }
QPushButton:hover { background: #363a4d; }
QPushButton:pressed { background: #7c5cff; }
QPushButton#accent { background: #7c5cff; color: white; font-weight: 600; }
QPushButton#accent:hover { background: #8e72ff; }
QPushButton#round { border-radius: 18px; min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; padding: 0; font-size: 15px; }
QSlider::groove:horizontal { height: 5px; background: #2c3040; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #7c5cff; border-radius: 2px; }
QSlider::handle:horizontal { background: white; width: 13px; height: 13px; margin: -4px 0; border-radius: 6px; }
QSlider::handle:horizontal:disabled { background: #555a6b; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #363a4d; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #7c5cff; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { height: 0; }
QSplitter::handle { background: #14151a; width: 6px; }
QToolTip { background: #1b1d24; color: #e6e8ef; border: 1px solid #7c5cff; }
QFrame#card { background: #1b1d24; border-radius: 12px; }
QMessageBox, QDialog { background: #14151a; }
"""


def make_pixmap(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#8e72ff"))
    g.setColorAt(1, QColor("#3b82f6"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(size * .04, size * .04, size * .92, size * .92), size * .22, size * .22)
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([QPointF(size * .38, size * .27), QPointF(size * .38, size * .73),
                             QPointF(size * .75, size * .5)]))
    p.end()
    return pm


def clean_title(t):
    t = t.replace("  [masqué]", "")
    while t and t[0] in "★📺🎬🍿":
        t = t[1:].lstrip()
    return t


def fmt_ms(ms):
    s = max(0, int(ms // 1000))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def norm_eps(eps):
    out = {}
    if isinstance(eps, dict):
        for k, v in eps.items():
            out[str(k)] = v if isinstance(v, list) else []
    elif isinstance(eps, list):
        for i, v in enumerate(eps):
            if isinstance(v, list):
                out[str(i + 1)] = v
            elif isinstance(v, dict):
                out.setdefault(str(v.get("season", 1)), []).append(v)
    return sorted(out.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999)


def as_int(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


def b64(s):
    """Les titres/descriptions EPG Xtream sont en base64 ; sinon on garde le texte."""
    if not s:
        return ""
    try:
        d = base64.b64decode(s, validate=True).decode("utf-8")
        if d.replace("\n", "").replace("\r", "").isprintable():
            return d
    except Exception:
        pass
    return str(s)


def parse_epg(data):
    out = []
    lst = data.get("epg_listings") if isinstance(data, dict) else None
    for e in lst or []:
        st, en = as_int(e.get("start_timestamp")), as_int(e.get("stop_timestamp"))
        if not (st and en):
            try:
                st = int(time.mktime(time.strptime(e.get("start"), "%Y-%m-%d %H:%M:%S")))
                en = int(time.mktime(time.strptime(e.get("end"), "%Y-%m-%d %H:%M:%S")))
            except Exception:
                continue
        if en > st:
            out.append(dict(start=st, end=en, title=b64(e.get("title")) or "?", desc=b64(e.get("description"))))
    return sorted(out, key=lambda x: x["start"])


PPM = 5  # pixels par minute


def hhmm(t):
    return time.strftime("%H:%M", time.localtime(t))


class EpgCanvas(QWidget):
    H = 64

    def __init__(self):
        super().__init__()
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.items, self.t0 = [], 0
        self.msg = "Sélectionne une chaîne pour voir son programme"

    def set_message(self, msg):
        self.items, self.msg = [], msg
        self.setFixedWidth(700)
        self.update()

    def set_items(self, items):
        now = time.time()
        items = [i for i in items if i["end"] > now - 3 * 3600]
        if not items:
            return self.set_message("Programme indisponible pour cette chaîne")
        self.items = items
        self.t0 = min(items[0]["start"], int(now - 3600))
        self.setFixedWidth(max(50, int((items[-1]["end"] - self.t0) / 60 * PPM) + 20))
        self.update()

    def x_of(self, t):
        return (t - self.t0) / 60 * PPM

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1b1d24"))
        if not self.items:
            p.setPen(QColor("#8b90a0"))
            p.drawText(QRectF(12, 0, 680, self.H), Qt.AlignVCenter | Qt.AlignLeft, self.msg)
            return
        now = time.time()
        fm = QFontMetrics(p.font())
        t = (self.t0 // 1800 + 1) * 1800  # axe : graduation toutes les 30 min
        p.setPen(QColor("#6c7185"))
        while self.x_of(t) < self.width():
            x = self.x_of(t)
            p.drawLine(QPointF(x, 14), QPointF(x, 19))
            p.drawText(QPointF(x + 3, 12), hhmm(t))
            t += 1800
        for it in self.items:
            x, w = self.x_of(it["start"]), (it["end"] - it["start"]) / 60 * PPM
            r = QRectF(x + 1, 22, max(2, w - 2), self.H - 26)
            cur = it["start"] <= now < it["end"]
            past = it["end"] <= now
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#7c5cff") if cur else QColor("#232632") if past else QColor("#2e3243"))
            p.drawRoundedRect(r, 6, 6)
            if cur:  # progression du programme en cours
                prog = (now - it["start"]) / (it["end"] - it["start"])
                p.setBrush(QColor(255, 255, 255, 70))
                p.drawRoundedRect(QRectF(r.x(), r.bottom() - 4, r.width() * prog, 4), 2, 2)
            tw = int(r.width()) - 12
            if tw > 20:
                p.setPen(QColor("white") if cur else QColor("#8b90a0") if past else QColor("#d0d3df"))
                p.drawText(QPointF(r.x() + 6, r.y() + 16), fm.elidedText(it["title"], Qt.ElideRight, tw))
                p.setPen(QColor(255, 255, 255, 150) if cur else QColor("#6c7185"))
                p.drawText(QPointF(r.x() + 6, r.y() + 31),
                           fm.elidedText(f"{hhmm(it['start'])} – {hhmm(it['end'])}", Qt.ElideRight, tw))
        if self.x_of(now) >= 0:  # trait « maintenant »
            p.setPen(QColor("#ff4d4d"))
            x = self.x_of(now)
            p.drawLine(QPointF(x, 18), QPointF(x, self.H))

    def item_at(self, x):
        t = self.t0 + x / PPM * 60
        return next((i for i in self.items if i["start"] <= t < i["end"]), None)

    def mouseMoveEvent(self, e):
        it = self.item_at(e.position().x())
        if not it:
            return QToolTip.hideText()
        tip = f"<b>{it['title']}</b><br>{hhmm(it['start'])} – {hhmm(it['end'])}"
        if it["desc"]:
            tip += "<br><br>" + it["desc"][:500].replace("\n", "<br>")
        QToolTip.showText(e.globalPosition().toPoint(), f"<div style='max-width:360px'>{tip}</div>", self)

    def current(self):
        now = time.time()
        return next((i for i in self.items if i["start"] <= now < i["end"]), None)


class EpgBar(QScrollArea):
    """Bandeau chronologique du programme (défilement horizontal)."""

    def __init__(self):
        super().__init__()
        self.canvas = EpgCanvas()
        self.setWidget(self.canvas)
        self.setFixedHeight(EpgCanvas.H + 12)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea{background:#1b1d24;border-radius:10px;}"
            "QScrollBar:horizontal{height:8px;background:transparent;margin:0 6px;}"
            "QScrollBar::handle:horizontal{background:#363a4d;border-radius:4px;min-width:30px;}"
            "QScrollBar::handle:horizontal:hover{background:#7c5cff;}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.canvas.update)
        self.timer.start(30000)

    def wheelEvent(self, e):
        sb = self.horizontalScrollBar()
        sb.setValue(sb.value() - (e.angleDelta().y() or e.angleDelta().x()))

    def message(self, msg):
        self.canvas.set_message(msg)
        self.horizontalScrollBar().setValue(0)

    def show_items(self, items):
        self.canvas.set_items(items)
        QTimer.singleShot(0, lambda: self.horizontalScrollBar().setValue(
            max(0, int(self.canvas.x_of(time.time() - 1500)))))


class Section:
    def __init__(self, key, tree):
        self.key, self.tree = key, tree
        for k, v in SECTIONS[key].items():
            setattr(self, k, v)
        self.cats, self.items, self.loaded, self.loading = [], [], False, False
        self.stats = (0, 0, 0)


class FavTree(QTreeWidget):
    """Arbre des favoris : on peut glisser des éléments sur un groupe pour les y ranger."""

    def __init__(self):
        super().__init__()
        self.on_drop = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, e):
        target = self.itemAt(e.position().toPoint())
        if target is None:
            return e.ignore()
        if target.parent() is not None:
            target = target.parent()
        keys = [f"{FAV_KIND[i.data(0, KIND)]}:{i.data(0, KEY)}" for i in self.selectedItems()
                if i.data(0, KIND) in FAV_KIND]
        group = target.data(0, KEY)
        e.setDropAction(Qt.IgnoreAction)  # on reconstruit l'arbre nous-mêmes
        e.ignore()
        if keys and self.on_drop:
            QTimer.singleShot(0, lambda: self.on_drop(keys, group))


class VideoWidget(QVideoWidget):
    toggleSemi = Signal()
    escape = Signal()

    def mouseDoubleClickEvent(self, e):
        if self.isFullScreen():
            self.setFullScreen(False)
        else:
            self.toggleSemi.emit()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            if self.isFullScreen():
                self.setFullScreen(False)
            else:
                self.escape.emit()
        elif k == Qt.Key_F11:
            self.setFullScreen(not self.isFullScreen())
        elif k == Qt.Key_F:
            if self.isFullScreen():
                self.setFullScreen(False)
            else:
                self.toggleSemi.emit()
        else:
            super().keyPressEvent(e)


NOWIN = 0x08000000  # CREATE_NO_WINDOW


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def probe_stream(url):
    """Lit codec/résolution/fps via ffmpeg. Renvoie un dict ou None."""
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-hide_banner", "-rw_timeout", "10000000", "-analyzeduration", "3000000",
                            "-probesize", "3000000", "-i", url],
                           capture_output=True, text=True, timeout=40, creationflags=NOWIN)
    except Exception:
        return None
    vl = next((l for l in r.stderr.splitlines() if "Video:" in l), None)
    if not vl:
        return None
    al = next((l for l in r.stderr.splitlines() if "Audio:" in l), "")
    wh = re.search(r"\b(\d{3,5})x(\d{3,5})\b", vl)
    fps = re.search(r"([\d.]+) fps", vl)
    pf = re.search(r"\b(yuv\w+|nv12|p010\w*)", vl)
    return dict(
        vcodec=vl.split("Video:")[1].split()[0].strip(","),
        pix=pf[1] if pf else "",
        w=int(wh[1]) if wh else 0, h=int(wh[2]) if wh else 0,
        fps=float(fps[1]) if fps else 25.0,
        acodec=(al.split("Audio:")[1].split()[0].strip(",") if "Audio:" in al else ""),
    )


def plan_transcode(url, mode):
    """Décide si le flux doit être converti pour la Chromecast. None = flux direct."""
    if mode == "never":
        return None
    info = probe_stream(url)
    if info is None:
        return None
    blocks = -(-info["w"] // 16) * -(-info["h"] // 16)
    v_ok = (info["vcodec"] == "h264" and "10" not in info["pix"] and info["h"] <= 1080
            and blocks * info["fps"] <= 245000)  # H.264 niveau 4.1 : 1080p30 ou 720p60
    a_ok = info["acodec"] in ("aac", "mp3", "ac3", "eac3", "")
    if mode == "auto" and v_ok and a_ok:
        return None
    h = 0
    if not v_ok:
        h = min(info["h"], 720 if info["fps"] > 30 else 1080)
        if h >= info["h"]:
            h = 0
    return dict(v="copy" if v_ok and mode == "auto" else "enc", h=h, a="copy" if a_ok else "enc")


_JOB = None


def kill_with_parent(proc):
    """Rattache un processus enfant à un objet Job Windows : il est tué si l'application se ferme ou plante."""
    global _JOB
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _JOB is None:
            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class EXT(ctypes.Structure):
                _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            h = k.CreateJobObjectW(None, None)
            info = EXT()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))
            _JOB = h
        k.AssignProcessToJobObject(_JOB, int(proc._handle))
    except Exception:
        pass


def clean_stale_temp():
    """Supprime les enregistrements temporaires laissés par un plantage précédent."""
    base = tempfile.gettempdir()
    try:
        for name in os.listdir(base):
            if name.startswith(("iptvts_", "iptvhls_")):
                path = os.path.join(base, name)
                if time.time() - os.path.getmtime(path) > 3600:
                    shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def local_ip(remote):
    """Adresse LAN du PC telle que la voit l'appareil `remote`."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sk.connect((remote, 8009))
        return sk.getsockname()[0]
    finally:
        sk.close()


class Relay:
    """Relais HTTP local pour Chromecast : ajoute les en-têtes CORS (obligatoires pour le HLS),
    réécrit les playlists .m3u8 pour que tous les segments passent aussi par le PC."""

    def __init__(self):
        self.srv = None
        self.port = 0
        self.srv_lan, self.lan_port = None, 0
        self.hls_proc = self.hls_dir = self.hls_id = None
        self.active = set()  # connexions amont en cours (libérées à l'arrêt)
        self.ts_deleted = 0
        self.rec_files = []        # enregistrement brut : [(durée en s, nom du fichier)]
        self.rec_stop = threading.Event()
        self.rec_resp = None

    def ffmpeg_cmd(self, url, v, h, a, outdir):
        cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-fflags", "+genpts+discardcorrupt",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
               "-i", url, "-map", "0:v:0", "-map", "0:a:0?"]
        if v == "copy":
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level", "4.1",
                    "-pix_fmt", "yuv420p", "-g", "50", "-sc_threshold", "0",
                    "-crf", "23", "-maxrate", "8M", "-bufsize", "12M"]
            if h:
                cmd += ["-vf", f"scale=-2:{h}"]
        cmd += ["-c:a", "copy"] if a == "copy" else ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
        cmd += ["-f", "hls", "-hls_time", "2", "-hls_list_size", "10",
                "-hls_flags", "delete_segments+independent_segments",
                "-hls_segment_filename", os.path.join(outdir, "seg%05d.ts"), os.path.join(outdir, "index.m3u8")]
        return cmd

    def start_hls(self, url, plan):
        """Lance ffmpeg : conversion du flux en HLS compatible Chromecast, servi sous /h/<id>/."""
        self.stop_hls()
        self.hls_dir = tempfile.mkdtemp(prefix="iptvhls_")
        self.hls_id = os.path.basename(self.hls_dir)
        cmd = self.ffmpeg_cmd(url, plan["v"], plan["h"], plan["a"], self.hls_dir)
        self.hls_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, creationflags=NOWIN)
        kill_with_parent(self.hls_proc)

    def start_timeshift(self, url):
        """Enregistre le flux MPEG-TS du fournisseur tel quel (aucune conversion : lecture identique au direct)
        par morceaux d'environ 2 s, pour permettre pause / retour arrière."""
        self.start()
        self.stop_hls()
        self.hls_dir = tempfile.mkdtemp(prefix="iptvts_")
        self.hls_id = os.path.basename(self.hls_dir)
        self.ts_deleted = 0
        self.rec_files = []
        self.rec_stop = threading.Event()
        threading.Thread(target=self._rec_loop, args=(url, self.rec_stop, self.hls_dir, self.rec_files),
                         daemon=True).start()
        return f"http://127.0.0.1:{self.port}/h/{self.hls_id}"

    def _rec_loop(self, url, stop, outdir, files):
        n, fh, t0, w0, name = 0, None, None, 0.0, None
        pcr_pid = None
        while not stop.is_set():
            buf, synced = b"", False
            try:
                r = requests.get(url, headers=HEADERS, stream=True, timeout=15)
                r.raise_for_status()
                self.rec_resp = r
                for chunk in r.iter_content(65536):
                    if stop.is_set():
                        break
                    buf += chunk
                    if not synced:  # recale sur les paquets de 188 octets (octet de synchro 0x47)
                        o = next((i for i in range(min(188, len(buf) - 376))
                                  if buf[i] == 0x47 and buf[i + 188] == 0x47 and buf[i + 376] == 0x47), None)
                        if o is None:
                            buf = buf[-376:] if len(buf) > 376 else buf
                            continue
                        buf, synced = buf[o:], True
                    k = len(buf) // 188 * 188
                    data, buf = buf[:k], buf[k:]
                    pcr = None
                    for off in range(0, k, 188):  # horloge du flux (PCR) : durée exacte de chaque morceau
                        if data[off + 3] & 0x20 and data[off + 4] >= 7 and data[off + 5] & 0x10:
                            pid = ((data[off + 1] & 0x1F) << 8) | data[off + 2]
                            if pcr_pid is None:
                                pcr_pid = pid
                            if pid == pcr_pid:
                                x = data[off + 6:off + 11]
                                pcr = ((x[0] << 25) | (x[1] << 17) | (x[2] << 9) | (x[3] << 1) | (x[4] >> 7)) / 90.0
                                break
                    now = time.time()
                    if fh is not None:
                        span = (pcr - t0) if (pcr is not None and t0 is not None) else (now - w0) * 1000
                        if span >= 2000 or span < 0:
                            if not (0 < span < 20000):
                                span = (now - w0) * 1000
                            fh.close()
                            files.append((span / 1000.0, name))
                            fh = None
                    if fh is None:
                        name = f"r{n:06d}.ts"
                        n += 1
                        fh = open(os.path.join(outdir, name), "wb")
                        t0, w0 = pcr, now
                    elif t0 is None and pcr is not None:
                        t0 = pcr
                    fh.write(data)
            except Exception:
                pass
            finally:
                self.rec_resp = None
            if fh is not None:  # connexion coupée : on ferme le morceau en cours
                try:
                    fh.close()
                    files.append((max(0.1, time.time() - w0), name))
                except Exception:
                    pass
                fh, t0 = None, None
            if not stop.is_set():
                time.sleep(1)

    def ts_segments(self):
        return list(self.rec_files)

    def ts_trim(self, keep_from_ms):
        """Supprime du disque les segments plus anciens que la fenêtre de retour arrière."""
        t = 0.0
        for i, (d, name) in enumerate(self.ts_segments()):
            if (t + d) * 1000 >= keep_from_ms - 15000:
                break
            if i >= self.ts_deleted and self.hls_dir:
                try:
                    os.remove(os.path.join(self.hls_dir, name))
                except OSError:
                    pass
                self.ts_deleted = i + 1
            t += d

    def stop_hls(self):
        self.rec_stop.set()
        r = self.rec_resp
        if r is not None:
            try:
                r.close()
            except Exception:
                pass
        if self.hls_proc:
            try:
                self.hls_proc.kill()
                self.hls_proc.wait(timeout=5)
            except Exception:
                pass
        if self.hls_dir:
            shutil.rmtree(self.hls_dir, ignore_errors=True)
        self.hls_proc, self.hls_dir, self.hls_id = None, None, None
        self.rec_files = []

    def stop_all(self):
        self.stop_hls()
        for r in list(self.active):
            try:
                r.close()
            except Exception:
                pass
        self.active.clear()

    def start(self):
        if self.srv:
            return
        relay = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Expose-Headers", "*")

            def do_OPTIONS(self):
                self.send_response(204)
                self.cors()
                self.end_headers()

            def do_HEAD(self):
                self.serve(head=True)

            def do_GET(self):
                self.serve()

            def serve_hls(self, name, head, q):
                if name == "c.ts":  # flux MPEG-TS continu : les segments enregistrés mis bout à bout
                    idx = int(q.get("s", ["0"])[0])
                    sid = relay.hls_id
                    self.send_response(200)
                    self.cors()
                    self.send_header("Content-Type", "video/mp2t")
                    self.end_headers()
                    if head:
                        return
                    while relay.hls_id == sid and relay.hls_dir:
                        segs = relay.ts_segments()
                        if idx >= len(segs):
                            time.sleep(0.1)
                            continue
                        try:
                            with open(os.path.join(relay.hls_dir, segs[idx][1]), "rb") as fh:
                                while True:
                                    b = fh.read(262144)
                                    if not b:
                                        break
                                    self.wfile.write(b)
                        except FileNotFoundError:
                            pass  # segment supprimé (hors fenêtre) : on passe au suivant
                        idx += 1
                    return
                f = os.path.join(relay.hls_dir or "", os.path.basename(name))
                if name.endswith(".m3u8"):  # attendre le 1er segment
                    for _ in range(80):
                        if os.path.exists(f) and os.path.getsize(f) > 0:
                            break
                        time.sleep(0.25)
                try:
                    with open(f, "rb") as fh:
                        body = fh.read()
                except Exception:
                    self.send_response(404)
                    self.cors()
                    self.end_headers()
                    return
                self.send_response(200)
                self.cors()
                self.send_header("Content-Type", "application/vnd.apple.mpegurl" if name.endswith(".m3u8")
                                 else "video/mp2t")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def serve(self, head=False):
                try:
                    if not ipaddress.ip_address(self.client_address[0]).is_private:
                        self.send_response(403)
                        self.end_headers()
                        return
                except ValueError:
                    return
                path = urlparse(self.path).path
                q = parse_qs(urlparse(self.path).query)
                if path.startswith("/h/"):
                    parts = path.split("/")
                    if len(parts) == 4 and parts[2] == relay.hls_id:
                        try:
                            return self.serve_hls(parts[3], head, q)
                        except (BrokenPipeError, ConnectionError, OSError):
                            return
                    self.send_response(404)
                    self.end_headers()
                    return
                if "u" not in q:
                    self.send_response(404)
                    self.end_headers()
                    return
                up = q["u"][0]
                hdr = dict(HEADERS)
                if self.headers.get("Range"):
                    hdr["Range"] = self.headers["Range"]
                r = None
                try:
                    r = requests.get(up, headers=hdr, stream=True, timeout=20)
                    relay.active.add(r)
                    it = r.iter_content(65536)
                    first = next(it, b"")
                    if first.lstrip().startswith(b"#EXTM3U"):
                        text = (first + b"".join(it)).decode("utf-8", "ignore")
                        body = relay.rewrite(text, r.url, self.headers.get("Host")).encode()
                        self.send_response(200)
                        self.cors()
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        if not head:
                            self.wfile.write(body)
                        return
                    self.send_response(r.status_code)
                    self.cors()
                    for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                        if r.headers.get(k):
                            self.send_header(k, r.headers[k])
                    self.end_headers()
                    if head:
                        return
                    self.wfile.write(first)
                    for chunk in it:
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionError, OSError):
                    pass
                except Exception:
                    try:
                        self.send_response(502)
                        self.cors()
                        self.end_headers()
                    except Exception:
                        pass
                finally:
                    if r is not None:
                        relay.active.discard(r)
                        r.close()

        self._handler = H
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)  # lecture locale : pas d'ouverture réseau
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def start_lan(self):
        """Serveur accessible depuis le réseau local, créé seulement pour la diffusion Chromecast."""
        self.start()
        if self.srv_lan is None:
            self.srv_lan = ThreadingHTTPServer(("0.0.0.0", 0), self._handler)
            self.srv_lan.daemon_threads = True
            self.lan_port = self.srv_lan.server_address[1]
            threading.Thread(target=self.srv_lan.serve_forever, daemon=True).start()

    @staticmethod
    def wrap(url, host):
        return f"http://{host}/r?u={quote(url, safe='')}"

    def rewrite(self, text, base, host):
        out = []
        for line in text.splitlines():
            t = line.strip()
            if not t:
                out.append(line)
            elif t.startswith("#"):
                out.append(re.sub(r'URI="([^"]+)"',
                                  lambda m: 'URI="' + self.wrap(urljoin(base, m.group(1)), host) + '"', line))
            else:
                out.append(self.wrap(urljoin(base, t), host))
        return "\n".join(out) + "\n"

    def transcode_url(self, url, remote_host, plan):
        self.start_lan()
        self.start_hls(url, plan)
        return f"http://{local_ip(remote_host)}:{self.lan_port}/h/{self.hls_id}/index.m3u8"

    def url_for(self, url, remote_host):
        self.start_lan()
        return f"http://{local_ip(remote_host)}:{self.lan_port}/r?u={quote(url, safe='')}"


# ---------- mises à jour (GitHub Releases) ----------
def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) or (0,)


def is_installed():
    """Vrai si lancé depuis l'installation faite par KFluxTV-Setup (marqueur posé par le setup)."""
    return bool(getattr(sys, "frozen", False)) and os.path.exists(
        os.path.join(os.path.dirname(sys.executable), "KFluxTV.installed"))


def fetch_latest():
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=20,
                     headers={**HEADERS, "Accept": "application/vnd.github+json"})
    if r.status_code == 404:
        raise RuntimeError("Aucune version publiée n'est accessible (dépôt privé ou sans release).")
    r.raise_for_status()
    d = r.json()
    return dict(tag=d["tag_name"], ver=d["tag_name"].lstrip("vV"), notes=d.get("body") or "",
                page=d.get("html_url", ""),
                assets=[(a["name"], a["browser_download_url"], a.get("size", 0)) for a in d.get("assets", [])])


def pick_asset(assets, installed):
    key = "setup" if installed else "portable"
    return next((a for a in assets if key in a[0].lower() and a[0].lower().endswith(".exe")), None)


class Downloader(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    fail = Signal(str)

    def __init__(self, url, dest):
        super().__init__()
        self.url, self.dest = url, dest

    def run(self):
        try:
            with requests.get(self.url, headers=HEADERS, stream=True, timeout=30) as r:
                r.raise_for_status()
                total, got = int(r.headers.get("Content-Length", 0)), 0
                with open(self.dest, "wb") as f:
                    for chunk in r.iter_content(262144):
                        f.write(chunk)
                        got += len(chunk)
                        self.progress.emit(got, total)
            self.done.emit(self.dest)
        except Exception as e:
            self.fail.emit(str(e))


def launch_update(path, installed):
    """Lance l'installation de la mise à jour ; l'appli doit se fermer juste après."""
    flags = 0x00000008 | 0x00000200 | NOWIN  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    if installed:
        subprocess.Popen([path, "/S"], creationflags=flags, close_fds=True)
        return
    cur = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "kfluxtv_update.bat")
    with open(bat, "w", encoding="mbcs") as f:
        f.write(f"""@echo off
set n=0
:retry
move /y "{path}" "{cur}" >nul 2>&1 && goto ok
set /a n+=1
if %n% GEQ 60 goto end
ping -n 2 127.0.0.1 >nul
goto retry
:ok
start "" "{cur}"
:end
del "%~f0"
""")
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True)


class UpdateDialog(QDialog):
    def __init__(self, parent=None, latest=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP} — Mise à jour")
        self.setMinimumSize(520, 440)
        self.latest = None
        self.installed = is_installed()
        self.title = QLabel(f"Version installée : <b>{VERSION}</b> "
                            f"({'installée' if self.installed else 'portable'})")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.b_check = QPushButton("🔄  Rechercher")
        self.b_check.clicked.connect(self.check)
        self.b_go = QPushButton("⬇  Télécharger et installer")
        self.b_go.setObjectName("accent")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.download)
        b_close = QPushButton("Fermer")
        b_close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.b_check)
        row.addStretch()
        row.addWidget(self.b_go)
        row.addWidget(b_close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(self.status)
        lay.addWidget(self.notes, 1)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        self.jobs = []
        if latest:
            self.show_latest(latest)
        else:
            self.check()

    def check(self):
        self.b_check.setEnabled(False)
        self.status.setText("Recherche de mises à jour…")
        j = Job(fetch_latest)
        self.jobs.append(j)
        j.done.connect(self.show_latest)
        j.fail.connect(self.on_fail)
        j.start()

    def on_fail(self, m):
        self.b_check.setEnabled(True)
        self.status.setText(f"Impossible de vérifier : {m}")

    def show_latest(self, d):
        self.b_check.setEnabled(True)
        self.latest = d
        self.notes.setMarkdown(d["notes"] or "_Pas de notes de version._")
        newer = vtuple(d["ver"]) > vtuple(VERSION)
        self.asset = pick_asset(d["assets"], self.installed)
        if not newer:
            self.status.setText(f"✔ Tu as la dernière version ({VERSION}).")
            self.b_go.setEnabled(False)
        elif not self.asset:
            self.status.setText(f"Version {d['ver']} disponible, mais aucun fichier "
                                f"{'setup' if self.installed else 'portable'} n'est joint à la release.")
            self.b_go.setEnabled(False)
        else:
            self.status.setText(f"⬆ Nouvelle version disponible : <b>{d['ver']}</b> "
                                f"({self.asset[2] / 1e6:.0f} Mo).")
            self.b_go.setEnabled(True)

    def download(self):
        if not getattr(sys, "frozen", False):
            return QMessageBox.information(self, APP, "Mise à jour automatique disponible uniquement "
                                                      "dans la version .exe.")
        name, url, _ = self.asset
        dest = os.path.join(tempfile.gettempdir(), name)
        self.b_go.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText("Téléchargement…")
        dl = Downloader(url, dest)
        self.jobs.append(dl)
        dl.progress.connect(lambda g, t: (self.bar.setRange(0, t or 0), self.bar.setValue(g)))
        dl.done.connect(self.install)
        dl.fail.connect(lambda m: (self.status.setText(f"Échec du téléchargement : {m}"),
                                   self.b_go.setEnabled(True), self.bar.setVisible(False)))
        dl.start()

    def install(self, path):
        self.status.setText("Installation… l'application va redémarrer.")
        try:
            launch_update(path, self.installed)
        except Exception as e:
            return self.status.setText(f"Échec du lancement de la mise à jour : {e}")
        QApplication.quit()


class OptionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP} — Options")
        self.setMinimumWidth(460)
        st = load_settings()
        self.c_auto = QCheckBox("Se connecter automatiquement au dernier compte au démarrage")
        self.c_auto.setChecked(st.get("auto_connect", True))
        self.c_upd = QCheckBox("Rechercher les mises à jour au démarrage")
        self.c_upd.setChecked(st.get("auto_update", True))
        self.c_ts = QCheckBox("Direct différé : pause et retour arrière sur les chaînes en direct")
        self.c_ts.setChecked(st.get("timeshift", True))
        self.sp_ts = QSpinBox()
        self.sp_ts.setRange(5, 240)
        self.sp_ts.setSuffix(" min")
        self.sp_ts.setValue(int(st.get("ts_minutes", 60)))
        self.sp_margin = QSpinBox()
        self.sp_margin.setRange(3, 60)
        self.sp_margin.setSuffix(" s")
        self.sp_margin.setValue(int(st.get("ts_margin", 20)))
        self.sp_margin.setToolTip("Retard gardé sur le direct. Plus il est grand, moins il y a de saccades "
                                  "quand le fournisseur envoie les images par à-coups.")
        row_mg = QHBoxLayout()
        row_mg.addSpacing(24)
        row_mg.addWidget(QLabel("Délai de sécurité anti-saccades"))
        row_mg.addWidget(self.sp_margin)
        row_mg.addStretch()
        row_ts = QHBoxLayout()
        row_ts.addSpacing(24)
        row_ts.addWidget(QLabel("Retour arrière possible sur"))
        row_ts.addWidget(self.sp_ts)
        row_ts.addStretch()
        b_upd = QPushButton("🔄  Mises à jour…")
        b_upd.clicked.connect(lambda: UpdateDialog(self).exec())
        b_dir = QPushButton("📁  Ouvrir le dossier des données")
        b_dir.clicked.connect(lambda: os.startfile(DATA_DIR) if os.path.isdir(DATA_DIR) else None)
        about = QLabel(f"<b>{APP}</b> version {VERSION} — {'installée' if is_installed() else 'portable'}<br>"
                       f"<span style='color:#8b90a0'>Lecteur de flux Xtream Codes · github.com/{REPO}<br>"
                       f"Aucun contenu n'est fourni : utilise uniquement tes propres identifiants.</span>")
        about.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(about)
        lay.addWidget(self.c_auto)
        lay.addWidget(self.c_upd)
        lay.addWidget(self.c_ts)
        lay.addLayout(row_ts)
        lay.addLayout(row_mg)
        lay.addWidget(b_upd)
        lay.addWidget(b_dir)
        lay.addWidget(bb)

    def ok(self):
        st = load_settings()
        st["auto_connect"] = self.c_auto.isChecked()
        st["auto_update"] = self.c_upd.isChecked()
        st["timeshift"] = self.c_ts.isChecked()
        st["ts_minutes"] = self.sp_ts.value()
        st["ts_margin"] = self.sp_margin.value()
        save_settings(st)
        self.accept()


class CastListener:
    def __init__(self, sig):
        self.sig = sig

    def new_media_status(self, status):
        self.sig.emit(status.player_state or "", status.idle_reason or "")


class Main(QMainWindow):
    cast_event = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon(make_pixmap()))
        self.resize(1280, 760)
        self.profile = None
        self.semi = False
        self.cands, self.cand_i = [], 0
        self.local_cands = []
        self.fav_total, self.fav_missing = 0, False
        self.hc, self.hh = set(), set()  # clés masquées (préfixées par section)
        self.jobs = set()
        self.img_cache = {}
        self.card_tok = 0
        self.series_loading = set()
        self.info_cache = {}
        self.cast = None          # Chromecast connecté (None = lecture locale)
        self.cast_devs = {}
        self.relay = Relay()
        clean_stale_temp()
        self.zc = None
        self.cast_menu = QMenu("&Diffusion", self)
        self.cast_event.connect(self.on_cast_event)
        self.cast_timer = QTimer(self)
        self.cast_timer.timeout.connect(self.cast_tick)
        self.favs = set()         # favoris : "live:ID", "vod:ID", "series:ID"
        self.fav_groups = []      # groupes de favoris (ordre de création)
        self.fav_group_of = {}    # clé favori -> nom du groupe ("" = non classé)
        self.ts_active = False    # lecture locale via l'enregistrement tampon (direct différé)
        self.ts_failed = False
        self.ts_window_ms = 3600000
        self.ts_margin = 20000     # retard gardé sur le direct pour absorber les à-coups du fournisseur
        self.ts_url, self.ts_pending, self.ts_last = "", None, 0
        self.ts_base = 0           # instant d'enregistrement où le lecteur a (ré)ouvert la liste
        self.ts_autolive = False
        self.ts_hist = []
        self.ts_live, self.ts_live_shown, self.ts_rng = True, None, None
        self.ts_timer = QTimer(self)
        self.ts_timer.timeout.connect(self.ts_tick)

        # ----- panneau gauche -----
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Rechercher…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.filter())
        self.tabs = QTabWidget()
        self.sec = {}
        self.tree_sec = {}
        for key in SECTIONS:
            tree = self.make_tree()
            sec = Section(key, tree)
            self.sec[key] = sec
            self.tree_sec[tree] = sec
            self.tabs.addTab(tree, sec.title)
        self.fav_tree = self.make_tree(FavTree)
        self.fav_tree.on_drop = self.drop_on_group
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        b_grp = QPushButton("＋  Nouveau groupe")
        b_grp.setToolTip("Créer un groupe pour ranger tes favoris (glisse-dépose ensuite)")
        b_grp.clicked.connect(self.new_group_dialog)
        pl.addWidget(b_grp)
        pl.addWidget(self.fav_tree, 1)
        self.tabs.addTab(page, "⭐ Favoris")
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.currentChanged.connect(self.on_tab)

        self.card = QWidget()
        self.card.setObjectName("card")
        self.card.setFixedHeight(168)
        self.poster = QLabel()
        self.poster.setFixedSize(100, 148)
        self.poster.setAlignment(Qt.AlignCenter)
        self.poster.setStyleSheet("background:#232632;border-radius:8px;")
        self.card_title = QLabel("")
        self.card_title.setObjectName("cardtitle")
        self.card_title.setWordWrap(True)
        self.card_sub = QLabel("")
        self.card_sub.setObjectName("muted")
        self.card_sub.setWordWrap(True)
        self.card_plot = QLabel("")
        self.card_plot.setWordWrap(True)
        self.card_plot.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        txt = QVBoxLayout()
        txt.addWidget(self.card_title)
        txt.addWidget(self.card_sub)
        txt.addWidget(self.card_plot, 1)
        cl = QHBoxLayout(self.card)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.addWidget(self.poster)
        cl.addLayout(txt, 1)
        self.card.setStyleSheet("QWidget#card{background:#1b1d24;border-radius:12px;}")

        self.count = QLabel("")
        self.count.setObjectName("muted")
        self.left = QWidget()
        ll = QVBoxLayout(self.left)
        ll.setContentsMargins(10, 4, 4, 4)
        ll.setSpacing(8)
        b_exp = QPushButton("⊞")
        b_exp.setObjectName("round")
        b_exp.setToolTip("Tout développer (Ctrl+E)")
        b_exp.clicked.connect(self.expand_all)
        b_col = QPushButton("⊟")
        b_col.setObjectName("round")
        b_col.setToolTip("Tout réduire (Ctrl+Maj+E)")
        b_col.clicked.connect(self.collapse_all)
        srow = QHBoxLayout()
        srow.addWidget(self.search, 1)
        srow.addWidget(b_exp)
        srow.addWidget(b_col)
        ll.addLayout(srow)
        ll.addWidget(self.tabs, 1)
        ll.addWidget(self.card)
        ll.addWidget(self.count)

        # ----- lecteur -----
        self.video = VideoWidget()
        self.video.setStyleSheet("background:black;")
        self.video.toggleSemi.connect(self.toggle_semi)
        self.video.escape.connect(self.exit_semi)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.errorOccurred.connect(self.on_error)
        self.player.positionChanged.connect(self.on_pos)
        self.player.durationChanged.connect(self.on_dur)
        self.player.playbackStateChanged.connect(self.on_state)
        self.audio.setVolume(0.7)

        self.title = QLabel("Aucune lecture")
        self.title.setObjectName("title")
        self.b_play = QPushButton("▶")
        self.b_play.setObjectName("round")
        self.b_play.clicked.connect(self.toggle_play)
        b_stop = QPushButton("■")
        b_stop.setObjectName("round")
        b_stop.clicked.connect(self.stop)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setEnabled(False)
        self.seek.sliderMoved.connect(self.seek_to)
        self.seek.sliderReleased.connect(lambda: self.seek_to(self.seek.value()))
        self.t_cur = QLabel("--:--")
        self.t_end = QLabel("")
        self.t_cur.setObjectName("muted")
        self.t_end.setObjectName("muted")
        self.t_cur.setMinimumWidth(56)
        self.t_end.setMinimumWidth(120)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(70)
        self.vol.setMaximumWidth(120)
        self.vol.valueChanged.connect(self.set_volume)
        b_semi = QPushButton("⛶  Semi")
        b_semi.setToolTip("Semi plein écran : la vidéo remplit la fenêtre (F)")
        b_semi.clicked.connect(self.toggle_semi)
        b_fs = QPushButton("⤢  Plein")
        b_fs.setToolTip("Plein écran (F11)")
        b_fs.clicked.connect(lambda: self.video.setFullScreen(True))

        self.barw = QWidget()
        bl = QVBoxLayout(self.barw)
        bl.setContentsMargins(0, 6, 0, 0)
        self.epg = EpgBar()
        self.epg.setVisible(False)
        self.epg_tok = 0
        bl.addWidget(self.epg)
        r1 = QHBoxLayout()
        r1.addWidget(self.t_cur)
        r1.addWidget(self.seek, 1)
        r1.addWidget(self.t_end)
        r2 = QHBoxLayout()
        r2.addWidget(self.b_play)
        r2.addWidget(b_stop)
        b_back = QPushButton("↺ 10")
        b_back.setToolTip("Reculer de 10 secondes (Ctrl+←)")
        b_back.clicked.connect(lambda: self.skip(-10000))
        b_fwd = QPushButton("10 ↻")
        b_fwd.setToolTip("Avancer de 10 secondes (Ctrl+→)")
        b_fwd.clicked.connect(lambda: self.skip(10000))
        self.b_live = QPushButton("● DIRECT")
        self.b_live.setToolTip("Revenir au direct (Ctrl+L)")
        self.b_live.clicked.connect(self.go_live)
        self.b_live.setVisible(False)
        self.b_live.setFixedWidth(160)
        r2.addWidget(b_back)
        r2.addWidget(b_fwd)
        r2.addWidget(self.b_live)
        r2.addSpacing(8)
        r2.addWidget(self.title, 1)
        r2.addWidget(QLabel("🔊"))
        r2.addWidget(self.vol)
        r2.addSpacing(8)
        self.b_cast = QPushButton("📡  Diffusion")
        self.b_cast.setMenu(self.cast_menu)
        r2.addWidget(self.b_cast)
        r2.addWidget(b_semi)
        r2.addWidget(b_fs)
        bl.addLayout(r1)
        bl.addLayout(r2)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 10, 4)
        rl.addWidget(self.video, 1)
        rl.addWidget(self.barw)

        sp = QSplitter()
        sp.addWidget(self.left)
        sp.addWidget(right)
        sp.setSizes([440, 840])
        sp.setStretchFactor(1, 1)
        self.setCentralWidget(sp)

        self.build_menus()
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.fill_audio_menu)
        self.fill_audio_menu()
        self.fill_cast_menu()
        self.statusBar().showMessage("Prêt")
        QTimer.singleShot(0, self.auto_start)
        if load_settings().get("auto_update", True) and getattr(sys, "frozen", False):
            QTimer.singleShot(5000, self.startup_update_check)

    def make_tree(self, cls=QTreeWidget):
        t = cls()
        t.setHeaderHidden(True)
        t.setIndentation(16)
        t.setUniformRowHeights(True)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setContextMenuPolicy(Qt.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos, t=t: self.tree_menu(t, pos))
        t.itemActivated.connect(self.play_item)
        t.itemDoubleClicked.connect(self.play_item)
        t.itemExpanded.connect(self.on_expand)
        t.currentItemChanged.connect(lambda cur, _prev: self.show_card(cur))
        return t

    def cur_sec(self):
        """Section de l'onglet affiché (None pour l'onglet Favoris)."""
        i = self.tabs.currentIndex()
        return self.sec[list(SECTIONS)[i]] if i < len(SECTIONS) else None

    def cur_tree(self):
        return self.fav_tree if self.tabs.currentIndex() >= len(SECTIONS) else self.tabs.currentWidget()

    def run_job(self, fn, ok, err=None):
        j = Job(fn)
        self.jobs.add(j)
        j.done.connect(ok)
        j.fail.connect(err or (lambda m: self.statusBar().showMessage("Erreur : " + m)))
        j.finished.connect(lambda: self.jobs.discard(j))
        j.start()

    # ---------- menus ----------
    def act(self, menu, text, fn, shortcut=None, checkable=False):
        a = QAction(text, self)
        a.setCheckable(checkable)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(fn)
        menu.addAction(a)
        self.addAction(a)  # le raccourci reste actif même menu caché
        return a

    def build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Compte")
        self.act(m, "Gérer les comptes…", self.open_accounts, "Ctrl+P")
        self.act(m, "Informations du compte…", self.account_info)
        self.act(m, "Recharger", self.reload, "F5")
        m.addSeparator()
        self.act(m, "Se déconnecter", self.logout)
        self.act(m, "Quitter", self.close, "Ctrl+Q")
        m = mb.addMenu("C&ontenu")
        self.act_show = self.act(m, "Afficher les éléments masqués", lambda: self.build_tree(), checkable=True)
        self.act(m, "Gérer les éléments masqués…", self.manage_hidden)
        m.addSeparator()
        self.act(m, "Ajouter / retirer des favoris", self.toggle_fav_selected, "Ctrl+D")
        self.act(m, "Tout développer", self.expand_all, "Ctrl+E")
        self.act(m, "Tout réduire", self.collapse_all, "Ctrl+Shift+E")
        m = mb.addMenu("&Lecture")
        self.act(m, "Lecture / Pause", self.toggle_play, "Space")
        self.act(m, "Revenir au direct", self.go_live, "Ctrl+L")
        self.act(m, "Avancer de 10 s", lambda: self.skip(10000), "Ctrl+Right")
        self.act(m, "Reculer de 10 s", lambda: self.skip(-10000), "Ctrl+Left")
        self.audio_menu = mb.addMenu("A&udio")
        mb.addMenu(self.cast_menu)
        m = mb.addMenu("&Affichage")
        self.act_semi = self.act(m, "Mode semi plein écran", self.toggle_semi, "F", checkable=True)
        self.act(m, "Plein écran", lambda: self.video.setFullScreen(True), "F11")
        self.act(m, "Quitter le semi plein écran", self.exit_semi, "Esc")
        m = mb.addMenu("&Options")
        self.act(m, "Paramètres…", lambda: OptionsDialog(self).exec(), "Ctrl+,")
        self.act(m, "Mises à jour…", lambda: UpdateDialog(self).exec())

    # ---------- sortie audio ----------
    def fill_audio_menu(self):
        m = self.audio_menu
        m.clear()
        saved = load_settings().get("audio_device")
        devs = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        group = QActionGroup(self)
        group.setExclusive(True)
        self.audio_group = group
        chosen = next((d for d in devs if bytes(d.id()).hex() == saved), None)
        for label, dev in [("Périphérique par défaut du système", None)] + [(d.description(), d) for d in devs]:
            a = QAction(label, self, checkable=True)
            a.setChecked(dev is chosen)
            a.triggered.connect(lambda _=False, d=dev: self.set_audio_device(d))
            group.addAction(a)
            m.addAction(a)
        m.addSeparator()
        self.act_mute = QAction("Couper le son", self, checkable=True)
        self.act_mute.setShortcut(QKeySequence("M"))
        self.act_mute.setChecked(self.audio.isMuted())
        self.act_mute.toggled.connect(self.set_muted)
        m.addAction(self.act_mute)
        self.addAction(self.act_mute)
        self.audio.setDevice(chosen or default)

    def set_audio_device(self, dev):
        s = load_settings()
        s["audio_device"] = bytes(dev.id()).hex() if dev else None
        save_settings(s)
        self.audio.setDevice(dev or QMediaDevices.defaultAudioOutput())
        self.statusBar().showMessage("Sortie audio : " + (dev.description() if dev else "défaut"))

    # ---------- semi plein écran ----------
    def toggle_semi(self):
        self.semi = not self.semi
        self.apply_semi()

    def exit_semi(self):
        if self.semi:
            self.semi = False
            self.apply_semi()

    def apply_semi(self):
        v = not self.semi
        for w in (self.left, self.barw, self.menuBar(), self.statusBar()):
            w.setVisible(v)
        self.act_semi.setChecked(self.semi)

    # ---------- comptes ----------
    def auto_start(self):
        """Se connecte directement au dernier compte utilisé (ou au premier)."""
        profs = load_profiles()
        if not profs or not load_settings().get("auto_connect", True):
            self.statusBar().showMessage("Aucun compte connecté : menu Compte, Gérer les comptes (Ctrl+P).")
            return
        last = load_settings().get("last_profile")
        self.use_profile(next((p for p in profs if p["name"] == last), profs[0]))

    def startup_update_check(self):
        def ok(d):
            if vtuple(d["ver"]) > vtuple(VERSION):
                self.statusBar().showMessage(f"Mise à jour {d['ver']} disponible.")
                UpdateDialog(self, latest=d).exec()

        self.run_job(fetch_latest, ok, lambda m: None)

    def use_profile(self, p):
        self.profile = p
        self.hc = set(p.get("hidden_cats", []))
        self.hh = set(p.get("hidden_chs", []))
        self.favs = set(p.get("favs", []))
        self.fav_groups = list(p.get("fav_groups", []))
        self.fav_group_of = dict(p.get("fav_group_of", {}))
        st = load_settings()
        st["last_profile"] = p["name"]
        save_settings(st)
        self.setWindowTitle(f"{APP} — {p['name']}")
        self.tabs.setCurrentIndex(0)
        self.reload()

    def open_accounts(self):
        d = ProfilesDialog(self, self.profile["name"] if self.profile else None)
        if d.exec() and d.selected:
            self.use_profile(d.selected)
        elif self.profile:
            if next((p for p in load_profiles() if p["name"] == self.profile["name"]), None) is None:
                self.logout()

    def logout(self):
        self.player.stop()
        self.profile = None
        self.clear_sections()
        self.title.setText("Aucune lecture")
        self.count.setText("")
        self.setWindowTitle(APP)
        self.statusBar().showMessage("Déconnecté")

    def clear_sections(self):
        for s in self.sec.values():
            s.cats, s.items, s.loaded, s.loading = [], [], False, False
            s.tree.clear()
        self.series_loading.clear()
        self.info_cache.clear()
        self.fav_tree.clear()
        self.fav_total, self.fav_missing = 0, False
        self.show_card(None)

    def account_info(self):
        if not self.profile:
            return QMessageBox.information(self, APP, "Aucun compte connecté.")
        p = self.profile
        self.statusBar().showMessage("Lecture des infos du compte…")
        self.run_job(lambda: api(p), self.show_info, lambda m: QMessageBox.critical(self, APP, m))

    def show_info(self, data):
        self.statusBar().showMessage("Prêt")
        ui = data.get("user_info", {}) if isinstance(data, dict) else {}
        si = data.get("server_info", {}) if isinstance(data, dict) else {}
        lines = [
            f"Profil : {self.profile['name']}",
            f"Serveur : {self.profile['server']}",
            f"Utilisateur : {ui.get('username', self.profile['user'])}",
            f"Statut : {ui.get('status', '?')}",
            f"Expiration : {fmt_ts(ui.get('exp_date'))}",
            f"Créé le : {fmt_ts(ui.get('created_at'))}",
            f"Essai : {'oui' if str(ui.get('is_trial')) == '1' else 'non'}",
            f"Connexions : {ui.get('active_cons', '?')} / {ui.get('max_connections', '?')}",
            f"Formats : {', '.join(ui.get('allowed_output_formats', []) or ['?'])}",
            f"Fuseau serveur : {si.get('timezone', '?')}",
        ]
        QMessageBox.information(self, "Informations du compte", "\n".join(lines))

    # ---------- chargement ----------
    def reload(self):
        if not self.profile:
            return self.open_accounts()
        self.clear_sections()
        self.load_section("live")
        cs = self.cur_sec()
        if cs is None:
            self.ensure_fav_sections()
        elif cs.key != "live":
            self.load_section(cs.key)

    def on_tab(self, _):
        sec = self.cur_sec()
        if sec is None:
            self.ensure_fav_sections()
            self.build_favs()
        else:
            if self.profile and not sec.loaded and not sec.loading:
                self.load_section(sec.key)
            self.refresh_stars(sec)
        self.filter()
        self.update_count()
        self.show_card(self.cur_tree().currentItem())

    def load_section(self, key):
        sec, p = self.sec[key], self.profile
        sec.loading = True
        self.statusBar().showMessage(f"Chargement : {sec.title.strip()}…")

        def work():
            if key == "live":
                return load_all(p)
            cats, items = api(p, sec.cat), api(p, sec.lst)
            if not isinstance(cats, list) or not isinstance(items, list):
                raise RuntimeError("Réponse serveur invalide (section non disponible ?).")
            return cats, items

        def ok(data):
            sec.loading = False
            if self.profile is not p:
                return
            sec.cats, sec.items = data
            sec.loaded = True
            self.build_tree(key)
            self.statusBar().showMessage("Prêt")

        def fail(m):
            sec.loading = False
            self.statusBar().showMessage("Erreur")
            QMessageBox.critical(self, APP, f"{sec.title.strip()} : {m}")

        self.run_job(work, ok, fail)

    # ---------- arbre ----------
    def build_tree(self, key=None):
        for k, sec in self.sec.items():
            if (key is None or k == key) and sec.loaded:
                self.fill_tree(sec)
        self.build_favs()
        self.filter()
        self.update_count()

    def item_text(self, sec, s, star=True):
        sid = str(s.get(sec.id))
        t = ("★ " if star and f"{sec.key}:{sid}" in self.favs else "") + (s.get("name") or "?")
        return t + ("  [masqué]" if (sec.pre + sid) in self.hh else "")

    def new_item(self, parent, sec, s, star=True, icon=""):
        sid = str(s.get(sec.id))
        it = QTreeWidgetItem(parent, [(icon + " " if icon else "") + self.item_text(sec, s, star)])
        it.setData(0, KIND, sec.kind)
        it.setData(0, KEY, sid)
        it.setData(0, DATA, s)
        if sec.kind == "series":
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return it

    def fill_tree(self, sec):
        show = self.act_show.isChecked()
        pre = sec.pre
        tree = sec.tree
        tree.setUpdatesEnabled(False)
        tree.clear()
        cats = [(str(c.get("category_id")), c.get("category_name", "?")) for c in sec.cats]
        known = {cid for cid, _ in cats}
        by = {}
        for s in sorted(sec.items, key=lambda x: (x.get("name") or "").lower()):
            cid = str(s.get("category_id"))
            by.setdefault(cid if cid in known else "none", []).append(s)
        if "none" in by:
            cats.append(("none", "Sans catégorie"))
        grey = QBrush(QColor(130, 130, 130))
        ncat = nitem = nhid = 0
        for cid, name in cats:
            chans = by.get(cid, [])
            cat_hidden = (pre + cid) in self.hc
            kids = [s for s in chans if show or (pre + str(s.get(sec.id))) not in self.hh]
            if not show:
                nhid += len(chans) if cat_hidden else len(chans) - len(kids)
            if (cat_hidden and not show) or not kids:
                continue
            top = QTreeWidgetItem(tree, [f"{name}  ({len(kids)})" + ("  [masqué]" if cat_hidden else "")])
            top.setData(0, KIND, "cat")
            top.setData(0, KEY, cid)
            if cat_hidden:
                top.setForeground(0, grey)
            ncat += 1
            for s in kids:
                it = self.new_item(top, sec, s)
                if cat_hidden or (pre + str(s.get(sec.id))) in self.hh:
                    it.setForeground(0, grey)
                nitem += 1
        sec.stats = (ncat, nitem, nhid)
        tree.setUpdatesEnabled(True)

    def update_count(self):
        sec = self.cur_sec()
        if sec is None:
            self.count.setText(f"{self.fav_total} favoris" + (" · chargement…" if self.fav_missing else ""))
            return
        if not sec.loaded:
            self.count.setText("Chargement…" if sec.loading else "")
            return
        c, n, h = sec.stats
        self.count.setText(f"{c} catégories · {n} éléments · {h} masqués")

    def filter(self):
        t = self.search.text().lower().strip()
        tree = self.cur_tree()
        for i in range(tree.topLevelItemCount()):
            cat = tree.topLevelItem(i)
            vis = 0
            for j in range(cat.childCount()):
                c = cat.child(j)
                show = not t or t in c.text(0).lower()
                c.setHidden(not show)
                vis += show
            cat.setHidden(bool(t) and vis == 0)
            if t and vis:
                cat.setExpanded(True)

    # ---------- séries (chargement à la demande) ----------
    def on_expand(self, item):
        if item.data(0, KIND) != "series" or item.childCount() or not self.profile:
            return
        sid = item.data(0, KEY)
        if sid in self.series_loading:
            return
        self.series_loading.add(sid)
        p = self.profile
        self.statusBar().showMessage("Chargement des épisodes…")

        def ok(data):
            self.series_loading.discard(sid)
            if self.profile is not p:
                return
            self.add_episodes(item, data)
            self.statusBar().showMessage("Prêt")

        def fail(m):
            self.series_loading.discard(sid)
            self.statusBar().showMessage("Erreur : " + m)

        self.run_job(lambda: requests.get(
            f"{p['server']}/player_api.php",
            params={"username": p["user"], "password": p["pwd"], "action": "get_series_info", "series_id": sid},
            headers=HEADERS, timeout=60).json(), ok, fail)

    def add_episodes(self, item, data):
        seasons = norm_eps(data.get("episodes") if isinstance(data, dict) else None)
        if not seasons:
            item.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            return self.statusBar().showMessage("Aucun épisode disponible pour cette série.")
        for num, eps in seasons:
            si = QTreeWidgetItem(item, [f"Saison {num}  ({len(eps)})"])
            si.setData(0, KIND, "season")
            for ep in sorted(eps, key=lambda e: as_int(e.get("episode_num"))):
                label = f"{ep.get('episode_num', '?')}.  {ep.get('title') or 'Épisode'}"
                ei = QTreeWidgetItem(si, [label])
                ei.setData(0, KIND, "ep")
                ei.setData(0, KEY, str(ep.get("id")))
                ei.setData(0, DATA, ep)
            if len(seasons) == 1:
                si.setExpanded(True)
        item.setExpanded(True)

    # ---------- masquage ----------
    HIDEABLE = ("cat", "ch", "movie", "series")

    def key_of(self, sec, it):
        return sec.pre + it.data(0, KEY)

    def is_hidden(self, sec, it):
        return self.key_of(sec, it) in (self.hc if it.data(0, KIND) == "cat" else self.hh)

    def save_hidden(self):
        if not self.profile:
            return
        profs = load_profiles()
        for p in profs:
            if p["name"] == self.profile["name"]:
                p["hidden_cats"] = sorted(self.hc)
                p["hidden_chs"] = sorted(self.hh)
        save_profiles(profs)

    def set_hidden(self, sec, items, hide):
        for it in items:
            s = self.hc if it.data(0, KIND) == "cat" else self.hh
            (s.add if hide else s.discard)(self.key_of(sec, it))
        self.save_hidden()
        self.build_tree(sec.key)

    def tree_menu(self, tree, pos):
        isfav = tree is self.fav_tree
        sel = tree.selectedItems()
        items = [i for i in sel if i.data(0, KIND) in FAV_KIND]
        groups = [i for i in sel if i.data(0, KIND) == "group"]
        m = QMenu(self)
        acts = {}

        def add(menu, text, fn):
            a = menu.addAction(text)
            acts[a] = fn
            return a

        if isfav:
            if items:
                sub = m.addMenu("📁  Déplacer vers")
                add(sub, "⭐  Non classés", lambda: self.move_to_group(items, ""))
                for g in self.fav_groups:
                    add(sub, f"📁  {g}", lambda g=g: self.move_to_group(items, g))
                sub.addSeparator()
                add(sub, "＋  Nouveau groupe…", lambda: self.move_to_new_group(items))
                add(m, "☆  Retirer des favoris", lambda: self.set_fav(items, False))
            if len(groups) == 1 and groups[0].data(0, KEY):
                g = groups[0].data(0, KEY)
                add(m, "✏  Renommer le groupe…", lambda: self.rename_group(g))
                add(m, "🗑  Supprimer le groupe", lambda: self.delete_group(g))
            if m.actions():
                m.addSeparator()
            add(m, "＋  Nouveau groupe…", self.new_group_dialog)
        else:
            hide_items = [i for i in sel if i.data(0, KIND) in self.HIDEABLE]
            if not items and not hide_items:
                return
            if items:
                if all(self.fav_key(i) in self.favs for i in items):
                    add(m, "☆  Retirer des favoris", lambda: self.set_fav(items, False))
                else:
                    add(m, "⭐  Ajouter aux favoris", lambda: self.set_fav(items, True, ""))
                    sub = m.addMenu("📁  Ajouter au groupe")
                    for g in self.fav_groups:
                        add(sub, f"📁  {g}", lambda g=g: self.set_fav(items, True, g))
                    if self.fav_groups:
                        sub.addSeparator()
                    add(sub, "＋  Nouveau groupe…", lambda: self.add_to_new_group(items))
            if hide_items:
                sec = self.tree_sec[tree]
                all_hidden = all(self.is_hidden(sec, i) for i in hide_items)
                add(m, "Réafficher" if all_hidden else "Masquer",
                    lambda: self.set_hidden(sec, hide_items, not all_hidden))
        r = m.exec(tree.viewport().mapToGlobal(pos))
        if r in acts:
            acts[r]()

    # ---------- favoris ----------
    def fav_key(self, it):
        return f"{FAV_KIND[it.data(0, KIND)]}:{it.data(0, KEY)}"

    def save_favs(self):
        if not self.profile:
            return
        profs = load_profiles()
        for p in profs:
            if p["name"] == self.profile["name"]:
                p["favs"] = sorted(self.favs)
                p["fav_groups"] = list(self.fav_groups)
                p["fav_group_of"] = {k: g for k, g in self.fav_group_of.items() if k in self.favs and g}
        save_profiles(profs)

    def group_of(self, key):
        g = self.fav_group_of.get(key, "")
        return g if g in self.fav_groups else ""

    def set_fav(self, items, add, group=None):
        for it in items:
            k = self.fav_key(it)
            if add:
                self.favs.add(k)
                if group is not None:
                    self.fav_group_of[k] = group
            else:
                self.favs.discard(k)
                self.fav_group_of.pop(k, None)
        self.after_fav_change()
        self.statusBar().showMessage("Ajouté aux favoris." if add else "Retiré des favoris.")

    def after_fav_change(self):
        self.save_favs()
        sec = self.cur_sec()
        if sec:
            self.refresh_stars(sec)
        self.build_favs()
        self.update_count()

    def toggle_fav_selected(self):
        items = [i for i in self.cur_tree().selectedItems() if i.data(0, KIND) in FAV_KIND]
        if not items:
            return self.statusBar().showMessage("Sélectionne une chaîne, un film ou une série.")
        self.set_fav(items, not all(self.fav_key(i) in self.favs for i in items), "")

    def new_group(self):
        """Demande un nom et crée le groupe ; renvoie le nom (ou None)."""
        name, ok = QInputDialog.getText(self, "Nouveau groupe", "Nom du groupe de favoris :")
        name = name.strip()
        if not ok or not name:
            return None
        if name in self.fav_groups:
            self.statusBar().showMessage(f"Le groupe « {name} » existe déjà.")
            return name
        self.fav_groups.append(name)
        self.save_favs()
        return name  # l'arbre est reconstruit par l'appelant (les éléments sélectionnés restent valides)

    def new_group_dialog(self):
        if self.new_group() is not None:
            self.build_favs()

    def add_to_new_group(self, items):
        g = self.new_group()
        if g is not None:
            self.set_fav(items, True, g)

    def move_to_new_group(self, items):
        g = self.new_group()
        if g is not None:
            self.move_to_group(items, g)

    def move_to_group(self, items, group):
        for it in items:
            k = self.fav_key(it)
            if group:
                self.fav_group_of[k] = group
            else:
                self.fav_group_of.pop(k, None)
        self.after_fav_change()

    def drop_on_group(self, keys, group):
        for k in keys:
            if group:
                self.fav_group_of[k] = group
            else:
                self.fav_group_of.pop(k, None)
        self.after_fav_change()

    def rename_group(self, old):
        name, ok = QInputDialog.getText(self, "Renommer le groupe", "Nouveau nom :", text=old)
        name = name.strip()
        if not ok or not name or name == old:
            return
        if name in self.fav_groups:
            return self.statusBar().showMessage(f"Le groupe « {name} » existe déjà.")
        self.fav_groups[self.fav_groups.index(old)] = name
        self.fav_group_of = {k: (name if g == old else g) for k, g in self.fav_group_of.items()}
        self.after_fav_change()

    def delete_group(self, g):
        if QMessageBox.question(self, APP, f"Supprimer le groupe « {g} » ?\n"
                                           "Ses favoris sont conservés dans « Non classés ».") != QMessageBox.Yes:
            return
        self.fav_groups.remove(g)
        self.fav_group_of = {k: v for k, v in self.fav_group_of.items() if v != g}
        self.after_fav_change()

    def refresh_stars(self, sec):
        """Remet à jour l'étoile ★ devant les éléments de la liste affichée."""
        t = sec.tree
        t.setUpdatesEnabled(False)
        for i in range(t.topLevelItemCount()):
            top = t.topLevelItem(i)
            for j in range(top.childCount()):
                c = top.child(j)
                d = c.data(0, DATA)
                if d:
                    txt = self.item_text(sec, d)
                    if c.text(0) != txt:
                        c.setText(0, txt)
        t.setUpdatesEnabled(True)

    def ensure_fav_sections(self):
        for key in SECTIONS:
            sec = self.sec[key]
            if self.profile and not sec.loaded and not sec.loading and any(
                    k.startswith(key + ":") for k in self.favs):
                self.load_section(key)

    def build_favs(self):
        t = self.fav_tree
        opened = {t.topLevelItem(i).data(0, KEY): t.topLevelItem(i).isExpanded()
                  for i in range(t.topLevelItemCount())}
        t.setUpdatesEnabled(False)
        t.clear()
        self.fav_total, self.fav_missing = 0, False
        by = {g: [] for g in self.fav_groups}
        by[""] = []
        icons = {"live": "📺", "vod": "🎬", "series": "🍿"}
        for key in ("live", "vod", "series"):
            ids = {k.split(":", 1)[1] for k in self.favs if k.startswith(key + ":")}
            if not ids:
                continue
            sec = self.sec[key]
            if not sec.loaded:
                self.fav_missing = True
                continue
            for x in sec.items:
                sid = str(x.get(sec.id))
                if sid in ids:
                    by[self.group_of(f"{key}:{sid}")].append((sec, x))
        for g in self.fav_groups + [""]:
            items = sorted(by[g], key=lambda sx: (sx[1].get("name") or "").lower())
            if not g and not items:
                continue
            title = f"📁  {g}" if g else "⭐  Non classés"
            top = QTreeWidgetItem(t, [f"{title}  ({len(items)})"])
            top.setData(0, KIND, "group")
            top.setData(0, KEY, g)
            for sec, x in items:
                self.new_item(top, sec, x, star=False, icon=icons[sec.key])
            self.fav_total += len(items)
            top.setExpanded(opened.get(g, True))
        t.setUpdatesEnabled(True)
        if self.tabs.currentIndex() == len(SECTIONS):
            self.filter()

    def expand_all(self):
        t = self.cur_tree()
        t.setUpdatesEnabled(False)
        for i in range(t.topLevelItemCount()):
            it = t.topLevelItem(i)
            if not it.isHidden():
                it.setExpanded(True)  # catégories seulement (les séries restent repliées)
        t.setUpdatesEnabled(True)

    def collapse_all(self):
        self.cur_tree().collapseAll()

    def manage_hidden(self):
        names, cn = {}, {}
        for sec in self.sec.values():
            for s in sec.items:
                names[sec.pre + str(s.get(sec.id))] = f"[{sec.tag}] {s.get('name') or '?'}"
            for c in sec.cats:
                cn[sec.pre + str(c.get("category_id"))] = f"[Catégorie · {sec.tag}] {c.get('category_name', '?')}"
        entries = [("cat", k, cn.get(k, f"[Catégorie] {k}")) for k in sorted(self.hc)]
        entries += [("item", k, names.get(k, f"[?] {k}")) for k in sorted(self.hh)]
        if not entries:
            return QMessageBox.information(self, APP, "Aucun élément masqué.")
        d = HiddenDialog(self, entries)
        if d.exec():
            for kind, key in d.restore:
                (self.hc if kind == "cat" else self.hh).discard(key)
            self.save_hidden()
            self.build_tree()

    # ---------- fiche (affiche / description) ----------
    def show_card(self, it):
        self.card_tok += 1
        tok = self.card_tok
        self.poster.clear()
        for l in (self.card_title, self.card_sub, self.card_plot):
            l.setText("")
        if it is None:
            return
        kind, d = it.data(0, KIND), it.data(0, DATA) or {}
        if kind not in ("ch", "movie", "series", "ep"):
            self.card_title.setText(it.text(0))
            return
        info = d.get("info") if kind == "ep" and isinstance(d.get("info"), dict) else d
        title = clean_title(it.text(0))
        sub, plot = [], ""
        if kind == "ch":
            sub.append("Chaîne en direct")
            img = d.get("stream_icon")
        elif kind == "series":
            sub += [str(x) for x in (d.get("genre"), d.get("releaseDate") or d.get("release_date")) if x]
            if d.get("rating") and str(d.get("rating")) not in ("0", "0.0"):
                sub.append(f"★ {d['rating']}")
            plot, img = d.get("plot") or "", d.get("cover")
        elif kind == "ep":
            sub += [str(x) for x in (info.get("duration"),) if x]
            plot, img = info.get("plot") or "", info.get("movie_image")
        else:
            if d.get("rating") and str(d.get("rating")) not in ("0", "0.0"):
                sub.append(f"★ {d['rating']}")
            img = d.get("stream_icon")
            self.fetch_vod_info(str(d.get("stream_id")), tok)
        self.card_title.setText(title)
        self.card_sub.setText("  ·  ".join(sub))
        self.card_plot.setText(plot[:420] + ("…" if len(plot) > 420 else ""))
        self.load_image(img, tok)

    def fetch_vod_info(self, vid, tok):
        p = self.profile

        def apply(data):
            if tok != self.card_tok:
                return
            inf = data.get("info", {}) if isinstance(data, dict) else {}
            sub = [self.card_sub.text()] if self.card_sub.text() else []
            sub += [str(x) for x in (inf.get("genre"), inf.get("releasedate"), inf.get("duration")) if x]
            self.card_sub.setText("  ·  ".join(dict.fromkeys(sub)))
            plot = inf.get("plot") or inf.get("description") or ""
            self.card_plot.setText(plot[:420] + ("…" if len(plot) > 420 else ""))
            if not self.poster.pixmap() or self.poster.pixmap().isNull():
                self.load_image(inf.get("movie_image") or inf.get("cover_big"), tok)

        if vid in self.info_cache:
            return apply(self.info_cache[vid])

        def ok(data):
            self.info_cache[vid] = data
            apply(data)

        self.run_job(lambda: requests.get(
            f"{p['server']}/player_api.php",
            params={"username": p["user"], "password": p["pwd"], "action": "get_vod_info", "vod_id": vid},
            headers=HEADERS, timeout=30).json(), ok, lambda m: None)

    def load_image(self, url, tok):
        if not url or not str(url).startswith("http"):
            return
        if url in self.img_cache:
            return self.set_poster(self.img_cache[url], tok)

        def ok(b):
            pm = QPixmap()
            if b and pm.loadFromData(b):
                self.img_cache[url] = pm
                self.set_poster(pm, tok)

        self.run_job(lambda: requests.get(url, headers=HEADERS, timeout=15).content, ok, lambda m: None)

    def set_poster(self, pm, tok):
        if tok == self.card_tok:
            self.poster.setPixmap(pm.scaled(self.poster.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


    # ---------- Chromecast ----------
    CTYPES = {"m3u8": "application/x-mpegURL", "ts": "video/mp2t", "mp4": "video/mp4",
              "mkv": "video/x-matroska", "avi": "video/x-msvideo", "mov": "video/quicktime"}

    def fill_cast_menu(self):
        m = self.cast_menu
        m.clear()
        if pychromecast is None:
            m.addAction("Chromecast indisponible").setEnabled(False)
            return
        grp = QActionGroup(self)
        grp.setExclusive(True)
        self.cast_group = grp
        a = QAction("🖥  Ce PC (lecture locale)", self, checkable=True)
        a.setChecked(self.cast is None)
        a.triggered.connect(lambda _=False: self.cast_disconnect())
        grp.addAction(a)
        m.addAction(a)
        for name, info in sorted(self.cast_devs.items()):
            a = QAction(f"📺  {name}", self, checkable=True)
            a.setChecked(self.cast is not None and self.cast.name == name)
            a.triggered.connect(lambda _=False, i=info: self.cast_connect(i))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        m.addAction("🔄  Rechercher les appareils…", self.cast_scan)
        sub = m.addMenu("⚙  Compatibilité TV (transcodage)")
        mg = QActionGroup(self)
        mg.setExclusive(True)
        self.mode_group = mg
        cur = load_settings().get("cast_mode", "auto")
        for key, label in (("auto", "Automatique (recommandé)"), ("always", "Toujours convertir"),
                           ("never", "Jamais (flux direct)")):
            a = QAction(label, self, checkable=True)
            a.setChecked(cur == key)
            a.triggered.connect(lambda _=False, k=key: self.set_cast_mode(k))
            mg.addAction(a)
            sub.addAction(a)
        self.b_cast.setStyleSheet("background:#7c5cff;color:white;" if self.cast else "")
        self.b_cast.setText(f"📡  {self.cast.name}" if self.cast else "📡  Diffusion")

    def cast_scan(self):
        self.statusBar().showMessage("Recherche d'appareils Chromecast sur le réseau…")

        def work():
            # stop_discovery() ferme l'instance zeroconf : on en crée une dédiée à la recherche
            browser = pychromecast.discovery.CastBrowser(
                pychromecast.discovery.SimpleCastListener(lambda uuid, service: None), zeroconf.Zeroconf())
            browser.start_discovery()
            time.sleep(6)
            infos = list(browser.devices.values())
            browser.stop_discovery()
            return infos

        def ok(casts):
            self.cast_devs.update({c.friendly_name: c for c in casts})
            self.fill_cast_menu()
            n = len(casts)
            self.statusBar().showMessage(
                f"{n} appareil(s) Chromecast trouvé(s). Choisis-le dans le menu Diffusion." if n
                else "Aucun appareil Chromecast trouvé (même réseau Wi-Fi ? pare-feu Windows ?).")
            if n:
                self.cast_menu.popup(self.b_cast.mapToGlobal(self.b_cast.rect().topLeft())
                                     if self.b_cast.isVisible() else self.cursor().pos())

        self.run_job(work, ok, lambda m: self.statusBar().showMessage("Erreur de recherche : " + m))

    def cast_connect(self, info):
        self.statusBar().showMessage(f"Connexion à {info.friendly_name}…")

        def work():
            zc = zeroconf.Zeroconf()
            c = pychromecast.get_chromecast_from_cast_info(info, zc)
            c._zc = zc
            try:
                c.wait(timeout=20)
            except Exception:
                c.disconnect(timeout=0)
                zc.close()
                raise
            return c

        def ok(c):
            old = self.cast
            if old is not None:
                self.cast_release(old)
            self.player.stop()
            self.end_timeshift()
            self.cast = c
            c.media_controller.register_status_listener(CastListener(self.cast_event))
            try:
                c.set_volume(self.vol.value() / 100)
            except Exception:
                pass
            self.cast_timer.start(1000)
            self.fill_cast_menu()
            self.statusBar().showMessage(f"Diffusion sur {c.name} : choisis une chaîne ou un film.")
            if self.cands:
                self.start()

        def fail(m):
            self.fill_cast_menu()
            self.statusBar().showMessage(f"Connexion à {info.friendly_name} impossible : {m}")

        self.run_job(work, ok, fail)

    def cast_release(self, c):
        def work():
            try:
                c.quit_app()
            except Exception:
                pass
            c.disconnect()
            try:
                c._zc.close()
            except Exception:
                pass
        self.run_job(work, lambda _: None, lambda m: None)

    def cast_disconnect(self):
        c, self.cast = self.cast, None
        self.cast_timer.stop()
        self.relay.stop_all()  # libère les connexions du fournisseur utilisées par la TV
        if c is not None:
            self.cast_release(c)
        self.fill_cast_menu()
        self.on_dur(0)
        self.statusBar().showMessage("Retour sur ce PC…")
        if self.cands:
            # petite pause : le fournisseur limite souvent le nombre de connexions simultanées
            QTimer.singleShot(2500, self.resume_local)
        else:
            self.statusBar().showMessage("Lecture locale sur ce PC")

    def resume_local(self):
        if self.cast is None and self.cands:
            self.cand_i = 0
            self.start()
            self.statusBar().showMessage("Lecture locale sur ce PC")

    def set_cast_mode(self, mode):
        st = load_settings()
        st["cast_mode"] = mode
        save_settings(st)
        if self.cast and self.cands:
            self.start()

    def cast_play(self):
        url, c = self.cands[self.cand_i], self.cast
        ext = url.rsplit(".", 1)[-1].lower()
        ctype, live = self.CTYPES.get(ext, "video/mp4"), "/live/" in url
        title = self.title.text()
        self.statusBar().showMessage(f"Envoi vers {c.name}…")

        def work():
            mc = c.media_controller
            ct = ctype
            plan = None
            if live:
                src = url[:-5] + ".ts" if url.endswith(".m3u8") else url
                plan = plan_transcode(src, load_settings().get("cast_mode", "auto"))
            if plan:
                purl = self.relay.transcode_url(src, c.cast_info.host, plan)
                ct = "application/x-mpegURL"
            else:
                self.relay.stop_hls()
                purl = self.relay.url_for(url, c.cast_info.host)
            mc.play_media(purl, ct, title=title, stream_type="LIVE" if live else "BUFFERED")
            mc.block_until_active(15)

        self.run_job(work, lambda _: self.statusBar().showMessage(f"Diffusion sur {c.name}"),
                     lambda m: self.statusBar().showMessage(f"Erreur Chromecast : {m}"))

    def on_cast_event(self, state, reason):
        self.b_play.setText("⏸" if state in ("PLAYING", "BUFFERING") else "▶")
        if state == "IDLE" and reason == "ERROR" and self.cast:
            if self.cand_i + 1 < len(self.cands):
                self.cand_i += 1
                self.cast_play()
            else:
                self.statusBar().showMessage("La TV n'a pas pu lire ce flux (format non pris en charge ?).")

    def cast_tick(self):
        if not self.cast:
            return
        try:
            st = self.cast.media_controller.status
        except Exception:
            return
        if not st or st.player_state in ("IDLE", "UNKNOWN"):
            return
        dur = st.duration or 0
        pos = st.adjusted_current_time or 0
        if dur > 0:
            self.seek.setRange(0, int(dur * 1000))
            self.seek.setEnabled(True)
            if not self.seek.isSliderDown():
                self.seek.setValue(int(pos * 1000))
            self.t_cur.setText(fmt_ms(pos * 1000))
            self.t_end.setText(fmt_ms(dur * 1000))
        else:
            self.on_dur(0)

    def closeEvent(self, e):
        self.relay.stop_all()
        if self.cast:
            try:
                self.cast.quit_app()
                self.cast.disconnect()
            except Exception:
                pass
        super().closeEvent(e)

    # ---------- lecture ----------
    def play_item(self, item, _=0):
        kind, p = item.data(0, KIND), self.profile
        if not p:
            return
        base = f"{p['server']}"
        cred = f"{p['user']}/{p['pwd']}"
        d = item.data(0, DATA) or {}
        if kind == "series":
            item.setExpanded(not item.isExpanded())
            return
        if kind == "ch":
            sid = item.data(0, KEY)
            urls = [f"{base}/live/{cred}/{sid}.m3u8", f"{base}/live/{cred}/{sid}.ts"]
            name = item.text(0)
        elif kind == "movie":
            ext = d.get("container_extension") or "mp4"
            urls = [f"{base}/movie/{cred}/{item.data(0, KEY)}.{ext}"]
            name = item.text(0)
        elif kind == "ep":
            ext = d.get("container_extension") or "mp4"
            urls = [f"{base}/series/{cred}/{item.data(0, KEY)}.{ext}"]
            serie = item.parent().parent().text(0) if item.parent() and item.parent().parent() else ""
            name = f"{serie} — {item.text(0)}" if serie else item.text(0)
        else:
            return
        self.title.setText(clean_title(name))
        self.ts_failed = False
        self.epg_tok += 1
        self.epg.setVisible(kind == "ch")
        if kind == "ch":
            self.fetch_epg(item.data(0, KEY), self.title.text())
        self.cands, self.cand_i = urls, 0
        self.start()

    def fetch_epg(self, sid, name):
        p, tok = self.profile, self.epg_tok
        self.epg.message("Chargement du programme…")

        def get(action, **kw):
            r = requests.get(f"{p['server']}/player_api.php", headers=HEADERS, timeout=30,
                             params={"username": p["user"], "password": p["pwd"], "action": action,
                                     "stream_id": sid, **kw})
            return parse_epg(r.json())

        def work():
            return get("get_simple_data_table") or get("get_short_epg", limit=30)

        def ok(items):
            if tok != self.epg_tok:
                return
            self.epg.show_items(items)
            cur = self.epg.canvas.current()
            if cur:
                self.title.setText(f"{name}  ·  {cur['title']}")

        def fail(m):
            if tok == self.epg_tok:
                self.epg.message("Programme indisponible pour cette chaîne")

        self.run_job(work, ok, fail)

    def start(self):
        if self.cast:
            return self.cast_play()
        st = load_settings()
        url0 = self.cands[0]
        if "/live/" in url0 and st.get("timeshift", True) and not self.ts_failed:
            src = url0[:-5] + ".ts" if url0.endswith(".m3u8") else url0
            self.ts_window_ms = int(st.get("ts_minutes", 60)) * 60000
            self.ts_margin = int(st.get("ts_margin", 20)) * 1000
            self.ts_url, self.ts_pending = self.relay.start_timeshift(src), None
            self.ts_base = 0
            self.ts_autolive = True
            self.ts_hist = []
            self.ts_live, self.ts_live_shown, self.ts_rng = True, None, None
            self.ts_active = True
            self.b_live.setVisible(True)
            self.seek.setEnabled(False)
            self.ts_timer.start(1000)
            self.ts_open(0)
            return
        self.end_timeshift()
        cands = self.cands
        if "/live/" in cands[0]:  # le flux .ts brut est bien plus régulier que la playlist .m3u8
            ts = cands[0][:-5] + ".ts" if cands[0].endswith(".m3u8") else cands[0]
            cands = [ts, ts[:-3] + ".m3u8"]
        self.local_cands = cands
        self.player.setSource(QUrl(cands[min(self.cand_i, len(cands) - 1)]))
        self.player.play()

    def end_timeshift(self):
        was = self.ts_active
        self.ts_active = False
        self.ts_timer.stop()
        self.b_live.setVisible(False)
        if was:
            self.relay.stop_hls()
            self.on_dur(0)

    def rec_pos(self):
        """Position dans l'enregistrement (la position du lecteur est relative au point d'ouverture)."""
        return self.ts_base + self.player.position()

    def ts_open(self, ms):
        """(Ré)ouvre la lecture sur l'enregistrement à partir de ms : liste qui commence au bon segment."""
        t, idx, base = 0.0, 0, 0
        for i, (d, _) in enumerate(self.relay.ts_segments()):
            idx, base = i, int(t * 1000)
            if (t + d) * 1000 > ms:
                break
            t += d
        self.ts_base = base
        self.ts_last = ms
        self.player.setSource(QUrl(f"{self.ts_url}/c.ts?s={idx}"))
        self.player.play()

    def ts_bounds(self):
        rec = int(sum(d for d, _ in self.relay.ts_segments()) * 1000)
        return max(0, rec - self.ts_window_ms), rec

    def ts_tick(self):
        if not self.ts_active:
            return
        segs = self.relay.ts_segments()
        hi = int(sum(d for d, _ in segs) * 1000)
        if hi <= 0:
            return
        lo = max(0, hi - self.ts_window_ms)
        self.relay.ts_trim(lo)
        self.ts_hist.append(hi)
        if self.ts_autolive:
            # le fournisseur envoie d'abord un arriéré (le tampon grossit bien plus vite que le temps réel) ;
            # une fois le débit revenu à la normale, on saute au vrai direct
            n = len(self.ts_hist)
            if (n >= 6 and hi - self.ts_hist[-5] <= 8000) or n >= 25:
                self.ts_autolive = False
                return self.ts_seek(hi - self.ts_margin)
        pos = self.rec_pos()
        if pos < lo - 1500 and self.player.playbackState() != QMediaPlayer.StoppedState:
            self.ts_seek(lo + 2000)  # on a dépassé la fenêtre conservée
            pos = lo + 2000
        if self.ts_rng != (lo, hi):
            self.ts_rng = (lo, hi)
            self.seek.setRange(lo, hi)
        if not self.seek.isEnabled():
            self.seek.setEnabled(True)
        if not self.seek.isSliderDown():
            self.seek.setValue(max(lo, min(pos, hi)))
        behind = max(0, hi - pos)
        # hystérésis : le segment le plus récent n'arrive que toutes les ~2 s, sans seuil double
        # l'état « direct / en retard » basculerait en permanence
        if self.ts_live and behind > self.ts_margin + 18000:
            self.ts_live = False
        elif not self.ts_live and behind < self.ts_margin + 10000:
            self.ts_live = True
        self.set_ts_label(self.t_cur, "" if self.ts_live else f"-{fmt_ms(behind // 5000 * 5000)}")
        self.set_ts_label(self.t_end, f"{fmt_ms((hi - lo) // 10000 * 10000)} en mémoire")
        if self.ts_live != self.ts_live_shown:  # on ne touche au bouton que s'il change vraiment
            self.ts_live_shown = self.ts_live
            self.b_live.setText("● DIRECT" if self.ts_live else "⏭  Revenir au direct")
            self.b_live.setStyleSheet("color:#ff4d4d;" if self.ts_live else "background:#7c5cff;color:white;")

    @staticmethod
    def set_ts_label(lbl, text):
        if lbl.text() != text:
            lbl.setText(text)

    def go_live(self):
        if self.cast or not self.ts_active:
            return
        lo, hi = self.ts_bounds()
        self.ts_seek(max(lo, hi - self.ts_margin))

    def ts_seek(self, ms):
        """Se déplace dans l'enregistrement : le flux continu est rouvert au bon endroit
        (comme pour un flux direct, pas de déplacement sur place)."""
        lo, hi = self.ts_bounds()
        ms = max(lo, min(ms, hi - 1500))
        self.ts_open(ms)

    def on_error(self, err, msg):
        if self.ts_active and "seek" in msg.lower():  # déplacement refusé : on rouvre la liste
            return self.ts_open(self.ts_last)
        if self.ts_active:  # le tampon n'a pas pu démarrer : lecture directe classique
            self.ts_failed = True
            self.end_timeshift()
            self.cand_i = 0
            self.statusBar().showMessage("Direct différé indisponible, lecture directe…")
            return self.start()
        if self.cand_i + 1 < len(self.local_cands):
            self.cand_i += 1
            self.start()
        elif self.cands:
            self.statusBar().showMessage(f"Lecture impossible : {msg}")

    def toggle_play(self):
        if self.cast:
            try:
                mc = self.cast.media_controller
                mc.pause() if mc.status.player_is_playing else mc.play()
            except Exception as e:
                self.statusBar().showMessage(f"Chromecast : {e}")
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif self.cands:
            self.player.play()

    def stop(self):
        if self.cast:
            try:
                self.cast.media_controller.stop()
            except Exception:
                pass
        self.player.stop()
        self.end_timeshift()

    def seek_to(self, ms):
        if self.cast:
            try:
                self.cast.media_controller.seek(ms / 1000)
            except Exception:
                pass
        else:
            if self.ts_active:
                self.ts_seek(ms)
            else:
                self.player.setPosition(ms)

    def set_volume(self, v):
        self.audio.setVolume(v / 100)
        if self.cast:
            try:
                self.cast.set_volume(v / 100)
            except Exception:
                pass

    def set_muted(self, m):
        self.audio.setMuted(m)
        if self.cast:
            try:
                self.cast.set_volume_muted(m)
            except Exception:
                pass

    def skip(self, ms):
        if self.cast:
            try:
                st = self.cast.media_controller.status
                if st and st.duration:
                    self.seek_to(max(0, int(st.adjusted_current_time * 1000) + ms))
            except Exception:
                pass
            return
        if self.player.isSeekable():
            self.seek_to(max(0, (self.rec_pos() if self.ts_active else self.player.position()) + ms))

    def on_state(self, st):
        self.b_play.setText("⏸" if st == QMediaPlayer.PlayingState else "▶")

    def on_pos(self, pos):
        if self.ts_active:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        self.t_cur.setText(fmt_ms(pos) if self.player.duration() > 0 else "")

    def on_dur(self, dur):
        if self.ts_active:
            return
        self.seek.setRange(0, max(0, dur))
        self.seek.setEnabled(dur > 0)
        self.t_end.setText(fmt_ms(dur) if dur > 0 else "● DIRECT")
        if dur <= 0:
            self.t_cur.setText("")


def apply_theme(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#14151a"))
    pal.setColor(QPalette.WindowText, QColor("#e6e8ef"))
    pal.setColor(QPalette.Base, QColor("#1b1d24"))
    pal.setColor(QPalette.AlternateBase, QColor("#232632"))
    pal.setColor(QPalette.Text, QColor("#e6e8ef"))
    pal.setColor(QPalette.Button, QColor("#2a2d3a"))
    pal.setColor(QPalette.ButtonText, QColor("#e6e8ef"))
    pal.setColor(QPalette.Highlight, QColor("#7c5cff"))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor("#1b1d24"))
    pal.setColor(QPalette.ToolTipText, QColor("#e6e8ef"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)


def selftest(out):
    """Vérifie dans l'exe compilé que lecteur, ffmpeg et Chromecast sont bien embarqués."""
    res = []
    try:
        exe = ffmpeg_exe()
        res.append(f"ffmpeg: {bool(exe and os.path.exists(exe))}")
        res.append(f"chromecast: {pychromecast is not None}")
        app = QApplication([])
        res.append(f"audio outputs: {len(QMediaDevices.audioOutputs())}")
        f = os.path.join(tempfile.gettempdir(), "kflux_selftest.mp4")
        subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", f], creationflags=NOWIN)
        pl = QMediaPlayer()
        pl.setAudioOutput(QAudioOutput())
        pl.setSource(QUrl.fromLocalFile(f))
        pl.play()
        t = time.time()
        while time.time() - t < 6 and pl.position() < 300:
            app.processEvents()
            time.sleep(0.02)
        res.append(f"playback position ms: {pl.position()} state={pl.playbackState().name}")
        res.append(f"relay: {Relay().start() is None}")
    except Exception as e:
        res.append(f"ERROR {type(e).__name__}: {e}")
    open(out, "w", encoding="utf-8").write(chr(10).join(res))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
        sys.exit(0)
    if "--icon" in sys.argv:  # génère icon.ico pour la compilation
        app = QApplication([])
        make_pixmap(256).toImage().save("icon.ico")
        sys.exit(0)
    app = QApplication(sys.argv)
    apply_theme(app)
    w = Main()
    w.show()
    sys.exit(app.exec())
