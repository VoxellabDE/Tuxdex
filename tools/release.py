#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 PyloGER (Voxellab)
"""Release-Helfer: hält Version, Badges, Changelogs und PKGBUILD-Prüfsummen zusammen.

    tools/release.py check            prüft, ob alles zusammenpasst (läuft auch in der CI)
    tools/release.py sums             berechnet die sha256sums im PKGBUILD neu
    tools/release.py bump 1.6.2       setzt die Version überall, legt Changelog-Abschnitte an, rechnet Prüfsummen
    tools/release.py bump 1.6.2 --stable    wie oben, aber Badge ohne „-beta“ (für ein Release auf main)

Nach `bump` die beiden Changelog-Abschnitte ausfüllen und zum Schluss noch einmal `sums` und `check` laufen lassen.
"""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READMES = ("README.md", "README.en.md")
CHANGELOGS = ("CHANGELOG.md", "CHANGELOG.en.md")
BADGE_RE = re.compile(r"(img\.shields\.io/badge/version-)([\w.]+?)(--beta)?(-2fb3a3)")
SUMS_RE = re.compile(r"^sha256sums=\((.*?)\)", re.S | re.M)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def write(name, text):
    if not text.strip():                 # nie eine Datei leer schreiben
        raise SystemExit(f"Abbruch: {name} wäre leer geworden")
    tmp = os.path.join(ROOT, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(ROOT, name))


def app_version():
    m = re.search(r'^APP_VERSION = "([\w.-]+)"', read("tuxdex.py"), re.M)
    return m.group(1) if m else ""


def pkg_sources(pkgbuild):
    m = re.search(r"^source=\((.*?)\)", pkgbuild, re.S | re.M)
    return [f.strip("'\"") for f in (m.group(1).split() if m else [])]


def real_sums(pkgbuild):
    out = []
    for name in pkg_sources(pkgbuild):
        with open(os.path.join(ROOT, name), "rb") as f:
            out.append(hashlib.sha256(f.read()).hexdigest())
    return out


def check():
    """Liste der Unstimmigkeiten (leer = alles passt)."""
    problems = []
    ver = app_version()
    pkgbuild = read("PKGBUILD")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
        problems.append(f"APP_VERSION „{ver}“ ist keine gültige Version")
    m = re.search(r"^pkgver=(.+)$", pkgbuild, re.M)
    if not m or m.group(1) != ver:
        problems.append(f"PKGBUILD pkgver={m.group(1) if m else '?'} passt nicht zu APP_VERSION {ver}")
    for name in READMES:
        b = BADGE_RE.search(read(name))
        if not b or b.group(2) != ver:
            problems.append(f"{name}: Versions-Badge zeigt {b.group(2) if b else 'nichts'} statt {ver}")
    for name in CHANGELOGS:
        section = re.search(rf"^## {re.escape(ver)}\n(.*?)(?=^## |\Z)", read(name), re.S | re.M)
        if not section:
            problems.append(f"{name}: Abschnitt „## {ver}“ fehlt")
        elif not re.search(r"^\s*[-*] \S", section.group(1), re.M):
            problems.append(f"{name}: Abschnitt „## {ver}“ ist leer")
    stated = re.findall(r"[0-9a-f]{64}", (SUMS_RE.search(pkgbuild) or [None, ""])[1])
    names, real = pkg_sources(pkgbuild), real_sums(pkgbuild)
    if len(stated) != len(real):
        problems.append(f"PKGBUILD: {len(stated)} Prüfsummen für {len(real)} Dateien")
    for name, a, b in zip(names, stated, real):
        if a != b:
            problems.append(f"PKGBUILD: Prüfsumme von {name} stimmt nicht (tools/release.py sums)")
    return problems


def sums():
    pkgbuild = read("PKGBUILD")
    block = "sha256sums=(" + "\n            ".join(f"'{h}'" for h in real_sums(pkgbuild)) + ")"
    new, n = SUMS_RE.subn(lambda _m: block, pkgbuild)
    if n != 1:
        raise SystemExit("Abbruch: sha256sums-Block im PKGBUILD nicht gefunden")
    if new != pkgbuild:
        write("PKGBUILD", new)


def bump(ver, stable):
    if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
        raise SystemExit(f"„{ver}“ ist keine gültige Version (erwartet z. B. 1.6.2)")
    old = app_version()
    code = read("tuxdex.py")
    write("tuxdex.py", re.sub(r'^APP_VERSION = "[\w.-]+"', f'APP_VERSION = "{ver}"', code, count=1, flags=re.M))
    write("PKGBUILD", re.sub(r"^pkgver=.+$", f"pkgver={ver}", read("PKGBUILD"), count=1, flags=re.M))
    for name in READMES:
        write(name, BADGE_RE.sub(lambda m: m.group(1) + ver + ("" if stable else "--beta") + m.group(4),
                                 read(name), count=1))
    for name in CHANGELOGS:
        text = read(name)
        if re.search(rf"^## {re.escape(ver)}$", text, re.M):
            continue
        first = re.search(r"^## ", text, re.M)
        if not first:
            raise SystemExit(f"Abbruch: in {name} gibt es noch keinen Abschnitt")
        write(name, text[:first.start()] + f"## {ver}\n\n- \n\n" + text[first.start():])
    sums()
    print(f"{old} → {ver}. Jetzt die Changelog-Abschnitte ausfüllen, dann: tools/release.py sums && tools/release.py check")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        problems = check()
        for p in problems:
            print("✕ " + p)
        if not problems:
            print(f"✓ Version {app_version()}: Badges, Changelogs und Prüfsummen passen zusammen")
        return 1 if problems else 0
    if cmd == "sums":
        sums()
        return 0
    if cmd == "bump" and len(argv) > 2:
        bump(argv[2], "--stable" in argv[3:])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
