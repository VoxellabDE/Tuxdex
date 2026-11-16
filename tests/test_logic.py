# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests der reinen Logik (ohne Oberfläche): Parser, Versionen, Einstellungen, Übersetzungen."""
import os

import pytest


# ---- Versionen ----------------------------------------------------------------

def test_version_order(tx):
    v = tx.version_tuple
    assert v("1.6.0") > v("1.6.0-beta.2") > v("1.6.0-beta.1") > v("1.5.4")
    assert v("1.6.1.11") > v("1.6.1.9")              # vierte Stelle zählt als Zahl, nicht als Text
    assert v("v1.6.0") == v("1.6.0")
    assert v("") == (0,) and v(None) == (0,)


def test_changes_since_only_newer_sections(tx):
    log = "## 1.2.0\n\n- neu <b>\n\n## 1.1.0\n\n- alt\n"
    html = tx.changes_since(log, "1.1.0")
    assert "1.2.0" in html and "neu &lt;b>" in html
    assert "alt" not in html


@pytest.mark.parametrize("text,want", [
    ("https://github.com/VoxellabDE/Tuxdex", "VoxellabDE/Tuxdex"),
    ("https://github.com/VoxellabDE/Tuxdex.git", "VoxellabDE/Tuxdex"),
    ("git@github.com:VoxellabDE/Tuxdex.git", "VoxellabDE/Tuxdex"),
    ("VoxellabDE/Tuxdex", "VoxellabDE/Tuxdex"),
    ("kein repo", ""), ("", ""), (None, ""),
])
def test_normalize_repo(tx, text, want):
    assert tx.normalize_repo(text) == want


# ---- Anzeige ------------------------------------------------------------------

def test_fmt_bytes(tx):
    assert tx.fmt_bytes(0) == "0 B"
    assert tx.fmt_bytes(1536) == "1.5 KiB"
    assert tx.fmt_bytes(None) == "—"


def test_fmt_active_translated(tx):
    tx.set_language("de")
    assert tx.fmt_active(216 * 86400 + 2 * 3600) == "216 Tage 2 Std."
    assert tx.fmt_active(None) == "—"
    tx.set_language("en")
    assert tx.fmt_active(216 * 86400 + 2 * 3600) == "216 days 2 h"
    assert tx.fmt_active(5000) == "1 h 23 min"
    tx.set_language("de")


# ---- Parser -------------------------------------------------------------------

LSPCI = """\
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation TU117M [GeForce GTX 1650] [10de:1f99] (rev a1)
\tSubsystem: ASUSTeK Computer Inc. Device [1043:1a52]
\tKernel driver in use: nvidia
\tKernel modules: nouveau, nvidia_drm, nvidia
03:00.0 Network controller [0280]: MEDIATEK Corp. MT7921 802.11ax [14c3:7961]
\tKernel driver in use: mt7921e
"""


def test_parse_lspci(tx):
    devs = tx.parse_lspci(LSPCI)
    assert len(devs) == 2
    gpu, wifi = devs
    assert (gpu["vendor"], gpu["device"], gpu["driver"]) == ("10de", "1f99", "nvidia")
    assert gpu["class_id"] == "0300" and "nouveau" in gpu["modules"]
    assert (wifi["vendor"], wifi["driver"]) == ("14c3", "mt7921e")


def test_gpu_advice_nvidia_active(tx):
    gpu = tx.parse_lspci(LSPCI)[0]
    tone, short, _detail, pkgs, _note = tx.gpu_advice(gpu, {"nvidia-open"}, "linux", False)
    assert tone == "ok" and pkgs is None


def test_gpu_advice_nvidia_missing_uses_dkms_for_other_kernels(tx):
    gpu = dict(tx.parse_lspci(LSPCI)[0], driver="nouveau")
    tone, _short, _detail, pkgs, _note = tx.gpu_advice(gpu, set(), "linux-cachyos", False)
    assert tone == "warn"
    assert "nvidia-open-dkms" in pkgs and "linux-cachyos-headers" in pkgs


def test_parse_shadow(tx):
    res = tx.parse_shadow("anna:$y$abc:19900:0:99999:7:::\ngast:!:19000::::::\nkaputt\n")
    assert res["anna"] == (19900, False)
    assert res["gast"] == (19000, True)
    assert "kaputt" not in res


def test_pacman_conf_repos(tx, monkeypatch):
    conf = {
        "/etc/pacman.conf": "[options]\nParallelDownloads = 5\n# [aus]\n[cachyos-v3]\nInclude = /a\n"
                            "[core]\nInclude = /b\n[tuxdex]\nSigLevel = Optional\nServer = https://x/$repo/$arch\n",
        "/a": "Server = https://m/$arch_v3/$repo\nServer = https://zweiter/$repo\n",
        "/b": "# Kommentar\nServer = https://n/$repo/os/$arch\n",
    }
    monkeypatch.setattr(tx, "_read", lambda p: conf.get(p, ""))
    assert tx.pacman_conf_repos() == [
        ("cachyos-v3", "https://m/x86_64_v3/cachyos-v3"),
        ("core", "https://n/core/os/x86_64"),
        ("tuxdex", "https://x/tuxdex/x86_64"),
    ]


def test_bootmenu_steps(tx, monkeypatch):
    monkeypatch.setattr(tx, "which", lambda name: name == "grub-mkconfig")
    steps = tx.bootmenu_steps("grub", "/boot/grub/grub.cfg")
    assert [s["cmd"][0] for s in steps] == ["cp", "grub-mkconfig"]       # erst Sicherung, dann neu erzeugen
    assert all(s["needs_sudo"] for s in steps)
    assert tx.bootmenu_steps("limine", "/boot/limine.conf") is None      # Werkzeug fehlt → nichts tun
    assert tx.bootmenu_steps("unbekannt", "") is None


def test_aur_helper_prefers_paru(tx, monkeypatch):
    monkeypatch.setattr(tx, "which", lambda name: name in ("paru", "yay"))
    assert tx.aur_helper() == "paru"
    monkeypatch.setattr(tx, "which", lambda name: name == "yay")
    assert tx.aur_helper() == "yay"
    monkeypatch.setattr(tx, "which", lambda name: False)
    assert tx.aur_helper() is None


# ---- Einstellungen ------------------------------------------------------------

@pytest.fixture
def settings_file(tx, tmp_path, monkeypatch):
    path = str(tmp_path / "settings.json")
    monkeypatch.setattr(tx, "SETTINGS_FILE", path)
    return path


def test_stale_copy_never_overwrites_modules(tx, settings_file):
    tx.save_settings({"modules": {"swap": True}}, force=("modules",))
    stale = tx.load_settings()                     # so hält der Update-Prüfer seine Kopie
    tx.save_settings({"modules": {"swap": True, "backup": False}}, force=("modules",))
    stale["channels"] = {"main": "1.0"}
    tx.save_settings(stale)                        # alte Kopie speichern darf die Module nicht zurückdrehen
    now = tx._load_json(settings_file, {})
    assert now["modules"] == {"swap": True, "backup": False}
    assert now["channels"] == {"main": "1.0"}


def test_save_is_atomic_and_leaves_no_temp_file(tx, settings_file):
    tx.save_settings({"lang": "en"})
    assert tx._load_json(settings_file, {})["lang"] == "en"
    assert os.listdir(os.path.dirname(settings_file)) == ["settings.json"]


def test_module_enabled_defaults(tx):
    assert tx.module_enabled("update", {"modules": {"update": False}})     # Kern-Modul lässt sich nicht abwählen
    assert not tx.module_enabled("swap", {})                              # für Fortgeschrittene: anfangs aus
    assert tx.module_enabled("software", {})
    assert not tx.module_enabled("software", {"modules": {"software": False}})


# ---- Übersetzungen ------------------------------------------------------------

def test_catalog_placeholders_match(tx):
    """Jede Vorlage muss in beiden Sprachen gleich viele {} haben – sonst gehen Werte verloren."""
    import re
    # „alte{}“ ist eine deutsche Endung (alter/alte), die im Englischen wegfällt – die zählt nicht mit
    values = lambda de: len(re.findall(r"(?<![A-Za-zÄÖÜäöüß])\{\}", de))
    bad = [de for de, en in tx.EN.items() if not values(de) <= en.count("{}") <= de.count("{}")]
    assert not bad, "Platzhalter-Anzahl weicht ab:\n" + "\n".join(bad[:20])


def test_catalog_has_no_empty_translation(tx):
    bad = [de for de, en in tx.EN.items() if de.strip() and not en.strip()]
    assert not bad, "leere Übersetzung:\n" + "\n".join(bad[:20])


SAME = ("Updates", "Software", "Flatpak", "Kernel", "Backup", "Swap", "Antivirus")     # in beiden Sprachen gleich


def test_module_names_are_translated(tx):
    tx.set_language("en")
    try:
        missing = [label for _k, label, _c in tx.MODULES if tx.tr(label) == label and label not in SAME]
    finally:
        tx.set_language("de")
    assert not missing, "Modulnamen ohne englische Übersetzung: " + ", ".join(missing)
