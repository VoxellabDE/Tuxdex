# Mitmachen / Contributing

Danke für dein Interesse! Fehler, Ideen und Fragen bitte über die
[Issues und Discussions](https://github.com/VoxellabDE/Tuxdex/issues/new/choose). Sicherheitslücken nicht öffentlich
melden, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

*English: bug reports, ideas and pull requests are welcome – in German or English. Code comments and UI source
strings are German; English UI texts live in `tuxdex_i18n.py`.*

## Entwicklungsumgebung

```bash
sudo pacman -S --needed pyside6 qt6-svg python-pytest ruff shellcheck
git clone https://github.com/VoxellabDE/Tuxdex.git && cd Tuxdex
python tuxdex.py            # direkt aus dem Klon starten
```

## Vor jedem Pull Request

```bash
ruff check .                 # Linter: doppelte/undefinierte Namen, unbenutzte Variablen
python -m pytest             # Start-Test aller Module + Logik-Tests (ohne Bildschirm, ohne root)
shellcheck install.sh
tools/release.py check       # Version, Badges, Changelogs, Prüfsummen passen zusammen
```

Dieselben Prüfungen laufen in der CI. Pull Requests bitte gegen den Branch **`develop`**.

## Worauf wir achten

- Der Aufbau und die Regeln stehen in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – kurz: Befehle als Liste,
  root nur über `sudo -n` nach Zustimmung, lange Arbeit im Hintergrund, Fehler mit `log_caught(...)` festhalten.
- Neue Logik (Parser, Entscheidungen) als freie Funktion schreiben und in `tests/test_logic.py` testen.
- Neue Oberflächentexte auf Deutsch; die englische Fassung in `tuxdex_i18n.py` ergänzen
  (`python tools/i18n_extract.py` zeigt, was fehlt).
- Änderungen an `tuxdex.py` gehören in den Changelog (beide Sprachen). Den Release-Ablauf beschreibt
  [docs/RELEASE.md](docs/RELEASE.md).

## Lizenz

Mit einem Beitrag stimmst du zu, dass er unter der [GPL-3.0-or-later](LICENSE) veröffentlicht wird.
