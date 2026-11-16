# Release-Ablauf

Zwei Kanäle: **Beta** (Branch `beta`) für alles Neue, **Stabil** (Branch `main`) für geprüfte Stände.
Entwickelt wird auf `develop`/`beta`; auf `main` kommt nur, was die Checkliste unten bestanden hat.

## Rhythmus

- **Beta: gebündelt, etwa einmal pro Woche.** Kleine Korrekturen sammeln und zusammen veröffentlichen. Sofort
  veröffentlicht wird nur, was Nutzer blockiert (Absturz, Datenverlust, Update geht nicht).
- **Stabil: wenn eine Beta eine Woche ohne neue Fehlerberichte lief** und die Checkliste durch ist.
- **Versionen:** `HAUPT.NEBEN.PATCH` – z. B. `1.7.0` für neue Module, `1.6.3` für Korrekturen. Eine vierte Stelle
  nur für dringende Nachbesserungen einer bereits veröffentlichten Version.

## Beta veröffentlichen

```bash
tools/release.py bump 1.6.3          # Version, Badges, Changelog-Gerüst, Prüfsummen
# CHANGELOG.md und CHANGELOG.en.md ausfüllen
tools/release.py sums && tools/release.py check
ruff check . && python -m pytest
git commit -am "1.6.3: …" && git push origin develop develop:beta
```

Die CI muss grün sein (GitHub → Actions → CI). Sie prüft Linter, Tests und den Paketbau in einem frischen
Arch-Container mit genau den Abhängigkeiten aus dem `PKGBUILD`.

## Stabil veröffentlichen

1. Checkliste unten auf **beiden** Testsystemen durchgehen.
2. `tools/release.py bump <version> --stable` (Badge ohne „-beta“), Changelog prüfen.
3. Pull Request `beta` → `main`, CI abwarten, mergen. Der Release-Workflow legt Release, Paket und
   pacman-Repository an – und bricht ab, wenn Tests fehlschlagen oder eine erwartete Signatur fehlt.
4. `main` zurück nach `develop` und `beta` mergen.
5. Prüfen: Release-Seite zeigt Paket (`.pkg.tar.zst`), Datenbank (`tuxdex.db`) und – sobald signiert – `.sig`.

## Checkliste vor einem stabilen Release

Auf einer **Arch-VM** und einer **CachyOS-VM** (jeweils Snapshot vorher, frisch aktualisiert):

**Installation und Update**
- [ ] `install.sh` auf einem System ohne Tuxdex: läuft durch, Tuxdex startet aus dem Menü
- [ ] Update von der vorherigen stabilen Version über Einstellungen → Aktualisierung
- [ ] `sudo pacman -Syu` zeigt keine Fehler zum `[tuxdex]`-Repository

**Start und Sprache**
- [ ] Erster Start: Zustimmungsdialog erscheint einmal, danach nicht mehr
- [ ] Sprache DE ↔ EN umschalten: kein deutscher Rest in der englischen Oberfläche der geänderten Module
- [ ] Jedes Modul einmal öffnen: kein „Interner Fehler“, Protokoll ohne `FEHLER`-Einträge

**Kernfunktionen (je eine echte Aktion)**
- [ ] Updates: suchen und einspielen (pacman, AUR mit paru oder yay, Flatpak)
- [ ] Software: ein Paket installieren und wieder entfernen
- [ ] Treiber und Diagnose: Befunde stimmen mit dem System überein
- [ ] Kernel: zweiten Kernel installieren, erscheint im Bootmenü, System startet damit, wieder entfernen
- [ ] Backup: Sicherung anlegen und eine Datei daraus wiederherstellen
- [ ] Datenträger / Flash-Stick: nur mit einem Wegwerf-Stick

**Alles, was in dieser Version neu oder geändert ist**
- [ ] Jeden Punkt aus dem Changelog einmal von Hand auslösen

Findet sich ein Fehler: beheben, als Beta veröffentlichen, Checkliste für die betroffenen Punkte wiederholen.

## AUR

`tools/release.py aur` erzeugt aus dem `PKGBUILD` eine AUR-Fassung in `packaging/aur/` (Quelle: das signierbare
Tag-Archiv von GitHub statt lokaler Dateien). Veröffentlichen muss der Maintainer selbst – das braucht ein
AUR-Konto mit hinterlegtem SSH-Schlüssel:

```bash
git clone ssh://aur@aur.archlinux.org/tuxdex.git aur-tuxdex
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO aur-tuxdex/
cd aur-tuxdex && git add PKGBUILD .SRCINFO && git commit -m "tuxdex <version>" && git push
```

Das eigene pacman-Repository bleibt daneben bestehen; das AUR-Paket ist der Weg, den Arch-Nutzer erwarten.
