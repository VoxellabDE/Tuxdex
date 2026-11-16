# SPDX-License-Identifier: GPL-3.0-or-later
"""Start-Test: Jedes Modul wird einmal ohne Bildschirm aufgebaut – in beiden Sprachen.
Fängt Abstürze beim Öffnen eines Tabs (Tippfehler, umbenannte Funktionen, falsche Datenformen)."""
import os
import time

import pytest


def _pump(app, secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _internal_errors():
    path = os.path.expanduser("~/tuxdex_error.log")
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


@pytest.mark.parametrize("lang", ["de", "en"])
def test_all_modules_build(tx, qapp, errors, lang):
    tx.set_language(lang)
    before = _internal_errors()
    win = tx.MainWindow()
    try:
        assert set(win.page_classes) == {m[0] for m in tx.MODULES}, "page_classes und MODULES passen nicht zusammen"
        for key, _label, _color in tx.MODULES:
            page = win.page(key)
            assert page is not None, key
            _pump(qapp, 0.4)           # Hintergrund-Abfragen des Tabs dürfen zurückmelden
        win.open_settings()
        _pump(qapp, 2.0)
    finally:
        win.close()
        win.deleteLater()
        _pump(qapp, 0.2)
    assert not errors, "Ausnahmen beim Aufbau:\n" + "\n".join(errors)
    new = _internal_errors()[len(before):]
    assert not new, "Interne Fehler (tuxdex_error.log):\n" + new


def test_every_module_is_described(tx):
    keys = {m[0] for m in tx.MODULES}
    assert keys == set(tx.MODULE_INFO), "MODULE_INFO fehlt für: " + ", ".join(sorted(keys ^ set(tx.MODULE_INFO)))
    assert tx.MODULES_CORE <= keys and tx.MODULES_OFF_BY_DEFAULT <= keys
