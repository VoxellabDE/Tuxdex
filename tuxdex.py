#!/usr/bin/env python3
"""
Tuxdex - grafische Systemverwaltung für Arch Linux: Updates, Paketverwaltung,
Fallback-Speicher (Swap), Firewall und Benutzerübersicht.

Oberfläche: PySide6 (Qt 6), dunkles Theme mit Petrol-Akzent, IBM Plex.
(dunkles Theme, Petrol-Akzent, IBM Plex).

Alles läuft innerhalb des Programmfensters - es wird KEIN externes
Terminal geöffnet. Für privilegierte Aktionen wird beim ersten Mal per
Dialog nach dem sudo-Passwort gefragt; danach bleibt die sudo-Sitzung
für den Rest des Programmlaufs aktiv (kein wiederholtes Eingeben).

Abhängigkeiten (per pacman installieren):
    sudo pacman -S pyside6 sudo pacman-contrib ttf-ibm-plex
    # paru (AUR-Helper) und flatpak nur nötig, wenn diese Bereiche
    # genutzt werden sollen.

Start:
    python3 tuxdex.py      (oder nach der Installation: tuxdex)
"""

import os
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import sys
import json
import select
import tempfile
import threading
import time
from datetime import datetime
from string import Template

PKG_NAME_RE = re.compile(r"^[A-Za-z0-9@_.+-]+$")
PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
# Hier legt Tuxdex nie ein Swapfile an und löscht dort nichts
SWAP_FORBIDDEN = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/efi", "/dev", "/proc", "/sys",
                  "/run", "/var/lib/pacman", "/var/cache/pacman", "/root/.ssh")


def _is_swapfile(path):
    """True, wenn path ein aktiver Swap ist oder eine Swap-Signatur trägt (blkid, braucht sudo-Sitzung)."""
    active = [l.split()[0] for l in _read("/proc/swaps").splitlines()[1:] if l.split()]
    if path in active:
        return True
    try:
        r = subprocess.run(["sudo", "-n", "blkid", "-p", "-o", "value", "-s", "TYPE", path],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "swap"
    except Exception:
        return False


def swap_path_problem(path, must_exist=False):
    """Fehlertext, wenn path kein sicherer Ort für ein Swapfile ist – sonst None."""
    if not PATH_RE.match(path) or os.path.normpath(path) != path or path == "/":
        return "Bitte einen einfachen absoluten Pfad angeben (ohne Leerzeichen, „..“ oder doppelte /)."
    if any(path == d or path.startswith(d + "/") for d in SWAP_FORBIDDEN):
        return f"In {os.path.dirname(path)} legt Tuxdex aus Sicherheitsgründen kein Swapfile an."
    if not os.path.isdir(os.path.dirname(path)):
        return f"Den Ordner {os.path.dirname(path)} gibt es nicht."
    if os.path.islink(path):
        return f"{path} ist eine Verknüpfung – bitte den echten Pfad angeben."
    if os.path.lexists(path):
        if not os.path.isfile(path):
            return f"{path} ist keine normale Datei."
        if not _is_swapfile(path):
            return (f"{path} existiert schon und ist kein Swapfile. Tuxdex überschreibt keine anderen Dateien – "
                    "bitte einen anderen Namen wählen.")
    elif must_exist:
        return f"{path} gibt es nicht."
    return None


# --------------------------------------------------------------------------
# Fehlerbehandlung, falls PySide6 selbst fehlt
# --------------------------------------------------------------------------

def _show_fatal_error(title, message):
    log_path = os.path.expanduser("~/tuxdex_error.log")
    try:
        with open(log_path, "a") as f:
            f.write(f"\n--- {datetime.now().isoformat()} | {title} ---\n{message}\n")
    except Exception:
        pass
    full_msg = f"{message}\n\n(Details auch in {log_path})"
    for tool, args in (
        ("kdialog", ["--title", title, "--error", full_msg]),
        ("zenity", ["--error", "--title", title, "--text", full_msg]),
        ("notify-send", ["-u", "critical", title, message]),
    ):
        if shutil.which(tool):
            try:
                subprocess.run([tool] + args, timeout=15)
                return
            except Exception:
                continue
    print(f"{title}: {message}", file=sys.stderr)


try:
    from PySide6.QtCore import QObject, Qt, Signal, Slot, QTimer, QSize, QRectF
    from PySide6.QtGui import (QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QTextCharFormat,
                               QTextCursor, QIcon, QPixmap)
    from PySide6.QtWidgets import (
        QApplication, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
        QGridLayout, QStackedWidget, QPlainTextEdit, QLineEdit, QCheckBox, QComboBox,
        QListWidget, QAbstractItemView, QTableWidget, QTableWidgetItem, QHeaderView,
        QTreeWidget, QTreeWidgetItem, QLayout,
        QSlider, QDialog, QMessageBox, QScrollArea, QSizePolicy,
    )
except Exception as e:
    _show_fatal_error(
        "Tuxdex - Start fehlgeschlagen",
        "PySide6 konnte nicht geladen werden. Vermutlich fehlt das Paket 'pyside6'.\n\n"
        "Installieren mit: sudo pacman -S pyside6\n\n"
        f"Fehlermeldung: {e}",
    )
    sys.exit(1)


# --------------------------------------------------------------------------
# Sprache: Deutsch (Quelltext) oder Englisch (Katalog EN am Dateiende)
# Alle Oberflächentexte laufen über tr(): Qt-Textfunktionen und die Konstruktoren
# von Label, Button, Checkbox und Tabelleneinträgen werden dafür einmal umhüllt.
# --------------------------------------------------------------------------

LANG = "de"
_TR_CACHE = {}
_TR_PATS = None      # [(Regex, englische Vorlage, Platzhalter-ist-Endung, Länge)]
_TR_SUB = None       # Regex über alle festen Texte (für zusammengesetzte Sätze)
_TR_UP = None        # Katalog in GROSSBUCHSTABEN (Feldüberschriften, Tabellenköpfe)
_WORD = "A-Za-zÄÖÜäöüß0-9_"
_SUFFIXES = {"e", "en", "n", "er", "r", "s", "es"}
_DE_WORD = re.compile(r"[äöüß]|\b(und|der|die|das|nicht|mit|für|oder|von|seit|ist|sind|noch|vor|zum|beim|den|dem)\b")


def _tr_build():
    global _TR_PATS, _TR_SUB, _TR_UP
    _TR_UP = {k.upper(): v.upper() for k, v in EN.items() if "{}" not in k}
    pats, lits = [], []
    for de, en in EN.items():
        if "{}" in de:
            parts = de.split("{}")
            suffix = [bool(re.search(f"[{_WORD}]$", parts[i])) and not re.match(f"[{_WORD}]", parts[i + 1] or " ")
                      for i in range(len(parts) - 1)]
            rx = "".join(re.escape(p) + (("([a-zäöüß]{0,3})" if suffix[i] else "(.*?)") if i < len(parts) - 1 else "")
                         for i, p in enumerate(parts))
            letters = len(re.sub("[^A-Za-zÄÖÜäöüß]", "", "".join(parts)))
            pats.append((re.compile(rx, re.S), en, suffix, sum(len(p) for p in parts), letters))
        elif " " in de.strip() or not re.match(f"[{_WORD}]", de):
            lits.append(de)      # nur Satzteile – einzelne Wörter nie mitten im Text ersetzen
    pats.sort(key=lambda p: -p[3])
    _TR_PATS = pats
    lits.sort(key=len, reverse=True)

    def lit_rx(x):
        a = f"(?<![{_WORD}/~-])" if re.match(f"[{_WORD}]", x) else ""
        b = f"(?![{_WORD}/])" if re.search(f"[{_WORD}]$", x) else ""
        return a + re.escape(x) + b
    _TR_SUB = re.compile("|".join(lit_rx(x) for x in lits))


def _fill(tmpl, vals):
    out, i = [], 0
    for piece in tmpl.split("{}")[:-1]:
        out.append(piece)
        out.append(vals[i] if i < len(vals) else "")
        i += 1
    out.append(tmpl.split("{}")[-1])
    return "".join(out)


def _tr(s, depth=0, loose=True):
    if _TR_PATS is None:
        _tr_build()
    core = s.strip()
    lead, trail = s[:len(s) - len(s.lstrip())], s[len(s.rstrip()):]
    pre = ""
    m = re.match(r"^([●▲✕○•⬆↓↑⚠\s]+)(\S.*)$", core, re.S)
    if m and m.group(1).strip() and core not in EN and not _tr_pattern(core, depth, loose):
        pre, core = m.group(1), m.group(2)
    en = EN.get(core)
    if en is None and core[-1:] in ".:" and core[:-1] in EN:
        en = EN[core[:-1]] + core[-1]
    if en is None and core.isupper():
        en = _TR_UP.get(core)
    if en is None and depth < 4:
        en = _tr_pattern(core, depth, loose)
    if en is None and depth < 4:
        # zusammengesetzte Zeilen stufenweise zerlegen: Zeilen/Absätze → Sätze → „ · “ → „: “
        for sep in (r"(<br>|\n+)", r"((?<=[.!?])\s+(?=[A-ZÄÖÜ„(]))", r"(\s{2,}·\s{2,}|\s+·\s+|\s{2,})", r"(:\s+)"):
            pieces = re.split(sep, core)
            if len(pieces) > 1:
                out = [p if i % 2 else _tr(p, depth + 1, loose=False) for i, p in enumerate(pieces)]
                if out != pieces:
                    en = "".join(out)
                break
    if en is None:
        en = core
    if loose and _TR_SUB and depth == 0:
        en = _TR_SUB.sub(lambda x: EN[x.group(0)], en)
    return lead + pre + en + trail


def _tr_pattern(core, depth, loose):
    """Erste passende Vorlage mit Platzhaltern – eingesetzte Werte werden ebenfalls übersetzt."""
    if _TR_PATS is None:
        _tr_build()
    if depth < 3:
        for rx, tmpl, suf, _, letters in _TR_PATS:
            mm = rx.fullmatch(core)
            if not mm:
                continue
            groups = mm.groups()
            if any(re.search(r"\s·\s|<br>|\n", g) for g in groups) and "·" not in tmpl and "<br>" not in tmpl:
                continue       # Trennzeichen gehören der Zerlegung, nicht einem Platzhalter
            if letters < 12 and any(len(g.split()) > 1 and _DE_WORD.search(g) for g in groups):
                continue       # „{} über {}“ o. Ä. soll nicht ganze deutsche Sätze verschlucken
            vals = []
            for g, is_suf in zip(mm.groups(), suf):
                if is_suf and g in _SUFFIXES | {""}:
                    vals.append("s" if g else "")
                elif g and re.search("[A-Za-zÄÖÜäöüß]{3}", g) and not g.startswith(("/", "~", "$")):
                    vals.append(_tr(g, depth + 1, loose))
                else:
                    vals.append(g)
            return _fill(tmpl, vals)
    return None


def tr(s, loose=True):
    """Übersetzt einen Oberflächentext ins Englische (bei LANG == "en"), sonst unverändert."""
    if LANG == "de" or not isinstance(s, str) or not re.search("[A-Za-zÄÖÜäöüß]{2}", s):
        return s
    key = (s, loose)
    hit = _TR_CACHE.get(key)
    if hit is None:
        try:
            hit = _tr(s, loose=loose)
        except Exception:
            hit = s
        if len(_TR_CACHE) > 20000:
            _TR_CACHE.clear()
        _TR_CACHE[key] = hit
    return hit


def _tr_args(args):
    """Erster Text (bzw. Textliste) in den Argumenten wird übersetzt."""
    out, done = [], False
    for a in args:
        if not done and isinstance(a, str):
            out.append(tr(a))
            done = True
        elif not done and isinstance(a, (list, tuple)) and a and all(isinstance(x, str) for x in a):
            out.append([tr(x) for x in a])
            done = True
        else:
            out.append(a)
    return out


_TR_ORIG = {}


def set_text_raw(widget, text, method="setText"):
    """Text ohne Übersetzung setzen (z. B. Changelog, der schon in der richtigen Sprache kommt)."""
    for cls in type(widget).__mro__:
        orig = _TR_ORIG.get((cls.__name__, method))
        if orig:
            return orig(widget, text)
    return getattr(widget, method)(text)


def _install_i18n():
    import PySide6.QtWidgets as W

    def wrap(cls, name):
        orig = getattr(cls, name)
        _TR_ORIG[(cls.__name__, name)] = orig

        def f(self, *a, **k):
            return orig(self, *_tr_args(a), **k)
        setattr(cls, name, f)

    for cls, names in ((W.QLabel, ("setText",)), (W.QAbstractButton, ("setText",)),
                       (W.QWidget, ("setToolTip", "setWindowTitle")), (W.QLineEdit, ("setPlaceholderText",)),
                       (W.QComboBox, ("addItem", "addItems", "insertItem", "setItemText")),
                       (W.QTableWidget, ("setHorizontalHeaderLabels",)), (W.QTreeWidget, ("setHeaderLabels",)),
                       (W.QTableWidgetItem, ("setText", "setToolTip")), (W.QTreeWidgetItem, ("setText", "setToolTip")),
                       (W.QListWidgetItem, ("setText", "setToolTip")),
                       (W.QMessageBox, ("setText", "setInformativeText", "addButton"))):
        for n in names:
            wrap(cls, n)

    import PySide6.QtGui as G
    wrap(G.QPainter, "drawText")                  # selbst gezeichnete Texte (Balkenlisten, Hinweise)

    class ListItem(W.QListWidgetItem):          # wird im Code lokal importiert
        def __init__(self, *a, **k):
            super().__init__(*_tr_args(a), **k)
    W.QListWidgetItem = ListItem

    for name in ("getExistingDirectory", "getOpenFileName", "getSaveFileName"):
        orig = getattr(W.QFileDialog, name)

        def f(parent=None, caption="", *a, _o=orig, **k):
            return _o(parent, tr(caption), *_tr_args(a), **k) if a else _o(parent, tr(caption), **k)
        setattr(W.QFileDialog, name, staticmethod(f))


class QLabel(QLabel):
    def __init__(self, *a, **k):
        super().__init__(*_tr_args(a), **k)


class QPushButton(QPushButton):
    def __init__(self, *a, **k):
        super().__init__(*_tr_args(a), **k)


class QCheckBox(QCheckBox):
    def __init__(self, *a, **k):
        super().__init__(*_tr_args(a), **k)


class QTableWidgetItem(QTableWidgetItem):
    def __init__(self, *a, **k):
        super().__init__(*_tr_args(a), **k)


class QTreeWidgetItem(QTreeWidgetItem):
    def __init__(self, *a, **k):
        super().__init__(*_tr_args(a), **k)


def init_language():
    """Sprache aus den Einstellungen, sonst aus der Systemsprache (deutsch → de, alles andere → en)."""
    global LANG
    lang = _load_json(os.path.join(os.path.expanduser("~/.config"), "tuxdex", "settings.json"), {}).get("lang")
    if lang not in ("de", "en"):
        env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
        lang = "de" if env.lower().startswith("de") else "en"
    LANG = lang
    if LANG != "de":
        _install_i18n()


# --------------------------------------------------------------------------
# Design-Tokens (Theme „Dunkel“)
# --------------------------------------------------------------------------

COLORS = {
    "bg0": "#0e1113",          # bg-000  Fenster
    "bg1": "#151a1d",          # bg-100  Kopfleiste, Tabs, Log
    "bg2": "#1c2327",          # bg-200  Panels, Tabellen
    "bg3": "#253036",          # bg-300  Hover, Ghost-Button
    "bg3h": "#2f3c43",         # Ghost-Hover
    "line": "#2a353b",         # line
    "line_strong": "#5f717a",  # line-strong (Eingaben)
    "ink": "#e6edf0",
    "muted": "#9aa9b0",
    "accent": "#2fb3a3",
    "accent_h": "#4fcfbf",
    "on_accent": "#07201d",
    "focus": "#7fe3d6",
    "ok": "#4cc38a",
    "warn": "#e8a33d",
    "danger": "#f0726a",
    "danger_h": "#f58d86",
    "on_danger": "#2a0906",
    "info": "#5aa6f0",
}

# Modul-Kennfarben
MODULES = [
    ("update", "Updates", "#2fb3a3"),
    ("setup", "Einrichten", "#f7a072"),
    ("software", "Software", "#5aa6f0"),
    ("flatpak", "Flatpak", "#8fa8ff"),
    ("disks", "Datenträger", "#d9b95c"),
    ("storage", "Speicher", "#56c2d6"),
    ("backup", "Backup", "#e9c46a"),
    ("restore", "Wiederherstellung", "#7fd1b9"),
    ("swap", "Swap", "#6cc56f"),
    ("tasks", "Taskmanager", "#b5d86b"),
    ("antivirus", "Antivirus", "#a98bf0"),
    ("security", "Sicherheit", "#f08a4b"),
    ("users", "Benutzer", "#e87fa8"),
]

# Module wie Bausteine: Updates ist immer dabei, der Rest lässt sich unter Einstellungen → Module
# zu- und abwählen. Abgewählte Module werden gar nicht erst geladen (spart RAM und Startzeit).
MODULE_INFO = {
    "update": "System-, AUR- und Flatpak-Updates mit Hinweisen vor riskanten Updates.",
    "setup": "Basics wie Schriften und Codecs mit einem Klick, dazu Ersatz für Windows-Programme.",
    "software": "Programme suchen, installieren und entfernen (pacman und AUR).",
    "flatpak": "Flatpak-Apps verwalten und ihre Rechte per Schalter einstellen.",
    "disks": "USB-Sticks und Festplatten einhängen, formatieren und prüfen.",
    "storage": "Sehen, was Platz belegt, und typische Platzfresser aufräumen.",
    "backup": "Sicherungen auf externe Laufwerke – mit Zeitplan und Wiederherstellen.",
    "restore": "System-Snapshots vor Updates und Zurücksetzen per Klick (snapper oder Timeshift).",
    "swap": "Auslagerungsspeicher (Swapfile, zram) einrichten. Für Fortgeschrittene.",
    "tasks": "Laufende Programme, Leistung, Autostart und Bootzeit.",
    "antivirus": "ClamAV-Virenscanner. Auf Linux-Desktops wenig nützlich – vor allem für Server und "
                 "Dateien, die an Windows-Rechner weitergehen.",
    "security": "Sicherheits-Check, Checkliste, Firewall, offene Ports und VPN.",
    "users": "Benutzerkonten und Gruppen verwalten. Für Fortgeschrittene.",
}
MODULES_CORE = {"update"}
MODULES_OFF_BY_DEFAULT = {"swap", "antivirus", "users"}


def module_enabled(key, settings=None):
    if key in MODULES_CORE:
        return True
    mods = (settings if settings is not None else load_settings()).get("modules", {})
    return bool(mods.get(key, key not in MODULES_OFF_BY_DEFAULT))


FONTS = {"sans": "Sans Serif", "mono": "Monospace"}

APP_ID = "tuxdex"
APP_VERSION = "1.3.0"
APP_STAGE = "alpha"        # Reifegrad – wird nur angezeigt, die Versionsnummer selbst bleibt ohne Zusatz


def vlabel(v=None):
    """Version zum Anzeigen: „1.1.3-alpha“; Beta-Stände („1.2.0-beta.1“) bleiben, wie sie sind."""
    v = APP_VERSION if v is None else v
    return v if "-" in v or not APP_STAGE or not re.match(r"\d", v) else f"{v}-{APP_STAGE}"
SYSTEM_INSTALL = os.path.abspath(__file__).startswith("/usr/")

# App-Logo (Kachel mit drei Reglern) – Taskleiste, Kopfzeile, Starter
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <title>Tuxdex</title>
  <!-- Kachel: bg-100 (#151a1d) mit line-Rand (#2a353b) -->
  <rect x="8" y="8" width="496" height="496" rx="116" fill="#151a1d"/>
  <rect x="13" y="13" width="486" height="486" rx="111" fill="none" stroke="#2a353b" stroke-width="10"/>
  <!-- drei Regler = „steuern & verwalten“: Spur bg-300, Füllung accent (Petrol) -->
  <g stroke-linecap="round" stroke-width="56">
    <line x1="112" y1="148" x2="400" y2="148" stroke="#27343a"/>
    <line x1="112" y1="256" x2="400" y2="256" stroke="#27343a"/>
    <line x1="112" y1="364" x2="400" y2="364" stroke="#27343a"/>
    <line x1="112" y1="148" x2="300" y2="148" stroke="#2fb3a3"/>
    <line x1="112" y1="256" x2="196" y2="256" stroke="#2fb3a3"/>
    <line x1="112" y1="364" x2="352" y2="364" stroke="#2fb3a3"/>
  </g>
  <!-- Knöpfe als abgerundete Quadrate – das Kachel-Motiv im Kleinen -->
  <g fill="#e6edf0" stroke="#151a1d" stroke-width="14">
    <rect x="252" y="100" width="96" height="96" rx="28"/>
    <rect x="148" y="208" width="96" height="96" rx="28"/>
    <rect x="304" y="316" width="96" height="96" rx="28"/>
  </g>
</svg>"""


def logo_pixmap(size, dpr=1.0):
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(LOGO_SVG.encode())).render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    px.setDevicePixelRatio(dpr)
    return px


def logo_icon():
    icon = QIcon()
    for s in (16, 22, 24, 32, 48, 64, 128, 256, 512):
        icon.addPixmap(logo_pixmap(s))
    return icon


def migrate_legacy():
    """Übernimmt Daten der früheren Namensversion „arch-manager“ (einmalig, ohne root)."""
    home = os.path.expanduser("~")
    moves = [(os.path.join(home, ".cache", "arch-manager"), os.path.join(home, ".cache", "tuxdex")),
             (os.path.join(home, ".local", "share", "arch-manager"), os.path.join(home, ".local", "share", "tuxdex")),
             (os.path.join(home, "arch_manager_error.log"), os.path.join(home, "tuxdex_error.log"))]
    for old, new in moves:
        try:
            if os.path.exists(old) and not os.path.exists(new):
                shutil.move(old, new)
        except Exception:
            pass
    share = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    old_desk = os.path.join(share, "applications", "arch-manager.desktop")
    try:
        with open(old_desk) as f:
            txt = f.read()
        if "X-Arch-Manager-Generated=true" in txt or "Exec=python3" in txt:
            os.remove(old_desk)
            old_icon = os.path.join(share, "icons", "hicolor", "scalable", "apps", "arch-manager.svg")
            if os.path.exists(old_icon):
                os.remove(old_icon)
    except Exception:
        pass


def install_desktop_entry():
    """Legt Icon + Starter im Benutzerprofil ab, damit Taskleiste/Dock (auch unter
    Wayland) und das Anwendungsmenü das Logo zeigen. Nur wenn nötig, ohne root."""
    try:
        share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        icon_path = os.path.join(share, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")
        desk_path = os.path.join(share, "applications", f"{APP_ID}.desktop")
        if SYSTEM_INSTALL:
            # Als Paket installiert: der System-Starter gilt; alte selbst angelegte Einträge entfernen,
            # sonst würden sie den System-Starter überdecken.
            try:
                with open(desk_path) as f:
                    old = f.read()
                if "X-Tuxdex-Generated=true" in old:
                    os.remove(desk_path)
                    if os.path.exists(icon_path):
                        os.remove(icon_path)
            except FileNotFoundError:
                pass
            return
        script = os.path.abspath(sys.argv[0])
        desktop = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Tuxdex\n"
            "GenericName=Systemverwaltung für Arch Linux\n"
            "Comment=Updates, Software, Datenträger, Speicher, Taskmanager, Antivirus, Firewall und Benutzer\n"
            f"Exec=python3 {shlex.quote(script)}\n"
            f"Icon={APP_ID}\n"
            "Terminal=false\n"
            "Categories=System;Monitor;\n"
            "X-Tuxdex-Generated=true\n"
        )
        changed = False
        def _read(p):
            try:
                with open(p) as f:
                    return f.read()
            except Exception:
                return ""
        if _read(icon_path) != LOGO_SVG:
            os.makedirs(os.path.dirname(icon_path), exist_ok=True)
            with open(icon_path, "w") as f:
                f.write(LOGO_SVG)
            changed = True
        if _read(desk_path) != desktop:
            os.makedirs(os.path.dirname(desk_path), exist_ok=True)
            with open(desk_path, "w") as f:
                f.write(desktop)
            changed = True
        if changed and which("update-desktop-database"):
            subprocess.run(["update-desktop-database", os.path.dirname(desk_path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass

QSS = Template("""
* { outline: none; }
QWidget { color: $ink; font-size: 10pt; }
QMainWindow, #Root, #Page, QScrollArea, QScrollArea > QWidget > QWidget { background: $bg0; }
QToolTip { background: $bg2; color: $ink; border: 1px solid $line; padding: 4px 8px; }

/* Kopfleiste & Tabs */
#Topbar { background: $bg1; }
#AppTitle { font-size: 15pt; font-weight: 600; }
#TabBar { background: $bg1; border-bottom: 1px solid $line; }
#Tab { background: transparent; border-top-left-radius: 6px; border-top-right-radius: 6px; }
#Tab[selected="true"] { background: $bg0; }
#Tab:hover { background: $bg2; }
#Tab[selected="true"]:hover { background: $bg0; }
#TabText { color: $muted; font-weight: 600; background: transparent; }
#Tab[selected="true"] #TabText, #Tab:hover #TabText { color: $ink; }
#Statusbar { background: $bg0; border-top: 1px solid $line; }
#StatusText { color: $muted; font-size: 9pt; }

/* Typografie */
#PageTitle { font-size: 15pt; font-weight: 600; }
#Muted { color: $muted; }
#Small { color: $muted; font-size: 9pt; }
#Error { color: $danger; }
#FieldLabel { color: $muted; font-size: 8pt; font-weight: 600; }
#Value { font-family: "$mono"; font-weight: 600; }

/* Panels */
#Panel { background: $bg2; border: 1px solid $line; border-radius: 10px; }
#PanelTitle { font-weight: 600; background: transparent; }
#Panel[clickable="true"]:hover { border-color: $line_strong; }
#Panel[clickable="true"]:focus { border: 2px solid $focus; }
#Panel[selected="true"], #Panel[selected="true"]:hover { border: 1px solid $accent; background: $bg3; }
#KvValue { font-family: "$mono"; }
#Panel QLabel, #Panel QCheckBox { background: transparent; }

/* Buttons */
QPushButton {
    background: $bg3; color: $ink; border: 0; border-radius: 6px;
    padding: 7px 16px; font-weight: 600;
}
QPushButton:hover { background: $bg3h; }
QPushButton:focus { border: 2px solid $focus; padding: 5px 14px; }
QPushButton[variant="primary"] { background: $accent; color: $on_accent; }
QPushButton[variant="primary"]:hover { background: $accent_h; }
QPushButton[variant="danger"] { background: $danger; color: $on_danger; }
QPushButton[variant="danger"]:hover { background: $danger_h; }
QPushButton:disabled, QPushButton[variant="primary"]:disabled, QPushButton[variant="danger"]:disabled {
    background: transparent; color: $line_strong; border: 1px solid $line; padding: 6px 15px;
}
QPushButton[variant="icon"] { padding: 6px 10px; min-width: 16px; }

/* Status-Badges */
#Badge { border: 1px solid; border-radius: 3px; padding: 2px 8px; font-size: 9pt; font-weight: 600; background: $bg2; }
#Badge[tone="ok"] { color: $ok; border-color: $ok; }
#Badge[tone="warn"] { color: $warn; border-color: $warn; }
#Badge[tone="danger"] { color: $danger; border-color: $danger; }
#Badge[tone="info"] { color: $info; border-color: $info; }
#Badge[tone="off"] { color: $muted; border-color: $muted; }

/* Eingaben */
QLineEdit, QComboBox {
    background: $bg0; color: $ink; border: 1px solid $line_strong; border-radius: 6px;
    padding: 6px 10px; selection-background-color: $accent; selection-color: $on_accent;
    min-height: 22px;
}
QLineEdit:focus, QComboBox:focus, QComboBox:on { border: 2px solid $focus; padding: 5px 9px; }
QLineEdit[mono="true"] { font-family: "$mono"; }
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox::down-arrow { image: url("$arrow"); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: $bg2; color: $ink; border: 1px solid $line; border-radius: 6px; padding: 4px;
    selection-background-color: $accent; selection-color: $on_accent; outline: 0;
}

QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1.5px solid $line_strong; border-radius: 3px; background: $bg0;
}
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url("$check"); }
QCheckBox::indicator:focus { border-color: $focus; }
QCheckBox:disabled { color: $muted; }
QCheckBox::indicator:disabled { background: $bg3; border-color: $bg3; }

QSlider::groove:horizontal { height: 8px; background: $bg3; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal {
    background: $ink; width: 16px; height: 16px; margin: -4px 0; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: $focus; }

/* Listen & Tabellen */
QListWidget, QTableWidget, QTreeWidget {
    background: $bg1; color: $ink; border: 1px solid $line; border-radius: 6px;
    font-family: "$mono"; alternate-background-color: $bg1;
}
QListWidget::item { padding: 4px 8px; border: 0; }
QListWidget::item:hover, QTableWidget::item:hover { background: $bg3; }
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {
    background: $accent; color: $on_accent;
}
QTreeWidget::item { padding: 5px 4px; }
QTreeWidget::item:hover { background: $bg3; }
QTableWidget { gridline-color: $line; }
QTableWidget::item { padding: 4px 10px; border-bottom: 1px solid $line; }
QHeaderView::section {
    background: $bg1; color: $muted; border: 0; border-bottom: 1px solid $line;
    padding: 8px 10px; font-family: "$sans"; font-size: 8pt; font-weight: 600;
}
QTableCornerButton::section { background: $bg1; border: 0; }

/* Log */
#Log {
    background: $bg1; color: $ink; border: 1px solid $line; border-radius: 6px;
    padding: 6px 8px; font-family: "$mono";
    selection-background-color: $accent; selection-color: $on_accent;
}

/* Scrollbars */
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle { background: $bg3; border-radius: 4px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: $line_strong; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* Dialoge */
QDialog, QMessageBox { background: $bg2; }
/* Feste Fläche statt transparent – sonst malen manche Plattform-Themes (KDE) die Labels schwarz */
QDialog QLabel { background: $bg2; }
QDialogButtonBox { background: $bg2; }
QMessageBox QPushButton { min-width: 88px; }
/* Reihenfolge wie in den eigenen Dialogen: Abbrechen links, Aktion rechts */
QDialogButtonBox { button-layout: 3; }
#DialogTitle { font-size: 14pt; font-weight: 600; }
#Warn { color: $warn; }
#Changed { color: $accent; font-size: 8pt; font-weight: 600; border: 1px solid $accent; border-radius: 3px; padding: 0 5px; }
QListWidget#AppList { font-family: "$sans"; }
QListWidget#AppList::item { padding: 6px 6px; border-radius: 6px; }
QTableWidget::indicator, QListWidget::indicator {
    width: 16px; height: 16px; border: 1.5px solid $line_strong; border-radius: 3px; background: $bg0;
}
QTableWidget::indicator:checked { background: $accent; border-color: $accent; image: url("$check"); }
QPushButton#SysHint { background: transparent; color: $warn; border: 1px solid $warn; padding: 2px 10px;
    font-size: 9pt; }
QPushButton#SysHint[tone="danger"] { color: $danger; border-color: $danger; }
QPushButton#SysHint:hover { background: $bg2; }
QPushButton#UpdHint { background: transparent; color: $warn; border: 1px solid $warn; padding: 2px 10px;
    font-size: 9pt; }
QPushButton#UpdHint:hover { background: $bg2; }
QPushButton#Info { background: transparent; border: 0; border-radius: 11px; padding: 0; min-width: 0; }
QPushButton#Info:hover { background: $bg3; }
QPushButton#Info:focus { border: 2px solid $focus; }
QPushButton#Gear { background: transparent; border: 0; border-radius: 6px; padding: 5px; min-width: 0; }
QPushButton#Gear:hover { background: $bg2; }
QPushButton#Gear:checked { background: $bg3; }
#MidValue { font-family: "$mono"; font-size: 15pt; font-weight: 600; }
#Segmented { background: $bg1; border: 1px solid $line; border-radius: 8px; }
QPushButton#Seg { background: transparent; color: $muted; padding: 5px 14px; border-radius: 6px; }
QPushButton#Seg:hover { color: $ink; background: $bg2; }
QPushButton#Seg:checked { background: $bg3; color: $ink; }
#BigValue { font-family: "$mono"; font-size: 20pt; font-weight: 600; }
#Danger { color: $danger; font-weight: 600; }
#Hint { color: $muted; font-size: 9pt; }
""")


def _asset_dir():
    """Privater Ordner für Icons: /run/user/<uid> (nur für dich lesbar) oder ~/.cache/tuxdex – nie ein
    vorhersagbarer Ordner in /tmp, den ein anderer Benutzer vorher anlegen und präparieren könnte."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not (base and os.path.isdir(base) and os.stat(base).st_uid == os.getuid()):
        base = os.path.join(os.path.expanduser("~/.cache"))
    d = os.path.join(base, "tuxdex-assets")
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.lstat(d)
    if os.path.islink(d) or st.st_uid != os.getuid():
        d = tempfile.mkdtemp(prefix="tuxdex-")
    return d


def _write_asset(name, svg):
    path = os.path.join(_asset_dir(), name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(svg)
    return path


def tuxdex_palette():
    """Eigene Farbpalette – sonst malt das Desktop-Theme (z. B. KDE Breeze) Fensterflächen in seinen Farben,
    und Texte in Dialogen stehen auf andersfarbigen Kästen."""
    from PySide6.QtGui import QPalette
    pal = QPalette()
    c = lambda k: QColor(COLORS[k])
    for role, key in ((QPalette.Window, "bg2"), (QPalette.WindowText, "ink"), (QPalette.Base, "bg0"),
                      (QPalette.AlternateBase, "bg1"), (QPalette.Text, "ink"), (QPalette.Button, "bg3"),
                      (QPalette.ButtonText, "ink"), (QPalette.BrightText, "ink"), (QPalette.ToolTipBase, "bg2"),
                      (QPalette.ToolTipText, "ink"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "on_accent"), (QPalette.PlaceholderText, "muted"),
                      (QPalette.Link, "accent"), (QPalette.LinkVisited, "accent_h"), (QPalette.Light, "bg3"),
                      (QPalette.Midlight, "bg3"), (QPalette.Mid, "line"), (QPalette.Dark, "bg1"),
                      (QPalette.Shadow, "bg0")):
        pal.setColor(role, c(key))
    for role, key in ((QPalette.WindowText, "muted"), (QPalette.Text, "muted"), (QPalette.ButtonText, "muted")):
        pal.setColor(QPalette.Disabled, role, c(key))
    return pal


def apply_theme(app):
    families = set(QFontDatabase.families())

    def pick(cands, fallback):
        for c in cands:
            if c in families:
                return c
        return fallback

    FONTS["sans"] = pick(["IBM Plex Sans", "Noto Sans", "DejaVu Sans", "Cantarell"], app.font().family())
    FONTS["mono"] = pick(["IBM Plex Mono", "JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono"],
                         QFontDatabase.systemFont(QFontDatabase.FixedFont).family())
    font = QFont(FONTS["sans"], 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)

    check = _write_asset("check.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
        f'<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{COLORS["on_accent"]}" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'))
    arrow = _write_asset("arrow.svg", (
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
        f'<path d="M2.5 4.5l3.5 3.5 3.5-3.5" fill="none" stroke="{COLORS["ink"]}" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'))
    app.setStyle("Fusion")
    app.setPalette(tuxdex_palette())
    app.setStyleSheet(QSS.substitute(COLORS, check=check, arrow=arrow, sans=FONTS["sans"], mono=FONTS["mono"]))


def short_path(p):
    """Home-Ordner als ~ anzeigen, damit kein Benutzername im Bild steht."""
    home = os.path.expanduser("~")
    p = str(p)
    return "~" + p[len(home):] if home not in ("", "/") and (p == home or p.startswith(home + "/")) else p


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# --------------------------------------------------------------------------
# Hilfsfunktionen: Prozesse
# --------------------------------------------------------------------------

def which(cmd):
    return shutil.which(cmd) is not None


try:
    import ctypes
    _LIBC = ctypes.CDLL("libc.so.6")
    _LIBC.mallopt(-8, 2)          # M_ARENA_MAX: höchstens 2 Speicher-Pools, auch bei vielen Worker-Threads
except Exception:
    _LIBC = None


def trim_memory():
    """Freigegebenen Speicher ans System zurückgeben (glibc hält ihn sonst oft fest)."""
    import gc
    gc.collect()
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


def valid_pkg_tokens(text):
    tokens = text.split()
    if not tokens:
        return None
    for t in tokens:
        if not PKG_NAME_RE.match(t):
            return None
    return tokens


def quoted(tokens):
    return " ".join(shlex.quote(t) for t in tokens)


class _Invoker(QObject):
    """Führt Funktionen aus Worker-Threads im GUI-Thread aus."""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except RuntimeError as e:
            if "already deleted" not in str(e):     # Tab wurde inzwischen abgebaut → Rückmeldung verwerfen
                raise
        except Exception:
            import traceback
            tb = traceback.format_exc()
            print(tb, file=sys.stderr)
            try:
                with open(os.path.expanduser("~/tuxdex_error.log"), "a") as f:
                    f.write(f"\n--- {datetime.now().isoformat()} ---\n{tb}")
            except Exception:
                pass
            win = QApplication.activeWindow()
            if hasattr(win, "set_status"):
                win.set_status("Interner Fehler – Details in ~/tuxdex_error.log")


def kernel_modules_missing():
    """True, wenn der laufende Kernel nach einem Update keine Module mehr hat (Neustart nötig)."""
    return not os.path.isdir(f"/usr/lib/modules/{os.uname().release}")


def usb_storage_devices():
    """USB-Massenspeicher auf USB-Ebene: [(Name, Treiber geladen?)] – auch wenn kein Laufwerk entsteht."""
    found = []
    base = "/sys/bus/usb/devices"
    try:
        entries = os.listdir(base)
    except Exception:
        return found
    for e in entries:
        if ":" not in e:
            continue
        iface = os.path.join(base, e)
        try:
            with open(os.path.join(iface, "bInterfaceClass")) as f:
                if f.read().strip() != "08":
                    continue
        except Exception:
            continue
        dev = os.path.join(base, e.split(":")[0])
        name = []
        for fn in ("manufacturer", "product"):
            try:
                with open(os.path.join(dev, fn)) as f:
                    name.append(f.read().strip())
            except Exception:
                pass
        found.append((" ".join(name) or e, os.path.exists(os.path.join(iface, "driver"))))
    return found


_INVOKER = None


def ui(fn):
    _INVOKER.call.emit(fn)


def run_capture_async(args, callback, needs_sudo=False, timeout=60):
    """Führt args (optional mit 'sudo -n' Präfix) im Hintergrund aus.
    callback(rc, stdout, stderr) läuft im GUI-Thread. 'sudo -n' fragt NIE
    nach einem Passwort - schlägt einfach fehl, wenn keine gültige
    Sitzung existiert."""
    full_args = (["sudo", "-n"] + args) if needs_sudo else args
    if needs_sudo and not alpha_accepted():
        QTimer.singleShot(0, lambda: callback(1, "", "Alpha-Hinweis nicht bestätigt – keine root-Aktion."))
        return

    def worker():
        try:
            p = subprocess.run(full_args, capture_output=True, text=True, timeout=timeout)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except Exception as e:
            rc, out, err = 1, "", str(e)
        ui(lambda: callback(rc, out, err))

    threading.Thread(target=worker, daemon=True).start()


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Za-z0-9]|\x1b[=>]")
# Eine „offene“ letzte Zeile, die auf eine Eingabe wartet (pacman, paru, sudo …)
PROMPT_END_RE = re.compile(r"(\[[^\[\]]{1,40}\]|[:?]|==>)\s*$")


_ACTIVE_RUNS = set()        # laufende ProcessRun-Objekte (ein Tab mit laufendem Befehl wird nie abgebaut)
_CHILDREN = set()           # alle gestarteten Hintergrundprozesse – werden beim Beenden von Tuxdex mit beendet


def track(proc):
    _CHILDREN.add(proc)
    return proc


def running_children():
    return [p for p in list(_CHILDREN) if p.poll() is None]


def stop_children(timeout=3):
    """Beim Beenden: alle noch laufenden Kindprozesse (auch sudo …) beenden."""
    procs = running_children()
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    end = time.time() + timeout
    for p in procs:
        try:
            p.wait(max(0.1, end - time.time()))
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def orphaned_jobs():
    """Scans/Backups früherer Tuxdex-Sitzungen, die noch laufen: [(pid, name, rss_bytes, sekunden)]."""
    mine = os.getpid()
    res = []
    try:
        boot = float(_read("/proc/uptime").split()[0])
        hz = os.sysconf("SC_CLK_TCK")
    except Exception:
        boot, hz = 0, 100
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        cmd = _read(f"/proc/{d}/cmdline").replace("\0", " ")
        name = cmd.split(" ", 1)[0].rsplit("/", 1)[-1]
        if not ((name == "clamscan" and "tuxdex/quarantine" in cmd) or
                (name in ("rsync", "tar") and BACKUP_DIRNAME in cmd)):
            continue
        # gehört er zu diesem Tuxdex? Eltern-Kette hochlaufen
        pid, ours = d, False
        for _ in range(8):
            st = _read(f"/proc/{pid}/stat")
            ppid = st.rsplit(")", 1)[-1].split()[1] if ")" in st else "1"
            if ppid in ("0", "1"):
                break
            if int(ppid) == mine:
                ours = True
                break
            pid = ppid
        if ours:
            continue
        rss = 0
        m = re.search(r"^VmRSS:\s+(\d+)", _read(f"/proc/{d}/status"), re.M)
        if m:
            rss = int(m.group(1)) * 1024
        try:
            start = int(_read(f"/proc/{d}/stat").rsplit(")", 1)[-1].split()[19]) / hz
            age = boot - start
        except Exception:
            age = 0
        res.append((int(d), name, rss, age))
    return res


class ProcessRun:
    """Startet einen Prozess und streamt die Ausgabe ins LogView.

    interactive=True: der Prozess läuft in einem Pseudo-Terminal (über
    'script'), damit pacman/paru Fortschritt anzeigen und Rückfragen stellen.
    Wartet der Prozess auf eine Eingabe, erscheint ein Dialog; die Antwort
    wird an den Prozess geschickt."""

    def __init__(self, cmd, log, needs_sudo=False, on_done=None, interactive=False, on_line=None, cwd=None):
        self.log = log
        self.cwd = cwd
        self.on_done = on_done
        self.on_line = on_line   # optional: pro vollständiger Zeile (GUI-Thread)
        self.proc = None
        self.cancelled = False
        self.interactive = interactive and which("script")
        if self.interactive:
            inner = ["script", "-qefc", " ".join(shlex.quote(c) for c in cmd), "/dev/null"]
        else:
            inner = cmd
        self.full_cmd = (["sudo", "-n"] + inner) if needs_sudo else inner
        if needs_sudo and not alpha_accepted():
            log.append_text("Abgebrochen: Der Alpha-Hinweis wurde nicht bestätigt – keine Aktion mit root-Rechten.\n")
            if on_done:
                QTimer.singleShot(0, lambda: on_done(1))
            return
        _ACTIVE_RUNS.add(self)
        threading.Thread(target=self._worker, daemon=True).start()

    # -- Steuerung (GUI-Thread) --
    def send(self, text):
        try:
            self.proc.stdin.write(text.encode())
            self.proc.stdin.flush()
        except Exception:
            pass

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            if self.interactive:
                self.send("\x03")
                QTimer.singleShot(3000, self._kill)
            else:
                self._kill()

    def _kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    # -- Hintergrund --
    def _worker(self):
        try:
            self._work()
        finally:
            _ACTIVE_RUNS.discard(self)

    def _work(self):
        env = {**os.environ, "COLUMNS": "110", "LINES": "40"}
        try:
            self.proc = track(subprocess.Popen(
                self.full_cmd, stdin=subprocess.PIPE if self.interactive else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=self.cwd))
        except Exception as e:
            msg = f"Fehler beim Start: {e}\n"
            if self.log is not None:
                ui(lambda m=msg: self.log.append_text(m))
            if self.on_done:
                ui(lambda: self.on_done(1))
            return
        fd = self.proc.stdout.fileno()
        tail = ""          # aktuelle, noch nicht abgeschlossene Zeile
        asked_for = None   # Prompt, für den schon ein Dialog offen ist
        self.answered = threading.Event()
        while True:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                data = os.read(fd, 8192)
                if not data:
                    break
                text = data.decode(errors="replace")
                if self.log is not None:
                    ui(lambda t=text: self.log.write_raw(t))
                clean = ANSI_RE.sub("", text).replace("\r\n", "\n")
                if self.on_line:
                    self._linebuf = getattr(self, "_linebuf", "") + clean
                    *full, self._linebuf = self._linebuf.split("\n")
                    for ln in full:
                        ui(lambda l=ln: self.on_line(l))
                tail = (tail + clean).rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
                if asked_for is not None and tail != asked_for:
                    asked_for = None
            elif self.interactive and tail.strip() and asked_for != tail and PROMPT_END_RE.search(tail):
                # Prozess ist still und die letzte Zeile sieht nach einer Frage aus
                asked_for = tail
                self.answered.clear()
                ui(lambda t=tail: self._ask(t))
                self.answered.wait()
            elif self.proc.poll() is not None:
                break
        rc = self.proc.wait()
        if self.on_done:
            ui(lambda: self.on_done(rc))

    def _ask(self, prompt):
        try:
            if self.cancelled:
                return
            answer = ask_process_prompt(self.log.window(), prompt, self.log.last_lines(14))
            if answer is None:
                self.log.write_raw("\n[Abgebrochen]\n")
                self.cancel()
            else:
                self.send(answer + "\n")
        finally:
            self.answered.set()


def run_streaming(cmd, log, needs_sudo=False, on_done=None, clear_first=True, interactive=False, on_line=None,
                  cwd=None):
    """Führt cmd aus und streamt die Ausgabe live in das LogView - alles
    innerhalb des Programmfensters, kein externes Terminal. Gibt ein
    ProcessRun-Objekt zurück (u. a. zum Abbrechen)."""
    if clear_first:
        log.set_text("")
    return ProcessRun(cmd, log, needs_sudo=needs_sudo, on_done=on_done, interactive=interactive,
                      on_line=on_line, cwd=cwd)


class SequenceRun:
    """Mehrere Schritte nacheinander; cancel() stoppt den laufenden Schritt und alle weiteren."""

    def __init__(self, steps, log, on_all_done=None):
        self.steps, self.log, self.on_all_done = steps, log, on_all_done
        self.i = 0
        self.current = None
        self.cancelled = False
        log.set_text("")
        self._next()

    def _next(self):
        if self.cancelled or self.i >= len(self.steps):
            if self.on_all_done:
                self.on_all_done()
            return
        step = self.steps[self.i]
        self.i += 1
        self.log.append_text(f"\n$ {step['label']}\n")

        def done(rc):
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            self._next()

        self.current = run_streaming(step["cmd"], self.log, needs_sudo=step["needs_sudo"], on_done=done,
                                     clear_first=False, interactive=step.get("interactive", False),
                                     cwd=step.get("cwd"))

    def cancel(self):
        self.cancelled = True
        if self.current:
            self.current.cancel()


def run_sequence(steps, log, on_all_done=None):
    """Führt mehrere Schritte nacheinander aus. steps: Liste von Dicts
    {'cmd': [...], 'needs_sudo': bool, 'label': str, 'interactive': bool}."""
    return SequenceRun(steps, log, on_all_done)


# --------------------------------------------------------------------------
# Bausteine aus dem Design-System
# --------------------------------------------------------------------------

def Button(text, variant="ghost", on_click=None, tooltip=None):
    b = QPushButton(text)
    b.setProperty("variant", variant)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)  # Fokusring nur bei Tastatur-Navigation
    if on_click:
        b.clicked.connect(on_click)
    if tooltip:
        b.setToolTip(tooltip)
    return b


INFO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.6" fill="none"
 stroke="{c}" stroke-width="1.3"/><rect x="7.3" y="7" width="1.4" height="4.6" rx=".7" fill="{c}"/>
<circle cx="8" cy="4.9" r=".9" fill="{c}"/></svg>"""


def info_button(title, text):
    """Kleines (i): Tooltip beim Drüberfahren, Klick öffnet die Erklärung als Fenster."""
    b = QPushButton()
    b.setObjectName("Info")
    b.setIcon(svg_icon(INFO_SVG.replace("{c}", COLORS["muted"]), 16))
    b.setIconSize(QSize(16, 16))
    b.setFixedSize(22, 22)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)
    b.setToolTip(text)
    b.clicked.connect(lambda: show_info(b.window(), title, text))
    return b


def Label(text="", name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


class StatusBadge(QLabel):
    GLYPH = {"ok": "●", "warn": "▲", "danger": "✕", "info": "●", "off": "○"}

    def __init__(self, tone="off", text=""):
        super().__init__()
        self.setObjectName("Badge")
        self.set(tone, text)

    def set(self, tone, text):
        self.setProperty("tone", tone)
        self.setText(f"{self.GLYPH.get(tone, '○')}  {text}")
        repolish(self)


class Panel(QFrame):
    """Karte auf bg-200 mit Titelzeile (Titel links, Aktionen rechts)."""

    def __init__(self, title=None, actions=()):
        super().__init__()
        self.setObjectName("Panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.setSpacing(12)
        if title or actions:
            head = QHBoxLayout()
            head.setSpacing(8)
            if title:
                head.addWidget(Label(title, "PanelTitle"))
            head.addStretch(1)
            for a in actions:
                head.addWidget(a)
            outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)


def Field(label, widget):
    box = QVBoxLayout()
    box.setSpacing(4)
    box.addWidget(Label(label.upper(), "FieldLabel"))
    box.addWidget(widget)
    return box


def LineEdit(text="", placeholder="", mono=False, width=None):
    e = QLineEdit(text)
    if placeholder:
        e.setPlaceholderText(placeholder)
    if mono:
        e.setProperty("mono", True)
    if width:
        e.setMinimumWidth(width)
    e.setMinimumHeight(38)
    return e


class LogView(QPlainTextEdit):
    """Panel „Ausgabe“: Monospace, farbige Zeilen je nach Inhalt."""
    RE_EXIT = re.compile(r"^\[Exit-Code (-?\d+)\]")

    def __init__(self, min_height=140):
        super().__init__()
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setMinimumHeight(min_height)
        self.setFont(QFont(FONTS["mono"], 10))
        self._fmts = {}
        for tag, key in (("cmd", "accent"), ("head", "info"), ("ok", "ok"), ("warn", "warn"),
                         ("err", "danger"), ("muted", "muted"), (None, "ink")):
            f = QTextCharFormat()
            f.setForeground(QColor(COLORS[key]))
            self._fmts[tag] = f

    def _tag(self, line):
        s = line.strip()
        if not s:
            return None
        m = self.RE_EXIT.match(s)
        if m:
            return "ok" if m.group(1) == "0" else "err"
        if s.startswith("$ "):
            return "cmd"
        if s.startswith(("==", "::")):
            return "head"
        low = s.lower()
        if low.startswith(("error", "fehler", "e:")) or "fehlgeschlagen" in low or "failed" in low:
            return "err"
        if low.startswith(("warning", "warnung", "w:")):
            return "warn"
        return None

    def set_text(self, s):
        self.clear()
        self.append_text(s)

    def append_text(self, s):
        if LANG != "de" and isinstance(s, str):
            # eigene Meldungen zeilenweise übersetzen – nur ganze Zeilen, Befehlsausgaben bleiben unverändert
            s = "".join(tr(line, loose=False) for line in s.splitlines(keepends=True))
        self.write_raw(s)

    def write_raw(self, s):
        """Schreibt Terminal-Ausgabe: entfernt Farbcodes, \\r überschreibt die
        aktuelle Zeile (Fortschrittsbalken), Zeilen werden nach Inhalt eingefärbt."""
        s = ANSI_RE.sub("", s)
        if getattr(self, "_pending_cr", False):
            s = "\r" + s
        self._pending_cr = s.endswith("\r")
        if self._pending_cr:
            s = s[:-1]
        s = s.replace("\r\n", "\n")
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        for part in re.split(r"(\n|\r)", s):
            if part == "\n":
                cur.insertText("\n")
            elif part == "\r":
                cur.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
                cur.removeSelectedText()
            elif part:
                cur.insertText(part, self._fmts[self._tag(cur.block().text() + part)])
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def last_lines(self, n):
        lines = [l for l in self.toPlainText().splitlines() if l.strip()]
        return "\n".join(lines[-n:])


class UsageBar(QWidget):
    """Belegung (Swap/Datenträger): Name, Wert, Prozent, Balken. Ab 80 % warn, ab 90 % danger."""

    def __init__(self, label, used, total, unit="GiB"):
        super().__init__()
        self.label, self.used, self.total, self.unit = label, used, total, unit
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(320, 36)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pct = 0 if not self.total else max(0, min(100, round(self.used / self.total * 100)))
        mono = QFont(FONTS["mono"], 10)
        p.setFont(mono)
        p.setPen(QColor(COLORS["ink"]))
        w = self.width()
        p.drawText(QRectF(0, 0, w, 20), Qt.AlignLeft | Qt.AlignVCenter, self.label)
        g = 1024 ** 3 if self.unit == "GiB" else 1
        val = f"{fmt_bytes(self.used * g)} / {fmt_bytes(self.total * g)}   {pct} %"
        bold = QFont(FONTS["mono"], 10)
        bold.setWeight(QFont.DemiBold)
        p.setFont(bold)
        p.drawText(QRectF(0, 0, w, 20), Qt.AlignRight | Qt.AlignVCenter, val)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["bg3"]))
        p.drawRoundedRect(QRectF(0, 26, w, 8), 3, 3)
        color = COLORS["danger"] if pct >= 90 else COLORS["warn"] if pct >= 80 else COLORS["accent"]
        p.setBrush(QColor(color))
        if pct:
            p.drawRoundedRect(QRectF(0, 26, max(w * pct / 100, 6), 8), 3, 3)
        p.end()


class ProgressBar(QWidget):
    """Fortschrittsbalken; value=None zeigt einen wandernden Balken (unbestimmt)."""

    def __init__(self):
        super().__init__()
        self.value, self.text, self._phase = 0, "", 0
        self.setFixedHeight(22)
        self._t = QTimer(self)
        self._t.timeout.connect(self._anim)

    def set(self, value, text=""):
        self.value, self.text = value, text
        if value is None and not self._t.isActive():
            self._t.start(60)
        elif value is not None:
            self._t.stop()
        self.update()

    def _anim(self):
        self._phase = (self._phase + 2) % 100
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), 10
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["bg3"]))
        p.drawRoundedRect(QRectF(0, 6, w - 70, h), 5, 5)
        p.setBrush(QColor(COLORS["accent"]))
        if self.value is None:
            seg = (w - 70) * 0.25
            x = (w - 70 + seg) * self._phase / 100 - seg
            p.setClipRect(QRectF(0, 6, w - 70, h))
            p.drawRoundedRect(QRectF(x, 6, seg, h), 5, 5)
            p.setClipping(False)
        elif self.value > 0:
            p.drawRoundedRect(QRectF(0, 6, max(10, (w - 70) * self.value / 100), h), 5, 5)
        f = QFont(FONTS["mono"], 10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(COLORS["ink"]))
        p.drawText(QRectF(w - 64, 0, 64, 22), Qt.AlignRight | Qt.AlignVCenter,
                   self.text if self.value is not None else "…")
        p.end()


def page_header(title, *right):
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(Label(title, "PageTitle"))
    row.addStretch(1)
    for w in right:
        row.addWidget(w)
    return row


class Page(QScrollArea):
    """Seite eines Moduls: scrollbar, 24px Rand, 16px zwischen Blöcken."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("Page")
        self.setWidget(inner)
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(24, 20, 24, 20)
        self.lay.setSpacing(16)


# --------------------------------------------------------------------------
# Dialoge
# --------------------------------------------------------------------------

# Flache Dialog-Symbole in den Status-Farben statt der Symbole des System-Themes
_MSG_ICONS = {
    QMessageBox.Information: ("info", '<rect x="21.5" y="20" width="5" height="15" rx="2.5"/>'
                                      '<circle cx="24" cy="13.5" r="3"/>'),
    QMessageBox.Warning: ("warn", '<rect x="21.5" y="11" width="5" height="16" rx="2.5"/>'
                                  '<circle cx="24" cy="34" r="3"/>'),
    QMessageBox.Critical: ("danger", '<path d="M17 17l14 14M31 17l-14 14" stroke="{bg}" stroke-width="5" '
                                     'stroke-linecap="round" fill="none"/>'),
    QMessageBox.Question: ("accent", '<path d="M18.5 18.5a5.5 5.5 0 1 1 8 4.9c-1.6.8-2.5 2-2.5 3.6v1" '
                                     'stroke="{bg}" stroke-width="4.5" stroke-linecap="round" fill="none"/>'
                                     '<circle cx="24" cy="35" r="2.8"/>'),
}


def set_msg_icon(box, icon):
    tone, glyph = _MSG_ICONS[icon]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">'
           f'<circle cx="24" cy="24" r="22" fill="{COLORS[tone]}"/>'
           f'<g fill="{COLORS["bg2"]}">{glyph.replace("{bg}", COLORS["bg2"])}</g></svg>')
    box.setIconPixmap(svg_icon(svg, 40).pixmap(40, 40))


def style_msg_box(box):
    """Luft wie bei den eigenen Dialogen, Button-Varianten nachträglich anwenden."""
    box.layout().setContentsMargins(24, 20, 24, 16)
    box.layout().setHorizontalSpacing(16)
    box.layout().setVerticalSpacing(12)
    for b in box.buttons():
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.TabFocus)  # Fokusring nur bei Tastatur-Navigation
        repolish(b)


def _msg(parent, icon, title, text, buttons):
    box = QMessageBox(parent)
    set_msg_icon(box, icon)
    box.setWindowTitle(title)
    box.setText(text)
    result = {}
    for label, variant, value in buttons:
        b = box.addButton(label, QMessageBox.AcceptRole if value else QMessageBox.RejectRole)
        b.setProperty("variant", variant)
        result[b] = value
        if value:
            box.setDefaultButton(b)
    style_msg_box(box)
    box.exec()
    return result.get(box.clickedButton(), False)


def ask_confirm(parent, title, text, confirm="Bestätigen", danger=False):
    return _msg(parent, QMessageBox.Question, title, text,
                [("Abbrechen", "ghost", False), (confirm, "danger" if danger else "primary", True)])


def show_info(parent, title, text):
    _msg(parent, QMessageBox.Information, title, text, [("OK", "primary", True)])


def show_warning(parent, title, text):
    _msg(parent, QMessageBox.Warning, title, text, [("OK", "primary", True)])


def show_error(parent, title, text):
    _msg(parent, QMessageBox.Critical, title, text, [("OK", "primary", True)])


ALPHA_FILE = os.path.join(os.path.expanduser("~/.config"), "tuxdex", "settings.json")
ALPHA_TEXT = ("Tuxdex ist in der <b>Alpha-Phase</b>. Aktionen mit Administrator-Rechten (root) ändern dein System "
              "direkt – z. B. Pakete, Datenträger, Swap, Firewall, Systemdateien. Trotz Rückfragen und Prüfungen "
              "können Fehler passieren, bis hin zu Datenverlust oder einem System, das nicht mehr startet.<br><br>"
              "<b>Nutzung auf eigenes Risiko.</b> Es gibt keine Gewährleistung (MIT-Lizenz). Lege vorher ein Backup "
              "an und lies bei jeder Rückfrage, welcher Befehl ausgeführt wird – er steht immer im Ausgabefeld.")


def alpha_accepted():
    return bool(_load_json(ALPHA_FILE, {}).get("alpha_accepted"))


class AlphaDialog(QDialog):
    """Einmalige Zustimmung vor der ersten Aktion mit root-Rechten."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Alpha-Version – Hinweis")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(14)
        spacer = QWidget()
        spacer.setFixedSize(480, 0)
        lay.addWidget(spacer)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(StatusBadge("warn", "ALPHA"))
        head.addWidget(Label("Aktionen mit root-Rechten", "DialogTitle"), 1)
        lay.addLayout(head)
        txt = Label(ALPHA_TEXT, wrap=True)
        txt.setTextFormat(Qt.RichText)
        lay.addWidget(txt)
        self.cb = QCheckBox("Ich habe verstanden und nutze Tuxdex auf eigenes Risiko.")
        lay.addWidget(self.cb)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(Button("Abbrechen", "ghost", self.reject))
        self.ok = Button("Akzeptieren", "primary", self.accept)
        self.ok.setEnabled(False)
        self.cb.toggled.connect(self.ok.setEnabled)
        btns.addWidget(self.ok)
        lay.addLayout(btns)


def ask_alpha_consent(parent):
    """True, wenn der Alpha-Hinweis schon bestätigt ist oder jetzt bestätigt wird. Wird gespeichert."""
    if alpha_accepted():
        return True
    if AlphaDialog(parent).exec() != QDialog.Accepted:
        return False
    data = _load_json(ALPHA_FILE, {})
    data["alpha_accepted"] = datetime.now().isoformat(timespec="seconds")
    data["alpha_version"] = APP_VERSION
    _save_json(ALPHA_FILE, data)
    win = parent.window() if parent else None
    if hasattr(win, "refresh_alpha"):
        win.refresh_alpha()
    return True


class PasswordDialog(QDialog):
    def __init__(self, parent, title="Administrator-Passwort", heading="sudo-Passwort eingeben",
                 note="Wird für die Dauer des Programmlaufs gemerkt – du musst es danach nicht erneut eingeben.",
                 ok_text="Anmelden"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        spacer = QWidget()
        spacer.setFixedSize(380, 0)
        lay.addWidget(spacer)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)
        lay.addWidget(Label(heading, "DialogTitle"))
        lay.addWidget(Label(note, "Small", wrap=True))
        if heading == "sudo-Passwort eingeben":
            lay.addWidget(Label("▲  Alpha-Version: Aktionen mit root-Rechten auf eigenes Risiko.", "Warn", wrap=True))
        self.entry = LineEdit()
        self.entry.setEchoMode(QLineEdit.Password)
        self.entry.setMinimumHeight(42)
        f = self.entry.font()
        f.setPointSize(12)
        self.entry.setFont(f)
        self.error = Label("", "Error")
        self.error.hide()
        lay.addLayout(Field("Passwort", self.entry))
        lay.addWidget(self.error)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(Button("Abbrechen", "ghost", self.reject))
        ok = Button(ok_text, "primary", self.accept)
        ok.setDefault(True)
        btns.addWidget(ok)
        lay.addSpacing(8)
        lay.addLayout(btns)
        self.entry.returnPressed.connect(self.accept)
        self.entry.setFocus()


def ask_password(parent, title="Administrator-Passwort", error=None):
    dlg = PasswordDialog(parent, title)
    if error:
        dlg.error.setText(error)
        dlg.error.show()
    if dlg.exec() == QDialog.Accepted:
        return dlg.entry.text()
    return None


class PromptDialog(QDialog):
    """Rückfrage eines laufenden Prozesses (pacman/paru) als Fenster."""

    def __init__(self, parent, prompt, context, mode, yes_char="j", default_yes=True):
        super().__init__(parent)
        self.setWindowTitle("Rückfrage")
        self.answer = None
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)
        spacer = QWidget()
        spacer.setFixedSize(560, 0)
        lay.addWidget(spacer)
        lay.addWidget(Label("Der Vorgang wartet auf deine Antwort", "DialogTitle"))
        ctx = QPlainTextEdit(context)
        ctx.setObjectName("Log")
        ctx.setReadOnly(True)
        ctx.setFont(QFont(FONTS["mono"], 9))
        ctx.setMinimumHeight(180)
        ctx.moveCursor(QTextCursor.End)
        lay.addWidget(ctx)
        q = Label(prompt.strip(), "Value", wrap=True)
        lay.addWidget(q)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        stop = Button("Vorgang abbrechen", "danger", self.reject)
        btns.addWidget(stop)
        btns.addStretch(1)
        if mode == "yesno":
            no = Button("Nein", "ghost", lambda: self._done("n"))
            yes = Button("Ja", "primary", lambda: self._done(yes_char))
            btns.addWidget(no)
            btns.addWidget(yes)
            (yes if default_yes else no).setDefault(True)
            (yes if default_yes else no).setFocus()
        else:
            self.entry = LineEdit(mono=True, placeholder="leer lassen = Standard")
            lay.insertWidget(lay.count(), self.entry)
            btns.addWidget(Button("Standard übernehmen", "ghost", lambda: self._done("")))
            ok = Button("Senden", "primary", lambda: self._done(self.entry.text()))
            ok.setDefault(True)
            btns.addWidget(ok)
            self.entry.returnPressed.connect(lambda: self._done(self.entry.text()))
            self.entry.setFocus()
        lay.addLayout(btns)

    def _done(self, answer):
        self.answer = answer
        self.accept()


def ask_process_prompt(parent, prompt, context):
    """Gibt die Antwort (ohne Zeilenumbruch) zurück oder None = Vorgang abbrechen."""
    if re.search(r"passw|kennwort", prompt, re.I):
        return ask_password(parent, "Passwort angefragt")
    m = re.search(r"\[([JjYy])/([Nn])\]|\[([Nn])/([JjYy])\]", prompt)
    if m:
        if m.group(1):
            dlg = PromptDialog(parent, prompt, context, "yesno", m.group(1).lower(), m.group(1).isupper())
        else:
            dlg = PromptDialog(parent, prompt, context, "yesno", m.group(4).lower(), m.group(4).isupper())
    else:
        dlg = PromptDialog(parent, prompt, context, "text")
    if dlg.exec() == QDialog.Accepted:
        return dlg.answer
    return None


class PrivilegeManager:
    """Fragt bei Bedarf einmal per Dialog nach dem sudo-Passwort und hält
    die sudo-Sitzung danach über 'sudo -v' aktiv. Weitere Aufrufe nutzen
    'sudo -n' (nie interaktiv) und profitieren automatisch vom Cache -
    das gilt auch für interne sudo-Aufrufe von 'paru'."""

    def __init__(self, root, on_change=None):
        self.root = root
        self.on_change = on_change

    def is_authenticated(self):
        try:
            r = subprocess.run(["sudo", "-n", "-v"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def is_authenticated_nonblocking(self):
        """Prüft ohne die Sitzung zu verlängern, ob sudo ohne Passwort geht."""
        try:
            r = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def _notify(self, ok):
        if self.on_change:
            self.on_change(ok)

    def ensure(self, parent=None):
        """Blockiert kurz (GUI-Thread!) und zeigt bei Bedarf den Passwort-
        Dialog. Gibt True zurück, wenn eine gültige sudo-Sitzung besteht."""
        if not ask_alpha_consent(parent or self.root):
            return False
        if self.is_authenticated():
            self._notify(True)
            return True

        widget = parent or self.root
        error = None
        for attempt in range(3):
            pw = ask_password(widget, error=error)
            if pw is None:
                return False
            try:
                r = subprocess.run(
                    ["sudo", "-S", "-p", "", "-v"],
                    input=pw + "\n", capture_output=True, text=True, timeout=15,
                )
            except Exception as e:
                show_error(widget, "Fehler", f"Authentifizierung fehlgeschlagen: {e}")
                self._notify(False)
                return False
            finally:
                pw = None
            if r.returncode == 0:
                self._notify(True)
                return True
            left = 2 - attempt
            error = (f"Falsches Passwort – noch {left} Versuch{'e' if left != 1 else ''}."
                     if left else None)
        show_error(widget, "Fehler", "Passwort falsch oder Authentifizierung fehlgeschlagen.")
        self._notify(False)
        return False

    def logout(self):
        try:
            subprocess.run(["sudo", "-k"], timeout=10)
        except Exception:
            pass
        self._notify(False)


# --------------------------------------------------------------------------
# Modul: Updates (inkl. Update-Informationen)
# --------------------------------------------------------------------------

CACHE_DIR = os.path.join(os.path.expanduser("~/.cache"), "tuxdex")
UPDATE_CACHE = os.path.join(CACHE_DIR, "updates.json")

# Pakete, deren Update besondere Aufmerksamkeit verdient (Kernel, Boot, Basis-System, Grafik)
CRITICAL_PKGS = {
    "linux", "linux-lts", "linux-zen", "linux-hardened", "linux-rt", "linux-firmware",
    "systemd", "systemd-libs", "glibc", "gcc-libs", "grub", "mkinitcpio", "pacman", "sudo",
    "filesystem", "openssl", "mesa", "nvidia", "nvidia-open", "nvidia-dkms", "nvidia-utils",
    "nvidia-lts", "xorg-server", "wayland", "plasma-workspace", "kwin", "gnome-shell", "mutter",
    "python", "efibootmgr", "systemd-boot", "shim",
}
REBOOT_PKGS = {"linux", "linux-lts", "linux-zen", "linux-hardened", "linux-rt", "linux-firmware",
               "systemd", "glibc", "nvidia", "nvidia-open", "nvidia-dkms", "nvidia-utils",
               "nvidia-lts", "mesa"}

# Programme, die ihre Hauptversion routinemäßig hochzählen – dort ist ein Sprung kein „Major“
RAPID_RELEASE = {"firefox", "firefox-developer-edition", "librewolf", "chromium", "google-chrome",
                 "brave-bin", "vivaldi", "opera", "thunderbird", "microsoft-edge-stable-bin",
                 "zen-browser-bin", "ungoogled-chromium", "discord", "signal-desktop", "spotify"}
# Hier zählt schon ein Sprung der zweiten Stelle als Major (z. B. Python 3.12 → 3.13)
MINOR_IS_MAJOR = {"python", "perl", "ruby", "php", "llvm", "llvm-libs", "qt6-base", "qt5-base",
                  "boost", "boost-libs", "icu", "protobuf"}

UPDATE_RE = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)")


def _version_key(v, depth=1):
    epoch, rest = v.split(":", 1) if ":" in v else ("0", v)
    nums = re.findall(r"\d+", rest.split("-")[0])[:depth]
    return epoch, (tuple(int(n) for n in nums) if nums else None)


def classify_update(name, old, new):
    """'major' = Hauptversion/Epoch springt, 'system' = kritisches Paket, '' = normal."""
    if old and new and old != "—" and name not in RAPID_RELEASE:
        depth = 2 if name in MINOR_IS_MAJOR else 1
        e1, m1 = _version_key(old, depth)
        e2, m2 = _version_key(new, depth)
        if e1 != e2 or (m1 is not None and m2 is not None and m1 != m2):
            return "major"
    if name in CRITICAL_PKGS:
        return "system"
    return ""


def fmt_ago(ts):
    d = max(0, int(time.time() - ts))
    if d < 60:
        return "gerade eben"
    if d < 3600:
        return f"vor {d // 60} Min."
    if d < 86400:
        return f"vor {d // 3600} Std."
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


class UpdaterTab(Page):
    COLS = [("Paket", 220), ("Quelle", 90), ("Installiert", 170), ("Neu", 170), ("Hinweis", 160)]

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.checking = False
        self.updates = []
        self.checked_at = None

        self.badge = StatusBadge("off", "Noch nicht geprüft")
        self.lay.addLayout(page_header("System-Update", self.badge))

        row = QHBoxLayout()
        row.setSpacing(16)

        src = Panel("Quellen")
        self.cb_pacman = QCheckBox("Pacman")
        self.cb_pacman.setChecked(True)
        self.cb_aur = QCheckBox("AUR (paru)" if which("paru") else "AUR (paru nicht installiert)")
        self.cb_aur.setChecked(which("paru"))
        self.cb_aur.setEnabled(which("paru"))
        self.cb_flatpak = QCheckBox("Flatpak" if which("flatpak") else "Flatpak (nicht installiert)")
        self.cb_flatpak.setChecked(which("flatpak"))
        self.cb_flatpak.setEnabled(which("flatpak"))
        for cb in (self.cb_pacman, self.cb_flatpak, self.cb_aur):
            src.body.addWidget(cb)
        src.body.addSpacing(8)
        self.btn_update = Button("Update starten", "primary", self.start_update)
        src.body.addWidget(self.btn_update)
        self.btn_check = Button("Auf Updates prüfen", "ghost", self.check_updates)
        src.body.addWidget(self.btn_check)
        self.btn_cancel = Button("Update abbrechen", "danger", self.cancel_update)
        self.btn_cancel.hide()
        src.body.addWidget(self.btn_cancel)
        self.run = None
        src.body.addStretch(1)
        src.setFixedWidth(260)
        row.addWidget(src)

        info = Panel("Update-Informationen",
                     [Button("↻", "icon", self.load_last_update_times, "Aktualisieren")])
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(24)
        self.info_grid.setVerticalSpacing(12)
        info.body.addLayout(self.info_grid)
        self.major_note = Label("", "Danger", wrap=True)
        self.major_note.hide()
        info.body.addWidget(self.major_note)
        info.body.addStretch(1)
        row.addWidget(info, 1)
        self.lay.addLayout(row)

        lst = Panel("Verfügbare Updates")
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([c[0].upper() for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, (_, w) in enumerate(self.COLS):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(200)
        self.empty_label = Label("Noch nicht geprüft – „Auf Updates prüfen“ klicken.", "Muted")
        lst.body.addWidget(self.empty_label)
        lst.body.addWidget(self.table)
        self.table.hide()
        self.lay.addWidget(lst, 1)

        out = Panel("Ausgabe")
        self.log = LogView(180)
        out.body.addWidget(self.log)
        self.lay.addWidget(out, 1)

        self.load_last_update_times()
        self._load_cache()
        # Beim App-Start nach System-Updates suchen (pacman, AUR, Flatpak) – abschaltbar in den Einstellungen
        if load_settings().get("sys_check_on_start", True):
            QTimer.singleShot(1500, lambda: self.check_updates(silent=True))

    # ---- Anzeige --------------------------------------------------------

    def _info_item(self, row, label, value):
        self.info_grid.addLayout(Field(label, Label(value, "Value", wrap=True)), row // 2, row % 2)

    def load_last_update_times(self):
        while self.info_grid.count():
            item = self.info_grid.takeAt(0)
            lay = item.layout()
            if lay:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
        last_pacman = "keine Angabe gefunden"
        try:
            with open("/var/log/pacman.log", errors="ignore") as f:
                content = f.read()
            matches = re.findall(r"\[([^\]]+)\][^\n]*starting full system upgrade", content)
            if matches:
                last_pacman = matches[-1]
        except Exception:
            pass
        self._info_item(0, "Letztes vollständiges Update", last_pacman)
        self._info_item(1, "Zuletzt geprüft",
                        fmt_ago(self.checked_at) if self.checked_at else "noch nie")
        if which("flatpak"):
            try:
                mtime = os.path.getmtime("/var/lib/flatpak")
                fp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                fp = "nicht ermittelbar"
            self._info_item(2, "Letzte Änderung Flatpak (Näherung)", fp)

    def _render(self):
        ups = sorted(self.updates, key=lambda u: ({"major": 0, "system": 1}.get(u["kind"], 2), u["name"]))
        self.table.setRowCount(0)
        danger = QColor(COLORS["danger"])
        warn = QColor(COLORS["warn"])
        for u in ups:
            r = self.table.rowCount()
            self.table.insertRow(r)
            hint = {"major": "▲ Major-Version", "system": "▲ System/Kernel"}.get(u["kind"], "")
            if u["name"] in REBOOT_PKGS:
                hint = (hint + " · Neustart") if hint else "Neustart nötig"
            for c, v in enumerate((u["name"], u["source"], u["old"], u["new"], hint)):
                it = QTableWidgetItem(v)
                if u["kind"] == "major" and c in (0, 3, 4):
                    it.setForeground(danger)
                elif u["kind"] == "system" and c in (0, 4):
                    it.setForeground(warn)
                self.table.setItem(r, c, it)

        n = len(ups)
        important = [u for u in ups if u["kind"]]
        if self.checked_at is not None and hasattr(self.app, "show_sys_updates"):
            self.app.show_sys_updates(n, len(important))
        majors = [u for u in ups if u["kind"] == "major"]
        reboot = [u["name"] for u in ups if u["name"] in REBOOT_PKGS]
        if self.checked_at is None:
            self.badge.set("off", "Noch nicht geprüft")
        elif n == 0:
            self.badge.set("ok", "System aktuell")
        elif important:
            self.badge.set("danger", f"{n} Updates · {len(important)} wichtig")
        else:
            self.badge.set("warn", f"{n} Updates verfügbar")

        def names(lst):
            return ", ".join(lst[:8]) + (" …" if len(lst) > 8 else "")
        lines = []
        if majors:
            lines.append(f'<span style="color:{COLORS["danger"]}">▲ Major-Updates: '
                         f'{names([u["name"] for u in majors])} – vorher die Arch-News lesen '
                         f'(archlinux.org/news).</span>')
        sys_ups = [u["name"] for u in important if u["kind"] == "system"]
        if sys_ups:
            lines.append(f'<span style="color:{COLORS["warn"]}">▲ System-Pakete: {names(sys_ups)}</span>')
        if kernel_modules_missing():
            lines.append(f'<span style="color:{COLORS["danger"]}">✕ Kernel aktualisiert – bitte neu starten '
                         f'(sonst werden z. B. USB-Sticks nicht erkannt).</span>')
        if reboot:
            lines.append(f'<span style="color:{COLORS["muted"]}">Nach dem Update ist ein Neustart nötig '
                         f'({names(reboot)}).</span>')
        self.major_note.setTextFormat(Qt.RichText)
        self.major_note.setText("<br>".join(lines))
        self.major_note.setVisible(bool(lines))
        self.table.setMinimumHeight(min(max(n, 3), 12) * 30 + 44)

        self.table.setVisible(n > 0)
        self.empty_label.setVisible(n == 0)
        if self.checked_at is not None and n == 0:
            self.empty_label.setText("Keine Updates verfügbar – das System ist aktuell.")
        self.load_last_update_times()

    # ---- Cache ----------------------------------------------------------

    def _load_cache(self):
        try:
            with open(UPDATE_CACHE) as f:
                data = json.load(f)
            self.updates = data.get("updates", [])
            self.checked_at = float(data["checked_at"])
        except Exception:
            self.updates, self.checked_at = [], None
        self._render()

    def _save_cache(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(UPDATE_CACHE, "w") as f:
                json.dump({"checked_at": self.checked_at, "updates": self.updates}, f)
        except Exception:
            pass

    # ---- Aktionen -------------------------------------------------------

    def check_updates(self, silent=False):
        if self.checking:
            return
        self.checking = True
        self.btn_check.setEnabled(False)
        self.btn_check.setText("Prüfe …")
        if not silent:
            self.log.set_text("Prüfe auf Updates…\n")
        self.badge.set("info", "Prüfe …")

        def worker():
            parts, found = [], []
            if which("checkupdates"):
                r = subprocess.run(["checkupdates"], capture_output=True, text=True)
                for line in r.stdout.splitlines():
                    m = UPDATE_RE.match(line)
                    if m:
                        found.append(("Pacman",) + m.groups())
                parts.append("== Pacman ==\n" + (r.stdout.strip() or "Keine Updates verfügbar."))
            else:
                parts.append("== Pacman ==\ncheckupdates (Paket pacman-contrib) nicht installiert.")

            if which("paru"):
                r = subprocess.run(["paru", "-Qua"], capture_output=True, text=True)
                for line in r.stdout.splitlines():
                    m = UPDATE_RE.match(line)
                    if m:
                        found.append(("AUR",) + m.groups())
                parts.append("== AUR ==\n" + (r.stdout.strip() or "Keine Updates verfügbar."))
            else:
                parts.append("== AUR ==\nparu nicht installiert.")

            if which("flatpak"):
                r = subprocess.run(["flatpak", "remotes", "--columns=name"], capture_output=True, text=True)
                remotes = [l.strip() for l in r.stdout.splitlines() if l.strip()]
                fp_parts = []
                for remote in remotes:
                    r2 = subprocess.run(["flatpak", "remote-ls", "--updates", "--columns=application,version",
                                         remote], capture_output=True, text=True)
                    if r2.stdout.strip():
                        fp_parts.append(f"[{remote}]\n{r2.stdout.strip()}")
                        for line in r2.stdout.splitlines():
                            cols = line.split("\t")
                            if cols and cols[0].strip():
                                new = cols[1].strip() if len(cols) > 1 and cols[1].strip() else "—"
                                found.append(("Flatpak", cols[0].strip(), "—", new))
                parts.append("== Flatpak ==\n" + ("\n".join(fp_parts) if fp_parts else "Keine Updates verfügbar."))
            else:
                parts.append("== Flatpak ==\nflatpak nicht installiert.")

            text = "\n\n".join(parts)
            ups = [{"source": s, "name": n, "old": o, "new": nw, "kind": classify_update(n, o, nw)}
                   for s, n, o, nw in found]

            def show():
                self.checking = False
                self.btn_check.setEnabled(True)
                self.btn_check.setText("Auf Updates prüfen")
                if not silent:
                    self.log.set_text(text)
                self.updates = ups
                self.checked_at = time.time()
                self._save_cache()
                self._render()
            ui(show)

        threading.Thread(target=worker, daemon=True).start()

    def start_update(self):
        pacman = self.cb_pacman.isChecked()
        aur = self.cb_aur.isChecked() and which("paru")
        flatpak = self.cb_flatpak.isChecked() and which("flatpak")

        if not (pacman or aur or flatpak):
            show_info(self, "Nichts ausgewählt", "Bitte mindestens eine Quelle auswählen.")
            return
        text = "System jetzt aktualisieren?"
        majors = [u["name"] for u in self.updates if u["kind"] == "major"]
        if majors:
            text += ("\n\nAchtung, Major-Updates: " + ", ".join(majors[:10])
                     + "\nLies vorher die Arch-News (archlinux.org/news).")
        if not ask_confirm(self, "Update starten", text, "Update starten"):
            return
        if (pacman or aur) and not self.app.priv.ensure(self):
            return

        steps = []
        snap = snapshot_before_update_step() if (pacman or aur) else None
        if snap:
            steps.append(snap)
        if pacman and aur:
            steps.append({"cmd": ["paru", "-Syu"], "needs_sudo": False, "interactive": True,
                          "label": "paru -Syu  (Repos + AUR)"})
        elif pacman:
            steps.append({"cmd": ["pacman", "-Syu"], "needs_sudo": True, "interactive": True,
                          "label": "sudo pacman -Syu"})
        elif aur:
            steps.append({"cmd": ["paru", "-Sua"], "needs_sudo": False, "interactive": True,
                          "label": "paru -Sua"})
        if flatpak:
            steps.append({"cmd": ["flatpak", "update", "-y"], "needs_sudo": False, "interactive": True,
                          "label": "flatpak update"})

        self.btn_update.setEnabled(False)
        self.btn_update.setText("Läuft …")
        self.btn_cancel.show()
        self.badge.set("info", "Update läuft")

        def all_done():
            cancelled = self.run.cancelled
            self.run = None
            self.btn_cancel.hide()
            self.btn_update.setEnabled(True)
            self.btn_update.setText("Update starten")
            self.app.set_status("Update abgebrochen." if cancelled else "Update abgeschlossen.")
            self.check_updates(silent=True)   # Liste & gespeicherten Stand auffrischen

        self.run = run_sequence(steps, self.log, on_all_done=all_done)
        self.app.set_status("Update läuft … Rückfragen erscheinen als Fenster.")

    def cancel_update(self):
        if self.run and ask_confirm(self, "Update abbrechen", "Laufendes Update wirklich abbrechen?\n\n"
                                    "Pacman bricht sauber ab, solange noch nichts installiert wird.",
                                    "Abbrechen", danger=True):
            self.run.cancel()


# --------------------------------------------------------------------------
# Modul: Software (Übersicht + Install/Uninstall)
# --------------------------------------------------------------------------

_DEC_UNITS = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
              "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4, "bytes": 1}


def parse_size(text):
    m = re.search(r"([\d.,]+)\s*([A-Za-z]+)", text or "")
    if not m:
        return 0
    try:
        return int(float(m.group(1).replace(",", ".")) * _DEC_UNITS.get(m.group(2), 1))
    except ValueError:
        return 0


def pacman_infos(names=None):
    """{name: {version, desc, size, date, url, reason, required_by, depends}} über LC_ALL=C pacman -Qi"""
    cmd = ["pacman", "-Qi"] + (list(names) if names else [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
                             timeout=120).stdout
    except Exception:
        return {}
    res = {}
    for block in out.split("\n\n"):
        d, key = {}, None
        for line in block.splitlines():
            if not line.strip():
                continue
            if line[:1] != " " and ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                d[key] = val.strip()
            elif key:
                d[key] += " " + line.strip()
        if "Name" not in d:
            continue
        try:
            date = parse_c_date(d.get("Install Date", "")).timestamp()
        except Exception:
            date = 0
        res[d["Name"]] = {
            "version": d.get("Version", ""), "desc": d.get("Description", ""),
            "size": parse_size(d.get("Installed Size", "")), "date": date, "url": d.get("URL", ""),
            "reason": d.get("Install Reason", ""),
            "required_by": [x for x in d.get("Required By", "").split() if x != "None"],
            "depends": [x for x in d.get("Depends On", "").split() if x != "None"],
        }
    return res


def pacman_repos():
    """{paket: repo} aus den Sync-Datenbanken"""
    try:
        out = subprocess.run(["pacman", "-Sl"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return {}
    m = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 2:
            m.setdefault(p[1], p[0])
    return m


def pacman_desktop_icons():
    """{paket: (icon, anzeigename)} über die .desktop-Dateien, die ein Paket mitbringt"""
    res = {}
    try:
        proc = subprocess.Popen(["pacman", "-Ql"], stdout=subprocess.PIPE, text=True,
                                stderr=subprocess.DEVNULL)
    except Exception:
        return res
    for line in proc.stdout:
        if "/usr/share/applications/" not in line or not line.rstrip().endswith(".desktop"):
            continue
        pkg, _, path = line.rstrip().partition(" ")
        if pkg in res:
            continue
        icon = name = None
        nodisplay = False
        in_main = False
        for l in _read(path).splitlines():
            if l.startswith("["):
                in_main = l.strip() == "[Desktop Entry]"
            elif in_main and l.startswith("Icon=") and icon is None:
                icon = l[5:].strip()
            elif in_main and l.startswith("Name=") and name is None:
                name = l[5:].strip()
            elif in_main and l.strip() == "NoDisplay=true":
                nodisplay = True
        if icon and not nodisplay:
            res[pkg] = (icon, name or pkg)
    proc.wait()
    return res


def flatpak_apps():
    """[{id, name, version, branch, origin, installation, size, date, path, desc}]"""
    if not which("flatpak"):
        return []
    try:
        out = subprocess.run(["flatpak", "list", "--app",
                              "--columns=application,name,version,branch,origin,installation,size,description"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return []
    apps = []
    for line in out.splitlines():
        c = line.split("\t")
        if len(c) < 7 or not c[0].strip():
            continue
        inst = c[5].strip()
        base = os.path.expanduser("~/.local/share/flatpak") if inst == "user" else "/var/lib/flatpak"
        path = os.path.join(base, "app", c[0].strip())
        try:
            date = os.path.getmtime(os.path.realpath(os.path.join(path, "current", "active")))
        except Exception:
            date = 0
        apps.append({"id": c[0].strip(), "name": c[1].strip() or c[0].strip(), "version": c[2].strip(),
                     "branch": c[3].strip(), "origin": c[4].strip(), "installation": inst,
                     "size": parse_size(c[6]), "date": date, "path": path,
                     "desc": c[7].strip() if len(c) > 7 else ""})
    return apps


class DateItem(QTableWidgetItem):
    def __init__(self, ts):
        super().__init__(datetime.fromtimestamp(ts).strftime("%d.%m.%Y") if ts else "—")
        self.setData(Qt.UserRole, ts or 0)

    def __lt__(self, other):
        return (self.data(Qt.UserRole) or 0) < (other.data(Qt.UserRole) or 0)


class SoftwareTab(Page):
    CATEGORIES = ["Programme (selbst installiert)", "AUR / Fremd-Pakete", "Flatpak",
                  "Alle Pakete (inkl. Abhängigkeiten)"]
    COLS = [("Name", 260), ("Version", 150), ("Größe", 110), ("Quelle · Ort", 190),
            ("Installiert am", 110), ("Beschreibung", 300)]

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.items = []          # [{key, label, icon, version, size, source, where, date, desc, kind, ...}]
        self.loading = False

        self.count_badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Software", self.count_badge))

        pk = Panel("Installierte Software")
        top = QHBoxLayout()
        top.setSpacing(8)
        self.cat_cb = QComboBox()
        self.cat_cb.addItems(self.CATEGORIES)
        self.cat_cb.setMinimumWidth(270)
        self.cat_cb.currentIndexChanged.connect(lambda _: self.refresh_list())
        top.addLayout(Field("Anzeigen", self.cat_cb))
        self.search = LineEdit(placeholder="Name oder Beschreibung …")
        self.search.textChanged.connect(lambda _: self.apply_filter())
        top.addLayout(Field("Suche", self.search), 1)
        refresh_box = QVBoxLayout()
        refresh_box.addStretch(1)
        refresh_box.addWidget(Button("↻", "icon", self.refresh_list, "Liste neu laden"))
        top.addLayout(refresh_box)
        pk.body.addLayout(top)

        selrow = QHBoxLayout()
        selrow.setSpacing(12)
        self.cb_all = QCheckBox("Alle sichtbaren auswählen")
        self.cb_all.clicked.connect(self._check_all)
        selrow.addWidget(self.cb_all)
        self.sel_count = Label("Nichts ausgewählt", "Muted")
        selrow.addWidget(self.sel_count, 1)
        self.b_uninst = Button("Auswahl deinstallieren", "danger", self.uninstall)
        self.b_uninst.setEnabled(False)
        selrow.addWidget(self.b_uninst)
        pk.body.addLayout(selrow)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([c[0].upper() for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(22, 22))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setStretchLastSection(True)
        for i, (_, w) in enumerate(self.COLS):
            self.table.setColumnWidth(i, w)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(360)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(lambda *a: self._show_details())
        self.table.cellDoubleClicked.connect(self._toggle_row)
        pk.body.addWidget(self.table)
        pk.body.addWidget(Label("Kästchen anklicken (oder Doppelklick auf die Zeile) wählt aus – "
                                "kein Strg nötig.", "Hint"))

        self.details = Label("", "Muted", wrap=True)
        self.details.setTextFormat(Qt.RichText)
        self.details.setOpenExternalLinks(True)
        self.details.setTextInteractionFlags(Qt.TextBrowserInteraction)
        pk.body.addWidget(self.details)
        self.lay.addWidget(pk, 2)

        act = Panel("Installieren")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.install_edit = LineEdit(placeholder="z. B. firefox htop  ·  bei Flatpak: org.gimp.GIMP", mono=True)
        self.install_edit.returnPressed.connect(self.install)
        row.addLayout(Field("Paketname(n)", self.install_edit), 1)
        self.inst_src = QComboBox()
        self.inst_src.addItems(["Offizielle Paketquellen (pacman)", "AUR (paru)", "Flatpak (Flathub)"])
        self.inst_src.setMinimumWidth(240)
        row.addLayout(Field("Woher", self.inst_src))
        btns = QVBoxLayout()
        btns.addStretch(1)
        btns.addWidget(Button("Installieren", "primary", self.install))
        row.addLayout(btns)
        act.body.addLayout(row)
        self.cb_user_flatpak = QCheckBox("Flatpak: nur für mich installieren (--user, ohne Passwort)")
        act.body.addWidget(self.cb_user_flatpak)
        self.lay.addWidget(act)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out, 1)

        self.refresh_list()

    # ---- Laden --------------------------------------------------------------

    def refresh_list(self):
        if self.loading:
            return
        self.loading = True
        cat = self.cat_cb.currentIndex()
        self.count_badge.set("info", "Lade …")
        self.details.setText("")

        def worker():
            items, err = [], None
            try:
                if cat == 2:
                    if not which("flatpak"):
                        err = "flatpak ist nicht installiert"
                    for a in flatpak_apps():
                        items.append({"key": a["id"], "label": a["name"], "icon": a["id"], "version": a["version"],
                                      "size": a["size"], "date": a["date"], "desc": a["desc"], "kind": "flatpak",
                                      "source": f"Flatpak · {'nur ich' if a['installation'] == 'user' else 'System'}",
                                      "where": a["path"], "installation": a["installation"],
                                      "extra": f"{a['origin']} · Zweig {a['branch']}"})
                else:
                    flag = {0: "-Qeq", 1: "-Qmq", 3: "-Qq"}[cat]
                    names = subprocess.run(["pacman", flag], capture_output=True, text=True).stdout.split()
                    infos = pacman_infos(names)
                    repos = pacman_repos()
                    icons = pacman_desktop_icons()
                    for n in names:
                        i = infos.get(n, {})
                        repo = repos.get(n, "AUR / lokal")
                        ic = icons.get(n)
                        items.append({"key": n, "label": n, "app_name": ic[1] if ic else "",
                                      "icon": ic[0] if ic else None, "version": i.get("version", ""),
                                      "size": i.get("size", 0), "date": i.get("date", 0), "desc": i.get("desc", ""),
                                      "kind": "pacman", "source": f"{repo} · /usr", "repo": repo, "where": "/usr",
                                      "url": i.get("url", ""), "reason": i.get("reason", ""),
                                      "required_by": i.get("required_by", []), "depends": i.get("depends", [])})
            except Exception as e:
                err = str(e)
            ui(lambda: self._fill(items, err))
        threading.Thread(target=worker, daemon=True).start()

    def _fill(self, items, err):
        self.loading = False
        self.items = items
        if err and not items:
            self.count_badge.set("danger" if "nicht installiert" not in err else "off", err)
        else:
            total = sum(i["size"] for i in items)
            self.count_badge.set("ok", f"{len(items)} Pakete · {fmt_bytes(total)}")
        self.apply_filter()

    def apply_filter(self):
        q = self.search.text().lower().strip()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        rows = [i for i in self.items
                if not q or q in f"{i['label']} {i.get('app_name', '')} {i['key']} {i['desc']}".lower()]
        self.table.setRowCount(len(rows))
        mono = QFont(FONTS["mono"], 10)
        for r, it in enumerate(rows):
            label = it["label"] + (f"  ·  {it['app_name']}" if it.get("app_name") and it["app_name"].lower() != it["label"].lower() else "")
            name = QTableWidgetItem(label)
            name.setData(Qt.UserRole, it["key"])
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked if it.get("checked") else Qt.Unchecked)
            if it.get("icon"):
                ic = themed_icon(it["icon"])
                name.setIcon(ic if not ic.isNull() else letter_icon(it["label"]))
            else:
                name.setIcon(letter_icon(it["label"]))
            cells = [name, QTableWidgetItem(it["version"]), NumItem(fmt_bytes(it["size"]) if it["size"] else "—",
                                                                    it["size"]),
                     QTableWidgetItem(it["source"]), DateItem(it["date"]), QTableWidgetItem(it["desc"])]
            for c, cell in enumerate(cells):
                if c in (1, 2):
                    cell.setFont(mono)
                if c == 2:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 5:
                    cell.setToolTip(it["desc"])
                self.table.setItem(r, c, cell)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        self._update_sel()
        QTimer.singleShot(500, trim_memory)

    # ---- Auswahl ------------------------------------------------------------

    def _by_key(self, key):
        return next((i for i in self.items if i["key"] == key), None)

    def _item_changed(self, cell):
        if cell.column() != 0:
            return
        it = self._by_key(cell.data(Qt.UserRole))
        if it is not None:
            it["checked"] = cell.checkState() == Qt.Checked
        self._update_sel()

    def _toggle_row(self, row, col):
        cell = self.table.item(row, 0)
        if cell:
            cell.setCheckState(Qt.Unchecked if cell.checkState() == Qt.Checked else Qt.Checked)

    def _check_all(self, on):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            cell = self.table.item(r, 0)
            cell.setCheckState(Qt.Checked if on else Qt.Unchecked)
            it = self._by_key(cell.data(Qt.UserRole))
            if it is not None:
                it["checked"] = on
        self.table.blockSignals(False)
        self._update_sel()

    def checked(self):
        return [i for i in self.items if i.get("checked")]

    def _update_sel(self):
        sel = self.checked()
        self.b_uninst.setEnabled(bool(sel))
        if sel:
            self.sel_count.setText(f"{len(sel)} ausgewählt · {fmt_bytes(sum(i['size'] for i in sel))} werden frei")
        else:
            self.sel_count.setText("Nichts ausgewählt")

    def _show_details(self):
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        it = self._by_key(cell.data(Qt.UserRole)) if cell else None
        if not it:
            self.details.setText("")
            return
        parts = [f"<b>{it['label']}</b> {it['version']} – {it['desc']}"]
        where = it["where"]
        parts.append(f"Ort: <code>{short_path(where)}</code> · Quelle: {it['source'].split(' · ')[0]}"
                     + (f" · {it['extra']}" if it.get("extra") else ""))
        if it.get("reason"):
            parts.append("Installiert " + ("von dir" if it["reason"].startswith("Explicitly") else
                                           "als Abhängigkeit"))
        if it.get("required_by"):
            parts.append("Wird benötigt von: " + ", ".join(it["required_by"][:12])
                         + (" …" if len(it["required_by"]) > 12 else ""))
        if it.get("url"):
            parts.append(f'<a style="color:{COLORS["accent"]}" href="{it["url"]}">{it["url"]}</a>')
        self.details.setText("<br>".join(parts))

    # ---- Aktionen -----------------------------------------------------------

    def install(self):
        pkgs = valid_pkg_tokens(self.install_edit.text())
        if not pkgs:
            show_warning(self, "Ungültige Eingabe",
                         "Bitte gültige(n) Paketnamen eingeben (Leerzeichen-getrennt für mehrere).")
            return
        src = self.inst_src.currentIndex()
        if src == 0:
            if not self.app.priv.ensure(self):
                return
            cmd, needs_sudo, label = ["pacman", "-S", "--"] + pkgs, True, "sudo pacman -S " + quoted(pkgs)
        elif src == 1:
            if not which("paru"):
                show_error(self, "paru fehlt", "paru ist nicht installiert.")
                return
            if not self.app.priv.ensure(self):
                return
            cmd, needs_sudo, label = ["paru", "-S", "--"] + pkgs, False, "paru -S " + quoted(pkgs)
        else:
            if not which("flatpak"):
                show_error(self, "flatpak fehlt", "flatpak ist nicht installiert – im Tab „Flatpak“ einrichten.")
                return
            scope_user = self.cb_user_flatpak.isChecked()
            if not scope_user and not self.app.priv.ensure(self):
                return
            cmd = ["flatpak", "install"] + (["--user"] if scope_user else []) + ["-y", "flathub", "--"] + pkgs
            needs_sudo, label = not scope_user, "flatpak install " + quoted(pkgs)

        self.log.set_text(f"$ {label}\n")

        def done(rc):
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            self.refresh_list()
            self.app.set_status(f"Installation beendet (Exit {rc}).")

        run_streaming(cmd, self.log, needs_sudo=needs_sudo, clear_first=False, on_done=done, interactive=True)
        self.install_edit.clear()

    def uninstall(self):
        sel = self.checked()
        if not sel:
            return
        names = [i["key"] for i in sel]
        needed = [f"{i['key']} (benötigt von {', '.join(i['required_by'][:3])})" for i in sel
                  if i.get("required_by") and not set(i["required_by"]) <= set(names)]
        text = f"{len(sel)} Paket(e) deinstallieren? ({fmt_bytes(sum(i['size'] for i in sel))})\n\n" \
               + "\n".join(names[:25]) + ("\n…" if len(names) > 25 else "")
        if needed:
            text += "\n\n▲ Werden noch gebraucht – pacman entfernt dann auch die abhängigen Pakete oder bricht ab:\n" \
                    + "\n".join(needed[:8])
        if not ask_confirm(self, "Deinstallieren", text, "Deinstallieren", danger=True):
            return
        steps = []
        pac = [i["key"] for i in sel if i["kind"] == "pacman"]
        fp_sys = [i["key"] for i in sel if i["kind"] == "flatpak" and i.get("installation") != "user"]
        fp_user = [i["key"] for i in sel if i["kind"] == "flatpak" and i.get("installation") == "user"]
        if (pac or fp_sys) and not self.app.priv.ensure(self):
            return
        if pac:
            steps.append({"cmd": ["pacman", "-Rns", "--"] + pac, "needs_sudo": True, "interactive": True,
                          "label": "sudo pacman -Rns " + quoted(pac)})
        if fp_sys:
            steps.append({"cmd": ["flatpak", "uninstall", "--system", "-y", "--"] + fp_sys, "needs_sudo": True,
                          "label": "sudo flatpak uninstall " + quoted(fp_sys)})
        if fp_user:
            steps.append({"cmd": ["flatpak", "uninstall", "--user", "-y", "--"] + fp_user, "needs_sudo": False,
                          "label": "flatpak uninstall --user " + quoted(fp_user)})
        run_sequence(steps, self.log, on_all_done=lambda: (self.refresh_list(),
                                                           self.app.set_status("Deinstallation beendet.")))


# --------------------------------------------------------------------------
# Modul: Flatpak (Apps & Berechtigungen – wie Flatseal)
# --------------------------------------------------------------------------

class Switch(QPushButton):
    """Kippschalter (an/aus) im Stil des Design-Systems."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(42, 24)
        self.setObjectName("Switch")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QColor(COLORS["accent"] if on else COLORS["bg3"])
        if not self.isEnabled():
            track = QColor(COLORS["line"])
        p.setPen(Qt.NoPen if on else QPen(QColor(COLORS["line_strong"]), 1))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(1, 2, 40, 20), 10, 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["on_accent"] if on else COLORS["muted"]) if self.isEnabled()
                   else QColor(COLORS["line_strong"]))
        x = 23 if on else 5
        p.drawEllipse(QRectF(x, 5, 14, 14))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(COLORS["focus"]), 2))
            p.drawRoundedRect(QRectF(1, 2, 40, 20), 10, 10)
        p.end()


FLATPAK_OVERRIDE_DIR = os.path.expanduser("~/.local/share/flatpak/overrides")
FLATPAK_SYS_OVERRIDE_DIR = "/var/lib/flatpak/overrides"

# (Gruppe, Schlüssel, Name, Titel, Beschreibung, Risiko)  – Risiko: "" | "warn" | "danger"
FLATPAK_PERMS = [
    ("Netzwerk", "shared", "network", "Internet & Netzwerk", "Darf Verbindungen ins Internet und lokale Netz aufbauen.", ""),
    ("Netzwerk", "shared", "ipc", "Gemeinsamer Speicher (IPC)", "Nötig für viele X11-Programme; teilt Speicher mit dem System.", ""),
    ("Anzeige & Ton", "sockets", "wayland", "Wayland-Fenster", "Moderne, abgeschottete Fensterdarstellung.", ""),
    ("Anzeige & Ton", "sockets", "x11", "X11-Fenster", "Ältere Fensterdarstellung – X11-Programme können Tastatur und Bildschirm anderer Programme mitlesen.", "warn"),
    ("Anzeige & Ton", "sockets", "fallback-x11", "X11 nur als Ausweichlösung", "X11 nur benutzen, wenn kein Wayland läuft.", ""),
    ("Anzeige & Ton", "sockets", "pulseaudio", "Ton & Mikrofon", "Wiedergabe und Aufnahme über PulseAudio/PipeWire.", ""),
    ("Anzeige & Ton", "devices", "dri", "Grafikbeschleunigung (GPU)", "Direkter Zugriff auf die Grafikkarte für 3D und Video.", ""),
    ("Geräte", "devices", "all", "Alle Geräte", "Webcam, Controller, USB-Geräte usw. – sehr weitreichend.", "danger"),
    ("Geräte", "devices", "kvm", "Virtualisierung (KVM)", "Für virtuelle Maschinen und Emulatoren.", ""),
    ("Geräte", "devices", "shm", "Gemeinsamer Gerätespeicher (/dev/shm)", "Für manche Spiele und Kommunikations-Apps nötig.", ""),
    ("Geräte", "features", "bluetooth", "Bluetooth", "Direkter Bluetooth-Zugriff.", ""),
    ("Geräte", "sockets", "cups", "Drucken", "Zugriff auf den Druckdienst.", ""),
    ("Geräte", "sockets", "pcsc", "Smartcards", "Chipkartenleser, z. B. für den Personalausweis.", ""),
    ("Geräte", "sockets", "gpg-agent", "GPG-Schlüssel", "Darf deinen GPG-Agenten zum Signieren nutzen.", "warn"),
    ("Geräte", "sockets", "ssh-auth", "SSH-Schlüssel", "Darf deinen SSH-Agenten für Anmeldungen nutzen.", "warn"),
    ("Dateien", "filesystems", "host", "Alle Dateien", "Vollzugriff auf das ganze Dateisystem – hebt die Abschottung weitgehend auf.", "danger"),
    ("Dateien", "filesystems", "host-os", "Systemdateien", "Lesezugriff auf Programme und Bibliotheken des Systems.", "warn"),
    ("Dateien", "filesystems", "host-etc", "Systemeinstellungen (/etc)", "Zugriff auf die Konfiguration des Systems.", "warn"),
    ("Dateien", "filesystems", "home", "Persönlicher Ordner", "Zugriff auf alle deine Dateien im Home-Ordner.", "warn"),
    ("Dateien", "filesystems", "xdg-download", "Downloads", "", ""),
    ("Dateien", "filesystems", "xdg-documents", "Dokumente", "", ""),
    ("Dateien", "filesystems", "xdg-pictures", "Bilder", "", ""),
    ("Dateien", "filesystems", "xdg-music", "Musik", "", ""),
    ("Dateien", "filesystems", "xdg-videos", "Videos", "", ""),
    ("Dateien", "filesystems", "xdg-desktop", "Schreibtisch", "", ""),
    ("System & Entwicklung", "sockets", "session-bus", "Kompletter Sitzungs-Bus", "Darf mit allen Programmen deiner Sitzung sprechen.", "danger"),
    ("System & Entwicklung", "sockets", "system-bus", "Kompletter System-Bus", "Darf mit allen Systemdiensten sprechen.", "danger"),
    ("System & Entwicklung", "features", "devel", "Entwickler-Funktionen", "z. B. Debugger (ptrace) erlauben.", "warn"),
    ("System & Entwicklung", "features", "multiarch", "32-Bit-Programme", "Nötig für manche Spiele (Steam, Wine).", ""),
    ("System & Entwicklung", "features", "per-app-dev-shm", "Eigener /dev/shm", "Trennt gemeinsamen Speicher zwischen Apps.", ""),
]
FS_MODES = [("rw", "Lesen & Schreiben"), ("ro", "Nur lesen"), ("create", "Lesen, Schreiben & Anlegen")]


def read_keyfile(text):
    """{section: {key: value}} – einfacher GKeyFile-Leser"""
    data, sec = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            data.setdefault(sec, {})
        elif sec is not None and "=" in line:
            k, _, v = line.partition("=")
            data[sec][k.strip()] = v.strip()
    return data


def write_keyfile(data):
    out = []
    for sec, kv in data.items():
        kv = {k: v for k, v in kv.items() if v not in ("", None)}
        if not kv:
            continue
        out.append(f"[{sec}]")
        out += [f"{k}={v}" for k, v in kv.items()]
        out.append("")
    return "\n".join(out)


def _tokens(value):
    return [t for t in (value or "").split(";") if t]


def _fs_parse(tok):
    neg = tok.startswith("!")
    tok = tok[1:] if neg else tok
    name, _, mode = tok.partition(":")
    return name, (mode or "rw"), neg


class FlatpakTab(Page):
    ALL = "__global__"

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.apps = []
        self.cur = None           # App-ID oder ALL
        self.meta = {}            # Standardrechte der App ([Context])
        self.rows = {}            # (kind, name) -> (switch, changed_label, mode_combo)
        self._building = False

        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Flatpak", self.badge, Button("↻", "icon", self.refresh, "Neu laden")))

        self.setup = Panel()
        self.setup_text = Label("", "Muted", wrap=True)
        self.setup_text.setTextFormat(Qt.RichText)
        self.setup.body.addWidget(self.setup_text)
        sb = QHBoxLayout()
        self.b_install_fp = Button("Flatpak installieren", "primary", self.install_flatpak)
        self.b_flathub = Button("Flathub hinzufügen", "primary", self.add_flathub)
        self.b_unused = Button("Ungenutzte Laufzeiten entfernen", "ghost", self.remove_unused)
        for b in (self.b_install_fp, self.b_flathub, self.b_unused):
            sb.addWidget(b)
        sb.addStretch(1)
        self.setup.body.addLayout(sb)
        self.lay.addWidget(self.setup)

        main = QHBoxLayout()
        main.setSpacing(16)
        # --- Liste links ---
        left = Panel("Apps")
        left.setFixedWidth(320)
        self.search = LineEdit(placeholder="App suchen …")
        self.search.textChanged.connect(lambda _: self._fill_list())
        left.body.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("AppList")
        self.list.setIconSize(QSize(32, 32))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setWordWrap(False)
        self.list.setMinimumHeight(560)
        self.list.currentItemChanged.connect(lambda cur, prev: self._select(cur.data(Qt.UserRole) if cur else None))
        left.body.addWidget(self.list, 1)
        main.addWidget(left, 0, Qt.AlignTop)

        # --- Details rechts ---
        right = QVBoxLayout()
        right.setSpacing(16)
        head = Panel()
        hl = QHBoxLayout()
        hl.setSpacing(16)
        self.h_icon = QLabel()
        self.h_icon.setFixedSize(64, 64)
        hl.addWidget(self.h_icon, 0, Qt.AlignTop)
        ht = QVBoxLayout()
        ht.setSpacing(2)
        self.h_name = Label("", "PageTitle")
        self.h_id = Label("", "Value")
        self.h_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.h_meta = Label("", "Hint", wrap=True)
        for w in (self.h_name, self.h_id, self.h_meta):
            ht.addWidget(w)
        hl.addLayout(ht, 1)
        head.body.addLayout(hl)
        ab = QHBoxLayout()
        ab.setSpacing(8)
        self.b_run = Button("Starten", "primary", self.run_app)
        self.b_restart = Button("Neu starten", "ghost", self.restart_app, "App beenden und neu starten – "
                                "damit geänderte Rechte gelten")
        self.b_update = Button("Aktualisieren", "ghost", self.update_app)
        self.b_data = Button("Datenordner", "ghost", self.open_data)
        self.b_reset = Button("Rechte zurücksetzen", "ghost", self.reset_overrides)
        self.b_remove = Button("Deinstallieren …", "danger", self.uninstall_app)
        for b in (self.b_run, self.b_restart, self.b_update, self.b_data, self.b_reset):
            ab.addWidget(b)
        ab.addStretch(1)
        ab.addWidget(self.b_remove)
        head.body.addLayout(ab)
        self.h_note = Label("", "Warn", wrap=True)
        self.h_note.hide()
        head.body.addWidget(self.h_note)
        right.addWidget(head)

        # Berechtigungs-Karten
        groups = []
        for g, *_ in FLATPAK_PERMS:
            if g not in groups:
                groups.append(g)
        self.cards = {}
        grid = QGridLayout()
        grid.setSpacing(16)
        for i, g in enumerate(groups):
            card = Panel(g)
            self.cards[g] = card
            for (gg, kind, name, title, desc, risk) in FLATPAK_PERMS:
                if gg == g:
                    self._perm_row(card, kind, name, title, desc, risk)
            if g == "Dateien":
                self._custom_paths_ui(card)
            grid.addWidget(card, i, 0)
        grid.setColumnStretch(0, 1)
        right.addLayout(grid)

        env = Panel("Umgebungsvariablen")
        self.env_table = QTableWidget(0, 2)
        self.env_table.setHorizontalHeaderLabels(["VARIABLE", "WERT"])
        self.env_table.verticalHeader().setVisible(False)
        self.env_table.setShowGrid(False)
        self.env_table.setColumnWidth(0, 220)
        self.env_table.horizontalHeader().setStretchLastSection(True)
        self.env_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.env_table.setMaximumHeight(160)
        self.env_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.env_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        env.body.addWidget(self.env_table)
        er = QHBoxLayout()
        er.setSpacing(8)
        self.env_key = LineEdit(placeholder="NAME", mono=True)
        self.env_val = LineEdit(placeholder="Wert", mono=True)
        er.addWidget(self.env_key)
        er.addWidget(self.env_val, 1)
        er.addWidget(Button("Setzen", "ghost", self.env_add))
        er.addWidget(Button("Entfernen", "ghost", self.env_del))
        env.body.addLayout(er)
        right.addWidget(env)

        portal = Panel("Freigaben über Portale", [Button("Zurücksetzen", "ghost", self.reset_portals,
                                                          "Alle gespeicherten Freigaben (Kamera, Ort, Bildschirm …) "
                                                          "dieser App vergessen")])
        self.portal_label = Label("", "Muted", wrap=True)
        portal.body.addWidget(self.portal_label)
        right.addWidget(portal)
        self.portal_panel = portal
        self.env_panel = env
        right.addStretch(1)
        main.addLayout(right, 1)
        self.lay.addLayout(main)

        out = Panel("Ausgabe")
        self.log = LogView(120)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        self._select(None)
        QTimer.singleShot(300, self.refresh)

    # ---- Aufbau ---------------------------------------------------------------

    def _perm_row(self, card, kind, name, title, desc, risk):
        row = QHBoxLayout()
        row.setSpacing(12)
        sw = Switch()
        sw.clicked.connect(lambda on, k=kind, n=name: self._toggle(k, n, on))
        row.addWidget(sw, 0, Qt.AlignTop)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        t = QHBoxLayout()
        t.setSpacing(8)
        t.addWidget(Label(title, "PanelTitle"))
        if risk:
            rb = StatusBadge(risk, "riskant" if risk == "danger" else "Vorsicht")
            t.addWidget(rb)
        ch = Label("geändert", "Changed")
        ch.setToolTip("Weicht von der Voreinstellung der App ab")
        ch.hide()
        t.addWidget(ch)
        t.addStretch(1)
        txt.addLayout(t)
        if desc:
            txt.addWidget(Label(desc, "Hint", wrap=True))
        row.addLayout(txt, 1)
        mode = None
        if kind == "filesystems":
            mode = QComboBox()
            for m, lab in FS_MODES:
                mode.addItem(lab, m)
            mode.setMinimumHeight(34)
            mode.setFixedWidth(210)
            mode.activated.connect(lambda _, n=name: self._fs_mode(n))
            row.addWidget(mode, 0, Qt.AlignTop)
        card.body.addLayout(row)
        self.rows[(kind, name)] = (sw, ch, mode)

    def _custom_paths_ui(self, card):
        card.body.addWidget(Label("WEITERE ORDNER", "FieldLabel"))
        self.custom_box = QVBoxLayout()
        self.custom_box.setSpacing(6)
        card.body.addLayout(self.custom_box)
        cr = QHBoxLayout()
        cr.setSpacing(8)
        self.custom_path = LineEdit(placeholder="z. B. ~/Spiele oder /mnt/daten", mono=True)
        cr.addWidget(self.custom_path, 1)
        cr.addWidget(Button("…", "icon", self._pick_folder, "Ordner auswählen"))
        cr.addWidget(Button("Freigeben", "ghost", self._add_custom))
        card.body.addLayout(cr)

    # ---- Daten ----------------------------------------------------------------

    def refresh(self):
        def worker():
            installed = which("flatpak")
            apps = flatpak_apps() if installed else []
            remotes = ""
            if installed:
                remotes = subprocess.run(["flatpak", "remotes", "--columns=name"], capture_output=True,
                                         text=True).stdout
            ui(lambda: self._loaded(installed, apps, remotes))
        threading.Thread(target=worker, daemon=True).start()

    def _loaded(self, installed, apps, remotes):
        self.apps = sorted(apps, key=lambda a: a["name"].lower())
        has_flathub = "flathub" in remotes.split()
        self.b_install_fp.setVisible(not installed)
        self.b_flathub.setVisible(installed and not has_flathub)
        self.b_unused.setVisible(installed)
        if not installed:
            self.badge.set("off", "Nicht installiert")
            self.setup_text.setText("Flatpak ist nicht installiert. Flatpak-Apps laufen abgeschottet vom System – "
                                    "hier legst du fest, was jede App darf.")
        elif not has_flathub:
            self.badge.set("warn", "Flathub fehlt")
            self.setup_text.setText("Flatpak ist installiert, aber <b>Flathub</b> (die große App-Quelle) ist noch "
                                    "nicht eingerichtet.")
        else:
            self.badge.set("ok", f"{len(self.apps)} Apps · {fmt_bytes(sum(a['size'] for a in self.apps))}")
            self.setup_text.setText("Rechte ändern gilt für dich (Benutzer-Einstellung, kein Passwort nötig) und "
                                    "wirkt beim <b>nächsten Start</b> der App.")
        self._fill_list()

    def _override_path(self, appid):
        return os.path.join(FLATPAK_OVERRIDE_DIR, "global" if appid == self.ALL else appid)

    def _n_changes(self, appid):
        d = read_keyfile(_read(self._override_path(appid)))
        return sum(len(_tokens(v)) for v in d.get("Context", {}).values()) + len(d.get("Environment", {}))

    def _fill_list(self):
        q = self.search.text().strip().lower()
        cur = self.cur
        self.list.blockSignals(True)
        self.list.clear()
        from PySide6.QtWidgets import QListWidgetItem
        n = self._n_changes(self.ALL)
        it = QListWidgetItem(svg_icon(GEAR_SVG.replace("{c}", COLORS["accent"]), 32),
                             "Alle Apps" + (f"  ·  {n} Regel{'n' if n != 1 else ''}" if n else ""))
        it.setData(Qt.UserRole, self.ALL)
        it.setToolTip("Regeln, die für alle Flatpak-Apps gelten")
        self.list.addItem(it)
        select = 0
        for a in self.apps:
            if q and q not in f"{a['name']} {a['id']}".lower():
                continue
            n = self._n_changes(a["id"])
            ic = themed_icon(a["id"])
            li = QListWidgetItem(ic if not ic.isNull() else letter_icon(a["name"]),
                                 a["name"] + (f"  ✎{n}" if n else ""))
            li.setData(Qt.UserRole, a["id"])
            li.setToolTip(a["id"])
            self.list.addItem(li)
            if a["id"] == cur:
                select = self.list.count() - 1
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(select)

    def _app(self, appid):
        return next((a for a in self.apps if a["id"] == appid), None)

    def _select(self, appid):
        self.cur = appid
        enabled = appid is not None
        for (sw, ch, mode) in self.rows.values():
            sw.setEnabled(enabled)
            if mode:
                mode.setEnabled(enabled)
        is_app = enabled and appid != self.ALL
        for b in (self.b_run, self.b_restart, self.b_update, self.b_data, self.b_remove):
            b.setVisible(is_app)
        self.b_reset.setVisible(enabled)
        self.portal_panel.setVisible(is_app)
        if not enabled:
            self.h_name.setText("Keine App ausgewählt")
            self.h_id.setText("")
            self.h_meta.setText("")
            self.h_icon.clear()
            return
        if appid == self.ALL:
            self.h_name.setText("Alle Apps")
            self.h_id.setText("Globale Regeln")
            self.h_meta.setText("Was du hier einstellst, gilt für jede Flatpak-App – einzelne Apps können es "
                                "wieder überschreiben.")
            self.h_icon.setPixmap(svg_icon(GEAR_SVG.replace("{c}", COLORS["accent"]), 64).pixmap(64, 64))
            self.meta = {}
            self._apply_state()
            return
        a = self._app(appid)
        ic = themed_icon(appid)
        self.h_icon.setPixmap((ic if not ic.isNull() else letter_icon(a["name"] if a else appid)).pixmap(64, 64))
        self.h_name.setText(a["name"] if a else appid)
        self.h_id.setText(appid)
        if a:
            self.h_meta.setText(f"Version {a['version'] or '—'} · Zweig {a['branch']} · Quelle {a['origin']} · "
                                f"{'nur für dich' if a['installation'] == 'user' else 'systemweit'} installiert · "
                                f"{fmt_bytes(a['size'])}"
                                + (f" · seit {datetime.fromtimestamp(a['date']).strftime('%d.%m.%Y')}" if a["date"] else "")
                                + (f"\n{a['desc']}" if a["desc"] else ""))

        def worker():
            meta = subprocess.run(["flatpak", "info", "--show-metadata", appid], capture_output=True,
                                  text=True).stdout
            perms = subprocess.run(["flatpak", "permission-show", appid], capture_output=True, text=True).stdout
            ui(lambda: self._meta_loaded(appid, meta, perms))
        threading.Thread(target=worker, daemon=True).start()

    def _meta_loaded(self, appid, meta, perms):
        if appid != self.cur:
            return
        self.meta = read_keyfile(meta).get("Context", {})
        lines = [l for l in perms.splitlines()[1:] if l.strip()]
        if lines:
            txt = []
            for l in lines:
                c = l.split("\t") if "\t" in l else l.split()
                if len(c) >= 4:
                    txt.append(f"{c[0]} / {c[1]}: {c[3]}")
            self.portal_label.setText("\n".join(txt) or "Keine gespeicherten Freigaben.")
        else:
            self.portal_label.setText("Keine gespeicherten Freigaben (Kamera, Standort, Bildschirmaufnahme …).")
        self._apply_state()

    # ---- Berechtigungen: Zustand berechnen ------------------------------------

    def _layers(self, include_app=True):
        """Standard der App → System-Overrides → globale Benutzer-Regeln → App-Regeln.
        include_app=False liefert den Zustand ohne die gerade bearbeitete Ebene."""
        is_app = self.cur not in (None, self.ALL)
        layers = [self.meta]
        paths = [os.path.join(FLATPAK_SYS_OVERRIDE_DIR, "global")]
        if is_app:
            paths.append(os.path.join(FLATPAK_SYS_OVERRIDE_DIR, self.cur))
        if is_app or include_app:
            paths.append(self._override_path(self.ALL))
        if is_app and include_app:
            paths.append(self._override_path(self.cur))
        for p in paths:
            layers.append(read_keyfile(_read(p)).get("Context", {}))
        return layers

    def _state(self, kind, name, include_app=True):
        on, mode = False, "rw"
        for layer in self._layers(include_app):
            for tok in _tokens(layer.get(kind)):
                if kind == "filesystems":
                    n, m, neg = _fs_parse(tok)
                    if n == name:
                        on, mode = (not neg), (m if not neg else mode)
                else:
                    if tok == name:
                        on = True
                    elif tok == "!" + name:
                        on = False
        return on, mode

    def _apply_state(self):
        self._building = True
        ov = read_keyfile(_read(self._override_path(self.cur))).get("Context", {}) if self.cur else {}
        for (kind, name), (sw, ch, mode) in self.rows.items():
            on, m = self._state(kind, name)
            sw.setChecked(on)
            sw.update()
            touched = any((_fs_parse(t)[0] == name) if kind == "filesystems" else t.lstrip("!") == name
                          for t in _tokens(ov.get(kind)))
            ch.setVisible(touched)
            if mode:
                mode.setCurrentIndex(max(0, [x for x, _ in FS_MODES].index(m) if m in dict(FS_MODES) else 0))
                mode.setVisible(on)
        self._render_custom(ov)
        self._render_env()
        self._building = False
        n = self._n_changes(self.cur) if self.cur else 0
        self.b_reset.setEnabled(n > 0)

    # ---- Berechtigungen: ändern -----------------------------------------------

    def _edit(self, fn):
        path = self._override_path(self.cur)
        data = read_keyfile(_read(path))
        fn(data)
        text = write_keyfile(data)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if text.strip():
                with open(path, "w") as f:
                    f.write(text)
            elif os.path.exists(path):
                os.remove(path)
        except Exception as e:
            show_error(self, "Fehler", f"Einstellung konnte nicht gespeichert werden: {e}")
        self._apply_state()
        self._fill_list_keep()
        name = "allen Apps" if self.cur == self.ALL else (self._app(self.cur) or {}).get("name", self.cur)
        self.app.set_status(f"Gespeichert – gilt beim nächsten Start von {name}.")
        if self.cur != self.ALL:
            self.h_note.setText("▲ Änderungen wirken beim nächsten Start der App – „Neu starten“ übernimmt sie sofort.")
            self.h_note.show()

    def _fill_list_keep(self):
        row = self.list.currentRow()
        for i in range(self.list.count()):
            it = self.list.item(i)
            aid = it.data(Qt.UserRole)
            n = self._n_changes(aid)
            base = "Alle Apps" if aid == self.ALL else (self._app(aid) or {}).get("name", aid)
            it.setText(base + (f"  ✎{n}" if n else ""))
            it.setToolTip(f"{aid}\n{n} eigene Einstellung(en)" if n else aid)
        self.list.setCurrentRow(row)

    def _toggle(self, kind, name, on):
        if self._building or not self.cur:
            return
        risky = next((r for (_, k, n, _, _, r) in FLATPAK_PERMS if k == kind and n == name), "")
        if on and risky == "danger":
            title = next(t for (_, k, n, t, _, _) in FLATPAK_PERMS if k == kind and n == name)
            if not ask_confirm(self, "Riskante Berechtigung", f"„{title}“ erlauben?\n\nDas schwächt die "
                               "Abschottung der App deutlich. Nur für vertrauenswürdige Apps.", "Erlauben"):
                self._apply_state()
                return
        mode_combo = self.rows[(kind, name)][2]
        mode = mode_combo.currentData() if mode_combo else "rw"
        self._set_perm(kind, name, on, mode)

    def _set_perm(self, kind, name, on, mode="rw"):
        base_on, base_mode = self._state(kind, name, include_app=False)

        def fn(data):
            ctx = data.setdefault("Context", {})
            toks = [t for t in _tokens(ctx.get(kind))
                    if ((_fs_parse(t)[0] != name) if kind == "filesystems" else t.lstrip("!") != name)]
            if on != base_on or (on and kind == "filesystems" and mode != base_mode):
                if kind == "filesystems":
                    toks.append(name + ("" if mode == "rw" else f":{mode}") if on else "!" + name)
                else:
                    toks.append(name if on else "!" + name)
            ctx[kind] = ";".join(toks) + (";" if toks else "")
        self._edit(fn)

    def _fs_mode(self, name):
        sw, ch, mode = self.rows[("filesystems", name)]
        if sw.isChecked():
            self._set_perm("filesystems", name, True, mode.currentData())

    # --- eigene Ordner ---
    def _render_custom(self, ov):
        TaskTab._clear(self.custom_box)
        known = {n for (_, k, n, *_r) in FLATPAK_PERMS if k == "filesystems"}
        entries = {}
        for layer_name, layer in (("app", self.meta), ("ov", ov)):
            for tok in _tokens(layer.get("filesystems")):
                n, m, neg = _fs_parse(tok)
                if n in known:
                    continue
                entries[n] = (not neg, m, layer_name)
        if not entries:
            self.custom_box.addWidget(Label("Keine weiteren Ordner freigegeben.", "Hint"))
        for n, (on, m, src) in sorted(entries.items()):
            r = QHBoxLayout()
            r.setSpacing(8)
            r.addWidget(StatusBadge("ok" if on else "off", "frei" if on else "gesperrt"))
            r.addWidget(Label(n, "Value"), 1)
            r.addWidget(Label(dict(FS_MODES).get(m, m) if on else "", "Hint"))
            if on:
                r.addWidget(Button("Sperren", "ghost", lambda _=False, p=n: self._set_perm("filesystems", p, False)))
            else:
                r.addWidget(Button("Freigeben", "ghost", lambda _=False, p=n: self._set_perm("filesystems", p, True)))
            if src == "ov":
                r.addWidget(Button("↺", "icon", lambda _=False, p=n: self._forget_custom(p), "Eigene Regel entfernen"))
            self.custom_box.addLayout(r)

    def _forget_custom(self, name):
        def fn(data):
            ctx = data.setdefault("Context", {})
            toks = [t for t in _tokens(ctx.get("filesystems")) if _fs_parse(t)[0] != name]
            ctx["filesystems"] = ";".join(toks) + (";" if toks else "")
        self._edit(fn)

    def _pick_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Ordner freigeben", os.path.expanduser("~"))
        if d:
            home = os.path.expanduser("~")
            self.custom_path.setText("~" + d[len(home):] if d.startswith(home) else d)

    def _add_custom(self):
        p = self.custom_path.text().strip()
        if not p or ";" in p or not (p.startswith(("/", "~", "xdg-"))):
            show_warning(self, "Ordner", "Bitte einen Pfad wie ~/Spiele oder /mnt/daten angeben.")
            return
        self.custom_path.clear()
        self._set_perm("filesystems", p.rstrip("/"), True, "rw")

    # --- Umgebungsvariablen ---
    def _render_env(self):
        ov = read_keyfile(_read(self._override_path(self.cur))).get("Environment", {}) if self.cur else {}
        self.env_table.setRowCount(len(ov))
        for r, (k, v) in enumerate(sorted(ov.items())):
            a, b = QTableWidgetItem(k), QTableWidgetItem(v)
            a.setFont(QFont(FONTS["mono"], 10))
            b.setFont(QFont(FONTS["mono"], 10))
            self.env_table.setItem(r, 0, a)
            self.env_table.setItem(r, 1, b)

    def env_add(self):
        k, v = self.env_key.text().strip(), self.env_val.text()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) or "\n" in v:
            show_warning(self, "Variable", "Name nur aus Buchstaben, Ziffern und _ (z. B. GDK_SCALE).")
            return
        self.env_key.clear()
        self.env_val.clear()
        self._edit(lambda d: d.setdefault("Environment", {}).__setitem__(k, v))

    def env_del(self):
        rows = sorted({i.row() for i in self.env_table.selectedIndexes()})
        keys = [self.env_table.item(r, 0).text() for r in rows]
        if keys:
            self._edit(lambda d: [d.setdefault("Environment", {}).pop(k, None) for k in keys])

    # ---- Aktionen ---------------------------------------------------------------

    def reset_overrides(self):
        name = "alle Apps" if self.cur == self.ALL else (self._app(self.cur) or {}).get("name", self.cur)
        if not ask_confirm(self, "Zurücksetzen", f"Alle eigenen Rechte-Einstellungen für {name} entfernen?\n"
                           "Danach gelten wieder die Voreinstellungen der App.", "Zurücksetzen"):
            return
        try:
            os.remove(self._override_path(self.cur))
        except FileNotFoundError:
            pass
        self._apply_state()
        self._fill_list_keep()

    def reset_portals(self):
        if self.cur and ask_confirm(self, "Portal-Freigaben", "Gespeicherte Freigaben dieser App vergessen? "
                                    "Sie fragt beim nächsten Mal erneut.", "Zurücksetzen"):
            run_streaming(["flatpak", "permission-reset", self.cur], self.log,
                          on_done=lambda rc: self._select(self.cur))

    def run_app(self):
        subprocess.Popen(["flatpak", "run", self.cur], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        self.app.set_status(f"{self.cur} wird gestartet …")

    def restart_app(self):
        subprocess.run(["flatpak", "kill", self.cur], capture_output=True)
        QTimer.singleShot(800, self.run_app)
        self.h_note.hide()

    def open_data(self):
        p = os.path.expanduser(f"~/.var/app/{self.cur}")
        if os.path.isdir(p) and which("xdg-open"):
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            show_info(self, "Datenordner", f"Die App hat noch keinen Datenordner ({short_path(p)}).")

    def _scope(self):
        a = self._app(self.cur)
        return "user" if a and a["installation"] == "user" else "system"

    def update_app(self):
        user = self._scope() == "user"
        if not user and not self.app.priv.ensure(self):
            return
        self.log.set_text(f"$ flatpak update {self.cur}\n")
        run_streaming(["flatpak", "update", "--user" if user else "--system", "-y", self.cur], self.log,
                      needs_sudo=not user, clear_first=False, interactive=True,
                      on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"), self.refresh()))

    def uninstall_app(self):
        a = self._app(self.cur)
        if not a:
            return
        box = QMessageBox(self)
        set_msg_icon(box, QMessageBox.Question)
        box.setWindowTitle("Deinstallieren")
        box.setText(f"{a['name']} deinstallieren?")
        box.setInformativeText("Persönliche Daten der App (~/.var/app) können mit gelöscht werden.")
        keep = box.addButton("Deinstallieren, Daten behalten", QMessageBox.AcceptRole)
        wipe = box.addButton("Mit Daten löschen", QMessageBox.DestructiveRole)
        wipe.setProperty("variant", "danger")
        keep.setProperty("variant", "primary")
        cancel = box.addButton("Abbrechen", QMessageBox.RejectRole)
        cancel.setProperty("variant", "ghost")
        box.setDefaultButton(keep)
        style_msg_box(box)
        box.exec()
        if box.clickedButton() not in (keep, wipe):
            return
        user = a["installation"] == "user"
        if not user and not self.app.priv.ensure(self):
            return
        cmd = ["flatpak", "uninstall", "--user" if user else "--system", "-y"] + \
            (["--delete-data"] if box.clickedButton() is wipe else []) + [a["id"]]
        self.log.set_text("$ " + " ".join(cmd) + "\n")
        run_streaming(cmd, self.log, needs_sudo=not user, clear_first=False,
                      on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"), self.refresh()))

    def install_flatpak(self):
        if not self.app.priv.ensure(self):
            return
        steps = [{"cmd": ["pacman", "-S", "flatpak"], "needs_sudo": True, "interactive": True,
                  "label": "sudo pacman -S flatpak"}]
        run_sequence(steps, self.log, on_all_done=self.refresh)

    def add_flathub(self):
        if not self.app.priv.ensure(self):
            return
        self.log.set_text("$ sudo flatpak remote-add --if-not-exists flathub …\n")
        run_streaming(["flatpak", "remote-add", "--if-not-exists", "flathub",
                       "https://dl.flathub.org/repo/flathub.flatpakrepo"], self.log, needs_sudo=True,
                      clear_first=False, on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"),
                                                             self.refresh()))

    def remove_unused(self):
        if not self.app.priv.ensure(self):
            return
        self.log.set_text("$ sudo flatpak uninstall --unused -y\n")
        run_streaming(["flatpak", "uninstall", "--unused", "-y"], self.log, needs_sudo=True, clear_first=False,
                      on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"), self.refresh()))


# --------------------------------------------------------------------------
# Modul: Fallback-Speicher (Swap)
# --------------------------------------------------------------------------

class SwapTab(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app

        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Fallback-Speicher", self.badge))

        grid = QGridLayout()
        grid.setSpacing(16)

        status = Panel("Aktueller Status", [Button("↻", "icon", self.refresh_status, "Status aktualisieren")])
        self.status_box = QVBoxLayout()
        self.status_box.setSpacing(12)
        status.body.addLayout(self.status_box)
        self.swappiness_info = Label("", "Small")
        status.body.addWidget(self.swappiness_info)
        status.body.addStretch(1)
        grid.addWidget(status, 0, 0)

        sf = Panel("Swapfile anlegen / ersetzen")
        r = QHBoxLayout()
        r.setSpacing(8)
        self.path_edit = LineEdit("/swapfile", mono=True)
        self.size_edit = LineEdit("4", mono=True)
        self.size_edit.setFixedWidth(90)
        r.addLayout(Field("Pfad", self.path_edit), 1)
        r.addLayout(Field("Größe (GB)", self.size_edit))
        sf.body.addLayout(r)
        b = QHBoxLayout()
        b.addWidget(Button("Swapfile erstellen && aktivieren", "primary", self.create_swapfile))
        b.addStretch(1)
        sf.body.addLayout(b)
        sf.body.addStretch(1)
        grid.addWidget(sf, 0, 1)

        sw = Panel("Swappiness")
        r = QHBoxLayout()
        r.setSpacing(12)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(60)
        self.swap_value = Label("60", "Value")
        self.swap_value.setFixedWidth(32)
        self.swap_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider.valueChanged.connect(lambda v: self.swap_value.setText(str(v)))
        r.addWidget(self.slider, 1)
        r.addWidget(self.swap_value)
        sw.body.addLayout(r)
        sw.body.addWidget(Label("Niedrig = RAM bevorzugen, hoch = früher auslagern.", "Small"))
        b = QHBoxLayout()
        b.addWidget(Button("Swappiness setzen (dauerhaft)", "primary", self.set_swappiness))
        b.addStretch(1)
        sw.body.addLayout(b)
        grid.addWidget(sw, 1, 0)

        off = Panel("Swap deaktivieren")
        self.cb_remove = QCheckBox("Datei zusätzlich löschen und Eintrag aus /etc/fstab entfernen")
        off.body.addWidget(self.cb_remove)
        b = QHBoxLayout()
        b.addWidget(Button("Swap deaktivieren", "danger", self.disable_swap))
        b.addStretch(1)
        off.body.addLayout(b)
        off.body.addStretch(1)
        grid.addWidget(off, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.lay.addLayout(grid)

        out = Panel("Ausgabe")
        self.log = LogView(160)
        out.body.addWidget(self.log)
        self.lay.addWidget(out, 1)

        self.refresh_status()

    def refresh_status(self):
        def worker():
            devices = []
            try:
                with open("/proc/swaps") as f:
                    for line in f.read().splitlines()[1:]:
                        p = line.split()
                        if len(p) >= 4:
                            devices.append((p[0], int(p[3]) / 1048576, int(p[2]) / 1048576))
            except Exception:
                pass
            try:
                with open("/proc/sys/vm/swappiness") as f:
                    swappiness = f.read().strip()
            except Exception:
                swappiness = "unbekannt"
            ui(lambda: self._show_status(devices, swappiness))
        threading.Thread(target=worker, daemon=True).start()

    def _show_status(self, devices, swappiness):
        while self.status_box.count():
            w = self.status_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if devices:
            for name, used, total in devices:
                self.status_box.addWidget(UsageBar(name, used, total))
            total = sum(d[2] for d in devices)
            self.badge.set("ok", f"Aktiv · {total:.1f} GiB")
        else:
            self.status_box.addWidget(Label("Kein aktiver Swap gefunden.", "Muted"))
            self.badge.set("off", "Kein Swap aktiv")
        self.swappiness_info.setText(f"Aktuelle Swappiness: {swappiness}")
        if swappiness.isdigit():
            self.slider.setValue(int(swappiness))

    def create_swapfile(self):
        path = self.path_edit.text().strip()
        size = self.size_edit.text().strip()
        if not PATH_RE.match(path) or not re.match(r"^\d+$", size) or int(size) <= 0:
            show_warning(self, "Ungültige Eingabe",
                         "Bitte einen einfachen absoluten Pfad (keine Leerzeichen/Sonderzeichen) "
                         "und eine positive Größe in GB angeben.")
            return
        if not ask_confirm(self, "Bestätigen",
                           f"Swapfile {path} mit {size} GB anlegen bzw. ersetzen und in /etc/fstab eintragen?",
                           "Anlegen"):
            return
        if not self.app.priv.ensure(self):
            return
        err = swap_path_problem(path)
        if err:
            show_warning(self, "Swapfile", err)
            return

        p = shlex.quote(path)
        fs = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", os.path.dirname(path)],
                            capture_output=True, text=True).stdout.strip()
        if fs == "btrfs":
            # btrfs braucht eine Datei ohne Copy-on-Write – das erledigt btrfs selbst
            make = f"rm -f -- {p} && btrfs filesystem mkswapfile --size {size}g {p}"
        else:
            make = (f"rm -f -- {p} && (fallocate -l {size}G {p} || dd if=/dev/zero of={p} bs=1M "
                    f"count=$(({size}*1024)) status=progress) && chmod 600 {p} && mkswap {p}")
        script = (
            f"swapoff {p} 2>/dev/null; "
            f"{make} && "
            f"swapon {p} && "
            f"(awk -v p={p} '$1==p {{f=1}} END {{exit !f}}' /etc/fstab || "
            f"echo {shlex.quote(path + ' none swap defaults 0 0')} >> /etc/fstab)"
        )
        self._run_root(script)

    def set_swappiness(self):
        val = int(self.slider.value())
        if not self.app.priv.ensure(self):
            return
        script = f"sysctl vm.swappiness={val} && echo 'vm.swappiness={val}' > /etc/sysctl.d/99-swappiness.conf"
        self._run_root(script)

    def disable_swap(self):
        path = self.path_edit.text().strip()
        if not PATH_RE.match(path):
            show_warning(self, "Ungültige Eingabe", "Bitte einen gültigen Pfad angeben.")
            return
        if not ask_confirm(self, "Bestätigen", f"Swap {path} deaktivieren?", "Deaktivieren", danger=True):
            return
        if not self.app.priv.ensure(self):
            return

        p = shlex.quote(path)
        script = f"swapoff {p}"
        if self.cb_remove.isChecked():
            err = swap_path_problem(path, must_exist=True)
            if err:
                show_warning(self, "Swapfile", err + "\n\nEs wird nur deaktiviert, nichts gelöscht.")
            else:
                # nur die fstab-Zeile, deren erstes Feld genau dieser Pfad ist; Sicherung als fstab.tuxdex.bak
                script += (f" && rm -f -- {p} && cp -a /etc/fstab /etc/fstab.tuxdex.bak && "
                           f"awk -v p={p} '$1!=p' /etc/fstab > /etc/fstab.tuxdex.tmp && "
                           "cat /etc/fstab.tuxdex.tmp > /etc/fstab && rm -f /etc/fstab.tuxdex.tmp")
        self._run_root(script)

    def _run_root(self, script):
        self.log.set_text(f"$ (root) {script}\n\n")

        def done(rc):
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            self.refresh_status()

        run_streaming(["bash", "-c", script], self.log, needs_sudo=True, clear_first=False, on_done=done)


# --------------------------------------------------------------------------
# Modul: Wiederherstellung (System-Snapshots mit snapper oder Timeshift)
# --------------------------------------------------------------------------

SNAP_DESC = "Tuxdex"


def _findmnt(target, col):
    return _cmd_out(["findmnt", "-n", "-o", col, "--target", target]).strip()


def _pkg_installed(name):
    try:
        return subprocess.run(["pacman", "-Q", name], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def snapshot_env():
    """Was ist da? Dateisystem, Werkzeug, Einrichtung, automatische Snapshots bei pacman."""
    fs = _findmnt("/", "FSTYPE")
    home_sep = _findmnt("/home", "TARGET") == "/home"
    e = {"fs": fs, "home_separate": home_sep, "tool": None, "configured": False,
         "snap_pac": False, "autosnap": False, "grub_btrfs": False}
    if which("snapper"):
        e["tool"] = "snapper"
        e["configured"] = os.path.exists("/etc/snapper/configs/root")
        e["snap_pac"] = _pkg_installed("snap-pac")
        e["grub_btrfs"] = _pkg_installed("grub-btrfs")
    elif which("timeshift"):
        e["tool"] = "timeshift"
        e["configured"] = os.path.exists("/etc/timeshift/timeshift.json")
        e["autosnap"] = _pkg_installed("timeshift-autosnap")
    return e


def parse_snapper_json(text):
    try:
        data = json.loads(text)
    except Exception:
        return []
    out = []
    for s in data.get("root", []):
        if not s.get("number"):
            continue                    # 0 = aktueller Zustand
        out.append({"id": str(s["number"]), "date": (s.get("date") or "")[:16],
                    "desc": s.get("description") or "", "kind": s.get("type") or "",
                    "pre": s.get("pre-number")})
    return out


def parse_timeshift_list(text):
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*\d+\s+>\s+(\S+)\s+([ODWMBH]*)\s*(.*)$", line)
        if m:
            name = m.group(1)
            date = re.sub(r"^(\d{4}-\d\d-\d\d)_(\d\d)-(\d\d).*", r"\1 \2:\3", name)
            out.append({"id": name, "date": date, "desc": m.group(3).strip(), "kind": m.group(2) or "",
                        "pre": None})
    return out


def snapshot_create_cmd(tool, desc):
    if tool == "snapper":
        return ["snapper", "-c", "root", "create", "-d", desc, "--cleanup-algorithm", "number"]
    return ["timeshift", "--create", "--comments", desc, "--scripted"]


def snapshot_before_update_step():
    """Schritt für das Update: Snapshot vorher – nur wenn eingerichtet und nicht schon snap-pac/autosnap das tun."""
    if not load_settings().get("snap_before_update", True):
        return None
    e = snapshot_env()
    if not e["tool"] or not e["configured"] or e["snap_pac"] or e["autosnap"]:
        return None
    return {"cmd": snapshot_create_cmd(e["tool"], f"{SNAP_DESC}: vor dem Update"), "needs_sudo": True,
            "label": "Snapshot vor dem Update"}


class RestoreTab(Page):
    COLS = [("Nr.", 70), ("Datum", 180), ("Beschreibung", 420), ("Art", 90)]

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.env = {}
        self.snaps = []
        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Systemwiederherstellung", self.badge,
                                       Button("↻", "icon", self.refresh, "Aktualisieren")))

        intro = Panel("So funktioniert es")
        intro.body.addWidget(Label(
            "Ein Snapshot hält den Stand deines Systems fest – in Sekunden und fast ohne Speicherplatz. "
            "Geht nach einem Update etwas kaputt, setzt du das System mit einem Klick auf den Stand davor zurück. "
            "Deine eigenen Dateien in /home bleiben dabei unberührt.", "Hint", wrap=True))
        self.lay.addWidget(intro)

        top = QHBoxLayout()
        top.setSpacing(16)
        st = Panel("Status")
        self.rows = {}
        for key, label in (("fs", "Dateisystem"), ("tool", "Werkzeug"), ("auto", "Automatisch vor Updates"),
                           ("count", "Snapshots")):
            r = QHBoxLayout()
            r.addWidget(Label(label.upper(), "FieldLabel"))
            r.addStretch(1)
            v = Label("…", "Value")
            r.addWidget(v)
            self.rows[key] = v
            st.body.addLayout(r)
        self.setup_hint = Label("", "Hint", wrap=True)
        st.body.addWidget(self.setup_hint)
        b = QHBoxLayout()
        self.b_setup = Button("Einrichten", "primary", self.setup)
        self.b_create = Button("Snapshot jetzt erstellen", "primary", self.create)
        b.addWidget(self.b_setup)
        b.addWidget(self.b_create)
        b.addStretch(1)
        st.body.addLayout(b)
        top.addWidget(st, 1)

        opt = Panel("Vor Updates")
        self.cb_auto = QCheckBox("Vor jedem Update in Tuxdex automatisch einen Snapshot anlegen")
        self.cb_auto.setChecked(bool(load_settings().get("snap_before_update", True)))
        self.cb_auto.toggled.connect(self._auto_changed)
        opt.body.addWidget(self.cb_auto)
        self.auto_hint = Label("", "Hint", wrap=True)
        opt.body.addWidget(self.auto_hint)
        opt.body.addStretch(1)
        top.addWidget(opt, 1)
        self.lay.addLayout(top)

        lp = Panel("Snapshots")
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([c[0].upper() for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, (_, w) in enumerate(self.COLS):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        self.table.setMinimumHeight(260)
        lp.body.addWidget(self.table)
        self.list_hint = Label("", "Hint", wrap=True)
        lp.body.addWidget(self.list_hint)
        b = QHBoxLayout()
        self.b_restore = Button("Auf diesen Stand zurücksetzen", "danger", self.restore)
        self.b_delete = Button("Löschen", "ghost", self.delete)
        self.b_login = Button("Anmelden und Snapshots anzeigen", "primary", self._login)
        b.addWidget(self.b_restore)
        b.addWidget(self.b_delete)
        b.addWidget(self.b_login)
        b.addStretch(1)
        lp.body.addLayout(b)
        self.lay.addWidget(lp)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)
        self.refresh()

    # --- Zustand ---------------------------------------------------------
    def refresh(self):
        def worker():
            e = snapshot_env()
            ui(lambda: self._show_env(e))
        threading.Thread(target=worker, daemon=True).start()

    def _show_env(self, e):
        self.env = e
        tool = e["tool"]
        self.rows["fs"].setText(e["fs"] or "—")
        if not tool:
            self.rows["tool"].setText("Nicht installiert")
        else:
            self.rows["tool"].setText(("Snapper" if tool == "snapper" else "Timeshift")
                                      + ("" if e["configured"] else " (nicht eingerichtet)"))
        ready = bool(tool and e["configured"])
        if e["snap_pac"]:
            auto = "Ja – snap-pac (bei jeder Paketänderung)"
        elif e["autosnap"]:
            auto = "Ja – timeshift-autosnap"
        elif ready and self.cb_auto.isChecked():
            auto = "Ja – bei Updates über Tuxdex"
        else:
            auto = "Nein"
        self.rows["auto"].setText(auto)
        self.b_setup.setVisible(not ready)
        self.b_create.setVisible(ready)
        self.cb_auto.setEnabled(ready and not (e["snap_pac"] or e["autosnap"]))
        if e["snap_pac"] or e["autosnap"]:
            self.auto_hint.setText("Das übernimmt bereits " + ("snap-pac" if e["snap_pac"] else "timeshift-autosnap")
                                   + " – auch bei Updates im Terminal. Tuxdex legt deshalb keinen zusätzlichen an.")
        else:
            self.auto_hint.setText("Tuxdex legt den Snapshot direkt vor „Update starten“ an. Updates im Terminal "
                                   "sind damit nicht abgedeckt – dafür beim Einrichten snap-pac mitinstallieren.")
        if ready:
            self.setup_hint.setText("")
            self.badge.set("ok", "Eingerichtet")
        elif e["fs"] == "btrfs":
            self.setup_hint.setText("Dein System liegt auf btrfs – ideal. „Einrichten“ installiert snapper und "
                                    "snap-pac: dann entsteht vor und nach jeder Paketänderung automatisch ein Snapshot.")
            self.badge.set("warn", "Nicht eingerichtet")
        elif tool == "timeshift":
            self.setup_hint.setText("Timeshift ist installiert, aber noch nicht eingerichtet. „Einrichten“ öffnet "
                                    "Timeshift – dort einmal den Speicherort wählen.")
            self.badge.set("warn", "Nicht eingerichtet")
        else:
            self.setup_hint.setText(f"Dein System liegt auf {e['fs'] or 'einem Dateisystem'} ohne eigene Snapshots. "
                                    "„Einrichten“ installiert Timeshift; die Snapshots landen dann als Kopie auf "
                                    "der Festplatte (braucht mehr Platz als bei btrfs).")
            self.badge.set("warn", "Nicht eingerichtet")
        self.load_snapshots()

    def load_snapshots(self):
        e = self.env
        self.table.setRowCount(0)
        self.snaps = []
        ready = bool(e.get("tool") and e.get("configured"))
        self.b_restore.setEnabled(False)
        self.b_delete.setEnabled(False)
        self.b_login.hide()
        if not ready:
            self.rows["count"].setText("—")
            self.list_hint.setText("Noch keine Snapshots – erst einrichten.")
            return
        cmd = (["snapper", "--jsonout", "-c", "root", "list", "--disable-used-space"] if e["tool"] == "snapper"
               else ["timeshift", "--list", "--scripted"])
        self.list_hint.setText("Lade …")

        def cb(rc, out, err):
            if rc != 0:
                self.rows["count"].setText("?")
                self.list_hint.setText("Zum Anzeigen der Snapshots sind root-Rechte nötig.")
                self.b_login.show()
                return
            self._fill(parse_snapper_json(out) if e["tool"] == "snapper" else parse_timeshift_list(out))
        run_capture_async(cmd, cb, needs_sudo=True, timeout=60)

    def _fill(self, snaps):
        self.snaps = list(reversed(snaps))          # neueste oben
        self.rows["count"].setText(str(len(snaps)))
        kinds = {"pre": "vorher", "post": "nachher", "single": "einzeln"}
        for s in self.snaps:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, v in enumerate((s["id"], s["date"], s["desc"], kinds.get(s["kind"], s["kind"]))):
                self.table.setItem(r, c, QTableWidgetItem(v))
        if self.snaps:
            self.table.selectRow(0)
        on = bool(self.snaps)
        self.b_restore.setEnabled(on)
        self.b_delete.setEnabled(on)
        self.list_hint.setText("" if on else "Noch keine Snapshots vorhanden.")
        if self.env.get("tool") == "snapper" and not self.env.get("home_separate"):
            self.b_restore.setEnabled(False)
            self.list_hint.setText("Zurücksetzen ist gesperrt: /home liegt nicht in einem eigenen Subvolume, "
                                   "deine eigenen Dateien würden mit zurückgesetzt.")

    def _selected(self):
        r = self.table.currentRow()
        return self.snaps[r] if 0 <= r < len(self.snaps) else None

    def _login(self):
        if self.app.priv.ensure(self):
            self.load_snapshots()

    def _auto_changed(self, on):
        st = load_settings()
        st["snap_before_update"] = bool(on)
        save_settings(st)
        if self.env:
            self._show_env(self.env)

    # --- Aktionen --------------------------------------------------------
    def _run(self, steps, done=None):
        def all_done():
            self.refresh()
            if done:
                done()
        run_sequence(steps, self.log, on_all_done=all_done)

    def setup(self):
        e = self.env
        if e.get("tool") == "timeshift" and not e.get("configured"):
            self._open_timeshift()
            return
        if e.get("fs") == "btrfs":
            text = ("snapper und snap-pac installieren und für das System einrichten?\n\n"
                    "Danach entsteht vor und nach jeder Paketänderung automatisch ein Snapshot. "
                    "Alte Snapshots räumt snapper selbst auf.")
            # @snapshots-Layout (archinstall u. a.): vorhandenes /.snapshots-Subvolume weiterverwenden
            conf = ("if [ ! -e /etc/snapper/configs/root ]; then "
                    "if mountpoint -q /.snapshots; then umount /.snapshots && rmdir /.snapshots && "
                    "snapper -c root create-config / && btrfs subvolume delete /.snapshots && "
                    "mkdir /.snapshots && mount -a && chmod 750 /.snapshots; "
                    "else snapper -c root create-config /; fi; fi")
            steps = [{"cmd": ["pacman", "-S", "--needed", "snapper", "snap-pac"], "needs_sudo": True,
                      "interactive": True, "label": "sudo pacman -S snapper snap-pac"},
                     {"cmd": ["bash", "-c", conf], "needs_sudo": True, "label": "snapper -c root create-config /"},
                     {"cmd": snapshot_create_cmd("snapper", f"{SNAP_DESC}: erster Snapshot"), "needs_sudo": True,
                      "label": "Erster Snapshot"}]
        else:
            text = ("Timeshift installieren?\n\nDanach öffnet sich Timeshift einmal, um den Speicherort "
                    "für die Snapshots festzulegen.")
            steps = [{"cmd": ["pacman", "-S", "--needed", "timeshift"], "needs_sudo": True, "interactive": True,
                      "label": "sudo pacman -S timeshift"}]
        if not ask_confirm(self, "Wiederherstellung einrichten", text, "Einrichten"):
            return
        if not self.app.priv.ensure(self):
            return
        self._run(steps, done=lambda: e.get("fs") != "btrfs" and which("timeshift") and self._open_timeshift())

    def _open_timeshift(self):
        from PySide6.QtCore import QProcess
        exe = "timeshift-launcher" if which("timeshift-launcher") else "timeshift-gtk"
        QProcess.startDetached(exe, [])
        self.app.set_status("Timeshift ist geöffnet – nach dem Einrichten hier auf ↻ klicken.")

    def create(self):
        if not self.app.priv.ensure(self):
            return
        self._run([{"cmd": snapshot_create_cmd(self.env["tool"], f"{SNAP_DESC}: manuell"), "needs_sudo": True,
                    "label": "Snapshot erstellen"}])

    def delete(self):
        s = self._selected()
        if not s or not ask_confirm(self, "Snapshot löschen", f"Snapshot {s['id']} vom {s['date']} löschen?",
                                    "Löschen", danger=True):
            return
        if not self.app.priv.ensure(self):
            return
        cmd = (["snapper", "-c", "root", "delete", s["id"]] if self.env["tool"] == "snapper"
               else ["timeshift", "--delete", "--snapshot", s["id"], "--scripted"])
        self._run([{"cmd": cmd, "needs_sudo": True, "label": f"Snapshot {s['id']} löschen"}])

    def restore(self):
        s = self._selected()
        if not s:
            return
        tool = self.env["tool"]
        text = (f"System auf den Stand vom {s['date']} zurücksetzen?\n\n„{s['desc']}“\n\n"
                "Alle Systemänderungen seit diesem Snapshot werden rückgängig gemacht – auch installierte "
                "Updates und Programme. Deine Dateien in /home bleiben unberührt.\n\n"
                "Danach bitte neu starten.")
        if tool == "timeshift":
            text += "\n\nTimeshift startet den Rechner nach dem Zurücksetzen selbst neu."
        if not ask_confirm(self, "Zurücksetzen", text, "Zurücksetzen", danger=True):
            return
        if not self.app.priv.ensure(self):
            return
        if tool == "snapper":
            cmd = ["snapper", "-c", "root", "undochange", f"{s['id']}..0"]
        else:
            cmd = ["timeshift", "--restore", "--snapshot", s["id"], "--scripted", "--yes"]
        self._run([{"cmd": snapshot_create_cmd(tool, f"{SNAP_DESC}: vor dem Zurücksetzen"), "needs_sudo": True,
                    "label": "Sicherheits-Snapshot des jetzigen Stands"},
                   {"cmd": cmd, "needs_sudo": True, "label": f"Zurücksetzen auf {s['id']}"}],
                  done=lambda: self.app.set_status("Zurückgesetzt – bitte jetzt neu starten."))


# --------------------------------------------------------------------------
# Modul: Einrichten (Basics mit einem Klick, Ersatz für Windows-Programme)
# --------------------------------------------------------------------------

# (Kennung, Titel, Beschreibung, pacman-Pakete)
BASICS = [
    ("fonts", "Schriften für Office-Dokumente",
     "Liberation (passt in der Breite zu Arial, Times New Roman und Courier New), Noto mit Emojis und DejaVu – "
     "Word-Dokumente sehen damit aus wie unter Windows.",
     ["ttf-liberation", "noto-fonts", "noto-fonts-emoji", "ttf-dejavu"]),
    ("codecs", "Audio- und Video-Codecs",
     "Damit spielen MP3, MP4, H.264/H.265 und Co. in allen Programmen ab.",
     ["gst-plugins-good", "gst-plugins-bad", "gst-plugins-ugly", "gst-libav", "ffmpeg"]),
    ("power", "Energieprofile",
     "Zwischen Energiesparen, Ausgewogen und Leistung umschalten – wie unter Windows. "
     "Nicht zusammen mit TLP nutzen.",
     ["power-profiles-daemon"]),
]

# Windows-Programm → Alternativen: (Name, Beschreibung, Quelle, Paket/App-ID)
# Quelle: "pacman", "flathub", "web" (nur Link) oder "tuxdex" (eingebaut, Modulkennung)
ALTERNATIVES = [
    (("Photoshop", "Bildbearbeitung"), [
        ("GIMP", "Klassische Bildbearbeitung", "pacman", "gimp"),
        ("Krita", "Malen und Bildbearbeitung", "pacman", "krita"),
        ("Photopea", "Photoshop-ähnlich im Browser, öffnet PSD", "web", "https://www.photopea.com")]),
    (("Lightroom", "RAW", "Fotos entwickeln"), [
        ("darktable", "RAW-Fotos entwickeln und verwalten", "pacman", "darktable"),
        ("RawTherapee", "RAW-Entwicklung", "pacman", "rawtherapee")]),
    (("Illustrator", "CorelDRAW", "Vektorgrafik"), [
        ("Inkscape", "Vektorgrafik, öffnet SVG und AI", "pacman", "inkscape")]),
    (("Paint",), [
        ("KolourPaint", "Einfach malen wie in Paint", "pacman", "kolourpaint"),
        ("Pinta", "Wie Paint.NET", "flathub", "com.github.PintaProject.Pinta")]),
    (("Microsoft Office", "Office", "Word", "Excel", "PowerPoint"), [
        ("LibreOffice", "Texte, Tabellen, Präsentationen – öffnet DOCX, XLSX, PPTX", "pacman", "libreoffice-fresh"),
        ("OnlyOffice", "Sieht aus wie Microsoft Office", "flathub", "org.onlyoffice.desktopeditors")]),
    (("Outlook", "E-Mail"), [
        ("Thunderbird", "E-Mail, Kalender, Kontakte", "pacman", "thunderbird")]),
    (("OneNote", "Notizen", "Evernote"), [
        ("Joplin", "Notizen mit Synchronisierung", "flathub", "net.cozic.joplin_desktop"),
        ("Obsidian", "Notizen und Wissenssammlung", "pacman", "obsidian")]),
    (("Adobe Reader", "Acrobat", "PDF"), [
        ("Okular", "PDFs lesen und kommentieren", "pacman", "okular")]),
    (("Premiere", "Vegas", "Videoschnitt"), [
        ("Kdenlive", "Videoschnitt", "pacman", "kdenlive"),
        ("Shotcut", "Einfacher Videoschnitt", "pacman", "shotcut")]),
    (("Audition", "Audio bearbeiten"), [
        ("Audacity", "Audio aufnehmen und schneiden", "pacman", "audacity")]),
    (("OBS", "Bildschirm aufnehmen", "Streaming"), [
        ("OBS Studio", "Aufnehmen und streamen", "pacman", "obs-studio")]),
    (("Windows Media Player", "Video abspielen", "Musik"), [
        ("VLC", "Spielt praktisch alles ab", "pacman", "vlc"),
        ("Strawberry", "Musiksammlung wie iTunes", "pacman", "strawberry")]),
    (("iTunes",), [
        ("Strawberry", "Musiksammlung verwalten", "pacman", "strawberry")]),
    (("Spotify",), [
        ("Spotify", "Gibt es auch für Linux", "flathub", "com.spotify.Client")]),
    (("Notepad++", "Editor", "Notepad"), [
        ("Kate", "Starker Text-Editor", "pacman", "kate")]),
    (("Visual Studio", "VS Code", "Programmieren"), [
        ("Code – OSS", "Open-Source-Version von VS Code", "pacman", "code"),
        ("VS Code", "Die Microsoft-Version", "flathub", "com.visualstudio.code")]),
    (("WinRAR", "7-Zip", "ZIP", "Entpacken"), [
        ("Ark", "Archive packen und entpacken", "pacman", "ark"),
        ("7-Zip", "7-Zip für die Kommandozeile", "pacman", "7zip")]),
    (("Explorer", "Dateien"), [
        ("Dolphin", "Dateimanager von KDE", "pacman", "dolphin")]),
    (("Snipping Tool", "Screenshot"), [
        ("Spectacle", "Screenshots und Bildschirmaufnahmen", "pacman", "spectacle")]),
    (("Chrome", "Edge", "Browser"), [
        ("Firefox", "Schneller, privater Browser", "pacman", "firefox"),
        ("Chromium", "Open-Source-Basis von Chrome", "pacman", "chromium")]),
    (("Teams",), [
        ("Teams for Linux", "Inoffizielle Teams-App", "flathub", "com.github.IsmaelMartinez.teams_for_linux")]),
    (("Zoom",), [
        ("Zoom", "Gibt es auch für Linux", "flathub", "us.zoom.Zoom")]),
    (("Discord",), [
        ("Discord", "Gibt es auch für Linux", "pacman", "discord")]),
    (("Telegram",), [
        ("Telegram", "Gibt es auch für Linux", "pacman", "telegram-desktop")]),
    (("KeePass", "1Password", "Passwörter"), [
        ("KeePassXC", "Passwort-Manager", "pacman", "keepassxc")]),
    (("FileZilla", "WinSCP", "FTP"), [
        ("FileZilla", "Gibt es auch für Linux", "pacman", "filezilla")]),
    (("AutoCAD", "CAD"), [
        ("FreeCAD", "3D-Konstruktion", "pacman", "freecad"),
        ("LibreCAD", "2D-Zeichnungen", "pacman", "librecad")]),
    (("Steam", "Spiele"), [
        ("Steam", "Spiele-Plattform mit Proton für Windows-Spiele", "flathub", "com.valvesoftware.Steam")]),
    (("Task-Manager", "Taskmanager"), [
        ("Tuxdex-Taskmanager", "Ist in Tuxdex schon eingebaut", "tuxdex", "tasks")]),
    (("Datenträgerverwaltung", "Laufwerke"), [
        ("Tuxdex-Datenträger", "Ist in Tuxdex schon eingebaut", "tuxdex", "disks")]),
    (("Systemwiederherstellung", "Wiederherstellungspunkt"), [
        ("Tuxdex-Wiederherstellung", "Ist in Tuxdex schon eingebaut", "tuxdex", "restore")]),
]

POWER_PROFILES = [("power-saver", "Energiesparen"), ("balanced", "Ausgewogen"), ("performance", "Leistung")]


def installed_pacman():
    try:
        return set(subprocess.run(["pacman", "-Qq"], capture_output=True, text=True, timeout=15).stdout.split())
    except Exception:
        return set()


def installed_flatpaks():
    if not which("flatpak"):
        return set()
    try:
        return set(subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                                  capture_output=True, text=True, timeout=15).stdout.split())
    except Exception:
        return set()


def power_profile():
    return _cmd_out(["powerprofilesctl", "get"]).strip() if which("powerprofilesctl") else ""


def find_alternatives(query):
    q = query.strip().lower()
    if not q:
        return ALTERNATIVES
    return [e for e in ALTERNATIVES if any(q in k.lower() or q in tr(k).lower() for k in e[0])
            or any(q in a[0].lower() for a in e[1])]


class SetupTab(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.pac = set()
        self.fp = set()
        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Einrichten", self.badge))
        self.lay.addWidget(Label("Was nach einem Umstieg von Windows fehlt – mit einem Klick erledigt.",
                                 "Hint", wrap=True))

        bp = Panel("Basics mit einem Klick")
        self.basic_rows = {}
        for key, title, desc, pkgs in BASICS:
            row = QHBoxLayout()
            row.setSpacing(12)
            col = QVBoxLayout()
            col.setSpacing(2)
            t = Label(title)
            t.setStyleSheet("font-weight:600;")
            col.addWidget(t)
            col.addWidget(Label(desc, "Hint", wrap=True))
            row.addLayout(col, 1)
            b = Button("Installieren", "primary", lambda _=False, k=key: self.install_basic(k))
            b.setMinimumWidth(130)
            row.addWidget(b, 0, Qt.AlignVCenter)
            badge = StatusBadge("off", "…")
            badge.setMinimumWidth(110)
            row.addWidget(badge, 0, Qt.AlignVCenter)
            bp.body.addLayout(row)
            bp.body.addSpacing(6)
            self.basic_rows[key] = (badge, b)
        self.power_row = QHBoxLayout()
        self.power_row.addWidget(Label("Aktuelles Profil".upper(), "FieldLabel"))
        self.power_seg = Segmented([p[1] for p in POWER_PROFILES], self.set_power)
        self.power_row.addWidget(self.power_seg)
        self.power_row.addStretch(1)
        self.power_box = QWidget()
        self.power_box.setLayout(self.power_row)
        self.power_box.hide()
        bp.body.addWidget(self.power_box)
        self.lay.addWidget(bp)

        ap = Panel("Ersatz für Windows-Programme")
        ap.body.addWidget(Label("Gib ein, was du unter Windows benutzt hast – z. B. „Photoshop“ oder „Office“.",
                                "Hint", wrap=True))
        self.search = LineEdit("", "Windows-Programm suchen …")
        self.search.textChanged.connect(self.render_alternatives)
        ap.body.addWidget(self.search)
        self.alt_box = QVBoxLayout()
        self.alt_box.setSpacing(10)
        ap.body.addLayout(self.alt_box)
        self.lay.addWidget(ap)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)
        self.refresh()

    def refresh(self):
        def worker():
            pac, fp, prof = installed_pacman(), installed_flatpaks(), power_profile()
            ui(lambda: self._apply(pac, fp, prof))
        threading.Thread(target=worker, daemon=True).start()

    def _apply(self, pac, fp, prof):
        self.pac, self.fp = pac, fp
        done = 0
        for key, _, _, pkgs in BASICS:
            badge, b = self.basic_rows[key]
            missing = [p for p in pkgs if p not in pac]
            if not missing:
                badge.set("ok", "Installiert")
                done += 1
            elif len(missing) < len(pkgs):
                badge.set("warn", f"{len(pkgs) - len(missing)} von {len(pkgs)}")
            else:
                badge.set("off", "Fehlt")
            b.setVisible(bool(missing))
        self.badge.set("ok" if done == len(BASICS) else "info", f"{done} von {len(BASICS)} Basics")
        ids = [p[0] for p in POWER_PROFILES]
        self.power_box.setVisible(prof in ids)
        if prof in ids:
            self.power_seg.set(ids.index(prof))
        self.render_alternatives()

    def render_alternatives(self, *_):
        clear_layout(self.alt_box)
        hits = find_alternatives(self.search.text())
        if not hits:
            self.alt_box.addWidget(Label("Dazu kenne ich noch keinen Ersatz. Im Tab „Software“ kannst du "
                                         "frei nach Programmen suchen.", "Muted", wrap=True))
            return
        for keys, alts in hits[:12]:
            card = QFrame()
            card.setObjectName("Panel")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 10, 14, 10)
            cl.setSpacing(6)
            head = Label(f"Statt {keys[0]}")
            head.setStyleSheet("font-weight:600;")
            cl.addWidget(head)
            for name, desc, src, ident in alts:
                r = QHBoxLayout()
                r.setSpacing(10)
                r.addWidget(Label(name), 0)
                r.addWidget(Label(desc, "Hint"), 1)
                r.addWidget(self._alt_button(name, src, ident), 0)
                cl.addLayout(r)
            self.alt_box.addWidget(card)
        if len(hits) > 12:
            self.alt_box.addWidget(Label(f"… und {len(hits) - 12} weitere – einfach suchen.", "Hint"))

    def _alt_button(self, name, src, ident):
        if src == "web":
            return Button("Im Browser öffnen", "ghost", lambda: subprocess.Popen(
                ["xdg-open", ident], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if src == "tuxdex":
            return Button("Öffnen", "ghost", lambda: self.app.select([m[0] for m in MODULES].index(ident)))
        have = ident in (self.pac if src == "pacman" else self.fp)
        if have:
            b = Button("Installiert", "ghost")
            b.setEnabled(False)
            return b
        label = "Installieren" + (" (Flathub)" if src == "flathub" else "")
        b = Button(label, "primary", lambda: self.install_alt(name, src, ident))
        if src == "flathub" and not which("flatpak"):
            b.setEnabled(False)
            b.setToolTip("Flatpak fehlt – im Tab „Flatpak“ einrichten.")
        return b

    # --- Aktionen --------------------------------------------------------
    def _run(self, steps):
        def all_done():
            self.refresh()
            self.app.set_status("Fertig.")
        run_sequence(steps, self.log, on_all_done=all_done)

    def install_basic(self, key):
        _, title, _, pkgs = next(b for b in BASICS if b[0] == key)
        missing = [p for p in pkgs if p not in self.pac]
        if not missing or not ask_confirm(self, title, f"Installieren: {', '.join(missing)}?", "Installieren"):
            return
        if not self.app.priv.ensure(self):
            return
        steps = [{"cmd": ["pacman", "-S", "--needed", "--"] + missing, "needs_sudo": True, "interactive": True,
                  "label": "sudo pacman -S " + " ".join(missing)}]
        if key == "power":
            steps.append({"cmd": ["systemctl", "enable", "--now", "power-profiles-daemon"], "needs_sudo": True,
                          "label": "systemctl enable --now power-profiles-daemon"})
        self._run(steps)

    def install_alt(self, name, src, ident):
        where = "aus den Arch-Paketquellen" if src == "pacman" else "von Flathub"
        if not ask_confirm(self, name, f"{name} {where} installieren?", "Installieren"):
            return
        if not self.app.priv.ensure(self):
            return
        if src == "pacman":
            step = {"cmd": ["pacman", "-S", "--needed", "--", ident], "needs_sudo": True, "interactive": True,
                    "label": f"sudo pacman -S {ident}"}
        else:
            step = {"cmd": ["flatpak", "install", "-y", "flathub", ident], "needs_sudo": True, "interactive": True,
                    "label": f"flatpak install flathub {ident}"}
        self._run([step])

    def set_power(self, idx):
        prof = POWER_PROFILES[idx][0]

        def cb(rc, out, err):
            self.app.set_status(f"Energieprofil: {POWER_PROFILES[idx][1]}." if rc == 0
                                else f"Energieprofil ließ sich nicht setzen: {err.strip()}")
        run_capture_async(["powerprofilesctl", "set", prof], cb)


# --------------------------------------------------------------------------
# Modul: Benutzer
# --------------------------------------------------------------------------

class UsersTab(Page):
    COLS = [("Benutzer", 140), ("UID", 70), ("Home", 180), ("Shell", 150), ("Letzter Login", 240)]

    def __init__(self, app):
        super().__init__()
        self.app = app

        self.lay.addLayout(page_header(
            "Registrierte Benutzer",
            Button("Aktualisieren (mit Login-Zeiten)", "ghost", self.refresh_with_auth)))

        p = Panel()
        self.tree = QTableWidget(0, len(self.COLS))
        self.tree.setHorizontalHeaderLabels([c[0].upper() for c in self.COLS])
        self.tree.verticalHeader().setVisible(False)
        self.tree.setShowGrid(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.verticalHeader().setDefaultSectionSize(32)
        hh = self.tree.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, (_, w) in enumerate(self.COLS):
            self.tree.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        self.tree.setMinimumHeight(320)
        p.body.addWidget(self.tree)
        p.body.addWidget(Label("Login-Zeiten benötigen root-Rechte – Klick auf „Aktualisieren“ fragt "
                               "bei Bedarf einmal nach dem Passwort.", "Small", wrap=True))
        self.lay.addWidget(p, 1)

        self.load_users()

    def load_users(self):
        self.tree.setRowCount(0)
        try:
            with open("/etc/passwd") as f:
                lines = f.readlines()
        except Exception:
            return

        users = []
        for line in lines:
            parts = line.strip().split(":")
            if len(parts) < 7:
                continue
            name, _, uid, _, _, home, shell = parts[:7]
            try:
                uid_i = int(uid)
            except ValueError:
                continue
            if 1000 <= uid_i < 60000 and "nologin" not in shell and "false" not in shell:
                users.append((name, uid, home, shell))

        for name, uid, home, shell in users:
            r = self.tree.rowCount()
            self.tree.insertRow(r)
            for c, v in enumerate((name, uid, home, shell, "—")):
                self.tree.setItem(r, c, QTableWidgetItem(v))

        # Unaufgefordert (ohne Passwort-Dialog!) versuchen - klappt nur,
        # wenn bereits eine sudo-Sitzung aus einer anderen Aktion aktiv ist.
        def cb(rc, out, err):
            if rc != 0:
                return
            self._apply_lastlog(out)
        run_capture_async(["lastlog"], cb, needs_sudo=True)

    def refresh_with_auth(self):
        self.app.priv.ensure(self)
        self.load_users()

    def _apply_lastlog(self, out):
        logins = {}
        for line in out.splitlines()[1:]:
            if not line.strip():
                continue
            uname = line.split(None, 1)[0]
            rest = line[len(uname):].strip()
            logins[uname] = rest if rest else "nie"
        for r in range(self.tree.rowCount()):
            name = self.tree.item(r, 0).text()
            self.tree.setItem(r, 4, QTableWidgetItem(logins.get(name, "unbekannt")))


# --------------------------------------------------------------------------
# Modul: Datenträger (Einhängen, Umbenennen, Formatieren, Prüfen, Auswerfen)
# --------------------------------------------------------------------------

def fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# Mountpoints, die nie über die App ausgehängt/formatiert werden dürfen
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var", "[SWAP]"}

# Label-Werkzeuge: (Befehl-Builder, max. Länge, muss ausgehängt sein, Paket)
LABEL_TOOLS = {
    "ext2": (lambda d, l, mp: ["e2label", d, l], 16, False, "e2fsprogs"),
    "ext3": (lambda d, l, mp: ["e2label", d, l], 16, False, "e2fsprogs"),
    "ext4": (lambda d, l, mp: ["e2label", d, l], 16, False, "e2fsprogs"),
    "btrfs": (lambda d, l, mp: ["btrfs", "filesystem", "label", mp or d, l], 255, False, "btrfs-progs"),
    "vfat": (lambda d, l, mp: ["fatlabel", d, l.upper()], 11, True, "dosfstools"),
    "exfat": (lambda d, l, mp: ["exfatlabel", d, l], 15, True, "exfatprogs"),
    "ntfs": (lambda d, l, mp: ["ntfslabel", d, l], 32, True, "ntfs-3g"),
    "xfs": (lambda d, l, mp: ["xfs_admin", "-L", l, d], 12, True, "xfsprogs"),
    "swap": (lambda d, l, mp: ["swaplabel", "-L", l, d], 16, False, "util-linux"),
}

# Formatieren: Anzeigename → (Befehl-Builder, max. Label-Länge, Paket)
FORMATS = {
    "ext4 (Linux)": (lambda d, l: ["mkfs.ext4", "-F"] + (["-L", l] if l else []) + [d], 16, "e2fsprogs"),
    "btrfs (Linux)": (lambda d, l: ["mkfs.btrfs", "-f"] + (["-L", l] if l else []) + [d], 255, "btrfs-progs"),
    "xfs (Linux)": (lambda d, l: ["mkfs.xfs", "-f"] + (["-L", l] if l else []) + [d], 12, "xfsprogs"),
    "exFAT (USB-Sticks, alle Systeme)": (lambda d, l: ["mkfs.exfat"] + (["-L", l] if l else []) + [d], 15, "exfatprogs"),
    "FAT32 (maximal kompatibel)": (lambda d, l: ["mkfs.fat", "-F", "32"] + (["-n", l.upper()] if l else []) + [d], 11, "dosfstools"),
    "NTFS (Windows)": (lambda d, l: ["mkfs.ntfs", "-f"] + (["-L", l] if l else []) + [d], 32, "ntfs-3g"),
}

CHECK_TOOLS = {
    "ext2": ["e2fsck", "-f", "-n"], "ext3": ["e2fsck", "-f", "-n"], "ext4": ["e2fsck", "-f", "-n"],
    "btrfs": ["btrfs", "check", "--readonly"], "vfat": ["fsck.fat", "-n"],
    "exfat": ["fsck.exfat", "-n"], "ntfs": ["ntfsfix", "-n"], "xfs": ["xfs_repair", "-n"],
}

LABEL_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9 _.-]{0,254}$")   # nie mit „-“ beginnen (sonst Option)


def _mounts(node):
    mps = node.get("mountpoints")
    if mps is None:
        mps = [node.get("mountpoint")]
    return [m for m in mps if m]


def format_blockers(dev):
    """Gründe, warum dev jetzt nicht formatiert werden darf: eingehängte oder aktive Teile darunter
    (Partitionen, geöffnete LUKS-Container, LVM, Swap) oder das Laufwerk des laufenden Systems."""
    try:
        data = json.loads(subprocess.run(["lsblk", "-J", "-o", "PATH,TYPE,FSTYPE,MOUNTPOINTS", dev],
                                         capture_output=True, text=True, timeout=10).stdout or "{}")
    except Exception:
        return [f"{dev} konnte nicht geprüft werden (lsblk)."]
    out, stack = [], list(data.get("blockdevices", []))
    if not stack:
        return [f"{dev} wurde nicht gefunden."]
    swaps = {l.split()[0] for l in _read("/proc/swaps").splitlines()[1:] if l.split()}
    while stack:
        n = stack.pop()
        stack += n.get("children") or []
        mps = [m for m in (n.get("mountpoints") or []) if m]
        if mps:
            out.append(f"{n['path']} ist eingehängt ({', '.join(mps)})")
        if n.get("path") in swaps:
            out.append(f"{n['path']} wird als Swap benutzt")
        if n.get("path") != dev and n.get("type") in ("crypt", "lvm", "raid1", "raid0", "raid5", "raid10"):
            out.append(f"{n['path']} ist geöffnet/aktiv ({n['type']})")
    return out


def _all_mounts(node):
    out = list(_mounts(node))
    for ch in node.get("children") or []:
        out += _all_mounts(ch)
    return out


class RenameDialog(QDialog):
    def __init__(self, parent, dev, current, maxlen):
        super().__init__(parent)
        self.setWindowTitle("Datenträger umbenennen")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        spacer = QWidget()
        spacer.setFixedSize(380, 0)
        lay.addWidget(spacer)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)
        lay.addWidget(Label("Datenträger umbenennen", "DialogTitle"))
        lay.addWidget(Label(f"{dev} · höchstens {maxlen} Zeichen (Buchstaben, Ziffern, Leerzeichen, _ . -)",
                            "Small", wrap=True))
        self.entry = LineEdit(current or "", mono=True)
        self.entry.setMaxLength(maxlen)
        lay.addLayout(Field("Neue Bezeichnung", self.entry))
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(Button("Abbrechen", "ghost", self.reject))
        ok = Button("Umbenennen", "primary", self.accept)
        ok.setDefault(True)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self.entry.selectAll()
        self.entry.setFocus()


class FormatDialog(QDialog):
    def __init__(self, parent, dev, name, size):
        super().__init__(parent)
        self.setWindowTitle("Datenträger formatieren")
        self.setMinimumWidth(480)
        self.name = name
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QLayout.SetMinimumSize)
        spacer = QWidget()
        spacer.setFixedSize(380, 0)
        lay.addWidget(spacer)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)
        lay.addWidget(Label("Datenträger formatieren", "DialogTitle"))
        lay.addWidget(Label(f"✕  Alle Daten auf {dev} ({size}) werden unwiderruflich gelöscht.", "Danger", wrap=True))
        self.fs = QComboBox()
        for k in FORMATS:
            self.fs.addItem(k, k)          # Schlüssel als Daten – der sichtbare Text wird ggf. übersetzt
        self.fs.setMinimumHeight(38)
        lay.addLayout(Field("Dateisystem", self.fs))
        self.label = LineEdit(placeholder="optional, z. B. USB-Stick", mono=True)
        lay.addLayout(Field("Bezeichnung", self.label))
        self.confirm = LineEdit(placeholder=name, mono=True)
        lay.addLayout(Field("Zur Bestätigung den Gerätenamen eintippen", self.confirm))
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(Button("Abbrechen", "ghost", self.reject))
        self.ok = Button("Formatieren", "danger", self.accept)
        self.ok.setEnabled(False)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.confirm.textChanged.connect(lambda t: self.ok.setEnabled(t.strip() == name))
        self.fs.currentIndexChanged.connect(self._limit)
        self._limit()

    def _limit(self):
        self.label.setMaxLength(FORMATS[self.fs.currentData()][1])


class DisksTab(Page):
    COLS = [("Gerät", 200), ("Bezeichnung", 150), ("Dateisystem", 110), ("Größe", 90),
            ("Belegt", 90), ("Eingehängt unter", 200)]

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.nodes = {}      # path -> lsblk-Knoten
        self.parents = {}    # path -> Eltern-Knoten
        self.current = None

        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Datenträger", self.badge,
                                       Button("Diagnose", "ghost", self.diagnose,
                                              "Rohdaten von lsblk und USB in die Ausgabe schreiben"),
                                       Button("↻", "icon", self.refresh, "Neu einlesen")))
        self.warn = Label("", "Danger", wrap=True)
        self.warn.setTextFormat(Qt.RichText)
        self.warn.hide()
        self.lay.addWidget(self.warn)

        lst = Panel("Laufwerke & Partitionen")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLS))
        self.tree.setHeaderLabels([c[0].upper() for c in self.COLS])
        for i, (_, w) in enumerate(self.COLS):
            self.tree.setColumnWidth(i, w)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.tree.setMinimumHeight(320)
        self.tree.setRootIsDecorated(True)
        self.tree.currentItemChanged.connect(lambda cur, prev: self._select(cur))
        lst.body.addWidget(self.tree)
        self.lay.addWidget(lst, 1)

        det = Panel("Details")
        self.det_title = Label("Kein Gerät ausgewählt", "PanelTitle")
        det.body.addWidget(self.det_title)
        self.det_grid = QGridLayout()
        self.det_grid.setHorizontalSpacing(24)
        self.det_grid.setVerticalSpacing(10)
        det.body.addLayout(self.det_grid)
        self.det_usage = QVBoxLayout()
        det.body.addLayout(self.det_usage)
        self.det_note = Label("", "Hint", wrap=True)
        det.body.addWidget(self.det_note)

        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.b_mount = Button("Einhängen", "primary", self.mount)
        self.b_umount = Button("Aushängen", "ghost", self.umount)
        self.b_rename = Button("Umbenennen", "ghost", self.rename)
        self.b_check = Button("Prüfen (nur lesen)", "ghost", self.check_fs)
        self.b_eject = Button("Sicher entfernen", "ghost", self.eject)
        self.b_format = Button("Formatieren …", "danger", self.format)
        for b in (self.b_mount, self.b_umount, self.b_rename, self.b_check, self.b_eject):
            acts.addWidget(b)
        acts.addStretch(1)
        acts.addWidget(self.b_format)
        det.body.addLayout(acts)
        self.lay.addWidget(det)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        self._select(None)
        self.refresh()

    # ---- Daten ----------------------------------------------------------

    def refresh(self):
        keep = self.current.get("path") if self.current else None
        cols = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,RM,HOTPLUG,FSUSED,FSSIZE,TRAN,RO,UUID"

        def done(rc, out, err):
            if rc != 0 and "MOUNTPOINTS" in cols:
                # ältere util-linux-Versionen kennen MOUNTPOINTS nicht
                run_capture_async(["lsblk", "-J", "-b", "-o", cols.replace("MOUNTPOINTS", "MOUNTPOINT")],
                                  lambda r2, o2, e2: self._fill(r2, o2, e2, keep))
                return
            self._fill(rc, out, err, keep)

        run_capture_async(["lsblk", "-J", "-b", "-o", cols], done)

    def _fill(self, rc, out, err, keep):
        try:
            self._fill_inner(rc, out, err, keep)
        except Exception as e:
            self.badge.set("danger", "Fehler")
            self.log.set_text(f"error: Laufwerksliste konnte nicht aufgebaut werden: {e}\n")
            raise
        self._check_usb()

    def _check_usb(self):
        msgs = []
        usb = usb_storage_devices()
        no_driver = [n for n, ok in usb if not ok]
        if kernel_modules_missing():
            msgs.append(f"✕ Der Kernel wurde aktualisiert, läuft aber noch in der alten Version "
                        f"({os.uname().release}). Bis zum <b>Neustart</b> können neue USB-Sticks nicht "
                        f"erkannt werden, weil die Treiber des laufenden Kernels gelöscht wurden.")
        if no_driver:
            msgs.append("✕ USB-Speicher angeschlossen, aber ohne Treiber: " + ", ".join(no_driver)
                        + (" – bitte neu starten." if kernel_modules_missing()
                           else " – Stick abziehen und neu einstecken, sonst „Diagnose“ klicken."))
        self.warn.setText("<br>".join(msgs))
        self.warn.setVisible(bool(msgs))

    def diagnose(self):
        self.log.set_text("")
        lines = [f"$ uname -r\n{os.uname().release}",
                 f"Module für laufenden Kernel vorhanden: {'nein – Neustart nötig!' if kernel_modules_missing() else 'ja'}"]
        usb = usb_storage_devices()
        lines.append("USB-Massenspeicher (USB-Ebene): " + (", ".join(
            f"{n} [{'Treiber ok' if ok else 'KEIN Treiber'}]" for n, ok in usb) or "keiner gefunden"))
        self.log.append_text("\n".join(lines) + "\n\n")
        run_streaming(["lsblk", "-o", "NAME,TYPE,SIZE,FSTYPE,LABEL,RM,HOTPLUG,TRAN,MOUNTPOINTS"],
                      self.log, clear_first=False)

    def _fill_inner(self, rc, out, err, keep):
        self.tree.clear()
        self.nodes.clear()
        self.parents.clear()
        try:
            devs = json.loads(out)["blockdevices"]
        except Exception:
            self.badge.set("danger", "lsblk-Fehler")
            self.log.set_text(f"error: lsblk fehlgeschlagen (Exit {rc}): {err.strip()}\n"
                              f"Ausgabe: {out[:500]}\n")
            return
        devs = [d for d in devs if d.get("type") not in ("loop", "rom") and not str(d.get("name", "")).startswith("zram")]
        n_disks = 0
        select_item = None

        def add(node, parent_item, parent_node):
            nonlocal select_item
            path = node.get("path") or f"/dev/{node.get('name')}"
            node["path"] = path
            self.nodes[path] = node
            self.parents[path] = parent_node
            name = path
            if node.get("type") == "disk":
                extra = " · ".join(x for x in (node.get("model") or "", (node.get("tran") or "").upper()) if x)
                name = f"{path}  {extra}" if extra else path
            used = node.get("fsused")
            vals = [name, node.get("label") or "", node.get("fstype") or "",
                    fmt_bytes(node.get("size")), fmt_bytes(used) if used else "",
                    ", ".join(_mounts(node))]
            it = QTreeWidgetItem(parent_item, vals) if parent_item else QTreeWidgetItem(self.tree, vals)
            it.setData(0, Qt.UserRole, path)
            for c in (0, 3, 4, 5):
                it.setFont(c, QFont(FONTS["mono"], 10))
            if node.get("type") == "disk":
                f = QFont(FONTS["sans"], 10)
                f.setWeight(QFont.DemiBold)
                it.setFont(0, f)
            if path == keep:
                select_item = it
            for ch in node.get("children") or []:
                add(ch, it, node)
            return it

        for d in devs:
            n_disks += 1
            add(d, None, None)
        self.tree.expandAll()
        self.badge.set("info", f"{n_disks} Laufwerk{'e' if n_disks != 1 else ''}")
        if select_item:
            self.tree.setCurrentItem(select_item)
        else:
            self._select(None)

    # ---- Auswahl --------------------------------------------------------

    def _select(self, item):
        while self.det_grid.count():
            lay = self.det_grid.takeAt(0).layout()
            if lay:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
        while self.det_usage.count():
            w = self.det_usage.takeAt(0).widget()
            if w:
                w.deleteLater()
        node = self.nodes.get(item.data(0, Qt.UserRole)) if item else None
        self.current = node
        for b in (self.b_mount, self.b_umount, self.b_rename, self.b_check, self.b_eject, self.b_format):
            b.setEnabled(False)
        if not node:
            self.det_title.setText("Kein Gerät ausgewählt")
            self.det_note.setText("Wähle oben ein Laufwerk oder eine Partition aus.")
            return

        path, fs, typ = node["path"], node.get("fstype") or "", node.get("type")
        mps = _mounts(node)
        self.det_title.setText(path + (f"  ·  {node['label']}" if node.get("label") else ""))
        info = [("Typ", {"disk": "Laufwerk", "part": "Partition", "crypt": "Verschlüsselt (geöffnet)",
                         "lvm": "LVM-Volume"}.get(typ, typ or "—")),
                ("Dateisystem", fs or "—"), ("Größe", fmt_bytes(node.get("size"))),
                ("Eingehängt unter", ", ".join(mps) or "nicht eingehängt"),
                ("Wechseldatenträger", "ja" if self._removable(node) else "nein"),
                ("UUID", node.get("uuid") or "—")]
        for i, (k, v) in enumerate(info):
            self.det_grid.addLayout(Field(k, Label(v, "Value", wrap=True)), i // 3, i % 3)
        if node.get("fsused") and node.get("fssize"):
            self.det_usage.addWidget(UsageBar(mps[0] if mps else path, int(node["fsused"]) / 1073741824,
                                              int(node["fssize"]) / 1073741824))

        system = any(m in SYSTEM_MOUNTS for m in _all_mounts(node))
        has_children = bool(node.get("children"))
        mountable = bool(fs) and fs not in ("swap", "crypto_LUKS", "LVM2_member", "linux_raid_member")
        notes = []
        self.b_mount.setEnabled(mountable and not mps)
        self.b_umount.setEnabled(bool(mps) and not system and fs != "swap")
        self.b_rename.setEnabled(fs in LABEL_TOOLS and not system)
        self.b_check.setEnabled(fs in CHECK_TOOLS and not mps)
        self.b_eject.setEnabled(self._removable(node) and not system)
        self.b_format.setEnabled(not system and not _all_mounts(node) and not (typ == "disk" and has_children)
                                 and typ in ("part", "disk"))
        if system:
            notes.append("System-Datenträger – Aushängen, Umbenennen und Formatieren sind gesperrt.")
        elif typ == "disk" and has_children:
            notes.append("Zum Formatieren eine Partition auswählen.")
        elif _all_mounts(node):
            notes.append("Zum Formatieren oder Prüfen erst aushängen.")
        if fs == "crypto_LUKS":
            notes.append("Verschlüsselte Partition – bitte mit cryptsetup/Dateimanager entsperren.")
        if not fs and typ == "part":
            notes.append("Kein Dateisystem – mit „Formatieren“ anlegen.")
        self.det_note.setText("  ".join(notes))

    def _removable(self, node):
        disk = node if node.get("type") == "disk" else self.parents.get(node["path"])
        while disk is not None and disk.get("type") != "disk":
            disk = self.parents.get(disk["path"])
        return bool(disk and (disk.get("rm") or disk.get("hotplug")))

    # ---- Aktionen -------------------------------------------------------

    def _root(self, cmd, label=None, then=None):
        if not self.app.priv.ensure(self):
            return
        self.log.set_text(f"$ sudo {label or ' '.join(shlex.quote(c) for c in cmd)}\n")

        def done(rc):
            self.log.append_text(f"[Exit-Code {rc}]\n")
            if then:
                then(rc)
            self.refresh()

        run_streaming(cmd, self.log, needs_sudo=True, clear_first=False, on_done=done)

    def _need_tool(self, tool, pkg):
        if which(tool):
            return True
        show_error(self, "Werkzeug fehlt", f"'{tool}' ist nicht installiert.\n\nsudo pacman -S {pkg}")
        return False

    def mount(self):
        n = self.current
        dev = n["path"]
        if which("udisksctl"):
            self.log.set_text(f"$ udisksctl mount -b {dev}\n")

            def done(rc):
                self.log.append_text(f"[Exit-Code {rc}]\n")
                if rc != 0:
                    self.log.append_text("udisks hat abgelehnt – versuche es mit sudo …\n")
                    self._mount_sudo(n)
                else:
                    self.refresh()
            run_streaming(["udisksctl", "mount", "-b", dev], self.log, clear_first=False, on_done=done)
        else:
            self._mount_sudo(n)

    def _mount_sudo(self, n):
        dev = n["path"]
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", n.get("label") or n.get("name") or os.path.basename(dev))
        target = f"/mnt/{name}"
        opts = []
        if (n.get("fstype") or "") in ("vfat", "exfat", "ntfs"):
            opts = ["-o", f"uid={os.getuid()},gid={os.getgid()}"]
        script = f"mkdir -p {shlex.quote(target)} && mount {' '.join(opts)} {shlex.quote(dev)} {shlex.quote(target)}"
        self._root(["bash", "-c", script], f"mount {dev} {target}")

    def umount(self):
        n = self.current
        dev = n["path"]
        if which("udisksctl"):
            self.log.set_text(f"$ udisksctl unmount -b {dev}\n")

            def done(rc):
                self.log.append_text(f"[Exit-Code {rc}]\n")
                if rc != 0:
                    self.log.append_text("udisks hat abgelehnt – versuche es mit sudo …\n")
                    self._root(["umount", dev])
                else:
                    self.refresh()
            run_streaming(["udisksctl", "unmount", "-b", dev], self.log, clear_first=False, on_done=done)
        else:
            self._root(["umount", dev])

    def rename(self):
        n = self.current
        fs = n.get("fstype")
        builder, maxlen, need_unmounted, pkg = LABEL_TOOLS[fs]
        mps = _mounts(n)
        if need_unmounted and mps:
            show_info(self, "Erst aushängen", f"{fs} kann nur ausgehängt umbenannt werden.")
            return
        dlg = RenameDialog(self, n["path"], n.get("label"), maxlen)
        if dlg.exec() != QDialog.Accepted:
            return
        label = dlg.entry.text().strip()
        if not LABEL_RE.match(label):
            show_warning(self, "Ungültige Bezeichnung",
                         "Erlaubt sind Buchstaben, Ziffern, Leerzeichen und _ . -")
            return
        cmd = builder(n["path"], label, mps[0] if mps else None)
        if self._need_tool(cmd[0], pkg):
            self._root(cmd)

    def check_fs(self):
        n = self.current
        cmd = CHECK_TOOLS[n["fstype"]] + [n["path"]]
        if self._need_tool(cmd[0], "e2fsprogs / btrfs-progs / dosfstools / exfatprogs"):
            self._root(cmd)

    def eject(self):
        n = self.current
        disk = n if n.get("type") == "disk" else self.parents.get(n["path"])
        if not which("udisksctl"):
            show_error(self, "Werkzeug fehlt", "udisksctl ist nicht installiert.\n\nsudo pacman -S udisks2")
            return
        if not ask_confirm(self, "Sicher entfernen",
                           f"{disk['path']} aushängen und ausschalten?", "Sicher entfernen"):
            return
        parts = [c["path"] for c in (disk.get("children") or []) if _mounts(c)]
        if _mounts(disk):
            parts.append(disk["path"])
        steps = [{"cmd": ["udisksctl", "unmount", "-b", p], "needs_sudo": False,
                  "label": f"udisksctl unmount -b {p}"} for p in parts]
        steps.append({"cmd": ["udisksctl", "power-off", "-b", disk["path"]], "needs_sudo": False,
                      "label": f"udisksctl power-off -b {disk['path']}"})
        run_sequence(steps, self.log, on_all_done=lambda: (
            self.app.set_status(f"{disk['path']} kann jetzt entfernt werden."), self.refresh()))

    def format(self):
        n = self.current
        name = os.path.basename(n["path"])
        dlg = FormatDialog(self, n["path"], name, fmt_bytes(n.get("size")))
        if dlg.exec() != QDialog.Accepted or dlg.confirm.text().strip() != name:
            return
        builder, maxlen, pkg = FORMATS[dlg.fs.currentData()]
        label = dlg.label.text().strip()
        if label and not LABEL_RE.match(label):
            show_warning(self, "Ungültige Bezeichnung", "Erlaubt sind Buchstaben, Ziffern, Leerzeichen und _ . -")
            return
        # Sicherheitsnetz: direkt vor dem Formatieren alles darunter prüfen (auch Partitionen, LUKS, Swap)
        blockers = format_blockers(n["path"])
        if blockers:
            show_error(self, "Formatieren nicht möglich", "\n".join(blockers) + "\n\nBitte erst aushängen bzw. "
                       "schließen.")
            return
        cmd = builder(n["path"], label)
        if self._need_tool(cmd[0], pkg):
            self._root(cmd, then=lambda rc: self.app.set_status(
                f"Formatieren von {n['path']} {'abgeschlossen' if rc == 0 else 'fehlgeschlagen'}."))


# --------------------------------------------------------------------------
# Modul: Speicher (Belegung je Festplatte, Ordner-Analyse, Aufräumen)
# --------------------------------------------------------------------------

class BarList(QWidget):
    """Liste mit Größenbalken (eine Farbe = Menge). Doppelklick auf Ordner öffnet ihn."""
    opened = Signal(str)
    ROW = 40

    def __init__(self):
        super().__init__()
        self.rows = []      # (name, size, path, is_dir)
        self.total = 0
        self.hover = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_rows([], 0)

    def set_rows(self, rows, total):
        self.rows, self.total, self.hover = rows, max(total, 1), -1
        self.setFixedHeight(max(len(rows), 1) * self.ROW)
        self.update()

    def _row_at(self, y):
        i = int(y // self.ROW)
        return i if 0 <= i < len(self.rows) else -1

    def mouseMoveEvent(self, e):
        i = self._row_at(e.position().y())
        if i != self.hover:
            self.hover = i
            if i >= 0:
                name, size, path, is_dir = self.rows[i]
                self.setToolTip(f"{path}\n{fmt_bytes(size)} · {size / self.total * 100:.1f} %"
                                + ("\nDoppelklick zum Öffnen" if is_dir else ""))
                self.setCursor(Qt.PointingHandCursor if is_dir else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseDoubleClickEvent(self, e):
        i = self._row_at(e.position().y())
        if i >= 0 and self.rows[i][3]:
            self.opened.emit(self.rows[i][2])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        if not self.rows:
            p.setPen(QColor(COLORS["muted"]))
            p.setFont(QFont(FONTS["sans"], 10))
            p.drawText(QRectF(0, 0, w, self.ROW), Qt.AlignLeft | Qt.AlignVCenter,
                       "Noch keine Analyse – Ordner wählen und „Analysieren“ klicken.")
            return
        name_font = QFont(FONTS["mono"], 10)
        val_font = QFont(FONTS["mono"], 10)
        val_font.setWeight(QFont.DemiBold)
        biggest = max(r[1] for r in self.rows) or 1
        for i, (name, size, path, is_dir) in enumerate(self.rows):
            y = i * self.ROW
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(COLORS["bg3"]))
                p.drawRoundedRect(QRectF(-6, y + 1, w + 12, self.ROW - 2), 6, 6)
            p.setFont(name_font)
            p.setPen(QColor(COLORS["ink"]))
            label = (name + "/") if is_dir else name
            p.drawText(QRectF(0, y + 2, w - 170, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(label, Qt.ElideMiddle, int(w - 180)))
            p.setFont(val_font)
            p.drawText(QRectF(w - 170, y + 2, 170, 20), Qt.AlignRight | Qt.AlignVCenter,
                       f"{fmt_bytes(size)}   {size / self.total * 100:4.1f} %")
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["bg3"]))
            p.drawRoundedRect(QRectF(0, y + 26, w, 6), 3, 3)
            p.setBrush(QColor(COLORS["accent"] if is_dir else COLORS["muted"]))
            bw = max(w * size / biggest, 4) if size else 0
            if bw:
                p.drawRoundedRect(QRectF(0, y + 26, bw, 6), 3, 3)
        p.end()


def _du_size(path, use_sudo, timeout=120):
    """Belegter Platz in Bytes; None bei Zeitüberschreitung."""
    cmd = (["sudo", "-n"] if use_sudo else []) + ["du", "-sxB1", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return int(r.stdout.split()[0]) if r.stdout.strip() else 0
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return 0


_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}


class StorageTab(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.busy = False
        self.path = None
        self.history = []

        self.badge = StatusBadge("off", "Lade …")
        self.lay.addLayout(page_header("Speicherbelegung", self.badge,
                                       Button("↻", "icon", self.refresh_fs, "Neu einlesen")))

        fsp = Panel("Festplatten & Partitionen")
        self.fs_box = QVBoxLayout()
        self.fs_box.setSpacing(12)
        fsp.body.addLayout(self.fs_box)
        self.lay.addWidget(fsp)

        ana = Panel("Was belegt den Platz?")
        top = QHBoxLayout()
        top.setSpacing(8)
        self.mp_cb = QComboBox()
        self.mp_cb.setMinimumWidth(220)
        top.addLayout(Field("Festplatte / Ordner", self.mp_cb))
        top.addStretch(1)
        right = QVBoxLayout()
        right.addStretch(1)
        rb = QHBoxLayout()
        rb.setSpacing(8)
        self.cb_root = QCheckBox("Mit root-Rechten (genauer)")
        self.cb_root.setChecked(True)
        rb.addWidget(self.cb_root)
        self.b_analyze = Button("Analysieren", "primary", lambda: self.analyze(self.mp_cb.currentData()))
        rb.addWidget(self.b_analyze)
        right.addLayout(rb)
        top.addLayout(right)
        ana.body.addLayout(top)

        crumb = QHBoxLayout()
        crumb.setSpacing(8)
        self.b_back = Button("← Zurück", "ghost", self.back)
        self.b_back.setEnabled(False)
        crumb.addWidget(self.b_back)
        self.crumb = Label("", "Value")
        crumb.addWidget(self.crumb, 1)
        self.crumb_total = Label("", "Muted")
        crumb.addWidget(self.crumb_total)
        ana.body.addLayout(crumb)
        self.bars = BarList()
        self.bars.opened.connect(self._open)
        ana.body.addWidget(self.bars)
        ana.body.addWidget(Label("Petrol = Ordner (Doppelklick öffnet ihn), grau = einzelne Dateien. "
                                 "Die größten 25 Einträge werden gezeigt.", "Hint", wrap=True))
        self.lay.addWidget(ana)

        self.b_measure = Button("Größen ermitteln", "ghost", self.scan_cleanup)
        cl = Panel("Typische Platzfresser & Aufräumen", [self.b_measure])
        # Status wie beim Virenscan: läuft/fertig · was gerade gemessen wird · Fortschritt
        self.cl_status = QWidget()
        stl = QVBoxLayout(self.cl_status)
        stl.setContentsMargins(0, 0, 0, 6)
        stl.setSpacing(6)
        srow = QHBoxLayout()
        srow.setSpacing(12)
        self.cl_badge = StatusBadge("off", "Nicht aktiv")
        srow.addWidget(self.cl_badge)
        self.cl_time = Label("", "Value")
        srow.addWidget(self.cl_time)
        srow.addStretch(1)
        stl.addLayout(srow)
        self.cl_bar = ProgressBar()
        stl.addWidget(self.cl_bar)
        self.cl_info = Label("", "Hint", wrap=True)
        stl.addWidget(self.cl_info)
        self.cl_status.hide()
        cl.body.addWidget(self.cl_status)
        self.cl_running = False
        self.cl_timer = QTimer(self)
        self.cl_timer.timeout.connect(self._cl_tick)
        self.cl_grid = QGridLayout()
        self.cl_grid.setHorizontalSpacing(16)
        self.cl_grid.setVerticalSpacing(10)
        self.cl_grid.setColumnStretch(0, 1)
        cl.body.addLayout(self.cl_grid)
        self.lay.addWidget(cl)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        self._build_cleanup_rows()
        self.refresh_fs()

    # ---- Dateisysteme ---------------------------------------------------

    def refresh_fs(self):
        args = ["df", "-B1", "--output=source,fstype,size,used,avail,target",
                "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs", "-x", "overlay", "-x", "efivarfs"]

        def done(rc, out, err):
            while self.fs_box.count():
                w = self.fs_box.takeAt(0).widget()
                if w:
                    w.deleteLater()
            prev = self.mp_cb.currentData()
            self.mp_cb.clear()
            seen = set()
            rows = []
            for line in out.splitlines()[1:]:
                p = line.split(None, 5)
                if len(p) < 6 or not p[0].startswith("/dev/") or p[0] in seen:
                    continue
                seen.add(p[0])
                rows.append((p[0], p[1], int(p[2]), int(p[3]), p[5]))
            for src, fstype, size, used, target in rows:
                self.fs_box.addWidget(UsageBar(f"{target}   {src} · {fstype}", used / 1073741824,
                                               size / 1073741824))
                self.mp_cb.addItem(f"{target}  ({fmt_bytes(used)} belegt)", target)
            if not rows:
                self.fs_box.addWidget(Label("Keine Dateisysteme gefunden.", "Muted"))
            self.mp_cb.addItem("Home-Ordner (~)", os.path.expanduser("~"))
            i = self.mp_cb.findData(prev)
            if i >= 0:
                self.mp_cb.setCurrentIndex(i)
            root = next((r for r in rows if r[4] == "/"), rows[0] if rows else None)
            if root:
                pct = root[3] / root[2] * 100 if root[2] else 0
                tone = "danger" if pct >= 90 else "warn" if pct >= 80 else "ok"
                self.badge.set(tone, f"System: {fmt_bytes(root[2] - root[3])} frei ({100 - pct:.0f} %)")

        run_capture_async(args, done)

    # ---- Ordner-Analyse ---------------------------------------------------

    def analyze(self, path, push=False):
        if not path or self.busy:
            return
        use_sudo = self.cb_root.isChecked()
        if use_sudo and not self.app.priv.ensure(self):
            use_sudo = False
        if push and self.path:
            self.history.append(self.path)
        self.path = path
        self.busy = True
        self.b_analyze.setEnabled(False)
        self.b_analyze.setText("Analysiere …")
        self.crumb.setText(path)
        self.crumb_total.setText("wird berechnet …")
        self.b_back.setEnabled(bool(self.history))
        self.app.set_status(f"Analysiere {path} … (kann bei großen Platten etwas dauern)")

        def worker():
            cmd = (["sudo", "-n"] if use_sudo else []) + ["du", "-x", "-B1", "-d1", "-a", path]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True)
                out = r.stdout
            except Exception as e:
                out = ""
                ui(lambda m=f"error: {e}\n": self.log.append_text(m))
            total, rows = 0, []
            norm = os.path.normpath(path)
            for line in out.splitlines():
                if "\t" not in line:
                    continue
                size_s, p = line.split("\t", 1)
                try:
                    size = int(size_s)
                except ValueError:
                    continue
                if os.path.normpath(p) == norm:
                    total = size
                else:
                    rows.append((os.path.basename(p) or p, size, p, os.path.isdir(p) and not os.path.islink(p)))
            rows.sort(key=lambda r: -r[1])
            if len(rows) > 25:
                rest = sum(r[1] for r in rows[25:])
                rows = rows[:25] + [(f"… {len(rows) - 25} weitere", rest, path, False)]
            ui(lambda: self._show(path, rows, total))

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, path, rows, total):
        self.busy = False
        self.b_analyze.setEnabled(True)
        self.b_analyze.setText("Analysieren")
        self.bars.set_rows(rows, total)
        self.crumb_total.setText(f"gesamt {fmt_bytes(total)}")
        self.app.set_status(f"Analyse von {path} fertig.")

    def _open(self, path):
        self.analyze(path, push=True)

    def back(self):
        if self.history:
            prev = self.history.pop()
            self.path = None
            self.analyze(prev)

    # ---- Aufräumen --------------------------------------------------------

    def _build_cleanup_rows(self):
        home = os.path.expanduser("~")
        self.cleanup = [
            {"key": "pkgcache", "name": "Pacman-Paketcache", "path": "/var/cache/pacman/pkg",
             "action": "Alte Versionen löschen", "hint": "behält die 2 neuesten Versionen je Paket",
             "info": "pacman hebt jede heruntergeladene Paketversion in /var/cache/pacman/pkg auf – über Monate "
                     "werden das schnell mehrere GB.\n\n„Alte Versionen löschen“ führt paccache -rk2 aus: je Paket "
                     "bleiben die 2 neuesten Versionen liegen (für ein Zurückstufen, falls ein Update Probleme macht). "
                     "Zusätzlich entfernt paccache -ruk0 alle Dateien von Paketen, die gar nicht mehr installiert sind. "
                     "Ohne paccache (Paket pacman-contrib) nutzt Tuxdex pacman -Sc: dann bleibt nur die installierte "
                     "Version im Cache.\n\nBraucht root. Installierte Programme bleiben unberührt."},
            {"key": "orphans", "name": "Verwaiste Pakete", "path": "pacman -Qdtq",
             "action": "Entfernen", "hint": "Abhängigkeiten, die nichts mehr braucht",
             "info": "Pakete, die einmal als Abhängigkeit eines anderen Programms installiert wurden, das es nicht "
                     "mehr gibt (pacman -Qdtq).\n\n„Entfernen“ zeigt vorher die Liste und löscht sie dann mit "
                     "pacman -Rns – samt ihrer eigenen, ebenfalls unnötigen Abhängigkeiten und Konfigurationsdateien.\n\n"
                     "Selbst installierte Programme sind nie dabei. Braucht root."},
            {"key": "journal", "name": "System-Logs (Journal)", "path": "/var/log/journal",
             "action": "Auf 200 MB kürzen", "hint": "",
             "info": "Das System-Protokoll (systemd-journald) sammelt Meldungen aller Dienste und des Kernels.\n\n"
                     "„Auf 200 MB kürzen“ führt journalctl --vacuum-size=200M aus: die ältesten Einträge werden "
                     "gelöscht, bis das Protokoll noch 200 MB belegt. Neue Meldungen werden weiter geschrieben.\n\n"
                     "Dauerhaft begrenzen: Sicherheit → Checkliste → System-Protokoll. Braucht root."},
            {"key": "trash", "name": "Papierkorb", "path": os.path.join(home, ".local/share/Trash"),
             "action": "Leeren", "hint": "",
             "info": "Dateien, die du im Dateimanager gelöscht hast, landen zuerst hier (~/.local/share/Trash).\n\n"
                     "„Leeren“ löscht sie endgültig – sie lassen sich danach nicht mehr wiederherstellen."},
            {"key": "flatpak", "name": "Flatpak (System + Benutzer)", "path": "/var/lib/flatpak",
             "action": "Unbenutzte entfernen", "hint": "entfernt ungenutzte Laufzeiten",
             "info": "Flatpak-Apps brauchen Laufzeiten (z. B. GNOME- oder KDE-Plattform). Nach Updates oder dem "
                     "Deinstallieren von Apps bleiben alte Versionen liegen.\n\n„Unbenutzte entfernen“ führt "
                     "flatpak uninstall --unused aus. Apps und ihre Daten "
                     "bleiben erhalten."},
            {"key": "usercache", "name": "Benutzer-Cache (~/.cache)", "path": os.path.join(home, ".cache"),
             "action": None, "hint": "nur Anzeige – Programme legen hier Zwischendaten ab",
             "info": "Browser, Thumbnail-Vorschauen, Spiele-Launcher und viele andere Programme legen in ~/.cache "
                     "Zwischendaten ab. Löschen ist grundsätzlich möglich – die Programme bauen den Cache neu auf.\n\n"
                     "Tuxdex zeigt hier nur die Größe, weil laufende Programme beim Löschen durcheinanderkommen können. "
                     "Welcher Ordner groß ist, siehst du oben in der Speicher-Übersicht."},
            {"key": "paru", "name": "AUR-Build-Cache (paru)", "path": os.path.join(home, ".cache/paru"),
             "action": None, "hint": "nur Anzeige",
             "info": "paru baut AUR-Pakete in ~/.cache/paru/clone und hebt Quellcode und fertige Pakete auf.\n\n"
                     "Zum Aufräumen im Terminal: paru -Sc (fragt nach, was gelöscht wird)."},
            {"key": "docker", "name": "Docker", "path": "/var/lib/docker", "action": None, "hint": "nur Anzeige",
             "info": "Docker speichert Images, Container und Volumes in /var/lib/docker.\n\nZum Aufräumen im "
                     "Terminal: docker system prune (entfernt gestoppte Container, ungenutzte Netzwerke und "
                     "Images ohne Namen; mit -a auch alle unbenutzten Images). Volumes bleiben, außer mit --volumes."},
        ]
        for i, c in enumerate(self.cleanup):
            txt = QVBoxLayout()
            txt.setSpacing(0)
            head = QHBoxLayout()
            head.setSpacing(6)
            head.addWidget(Label(c["name"]))
            if c.get("info"):
                head.addWidget(info_button(c["name"], c["info"]))
            head.addStretch(1)
            txt.addLayout(head)
            txt.addWidget(Label(short_path(c["path"]) + (f" · {c['hint']}" if c["hint"] else ""), "Hint"))
            self.cl_grid.addLayout(txt, i, 0)
            c["size_lbl"] = Label("—", "Value")
            c["size_lbl"].setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            c["size_lbl"].setMinimumWidth(110)
            self.cl_grid.addWidget(c["size_lbl"], i, 1)
            if c["action"]:
                b = Button(c["action"], "ghost", lambda _=False, k=c["key"]: self.clean(k))
                c["btn"] = b
                self.cl_grid.addWidget(b, i, 2)

    def scan_cleanup(self):
        if self.cl_running:
            return
        use_sudo = self.app.priv.is_authenticated_nonblocking()
        for c in self.cleanup:
            c["size_lbl"].setText("…")
        self.cl_running = True
        self.cl_started = time.time()
        self.cl_active = {}                # key -> Name, solange gemessen wird
        self.cl_done = 0
        self.cl_sizes = {}
        self.b_measure.setEnabled(False)
        self.b_measure.setText("Ermittle …")
        self.cl_status.show()
        self.cl_badge.set("ok", "Läuft")
        self._cl_tick()
        self.cl_timer.start(500)

        def measure(c):
            k = c["key"]
            if k == "orphans":
                r = subprocess.run(["pacman", "-Qdtq"], capture_output=True, text=True, timeout=60) \
                    if which("pacman") else None
                pkgs = r.stdout.split() if r and r.returncode == 0 else []
                size = 0
                if pkgs:
                    qi = subprocess.run(["pacman", "-Qi"] + pkgs, capture_output=True, text=True, timeout=60,
                                        env={**os.environ, "LC_ALL": "C"}).stdout
                    for m in re.finditer(r"^Installed Size\s*:\s*([\d.,]+)\s*(\S+)", qi, re.M):
                        size += float(m.group(1).replace(",", ".")) * _SIZE_UNITS.get(m.group(2), 1)
                return size, (f"{len(pkgs)} Pakete · {fmt_bytes(size)}" if pkgs else "keine")
            paths = ["/var/lib/flatpak", os.path.expanduser("~/.local/share/flatpak")] if k == "flatpak" \
                else [c["path"]]
            paths = [p for p in paths if os.path.exists(p)]
            if not paths:
                return 0, "—" if k == "flatpak" else "nicht vorhanden"
            sizes = [_du_size(p, use_sudo) for p in paths]
            if any(x is None for x in sizes):
                return 0, "zu viele Dateien"
            return sum(sizes), fmt_bytes(sum(sizes))

        def job(c):
            ui(lambda: self.cl_active.__setitem__(c["key"], c["name"]))
            try:
                res = measure(c)
            except Exception:
                res = (0, "Fehler")
            ui(lambda: self._cl_result(c, *res))

        def worker():
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(job, self.cleanup))
            ui(self._cl_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _cl_result(self, c, size, txt):
        c["size_lbl"].setText(txt)
        self.cl_active.pop(c["key"], None)
        self.cl_sizes[c["key"]] = size
        self.cl_done += 1
        self._cl_tick()

    def _cl_tick(self):
        el = int(time.time() - self.cl_started)
        n = len(self.cleanup)
        self.cl_time.setText(f"{self.cl_done} / {n}  ·  {el // 60}:{el % 60:02d}")
        self.cl_bar.set(self.cl_done / n * 100, f"{self.cl_done / n * 100:.0f} %")
        if self.cl_running:
            self.cl_info.setText("Misst gerade: " + (", ".join(self.cl_active.values()) or "…")
                                 + " – große Ordner mit vielen Dateien brauchen etwas.")

    def _cl_finished(self):
        self.cl_running = False
        self.cl_timer.stop()
        self._cl_tick()
        self.b_measure.setEnabled(True)
        self.b_measure.setText("Größen ermitteln")
        el = int(time.time() - self.cl_started)
        self.cl_badge.set("ok", "Fertig")
        self.cl_info.setText(f"Alle {len(self.cleanup)} Bereiche gemessen · Dauer {el // 60}:{el % 60:02d}")

    def clean(self, key):
        home = os.path.expanduser("~")
        if key == "pkgcache":
            if which("paccache"):
                cmd, label, sudo = ["bash", "-c", "paccache -rk2 && paccache -ruk0"], "paccache -rk2 && paccache -ruk0", True
            else:
                cmd, label, sudo = ["pacman", "-Sc", "--noconfirm"], "pacman -Sc", True
            text = "Alte Paketversionen aus dem Pacman-Cache löschen?"
        elif key == "orphans":
            r = subprocess.run(["pacman", "-Qdtq"], capture_output=True, text=True)
            pkgs = r.stdout.split()
            if not pkgs:
                show_info(self, "Nichts zu tun", "Es gibt keine verwaisten Pakete.")
                return
            cmd, label, sudo = ["pacman", "-Rns", "--noconfirm", "--"] + pkgs, "pacman -Rns " + quoted(pkgs), True
            text = "Diese verwaisten Pakete entfernen?\n\n" + "\n".join(pkgs[:30]) + ("\n…" if len(pkgs) > 30 else "")
        elif key == "journal":
            cmd, label, sudo = ["journalctl", "--vacuum-size=200M"], "journalctl --vacuum-size=200M", True
            text = "System-Logs auf 200 MB kürzen?"
        elif key == "trash":
            t = shlex.quote(os.path.join(home, ".local/share/Trash"))
            cmd = ["bash", "-c", f"find {t}/files {t}/info -mindepth 1 -delete 2>/dev/null; true"]
            label, sudo = "Papierkorb leeren", False
            text = "Papierkorb endgültig leeren?"
        elif key == "flatpak":
            cmd, label, sudo = ["flatpak", "uninstall", "--unused", "-y"], "flatpak uninstall --unused -y", False
            text = "Ungenutzte Flatpak-Laufzeiten entfernen?"
        else:
            return
        if not ask_confirm(self, "Aufräumen", text, "Ausführen", danger=key in ("trash", "orphans")):
            return
        if sudo and not self.app.priv.ensure(self):
            return
        self.log.set_text(f"$ {'sudo ' if sudo else ''}{label}\n")

        def done(rc):
            self.log.append_text(f"[Exit-Code {rc}]\n")
            self.refresh_fs()
            self.scan_cleanup()

        run_streaming(cmd, self.log, needs_sudo=sudo, clear_first=False, on_done=done)


# --------------------------------------------------------------------------
# Modul: Taskmanager (Leistung + Prozesse, liest direkt aus /proc)
# --------------------------------------------------------------------------

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
N_CPU = os.cpu_count() or 1


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return default


def cpu_times():
    """(gesamt, leerlauf) in Jiffies aus /proc/stat."""
    parts = _read("/proc/stat").split("\n", 1)[0].split()[1:]
    vals = [int(x) for x in parts[:8]] if parts else [0] * 8
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def meminfo():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        try:
            info[k] = int(v.split()[0]) * 1024
        except (ValueError, IndexError):
            pass
    return info


_USERS = {}


def _user(uid):
    if uid not in _USERS:
        try:
            _USERS[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _USERS[uid] = str(uid)
    return _USERS[uid]


def read_processes():
    """{pid: dict(name, user, uid, state, ticks, rss, cmd, nice, threads)}"""
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        stat = _read(f"/proc/{pid}/stat")
        if not stat:
            continue
        try:
            rpar = stat.rindex(")")
            name = stat[stat.index("(") + 1:rpar]
            f = stat[rpar + 2:].split()
            state, ticks = f[0], int(f[11]) + int(f[12])
            nice, threads = int(f[16]), int(f[17])
            rss = int(f[21]) * PAGE_SIZE
        except (ValueError, IndexError):
            continue
        try:
            uid = os.stat(f"/proc/{pid}").st_uid
        except OSError:
            continue
        cmd = _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
        procs[pid] = {"name": name, "uid": uid, "user": _user(uid), "state": state, "ticks": ticks,
                      "rss": rss, "cmd": cmd or f"[{name}]", "nice": nice, "threads": threads}
    return procs


STATES = {"R": "läuft", "S": "schläft", "D": "wartet (E/A)", "Z": "Zombie", "T": "angehalten",
          "t": "angehalten", "I": "Leerlauf", "X": "beendet"}


class NumItem(QTableWidgetItem):
    """Tabellenzelle, die nach Zahlwert statt Text sortiert."""

    def __init__(self, text, value):
        super().__init__(text)
        self.setData(Qt.UserRole, value)

    def __lt__(self, other):
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class StatTile(QFrame):
    """Kennzahl-Kachel: Label, große Zahl, Zusatzzeile, Verlauf (eine Serie → keine Legende)."""
    HISTORY = 60

    def __init__(self, title, color=None, compact=False, maxval=100):
        super().__init__()
        self.setObjectName("Panel")
        self.color = QColor(color or COLORS["accent"])
        self.values = []
        self.maxval = maxval   # None = automatisch nach größtem Wert skalieren
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        lay.addWidget(Label(title.upper(), "FieldLabel"))
        self.big = Label("—", "BigValue")
        lay.addWidget(self.big)
        self.sub = Label("", "Hint")
        lay.addWidget(self.sub)
        self.spark = _Spark(self)
        if compact:
            self.spark.setFixedHeight(26)
            self.big.setObjectName("MidValue")
        lay.addWidget(self.spark)

    def make_clickable(self, on_click):
        """Kachel öffnet per Klick (oder Enter/Leertaste) die Detail-Ansicht."""
        self._on_click = on_click
        self.setProperty("clickable", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setToolTip("Anklicken für Details")

    def set_selected(self, on):
        self.setProperty("selected", on)
        repolish(self)

    def mousePressEvent(self, e):
        if getattr(self, "_on_click", None) and e.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if getattr(self, "_on_click", None) and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._on_click()
            return
        super().keyPressEvent(e)

    def set(self, big, sub, value=None):
        self.big.setText(big)
        self.sub.setText(sub)
        if value is not None:
            self.values = (self.values + [max(0.0, float(value))])[-self.HISTORY:]
            self.spark.setToolTip(f"Verlauf der letzten {len(self.values) * 2} s · aktuell {big}")
            self.spark.update()

    def scaled(self):
        top = self.maxval or max(max(self.values, default=0), 1)
        return [min(100.0, v / top * 100) for v in self.values]


class _Spark(QWidget):
    def __init__(self, tile):
        super().__init__()
        self.tile = tile
        self.setFixedHeight(40)

    def paintEvent(self, e):
        vals = self.tile.scaled()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor(COLORS["line"]))
        p.drawLine(0, h - 1, w, h - 1)
        if len(vals) < 2:
            p.end()
            return
        step = w / (StatTile.HISTORY - 1)
        x0 = w - step * (len(vals) - 1)
        pts = [(x0 + i * step, h - 2 - (v / 100) * (h - 6)) for i, v in enumerate(vals)]
        area = QPainterPath()
        area.moveTo(pts[0][0], h - 1)
        for x, y in pts:
            area.lineTo(x, y)
        area.lineTo(pts[-1][0], h - 1)
        fill = QColor(self.tile.color)
        fill.setAlpha(40)
        p.fillPath(area, fill)
        line = QPainterPath()
        line.moveTo(*pts[0])
        for x, y in pts[1:]:
            line.lineTo(x, y)
        pen = QPen(self.tile.color, 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPath(line)
        p.setBrush(self.tile.color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(pts[-1][0] - 3, pts[-1][1] - 3, 6, 6))
        p.end()


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            clear_layout(it.layout())


class DetailPanel(QFrame):
    """Details zur angeklickten Leistungs-Kachel: Kennzahlen oben, Eigenschaften darunter."""
    COLS = 4

    def __init__(self, on_close, on_device):
        super().__init__()
        self.setObjectName("Panel")
        self._keys = None
        self._cells = {}
        self._bars = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = Label("", "PanelTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.dev = QComboBox()
        self.dev.setMinimumWidth(220)
        self.dev.hide()
        self.dev.currentIndexChanged.connect(lambda _: on_device(self.dev.currentData()))
        head.addWidget(self.dev)
        head.addWidget(Button("Schließen", "ghost", on_close, "Details ausblenden (oder Kachel erneut anklicken)"))
        outer.addLayout(head)
        self.stats = QGridLayout()
        self.stats.setHorizontalSpacing(24)
        self.stats.setVerticalSpacing(12)
        outer.addLayout(self.stats)
        self.sep = QFrame()
        self.sep.setFixedHeight(1)
        self.sep.setStyleSheet(f"background: {COLORS['line']};")
        outer.addWidget(self.sep)
        self.kv = QGridLayout()
        self.kv.setHorizontalSpacing(24)
        self.kv.setVerticalSpacing(6)
        outer.addLayout(self.kv)
        self.bar_title = Label("", "FieldLabel")
        outer.addWidget(self.bar_title)
        self.bar_box = QVBoxLayout()
        self.bar_box.setSpacing(10)
        outer.addLayout(self.bar_box)
        self.hint = Label("", "Hint", wrap=True)
        outer.addWidget(self.hint)

    def set_devices(self, items, current):
        """items: [(Anzeige, Schlüssel)] – Auswahl nur bei mehreren Geräten."""
        self.dev.blockSignals(True)
        if [self.dev.itemData(i) for i in range(self.dev.count())] != [k for _, k in items]:
            self.dev.clear()
            for text, key in items:
                self.dev.addItem(text, key)
        i = self.dev.findData(current)
        if i >= 0:
            self.dev.setCurrentIndex(i)
        self.dev.setVisible(len(items) > 1)
        self.dev.blockSignals(False)

    def loading(self, title):
        self.title.setText(title)
        self._keys = None
        clear_layout(self.stats)
        clear_layout(self.kv)
        clear_layout(self.bar_box)
        self._bars = []
        self.bar_title.hide()
        self.hint.setText("Wird ermittelt …")
        self.dev.hide()

    def show_data(self, title, stats, kv, bars=(), bar_title="", hint=""):
        self.title.setText(title)
        keys = ([k for k, _ in stats], [k for k, _ in kv], len(bars))
        if keys != self._keys:
            self._keys = keys
            clear_layout(self.stats)
            clear_layout(self.kv)
            clear_layout(self.bar_box)
            self._cells = {}
            for i, (k, _) in enumerate(stats):
                box = QVBoxLayout()
                box.setSpacing(2)
                box.addWidget(Label(k.upper(), "FieldLabel"))
                v = Label("", "MidValue")
                box.addWidget(v)
                self._cells[("s", k)] = v
                self.stats.addLayout(box, i // self.COLS, i % self.COLS)
            for c in range(self.COLS):
                self.stats.setColumnStretch(c, 1)
            for i, (k, _) in enumerate(kv):
                self.kv.addWidget(Label(k, "Muted"), i, 0, Qt.AlignTop)
                v = Label("", "KvValue", wrap=True)
                v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self._cells[("k", k)] = v
                self.kv.addWidget(v, i, 1)
            self.kv.setColumnStretch(1, 1)
            self._bars = []
            for _ in bars:
                b = UsageBar("", 0, 0, unit="B")
                self._bars.append(b)
                self.bar_box.addWidget(b)
        for k, v in stats:
            self._cells[("s", k)].setText(str(v))
        for k, v in kv:
            self._cells[("k", k)].setText(str(v))
        for b, (label, used, total) in zip(self._bars, bars):
            b.label, b.used, b.total = label, used or 0, total or 0
            b.update()
        self.sep.setVisible(bool(stats) and bool(kv))
        self.bar_title.setText(bar_title.upper())
        self.bar_title.setVisible(bool(bars) and bool(bar_title))
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


# --------------------------------------------------------------------------
# Systeminformationen (Hardware, Akku, Netzwerk, Temperaturen) – ohne root
# --------------------------------------------------------------------------

def _first_line(path):
    return _read(path).strip().split("\n", 1)[0].strip()


def cpu_model():
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model name", "hardware", "processor\t: arm")):
            name = line.split(":", 1)[1].strip()
            return re.sub(r"\s+", " ", name.replace("(R)", "").replace("(TM)", "").replace(" CPU", ""))
    return platform_machine()


def platform_machine():
    try:
        return os.uname().machine
    except Exception:
        return "unbekannt"


def cpu_mhz():
    vals = []
    base = "/sys/devices/system/cpu"
    try:
        for d in os.listdir(base):
            if re.match(r"cpu\d+$", d):
                v = _first_line(f"{base}/{d}/cpufreq/scaling_cur_freq")
                if v.isdigit():
                    vals.append(int(v) / 1000)
    except Exception:
        pass
    if not vals:
        vals = [float(l.split(":")[1]) for l in _read("/proc/cpuinfo").splitlines() if l.startswith("cpu MHz")]
    return (sum(vals) / len(vals), max(vals)) if vals else (None, None)


def cpu_threads_cores():
    threads = os.cpu_count() or 1
    cores = set()
    phys = None
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("physical id"):
            phys = line.split(":")[1].strip()
        elif line.startswith("core id"):
            cores.add((phys, line.split(":")[1].strip()))
    return (len(cores) or threads), threads


def temperatures():
    """{'cpu': °C, 'gpu': °C, ...} aus /sys/class/hwmon"""
    out = {}
    base = "/sys/class/hwmon"
    try:
        mons = os.listdir(base)
    except Exception:
        return out
    for m in mons:
        name = _first_line(f"{base}/{m}/name")
        try:
            files = sorted(f for f in os.listdir(f"{base}/{m}") if re.match(r"temp\d+_input$", f))
        except Exception:
            continue
        for f in files:
            label = _first_line(f"{base}/{m}/{f.replace('_input', '_label')}")
            try:
                t = int(_first_line(f"{base}/{m}/{f}")) / 1000
            except ValueError:
                continue
            if name in ("k10temp", "zenpower") and (label in ("Tctl", "Tdie", "") and "cpu" not in out):
                out["cpu"] = t
            elif name == "coretemp" and label.startswith("Package") and "cpu" not in out:
                out["cpu"] = t
            elif name in ("amdgpu", "nouveau", "radeon") and "gpu" not in out:
                out["gpu"] = t
            elif name == "nvme" and "nvme" not in out:
                out["nvme"] = t
            elif name in ("acpitz", "cpu_thermal") and "cpu" not in out:
                out.setdefault("_acpi", t)
    if "cpu" not in out and "_acpi" in out:
        out["cpu"] = out["_acpi"]
    out.pop("_acpi", None)
    return out


_PCI_IDS = None


def _pci_name(vendor, device):
    global _PCI_IDS
    if _PCI_IDS is None:
        _PCI_IDS = {}
        for path in ("/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids"):
            if os.path.exists(path):
                cur = None
                with open(path, errors="ignore") as f:
                    for line in f:
                        if line.startswith("#") or not line.strip():
                            continue
                        if line.startswith("C "):
                            break
                        if not line.startswith("\t"):
                            cur = line[:4].lower()
                            _PCI_IDS[cur] = line[6:].strip()
                        elif not line.startswith("\t\t") and cur:
                            _PCI_IDS[f"{cur}:{line[1:5].lower()}"] = line[7:].strip()
                break
    v = _PCI_IDS.get(vendor, vendor)
    d = _PCI_IDS.get(f"{vendor}:{device}", device)
    v = v.replace("Advanced Micro Devices, Inc. [AMD/ATI]", "AMD").replace("NVIDIA Corporation", "NVIDIA") \
         .replace("Intel Corporation", "Intel")
    return f"{v} {d}"


def gpus():
    """[{name, card, busy%(oder None), vram_used, vram_total}]"""
    res = []
    seen = set()
    base = "/sys/class/drm"
    try:
        cards = sorted(c for c in os.listdir(base) if re.match(r"card\d+$", c))
    except Exception:
        cards = []
    for c in cards:
        dev = f"{base}/{c}/device"
        vendor = _first_line(f"{dev}/vendor").replace("0x", "").lower()
        device = _first_line(f"{dev}/device").replace("0x", "").lower()
        if not vendor or (vendor, device) in seen:
            continue
        seen.add((vendor, device))
        g = {"name": _pci_name(vendor, device), "card": c, "busy": None, "vram_used": None, "vram_total": None}
        busy = _first_line(f"{dev}/gpu_busy_percent")
        if busy.isdigit():
            g["busy"] = int(busy)
        vu, vt = _first_line(f"{dev}/mem_info_vram_used"), _first_line(f"{dev}/mem_info_vram_total")
        if vu.isdigit() and vt.isdigit():
            g["vram_used"], g["vram_total"] = int(vu), int(vt)
        res.append(g)
    if which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
            for i, line in enumerate(r.stdout.strip().splitlines()):
                name, util, mu, mt = [x.strip() for x in line.split(",")]
                nv = [g for g in res if g["name"].startswith("NVIDIA")]
                g = nv[i] if i < len(nv) else {"card": f"nvidia{i}"}
                g.update({"name": "NVIDIA " + name.replace("NVIDIA ", ""), "busy": int(util),
                          "vram_used": int(mu) * 1048576, "vram_total": int(mt) * 1048576})
                if g not in res:
                    res.append(g)
        except Exception:
            pass
    return res


def batteries():
    """[{name, capacity%, status, watts, hours_left, health%, model}] + ac_online"""
    res, ac = [], None
    base = "/sys/class/power_supply"
    try:
        items = os.listdir(base)
    except Exception:
        return res, ac
    for it in items:
        p = f"{base}/{it}"
        typ = _first_line(f"{p}/type")
        if typ == "Mains":
            ac = _first_line(f"{p}/online") == "1"
        if typ != "Battery" or _first_line(f"{p}/scope") == "Device":
            continue

        def num(n):
            v = _first_line(f"{p}/{n}")
            return int(v) if v.lstrip("-").isdigit() else None
        cap = num("capacity")
        status = _first_line(f"{p}/status")
        power = num("power_now")
        if power is None and num("current_now") is not None and num("voltage_now"):
            power = num("current_now") * num("voltage_now") / 1e6
        e_now = num("energy_now") or (num("charge_now") and num("voltage_now") and num("charge_now") * num("voltage_now") / 1e6)
        e_full = num("energy_full") or (num("charge_full") and num("voltage_now") and num("charge_full") * num("voltage_now") / 1e6)
        e_design = num("energy_full_design") or (num("charge_full_design") and num("voltage_now")
                                                 and num("charge_full_design") * num("voltage_now") / 1e6)
        watts = abs(power) / 1e6 if power else None
        hours = None
        if watts and watts > 0.1 and e_now:
            if status == "Discharging":
                hours = (e_now / 1e6) / watts
            elif status == "Charging" and e_full:
                hours = max(0, (e_full - e_now) / 1e6) / watts
        res.append({"name": it, "capacity": cap, "status": status, "watts": watts, "hours": hours,
                    "health": (e_full / e_design * 100) if (e_full and e_design) else None,
                    "model": " ".join(x for x in (_first_line(f"{p}/manufacturer"),
                                                  _first_line(f"{p}/model_name")) if x)})
    return res, ac


BAT_STATUS = {"Charging": "lädt", "Discharging": "Akkubetrieb", "Full": "voll", "Not charging": "lädt nicht",
              "Unknown": "unbekannt"}


# --------------------------------------------------------------------------
# Detail-Infos für die Leistungsansicht (Kachel anklicken) – ohne root
# --------------------------------------------------------------------------

def _size_str(s):
    """'32K' / '1024K' / '16M' aus sysfs → Bytes"""
    m = re.match(r"(\d+)\s*([KMG]?)", s or "")
    if not m:
        return 0
    return int(m.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2)]


def fmt_ghz(khz):
    return f"{khz / 1e6:.2f} GHz" if khz else "—"


def fmt_uptime(secs):
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}:{h:02d}:{rem // 60:02d}:{rem % 60:02d}"


def _cmd_out(cmd, timeout=4):
    if not which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


_CPU_STATIC = None


def cpu_static():
    """Einmal ermittelte CPU-Daten: Modell, Takt, Sockel, Kerne, Caches, Virtualisierung."""
    global _CPU_STATIC
    if _CPU_STATIC is not None:
        return _CPU_STATIC
    base = "/sys/devices/system/cpu"
    info = _read("/proc/cpuinfo")
    flags = next((l.split(":", 1)[1].split() for l in info.splitlines() if l.startswith(("flags", "Features"))), [])
    sockets = {l.split(":")[1].strip() for l in info.splitlines() if l.startswith("physical id")}
    cores, threads = cpu_threads_cores()
    c0 = f"{base}/cpu0/cpufreq"
    base_khz = next((int(v) for v in (_first_line(f"{c0}/base_frequency"),
                                      _first_line(f"{c0}/amd_pstate_nominal_freq"),
                                      _first_line(f"{c0}/bios_limit")) if v.isdigit()), None)
    max_khz = _first_line(f"{c0}/cpuinfo_max_freq")
    # Caches: je (Ebene, Typ) die Summe aller unterschiedlichen Instanzen
    caches, seen = {}, set()
    try:
        cpus = [d for d in os.listdir(base) if re.match(r"cpu\d+$", d)]
    except Exception:
        cpus = []
    for c in cpus:
        cdir = f"{base}/{c}/cache"
        try:
            idx = [d for d in os.listdir(cdir) if d.startswith("index")]
        except Exception:
            continue
        for i in idx:
            lvl, typ = _first_line(f"{cdir}/{i}/level"), _first_line(f"{cdir}/{i}/type")
            shared = _first_line(f"{cdir}/{i}/shared_cpu_list")
            key = (lvl, typ, shared)
            if not lvl or key in seen:
                continue
            seen.add(key)
            name = f"L{lvl}"
            caches[name] = caches.get(name, 0) + _size_str(_first_line(f"{cdir}/{i}/size"))
    virt = "AMD-V" if "svm" in flags else "Intel VT-x" if "vmx" in flags else None
    if virt and os.path.exists("/dev/kvm"):
        virt = "KVM / " + virt
    vm = _cmd_out(["systemd-detect-virt", "--vm"]).strip()
    if not vm:
        vm = "ja" if "hypervisor" in flags else "none"
    _CPU_STATIC = {
        "model": cpu_model(), "base": base_khz, "max": int(max_khz) if max_khz.isdigit() else None,
        "sockets": len(sockets) or 1, "cores": cores, "threads": threads, "caches": caches,
        "virt": virt or "nicht unterstützt", "vm": "Nein" if vm == "none" else f"Ja ({vm})",
        "driver": _first_line(f"{c0}/scaling_driver") or "—",
    }
    return _CPU_STATIC


def cpu_live():
    c0 = "/sys/devices/system/cpu/cpu0/cpufreq"
    fnr = _read("/proc/sys/fs/file-nr").split()
    boost = _first_line("/sys/devices/system/cpu/cpufreq/boost")
    return {"governor": _first_line(f"{c0}/scaling_governor") or "—",
            "epp": _first_line(f"{c0}/energy_performance_preference") or "—",
            "handles": int(fnr[0]) if fnr and fnr[0].isdigit() else None,
            "boost": {"1": "an", "0": "aus"}.get(boost, "—")}


def fans():
    """[(Name, Chip, U/min)] aus /sys/class/hwmon"""
    out = []
    base = "/sys/class/hwmon"
    try:
        mons = sorted(os.listdir(base))
    except Exception:
        return out
    for m in mons:
        chip = _first_line(f"{base}/{m}/name")
        try:
            files = sorted(f for f in os.listdir(f"{base}/{m}") if re.match(r"fan\d+_input$", f))
        except Exception:
            continue
        for f in files:
            v = _first_line(f"{base}/{m}/{f}")
            if not v.isdigit():
                continue
            label = _first_line(f"{base}/{m}/{f.replace('_input', '_label')}")
            out.append((label or f"Lüfter {len(out)}", chip, int(v)))
    return out


_MEM_HW = None


def memory_hw():
    """Takt, Steckplätze, Bauform, Typ – aus den udev-Daten der DMI-Tabelle (lesbar ohne root)."""
    global _MEM_HW
    if _MEM_HW is not None:
        return _MEM_HW
    props = {}
    for line in _cmd_out(["udevadm", "info", "--query=property", "--path=/sys/devices/virtual/dmi/id"]).splitlines():
        k, _, v = line.partition("=")
        props[k] = v
    devs = {}
    for k, v in props.items():
        m = re.match(r"MEMORY_DEVICE_(\d+)_(\w+)$", k)
        if m:
            devs.setdefault(m.group(1), {})[m.group(2)] = v
    present = [d for d in devs.values() if d.get("PRESENT", "1") != "0" and d.get("SIZE", "0") not in ("0", "")]
    hw = {}
    if devs:
        hw["slots"] = f"{len(present)} von {props.get('MEMORY_ARRAY_NUM_DEVICES') or len(devs)}"
    if present:
        d = present[0]
        speed = d.get("CONFIGURED_SPEED_MTS") or d.get("SPEED_MTS")
        if speed:
            hw["speed"] = f"{speed} MT/s"
        for key, name in (("TYPE", "type"), ("FORM_FACTOR", "form"), ("MANUFACTURER", "vendor")):
            if d.get(key) and d[key] not in ("Unknown", "Other"):
                hw[name] = d[key]
            elif d.get(key):
                hw[name] = {"Other": "Sonstige (meist verlötet)", "Unknown": "unbekannt"}[d[key]]
    _MEM_HW = hw
    return hw


def zram_stats():
    """(Originalgröße, komprimiert) aller zram-Geräte"""
    orig = comp = 0
    try:
        devs = [d for d in os.listdir("/sys/block") if d.startswith("zram")]
    except Exception:
        devs = []
    for d in devs:
        f = _read(f"/sys/block/{d}/mm_stat").split()
        if len(f) >= 3:
            orig += int(f[0])
            comp += int(f[2])
    return (orig, comp) if devs else None


def swap_devices():
    """[(Gerät, Typ, Größe, belegt, Priorität)] aus /proc/swaps"""
    out = []
    for line in _read("/proc/swaps").splitlines()[1:]:
        f = line.split()
        if len(f) >= 5:
            out.append((f[0], f[1], int(f[2]) * 1024, int(f[3]) * 1024, f[4]))
    return out


def diskstats():
    """{Laufwerk: (gelesen B, geschrieben B, E/A-Zeit ms, Anfragen, Wartezeit ms)} physischer Laufwerke"""
    out = {}
    for line in _read("/proc/diskstats").splitlines():
        f = line.split()
        if len(f) > 13 and re.match(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|mmcblk\d+|hd[a-z]+)$", f[2]):
            out[f[2]] = (int(f[5]) * 512, int(f[9]) * 512, int(f[12]),
                         int(f[3]) + int(f[7]), int(f[6]) + int(f[10]))
    return out


_DISK_STATIC = {}


def disk_static(name):
    """Größe, Modell, Seriennummer, Typ, Partitionen eines Laufwerks (30 s zwischengespeichert)"""
    hit = _DISK_STATIC.get(name)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    res = _disk_static(name)
    _DISK_STATIC[name] = (time.time(), res)
    return res


def _disk_static(name):
    sysd = f"/sys/block/{name}"
    size = _first_line(f"{sysd}/size")
    rot = _first_line(f"{sysd}/queue/rotational")
    typ = "NVMe" if name.startswith("nvme") else "SD/eMMC" if name.startswith("mmcblk") else \
        "Festplatte (HDD)" if rot == "1" else "SSD"
    if _first_line(f"{sysd}/removable") == "1" or "usb" in os.path.realpath(sysd):
        typ += " · USB/wechselbar"
    wwn = _first_line(f"{sysd}/device/wwid") or _first_line(f"{sysd}/wwid")
    parts = []
    try:
        data = json.loads(_cmd_out(["lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,MOUNTPOINT,FSUSED,FSSIZE,SIZE",
                                    f"/dev/{name}"]) or "{}")
        stack = list(data.get("blockdevices", [{}])[0].get("children", []))
        while stack:
            c = stack.pop(0)
            stack[0:0] = c.get("children", []) or []
            parts.append({"path": c.get("path") or c.get("name"), "fs": c.get("fstype") or "",
                          "mount": c.get("mountpoint") or "", "used": c.get("fsused"),
                          "fssize": c.get("fssize"), "size": c.get("size")})
    except Exception:
        pass
    return {"size": int(size) * 512 if size.isdigit() else None,
            "model": (_first_line(f"{sysd}/device/model") or "").strip() or "—",
            "serial": _first_line(f"{sysd}/device/serial") or "—",
            "wwn": wwn or "—", "type": typ, "parts": parts,
            "system": any(p["mount"] == "/" for p in parts),
            "formatted": sum(int(p["fssize"]) for p in parts if str(p.get("fssize") or "").isdigit())}


def _hwmon_dir(dev):
    try:
        h = os.listdir(f"{dev}/hwmon")
        return f"{dev}/hwmon/{h[0]}" if h else None
    except Exception:
        return None


def _dpm(path):
    """(aktuell, höchster) Takt aus pp_dpm_sclk/mclk-Zeilen wie '1: 2100Mhz *'"""
    cur = top = None
    for line in _read(path).splitlines():
        m = re.search(r"(\d+)\s*Mhz", line, re.I)
        if m:
            v = int(m.group(1))
            top = max(top or 0, v)
            if "*" in line:
                cur = v
    return cur, top


_GPU_STATIC = {}


def gpu_static(card):
    if card in _GPU_STATIC:
        return _GPU_STATIC[card]
    dev = f"/sys/class/drm/{card}/device"
    real = os.path.realpath(dev)
    drv = os.path.basename(os.path.realpath(f"{dev}/driver")) if os.path.exists(f"{dev}/driver") else "—"

    def link(kind):
        sp, wd = _first_line(f"{dev}/{kind}_link_speed"), _first_line(f"{dev}/{kind}_link_width")
        gen = {"2.5": 1, "5.0": 2, "8.0": 3, "16.0": 4, "32.0": 5, "64.0": 6}
        m = re.match(r"([\d.]+)", sp)
        if not m or not wd:
            return None
        g = gen.get(m.group(1))
        return f"PCIe Gen {g} x{wd}" if g else f"{sp} x{wd}"
    gl = ""
    out = _cmd_out(["glxinfo", "-B"], 5)
    m = re.search(r"OpenGL core profile version string:\s*([\d.]+)", out) or \
        re.search(r"OpenGL version string:\s*([\d.]+)", out)
    if m:
        gl = m.group(1)
    vk = ""
    m = re.search(r"apiVersion\s*=\s*([\d.]+)", _cmd_out(["vulkaninfo", "--summary"], 6))
    if m:
        vk = m.group(1)
    st = {"driver": drv, "bus": os.path.basename(real) if re.match(r"[0-9a-f]{4}:", os.path.basename(real)) else "—",
          "link": link("current"), "link_max": link("max"), "gl": gl or "—", "vk": vk or "—"}
    _GPU_STATIC[card] = st
    return st


def gpu_live(g, nv_idx=0):
    """Takt, Leistung, Speicher, Video-Engines, Temperatur, Lüfter einer Grafikkarte"""
    card = g.get("card", "")
    dev = f"/sys/class/drm/{card}/device"
    d = {}
    if card.startswith("card"):
        d["clk"], d["clk_max"] = _dpm(f"{dev}/pp_dpm_sclk")
        d["mclk"], d["mclk_max"] = _dpm(f"{dev}/pp_dpm_mclk")
        if d["clk"] is None:   # Intel
            cur, top = _first_line(f"/sys/class/drm/{card}/gt_act_freq_mhz") or \
                _first_line(f"/sys/class/drm/{card}/gt_cur_freq_mhz"), _first_line(f"/sys/class/drm/{card}/gt_max_freq_mhz")
            if cur.isdigit():
                d["clk"], d["clk_max"] = int(cur), int(top) if top.isdigit() else None
        hw = _hwmon_dir(dev)
        if hw:
            for n in ("power1_average", "power1_input"):
                v = _first_line(f"{hw}/{n}")
                if v.isdigit():
                    d["power"] = int(v) / 1e6
                    break
            cap = _first_line(f"{hw}/power1_cap")
            if cap.isdigit():
                d["power_cap"] = int(cap) / 1e6
            t = _first_line(f"{hw}/temp1_input")
            if t.lstrip("-").isdigit():
                d["temp"] = int(t) / 1000
            fan = _first_line(f"{hw}/fan1_input")
            if fan.isdigit():
                d["fan"] = f"{fan} U/min"
    if g.get("name", "").startswith("NVIDIA") and which("nvidia-smi"):
        q = ("clocks.gr,clocks.max.gr,clocks.mem,clocks.max.mem,power.draw,power.limit,utilization.encoder,"
             "utilization.decoder,temperature.gpu,fan.speed,pcie.link.gen.current,pcie.link.gen.max,"
             "pcie.link.width.current,pcie.link.width.max,driver_version,pci.bus_id")
        out = _cmd_out(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"])
        rows = [[x.strip() for x in l.split(",")] for l in out.strip().splitlines()]
        nv = [x for x in rows if len(x) == 16]
        if nv:
            r = nv[min(nv_idx, len(nv) - 1)]

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            d.update({"clk": num(r[0]), "clk_max": num(r[1]), "mclk": num(r[2]), "mclk_max": num(r[3]),
                      "power": num(r[4]), "power_cap": num(r[5]), "enc": num(r[6]), "dec": num(r[7]),
                      "temp": num(r[8]), "nv_driver": r[14], "nv_bus": r[15]})
            if num(r[9]) is not None:
                d["fan"] = f"{r[9]} %"
            if r[10].isdigit():
                d["nv_link"] = f"PCIe Gen {r[10]} x{r[12]}"
                d["nv_link_max"] = f"PCIe Gen {r[11]} x{r[13]}"
    return d


def net_static(name):
    speed = _first_line(f"/sys/class/net/{name}/speed")
    return {"speed": f"{int(speed) / 1000:g} Gbit/s" if speed.isdigit() and int(speed) >= 1000
            else f"{speed} Mbit/s" if speed.isdigit() and int(speed) > 0 else "—",
            "mtu": _first_line(f"/sys/class/net/{name}/mtu") or "—",
            "mac": _first_line(f"/sys/class/net/{name}/address") or "—",
            "driver": os.path.basename(os.path.realpath(f"/sys/class/net/{name}/device/driver"))
            if os.path.exists(f"/sys/class/net/{name}/device/driver") else "—"}


def battery_extra(name):
    p = f"/sys/class/power_supply/{name}"

    def num(n):
        v = _first_line(f"{p}/{n}")
        return int(v) if v.lstrip("-").isdigit() else None
    volt = num("voltage_now")
    e_full, e_design = num("energy_full"), num("energy_full_design")
    if e_full is None and num("charge_full") and volt:
        e_full = num("charge_full") * volt / 1e6
        e_design = (num("charge_full_design") or 0) * volt / 1e6 or None
    return {"cycles": num("cycle_count"), "tech": _first_line(f"{p}/technology") or "—",
            "volt": volt / 1e6 if volt else None,
            "full": e_full / 1e6 if e_full else None, "design": e_design / 1e6 if e_design else None,
            "limit": num("charge_control_end_threshold")}


def net_counters():
    """{iface: (rx_bytes, tx_bytes)}"""
    out = {}
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            out[name.strip()] = (int(f[0]), int(f[8]))
    return out


def disk_counters():
    """(read_bytes, write_bytes) aller physischen Laufwerke"""
    rd = wr = 0
    for line in _read("/proc/diskstats").splitlines():
        f = line.split()
        if len(f) > 9 and re.match(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|mmcblk\d+|hd[a-z]+)$", f[2]):
            rd += int(f[5]) * 512
            wr += int(f[9]) * 512
    return rd, wr


def iface_kind(name):
    if name == "lo":
        return "Loopback"
    if os.path.isdir(f"/sys/class/net/{name}/wireless") or name.startswith(("wl", "wlan")):
        return "WLAN"
    if name.startswith(("wg", "tun", "tap", "ppp", "proton", "mullvad", "tailscale", "nordlynx", "vpn")):
        return "VPN"
    if name.startswith(("docker", "br-", "virbr", "veth", "vnet")):
        return "Virtuell"
    if name.startswith(("en", "eth")):
        return "LAN"
    return "Netzwerk"


def net_interfaces():
    """[{name, kind, state, ipv4[], ipv6[], mac}], gateway, dns[]"""
    ifaces, gw, dns = [], None, []
    try:
        data = json.loads(subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5).stdout)
    except Exception:
        data = []
    for it in data:
        name = it.get("ifname", "")
        if name == "lo":
            continue
        v4 = [f"{a['local']}/{a.get('prefixlen', '')}" for a in it.get("addr_info", []) if a.get("family") == "inet"]
        v6 = [a["local"] for a in it.get("addr_info", [])
              if a.get("family") == "inet6" and a.get("scope") == "global"]
        ifaces.append({"name": name, "kind": iface_kind(name), "state": it.get("operstate", "?"),
                       "ipv4": v4, "ipv6": v6, "mac": it.get("address", "")})
    try:
        routes = json.loads(subprocess.run(["ip", "-j", "route", "show", "default"], capture_output=True,
                                           text=True, timeout=5).stdout)
        if routes:
            gw = f"{routes[0].get('gateway', '—')} über {routes[0].get('dev', '?')}"
    except Exception:
        pass
    if which("resolvectl"):
        r = subprocess.run(["resolvectl", "dns"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2:
                dns += [x for x in parts[1].split() if x not in dns]
    if not dns:
        dns = [l.split()[1] for l in _read("/etc/resolv.conf").splitlines() if l.startswith("nameserver")]
    return ifaces, gw, dns


def internet_route_dev():
    """Über welche Schnittstelle geht der Internetverkehr gerade? (inkl. Policy-Routing von VPNs)"""
    try:
        data = json.loads(subprocess.run(["ip", "-j", "route", "get", "1.1.1.1"], capture_output=True,
                                         text=True, timeout=5).stdout or "[]")
        return data[0].get("dev") if data else None
    except Exception:
        return None


DNS_PROVIDERS = {
    "1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "1.1.1.2": "Cloudflare", "1.1.1.3": "Cloudflare",
    "2606:4700:4700::1111": "Cloudflare", "2606:4700:4700::1001": "Cloudflare",
    "8.8.8.8": "Google", "8.8.4.4": "Google", "2001:4860:4860::8888": "Google", "2001:4860:4860::8844": "Google",
    "9.9.9.9": "Quad9", "149.112.112.112": "Quad9", "2620:fe::fe": "Quad9", "2620:fe::9": "Quad9",
    "10.64.0.1": "Mullvad", "100.100.100.100": "Tailscale",
    "208.67.222.222": "OpenDNS", "208.67.220.220": "OpenDNS",
    "94.140.14.14": "AdGuard", "94.140.15.15": "AdGuard",
}


def _dns_provider(ip, link):
    if ip in DNS_PROVIDERS:
        return DNS_PROVIDERS[ip]
    if link.startswith(("wg0-mullvad", "mullvad")) or ip.startswith("194.242.2."):
        return "Mullvad"
    if ip.startswith("127.") or ip == "::1":
        return "Lokaler DNS-Dienst"
    try:
        import ipaddress
        if ipaddress.ip_address(ip).is_private:
            return "Router im lokalen Netz"
    except ValueError:
        pass
    return "Internetanbieter oder unbekannter Anbieter"


def dns_state():
    """Welcher DNS-Server beantwortet gerade die Anfragen?
    {server, provider, link, dot} – dot: DNS-over-TLS aktiv. None, wenn nichts gefunden."""
    route = internet_route_dev() or ""
    links = []                 # (link, aktueller Server, DoT)
    if which("resolvectl"):
        try:
            out = subprocess.run(["resolvectl", "status"], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            out = ""
        for block in re.split(r"\n(?=Global|Link \d)", out):
            head = block.splitlines()[0] if block.strip() else ""
            m = re.match(r"Link \d+ \(([^)]+)\)", head)
            link = m.group(1) if m else ("" if head.startswith("Global") else None)
            if link is None:
                continue
            cur = re.search(r"Current DNS Server:\s*(\S+)", block)
            if cur:
                links.append((link, cur.group(1), "+DNSOverTLS" in block))
    if links:
        link, srv, dot = next((l for l in links if l[0] == route), None) or \
            next((l for l in links if l[0] == ""), None) or links[0]
    else:
        ns = [l.split()[1] for l in _read("/etc/resolv.conf").splitlines()
              if l.startswith("nameserver") and len(l.split()) > 1]
        if not ns:
            return None
        link, srv, dot = "", ns[0], False
    name = srv.split("#", 1)[1] if "#" in srv else ""
    ip = srv.split("#", 1)[0].split("%", 1)[0]
    if ip.count(":") == 1:          # IPv4 mit Port
        ip = ip.split(":", 1)[0]
    return {"server": ip, "provider": _dns_provider(ip, link) + (f" ({name})" if name else ""),
            "link": link or route, "dot": dot}


def tailscale_state():
    """None (nicht installiert) oder dict(running, state, exit_node, ips)"""
    if not which("tailscale"):
        return None
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=6)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return {"running": False, "state": "Dienst nicht erreichbar", "exit_node": None, "ips": []}
    state = data.get("BackendState", "") or "unbekannt"
    exit_name = None
    if data.get("ExitNodeStatus"):
        for p in (data.get("Peer") or {}).values():
            if p.get("ExitNode"):
                exit_name = (p.get("HostName") or p.get("DNSName") or "").rstrip(".") or "Exit-Node"
                break
        exit_name = exit_name or "Exit-Node"
    return {"running": state == "Running", "state": state, "exit_node": exit_name,
            "ips": (data.get("Self") or {}).get("TailscaleIPs") or data.get("TailscaleIPs") or []}


TS_STATES = {"Stopped": "gestoppt/pausiert", "NeedsLogin": "nicht angemeldet", "NeedsMachineAuth": "wartet auf Freigabe",
             "Starting": "startet", "NoState": "aus"}


def vpn_state(ifaces=None):
    """Einheitlicher VPN-Zustand für die Sicherheits-Übersicht.
    Zählt nur Verbindungen, die wirklich aktiv sind – eine vorhandene, aber pausierte Schnittstelle
    (z. B. tailscale0 nach „tailscale down“) gilt nicht als verbunden."""
    if ifaces is None:
        ifaces = net_interfaces()[0]
    res = {"mullvad": None, "tailscale": tailscale_state(), "others": [], "route_dev": internet_route_dev()}
    if which("mullvad"):
        rc, st = _mullvad(["status"])
        res["mullvad"] = st
        res["mv_lockdown"] = _on(_mullvad(["lockdown-mode", "get"])[1], "block traffic", "lockdown")
    for i in ifaces:
        if i["kind"] != "VPN" or i["name"].startswith("tailscale"):
            continue
        if i["name"].startswith(("wg0-mullvad", "mullvad")) and res["mullvad"] is not None:
            continue
        if i["state"] in ("UP", "UNKNOWN") and (i["ipv4"] or i["ipv6"]):
            res["others"].append(i["name"])
    return res


def public_ip_info():
    """Fragt am.i.mullvad.net nach öffentlicher IP, Ort, Anbieter und ob Mullvad genutzt wird."""
    import urllib.request
    req = urllib.request.Request("https://am.i.mullvad.net/json", headers={"User-Agent": "tuxdex"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def _pkg_version(*names):
    """(name, version) des ersten installierten Pakets aus names – sonst (None, None)."""
    for n in names:
        try:
            r = subprocess.run(["pacman", "-Q", n], capture_output=True, text=True, timeout=5)
        except Exception:
            return None, None
        if r.returncode == 0 and r.stdout.split():
            return n, r.stdout.split()[1]
    return None, None


def _pending_update(pkg):
    """Neue Version aus der letzten Update-Prüfung oder None."""
    for u in _load_json(UPDATE_CACHE, {}).get("updates", []):
        if u.get("name") == pkg:
            return u.get("new")
    return None


def version_status():
    """Versionsstand von Grafiktreiber, CPU-Microcode, BIOS/UEFI, Kernel und Firmware.
    [(titel, ton, kurz, detail)]"""
    res = []
    # --- Grafiktreiber ---
    base = "/sys/class/drm"
    seen = set()
    try:
        cards = sorted(c for c in os.listdir(base) if re.match(r"card\d+$", c))
    except Exception:
        cards = []
    for c in cards:
        dev = f"{base}/{c}/device"
        try:
            drv = os.path.basename(os.readlink(f"{dev}/driver"))
        except OSError:
            continue
        vendor = _first_line(f"{dev}/vendor").replace("0x", "").lower()
        device = _first_line(f"{dev}/device").replace("0x", "").lower()
        if (vendor, device) in seen:
            continue
        seen.add((vendor, device))
        name = _pci_name(vendor, device)
        if drv == "nvidia":
            ver = _first_line("/sys/module/nvidia/version")
            pkg, pver = _pkg_version("nvidia", "nvidia-open", "nvidia-dkms", "nvidia-open-dkms", "nvidia-lts",
                                     "nvidia-open-lts", "nvidia-580xx-dkms", "nvidia-470xx-dkms")
            upkg, _ = _pkg_version("nvidia-utils")
            new = _pending_update(pkg) if pkg else None
            new = new or (_pending_update("nvidia-utils") if upkg else None)
            txt = f"{name} · NVIDIA-Treiber {ver or pver or '?'}" + (f" (Paket {pkg})" if pkg else "")
            if ver and pver and not pver.startswith(ver):
                res.append(("Grafiktreiber", "warn", "Neustart nötig",
                            txt + f" · installiert ist schon {pver} – nach einem Neustart aktiv."))
            elif new:
                res.append(("Grafiktreiber", "warn", "Update da", txt + f" · neue Version {new} verfügbar."))
            else:
                res.append(("Grafiktreiber", "ok", "Aktuell", txt + "."))
        else:
            mpkg, mver = _pkg_version("mesa")
            vk = {"amdgpu": "vulkan-radeon", "radeon": "vulkan-radeon", "i915": "vulkan-intel",
                  "xe": "vulkan-intel", "nouveau": "vulkan-nouveau"}.get(drv)
            vpkg, vver = _pkg_version(vk) if vk else (None, None)
            txt = f"{name} · Kernel-Treiber {drv} ({os.uname().release})"
            if mver:
                txt += f" · Mesa {mver.split('-')[0]}"
            if vver:
                txt += f" · Vulkan {vver.split('-')[0]}"
            new = (_pending_update("mesa") if mpkg else None) or (_pending_update(vpkg) if vpkg else None)
            if drv in ("simpledrm", "efifb", "vesafb"):
                res.append(("Grafiktreiber", "warn", "Notfall-Treiber",
                            f"{name} läuft nur mit dem einfachen Bildschirmtreiber {drv} – keine Beschleunigung."))
            elif new:
                res.append(("Grafiktreiber", "warn", "Update da", txt + f" · Mesa/Vulkan {new} verfügbar."))
            else:
                res.append(("Grafiktreiber", "ok" if mver else "info", "Aktuell" if mver else "Mesa fehlt",
                            txt + ("." if mver else " · Mesa ist nicht installiert (keine 3D-Beschleunigung).")))
    # --- CPU-Microcode ---
    rev = re.search(r"^microcode\s*:\s*(\S+)", _read("/proc/cpuinfo"), re.M)
    pkg, ok = microcode_state()
    if pkg is None:
        res.append(("CPU-Microcode", "off", "VM", "Virtuelle Maschine – der Host liefert den Microcode."
                    + (f" Revision {rev.group(1)}." if rev else "")))
    else:
        _, pver = _pkg_version(pkg)
        new = _pending_update(pkg)
        txt = f"{cpu_model()} · Revision {rev.group(1) if rev else '?'}"
        if not ok:
            res.append(("CPU-Microcode", "warn", "Fehlt", txt + f" · {pkg} ist nicht installiert – nur der Stand "
                        "aus dem BIOS ist aktiv. Im Tab Sicherheit installierbar."))
        elif new:
            res.append(("CPU-Microcode", "warn", "Update da", txt + f" · {pkg} {pver} → {new} verfügbar."))
        else:
            res.append(("CPU-Microcode", "ok", "Aktuell", txt + f" · {pkg} {pver}."))
    # --- Mainboard / BIOS ---
    dmi = "/sys/class/dmi/id"
    board = " ".join(x for x in (_first_line(f"{dmi}/board_vendor"), _first_line(f"{dmi}/board_name")) if x)
    bver, bdate = _first_line(f"{dmi}/bios_version"), _first_line(f"{dmi}/bios_date")
    age = None
    try:
        age = (datetime.now() - datetime.strptime(bdate, "%m/%d/%Y")).days / 365.25
    except ValueError:
        pass
    txt = f"{board or 'Mainboard unbekannt'} · BIOS/UEFI {bver or '?'}"
    if bdate:
        try:
            txt += f" vom {datetime.strptime(bdate, '%m/%d/%Y'):%d.%m.%Y}"
        except ValueError:
            txt += f" vom {bdate}"
    if age is not None and age >= 2:
        res.append(("Mainboard & BIOS", "info", f"{age:.0f} Jahre alt",
                    txt + " · beim Hersteller nach einem neueren BIOS schauen (Sicherheits- und Stabilitätsfixes)."))
    else:
        res.append(("Mainboard & BIOS", "ok" if age is not None else "off",
                    f"{age * 12:.0f} Monate alt" if age is not None else "Unbekannt", txt + "."))
    # --- Kernel ---
    kpkg = {"lts": "linux-lts", "zen": "linux-zen", "hardened": "linux-hardened"}
    rel = os.uname().release
    kp = next((v for k, v in kpkg.items() if k in rel), "linux")
    _, kver = _pkg_version(kp)
    new = _pending_update(kp)
    kbase = re.match(r"\d+(?:\.\d+)+", kver or "")
    if kbase and not re.match(re.escape(kbase.group(0)) + r"(?![\d.])", rel):
        res.append(("Kernel", "warn", "Neustart nötig", f"Läuft: {rel} · installiert: {kver} – nach einem Neustart "
                    "aktiv."))
    elif new:
        res.append(("Kernel", "warn", "Update da", f"{rel} · {kp} {new} verfügbar."))
    else:
        res.append(("Kernel", "ok", "Aktuell", f"{rel}" + (f" ({kp})" if kver else "") + "."))
    # --- Firmware über fwupd ---
    if which("fwupdmgr"):
        try:
            r = subprocess.run(["fwupdmgr", "get-updates", "--json", "--no-unreported-check",
                                "--no-metadata-check"], capture_output=True, text=True, timeout=25)
            data = json.loads(r.stdout or "{}")
            devs = [d.get("Name", "?") for d in data.get("Devices", []) if d.get("Releases")]
            if devs:
                res.append(("Firmware (fwupd)", "warn", f"{len(devs)} Updates",
                            "Firmware-Updates verfügbar für: " + ", ".join(devs[:6])
                            + ". Einspielen mit: fwupdmgr update"))
            else:
                res.append(("Firmware (fwupd)", "ok", "Aktuell", "fwupd kennt keine neueren Firmware-Versionen "
                            "(BIOS, SSD, Dock …)."))
        except Exception:
            res.append(("Firmware (fwupd)", "off", "Nicht prüfbar", "fwupd hat nicht geantwortet."))
    else:
        res.append(("Firmware (fwupd)", "off", "Nicht installiert", "Optional: Mit fwupd lassen sich BIOS-, SSD- "
                    "und Geräte-Firmware prüfen und aktualisieren (Paket fwupd)."))
    return res


def proxy_state():
    """Eingestellter Proxy: [(quelle, beschreibung)] – leer, wenn keiner gesetzt ist."""
    res = []
    for k in ("all_proxy", "https_proxy", "http_proxy", "socks_proxy"):
        v = os.environ.get(k) or os.environ.get(k.upper())
        if v:
            res.append(("Umgebungsvariable", f"{k} = {re.sub(r'//[^@/]+@', '//…@', v)}"))
    if which("gsettings"):
        try:
            mode = subprocess.run(["gsettings", "get", "org.gnome.system.proxy", "mode"], capture_output=True,
                                  text=True, timeout=3).stdout.strip().strip("'")
            if mode == "manual":
                parts = []
                for proto in ("https", "http", "socks"):
                    h = subprocess.run(["gsettings", "get", f"org.gnome.system.proxy.{proto}", "host"],
                                       capture_output=True, text=True, timeout=3).stdout.strip().strip("'")
                    pt = subprocess.run(["gsettings", "get", f"org.gnome.system.proxy.{proto}", "port"],
                                        capture_output=True, text=True, timeout=3).stdout.strip()
                    if h:
                        parts.append(f"{proto.upper()} {h}:{pt}")
                res.append(("GNOME", "manuell · " + (", ".join(parts) or "ohne Adresse")))
            elif mode == "auto":
                url = subprocess.run(["gsettings", "get", "org.gnome.system.proxy", "autoconfig-url"],
                                     capture_output=True, text=True, timeout=3).stdout.strip().strip("'")
                res.append(("GNOME", "automatisch (PAC)" + (f" · {url}" if url else "")))
        except Exception:
            pass
    kio = _read(os.path.expanduser("~/.config/kioslaverc"))
    m = re.search(r"^ProxyType=(\d)", kio, re.M)
    if m and m.group(1) != "0":
        kinds = {"1": "manuell", "2": "automatisch (PAC)", "3": "automatisch (WPAD)", "4": "aus Umgebungsvariablen"}
        detail = kinds.get(m.group(1), "an")
        for key in ("httpsProxy", "httpProxy", "socksProxy"):
            mm = re.search(rf"^{key}=(.+)$", kio, re.M)
            if mm and mm.group(1).strip() and m.group(1) == "1":
                detail += f" · {mm.group(1).strip().replace(' ', ':')}"
                break
        res.append(("KDE", detail))
    return res


def _get_json(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "tuxdex"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_text(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "tuxdex"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode().strip()


def dns_leak_servers():
    """DNS-Leak-Test über bash.ws: löst zufällige Namen über den System-DNS auf und fragt ab, welche
    DNS-Server sie angefragt haben. [(ip, land, anbieter/ASN)]"""
    import socket
    tid = _get_text("https://bash.ws/id")
    if not re.match(r"^\w+$", tid):
        raise ValueError("unerwartete Antwort von bash.ws")
    for i in range(6):
        try:
            socket.getaddrinfo(f"{i}.{tid}.bash.ws", None)
        except OSError:
            pass                 # NXDOMAIN ist erwartet – die Anfrage selbst zählt
    data = _get_json(f"https://bash.ws/dnsleak/test/{tid}?json", timeout=15)
    return [(d.get("ip", "?"), d.get("country_name") or d.get("country") or "", d.get("asn") or "")
            for d in data if d.get("type") == "dns"]


def ip_reputation():
    """Wie Webseiten deine IP einstufen (ipapi.is): dict(ip, org, country, city, vpn, vpn_name, proxy, tor,
    hosting)."""
    d = _get_json("https://api.ipapi.is/")
    comp, asn, loc = d.get("company") or {}, d.get("asn") or {}, d.get("location") or {}
    vpn = d.get("vpn") or {}
    return {"ip": d.get("ip", "?"), "org": comp.get("name") or asn.get("org") or "",
            "asn": f"AS{asn.get('asn')}" if asn.get("asn") else "", "country": loc.get("country", ""),
            "city": loc.get("city", ""), "vpn": bool(d.get("is_vpn")),
            "vpn_name": vpn.get("service") or vpn.get("name") or "", "proxy": bool(d.get("is_proxy")),
            "tor": bool(d.get("is_tor")), "hosting": bool(d.get("is_datacenter"))}


def vpn_active(v):
    """Läuft der Internetverkehr gerade durch ein VPN?"""
    mv = (v.get("mullvad") or "").lower()
    ts = v.get("tailscale") or {}
    return mv.startswith("connected") or bool(ts.get("running") and ts.get("exit_node")) or \
        (v.get("route_dev") or "") in v.get("others", [])


def leak_test():
    """Kompletter Test – jeder Teil einzeln, damit ein ausgefallener Dienst nicht alles verhindert."""
    res = {"vpn": vpn_active(vpn_state()), "local_dns": dns_state(), "proxy": proxy_state()}
    for key, fn in (("rep", ip_reputation), ("dns", dns_leak_servers), ("mullvad", public_ip_info)):
        try:
            res[key] = fn()
        except Exception as e:
            res[key + "_err"] = str(e)[:120]
    return res


def system_summary():
    osr = {}
    for line in _read("/etc/os-release").splitlines():
        k, _, v = line.partition("=")
        osr[k] = v.strip('"')
    dmi = "/sys/class/dmi/id"
    vendor, product = _first_line(f"{dmi}/sys_vendor"), _first_line(f"{dmi}/product_name")
    board = " ".join(x for x in (_first_line(f"{dmi}/board_vendor"), _first_line(f"{dmi}/board_name")) if x)
    bios = " ".join(x for x in (_first_line(f"{dmi}/bios_version"), _first_line(f"{dmi}/bios_date")) if x)
    cores, threads = cpu_threads_cores()
    mem = meminfo()
    session = os.environ.get("XDG_SESSION_TYPE", "")
    desk = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    return {
        "Computer": " ".join(x for x in (vendor, product) if x and "To be filled" not in x) or "—",
        "Mainboard": board or "—",
        "BIOS/UEFI": (bios or "—") + ("  ·  UEFI" if os.path.isdir("/sys/firmware/efi") else "  ·  Legacy-BIOS"),
        "Prozessor": f"{cpu_model()}  ·  {cores} Kerne / {threads} Threads",
        "Grafik": "\n".join(g["name"] for g in gpus()) or "—",
        "Arbeitsspeicher": fmt_bytes(mem.get("MemTotal", 0)),
        "Betriebssystem": osr.get("PRETTY_NAME", "Linux"),
        "Kernel": os.uname().release,
        "Desktop": " · ".join(x for x in (desk, session.capitalize() if session else "") if x) or "—",
        "Rechnername": os.uname().nodename,
    }


# ---- Programm-Icons für Prozesse ------------------------------------------

_DESKTOP_MAP = None


def desktop_map():
    """{schlüssel: (icon, name)} – Schlüssel: Programmname aus Exec, Desktop-ID, StartupWMClass"""
    global _DESKTOP_MAP
    if _DESKTOP_MAP is not None:
        return _DESKTOP_MAP
    m = {}
    dirs = [os.path.expanduser("~/.local/share/applications"), "/usr/local/share/applications",
            "/usr/share/applications", "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
            "/var/lib/snapd/desktop/applications"]
    for d in dirs:
        try:
            files = [f for f in os.listdir(d) if f.endswith(".desktop")]
        except Exception:
            continue
        for fn in files:
            entry, in_main = {}, False
            for line in _read(os.path.join(d, fn)).splitlines():
                if line.startswith("["):
                    in_main = line.strip() == "[Desktop Entry]"
                    continue
                if in_main and "=" in line:
                    k, _, v = line.partition("=")
                    entry.setdefault(k.strip(), v.strip())
            icon = entry.get("Icon")
            if not icon:
                continue
            val = (icon, entry.get("Name", fn[:-8]))
            did = fn[:-8]
            m.setdefault(did.lower(), val)
            m.setdefault(did.split(".")[-1].lower(), val)
            if entry.get("StartupWMClass"):
                m.setdefault(entry["StartupWMClass"].lower(), val)
            exe = entry.get("Exec", "")
            toks = [t for t in shlex.split(exe, posix=True) if "=" not in t or t.startswith("/")] if exe else []
            toks = [t for t in toks if t not in ("env", "flatpak", "run") and not t.startswith(("-", "%"))]
            # Interpreter überspringen: „python3 /pfad/skript.py“ → skript
            while toks and re.match(r"^(python[\d.]*|bash|sh|java|node|perl|ruby|gjs)$", os.path.basename(toks[0])):
                toks = toks[1:]
            if toks:
                base = os.path.basename(toks[0]).lower()
                m.setdefault(base, val)
                m.setdefault(re.sub(r"\.(py|sh|js)$", "", base).replace("_", "-"), val)
                if "flatpak" in exe and len(toks) >= 1:
                    m.setdefault(toks[-1].lower(), val)
    _DESKTOP_MAP = m
    return m


def app_for_process(pid, name, cmd):
    """(icon_name, app_name) oder None"""
    m = desktop_map()
    cg = _read(f"/proc/{pid}/cgroup")
    mt = re.search(r"app-flatpak-([\w.\-]+?)-\d+\.scope", cg) or \
        re.search(r"app-(?:[\w]+-)?([\w.\-]+?)(?:@[\w]+\.service|-\d+\.scope)", cg)
    via = None
    if mt:
        key = mt.group(1).lower()
        for k in (key, key.split(".")[-1]):
            if k in m:
                via = m[k]
                break
    cand = [name.lower()]
    first = cmd.split(" ", 1)[0] if cmd and not cmd.startswith("[") else ""
    if first:
        cand.append(os.path.basename(first).lower())
        if re.match(r"^(python[\d.]*|bash|sh|java|node|perl|ruby|gjs)$", os.path.basename(first)) \
                and len(cmd.split()) > 1:
            cand = [os.path.basename(cmd.split()[1]).lower()]
            cand.append(re.sub(r"\.(py|sh|js)$", "", cand[0]).replace("_", "-"))
    for c in cand:
        if c in m:
            return m[c]
    if via:
        # läuft in der Gruppe einer App: eigene Hilfsprozesse zählen zur App, root-Prozesse (sudo clamscan …)
        # bekommen ihren echten Namen, damit ihr Speicher nicht der App zugerechnet wird
        uid = re.search(r"^Uid:\s+(\d+)", _read(f"/proc/{pid}/status"), re.M)
        if uid and int(uid.group(1)) != os.getuid():
            return via[0], f"{name} · gestartet von {via[1]}"
        return via
    return None


_ICON_CACHE = {}


def themed_icon(name):
    if name in _ICON_CACHE:
        return _ICON_CACHE[name]
    icon = QIcon()
    if name.startswith("/") and os.path.exists(name):
        icon = QIcon(name)
    else:
        icon = QIcon.fromTheme(name)
        if icon.isNull():
            for ext in ("png", "svg", "xpm"):
                p = f"/usr/share/pixmaps/{name}.{ext}"
                if os.path.exists(p):
                    icon = QIcon(p)
                    break
        if icon.isNull():
            for base in ("/usr/share/icons/hicolor", os.path.expanduser("~/.local/share/icons/hicolor"),
                         "/var/lib/flatpak/exports/share/icons/hicolor"):
                for sz in ("scalable", "256x256", "128x128", "64x64", "48x48", "32x32"):
                    for ext in ("svg", "png"):
                        p = f"{base}/{sz}/apps/{name}.{ext}"
                        if os.path.exists(p):
                            icon = QIcon(p)
                            break
                    if not icon.isNull():
                        break
                if not icon.isNull():
                    break
    _ICON_CACHE[name] = icon
    return icon


def letter_icon(text, size=20):
    key = ("letter", text[:1].upper())
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    px = QPixmap(size * 2, size * 2)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(COLORS["bg3"]))
    p.drawRoundedRect(QRectF(0, 0, size * 2, size * 2), 10, 10)
    f = QFont(FONTS["sans"])
    f.setPixelSize(int(size * 1.1))
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor(COLORS["muted"]))
    p.drawText(QRectF(0, 0, size * 2, size * 2), Qt.AlignCenter, text[:1].upper() or "?")
    p.end()
    icon = QIcon(px)
    _ICON_CACHE[key] = icon
    return icon


def setup_icon_theme():
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        for t in ("breeze-dark", "breeze", "Papirus-Dark", "Papirus", "Adwaita", "hicolor"):
            if os.path.isdir(f"/usr/share/icons/{t}"):
                QIcon.setThemeName(t)
                break
    QIcon.setFallbackThemeName("hicolor")


class Segmented(QFrame):
    """Umschalter innerhalb eines Tabs (z. B. Prozesse | Leistung | System)."""

    def __init__(self, labels, on_change):
        super().__init__()
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.btns = []
        for i, l in enumerate(labels):
            b = QPushButton(l)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.TabFocus)
            b.clicked.connect(lambda _=False, i=i: (self.set(i), on_change(i)))
            lay.addWidget(b)
            self.btns.append(b)
        self.set(0)

    def set(self, idx):
        for i, b in enumerate(self.btns):
            b.setChecked(i == idx)


ENERGY_LEVELS = [(0.5, "sehr niedrig"), (5, "niedrig"), (20, "mittel"), (50, "hoch"), (1e9, "sehr hoch")]


def energy_label(cpu):
    for lim, txt in ENERGY_LEVELS:
        if cpu < lim:
            return txt
    return "sehr hoch"



# ---- Autostart & Bootzeit -------------------------------------------------

AUTOSTART_USER = os.path.join(os.path.expanduser("~/.config"), "autostart")
AUTOSTART_SYSTEM = "/etc/xdg/autostart"
APP_DIRS = [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications",
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications")]


def read_desktop(path):
    """[Desktop Entry] als dict (nur Hauptgruppe, erster Wert gewinnt)."""
    entry, main = {}, False
    for line in _read(path).splitlines():
        if line.startswith("["):
            main = line.strip() == "[Desktop Entry]"
        elif main and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            entry.setdefault(k.strip(), v.strip())
    return entry


def _desktop_off(e):
    return e.get("Hidden", "").lower() == "true" or e.get("X-GNOME-Autostart-enabled", "").lower() == "false"


def _desktop_shown(e):
    """Gilt der Eintrag in dieser Desktop-Umgebung (OnlyShowIn/NotShowIn)?"""
    cur = [d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d]
    only = [d.lower() for d in e.get("OnlyShowIn", "").split(";") if d]
    not_ = [d.lower() for d in e.get("NotShowIn", "").split(";") if d]
    if only and cur and not set(only) & set(cur):
        return False
    return not (not_ and set(not_) & set(cur))


def autostart_entries():
    """[{id, name, icon, exec, comment, on, source: user|system|both, path}] – Benutzer überschreibt System."""
    res = {}
    for src, d in (("system", AUTOSTART_SYSTEM), ("user", AUTOSTART_USER)):
        try:
            files = sorted(f for f in os.listdir(d) if f.endswith(".desktop"))
        except OSError:
            continue
        for fn in files:
            e = read_desktop(os.path.join(d, fn))
            if not e:
                continue
            prev = res.get(fn)
            if src == "system" and not _desktop_shown(e):
                continue
            item = {"id": fn, "name": e.get("Name") or fn[:-8], "icon": e.get("Icon", ""),
                    "exec": e.get("Exec", ""), "comment": e.get("Comment", ""), "on": not _desktop_off(e),
                    "source": "both" if prev else src, "path": os.path.join(d, fn)}
            if prev and not item["exec"]:            # Benutzer-Kopie nur mit Hidden=true
                item.update(name=prev["name"], icon=prev["icon"], exec=prev["exec"], comment=prev["comment"])
            res[fn] = item
    return sorted(res.values(), key=lambda x: x["name"].lower())


def autostart_set(entry, on):
    """Ein/aus nach XDG-Regel: eigene Kopie in ~/.config/autostart mit Hidden=true/false."""
    os.makedirs(AUTOSTART_USER, exist_ok=True)
    dst = os.path.join(AUTOSTART_USER, entry["id"])
    src = dst if os.path.exists(dst) else os.path.join(AUTOSTART_SYSTEM, entry["id"])
    lines = _read(src).splitlines() or ["[Desktop Entry]", "Type=Application", f"Name={entry['name']}",
                                         f"Exec={entry['exec']}"]
    out, main, done = [], False, False
    for line in lines:
        if line.startswith("["):
            if main and not done:
                out.append(f"Hidden={'false' if on else 'true'}")
                done = True
            main = line.strip() == "[Desktop Entry]"
        elif main and line.split("=", 1)[0].strip() in ("Hidden", "X-GNOME-Autostart-enabled"):
            continue
        out.append(line)
    if not done:
        out.append(f"Hidden={'false' if on else 'true'}")
    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")


def autostart_add(desktop_path):
    os.makedirs(AUTOSTART_USER, exist_ok=True)
    dst = os.path.join(AUTOSTART_USER, os.path.basename(desktop_path))
    shutil.copyfile(desktop_path, dst)
    autostart_set({"id": os.path.basename(desktop_path), "name": "", "exec": ""}, True)
    return dst


def installed_apps():
    """[(name, pfad)] aller sichtbaren Programme – für „Programm hinzufügen“."""
    seen, res = set(), []
    for d in APP_DIRS:
        try:
            files = os.listdir(d)
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".desktop") or fn in seen:
                continue
            e = read_desktop(os.path.join(d, fn))
            if e.get("Type", "Application") != "Application" or e.get("NoDisplay", "").lower() == "true" \
                    or not e.get("Exec") or not _desktop_shown(e):
                continue
            seen.add(fn)
            res.append((e.get("Name", fn[:-8]), os.path.join(d, fn), e.get("Icon", "")))
    return sorted(res, key=lambda x: x[0].lower())


def user_services():
    """Aktivierte systemd-Benutzerdienste: [(unit, beschreibung, aktiv)]"""
    try:
        out = subprocess.run(["systemctl", "--user", "list-unit-files", "--type=service", "--state=enabled",
                              "--no-legend", "--no-pager"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        unit = line.split()[0] if line.split() else ""
        if not unit or "@" in unit:
            continue
        try:
            desc = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Description", "--value"],
                                  capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            desc = ""
        res.append((unit, desc, svc_user_active(unit)))
    return res


def svc_user_active(unit):
    try:
        return subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True,
                              timeout=3).stdout.strip() == "active"
    except Exception:
        return False


# Dienste, die oft den Start bremsen und meist gefahrlos entfallen können
SLOW_HINTS = {
    "NetworkManager-wait-online.service": "Wartet beim Start aufs Netzwerk – auf Desktops meist unnötig.",
    "systemd-networkd-wait-online.service": "Wartet beim Start aufs Netzwerk – auf Desktops meist unnötig.",
}


class TimeBar(QWidget):
    """Name links, Dauer rechts, dünner Balken darunter (Bootzeit)."""

    def __init__(self, label, value, frac, warn=False):
        super().__init__()
        self.label, self.value, self.frac, self.warn = label, value, max(0.0, min(1.0, frac)), warn
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(320, 30)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        p.setFont(QFont(FONTS["mono"], 10))
        p.setPen(QColor(COLORS["ink"]))
        p.drawText(QRectF(0, 0, w - 90, 18), Qt.AlignLeft | Qt.AlignVCenter, self.label)
        p.drawText(QRectF(w - 90, 0, 90, 18), Qt.AlignRight | Qt.AlignVCenter, self.value)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["bg3"]))
        p.drawRoundedRect(QRectF(0, 22, w, 6), 3, 3)
        p.setBrush(QColor(COLORS["warn" if self.warn else "accent"]))
        if self.frac > 0:
            p.drawRoundedRect(QRectF(0, 22, max(6, w * self.frac), 6), 3, 3)
        p.end()


def _secs(txt):
    """„1min 2.345s“, „850ms“ → Sekunden"""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)\s*(min|ms|us|s|h)\b", txt):
        total += float(num) * {"h": 3600, "min": 60, "s": 1, "ms": 0.001, "us": 1e-6}[unit]
    return total


def boot_times():
    """{phases: [(name, sek)], total, target, blame: [(sek, unit)]} über systemd-analyze – None ohne systemd."""
    if not which("systemd-analyze"):
        return None
    try:
        t = subprocess.run(["systemd-analyze", "time"], capture_output=True, text=True, timeout=15,
                           env={**os.environ, "LC_ALL": "C"}).stdout
    except Exception:
        return None
    m = re.search(r"Startup finished in (.+?)=\s*(.+)", t)
    if not m:
        return {"error": (t.strip() or "Der Start ist noch nicht abgeschlossen.")}
    names = {"firmware": "Firmware (UEFI/BIOS)", "loader": "Bootloader", "kernel": "Kernel",
             "initrd": "Initramfs", "userspace": "System (Dienste)"}
    phases = [(names.get(k, k), _secs(v)) for v, k in re.findall(r"([\d.]+(?:min|ms|us|s|h)(?:\s*[\d.]+(?:ms|s))?)"
                                                                    r"\s*\((\w+)\)", m.group(1))]
    tgt = re.search(r"graphical\.target reached after (.+?) in userspace", t)
    blame = []
    try:
        b = subprocess.run(["systemd-analyze", "blame", "--no-pager"], capture_output=True, text=True, timeout=15,
                           env={**os.environ, "LC_ALL": "C"}).stdout
        for line in b.splitlines():
            parts = line.strip().rsplit(" ", 1)
            if len(parts) == 2:
                blame.append((_secs(parts[0]), parts[1]))
    except Exception:
        pass
    # Dienste, die ein Timer auslöst (snapper-cleanup, man-db …), laufen nach dem Start – nicht mitzählen
    try:
        timers = subprocess.run(["systemctl", "list-unit-files", "--type=timer", "--no-legend", "--no-pager"],
                                capture_output=True, text=True, timeout=5).stdout
        timed = {l.split()[0].removesuffix(".timer") + ".service" for l in timers.splitlines() if l.split()}
    except Exception:
        timed = set()
    blame = [b for b in blame if b[1] not in timed]
    return {"phases": phases, "total": _secs(m.group(2)), "target": _secs(tgt.group(1)) if tgt else None,
            "blame": sorted(blame, reverse=True)[:12]}


class _SortItem(QTreeWidgetItem):
    """Baumzeile, die nach Zahlen statt nach Text sortiert (CPU, RAM …)."""

    def __init__(self, texts, keys):
        super().__init__(texts)
        self.keys = keys

    def __lt__(self, other):
        c = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.keys[c], getattr(other, "keys", [None] * 9)[c]
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class TaskTab(Page):
    COLS = [("Name", 230), ("PID", 70), ("Benutzer", 95), ("CPU", 70), ("Arbeitsspeicher", 115),
            ("Datenträger", 100), ("Energie (gesch.)", 120), ("Status", 100), ("Befehl", 300)]
    INTERVAL = 2000

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.prev_total = None
        self.prev_ticks = {}
        self.prev_io = {}
        self.prev_net = None
        self.prev_disk = None
        self.data = {}
        self.busy = False
        self.app_cache = {}   # pid -> (icon_name, app_name) | None

        self.badge = StatusBadge("info", "Live · alle 2 s")
        self.pause_btn = Button("Pausieren", "ghost", self.toggle_pause)
        self.seg = Segmented(["Prozesse", "Leistung", "System", "Autostart"], self._switch)
        self.lay.addLayout(page_header("Taskmanager", self.seg, self.badge, self.pause_btn))

        self.views = QStackedWidget()
        self.lay.addWidget(self.views, 1)

        # ---------- Prozesse ----------
        pv = QWidget()
        pl = QVBoxLayout(pv)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(16)
        mini = QHBoxLayout()
        mini.setSpacing(16)
        self.m_cpu, self.m_ram, self.m_net, self.m_bat = (StatTile(t, compact=True) for t in
                                                          ("Prozessor", "Arbeitsspeicher", "Netzwerk", "Akku"))
        for t in (self.m_cpu, self.m_ram, self.m_net, self.m_bat):
            mini.addWidget(t, 1)
        pl.addLayout(mini)

        pp = Panel("Prozesse")
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = LineEdit(placeholder="Name, PID, Benutzer oder Befehl …")
        self.search.textChanged.connect(lambda _: self._render())
        top.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["Alle Prozesse", "Programme", "Hintergrundprozesse", "Nur meine"])
        self.filter.setMinimumWidth(190)
        self.filter.setMinimumHeight(38)
        self.filter.currentIndexChanged.connect(lambda _: self._render())
        top.addWidget(self.filter)
        self.cb_group = QCheckBox("Nach Programm gruppieren")
        self.cb_group.setChecked(True)
        self.cb_group.toggled.connect(lambda _: self._render())
        top.addWidget(self.cb_group)
        pp.body.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(self.COLS))
        self.tree.setHeaderLabels([c[0].upper() for c in self.COLS])
        self.tree.setIconSize(QSize(20, 20))
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        hh = self.tree.header()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setStretchLastSection(True)
        hh.setSortIndicatorShown(True)
        for i, (_, w) in enumerate(self.COLS):
            self.tree.setColumnWidth(i, w + (40 if i == 0 else 0))
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.DescendingOrder)
        self.tree.setMinimumHeight(420)
        self.tree.itemSelectionChanged.connect(self._sel_changed)
        self.tree.itemExpanded.connect(lambda it: self.expanded.add(it.data(0, Qt.UserRole)))
        self.tree.itemCollapsed.connect(lambda it: self.expanded.discard(it.data(0, Qt.UserRole)))
        self.expanded = set()       # aufgeklappte Gruppen bleiben beim Aktualisieren offen
        pp.body.addWidget(self.tree, 1)

        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.sel_label = Label("Kein Prozess ausgewählt", "Muted")
        acts.addWidget(self.sel_label, 1)
        self.b_nice_down = Button("Priorität senken", "ghost", lambda: self.renice(+5),
                                  "nice +5 – Prozess bekommt weniger CPU-Zeit")
        self.b_nice_up = Button("Priorität erhöhen", "ghost", lambda: self.renice(-5),
                                "nice −5 – braucht root-Rechte")
        self.b_term = Button("Beenden", "ghost", lambda: self.kill(signal.SIGTERM))
        self.b_kill = Button("Erzwingen", "danger", lambda: self.kill(signal.SIGKILL))
        for b in (self.b_nice_down, self.b_nice_up, self.b_term, self.b_kill):
            acts.addWidget(b)
        pp.body.addLayout(acts)
        pp.body.addWidget(Label(
            "CPU = Anteil an der Leistung aller Kerne. Energie wird aus der CPU-Last geschätzt – Linux misst den "
            "Verbrauch einzelner Programme nicht direkt. Datenträger = Lesen + Schreiben pro Sekunde "
            "(bei Prozessen anderer Benutzer nur mit root sichtbar).", "Hint", wrap=True))
        pl.addWidget(pp, 1)
        self.views.addWidget(pv)

        # ---------- Leistung ----------
        lv = QWidget()
        ll = QGridLayout(lv)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(16)
        self.t_cpu = StatTile("Prozessor")
        self.t_ram = StatTile("Arbeitsspeicher")
        self.t_gpu = StatTile("Grafik")
        self.t_net = StatTile("Netzwerk", maxval=None)
        self.t_disk = StatTile("Datenträger", maxval=None)
        self.t_bat = StatTile("Akku")
        self.t_swap = StatTile("Swap")
        self.t_sys = StatTile("System")
        self.t_sys.spark.hide()
        self.perf_tiles = {"cpu": self.t_cpu, "ram": self.t_ram, "gpu": self.t_gpu, "net": self.t_net,
                           "disk": self.t_disk, "bat": self.t_bat, "swap": self.t_swap, "sys": self.t_sys}
        for i, (kind, t) in enumerate(self.perf_tiles.items()):
            t.make_clickable(lambda k=kind: self.toggle_detail(k))
            ll.addWidget(t, i // 3, i % 3)
        for c in range(3):
            ll.setColumnStretch(c, 1)
        self.detail = DetailPanel(lambda: self.toggle_detail(None), self._detail_device)
        self.detail.hide()
        ll.addWidget(self.detail, 3, 0, 1, 3)
        self.detail_hint = Label("Kachel anklicken, um Details zu sehen – z. B. Caches, Takt und Virtualisierung "
                                 "beim Prozessor, Partitionen beim Datenträger, Takt und Leistung bei der Grafik.",
                                 "Hint", wrap=True)
        ll.addWidget(self.detail_hint, 4, 0, 1, 3)
        ll.setRowStretch(5, 1)
        self.detail_kind = None
        self.detail_dev = {}      # Art → gewähltes Gerät (Laufwerk, Grafikkarte, Schnittstelle)
        self.detail_prev = {}     # Art → (Zeit, Zähler) für Raten
        self.detail_busy = False
        self.last_snap = None
        self.cpu_pct = None
        self.views.addWidget(lv)

        # ---------- System ----------
        sv = QWidget()
        sl = QVBoxLayout(sv)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(16)
        hw = Panel("Hardware & System")
        self.sys_grid = QGridLayout()
        self.sys_grid.setHorizontalSpacing(24)
        self.sys_grid.setVerticalSpacing(12)
        hw.body.addLayout(self.sys_grid)
        sl.addWidget(hw)
        vp = Panel("Versionsstand – Treiber, Microcode, BIOS",
                   [Button("↻", "icon", self.load_versions, "Neu prüfen")])
        self.ver_box = QVBoxLayout()
        self.ver_box.setSpacing(0)
        vp.body.addLayout(self.ver_box)
        vp.body.addWidget(Label("„Update da“ stützt sich auf die letzte Prüfung im Tab Updates.", "Hint", wrap=True))
        sl.addWidget(vp)
        netp = Panel("Netzwerk & IP-Adressen", [Button("Öffentliche IP prüfen", "ghost", self.check_public_ip,
                                                       "Fragt am.i.mullvad.net nach deiner öffentlichen IP")])
        self.net_box = QVBoxLayout()
        self.net_box.setSpacing(8)
        netp.body.addLayout(self.net_box)
        self.pub_label = Label("Öffentliche IP: noch nicht geprüft (Abfrage über am.i.mullvad.net)", "Hint", wrap=True)
        self.pub_label.setTextFormat(Qt.RichText)
        netp.body.addWidget(self.pub_label)
        sl.addWidget(netp)
        batp = Panel("Akku")
        self.bat_box = QVBoxLayout()
        batp.body.addLayout(self.bat_box)
        sl.addWidget(batp)
        self.bat_panel = batp
        sl.addStretch(1)
        self.views.addWidget(sv)

        # ---------- Autostart & Bootzeit ----------
        av = QWidget()
        al = QVBoxLayout(av)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(16)
        self.add_app_cb = QComboBox()
        self.add_app_cb.setMinimumWidth(260)
        self.add_app_cb.setMinimumHeight(38)
        ap = Panel("Autostart-Programme", [self.add_app_cb, Button("Hinzufügen", "ghost", self.autostart_add),
                                           Button("↻", "icon", self.load_autostart, "Neu einlesen")])
        ap.body.addWidget(Label("Programme, die nach der Anmeldung automatisch starten. Ausschalten ist jederzeit "
                                "umkehrbar – für System-Einträge legt Tuxdex nur eine eigene Einstellung in "
                                "~/.config/autostart an.", "Hint", wrap=True))
        self.as_box = QVBoxLayout()
        self.as_box.setSpacing(2)
        ap.body.addLayout(self.as_box)
        al.addWidget(ap)
        sp = Panel("Hintergrunddienste des Benutzers (systemd --user)")
        self.us_box = QVBoxLayout()
        self.us_box.setSpacing(2)
        sp.body.addLayout(self.us_box)
        al.addWidget(sp)
        bp = Panel("Bootzeit", [Button("↻", "icon", self.load_boot, "Neu messen")])
        self.boot_head = Label("", "Value", wrap=True)
        bp.body.addWidget(self.boot_head)
        self.boot_phases = QVBoxLayout()
        self.boot_phases.setSpacing(4)
        bp.body.addLayout(self.boot_phases)
        bp.body.addWidget(Label("LANGSAMSTE DIENSTE BEIM START", "FieldLabel"))
        self.boot_blame = QVBoxLayout()
        self.boot_blame.setSpacing(2)
        bp.body.addLayout(self.boot_blame)
        bp.body.addWidget(Label("Dienste starten größtenteils parallel – die Zeiten addieren sich nicht. Entscheidend "
                                "ist vor allem „System (Dienste)“.", "Hint", wrap=True))
        al.addWidget(bp)
        al.addStretch(1)
        self.views.addWidget(av)

        self._sel_changed()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self.INTERVAL)
        QTimer.singleShot(200, self.tick)

    # ---- Ansichten --------------------------------------------------------

    def _switch(self, idx):
        self.views.setCurrentIndex(idx)
        if idx == 2:
            self.load_system()
        elif idx == 3:
            self.load_autostart()
            self.load_boot()

    # ---- Autostart --------------------------------------------------------

    def load_autostart(self):
        def worker():
            ents, svcs, apps = autostart_entries(), user_services(), installed_apps()
            ui(lambda: self._show_autostart(ents, svcs, apps))
        threading.Thread(target=worker, daemon=True).start()

    def _as_row(self, icon, name, detail, on, toggle, remove=None):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        ic = QLabel()
        qi = themed_icon(icon) if icon else QIcon()
        ic.setPixmap((qi if not qi.isNull() else letter_icon(name)).pixmap(24, 24))
        ic.setFixedSize(24, 24)
        h.addWidget(ic)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        txt.addWidget(Label(name, "PanelTitle"))
        d = Label(detail, "Hint")
        d.setToolTip(detail)
        txt.addWidget(d)
        h.addLayout(txt, 1)
        if remove:
            h.addWidget(Button("Entfernen", "ghost", remove))
        sw = Switch()
        sw.setChecked(on)
        sw.toggled.connect(toggle)
        h.addWidget(sw)
        return w

    def _show_autostart(self, ents, svcs, apps):
        self._clear(self.as_box)
        if not ents:
            self.as_box.addWidget(Label("Keine Autostart-Programme.", "Muted"))
        src = {"user": "eigener Eintrag", "system": "vom System", "both": "vom System · angepasst"}
        for e in ents:
            cmd = e["exec"].replace("%U", "").replace("%u", "").replace("%F", "").replace("%f", "").strip()
            self.as_box.addWidget(self._as_row(
                e["icon"], e["name"], f"{src[e['source']]} · {cmd}", e["on"],
                lambda on, e=e: self._as_toggle(e, on),
                (lambda _=False, e=e: self._as_remove(e)) if e["source"] == "user" else None))
        self._clear(self.us_box)
        if not svcs:
            self.us_box.addWidget(Label("Keine aktivierten Benutzerdienste.", "Muted"))
        for unit, desc, active in svcs:
            self.us_box.addWidget(self._as_row(
                "", unit.removesuffix(".service"), f"{desc or unit} · {'läuft' if active else 'gestoppt'}", True,
                lambda on, u=unit: self._svc_toggle(u, on)))
        cur = {e["id"] for e in ents}
        self.add_app_cb.clear()
        self.add_app_cb.addItem("Programm auswählen …", None)
        for name, path, icon in apps:
            if os.path.basename(path) not in cur:
                qi = themed_icon(icon) if icon else QIcon()
                self.add_app_cb.addItem(qi if not qi.isNull() else letter_icon(name), name, path)

    def _as_toggle(self, e, on):
        try:
            autostart_set(e, on)
            self.app.set_status(f"{e['name']} startet {'jetzt' if on else 'nicht mehr'} automatisch.")
        except OSError as err:
            show_error(self, "Autostart", f"Konnte nicht speichern: {err}")
        self.load_autostart()

    def _as_remove(self, e):
        if ask_confirm(self, "Autostart", f"{e['name']} aus dem Autostart entfernen?", "Entfernen"):
            try:
                os.remove(e["path"])
            except OSError:
                pass
            self.load_autostart()

    def autostart_add(self):
        path = self.add_app_cb.currentData()
        if not path:
            return
        try:
            autostart_add(path)
            self.app.set_status(f"{self.add_app_cb.currentText()} startet jetzt automatisch.")
        except OSError as err:
            show_error(self, "Autostart", f"Konnte nicht speichern: {err}")
        self.load_autostart()

    def _svc_toggle(self, unit, on):
        if not on and not ask_confirm(self, "Dienst", f"{unit} nicht mehr automatisch starten und jetzt stoppen?",
                                      "Deaktivieren"):
            self.load_autostart()
            return
        run_capture_async(["systemctl", "--user", "enable" if on else "disable", "--now", unit],
                          lambda rc, o, e: (self.app.set_status(
                              f"{unit} {'aktiviert' if on else 'deaktiviert'}." if rc == 0
                              else f"Fehler: {e.strip()}"), self.load_autostart()))

    # ---- Bootzeit ---------------------------------------------------------

    def load_boot(self):
        self.boot_head.setText("Wird gemessen …")

        def worker():
            b = boot_times()
            ui(lambda: self._show_boot(b))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _fmt_s(sec):
        return f"{sec / 60:.0f} min {sec % 60:.0f} s" if sec >= 60 else f"{sec:.1f} s"

    def _show_boot(self, b):
        self._clear(self.boot_phases)
        self._clear(self.boot_blame)
        if not b:
            self.boot_head.setText("systemd-analyze ist nicht verfügbar.")
            return
        if b.get("error"):
            self.boot_head.setText(b["error"])
            return
        tone = "schnell" if b["total"] < 20 else ("normal" if b["total"] < 45 else "langsam")
        self.boot_head.setText(f"Letzter Start: {self._fmt_s(b['total'])} ({tone})"
                               + (f" · Anmeldebildschirm nach {self._fmt_s(b['target'])} Systemzeit"
                                  if b.get("target") else ""))
        top = max([s for _, s in b["phases"]] + [0.001])
        for name, sec in b["phases"]:
            self.boot_phases.addWidget(TimeBar(name, self._fmt_s(sec), sec / top))
        topb = b["blame"][0][0] if b["blame"] else 1
        for sec, unit in b["blame"]:
            row = QHBoxLayout()
            row.setSpacing(12)
            bar = TimeBar(unit, self._fmt_s(sec), sec / topb, warn=unit in SLOW_HINTS)
            row.addWidget(bar, 1)
            hint = SLOW_HINTS.get(unit)
            if hint:
                bar.setToolTip(hint)
                row.addWidget(Button("Deaktivieren", "ghost", lambda _=False, u=unit: self._disable_unit(u), hint))
            self.boot_blame.addLayout(row)

    def _disable_unit(self, unit):
        if not ask_confirm(self, "Dienst deaktivieren", f"{unit} beim Start nicht mehr ausführen?\n\n"
                           f"{SLOW_HINTS.get(unit, '')}\nRückgängig: sudo systemctl enable {unit}", "Deaktivieren"):
            return
        if not self.app.priv.ensure(self):
            return
        run_capture_async(["systemctl", "disable", unit],
                          lambda rc, o, e: self.app.set_status(f"{unit} deaktiviert – wirkt beim nächsten Start."
                                                               if rc == 0 else f"Fehler: {e.strip()}"),
                          needs_sudo=True)

    def toggle_pause(self):
        if self.timer.isActive():
            self.timer.stop()
            self.pause_btn.setText("Fortsetzen")
            self.badge.set("off", "Pausiert")
        else:
            self.timer.start(self.INTERVAL)
            self.pause_btn.setText("Pausieren")
            self.badge.set("info", "Live · alle 2 s")
            self.tick()

    # ---- Datenerfassung (Hintergrund-Thread) ------------------------------

    def tick(self):
        if self.busy or not self.isVisible():
            return
        self.busy = True
        want_gpu = self.views.currentIndex() == 1

        def worker():
            total, idle = cpu_times()
            procs = read_processes()
            for pid, p in procs.items():
                io = _read(f"/proc/{pid}/io")
                m1 = re.search(r"read_bytes: (\d+)", io)
                m2 = re.search(r"write_bytes: (\d+)", io)
                p["io"] = int(m1.group(1)) + int(m2.group(1)) if (m1 and m2) else None
                if pid not in self.app_cache:
                    self.app_cache[pid] = app_for_process(pid, p["name"], p["cmd"])
                p["app"] = self.app_cache[pid]
            snap = {
                "t": time.time(), "total": total, "idle": idle, "procs": procs, "mem": meminfo(),
                "load": _read("/proc/loadavg").split()[:3],
                "up": float((_read("/proc/uptime").split() or ["0"])[0]),
                "net": net_counters(), "disk": disk_counters(), "bat": batteries(),
                "temps": temperatures(), "mhz": cpu_mhz(), "gpus": gpus() if want_gpu else None,
            }
            ui(lambda: self._update(snap))

        threading.Thread(target=worker, daemon=True).start()

    def _update(self, s):
        self.busy = False
        procs, mem = s["procs"], s["mem"]
        cpu_pct, dt = None, 0
        if self.prev_total:
            dt = s["total"] - self.prev_total[0]
            di = s["idle"] - self.prev_total[1]
            cpu_pct = 100.0 * (dt - di) / dt if dt > 0 else 0.0
        secs = max(0.5, s["t"] - (self.prev_total[2] if self.prev_total else s["t"] - 2))
        for pid, p in procs.items():
            prev = self.prev_ticks.get(pid)
            p["cpu"] = 100.0 * (p["ticks"] - prev) / dt if (prev is not None and dt > 0) else 0.0
            pio = self.prev_io.get(pid)
            p["io_rate"] = (p["io"] - pio) / secs if (p["io"] is not None and pio is not None) else \
                (None if p["io"] is None else 0)
        self.cpu_pct = cpu_pct
        self.prev_total = (s["total"], s["idle"], s["t"])
        self.prev_ticks = {pid: p["ticks"] for pid, p in procs.items()}
        self.prev_io = {pid: p["io"] for pid, p in procs.items() if p["io"] is not None}
        self.app_cache = {pid: v for pid, v in self.app_cache.items() if pid in procs}
        self.data = procs

        # Prozessor
        temps = s["temps"]
        avg_mhz, max_mhz = s["mhz"]
        cpu_sub = [f"{N_CPU} Threads"]
        if avg_mhz:
            cpu_sub.append(f"{avg_mhz / 1000:.2f} GHz")
        if "cpu" in temps:
            cpu_sub.append(f"{temps['cpu']:.0f} °C")
        cpu_sub.append("Last " + " / ".join(s["load"]))
        if cpu_pct is not None:
            for t in (self.t_cpu, self.m_cpu):
                t.set(f"{cpu_pct:.0f} %", " · ".join(cpu_sub), cpu_pct)
        # RAM
        mt, ma = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        used = mt - ma
        for t in (self.t_ram, self.m_ram):
            t.set(f"{used / mt * 100:.0f} %" if mt else "—", f"{fmt_bytes(used)} von {fmt_bytes(mt)}",
                  used / mt * 100 if mt else 0)
        # Swap
        st, sf = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
        if st:
            self.t_swap.set(f"{(st - sf) / st * 100:.0f} %", f"{fmt_bytes(st - sf)} von {fmt_bytes(st)}",
                            (st - sf) / st * 100)
        else:
            self.t_swap.set("aus", "kein Swap aktiv", 0)
        # Netzwerk
        net = {k: v for k, v in s["net"].items() if k != "lo"}
        rx = sum(v[0] for v in net.values())
        tx = sum(v[1] for v in net.values())
        if self.prev_net:
            drx, dtx = max(0, rx - self.prev_net[0]) / secs, max(0, tx - self.prev_net[1]) / secs
            txt = f"↓ {fmt_bytes(drx)}/s"
            sub = f"↑ {fmt_bytes(dtx)}/s · gesamt ↓ {fmt_bytes(rx)} ↑ {fmt_bytes(tx)}"
            self.t_net.set(txt, sub, drx + dtx)
            self.m_net.set(txt, f"↑ {fmt_bytes(dtx)}/s", drx + dtx)
        self.prev_net = (rx, tx)
        # Datenträger
        rd, wr = s["disk"]
        if self.prev_disk:
            drd, dwr = max(0, rd - self.prev_disk[0]) / secs, max(0, wr - self.prev_disk[1]) / secs
            self.t_disk.set(f"{fmt_bytes(drd + dwr)}/s", f"Lesen {fmt_bytes(drd)}/s · Schreiben {fmt_bytes(dwr)}/s",
                            drd + dwr)
        self.prev_disk = (rd, wr)
        # Grafik
        if s["gpus"] is not None:
            gl = s["gpus"]
            g = next((x for x in gl if x.get("busy") is not None), gl[0] if gl else None)
            if g:
                sub = [g["name"]]
                if g.get("vram_total"):
                    sub.append(f"VRAM {fmt_bytes(g['vram_used'])} / {fmt_bytes(g['vram_total'])}")
                if "gpu" in temps:
                    sub.append(f"{temps['gpu']:.0f} °C")
                if g.get("busy") is not None:
                    self.t_gpu.set(f"{g['busy']} %", " · ".join(sub), g["busy"])
                else:
                    self.t_gpu.set("—", " · ".join(sub) + " · Auslastung vom Treiber nicht gemeldet", None)
            else:
                self.t_gpu.set("—", "keine Grafikkarte erkannt", None)
        # Akku
        bats, ac = s["bat"]
        if bats:
            b = bats[0]
            st_txt = BAT_STATUS.get(b["status"], b["status"])
            sub = [st_txt]
            if b["watts"] is not None:
                sub.append(f"{b['watts']:.1f} W")
            if b["hours"]:
                h, m = int(b["hours"]), int((b["hours"] % 1) * 60)
                sub.append(("noch " if b["status"] == "Discharging" else "voll in ") + f"{h}:{m:02d} h")
            for t in (self.t_bat, self.m_bat):
                t.set(f"{b['capacity']} %" if b["capacity"] is not None else "—", " · ".join(sub),
                      b["capacity"])
        else:
            for t in (self.t_bat, self.m_bat):
                t.set("Netz", "kein Akku – Netzbetrieb", None)
        # System
        d, rem = divmod(int(s["up"]), 86400)
        h, rem = divmod(rem, 3600)
        n_apps = sum(1 for p in procs.values() if p.get("app"))
        self.t_sys.set(f"{len(procs)}", f"Prozesse ({n_apps} von Programmen) · läuft seit "
                       + (f"{d} T " if d else "") + f"{h} Std {rem // 60} Min")
        if self.views.currentIndex() == 0:
            self._render()
        self.last_snap = s
        if self.detail_kind and self.views.currentIndex() == 1:
            self.refresh_detail()

    # ---- Detail-Ansicht (Kachel angeklickt) --------------------------------

    DETAIL_TITLES = {"cpu": "Prozessor", "ram": "Arbeitsspeicher", "gpu": "Grafik", "net": "Netzwerk",
                     "disk": "Datenträger", "bat": "Akku", "swap": "Swap", "sys": "System & Lüfter"}

    def toggle_detail(self, kind):
        if kind == self.detail_kind:
            kind = None
        self.detail_kind = kind
        for k, t in self.perf_tiles.items():
            t.set_selected(k == kind)
        self.detail_hint.setVisible(not kind)
        if not kind:
            self.detail.hide()
            return
        self.detail.loading(self.DETAIL_TITLES[kind])
        self.detail.show()
        self.refresh_detail()
        page = self.detail.parent()
        while page and not isinstance(page, QScrollArea):
            page = page.parent()
        if page:
            QTimer.singleShot(50, lambda: page.ensureWidgetVisible(self.detail, 0, 24))

    def _detail_device(self, key):
        if self.detail_kind and key is not None:
            self.detail_dev[self.detail_kind] = key
            self.detail_prev.pop(self.detail_kind, None)
            self.detail.loading(self.DETAIL_TITLES[self.detail_kind])
            self.refresh_detail()

    def refresh_detail(self):
        kind, s = self.detail_kind, self.last_snap
        if not kind or not s or self.detail_busy:
            return
        self.detail_busy = True
        dev = self.detail_dev.get(kind)
        procs = s["procs"]
        extra = {"threads": sum(p.get("threads", 0) for p in procs.values()), "n_procs": len(procs),
                 "n_apps": sum(1 for p in procs.values() if p.get("app")), "cpu_pct": self.cpu_pct}

        def worker():
            try:
                res = getattr(self, f"_detail_{kind}")(s, dev, extra)
            except Exception as e:
                res = (self.DETAIL_TITLES[kind], [], [("Fehler", str(e))], [], "", "", None)
            ui(lambda: self._show_detail(kind, res))
        threading.Thread(target=worker, daemon=True).start()

    def _show_detail(self, kind, res):
        self.detail_busy = False
        if kind != self.detail_kind:
            return
        title, stats, kv, bars, bar_title, hint, devices = res
        if devices:
            items, cur = devices
            self.detail_dev[kind] = cur
            self.detail.set_devices(items, cur)
        else:
            self.detail.set_devices([], None)
        self.detail.show_data(title, stats, kv, bars, bar_title, hint)

    def _rate(self, kind, key, counters):
        """Raten pro Sekunde aus Zählern (Tupel) seit dem letzten Aufruf – None beim ersten Mal."""
        now = time.time()
        prev = self.detail_prev.get(kind)
        self.detail_prev[kind] = (key, now, counters)
        if not prev or prev[0] != key or now - prev[1] <= 0.2:
            return None, 0
        dt = now - prev[1]
        return tuple(max(0, a - b) for a, b in zip(counters, prev[2])), dt

    def _detail_cpu(self, s, dev, x):
        st, live = cpu_static(), cpu_live()
        avg, _ = s["mhz"]
        temp = s["temps"].get("cpu")
        stats = [("Auslastung", f"{x['cpu_pct']:.0f} %" if x["cpu_pct"] is not None else "—"),
                 ("Geschwindigkeit", f"{avg / 1000:.2f} GHz" if avg else "—"),
                 ("Temperatur", f"{temp:.0f} °C" if temp is not None else "—"),
                 ("Betriebszeit", fmt_uptime(s["up"])),
                 ("Prozesse", x["n_procs"]), ("Threads", x["threads"]),
                 ("Handles", live["handles"] if live["handles"] is not None else "—"),
                 ("Last 1 / 5 / 15 Min", " / ".join(s["load"]))]
        c = st["caches"]
        kv = [("Modell", st["model"]),
              ("Basisgeschwindigkeit", fmt_ghz(st["base"])),
              ("Max. Geschwindigkeit", fmt_ghz(st["max"])),
              ("Sockets", st["sockets"]), ("Kerne", st["cores"]), ("Virtuelle Prozessoren", st["threads"]),
              ("Virtualisierung", st["virt"]), ("Virtuelle Maschine", st["vm"]),
              ("L1-Cache", fmt_bytes(c.get("L1")) if c.get("L1") else "—"),
              ("L2-Cache", fmt_bytes(c.get("L2")) if c.get("L2") else "—"),
              ("L3-Cache", fmt_bytes(c.get("L3")) if c.get("L3") else "—"),
              ("CPUfreq-Treiber", st["driver"]), ("CPUfreq-Regler", live["governor"]),
              ("Energiemodus", live["epp"]), ("Turbo / Boost", live["boost"])]
        return (f"Prozessor · {st['model']}", stats, kv, [], "",
                "Handles = geöffnete Dateien und Verbindungen im ganzen System. Die Last zeigt, wie viele Prozesse "
                "im Schnitt auf Rechenzeit warten – mehr als die Zahl der Threads heißt Überlastung.", None)

    def _detail_ram(self, s, dev, x):
        m = s["mem"]
        total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
        cache = m.get("Cached", 0) + m.get("Buffers", 0) + m.get("SReclaimable", 0)
        swt, swf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
        z = zram_stats()
        hw = memory_hw()
        stats = [("In Verwendung", fmt_bytes(total - avail)), ("Verfügbar", fmt_bytes(avail)),
                 ("Zugesichert", f"{fmt_bytes(m.get('Committed_AS'))} / {fmt_bytes(m.get('CommitLimit'))}"),
                 ("Im Cache", fmt_bytes(cache)),
                 ("Verwendeter Swap", fmt_bytes(swt - swf)), ("Verfügbarer Swap", fmt_bytes(swf))]
        if z:
            stats += [("Komprimiert (zram)", fmt_bytes(z[1])), ("Ersparnis", fmt_bytes(max(0, z[0] - z[1])))]
        kv = [("Gesamt", fmt_bytes(total)),
              ("Geschwindigkeit", hw.get("speed", "—")), ("Steckplätze verwendet", hw.get("slots", "—")),
              ("Formfaktor", hw.get("form", "—")), ("Typ", hw.get("type", "—")),
              ("Hersteller", hw.get("vendor", "—")),
              ("Gemeinsam genutzt", fmt_bytes(m.get("Shmem"))), ("Kernel (Slab)", fmt_bytes(m.get("Slab"))),
              ("Noch zu schreiben", fmt_bytes(m.get("Dirty")))]
        return ("Arbeitsspeicher", stats, kv, [("Belegt", total - avail, total)], "Belegung",
                "„Im Cache“ ist Speicher für zuletzt gelesene Dateien – er wird sofort freigegeben, wenn Programme "
                "ihn brauchen. „Zugesichert“ ist, was Programme angefordert haben (auch ungenutzt)."
                + ("" if hw else " Takt und Steckplätze meldet dieses System nicht (udev-DMI-Daten fehlen)."), None)

    def _detail_disk(self, s, dev, x):
        stats_all = diskstats()
        if not stats_all:
            return ("Datenträger", [], [("Hinweis", "keine Laufwerke gefunden")], [], "", "", None)
        names = sorted(stats_all, key=lambda n: (not n.startswith("nvme"), n))
        if dev not in stats_all:
            dev = next((n for n in names if disk_static(n)["system"]), names[0])
        st = disk_static(dev)
        rd, wr, io_ms, ios, wait_ms = stats_all[dev]
        d, dt = self._rate("disk", dev, (rd, wr, io_ms, ios, wait_ms))
        temp = None
        for base in (f"/sys/block/{dev}/device", f"/sys/block/{dev}/device/device"):
            hw = _hwmon_dir(base)
            if hw and _first_line(f"{hw}/temp1_input").isdigit():
                temp = int(_first_line(f"{hw}/temp1_input")) / 1000
                break
        stats = [("Lesegeschwindigkeit", f"{fmt_bytes(d[0] / dt)}/s" if d else "…"),
                 ("Schreibgeschwindigkeit", f"{fmt_bytes(d[1] / dt)}/s" if d else "…"),
                 ("Aktive Zeit", f"{min(100, d[2] / (dt * 10)):.0f} %" if d else "…"),
                 ("Ø Antwortzeit", (f"{d[4] / d[3]:.2f} ms" if d[3] else "0 ms") if d else "…"),
                 ("Insgesamt gelesen", fmt_bytes(rd)), ("Insgesamt geschrieben", fmt_bytes(wr))]
        if temp is not None:
            stats.append(("Temperatur", f"{temp:.0f} °C"))
        kv = [("Modell", st["model"]), ("Kapazität", fmt_bytes(st["size"])),
              ("Formatiert", fmt_bytes(st["formatted"]) if st["formatted"] else "—"),
              ("Systemdatenträger", "Ja" if st["system"] else "Nein"), ("Typ", st["type"]),
              ("WWN", st["wwn"]), ("Seriennummer", st["serial"])]
        bars = [(f"{p['path']} · {p['fs'] or '—'}" + (f" · {short_path(p['mount'])}" if p["mount"] else ""),
                 int(p["used"]), int(p["fssize"]))
                for p in st["parts"] if str(p.get("fssize") or "").isdigit() and str(p.get("used") or "").isdigit()]
        items = [(n + (f" · {disk_static(n)['model']}" if disk_static(n)["model"] != "—" else ""), n) for n in names]
        return (f"Datenträger · {dev}", stats, kv, bars, "Partitionen (eingehängt)",
                "Aktive Zeit = Anteil der Zeit, in der das Laufwerk beschäftigt war. Werte seit dem Systemstart.",
                (items, dev))

    def _detail_gpu(self, s, dev, x):
        gl = s.get("gpus") or []
        if not gl:
            return ("Grafik", [], [("Hinweis", "keine Grafikkarte erkannt")], [], "", "", None)
        cards = [g.get("card") for g in gl]
        if dev not in cards:
            dev = next((g["card"] for g in gl if g.get("busy") is not None), cards[0])
        g = gl[cards.index(dev)]
        nvs = [gg for gg in gl if gg.get("name", "").startswith("NVIDIA")]
        st, lv = gpu_static(dev), gpu_live(g, nvs.index(g) if g in nvs else 0)
        temp = lv.get("temp", s["temps"].get("gpu") if len(gl) == 1 else None)

        def clk(a, b):
            if not a:
                return "—"
            return f"{a / 1000:.2f} GHz" + (f" / {b / 1000:.2f} GHz" if b else "")
        stats = [("Auslastung", f"{g['busy']} %" if g.get("busy") is not None else "—"),
                 ("Taktgeschwindigkeit", clk(lv.get("clk"), lv.get("clk_max"))),
                 ("Leistungsaufnahme", (f"{lv['power']:.1f} W" + (f" / {lv['power_cap']:.0f} W"
                                                                   if lv.get("power_cap") else ""))
                  if lv.get("power") is not None else "—"),
                 ("Speicherverbrauch", f"{fmt_bytes(g['vram_used'])} / {fmt_bytes(g['vram_total'])}"
                  if g.get("vram_total") else "—"),
                 ("Speichertakt", clk(lv.get("mclk"), lv.get("mclk_max"))),
                 ("Temperatur", f"{temp:.0f} °C" if temp is not None else "—")]
        if lv.get("enc") is not None:
            stats += [("Video kodieren", f"{lv['enc']:.0f} %"), ("Video dekodieren", f"{lv['dec']:.0f} %")]
        if lv.get("fan"):
            stats.append(("Lüfter", lv["fan"]))
        kv = [("Modell", g.get("name", "—")),
              ("Treiber", st["driver"] + (f" {lv['nv_driver']}" if lv.get("nv_driver") else "")),
              ("OpenGL-Version", st["gl"]), ("Vulkan-Version", st["vk"]),
              ("PCI-Express-Geschwindigkeit", lv.get("nv_link") or st["link"] or "—"),
              ("Max. PCI-Express-Geschwindigkeit", lv.get("nv_link_max") or st["link_max"] or "—"),
              ("PCI-Busadresse", st["bus"] if st["bus"] != "—" else lv.get("nv_bus", "—"))]
        items = [(gg.get("name", gg.get("card")), gg.get("card")) for gg in gl]
        hint = "" if (st["gl"] != "—" or st["vk"] != "—") else \
            "OpenGL- und Vulkan-Version zeigt Tuxdex, wenn mesa-utils (glxinfo) bzw. vulkan-tools installiert sind."
        return (f"Grafik · {g.get('name', dev)}", stats, kv, [], "", hint, (items, dev))

    def _detail_net(self, s, dev, x):
        net = {k: v for k, v in s["net"].items() if k != "lo"}
        if not net:
            return ("Netzwerk", [], [("Hinweis", "keine Netzwerkschnittstelle gefunden")], [], "", "", None)
        names = sorted(net, key=lambda n: (_first_line(f"/sys/class/net/{n}/operstate") != "up",
                                           iface_kind(n) in ("Virtuell",), n))
        if dev not in net:
            dev = internet_route_dev() if internet_route_dev() in net else names[0]
        rx, tx = net[dev]
        d, dt = self._rate("net", dev, (rx, tx))
        st = net_static(dev)
        addrs = self.detail_prev.get(("addr", dev))
        if not addrs or time.time() - addrs[0] > 30:
            try:
                data = json.loads(_cmd_out(["ip", "-j", "addr", "show", "dev", dev]) or "[]")
                info = data[0].get("addr_info", []) if data else []
            except Exception:
                info = []
            addrs = (time.time(), [f"{a['local']}/{a.get('prefixlen', '')}" for a in info if a.get("family") == "inet"],
                     [a["local"] for a in info if a.get("family") == "inet6"])
            self.detail_prev[("addr", dev)] = addrs
        stats = [("Empfangen", f"{fmt_bytes(d[0] / dt)}/s" if d else "…"),
                 ("Senden", f"{fmt_bytes(d[1] / dt)}/s" if d else "…"),
                 ("Insgesamt empfangen", fmt_bytes(rx)), ("Insgesamt gesendet", fmt_bytes(tx))]
        kv = [("Typ", iface_kind(dev)), ("Status", _first_line(f"/sys/class/net/{dev}/operstate") or "—"),
              ("Verbindungsgeschwindigkeit", st["speed"]), ("Treiber", st["driver"]),
              ("MAC-Adresse", st["mac"]), ("MTU", st["mtu"]),
              ("IPv4", ", ".join(addrs[1]) or "—"), ("IPv6", "\n".join(addrs[2]) or "—")]
        items = [(f"{n} · {iface_kind(n)}", n) for n in names]
        return (f"Netzwerk · {dev}", stats, kv, [], "", "Summen seit dem Systemstart.", (items, dev))

    def _detail_bat(self, s, dev, x):
        bats, ac = s["bat"]
        if not bats:
            return ("Akku", [], [("Stromversorgung", "kein Akku – Netzbetrieb")], [], "", "", None)
        b = bats[0]
        e = battery_extra(b["name"])
        rest = "—"
        if b["hours"]:
            rest = f"{int(b['hours'])}:{int((b['hours'] % 1) * 60):02d} h"
        stats = [("Ladestand", f"{b['capacity']} %" if b["capacity"] is not None else "—"),
                 ("Status", BAT_STATUS.get(b["status"], b["status"])),
                 ("Leistung", f"{b['watts']:.1f} W" if b["watts"] is not None else "—"),
                 ("Noch" if b["status"] == "Discharging" else "Voll in", rest),
                 ("Zustand", f"{b['health']:.0f} %" if b["health"] else "—"),
                 ("Ladezyklen", e["cycles"] if e["cycles"] is not None else "—")]
        kv = [("Modell", b["model"] or "—"), ("Technologie", e["tech"]),
              ("Spannung", f"{e['volt']:.2f} V" if e["volt"] else "—"),
              ("Kapazität jetzt", f"{e['full']:.1f} Wh" if e["full"] else "—"),
              ("Kapazität neu", f"{e['design']:.1f} Wh" if e["design"] else "—"),
              ("Ladegrenze", f"{e['limit']} %" if e["limit"] else "keine"),
              ("Netzteil", "angeschlossen" if ac else "nicht angeschlossen" if ac is False else "—")]
        return ("Akku", stats, kv, [], "",
                "Zustand = heutige volle Kapazität im Vergleich zum Neuzustand.", None)

    def _detail_swap(self, s, dev, x):
        m = s["mem"]
        swt, swf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
        z = zram_stats()
        devs = swap_devices()
        stats = [("Belegt", fmt_bytes(swt - swf)), ("Gesamt", fmt_bytes(swt)),
                 ("Swappiness", _first_line("/proc/sys/vm/swappiness") or "—")]
        if z:
            stats += [("Komprimiert (zram)", fmt_bytes(z[1])), ("Ersparnis", fmt_bytes(max(0, z[0] - z[1])))]
        kv = [(short_path(p), f"{'zram' if 'zram' in p else typ} · Priorität {prio}") for p, typ, _, _, prio in devs] \
            or [("Swap", "nicht aktiv")]
        bars = [(short_path(p), used, size) for p, _, size, used, _ in devs]
        return ("Swap", stats, kv, bars, "Belegung je Gerät",
                "Swappiness (0–200): je höher, desto früher lagert Linux ungenutzten Speicher aus.", None)

    def _detail_sys(self, s, dev, x):
        live = cpu_live()
        boot = datetime.fromtimestamp(time.time() - s["up"]).strftime("%d.%m.%Y %H:%M")
        stats = [("Prozesse", x["n_procs"]), ("davon Programme", x["n_apps"]), ("Threads", x["threads"]),
                 ("Handles", live["handles"] if live["handles"] is not None else "—"),
                 ("Betriebszeit", fmt_uptime(s["up"])), ("Last 1 / 5 / 15 Min", " / ".join(s["load"]))]
        kv = [("Kernel", os.uname().release), ("Gestartet am", boot)]
        fl = fans()
        kv += [(f"Lüfter · {name}", f"{rpm} U/min  ({chip})") for name, chip, rpm in fl] or \
            [("Lüfter", "keine Drehzahl gemeldet")]
        for k, v in sorted(s["temps"].items()):
            kv.append((f"Temperatur · {k.upper()}", f"{v:.0f} °C"))
        return ("System & Lüfter", stats, kv, [], "",
                "" if fl else "Lüfterdrehzahlen erscheinen, wenn der Treiber sie meldet (bei vielen Laptops nur mit "
                "passendem Modul, z. B. thinkpad_acpi, dell-smm-hwmon, asus-wmi oder nct6775).", None)

    # ---- Tabelle ----------------------------------------------------------

    def _render(self):
        q = self.search.text().strip().lower()
        mode = self.filter.currentIndex()
        me = os.getuid()
        rows = []
        for pid, p in self.data.items():
            if mode == 1 and not p.get("app"):
                continue
            if mode == 2 and p.get("app"):
                continue
            if mode == 3 and p["uid"] != me:
                continue
            app_name = p["app"][1] if p.get("app") else ""
            if q and q not in f"{p['name']} {app_name} {pid} {p['user']} {p['cmd']}".lower():
                continue
            rows.append((pid, p))

        # Gruppen: Programme nach Name, Hintergrundprozesse nach Prozessname (kworker/0:1 → kworker)
        groups = {}
        for pid, p in rows:
            if self.cb_group.isChecked():
                key = "app:" + p["app"][1] if p.get("app") else "proc:" + p["name"].split("/")[0]
            else:
                key = f"pid:{pid}"
            groups.setdefault(key, []).append((pid, p))

        sel = self._selected_key()
        vbar = self.tree.verticalScrollBar().value()
        sort_col = self.tree.header().sortIndicatorSection()
        sort_ord = self.tree.header().sortIndicatorOrder()
        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        mono = QFont(FONTS["mono"], 10)
        hot = QColor(COLORS["warn"])
        sel_item = None
        for key, members in groups.items():
            if len(members) == 1:
                it = self._proc_item(*members[0], mono, hot)
                self.tree.addTopLevelItem(it)
                if sel in (key, f"pid:{members[0][0]}"):
                    sel_item = it
                continue
            members.sort(key=lambda m: m[1]["rss"], reverse=True)
            first = members[0][1]
            app = first.get("app")
            label = app[1] if app else first["name"].split("/")[0]
            cpu = sum(p["cpu"] for _, p in members)
            rss = sum(p["rss"] for _, p in members)
            ios = [p.get("io_rate") for _, p in members if p.get("io_rate") is not None]
            io = sum(ios) if ios else None
            users = sorted({p["user"] for _, p in members})
            g = _SortItem([f"{label}  ({len(members)})", "", ", ".join(users),
                           f"{cpu:.1f} %", fmt_bytes(rss),
                           "—" if io is None else (f"{fmt_bytes(io)}/s" if io else "0"),
                           energy_label(cpu), f"{len(members)} Prozesse", first["cmd"]],
                          [label.lower(), len(members), ", ".join(users), cpu, rss, -1 if io is None else io,
                           cpu, "", first["cmd"]])
            g.setData(0, Qt.UserRole, key)
            self._style_item(g, label, app, first, mono, hot, cpu)
            f = g.font(0)
            f.setWeight(QFont.DemiBold)
            g.setFont(0, f)
            for pid, p in members:
                ch = self._proc_item(pid, p, mono, hot, in_group=True)
                g.addChild(ch)
                if sel == f"pid:{pid}":
                    sel_item = ch
            self.tree.addTopLevelItem(g)
            if key in self.expanded:
                g.setExpanded(True)
            if sel == key:
                sel_item = g
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(sort_col, sort_ord)
        if sel_item is not None:
            self.tree.setCurrentItem(sel_item)
        self.tree.blockSignals(False)
        self.tree.verticalScrollBar().setValue(vbar)
        self._sel_changed()

    def _style_item(self, it, label, app, p, mono, hot, cpu):
        if app:
            ic = themed_icon(app[0])
            it.setIcon(0, ic if not ic.isNull() else letter_icon(label))
        elif not p["cmd"].startswith("["):
            it.setIcon(0, letter_icon(p["name"]))
        for c in range(len(self.COLS)):
            if c in (1, 3, 4, 5, 8):
                it.setFont(c, mono)
            if c in (1, 3, 4, 5):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            if c in (3, 6) and cpu >= 50:
                it.setForeground(c, hot)

    def _proc_item(self, pid, p, mono, hot, in_group=False):
        app = p.get("app")
        label = p["name"] if in_group or not app else app[1]     # im Ordner: echter Prozessname
        iorate = p.get("io_rate")
        it = _SortItem([label, str(pid), p["user"], f"{p['cpu']:.1f} %", fmt_bytes(p["rss"]),
                        "—" if iorate is None else (f"{fmt_bytes(iorate)}/s" if iorate else "0"),
                        energy_label(p["cpu"]), STATES.get(p["state"], p["state"]), p["cmd"]],
                       [label.lower(), pid, p["user"], p["cpu"], p["rss"], -1 if iorate is None else iorate,
                        p["cpu"], p["state"], p["cmd"]])
        it.setData(0, Qt.UserRole, f"pid:{pid}")
        self._style_item(it, label, app, p, mono, hot, p["cpu"])
        tip = f"{label} · PID {pid}\n{p['cmd']}"
        for c in range(len(self.COLS)):
            it.setToolTip(c, tip)
        return it

    def _selected_key(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it and it.isSelected() else None

    def _selected_pids(self):
        """(anzeigename, [pids]) der Auswahl – bei einer Gruppe alle Prozesse darin."""
        it = self.tree.currentItem()
        if not it or not it.isSelected():
            return None, []
        key = it.data(0, Qt.UserRole) or ""
        if key.startswith("pid:"):
            pid = int(key[4:])
            p = self.data.get(pid)
            return ((p["app"][1] if p.get("app") else p["name"]) if p else "?"), [pid]
        pids = [int(it.child(i).data(0, Qt.UserRole)[4:]) for i in range(it.childCount())]
        return it.text(0).rsplit("  (", 1)[0], [pid for pid in pids if pid in self.data]

    def _selected_pid(self):
        _, pids = self._selected_pids()
        return pids[0] if len(pids) == 1 else None

    def _sel_changed(self):
        name, pids = self._selected_pids()
        ok = bool(pids) and all(pid > 1 for pid in pids)
        for b in (self.b_nice_down, self.b_nice_up, self.b_term, self.b_kill):
            b.setEnabled(ok)
        self.b_term.setText("Alle beenden" if len(pids) > 1 else "Beenden")
        self.b_kill.setText("Alle erzwingen" if len(pids) > 1 else "Erzwingen")
        if len(pids) > 1:
            rss = sum(self.data[p]["rss"] for p in pids)
            self.sel_label.setText(f"{name} · {len(pids)} Prozesse · {fmt_bytes(rss)}")
        elif pids:
            p = self.data[pids[0]]
            self.sel_label.setText(f"{name} · PID {pids[0]} · {p['user']} · nice {p['nice']}")
        else:
            self.sel_label.setText("Kein Prozess ausgewählt")

    # ---- System-Ansicht ---------------------------------------------------

    def load_versions(self):
        def worker():
            v = version_status()
            ui(lambda: self._show_versions(v))
        threading.Thread(target=worker, daemon=True).start()

    def _show_versions(self, rows):
        self._clear(self.ver_box)
        for title, tone, short, detail in rows:
            r = CheckRow(title)
            r.set(tone, short, detail)
            self.ver_box.addWidget(r)

    def load_system(self):
        self.load_versions()

        def worker():
            info = system_summary()
            ifaces, gw, dns = net_interfaces()
            bats, ac = batteries()
            ui(lambda: self._show_system(info, ifaces, gw, dns, bats, ac))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                TaskTab._clear(item.layout())

    def _show_system(self, info, ifaces, gw, dns, bats, ac):
        self._clear(self.sys_grid)
        for i, (k, v) in enumerate(info.items()):
            self.sys_grid.addLayout(Field(k, Label(v, "Value", wrap=True)), i // 2, i % 2)
        self._clear(self.net_box)
        for it in ifaces:
            if it["kind"] == "Virtuell" and not it["ipv4"]:
                continue
            row = QHBoxLayout()
            row.setSpacing(12)
            up = it["state"] in ("UP", "UNKNOWN") and (it["ipv4"] or it["ipv6"])
            row.addWidget(StatusBadge("ok" if up else "off", f"{it['kind']} · {it['name']}"))
            addrs = ", ".join(it["ipv4"] + it["ipv6"][:1]) or "keine Adresse"
            lab = Label(addrs, "Value", wrap=True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(lab, 1)
            row.addWidget(Label(it["mac"], "Hint"))
            self.net_box.addLayout(row)
        extra = Label(f"Standard-Gateway: {gw or '—'}   ·   DNS-Server: {', '.join(dns) or '—'}", "Hint", wrap=True)
        extra.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.net_box.addWidget(extra)
        self._clear(self.bat_box)
        if not bats:
            self.bat_box.addWidget(Label("Kein Akku gefunden – das Gerät läuft am Netz.", "Muted"))
        for b in bats:
            parts = [f"{b['capacity']} %" if b["capacity"] is not None else "—",
                     BAT_STATUS.get(b["status"], b["status"])]
            if b["watts"] is not None:
                parts.append(f"{b['watts']:.1f} W")
            if b["health"]:
                parts.append(f"Zustand {b['health']:.0f} % der Originalkapazität")
            if b["model"]:
                parts.append(b["model"])
            self.bat_box.addWidget(Label("  ·  ".join(parts), "Value", wrap=True))

    def check_public_ip(self):
        self.pub_label.setText("Öffentliche IP wird geprüft …")

        def worker():
            try:
                d = public_ip_info()
                txt = (f"Öffentliche IP: <b>{d.get('ip', '?')}</b> · {d.get('city') or ''} "
                       f"{d.get('country') or ''} · {d.get('organization') or ''} · "
                       + (f'<span style="color:{COLORS["ok"]}">● über Mullvad-VPN</span>'
                          if d.get("mullvad_exit_ip") else
                          f'<span style="color:{COLORS["warn"]}">▲ nicht über Mullvad</span>'))
            except Exception as e:
                txt = f"Öffentliche IP konnte nicht ermittelt werden ({e})."
            ui(lambda: self.pub_label.setText(txt))
        threading.Thread(target=worker, daemon=True).start()

    # ---- Aktionen ---------------------------------------------------------

    def _as_root(self, p):
        return p["uid"] != os.getuid()

    def kill(self, sig):
        nm, pids = self._selected_pids()
        procs = [(pid, self.data[pid]) for pid in pids if pid in self.data]
        if not procs:
            return
        hard = sig == signal.SIGKILL
        root = [pid for pid, p in procs if self._as_root(p)]
        what = f"{nm} (PID {procs[0][0]})" if len(procs) == 1 else f"{nm} – alle {len(procs)} Prozesse"
        if not ask_confirm(self, "Prozess erzwingen" if hard else "Prozess beenden",
                           f"{what} {'sofort stoppen' if hard else 'beenden'}?"
                           + ("\n\nUngespeicherte Daten gehen verloren." if hard else "")
                           + ("\n\nEinige gehören anderen Benutzern – dafür sind root-Rechte nötig." if root else ""),
                           "Erzwingen" if hard else "Beenden", danger=hard):
            return
        gone = 0
        for pid, p in procs:
            if pid in root:
                continue
            try:
                os.kill(pid, sig)
                gone += 1
            except ProcessLookupError:
                pass
            except PermissionError:
                root.append(pid)
        if root:
            if not self.app.priv.ensure(self):
                return
            run_capture_async(["kill", f"-{int(sig)}"] + [str(x) for x in root],
                              lambda rc, o, e: (self.app.set_status(
                                  f"{nm}: " + ("beendet." if rc == 0 else f"Fehler: {e.strip()}")), self.tick()),
                              needs_sudo=True)
            return
        self.app.set_status(f"Signal an {nm} gesendet" + (f" ({gone} Prozesse)." if gone > 1 else "."))
        QTimer.singleShot(300, self.tick)

    def renice(self, delta):
        nm, pids = self._selected_pids()
        procs = [(pid, self.data[pid]) for pid in pids if pid in self.data]
        if not procs:
            return
        needs_root = delta < 0 or any(self._as_root(p) for _, p in procs)
        if needs_root and not self.app.priv.ensure(self):
            return
        cmds = [f"renice -n {max(-20, min(19, p['nice'] + delta))} -p {pid}" for pid, p in procs]
        run_capture_async(["sh", "-c", " ; ".join(cmds)],
                          lambda rc, o, e: (self.app.set_status(
                              f"Priorität von {nm} geändert." if rc == 0
                              else f"renice fehlgeschlagen: {e.strip()}"), self.tick()),
                          needs_sudo=needs_root)

# --------------------------------------------------------------------------
# Modul: Antivirus (ClamAV)
# --------------------------------------------------------------------------

QUARANTINE_DIR = os.path.join(os.path.expanduser("~/.local/share"), "tuxdex", "quarantine")
QUARANTINE_INDEX = os.path.join(QUARANTINE_DIR, "index.json")
AV_LAST = os.path.join(CACHE_DIR, "clamav_last.json")
FOUND_RE = re.compile(r"^(.*): (.+) FOUND$")


_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


def parse_c_date(text):
    """Englisches Datum wie „Sat Sep 26 08:24:13 2026“ unabhängig von der Systemsprache lesen."""
    m = re.search(r"([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(\d{4})", text)
    if not m or m.group(1).lower() not in _MONTHS:
        raise ValueError(f"unbekanntes Datum: {text!r}")
    return datetime(int(m.group(6)), _MONTHS[m.group(1).lower()], int(m.group(2)),
                    int(m.group(3)), int(m.group(4)), int(m.group(5)))


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=1)
    except Exception:
        pass


class AntivirusTab(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.run = None
        self.found = []          # [{path, threat, status}]
        self.scan_started = None
        self.summary = {}

        self.badge = StatusBadge("off", "Prüfe …")
        self.lay.addLayout(page_header("Antivirus (ClamAV)", self.badge,
                                       Button("↻", "icon", self.refresh_status, "Status neu prüfen")))

        # --- Status ---
        st = Panel("Schutz-Status")
        self.st_grid = QGridLayout()
        self.st_grid.setHorizontalSpacing(24)
        self.st_grid.setVerticalSpacing(10)
        st.body.addLayout(self.st_grid)
        self.st_note = Label("", "Warn", wrap=True)
        self.st_note.setTextFormat(Qt.RichText)
        st.body.addWidget(self.st_note)
        b = QHBoxLayout()
        b.setSpacing(8)
        self.b_fresh = Button("Signaturen aktualisieren", "ghost", self.update_signatures)
        self.b_auto = Button("Automatische Updates aktivieren", "ghost", self.enable_auto)
        for x in (self.b_fresh, self.b_auto):
            b.addWidget(x)
        b.addStretch(1)
        st.body.addLayout(b)
        self.lay.addWidget(st)

        # --- Scan ---
        sc = Panel("Scan")
        r = QHBoxLayout()
        r.setSpacing(8)
        self.target = QComboBox()
        home = os.path.expanduser("~")
        self.target.addItem("Persönlicher Ordner  (~)", home)
        if os.path.isdir(os.path.join(home, "Downloads")):
            self.target.addItem("Downloads  (~/Downloads)", os.path.join(home, "Downloads"))
        self.target.addItem("Ganzes System  (/)", "/")
        self.target.addItem("Eigener Ordner …", "__custom__")
        self.target.setMinimumWidth(280)
        self.target.activated.connect(self._target_chosen)
        r.addLayout(Field("Was scannen?", self.target))
        r.addStretch(1)
        right = QVBoxLayout()
        right.addStretch(1)
        rb = QHBoxLayout()
        rb.setSpacing(8)
        self.b_scan = Button("Scan starten", "primary", self.start_scan)
        self.b_stop = Button("Scan abbrechen", "danger", self.stop_scan)
        self.b_stop.hide()
        rb.addWidget(self.b_scan)
        rb.addWidget(self.b_stop)
        right.addLayout(rb)
        r.addLayout(right)
        sc.body.addLayout(r)
        opts = QHBoxLayout()
        opts.setSpacing(16)
        self.cb_root = QCheckBox("Mit root-Rechten (nötig für Systemordner)")
        self.cb_quar = QCheckBox("Funde automatisch in Quarantäne verschieben")
        opts.addWidget(self.cb_root)
        opts.addWidget(self.cb_quar)
        opts.addStretch(1)
        sc.body.addLayout(opts)
        # Live-Status des Scans
        self.scan_box = QWidget()
        sbx = QVBoxLayout(self.scan_box)
        sbx.setContentsMargins(0, 4, 0, 0)
        sbx.setSpacing(8)
        srow = QHBoxLayout()
        srow.setSpacing(12)
        self.scan_state = StatusBadge("off", "Nicht aktiv")
        srow.addWidget(self.scan_state)
        self.scan_time = Label("", "Value")
        srow.addWidget(self.scan_time)
        srow.addStretch(1)
        self.scan_eta = Label("", "Muted")
        srow.addWidget(self.scan_eta)
        sbx.addLayout(srow)
        self.scan_bar = ProgressBar()
        sbx.addWidget(self.scan_bar)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        self.st_files = Label("—", "Value")
        self.st_bytes = Label("—", "Value")
        self.st_rate = Label("—", "Value")
        self.st_found = Label("0", "Value")
        for i, (k, w) in enumerate((("Dateien", self.st_files), ("Datenmenge", self.st_bytes),
                                    ("Tempo", self.st_rate), ("Funde", self.st_found))):
            grid.addLayout(Field(k, w), 0, i)
        sbx.addLayout(grid)
        self.scan_cur = Label("", "Hint")
        self.scan_cur.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sbx.addWidget(self.scan_cur)
        self.scan_box.hide()
        sc.body.addWidget(self.scan_box)
        self.progress = Label("", "Muted")
        sc.body.addWidget(self.progress)
        self.last_scan = Label("", "Hint")
        sc.body.addWidget(self.last_scan)
        self.lay.addWidget(sc)
        self.scan_panel = sc

        # --- Funde ---
        fp = Panel("Funde")
        self.ftable = QTableWidget(0, 3)
        self.ftable.setHorizontalHeaderLabels(["DATEI", "BEDROHUNG", "STATUS"])
        self.ftable.verticalHeader().setVisible(False)
        self.ftable.setShowGrid(False)
        self.ftable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ftable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ftable.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.ftable.setColumnWidth(0, 460)
        self.ftable.setColumnWidth(1, 340)
        self.ftable.horizontalHeader().setStretchLastSection(True)
        self.ftable.setMinimumHeight(150)
        self.ftable.itemSelectionChanged.connect(self._found_sel)
        self.f_empty = Label("Noch keine Funde.", "Muted")
        fp.body.addWidget(self.f_empty)
        fp.body.addWidget(self.ftable)
        self.ftable.hide()
        fa = QHBoxLayout()
        fa.setSpacing(8)
        self.b_q = Button("In Quarantäne verschieben", "primary", self.quarantine_selected)
        self.b_show = Button("Ordner öffnen", "ghost", self.show_folder)
        self.b_del = Button("Endgültig löschen", "danger", self.delete_selected)
        fa.addWidget(self.b_q)
        fa.addWidget(self.b_show)
        fa.addStretch(1)
        fa.addWidget(self.b_del)
        fp.body.addLayout(fa)
        self.lay.addWidget(fp)
        self.found_panel = fp

        # --- Quarantäne ---
        qp = Panel("Quarantäne", [Button("↻", "icon", self.refresh_quarantine, "Neu laden")])
        self.qtable = QTableWidget(0, 3)
        self.qtable.setHorizontalHeaderLabels(["URSPRÜNGLICHER ORT", "BEDROHUNG", "SEIT"])
        self.qtable.verticalHeader().setVisible(False)
        self.qtable.setShowGrid(False)
        self.qtable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.qtable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.qtable.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.qtable.setColumnWidth(0, 460)
        self.qtable.setColumnWidth(1, 340)
        self.qtable.horizontalHeader().setStretchLastSection(True)
        self.qtable.setMinimumHeight(120)
        self.qtable.itemSelectionChanged.connect(self._q_sel)
        self.q_empty = Label("Die Quarantäne ist leer.", "Muted")
        qp.body.addWidget(self.q_empty)
        qp.body.addWidget(self.qtable)
        qa = QHBoxLayout()
        qa.setSpacing(8)
        self.b_restore = Button("Wiederherstellen", "ghost", self.restore_selected)
        self.b_qdel = Button("Endgültig löschen", "danger", self.qdelete_selected)
        qa.addWidget(self.b_restore)
        qa.addStretch(1)
        qa.addWidget(self.b_qdel)
        qp.body.addLayout(qa)
        qp.body.addWidget(Label(f"Dateien in Quarantäne liegen ohne Ausführungsrechte in {short_path(QUARANTINE_DIR)}.",
                                "Hint", wrap=True))
        self.lay.addWidget(qp)

        out = Panel("Ausgabe")
        self.log = LogView(150)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._tick)

        self._found_sel()
        self._render_found()
        self.refresh_quarantine()
        self._show_last()
        self.refresh_status()

    # ---- Status -----------------------------------------------------------

    def _st_item(self, i, label, value, name="Value"):
        self.st_grid.addLayout(Field(label, Label(value, name, wrap=True)), i // 3, i % 3)

    def refresh_status(self):
        def worker():
            res = {"installed": which("clamscan")}
            if res["installed"]:
                r = subprocess.run(["clamscan", "--version"], capture_output=True, text=True)
                res["version"] = r.stdout.strip()
                for svc in ("clamav-freshclam", "clamav-daemon"):
                    try:
                        a = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
                        res[svc] = a.stdout.strip()
                    except Exception:
                        res[svc] = "unbekannt"
            ui(lambda: self._show_status(res))
        threading.Thread(target=worker, daemon=True).start()

    def _show_status(self, res):
        while self.st_grid.count():
            lay = self.st_grid.takeAt(0).layout()
            if lay:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        w.deleteLater()
        self.installed = res["installed"]
        for x in (self.b_fresh, self.b_auto, self.b_scan):
            x.setEnabled(self.installed)
            x.setVisible(self.installed)
        self.scan_panel.setVisible(self.installed)
        self.found_panel.setVisible(self.installed)
        if not self.installed:
            self.badge.set("off", "Nicht installiert")
            self._st_item(0, "ClamAV", "nicht installiert")
            self.st_note.setText(f'<span style="color:{COLORS["muted"]}">ClamAV ist nicht installiert. Tuxdex '
                                 f'funktioniert auch ohne. Sobald ClamAV auf dem System vorhanden ist, lässt es sich '
                                 f'hier bedienen.</span>')
            return
        # "ClamAV 1.4.1/27411/Wed Sep 25 08:34:07 2026"
        ver = res.get("version", "")
        parts = ver.split("/")
        engine = parts[0].replace("ClamAV", "").strip() if parts else "?"
        sig_ver = parts[1] if len(parts) > 1 else "—"
        sig_date, age_days = "keine Signaturen", None
        if len(parts) > 2:
            try:
                dt = parse_c_date(parts[2])
                sig_date = dt.strftime("%d.%m.%Y %H:%M")
                age_days = (datetime.now() - dt).days
            except ValueError:
                sig_date = parts[2].strip()
        fresh = res.get("clamav-freshclam") == "active"
        daemon = res.get("clamav-daemon") == "active"
        self.daemon_active = daemon
        self._st_item(0, "Engine", engine)
        try:
            db = [os.path.getmtime(os.path.join("/var/lib/clamav", f)) for f in os.listdir("/var/lib/clamav")
                  if f.endswith((".cvd", ".cld"))]
        except Exception:
            db = []
        if db and age_days is None:
            # Fallback: Dateidatum der Datenbank, falls die Versionszeile nicht lesbar ist
            dt = datetime.fromtimestamp(max(db))
            sig_date, age_days = dt.strftime("%d.%m.%Y %H:%M"), (datetime.now() - dt).days
        self._st_item(1, "Signaturen", f"{sig_date}  (Version {sig_ver})")
        self._st_item(2, "Automatische Updates", "● aktiv" if fresh else "○ aus",
                      "Value")
        self._st_item(3, "Scan-Dienst (clamd)", "● läuft – schnelle Scans" if daemon else "○ aus – Scans mit clamscan")
        notes = []
        if age_days is None:
            self.badge.set("danger", "Keine Signaturen")
            notes.append(f'<span style="color:{COLORS["danger"]}">✕ Es sind noch keine Virensignaturen geladen – '
                         + ("der Dienst lädt sie gerade (beim ersten Mal einige Minuten).</span>" if fresh else
                            "„Signaturen aktualisieren“ klicken.</span>"))
        elif age_days > 3:
            self.badge.set("warn", f"Signaturen {age_days} Tage alt")
            notes.append(f"▲ Die Signaturen sind {age_days} Tage alt. Aktualisieren oder automatische "
                         f"Updates aktivieren.")
        else:
            self.badge.set("ok", "Signaturen aktuell")
        if not fresh:
            notes.append(f'<span style="color:{COLORS["muted"]}">Tipp: Automatische Updates halten die '
                         f'Signaturen täglich aktuell (Dienst clamav-freshclam).</span>')
        self.b_auto.setVisible(not fresh)
        self.b_scan.setEnabled(age_days is not None)
        self.b_scan.setToolTip("" if age_days is not None else "Erst Signaturen laden – ohne sie kann ClamAV nichts erkennen.")
        self.b_fresh.setProperty("variant", "primary" if (age_days is None or age_days > 3) else "ghost")
        repolish(self.b_fresh)
        self.st_note.setText("<br>".join(notes))

    def update_signatures(self):
        if not self.app.priv.ensure(self):
            return
        fresh_active = subprocess.run(["systemctl", "is-active", "clamav-freshclam"],
                                      capture_output=True, text=True).stdout.strip() == "active"
        running = subprocess.run(["pgrep", "-x", "freshclam"], capture_output=True, text=True).stdout.strip()
        if fresh_active:
            # Der Dienst hält die Sperre – ein zweites freshclam würde scheitern.
            # Neustart des Dienstes löst sofort eine Aktualisierung aus.
            self.log.set_text("$ sudo systemctl restart clamav-freshclam\n"
                              "Der Dienst clamav-freshclam läuft bereits und lädt die Signaturen selbst.\n"
                              "Neustart löst sofort eine Prüfung aus – der erste Download (~200 MB) kann "
                              "einige Minuten dauern.\n\n")
            run_streaming(["systemctl", "restart", "clamav-freshclam"], self.log, needs_sudo=True,
                          clear_first=False, on_done=lambda rc: self._watch_freshclam())
            return
        if running:
            self.log.set_text("freshclam läuft bereits (PID " + running.replace("\n", ", ")
                              + ") – warte auf das Ergebnis …\n")
            self._watch_freshclam()
            return
        self.log.set_text("$ sudo freshclam\n")

        def done(rc):
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            if rc != 0:
                self.log.append_text("Tipp: „Automatische Updates aktivieren“ – der Dienst lädt die Signaturen "
                                     "dann selbstständig und täglich.\n")
            self.refresh_status()

        run_streaming(["freshclam"], self.log, needs_sudo=True, clear_first=False, on_done=done)

    def _watch_freshclam(self, rounds=0):
        """Verfolgt den freshclam-Dienst: zeigt neue Meldungen, erkennt Fehler (z. B. Download-Sperre
        des ClamAV-Servers) und hört auf, sobald Signaturen da sind (max. 10 Min.)."""
        def cb(rc, out, err):
            lines = [l for l in out.splitlines() if l.strip()]
            if rc != 0 and not lines:
                lines = ["(Dienst-Meldungen nicht lesbar – bitte oben rechts anmelden)"]
            if lines:
                self.log.append_text("\n".join(lines[-8:]) + "\n")
            text = "\n".join(lines).lower()
            # Signaturen vorhanden?
            ver = subprocess.run(["clamscan", "--version"], capture_output=True, text=True).stdout.strip()
            parts = ver.split("/")
            if len(parts) > 2:
                self.log.append_text(f"● Signaturen sind geladen (Version {parts[1]}, Stand {parts[2].strip()}).\n")
                self.refresh_status()
                return
            # typische Fehler sofort melden statt weiter zu warten
            if any(k in text for k in ("cool-down", "cooldown", "429", "rate limit", "forbidden", "403")):
                self.log.append_text("✕ Der ClamAV-Server lässt gerade keine Downloads zu (zu viele Anfragen von "
                                     "deiner IP, z. B. über ein VPN). Später erneut versuchen oder VPN-Server wechseln.\n")
                self.refresh_status()
                return
            if any(k in text for k in ("can't connect", "connection failed", "could not resolve", "no route")):
                self.log.append_text("✕ Keine Verbindung zum ClamAV-Server – Internet/DNS prüfen.\n")
                self.refresh_status()
                return
            if rounds >= 60:
                self.log.append_text("▲ Noch keine Signaturen nach 10 Minuten – Meldungen oben prüfen.\n")
                self.refresh_status()
                return
            el = (rounds + 1) * 10
            self.badge.set("info", f"Lade Signaturen … {el // 60}:{el % 60:02d}")
            QTimer.singleShot(10000, lambda: self._watch_freshclam(rounds + 1))

        args = ["journalctl", "-u", "clamav-freshclam", "--no-pager", "-o", "cat"]
        args += ["-n", "15"] if rounds == 0 else ["--since", "-10s"]
        run_capture_async(args, cb, needs_sudo=True)

    def enable_auto(self):
        if not self.app.priv.ensure(self):
            return
        self.log.set_text("$ sudo systemctl enable --now clamav-freshclam\n")
        run_streaming(["systemctl", "enable", "--now", "clamav-freshclam"], self.log, needs_sudo=True,
                      clear_first=False, on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"),
                                                             self.refresh_status()))

    # ---- Scan -------------------------------------------------------------

    def _target_chosen(self, idx):
        if self.target.itemData(idx) != "__custom__":
            if self.target.itemData(idx) == "/":
                self.cb_root.setChecked(True)
            return
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Ordner zum Scannen wählen", os.path.expanduser("~"))
        if d:
            i = self.target.findData(d)
            if i < 0:
                self.target.insertItem(self.target.count() - 1, d, d)
                i = self.target.findData(d)
            self.target.setCurrentIndex(i)
        else:
            self.target.setCurrentIndex(0)

    def start_scan(self):
        path = self.target.currentData()
        if not path or path == "__custom__":
            return
        use_root = self.cb_root.isChecked()
        if use_root and not self.app.priv.ensure(self):
            return
        excl = [f"--exclude-dir=^{re.escape(QUARANTINE_DIR)}"]
        prune = [QUARANTINE_DIR]
        if path == "/":
            excl.insert(0, "--exclude-dir=^/(proc|sys|dev|run)(/|$)")
            prune += ["/proc", "/sys", "/dev", "/run"]
        # ohne -i: clamscan meldet jede Datei → daraus Fortschritt, Tempo und „lebt es noch?“
        cmd = ["clamscan", "-r"] + excl + [path]
        self.found, self.summary = [], {}
        self._render_found()
        self.scan_target = path
        self.scan_started = time.time()
        self.sc = {"files": 0, "bytes": 0, "total_files": None, "total_bytes": None, "counting": True,
                   "last_line": time.time(), "last_file": "", "warnings": 0, "proc": None, "count_proc": None,
                   "cancelled": False, "done": False, "first_at": None}
        self.b_scan.hide()
        self.b_stop.show()
        self.scan_box.show()
        self.progress.setText("")
        self.badge.set("info", "Scan läuft")
        self.log.set_text(f"$ {'sudo ' if use_root else ''}{' '.join(shlex.quote(c) for c in cmd)}\n"
                          "(Nur Funde, Warnungen und die Zusammenfassung werden hier angezeigt.)\n")
        sudo = ["sudo", "-n"] if use_root else []
        threading.Thread(target=self._count_worker, args=(sudo, path, prune), daemon=True).start()
        threading.Thread(target=self._scan_worker, args=(sudo + cmd,), daemon=True).start()
        self.tick_timer.start(500)
        self._tick()

    def _count_worker(self, sudo, path, prune):
        """Zählt parallel, wie viele Dateien/Bytes insgesamt anstehen (für Prozent und Restzeit)."""
        sc = self.sc
        expr = []
        for p in prune:
            expr += ["-path", p, "-prune", "-o"]
        cmd = sudo + ["find", path] + expr + ["-type", "f", "-printf", "%s\n"]
        n = size = 0
        try:
            proc = track(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True))
            sc["count_proc"] = proc
            for line in proc.stdout:
                n += 1
                try:
                    size += int(line)
                except ValueError:
                    pass
                if n % 5000 == 0:
                    sc["count_files"], sc["count_bytes"] = n, size
                if sc["cancelled"]:
                    proc.kill()
                    return
            proc.wait()
        except Exception:
            pass
        sc["total_files"], sc["total_bytes"], sc["counting"] = n, size, False

    def _scan_worker(self, cmd):
        sc = self.sc
        try:
            proc = track(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace", bufsize=1))
        except Exception as e:
            ui(lambda m=str(e): (self.log.append_text(f"error: {m}\n"), self._scan_done(2)))
            return
        sc["proc"] = proc
        in_summary = False
        for line in proc.stdout:
            line = line.rstrip("\n")
            sc["last_line"] = time.time()
            if in_summary:
                ui(lambda l=line: self._scan_line(l))
                ui(lambda l=line: self.log.append_text(l + "\n"))
                continue
            if line.startswith("----------- SCAN SUMMARY"):
                in_summary = True
                ui(lambda l=line: self.log.append_text("\n" + l + "\n"))
                continue
            if line.endswith(" FOUND"):
                sc["files"] += 1
                ui(lambda l=line: (self._scan_line(l), self.log.append_text(l + "\n")))
                continue
            p, sep, res = line.rpartition(": ")
            if sep and p.startswith("/"):
                if res in ("Symbolic link", "Excluded"):
                    continue          # wird übersprungen, zählt nicht als Datei (wie bei find -type f)
                sc["files"] += 1
                sc["last_file"] = p
                if sc["first_at"] is None:
                    sc["first_at"] = time.time()     # ab hier wird gescannt (vorher: Signaturen laden)
                if res == "OK":
                    try:
                        sc["bytes"] += os.lstat(p).st_size
                    except OSError:
                        pass
                continue
            if line.strip():
                sc["warnings"] += 1
                if sc["warnings"] <= 40:
                    ui(lambda l=line: self.log.append_text(l + "\n"))
                elif sc["warnings"] == 41:
                    ui(lambda: self.log.append_text("… weitere Warnungen ausgeblendet\n"))
        rc = proc.wait()
        ui(lambda: self._scan_done(rc))

    def _tick(self):
        sc = getattr(self, "sc", None)
        if not sc or not self.scan_started or sc["done"]:
            return
        now = time.time()
        el = int(now - self.scan_started)
        self.scan_time.setText(f"{self.scan_target}  ·  {el // 3600:d}:{el // 60 % 60:02d}:{el % 60:02d}")
        files, byts = sc["files"], sc["bytes"]
        tf, tb = sc["total_files"], sc["total_bytes"]
        if tf:
            pct = min(99.0, files / tf * 100)
            self.scan_bar.set(pct, f"{pct:.0f} %")
            self.st_files.setText(f"{files:,} / {tf:,}".replace(",", "."))
            self.st_bytes.setText(f"{fmt_bytes(byts)} / {fmt_bytes(tb)}")
        else:
            cf = sc.get("count_files")
            self.scan_bar.set(None, "zähle Dateien …")
            self.st_files.setText(f"{files:,}".replace(",", ".") + (f" / ≥ {cf:,}".replace(",", ".") if cf else ""))
            self.st_bytes.setText(fmt_bytes(byts))
        rate = files / max(1, el)
        brate = byts / max(1, el)
        self.st_rate.setText(f"{rate:.0f} Dateien/s · {fmt_bytes(brate)}/s")
        self.st_found.setText(str(len(self.found)))
        self.st_found.setStyleSheet(f"color: {COLORS['danger']};" if self.found else "")
        self.scan_eta.setText(self._eta_text(sc, now))
        quiet = int(now - sc["last_line"])
        last = sc["last_file"]
        proc = sc["proc"]
        if proc is not None and proc.poll() is None:
            if quiet < 60:
                self.scan_state.set("ok", "Läuft")
                self.scan_cur.setText(f"Zuletzt geprüft: {last}" if last else "Startet … Signaturen werden geladen")
            elif quiet < 600:
                self.scan_state.set("warn", f"Arbeitet – seit {quiet // 60}:{quiet % 60:02d} keine Meldung")
                self.scan_cur.setText("Wahrscheinlich eine große Datei oder ein Archiv nach: " + (last or "—"))
            else:
                self.scan_state.set("danger", f"Hängt? {quiet // 60} Min keine Meldung")
                self.scan_cur.setText("Keine Rückmeldung seit über 10 Minuten nach: " + (last or "—")
                                      + " – Abbrechen und den Ordner ausschließen oder erneut versuchen.")
        elif proc is None:
            self.scan_state.set("info", "Startet …")

    @staticmethod
    def _dur(sec):
        sec = int(sec)
        if sec >= 3600:
            return f"{sec // 3600}:{sec // 60 % 60:02d} h"
        return f"{sec // 60} Min" if sec >= 60 else "unter 1 Min"

    def _eta_text(self, sc, now):
        """Hochrechnung: Tempo seit der ersten gescannten Datei (ohne die Ladezeit der Signaturen)."""
        first, files = sc["first_at"], sc["files"]
        total = sc["total_files"] or sc.get("count_files")
        if not first or not total or now - first < 15 or files < 50:
            return "Hochrechnung läuft …"
        rate = files / (now - first)
        if files >= total:
            return ""
        rest = (total - files) / rate
        end = datetime.fromtimestamp(now + rest)
        day = "" if end.date() == datetime.now().date() else \
            ("morgen " if (end.date() - datetime.now().date()).days == 1 else end.strftime("%d.%m. "))
        whole = (now - self.scan_started) + rest
        if sc["total_files"]:
            return (f"Restzeit ca. {self._dur(rest)} · fertig ca. {day}{end:%H:%M} Uhr · "
                    f"gesamt ca. {self._dur(whole)}")
        return f"Restzeit mind. {self._dur(rest)} (Dateien werden noch gezählt)"

    def _scan_line(self, line):
        line = line.strip()
        m = FOUND_RE.match(line)
        if m:
            self.found.append({"path": m.group(1), "threat": m.group(2), "status": "gefunden"})
            self._render_found()
            return
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            if k in ("Scanned files", "Infected files", "Scanned directories", "Time", "Data scanned"):
                self.summary[k] = v.strip()

    def stop_scan(self):
        sc = getattr(self, "sc", None)
        if not sc:
            return
        sc["cancelled"] = True
        for key in ("proc", "count_proc"):
            p = sc.get(key)
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        self.scan_state.set("info", "Wird abgebrochen …")

    def _scan_done(self, rc):
        sc = self.sc
        if sc["done"]:
            return
        sc["done"] = True
        self.tick_timer.stop()
        cp = sc.get("count_proc")
        if cp is not None and cp.poll() is None:
            cp.kill()
        el = int(time.time() - (self.scan_started or time.time()))
        self.scan_started = None
        self.b_stop.hide()
        self.b_scan.show()
        cancelled = sc["cancelled"]
        n = len(self.found)
        self.log.append_text(f"\n[Exit-Code {rc}]\n")
        dur = f"{el // 3600:d}:{el // 60 % 60:02d}:{el % 60:02d}"
        files = sc["files"]
        self.st_files.setText(f"{files:,}".replace(",", ".") + (f" / {sc['total_files']:,}".replace(",", ".")
                                                                if sc["total_files"] else ""))
        self.st_bytes.setText(fmt_bytes(sc["bytes"]))
        self.st_found.setText(str(n))
        self.scan_eta.setText("")
        if cancelled:
            self.scan_state.set("off", "Abgebrochen")
            self.badge.set("off", "Scan abgebrochen")
            self.progress.setText(f"Scan nach {dur} abgebrochen – {files:,} Dateien geprüft.".replace(",", "."))
        elif rc == 2 and not n:
            self.scan_state.set("danger", "Fehler")
            self.badge.set("danger", "Scan-Fehler")
            self.progress.setText("Scan mit Fehlern beendet – Details in der Ausgabe.")
        else:
            self.scan_bar.set(100, "100 %")
            self.scan_state.set("danger" if n else "ok", "Fertig")
            if n:
                self.badge.set("danger", f"{n} Bedrohung{'en' if n != 1 else ''} gefunden")
                self.progress.setText(f"✕ {n} infizierte Datei{'en' if n != 1 else ''} gefunden · Dauer {dur}.")
            else:
                self.badge.set("ok", "Keine Bedrohungen")
                self.progress.setText(f"● Keine Bedrohungen gefunden · {files:,} Dateien in {dur}.".replace(",", "."))
        self.scan_cur.setText("")
        if not cancelled:
            _save_json(AV_LAST, {"at": time.time(), "target": self.scan_target, "found": n,
                                 "scanned": self.summary.get("Scanned files", str(files)), "duration": el})
            self._show_last()
            if n and self.cb_quar.isChecked():
                self._quarantine([f for f in self.found if f["status"] == "gefunden"])

    def _show_last(self):
        last = _load_json(AV_LAST, None)
        if last:
            d = last.get("duration", 0)
            self.last_scan.setText(
                f"Letzter Scan: {fmt_ago(last['at'])} · {last['target']} · {last.get('scanned', '?')} Dateien · "
                f"{last['found']} Funde · Dauer {d // 60:02d}:{d % 60:02d}")
        else:
            self.last_scan.setText("Noch kein Scan durchgeführt.")

    # ---- Funde ------------------------------------------------------------

    def _render_found(self):
        self.ftable.setRowCount(len(self.found))
        danger = QColor(COLORS["danger"])
        for r, f in enumerate(self.found):
            cells = [QTableWidgetItem(f["path"]), QTableWidgetItem(f["threat"]), QTableWidgetItem(f["status"])]
            cells[0].setFont(QFont(FONTS["mono"], 10))
            if f["status"] == "gefunden":
                cells[1].setForeground(danger)
            for c, it in enumerate(cells):
                it.setToolTip(f["path"])
                self.ftable.setItem(r, c, it)
        self.ftable.setVisible(bool(self.found))
        self.f_empty.setVisible(not self.found)
        self._found_sel()

    def _sel_found(self):
        rows = sorted({i.row() for i in self.ftable.selectedIndexes()})
        return [self.found[r] for r in rows if r < len(self.found)]

    def _found_sel(self):
        sel = [f for f in self._sel_found() if f["status"] == "gefunden"]
        self.b_q.setEnabled(bool(sel))
        self.b_del.setEnabled(bool(sel))
        self.b_show.setEnabled(len(self._sel_found()) == 1)

    def _needs_root(self, path):
        d = os.path.dirname(path) or "/"
        return not (os.access(path, os.R_OK) and os.access(d, os.W_OK))

    def quarantine_selected(self):
        self._quarantine([f for f in self._sel_found() if f["status"] == "gefunden"])

    def _quarantine(self, items):
        if not items:
            return
        os.makedirs(QUARANTINE_DIR, mode=0o700, exist_ok=True)
        index = _load_json(QUARANTINE_INDEX, {})
        root_items = [f for f in items if self._needs_root(f["path"])]
        if root_items and not self.app.priv.ensure(self):
            items = [f for f in items if f not in root_items]
            root_items = []
        uid, gid = os.getuid(), os.getgid()
        for f in items:
            qname = f"{int(time.time() * 1000)}_{os.path.basename(f['path'])}"
            dest = os.path.join(QUARANTINE_DIR, qname)
            try:
                try:
                    st = os.lstat(f["path"])
                    owner = [st.st_uid, st.st_gid, st.st_mode & 0o7777]
                except OSError:
                    owner = None
                if f in root_items:
                    r = subprocess.run(["sudo", "-n", "bash", "-c",
                                        f"mv -- {shlex.quote(f['path'])} {shlex.quote(dest)} && "
                                        f"chown {uid}:{gid} {shlex.quote(dest)} && chmod 0400 {shlex.quote(dest)}"],
                                       capture_output=True, text=True)
                    if r.returncode != 0:
                        raise OSError(r.stderr.strip())
                else:
                    shutil.move(f["path"], dest)
                    os.chmod(dest, 0o400)
                index[qname] = {"orig": f["path"], "threat": f["threat"], "at": time.time(),
                                "root": f in root_items, "owner": owner}
                f["status"] = "in Quarantäne"
                self.log.append_text(f"In Quarantäne: {f['path']}\n")
            except Exception as e:
                self.log.append_text(f"error: {f['path']} konnte nicht verschoben werden: {e}\n")
        _save_json(QUARANTINE_INDEX, index)
        self._render_found()
        self.refresh_quarantine()

    def delete_selected(self):
        items = [f for f in self._sel_found() if f["status"] == "gefunden"]
        if not items or not ask_confirm(self, "Endgültig löschen",
                                        "Diese Dateien unwiderruflich löschen?\n\n"
                                        + "\n".join(f["path"] for f in items[:15]), "Löschen", danger=True):
            return
        root_items = [f for f in items if self._needs_root(f["path"])]
        if root_items and not self.app.priv.ensure(self):
            return
        for f in items:
            try:
                if f in root_items:
                    r = subprocess.run(["sudo", "-n", "rm", "-f", "--", f["path"]], capture_output=True, text=True)
                    if r.returncode != 0:
                        raise OSError(r.stderr.strip())
                else:
                    os.remove(f["path"])
                f["status"] = "gelöscht"
                self.log.append_text(f"Gelöscht: {f['path']}\n")
            except Exception as e:
                self.log.append_text(f"error: {f['path']}: {e}\n")
        self._render_found()

    def show_folder(self):
        sel = self._sel_found()
        if sel and which("xdg-open"):
            subprocess.Popen(["xdg-open", os.path.dirname(sel[0]["path"])],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- Quarantäne -------------------------------------------------------

    def refresh_quarantine(self):
        index = _load_json(QUARANTINE_INDEX, {})
        index = {k: v for k, v in index.items() if os.path.exists(os.path.join(QUARANTINE_DIR, k))}
        self.qitems = sorted(index.items(), key=lambda kv: -kv[1].get("at", 0))
        self.qtable.setRowCount(len(self.qitems))
        for r, (k, v) in enumerate(self.qitems):
            a = QTableWidgetItem(v["orig"])
            a.setFont(QFont(FONTS["mono"], 10))
            self.qtable.setItem(r, 0, a)
            self.qtable.setItem(r, 1, QTableWidgetItem(v["threat"]))
            self.qtable.setItem(r, 2, QTableWidgetItem(fmt_ago(v.get("at", 0))))
        self.qtable.setVisible(bool(self.qitems))
        self.q_empty.setVisible(not self.qitems)
        self._q_sel()

    def _sel_q(self):
        rows = sorted({i.row() for i in self.qtable.selectedIndexes()})
        return [self.qitems[r] for r in rows if r < len(self.qitems)]

    def _q_sel(self):
        sel = self._sel_q()
        self.b_restore.setEnabled(bool(sel))
        self.b_qdel.setEnabled(bool(sel))

    def restore_selected(self):
        sel = self._sel_q()
        if not sel or not ask_confirm(self, "Wiederherstellen",
                                      "Diese Dateien an ihren ursprünglichen Ort zurücklegen?\n"
                                      "Nur tun, wenn du sicher bist, dass es ein Fehlalarm ist.\n\n"
                                      + "\n".join(v["orig"] for _, v in sel[:15]), "Wiederherstellen"):
            return
        index = _load_json(QUARANTINE_INDEX, {})
        for k, v in sel:
            src = os.path.join(QUARANTINE_DIR, k)
            try:
                if v.get("root") or self._needs_root(v["orig"]):
                    if not self.app.priv.ensure(self):
                        return
                    orig = v["orig"]
                    if not os.path.isabs(orig) or os.path.normpath(orig) != orig or os.path.lexists(orig):
                        raise OSError("ungültiger Zielpfad oder am Zielort existiert bereits eine Datei")
                    # Besitzer und Rechte wie vor der Quarantäne; ohne Angabe: root, nicht ausführbar
                    own = v.get("owner") if isinstance(v.get("owner"), list) and len(v["owner"]) == 3 else None
                    uid_, gid_, mode_ = (int(own[0]), int(own[1]), int(own[2]) & 0o777) if own else (0, 0, 0o644)
                    q = shlex.quote
                    r = subprocess.run(["sudo", "-n", "sh", "-c",
                                        f"mv -n -T -- {q(src)} {q(orig)} && chown -h {uid_}:{gid_} -- {q(orig)} && "
                                        f"chmod {mode_:o} -- {q(orig)}"], capture_output=True, text=True)
                    if r.returncode != 0:
                        raise OSError(r.stderr.strip())
                else:
                    if os.path.exists(v["orig"]):
                        raise OSError("am Zielort existiert bereits eine Datei")
                    shutil.move(src, v["orig"])
                    os.chmod(v["orig"], 0o600)
                index.pop(k, None)
                self.log.append_text(f"Wiederhergestellt: {v['orig']}\n")
            except Exception as e:
                self.log.append_text(f"error: {v['orig']}: {e}\n")
        _save_json(QUARANTINE_INDEX, index)
        self.refresh_quarantine()

    def qdelete_selected(self):
        sel = self._sel_q()
        if not sel or not ask_confirm(self, "Endgültig löschen",
                                      f"{len(sel)} Datei(en) aus der Quarantäne unwiderruflich löschen?",
                                      "Löschen", danger=True):
            return
        index = _load_json(QUARANTINE_INDEX, {})
        for k, v in sel:
            try:
                p = os.path.join(QUARANTINE_DIR, k)
                os.chmod(p, 0o600)
                os.remove(p)
                index.pop(k, None)
            except Exception as e:
                self.log.append_text(f"error: {v['orig']}: {e}\n")
        _save_json(QUARANTINE_INDEX, index)
        self.refresh_quarantine()


# --------------------------------------------------------------------------
# Modul: Sicherheit (Übersicht, Mullvad VPN, Firewall, Netzwerk)
# --------------------------------------------------------------------------

def secure_boot_state():
    """True/False, oder None ohne UEFI."""
    if not os.path.isdir("/sys/firmware/efi"):
        return None
    try:
        for f in os.listdir("/sys/firmware/efi/efivars"):
            if f.startswith("SecureBoot-"):
                with open(f"/sys/firmware/efi/efivars/{f}", "rb") as fh:
                    data = fh.read()
                return bool(data) and data[-1] == 1
    except Exception:
        pass
    if which("mokutil"):
        r = subprocess.run(["mokutil", "--sb-state"], capture_output=True, text=True)
        return "enabled" in r.stdout.lower()
    return False


def luks_state():
    """(root_encrypted, [(gerät, geöffnet, eingehängt unter)])"""
    try:
        data = json.loads(subprocess.run(["lsblk", "-J", "-o", "NAME,PATH,TYPE,FSTYPE,MOUNTPOINTS"],
                                         capture_output=True, text=True, timeout=5).stdout)["blockdevices"]
    except Exception:
        return None, []
    luks, root_enc = [], False

    def walk(node, under_crypt):
        nonlocal root_enc
        mps = [m for m in (node.get("mountpoints") or []) if m]
        is_crypt = node.get("type") == "crypt"
        if node.get("fstype") == "crypto_LUKS":
            kids = node.get("children") or []
            opened = any(k.get("type") == "crypt" for k in kids)
            mounts = []
            for k in kids:
                mounts += _all_mounts(k)
            luks.append((node.get("path"), opened, ", ".join(mounts)))
        if "/" in mps and (under_crypt or is_crypt):
            root_enc = True
        for ch in node.get("children") or []:
            walk(ch, under_crypt or is_crypt)
    for d in data:
        walk(d, False)
    return root_enc, luks


def microcode_state():
    """(paketname, installiert) – (None, None) in einer VM oder bei unbekannter CPU."""
    info = _read("/proc/cpuinfo")
    if re.search(r"^flags\s*:.*\bhypervisor\b", info, re.M):
        return None, None
    pkg = "intel-ucode" if "GenuineIntel" in info else ("amd-ucode" if "AuthenticAMD" in info else None)
    if not pkg:
        return None, None
    try:
        ok = subprocess.run(["pacman", "-Q", pkg], capture_output=True, timeout=5).returncode == 0
    except Exception:
        ok = False
    return pkg, ok


def _dev_encrypted(dev):
    """True, wenn das Blockgerät (oder ein Elterngerät) ein dm-crypt-Container ist."""
    try:
        r = subprocess.run(["lsblk", "-s", "-n", "-o", "TYPE", dev], capture_output=True, text=True, timeout=5)
        return "crypt" in r.stdout.split()
    except Exception:
        return False


def swap_state():
    """[(pfad, verschlüsselt)] aller aktiven Swap-Bereiche. zram liegt im RAM und gilt als sicher."""
    res = []
    for line in _read("/proc/swaps").splitlines()[1:]:
        f = line.split()
        if len(f) < 2:
            continue
        path, kind = f[0].replace("\\040", " "), f[1]
        if "/zram" in path:
            res.append((path, True))
            continue
        dev = path
        if kind == "file":
            try:
                dev = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "--target", path], capture_output=True,
                                     text=True, timeout=5).stdout.strip().split("[")[0]
            except Exception:
                dev = ""
        res.append((path, bool(dev) and _dev_encrypted(dev)))
    return res


# Kernel-Schutz: nur Werte, die im Alltag nichts kaputt machen
HARDEN_SYSCTL = {"kernel.kexec_load_disabled": "1", "kernel.sysrq": "0", "kernel.dmesg_restrict": "1",
                 "kernel.kptr_restrict": "2"}
HARDEN_FILE = "/etc/sysctl.d/90-tuxdex-hardening.conf"


def sysctl_missing():
    """Namen der HARDEN_SYSCTL-Werte, die aktuell nicht gesetzt sind."""
    return [k for k, v in HARDEN_SYSCTL.items()
            if _read("/proc/sys/" + k.replace(".", "/")).strip() != v]


def arch_audit_state():
    """None (nicht installiert), "error" oder [(paket, behebbar, zeile)] betroffener Pakete."""
    if not which("arch-audit"):
        return None
    try:
        r = subprocess.run(["arch-audit"], capture_output=True, text=True, timeout=30)
    except Exception:
        return "error"
    if r.returncode != 0 and not r.stdout.strip():
        return "error"
    res = []
    for line in r.stdout.splitlines():
        m = re.match(r"Package (\S+) is affected by", line)
        if m:
            res.append((m.group(1), "Update to" in line, line))
    return res


def listening_ports():
    """[(proto, adresse, port, prozess)] – nur Dienste, die von außen erreichbar sind."""
    out = []
    try:
        r = subprocess.run(["ss", "-tulnpH"], capture_output=True, text=True, timeout=5)
    except Exception:
        return out
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        proto, local = f[0], f[4]
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        if addr.startswith("127.") or addr in ("::1", "localhost") or addr.startswith("fe80"):
            continue
        m = re.search(r'users:\(\("([^"]+)"', line)
        out.append((proto, addr, port, m.group(1) if m else "?"))
    seen, res = set(), []
    for o in out:
        k = (o[0], o[2])
        if k not in seen:
            seen.add(k)
            res.append(o)
    return res


def svc_active(name):
    try:
        return subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True,
                              timeout=5).stdout.strip() == "active"
    except Exception:
        return False


def svc_enabled(name):
    try:
        return subprocess.run(["systemctl", "is-enabled", name], capture_output=True, text=True,
                              timeout=5).stdout.strip() == "enabled"
    except Exception:
        return False


# --------------------------------------------------------------------------
# Checkliste: Wartung, Datenschutz, Performance (Sicherheit → Checkliste)
# --------------------------------------------------------------------------

JOURNALD_FILE = "/etc/systemd/journald.conf.d/90-tuxdex.conf"
COREDUMP_FILE = "/etc/systemd/coredump.conf.d/90-tuxdex.conf"
IOSCHED_FILE = "/etc/udev/rules.d/60-tuxdex-ioscheduler.rules"
# Muster für Zugangsdaten in der Shell-History – nur gezählt, nie angezeigt
SECRET_RE = re.compile(r"(passw(or)?d\s*[=:]|--password[= ]\S|\btoken\s*[=:]|api[_-]?key\s*[=:]|secret\s*[=:]|"
                       r"Authorization:\s*Bearer|\bAKIA[0-9A-Z]{16}\b|\bghp_[A-Za-z0-9]{30,}|\bglpat-[\w-]{20,}|"
                       r"\bsk-[A-Za-z0-9]{20,}|\bxox[bap]-[\w-]{10,}|sshpass\s+-p\s*\S)", re.I)


def _conf_value(paths, section, key):
    """Letzter Wert eines Schlüssels aus systemd-artigen .conf-Dateien (inkl. .d-Ordner)."""
    val = None
    files = []
    for p in paths:
        if os.path.isdir(p):
            try:
                files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".conf"))
            except OSError:
                pass        # z. B. Ordner ohne Leserecht (restriktive umask beim Anlegen)
        elif os.path.exists(p):
            files.append(p)
    for f in files:
        cur = None
        for line in _read(f).splitlines():
            line = line.strip()
            if line.startswith("["):
                cur = line.strip("[]")
            elif cur == section and re.match(rf"{key}\s*=", line):
                val = line.split("=", 1)[1].strip()
    return val


def _pacman_siglevel():
    """Liste der Stellen in pacman.conf, an denen Signaturen abgeschaltet sind."""
    bad, sect = [], None
    for line in _read("/etc/pacman.conf").splitlines():
        s = line.split("#", 1)[0].strip()
        if s.startswith("["):
            sect = s.strip("[]")
        elif re.match(r"SigLevel\s*=", s) and re.search(r"\bNever\b|\bTrustAll\b", s):
            bad.append(sect or "options")
    return bad


def _pacman_log_issues():
    """(Zeitpunkt, [Fehler/Warnungen]) des letzten vollständigen Updates aus pacman.log."""
    txt = _read("/var/log/pacman.log")
    i = txt.rfind("starting full system upgrade")
    if i < 0:
        return None, []
    seg = txt[i:]
    m = re.match(r"\[([^\]]+)\]", txt[txt.rfind("\n", 0, i) + 1:i])
    lines = [l for l in seg.splitlines() if re.search(r"\[ALPM(-SCRIPTLET)?\] (error|warning):|error:", l)]
    return (m.group(1) if m else None), lines[-40:]


def _history_hits():
    """{Datei: Anzahl verdächtiger Zeilen} in Bash/Zsh/Fish-History."""
    home = os.path.expanduser("~")
    res = {}
    for f in (".bash_history", ".zsh_history", ".histfile", ".local/share/fish/fish_history"):
        p = os.path.join(home, f)
        try:
            with open(p, errors="ignore") as fh:
                n = sum(1 for line in fh if SECRET_RE.search(line))
        except OSError:
            continue
        if n:
            res["~/" + f] = n
    return res


def _screen_lock():
    """True/False, wenn die Bildschirmsperre bekannt ist (KDE, GNOME, Cinnamon, MATE), sonst None."""
    for tool in ("kreadconfig6", "kreadconfig5"):
        if which(tool):
            v = _cmd_out([tool, "--file", "kscreenlockerrc", "--group", "Daemon", "--key", "Autolock"]).strip()
            return v.lower() != "false"
    for schema in ("org.gnome.desktop.screensaver", "org.cinnamon.desktop.screensaver", "org.mate.screensaver"):
        if which("gsettings"):
            v = _cmd_out(["gsettings", "get", schema, "lock-enabled"]).strip()
            if v in ("true", "false"):
                return v == "true"
    return None


def _vscode_telemetry():
    """[(Editor, Einstellung)] für VS Code/VSCodium mit eingeschalteter Telemetrie."""
    out = []
    for name, d in (("VS Code", "Code"), ("Code – OSS", "Code - OSS"), ("VSCodium", "VSCodium")):
        p = os.path.expanduser(f"~/.config/{d}/User/settings.json")
        if not os.path.isdir(os.path.dirname(os.path.dirname(p))):
            continue
        txt = _read(p)
        m = re.search(r'"telemetry\.telemetryLevel"\s*:\s*"(\w+)"', txt)
        if name == "VSCodium" and not m:
            continue                  # VSCodium hat Telemetrie ab Werk aus
        if not m or m.group(1) != "off":
            out.append((name, p))
    return out


def _stale_modules():
    """Modul-Ordner alter Kernel in /usr/lib/modules, die keinem Paket mehr gehören."""
    base = "/usr/lib/modules"
    try:
        dirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    except Exception:
        return []
    run = os.uname().release
    cand = [d for d in dirs if d != run and not d.startswith("extramodules")]
    if not cand:
        return []
    r = subprocess.run(["pacman", "-Qqo"] + [os.path.join(base, d) for d in cand], capture_output=True,
                       text=True, timeout=30, env={**os.environ, "LC_ALL": "C"}) if which("pacman") else None
    if r is None:
        return []
    owned = set()
    for line in (r.stderr or "").splitlines():
        m = re.search(r"No package owns (\S+)", line)
        if m:
            owned.add(os.path.basename(m.group(1).rstrip("/")))
    return sorted(owned)


def _disks_io():
    """[(Laufwerk, rotierend, aktueller Scheduler, verfügbare)]"""
    out = []
    try:
        names = os.listdir("/sys/block")
    except Exception:
        return out
    for n in sorted(names):
        if not re.match(r"^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+|vd[a-z]+)$", n):
            continue
        sch = _first_line(f"/sys/block/{n}/queue/scheduler")
        m = re.search(r"\[([\w-]+)\]", sch)
        out.append((n, _first_line(f"/sys/block/{n}/queue/rotational") == "1", m.group(1) if m else sch or "?",
                    sch.replace("[", "").replace("]", "").split()))
    return out


def _unit_enabled(unit):
    return _cmd_out(["systemctl", "is-enabled", unit]).strip() in ("enabled", "enabled-runtime", "static")


def checklist_state():
    """Alle Werte für die Checkliste – läuft im Hintergrund, ohne root."""
    c = {}
    # Pakete
    c["sig"] = _pacman_siglevel()
    ml = "/etc/pacman.d/mirrorlist"
    c["mirror_age"] = (time.time() - os.path.getmtime(ml)) / 86400 if os.path.exists(ml) else None
    c["reflector"] = which("reflector")
    c["reflector_timer"] = _unit_enabled("reflector.timer") if c["reflector"] else False
    c["paclog"] = _pacman_log_issues()
    c["reboot"] = kernel_modules_missing()
    c["kernel"] = os.uname().release
    c["kernel_pkgs"] = [k for k in ("linux", "linux-lts", "linux-zen", "linux-hardened")
                        if os.path.exists(f"/usr/lib/modules/{c['kernel']}/pkgbase")
                        and _read(f"/usr/lib/modules/{c['kernel']}/pkgbase").strip() == k]
    # Zugriff
    r = subprocess.run(["sudo", "-n", "sh", "-c", "cat /etc/sudoers /etc/sudoers.d/* 2>/dev/null"],
                       capture_output=True, text=True, timeout=5) if which("sudo") else None
    if r is not None and r.returncode == 0:
        c["nopasswd"] = [l.strip() for l in r.stdout.splitlines()
                         if "NOPASSWD" in l and not l.strip().startswith("#")]
    else:
        c["nopasswd"] = None
    c["groups"] = _cmd_out(["id", "-nG"]).split()
    c["lock"] = _screen_lock()
    c["apparmor"] = _first_line("/sys/module/apparmor/parameters/enabled") == "Y"
    c["usbguard"] = svc_active("usbguard") if which("usbguard") else None
    # Datenschutz
    c["journal_max"] = _conf_value(["/etc/systemd/journald.conf", "/etc/systemd/journald.conf.d"],
                                   "Journal", "SystemMaxUse")
    m = re.search(r"take up ([\d.]+\s*\w+)", _cmd_out(["journalctl", "--disk-usage"]))
    c["journal_use"] = m.group(1) if m else None
    c["core_storage"] = _conf_value(["/etc/systemd/coredump.conf", "/etc/systemd/coredump.conf.d"],
                                    "Coredump", "Storage")
    c["core_pattern"] = _read("/proc/sys/kernel/core_pattern").strip()
    try:
        c["core_files"] = len(os.listdir("/var/lib/systemd/coredump"))
    except Exception:
        c["core_files"] = 0
    c["history"] = _history_hits()
    c["ignorespace"] = bool(re.search(r"HISTCONTROL=\S*ignore(space|both)",
                                      _read(os.path.expanduser("~/.bashrc")) + _read(os.path.expanduser(
                                          "~/.bash_profile"))))
    c["telemetry"] = _vscode_telemetry()
    # Kernel
    c["aslr"] = _read("/proc/sys/kernel/randomize_va_space").strip()
    # Backup
    cfg = backup_load()
    c["bk_targets"] = cfg.get("targets", [])
    c["bk_last"] = (cfg.get("history") or [{}])[0].get("at")
    c["bk_sources"] = cfg.get("sources", [])
    c["bk_schedule"] = cfg.get("schedule", "off")
    c["root_fs"] = _cmd_out(["findmnt", "-n", "-o", "FSTYPE", "/"]).strip()
    c["snap_tool"] = next((t for t in ("snapper", "timeshift", "btrbk") if which(t)), None)
    # Performance
    c["swappiness"] = _read("/proc/sys/vm/swappiness").strip()
    c["mem"] = meminfo().get("MemTotal", 0)
    c["swaps"] = swap_devices()
    c["fstrim"] = _unit_enabled("fstrim.timer")
    c["disks"] = _disks_io()
    c["governor"] = _first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    c["tmp_fs"] = _cmd_out(["findmnt", "-n", "-o", "FSTYPE", "/tmp"]).strip()
    # Wartung
    out = _cmd_out(["journalctl", "-p", "3", "-b", "-q", "--no-pager", "-o", "short-monotonic"], 15)
    c["jerr"] = [l for l in out.splitlines() if l.strip()]
    c["failed"] = [l.split()[0] for l in _cmd_out(["systemctl", "--failed", "--no-legend", "--plain"]).splitlines()
                   if l.strip()]
    c["ntp"] = _cmd_out(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]).strip()
    c["ntp_on"] = _cmd_out(["timedatectl", "show", "-p", "NTP", "--value"]).strip()
    c["stale_mods"] = _stale_modules()
    c["orphans"] = [l for l in _cmd_out(["pacman", "-Qdtq"]).split() if l]
    try:
        c["cache"] = sum(e.stat().st_size for e in os.scandir("/var/cache/pacman/pkg") if e.is_file())
    except Exception:
        c["cache"] = None
    return c


def show_text(parent, title, heading, text):
    """Einfaches Fenster mit Text in Monospace (z. B. Fehlerliste)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(28, 24, 28, 20)
    lay.setSpacing(12)
    lay.addWidget(Label(heading, "DialogTitle"))
    box = QPlainTextEdit(text)
    box.setObjectName("Log")
    box.setReadOnly(True)
    box.setFont(QFont(FONTS["mono"], 9))
    box.setMinimumSize(760, 360)
    lay.addWidget(box)
    row = QHBoxLayout()
    row.addStretch(1)
    ok = Button("Schließen", "primary", dlg.accept)
    ok.setDefault(True)
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.exec()


class CheckRow(QFrame):
    """Zeile der Sicherheits-Übersicht: Status-Badge · Titel + Detail · optionaler Button."""

    def __init__(self, title):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(16)
        self.badge = StatusBadge("off", "…")
        self.badge.setFixedWidth(150)
        lay.addWidget(self.badge)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        txt.addWidget(Label(title, "PanelTitle"))
        self.detail = Label("", "Hint", wrap=True)
        txt.addWidget(self.detail)
        lay.addLayout(txt, 1)
        self.btn = Button("", "ghost")
        self.btn.hide()
        lay.addWidget(self.btn)
        self._cb = None
        self.btn.clicked.connect(lambda: self._cb and self._cb())

    def set(self, tone, short, detail, action=None, cb=None):
        self.badge.set(tone, short)
        self.detail.setText(detail)
        if action:
            self.btn.setText(action)
            self._cb = cb
            self.btn.show()
        else:
            self.btn.hide()


def _mullvad(args, timeout=15):
    try:
        r = subprocess.run(["mullvad"] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _on(text, *keys):
    """liest on/off, allow/block, true/false aus einer mullvad-„get“-Ausgabe."""
    low = text.lower()
    for k in keys:
        m = re.search(re.escape(k.lower()) + r"[^\n:]*:\s*(\w+)", low)
        if m:
            return m.group(1) in ("on", "true", "allow", "enabled", "yes")
    return any(w in low.split() for w in ("on", "true", "allow", "enabled"))


class SecurityTab(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.fw_backend = "ufw" if which("ufw") else ("firewalld" if which("firewall-cmd") else None)
        self.relays = {}       # code -> (name, {city_code: city_name})
        self.mv_loading = False

        self.badge = StatusBadge("off", "Prüfe …")
        self.lay.addLayout(page_header("Sicherheit", self.badge,
                                       Button("↻", "icon", self.refresh_all, "Alles neu prüfen")))

        # ---------- Übersicht ----------
        ov = Panel("Übersicht")
        self.rows = {}
        for key, title in (("vpn", "VPN"), ("dns", "DNS"), ("proxy", "Proxy"), ("fw", "Firewall"), ("luks", "Festplattenverschlüsselung (LUKS)"),
                           ("sb", "Secure Boot"), ("ucode", "CPU-Microcode"), ("swapenc", "Swap-Verschlüsselung"),
                           ("kernel", "Kernel-Schutz"), ("upd", "System-Updates"), ("cve", "Bekannte Sicherheitslücken"), ("av", "Antivirus"),
                           ("ports", "Offene Netzwerk-Ports"), ("ssh", "SSH-Server")):
            r = CheckRow(title)
            self.rows[key] = r
            ov.body.addWidget(r)
        self.lay.addWidget(ov)
        self.lay.addWidget(self._build_checklist())

        # ---------- Offene Ports ----------
        self.ports_panel = Panel("Offene Ports", [Button("↻", "icon", self.refresh_ports, "Neu prüfen")])
        self.ports_note = Label("", "Hint", wrap=True)
        self.ports_note.setTextFormat(Qt.RichText)
        self.ports_panel.body.addWidget(self.ports_note)
        self.ports_fw_btn = Button("Firewall aktivieren", "primary", lambda: self.fw_enable())
        self.ports_fw_btn.hide()
        pfb = QHBoxLayout()
        pfb.addWidget(self.ports_fw_btn)
        pfb.addStretch(1)
        self.ports_panel.body.addLayout(pfb)
        self.ports_grid = QGridLayout()
        self.ports_grid.setHorizontalSpacing(16)
        self.ports_grid.setVerticalSpacing(8)
        self.ports_panel.body.addLayout(self.ports_grid)
        self.lay.addWidget(self.ports_panel)

        # ---------- Netzwerk ----------
        net = Panel("Netzwerk & IP", [Button("Öffentliche IP prüfen", "ghost", self.check_public_ip,
                                             "Fragt am.i.mullvad.net – zeigt, wie dich Webseiten sehen")])
        self.ip_local = Label("", "Value", wrap=True)
        self.ip_local.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ip_pub = Label("Öffentliche IP: noch nicht geprüft", "Hint", wrap=True)
        self.ip_pub.setTextFormat(Qt.RichText)
        self.ip_pub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        net.body.addLayout(Field("Lokale Adressen", self.ip_local))
        net.body.addWidget(self.ip_pub)
        self.lay.addWidget(net)

        # ---------- Leak-Test ----------
        self.leak_btn = Button("Test starten", "primary", self.run_leak_test)
        lk = Panel("Leak-Test & VPN-Erkennung", [self.leak_btn])
        lk.body.addWidget(Label("Prüft, welche DNS-Server deine Anfragen wirklich beantworten (DNS-Leak) und ob "
                                "Webseiten deine Verbindung als VPN, Proxy, Tor oder Rechenzentrum erkennen. "
                                "Fragt bash.ws, ipapi.is und am.i.mullvad.net.", "Hint", wrap=True))
        self.leak_rows = {}
        for key, title in (("leak", "DNS-Leak"), ("detect", "Erkennung durch Webseiten"),
                           ("dnslist", "DNS-Server, die Webseiten sehen"), ("black", "Sperrlisten")):
            row = CheckRow(title)
            row.set("off", "Nicht geprüft", "")
            self.leak_rows[key] = row
            lk.body.addWidget(row)
        self.lay.addWidget(lk)

        # ---------- Mullvad ----------
        self.mv_panel = Panel("Mullvad VPN")
        mv = self.mv_panel.body
        head = QHBoxLayout()
        head.setSpacing(8)
        self.mv_badge = StatusBadge("off", "Prüfe …")
        head.addWidget(self.mv_badge)
        self.mv_where = Label("", "Value", wrap=True)
        head.addWidget(self.mv_where, 1)
        self.b_connect = Button("Verbinden", "primary", lambda: self._mv_action(["connect"], wait=True))
        self.b_disconnect = Button("Trennen", "ghost", lambda: self._mv_action(["disconnect"]))
        self.b_reconnect = Button("Neuer Server", "ghost", lambda: self._mv_action(["reconnect"], wait=True))
        for b in (self.b_connect, self.b_disconnect, self.b_reconnect):
            head.addWidget(b)
        mv.addLayout(head)

        self.mv_setup = QWidget()
        su = QVBoxLayout(self.mv_setup)
        su.setContentsMargins(0, 0, 0, 0)
        su.setSpacing(8)
        self.mv_setup_text = Label("", "Muted", wrap=True)
        self.mv_setup_text.setTextFormat(Qt.RichText)
        su.addWidget(self.mv_setup_text)
        sb = QHBoxLayout()
        self.b_mv_daemon = Button("Mullvad-Dienst starten", "primary", self.mv_start_daemon)
        sb.addWidget(self.b_mv_daemon)
        sb.addStretch(1)
        su.addLayout(sb)
        mv.addWidget(self.mv_setup)

        self.mv_main = QWidget()
        mm = QVBoxLayout(self.mv_main)
        mm.setContentsMargins(0, 0, 0, 0)
        mm.setSpacing(14)
        # Konto
        acc = QHBoxLayout()
        acc.setSpacing(8)
        self.acc_info = Label("", "Value", wrap=True)
        self.acc_num = ""          # volle Kontonummer, nur auf Wunsch sichtbar
        self.acc_rest = ""
        self.b_eye = Button("", "icon", self._toggle_acc, "Kontonummer anzeigen")
        self.b_eye.setCheckable(True)
        self.b_eye.setIcon(svg_icon(EYE_SVG.replace("{c}", COLORS["muted"])))
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        info_row.addWidget(self.b_eye)
        info_row.addWidget(self.acc_info, 1)
        acc_col = QVBoxLayout()
        acc_col.setSpacing(4)
        acc_col.addWidget(Label("KONTO", "FieldLabel"))
        acc_col.addLayout(info_row)
        acc.addLayout(acc_col, 1)
        self.acc_edit = LineEdit(placeholder="16-stellige Kontonummer", mono=True)
        self.acc_edit.setMaxLength(19)
        self.acc_edit.setEchoMode(QLineEdit.Password)
        self.acc_edit.setMinimumWidth(240)
        self.b_login = Button("Anmelden", "primary", self.mv_login)
        self.b_logout = Button("Abmelden", "ghost", self.mv_logout)
        box = QVBoxLayout()
        box.addStretch(1)
        ab = QHBoxLayout()
        ab.setSpacing(8)
        for w in (self.acc_edit, self.b_login, self.b_logout):
            ab.addWidget(w)
        box.addLayout(ab)
        acc.addLayout(box)
        mm.addLayout(acc)
        # Standort
        loc = QHBoxLayout()
        loc.setSpacing(8)
        self.cb_country = QComboBox()
        self.cb_country.setMinimumWidth(220)
        self.cb_country.currentIndexChanged.connect(self._fill_cities)
        self.cb_city = QComboBox()
        self.cb_city.setMinimumWidth(200)
        loc.addLayout(Field("Land", self.cb_country))
        loc.addLayout(Field("Stadt", self.cb_city))
        lb = QVBoxLayout()
        lb.addStretch(1)
        lb.addWidget(Button("Standort übernehmen", "ghost", self.mv_set_location))
        loc.addLayout(lb)
        loc.addStretch(1)
        mm.addLayout(loc)
        # Einstellungen
        mm.addWidget(Label("EINSTELLUNGEN", "FieldLabel"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.o_auto = QCheckBox("Beim Systemstart automatisch verbinden")
        self.o_lock = QCheckBox("Kill-Switch: Internet nur über VPN (Lockdown-Modus)")
        self.o_lan = QCheckBox("Geräte im lokalen Netz erreichbar (Drucker, NAS …)")
        self.o_ads = QCheckBox("Werbung blockieren (DNS)")
        self.o_track = QCheckBox("Tracker blockieren (DNS)")
        self.o_mal = QCheckBox("Schadsoftware-Seiten blockieren (DNS)")
        for i, cb in enumerate((self.o_auto, self.o_lock, self.o_lan, self.o_ads, self.o_track, self.o_mal)):
            grid.addWidget(cb, i % 3, i // 3)
        mm.addLayout(grid)
        self.o_auto.clicked.connect(lambda v: self._mv_action(["auto-connect", "set", "on" if v else "off"]))
        self.o_lock.clicked.connect(self._toggle_lockdown)
        self.o_lan.clicked.connect(lambda v: self._mv_action(["lan", "set", "allow" if v else "block"]))
        for cb in (self.o_ads, self.o_track, self.o_mal):
            cb.clicked.connect(lambda _=False: self._mv_dns())
        mm.addWidget(Label("Mullvad-Befehle brauchen kein Passwort – der Mullvad-Dienst erledigt das. "
                           "Mit Kill-Switch gibt es ohne VPN-Verbindung kein Internet.", "Hint", wrap=True))
        mv.addWidget(self.mv_main)
        self.lay.addWidget(self.mv_panel)

        # ---------- Firewall ----------
        fw = Panel("Firewall")
        fh = QHBoxLayout()
        fh.setSpacing(8)
        self.fw_badge = StatusBadge("off", "…")
        fh.addWidget(self.fw_badge)
        self.fw_label = Label("", "Value")
        fh.addWidget(self.fw_label, 1)
        self.b_fw_on = Button("Aktivieren", "primary", self.fw_enable)
        self.b_fw_status = Button("Regeln anzeigen", "ghost", self.fw_status)
        self.b_fw_off = Button("Deaktivieren", "danger", self.fw_disable)
        self.b_fw_install = Button("ufw installieren", "primary", self.fw_install)
        for b in (self.b_fw_install, self.b_fw_on, self.b_fw_status, self.b_fw_off):
            fh.addWidget(b)
        fw.body.addLayout(fh)
        self.fw_rules = QWidget()
        rl = QVBoxLayout(self.fw_rules)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        self.rule_table = QTableWidget(0, 2)
        self.rule_table.setHorizontalHeaderLabels(["NR.", "REGEL"])
        self.rule_table.verticalHeader().setVisible(False)
        self.rule_table.setShowGrid(False)
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rule_table.setColumnWidth(0, 60)
        self.rule_table.horizontalHeader().setStretchLastSection(True)
        self.rule_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.rule_table.setMinimumHeight(140)
        rl.addWidget(self.rule_table)
        add = QHBoxLayout()
        add.setSpacing(8)
        self.r_port = LineEdit(placeholder="z. B. 22 oder 8000:8100", mono=True)
        self.r_proto = QComboBox()
        self.r_proto.addItems(["tcp", "udp", "tcp+udp"])
        self.r_proto.setMinimumHeight(38)
        self.r_act = QComboBox()
        self.r_act.addItems(["erlauben", "sperren"])
        self.r_act.setMinimumHeight(38)
        add.addLayout(Field("Port", self.r_port), 1)
        add.addLayout(Field("Protokoll", self.r_proto))
        add.addLayout(Field("Aktion", self.r_act))
        ab2 = QVBoxLayout()
        ab2.addStretch(1)
        h2 = QHBoxLayout()
        h2.setSpacing(8)
        h2.addWidget(Button("Regel hinzufügen", "ghost", self.fw_add_rule))
        h2.addWidget(Button("Ausgewählte löschen", "danger", self.fw_del_rule))
        ab2.addLayout(h2)
        add.addLayout(ab2)
        rl.addLayout(add)
        fw.body.addWidget(self.fw_rules)
        self.fw_rules.setVisible(self.fw_backend == "ufw")
        self.lay.addWidget(fw)

        out = Panel("Ausgabe")
        self.log = LogView(150)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        QTimer.singleShot(400, self.refresh_all)
        self._vpn_timer = QTimer(self)
        self._vpn_timer.timeout.connect(self._poll_vpn)
        self._vpn_timer.start(10000)

    # ======================================================================
    # Übersicht
    # ======================================================================

    def refresh_all(self):
        self.badge.set("info", "Prüfe …")

        def worker():
            r = {}
            # VPN
            r["mullvad"] = which("mullvad")
            r["mv_status"] = _mullvad(["status"])[1] if r["mullvad"] else ""
            ifaces, gw, dns = net_interfaces()
            r["ifaces"], r["gw"], r["dns"] = ifaces, gw, dns
            r["vpn"] = vpn_state(ifaces)
            r["dns_now"] = dns_state()
            r["proxy"] = proxy_state()
            # Firewall
            r["fw"] = svc_active("ufw") or svc_active("firewalld") or svc_active("nftables") \
                or svc_active("iptables")
            r["fw_name"] = next((n for n in ("ufw", "firewalld", "nftables", "iptables") if svc_active(n)), None)
            r["fw_enabled"] = any(svc_enabled(n) for n in ("ufw", "firewalld", "nftables", "iptables"))
            r["luks"] = luks_state()
            r["sb"] = secure_boot_state()
            r["ucode"] = microcode_state()
            r["swap"] = swap_state()
            r["sysctl"] = sysctl_missing()
            r["cve"] = arch_audit_state()
            # Updates
            last = None
            try:
                with open("/var/log/pacman.log", errors="ignore") as f:
                    m = re.findall(r"\[([^\]]+)\][^\n]*starting full system upgrade", f.read())
                if m:
                    ts = m[-1].replace("T", " ")[:16]
                    last = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            except Exception:
                pass
            r["last_upgrade"] = last
            r["pending"] = _load_json(UPDATE_CACHE, {}).get("updates", [])
            # Antivirus
            if which("clamscan"):
                ver = subprocess.run(["clamscan", "--version"], capture_output=True, text=True).stdout
                parts = ver.strip().split("/")
                try:
                    r["av"] = (datetime.now() - parse_c_date(parts[2])).days
                except Exception:
                    r["av"] = -1
            else:
                r["av"] = None
            r["ports"] = listening_ports()
            r["ssh"] = svc_active("sshd")
            root_login = re.search(r"^\s*PermitRootLogin\s+(\S+)", _read("/etc/ssh/sshd_config"), re.M)
            r["ssh_root"] = root_login.group(1) if root_login else None
            r["ssh_pw"] = re.search(r"^\s*PasswordAuthentication\s+no", _read("/etc/ssh/sshd_config"), re.M) is None
            ui(lambda: self._show(r))
        threading.Thread(target=worker, daemon=True).start()
        self.mv_refresh()
        self.fw_refresh()
        self.refresh_ports()
        self.refresh_checklist()

    def _goto(self, widget):
        QTimer.singleShot(0, lambda: self.ensureWidgetVisible(widget, 0, 40))

    def _show(self, r):
        warn = 0
        # VPN
        warn += self._show_vpn(r["vpn"])
        # DNS
        d = r["dns_now"]
        if not d:
            self.rows["dns"].set("off", "Unbekannt", "Es wurde kein DNS-Server gefunden.")
        else:
            vpn_link = iface_kind(d["link"]) == "VPN" if d["link"] else False
            txt = (f"{d['provider']} · {d['server']}" + (f" über {d['link']}" if d["link"] else "") + ". "
                   + ("Anfragen laufen verschlüsselt (DNS-over-TLS)." if d["dot"] else
                      "Anfragen laufen durch den VPN-Tunnel." if vpn_link else
                      "Anfragen sind unverschlüsselt – der Netzbetreiber kann sehen, welche Seiten du aufrufst."))
            self.rows["dns"].set("ok" if d["dot"] or vpn_link else "info",
                                 "Verschlüsselt" if d["dot"] else ("Über VPN" if vpn_link else "Unverschlüsselt"), txt)
        # Proxy
        if r["proxy"]:
            self.rows["proxy"].set("info", "Eingestellt", " · ".join(f"{q}: {d}" for q, d in r["proxy"])
                                   + ". Programme schicken ihren Verkehr über diesen Proxy.")
        else:
            self.rows["proxy"].set("ok", "Keiner", "Es ist kein System-Proxy eingestellt.")
        # Firewall
        if r["fw"]:
            self.rows["fw"].set("ok", "Aktiv", f"{r['fw_name']} läuft"
                                + ("" if r["fw_enabled"] else " – startet aber nicht automatisch."))
        else:
            warn += 1
            self.rows["fw"].set("danger", "Aus", "Keine Firewall aktiv. Eingehende Verbindungen werden nicht gefiltert.",
                                "Aktivieren" if self.fw_backend else "ufw installieren",
                                self.fw_enable if self.fw_backend else self.fw_install)
        # LUKS
        root_enc, luks = r["luks"]
        others = ", ".join(f"{d} ({'offen' if o else 'gesperrt'})" for d, o, m in luks) or "keine"
        if root_enc:
            self.rows["luks"].set("ok", "Verschlüsselt", f"Die Systempartition liegt auf LUKS. LUKS-Geräte: {others}")
        elif root_enc is None:
            self.rows["luks"].set("off", "Unbekannt", "lsblk konnte nicht gelesen werden.")
        else:
            warn += 1
            self.rows["luks"].set("warn", "Nicht verschlüsselt",
                                  "Die Systempartition ist unverschlüsselt – bei Diebstahl sind alle Daten lesbar. "
                                  "Nachträglich nur über Neuinstallation/Backup sinnvoll. LUKS-Geräte: " + others)
        # Secure Boot
        sb = r["sb"]
        if sb is None:
            self.rows["sb"].set("off", "Kein UEFI", "Das System startet im Legacy-BIOS-Modus.")
        elif sb:
            self.rows["sb"].set("ok", "Aktiv", "Nur signierte Bootloader/Kernel werden gestartet.")
        else:
            self.rows["sb"].set("off", "Aus", "Optional: mit sbctl eigene Schlüssel einrichten "
                                              "(schützt vor manipulierten Bootloadern).")
        # Microcode
        pkg, ok = r["ucode"]
        if pkg is None:
            self.rows["ucode"].set("off", "Nicht nötig", "Virtuelle Maschine oder unbekannte CPU – der Host lädt "
                                                         "den Microcode.")
        elif ok:
            self.rows["ucode"].set("ok", "Installiert", f"{pkg} schließt CPU-Sicherheitslücken (z. B. Spectre).")
        else:
            warn += 1
            self.rows["ucode"].set("warn", "Fehlt", f"{pkg} fehlt – bekannte CPU-Sicherheitslücken bleiben offen. "
                                   "Wirkt nach dem nächsten Neustart.", "Installieren", self.install_ucode)
        # Swap
        plain = [p for p, enc in r["swap"] if not enc]
        if not r["swap"]:
            self.rows["swapenc"].set("ok", "Kein Swap", "Es wird kein Swap genutzt.")
        elif not plain:
            self.rows["swapenc"].set("ok", "Geschützt", "Swap liegt verschlüsselt oder im RAM (zram): "
                                     + ", ".join(p for p, _ in r["swap"]))
        else:
            warn += 1
            self.rows["swapenc"].set("warn", "Unverschlüsselt",
                                     f"{', '.join(plain)} ist unverschlüsselt – Passwörter und Schlüssel aus dem "
                                     "RAM können dort lesbar auf der Platte landen. Abhilfe: Swap-Partition "
                                     "entfernen und ein Swapfile auf der verschlüsselten Systempartition anlegen.",
                                     "Zum Swap", lambda: self.app.select([m[0] for m in MODULES].index("swap")))
        # Kernel-Schutz
        miss = r["sysctl"]
        if not miss:
            self.rows["kernel"].set("ok", "Aktiv", "Kernel-Austausch im laufenden Betrieb (kexec) und "
                                                   "SysRq-Tastenkürzel sind gesperrt, Kernel-Meldungen und "
                                                   "-Adressen nur für root lesbar.")
        else:
            warn += 1
            self.rows["kernel"].set("warn", "Offen", "Nicht gesetzt: " + ", ".join(
                f"{k}={HARDEN_SYSCTL[k]}" for k in miss) + ". Sperrt den Kernel-Austausch im laufenden Betrieb "
                "(kexec) und SysRq-Tastenkürzel und verbirgt Kernel-Meldungen (dmesg) und -Adressen vor normalen "
                "Programmen – im Alltag ohne Nachteile.", "Aktivieren", self.harden_kernel)
        # Updates
        last = r["last_upgrade"]
        imp = [u for u in r["pending"] if u.get("kind")]
        if last is None:
            self.rows["upd"].set("off", "Unbekannt", "Kein vollständiges Update im pacman-Log gefunden.")
        else:
            days = (datetime.now() - last).days
            txt = f"Letztes vollständiges Update vor {days} Tag{'en' if days != 1 else ''}"
            if r["pending"]:
                txt += f" · {len(r['pending'])} Updates offen" + (f", {len(imp)} wichtig" if imp else "")
            goto_upd = lambda: self.app.select([m[0] for m in MODULES].index("update"))
            if days > 14 or imp:
                warn += 1
                self.rows["upd"].set("warn", "Veraltet" if days > 14 else "Wichtige offen", txt + ".",
                                     "Zu den Updates", goto_upd)
            else:
                self.rows["upd"].set("ok", "Aktuell", txt + ".")
        # arch-audit
        cve = r["cve"]
        if cve is None:
            self.rows["cve"].set("off", "Nicht installiert", "Optional: arch-audit gleicht die installierten Pakete "
                                 "mit der Arch-Sicherheitsdatenbank ab (security.archlinux.org).", "Installieren",
                                 lambda: self._root(["pacman", "-S", "--needed", "arch-audit"], interactive=True))
        elif cve == "error":
            self.rows["cve"].set("off", "Nicht prüfbar", "arch-audit konnte die Sicherheitsdatenbank nicht abrufen "
                                                          "(keine Internetverbindung?).")
        else:
            fix = [c[0] for c in cve if c[1]]
            nofix = [c[0] for c in cve if not c[1]]

            def names(lst):
                return ", ".join(lst[:8]) + (" …" if len(lst) > 8 else "")
            if fix:
                warn += 1
                self.rows["cve"].set("warn", f"{len(fix)} behebbar",
                                     f"Updates schließen Lücken in: {names(fix)}."
                                     + (f" Noch ohne Fix: {names(nofix)}." if nofix else ""), "Zu den Updates",
                                     lambda: self.app.select([m[0] for m in MODULES].index("update")))
            elif nofix:
                self.rows["cve"].set("info", f"{len(nofix)} ohne Fix",
                                     f"Bekannte Lücken ohne verfügbares Update: {names(nofix)}. "
                                     "Nichts zu tun – der Fix kommt mit einem späteren Update.")
            else:
                self.rows["cve"].set("ok", "Keine bekannt", "Kein installiertes Paket hat eine bekannte Lücke.")
        # Antivirus
        av = r["av"]
        goto_av = lambda: self.app.select([m[0] for m in MODULES].index("antivirus"))
        if av is None:
            self.rows["av"].set("off", "Nicht installiert", "ClamAV ist optional – Tuxdex funktioniert auch ohne.")
        elif av < 0:
            self.rows["av"].set("warn", "Keine Signaturen", "ClamAV ist installiert, aber ohne Virensignaturen.",
                                "Einrichten", goto_av)
        elif av > 3:
            self.rows["av"].set("warn", f"{av} Tage alt", "Die Virensignaturen sind veraltet.", "Aktualisieren", goto_av)
        else:
            self.rows["av"].set("ok", "Aktuell", "ClamAV mit aktuellen Signaturen.")
        # Ports
        ports = r["ports"]
        if not ports:
            self.rows["ports"].set("ok", "Keine offen", "Kein Dienst wartet auf Verbindungen von außen.")
        else:
            lst = ", ".join(f"{p[2]}/{p[0]} ({p[3]})" for p in ports[:8]) + (" …" if len(ports) > 8 else "")
            tone = "info" if r["fw"] else "warn"
            if not r["fw"]:
                warn += 1
            self.rows["ports"].set(tone, f"{len(ports)} offen", f"{lst}. "
                                   + ("Die Firewall filtert diese Ports." if r["fw"]
                                      else "Ohne Firewall sind sie im Netz erreichbar."),
                                   "Verwalten", lambda: self._goto(self.ports_panel))
        # SSH
        if not r["ssh"]:
            self.rows["ssh"].set("ok", "Aus", "Kein Fernzugriff per SSH möglich.")
        else:
            risky = (r["ssh_root"] or "").lower() in ("yes",) or r["ssh_pw"]
            if risky:
                warn += 1
            self.rows["ssh"].set("warn" if risky else "info", "Läuft",
                                 "SSH-Server aktiv"
                                 + (" · root-Anmeldung erlaubt" if (r["ssh_root"] or "").lower() == "yes" else "")
                                 + (" · Passwort-Anmeldung erlaubt (Schlüssel sind sicherer)" if r["ssh_pw"] else ""),
                                 "Dienst stoppen", self.stop_ssh)
        # Netzwerk
        loc = []
        for i in r["ifaces"]:
            if i["ipv4"] or i["ipv6"]:
                loc.append(f"{i['kind']} {i['name']}: " + ", ".join(i["ipv4"] + i["ipv6"][:1]))
        self.ip_local.setText("\n".join(loc) + f"\nGateway: {r['gw'] or '—'}  ·  DNS: {', '.join(r['dns']) or '—'}")

        self.badge.set("ok" if warn == 0 else ("warn" if warn <= 2 else "danger"),
                       "Alles in Ordnung" if warn == 0 else f"{warn} Hinweis{'e' if warn != 1 else ''}")

    def _show_vpn(self, v):
        """Setzt die VPN-Zeile; gibt 1 zurück, wenn ein Hinweis gezählt werden soll."""
        mv = v.get("mullvad")
        mv_first = mv.splitlines()[0].strip().lower() if mv else ""
        ts = v.get("tailscale")
        route = v.get("route_dev") or "—"
        if mv_first.startswith("connected"):
            if v.get("mv_lockdown"):
                self.rows["vpn"].set("ok", "Aktiv", "Mullvad ist verbunden, Kill-Switch an. " + self._mv_location(mv))
                return 0
            self.rows["vpn"].set("info", "Aktiv", "Mullvad ist verbunden. " + self._mv_location(mv)
                                 + " Tipp: Kill-Switch (Lockdown) einschalten – dann geht auch bei einem "
                                 "Verbindungsabbruch nichts am Tunnel vorbei.", "Kill-Switch an",
                                 lambda: self._toggle_lockdown(True))
            return 0
        if ts and ts["running"] and ts["exit_node"]:
            self.rows["vpn"].set("ok", "Aktiv", f"Tailscale ist verbunden – dein Internetverkehr läuft über den "
                                 f"Exit-Node „{ts['exit_node']}“.")
            return 0
        if v["others"]:
            full = route in v["others"]
            self.rows["vpn"].set("ok" if full else "info", "Aktiv" if full else "Verbunden",
                                 f"VPN-Verbindung aktiv: {', '.join(v['others'])}. " +
                                 ("Der Internetverkehr läuft darüber." if full else
                                  f"Der Internetverkehr läuft aber direkt über {route} (Split-Tunnel)."))
            return 0
        if ts and ts["running"]:
            self.rows["vpn"].set("info", "Tailscale an", "Tailscale ist verbunden (privates Netz zwischen deinen "
                                 "Geräten). Ohne Exit-Node läuft der Internetverkehr direkt, nicht über ein VPN.")
            return 0
        extra = ""
        if ts is not None and not ts["running"]:
            extra = f" Tailscale ist {TS_STATES.get(ts['state'], ts['state'])}."
        if mv is not None:
            self.rows["vpn"].set("warn", "Aus", "Mullvad ist nicht verbunden – Anbieter und Webseiten sehen "
                                 "deine echte IP." + extra, "Verbinden",
                                 lambda: self._mv_action(["connect"], wait=True))
            return 1
        self.rows["vpn"].set("off", "Kein VPN", "Es ist kein VPN aktiv." + extra +
                             " Optional – Tuxdex funktioniert auch ohne VPN.")
        return 0

    def _poll_vpn(self):
        """Alle 10 s: VPN-Zeile auffrischen (z. B. nach „tailscale down“), solange der Tab sichtbar ist."""
        if not self.isVisible() or getattr(self, "_vpn_busy", False):
            return
        self._vpn_busy = True

        def worker():
            v = vpn_state()
            ui(lambda: (self._show_vpn(v), setattr(self, "_vpn_busy", False)))
        threading.Thread(target=worker, daemon=True).start()

    def stop_ssh(self):
        if not ask_confirm(self, "SSH stoppen", "SSH-Server stoppen und nicht mehr automatisch starten?\n"
                           "Laufende Fernverbindungen werden getrennt.", "Stoppen", danger=True):
            return
        self._root(["systemctl", "disable", "--now", "sshd"])

    def install_ucode(self):
        pkg, ok = microcode_state()
        if not pkg or ok:
            return
        self._root(["pacman", "-S", "--needed", pkg], interactive=True)

    def harden_kernel(self):
        if not ask_confirm(self, "Kernel-Schutz", "Kernel-Austausch im laufenden Betrieb (kexec) und "
                           "SysRq-Tastenkürzel sperren, Kernel-Meldungen (dmesg) und -Adressen nur für root?\n\nWird in " + HARDEN_FILE + " gespeichert und gilt "
                           "sofort und nach jedem Neustart.", "Aktivieren"):
            return
        body = "".join(f"{k} = {v}\n" for k, v in HARDEN_SYSCTL.items())
        script = (f"printf %s {shlex.quote('# Tuxdex: Kernel-Schutz' + chr(10) + body)} > {HARDEN_FILE} && "
                  f"sysctl -p {HARDEN_FILE}")
        self._root(["sh", "-c", script])

    def check_public_ip(self):
        self.ip_pub.setText("Öffentliche IP wird geprüft …")

        def worker():
            try:
                d = public_ip_info()
                txt = (f"Öffentliche IP: <b>{d.get('ip', '?')}</b> · {d.get('city') or ''} {d.get('country') or ''}"
                       f" · {d.get('organization') or ''}<br>"
                       + (f'<span style="color:{COLORS["ok"]}">● Dein Verkehr läuft über Mullvad '
                          f'({d.get("mullvad_exit_ip_hostname", "")}).</span>' if d.get("mullvad_exit_ip") else
                          f'<span style="color:{COLORS["warn"]}">▲ Nicht über Mullvad – Webseiten sehen diese '
                          f'Adresse.</span>'))
            except Exception as e:
                txt = f"Öffentliche IP konnte nicht ermittelt werden ({e})."
            ui(lambda: self.ip_pub.setText(txt))
        threading.Thread(target=worker, daemon=True).start()

    def run_leak_test(self):
        self.leak_btn.setEnabled(False)
        self.leak_btn.setText("Teste …")
        for row in self.leak_rows.values():
            row.set("info", "Prüfe …", "")

        def worker():
            r = leak_test()
            ui(lambda: self._show_leak(r))
        threading.Thread(target=worker, daemon=True).start()

    def _show_leak(self, r):
        self.leak_btn.setEnabled(True)
        self.leak_btn.setText("Erneut testen")
        vpn = r["vpn"]
        rep = r.get("rep")
        # --- Erkennung ---
        if rep:
            where = " · ".join(x for x in (rep["ip"], rep["city"], rep["country"], rep["org"]) if x)
            kinds = [k for k, on in (("VPN" + (f" ({rep['vpn_name']})" if rep["vpn_name"] else ""), rep["vpn"]),
                                     ("Proxy", rep["proxy"]), ("Tor", rep["tor"]),
                                     ("Rechenzentrum/Hosting", rep["hosting"])) if on]
            if kinds:
                self.leak_rows["detect"].set("info", "Erkannt", f"{where}. Webseiten erkennen: {', '.join(kinds)}. "
                                             "Manche Dienste (Streaming, Banken) sperren oder fragen dann nach.")
            else:
                self.leak_rows["detect"].set("ok" if not vpn else "info", "Nicht erkannt",
                                             f"{where}. Sieht aus wie ein normaler Internetanschluss"
                                             + (" – das VPN wird nicht als solches erkannt." if vpn else "."))
        else:
            self.leak_rows["detect"].set("off", "Nicht prüfbar", f"ipapi.is nicht erreichbar ({r.get('rep_err')}).")
        # --- DNS-Server laut bash.ws ---
        dns = r.get("dns")
        local = r.get("local_dns") or {}
        local_via_vpn = bool(local.get("link")) and iface_kind(local["link"]) == "VPN"
        if dns is not None:
            if dns:
                lst = "; ".join(f"{ip} ({', '.join(x for x in (c, a) if x)})" for ip, c, a in dns[:6])
                self.leak_rows["dnslist"].set("info", f"{len(dns)} Server", lst + ".")
            else:
                self.leak_rows["dnslist"].set("off", "Keine gesehen", "bash.ws hat keine DNS-Anfrage empfangen.")
        else:
            self.leak_rows["dnslist"].set("off", "Nicht prüfbar", f"bash.ws nicht erreichbar ({r.get('dns_err')}).")
        # --- Leak-Bewertung ---
        if not vpn:
            self.leak_rows["leak"].set("off", "Kein VPN", "Ohne VPN gibt es kein Leck im eigentlichen Sinn: "
                                       f"DNS geht an {local.get('provider', 'deinen DNS-Server')}"
                                       + (" (verschlüsselt)." if local.get("dot") else
                                          " – dein Netzbetreiber kann die aufgerufenen Seiten sehen."))
        else:
            rep_asn = (rep or {}).get("asn", "")
            foreign = [d for d in (dns or []) if rep_asn and d[2] and rep_asn not in d[2]]
            if local and not local_via_vpn and not local.get("dot") and \
                    local.get("provider", "").startswith(("Router", "Internetanbieter")):
                self.leak_rows["leak"].set("danger", "Leck", f"DNS-Anfragen gehen an {local['provider']} "
                                           f"({local['server']}) über {local.get('link') or 'das normale Netz'} – "
                                           "am VPN vorbei. Im VPN-Programm den VPN-DNS erzwingen (Mullvad: "
                                           "Kill-Switch/Lockdown).")
            elif foreign and not r.get("mullvad", {}).get("mullvad_exit_ip"):
                self.leak_rows["leak"].set("warn", "Möglich", "Einige DNS-Server gehören nicht zum Anbieter deiner "
                                           "VPN-Adresse: " + ", ".join(f"{d[0]} ({d[2]})" for d in foreign[:4])
                                           + ". Prüfen, ob das dein gewollter DNS-Dienst ist.")
            elif dns is None:
                self.leak_rows["leak"].set("off", "Nicht prüfbar", "Der Leak-Test-Dienst war nicht erreichbar.")
            else:
                self.leak_rows["leak"].set("ok", "Kein Leck", "Alle DNS-Anfragen laufen über das VPN.")
        # --- Sperrlisten (Mullvad-Check) ---
        mv = r.get("mullvad")
        if mv:
            bl = mv.get("blacklisted") or {}
            hits = [x.get("name") or x.get("link") or "?" for x in bl.get("results", []) if x.get("blacklisted")]
            if bl.get("blacklisted") or hits:
                self.leak_rows["black"].set("warn", "Gelistet", "Deine öffentliche IP steht auf Sperrlisten: "
                                            + ", ".join(hits[:5]) + ". Manche Seiten zeigen dann Captchas.")
            else:
                self.leak_rows["black"].set("ok", "Sauber", "Deine öffentliche IP steht auf keiner bekannten "
                                                           "Sperrliste.")
        else:
            self.leak_rows["black"].set("off", "Nicht prüfbar", f"am.i.mullvad.net nicht erreichbar "
                                                                f"({r.get('mullvad_err')}).")

    def _root(self, cmd, then=None, interactive=False):
        if not self.app.priv.ensure(self):
            return
        self.log.set_text(f"$ sudo {' '.join(shlex.quote(c) for c in cmd)}\n")

        def done(rc):
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            if then:
                then(rc)
            self.refresh_all()
        run_streaming(cmd, self.log, needs_sudo=True, clear_first=False, on_done=done, interactive=interactive)


    # ======================================================================
    # Checkliste: Wartung, Datenschutz, Performance
    # ======================================================================

    CHECK_GROUPS = [
        ("Pakete & Updates", [("sig", "Paketsignaturen"), ("mirror", "Spiegelserver (Mirrors)"),
                              ("paclog", "Letztes Update (pacman.log)"), ("kern", "Kernel & Neustart")]),
        ("Zugriff", [("sudo", "sudo ohne Passwort (NOPASSWD)"), ("groups", "Benutzergruppen"),
                     ("lock", "Bildschirmsperre"), ("mac", "AppArmor"), ("usb", "USB-Schutz (usbguard)")]),
        ("Datenschutz", [("journal", "System-Protokoll (journald)"), ("core", "Speicherabbilder (Core Dumps)"),
                         ("hist", "Shell-Verlauf"), ("telem", "Telemetrie in Editoren")]),
        ("Kernel", [("aslr", "Adress-Zufall (ASLR)")]),
        ("Backup", [("backup", "Backup"), ("pkglist", "Paketliste"), ("snap", "System-Snapshots")]),
        ("Performance", [("trim", "TRIM für SSDs"), ("io", "I/O-Scheduler"), ("swapcfg", "Swap & Swappiness"),
                         ("gov", "CPU-Regler"), ("tmp", "/tmp im Arbeitsspeicher")]),
        ("Laufende Wartung", [("jerr", "Fehler seit dem Start"), ("failed", "Fehlgeschlagene Dienste"),
                              ("ntp", "Uhrzeit (NTP)"), ("mods", "Alte Kernel-Module"),
                              ("clean", "Verwaiste Pakete & Paket-Cache")]),
    ]

    def _build_checklist(self):
        self.cl_badge = StatusBadge("off", "Prüfe …")
        p = Panel("Checkliste: Wartung, Datenschutz & Performance",
                  [self.cl_badge, Button("↻", "icon", self.refresh_checklist, "Neu prüfen")])
        p.body.addWidget(Label("Weitere Punkte für ein gepflegtes Arch-System. Grün passt, Gelb lohnt einen Blick, "
                               "Grau ist optional oder nur ein Hinweis. Knöpfe ändern nur, was dabeisteht.",
                               "Hint", wrap=True))
        self.cl_rows = {}
        for group, items in self.CHECK_GROUPS:
            head = Label(group.upper(), "FieldLabel")
            head.setContentsMargins(0, 8, 0, 0)
            p.body.addWidget(head)
            for key, title in items:
                r = CheckRow(title)
                r.set("off", "…", "")
                self.cl_rows[key] = r
                p.body.addWidget(r)
        return p

    def refresh_checklist(self):
        self.cl_badge.set("off", "Prüfe …")

        def worker():
            try:
                c = checklist_state()
            except Exception as e:
                c = {"_error": str(e)}
            ui(lambda: self._show_checklist(c))
        threading.Thread(target=worker, daemon=True).start()

    def _show_checklist(self, c):
        if "_error" in c:
            self.cl_badge.set("danger", "Fehler")
            self.cl_rows["sig"].set("danger", "Fehler", c["_error"])
            return
        R = self.cl_rows
        goto = lambda key: (lambda: self.app.select([m[0] for m in MODULES].index(key)))
        # --- Pakete & Updates
        if c["sig"]:
            R["sig"].set("danger", "Abgeschaltet", "In /etc/pacman.conf steht SigLevel = Never/TrustAll bei: "
                         + ", ".join(c["sig"]) + ". Pakete werden dann nicht auf Echtheit geprüft – dort auf "
                         "„Required DatabaseOptional“ zurückstellen.")
        else:
            R["sig"].set("ok", "Aktiv", "pacman prüft die Signatur jedes Pakets.")
        age = c["mirror_age"]
        if c["reflector_timer"]:
            R["mirror"].set("ok", "Automatisch", "reflector.timer hält die Liste aktuell.")
        elif age is None:
            R["mirror"].set("off", "Unbekannt", "Keine /etc/pacman.d/mirrorlist gefunden.")
        else:
            act = ("Jetzt aktualisieren", self.cl_mirrors) if c["reflector"] else \
                ("reflector installieren", lambda: self._root(["pacman", "-S", "--needed", "reflector"],
                                                             interactive=True))
            R["mirror"].set("ok" if age < 90 else "warn", f"{age:.0f} Tage alt",
                            "Die Liste der Download-Server. Ältere Listen führen zu langsamen oder veralteten "
                            "Servern." + ("" if c["reflector"] else " reflector sucht die schnellsten aktuellen."),
                            *act)
        when, issues = c["paclog"]
        if when is None:
            R["paclog"].set("off", "Kein Eintrag", "In pacman.log steht noch kein vollständiges Update.")
        elif issues:
            R["paclog"].set("warn", f"{len(issues)} Meldungen", f"Beim Update am {when[:16].replace('T', ' ')} gab "
                            "es Fehler oder Warnungen (z. B. .pacnew-Dateien, fehlgeschlagene Hooks).", "Anzeigen",
                            lambda: show_text(self, "pacman.log", "Meldungen beim letzten Update",
                                              "\n".join(issues)))
        else:
            R["paclog"].set("ok", "Sauber", f"Letztes Update am {when[:16].replace('T', ' ')} ohne Fehler.")
        kv = c["kernel"] + (f" ({c['kernel_pkgs'][0]})" if c["kernel_pkgs"] else "")
        if c["reboot"]:
            R["kern"].set("warn", "Neustart nötig", f"Läuft: {kv}. Der Kernel wurde aktualisiert – bis zum "
                          "Neustart fehlen Module (z. B. für USB-Sticks) und der neue Kernel ist nicht aktiv.")
        else:
            R["kern"].set("ok", "Aktuell", f"Läuft: {kv}."
                          + ("" if "hardened" in kv else " Für erhöhten Schutzbedarf gibt es linux-hardened "
                             "(manche Programme laufen damit eingeschränkt)."))
        # --- Zugriff
        if c["nopasswd"] is None:
            R["sudo"].set("off", "Nicht geprüft", "Die sudo-Regeln sind nur mit Administrator-Rechten lesbar.",
                          "Prüfen", lambda: self.app.priv.ensure(self) and self.refresh_checklist())
        elif c["nopasswd"]:
            R["sudo"].set("warn", f"{len(c['nopasswd'])} Regel(n)", "Ohne Passwort erlaubt: "
                          + " · ".join(c["nopasswd"][:3]) + ". Jedes Programm unter deinem Benutzer kann diese "
                          "Befehle als root ausführen – nur behalten, wenn nötig (visudo).")
        else:
            R["sudo"].set("ok", "Keine", "sudo fragt immer nach dem Passwort.")
        risky = [g for g in c["groups"] if g in ("docker", "disk", "libvirt", "lxd", "root")]
        R["groups"].set("warn" if risky else "ok", ", ".join(risky) if risky else "Unauffällig",
                        ("Mitglied in: " + ", ".join(c["groups"]) + ". ")
                        + ("Diese Gruppen geben praktisch root-Rechte ohne Passwort – nur behalten, wenn du sie "
                           "brauchst (gpasswd -d BENUTZER GRUPPE)." if risky else ""))
        if c["lock"] is None:
            R["lock"].set("off", "Unbekannt", "Für diese Desktop-Umgebung kann Tuxdex die Sperre nicht auslesen – "
                          "bitte in den Systemeinstellungen prüfen.")
        else:
            R["lock"].set("ok" if c["lock"] else "warn", "An" if c["lock"] else "Aus",
                          "Der Bildschirm sperrt sich bei Inaktivität." if c["lock"] else
                          "Der Bildschirm sperrt sich nicht automatisch – in den Systemeinstellungen einschalten.")
        R["mac"].set("ok" if c["apparmor"] else "off", "Aktiv" if c["apparmor"] else "Optional",
                     "AppArmor schränkt ein, worauf einzelne Programme zugreifen dürfen." if c["apparmor"] else
                     "AppArmor ist nicht aktiv. Sinnvoll für Server oder erhöhten Schutzbedarf; braucht einen "
                     "Kernel-Parameter (lsm=…,apparmor) und das Paket apparmor.")
        if c["usbguard"] is None:
            R["usb"].set("off", "Optional", "usbguard blockiert unbekannte USB-Geräte (Schutz gegen manipulierte "
                         "Sticks). Nur bei physischem Zugriff Fremder sinnvoll.")
        else:
            R["usb"].set("ok" if c["usbguard"] else "warn", "Aktiv" if c["usbguard"] else "Installiert, aus",
                         "usbguard läuft." if c["usbguard"] else "usbguard ist installiert, der Dienst läuft nicht.")
        # --- Datenschutz
        use = c["journal_use"] or "?"
        if c["journal_max"]:
            R["journal"].set("ok", "Begrenzt", f"Höchstens {c['journal_max']} (belegt: {use}).")
        else:
            R["journal"].set("info", "Unbegrenzt", f"Belegt: {use}. Ohne Grenze darf das Protokoll bis zu 10 % der "
                             "Partition nutzen und hält Einträge sehr lange.", "Auf 500 MB / 1 Monat",
                             self.cl_journald)
        if (c["core_storage"] or "").lower() == "none" or not c["core_pattern"] or "false" in c["core_pattern"]:
            R["core"].set("ok", "Aus", "Abgestürzte Programme hinterlassen keine Speicherabbilder.")
        else:
            R["core"].set("info", f"{c['core_files']} Dumps" if c["core_files"] else "An",
                          "Bei Abstürzen landet der Arbeitsspeicher des Programms auf der Platte – darin können "
                          "Passwörter stehen.", "Abschalten", self.cl_coredump)
        hits = c["history"]
        if hits:
            R["hist"].set("warn", f"{sum(hits.values())} Treffer",
                          "Zeilen, die nach Passwort oder Token aussehen, in: " + ", ".join(
                              f"{f} ({n})" for f, n in hits.items()) + ". Tuxdex zeigt sie nicht an – bitte selbst "
                          "prüfen und löschen." + ("" if c["ignorespace"] else " Tipp: HISTCONTROL=ignorespace in "
                                                   "~/.bashrc – Befehle mit Leerzeichen davor landen nicht im Verlauf."))
        else:
            R["hist"].set("ok", "Unauffällig", "Kein Passwort oder Token im Shell-Verlauf gefunden."
                          + ("" if c["ignorespace"] else " Tipp: HISTCONTROL=ignorespace in ~/.bashrc – Befehle mit "
                             "Leerzeichen davor landen nicht im Verlauf."))
        tel = c["telemetry"]
        if tel:
            R["telem"].set("warn", "An", "Telemetrie aktiv in: " + ", ".join(n for n, _ in tel) + ".",
                           "Abschalten", lambda: self.cl_telemetry(tel))
        else:
            R["telem"].set("ok", "Aus", "VS Code / VSCodium senden keine Telemetrie (oder sind nicht installiert). "
                           "Browser-Telemetrie bitte in dessen Einstellungen prüfen.")
        # --- Kernel
        R["aslr"].set("ok" if c["aslr"] == "2" else "danger", "Voll" if c["aslr"] == "2" else f"Wert {c['aslr']}",
                      "Speicheradressen werden zufällig vergeben – erschwert Angriffe." if c["aslr"] == "2" else
                      "kernel.randomize_va_space sollte 2 sein (Standard). Jemand hat es abgeschaltet.")
        # --- Backup
        if not c["bk_targets"]:
            R["backup"].set("warn", "Kein Ziel", "Noch kein Backup-Ziel festgelegt.", "Zum Backup",
                            goto("backup"))
        else:
            days = (time.time() - c["bk_last"]) / 86400 if c["bk_last"] else None
            src = " ".join(c["bk_sources"])
            miss = [n for n, p in (("/etc", "/etc"), ("Home", os.path.expanduser("~"))) if p not in src]
            detail = ("Letztes Backup " + (fmt_ago(c["bk_last"]) if c["bk_last"] else "noch nie") + ". "
                      + ("Zeitplan: " + {"off": "aus", "daily": "täglich", "weekly": "wöchentlich"}.get(
                          c["bk_schedule"], c["bk_schedule"]) + ". ")
                      + (f"Nicht gesichert: {', '.join(miss)}. " if miss else "")
                      + "Wiederherstellen einmal ausprobieren, bevor es ernst wird.")
            R["backup"].set("ok" if days is not None and days < 8 and not miss else "warn",
                            f"vor {days:.0f} Tagen" if days is not None else "Noch nie", detail, "Zum Backup",
                            goto("backup"))
        pl = PKGLIST_FILE
        if os.path.exists(pl):
            R["pkglist"].set("ok", "Gespeichert", f"{short_path(pl)} vom "
                             f"{datetime.fromtimestamp(os.path.getmtime(pl)).strftime('%d.%m.%Y')} – wird bei jedem "
                             "Backup erneuert. Neu installieren: pacman -S --needed - < pakete.txt",
                             "Jetzt speichern", self.cl_pkglist)
        else:
            R["pkglist"].set("warn", "Fehlt", "Die Liste aller selbst installierten Pakete macht eine Neuinstallation "
                             "leicht. Tuxdex legt sie in ~/.config/tuxdex ab und erneuert sie bei jedem Backup.",
                             "Jetzt speichern", self.cl_pkglist)
        if c["root_fs"] == "btrfs":
            R["snap"].set("ok" if c["snap_tool"] else "info", c["snap_tool"] or "Kein Werkzeug",
                          "Btrfs-Snapshots vor Updates " + ("sind eingerichtet." if c["snap_tool"] else
                                                            "machen ein Zurück in Sekunden möglich – z. B. mit "
                                                            "snapper + snap-pac oder timeshift."))
        else:
            R["snap"].set("off", "Nicht möglich", f"System-Snapshots brauchen Btrfs (hier: {c['root_fs'] or '?'}). "
                          "Die Tuxdex-Backups decken das über Snapshots auf einem Ziel ab.")
        # --- Performance
        ssd = [d for d in c["disks"] if not d[1]]
        if not ssd:
            R["trim"].set("off", "Keine SSD", "Kein SSD/NVMe-Laufwerk gefunden.")
        else:
            R["trim"].set("ok" if c["fstrim"] else "warn", "Wöchentlich" if c["fstrim"] else "Aus",
                          "fstrim.timer gibt freie Blöcke einmal pro Woche an die SSD zurück." if c["fstrim"] else
                          "Ohne TRIM werden SSDs mit der Zeit langsamer.", None if c["fstrim"] else "Einschalten",
                          None if c["fstrim"] else lambda: self._root(["systemctl", "enable", "--now",
                                                                       "fstrim.timer"]))
        bad = [(n, cur) for n, rot, cur, av in c["disks"]
               if (not rot and cur not in ("none", "mq-deadline", "kyber")) or (rot and cur not in ("bfq",
                                                                                                    "mq-deadline"))]
        if not c["disks"]:
            R["io"].set("off", "—", "Keine Laufwerke gefunden.")
        elif bad:
            R["io"].set("info", "Anpassen", "Ungewöhnlich: " + ", ".join(f"{n}: {s}" for n, s in bad)
                        + ". Empfohlen: none für NVMe, mq-deadline für SSD, bfq für Festplatten.", "Empfohlen setzen",
                        self.cl_iosched)
        else:
            R["io"].set("ok", "Passend", " · ".join(f"{n}: {cur}" for n, _, cur, _ in c["disks"]))
        ram_gb = c["mem"] / 1024 ** 3
        sw = c["swappiness"]
        if not c["swaps"]:
            R["swapcfg"].set("warn", "Kein Swap", "Ohne Swap/zram beendet Linux bei vollem Speicher Programme.",
                             "Zum Swap", goto("swap"))
        elif sw.isdigit() and ram_gb >= 12 and int(sw) >= 60 and not any("zram" in d[0] for d in c["swaps"]):
            R["swapcfg"].set("info", f"Swappiness {sw}", f"{ram_gb:.0f} GB RAM – mit 10–20 bleibt mehr im schnellen "
                             "Arbeitsspeicher.", "Zum Swap", goto("swap"))
        else:
            R["swapcfg"].set("ok", f"Swappiness {sw}", ", ".join(short_path(d[0]) for d in c["swaps"])
                             + (" – bei zram ist ein hoher Wert richtig." if any("zram" in d[0] for d in c["swaps"])
                                else ""))
        g = c["governor"]
        R["gov"].set("ok" if g else "off", g or "—",
                     {"powersave": "Stromsparend – bei amd-pstate/intel_pstate trotzdem voll schnell, der Energiemodus "
                                   "entscheidet.", "performance": "Immer höchster Takt – schnell, aber mehr Strom und "
                                   "Wärme.", "schedutil": "Passt den Takt der Last an – guter Standard."}.get(
                         g, "Der Taktregler der CPU. Details: Taskmanager → Leistung → Prozessor."))
        R["tmp"].set("ok" if c["tmp_fs"] == "tmpfs" else "info", "tmpfs" if c["tmp_fs"] == "tmpfs" else
                     (c["tmp_fs"] or "Platte"), "/tmp liegt im Arbeitsspeicher – schnell und nach Neustart leer."
                     if c["tmp_fs"] == "tmpfs" else "/tmp liegt auf der Platte. Arch nutzt normalerweise tmpfs "
                     "(tmp.mount) – prüfen, ob /etc/fstab das überschreibt.")
        # --- Laufende Wartung
        je = c["jerr"]
        R["jerr"].set("ok" if not je else "info", "Keine" if not je else f"{len(je)} Meldungen",
                      "Seit dem Start keine Fehler im System-Protokoll." if not je else
                      "Fehler im System-Protokoll seit dem Start (journalctl -p 3 -b). Viele sind harmlos (z. B. "
                      "Firmware-Hinweise) – wiederkehrende lohnen einen Blick.", "Anzeigen" if je else None,
                      (lambda: show_text(self, "Fehler seit dem Start", "journalctl -p 3 -b", "\n".join(je[-300:])))
                      if je else None)
        f = c["failed"]
        R["failed"].set("ok" if not f else "warn", "Keine" if not f else f"{len(f)} Dienst(e)",
                        "Alle Dienste laufen." if not f else "Fehlgeschlagen: " + ", ".join(f[:6])
                        + ". Details: systemctl status NAME.", "Zurücksetzen" if f else None,
                        (lambda: self._root(["systemctl", "reset-failed"])) if f else None)
        if not c["ntp"]:
            R["ntp"].set("off", "Unbekannt", "timedatectl meldet keinen Zeitabgleich (kein systemd-timesyncd?).")
        elif c["ntp"] == "yes":
            R["ntp"].set("ok", "Synchron", "Die Uhrzeit wird über das Netz abgeglichen.")
        else:
            R["ntp"].set("warn", "Nicht synchron", "Falsche Uhrzeit stört Zertifikate, Updates und Logs."
                         + (" NTP ist aus." if c["ntp_on"] != "yes" else ""), "NTP einschalten",
                         lambda: self._root(["timedatectl", "set-ntp", "true"]))
        mods = c["stale_mods"]
        R["mods"].set("ok" if not mods else "info", "Keine" if not mods else f"{len(mods)} Ordner",
                      "Keine Reste alter Kernel." if not mods else "Übrig von entfernten Kernels: "
                      + ", ".join(mods) + " (in /usr/lib/modules).", "Entfernen" if mods else None,
                      (lambda: self.cl_stale_mods(mods)) if mods else None)
        orph, cache = c["orphans"], c["cache"]
        big = cache and cache > 3 * 1024 ** 3
        R["clean"].set("info" if (orph or big) else "ok", f"{len(orph)} verwaist" if orph else "Sauber",
                       (f"{len(orph)} Pakete, die nichts mehr braucht. " if orph else "")
                       + (f"Paket-Cache: {fmt_bytes(cache)}." if cache is not None else ""),
                       "Zum Aufräumen" if (orph or big) else None, goto("storage") if (orph or big) else None)
        n_warn = sum(1 for r in R.values() if r.badge.property("tone") in ("warn", "danger"))
        self.cl_badge.set("ok" if not n_warn else "warn", "Alles gut" if not n_warn else f"{n_warn} Hinweise")

    # ---- Aktionen der Checkliste ----------------------------------------

    def cl_mirrors(self):
        if ask_confirm(self, "Spiegelserver", "Die 20 schnellsten aktuellen HTTPS-Server suchen und als "
                       "/etc/pacman.d/mirrorlist speichern? Die alte Liste bleibt als mirrorlist.bak.", "Aktualisieren"):
            self._root(["sh", "-c", "cp /etc/pacman.d/mirrorlist /etc/pacman.d/mirrorlist.bak; reflector --latest 20 "
                        "--protocol https --sort rate --save /etc/pacman.d/mirrorlist"])

    def cl_journald(self):
        if not ask_confirm(self, "System-Protokoll begrenzen", "Das System-Protokoll auf 500 MB und einen Monat "
                           f"begrenzen?\n\nWird in {JOURNALD_FILE} gespeichert. Ältere Einträge werden gelöscht.",
                           "Begrenzen"):
            return
        body = "# Tuxdex\n[Journal]\nSystemMaxUse=500M\nMaxRetentionSec=1month\n"
        self._root(["sh", "-c", f"umask 022 && mkdir -p {os.path.dirname(JOURNALD_FILE)} && chmod 755 {os.path.dirname(JOURNALD_FILE)} && "
                    f"printf %s {shlex.quote(body)} > {JOURNALD_FILE} && chmod 644 {JOURNALD_FILE} && "
                    f"systemctl restart systemd-journald && journalctl --vacuum-size=500M "
                    "--vacuum-time=1month"])

    def cl_coredump(self):
        if not ask_confirm(self, "Speicherabbilder abschalten", "Bei Abstürzen keine Speicherabbilder mehr speichern "
                           "und vorhandene löschen?\n\nEntwickler brauchen sie manchmal zur Fehlersuche. Wird in "
                           f"{COREDUMP_FILE} gespeichert.", "Abschalten"):
            return
        body = "# Tuxdex\n[Coredump]\nStorage=none\nProcessSizeMax=0\n"
        self._root(["sh", "-c", f"umask 022 && mkdir -p {os.path.dirname(COREDUMP_FILE)} && chmod 755 {os.path.dirname(COREDUMP_FILE)} && "
                    f"printf %s {shlex.quote(body)} > {COREDUMP_FILE} && chmod 644 {COREDUMP_FILE} && "
                    f"rm -f /var/lib/systemd/coredump/* && systemctl daemon-reload"])

    def cl_telemetry(self, items):
        names = ", ".join(n for n, _ in items)
        if not ask_confirm(self, "Telemetrie abschalten", f"In {names} „telemetry.telemetryLevel“ auf „off“ setzen?",
                           "Abschalten"):
            return
        for _, path in items:
            txt = _read(path).strip()
            try:
                data = json.loads(txt) if txt else {}
            except ValueError:
                show_warning(self, "Telemetrie", f"{short_path(path)} enthält Kommentare oder ist ungültig – bitte "
                             "im Editor unter Einstellungen → Telemetry selbst auf „off“ stellen.")
                continue
            data["telemetry.telemetryLevel"] = "off"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
            except OSError as e:
                show_error(self, "Telemetrie", str(e))
        self.refresh_checklist()

    def cl_iosched(self):
        rules = ('# Tuxdex: I/O-Scheduler je Laufwerkstyp\n'
                 'ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", ATTR{queue/scheduler}="none"\n'
                 'ACTION=="add|change", KERNEL=="sd[a-z]*|mmcblk[0-9]*", ATTR{queue/rotational}=="0", '
                 'ATTR{queue/scheduler}="mq-deadline"\n'
                 'ACTION=="add|change", KERNEL=="sd[a-z]*", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"\n')
        if not ask_confirm(self, "I/O-Scheduler", "Empfohlene Scheduler setzen (NVMe: none, SSD: mq-deadline, "
                           f"Festplatte: bfq)?\n\nWird als udev-Regel in {IOSCHED_FILE} gespeichert.", "Setzen"):
            return
        self._root(["sh", "-c", f"umask 022 && printf %s {shlex.quote(rules)} > {IOSCHED_FILE} && chmod 644 {IOSCHED_FILE} && modprobe -q bfq; "
                    "udevadm control --reload && udevadm trigger --subsystem-match=block --action=change"])

    def cl_stale_mods(self, mods):
        if not ask_confirm(self, "Alte Kernel-Module", "Diese Ordner gehören zu keinem installierten Kernel mehr und "
                           "werden gelöscht:\n\n" + "\n".join(f"/usr/lib/modules/{m}" for m in mods), "Entfernen",
                           danger=True):
            return
        self._root(["rm", "-rf", "--"] + [f"/usr/lib/modules/{m}" for m in mods
                                          if re.match(r"^[\w.+-]+$", m) and m != os.uname().release])

    def cl_pkglist(self):
        ok = save_pkglist()
        self.app.set_status(f"Paketliste gespeichert: {short_path(PKGLIST_FILE)}" if ok else
                            "Paketliste konnte nicht gespeichert werden.")
        self.refresh_checklist()

    # ======================================================================
    # Offene Ports – je Port ein Knopf „Sperren“ / „Freigeben“
    # ======================================================================

    PORT_NAMES = {"22": "SSH", "80": "Webserver", "443": "Webserver (HTTPS)", "631": "Drucken (CUPS)",
                  "5353": "Geräteerkennung (mDNS)", "1716": "KDE Connect", "5900": "Bildschirmfreigabe (VNC)",
                  "3389": "Remotedesktop", "139": "Dateifreigabe (Samba)", "445": "Dateifreigabe (Samba)",
                  "8080": "Webserver", "3000": "Entwicklungsserver", "5432": "PostgreSQL", "3306": "MySQL",
                  "6379": "Redis", "27017": "MongoDB", "51820": "WireGuard", "53": "DNS", "68": "DHCP",
                  "546": "DHCPv6", "5355": "Namensauflösung (LLMNR)", "27036": "Steam", "57621": "Spotify"}

    def refresh_ports(self):
        backend = "ufw" if svc_active("ufw") else ("firewalld" if svc_active("firewalld") else None)
        authed = self.app.priv.is_authenticated_nonblocking()

        def worker():
            ports = listening_ports()
            rules = None
            if backend == "ufw" and authed:
                r = subprocess.run(["sudo", "-n", "ufw", "status"], capture_output=True, text=True)
                rules = {}
                for line in r.stdout.splitlines():
                    m = re.match(r"^(\d+)(?:/(tcp|udp))?\s+(?:\(v6\)\s+)?(ALLOW|DENY|REJECT|LIMIT)", line.strip())
                    if m:
                        for pr in ([m.group(2)] if m.group(2) else ["tcp", "udp"]):
                            rules[(m.group(1), pr)] = m.group(3) in ("ALLOW", "LIMIT")
                default_allow = "default: allow (incoming)" in subprocess.run(
                    ["sudo", "-n", "ufw", "status", "verbose"], capture_output=True, text=True).stdout.lower()
                rules["__default__"] = default_allow
            elif backend == "firewalld" and authed:
                r = subprocess.run(["sudo", "-n", "firewall-cmd", "--list-ports"], capture_output=True, text=True)
                rules = {}
                for tok in r.stdout.split():
                    port, _, pr = tok.partition("/")
                    rules[(port, pr)] = True
                rules["__default__"] = False
            ui(lambda: self._show_ports(ports, backend, rules))
        threading.Thread(target=worker, daemon=True).start()

    def _show_ports(self, ports, backend, rules):
        TaskTab._clear(self.ports_grid)
        self.ports_fw_btn.setVisible(bool(ports) and backend is None)
        if not ports:
            self.ports_note.setText("Kein Programm wartet auf Verbindungen von außen – nichts zu tun.")
            return
        if backend is None:
            self.ports_note.setText(f'<span style="color:{COLORS["warn"]}">▲ Ohne aktive Firewall sind alle '
                                    f'Ports unten aus dem Netz erreichbar.</span> Nach dem Aktivieren sind sie '
                                    f'gesperrt und du kannst jeden einzeln per Knopf freigeben.')
        elif rules is None:
            self.ports_note.setText("Oben rechts <b>anmelden</b>, um zu sehen, welche Ports die Firewall "
                                    "durchlässt – dann kannst du sie per Knopf sperren oder freigeben.")
        else:
            self.ports_note.setText("<b>Freigegeben</b> = aus dem Netz erreichbar. <b>Gesperrt</b> = die Firewall "
                                    "blockt Verbindungen von außen; das Programm läuft trotzdem weiter.")
        for c, h in enumerate(("PORT", "PROGRAMM", "WOFÜR", "STATUS", "")):
            self.ports_grid.addWidget(Label(h, "FieldLabel"), 0, c)
        for i, (proto, addr, port, proc) in enumerate(ports, start=1):
            pr = "udp" if proto.startswith("udp") else "tcp"
            self.ports_grid.addWidget(Label(f"{port}/{pr}", "Value"), i, 0)
            self.ports_grid.addWidget(Label(proc if proc != "?" else "unbekannt", "Muted"), i, 1)
            self.ports_grid.addWidget(Label(self.PORT_NAMES.get(port, "—"), "Muted"), i, 2)
            if backend is None:
                open_ = True          # ohne Firewall ist jeder dieser Ports erreichbar
            elif rules is None:
                open_ = None
            else:
                open_ = rules.get((port, pr), rules.get("__default__", False))
            badge = StatusBadge("off", "unbekannt") if open_ is None else \
                (StatusBadge("warn", "Erreichbar" if backend is None else "Freigegeben") if open_
                 else StatusBadge("ok", "Gesperrt"))
            self.ports_grid.addWidget(badge, i, 3)
            if backend and rules is not None:
                b = Button("Sperren" if open_ else "Freigeben", "ghost" if open_ else "primary",
                           lambda _=False, p=port, q=pr, o=open_, n=proc: self.toggle_port(backend, p, q, not o, n))
            elif backend:
                b = Button("Anmelden", "ghost", lambda: (self.app.priv.ensure(self) and self.refresh_ports()))
            else:
                b = None
            if b:
                self.ports_grid.addWidget(b, i, 4)
        self.ports_grid.setColumnStretch(2, 1)

    def toggle_port(self, backend, port, proto, allow, proc):
        what = f"Port {port}/{proto} ({proc})"
        if allow and not ask_confirm(self, "Port freigeben",
                                     f"{what} für Verbindungen aus dem Netz freigeben?\n\n"
                                     "Nur tun, wenn andere Geräte diesen Dienst erreichen sollen.", "Freigeben"):
            return
        if not self.app.priv.ensure(self):
            return
        spec = f"{port}/{proto}"
        if backend == "ufw":
            old, new = ("deny", "allow") if allow else ("allow", "deny")
            steps = [{"cmd": ["ufw", "--force", "delete", old, spec], "needs_sudo": True,
                      "label": f"ufw delete {old} {spec}"},
                     {"cmd": ["ufw", new, spec], "needs_sudo": True, "label": f"ufw {new} {spec}"}]
        else:
            act = "--add-port" if allow else "--remove-port"
            steps = [{"cmd": ["firewall-cmd", "--permanent", f"{act}={spec}"], "needs_sudo": True,
                      "label": f"firewall-cmd --permanent {act}={spec}"},
                     {"cmd": ["firewall-cmd", "--reload"], "needs_sudo": True, "label": "firewall-cmd --reload"}]
        self.app.set_status(f"{what} wird {'freigegeben' if allow else 'gesperrt'} …")
        run_sequence(steps, self.log, on_all_done=lambda: (
            self.app.set_status(f"{what} {'freigegeben' if allow else 'gesperrt'}."), self.refresh_ports()))

    # ======================================================================
    # Mullvad
    # ======================================================================

    @staticmethod
    def _mv_location(status):
        m = re.search(r"Visible location:\s*(.+?)(?:\. IPv4|\n|$)", status) or \
            re.search(r"in (.+?)(?:\n|$)", status)
        relay = re.search(r"Relay:\s*(\S+)", status) or re.search(r"Connected to (\S+)", status)
        parts = []
        if m:
            parts.append(m.group(1).strip())
        if relay:
            parts.append(f"Server {relay.group(1)}")
        return " · ".join(parts)

    def mv_refresh(self):
        if self.mv_loading:
            return
        self.mv_loading = True

        def worker():
            r = {"installed": which("mullvad")}
            if r["installed"]:
                rc, st = _mullvad(["status"])
                r["daemon"] = rc == 0 and "not running" not in st.lower() and "failed to connect" not in st.lower()
                r["status"] = st
                if r["daemon"]:
                    r["account"] = _mullvad(["account", "get"])[1]
                    r["auto"] = _mullvad(["auto-connect", "get"])[1]
                    r["lock"] = _mullvad(["lockdown-mode", "get"])[1]
                    r["lan"] = _mullvad(["lan", "get"])[1]
                    r["dns"] = _mullvad(["dns", "get"])[1]
                    if not self.relays:
                        r["relays"] = _mullvad(["relay", "list"], timeout=20)[1]
                    r["relay_get"] = _mullvad(["relay", "get"])[1]
            ui(lambda: self._mv_show(r))
        threading.Thread(target=worker, daemon=True).start()

    def _mv_show(self, r):
        self.mv_loading = False
        self.mv_connected = False
        installed = r["installed"]
        daemon = r.get("daemon", False)
        self.mv_setup.setVisible(not (installed and daemon))
        self.mv_main.setVisible(installed and daemon)
        for b in (self.b_connect, self.b_disconnect, self.b_reconnect):
            b.setVisible(installed and daemon)
        if not installed:
            self.mv_badge.set("off", "Nicht installiert")
            self.mv_where.setText("")
            self.mv_setup_text.setText("Mullvad VPN ist nicht installiert. Tuxdex funktioniert auch ohne. Sobald "
                                       "Mullvad auf dem System vorhanden ist, lässt es sich hier bedienen.")
            self.b_mv_daemon.hide()
            return
        if not daemon:
            self.mv_badge.set("warn", "Dienst aus")
            self.mv_where.setText("")
            self.mv_setup_text.setText("Mullvad ist installiert, aber der Hintergrunddienst <b>mullvad-daemon</b> "
                                       "läuft nicht.")
            self.b_mv_daemon.show()
            return
        st = r.get("status", "")
        first = st.splitlines()[0].strip().lower() if st else ""
        if first.startswith("connected"):
            self.mv_badge.set("ok", "Verbunden")
            self.mv_connected = True
            self.b_connect.hide()
            self.b_disconnect.show()
            self.b_reconnect.show()
        elif first.startswith("connecting"):
            self.mv_badge.set("info", "Verbinde …")
        elif first.startswith("blocked") or "block" in first:
            self.mv_badge.set("warn", "Blockiert")
        else:
            self.mv_badge.set("off", "Getrennt")
            self.b_disconnect.hide()
            self.b_reconnect.hide()
            self.b_connect.show()
        self.mv_where.setText(self._mv_location(st) or ("Kill-Switch aktiv – ohne VPN kein Internet"
                                                         if _on(r.get("lock", ""), "block") and
                                                         not first.startswith("connected") else ""))
        # Konto
        acc = r.get("account", "")
        logged_in = bool(re.search(r"account(?: number)?:\s*\d", acc, re.I)) or "expires" in acc.lower()
        if logged_in:
            exp = re.search(r"Expires at\s*:\s*(.+)", acc, re.I)
            dev = re.search(r"Device name\s*:\s*(.+)", acc, re.I)
            exp_txt = exp.group(1).strip() if exp else "?"
            try:
                exp_dt = datetime.fromisoformat(exp_txt.replace(" UTC", "").replace("Z", "")[:19])
                days = (exp_dt - datetime.now()).days
                exp_txt = exp_dt.strftime("%d.%m.%Y") + (f"  (noch {days} Tage)" if days >= 0 else "  (abgelaufen)")
            except Exception:
                pass
            full = re.search(r"account(?: number)?:\s*([\d ]+)", acc, re.I)
            self.acc_num = re.sub(r"\D", "", full.group(1)) if full else ""
            self.acc_rest = f"  ·  gültig bis {exp_txt}" + (f"  ·  Gerät „{dev.group(1).strip()}“" if dev else "")
            self._show_acc()
        else:
            self.acc_num = self.acc_rest = ""
            self.acc_info.setText("Nicht angemeldet")
        self.b_eye.setVisible(logged_in and bool(self.acc_num))
        for w in (self.acc_edit, self.b_login):
            w.setVisible(not logged_in)
        self.b_logout.setVisible(logged_in)
        # Einstellungen
        self.o_auto.setChecked(_on(r.get("auto", ""), "autoconnect", "auto-connect"))
        self.o_lock.setChecked(_on(r.get("lock", ""), "block traffic", "lockdown"))
        self.o_lan.setChecked(_on(r.get("lan", ""), "local network sharing", "lan"))
        dns = r.get("dns", "")
        self.o_ads.setChecked(_on(dns, "block ads"))
        self.o_track.setChecked(_on(dns, "block trackers"))
        self.o_mal.setChecked(_on(dns, "block malware"))
        # Standorte
        if r.get("relays"):
            self._parse_relays(r["relays"])
        cur = r.get("relay_get", "")
        m = re.search(r"country (\w\w)\b", cur) or re.search(r"\(([a-z]{2})\)", cur)
        if m and not getattr(self, "_loc_set", False):
            i = self.cb_country.findData(m.group(1))
            if i >= 0:
                self.cb_country.setCurrentIndex(i)
            self._loc_set = True

    def _parse_relays(self, text):
        self.relays = {}
        country = None
        for line in text.splitlines():
            if not line.strip():
                continue
            if not line.startswith(("\t", " ")):
                m = re.match(r"^(.+?) \((\w+)\)", line)
                if m:
                    country = m.group(2)
                    self.relays[country] = (m.group(1), {})
            elif country and not line.startswith(("\t\t", "        ")):
                m = re.match(r"^\s+(.+?) \((\w+)\)", line)
                if m:
                    self.relays[country][1][m.group(2)] = m.group(1)
        self.cb_country.blockSignals(True)
        self.cb_country.clear()
        self.cb_country.addItem("Beliebig (schnellster)", "any")
        for code, (name, _) in sorted(self.relays.items(), key=lambda kv: kv[1][0]):
            self.cb_country.addItem(name, code)
        self.cb_country.blockSignals(False)
        self._fill_cities()

    def _fill_cities(self, *_):
        code = self.cb_country.currentData()
        self.cb_city.clear()
        self.cb_city.addItem("Beliebige Stadt", None)
        if code in self.relays:
            for c, name in sorted(self.relays[code][1].items(), key=lambda kv: kv[1]):
                self.cb_city.addItem(name, c)
        self.cb_city.setEnabled(code in self.relays)

    def _mv_action(self, args, wait=False, then=None):
        self.log.set_text(f"$ mullvad {' '.join(args)}\n")

        def done(rc):
            self.log.append_text(f"[Exit-Code {rc}]\n")
            if then:
                then(rc)
            if wait:
                self._poll_mv(0)
            else:
                self.mv_refresh()
                QTimer.singleShot(800, self.refresh_all)
        run_streaming(["mullvad"] + args, self.log, clear_first=False, on_done=done)

    def _poll_mv(self, n):
        """nach dem Verbinden ein paar Sekunden den Status verfolgen, dann IP prüfen"""
        def cb():
            rc, st = _mullvad(["status"])
            first = st.splitlines()[0].lower() if st else ""
            if first.startswith("connecting") and n < 15:
                QTimer.singleShot(1000, lambda: self._poll_mv(n + 1))
            else:
                self.refresh_all()
                QTimer.singleShot(1500, self.check_public_ip)
        QTimer.singleShot(700, cb)

    def _toggle_lockdown(self, on):
        if on and not ask_confirm(self, "Kill-Switch aktivieren",
                                  "Mit dem Kill-Switch gibt es nur noch Internet, solange das VPN verbunden ist – "
                                  "auch wenn die App geschlossen ist.\n\nAktivieren?", "Aktivieren"):
            self.o_lock.setChecked(False)
            return
        self._mv_action(["lockdown-mode", "set", "on" if on else "off"])

    def _mv_dns(self):
        args = ["dns", "set", "default"]
        if self.o_ads.isChecked():
            args.append("--block-ads")
        if self.o_track.isChecked():
            args.append("--block-trackers")
        if self.o_mal.isChecked():
            args.append("--block-malware")
        self._mv_action(args)

    def mv_set_location(self):
        c = self.cb_country.currentData()
        city = self.cb_city.currentData()
        if c == "any" or c is None:
            args = ["relay", "set", "location", "any"]
        else:
            args = ["relay", "set", "location", c] + ([city] if city else [])
        connected = getattr(self, "mv_connected", False)
        self._mv_action(args, then=(lambda rc: rc == 0 and connected and
                                    self._mv_action(["reconnect"], wait=True)))

    def mv_login(self):
        num = re.sub(r"\D", "", self.acc_edit.text())
        if len(num) != 16:
            show_warning(self, "Kontonummer", "Die Mullvad-Kontonummer hat 16 Ziffern.")
            return
        self.acc_edit.clear()
        self.log.set_text("$ mullvad account login ****\n")
        run_streaming(["mullvad", "account", "login", num], None, clear_first=False,
                      on_done=lambda rc: (self.log.append_text(
                          "Angemeldet.\n" if rc == 0 else "error: Anmeldung fehlgeschlagen – Nummer prüfen oder "
                          "Gerätelimit (5 Geräte) im Mullvad-Konto erreicht.\n"), self.mv_refresh()))

    def _show_acc(self):
        """Kontonummer standardmäßig komplett verdeckt; das Auge zeigt sie an."""
        if self.b_eye.isChecked() and self.acc_num:
            num = " ".join(self.acc_num[i:i + 4] for i in range(0, len(self.acc_num), 4))
        else:
            num = "•••• •••• •••• ••••"
        self.acc_info.setText(num + self.acc_rest)

    def _toggle_acc(self):
        shown = self.b_eye.isChecked()
        self.b_eye.setIcon(svg_icon((EYE_OFF_SVG if shown else EYE_SVG).replace("{c}", COLORS["muted"])))
        self.b_eye.setToolTip("Kontonummer verbergen" if shown else "Kontonummer anzeigen")
        self._show_acc()

    def mv_logout(self):
        if ask_confirm(self, "Mullvad abmelden", "Dieses Gerät vom Mullvad-Konto abmelden?\n"
                       "Das VPN wird getrennt.", "Abmelden", danger=True):
            self._mv_action(["account", "logout"])

    def mv_start_daemon(self):
        self._root(["systemctl", "enable", "--now", "mullvad-daemon"])

    # ======================================================================
    # Firewall
    # ======================================================================

    def fw_refresh(self):
        def worker():
            active = svc_active("ufw") if self.fw_backend == "ufw" else \
                svc_active("firewalld") if self.fw_backend == "firewalld" else False
            ui(lambda: self._fw_show(active))
        threading.Thread(target=worker, daemon=True).start()

    def _fw_show(self, active):
        b = self.fw_backend
        self.b_fw_install.setVisible(b is None)
        for x in (self.b_fw_on, self.b_fw_status, self.b_fw_off):
            x.setVisible(b is not None)
        if b is None:
            self.fw_badge.set("danger", "Nicht installiert")
            self.fw_label.setText("Weder ufw noch firewalld gefunden.")
            return
        self.fw_badge.set("ok" if active else "danger", "Aktiv" if active else "Aus")
        self.fw_label.setText(f"Backend: {b}")
        self.b_fw_on.setVisible(not active)
        self.b_fw_off.setVisible(active)

    def fw_install(self):
        self._root(["pacman", "-S", "ufw"], interactive=True,
                   then=lambda rc: (setattr(self, "fw_backend", "ufw" if which("ufw") else None),
                                    self.fw_rules.setVisible(self.fw_backend == "ufw")))

    def fw_enable(self):
        if not ask_confirm(self, "Firewall", "Firewall aktivieren? Eingehende Verbindungen werden dann blockiert "
                           "(ausgehende bleiben erlaubt).", "Aktivieren"):
            return
        if self.fw_backend == "ufw":
            if not self.app.priv.ensure(self):
                return
            steps = [{"cmd": ["ufw", "default", "deny", "incoming"], "needs_sudo": True, "label": "ufw default deny incoming"},
                     {"cmd": ["ufw", "default", "allow", "outgoing"], "needs_sudo": True, "label": "ufw default allow outgoing"},
                     {"cmd": ["ufw", "--force", "enable"], "needs_sudo": True, "label": "ufw --force enable"},
                     {"cmd": ["systemctl", "enable", "--now", "ufw"], "needs_sudo": True, "label": "systemctl enable --now ufw"}]
            run_sequence(steps, self.log, on_all_done=lambda: (self.fw_status(), self.refresh_all()))
        elif self.fw_backend == "firewalld":
            self._root(["systemctl", "enable", "--now", "firewalld"])

    def fw_disable(self):
        if not ask_confirm(self, "Firewall", "Firewall wirklich deaktivieren?", "Deaktivieren", danger=True):
            return
        if self.fw_backend == "ufw":
            self._root(["ufw", "disable"])
        else:
            self._root(["systemctl", "disable", "--now", "firewalld"])

    def fw_status(self):
        if not self.app.priv.ensure(self):
            return
        if self.fw_backend == "firewalld":
            self.log.set_text("$ sudo firewall-cmd --list-all\n")
            run_streaming(["firewall-cmd", "--list-all"], self.log, needs_sudo=True, clear_first=False)
            return
        self.log.set_text("$ sudo ufw status numbered\n")
        lines = []

        def on_line(l):
            lines.append(l)

        def done(rc):
            self.rule_table.setRowCount(0)
            for l in lines:
                m = re.match(r"^\[\s*(\d+)\]\s+(.+)$", l.strip())
                if m:
                    r = self.rule_table.rowCount()
                    self.rule_table.insertRow(r)
                    self.rule_table.setItem(r, 0, NumItem(m.group(1), int(m.group(1))))
                    it = QTableWidgetItem(re.sub(r"\s{2,}", "   ", m.group(2)))
                    it.setFont(QFont(FONTS["mono"], 10))
                    self.rule_table.setItem(r, 1, it)
            self.fw_refresh()
        run_streaming(["ufw", "status", "numbered"], self.log, needs_sudo=True, clear_first=False,
                      on_line=on_line, on_done=done)

    def fw_add_rule(self):
        port = self.r_port.text().strip()
        if not re.match(r"^\d{1,5}(:\d{1,5})?$", port):
            show_warning(self, "Port", "Bitte einen Port (z. B. 22) oder Bereich (8000:8100) angeben.")
            return
        proto = self.r_proto.currentText()
        act = "allow" if self.r_act.currentIndex() == 0 else "deny"
        spec = port if proto == "tcp+udp" and ":" not in port else f"{port}/{proto.split('+')[0]}"
        self._root(["ufw", act, spec], then=lambda rc: self.fw_status())

    def fw_del_rule(self):
        rows = sorted({i.row() for i in self.rule_table.selectedIndexes()}, reverse=True)
        if not rows:
            show_info(self, "Regel", "Bitte eine Regel in der Liste auswählen.")
            return
        nums = [self.rule_table.item(r, 0).text() for r in rows]
        if not ask_confirm(self, "Regel löschen", f"Regel(n) {', '.join(nums)} löschen?", "Löschen", danger=True):
            return
        if not self.app.priv.ensure(self):
            return
        steps = [{"cmd": ["ufw", "--force", "delete", n], "needs_sudo": True, "label": f"ufw delete {n}"}
                 for n in sorted(nums, key=int, reverse=True)]
        run_sequence(steps, self.log, on_all_done=self.fw_status)


# --------------------------------------------------------------------------
# Einstellungen (vorerst: Über das Projekt & Autor)
# --------------------------------------------------------------------------

APP_AUTHOR = "PyloGER"
APP_COMPANY = "Voxellab"
APP_AUTHOR_MAIL = "contact@voxellab.de"

EYE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}"
 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>"""
EYE_OFF_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}"
 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M10.6 5.1A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-2.9 3.9M6.6 6.6C3.7 8.5 2 12 2 12s3.5 7 10 7
a10.4 10.4 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M3 3l18 18"/></svg>"""
GEAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}"
 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/>
<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21
a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8
1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1
a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1
a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>
</svg>"""


def svg_icon(svg, size=18):
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray
    icon = QIcon()
    for scale in (1, 2):
        px = QPixmap(size * scale, size * scale)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
        px.setDevicePixelRatio(scale)
        icon.addPixmap(px)
    return icon


# --------------------------------------------------------------------------
# Selbst-Aktualisierung (GitHub oder lokale Datei/Ordner)
# --------------------------------------------------------------------------

DEFAULT_REPO = "PyloGER/Tuxdex"   # GitHub-Repository für Updates (in den Einstellungen änderbar)
DEFAULT_BRANCH = "main"
# --------------------------------------------------------------------------
# Backup: Snapshots (rsync + Hardlinks), Spiegel, komprimierte Archive –
# auf mehrere Ziele gleichzeitig. Ohne Qt, damit der Zeitplan (tuxdex --backup)
# auch ohne Fenster läuft.
# --------------------------------------------------------------------------

BACKUP_FILE = os.path.join(os.path.expanduser("~/.config"), "tuxdex", "backup.json")
BACKUP_DIRNAME = "Tuxdex-Backup"
BACKUP_TS = "%Y-%m-%d_%H%M%S"
BACKUP_DEFAULTS = {
    "sources": [os.path.expanduser("~")],
    "excludes": ["~/.cache", "~/.local/share/Trash", "~/.local/share/Steam/steamapps"],
    "targets": [], "disabled": [], "mode": "snapshot", "compression": "zstd", "level": "standard",
    "keep": 10, "verify": True, "delete": True, "root": False, "encrypt": False, "schedule": "off",
    "history": [], "name_pattern": "",
}
BACKUP_INDEX = ".tuxdex-names.json"   # Name → Zeitpunkt, damit frei benannte Backups richtig sortiert werden
# Platzhalter im Namensmuster: yyyy mm dd (Datum), HH MM SS (Uhrzeit). Nur, wenn sie nicht an Buchstaben
# grenzen – „Sommer“ bleibt „Sommer“, „yyyymmdd“ wird trotzdem ersetzt.
_NAME_TOKENS = {"yyyy": "%Y", "mm": "%m", "dd": "%d", "HH": "%H", "MM": "%M", "SS": "%S"}
_NAME_RUN = re.compile(r"(?<![A-Za-zÄÖÜäöüß])((?:yyyy|mm|dd|HH|MM|SS)+)(?![A-Za-zÄÖÜäöüß])")


def backup_label(pattern, when):
    """Ordner-/Dateiname eines Backups aus dem Namensmuster, z. B. „Laptop_yyyy-mm-dd“ → „Laptop_2026-09-27“."""
    pattern = (pattern or "").strip()
    if not pattern:
        return when.strftime(BACKUP_TS)
    s = _NAME_RUN.sub(lambda m: re.sub(r"yyyy|mm|dd|HH|MM|SS", lambda t: when.strftime(_NAME_TOKENS[t.group(0)]),
                                       m.group(1)), pattern)
    s = re.sub(r"[/\\\x00-\x1f]", "-", s).strip().lstrip(".")
    return s[:120] or when.strftime(BACKUP_TS)


def _backup_when(name, path, index):
    """Zeitpunkt eines Backups: aus der Namensliste, dem Standardnamen, einem Datum im Namen oder der Datei."""
    if name in index:
        try:
            return datetime.fromisoformat(index[name])
        except ValueError:
            pass
    m = re.search(r"(\d{4}-\d{2}-\d{2}_\d{6})", name)
    if m:
        try:
            return datetime.strptime(m.group(1), BACKUP_TS)
        except ValueError:
            pass
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(path))
# Dateisysteme mit Linux-Rechten und Hardlinks (Snapshots möglich)
LINUX_FS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "jfs", "reiserfs", "zfs", "bcachefs", "nilfs2"}
FAT_FS = {"vfat", "msdos", "fat"}                  # 4-GB-Grenze je Datei → Archive werden geteilt
PART_SIZE = 4000 * 1024 * 1024
# name: (Programm, Endung, Stufen, Optionen fürs Packen, Befehl zum Entpacken)
COMPRESSORS = {
    "zstd": ("zstd", ".tar.zst", {"schnell": ["-1"], "standard": ["-6"], "stark": ["-19"]},
             ["-T0", "-q", "-c"], ["zstd", "-d", "-q", "-c"]),
    "xz": ("xz", ".tar.xz", {"schnell": ["-1"], "standard": ["-6"], "stark": ["-9e"]},
           ["-T0", "-c"], ["xz", "-d", "-c"]),
    "gzip": ("pigz" if shutil.which("pigz") else "gzip", ".tar.gz", {"schnell": ["-1"], "standard": ["-6"],
                                                                   "stark": ["-9"]}, ["-c"], ["gzip", "-d", "-c"]),
    "none": (None, ".tar", {}, [], None),
}
ARCHIVE_RE = re.compile(r"^(?P<base>.+?(?P<ext>\.tar(?:\.zst|\.xz|\.gz)?)(?P<gpg>\.gpg)?)(?:\.part(?P<part>\d{3}))?$")


PKGLIST_FILE = os.path.join(os.path.expanduser("~/.config"), "tuxdex", "pakete.txt")


def save_pkglist():
    """Selbst installierte Pakete (pakete.txt) und solche aus dem AUR (pakete-aur.txt) in ~/.config/tuxdex."""
    if not which("pacman"):
        return False
    try:
        os.makedirs(os.path.dirname(PKGLIST_FILE), exist_ok=True)
        for args, path in ((["-Qqen"], PKGLIST_FILE), (["-Qqem"], PKGLIST_FILE.replace(".txt", "-aur.txt"))):
            out = subprocess.run(["pacman"] + args, capture_output=True, text=True, timeout=30).stdout
            with open(path, "w") as f:
                f.write(out)
        return True
    except Exception:
        return False


def backup_load():
    cfg = dict(BACKUP_DEFAULTS)
    cfg.update(_load_json(BACKUP_FILE, {}))
    return cfg


def backup_save(cfg):
    _save_json(BACKUP_FILE, cfg)


def backup_host():
    import socket
    return re.sub(r"[^\w.-]", "_", socket.gethostname() or "linux")


def fs_info(path):
    """(Dateisystem, freie Bytes, Einhängepunkt) – (None, 0, None), wenn nicht erreichbar."""
    if not os.path.isdir(path):
        return None, 0, None
    try:
        st = os.statvfs(path)
        free = st.f_bavail * st.f_frsize
    except OSError:
        return None, 0, None
    try:
        out = subprocess.run(["findmnt", "-n", "-o", "FSTYPE,TARGET", "--target", path], capture_output=True,
                             text=True, timeout=5).stdout.split(None, 1)
        return out[0], free, out[1].strip() if len(out) > 1 else None
    except Exception:
        return "?", free, None


def backup_dir(target):
    return os.path.join(target, BACKUP_DIRNAME, backup_host())


def _expand(p):
    return os.path.normpath(os.path.expanduser(p.strip()))


def list_backups(target):
    """Alle Backups auf einem Ziel, neueste zuerst: dict(kind, name, path, when, size, parts, encrypted)."""
    base = backup_dir(target)
    res = []
    index = _load_json(os.path.join(base, BACKUP_INDEX), {})
    snaps = os.path.join(base, "snapshots")
    if os.path.isdir(snaps):
        for n in os.listdir(snaps):
            path = os.path.join(snaps, n)
            if n.startswith(".") or n.endswith(".partial") or os.path.islink(path) or not os.path.isdir(path):
                continue
            when = _backup_when(n, path, index)
            res.append({"kind": "snapshot", "name": n, "path": os.path.join(snaps, n), "when": when,
                        "size": None, "parts": [], "encrypted": False})
    mirror = os.path.join(base, "mirror")
    if os.path.isdir(mirror):
        stamp = os.path.join(base, ".mirror-stamp")
        when = datetime.fromtimestamp(os.path.getmtime(stamp if os.path.exists(stamp) else mirror))
        res.append({"kind": "mirror", "name": "Spiegel", "path": mirror, "when": when, "size": None,
                    "parts": [], "encrypted": False})
    arch = os.path.join(base, "archives")
    if os.path.isdir(arch):
        groups = {}
        for n in sorted(os.listdir(arch)):
            m = ARCHIVE_RE.match(n)
            if not m or n.endswith(".partial"):
                continue
            groups.setdefault(m.group("base"), []).append(os.path.join(arch, n))
        for b, parts in groups.items():
            when = _backup_when(b, parts[0], index)
            res.append({"kind": "archive", "name": b, "path": parts[0], "when": when,
                        "size": sum(os.path.getsize(p) for p in parts), "parts": parts,
                        "encrypted": b.endswith(".gpg")})
    return sorted(res, key=lambda r: r["when"], reverse=True)


def estimate_size(sources, excludes, use_sudo=False):
    """Bytes aller Quellen ohne Ausnahmen (du)."""
    cmd = (["sudo", "-n"] if use_sudo else []) + ["du", "-scb"] + [f"--exclude={e}" for e in excludes] + sources
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900).stdout.strip().splitlines()
        return int(out[-1].split()[0]) if out else 0
    except Exception:
        return 0


class _PartWriter:
    """Schreibt einen Datenstrom in eine Datei – auf FAT in 4-GB-Teile (.part001, .part002 …)."""

    def __init__(self, path, split):
        self.path, self.split = path, split
        self.files, self.fh, self.cur, self.written = [], None, 0, 0
        self._next()

    def _next(self):
        if self.fh:
            self.fh.close()
        name = (f"{self.path}.part{len(self.files) + 1:03d}" if self.split else self.path) + ".partial"
        self.files.append(name)
        self.fh = open(name, "wb")
        self.cur = 0

    def write(self, data):
        while data:
            if self.split and self.cur >= PART_SIZE:
                self._next()
            n = len(data) if not self.split else min(len(data), PART_SIZE - self.cur)
            self.fh.write(data[:n])
            self.cur += n
            self.written += n
            data = data[n:]

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None

    def finish(self):
        self.close()
        final = []
        for f in self.files:
            os.replace(f, f[:-len(".partial")])
            final.append(f[:-len(".partial")])
        if self.split and len(final) == 1:          # nur ein Teil → normaler Dateiname
            os.replace(final[0], self.path)
            final = [self.path]
        self.files = final
        return final

    def discard(self):
        self.close()
        for f in self.files:
            try:
                os.remove(f)
            except OSError:
                pass


class BackupJob:
    """Führt ein Backup auf mehrere Ziele gleichzeitig aus.
    on_progress(ziel, dict) · on_log(text) · on_done(ok, zusammenfassung) – werden aus Threads aufgerufen."""

    RSYNC_RE = re.compile(r"^\s*([\d,.]+)\s+(\d+)%\s+(\S+/s)\s+(\d+:\d{2}:\d{2})")

    def __init__(self, cfg, targets, on_progress, on_log, on_done, passphrase=None, use_sudo=False):
        self.cfg, self.targets = cfg, targets
        self.on_progress, self.on_log, self.on_done = on_progress, on_log, on_done
        self.passphrase, self.use_sudo = passphrase, use_sudo
        self.procs, self.cancelled = [], False
        self.now = datetime.now()
        self.ts = self.now.strftime(BACKUP_TS)
        self.label = backup_label(cfg.get("name_pattern"), self.now)
        self.results = {}

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        for p in list(self.procs):
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass

    def _sudo(self):
        return ["sudo", "-n"] if self.use_sudo else []

    def _popen(self, cmd, **kw):
        p = track(subprocess.Popen(cmd, **kw))
        self.procs.append(p)
        return p

    def _prog(self, t, **kw):
        self.on_progress(t, kw)

    # ---- Ablauf ----
    def _run(self):
        t0 = time.time()
        cfg = self.cfg
        save_pkglist()           # Paketliste landet mit ~/.config im Backup
        self.sources = [s for s in (_expand(x) for x in cfg["sources"]) if os.path.exists(s)]
        self.excludes = [_expand(e) for e in cfg["excludes"] if e.strip()]
        # Ziele, die in einer Quelle liegen, nicht mitsichern (sonst sichert sich das Backup selbst)
        for t in self.targets:
            bd = os.path.join(t, BACKUP_DIRNAME)
            if any(bd == s or bd.startswith(s.rstrip("/") + "/") for s in self.sources):
                self.excludes.append(bd)
        if not self.sources:
            self.on_log("Keine vorhandenen Quellen ausgewählt.\n")
            self.on_done(False, "Keine Quellen")
            return
        self.on_log(f"Quellen: {', '.join(short_path(s) for s in self.sources)}\n"
                    f"Ausnahmen: {', '.join(short_path(e) for e in self.excludes) or '—'}\n")
        for t in self.targets:
            self._prog(t, state="wait", msg="Umfang wird berechnet …")
        self.total = estimate_size(self.sources, self.excludes, self.use_sudo)
        self.on_log(f"Umfang: {fmt_bytes(self.total)}\n")
        if self.cancelled:
            self.on_done(False, "Abgebrochen")
            return
        if self.cfg["mode"] == "archive":
            self._archive()
        else:
            threads = [threading.Thread(target=self._rsync, args=(t,), daemon=True) for t in self.targets]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        ok = [t for t, r in self.results.items() if r == "ok"]
        dur = int(time.time() - t0)
        summary = (f"{len(ok)} von {len(self.targets)} Zielen erfolgreich · {fmt_bytes(self.total)} · "
                   f"Dauer {dur // 3600}:{dur // 60 % 60:02d}:{dur % 60:02d}")
        if not self.cancelled:
            cfg = backup_load()
            cfg["history"] = ([{"at": time.time(), "mode": self.cfg["mode"], "targets": self.targets,
                                "ok": len(ok), "size": self.total, "dur": dur}] + cfg.get("history", []))[:30]
            backup_save(cfg)
        self.on_done(bool(ok) and len(ok) == len(self.targets) and not self.cancelled,
                     "Abgebrochen" if self.cancelled else summary)

    # ---- Snapshot / Spiegel (rsync, je Ziel ein Prozess) ----
    def _rsync(self, target):
        mode = self.cfg["mode"]
        fstype, free, _ = fs_info(target)
        base = backup_dir(target)
        linux = fstype in LINUX_FS
        if mode == "snapshot" and not linux:
            self.results[target] = "error"
            self._prog(target, state="error", msg=f"Snapshots brauchen ein Linux-Dateisystem (ist: {fstype}). "
                                                  "Für dieses Ziel „Archiv“ nutzen.")
            return
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            self.results[target] = "error"
            self._prog(target, state="error", msg=f"Ordner kann nicht angelegt werden: {e.strerror}")
            return
        opts = ["-aHAXR", "--numeric-ids"] if linux else ["-rtR", "--modify-window=2"]
        cmd = self._sudo() + ["rsync"] + opts + ["--info=progress2", "--no-inc-recursive", "--partial"]
        cmd += [f"--exclude={e}" for e in self.excludes]
        if mode == "snapshot":
            snaps = os.path.join(base, "snapshots")
            os.makedirs(snaps, exist_ok=True)
            prev = [b for b in list_backups(target) if b["kind"] == "snapshot"]
            if prev:
                cmd.append(f"--link-dest={prev[0]['path']}")
            if not getattr(self, "snap_name", None):
                self.snap_name = self._unique(self.label, lambda b, n: os.path.exists(
                    os.path.join(b, "snapshots", n)) or os.path.exists(os.path.join(b, "snapshots", n + ".partial")))
            dest = os.path.join(snaps, self.snap_name + ".partial")
        else:
            dest = os.path.join(base, "mirror")
            if self.cfg.get("delete", True):
                cmd += ["--delete", "--delete-excluded"]
        cmd += self.sources + [dest + "/"]
        self.on_log(f"[{short_path(target)}] $ {' '.join(shlex.quote(c) for c in cmd)}\n")
        self._prog(target, state="run", msg="Startet …")
        t0 = time.time()
        try:
            p = self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "LC_ALL": "C"})
        except Exception as e:
            self.results[target] = "error"
            self._prog(target, state="error", msg=str(e))
            return
        errs = []
        threading.Thread(target=lambda: errs.extend(p.stderr.read().decode(errors="replace").splitlines()),
                         daemon=True).start()
        buf = b""
        while True:
            ch = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not ch:
                break
            buf += ch
            *lines, buf = re.split(rb"[\r\n]", buf)
            for ln in lines:
                m = self.RSYNC_RE.match(ln.decode(errors="replace"))
                if m:
                    done = int(m.group(1).replace(",", "").replace(".", ""))
                    self._prog(target, state="run", pct=int(m.group(2)), done=done, speed=m.group(3),
                               eta=m.group(4), el=time.time() - t0)
        rc = p.wait()
        time.sleep(0.2)
        for e in errs[:30]:
            self.on_log(f"[{short_path(target)}] {e}\n")
        if (self.cancelled or rc not in (0, 23, 24)) and mode == "snapshot":
            self._run_quiet(self._sudo() + ["rm", "-rf", "--", dest])
        if self.cancelled:
            self.results[target] = "cancel"
            self._prog(target, state="error", msg="Abgebrochen")
            return
        if rc not in (0, 23, 24):
            self.results[target] = "error"
            self._prog(target, state="error", msg=f"rsync-Fehler (Code {rc}) – Details in der Ausgabe.")
            return
        note = ""
        if rc == 23:
            note = " · einige Dateien nicht lesbar (ggf. mit root-Rechten sichern)"
        elif rc == 24:
            note = " · einige Dateien verschwanden während des Backups"
        if mode == "snapshot":
            final = dest[:-len(".partial")]
            self._run_quiet(self._sudo() + ["mv", dest, final])
            link = os.path.join(base, "latest")
            try:
                if os.path.islink(link):
                    os.remove(link)
                os.symlink(os.path.join("snapshots", self.snap_name), link)
            except OSError:
                pass
            self._remember(target, self.snap_name)
            self._retention(target)
        else:
            try:
                with open(os.path.join(base, ".mirror-stamp"), "w") as f:
                    f.write(self.ts)
            except OSError:
                pass
        self.results[target] = "ok"
        self._prog(target, state="ok", pct=100, msg="Fertig" + note, el=time.time() - t0)

    def _run_quiet(self, cmd):
        try:
            return subprocess.run(cmd, capture_output=True, timeout=3600).returncode
        except Exception:
            return 1

    def _unique(self, name, exists):
        """Hängt _2, _3 … an, falls es den Namen auf einem Ziel schon gibt (z. B. zwei Backups am selben Tag)."""
        cand, i = name, 1
        while any(exists(backup_dir(t), cand) for t in self.targets):
            i += 1
            cand = f"{name}_{i}"
        return cand

    def _remember(self, target, name):
        """Zeitpunkt zum Namen merken – frei benannte Backups werden so richtig sortiert und ausgedünnt."""
        path = os.path.join(backup_dir(target), BACKUP_INDEX)
        index = _load_json(path, {})
        index[name] = self.now.isoformat(timespec="seconds")
        try:
            _save_json(path, index)
        except Exception:
            pass

    def _retention(self, target):
        keep = int(self.cfg.get("keep", 10) or 0)
        if keep <= 0:
            return
        kind = "archive" if self.cfg["mode"] == "archive" else "snapshot"
        old = [b for b in list_backups(target) if b["kind"] == kind][keep:]
        for b in old:
            self.on_log(f"[{short_path(target)}] Alte Version entfernt: {b['name']}\n")
            if kind == "snapshot":
                self._run_quiet(self._sudo() + ["rm", "-rf", "--", b["path"]])
            else:
                for p in b["parts"] + [b["parts"][0].split(".part")[0] + ".sha256"]:
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ---- Archiv: einmal packen, gleichzeitig auf alle Ziele schreiben ----
    def _archive(self):
        import hashlib
        prog, ext, levels, popts, _ = COMPRESSORS.get(self.cfg.get("compression"), COMPRESSORS["zstd"])
        enc = bool(self.passphrase)
        label = self.label if (self.cfg.get("name_pattern") or "").strip() else f"{backup_host()}_{self.ts}"
        suffix = ext + (".gpg" if enc else "")
        label = self._unique(label, lambda b, n: any(
            f.startswith(n + suffix) for f in (os.listdir(os.path.join(b, "archives"))
                                               if os.path.isdir(os.path.join(b, "archives")) else [])))
        name = label + suffix
        writers = {}
        for t in self.targets:
            fstype, free, _ = fs_info(t)
            d = os.path.join(backup_dir(t), "archives")
            try:
                os.makedirs(d, exist_ok=True)
                writers[t] = _PartWriter(os.path.join(d, name), fstype in FAT_FS)
                self._prog(t, state="run", msg="Packt …" + (" (in 4-GB-Teilen wegen FAT32)" if fstype in FAT_FS
                                                             else ""))
            except OSError as e:
                self.results[t] = "error"
                self._prog(t, state="error", msg=f"Kann nicht schreiben: {e.strerror}")
        if not writers:
            return
        rel = [s.lstrip("/") or "." for s in self.sources]
        tar = self._sudo() + ["tar", "-cpf", "-", "--xattrs", "--acls", "--ignore-failed-read",
                              "--warning=no-file-changed", "-C", "/", "--anchored"]
        tar += [f"--exclude={e.lstrip('/')}" for e in self.excludes] + ["--"] + rel
        self.on_log(f"$ {' '.join(shlex.quote(c) for c in tar)}"
                    + (f" | {prog} {' '.join(levels.get(self.cfg.get('level'), []))}" if prog else "")
                    + (" | gpg --symmetric (AES-256)" if enc else "") + "\n")
        errs = []
        try:
            p_tar = self._popen(tar, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            threading.Thread(target=lambda: errs.extend(p_tar.stderr.read().decode(errors="replace").splitlines()),
                             daemon=True).start()
            stages, src = [], None
            if prog:
                p_c = self._popen([prog] + levels.get(self.cfg.get("level"), []) + popts, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)
                stages.append(p_c)
            if enc:
                r, w = os.pipe()
                os.write(w, self.passphrase.encode())
                os.close(w)
                p_g = self._popen(["gpg", "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback",
                                   "--passphrase-fd", str(r), "--symmetric", "--cipher-algo", "AES256",
                                   "--compress-algo", "none", "-o", "-"],
                                  stdin=stages[-1].stdout if stages else subprocess.PIPE, stdout=subprocess.PIPE,
                                  pass_fds=(r,))
                os.close(r)
                if stages:
                    stages[-1].stdout.close()
                stages.append(p_g)
        except Exception as e:
            for w in writers.values():
                w.discard()
            for t in writers:
                self.results[t] = "error"
                self._prog(t, state="error", msg=f"Start fehlgeschlagen: {e}")
            return
        first_in = stages[0].stdin if stages else None
        last_out = stages[-1].stdout if stages else p_tar.stdout
        state = {"read": 0}
        t0 = time.time()

        def pump():                         # tar → Packer (zählt die unkomprimierten Bytes)
            try:
                while True:
                    ch = p_tar.stdout.read(1 << 20)
                    if not ch:
                        break
                    state["read"] += len(ch)
                    first_in.write(ch)
            except (BrokenPipeError, ValueError, OSError):
                pass
            finally:
                try:
                    first_in.close()
                except Exception:
                    pass
        if first_in:
            threading.Thread(target=pump, daemon=True).start()
        h = hashlib.sha256()
        failed = {}
        last_ui = 0
        while True:
            ch = last_out.read(1 << 20)
            if not ch:
                break
            if not first_in:
                state["read"] += len(ch)
            h.update(ch)
            for t, w in list(writers.items()):
                if t in failed:
                    continue
                try:
                    w.write(ch)
                except OSError as e:
                    failed[t] = "Ziel ist voll" if e.errno == 28 else f"Schreibfehler: {e.strerror}"
                    self._prog(t, state="error", msg=failed[t])
                    w.discard()
            if len(failed) == len(writers):
                self.cancel()
                break
            now = time.time()
            if now - last_ui > 0.5:
                last_ui = now
                el = now - t0
                rd = state["read"]
                spd = rd / el if el > 0 else 0
                pct = min(99, int(rd / self.total * 100)) if self.total else 0
                eta = (self.total - rd) / spd if spd > 0 and self.total > rd else 0
                for t, w in writers.items():
                    if t not in failed:
                        self._prog(t, state="run", pct=pct, done=rd, speed=f"{fmt_bytes(spd)}/s",
                                   eta=f"{int(eta) // 3600}:{int(eta) // 60 % 60:02d}:{int(eta) % 60:02d}",
                                   el=el, written=w.written)
        rc_tar = p_tar.wait()
        rcs = [s.wait() for s in stages]
        for e in errs[:30]:
            self.on_log(f"tar: {e}\n")
        broken = self.cancelled or rc_tar not in (0, 1) or any(rcs)
        digest = h.hexdigest()
        ok_targets = []
        for t, w in writers.items():
            if t in failed:
                self.results[t] = "error"
                continue
            if broken:
                w.discard()
                self.results[t] = "cancel" if self.cancelled else "error"
                self._prog(t, state="error", msg="Abgebrochen" if self.cancelled else
                           f"Packen fehlgeschlagen (tar {rc_tar}, Packer {rcs}) – Details in der Ausgabe.")
                continue
            files = w.finish()
            try:
                with open(os.path.join(os.path.dirname(w.path), name + ".sha256"), "w") as f:
                    f.write(f"{digest}  {name}\n")
            except OSError:
                pass
            ok_targets.append((t, files, w.written))
        if broken:
            return

        def verify(t, files, size):
            if self.cfg.get("verify", True):
                self._prog(t, state="run", pct=100, msg="Prüfe geschriebene Daten …")
                hv = hashlib.sha256()
                try:
                    for fpath in files:
                        with open(fpath, "rb") as f:
                            for ch in iter(lambda: f.read(1 << 20), b""):
                                hv.update(ch)
                except OSError as e:
                    hv = None
                    self.on_log(f"[{short_path(t)}] Prüfung fehlgeschlagen: {e}\n")
                if not hv or hv.hexdigest() != digest:
                    self.results[t] = "error"
                    self._prog(t, state="error", msg="Prüfsumme stimmt nicht – Datenträger defekt?")
                    return
            self._remember(t, name)
            self._retention(t)
            self.results[t] = "ok"
            note = f" · {len(files)} Teile" if len(files) > 1 else ""
            ratio = f" · {size / self.total * 100:.0f} % der Originalgröße" if self.total else ""
            self._prog(t, state="ok", pct=100, el=time.time() - t0, written=size,
                       msg=f"Fertig · {fmt_bytes(size)}{ratio}{note}"
                       + (" · geprüft" if self.cfg.get("verify", True) else ""))
        ths = [threading.Thread(target=verify, args=a, daemon=True) for a in ok_targets]
        for th in ths:
            th.start()
        for th in ths:
            th.join()


def restore_command(b, dest, use_sudo, passfile=None):
    """Shell-Befehl, der ein Backup nach dest zurückspielt (dest="/" = Originalort)."""
    sudo = "sudo -n " if use_sudo else ""
    q = shlex.quote
    if b["kind"] in ("snapshot", "mirror"):
        return f"{sudo}rsync -aHAX --info=progress2 {q(b['path'].rstrip('/') + '/')} {q(dest.rstrip('/') + '/')}"
    m = ARCHIVE_RE.match(os.path.basename(b["parts"][0]))
    ext = m.group("ext") if m else ".tar"
    dec = next((c[4] for c in COMPRESSORS.values() if c[1] == ext), None)
    cmd = "cat " + " ".join(q(p) for p in b["parts"])
    if b["encrypted"]:
        cmd += f" | gpg --batch --quiet --pinentry-mode loopback --passphrase-file {q(passfile)} -d"
    if dec:
        cmd += " | " + " ".join(dec)
    return cmd + f" | {sudo}tar -xpf - --xattrs --acls -C {q(dest)}"


def run_backup_cli():
    """tuxdex --backup: Backup ohne Fenster (für den Zeitplan)."""
    cfg = backup_load()
    if cfg["mode"] == "archive" and cfg.get("encrypt"):
        print("Verschlüsselte Archive brauchen ein Passwort und laufen nur aus dem Fenster.")
        return 2
    targets = [t for t in cfg["targets"] if t not in cfg.get("disabled", []) and os.path.isdir(t)]
    skipped = [t for t in cfg["targets"] if t not in targets and t not in cfg.get("disabled", [])]
    for t in skipped:
        print(f"Übersprungen (nicht angeschlossen): {t}")
    if not targets:
        print("Kein Ziel erreichbar – nichts zu tun.")
        return 1
    done = threading.Event()
    res = {}
    job = BackupJob(cfg, targets, lambda t, d: d.get("state") in ("ok", "error") and print(
                    f"[{t}] {d.get('msg', '')}", flush=True),
                    lambda s: print(s, end="", flush=True),
                    lambda ok, s: (res.update(ok=ok, s=s), done.set()))
    job.start()
    done.wait()
    print(res.get("s", ""))
    if which("notify-send"):
        subprocess.run(["notify-send", "-a", "Tuxdex", "-i", "tuxdex",
                        "Backup fertig" if res.get("ok") else "Backup mit Problemen", res.get("s", "")])
    return 0 if res.get("ok") else 1


SYSTEMD_USER = os.path.join(os.path.expanduser("~/.config"), "systemd", "user")


def backup_schedule_state():
    """(aktiv, nächster Lauf als Text)"""
    try:
        r = subprocess.run(["systemctl", "--user", "list-timers", "tuxdex-backup.timer", "--no-legend"],
                           capture_output=True, text=True, timeout=5).stdout.strip()
        if not r:
            return False, ""
        return True, " ".join(r.split()[:4])
    except Exception:
        return False, ""


def backup_schedule_set(when):
    """when: off | daily | weekly. Gibt (ok, text) zurück."""
    timer = os.path.join(SYSTEMD_USER, "tuxdex-backup.timer")
    service = os.path.join(SYSTEMD_USER, "tuxdex-backup.service")
    if when == "off":
        subprocess.run(["systemctl", "--user", "disable", "--now", "tuxdex-backup.timer"], capture_output=True)
        for f in (timer, service):
            try:
                os.remove(f)
            except OSError:
                pass
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return True, "Zeitplan aus"
    exe = "/usr/bin/tuxdex" if SYSTEM_INSTALL else f"/usr/bin/python3 {os.path.abspath(__file__)}"
    os.makedirs(SYSTEMD_USER, exist_ok=True)
    with open(service, "w") as f:
        f.write("[Unit]\nDescription=Tuxdex Backup\n\n[Service]\nType=oneshot\n"
                f"ExecStart={exe} --backup\nNice=10\nIOSchedulingClass=idle\n")
    with open(timer, "w") as f:
        f.write(f"[Unit]\nDescription=Tuxdex Backup ({when})\n\n[Timer]\n"
                f"OnCalendar={'daily' if when == 'daily' else 'weekly'}\nPersistent=true\n"
                "RandomizedDelaySec=15min\n\n[Install]\nWantedBy=timers.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "tuxdex-backup.timer"], capture_output=True,
                       text=True)
    return r.returncode == 0, (r.stderr.strip() or "Zeitplan aktiv")


class BackupTab(Page):
    MODES = [("snapshot", "Snapshots"), ("mirror", "Spiegel"), ("archive", "Archiv")]
    MODE_HINT = {
        "snapshot": "Jedes Backup ist eine eigene Version (Datum/Uhrzeit). Unveränderte Dateien werden nur "
                    "verlinkt und kosten keinen Platz – wie Time Machine. Braucht ext4, btrfs, xfs …",
        "mirror": "Eine 1:1-Kopie, die bei jedem Lauf nur die Änderungen überträgt. Schnell, aber nur ein Stand.",
        "archive": "Eine einzige komprimierte Datei pro Backup, optional mit Passwort. Wird einmal gepackt und "
                   "gleichzeitig auf alle Ziele geschrieben. Passt auf jeden Datenträger (FAT32: 4-GB-Teile).",
    }
    KEEP = [(3, "3"), (5, "5"), (10, "10"), (20, "20"), (50, "50"), (0, "alle")]

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = backup_load()
        self.job = None
        self.rows = {}

        self.badge = StatusBadge("off", "…")
        self.lay.addLayout(page_header("Backup", self.badge))

        # ---------- Was ----------
        src = Panel("Was sichern?", [Button("Home-Ordner", "ghost", lambda: self._add_source("~")),
                                      Button("Systemeinstellungen (/etc)", "ghost", lambda: self._add_source("/etc")),
                                      Button("Ordner hinzufügen …", "ghost", self._pick_source)])
        self.src_list = QListWidget()
        self.src_list.setMaximumHeight(110)
        self.src_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        src.body.addWidget(self.src_list)
        sb = QHBoxLayout()
        sb.addWidget(Button("Ausgewählte entfernen", "ghost", self._del_source))
        sb.addStretch(1)
        self.size_lbl = Label("", "Muted")
        sb.addWidget(self.size_lbl)
        sb.addWidget(Button("Größe berechnen", "ghost", self._estimate))
        src.body.addLayout(sb)
        self.excl = QPlainTextEdit()
        self.excl.setObjectName("Log")
        self.excl.setFont(QFont(FONTS["mono"], 10))
        self.excl.setMaximumHeight(80)
        self.excl.setPlaceholderText("Ein Pfad pro Zeile, z. B. ~/.cache")
        src.body.addLayout(Field("Nicht sichern (ein Pfad pro Zeile)", self.excl))
        self.lay.addWidget(src)

        # ---------- Wohin ----------
        tg = Panel("Wohin? – alle angehakten Ziele werden gleichzeitig beschrieben")
        self.tg_box = QVBoxLayout()
        self.tg_box.setSpacing(6)
        tg.body.addLayout(self.tg_box)
        tb = QHBoxLayout()
        tb.setSpacing(8)
        self.drive_cb = QComboBox()
        self.drive_cb.setMinimumWidth(320)
        self.drive_cb.setMinimumHeight(38)
        tb.addWidget(self.drive_cb, 1)
        tb.addWidget(Button("Laufwerk hinzufügen", "ghost", self._add_drive))
        tb.addWidget(Button("Ordner wählen …", "ghost", self._pick_target))
        tb.addWidget(Button("↻", "icon", self.refresh_targets, "Laufwerke neu einlesen"))
        tg.body.addLayout(tb)
        tg.body.addWidget(Label(f"Backups liegen auf dem Ziel im Ordner {BACKUP_DIRNAME}/{backup_host()}/.",
                                "Hint", wrap=True))
        self.lay.addWidget(tg)

        # ---------- Wie ----------
        how = Panel("Wie?")
        mrow = QHBoxLayout()
        self.seg = Segmented([m[1] for m in self.MODES], self._mode_changed)
        mrow.addWidget(self.seg)
        mrow.addStretch(1)
        how.body.addLayout(mrow)
        self.mode_hint = Label("", "Hint", wrap=True)
        how.body.addWidget(self.mode_hint)
        opts = QGridLayout()
        opts.setHorizontalSpacing(24)
        opts.setVerticalSpacing(8)
        self.cb_comp = QComboBox()
        for k, t in (("zstd", "zstd – schnell, gut (empfohlen)"), ("xz", "xz – am kleinsten, langsam"),
                     ("gzip", "gzip – überall lesbar"), ("none", "keine Kompression")):
            if k == "none" or which(COMPRESSORS[k][0]):
                self.cb_comp.addItem(t, k)
        self.cb_level = QComboBox()
        for k, t in (("schnell", "Schnell"), ("standard", "Ausgewogen"), ("stark", "Maximal (langsam)")):
            self.cb_level.addItem(t, k)
        self.cb_keep = QComboBox()
        for k, t in self.KEEP:
            self.cb_keep.addItem(t, k)
        for cb in (self.cb_comp, self.cb_level, self.cb_keep):
            cb.setMinimumHeight(38)
        self.f_comp = QWidget()
        self.f_comp.setLayout(Field("Kompression", self.cb_comp))
        self.f_level = QWidget()
        self.f_level.setLayout(Field("Stärke", self.cb_level))
        self.f_keep = QWidget()
        self.f_keep.setLayout(Field("Versionen behalten", self.cb_keep))
        opts.addWidget(self.f_comp, 0, 0)
        opts.addWidget(self.f_level, 0, 1)
        opts.addWidget(self.f_keep, 0, 2)
        opts.setColumnStretch(3, 1)
        how.body.addLayout(opts)
        self.name_edit = LineEdit(placeholder="leer = Standard, z. B. 2026-09-27_101500", mono=True)
        self.name_edit.setMaximumWidth(420)
        self.name_edit.textChanged.connect(self._name_preview)
        self.name_edit.editingFinished.connect(self._save)
        self.name_hint = Label("", "Hint", wrap=True)
        self.name_hint.setTextFormat(Qt.RichText)
        nb = QVBoxLayout()
        nb.setContentsMargins(0, 0, 0, 0)
        nb.addLayout(Field("Name der Sicherung", self.name_edit))
        nb.addWidget(self.name_hint)
        self.f_name = QWidget()
        self.f_name.setLayout(nb)
        how.body.addWidget(self.f_name)
        self.cb_comp.currentIndexChanged.connect(lambda _=0: self._name_preview())
        self.o_enc = QCheckBox("Mit Passwort verschlüsseln (AES-256, gpg)")
        self.o_enc.setEnabled(which("gpg"))
        self.pw1 = LineEdit(placeholder="Passwort")
        self.pw2 = LineEdit(placeholder="Passwort wiederholen")
        for pw in (self.pw1, self.pw2):
            pw.setEchoMode(QLineEdit.Password)
            pw.setMaximumWidth(260)
        erow = QHBoxLayout()
        erow.setSpacing(8)
        erow.addWidget(self.o_enc)
        erow.addWidget(self.pw1)
        erow.addWidget(self.pw2)
        erow.addStretch(1)
        self.enc_row = QWidget()
        self.enc_row.setLayout(erow)
        erow.setContentsMargins(0, 0, 0, 0)
        how.body.addWidget(self.enc_row)
        self.o_enc.toggled.connect(lambda v: (self.pw1.setVisible(v), self.pw2.setVisible(v)))
        self.o_verify = QCheckBox("Nach dem Schreiben prüfen (liest das Archiv zurück und vergleicht die Prüfsumme)")
        self.o_delete = QCheckBox("Im Original gelöschte Dateien auch im Spiegel löschen")
        self.o_root = QCheckBox("Mit root-Rechten (nötig für Systemordner wie /etc)")
        for w in (self.o_verify, self.o_delete, self.o_root):
            how.body.addWidget(w)
        brow = QHBoxLayout()
        self.b_start = Button("Backup starten", "primary", self.start)
        self.b_cancel = Button("Abbrechen", "danger", self.cancel)
        self.b_cancel.hide()
        brow.addWidget(self.b_start)
        brow.addWidget(self.b_cancel)
        brow.addStretch(1)
        how.body.addLayout(brow)
        self.lay.addWidget(how)

        # ---------- Fortschritt ----------
        self.prog_panel = Panel("Fortschritt")
        ph = QHBoxLayout()
        ph.setSpacing(12)
        self.run_badge = StatusBadge("off", "Bereit")
        ph.addWidget(self.run_badge)
        self.run_time = Label("", "Value")
        ph.addWidget(self.run_time)
        ph.addStretch(1)
        self.prog_panel.body.addLayout(ph)
        self.prog_box = QVBoxLayout()
        self.prog_box.setSpacing(10)
        self.prog_panel.body.addLayout(self.prog_box)
        self.prog_panel.hide()
        self.lay.addWidget(self.prog_panel)
        self.run_timer = QTimer(self)
        self.run_timer.timeout.connect(self._run_tick)

        # ---------- Vorhandene Backups ----------
        ex = Panel("Vorhandene Backups & Wiederherstellen")
        er = QHBoxLayout()
        er.setSpacing(8)
        self.ex_target = QComboBox()
        self.ex_target.setMinimumHeight(38)
        self.ex_target.setMinimumWidth(320)
        self.ex_target.currentIndexChanged.connect(lambda _=0: self.refresh_existing())
        er.addWidget(self.ex_target, 1)
        er.addWidget(Button("↻", "icon", self.refresh_existing, "Neu einlesen"))
        ex.body.addLayout(er)
        self.ex_table = QTableWidget(0, 4)
        self.ex_table.setHorizontalHeaderLabels(["DATUM", "ART", "GRÖSSE", "DETAILS"])
        self.ex_table.verticalHeader().setVisible(False)
        self.ex_table.setShowGrid(False)
        self.ex_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ex_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ex_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ex_table.horizontalHeader().setStretchLastSection(True)
        self.ex_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, w in enumerate((170, 110, 110)):
            self.ex_table.setColumnWidth(i, w)
        self.ex_table.setMinimumHeight(170)
        ex.body.addWidget(self.ex_table)
        xb = QHBoxLayout()
        xb.setSpacing(8)
        xb.addWidget(Button("Öffnen", "ghost", self.open_backup, "Im Dateimanager zeigen"))
        xb.addWidget(Button("In Ordner wiederherstellen …", "primary", lambda: self.restore(False)))
        xb.addWidget(Button("An Originalort zurückspielen …", "ghost", lambda: self.restore(True)))
        xb.addStretch(1)
        xb.addWidget(Button("Löschen", "danger", self.delete_backup))
        ex.body.addLayout(xb)
        self.lay.addWidget(ex)

        # ---------- Zeitplan ----------
        sc = Panel("Automatisch sichern")
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.cb_sched = QComboBox()
        self.cb_sched.setMinimumHeight(38)
        for k, t in (("off", "Aus"), ("daily", "Täglich"), ("weekly", "Wöchentlich")):
            self.cb_sched.addItem(t, k)
        sr.addWidget(self.cb_sched)
        sr.addWidget(Button("Übernehmen", "ghost", self.apply_schedule))
        self.sched_lbl = Label("", "Muted", wrap=True)
        sr.addWidget(self.sched_lbl, 1)
        sc.body.addLayout(sr)
        sc.body.addWidget(Label("Läuft im Hintergrund mit den Einstellungen oben – auch wenn Tuxdex geschlossen ist. "
                                "Nicht angeschlossene Ziele werden übersprungen, verpasste Termine nachgeholt. "
                                "Ohne root-Rechte und ohne Passwort-Verschlüsselung.", "Hint", wrap=True))
        self.lay.addWidget(sc)

        out = Panel("Ausgabe")
        self.log = LogView(140)
        out.body.addWidget(self.log)
        self.lay.addWidget(out)

        self._load_cfg()
        self.refresh_targets()
        self._refresh_badge()
        self._refresh_schedule()

    # ======================================================================
    # Einstellungen
    # ======================================================================

    def _load_cfg(self):
        c = self.cfg
        self.src_list.clear()
        for s in c["sources"]:
            self.src_list.addItem(short_path(_expand(s)))
        self.excl.setPlainText("\n".join(c["excludes"]))
        idx = next((i for i, m in enumerate(self.MODES) if m[0] == c["mode"]), 0)
        self.seg.set(idx)
        for cb, val in ((self.cb_comp, c["compression"]), (self.cb_level, c["level"]), (self.cb_keep, c["keep"]),
                        (self.cb_sched, c.get("schedule", "off"))):
            i = cb.findData(val)
            if i >= 0:
                cb.setCurrentIndex(i)
        self.name_edit.setText(c.get("name_pattern", ""))
        self._name_preview()
        self.o_verify.setChecked(c.get("verify", True))
        self.o_delete.setChecked(c.get("delete", True))
        self.o_root.setChecked(c.get("root", False))
        self.o_enc.setChecked(c.get("encrypt", False) and which("gpg"))
        self.pw1.setVisible(self.o_enc.isChecked())
        self.pw2.setVisible(self.o_enc.isChecked())
        self._mode_changed(idx, save=False)

    def _collect(self):
        c = self.cfg
        home = os.path.expanduser("~")
        c["sources"] = [self.src_list.item(i).text().replace("~", home, 1) if self.src_list.item(i).text()
                        .startswith("~") else self.src_list.item(i).text() for i in range(self.src_list.count())]
        c["excludes"] = [l.strip() for l in self.excl.toPlainText().splitlines() if l.strip()]
        c["mode"] = self.mode
        c["compression"] = self.cb_comp.currentData()
        c["level"] = self.cb_level.currentData()
        c["keep"] = self.cb_keep.currentData()
        c["name_pattern"] = self.name_edit.text().strip()
        c["verify"] = self.o_verify.isChecked()
        c["delete"] = self.o_delete.isChecked()
        c["root"] = self.o_root.isChecked()
        c["encrypt"] = self.o_enc.isChecked()
        c["disabled"] = [t for t, r in self.rows.items() if not r["cb"].isChecked()]
        return c

    def _save(self):
        cfg = self._collect()
        cfg["history"] = backup_load().get("history", [])
        backup_save(cfg)

    def _name_preview(self, *_):
        from html import escape as html_escape
        mode = getattr(self, "mode", "snapshot")
        pat = self.name_edit.text().strip()
        ex = backup_label(pat, datetime.now())
        if mode == "archive" and not pat:
            ex = f"{backup_host()}_{ex}"
        ext = COMPRESSORS.get(self.cb_comp.currentData() or "zstd", COMPRESSORS["zstd"])[1] \
            if mode == "archive" else ""
        self.name_hint.setText(
            f"Heute hieße die Sicherung: <b>{html_escape(ex + ext)}</b><br>"
            "Platzhalter: <b>yyyy</b> Jahr · <b>mm</b> Monat · <b>dd</b> Tag · <b>HH</b> Stunde · <b>MM</b> Minute · "
            "<b>SS</b> Sekunde – mit beliebigem Text davor oder dahinter, z. B. <b>Laptop_yyyy-mm-dd</b> oder "
            "<b>yyyy-mm-dd vor Update</b>. Gibt es den Namen schon, hängt Tuxdex _2, _3 … an.")

    def _mode_changed(self, idx, save=True):
        self.mode = self.MODES[idx][0]
        self.mode_hint.setText(self.MODE_HINT[self.mode])
        arch = self.mode == "archive"
        self.f_comp.setVisible(arch)
        self.f_level.setVisible(arch)
        self.enc_row.setVisible(arch)
        self.o_verify.setVisible(arch)
        self.f_keep.setVisible(self.mode != "mirror")
        self.f_name.setVisible(self.mode != "mirror")
        self._name_preview()
        self.o_delete.setVisible(self.mode == "mirror")
        self._update_target_notes()

    def _add_source(self, p):
        p = short_path(_expand(p))
        if not any(self.src_list.item(i).text() == p for i in range(self.src_list.count())):
            self.src_list.addItem(p)
            if p == "/etc":
                self.o_root.setChecked(True)
        self._save()

    def _pick_source(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Ordner sichern", os.path.expanduser("~"))
        if d:
            self._add_source(d)

    def _del_source(self):
        for it in self.src_list.selectedItems():
            self.src_list.takeItem(self.src_list.row(it))
        self._save()

    def _estimate(self):
        c = self._collect()
        use_sudo = c["root"] and self.app.priv.is_authenticated_nonblocking()
        self.size_lbl.setText("Berechne …")
        srcs = [_expand(s) for s in c["sources"] if os.path.exists(_expand(s))]
        exc = [_expand(e) for e in c["excludes"]]

        def worker():
            n = estimate_size(srcs, exc, use_sudo) if srcs else 0
            ui(lambda: self.size_lbl.setText(f"Umfang: {fmt_bytes(n)}"
                                             + ("" if use_sudo or not c["root"] else " (ohne root – evtl. zu wenig)")))
        threading.Thread(target=worker, daemon=True).start()

    # ======================================================================
    # Ziele
    # ======================================================================

    def refresh_targets(self):
        # Laufwerke zur Auswahl: alle eingehängten echten Datenträger außer Systempartitionen
        self.drive_cb.clear()
        try:
            out = subprocess.run(["df", "-B1", "--output=source,fstype,avail,target", "-x", "tmpfs", "-x",
                                  "devtmpfs", "-x", "squashfs", "-x", "overlay", "-x", "efivarfs"],
                                 capture_output=True, text=True, timeout=5).stdout
        except Exception:
            out = ""
        seen = set()
        for line in out.splitlines()[1:]:
            p = line.split(None, 3)
            if len(p) < 4 or not p[0].startswith("/dev/") or p[0] in seen:
                continue
            seen.add(p[0])
            tgt = p[3]
            if tgt in SYSTEM_MOUNTS or tgt.startswith(("/boot", "/efi")):
                continue
            self.drive_cb.addItem(f"{short_path(tgt)}  ·  {p[1]}  ·  {fmt_bytes(int(p[2]))} frei", tgt)
        if not self.drive_cb.count():
            self.drive_cb.addItem("Kein externes Laufwerk eingehängt – Stick einstecken oder Ordner wählen", None)
        self._build_target_rows()

    def _build_target_rows(self):
        while self.tg_box.count():
            it = self.tg_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = {}
        if not self.cfg["targets"]:
            self.tg_box.addWidget(Label("Noch kein Ziel – unten ein Laufwerk oder einen Ordner hinzufügen.", "Muted"))
        for t in self.cfg["targets"]:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(12)
            cb = QCheckBox(short_path(t))
            cb.setChecked(t not in self.cfg.get("disabled", []))
            cb.toggled.connect(lambda _=False: self._save())
            h.addWidget(cb)
            info = Label("", "Hint", wrap=True)
            h.addWidget(info, 1)
            h.addWidget(Button("Entfernen", "ghost", lambda _=False, t=t: self._del_target(t)))
            self.tg_box.addWidget(w)
            self.rows[t] = {"cb": cb, "info": info}
        self._update_target_notes()
        prev = self.ex_target.currentData()
        self.ex_target.blockSignals(True)
        self.ex_target.clear()
        for t in self.cfg["targets"]:
            self.ex_target.addItem(short_path(t), t)
        i = self.ex_target.findData(prev)
        self.ex_target.setCurrentIndex(max(0, i))
        self.ex_target.blockSignals(False)
        self.refresh_existing()

    def _update_target_notes(self):
        for t, r in self.rows.items():
            fstype, free, mnt = fs_info(t)
            if fstype is None:
                r["info"].setText("nicht angeschlossen – wird übersprungen")
                r["cb"].setEnabled(False)
                continue
            r["cb"].setEnabled(True)
            note = f"{fstype} · {fmt_bytes(free)} frei"
            if self.mode == "snapshot" and fstype not in LINUX_FS:
                note += " · ⚠ keine Snapshots auf diesem Dateisystem – „Archiv“ wählen"
            elif self.mode == "archive" and fstype in FAT_FS:
                note += " · FAT32: Archiv wird in 4-GB-Teile geteilt"
            elif self.mode != "archive" and fstype not in LINUX_FS:
                note += " · ohne Linux-Rechte (Besitzer/Rechte gehen verloren)"
            r["info"].setText(note)

    def _add_target(self, path):
        if not path:
            return
        path = os.path.normpath(path)
        srcs = [_expand(s) for s in self._collect()["sources"]]
        if any(path == s for s in srcs):
            show_warning(self, "Ungültiges Ziel", "Das Ziel darf nicht gleich einer Quelle sein.")
            return
        if path not in self.cfg["targets"]:
            self.cfg["targets"].append(path)
            self._save()
            self._build_target_rows()

    def _add_drive(self):
        self._add_target(self.drive_cb.currentData())

    def _pick_target(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Backup-Ziel wählen", "/run/media")
        if d:
            self._add_target(d)

    def _del_target(self, t):
        if t in self.cfg["targets"]:
            self.cfg["targets"].remove(t)
            self._save()
            self._build_target_rows()

    # ======================================================================
    # Backup ausführen
    # ======================================================================

    def start(self):
        if self.job:
            return
        cfg = self._collect()
        targets = [t for t, r in self.rows.items() if r["cb"].isChecked() and r["cb"].isEnabled()]
        if not cfg["sources"]:
            show_info(self, "Nichts ausgewählt", "Bitte mindestens einen Ordner zum Sichern hinzufügen.")
            return
        if not targets:
            show_info(self, "Kein Ziel", "Bitte mindestens ein angeschlossenes Ziel anhaken.")
            return
        if cfg["mode"] == "snapshot":
            bad = [t for t in targets if fs_info(t)[0] not in LINUX_FS]
            if bad:
                show_warning(self, "Snapshots nicht möglich",
                             "Diese Ziele haben kein Linux-Dateisystem:\n" + "\n".join(short_path(b) for b in bad)
                             + "\n\nFür sie „Archiv“ oder „Spiegel“ verwenden – oder abhaken.")
                return
        pw = None
        if cfg["mode"] == "archive" and cfg["encrypt"]:
            pw = self.pw1.text()
            if len(pw) < 8:
                show_warning(self, "Passwort", "Das Passwort muss mindestens 8 Zeichen haben.")
                return
            if pw != self.pw2.text():
                show_warning(self, "Passwort", "Die Passwörter stimmen nicht überein.")
                return
        use_sudo = cfg["root"]
        if use_sudo and not self.app.priv.ensure(self):
            return
        self._save()
        self.log.set_text("")
        # Fortschritts-Zeilen je Ziel
        while self.prog_box.count():
            it = self.prog_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.prog = {}
        for t in targets:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            hd = QHBoxLayout()
            hd.setSpacing(10)
            badge = StatusBadge("info", "Wartet")
            hd.addWidget(badge)
            hd.addWidget(Label(short_path(t), "PanelTitle"))
            hd.addStretch(1)
            eta = Label("", "Muted")
            hd.addWidget(eta)
            v.addLayout(hd)
            bar = ProgressBar()
            v.addWidget(bar)
            det = Label("", "Hint", wrap=True)
            v.addWidget(det)
            self.prog_box.addWidget(w)
            self.prog[t] = {"badge": badge, "bar": bar, "det": det, "eta": eta}
        self.prog_panel.show()
        self.run_badge.set("ok", "Läuft")
        self.run_started = time.time()
        self.run_timer.start(1000)
        self._run_tick()
        self.b_start.hide()
        self.b_cancel.show()
        self.job = BackupJob(dict(cfg), targets,
                             lambda t, d: ui(lambda: self._on_progress(t, d)),
                             lambda s: ui(lambda: self.log.append_text(s)),
                             lambda ok, s: ui(lambda: self._on_done(ok, s)),
                             passphrase=pw, use_sudo=use_sudo)
        self.job.start()
        QTimer.singleShot(0, lambda: self.ensureWidgetVisible(self.prog_panel, 0, 40))

    def cancel(self):
        if self.job and ask_confirm(self, "Backup abbrechen", "Laufendes Backup abbrechen? Unfertige Dateien "
                                    "werden entfernt, vorhandene Backups bleiben erhalten.", "Abbrechen",
                                    danger=True):
            self.job.cancel()

    def _run_tick(self):
        el = int(time.time() - self.run_started)
        self.run_time.setText(f"{el // 3600:d}:{el // 60 % 60:02d}:{el % 60:02d}")

    def _on_progress(self, t, d):
        p = self.prog.get(t)
        if not p:
            return
        st = d.get("state")
        if st == "ok":
            p["badge"].set("ok", "Fertig")
            p["bar"].set(100, "100 %")
            p["eta"].setText("")
        elif st == "error":
            p["badge"].set("danger", "Fehler")
            p["eta"].setText("")
        elif st == "wait":
            p["badge"].set("info", "Vorbereiten")
            p["bar"].set(None, "…")
        elif st == "run":
            p["badge"].set("ok", "Läuft")
            if "pct" in d:
                p["bar"].set(d["pct"], f"{d['pct']} %")
            if d.get("eta") and d.get("pct", 0) < 100:
                p["eta"].setText(f"Restzeit ca. {d['eta']}")
        if "msg" in d:
            p["det"].setText(d["msg"])
        elif "done" in d:
            speed = re.sub(r"(\d)([kKMGT]?B/s)$", r"\1 \2", d.get("speed", ""))
            txt = f"{fmt_bytes(d['done'])} / {fmt_bytes(self.job.total if self.job else 0)} · {speed}"
            if d.get("written") is not None:
                txt += f" · geschrieben (komprimiert): {fmt_bytes(d['written'])}"
            p["det"].setText(txt)

    def _on_done(self, ok, summary):
        self.run_timer.stop()
        self._run_tick()
        self.job = None
        self.b_cancel.hide()
        self.b_start.show()
        self.run_badge.set("ok" if ok else "warn", "Fertig" if ok else summary.split(" ·")[0])
        self.log.append_text(f"\n{summary}\n")
        self.pw1.clear()
        self.pw2.clear()
        self._refresh_badge()
        self._update_target_notes()
        self.refresh_existing()

    def _refresh_badge(self):
        h = backup_load().get("history", [])
        if not h:
            self.badge.set("warn", "Noch kein Backup")
            return
        last = h[0]
        days = (time.time() - last["at"]) / 86400
        txt = f"Letztes Backup {fmt_ago(last['at'])}"
        self.badge.set("ok" if days < 8 and last.get("ok") else "warn", txt)

    # ======================================================================
    # Vorhandene Backups
    # ======================================================================

    def refresh_existing(self):
        t = self.ex_target.currentData()
        self.ex_table.setRowCount(0)
        self.existing = []
        if not t or not os.path.isdir(t):
            return
        self.existing = list_backups(t)
        kinds = {"snapshot": "Snapshot", "mirror": "Spiegel", "archive": "Archiv"}
        self.ex_table.setRowCount(len(self.existing))
        for i, b in enumerate(self.existing):
            det = os.path.basename(b["name"]) if b["kind"] == "archive" else short_path(b["path"])
            if b["encrypted"]:
                det += " · 🔒 verschlüsselt"
            if len(b["parts"]) > 1:
                det += f" · {len(b['parts'])} Teile"
            for j, v in enumerate((b["when"].strftime("%d.%m.%Y %H:%M"), kinds[b["kind"]],
                                   fmt_bytes(b["size"]) if b["size"] is not None else "—", det)):
                self.ex_table.setItem(i, j, QTableWidgetItem(v))

    def _selected(self):
        r = self.ex_table.currentRow()
        if r < 0 or r >= len(getattr(self, "existing", [])):
            show_info(self, "Nichts ausgewählt", "Bitte zuerst ein Backup in der Liste auswählen.")
            return None
        return self.existing[r]

    def open_backup(self):
        b = self._selected()
        if b and which("xdg-open"):
            p = b["path"] if b["kind"] != "archive" else os.path.dirname(b["path"])
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def restore(self, original):
        b = self._selected()
        if not b or self.job:
            return
        if original:
            dest = "/"
            if not ask_confirm(self, "An Originalort zurückspielen",
                               f"Backup vom {b['when']:%d.%m.%Y %H:%M} an den ursprünglichen Ort zurückschreiben?\n\n"
                               "Gleichnamige Dateien werden durch den Stand aus dem Backup ersetzt. "
                               "Dateien, die es im Backup nicht gibt, bleiben erhalten.", "Zurückspielen",
                               danger=True):
                return
        else:
            from PySide6.QtWidgets import QFileDialog
            dest = QFileDialog.getExistingDirectory(self, "Wiederherstellen nach …", os.path.expanduser("~"))
            if not dest:
                return
            dest = os.path.join(dest, f"Wiederhergestellt_{b['when']:%Y-%m-%d_%H%M}")
            os.makedirs(dest, exist_ok=True)
        passfile = None
        if b["encrypted"]:
            dlg = PasswordDialog(self, "Archiv-Passwort", "Passwort des Archivs",
                                 "Das Backup ist verschlüsselt. Das Passwort wird nicht gespeichert.",
                                 "Wiederherstellen")
            pw = dlg.entry.text() if dlg.exec() == QDialog.Accepted else ""
            if not pw:
                return
            fd, passfile = tempfile.mkstemp(prefix="tuxdex-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
            os.write(fd, pw.encode())
            os.close(fd)
        use_sudo = original or self.o_root.isChecked()
        if use_sudo and not self.app.priv.ensure(self):
            if passfile:
                os.remove(passfile)
            return
        script = "set -o pipefail; " + restore_command(b, dest, use_sudo, passfile)
        self.log.set_text(f"$ {script.replace(passfile or '§', '<passwort>')}\n")

        def done(rc):
            if passfile:
                try:
                    os.remove(passfile)
                except OSError:
                    pass
            self.log.append_text(f"\n[Exit-Code {rc}]\n")
            if rc == 0:
                self.log.append_text(f"Wiederhergestellt nach {short_path(dest)}\n")
        run_streaming(["bash", "-c", script], self.log, clear_first=False, on_done=done)

    def delete_backup(self):
        b = self._selected()
        if not b:
            return
        if not ask_confirm(self, "Backup löschen", f"Backup vom {b['when']:%d.%m.%Y %H:%M} endgültig löschen?",
                           "Löschen", danger=True):
            return
        if b["kind"] == "archive":
            for p in b["parts"] + [b["parts"][0].split(".part")[0] + ".sha256"]:
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.refresh_existing()
            return
        # Snapshots enthalten ggf. root-Dateien → mit sudo, falls angemeldet
        use_sudo = self.app.priv.is_authenticated_nonblocking()
        self.log.set_text(f"$ rm -rf {short_path(b['path'])}\n")
        run_streaming(["rm", "-rf", "--", b["path"]], self.log, needs_sudo=use_sudo, clear_first=False,
                      on_done=lambda rc: (self.log.append_text(f"[Exit-Code {rc}]\n"), self.refresh_existing()))

    # ======================================================================
    # Zeitplan
    # ======================================================================

    def _refresh_schedule(self):
        on, nxt = backup_schedule_state()
        self.sched_lbl.setText(f"Aktiv · nächster Lauf: {nxt}" if on else "Kein Zeitplan aktiv")

    def apply_schedule(self):
        when = self.cb_sched.currentData()
        cfg = self._collect()
        if when != "off":
            if cfg["mode"] == "archive" and cfg["encrypt"]:
                show_warning(self, "Zeitplan", "Verschlüsselte Archive brauchen das Passwort – das wird nicht "
                                               "gespeichert. Für den Zeitplan Snapshots oder unverschlüsselte "
                                               "Archive verwenden.")
                return
            if not cfg["targets"]:
                show_info(self, "Zeitplan", "Bitte zuerst ein Ziel hinzufügen.")
                return
        cfg["schedule"] = when
        self._save()
        ok, txt = backup_schedule_set(when)
        if not ok:
            show_warning(self, "Zeitplan", txt)
        self._refresh_schedule()


SETTINGS_FILE = os.path.join(os.path.expanduser("~/.config"), "tuxdex", "settings.json")
BUILD_DIR = os.path.join(os.path.expanduser("~/.cache"), "tuxdex", "build")


def load_settings():
    s = {"repo": DEFAULT_REPO, "branch": DEFAULT_BRANCH, "auto_check": True, "ask_on_start": True, "local_dir": "",
         "sys_check_on_start": True}
    s.update(_load_json(SETTINGS_FILE, {}))
    return s


def save_settings(s):
    cur = _load_json(SETTINGS_FILE, {})
    cur.update(s)
    for k in ("alpha_accepted", "alpha_version"):      # Zustimmung nie durch einen älteren Stand überschreiben
        if k in _load_json(SETTINGS_FILE, {}):
            cur[k] = _load_json(SETTINGS_FILE, {})[k]
    _save_json(SETTINGS_FILE, cur)


def normalize_repo(text):
    """„https://github.com/a/b(.git)“, „git@github.com:a/b.git“ oder „a/b“ → „a/b“"""
    t = (text or "").strip()
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", t) or re.match(r"^([\w.-]+)/([\w.-]+)$", t)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def version_tuple(v):
    """Vergleichbare Version: 1.6.0 > 1.6.0-beta.2 > 1.6.0-beta.1 > 1.5.4 (auch „1.6.0beta1“ aus dem PKGBUILD)."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(?:[-.]?(alpha|beta|rc)\.?(\d*))?", v or "")
    if not m:
        return (0,)
    nums = [int(x) for x in m.group(1).split(".")][:4]
    nums += [0] * (4 - len(nums))
    stage = {"alpha": -3, "beta": -2, "rc": -1}.get(m.group(2), 0)
    return tuple(nums) + (stage, int(m.group(3) or 0))


def _http_get(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"tuxdex/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_commit(repo, branch):
    """SHA des neuesten Commits auf dem Zweig – ohne Zwischenspeicher.
    1. Git-Protokoll (wie „git ls-remote“, kein Abfragelimit), 2. GitHub-API als Reserve."""
    try:
        refs = _http_get(f"https://github.com/{repo}.git/info/refs?service=git-upload-pack",
                         timeout=15).decode(errors="replace")
        m = re.search(r"([0-9a-f]{40}) refs/heads/" + re.escape(branch) + r"(?:\n|\x00|$)", refs)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        data = json.loads(_http_get(f"https://api.github.com/repos/{repo}/commits/{branch}",
                                    timeout=15).decode())
        sha = data.get("sha", "")
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    except Exception:
        pass
    return None


def remote_info(repo, branch):
    """(version, changelog_markdown, commit) des Repos.
    Liest die Dateien über die Commit-ID statt über den Zweignamen: raw.githubusercontent.com
    speichert Zweig-Adressen bis zu 5 Minuten zwischen, Commit-Adressen ändern sich nie."""
    sha = latest_commit(repo, branch)
    base = f"https://raw.githubusercontent.com/{repo}/{sha or branch}"
    pkgb = _http_get(f"{base}/PKGBUILD").decode(errors="replace")
    m = re.search(r"^pkgver=([\w.]+)", pkgb, re.M)
    if not m:
        raise ValueError("PKGBUILD ohne pkgver gefunden")
    cl = ""
    for name in (["CHANGELOG.en.md"] if LANG == "en" else []) + ["CHANGELOG.md"]:
        try:
            cl = _http_get(f"{base}/{name}").decode(errors="replace")
            break
        except Exception:
            pass
    # pkgver kennt keinen Bindestrich: „1.6.0beta1“ → „1.6.0-beta.1“ (wie APP_VERSION)
    return re.sub(r"(\d)(alpha|beta|rc)(\d*)$", r"\1-\2.\3", m.group(1)), cl, sha


def fetch_package_sources(repo, ref, dest, progress=None):
    """Lädt PKGBUILD und alle darin unter source=() genannten Dateien einzeln vom Commit `ref`.
    progress(i, n, name) wird nach jeder Datei aufgerufen (aus dem Worker-Thread)."""
    base = f"https://raw.githubusercontent.com/{repo}/{ref}"
    pkgb = _http_get(f"{base}/PKGBUILD", timeout=30)
    text = pkgb.decode(errors="replace")
    if not re.search(r"^pkgname=tuxdex\b", text, re.M):
        raise ValueError("Das PKGBUILD im Repository gehört nicht zu tuxdex")
    m = re.search(r"^source=\((.*?)\)", text, re.S | re.M)
    files = [f.strip("'\"") for f in (m.group(1).split() if m else [])]
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "PKGBUILD"), "wb") as fh:
        fh.write(pkgb)
    n = len(files) + 1
    if progress:
        progress(1, n, "PKGBUILD")
    for i, f in enumerate(files, 2):
        if "/" in f or "::" in f or f.startswith(".") or not re.fullmatch(r"[\w.+-]+", f):
            raise ValueError(f"unerwarteter Dateiname im PKGBUILD: {f}")
        with open(os.path.join(dest, f), "wb") as fh:
            fh.write(_http_get(f"{base}/{f}", timeout=60))
        if progress:
            progress(i, n, f)
    return dest


def changes_since(changelog, current):
    """Abschnitte „## x.y.z“ aus dem Changelog, die neuer als die laufende Version sind."""
    out, keep = [], False
    for line in changelog.splitlines():
        h = re.match(r"^##\s+v?(\d[\w.-]*)", line)
        if h:
            keep = version_tuple(h.group(1)) > version_tuple(current)
            if keep:
                out.append(f"<b>Version {h.group(1)}</b>")
            continue
        if line.startswith("#"):          # andere Überschrift (z. B. „Vorabversionen“) beendet den Abschnitt
            keep = False
            continue
        if keep and line.strip().startswith(("-", "*")):
            item = line.strip()[1:].strip().replace("&", "&amp;").replace("<", "&lt;")
            item = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", item)
            item = re.sub(r"`([^`]+)`", r"<code>\1</code>", item)
            out.append("• " + item)
    return "<br>".join(out)


def _safe_extract(tar, dest):
    base = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if not target.startswith(base + os.sep) or m.issym() or m.islnk() or m.isdev():
            raise ValueError(f"unsicherer Eintrag im Archiv: {m.name}")
    tar.extractall(dest)


def find_pkgbuild(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".git", "src", "pkg")]
        if "PKGBUILD" in files and re.search(r"^pkgname=tuxdex\b", _read(os.path.join(d, "PKGBUILD")), re.M):
            return d
    return None


def fresh_build_dir():
    d = os.path.join(BUILD_DIR, datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


class UpdatePanel(QWidget):
    """Einstellungen → Aktualisierung"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = load_settings()
        self.remote_version = None
        self.remote_sha = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        p = Panel("Aktualisierung")
        head = QHBoxLayout()
        head.setSpacing(12)
        self.badge = StatusBadge("off", "Noch nicht geprüft")
        head.addWidget(self.badge)
        head.addWidget(Label(f"Installiert: {vlabel()}  ·  "
                             f"{'als Paket (pacman)' if SYSTEM_INSTALL else 'als Skript'}", "Value"))
        head.addStretch(1)
        self.b_check = Button("Nach Updates suchen", "ghost", self.check)
        self.b_update = Button("Jetzt aktualisieren", "primary", self.update_from_github)
        self.b_update.hide()
        head.addWidget(self.b_check)
        head.addWidget(self.b_update)
        p.body.addLayout(head)
        self.changes = Label("", "Muted", wrap=True)
        self.changes.setTextFormat(Qt.RichText)
        self.changes.hide()
        p.body.addWidget(self.changes)

        # Version: Stabil oder Beta – Umschalter wie in den Tabs
        self.branches = ["main", "beta"]
        cur = self.settings.get("branch", "main")
        if cur not in self.branches:
            self.branches.append(cur)
        labels = {"main": "Stabil", "beta": "Beta"}
        p.body.addWidget(Label("VERSION", "FieldLabel"))
        crow = QHBoxLayout()
        crow.setSpacing(12)
        self.channel = Segmented([labels.get(b, f"Zweig {b}") for b in self.branches], self._channel_changed)
        self.channel.set(self.branches.index(cur))
        crow.addWidget(self.channel)
        self.channel_info = Label("", "Muted", wrap=True)
        self.channel_info.setTextFormat(Qt.RichText)
        crow.addWidget(self.channel_info, 1)
        p.body.addLayout(crow)
        self.remote_versions = {}
        self._show_channel_info()

        # GitHub-Quelle
        src = QHBoxLayout()
        src.setSpacing(8)
        self.repo_edit = LineEdit(self.settings.get("repo", ""), placeholder="benutzer/tuxdex oder GitHub-Link",
                                  mono=True)
        src.addLayout(Field("GitHub-Repository", self.repo_edit), 1)
        sb = QVBoxLayout()
        sb.addStretch(1)
        sb.addWidget(Button("Speichern", "ghost", self.save_source))
        src.addLayout(sb)
        p.body.addLayout(src)
        self.cb_auto = QCheckBox("Beim Start automatisch nach Updates suchen")
        self.cb_auto.setChecked(bool(self.settings.get("auto_check", True)))
        self.cb_auto.toggled.connect(self._auto_changed)
        p.body.addWidget(self.cb_auto)
        self.cb_ask = QCheckBox("Gefundene Updates beim Start in einem Fenster anbieten (sonst nur Hinweis unten rechts)")
        self.cb_ask.setChecked(bool(self.settings.get("ask_on_start", True)))
        self.cb_ask.setEnabled(self.cb_auto.isChecked())
        self.cb_ask.toggled.connect(self._ask_changed)
        p.body.addWidget(self.cb_ask)

        # Lokal
        p.body.addWidget(Label("LOKAL AKTUALISIEREN", "FieldLabel"))
        lr = QHBoxLayout()
        lr.setSpacing(8)
        lr.addWidget(Button("Aus Datei …", "ghost", self.update_from_file,
                            "tuxdex-X.Y.Z.tar.gz oder (bei Skript-Start) eine tuxdex.py"))
        lr.addWidget(Button("Aus Ordner …", "ghost", self.update_from_folder,
                            "Ordner mit PKGBUILD, z. B. dein geklonter Git-Ordner"))
        self.b_last = Button("", "ghost", lambda: self._update_folder(self.settings.get("local_dir", "")))
        lr.addWidget(self.b_last)
        lr.addStretch(1)
        p.body.addLayout(lr)
        self._update_last_btn()
        p.body.addWidget(Label("Ist der Ordner ein Git-Klon, holt Tuxdex vorher die neueste Version (git pull). "
                               "Gebaut wird mit makepkg in einem Arbeitsordner, installiert mit pacman – "
                               "danach startet Tuxdex neu.", "Hint", wrap=True))
        self.step = Label("", "Hint")
        self.step.hide()
        p.body.addWidget(self.step)
        self.progress = ProgressBar()
        self.progress.hide()
        p.body.addWidget(self.progress)
        self.log = LogView(150)
        self.log.hide()
        p.body.addWidget(self.log)
        lay.addWidget(p)

    # ---- Einstellungen ------------------------------------------------------

    def _update_last_btn(self):
        d = self.settings.get("local_dir", "")
        self.b_last.setVisible(bool(d) and os.path.isdir(d))
        if d:
            home = os.path.expanduser("~")
            self.b_last.setText("Erneut aus " + ("~" + d[len(home):] if d.startswith(home) else d))

    def save_source(self):
        repo = normalize_repo(self.repo_edit.text())
        if self.repo_edit.text().strip() and not repo:
            show_warning(self, "Repository", "Bitte „benutzer/repo“ oder einen GitHub-Link angeben.")
            return
        self.settings["repo"] = repo
        self.repo_edit.setText(repo)
        save_settings(self.settings)
        self.app.set_status("Update-Quelle gespeichert.")
        if repo:
            self.check()

    def _channel_changed(self, idx):
        branch = self.branches[idx]
        old = self.settings.get("branch", "main")
        if branch == old:
            return
        if branch == "beta" and not ask_confirm(
                self, "Beta-Versionen", "Beta-Versionen bekommen neue Funktionen früher, können aber noch Fehler "
                "haben.\n\nZurück zur stabilen Version geht jederzeit hier.", "Beta verwenden"):
            self.channel.set(self.branches.index(old))
            return
        self.settings["branch"] = branch
        save_settings(self.settings)
        self.app.set_status("Beta-Versionen aktiv." if branch == "beta" else "Stabile Version ausgewählt.")
        self._show_channel_info()
        self.check()

    def _show_channel_info(self):
        """Welche Version gibt es in welchem Zweig – und was ist installiert?"""
        rv = self.remote_versions
        inst = "Beta" if "beta" in APP_VERSION or "rc" in APP_VERSION else tr("Stabil")
        parts = []
        for b, name in (("main", "Stabil"), ("beta", "Beta")):
            v = rv.get(b)
            parts.append(f"{name}: <b>{vlabel(v)}</b>" if v else f"{name}: {'…' if b not in rv else '—'}")
        self.channel_info.setText(" · ".join(parts) + f"<br>Installiert: <b>{vlabel()}</b> ({inst})")

    def _auto_changed(self, on):
        self.settings["auto_check"] = on
        self.cb_ask.setEnabled(on)
        save_settings(self.settings)

    def _ask_changed(self, on):
        self.settings["ask_on_start"] = on
        save_settings(self.settings)

    # ---- GitHub ---------------------------------------------------------------

    def check(self, silent=False):
        repo = self.settings.get("repo") or normalize_repo(self.repo_edit.text())
        if not repo:
            if not silent:
                self.badge.set("warn", "Kein Repository eingetragen")
            return
        self.badge.set("info", "Prüfe …")
        self.b_check.setEnabled(False)
        branch = self.settings.get("branch", "main")

        def worker():
            try:
                ver, cl, sha = remote_info(repo, branch)
                self.remote_sha = sha
                res = (ver, cl, None)
            except Exception as e:
                res = (None, "", str(e))
            # auch den jeweils anderen Zweig abfragen, damit beide Versionen angezeigt werden
            vers = {branch: res[0]}
            for b in ("main", "beta"):
                if b not in vers:
                    try:
                        vers[b] = remote_info(repo, b)[0]
                    except Exception:
                        vers[b] = None
            ui(lambda: (self.remote_versions.update(vers), self._show_channel_info(),
                        self._checked(*res, silent=silent)))
        threading.Thread(target=worker, daemon=True).start()

    def _checked(self, ver, cl, err, silent=False):
        self.b_check.setEnabled(True)
        if err:
            self.badge.set("danger", "Prüfen fehlgeschlagen")
            if not silent:
                self.changes.setText(f"GitHub nicht erreichbar oder Repository falsch: {err}")
                self.changes.show()
            return
        self.remote_version = ver
        newer = version_tuple(ver) > version_tuple(APP_VERSION)
        # Von einer Beta zurück zur stabilen Version: ältere Version anbieten, aber nicht beim Start aufdrängen
        back = not newer and self.settings.get("branch", "main") == "main" and \
            version_tuple(ver) < version_tuple(APP_VERSION)
        self.b_update.setText(f"Zur stabilen Version {vlabel(ver)} wechseln" if back else "Jetzt aktualisieren")
        self.b_update.setVisible(newer or back)
        self.app.show_update_hint(ver if newer else None)
        if back:
            self.badge.set("info", f"Beta {vlabel()} installiert")
            self.changes.setText(f"Die aktuelle stabile Version ist {vlabel(ver)}. Du nutzt noch die Beta {vlabel()} – "
                                 "wechseln installiert die stabile Version.")
            self.changes.setVisible(not silent)
        elif newer:
            self.badge.set("warn", f"Version {vlabel(ver)} verfügbar")
            txt = changes_since(cl, APP_VERSION)
            set_text_raw(self.changes, txt) if txt else self.changes.setText(f"Neue Version {vlabel(ver)}.")
            self.changes.show()
            if silent and self.settings.get("ask_on_start", True):
                self._offer_update(ver, txt)
        else:
            self.badge.set("ok", "Aktuell")
            self.changes.setText(f"Auf GitHub ist Version {ver} – du bist auf dem neuesten Stand.")
            self.changes.setVisible(not silent)

    def _offer_update(self, ver, changes_html):
        """Beim Start: neue Version in einem Fenster anbieten."""
        box = QMessageBox(self.window())
        set_msg_icon(box, QMessageBox.Information)
        box.setWindowTitle("Update verfügbar")
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b>Tuxdex {vlabel(ver)} ist verfügbar</b> – installiert ist {vlabel()}.")
        set_text_raw(box, changes_html or "", "setInformativeText")
        later = box.addButton("Später", QMessageBox.RejectRole)
        later.setProperty("variant", "ghost")
        now = box.addButton("Jetzt aktualisieren", QMessageBox.AcceptRole)
        now.setProperty("variant", "primary")
        box.setDefaultButton(now)
        style_msg_box(box)
        box.exec()
        if box.clickedButton() is now:
            self.app.open_settings()
            self.update_from_github(confirmed=True)

    # ---- Fortschritt ----------------------------------------------------------

    def _prog(self, value, text):
        """Fortschritt 0–100 (None = unbestimmt) mit Schritt-Beschreibung; Balken läuft nie zurück."""
        if value is not None:
            value = max(value, self.progress.value or 0)
        self.progress.show()
        self.step.show()
        self.step.setText(text)
        self.progress.set(value, "" if value is None else f"{int(value)} %")

    def _prog_fail(self, text):
        self.step.show()
        self.step.setText("✕  " + text)
        self.progress.set(self.progress.value or 0, "Fehler")

    def _need_admin(self):
        """Ohne gültige sudo-Sitzung startet keine Aktualisierung."""
        if not SYSTEM_INSTALL:
            return True
        if self.app.priv.ensure(self):
            return True
        self.badge.set("warn", "Abgebrochen – Admin-Rechte nötig")
        show_warning(self, "Admin-Rechte nötig",
                     "Zum Aktualisieren wird das Paket mit pacman installiert – dafür ist das sudo-Passwort nötig. "
                     "Ohne Anmeldung wird nichts heruntergeladen oder verändert.")
        return False

    def _start_progress(self):
        self.progress.value = 0
        self._prog(0, "Vorbereiten …")

    def update_from_github(self, confirmed=False):
        repo, branch = self.settings.get("repo"), self.settings.get("branch", "main")
        if not repo:
            return
        if not confirmed and not ask_confirm(self, "Aktualisieren",
                                             f"Tuxdex auf Version {self.remote_version} aktualisieren?\n\n"
                                             f"Quelle: github.com/{repo} ({branch})", "Aktualisieren"):
            return
        if not self._need_admin():
            return
        self.log.show()
        self.log.set_text(f"Lade github.com/{repo} ({branch}) …\n")
        self.badge.set("info", "Lade herunter …")
        self._start_progress()
        self._prog(2, "Suche neuesten Stand …")

        def step(i, n, name):
            ui(lambda: self._prog(5 + 20 * i / n, f"Lade Dateien {i}/{n}: {name}"))

        def worker():
            try:
                if SYSTEM_INSTALL:
                    ref = latest_commit(repo, branch) or branch
                    ui(lambda r=ref: (self.log.append_text(f"Commit {r[:7]} – lade Dateien …\n"),
                                      self._prog(5, f"Commit {r[:7]} – lade Dateien …")))
                    src = fetch_package_sources(repo, ref, os.path.join(fresh_build_dir(), "tuxdex"), step)
                    ui(lambda: self._build(src))
                else:
                    ref = latest_commit(repo, branch) or branch
                    code = _http_get(f"https://raw.githubusercontent.com/{repo}/{ref}/tuxdex.py", timeout=60)
                    ui(lambda: self._replace_script(code))
            except Exception as e:
                ui(lambda m=str(e): (self.log.append_text(f"error: {m}\n"),
                                     self.badge.set("danger", "Aktualisierung fehlgeschlagen"),
                                     self._prog_fail("Download fehlgeschlagen")))
        threading.Thread(target=worker, daemon=True).start()

    # ---- Lokal ----------------------------------------------------------------

    def update_from_file(self):
        from PySide6.QtWidgets import QFileDialog
        f, _ = QFileDialog.getOpenFileName(self, "Tuxdex-Update wählen", os.path.expanduser("~"),
                                           "Tuxdex (*.tar.gz *.tgz *.py);;Alle Dateien (*)")
        if not f:
            return
        if not f.endswith(".py") and not self._need_admin():
            return
        if not f.endswith(".py"):
            self._start_progress()
            self._prog(10, "Entpacke Archiv …")
        self.log.show()
        if f.endswith(".py"):
            if SYSTEM_INSTALL:
                show_info(self, "Paket-Installation", "Tuxdex ist als Paket installiert – bitte das Archiv "
                          "(tuxdex-X.Y.Z.tar.gz) oder den Ordner mit PKGBUILD wählen.")
                return
            with open(f, "rb") as fh:
                self._replace_script(fh.read())
            return
        try:
            import tarfile
            d = fresh_build_dir()
            with tarfile.open(f, "r:*") as t:
                _safe_extract(t, d)
            src = find_pkgbuild(d)
            if not src:
                raise ValueError("Im Archiv ist kein PKGBUILD für tuxdex.")
        except Exception as e:
            show_error(self, "Archiv", str(e))
            self._prog_fail("Archiv ungültig")
            return
        if SYSTEM_INSTALL:
            self._confirm_build(src, os.path.basename(f))
        else:
            self._script_from_dir(src)

    def update_from_folder(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Ordner mit Tuxdex (PKGBUILD) wählen",
                                             self.settings.get("local_dir") or os.path.expanduser("~"))
        if d:
            self._update_folder(d)

    def _update_folder(self, d):
        if not d or not os.path.isdir(d):
            return
        if not find_pkgbuild(d):
            show_error(self, "Ordner", "In diesem Ordner liegt kein PKGBUILD für tuxdex.")
            return
        if not self._need_admin():
            return
        self._start_progress()
        self._prog(5, "Hole neueste Version (git pull) …")
        self.settings["local_dir"] = d
        save_settings(self.settings)
        self._update_last_btn()
        self.log.show()
        if os.path.isdir(os.path.join(d, ".git")) and which("git"):
            self.log.set_text(f"$ git -C {d} pull --ff-only\n")
            run_streaming(["git", "-C", d, "pull", "--ff-only"], self.log, clear_first=False,
                          on_done=lambda rc: self._copy_and_build(d))
        else:
            self.log.set_text("")
            self._copy_and_build(d)

    def _copy_and_build(self, d):
        src = find_pkgbuild(d)
        dst = fresh_build_dir()
        shutil.copytree(src, os.path.join(dst, "tuxdex"), ignore=shutil.ignore_patterns(".git", "src", "pkg", "*.pkg.tar*"))
        if SYSTEM_INSTALL:
            self._confirm_build(os.path.join(dst, "tuxdex"), d)
        else:
            self._script_from_dir(os.path.join(dst, "tuxdex"))

    def _script_from_dir(self, d):
        p = os.path.join(d, "tuxdex.py")
        if not os.path.exists(p):
            show_error(self, "Update", "Im Paket fehlt tuxdex.py.")
            return
        with open(p, "rb") as fh:
            self._replace_script(fh.read())

    def _confirm_build(self, src, origin):
        m = re.search(r"^pkgver=([\w.]+)", _read(os.path.join(src, "PKGBUILD")), re.M)
        ver = m.group(1) if m else "?"
        if version_tuple(ver) <= version_tuple(APP_VERSION):
            if not ask_confirm(self, "Gleiche oder ältere Version", f"{origin} enthält Version {vlabel(ver)} – installiert ist "
                               f"{vlabel()}. Trotzdem neu installieren?", "Installieren"):
                self._prog_fail("Abgebrochen")
                return
        elif not ask_confirm(self, "Aktualisieren", f"Tuxdex {ver} aus {origin} installieren?", "Aktualisieren"):
            self._prog_fail("Abgebrochen")
            return
        self._build(src)

    # ---- Bauen, installieren, neu starten ------------------------------------

    # makepkg-/pacman-Meldungen → Fortschritt (Prozent, Beschreibung)
    MAKEPKG_STEPS = [
        ("Making package", 30, "Baue Paket …"),
        ("Checking runtime dependencies", 32, "Prüfe Abhängigkeiten …"),
        ("Installing missing dependencies", 35, "Installiere fehlende Abhängigkeiten …"),
        ("Retrieving sources", 45, "Quellen vorbereiten …"),
        ("Validating source", 50, "Prüfe Prüfsummen …"),
        ("Extracting sources", 55, "Entpacke Quellen …"),
        ("Starting package()", 62, "Stelle Paketinhalt zusammen …"),
        ("Tidying install", 68, "Räume auf …"),
        ("Creating package", 72, "Erzeuge Paketdatei …"),
        ("Compressing package", 76, "Komprimiere Paket …"),
        ("Finished making", 80, "Paket gebaut"),
    ]
    PACMAN_STEPS = [
        ("loading packages", 84, "Lade Paket …"),
        ("checking keys", 86, "Prüfe Paket …"),
        ("checking package integrity", 87, "Prüfe Paket …"),
        ("checking for file conflicts", 89, "Prüfe Dateikonflikte …"),
        ("upgrading tuxdex", 92, "Installiere neue Version …"),
        ("installing tuxdex", 92, "Installiere neue Version …"),
        ("Running post-transaction hooks", 96, "Abschluss-Hooks …"),
    ]

    def _progress_from(self, table, line):
        for key, pct, text in table:
            if key.lower() in line.lower():
                if pct >= (self.progress.value or 0):
                    self._prog(pct, text)
                return

    def _build(self, src):
        if not which("makepkg"):
            show_error(self, "makepkg fehlt", "makepkg (Paket pacman, Gruppe base-devel) wird benötigt.")
            self._prog_fail("makepkg fehlt")
            return
        if not self._need_admin():
            self._prog_fail("Keine Admin-Rechte")
            return
        self.log.show()
        self.badge.set("info", "Baue Paket …")
        self._prog(28, "Baue Paket …")
        self.log.append_text(f"\n$ makepkg -f -s   (in {short_path(src)})\n")

        def built(rc):
            pkgs = sorted(f for f in os.listdir(src) if f.startswith("tuxdex-") and ".pkg.tar" in f)
            if rc != 0 or not pkgs:
                self.badge.set("danger", "Bauen fehlgeschlagen")
                self._prog_fail("Bauen fehlgeschlagen")
                self.log.append_text(f"error: Kein Paket gebaut (Exit-Code {rc}) – Meldungen oben prüfen.\n")
                return
            pkg = os.path.join(src, pkgs[-1])
            self._prog(82, "Installiere mit pacman …")
            self.badge.set("info", "Installiere …")
            self.log.append_text(f"\n$ sudo pacman -U {pkgs[-1]}\n")
            run_streaming(["pacman", "-U", "--noconfirm", pkg], self.log, needs_sudo=True, clear_first=False,
                          interactive=True, on_done=self._installed,
                          on_line=lambda l: self._progress_from(self.PACMAN_STEPS, l))
        run_streaming(["makepkg", "-f", "-s", "--noconfirm"], self.log, clear_first=False, interactive=True,
                      cwd=src, on_done=built, on_line=lambda l: self._progress_from(self.MAKEPKG_STEPS, l))

    def _installed(self, rc):
        self.log.append_text(f"[Exit-Code {rc}]\n")
        if rc != 0:
            self.badge.set("danger", "Installation fehlgeschlagen")
            self._prog_fail("Installation fehlgeschlagen")
            return
        self.badge.set("ok", "Installiert")
        self._prog(100, "Fertig – neue Version installiert")
        if ask_confirm(self, "Aktualisiert", "Die neue Version ist installiert. Tuxdex jetzt neu starten?",
                       "Neu starten"):
            self.app.restart()

    def _replace_script(self, code):
        try:
            compile(code, "tuxdex.py", "exec")
        except SyntaxError as e:
            show_error(self, "Update", f"Die neue Datei ist fehlerhaft und wird nicht übernommen: {e}")
            return
        m = re.search(rb'^APP_VERSION = "([\w.-]+)"', code, re.M)
        ver = m.group(1).decode() if m else "?"
        path = os.path.abspath(__file__)
        if not ask_confirm(self, "Aktualisieren", f"Skript auf Version {ver} ersetzen?\n\n{path}\n"
                           "(Die alte Datei bleibt als .bak erhalten.)", "Ersetzen"):
            return
        try:
            shutil.copy2(path, path + ".bak")
            tmp = path + ".new"
            with open(tmp, "wb") as fh:
                fh.write(code)
            os.chmod(tmp, os.stat(path).st_mode)
            os.replace(tmp, path)
        except Exception as e:
            show_error(self, "Update", f"Datei konnte nicht ersetzt werden: {e}")
            return
        self.log.append_text(f"Skript auf {ver} aktualisiert (Sicherung: {path}.bak)\n")
        self.badge.set("ok", "Aktualisiert")
        if ask_confirm(self, "Aktualisiert", "Tuxdex jetzt neu starten?", "Neu starten"):
            self.app.restart()


class SettingsPage(Page):
    def _lang_changed(self, _=0):
        code = self.cb_lang.currentData()
        st = load_settings()
        st["lang"] = code
        save_settings(st)
        self.update_panel.settings["lang"] = code
        if code != LANG and ask_confirm(self, "Sprache", "Tuxdex jetzt neu starten, damit die Sprache wechselt?",
                                        "Neu starten"):
            self.app.restart()

    def _modules_panel(self):
        p = self.mod_panel = Panel("Module")
        p.body.addWidget(Label("Stell dir Tuxdex so zusammen, wie du es brauchst: Nicht jeder braucht jedes Werkzeug. "
                               "Abgewählte Module verschwinden aus der Leiste und werden nicht geladen – "
                               "du kannst sie hier jederzeit wieder hinzufügen.", "Hint", wrap=True))
        st = load_settings()
        self.mod_buttons = {}
        for key, label, color in MODULES:
            row = QHBoxLayout()
            row.setSpacing(12)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:2px;")
            row.addWidget(dot, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            name = Label(label)
            name.setStyleSheet("font-weight:600;")
            col.addWidget(name)
            col.addWidget(Label(MODULE_INFO.get(key, ""), "Hint", wrap=True))
            row.addLayout(col, 1)
            if key in MODULES_CORE:
                row.addWidget(Label("Immer dabei", "Hint"), 0, Qt.AlignVCenter)
            else:
                b = Button("", "ghost", lambda _=False, k=key: self._toggle_module(k))
                b.setMinimumWidth(130)
                self.mod_buttons[key] = b
                self._paint_module_button(key, module_enabled(key, st))
                row.addWidget(b, 0, Qt.AlignVCenter)
            p.body.addLayout(row)
        return p

    def _paint_module_button(self, key, on):
        b = self.mod_buttons[key]
        b.setText("Entfernen" if on else "Hinzufügen")
        b.setProperty("variant", "ghost" if on else "primary")
        repolish(b)

    def _toggle_module(self, key):
        on = not module_enabled(key)
        self.app.set_module(key, on)
        self._paint_module_button(key, on)
        name = dict((m[0], m[1]) for m in MODULES)[key]
        self.app.set_status(f"Modul „{name}“ hinzugefügt." if on else f"Modul „{name}“ ausgeblendet.")

    def _sys_changed(self, on):
        st = load_settings()
        st["sys_check_on_start"] = on
        save_settings(st)
        self.update_panel.settings["sys_check_on_start"] = on   # gemeinsame Datei nicht mit altem Stand überschreiben
        self.app.set_status("Gespeichert – gilt ab dem nächsten Start.")

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.lay.addLayout(page_header("Einstellungen", Button("← Zurück", "ghost", app.back_from_settings)))
        sysp = Panel("System-Updates")
        self.cb_sys = QCheckBox("Beim Start automatisch nach System-Updates suchen (pacman, AUR, Flatpak)")
        self.cb_sys.setChecked(bool(load_settings().get("sys_check_on_start", True)))
        self.cb_sys.toggled.connect(self._sys_changed)
        sysp.body.addWidget(self.cb_sys)
        sysp.body.addWidget(Label("Gefundene Updates erscheinen als Zahl am Tab „Updates“ und unten rechts – "
                                  "installiert wird erst, wenn du im Tab „Updates“ auf „Update starten“ klickst.",
                                  "Hint", wrap=True))
        self.lay.addWidget(sysp)
        langp = Panel("Sprache · Language")
        lrow = QHBoxLayout()
        self.cb_lang = QComboBox()
        self.cb_lang.setMinimumHeight(38)
        self.cb_lang.setMinimumWidth(220)
        for text, code in (("Deutsch", "de"), ("English", "en")):
            self.cb_lang.addItem(text, code)
        self.cb_lang.setCurrentIndex(0 if LANG == "de" else 1)
        self.cb_lang.currentIndexChanged.connect(self._lang_changed)
        lrow.addWidget(self.cb_lang)
        lrow.addStretch(1)
        langp.body.addLayout(lrow)
        langp.body.addWidget(Label("Wirkt nach einem Neustart von Tuxdex.", "Hint", wrap=True))
        self.lay.addWidget(langp)
        self.lay.addWidget(self._modules_panel())
        self.update_panel = UpdatePanel(app)
        self.lay.addWidget(self.update_panel)

        # --- Über ---
        about = Panel("Über das Projekt")
        top = QHBoxLayout()
        top.setSpacing(20)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(96, self.devicePixelRatioF() or 1.0))
        logo.setFixedSize(96, 96)
        top.addWidget(logo, 0, Qt.AlignTop)
        txt = QVBoxLayout()
        txt.setSpacing(4)
        txt.addWidget(Label("Tuxdex", "PageTitle"))
        txt.addWidget(Label(f"Version {vlabel()}  ·  {'als Paket installiert' if SYSTEM_INSTALL else 'als Skript gestartet'}",
                            "Value"))
        txt.addWidget(Label("Grafische Systemverwaltung für Arch Linux – alles in einem Fenster, ohne Terminal. "
                            "Befehle laufen sichtbar in der Ausgabe, root-Rechte werden nur bei Bedarf und "
                            "einmal pro Sitzung abgefragt.", "Muted", wrap=True))
        txt.addStretch(1)
        top.addLayout(txt, 1)
        about.body.addLayout(top)
        mods = QGridLayout()
        mods.setHorizontalSpacing(24)
        mods.setVerticalSpacing(6)
        desc = {
            "update": "Updates prüfen und einspielen (pacman, AUR, Flatpak), Major-Updates erkennen",
            "software": "Pakete mit Icons, Größe, Version und Datum – per Kästchen auswählen und entfernen",
            "flatpak": "Flatpak-Apps und ihre Rechte (Dateien, Geräte, Netzwerk …) wie mit Flatseal",
            "disks": "Laufwerke einhängen, umbenennen, prüfen, formatieren, sicher entfernen",
            "storage": "Belegung je Festplatte, größte Ordner, Aufräumen",
            "backup": "Snapshots, Spiegel und komprimierte Archive – auf mehrere Ziele gleichzeitig",
            "swap": "Fallback-Speicher (Swapfile) und Swappiness",
            "tasks": "Prozesse, Leistung, Hardware- und Netzwerkinfos",
            "antivirus": "ClamAV: Signaturen, Scans, Quarantäne",
            "security": "Sicherheits-Check, offene Ports, Mullvad VPN, Firewall",
            "users": "Benutzerkonten und letzte Anmeldung",
        }
        for i, (key, name, color) in enumerate(MODULES):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {color}; border-radius: 2px;")
            row.addWidget(dot)
            row.addWidget(Label(name, "PanelTitle"))
            row.addWidget(Label("– " + desc.get(key, ""), "Hint", wrap=True), 1)
            mods.addLayout(row, i // 2, i % 2)
        about.body.addWidget(Label("MODULE", "FieldLabel"))
        about.body.addLayout(mods)
        self.lay.addWidget(about)

        # --- Autor ---
        au = Panel("Autor")
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(12)
        g.addLayout(Field("Entwickelt von", Label(APP_AUTHOR, "Value")), 0, 0)
        g.addLayout(Field("Unternehmen", Label(APP_COMPANY, "Value")), 0, 2)
        gh = Label(f'<a style="color:{COLORS["accent"]}" href="https://github.com/{DEFAULT_REPO}">'
                   f'github.com/{DEFAULT_REPO}</a>', "Value")
        gh.setTextFormat(Qt.RichText)
        gh.setOpenExternalLinks(True)
        g.addLayout(Field("Projektseite", gh), 1, 2)
        g.addLayout(Field("Entstanden mit", Label("KI-gestützt entwickelt (AI made) – in Zusammenarbeit mit Claude "
                                                    "von Anthropic", "Value", wrap=True)), 2, 0, 1, 3)
        mail = Label(f'<a style="color:{COLORS["accent"]}" href="mailto:{APP_AUTHOR_MAIL}">{APP_AUTHOR_MAIL}</a>',
                     "Value")
        mail.setTextFormat(Qt.RichText)
        mail.setOpenExternalLinks(True)
        g.addLayout(Field("Kontakt", mail), 0, 1)
        g.addLayout(Field("Lizenz", Label("MIT – frei nutzbar, veränderbar und weitergebbar", "Value", wrap=True)), 1, 0)
        g.addLayout(Field("Erstellt", Label("2026", "Value")), 1, 1)
        au.body.addLayout(g)
        self.lay.addWidget(au)

        # --- Technik ---
        from PySide6 import __version__ as pyside_ver
        from PySide6.QtCore import qVersion
        te = Panel("Technik")
        g2 = QGridLayout()
        g2.setHorizontalSpacing(24)
        g2.setVerticalSpacing(12)
        paths = [
            ("Python", sys.version.split()[0]),
            ("Qt / PySide6", f"{qVersion()} / {pyside_ver}"),
            ("Schriften", f"{FONTS['sans']} · {FONTS['mono']}"),
            ("Programmdatei", short_path(os.path.abspath(__file__))),
            ("Zwischenspeicher", short_path(CACHE_DIR)),
            ("Quarantäne", short_path(QUARANTINE_DIR)),
            ("Fehlerprotokoll", "~/tuxdex_error.log"),
        ]
        for i, (k, v) in enumerate(paths):
            lab = Label(v, "Value", wrap=True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g2.addLayout(Field(k, lab), i // 2, i % 2)
        te.body.addLayout(g2)
        self.lay.addWidget(te)
        self.lay.addStretch(1)


# --------------------------------------------------------------------------
# Hauptfenster
# --------------------------------------------------------------------------

class TabButton(QFrame):
    """Tab im Stil des Design-Systems: Farbpunkt + Text, aktiv mit 3px-Strich in Modulfarbe."""

    def __init__(self, text, color, on_click):
        super().__init__()
        self.setObjectName("Tab")
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        self.color = color
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(11, 12, 11, 9)
        row.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {color}; border-radius: 2px;")
        row.addWidget(dot)
        self.text = Label(text, "TabText")
        row.addWidget(self.text)
        lay.addLayout(row)
        self.bar = QFrame()
        self.bar.setFixedHeight(3)
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(12, 0, 12, 0)
        bar_row.addWidget(self.bar)
        lay.addLayout(bar_row)
        self.set_selected(False)

    def set_selected(self, sel):
        self.setProperty("selected", "true" if sel else "false")
        self.bar.setStyleSheet(
            f"background: {self.color if sel else 'transparent'}; border-top-left-radius: 2px; "
            "border-top-right-radius: 2px;")
        repolish(self)
        repolish(self.text)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("Tuxdex")
        self.resize(1240, 900)
        self.setMinimumSize(1140, 640)

        self.priv = PrivilegeManager(self, on_change=self._on_auth_change)
        self._authed = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Kopfleiste
        top = QFrame()
        top.setObjectName("Topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(24, 16, 24, 8)
        tl.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(34, self.devicePixelRatioF() or 1.0))
        logo.setFixedSize(34, 34)
        tl.addWidget(logo)
        tl.addSpacing(4)
        tl.addWidget(Label("Tuxdex", "AppTitle"))
        self.alpha_badge = StatusBadge("warn", "ALPHA")
        self.alpha_badge.setToolTip("Alpha-Version: Aktionen mit root-Rechten auf eigenes Risiko. "
                                    "Vor der ersten root-Aktion fragt Tuxdex einmal nach deiner Zustimmung.")
        tl.addWidget(self.alpha_badge)
        tl.addStretch(1)
        self.auth_badge = StatusBadge("off", "Nicht angemeldet")
        tl.addWidget(self.auth_badge)
        self.auth_btn = Button("Anmelden", "primary", self._toggle_auth)
        tl.addWidget(self.auth_btn)
        root.addWidget(top)

        # Tab-Leiste
        tabbar = QFrame()
        tabbar.setObjectName("TabBar")
        tb = QHBoxLayout(tabbar)
        tb.setContentsMargins(16, 4, 16, 0)
        tb.setSpacing(2)
        root.addWidget(tabbar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Statusleiste
        sb = QFrame()
        sb.setObjectName("Statusbar")
        sl = QHBoxLayout(sb)
        sl.setContentsMargins(14, 4, 24, 4)
        self.gear = QPushButton()
        self.gear.setObjectName("Gear")
        self.gear.setIcon(svg_icon(GEAR_SVG.replace("{c}", COLORS["muted"])))
        self.gear.setIconSize(QSize(18, 18))
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.setFocusPolicy(Qt.TabFocus)
        self.gear.setToolTip("Einstellungen")
        self.gear.setCheckable(True)
        self.gear.clicked.connect(self.open_settings)
        sl.addWidget(self.gear)
        sl.addSpacing(8)
        self.status = Label("Bereit.", "StatusText")
        sl.addWidget(self.status)
        sl.addStretch(1)
        self.sys_hint = Button("", "ghost", lambda: self.select([m[0] for m in MODULES].index("update")))
        self.sys_hint.setObjectName("SysHint")
        self.sys_hint.hide()
        sl.addWidget(self.sys_hint)
        sl.addSpacing(8)
        self._sys_updates = (0, 0)
        self.upd_hint = Button("", "ghost", self.open_settings)
        self.upd_hint.setObjectName("UpdHint")
        self.upd_hint.hide()
        sl.addWidget(self.upd_hint)
        sl.addSpacing(12)
        sl.addWidget(Label(f"Version {vlabel()}", "StatusText"))
        root.addWidget(sb)

        # Tabs werden erst beim ersten Öffnen gebaut (spart RAM und Startzeit);
        # bis dahin steht ein leerer Platzhalter im Stack, damit die Indizes stimmen.
        self.page_classes = {
            "update": UpdaterTab, "software": SoftwareTab, "flatpak": FlatpakTab, "swap": SwapTab, "restore": RestoreTab, "setup": SetupTab,
            "disks": DisksTab, "storage": StorageTab, "backup": BackupTab, "tasks": TaskTab, "antivirus": AntivirusTab,
            "security": SecurityTab, "users": UsersTab,
        }
        self.tabs = []
        self.pages = {}
        self._used = {}
        self._last_tab_key = None
        for i, (key, label, color) in enumerate(MODULES):
            self.stack.addWidget(QWidget())
            t = TabButton(label, color, lambda i=i: self.select(i))
            t.setVisible(module_enabled(key))
            tb.addWidget(t)
            self.tabs.append(t)
        tb.addStretch(1)
        self.page("update")          # prüft beim Start auf System-Updates
        self.show_sys_updates(*self._sys_updates)
        self.settings_page = SettingsPage(self)
        self.stack.addWidget(self.settings_page)
        if self.settings_page.update_panel.settings.get("auto_check") and \
                self.settings_page.update_panel.settings.get("repo"):
            QTimer.singleShot(2500, lambda: self.settings_page.update_panel.check(silent=True))
        self._last_tab = 0
        self.select(0)

        # sudo-Status beim Start und danach alle 30 s prüfen (sudo-Sitzungen laufen ab)
        # Laufwerke & Einhängepunkte beobachten: USB-Stick einstecken → Listen aktualisieren
        self._dev_state = self._device_state()
        self._dev_timer = QTimer(self)
        self._dev_timer.timeout.connect(self._check_devices)
        self._dev_timer.start(2000)
        self._trim_timer = QTimer(self)
        self._trim_timer.timeout.connect(self._housekeeping)
        self._trim_timer.start(60000)
        QTimer.singleShot(4000, self._check_orphans)

        if kernel_modules_missing():
            self.set_status("Kernel wurde aktualisiert – bitte neu starten, damit z. B. USB-Sticks erkannt werden.")

        self._poll_auth()
        self._auth_timer = QTimer(self)
        self._auth_timer.timeout.connect(self._poll_auth)
        self._auth_timer.start(30000)

    def _poll_auth(self):
        def worker():
            ok = self.priv.is_authenticated_nonblocking()
            ui(lambda: self._on_auth_change(ok))
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_auth(self):
        if self._authed:
            self._logout()
        elif self.priv.ensure(self):
            self.set_status("Angemeldet – sudo-Sitzung aktiv.")

    @staticmethod
    def _device_state():
        try:
            blocks = sorted(os.listdir("/sys/block"))
        except Exception:
            blocks = []
        sizes = []
        for b in blocks:
            try:
                with open(f"/sys/block/{b}/size") as f:
                    sizes.append(f.read().strip())
            except Exception:
                sizes.append("")
        try:
            with open("/proc/self/mounts") as f:
                mounts = f.read()
        except Exception:
            mounts = ""
        return tuple(blocks), tuple(sizes), mounts

    def page(self, key):
        """Tab holen – beim ersten Aufruf bauen und den Platzhalter ersetzen."""
        if key not in self.pages:
            idx = [m[0] for m in MODULES].index(key)
            w = self.page_classes[key](self)
            old = self.stack.widget(idx)
            cur = self.stack.currentIndex()
            self.stack.insertWidget(idx, w)
            self.stack.removeWidget(old)
            old.deleteLater()
            self.stack.setCurrentIndex(cur)
            self.pages[key] = w
            QTimer.singleShot(3000, trim_memory)
        return self.pages[key]

    UNLOAD_AFTER = 300          # Sekunden unbenutzt, bevor ein Tab wieder abgebaut wird
    KEEP_LOADED = {"update"}    # liefert die Update-Anzeige unten rechts

    def _page_busy(self, w):
        if any(r.log is not None and w.isAncestorOf(r.log) for r in list(_ACTIVE_RUNS)):
            return True
        if any(getattr(w, a, False) for a in ("busy", "loading", "cl_running", "checking")):
            return True
        if getattr(w, "job", None) is not None or getattr(w, "run", None) is not None:
            return True
        sc = getattr(w, "sc", None)
        return bool(sc) and not sc.get("done", True)

    def _housekeeping(self):
        """Jede Minute: Tabs abbauen, die lange nicht benutzt wurden und nichts tun – dann Speicher freigeben."""
        now = time.time()
        cur = MODULES[self.stack.currentIndex()][0] if self.stack.currentIndex() < len(MODULES) else None
        for key in list(self.pages):
            if key == cur or key in self.KEEP_LOADED:
                continue
            if now - self._used.get(key, now) < self.UNLOAD_AFTER or self._page_busy(self.pages[key]):
                continue
            w = self.pages.pop(key)
            idx = [m[0] for m in MODULES].index(key)
            ph = QWidget()
            cur_idx = self.stack.currentIndex()
            self.stack.insertWidget(idx, ph)
            self.stack.removeWidget(w)
            self.stack.setCurrentIndex(cur_idx)
            w.deleteLater()
        QTimer.singleShot(500, trim_memory)

    def _check_devices(self):
        if "disks" in self.pages:
            self.pages["disks"]._check_usb()
        state = self._device_state()
        if state == self._dev_state:
            return
        old_blocks = set(self._dev_state[0])
        self._dev_state = state
        added = [b for b in state[0] if b not in old_blocks and not b.startswith(("loop", "zram", "ram"))]
        if added:
            self.set_status("Neues Laufwerk erkannt: " + ", ".join("/dev/" + b for b in added))
        for key, fn in (("disks", "refresh"), ("storage", "refresh_fs")):
            if key in self.pages:
                getattr(self.pages[key], fn)()

    def show_sys_updates(self, n, important):
        """Anzahl offener System-Updates: Hinweis unten rechts + Zahl am Tab „Updates“."""
        self._sys_updates = (n, important)
        if n:
            self.sys_hint.setText(f"{'▲' if important else '●'}  {n} System-Update{'s' if n != 1 else ''}"
                                  + (f" · {important} wichtig" if important else ""))
            self.sys_hint.setProperty("tone", "danger" if important else "warn")
            repolish(self.sys_hint)
            self.sys_hint.show()
        else:
            self.sys_hint.hide()
        tabs = getattr(self, "tabs", None)
        if tabs:
            i = [m[0] for m in MODULES].index("update")
            tabs[i].text.setText("Updates" + (f"  {n}" if n else ""))

    def show_update_hint(self, ver):
        if ver:
            self.upd_hint.setText(f"⬆  Version {ver} verfügbar")
            self.upd_hint.show()
        else:
            self.upd_hint.hide()

    def closeEvent(self, e):
        """Beim Schließen laufende Scans/Backups/Befehle nicht als Waisen zurücklassen."""
        busy = running_children()
        if busy:
            names = sorted({os.path.basename(str((p.args if isinstance(p.args, list) else [p.args])
                                                  [2 if str(p.args[0]).endswith("sudo") else 0]))
                            for p in busy})
            if not ask_confirm(self, "Tuxdex beenden", "Es läuft noch: " + ", ".join(names) + ".\n\n"
                               "Beim Schließen wird das abgebrochen.", "Beenden", danger=True):
                e.ignore()
                return
        stop_children()
        e.accept()

    def _check_orphans(self):
        def worker():
            o = orphaned_jobs()
            if o:
                ui(lambda: self._offer_kill(o))
        threading.Thread(target=worker, daemon=True).start()

    def _offer_kill(self, orphans):
        lines = [f"• {n} (PID {pid}) · {fmt_bytes(rss)} RAM · läuft seit {int(age) // 3600}:{int(age) // 60 % 60:02d} h"
                 for pid, n, rss, age in orphans]
        if not ask_confirm(self, "Alter Vorgang läuft noch",
                           "Aus einer früheren Tuxdex-Sitzung laufen noch im Hintergrund:\n\n" + "\n".join(lines)
                           + "\n\nSie belegen Speicher und Rechenzeit. Jetzt beenden?", "Beenden", danger=True):
            return
        pids = [str(o[0]) for o in orphans]
        subprocess.run(["kill", "--"] + pids, capture_output=True)
        time.sleep(0.3)
        left = [p for p in pids if os.path.exists(f"/proc/{p}")]
        if left:
            if not self.priv.ensure(self):
                return
            subprocess.run(["sudo", "-n", "kill", "--"] + left, capture_output=True)
        self.set_status(f"{len(orphans)} alte{'r' if len(orphans) == 1 else ''} Vorgang/Vorgänge beendet.")

    def restart(self):
        """Tuxdex neu starten (nach einem Update)."""
        exe = "/usr/bin/tuxdex" if SYSTEM_INSTALL and os.path.exists("/usr/bin/tuxdex") else None
        args = [exe] if exe else [sys.executable, os.path.abspath(__file__)]
        from PySide6.QtCore import QProcess
        QProcess.startDetached(args[0], args[1:])
        QApplication.quit()

    def open_settings(self):
        if self.stack.currentWidget() is self.settings_page:
            self.back_from_settings()
            return
        self.stack.setCurrentWidget(self.settings_page)
        self.gear.setChecked(True)
        for t in self.tabs:
            t.set_selected(False)

    def back_from_settings(self):
        self.select(self._last_tab)

    def set_module(self, key, on):
        """Modul zu- oder abwählen (Einstellungen → Module)."""
        st = load_settings()
        mods = dict(st.get("modules", {}))
        mods[key] = bool(on)
        st["modules"] = mods
        save_settings(st)
        i = [m[0] for m in MODULES].index(key)
        self.tabs[i].setVisible(bool(on))
        if not on and self._last_tab == i:
            self._last_tab = 0

    def select(self, idx):
        self.tabs[idx].setVisible(True)      # Sprung aus einem anderen Modul in ein abgewähltes: Tab kurz zeigen
        self._last_tab = idx
        self.gear.setChecked(False)
        new = MODULES[idx][0] not in self.pages
        if self._last_tab_key and self._last_tab_key != MODULES[idx][0]:
            self._used[self._last_tab_key] = time.time()      # verlassen → ab jetzt „unbenutzt“
        self._last_tab_key = MODULES[idx][0]
        self.page(MODULES[idx][0])
        self.stack.setCurrentIndex(idx)
        if MODULES[idx][0] == "disks" and not new:
            self.pages["disks"].refresh()
        for i, t in enumerate(self.tabs):
            t.set_selected(i == idx)

    def _on_auth_change(self, ok):
        if ok == self._authed:
            return
        self._authed = ok
        if ok:
            self.auth_badge.set("ok", "Angemeldet – sudo-Sitzung aktiv")
            self.auth_btn.setText("Abmelden")
            self.auth_btn.setProperty("variant", "ghost")
        else:
            self.auth_badge.set("off", "Nicht angemeldet")
            self.auth_btn.setText("Anmelden")
            self.auth_btn.setProperty("variant", "primary")
        repolish(self.auth_btn)

    def _logout(self):
        self.priv.logout()
        self.set_status("Sudo-Sitzung beendet.")

    def set_status(self, text):
        self.status.setText(text)


# --------------------------------------------------------------------------
# Englischer Katalog: deutscher Oberflächentext → englisch. {} = eingesetzter Wert.
# Pflege: python3 tools/i18n_extract.py zeigt fehlende Einträge.
# --------------------------------------------------------------------------

EN = {
    'Wiederherstellung': 'Restore',
    'System-Snapshots vor Updates und Zurücksetzen per Klick (snapper oder Timeshift).': 'System snapshots before updates and one-click rollback (snapper or Timeshift).',
    'Basics wie Schriften und Codecs mit einem Klick, dazu Ersatz für Windows-Programme.': 'Basics like fonts and codecs with one click, plus alternatives for Windows programs.',
    'Snapshot vor dem Update': 'Snapshot before the update',
    'Tuxdex: vor dem Update': 'Tuxdex: before the update',
    'Tuxdex: erster Snapshot': 'Tuxdex: first snapshot',
    'Tuxdex: manuell': 'Tuxdex: manual',
    'Tuxdex: vor dem Zurücksetzen': 'Tuxdex: before rollback',
    'Datum': 'Date',
    'Art': 'Type',
    'Nr.': 'No.',
    'Systemwiederherstellung': 'System restore',
    'So funktioniert es': 'How it works',
    'Ein Snapshot hält den Stand deines Systems fest – in Sekunden und fast ohne Speicherplatz. Geht nach einem Update etwas kaputt, setzt du das System mit einem Klick auf den Stand davor zurück. Deine eigenen Dateien in /home bleiben dabei unberührt.': 'A snapshot captures the state of your system – in seconds and using almost no space. If something breaks after an update, you roll the system back to the state before with one click. Your own files in /home stay untouched.',
    'Werkzeug': 'Tool',
    'Automatisch vor Updates': 'Automatically before updates',
    'Snapshot jetzt erstellen': 'Create snapshot now',
    'Vor Updates': 'Before updates',
    'Vor jedem Update in Tuxdex automatisch einen Snapshot anlegen': 'Automatically create a snapshot before every update in Tuxdex',
    'Auf diesen Stand zurücksetzen': 'Roll back to this state',
    'Anmelden und Snapshots anzeigen': 'Log in and show snapshots',
    'Snapper (nicht eingerichtet)': 'Snapper (not set up)',
    'Timeshift (nicht eingerichtet)': 'Timeshift (not set up)',
    'Ja – snap-pac (bei jeder Paketänderung)': 'Yes – snap-pac (on every package change)',
    'Ja – timeshift-autosnap': 'Yes – timeshift-autosnap',
    'Ja – bei Updates über Tuxdex': 'Yes – for updates via Tuxdex',
    'Das übernimmt bereits snap-pac – auch bei Updates im Terminal. Tuxdex legt deshalb keinen zusätzlichen an.': "snap-pac already takes care of this – also for updates in the terminal. So Tuxdex doesn't create an extra one.",
    'Das übernimmt bereits timeshift-autosnap – auch bei Updates im Terminal. Tuxdex legt deshalb keinen zusätzlichen an.': "timeshift-autosnap already takes care of this – also for updates in the terminal. So Tuxdex doesn't create an extra one.",
    'Tuxdex legt den Snapshot direkt vor „Update starten“ an. Updates im Terminal sind damit nicht abgedeckt – dafür beim Einrichten snap-pac mitinstallieren.': "Tuxdex creates the snapshot right before “Start update”. Updates in the terminal aren't covered – for that, install snap-pac during setup.",
    'Eingerichtet': 'Set up',
    'Nicht eingerichtet': 'Not set up',
    'Dein System liegt auf btrfs – ideal. „Einrichten“ installiert snapper und snap-pac: dann entsteht vor und nach jeder Paketänderung automatisch ein Snapshot.': 'Your system is on btrfs – ideal. “Set up” installs snapper and snap-pac: then a snapshot is created automatically before and after every package change.',
    'Timeshift ist installiert, aber noch nicht eingerichtet. „Einrichten“ öffnet Timeshift – dort einmal den Speicherort wählen.': 'Timeshift is installed but not set up yet. “Set up” opens Timeshift – choose the storage location there once.',
    'Dein System liegt auf {} ohne eigene Snapshots. „Einrichten“ installiert Timeshift; die Snapshots landen dann als Kopie auf der Festplatte (braucht mehr Platz als bei btrfs).': 'Your system is on {} without built-in snapshots. “Set up” installs Timeshift; snapshots are then stored as a copy on the drive (needs more space than btrfs).',
    'Noch keine Snapshots – erst einrichten.': 'No snapshots yet – set up first.',
    'Zum Anzeigen der Snapshots sind root-Rechte nötig.': 'Showing snapshots needs root rights.',
    'Noch keine Snapshots vorhanden.': 'No snapshots yet.',
    'Zurücksetzen ist gesperrt: /home liegt nicht in einem eigenen Subvolume, deine eigenen Dateien würden mit zurückgesetzt.': "Rollback is blocked: /home isn't on its own subvolume, so your own files would be rolled back too.",
    'snapper und snap-pac installieren und für das System einrichten?\n\nDanach entsteht vor und nach jeder Paketänderung automatisch ein Snapshot. Alte Snapshots räumt snapper selbst auf.': 'Install snapper and snap-pac and set them up for the system?\n\nAfterwards a snapshot is created automatically before and after every package change. snapper cleans up old snapshots itself.',
    'Erster Snapshot': 'First snapshot',
    'Timeshift installieren?\n\nDanach öffnet sich Timeshift einmal, um den Speicherort für die Snapshots festzulegen.': 'Install Timeshift?\n\nTimeshift then opens once so you can choose where snapshots are stored.',
    'Wiederherstellung einrichten': 'Set up system restore',
    'Timeshift ist geöffnet – nach dem Einrichten hier auf ↻ klicken.': 'Timeshift is open – click ↻ here after setting it up.',
    'Snapshot erstellen': 'Create snapshot',
    'Snapshot löschen': 'Delete snapshot',
    'Snapshot {} vom {} löschen?': 'Delete snapshot {} from {}?',
    'Snapshot {} löschen': 'Delete snapshot {}',
    'System auf den Stand vom {} zurücksetzen?\n\n„{}“\n\nAlle Systemänderungen seit diesem Snapshot werden rückgängig gemacht – auch installierte Updates und Programme. Deine Dateien in /home bleiben unberührt.\n\nDanach bitte neu starten.': 'Roll the system back to the state of {}?\n\n“{}”\n\nAll system changes since this snapshot are undone – including installed updates and programs. Your files in /home stay untouched.\n\nPlease restart afterwards.',
    'Timeshift startet den Rechner nach dem Zurücksetzen selbst neu.': 'Timeshift restarts the computer by itself after the rollback.',
    'Sicherheits-Snapshot des jetzigen Stands': 'Safety snapshot of the current state',
    'Zurücksetzen auf {}': 'Roll back to {}',
    'Zurückgesetzt – bitte jetzt neu starten.': 'Rolled back – please restart now.',
    'vorher': 'before',
    'nachher': 'after',
    'einzeln': 'single',
    'Schriften für Office-Dokumente': 'Fonts for Office documents',
    'Liberation (passt in der Breite zu Arial, Times New Roman und Courier New), Noto mit Emojis und DejaVu – Word-Dokumente sehen damit aus wie unter Windows.': 'Liberation (same widths as Arial, Times New Roman and Courier New), Noto with emojis and DejaVu – Word documents look just like on Windows.',
    'Audio- und Video-Codecs': 'Audio and video codecs',
    'Damit spielen MP3, MP4, H.264/H.265 und Co. in allen Programmen ab.': 'So MP3, MP4, H.264/H.265 and friends play in every program.',
    'Energieprofile': 'Power profiles',
    'Zwischen Energiesparen, Ausgewogen und Leistung umschalten – wie unter Windows. Nicht zusammen mit TLP nutzen.': "Switch between power saver, balanced and performance – like on Windows. Don't use together with TLP.",
    'Energiesparen': 'Power saver',
    'Aktuelles Profil': 'Current profile',
    'Bildbearbeitung': 'Image editing',
    'Klassische Bildbearbeitung': 'Classic image editing',
    'Malen und Bildbearbeitung': 'Painting and image editing',
    'Photoshop-ähnlich im Browser, öffnet PSD': 'Photoshop-like in the browser, opens PSD',
    'Fotos entwickeln': 'Photo development',
    'RAW-Fotos entwickeln und verwalten': 'Develop and manage RAW photos',
    'RAW-Entwicklung': 'RAW development',
    'Vektorgrafik': 'Vector graphics',
    'Vektorgrafik, öffnet SVG und AI': 'Vector graphics, opens SVG and AI',
    'Einfach malen wie in Paint': 'Simple painting like in Paint',
    'Wie Paint.NET': 'Like Paint.NET',
    'Texte, Tabellen, Präsentationen – öffnet DOCX, XLSX, PPTX': 'Documents, spreadsheets, presentations – opens DOCX, XLSX, PPTX',
    'Sieht aus wie Microsoft Office': 'Looks like Microsoft Office',
    'E-Mail, Kalender, Kontakte': 'Email, calendar, contacts',
    'Notizen': 'Notes',
    'Notizen mit Synchronisierung': 'Notes with sync',
    'Notizen und Wissenssammlung': 'Notes and knowledge base',
    'PDFs lesen und kommentieren': 'Read and annotate PDFs',
    'Videoschnitt': 'Video editing',
    'Einfacher Videoschnitt': 'Simple video editing',
    'Audio bearbeiten': 'Audio editing',
    'Audio aufnehmen und schneiden': 'Record and edit audio',
    'Bildschirm aufnehmen': 'Screen recording',
    'Aufnehmen und streamen': 'Record and stream',
    'Video abspielen': 'Play videos',
    'Spielt praktisch alles ab': 'Plays practically everything',
    'Musiksammlung wie iTunes': 'Music library like iTunes',
    'Musiksammlung verwalten': 'Manage your music library',
    'Gibt es auch für Linux': 'Also available for Linux',
    'Starker Text-Editor': 'Powerful text editor',
    'Programmieren': 'Programming',
    'Open-Source-Version von VS Code': 'Open-source version of VS Code',
    'Die Microsoft-Version': 'The Microsoft version',
    'Entpacken': 'Unzip',
    'Archive packen und entpacken': 'Create and extract archives',
    '7-Zip für die Kommandozeile': '7-Zip for the command line',
    'Dateimanager von KDE': "KDE's file manager",
    'Screenshots und Bildschirmaufnahmen': 'Screenshots and screen recordings',
    'Schneller, privater Browser': 'Fast, private browser',
    'Open-Source-Basis von Chrome': 'Open-source base of Chrome',
    'Inoffizielle Teams-App': 'Unofficial Teams app',
    'Passwörter': 'Passwords',
    'Passwort-Manager': 'Password manager',
    'Spiele': 'Games',
    'Spiele-Plattform mit Proton für Windows-Spiele': 'Gaming platform with Proton for Windows games',
    'Ist in Tuxdex schon eingebaut': 'Already built into Tuxdex',
    'Datenträgerverwaltung': 'Disk Management',
    'Laufwerke': 'Drives',
    'Wiederherstellungspunkt': 'Restore point',
    '3D-Konstruktion': '3D design',
    '2D-Zeichnungen': '2D drawings',
    'Tuxdex-Taskmanager': 'Tuxdex task manager',
    'Tuxdex-Datenträger': 'Tuxdex drives',
    'Tuxdex-Wiederherstellung': 'Tuxdex restore',
    'Was nach einem Umstieg von Windows fehlt – mit einem Klick erledigt.': "What's missing after switching from Windows – done with one click.",
    'Basics mit einem Klick': 'One-click basics',
    'Ersatz für Windows-Programme': 'Alternatives for Windows programs',
    'Gib ein, was du unter Windows benutzt hast – z. B. „Photoshop“ oder „Office“.': 'Type what you used on Windows – e.g. “Photoshop” or “Office”.',
    'Windows-Programm suchen …': 'Search for a Windows program …',
    '{} von {} Basics': '{} of {} basics',
    'Dazu kenne ich noch keinen Ersatz. Im Tab „Software“ kannst du frei nach Programmen suchen.': "I don't know an alternative for that yet. In the “Software” tab you can search freely for programs.",
    'Statt {}': 'Instead of {}',
    '… und {} weitere – einfach suchen.': '… and {} more – just search.',
    'Im Browser öffnen': 'Open in browser',
    'Installieren (Flathub)': 'Install (Flathub)',
    'Flatpak fehlt – im Tab „Flatpak“ einrichten.': 'Flatpak is missing – set it up in the “Flatpak” tab.',
    'Installieren: {}?': 'Install: {}?',
    '{} aus den Arch-Paketquellen installieren?': 'Install {} from the Arch repositories?',
    '{} von Flathub installieren?': 'Install {} from Flathub?',
    'Energieprofil: {}.': 'Power profile: {}.',
    'Energieprofil ließ sich nicht setzen: {}': "Couldn't set the power profile: {}",
    'Fertig.': 'Done.',
    'System-, AUR- und Flatpak-Updates mit Hinweisen vor riskanten Updates.': 'System, AUR and Flatpak updates with warnings before risky updates.',
    'Programme suchen, installieren und entfernen (pacman und AUR).': 'Search, install and remove programs (pacman and AUR).',
    'Flatpak-Apps verwalten und ihre Rechte per Schalter einstellen.': 'Manage Flatpak apps and set their permissions with switches.',
    'USB-Sticks und Festplatten einhängen, formatieren und prüfen.': 'Mount, format and check USB sticks and drives.',
    'Sehen, was Platz belegt, und typische Platzfresser aufräumen.': 'See what takes up space and clean up typical space hogs.',
    'Sicherungen auf externe Laufwerke – mit Zeitplan und Wiederherstellen.': 'Backups to external drives – with schedule and restore.',
    'Auslagerungsspeicher (Swapfile, zram) einrichten. Für Fortgeschrittene.': 'Set up swap (swapfile, zram). For advanced users.',
    'Laufende Programme, Leistung, Autostart und Bootzeit.': 'Running programs, performance, autostart and boot time.',
    'ClamAV-Virenscanner. Auf Linux-Desktops wenig nützlich – vor allem für Server und Dateien, die an Windows-Rechner weitergehen.': 'ClamAV virus scanner. Of little use on Linux desktops – mainly for servers and files passed on to Windows PCs.',
    'Sicherheits-Check, Checkliste, Firewall, offene Ports und VPN.': 'Security check, checklist, firewall, open ports and VPN.',
    'Benutzerkonten und Gruppen verwalten. Für Fortgeschrittene.': 'Manage user accounts and groups. For advanced users.',
    'Module': 'Modules',
    'Stell dir Tuxdex so zusammen, wie du es brauchst: Nicht jeder braucht jedes Werkzeug. Abgewählte Module verschwinden aus der Leiste und werden nicht geladen – du kannst sie hier jederzeit wieder hinzufügen.': "Put Tuxdex together the way you need it: not everyone needs every tool. Removed modules disappear from the bar and aren't loaded – you can add them back here at any time.",
    'Immer dabei': 'Always included',
    'Modul „{}“ hinzugefügt.': 'Module “{}” added.',
    'Modul „{}“ ausgeblendet.': 'Module “{}” hidden.',
    '$ sudo systemctl restart clamav-freshclam\nDer Dienst clamav-freshclam läuft bereits und lädt die Signaturen selbst.\nNeustart löst sofort eine Prüfung aus – der erste Download (~200 MB) kann einige Minuten dauern.': '$ sudo systemctl restart clamav-freshclam\nThe clamav-freshclam service is already running and loads the signatures itself.\nA restart triggers a check immediately – the first download (~200 MB) can take a few minutes.',
    '$ {}{}\n(Nur Funde, Warnungen und die Zusammenfassung werden hier angezeigt.)': '$ {}{}\n(Only findings, warnings and the summary are shown here.)',
    "'{}' ist nicht installiert.\n\nsudo pacman -S {}": "'{}' is not installed.\n\nsudo pacman -S {}",
    '(Dienst-Meldungen nicht lesbar – bitte oben rechts anmelden)': '(service messages not readable – please log in at the top right)',
    '(in 4-GB-Teilen wegen FAT32)': '(in 4 GB parts because of FAT32)',
    '(noch {} Tage)': '({} days left)',
    '(ohne root – evtl. zu wenig)': '(without root – possibly too little)',
    '(Paket {})': '(package {})',
    '(verschlüsselt).': '(encrypted).',
    '({} Prozesse).': '({} processes).',
    ') – warte auf das Ergebnis …': ') – waiting for the result …',
    ', {} wichtig': ', {} important',
    '.\n\nBeim Schließen wird das abgebrochen.': '.\n\nClosing will cancel it.',
    '. Details: systemctl status NAME.': '. Details: systemctl status NAME.',
    '. Einspielen mit: fwupdmgr update': '. Install with: fwupdmgr update',
    '. Empfohlen: none für NVMe, mq-deadline für SSD, bfq für Festplatten.': '. Recommended: none for NVMe, mq-deadline for SSD, bfq for hard disks.',
    '. Jedes Programm unter deinem Benutzer kann diese Befehle als root ausführen – nur behalten, wenn nötig (visudo).': '. Every program running as your user can run these commands as root – only keep them if needed (visudo).',
    '. Manche Seiten zeigen dann Captchas.': '. Some sites will then show captchas.',
    '. Pakete werden dann nicht auf Echtheit geprüft – dort auf „Required DatabaseOptional“ zurückstellen.': '. Packages are then not checked for authenticity – set it back to “Required DatabaseOptional” there.',
    '. Programme schicken ihren Verkehr über diesen Proxy.': '. Programs send their traffic through this proxy.',
    '. Prüfen, ob das dein gewollter DNS-Dienst ist.': '. Check whether this is your intended DNS service.',
    '. Sperrt den Kernel-Austausch im laufenden Betrieb (kexec) und SysRq-Tastenkürzel und verbirgt Kernel-Meldungen (dmesg) und -Adressen vor normalen Programmen – im Alltag ohne Nachteile.': '. Blocks replacing the kernel at runtime (kexec) and SysRq shortcuts and hides kernel messages (dmesg) and addresses from normal programs – no downside in daily use.',
    '. Tuxdex zeigt sie nicht an – bitte selbst prüfen und löschen.': ". Tuxdex doesn't show them – please check and delete them yourself.",
    '/ {} GHz': '/ {} GHz',
    '/tmp im Arbeitsspeicher': '/tmp in RAM',
    '/tmp liegt auf der Platte. Arch nutzt normalerweise tmpfs (tmp.mount) – prüfen, ob /etc/fstab das überschreibt.': '/tmp is on disk. Arch normally uses tmpfs (tmp.mount) – check whether /etc/fstab overrides it.',
    '/tmp liegt im Arbeitsspeicher – schnell und nach Neustart leer.': '/tmp is in RAM – fast and empty after a restart.',
    '0 ms': '0 ms',
    '16-stellige Kontonummer': '16-digit account number',
    '32-Bit-Programme': '32-bit programs',
    '<b>Freigegeben</b> = aus dem Netz erreichbar. <b>Gesperrt</b> = die Firewall blockt Verbindungen von außen; das Programm läuft trotzdem weiter.': '<b>Allowed</b> = reachable from the network. <b>Blocked</b> = the firewall blocks connections from outside; the program keeps running anyway.',
    '<b>Tuxdex {} ist verfügbar</b> – installiert ist {}.': '<b>Tuxdex {} is available</b> – installed is {}.',
    '<b>Version {}</b>': '<b>Version {}</b>',
    '<br>Installiert: <b>{}</b> ({})': '<br>Installed: <b>{}</b> ({})',
    '<span style="color:{}">ClamAV ist nicht installiert. Tuxdex funktioniert auch ohne. Sobald ClamAV auf dem System vorhanden ist, lässt es sich hier bedienen.</span>': '<span style="color:{}">ClamAV is not installed. Tuxdex also works without it. As soon as ClamAV is on the system, it can be used here.</span>',
    '<span style="color:{}">Nach dem Update ist ein Neustart nötig ({}).</span>': '<span style="color:{}">A restart is needed after the update ({}).</span>',
    '<span style="color:{}">Tipp: Automatische Updates halten die Signaturen täglich aktuell (Dienst clamav-freshclam).</span>': '<span style="color:{}">Tip: automatic updates keep the signatures up to date daily (service clamav-freshclam).</span>',
    '<span style="color:{}">▲ Major-Updates: {} – vorher die Arch-News lesen (archlinux.org/news).</span>': '<span style="color:{}">▲ Major updates: {} – read the Arch news first (archlinux.org/news).</span>',
    '<span style="color:{}">▲ Nicht über Mullvad – Webseiten sehen diese Adresse.</span>': '<span style="color:{}">▲ Not via Mullvad – websites see this address.</span>',
    '<span style="color:{}">▲ nicht über Mullvad</span>': '<span style="color:{}">▲ not via Mullvad</span>',
    '<span style="color:{}">▲ Ohne aktive Firewall sind alle Ports unten aus dem Netz erreichbar.</span> Nach dem Aktivieren sind sie gesperrt und du kannst jeden einzeln per Knopf freigeben.': '<span style="color:{}">▲ Without an active firewall, all ports below are reachable from the network.</span> After enabling it they are blocked and you can allow each one with a button.',
    '<span style="color:{}">▲ System-Pakete: {}</span>': '<span style="color:{}">▲ System packages: {}</span>',
    '<span style="color:{}">● Dein Verkehr läuft über Mullvad ({}).</span>': '<span style="color:{}">● Your traffic goes through Mullvad ({}).</span>',
    '<span style="color:{}">● über Mullvad-VPN</span>': '<span style="color:{}">● via Mullvad VPN</span>',
    '<span style="color:{}">✕ Es sind noch keine Virensignaturen geladen –': '<span style="color:{}">✕ No virus signatures loaded yet –',
    '<span style="color:{}">✕ Kernel aktualisiert – bitte neu starten (sonst werden z. B. USB-Sticks nicht erkannt).</span>': '<span style="color:{}">✕ Kernel updated – please restart (otherwise e.g. USB sticks won\'t be detected).</span>',
    '== AUR ==\nparu nicht installiert.': '== AUR ==\nparu not installed.',
    '== Flatpak ==\nflatpak nicht installiert.': '== Flatpak ==\nflatpak not installed.',
    '== Pacman ==\ncheckupdates (Paket pacman-contrib) nicht installiert.': '== Pacman ==\ncheckupdates (package pacman-contrib) not installed.',
    '[Abgebrochen]': '[Cancelled]',
    '[Exit-Code {}]': '[Exit code {}]',
    '[{}] Alte Version entfernt: {}': '[{}] Old version removed: {}',
    '[{}] Prüfung fehlgeschlagen: {}': '[{}] Verification failed: {}',
    'Abbrechen': 'Cancel',
    'Abgebrochen': 'Cancelled',
    'Abgebrochen – Admin-Rechte nötig': 'Cancelled – admin rights needed',
    'Abgebrochen: Der Alpha-Hinweis wurde nicht bestätigt – keine Aktion mit root-Rechten.': 'Cancelled: the alpha notice was not confirmed – no action with root rights.',
    'Abgeschaltet': 'Disabled',
    'abgeschlossen': 'finished',
    'Abgestürzte Programme hinterlassen keine Speicherabbilder.': "Crashed programs don't leave memory dumps.",
    'Abhängigkeiten, die nichts mehr braucht': 'Dependencies nothing needs anymore',
    'Abmelden': 'Log out',
    'Abschalten': 'Turn off',
    'Abschluss-Hooks …': 'Final hooks …',
    'Achtung, Major-Updates:': 'Attention, major updates:',
    'Admin-Rechte nötig': 'Admin rights needed',
    'Administrator-Passwort': 'Administrator password',
    'Adress-Zufall (ASLR)': 'Address randomization (ASLR)',
    'Akku': 'Battery',
    'Akkubetrieb': 'On battery',
    'Aktion': 'Action',
    'Aktionen mit root-Rechten': 'Actions with root rights',
    'Aktiv': 'Active',
    'Aktiv · nächster Lauf: {}': 'Active · next run: {}',
    'Aktiv · {} GiB': 'Active · {} GiB',
    'Aktive Zeit': 'Active time',
    'Aktive Zeit = Anteil der Zeit, in der das Laufwerk beschäftigt war. Werte seit dem Systemstart.': 'Active time = share of time the drive was busy. Values since system start.',
    'Aktivieren': 'Enable',
    'aktiviert': 'enabled',
    'Aktualisieren': 'Refresh',
    'Aktualisieren (mit Login-Zeiten)': 'Refresh (with login times)',
    'Aktualisiert': 'Updated',
    'Aktualisierung': 'Updating',
    'Aktualisierung fehlgeschlagen': 'Update failed',
    'Aktuell': 'Up to date',
    'Aktuelle Swappiness: {}': 'Current swappiness: {}',
    'Aktueller Status': 'Current status',
    'Akzeptieren': 'Accept',
    'alle': 'all',
    'alle Apps': 'all apps',
    'Alle Apps': 'All apps',
    'Alle beenden': 'End all',
    'Alle Dateien': 'All files',
    'Alle Dienste laufen.': 'All services are running.',
    'Alle DNS-Anfragen laufen über das VPN.': 'All DNS requests go through the VPN.',
    'Alle eigenen Rechte-Einstellungen für {} entfernen?\nDanach gelten wieder die Voreinstellungen der App.': "Remove all your own permission settings for {}?\nAfterwards the app's defaults apply again.",
    'Alle erzwingen': 'Force all',
    'Alle Geräte': 'All devices',
    'Alle gespeicherten Freigaben (Kamera, Ort, Bildschirm …) dieser App vergessen': 'Forget all saved permissions (camera, location, screen …) of this app',
    'Alle Pakete (inkl. Abhängigkeiten)': 'All packages (incl. dependencies)',
    'Alle Prozesse': 'All processes',
    'Alle sichtbaren auswählen': 'Select all visible',
    'Alle {} Bereiche gemessen · Dauer {}:{}': 'All {} areas measured · duration {}:{}',
    'allen Apps': 'all apps',
    'Alles gut': 'All good',
    'Alles in Ordnung': 'All good',
    'Alles neu prüfen': 'Check everything again',
    'Alpha-Hinweis nicht bestätigt – keine root-Aktion.': 'Alpha notice not confirmed – no root action.',
    'Alpha-Version – Hinweis': 'Alpha version – notice',
    'Alpha-Version: Aktionen mit root-Rechten auf eigenes Risiko. Vor der ersten root-Aktion fragt Tuxdex einmal nach deiner Zustimmung.': 'Alpha version: actions with root rights at your own risk. Before the first root action, Tuxdex asks once for your consent.',
    'als Abhängigkeit': 'as dependency',
    'als Paket (pacman)': 'as package (pacman)',
    'als Paket installiert': 'installed as package',
    'als Skript': 'as script',
    'als Skript gestartet': 'started as script',
    'Alte Kernel-Module': 'Old kernel modules',
    'Alte Paketversionen aus dem Pacman-Cache löschen?': 'Delete old package versions from the pacman cache?',
    'Alte Versionen löschen': 'Delete old versions',
    'Alter Vorgang läuft noch': 'Old operation still running',
    'am Zielort existiert bereits eine Datei': 'a file already exists at the target',
    'am.i.mullvad.net nicht erreichbar (': 'am.i.mullvad.net not reachable (',
    'am.i.mullvad.net nicht erreichbar ({}).': 'am.i.mullvad.net not reachable ({}).',
    'An': 'On',
    'An Originalort zurückspielen': 'Restore to original location',
    'An Originalort zurückspielen …': 'Restore to original location …',
    'Analyse von {} fertig.': 'Analysis of {} finished.',
    'Analysiere {} … (kann bei großen Platten etwas dauern)': 'Analyzing {} … (may take a while on large drives)',
    'Analysiere …': 'Analyzing …',
    'Analysieren': 'Analyze',
    'Anfragen laufen durch den VPN-Tunnel.': 'Requests go through the VPN tunnel.',
    'Anfragen laufen verschlüsselt (DNS-over-TLS).': 'Requests are encrypted (DNS-over-TLS).',
    'Anfragen sind unverschlüsselt – der Netzbetreiber kann sehen, welche Seiten du aufrufst.': 'Requests are unencrypted – the network operator can see which sites you visit.',
    'angehalten': 'stopped',
    'Angemeldet – sudo-Sitzung aktiv': 'Logged in – sudo session active',
    'Angemeldet – sudo-Sitzung aktiv.': 'Logged in – sudo session active.',
    'Angemeldet.': 'Logged in.',
    'angeschlossen': 'connected',
    'Anklicken für Details': 'Click for details',
    'Anlegen': 'Create',
    'Anmelden': 'Log in',
    'Anpassen': 'Adjust',
    'Antivirus (ClamAV)': 'Antivirus (ClamAV)',
    'Anzeige & Ton': 'Display & sound',
    'Anzeigen': 'Show',
    'App beenden und neu starten – damit geänderte Rechte gelten': 'Quit and restart the app – so changed permissions take effect',
    'App suchen …': 'Search app …',
    'AppArmor ist nicht aktiv. Sinnvoll für Server oder erhöhten Schutzbedarf; braucht einen Kernel-Parameter (lsm=…,apparmor) und das Paket apparmor.': 'AppArmor is not active. Useful for servers or higher protection needs; requires a kernel parameter (lsm=…,apparmor) and the apparmor package.',
    'AppArmor schränkt ein, worauf einzelne Programme zugreifen dürfen.': 'AppArmor restricts what individual programs may access.',
    'Arbeitet – seit {}:{} keine Meldung': 'Working – no message for {}:{}',
    'Arbeitsspeicher': 'Memory',
    'arch-audit konnte die Sicherheitsdatenbank nicht abrufen (keine Internetverbindung?).': "arch-audit couldn't fetch the security database (no internet connection?).",
    'Archiv': 'Archive',
    'Archiv ungültig': 'Invalid archive',
    'Archiv-Passwort': 'Archive password',
    'Auf 200 MB kürzen': 'Shrink to 200 MB',
    'Auf 500 MB / 1 Monat': 'To 500 MB / 1 month',
    'Auf GitHub ist Version {} – du bist auf dem neuesten Stand.': "GitHub has version {} – you're up to date.",
    'Auf Updates prüfen': 'Check for updates',
    'Aufräumen': 'Clean up',
    'AUR (paru nicht installiert)': 'AUR (paru not installed)',
    'AUR / Fremd-Pakete': 'AUR / foreign packages',
    'AUR / lokal': 'AUR / local',
    'AUR-Build-Cache (paru)': 'AUR build cache (paru)',
    'Aus': 'Off',
    'Aus Datei …': 'From file …',
    'Aus einer früheren Tuxdex-Sitzung laufen noch im Hintergrund:': 'Still running in the background from an earlier Tuxdex session:',
    'Aus Ordner …': 'From folder …',
    'aus Umgebungsvariablen': 'from environment variables',
    'Ausführen': 'Run',
    'Ausgabe': 'Output',
    'Ausgewogen': 'Balanced',
    'Ausgewählte entfernen': 'Remove selected',
    'Ausgewählte löschen': 'Delete selected',
    'Aushängen': 'Unmount',
    'Auslastung': 'Utilization',
    'Auswahl deinstallieren': 'Uninstall selection',
    'Authentifizierung fehlgeschlagen: {}': 'Authentication failed: {}',
    'Automatisch': 'Automatic',
    'Automatisch sichern': 'Back up automatically',
    'Automatische Updates': 'Automatic updates',
    'Automatische Updates aktivieren': 'Enable automatic updates',
    'Autor': 'Author',
    'Autostart-Programme': 'Autostart programs',
    'Backend: {}': 'Backend: {}',
    'Backup abbrechen': 'Cancel backup',
    'Backup fertig': 'Backup finished',
    'Backup löschen': 'Delete backup',
    'Backup mit Problemen': 'Backup with problems',
    'Backup starten': 'Start backup',
    'Backup vom {} an den ursprünglichen Ort zurückschreiben?\n\nGleichnamige Dateien werden durch den Stand aus dem Backup ersetzt. Dateien, die es im Backup nicht gibt, bleiben erhalten.': "Write the backup from {} back to the original location?\n\nFiles with the same name are replaced by the version from the backup. Files that aren't in the backup are kept.",
    'Backup vom {} endgültig löschen?': 'Permanently delete the backup from {}?',
    'Backup-Ziel wählen': 'Choose backup target',
    'Backups liegen auf dem Ziel im Ordner {}/{}/.': 'Backups are stored on the target in the folder {}/{}/.',
    'bash.ws hat keine DNS-Anfrage empfangen.': "bash.ws didn't receive a DNS request.",
    'bash.ws nicht erreichbar (': 'bash.ws not reachable (',
    'bash.ws nicht erreichbar ({}).': 'bash.ws not reachable ({}).',
    'Basisgeschwindigkeit': 'Base speed',
    'Baue Paket …': 'Building package …',
    'Bauen fehlgeschlagen': 'Build failed',
    'Beenden': 'End',
    'beenden': 'end',
    'beendet': 'ended',
    'beendet.': 'ended.',
    'Befehl': 'Command',
    'Begrenzen': 'Limit',
    'Begrenzt': 'Limited',
    'behält die 2 neuesten Versionen je Paket': 'keeps the 2 newest versions per package',
    'Bei Abstürzen keine Speicherabbilder mehr speichern und vorhandene löschen?\n\nEntwickler brauchen sie manchmal zur Fehlersuche. Wird in {} gespeichert.': 'Stop saving memory dumps on crashes and delete existing ones?\n\nDevelopers sometimes need them for debugging. Saved in {}.',
    'Bei Abstürzen landet der Arbeitsspeicher des Programms auf der Platte – darin können Passwörter stehen.': 'When a program crashes, its memory ends up on disk – it may contain passwords.',
    'Beim Start automatisch nach System-Updates suchen (pacman, AUR, Flatpak)': 'Check for system updates automatically at startup (pacman, AUR, Flatpak)',
    'Beim Start automatisch nach Updates suchen': 'Check for updates automatically at startup',
    'Beim Systemstart automatisch verbinden': 'Connect automatically at system start',
    'Beim Update am {} gab es Fehler oder Warnungen (z. B. .pacnew-Dateien, fehlgeschlagene Hooks).': 'The update on {} had errors or warnings (e.g. .pacnew files, failed hooks).',
    'Bekannte Lücken ohne verfügbares Update: {}. Nichts zu tun – der Fix kommt mit einem späteren Update.': 'Known vulnerabilities without an available update: {}. Nothing to do – the fix will come with a later update.',
    'Bekannte Sicherheitslücken': 'Known vulnerabilities',
    'Belegt': 'Used',
    'Belegt: {}. Ohne Grenze darf das Protokoll bis zu 10 % der Partition nutzen und hält Einträge sehr lange.': 'Used: {}. Without a limit the log may use up to 10 % of the partition and keeps entries for a very long time.',
    'Belegung': 'Usage',
    'Belegung je Festplatte, größte Ordner, Aufräumen': 'Usage per drive, largest folders, cleanup',
    'Belegung je Gerät': 'Usage per device',
    'Beliebig (schnellster)': 'Any (fastest)',
    'Beliebige Stadt': 'Any city',
    'Benutzer': 'Users',
    'Benutzer-Cache (~/.cache)': 'User cache (~/.cache)',
    'benutzer/tuxdex oder GitHub-Link': 'user/tuxdex or GitHub link',
    'Benutzergruppen': 'User groups',
    'Benutzerkonten und letzte Anmeldung': 'User accounts and last login',
    'Berechne …': 'Calculating …',
    'Bereit': 'Ready',
    'Bereit.': 'Ready.',
    'Beschreibung': 'Description',
    'Bestätigen': 'Confirm',
    'Beta verwenden': 'Use beta',
    'Beta {} installiert': 'Beta {} installed',
    'Beta-Versionen': 'Beta versions',
    'Beta-Versionen aktiv.': 'Beta versions active.',
    'Beta-Versionen bekommen neue Funktionen früher, können aber noch Fehler haben.\n\nZurück zur stabilen Version geht jederzeit hier.': 'Beta versions get new features earlier but may still have bugs.\n\nYou can switch back to the stable version here at any time.',
    'Betriebssystem': 'Operating system',
    'Betriebszeit': 'Uptime',
    'Bezeichnung': 'Label',
    'Bilder': 'Pictures',
    'Bildschirmfreigabe (VNC)': 'Screen sharing (VNC)',
    'Bildschirmsperre': 'Screen lock',
    'Bitte eine Regel in der Liste auswählen.': 'Please select a rule in the list.',
    'Bitte einen einfachen absoluten Pfad (keine Leerzeichen/Sonderzeichen) und eine positive Größe in GB angeben.': 'Please enter a simple absolute path (no spaces/special characters) and a positive size in GB.',
    'Bitte einen einfachen absoluten Pfad angeben (ohne Leerzeichen, „..“ oder doppelte /).': 'Please enter a simple absolute path (no spaces, “..” or double /).',
    'Bitte einen gültigen Pfad angeben.': 'Please enter a valid path.',
    'Bitte einen Pfad wie ~/Spiele oder /mnt/daten angeben.': 'Please enter a path like ~/Games or /mnt/data.',
    'Bitte einen Port (z. B. 22) oder Bereich (8000:8100) angeben.': 'Please enter a port (e.g. 22) or range (8000:8100).',
    'Bitte erst aushängen bzw. schließen.': 'Please unmount or close it first.',
    'Bitte gültige(n) Paketnamen eingeben (Leerzeichen-getrennt für mehrere).': 'Please enter valid package name(s) (space-separated for several).',
    'Bitte mindestens ein angeschlossenes Ziel anhaken.': 'Please check at least one connected target.',
    'Bitte mindestens eine Quelle auswählen.': 'Please select at least one source.',
    'Bitte mindestens einen Ordner zum Sichern hinzufügen.': 'Please add at least one folder to back up.',
    'Bitte zuerst ein Backup in der Liste auswählen.': 'Please select a backup in the list first.',
    'Bitte zuerst ein Ziel hinzufügen.': 'Please add a target first.',
    'Bitte „benutzer/repo“ oder einen GitHub-Link angeben.': 'Please enter “user/repo” or a GitHub link.',
    'Blockiert': 'Blocked',
    'Bootzeit': 'Boot time',
    'Browser, Thumbnail-Vorschauen, Spiele-Launcher und viele andere Programme legen in ~/.cache Zwischendaten ab. Löschen ist grundsätzlich möglich – die Programme bauen den Cache neu auf.\n\nTuxdex zeigt hier nur die Größe, weil laufende Programme beim Löschen durcheinanderkommen können. Welcher Ordner groß ist, siehst du oben in der Speicher-Übersicht.': 'Browsers, thumbnail previews, game launchers and many other programs store temporary data in ~/.cache. Deleting it is generally possible – the programs rebuild the cache.\n\nTuxdex only shows the size here because running programs can get confused when it is deleted. Which folder is large is shown above in the storage overview.',
    'btrfs (Linux)': 'btrfs (Linux)',
    'Btrfs-Snapshots vor Updates': 'Btrfs snapshots before updates',
    'Checkliste: Wartung, Datenschutz & Performance': 'Checklist: maintenance, privacy & performance',
    'Chipkartenleser, z. B. für den Personalausweis.': 'Smart card readers, e.g. for ID cards.',
    'ClamAV ist installiert, aber ohne Virensignaturen.': 'ClamAV is installed, but without virus signatures.',
    'ClamAV ist optional – Tuxdex funktioniert auch ohne.': 'ClamAV is optional – Tuxdex also works without it.',
    'ClamAV mit aktuellen Signaturen.': 'ClamAV with current signatures.',
    'ClamAV: Signaturen, Scans, Quarantäne': 'ClamAV: signatures, scans, quarantine',
    'Commit {} – lade Dateien …': 'Commit {} – loading files …',
    'CPU = Anteil an der Leistung aller Kerne. Energie wird aus der CPU-Last geschätzt – Linux misst den Verbrauch einzelner Programme nicht direkt. Datenträger = Lesen + Schreiben pro Sekunde (bei Prozessen anderer Benutzer nur mit root sichtbar).': "CPU = share of the power of all cores. Energy is estimated from the CPU load – Linux doesn't measure the consumption of individual programs directly. Disk = read + write per second (for other users' processes only visible with root).",
    'CPU-Regler': 'CPU governor',
    'CPUfreq-Regler': 'CPUfreq governor',
    'CPUfreq-Treiber': 'CPUfreq driver',
    'Darf deinen GPG-Agenten zum Signieren nutzen.': 'May use your GPG agent for signing.',
    'Darf deinen SSH-Agenten für Anmeldungen nutzen.': 'May use your SSH agent for logins.',
    'Darf mit allen Programmen deiner Sitzung sprechen.': 'May talk to all programs in your session.',
    'Darf mit allen Systemdiensten sprechen.': 'May talk to all system services.',
    'Darf Verbindungen ins Internet und lokale Netz aufbauen.': 'May connect to the internet and the local network.',
    'Das Backup ist verschlüsselt. Das Passwort wird nicht gespeichert.': 'The backup is encrypted. The password is not saved.',
    'das normale Netz': 'the normal network',
    'Das Passwort muss mindestens 8 Zeichen haben.': 'The password must have at least 8 characters.',
    'Das PKGBUILD im Repository gehört nicht zu tuxdex': "The PKGBUILD in the repository doesn't belong to tuxdex",
    'Das System startet im Legacy-BIOS-Modus.': 'The system boots in legacy BIOS mode.',
    'Das System-Protokoll (systemd-journald) sammelt Meldungen aller Dienste und des Kernels.\n\n„Auf 200 MB kürzen“ führt journalctl --vacuum-size=200M aus: die ältesten Einträge werden gelöscht, bis das Protokoll noch 200 MB belegt. Neue Meldungen werden weiter geschrieben.\n\nDauerhaft begrenzen: Sicherheit → Checkliste → System-Protokoll. Braucht root.': 'The system log (systemd-journald) collects messages from all services and the kernel.\n\n“Shrink to 200 MB” runs journalctl --vacuum-size=200M: the oldest entries are deleted until the log uses 200 MB. New messages keep being written.\n\nLimit permanently: Security → Checklist → System log. Needs root.',
    'Das System-Protokoll auf 500 MB und einen Monat begrenzen?\n\nWird in {} gespeichert. Ältere Einträge werden gelöscht.': 'Limit the system log to 500 MB and one month?\n\nSaved in {}. Older entries will be deleted.',
    'Das Ziel darf nicht gleich einer Quelle sein.': 'The target must not be the same as a source.',
    'Datei konnte nicht ersetzt werden: {}': 'File could not be replaced: {}',
    'Datei zusätzlich löschen und Eintrag aus /etc/fstab entfernen': 'Also delete the file and remove the entry from /etc/fstab',
    'Dateien': 'Files',
    'Dateien in Quarantäne liegen ohne Ausführungsrechte in {}.': 'Files in quarantine are stored without execute permissions in {}.',
    'Dateien, die du im Dateimanager gelöscht hast, landen zuerst hier (~/.local/share/Trash).\n\n„Leeren“ löscht sie endgültig – sie lassen sich danach nicht mehr wiederherstellen.': "Files you deleted in the file manager end up here first (~/.local/share/Trash).\n\n“Empty” deletes them permanently – they can't be restored afterwards.",
    'Dateifreigabe (Samba)': 'File sharing (Samba)',
    'Dateisystem': 'File system',
    'Datenmenge': 'Data',
    'Datenordner': 'Data folder',
    'Datenschutz': 'Privacy',
    'Datenträger': 'Drives',
    'Datenträger formatieren': 'Format drive',
    'Datenträger umbenennen': 'Rename drive',
    'Datenträger · {}': 'Drive · {}',
    'davon Programme': 'of which programs',
    'Deaktivieren': 'Disable',
    'deaktiviert': 'disabled',
    'Deine öffentliche IP steht auf keiner bekannten Sperrliste.': "Your public IP isn't on any known blocklist.",
    'Deine öffentliche IP steht auf Sperrlisten:': 'Your public IP is on blocklists:',
    'deinen DNS-Server': 'your DNS server',
    'Deinstallation beendet.': 'Uninstall finished.',
    'Deinstallieren': 'Uninstall',
    'Deinstallieren …': 'Uninstall …',
    'Deinstallieren, Daten behalten': 'Uninstall, keep data',
    'Den Ordner {} gibt es nicht.': "The folder {} doesn't exist.",
    'Der Bildschirm sperrt sich bei Inaktivität.': 'The screen locks when idle.',
    'Der Bildschirm sperrt sich nicht automatisch – in den Systemeinstellungen einschalten.': "The screen doesn't lock automatically – turn it on in the system settings.",
    'der Dienst lädt sie gerade (beim ersten Mal einige Minuten).</span>': 'the service is loading them right now (a few minutes the first time).</span>',
    'Der Internetverkehr läuft aber direkt über {} (Split-Tunnel).': 'But internet traffic goes directly via {} (split tunnel).',
    'Der Internetverkehr läuft darüber.': 'Internet traffic goes through it.',
    'Der Leak-Test-Dienst war nicht erreichbar.': "The leak test service wasn't reachable.",
    'Der Start ist noch nicht abgeschlossen.': "Startup hasn't finished yet.",
    'Der Taktregler der CPU. Details: Taskmanager → Leistung → Prozessor.': "The CPU's frequency governor. Details: Task manager → Performance → Processor.",
    'Der Vorgang wartet auf deine Antwort': 'The operation is waiting for your answer',
    'Details ausblenden (oder Kachel erneut anklicken)': 'Hide details (or click the tile again)',
    'Diagnose': 'Diagnosis',
    'Die 20 schnellsten aktuellen HTTPS-Server suchen und als /etc/pacman.d/mirrorlist speichern? Die alte Liste bleibt als mirrorlist.bak.': 'Find the 20 fastest up-to-date HTTPS servers and save them as /etc/pacman.d/mirrorlist? The old list is kept as mirrorlist.bak.',
    'Die aktuelle stabile Version ist {}. Du nutzt noch die Beta {} – wechseln installiert die stabile Version.': "The current stable version is {}. You're still using beta {} – switching installs the stable version.",
    'Die App hat noch keinen Datenordner ({}).': 'The app has no data folder yet ({}).',
    'Die Firewall filtert diese Ports.': 'The firewall filters these ports.',
    'Die Liste aller selbst installierten Pakete macht eine Neuinstallation leicht. Tuxdex legt sie in ~/.config/tuxdex ab und erneuert sie bei jedem Backup.': 'The list of all explicitly installed packages makes reinstalling easy. Tuxdex stores it in ~/.config/tuxdex and renews it with every backup.',
    'Die Liste der Download-Server. Ältere Listen führen zu langsamen oder veralteten Servern.': 'The list of download servers. Older lists lead to slow or outdated servers.',
    'Die Mullvad-Kontonummer hat 16 Ziffern.': 'The Mullvad account number has 16 digits.',
    'Die neue Datei ist fehlerhaft und wird nicht übernommen: {}': "The new file is faulty and won't be used: {}",
    'Die neue Version ist installiert. Tuxdex jetzt neu starten?': 'The new version is installed. Restart Tuxdex now?',
    'Die Passwörter stimmen nicht überein.': "The passwords don't match.",
    'Die Quarantäne ist leer.': 'The quarantine is empty.',
    'Die sudo-Regeln sind nur mit Administrator-Rechten lesbar.': 'The sudo rules are only readable with administrator rights.',
    'Die Systempartition ist unverschlüsselt – bei Diebstahl sind alle Daten lesbar. Nachträglich nur über Neuinstallation/Backup sinnvoll. LUKS-Geräte:': 'The system partition is unencrypted – if stolen, all data is readable. Can only be changed afterwards via reinstall/backup. LUKS devices:',
    'Die Systempartition liegt auf LUKS. LUKS-Geräte: {}': 'The system partition is on LUKS. LUKS devices: {}',
    'Die Uhrzeit wird über das Netz abgeglichen.': 'The time is synchronized over the network.',
    'Die Virensignaturen sind veraltet.': 'The virus signatures are outdated.',
    'Dienst': 'Service',
    'Dienst aus': 'Service off',
    'Dienst deaktivieren': 'Disable service',
    'Dienst nicht erreichbar': 'Service not reachable',
    'Dienst stoppen': 'Stop service',
    'Dienste starten größtenteils parallel – die Zeiten addieren sich nicht. Entscheidend ist vor allem „System (Dienste)“.': "Services mostly start in parallel – the times don't add up. What matters most is “System (services)”.",
    'Diese Dateien an ihren ursprünglichen Ort zurücklegen?\nNur tun, wenn du sicher bist, dass es ein Fehlalarm ist.': "Put these files back in their original location?\nOnly do this if you're sure it's a false alarm.",
    'Diese Dateien unwiderruflich löschen?': 'Permanently delete these files?',
    'Diese Gruppen geben praktisch root-Rechte ohne Passwort – nur behalten, wenn du sie brauchst (gpasswd -d BENUTZER GRUPPE).': 'These groups give practically root rights without a password – only keep them if you need them (gpasswd -d USER GROUP).',
    'Diese Ordner gehören zu keinem installierten Kernel mehr und werden gelöscht:': 'These folders no longer belong to any installed kernel and will be deleted:',
    'Diese verwaisten Pakete entfernen?': 'Remove these orphaned packages?',
    'Diese Ziele haben kein Linux-Dateisystem:': "These targets don't have a Linux file system:",
    'Dieses Gerät vom Mullvad-Konto abmelden?\nDas VPN wird getrennt.': 'Log this device out of the Mullvad account?\nThe VPN will be disconnected.',
    'Direkter Bluetooth-Zugriff.': 'Direct Bluetooth access.',
    'Direkter Zugriff auf die Grafikkarte für 3D und Video.': 'Direct access to the graphics card for 3D and video.',
    'DNS-Anfragen gehen an {} ({}) über {} – am VPN vorbei. Im VPN-Programm den VPN-DNS erzwingen (Mullvad: Kill-Switch/Lockdown).': 'DNS requests go to {} ({}) via {} – bypassing the VPN. Force the VPN DNS in the VPN program (Mullvad: kill switch/lockdown).',
    'DNS-Server, die Webseiten sehen': 'DNS servers websites see',
    'Docker speichert Images, Container und Volumes in /var/lib/docker.\n\nZum Aufräumen im Terminal: docker system prune (entfernt gestoppte Container, ungenutzte Netzwerke und Images ohne Namen; mit -a auch alle unbenutzten Images). Volumes bleiben, außer mit --volumes.': 'Docker stores images, containers and volumes in /var/lib/docker.\n\nTo clean up in the terminal: docker system prune (removes stopped containers, unused networks and untagged images; with -a also all unused images). Volumes are kept unless you add --volumes.',
    'Dokumente': 'Documents',
    'Doppelklick zum Öffnen': 'Double-click to open',
    'Download fehlgeschlagen': 'Download failed',
    'Downloads  (~/Downloads)': 'Downloads  (~/Downloads)',
    'Drucken': 'Printing',
    'Drucken (CUPS)': 'Printing (CUPS)',
    'Eigene Regel entfernen': 'Remove own rule',
    'eigener Eintrag': 'own entry',
    'Eigener Ordner …': 'Custom folder …',
    'Ein Pfad pro Zeile, z. B. ~/.cache': 'One path per line, e.g. ~/.cache',
    'Eine 1:1-Kopie, die bei jedem Lauf nur die Änderungen überträgt. Schnell, aber nur ein Stand.': 'A 1:1 copy that only transfers the changes on each run. Fast, but only one state.',
    'Eine einzige komprimierte Datei pro Backup, optional mit Passwort. Wird einmal gepackt und gleichzeitig auf alle Ziele geschrieben. Passt auf jeden Datenträger (FAT32: 4-GB-Teile).': 'One single compressed file per backup, optionally with a password. Packed once and written to all targets at the same time. Fits on any drive (FAT32: 4 GB parts).',
    'Eingehängt unter': 'Mounted at',
    'Eingestellt': 'Set',
    'Einhängen': 'Mount',
    'Einige DNS-Server gehören nicht zum Anbieter deiner VPN-Adresse:': "Some DNS servers don't belong to the provider of your VPN address:",
    'Einige gehören anderen Benutzern – dafür sind root-Rechte nötig.': 'Some belong to other users – that needs root rights.',
    'Einrichten': 'Set up',
    'Einschalten': 'Turn on',
    'Einstellung konnte nicht gespeichert werden: {}': 'Setting could not be saved: {}',
    'Einstellungen': 'Settings',
    'Empfangen': 'Received',
    'Empfohlen setzen': 'Set recommended',
    'Empfohlene Scheduler setzen (NVMe: none, SSD: mq-deadline, Festplatte: bfq)?\n\nWird als udev-Regel in {} gespeichert.': 'Set recommended schedulers (NVMe: none, SSD: mq-deadline, hard disk: bfq)?\n\nSaved as a udev rule in {}.',
    'Endgültig löschen': 'Delete permanently',
    'Energie (gesch.)': 'Energy (est.)',
    'Energiemodus': 'Energy mode',
    'Entfernen': 'Remove',
    'entfernt ungenutzte Laufzeiten': 'removes unused runtimes',
    'Entpacke Archiv …': 'Extracting archive …',
    'Entpacke Quellen …': 'Extracting sources …',
    'Entstanden mit': 'Made with',
    'Entwickelt von': 'Developed by',
    'Entwickler-Funktionen': 'Developer features',
    'Entwicklungsserver': 'Development server',
    'Erkannt': 'Detected',
    'Erkennung durch Webseiten': 'Detection by websites',
    'Erlauben': 'Allow',
    'erlauben': 'allow',
    'Erlaubt sind Buchstaben, Ziffern, Leerzeichen und _ . -': 'Allowed are letters, digits, spaces and _ . -',
    'Ermittle …': 'Measuring …',
    'Erneut aus': 'Off again',
    'Erneut testen': 'Test again',
    'Erreichbar': 'Reachable',
    'error: Anmeldung fehlgeschlagen – Nummer prüfen oder Gerätelimit (5 Geräte) im Mullvad-Konto erreicht.': 'error: login failed – check the number or the device limit (5 devices) of the Mullvad account has been reached.',
    'error: Kein Paket gebaut (Exit-Code {}) – Meldungen oben prüfen.': 'error: no package built (exit code {}) – check the messages above.',
    'error: Laufwerksliste konnte nicht aufgebaut werden: {}': 'error: drive list could not be built: {}',
    'error: lsblk fehlgeschlagen (Exit {}): {}\nAusgabe: {}': 'error: lsblk failed (exit {}): {}\nOutput: {}',
    'error: {} konnte nicht verschoben werden: {}': 'error: {} could not be moved: {}',
    'Ersetzen': 'Replace',
    'Ersparnis': 'Savings',
    'Erst aushängen': 'Unmount first',
    'Erst Signaturen laden – ohne sie kann ClamAV nichts erkennen.': "Load signatures first – without them ClamAV can't detect anything.",
    'Erstellt': 'Created',
    'Erzeuge Paketdatei …': 'Creating package file …',
    'Erzwingen': 'Force',
    'Es gibt keine verwaisten Pakete.': 'There are no orphaned packages.',
    'Es ist kein System-Proxy eingestellt.': 'No system proxy is set.',
    'Es ist kein VPN aktiv.': 'No VPN is active.',
    'Es läuft noch:': 'Still running:',
    'Es wird kein Swap genutzt.': 'No swap is used.',
    'Es wird nur deaktiviert, nichts gelöscht.': 'It will only be disabled, nothing deleted.',
    'Es wurde kein DNS-Server gefunden.': 'No DNS server was found.',
    'exFAT (USB-Sticks, alle Systeme)': 'exFAT (USB sticks, all systems)',
    'Exit-Node': 'Exit node',
    'ext4 (Linux)': 'ext4 (Linux)',
    'Fallback-Speicher': 'Fallback memory',
    'Fallback-Speicher (Swapfile) und Swappiness': 'Fallback memory (swap file) and swappiness',
    'Falsche Uhrzeit stört Zertifikate, Updates und Logs.': 'A wrong time disturbs certificates, updates and logs.',
    'Falsches Passwort – noch {} Versuch{}.': 'Wrong password – {} attempt{} left.',
    'FAT32 (maximal kompatibel)': 'FAT32 (maximum compatibility)',
    'Fehler': 'Error',
    'fehler': 'error',
    'Fehler beim Start: {}': 'Error at startup: {}',
    'Fehler im System-Protokoll seit dem Start (journalctl -p 3 -b). Viele sind harmlos (z. B. Firmware-Hinweise) – wiederkehrende lohnen einen Blick.': 'Errors in the system log since startup (journalctl -p 3 -b). Many are harmless (e.g. firmware notes) – recurring ones are worth a look.',
    'Fehler seit dem Start': 'Errors since startup',
    'Fehler: {}': 'Error: {}',
    'Fehlerprotokoll': 'Error log',
    'fehlgeschlagen': 'failed',
    'Fehlgeschlagen:': 'Failed:',
    'Fehlgeschlagene Dienste': 'Failed services',
    'Fehlt': 'Missing',
    'Fertig': 'Done',
    'Fertig · {}{}{}': 'Done · {}{}{}',
    'Fertig – neue Version installiert': 'Done – new version installed',
    'Festplatte (HDD)': 'Hard disk (HDD)',
    'Festplatte / Ordner': 'Drive / folder',
    'Festplatten & Partitionen': 'Hard drives & partitions',
    'Festplattenverschlüsselung (LUKS)': 'Disk encryption (LUKS)',
    'Firewall aktivieren': 'Enable firewall',
    'Firewall aktivieren? Eingehende Verbindungen werden dann blockiert (ausgehende bleiben erlaubt).': 'Enable the firewall? Incoming connections will then be blocked (outgoing stay allowed).',
    'Firewall wirklich deaktivieren?': 'Really disable the firewall?',
    'Firmware (fwupd)': 'Firmware (fwupd)',
    'Firmware (UEFI/BIOS)': 'Firmware (UEFI/BIOS)',
    'Firmware-Updates verfügbar für:': 'Firmware updates available for:',
    'Flathub fehlt': 'Flathub missing',
    'Flathub hinzufügen': 'Add Flathub',
    'Flatpak (Flathub)': 'Flatpak (Flathub)',
    'Flatpak (nicht installiert)': 'Flatpak (not installed)',
    'Flatpak (System + Benutzer)': 'Flatpak (system + user)',
    'flatpak fehlt': 'flatpak missing',
    'Flatpak installieren': 'Install Flatpak',
    'Flatpak ist installiert, aber <b>Flathub</b> (die große App-Quelle) ist noch nicht eingerichtet.': "Flatpak is installed, but <b>Flathub</b> (the big app source) isn't set up yet.",
    'flatpak ist nicht installiert': 'flatpak is not installed',
    'flatpak ist nicht installiert – im Tab „Flatpak“ einrichten.': 'flatpak is not installed – set it up in the “Flatpak” tab.',
    'Flatpak ist nicht installiert. Flatpak-Apps laufen abgeschottet vom System – hier legst du fest, was jede App darf.': 'Flatpak is not installed. Flatpak apps run isolated from the system – here you decide what each app may do.',
    'Flatpak · {}': 'Flatpak · {}',
    'Flatpak-Apps brauchen Laufzeiten (z. B. GNOME- oder KDE-Plattform). Nach Updates oder dem Deinstallieren von Apps bleiben alte Versionen liegen.\n\n„Unbenutzte entfernen“ führt flatpak uninstall --unused aus. Apps und ihre Daten bleiben erhalten.': 'Flatpak apps need runtimes (e.g. the GNOME or KDE platform). After updates or uninstalling apps, old versions are left behind.\n\n“Remove unused” runs flatpak uninstall --unused. Apps and their data are kept.',
    'Flatpak-Apps und ihre Rechte (Dateien, Geräte, Netzwerk …) wie mit Flatseal': 'Flatpak apps and their permissions (files, devices, network …) like Flatseal',
    'Flatpak: nur für mich installieren (--user, ohne Passwort)': 'Flatpak: install only for me (--user, no password)',
    'Formatieren': 'Format',
    'Formatieren nicht möglich': 'Formatting not possible',
    'Formatieren von {} {}.': 'Formatting {} {}.',
    'Formatieren …': 'Format …',
    'Formatiert': 'Formatted',
    'Formfaktor': 'Form factor',
    'Fortschritt': 'Progress',
    'Fortsetzen': 'Resume',
    'Fragt am.i.mullvad.net nach deiner öffentlichen IP': 'Asks am.i.mullvad.net for your public IP',
    'Fragt am.i.mullvad.net – zeigt, wie dich Webseiten sehen': 'Asks am.i.mullvad.net – shows how websites see you',
    'frei': 'free',
    'Freigaben über Portale': 'Sharing via portals',
    'Freigeben': 'Allow',
    'Freigegeben': 'Allowed',
    'freigegeben': 'allowed',
    'freshclam läuft bereits (PID': 'freshclam is already running (PID',
    'fstrim.timer gibt freie Blöcke einmal pro Woche an die SSD zurück.': 'fstrim.timer returns free blocks to the SSD once a week.',
    'Funde': 'Findings',
    'Funde automatisch in Quarantäne verschieben': 'Move findings to quarantine automatically',
    'fwupd hat nicht geantwortet.': "fwupd didn't respond.",
    'fwupd kennt keine neueren Firmware-Versionen (BIOS, SSD, Dock …).': 'fwupd knows no newer firmware versions (BIOS, SSD, dock …).',
    'Für diese Desktop-Umgebung kann Tuxdex die Sperre nicht auslesen – bitte in den Systemeinstellungen prüfen.': "Tuxdex can't read the lock setting for this desktop environment – please check in the system settings.",
    'Für erhöhten Schutzbedarf gibt es linux-hardened (manche Programme laufen damit eingeschränkt).': "If you need extra protection, there is linux-hardened (some programs run with restrictions).",
    'Für manche Spiele und Kommunikations-Apps nötig.': 'Needed by some games and communication apps.',
    'Für sie „Archiv“ oder „Spiegel“ verwenden – oder abhaken.': 'Use “Archive” or “Mirror” for them – or uncheck them.',
    'Für virtuelle Maschinen und Emulatoren.': 'For virtual machines and emulators.',
    'Ganzes System  (/)': 'Whole system  (/)',
    'Gateway: {}  ·  DNS: {}': 'Gateway: {}  ·  DNS: {}',
    'gefunden': 'found',
    'Gefundene Updates beim Start in einem Fenster anbieten (sonst nur Hinweis unten rechts)': 'Offer found updates in a window at startup (otherwise only a hint at the bottom right)',
    'Gefundene Updates erscheinen als Zahl am Tab „Updates“ und unten rechts – installiert wird erst, wenn du im Tab „Updates“ auf „Update starten“ klickst.': 'Found updates appear as a number on the “Updates” tab and at the bottom right – nothing is installed until you click “Start update” in the “Updates” tab.',
    'Gelistet': 'Listed',
    'gelöscht': 'deleted',
    'Gelöscht: {}': 'Deleted: {}',
    'Gemeinsam genutzt': 'Shared',
    'Gemeinsamer Gerätespeicher (/dev/shm)': 'Shared device memory (/dev/shm)',
    'Gemeinsamer Speicher (IPC)': 'Shared memory (IPC)',
    'gerade eben': 'just now',
    'Gerät': 'Device',
    'Gerät „{}“': 'device “{}”',
    'Geräte': 'Devices',
    'Geräte im lokalen Netz erreichbar (Drucker, NAS …)': 'Devices in the local network reachable (printers, NAS …)',
    'Geräteerkennung (mDNS)': 'Device discovery (mDNS)',
    'Gesamt': 'Total',
    'gesamt': 'total',
    'gesamt {}': 'total {}',
    'gesamt ↓ {} ↑ {}': 'total ↓ {} ↑ {}',
    'Geschwindigkeit': 'Speed',
    'Geschützt': 'Protected',
    'Gespeichert': 'Saved',
    'gespeichert und gilt sofort und nach jedem Neustart.': 'saved and applies immediately and after every restart.',
    'Gespeichert – gilt ab dem nächsten Start.': 'Saved – applies from the next start.',
    'Gespeichert – gilt beim nächsten Start von {}.': 'Saved – applies the next time {} starts.',
    'Gespeicherte Freigaben dieser App vergessen? Sie fragt beim nächsten Mal erneut.': "Forget this app's saved permissions? It will ask again next time.",
    'Gesperrt': 'Blocked',
    'gesperrt': 'blocked',
    'Gestartet am': 'Started on',
    'gestoppt': 'stopped',
    'gestoppt/pausiert': 'stopped/paused',
    'Getrennt': 'Disconnected',
    'geändert': 'changed',
    'GitHub nicht erreichbar oder Repository falsch: {}': 'GitHub not reachable or wrong repository: {}',
    'GitHub-Repository': 'GitHub repository',
    'Gleiche oder ältere Version': 'Same or older version',
    'Globale Regeln': 'Global rules',
    'GPG-Schlüssel': 'GPG keys',
    'Grafik': 'Graphics',
    'Grafik · {}': 'Graphics · {}',
    'Grafikbeschleunigung (GPU)': 'Graphics acceleration (GPU)',
    'Grafiktreiber': 'Graphics driver',
    'Grafische Systemverwaltung für Arch Linux – alles in einem Fenster, ohne Terminal. Befehle laufen sichtbar in der Ausgabe, root-Rechte werden nur bei Bedarf und einmal pro Sitzung abgefragt.': 'Graphical system management for Arch Linux – everything in one window, no terminal. Commands run visibly in the output, root rights are only requested when needed and once per session.',
    'GRÖSSE': 'SIZE',
    'Größe': 'Size',
    'Größe (GB)': 'Size (GB)',
    'Größe berechnen': 'Calculate size',
    'Größen ermitteln': 'Measure sizes',
    'gzip – überall lesbar': 'gzip – readable everywhere',
    'gültig bis {}': 'valid until {}',
    'Handles = geöffnete Dateien und Verbindungen im ganzen System. Die Last zeigt, wie viele Prozesse im Schnitt auf Rechenzeit warten – mehr als die Zahl der Threads heißt Überlastung.': 'Handles = open files and connections in the whole system. The load shows how many processes are waiting for CPU time on average – more than the number of threads means overload.',
    'Hardware & System': 'Hardware & system',
    'Hersteller': 'Manufacturer',
    'Heute hieße die Sicherung: <b>{}</b><br>Platzhalter: <b>yyyy</b> Jahr · <b>mm</b> Monat · <b>dd</b> Tag · <b>HH</b> Stunde · <b>MM</b> Minute · <b>SS</b> Sekunde – mit beliebigem Text davor oder dahinter, z. B. <b>Laptop_yyyy-mm-dd</b> oder <b>yyyy-mm-dd vor Update</b>. Gibt es den Namen schon, hängt Tuxdex _2, _3 … an.': 'Today the backup would be called: <b>{}</b><br>Placeholders: <b>yyyy</b> year · <b>mm</b> month · <b>dd</b> day · <b>HH</b> hour · <b>MM</b> minute · <b>SS</b> second – with any text before or after, e.g. <b>Laptop_yyyy-mm-dd</b> or <b>yyyy-mm-dd before update</b>. If the name already exists, Tuxdex appends _2, _3 ….',
    'Hintergrundprozesse': 'Background processes',
    'Hinweis': 'Notice',
    'Hinzufügen': 'Add',
    'hoch': 'high',
    'Hochrechnung läuft …': 'Estimating …',
    'Hole neueste Version (git pull) …': 'Fetching latest version (git pull) …',
    'Home-Ordner': 'Home folder',
    'Home-Ordner (~)': 'Home folder (~)',
    'Hängt? {} Min keine Meldung': 'Stuck? No message for {} min',
    'Höchstens {} (belegt: {}).': 'At most {} (used: {}).',
    'Ich habe verstanden und nutze Tuxdex auf eigenes Risiko.': 'I understand and use Tuxdex at my own risk.',
    'Im Archiv ist kein PKGBUILD für tuxdex.': 'The archive contains no PKGBUILD for tuxdex.',
    'Im Cache': 'Cached',
    'Im Dateimanager zeigen': 'Show in file manager',
    'Im Original gelöschte Dateien auch im Spiegel löschen': 'Also delete files in the mirror that were deleted in the original',
    'Im Paket fehlt tuxdex.py.': 'tuxdex.py is missing from the package.',
    'Immer höchster Takt – schnell, aber mehr Strom und Wärme.': 'Always highest clock – fast, but more power and heat.',
    'In /etc/pacman.conf steht SigLevel = Never/TrustAll bei:': 'In /etc/pacman.conf, SigLevel = Never/TrustAll is set for:',
    'In diesem Ordner liegt kein PKGBUILD für tuxdex.': 'This folder contains no PKGBUILD for tuxdex.',
    'In Ordner wiederherstellen …': 'Restore to folder …',
    'In pacman.log steht noch kein vollständiges Update.': "pacman.log doesn't contain a full update yet.",
    'in Quarantäne': 'in quarantine',
    'In Quarantäne verschieben': 'Move to quarantine',
    'In Quarantäne: {}': 'In quarantine: {}',
    'In Verwendung': 'In use',
    'In {} legt Tuxdex aus Sicherheitsgründen kein Swapfile an.': "For security reasons Tuxdex doesn't create a swap file in {}.",
    'In {} „telemetry.telemetryLevel“ auf „off“ setzen?': 'Set “telemetry.telemetryLevel” to “off” in {}?',
    'Insgesamt empfangen': 'Total received',
    'Insgesamt gelesen': 'Total read',
    'Insgesamt geschrieben': 'Total written',
    'Insgesamt gesendet': 'Total sent',
    'Installation beendet (Exit {}).': 'Installation finished (exit {}).',
    'Installation fehlgeschlagen': 'Installation failed',
    'Installiere fehlende Abhängigkeiten …': 'Installing missing dependencies …',
    'Installiere mit pacman …': 'Installing with pacman …',
    'Installiere neue Version …': 'Installing new version …',
    'Installiere …': 'Installing …',
    'Installieren': 'Install',
    'Installiert': 'Installed',
    'Installiert am': 'Installed on',
    'Installiert, aus': 'Installed, off',
    'Installiert: {}  ·  {}': 'Installed: {}  ·  {}',
    'Installierte Software': 'Installed software',
    'Interner Fehler – Details in ~/tuxdex_error.log': 'Internal error – details in ~/tuxdex_error.log',
    'Internet & Netzwerk': 'Internet & network',
    'Internetanbieter': 'Internet provider',
    'Internetanbieter oder unbekannter Anbieter': 'Internet provider or unknown provider',
    'ipapi.is nicht erreichbar (': 'ipapi.is not reachable (',
    'ipapi.is nicht erreichbar ({}).': 'ipapi.is not reachable ({}).',
    'Ist der Ordner ein Git-Klon, holt Tuxdex vorher die neueste Version (git pull). Gebaut wird mit makepkg in einem Arbeitsordner, installiert mit pacman – danach startet Tuxdex neu.': 'If the folder is a Git clone, Tuxdex first fetches the latest version (git pull). It is built with makepkg in a work folder and installed with pacman – then Tuxdex restarts.',
    'Ja': 'Yes',
    'Ja ({})': 'Yes ({})',
    'Jedes Backup ist eine eigene Version (Datum/Uhrzeit). Unveränderte Dateien werden nur verlinkt und kosten keinen Platz – wie Time Machine. Braucht ext4, btrfs, xfs …': 'Every backup is its own version (date/time). Unchanged files are only linked and take no space – like Time Machine. Needs ext4, btrfs, xfs …',
    'jetzt': 'now',
    'Jetzt aktualisieren': 'Update now',
    'Jetzt speichern': 'Save now',
    'Kachel anklicken, um Details zu sehen – z. B. Caches, Takt und Virtualisierung beim Prozessor, Partitionen beim Datenträger, Takt und Leistung bei der Grafik.': 'Click a tile to see details – e.g. caches, clock speed and virtualization for the processor, partitions for drives, clock speed and power for graphics.',
    'Kann nicht schreiben: {}': "Can't write: {}",
    'Kapazität': 'Capacity',
    'Kapazität jetzt': 'Capacity now',
    'Kapazität neu': 'Capacity when new',
    'KDE Connect': 'KDE Connect',
    'Kein Akku gefunden – das Gerät läuft am Netz.': 'No battery found – the device runs on mains power.',
    'kein Akku – Netzbetrieb': 'no battery – on mains power',
    'Kein aktiver Swap gefunden.': 'No active swap found.',
    'Kein Dateisystem – mit „Formatieren“ anlegen.': 'No file system – create one with “Format”.',
    'Kein Dienst wartet auf Verbindungen von außen.': 'No service is waiting for connections from outside.',
    'Kein Eintrag': 'No entry',
    'Kein externes Laufwerk eingehängt – Stick einstecken oder Ordner wählen': 'No external drive mounted – plug in a stick or choose a folder',
    'Kein Fernzugriff per SSH möglich.': 'No remote access via SSH possible.',
    'Kein Gerät ausgewählt': 'No device selected',
    'Kein installiertes Paket hat eine bekannte Lücke.': 'No installed package has a known vulnerability.',
    'Kein Leck': 'No leak',
    'Kein Passwort oder Token im Shell-Verlauf gefunden.': 'No password or token found in the shell history.',
    'Kein Programm wartet auf Verbindungen von außen – nichts zu tun.': 'No program is waiting for connections from outside – nothing to do.',
    'Kein Prozess ausgewählt': 'No process selected',
    'Kein Repository eingetragen': 'No repository set',
    'Kein SSD/NVMe-Laufwerk gefunden.': 'No SSD/NVMe drive found.',
    'Kein Swap': 'No swap',
    'Kein Swap aktiv': 'No swap active',
    'kein Swap aktiv': 'no swap active',
    'KEIN Treiber': 'NO driver',
    'Kein UEFI': 'No UEFI',
    'Kein vollständiges Update im pacman-Log gefunden.': 'No full update found in the pacman log.',
    'Kein VPN': 'No VPN',
    'Kein Werkzeug': 'No tool',
    'Kein Zeitplan aktiv': 'No schedule active',
    'Kein Ziel': 'No target',
    'Kein Ziel erreichbar – nichts zu tun.': 'No target reachable – nothing to do.',
    'Keine': 'None',
    'keine': 'none',
    'Keine /etc/pacman.d/mirrorlist gefunden.': 'No /etc/pacman.d/mirrorlist found.',
    'Keine Admin-Rechte': 'No admin rights',
    'keine Adresse': 'no address',
    'Keine aktivierten Benutzerdienste.': 'No enabled user services.',
    'keine Angabe gefunden': 'no information found',
    'Keine App ausgewählt': 'No app selected',
    'Keine Autostart-Programme.': 'No autostart programs.',
    'Keine Bedrohungen': 'No threats',
    'Keine bekannt': 'None known',
    'Keine Dateisysteme gefunden.': 'No file systems found.',
    'keine Drehzahl gemeldet': 'no speed reported',
    'Keine Firewall aktiv. Eingehende Verbindungen werden nicht gefiltert.': 'No firewall active. Incoming connections are not filtered.',
    'Keine gesehen': 'None seen',
    'Keine gespeicherten Freigaben (Kamera, Standort, Bildschirmaufnahme …).': 'No saved permissions (camera, location, screen recording …).',
    'Keine gespeicherten Freigaben.': 'No saved permissions.',
    'keine Grafikkarte erkannt': 'no graphics card detected',
    'keine Kompression': 'no compression',
    'keine Laufwerke gefunden': 'no drives found',
    'Keine Laufwerke gefunden.': 'No drives found.',
    'keine Netzwerkschnittstelle gefunden': 'no network interface found',
    'Keine offen': 'None open',
    'Keine Quellen': 'No sources',
    'Keine Reste alter Kernel.': 'No leftovers of old kernels.',
    'Keine Rückmeldung seit über 10 Minuten nach:': 'No response for over 10 minutes after:',
    'keine Signaturen': 'no signatures',
    'Keine Signaturen': 'No signatures',
    'Keine SSD': 'No SSD',
    'Keine Updates verfügbar – das System ist aktuell.': 'No updates available – the system is up to date.',
    'Keine Updates verfügbar.': 'No updates available.',
    'Keine vorhandenen Quellen ausgewählt.': 'No existing sources selected.',
    'Keine weiteren Ordner freigegeben.': 'No additional folders shared.',
    'Keiner': 'None',
    'keiner gefunden': 'none found',
    'Kerne': 'Cores',
    'Kernel & Neustart': 'Kernel & restart',
    'Kernel (Slab)': 'Kernel (slab)',
    'Kernel wurde aktualisiert – bitte neu starten, damit z. B. USB-Sticks erkannt werden.': 'Kernel was updated – please restart so that e.g. USB sticks are detected.',
    'Kernel-Austausch im laufenden Betrieb (kexec) und SysRq-Tastenkürzel sind gesperrt, Kernel-Meldungen und -Adressen nur für root lesbar.': 'Replacing the kernel at runtime (kexec) and SysRq shortcuts are blocked, kernel messages and addresses readable only by root.',
    'Kernel-Austausch im laufenden Betrieb (kexec) und SysRq-Tastenkürzel sperren, Kernel-Meldungen (dmesg) und -Adressen nur für root?\n\nWird in': 'Block replacing the kernel at runtime (kexec) and SysRq shortcuts, kernel messages (dmesg) and addresses only for root?\n\nSaved in',
    'Kernel-Schutz': 'Kernel protection',
    'kernel.randomize_va_space sollte 2 sein (Standard). Jemand hat es abgeschaltet.': 'kernel.randomize_va_space should be 2 (default). Someone turned it off.',
    'KI-gestützt entwickelt (AI made) – in Zusammenarbeit mit Claude von Anthropic': 'AI-assisted development (AI made) – in collaboration with Claude by Anthropic',
    'Kill-Switch aktiv – ohne VPN kein Internet': 'Kill switch active – no internet without VPN',
    'Kill-Switch aktivieren': 'Enable kill switch',
    'Kill-Switch an': 'Kill switch on',
    'Kill-Switch: Internet nur über VPN (Lockdown-Modus)': 'Kill switch: internet only via VPN (lockdown mode)',
    'Kompletter Sitzungs-Bus': 'Entire session bus',
    'Kompletter System-Bus': 'Entire system bus',
    'Kompression': 'Compression',
    'Komprimiere Paket …': 'Compressing package …',
    'Komprimiert (zram)': 'Compressed (zram)',
    'Konnte nicht speichern: {}': 'Could not save: {}',
    'Kontakt': 'Contact',
    'Kontonummer': 'Account number',
    'Kontonummer anzeigen': 'Show account number',
    'Kontonummer verbergen': 'Hide account number',
    'Kästchen anklicken (oder Doppelklick auf die Zeile) wählt aus – kein Strg nötig.': 'Clicking the checkbox (or double-clicking the row) selects – no Ctrl needed.',
    'Lade Dateien {}/{}: {}': 'Loading files {}/{}: {}',
    'Lade github.com/{} ({}) …': 'Loading github.com/{} ({}) …',
    'Lade herunter …': 'Downloading …',
    'Lade Paket …': 'Loading package …',
    'Lade Signaturen … {}:{}': 'Loading signatures … {}:{}',
    'Lade …': 'Loading …',
    'Ladegrenze': 'Charge limit',
    'Ladestand': 'Charge',
    'Ladezyklen': 'Charge cycles',
    'Land': 'Country',
    'langsam': 'slow',
    'Last': 'Load',
    'Last 1 / 5 / 15 Min': 'Load 1 / 5 / 15 min',
    'Last {}': 'Load {}',
    'Laufende Wartung': 'Ongoing maintenance',
    'Laufendes Backup abbrechen? Unfertige Dateien werden entfernt, vorhandene Backups bleiben erhalten.': 'Cancel the running backup? Unfinished files are removed, existing backups are kept.',
    'Laufendes Update wirklich abbrechen?\n\nPacman bricht sauber ab, solange noch nichts installiert wird.': 'Really cancel the running update?\n\nPacman stops cleanly as long as nothing is being installed yet.',
    'Laufwerk': 'Drive',
    'Laufwerk hinzufügen': 'Add drive',
    'Laufwerke & Partitionen': 'Drives & partitions',
    'Laufwerke einhängen, umbenennen, prüfen, formatieren, sicher entfernen': 'Mount, rename, check, format and safely remove drives',
    'Laufwerke neu einlesen': 'Rescan drives',
    'Leak-Test & VPN-Erkennung': 'Leak test & VPN detection',
    'Leck': 'Leak',
    'leer = Standard, z. B. 2026-09-27_101500': 'empty = default, e.g. 2026-09-27_101500',
    'leer lassen = Standard': 'leave empty = default',
    'Leeren': 'Empty',
    'Leerlauf': 'Idle',
    'Leistung': 'Performance',
    'Leistungsaufnahme': 'Power draw',
    'Lesegeschwindigkeit': 'Read speed',
    'Lesen & Schreiben': 'Read & write',
    'Lesen {}/s · Schreiben {}/s': 'Read {}/s · write {}/s',
    'Lesen, Schreiben & Anlegen': 'Read, write & create',
    'Lesezugriff auf Programme und Bibliotheken des Systems.': "Read access to the system's programs and libraries.",
    'Letzte Änderung Flatpak (Näherung)': 'Last Flatpak change (approximate)',
    'Letzter Login': 'Last login',
    'Letzter Scan: {} · {} · {} Dateien · {} Funde · Dauer {}:{}': 'Last scan: {} · {} · {} files · {} findings · duration {}:{}',
    'Letzter Start: {} ({})': 'Last boot: {} ({})',
    'Letztes Backup': 'Last backup',
    'Letztes Backup {}': 'Last backup {}',
    'Letztes Update (pacman.log)': 'Last update (pacman.log)',
    'Letztes Update am {} ohne Fehler.': 'Last update on {} without errors.',
    'Letztes vollständiges Update': 'Last full update',
    'Letztes vollständiges Update vor {} Tag{}': 'Last full update {} day{} ago',
    'Lies vorher die Arch-News (archlinux.org/news).': 'Read the Arch news first (archlinux.org/news).',
    'Liste neu laden': 'Reload list',
    'Live · alle 2 s': 'Live · every 2 s',
    'Lizenz': 'License',
    'Login-Zeiten benötigen root-Rechte – Klick auf „Aktualisieren“ fragt bei Bedarf einmal nach dem Passwort.': 'Login times need root rights – clicking “Refresh” asks for the password once if needed.',
    'Lokale Adressen': 'Local addresses',
    'Lokaler DNS-Dienst': 'Local DNS service',
    'lsblk konnte nicht gelesen werden.': 'lsblk could not be read.',
    'LVM-Volume': 'LVM volume',
    'lädt': 'charging',
    'lädt nicht': 'not charging',
    'Läuft': 'Running',
    'läuft': 'running',
    'Läuft im Hintergrund mit den Einstellungen oben – auch wenn Tuxdex geschlossen ist. Nicht angeschlossene Ziele werden übersprungen, verpasste Termine nachgeholt. Ohne root-Rechte und ohne Passwort-Verschlüsselung.': "Runs in the background with the settings above – even when Tuxdex is closed. Targets that aren't connected are skipped, missed runs are caught up. Without root rights and without password encryption.",
    'läuft seit {} Std {} Min': 'running for {} h {} min',
    'läuft seit {} T {} Std {} Min': 'running for {} d {} h {} min',
    'Läuft …': 'Running …',
    'Läuft: {} · installiert: {} – nach einem Neustart aktiv.': 'Running: {} · installed: {} – active after a restart.',
    'Läuft: {}.': 'Running: {}.',
    'Läuft: {}. Der Kernel wurde aktualisiert – bis zum Neustart fehlen Module (z. B. für USB-Sticks) und der neue Kernel ist nicht aktiv.': "Running: {}. The kernel was updated – until a restart, modules are missing (e.g. for USB sticks) and the new kernel isn't active.",
    'Löschen': 'Delete',
    'Lüfter': 'Fans',
    'Lüfter {}': 'Fan {}',
    'Lüfter · {}': 'Fan · {}',
    'Lüfterdrehzahlen erscheinen, wenn der Treiber sie meldet (bei vielen Laptops nur mit passendem Modul, z. B. thinkpad_acpi, dell-smm-hwmon, asus-wmi oder nct6775).': 'Fan speeds appear when the driver reports them (on many laptops only with a suitable module, e.g. thinkpad_acpi, dell-smm-hwmon, asus-wmi or nct6775).',
    'MAC-Adresse': 'MAC address',
    'machen ein Zurück in Sekunden möglich – z. B. mit snapper + snap-pac oder timeshift.': 'make going back possible in seconds – e.g. with snapper + snap-pac or timeshift.',
    'Mainboard & BIOS': 'Mainboard & BIOS',
    'Mainboard unbekannt': 'Mainboard unknown',
    'makepkg (Paket pacman, Gruppe base-devel) wird benötigt.': 'makepkg (package pacman, group base-devel) is required.',
    'makepkg fehlt': 'makepkg missing',
    'manuell': 'manual',
    'Max. Geschwindigkeit': 'Max. speed',
    'Max. PCI-Express-Geschwindigkeit': 'Max. PCI Express speed',
    'Maximal (langsam)': 'Maximum (slow)',
    'Meldungen beim letzten Update': 'Messages from the last update',
    'Mesa fehlt': 'Mesa missing',
    'Misst gerade:': 'Currently measuring:',
    'Mit Daten löschen': 'Delete with data',
    'Mit dem Kill-Switch gibt es nur noch Internet, solange das VPN verbunden ist – auch wenn die App geschlossen ist.\n\nAktivieren?': "With the kill switch there's only internet while the VPN is connected – even when the app is closed.\n\nEnable?",
    'Mit Passwort verschlüsseln (AES-256, gpg)': 'Encrypt with password (AES-256, gpg)',
    'Mit root-Rechten (genauer)': 'With root rights (more accurate)',
    'Mit root-Rechten (nötig für Systemordner wie /etc)': 'With root rights (needed for system folders like /etc)',
    'Mit root-Rechten (nötig für Systemordner)': 'With root rights (needed for system folders)',
    'MIT – frei nutzbar, veränderbar und weitergebbar': 'MIT – free to use, modify and share',
    'Mitglied in:': 'Member of:',
    'Mitglied in: {}.': 'Member of: {}.',
    'mittel': 'medium',
    'Modell': 'Model',
    'Moderne, abgeschottete Fensterdarstellung.': 'Modern, isolated window display.',
    'Module für laufenden Kernel vorhanden: {}': 'Modules for the running kernel present: {}',
    'morgen': 'tomorrow',
    'Mullvad abmelden': 'Log out of Mullvad',
    'Mullvad ist installiert, aber der Hintergrunddienst <b>mullvad-daemon</b> läuft nicht.': "Mullvad is installed, but the background service <b>mullvad-daemon</b> isn't running.",
    'Mullvad ist nicht verbunden – Anbieter und Webseiten sehen deine echte IP.': 'Mullvad is not connected – providers and websites see your real IP.',
    'Mullvad ist verbunden, Kill-Switch an.': 'Mullvad is connected, kill switch on.',
    'Mullvad ist verbunden.': 'Mullvad is connected.',
    'Mullvad VPN': 'Mullvad VPN',
    'Mullvad VPN ist nicht installiert. Tuxdex funktioniert auch ohne. Sobald Mullvad auf dem System vorhanden ist, lässt es sich hier bedienen.': 'Mullvad VPN is not installed. Tuxdex also works without it. As soon as Mullvad is on the system, it can be used here.',
    'Mullvad-Befehle brauchen kein Passwort – der Mullvad-Dienst erledigt das. Mit Kill-Switch gibt es ohne VPN-Verbindung kein Internet.': "Mullvad commands don't need a password – the Mullvad service takes care of that. With the kill switch there is no internet without a VPN connection.",
    'Mullvad-Dienst starten': 'Start Mullvad service',
    'Musik': 'Music',
    'Möglich': 'Possible',
    'Nach dem Schreiben prüfen (liest das Archiv zurück und vergleicht die Prüfsumme)': 'Verify after writing (reads the archive back and compares the checksum)',
    'Nach Programm gruppieren': 'Group by program',
    'Nach Updates suchen': 'Check for updates',
    'Name der Sicherung': 'Backup name',
    'Name nur aus Buchstaben, Ziffern und _ (z. B. GDK_SCALE).': 'Name only from letters, digits and _ (e.g. GDK_SCALE).',
    'Name oder Beschreibung …': 'Name or description …',
    'Name, PID, Benutzer oder Befehl …': 'Name, PID, user or command …',
    'Namensauflösung (LLMNR)': 'Name resolution (LLMNR)',
    'Nein': 'No',
    'nein': 'no',
    'nein – Neustart nötig!': 'no – restart needed!',
    'Netz': 'Mains',
    'Netzteil': 'Power adapter',
    'Netzwerk': 'Network',
    'Netzwerk & IP': 'Network & IP',
    'Netzwerk & IP-Adressen': 'Network & IP addresses',
    'Netzwerk · {}': 'Network · {}',
    'Neu': 'New',
    'Neu einlesen': 'Rescan',
    'Neu laden': 'Reload',
    'Neu messen': 'Measure again',
    'Neu prüfen': 'Check again',
    'Neu starten': 'Restart',
    'Neue Bezeichnung': 'New label',
    'Neue Version {}.': 'New version {}.',
    'Neuer Server': 'New server',
    'Neues Laufwerk erkannt:': 'New drive detected:',
    'Neustart nötig': 'Restart needed',
    'nice +5 – Prozess bekommt weniger CPU-Zeit': 'nice +5 – process gets less CPU time',
    'nice −5 – braucht root-Rechte': 'nice −5 – needs root rights',
    'Nicht aktiv': 'Not active',
    'nicht aktiv': 'not active',
    'nicht angemeldet': 'not logged in',
    'Nicht angemeldet': 'Not logged in',
    'nicht angeschlossen': 'not connected',
    'nicht angeschlossen – wird übersprungen': 'not connected – will be skipped',
    'nicht eingehängt': 'not mounted',
    'Nicht erkannt': 'Not detected',
    'nicht ermittelbar': 'not determinable',
    'Nicht geprüft': 'Not checked',
    'Nicht gesetzt:': 'Not set:',
    'Nicht gesichert: {}.': 'Not backed up: {}.',
    'Nicht installiert': 'Not installed',
    'nicht installiert': 'not installed',
    'nicht mehr': 'no longer',
    'Nicht möglich': 'Not possible',
    'Nicht nötig': 'Not needed',
    'Nicht prüfbar': 'Not checkable',
    'Nicht sichern (ein Pfad pro Zeile)': "Don't back up (one path per line)",
    'Nicht synchron': 'Not in sync',
    'nicht unterstützt': 'not supported',
    'Nicht verschlüsselt': 'Not encrypted',
    'nicht vorhanden': 'not present',
    'Nichts ausgewählt': 'Nothing selected',
    'Nichts zu tun': 'Nothing to do',
    'niedrig': 'low',
    'Niedrig = RAM bevorzugen, hoch = früher auslagern.': 'Low = prefer RAM, high = swap out earlier.',
    'Noch': 'Left',
    'Noch kein Backup': 'No backup yet',
    'Noch kein Backup-Ziel festgelegt.': 'No backup target set yet.',
    'Noch kein Scan durchgeführt.': 'No scan performed yet.',
    'Noch kein Ziel – unten ein Laufwerk oder einen Ordner hinzufügen.': 'No target yet – add a drive or folder below.',
    'Noch keine Analyse – Ordner wählen und „Analysieren“ klicken.': 'No analysis yet – choose a folder and click “Analyze”.',
    'Noch keine Funde.': 'No findings yet.',
    'Noch nicht geprüft': 'Not checked yet',
    'Noch nicht geprüft – „Auf Updates prüfen“ klicken.': 'Not checked yet – click “Check for updates”.',
    'noch nie': 'never',
    'Noch nie': 'Never',
    'Noch ohne Fix: {}.': 'No fix yet: {}.',
    'Noch zu schreiben': 'Waiting to be written',
    'noch {}:{} h': '{}:{} h left',
    'Notfall-Treiber': 'Fallback driver',
    'NTFS (Windows)': 'NTFS (Windows)',
    'NTP einschalten': 'Turn on NTP',
    'NTP ist aus.': 'NTP is off.',
    'nur Anzeige': 'display only',
    'nur Anzeige – Programme legen hier Zwischendaten ab': 'display only – programs store temporary data here',
    'nur für dich': 'only for you',
    'nur ich': 'only me',
    'Nur lesen': 'Read only',
    'Nur meine': 'Only mine',
    'Nur signierte Bootloader/Kernel werden gestartet.': 'Only signed bootloaders/kernels are started.',
    'Nötig für manche Spiele (Steam, Wine).': 'Needed by some games (Steam, Wine).',
    'Nötig für viele X11-Programme; teilt Speicher mit dem System.': 'Needed by many X11 programs; shares memory with the system.',
    'Oben rechts <b>anmelden</b>, um zu sehen, welche Ports die Firewall durchlässt – dann kannst du sie per Knopf sperren oder freigeben.': '<b>Log in</b> at the top right to see which ports the firewall lets through – then you can block or allow them with a button.',
    'Offen': 'Open',
    'offen': 'open',
    'Offene Netzwerk-Ports': 'Open network ports',
    'Offene Ports': 'Open ports',
    'Offizielle Paketquellen (pacman)': 'Official repositories (pacman)',
    'ohne Adresse': 'without address',
    'Ohne Firewall sind sie im Netz erreichbar.': 'Without a firewall they are reachable from the network.',
    'Ohne Passwort erlaubt:': 'Allowed without password:',
    'Ohne Swap/zram beendet Linux bei vollem Speicher Programme.': 'Without swap/zram, Linux kills programs when memory is full.',
    'Ohne TRIM werden SSDs mit der Zeit langsamer.': 'Without TRIM, SSDs get slower over time.',
    'Ohne VPN gibt es kein Leck im eigentlichen Sinn: DNS geht an {}': "Without a VPN there's no leak as such: DNS goes to {}",
    'OpenGL- und Vulkan-Version zeigt Tuxdex, wenn mesa-utils (glxinfo) bzw. vulkan-tools installiert sind.': 'Tuxdex shows the OpenGL and Vulkan version when mesa-utils (glxinfo) or vulkan-tools are installed.',
    'OpenGL-Version': 'OpenGL version',
    'Optional – Tuxdex funktioniert auch ohne VPN.': 'Optional – Tuxdex also works without a VPN.',
    'optional, z. B. USB-Stick': 'optional, e.g. USB stick',
    'Optional: arch-audit gleicht die installierten Pakete mit der Arch-Sicherheitsdatenbank ab (security.archlinux.org).': 'Optional: arch-audit compares the installed packages with the Arch security database (security.archlinux.org).',
    'Optional: Mit fwupd lassen sich BIOS-, SSD- und Geräte-Firmware prüfen und aktualisieren (Paket fwupd).': 'Optional: with fwupd, BIOS, SSD and device firmware can be checked and updated (package fwupd).',
    'Optional: mit sbctl eigene Schlüssel einrichten (schützt vor manipulierten Bootloadern).': 'Optional: set up your own keys with sbctl (protects against manipulated bootloaders).',
    'Ordner': 'Folder',
    'Ordner auswählen': 'Choose folder',
    'Ordner freigeben': 'Share folder',
    'Ordner hinzufügen …': 'Add folder …',
    'Ordner kann nicht angelegt werden: {}': "Folder can't be created: {}",
    'Ordner mit PKGBUILD, z. B. dein geklonter Git-Ordner': 'Folder with PKGBUILD, e.g. your cloned Git folder',
    'Ordner mit Tuxdex (PKGBUILD) wählen': 'Choose folder with Tuxdex (PKGBUILD)',
    'Ordner sichern': 'Back up folder',
    'Ordner wählen …': 'Choose folder …',
    'Ordner zum Scannen wählen': 'Choose folder to scan',
    'Ordner öffnen': 'Open folder',
    'Ort: <code>{}</code> · Quelle: {}': 'Location: <code>{}</code> · source: {}',
    'Packen fehlgeschlagen (tar {}, Packer {}) – Details in der Ausgabe.': 'Packing failed (tar {}, packer {}) – details in the output.',
    'Packt …': 'Packing …',
    'pacman hebt jede heruntergeladene Paketversion in /var/cache/pacman/pkg auf – über Monate werden das schnell mehrere GB.\n\n„Alte Versionen löschen“ führt paccache -rk2 aus: je Paket bleiben die 2 neuesten Versionen liegen (für ein Zurückstufen, falls ein Update Probleme macht). Zusätzlich entfernt paccache -ruk0 alle Dateien von Paketen, die gar nicht mehr installiert sind. Ohne paccache (Paket pacman-contrib) nutzt Tuxdex pacman -Sc: dann bleibt nur die installierte Version im Cache.\n\nBraucht root. Installierte Programme bleiben unberührt.': 'pacman keeps every downloaded package version in /var/cache/pacman/pkg – over months that quickly adds up to several GB.\n\n“Delete old versions” runs paccache -rk2: the 2 newest versions of each package are kept (for downgrading if an update causes problems). In addition, paccache -ruk0 removes all files of packages that are no longer installed. Without paccache (package pacman-contrib), Tuxdex uses pacman -Sc: then only the installed version stays in the cache.\n\nNeeds root. Installed programs are not touched.',
    'pacman prüft die Signatur jedes Pakets.': 'pacman checks the signature of every package.',
    'Pacman-Paketcache': 'Pacman package cache',
    'Paket': 'Package',
    'Paket gebaut': 'Package built',
    'Paket-Cache: {}.': 'Package cache: {}.',
    'Paket-Installation': 'Package installation',
    'Pakete & Updates': 'Packages & updates',
    'Pakete mit Icons, Größe, Version und Datum – per Kästchen auswählen und entfernen': 'Packages with icons, size, version and date – select with checkboxes and remove',
    'Pakete, die einmal als Abhängigkeit eines anderen Programms installiert wurden, das es nicht mehr gibt (pacman -Qdtq).\n\n„Entfernen“ zeigt vorher die Liste und löscht sie dann mit pacman -Rns – samt ihrer eigenen, ebenfalls unnötigen Abhängigkeiten und Konfigurationsdateien.\n\nSelbst installierte Programme sind nie dabei. Braucht root.': 'Packages that were once installed as a dependency of another program that no longer exists (pacman -Qdtq).\n\n“Remove” shows the list first and then deletes them with pacman -Rns – including their own, also unneeded dependencies and configuration files.\n\nExplicitly installed programs are never included. Needs root.',
    'Paketliste': 'Package list',
    'Paketliste gespeichert: {}': 'Package list saved: {}',
    'Paketliste konnte nicht gespeichert werden.': 'The package list could not be saved.',
    'Paketname(n)': 'Package name(s)',
    'Paketsignaturen': 'Package signatures',
    'Papierkorb': 'Trash',
    'Papierkorb endgültig leeren?': 'Permanently empty the trash?',
    'Papierkorb leeren': 'Empty trash',
    'Partitionen (eingehängt)': 'Partitions (mounted)',
    'paru baut AUR-Pakete in ~/.cache/paru/clone und hebt Quellcode und fertige Pakete auf.\n\nZum Aufräumen im Terminal: paru -Sc (fragt nach, was gelöscht wird).': 'paru builds AUR packages in ~/.cache/paru/clone and keeps source code and built packages.\n\nTo clean up in the terminal: paru -Sc (asks what to delete).',
    'paru fehlt': 'paru missing',
    'paru ist nicht installiert.': 'paru is not installed.',
    'Passend': 'Suitable',
    'Passt den Takt der Last an – guter Standard.': 'Adjusts the clock to the load – a good default.',
    'Passwort': 'Password',
    'Passwort angefragt': 'Password requested',
    'Passwort des Archivs': 'Archive password',
    'Passwort falsch oder Authentifizierung fehlgeschlagen.': 'Wrong password or authentication failed.',
    'Passwort wiederholen': 'Repeat password',
    'Pausieren': 'Pause',
    'Pausiert': 'Paused',
    'PCI-Busadresse': 'PCI bus address',
    'PCI-Express-Geschwindigkeit': 'PCI Express speed',
    'Persönliche Daten der App (~/.var/app) können mit gelöscht werden.': "The app's personal data (~/.var/app) can be deleted as well.",
    'Persönlicher Ordner': 'Home folder',
    'Persönlicher Ordner  (~)': 'Home folder  (~)',
    'Petrol = Ordner (Doppelklick öffnet ihn), grau = einzelne Dateien. Die größten 25 Einträge werden gezeigt.': 'Teal = folders (double-click opens), grey = single files. The largest 25 entries are shown.',
    'Pfad': 'Path',
    'PKGBUILD ohne pkgver gefunden': 'PKGBUILD without pkgver found',
    'Platte': 'Disk',
    'Port freigeben': 'Allow port',
    'Port {}/{} ({})': 'Port {}/{} ({})',
    'Portal-Freigaben': 'Portal permissions',
    'Priorität erhöhen': 'Raise priority',
    'Priorität senken': 'Lower priority',
    'Priorität von {} geändert.': 'Priority of {} changed.',
    'Programm auswählen …': 'Choose program …',
    'Programmdatei': 'Program file',
    'Programme': 'Programs',
    'Programme (selbst installiert)': 'Programs (explicitly installed)',
    'Programme, die nach der Anmeldung automatisch starten. Ausschalten ist jederzeit umkehrbar – für System-Einträge legt Tuxdex nur eine eigene Einstellung in ~/.config/autostart an.': 'Programs that start automatically after login. Turning them off can always be undone – for system entries Tuxdex only creates its own setting in ~/.config/autostart.',
    'Projektseite': 'Project page',
    'Protokoll': 'Protocol',
    'Prozess beenden': 'End process',
    'Prozess erzwingen': 'Force process',
    'Prozesse': 'Processes',
    'Prozesse ({} von Programmen)': 'Processes ({} from programs)',
    'Prozesse ({} von Programmen) · läuft seit': 'Processes ({} from programs) · running for',
    'Prozesse, Leistung, Hardware- und Netzwerkinfos': 'Processes, performance, hardware and network info',
    'Prozessor': 'Processor',
    'Prozessor · {}': 'Processor · {}',
    'Prüfe Abhängigkeiten …': 'Checking dependencies …',
    'Prüfe auf Updates…': 'Checking for updates…',
    'Prüfe Dateikonflikte …': 'Checking file conflicts …',
    'Prüfe geschriebene Daten …': 'Verifying written data …',
    'Prüfe Paket …': 'Checking package …',
    'Prüfe Prüfsummen …': 'Checking checksums …',
    'Prüfe …': 'Checking …',
    'Prüfen': 'Check',
    'Prüfen (nur lesen)': 'Check (read only)',
    'Prüfen fehlgeschlagen': 'Check failed',
    'Prüfsumme stimmt nicht – Datenträger defekt?': "Checksum doesn't match – drive defective?",
    'Prüft, welche DNS-Server deine Anfragen wirklich beantworten (DNS-Leak) und ob Webseiten deine Verbindung als VPN, Proxy, Tor oder Rechenzentrum erkennen. Fragt bash.ws, ipapi.is und am.i.mullvad.net.': 'Checks which DNS servers really answer your requests (DNS leak) and whether websites detect your connection as VPN, proxy, Tor or data center. Queries bash.ws, ipapi.is and am.i.mullvad.net.',
    "PySide6 konnte nicht geladen werden. Vermutlich fehlt das Paket 'pyside6'.\n\nInstallieren mit: sudo pacman -S pyside6\n\nFehlermeldung: {}": "PySide6 could not be loaded. The package 'pyside6' is probably missing.\n\nInstall with: sudo pacman -S pyside6\n\nError message: {}",
    'Qt / PySide6': 'Qt / PySide6',
    'Quarantäne': 'Quarantine',
    'Quelle': 'Source',
    'Quelle · Ort': 'Source · location',
    'Quellen': 'Sources',
    'Quellen vorbereiten …': 'Preparing sources …',
    'Quellen: {}\nAusnahmen: {}': 'Sources: {}\nExclusions: {}',
    'Rechnername': 'Hostname',
    'Rechte zurücksetzen': 'Reset permissions',
    'Rechte ändern gilt für dich (Benutzer-Einstellung, kein Passwort nötig) und wirkt beim <b>nächsten Start</b> der App.': 'Changing permissions applies to you (user setting, no password needed) and takes effect the <b>next time</b> the app starts.',
    'reflector installieren': 'Install reflector',
    'reflector sucht die schnellsten aktuellen.': 'reflector finds the fastest current ones.',
    'reflector.timer hält die Liste aktuell.': 'reflector.timer keeps the list up to date.',
    'Regel': 'Rule',
    'Regel hinzufügen': 'Add rule',
    'Regel löschen': 'Delete rule',
    'Regel(n) {} löschen?': 'Delete rule(s) {}?',
    'Regeln anzeigen': 'Show rules',
    'Regeln, die für alle Flatpak-Apps gelten': 'Rules that apply to all Flatpak apps',
    'Registrierte Benutzer': 'Registered users',
    'Remotedesktop': 'Remote desktop',
    'renice fehlgeschlagen: {}': 'renice failed: {}',
    'Restzeit ca. {}': 'About {} left',
    'Restzeit ca. {} · fertig ca. {}{} Uhr · gesamt ca. {}': 'About {} left · done around {}{} · total about {}',
    'Restzeit mind. {} (Dateien werden noch gezählt)': 'At least {} left (files are still being counted)',
    'Revision {}.': 'Revision {}.',
    'riskant': 'risky',
    'Riskante Berechtigung': 'Risky permission',
    'Rohdaten von lsblk und USB in die Ausgabe schreiben': 'Write raw data from lsblk and USB to the output',
    'Router im lokalen Netz': 'Router in the local network',
    'rsync-Fehler (Code {}) – Details in der Ausgabe.': 'rsync error (code {}) – details in the output.',
    'Räume auf …': 'Cleaning up …',
    'Rückfrage': 'Question',
    'Sauber': 'Clean',
    'Scan abbrechen': 'Cancel scan',
    'Scan abgebrochen': 'Scan cancelled',
    'Scan läuft': 'Scan running',
    'Scan mit Fehlern beendet – Details in der Ausgabe.': 'Scan finished with errors – details in the output.',
    'Scan nach {} abgebrochen – {} Dateien geprüft.': 'Scan cancelled after {} – {} files checked.',
    'Scan starten': 'Start scan',
    'Scan-Dienst (clamd)': 'Scan service (clamd)',
    'Scan-Fehler': 'Scan errors',
    'Schadsoftware-Seiten blockieren (DNS)': 'Block malware sites (DNS)',
    'Schließen': 'Close',
    'schläft': 'sleeping',
    'Schnell': 'Fast',
    'schnell': 'fast',
    'Schreibfehler: {}': 'Write error: {}',
    'Schreibgeschwindigkeit': 'Write speed',
    'Schreibtisch': 'Desktop',
    'Schriften': 'Fonts',
    'Schutz-Status': 'Protection status',
    'Secure Boot': 'Secure Boot',
    'sehr hoch': 'very high',
    'sehr niedrig': 'very low',
    'Seit dem Start keine Fehler im System-Protokoll.': 'No errors in the system log since startup.',
    'Senden': 'Send',
    'Seriennummer': 'Serial number',
    'Server {}': 'Server {}',
    'Setzen': 'Set',
    'Shell-Verlauf': 'Shell history',
    'Sicher entfernen': 'Safely remove',
    'Sicherheit': 'Security',
    'Sicherheits-Check, offene Ports, Mullvad VPN, Firewall': 'Security check, open ports, Mullvad VPN, firewall',
    'Sie belegen Speicher und Rechenzeit. Jetzt beenden?': 'They use memory and CPU time. End them now?',
    'Signal an {} gesendet': 'Signal sent to {}',
    'Signaturen': 'Signatures',
    'Signaturen aktualisieren': 'Update signatures',
    'Signaturen aktuell': 'Signatures up to date',
    'Signaturen {} Tage alt': 'Signatures {} days old',
    'sind eingerichtet.': 'are set up.',
    'Skript auf Version {} ersetzen?\n\n{}\n(Die alte Datei bleibt als .bak erhalten.)': 'Replace the script with version {}?\n\n{}\n(The old file is kept as .bak.)',
    'Skript auf {} aktualisiert (Sicherung: {}.bak)': 'Script updated to {} (backup: {}.bak)',
    'Snapshots brauchen ein Linux-Dateisystem (ist: {}). Für dieses Ziel „Archiv“ nutzen.': 'Snapshots need a Linux file system (is: {}). Use “Archive” for this target.',
    'Snapshots nicht möglich': 'Snapshots not possible',
    'Snapshots, Spiegel und komprimierte Archive – auf mehrere Ziele gleichzeitig': 'Snapshots, mirrors and compressed archives – to several targets at once',
    'sofort stoppen': 'stop immediately',
    'Sonstige (meist verlötet)': 'Other (usually soldered)',
    'Spannung': 'Voltage',
    'Speicher': 'Storage',
    'Speicherabbilder (Core Dumps)': 'Memory dumps (core dumps)',
    'Speicherabbilder abschalten': 'Disable memory dumps',
    'Speicheradressen werden zufällig vergeben – erschwert Angriffe.': 'Memory addresses are randomized – makes attacks harder.',
    'Speicherbelegung': 'Storage usage',
    'Speichern': 'Save',
    'Speichertakt': 'Memory clock',
    'Speicherverbrauch': 'Memory usage',
    'Sperren': 'Block',
    'sperren': 'block',
    'Sperrlisten': 'Blocklists',
    'Spiegel': 'Mirror',
    'Spiegelserver': 'Mirrors',
    'Spiegelserver (Mirrors)': 'Mirrors',
    'Sprache': 'Language',
    'Sprache · Language': 'Language',
    'Tuxdex jetzt neu starten, damit die Sprache wechselt?': 'Restart Tuxdex now to switch the language?',
    'Später': 'Later',
    'SSH stoppen': 'Stop SSH',
    'SSH-Schlüssel': 'SSH keys',
    'SSH-Server': 'SSH server',
    'SSH-Server aktiv': 'SSH server active',
    'SSH-Server stoppen und nicht mehr automatisch starten?\nLaufende Fernverbindungen werden getrennt.': 'Stop the SSH server and no longer start it automatically?\nRunning remote connections will be disconnected.',
    'Stadt': 'City',
    'Standard übernehmen': 'Use default',
    'Standard-Gateway: {}   ·   DNS-Server: {}': 'Default gateway: {}   ·   DNS servers: {}',
    'Standort übernehmen': 'Apply location',
    'stark': 'strong',
    'Start fehlgeschlagen: {}': 'Start failed: {}',
    'Starten': 'Start',
    'startet': 'starting',
    'Startet …': 'Starting …',
    'Startet … Signaturen werden geladen': 'Starting … loading signatures',
    'Status aktualisieren': 'Refresh status',
    'Status neu prüfen': 'Check status again',
    'Steckplätze verwendet': 'Slots used',
    'Stelle Paketinhalt zusammen …': 'Assembling package contents …',
    'Stoppen': 'Stop',
    'Stromsparend – bei amd-pstate/intel_pstate trotzdem voll schnell, der Energiemodus entscheidet.': 'Power saving – with amd-pstate/intel_pstate still full speed, the energy mode decides.',
    'Stromversorgung': 'Power supply',
    'Stärke': 'Level',
    'Suche': 'Search',
    'Suche neuesten Stand …': 'Looking for the latest version …',
    'sudo fragt immer nach dem Passwort.': 'sudo always asks for the password.',
    'sudo ohne Passwort (NOPASSWD)': 'sudo without password (NOPASSWD)',
    'sudo-Passwort eingeben': 'Enter sudo password',
    'Sudo-Sitzung beendet.': 'Sudo session ended.',
    'Summen seit dem Systemstart.': 'Totals since system start.',
    'Swap & Swappiness': 'Swap & swappiness',
    'Swap deaktivieren': 'Disable swap',
    'Swap liegt verschlüsselt oder im RAM (zram):': 'Swap is encrypted or in RAM (zram):',
    'Swap {} deaktivieren?': 'Disable swap {}?',
    'Swap-Verschlüsselung': 'Swap encryption',
    'Swapfile anlegen / ersetzen': 'Create / replace swap file',
    'Swapfile {} mit {} GB anlegen bzw. ersetzen und in /etc/fstab eintragen?': 'Create or replace swap file {} with {} GB and add it to /etc/fstab?',
    'Swappiness (0–200): je höher, desto früher lagert Linux ungenutzten Speicher aus.': 'Swappiness (0–200): the higher, the earlier Linux swaps out unused memory.',
    'Swappiness setzen (dauerhaft)': 'Set swappiness (permanently)',
    'Swappiness {}': 'Swappiness {}',
    'Synchron': 'In sync',
    'System & Entwicklung': 'System & development',
    'System & Lüfter': 'System & fans',
    'System (Dienste)': 'System (services)',
    'System aktuell': 'System up to date',
    'System jetzt aktualisieren?': 'Update the system now?',
    'System-Datenträger – Aushängen, Umbenennen und Formatieren sind gesperrt.': 'System drive – unmounting, renaming and formatting are locked.',
    'System-Logs (Journal)': 'System logs (journal)',
    'System-Logs auf 200 MB kürzen?': 'Shrink system logs to 200 MB?',
    'System-Protokoll (journald)': 'System log (journald)',
    'System-Protokoll begrenzen': 'Limit system log',
    'System-Snapshots': 'System snapshots',
    'System-Snapshots brauchen Btrfs (hier: {}). Die Tuxdex-Backups decken das über Snapshots auf einem Ziel ab.': 'System snapshots need Btrfs (here: {}). The Tuxdex backups cover this via snapshots on a target.',
    'System-Update': 'System update',
    'System-Updates': 'System updates',
    'System: {} frei ({} %)': 'System: {} free ({} %)',
    'systemd-analyze ist nicht verfügbar.': 'systemd-analyze is not available.',
    'Systemdateien': 'System files',
    'Systemdatenträger': 'System drive',
    'Systemeinstellungen (/etc)': 'System settings (/etc)',
    'systemweit': 'system-wide',
    'Tailscale an': 'Tailscale on',
    'Tailscale ist verbunden (privates Netz zwischen deinen Geräten). Ohne Exit-Node läuft der Internetverkehr direkt, nicht über ein VPN.': 'Tailscale is connected (private network between your devices). Without an exit node, internet traffic goes directly, not through a VPN.',
    'Tailscale ist verbunden – dein Internetverkehr läuft über den Exit-Node „{}“.': 'Tailscale is connected – your internet traffic goes through the exit node “{}”.',
    'Tailscale ist {}.': 'Tailscale is {}.',
    'Takt und Steckplätze meldet dieses System nicht (udev-DMI-Daten fehlen).': "This system doesn't report speed and slots (udev DMI data missing).",
    'Taktgeschwindigkeit': 'Clock speed',
    'Taskmanager': 'Task manager',
    'Technik': 'Technology',
    'Technologie': 'Technology',
    'Telemetrie': 'Telemetry',
    'Telemetrie abschalten': 'Disable telemetry',
    'Telemetrie aktiv in:': 'Telemetry active in:',
    'Telemetrie in Editoren': 'Telemetry in editors',
    'Temperatur': 'Temperature',
    'Temperatur · {}': 'Temperature · {}',
    'Tempo': 'Speed',
    'Test starten': 'Start test',
    'Teste …': 'Testing …',
    'timedatectl meldet keinen Zeitabgleich (kein systemd-timesyncd?).': 'timedatectl reports no time sync (no systemd-timesyncd?).',
    'Tipp: HISTCONTROL=ignorespace in ~/.bashrc – Befehle mit Leerzeichen davor landen nicht im Verlauf.': "Tip: HISTCONTROL=ignorespace in ~/.bashrc – commands with a leading space don't end up in the history.",
    'Tipp: Kill-Switch (Lockdown) einschalten – dann geht auch bei einem Verbindungsabbruch nichts am Tunnel vorbei.': 'Tip: turn on the kill switch (lockdown) – then nothing bypasses the tunnel even if the connection drops.',
    'Tipp: „Automatische Updates aktivieren“ – der Dienst lädt die Signaturen dann selbstständig und täglich.': 'Tip: “Enable automatic updates” – the service then loads the signatures by itself daily.',
    'Ton & Mikrofon': 'Sound & microphone',
    'Tracker blockieren (DNS)': 'Block trackers (DNS)',
    'Treiber': 'Driver',
    'Treiber ok': 'Driver ok',
    'Trennen': 'Disconnect',
    'Trennt gemeinsamen Speicher zwischen Apps.': 'Separates shared memory between apps.',
    'TRIM für SSDs': 'TRIM for SSDs',
    'Turbo / Boost': 'Turbo / boost',
    'Tuxdex (*.tar.gz *.tgz *.py);;Alle Dateien (*)': 'Tuxdex (*.tar.gz *.tgz *.py);;All files (*)',
    'Tuxdex - Fehler': 'Tuxdex - error',
    'Tuxdex - Start fehlgeschlagen': 'Tuxdex - start failed',
    'Tuxdex auf Version {} aktualisieren?\n\nQuelle: github.com/{} ({})': 'Update Tuxdex to version {}?\n\nSource: github.com/{} ({})',
    'Tuxdex beenden': 'Quit Tuxdex',
    'Tuxdex ist als Paket installiert – bitte das Archiv (tuxdex-X.Y.Z.tar.gz) oder den Ordner mit PKGBUILD wählen.': 'Tuxdex is installed as a package – please choose the archive (tuxdex-X.Y.Z.tar.gz) or the folder with the PKGBUILD.',
    'Tuxdex ist in der <b>Alpha-Phase</b>. Aktionen mit Administrator-Rechten (root) ändern dein System direkt – z. B. Pakete, Datenträger, Swap, Firewall, Systemdateien. Trotz Rückfragen und Prüfungen können Fehler passieren, bis hin zu Datenverlust oder einem System, das nicht mehr startet.<br><br><b>Nutzung auf eigenes Risiko.</b> Es gibt keine Gewährleistung (MIT-Lizenz). Lege vorher ein Backup an und lies bei jeder Rückfrage, welcher Befehl ausgeführt wird – er steht immer im Ausgabefeld.': 'Tuxdex is in the <b>alpha stage</b>. Actions with administrator rights (root) change your system directly – e.g. packages, drives, swap, firewall, system files. Despite confirmations and checks, errors can happen, up to data loss or a system that no longer boots.<br><br><b>Use at your own risk.</b> There is no warranty (MIT license). Make a backup first and read which command will run at every confirmation – it is always shown in the output box.',
    'Tuxdex jetzt neu starten?': 'Restart Tuxdex now?',
    'Tuxdex {} aus {} installieren?': 'Install Tuxdex {} from {}?',
    'Tuxdex-Update wählen': 'Choose Tuxdex update',
    'tuxdex-X.Y.Z.tar.gz oder (bei Skript-Start) eine tuxdex.py': 'tuxdex-X.Y.Z.tar.gz or (when started as a script) a tuxdex.py',
    'Typ': 'Type',
    'Typische Platzfresser & Aufräumen': 'Typical space hogs & cleanup',
    'Täglich': 'Daily',
    'täglich': 'daily',
    'udisks hat abgelehnt – versuche es mit sudo …': 'udisks refused – trying with sudo …',
    'udisksctl ist nicht installiert.\n\nsudo pacman -S udisks2': 'udisksctl is not installed.\n\nsudo pacman -S udisks2',
    'ufw installieren': 'Install ufw',
    'Uhrzeit (NTP)': 'Time (NTP)',
    'Umbenennen': 'Rename',
    'Umfang wird berechnet …': 'Calculating size …',
    'Umfang: {}': 'Size: {}',
    'Umgebungsvariable': 'Environment variable',
    'Umgebungsvariablen': 'Environment variables',
    'Unauffällig': 'Unremarkable',
    'Unbegrenzt': 'Unlimited',
    'Unbekannt': 'Unknown',
    'unbekannt': 'unknown',
    'unbekanntes Datum: {}': 'unknown date: {}',
    'Unbenutzte entfernen': 'Remove unused',
    'unerwartete Antwort von bash.ws': 'unexpected response from bash.ws',
    'unerwarteter Dateiname im PKGBUILD: {}': 'unexpected file name in PKGBUILD: {}',
    'Ungenutzte Flatpak-Laufzeiten entfernen?': 'Remove unused Flatpak runtimes?',
    'Ungenutzte Laufzeiten entfernen': 'Remove unused runtimes',
    'Ungespeicherte Daten gehen verloren.': 'Unsaved data will be lost.',
    'Ungewöhnlich:': 'Unusual:',
    'Ungültige Bezeichnung': 'Invalid label',
    'Ungültige Eingabe': 'Invalid input',
    'ungültiger Zielpfad oder am Zielort existiert bereits eine Datei': 'invalid target path or a file already exists at the target',
    'Ungültiges Ziel': 'Invalid target',
    'unsicherer Eintrag im Archiv: {}': 'unsafe entry in archive: {}',
    'unter 1 Min': 'under 1 min',
    'Unternehmen': 'Company',
    'Unverschlüsselt': 'Unencrypted',
    'Update abbrechen': 'Cancel update',
    'Update abgebrochen.': 'Update cancelled.',
    'Update abgeschlossen.': 'Update finished.',
    'Update da': 'Update available',
    'Update läuft': 'Update running',
    'Update läuft … Rückfragen erscheinen als Fenster.': 'Update running … questions appear as windows.',
    'Update starten': 'Start update',
    'Update verfügbar': 'Update available',
    'Update-Informationen': 'Update information',
    'Update-Quelle gespeichert.': 'Update source saved.',
    'Updates prüfen und einspielen (pacman, AUR, Flatpak), Major-Updates erkennen': 'Check and install updates (pacman, AUR, Flatpak), detect major updates',
    'Updates schließen Lücken in: {}.': 'Updates close vulnerabilities in: {}.',
    'URSPRÜNGLICHER ORT': 'ORIGINAL LOCATION',
    'USB-Massenspeicher (USB-Ebene):': 'USB mass storage (USB level):',
    'USB-Schutz (usbguard)': 'USB protection (usbguard)',
    'usbguard blockiert unbekannte USB-Geräte (Schutz gegen manipulierte Sticks). Nur bei physischem Zugriff Fremder sinnvoll.': 'usbguard blocks unknown USB devices (protection against manipulated sticks). Only useful if strangers have physical access.',
    'usbguard ist installiert, der Dienst läuft nicht.': "usbguard is installed, the service isn't running.",
    'usbguard läuft.': 'usbguard is running.',
    'Veraltet': 'Outdated',
    'Verbinde …': 'Connecting …',
    'Verbinden': 'Connect',
    'Verbindungsgeschwindigkeit': 'Link speed',
    'Verbunden': 'Connected',
    'Verfügbar': 'Available',
    'Verfügbare Updates': 'Available updates',
    'Verfügbarer Swap': 'Swap available',
    'Verlauf der letzten {} s · aktuell {}': 'History of the last {} s · current {}',
    'Verschlüsselt': 'Encrypted',
    'Verschlüsselt (geöffnet)': 'Encrypted (open)',
    'Verschlüsselte Archive brauchen das Passwort – das wird nicht gespeichert. Für den Zeitplan Snapshots oder unverschlüsselte Archive verwenden.': "Encrypted archives need the password – it isn't saved. Use snapshots or unencrypted archives for the schedule.",
    'Verschlüsselte Archive brauchen ein Passwort und laufen nur aus dem Fenster.': 'Encrypted archives need a password and only run from the window.',
    'Verschlüsselte Partition – bitte mit cryptsetup/Dateimanager entsperren.': 'Encrypted partition – please unlock with cryptsetup/file manager.',
    'Version {}': 'Version {}',
    'Version {}  ·  {}': 'Version {}  ·  {}',
    'Version {} verfügbar': 'Version {} available',
    'Version {} · Zweig {} · Quelle {} · {} installiert · {}': 'Version {} · branch {} · source {} · {} installed · {}',
    'Versionen behalten': 'Keep versions',
    'Versionsstand – Treiber, Microcode, BIOS': 'Version status – drivers, microcode, BIOS',
    'Verwaiste Pakete': 'Orphaned packages',
    'Verwaiste Pakete & Paket-Cache': 'Orphaned packages & package cache',
    'Verwalten': 'Manage',
    'Verwendeter Swap': 'Swap used',
    'Video dekodieren': 'Video decode',
    'Video kodieren': 'Video encode',
    'Virtualisierung': 'Virtualization',
    'Virtualisierung (KVM)': 'Virtualization (KVM)',
    'Virtuell': 'Virtual',
    'Virtuelle Maschine': 'Virtual machine',
    'Virtuelle Maschine oder unbekannte CPU – der Host lädt den Microcode.': 'Virtual machine or unknown CPU – the host loads the microcode.',
    'Virtuelle Maschine – der Host liefert den Microcode.': 'Virtual machine – the host provides the microcode.',
    'Virtuelle Prozessoren': 'Virtual processors',
    'Voll': 'Full',
    'voll': 'full',
    'voll in': 'full in',
    'Voll in': 'Full in',
    'voll in {}:{} h': 'full in {}:{} h',
    'Stabil': 'Stable',
    'Stabile Version ausgewählt.': 'Stable version selected.',
    'Vollzugriff auf das ganze Dateisystem – hebt die Abschottung weitgehend auf.': 'Full access to the entire file system – largely removes the isolation.',
    'vom System': 'from the system',
    'vom System · angepasst': 'from the system · customized',
    'von dir': 'by you',
    'vor {} Min.': '{} min ago',
    'vor {} Std.': '{} h ago',
    'vor {} Tagen': '{} days ago',
    'Vorbereiten': 'Preparing',
    'Vorbereiten …': 'Preparing …',
    'Vorgang abbrechen': 'Cancel operation',
    'Vorhandene Backups & Wiederherstellen': 'Existing backups & restore',
    'Vorsicht': 'Caution',
    'VPN-Verbindung aktiv: {}.': 'VPN connection active: {}.',
    'VS Code / VSCodium senden keine Telemetrie (oder sind nicht installiert). Browser-Telemetrie bitte in dessen Einstellungen prüfen.': "VS Code / VSCodium don't send telemetry (or aren't installed). Please check browser telemetry in its settings.",
    'Vulkan-Version': 'Vulkan version',
    'Wahrscheinlich eine große Datei oder ein Archiv nach:': 'Probably a large file or archive after:',
    'Wartet': 'Waiting',
    'wartet (E/A)': 'waiting (I/O)',
    'wartet auf Freigabe': 'waiting for approval',
    'Wartet beim Start aufs Netzwerk – auf Desktops meist unnötig.': 'Waits for the network at startup – usually unnecessary on desktops.',
    'Was belegt den Platz?': 'What is using the space?',
    'Was du hier einstellst, gilt für jede Flatpak-App – einzelne Apps können es wieder überschreiben.': 'What you set here applies to every Flatpak app – individual apps can override it.',
    'Was scannen?': 'What to scan?',
    'Was sichern?': 'What to back up?',
    'Wayland-Fenster': 'Wayland windows',
    'Webcam, Controller, USB-Geräte usw. – sehr weitreichend.': 'Webcam, controllers, USB devices etc. – very far-reaching.',
    'Webserver': 'Web server',
    'Webserver (HTTPS)': 'Web server (HTTPS)',
    'Wechseldatenträger': 'Removable',
    'Weder ufw noch firewalld gefunden.': 'Neither ufw nor firewalld found.',
    'Weicht von der Voreinstellung der App ab': "Differs from the app's default",
    'Weitere Punkte für ein gepflegtes Arch-System. Grün passt, Gelb lohnt einen Blick, Grau ist optional oder nur ein Hinweis. Knöpfe ändern nur, was dabeisteht.': "More points for a well-maintained Arch system. Green is fine, yellow is worth a look, grey is optional or just a note. Buttons only change what's written next to them.",
    'Werbung blockieren (DNS)': 'Block ads (DNS)',
    'Werkzeug fehlt': 'Tool missing',
    'Wert': 'Value',
    'Wert {}': 'Value {}',
    'Wichtige offen': 'Important pending',
    'Wie?': 'How?',
    'Wiedergabe und Aufnahme über PulseAudio/PipeWire.': 'Playback and recording via PulseAudio/PipeWire.',
    'Wiederhergestellt nach {}': 'Restored to {}',
    'Wiederhergestellt: {}': 'Restored: {}',
    'Wiederhergestellt_{}': 'Restored_{}',
    'Wiederherstellen': 'Restore',
    'Wiederherstellen einmal ausprobieren, bevor es ernst wird.': 'Try a restore once before it matters.',
    'Wiederherstellen nach …': 'Restore to …',
    'Wird abgebrochen …': 'Cancelling …',
    'Wird benötigt von:': 'Required by:',
    'wird berechnet …': 'calculating …',
    'Wird ermittelt …': 'Determining …',
    'Wird für die Dauer des Programmlaufs gemerkt – du musst es danach nicht erneut eingeben.': "Remembered while the program is running – you won't have to enter it again.",
    'Wird gemessen …': 'Measuring …',
    'Wirkt nach einem Neustart von Tuxdex.': 'Takes effect after restarting Tuxdex.',
    'WOFÜR': 'FOR',
    'Woher': 'From',
    'Wohin? – alle angehakten Ziele werden gleichzeitig beschrieben': 'Where to? – all checked targets are written at the same time',
    'Wähle oben ein Laufwerk oder eine Partition aus.': 'Select a drive or partition above.',
    'Wöchentlich': 'Weekly',
    'wöchentlich': 'weekly',
    'X11 nur als Ausweichlösung': 'X11 only as fallback',
    'X11 nur benutzen, wenn kein Wayland läuft.': "Use X11 only when Wayland isn't running.",
    'X11-Fenster': 'X11 windows',
    'xfs (Linux)': 'xfs (Linux)',
    'xz – am kleinsten, langsam': 'xz – smallest, slow',
    'z. B. 22 oder 8000:8100': 'e.g. 22 or 8000:8100',
    'z. B. Debugger (ptrace) erlauben.': 'e.g. allow debuggers (ptrace).',
    'z. B. firefox htop  ·  bei Flatpak: org.gimp.GIMP': 'e.g. firefox htop  ·  for Flatpak: org.gimp.GIMP',
    'z. B. ~/Spiele oder /mnt/daten': 'e.g. ~/Games or /mnt/data',
    'Zeilen, die nach Passwort oder Token aussehen, in:': 'Lines that look like a password or token, in:',
    'Zeitplan': 'Schedule',
    'Zeitplan aktiv': 'Schedule active',
    'Zeitplan aus': 'Schedule off',
    'Zeitplan:': 'Schedule:',
    'Ziel ist voll': 'Target is full',
    'Zombie': 'Zombie',
    'zstd – schnell, gut (empfohlen)': 'zstd – fast, good (recommended)',
    'Zu den Updates': 'Go to updates',
    'zu viele Dateien': 'too many files',
    'Zugesichert': 'Committed',
    'Zugriff': 'Access',
    'Zugriff auf alle deine Dateien im Home-Ordner.': 'Access to all your files in the home folder.',
    'Zugriff auf den Druckdienst.': 'Access to the printing service.',
    'Zugriff auf die Konfiguration des Systems.': 'Access to the system configuration.',
    'Zuletzt geprüft': 'Last checked',
    'Zuletzt geprüft: {}': 'Last checked: {}',
    'Zum Aktualisieren wird das Paket mit pacman installiert – dafür ist das sudo-Passwort nötig. Ohne Anmeldung wird nichts heruntergeladen oder verändert.': 'To update, the package is installed with pacman – this needs the sudo password. Without logging in, nothing is downloaded or changed.',
    'Zum Aufräumen': 'Go to cleanup',
    'Zum Backup': 'Go to backup',
    'Zum Formatieren eine Partition auswählen.': 'Select a partition to format.',
    'Zum Formatieren oder Prüfen erst aushängen.': 'Unmount first to format or check.',
    'Zum Swap': 'Go to swap',
    'Zur Bestätigung den Gerätenamen eintippen': 'Type the device name to confirm',
    'Zur stabilen Version {} wechseln': 'Switch to stable version {}',
    'Zurücksetzen': 'Reset',
    'Zurückspielen': 'Restore',
    'Zustand': 'Health',
    'Zustand = heutige volle Kapazität im Vergleich zum Neuzustand.': "Health = today's full capacity compared to new.",
    'Zustand {} % der Originalkapazität': 'Health {} % of original capacity',
    'Zweig {}': 'Branch {}',
    'Zwischenspeicher': 'Cache',
    'zähle Dateien …': 'counting files …',
    '{}\n\n(Details auch in {})': '{}\n\n(Details also in {})',
    '{}\n{} eigene Einstellung(en)': '{}\n{} own setting(s)',
    '{}  (Version {})': '{}  (version {})',
    '{}  ({} belegt)': '{}  ({} used)',
    '{}  {} System-Update{}': '{}  {} system update{}',
    '{}  ·  {}  ·  {} frei': '{}  ·  {}  ·  {} free',
    '{}  ·  {} Kerne / {} Threads': '{}  ·  {} cores / {} threads',
    '{} (benötigt von {})': '{} (required by {})',
    '{} alte{} Vorgang/Vorgänge beendet.': '{} old operation(s) ended.',
    '{} Apps · {}': '{} apps · {}',
    '{} aus dem Autostart entfernen?': 'Remove {} from autostart?',
    '{} ausgewählt · {} werden frei': '{} selected · {} will be freed',
    '{} aushängen und ausschalten?': 'Unmount and power off {}?',
    '{} Bedrohung{} gefunden': '{} threat{} found',
    '{} behebbar': '{} fixable',
    '{} beim Start nicht mehr ausführen?\n\n{}\nRückgängig: sudo systemctl enable {}': 'Stop running {} at startup?\n\n{}\nUndo: sudo systemctl enable {}',
    '{} Datei(en) aus der Quarantäne unwiderruflich löschen?': 'Permanently delete {} file(s) from the quarantine?',
    '{} Dateien/s · {}/s': '{} files/s · {}/s',
    '{} deaktiviert – wirkt beim nächsten Start.': '{} disabled – takes effect at the next boot.',
    '{} deinstallieren?': 'Uninstall {}?',
    '{} Dienst(e)': '{} service(s)',
    '{} Dumps': '{} dumps',
    '{} enthält Kommentare oder ist ungültig – bitte im Editor unter Einstellungen → Telemetry selbst auf „off“ stellen.': '{} contains comments or is invalid – please set it to “off” yourself in the editor under Settings → Telemetry.',
    '{} enthält Version {} – installiert ist {}. Trotzdem neu installieren?': '{} contains version {} – installed is {}. Reinstall anyway?',
    '{} existiert schon und ist kein Swapfile. Tuxdex überschreibt keine anderen Dateien – bitte einen anderen Namen wählen.': "{} already exists and is not a swap file. Tuxdex doesn't overwrite other files – please choose a different name.",
    '{} fehlt – bekannte CPU-Sicherheitslücken bleiben offen. Wirkt nach dem nächsten Neustart.': '{} is missing – known CPU vulnerabilities stay open. Takes effect after the next restart.',
    '{} für Verbindungen aus dem Netz freigeben?\n\nNur tun, wenn andere Geräte diesen Dienst erreichen sollen.': 'Allow {} for connections from the network?\n\nOnly do this if other devices should reach this service.',
    '{} GB RAM – mit 10–20 bleibt mehr im schnellen Arbeitsspeicher.': '{} GB RAM – with 10–20 more stays in fast memory.',
    '{} GHz': '{} GHz',
    '{} gibt es nicht.': "{} doesn't exist.",
    '{} Hinweise': '{} notice(s)',
    '{} Hinweis{}': '{} notice{}',
    '{} ist eine Verknüpfung – bitte den echten Pfad angeben.': '{} is a link – please enter the real path.',
    '{} ist eingehängt ({})': '{} is mounted ({})',
    '{} ist geöffnet/aktiv ({})': '{} is open/active ({})',
    '{} ist keine normale Datei.': '{} is not a regular file.',
    '{} ist unverschlüsselt – Passwörter und Schlüssel aus dem RAM können dort lesbar auf der Platte landen. Abhilfe: Swap-Partition entfernen und ein Swapfile auf der verschlüsselten Systempartition anlegen.': '{} is unencrypted – passwords and keys from RAM can end up readable on disk there. Fix: remove the swap partition and create a swap file on the encrypted system partition.',
    '{} Jahre alt': '{} years old',
    '{} kann jetzt entfernt werden.': '{} can now be removed.',
    '{} kann nur ausgehängt umbenannt werden.': '{} can only be renamed when unmounted.',
    '{} konnte nicht geprüft werden (lsblk).': '{} could not be checked (lsblk).',
    '{} Laufwerk{}': '{} drive{}',
    '{} läuft': '{} running',
    '{} läuft nur mit dem einfachen Bildschirmtreiber {} – keine Beschleunigung.': '{} only runs with the basic display driver {} – no acceleration.',
    '{} Mbit/s': '{} Mbit/s',
    '{} Meldungen': '{} messages',
    '{} Min': '{} min',
    '{} min {} s': '{} min {} s',
    '{} Monate alt': '{} months old',
    '{} ms': '{} ms',
    '{} nicht mehr automatisch starten und jetzt stoppen?': 'Stop starting {} automatically and stop it now?',
    '{} offen': '{} open',
    '{} ohne Fix': '{} without fix',
    '{} Ordner': '{} folders',
    '{} Paket(e) deinstallieren? ({})': 'Uninstall {} package(s)? ({})',
    '{} Pakete · {}': '{} packages · {}',
    '{} Pakete, die nichts mehr braucht.': '{} packages nothing needs anymore.',
    '{} Prozesse': '{} processes',
    '{} Regel(n)': '{} rule(s)',
    '{} s': '{} s',
    '{} schließt CPU-Sicherheitslücken (z. B. Spectre).': '{} closes CPU vulnerabilities (e.g. Spectre).',
    '{} Server': '{} servers',
    '{} startet jetzt automatisch.': '{} now starts automatically.',
    '{} startet {} automatisch.': '{} starts {} automatically.',
    '{} Std {} Min': '{} h {} min',
    '{} System-Update · {} wichtig': '{} system update · {} important',
    '{} System-Updates': '{} system updates',
    '{} System-Updates · {} wichtig': '{} system updates · {} important',
    '{} Tage alt': '{} days old',
    '{} Threads': '{} threads',
    '{} Treffer': '{} hits',
    '{} Updates': '{} updates',
    '{} Updates offen, {} wichtig.': '{} updates pending, {} important.',
    '{} Updates offen.': '{} updates pending.',
    '{} Updates verfügbar': '{} updates available',
    '{} Updates · {} wichtig': '{} updates · {} important',
    '{} verwaist': '{} orphaned',
    '{} vom {} – wird bei jedem Backup erneuert. Neu installieren: pacman -S --needed - < pakete.txt': '{} from {} – renewed with every backup. Reinstall with: pacman -S --needed - < pakete.txt',
    '{} von {}': '{} of {}',
    '{} von {} Zielen erfolgreich · {} · Dauer {}:{}:{}': '{} of {} targets successful · {} · duration {}:{}:{}',
    '{} Wh': '{} Wh',
    '{} wird als Swap benutzt': '{} is used as swap',
    '{} wird gestartet …': 'Starting {} …',
    '{} wird {} …': '{} is {} …',
    '{} wurde nicht gefunden.': '{} was not found.',
    '{} · gestartet von {}': '{} · started by {}',
    '{} · höchstens {} Zeichen (Buchstaben, Ziffern, Leerzeichen, _ . -)': '{} · at most {} characters (letters, digits, spaces, _ . -)',
    '{} · Kernel-Treiber {} ({})': '{} · kernel driver {} ({})',
    '{} · NVIDIA-Treiber {}': '{} · NVIDIA driver {}',
    '{} · PID {} · {} · nice {}': '{} · PID {} · {} · nice {}',
    '{} · Priorität {}': '{} · priority {}',
    '{} · Revision {}': '{} · revision {}',
    '{} · Zweig {}': '{} · branch {}',
    '{} · {} frei': '{} · {} free',
    '{} · {} Prozesse · {}': '{} · {} processes · {}',
    '{} · {} {} verfügbar.': '{} · {} {} available.',
    '{} über {}': '{} via {}',
    '{} – alle {} Prozesse': '{} – all {} processes',
    '{}. Sieht aus wie ein normaler Internetanschluss': '{}. Looks like a normal internet connection',
    '{}. Webseiten erkennen: {}. Manche Dienste (Streaming, Banken) sperren oder fragen dann nach.': '{}. Websites detect: {}. Some services (streaming, banks) then block or ask for verification.',
    '{}:{} h': '{}:{} h',
    '·  Gerät „{}“': '·  device “{}”',
    '·  gültig bis {}': '·  valid until {}',
    '·  Legacy-BIOS': '·  legacy BIOS',
    '·  {} Regel{}': '·  {} rule{}',
    '· Anmeldebildschirm nach {} Systemzeit': '· login screen after {} system time',
    '· Auslastung vom Treiber nicht gemeldet': '· load not reported by the driver',
    '· beim Hersteller nach einem neueren BIOS schauen (Sicherheits- und Stabilitätsfixes).': '· check the manufacturer for a newer BIOS (security and stability fixes).',
    '· einige Dateien nicht lesbar (ggf. mit root-Rechten sichern)': '· some files not readable (back up with root rights if needed)',
    '· einige Dateien verschwanden während des Backups': '· some files disappeared during the backup',
    '· FAT32: Archiv wird in 4-GB-Teile geteilt': '· FAT32: archive is split into 4 GB parts',
    '· geprüft': '· verified',
    '· geschrieben (komprimiert): {}': '· written (compressed): {}',
    '· installiert ist schon {} – nach einem Neustart aktiv.': '· {} is already installed – active after a restart.',
    '· Mesa ist nicht installiert (keine 3D-Beschleunigung).': '· Mesa is not installed (no 3D acceleration).',
    '· Mesa {}': '· Mesa {}',
    '· Mesa/Vulkan {} verfügbar.': '· Mesa/Vulkan {} available.',
    '· neue Version {} verfügbar.': '· new version {} available.',
    '· Neustart': '· restart',
    '· ohne Linux-Rechte (Besitzer/Rechte gehen verloren)': '· without Linux permissions (owner/permissions are lost)',
    '· Passwort-Anmeldung erlaubt (Schlüssel sind sicherer)': '· password login allowed (keys are safer)',
    '· root-Anmeldung erlaubt': '· root login allowed',
    '· seit {}': '· since {}',
    '· Vulkan {}': '· Vulkan {}',
    '· {} % der Originalgröße': '· {} % of the original size',
    '· {} ist nicht installiert – nur der Stand aus dem BIOS ist aktiv. Im Tab Sicherheit installierbar.': '· {} is not installed – only the BIOS version is active. Can be installed in the Security tab.',
    '· {} Teile': '· {} parts',
    '· {} Updates offen': '· {} updates pending',
    '· {} wichtig': '· {} important',
    '· {} {} → {} verfügbar.': '· {} {} → {} available.',
    '· ⚠ keine Snapshots auf diesem Dateisystem – „Archiv“ wählen': '· ⚠ no snapshots on this file system – choose “Archive”',
    '· 🔒 verschlüsselt': '· 🔒 encrypted',
    'Ältere Fensterdarstellung – X11-Programme können Tastatur und Bildschirm anderer Programme mitlesen.': 'Older window display – X11 programs can read keyboard input and the screen of other programs.',
    'Öffentliche IP konnte nicht ermittelt werden ({}).': 'Public IP could not be determined ({}).',
    'Öffentliche IP prüfen': 'Check public IP',
    'Öffentliche IP wird geprüft …': 'Checking public IP …',
    'Öffentliche IP: <b>{}</b> · {} {} · {} ·': 'Public IP: <b>{}</b> · {} {} · {} ·',
    'Öffentliche IP: <b>{}</b> · {} {} · {}<br>': 'Public IP: <b>{}</b> · {} {} · {}<br>',
    'Öffentliche IP: noch nicht geprüft': 'Public IP: not checked yet',
    'Öffentliche IP: noch nicht geprüft (Abfrage über am.i.mullvad.net)': 'Public IP: not checked yet (queried via am.i.mullvad.net)',
    'Öffnen': 'Open',
    'Ø Antwortzeit': 'Avg. response time',
    'Über das Projekt': 'About the project',
    'Über VPN': 'Via VPN',
    'über {}': 'via {}',
    'Übernehmen': 'Apply',
    'Übersicht': 'Overview',
    'Übersprungen (nicht angeschlossen): {}': 'Skipped (not connected): {}',
    'Übrig von entfernten Kernels:': 'Left over from removed kernels:',
    '– Abbrechen und den Ordner ausschließen oder erneut versuchen.': '– cancel and exclude the folder or try again.',
    '– bei zram ist ein hoher Wert richtig.': '– with zram a high value is correct.',
    '– bitte neu starten.': '– please restart.',
    '– das VPN wird nicht als solches erkannt.': "– the VPN isn't recognized as such.",
    '– dein Netzbetreiber kann die aufgerufenen Seiten sehen.': '– your network operator can see the sites you visit.',
    '– große Ordner mit vielen Dateien brauchen etwas.': '– large folders with many files take a while.',
    '– startet aber nicht automatisch.': "– but doesn't start automatically.",
    '– Stick abziehen und neu einstecken, sonst „Diagnose“ klicken.': '– unplug and replug the stick, otherwise click “Diagnosis”.',
    '„Im Cache“ ist Speicher für zuletzt gelesene Dateien – er wird sofort freigegeben, wenn Programme ihn brauchen. „Zugesichert“ ist, was Programme angefordert haben (auch ungenutzt).': '“Cached” is memory for recently read files – it is released immediately when programs need it. “Committed” is what programs have requested (even if unused).',
    '„Signaturen aktualisieren“ klicken.</span>': 'click “Update signatures”.</span>',
    '„Update da“ stützt sich auf die letzte Prüfung im Tab Updates.': '“Update available” is based on the last check in the Updates tab.',
    '„{}“ erlauben?\n\nDas schwächt die Abschottung der App deutlich. Nur für vertrauenswürdige Apps.': "Allow “{}”?\n\nThis clearly weakens the app's isolation. Only for trusted apps.",
    '• {} (PID {}) · {} RAM · läuft seit {}:{} h': '• {} (PID {}) · {} RAM · running for {}:{} h',
    '… weitere Warnungen ausgeblendet': '… more warnings hidden',
    '… {} weitere': '… {} more',
    '← Zurück': '← Back',
    '↑ {}/s · gesamt ↓ {} ↑ {}': '↑ {}/s · total ↓ {} ↑ {}',
    '▲  Alpha-Version: Aktionen mit root-Rechten auf eigenes Risiko.': '▲  Alpha version: actions with root rights at your own risk.',
    '▲ Die Signaturen sind {} Tage alt. Aktualisieren oder automatische Updates aktivieren.': '▲ The signatures are {} days old. Update them or enable automatic updates.',
    '▲ Major-Version': '▲ Major version',
    '▲ Noch keine Signaturen nach 10 Minuten – Meldungen oben prüfen.': '▲ Still no signatures after 10 minutes – check the messages above.',
    '▲ System/Kernel': '▲ System/kernel',
    '▲ Werden noch gebraucht – pacman entfernt dann auch die abhängigen Pakete oder bricht ab:': '▲ Still needed – pacman will then also remove the dependent packages or abort:',
    '▲ Änderungen wirken beim nächsten Start der App – „Neu starten“ übernimmt sie sofort.': '▲ Changes take effect the next time the app starts – “Restart” applies them immediately.',
    '○ aus': '○ off',
    '○ aus – Scans mit clamscan': '○ off – scans with clamscan',
    '● aktiv': '● active',
    '● Keine Bedrohungen gefunden · {} Dateien in {}.': '● No threats found · {} files in {}.',
    '● läuft – schnelle Scans': '● running – fast scans',
    '● Signaturen sind geladen (Version {}, Stand {}).': '● Signatures are loaded (version {}, as of {}).',
    '✕  Alle Daten auf {} ({}) werden unwiderruflich gelöscht.': '✕  All data on {} ({}) will be deleted irrevocably.',
    '✕ Der ClamAV-Server lässt gerade keine Downloads zu (zu viele Anfragen von deiner IP, z. B. über ein VPN). Später erneut versuchen oder VPN-Server wechseln.': "✕ The ClamAV server currently doesn't allow downloads (too many requests from your IP, e.g. via a VPN). Try again later or switch VPN server.",
    '✕ Der Kernel wurde aktualisiert, läuft aber noch in der alten Version ({}). Bis zum <b>Neustart</b> können neue USB-Sticks nicht erkannt werden, weil die Treiber des laufenden Kernels gelöscht wurden.': "✕ The kernel was updated but is still running the old version ({}). Until you <b>restart</b>, new USB sticks can't be detected because the running kernel's drivers were removed.",
    '✕ Keine Verbindung zum ClamAV-Server – Internet/DNS prüfen.': '✕ No connection to the ClamAV server – check internet/DNS.',
    '✕ USB-Speicher angeschlossen, aber ohne Treiber:': '✕ USB storage connected, but without driver:',
    '✕ {} infizierte Datei{} gefunden · Dauer {}.': '✕ {} infected file{} found · duration {}.',
    '⬆  Version {} verfügbar': '⬆  Version {} available',
}


def main():
    global _INVOKER
    if "--backup" in sys.argv:
        return run_backup_cli()
    init_language()
    app = QApplication(sys.argv)
    app.setApplicationName("Tuxdex")
    app.setApplicationDisplayName("Tuxdex")
    app.setDesktopFileName(APP_ID)          # Wayland: Taskleiste findet Icon über den Starter
    app.setWindowIcon(logo_icon())          # X11 + Fenster-/Dialog-Icons
    setup_icon_theme()
    migrate_legacy()
    install_desktop_entry()
    _INVOKER = _Invoker()
    apply_theme(app)
    app.aboutToQuit.connect(stop_children)      # auch beim Neustart nach einem Update
    win = MainWindow()
    win.setFocusPolicy(Qt.ClickFocus)
    win.show()
    win.setFocus()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _show_fatal_error("Tuxdex - Fehler", str(e))
        sys.exit(1)
