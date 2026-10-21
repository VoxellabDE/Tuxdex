#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 PyloGER (Voxellab)
"""Sammelt alle Oberflächentexte aus tuxdex.py und zeigt, welche im englischen Katalog (EN = {...}) fehlen.

    python3 tools/i18n_extract.py            → Anzahl fehlender Texte
    python3 tools/i18n_extract.py --json     → fehlende Texte als JSON (zum Übersetzen)

f-Strings werden zu Vorlagen mit {} für die eingesetzten Werte, z. B. "Letztes Backup {}".
"""
import ast
import json
import re
import sys

SRC = "tuxdex.py"

# Texte, die nie angezeigt werden oder nicht übersetzt werden dürfen (Befehle, Pfade, Regex, Schlüssel …)
CODE_RE = re.compile(r"^(\$ |sudo |pacman |paru |flatpak |systemctl |journalctl |[/~.][\w./-]*$|[a-z_.-][\w.-]*$|\^|#!)"
                     r"|\\[sdwbS]|\(\?|%[A-Za-z]|[A-Z_]{4,}=|--[a-z]")
UI_RE = re.compile(r"[äöüÄÖÜß]| [a-zäöü]|[A-ZÄÖÜ][a-zäöü]+")


def templates(src):
    tree = ast.parse(src)
    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    skip = set()
    for n in tree.body:        # den Katalog selbst nicht mitzählen
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EN" for t in n.targets):
            skip = {id(x) for x in ast.walk(n)}
    out = []
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            p = parent.get(n)
            if isinstance(p, ast.JoinedStr) or isinstance(p, ast.Expr):   # Teil eines f-Strings / Docstring
                continue
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            out.append("".join(v.value if isinstance(v, ast.Constant) else "{}" for v in n.values))
    return out


NOT_UI = ("<svg", "<rect", "<path", "<circle", "QWidget {", "\\x1b", "[Desktop Entry]", "[Journal]", "[Coredump]",
          " && ", "2>/dev/null", " > /", "ACTION==", "starting full", "reached after", "Corporation",
          "Sans", "Mono", "Serif")


def is_ui(t):
    s = t.strip()
    words = re.findall(r"[A-Za-zÄÖÜäöüß]{3,}", s)
    if len(words) >= 5 and re.search(r"[äöüß]| (und|der|die|das|nicht|mit|für|wird|nur|oder) ", s) \
            and not any(x in s for x in ("<svg", "QWidget {", "[Desktop Entry]")):
        return True        # ein deutscher Satz – auch wenn Befehle wie --user darin vorkommen
    if len(s) < 2 or not UI_RE.search(s) or CODE_RE.search(s):
        return False
    if any(x in s for x in NOT_UI) or re.fullmatch(r"[\w/ .-]+/[\w.-]+", s):
        return False
    if re.fullmatch(r"[A-Z][a-z]+( [a-z]+)*", s) and not re.search(r"[äöüß]", s) and s in ENGLISH_KEYS:
        return False
    return True


# Englische Rohwerte aus Systemausgaben (werden verglichen, nie angezeigt)
ENGLISH_KEYS = {"Not charging", "Charging", "Discharging", "Full", "Unknown"}


def catalog(src):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EN" for t in n.targets):
            return ast.literal_eval(n.value)
    return {}


def main():
    src = open(SRC, encoding="utf-8").read()
    cat = catalog(src)
    seen, missing = set(), []
    for t in templates(src):
        k = t.strip()
        if k in seen or not is_ui(k) or k in cat:
            continue
        seen.add(k)
        missing.append(k)
    if "--json" in sys.argv:
        json.dump(missing, sys.stdout, ensure_ascii=False, indent=0)
    else:
        print(f"{len(cat)} übersetzt, {len(missing)} fehlen")


if __name__ == "__main__":
    main()
