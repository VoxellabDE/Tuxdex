# SPDX-License-Identifier: GPL-3.0-or-later
"""Gemeinsame Vorbereitung: lädt tuxdex.py als Modul – ohne Bildschirm und mit einem leeren Test-HOME,
damit kein Test echte Einstellungen liest oder verändert."""
import importlib.util
import json
import os
import sys
import tempfile
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HOME = tempfile.mkdtemp(prefix="tuxdex-test-home-")
os.environ["HOME"] = _HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(_HOME, ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_HOME, ".cache")
os.environ["XDG_DATA_HOME"] = os.path.join(_HOME, ".local", "share")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.makedirs(os.path.join(_HOME, ".config", "tuxdex"), exist_ok=True)
with open(os.path.join(_HOME, ".config", "tuxdex", "settings.json"), "w") as fh:
    # keine Netzwerk-Prüfungen beim Start, alle Module sichtbar
    json.dump({"auto_check": False, "poll_check": False, "sys_check_on_start": False, "ask_on_start": False}, fh)


def _load():
    spec = importlib.util.spec_from_file_location("tuxdex", os.path.join(ROOT, "tuxdex.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tuxdex"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def tx():
    return _load()


@pytest.fixture(scope="session")
def qapp(tx):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    tx._INVOKER = tx._Invoker()
    tx.init_language()
    tx.apply_theme(app)
    yield app
    tx.stop_children(1)


@pytest.fixture
def errors(monkeypatch):
    """Sammelt Ausnahmen, die sonst nur als Traceback im Terminal landen (Qt-Slots, Hintergrund-Threads)."""
    found = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: found.append(f"{t.__name__}: {v}"))
    monkeypatch.setattr(threading, "excepthook", lambda a: found.append(f"{a.exc_type.__name__}: {a.exc_value}"))
    return found
