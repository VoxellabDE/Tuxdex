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


def _internal_errors(tx):
    """Fehler-Einträge aus dem Protokoll (dorthin schreibt Tuxdex Ausnahmen aus Rückmeldungen und Threads)."""
    try:
        with open(tx.LOG_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    return "\n".join(part for part in text.split("FEHLER in ")[1:])


@pytest.mark.parametrize("lang", ["de", "en"])
def test_all_modules_build(tx, qapp, errors, lang):
    tx.set_language(lang)
    before = _internal_errors(tx)
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
    new = _internal_errors(tx)[len(before):]
    assert not new, "Interne Fehler (Protokoll):\n" + new


def test_every_module_is_described(tx):
    keys = {m[0] for m in tx.MODULES}
    assert keys == set(tx.MODULE_INFO), "MODULE_INFO fehlt für: " + ", ".join(sorted(keys ^ set(tx.MODULE_INFO)))
    assert tx.MODULES_CORE <= keys and tx.MODULES_OFF_BY_DEFAULT <= keys


def test_swallowed_programming_error_reaches_the_log(tx, qapp, tmp_path, monkeypatch):
    """Nachgestellt: der Fehler aus 1.6.1.5 (Liste statt Wörterbuch im Software-Tab). Der Tab fängt ihn ab und
    zeigt nur einen Hinweis – im Protokoll muss er trotzdem als FEHLER stehen, sonst sieht ihn der Start-Test nie."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "pacman").write_text("#!/bin/sh\necho firefox\n")
    (stub / "pacman").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}:{os.environ['PATH']}")
    monkeypatch.setattr(tx, "LOG_FILE", str(tmp_path / "tuxdex.log"))
    monkeypatch.setattr(tx, "_LOG_SEEN", set())
    monkeypatch.setattr(tx, "pacman_repos", lambda: [("core", "https://example.invalid")])

    class App:
        priv = None
    tab = tx.SoftwareTab(App())
    try:
        end = time.time() + 15
        while tab.loading and time.time() < end:
            _pump(qapp, 0.1)
    finally:
        tab.deleteLater()
        _pump(qapp, 0.1)
    assert "FEHLER in SoftwareTab.refresh_list.worker" in _internal_errors_raw(tx)


def _internal_errors_raw(tx):
    try:
        with open(tx.LOG_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _run_and_wait(tx, qapp, cmd, log):
    done = []
    tx.run_streaming(cmd, log, clear_first=False, on_done=done.append)
    end = time.time() + 10
    while not done and time.time() < end:
        _pump(qapp, 0.05)
    assert done == [1]


def test_hidden_commands_never_reach_the_log_with_arguments(tx, qapp, tmp_path, monkeypatch):
    """Befehle ohne Ausgabefeld tragen Vertrauliches (z. B. die Mullvad-Kontonummer als Argument)."""
    monkeypatch.setattr(tx, "LOG_FILE", str(tmp_path / "tuxdex.log"))
    _run_and_wait(tx, qapp, ["false", "1234567890123456"], None)
    text = _internal_errors_raw(tx)
    assert "Exit-Code 1: false (Argumente nicht protokolliert)" in text
    assert "1234567890123456" not in text
    view = tx.LogView(80)
    try:
        _run_and_wait(tx, qapp, ["false", "sichtbar"], view)      # läuft sichtbar mit → darf ins Protokoll
    finally:
        view.deleteLater()
    assert "Exit-Code 1: false sichtbar" in _internal_errors_raw(tx)
