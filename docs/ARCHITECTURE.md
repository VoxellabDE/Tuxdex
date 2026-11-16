# Aufbau von Tuxdex

Tuxdex ist eine PySide6-Anwendung (Qt 6) für Arch Linux. Sie wird als pacman-Paket gebaut (`PKGBUILD`) und nach
`/usr/lib/tuxdex/` installiert; `/usr/bin/tuxdex` startet sie.

## Dateien

| Datei | Inhalt |
|---|---|
| `tuxdex.py` | Die Anwendung: Kern, alle Module, Einstellungen, Aktualisierung |
| `tuxdex_i18n.py` | Englischer Sprachkatalog (nur Daten). Wird beim Start aus demselben Ordner geladen |
| `install.sh` | Installer für Nutzer: trägt das pacman-Repository ein und installiert das Release-Paket |
| `PKGBUILD` | Paketbau. `source=()` listet jede Datei, die installiert wird, mit Prüfsumme |
| `tests/` | Start-Test aller Module, Logik-Tests, Signatur-Tests |
| `tools/release.py` | Version, Badges, Changelogs und Prüfsummen setzen und prüfen |
| `tools/i18n_extract.py` | Zeigt Oberflächentexte ohne englische Übersetzung |
| `.github/workflows/ci.yml` | Linter, Tests und Paketbau in einem Arch-Container – bei jedem Push |
| `.github/workflows/release.yml` | Release auf `main`: ruft erst die CI auf, baut dann Paket und pacman-Repository |

## Abschnitte in `tuxdex.py`

Die Datei ist von oben nach unten gegliedert; jeder Abschnitt beginnt mit einem Kommentarbalken.

1. **Protokoll** – `log_event`, `log_error`, `log_caught` (nur Standardbibliothek, steht vor dem Qt-Import)
2. **Übersetzung** – `tr()` und die Qt-Unterklassen, die Texte beim Setzen übersetzen
3. **Design** – Farben (`COLORS`), Stylesheet (`QSS`), Modul-Liste (`MODULES`)
4. **Kern** – `PrivilegeManager` (sudo-Sitzung), `ProcessRun`/`run_sequence` (Befehle mit Live-Ausgabe),
   Bausteine der Oberfläche (`Panel`, `Button`, `CheckRow`, `LogView`, `StatTile` …)
5. **Module** – je ein Abschnitt `# Modul: …` mit seinen Hilfsfunktionen und einer `…Tab`-Klasse
6. **Backup** – läuft auch ohne Fenster (`tuxdex --backup`, für den Zeitplan)
7. **Aktualisierung und Einstellungen** – `UpdatePanel`, `SettingsPage`, Signaturprüfung
8. **Hauptfenster** – `MainWindow` baut Tabs erst beim ersten Öffnen und entlädt lange unbenutzte wieder

## Regeln, die der Code einhält

- **Befehle immer als Liste**, nie über eine Shell (`shell=True` gibt es nicht). Root-Befehle laufen über
  `sudo -n` und nur nach der einmaligen Zustimmung des Nutzers.
- **Jeder Befehl ist sichtbar**: Was ausgeführt wird, steht im Ausgabefeld des Moduls.
- **Lange Arbeit im Hintergrund**: Abfragen laufen in Threads; zurück in die Oberfläche geht es nur über `ui(...)`.
- **Logik getrennt von der Oberfläche**: Parser und Entscheidungen sind freie Funktionen (`parse_lspci`,
  `gpu_advice`, `pacman_conf_repos` …), damit sie ohne Fenster testbar sind. Neue Logik bekommt einen Test.
- **Fehler nicht verschlucken**: In `except`-Blöcken `log_caught("Klasse.methode")` aufrufen. Programmierfehler
  landen so mit Ablauf im Protokoll (`~/.cache/tuxdex/tuxdex.log`), erwartbare Störungen als eine Zeile.
- **Texte auf Deutsch schreiben**, die englische Fassung in `tuxdex_i18n.py` ergänzen (`{}` für eingesetzte Werte).

## Warum (noch) eine große Datei?

`tuxdex.py` ist historisch gewachsen. Zwei Dinge machen das Aufteilen heikler, als es aussieht:

- **Aktualisierung alter Versionen.** Installierte Versionen laden beim Update genau die Dateien, die im `PKGBUILD`
  unter `source=()` stehen – und akzeptieren dort nur Dateinamen ohne Unterordner. Neue Dateien müssen deshalb flach
  im Wurzelordner liegen und im `PKGBUILD` stehen (ein Test prüft das).
- **Gemeinsamer Zustand.** Module teilen sich veränderliche Werte (`LANG`, `_INVOKER`, laufende Prozesse) und die
  übersetzenden Qt-Unterklassen. Beim Aufteilen muss dieser Zustand in einem gemeinsamen Kern-Modul liegen und darf
  nirgends kopiert werden (`from kern import *` würde genau das tun).

## Fahrplan für die weitere Aufteilung

Jeder Schritt ist für sich veröffentlichbar; der Start-Test (`tests/test_smoke.py`) sichert ihn ab.

1. ✅ Sprachkatalog → `tuxdex_i18n.py`
2. Reine Logik ohne Qt → `tuxdex_logic.py` (Parser, Hardware-Erkennung, Diagnose-Befunde, Backup-Kern)
3. Kern der Oberfläche → `tuxdex_core.py` (Protokoll, Übersetzung, Design, Befehlsausführung, Bausteine);
   Zugriff auf gemeinsamen Zustand nur über Funktionen
4. Module einzeln → `tuxdex_mod_<name>.py`, beginnend mit den unabhängigsten (Flash-Stick, Windows-Daten, Kernel)
