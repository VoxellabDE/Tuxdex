#!/usr/bin/env bash
# Tuxdex-Installer: trägt das pacman-Repository ein und installiert Tuxdex.
#   curl -fsSL https://raw.githubusercontent.com/VoxellabDE/Tuxdex/main/install.sh | bash
# Optionen: -y (nicht nachfragen), --dry-run (nur anzeigen, nichts ändern)
set -euo pipefail

SERVER="https://github.com/VoxellabDE/Tuxdex/releases/latest/download"
CONF="${TUXDEX_PACMAN_CONF:-/etc/pacman.conf}"
YES=0
DRY=0
for a in "$@"; do
    case "$a" in
        -y|--yes) YES=1 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,5p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unbekannte Option: $a" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

command -v pacman >/dev/null || die "Dies ist kein Arch-System (pacman fehlt). Tuxdex läuft nur auf Arch Linux und Arch-basierten Systemen."
[ "$(id -u)" -eq 0 ] && SUDO="" || { command -v sudo >/dev/null || die "sudo fehlt – als root ausführen oder sudo installieren."; SUDO="sudo"; }

cat <<MSG

  Tuxdex – Installer  (Alpha: Nutzung auf eigenes Risiko, vorher ein Backup anlegen)

  Das passiert:
    1. Das Tuxdex-Repository wird in $CONF eingetragen (vorher Sicherung als $CONF.tuxdex.bak)
    2. Dein System wird aktualisiert und Tuxdex installiert:  pacman -Syu tuxdex
  Entfernen später:  sudo pacman -R tuxdex

MSG

if [ "$YES" -eq 0 ] && [ "$DRY" -eq 0 ]; then
    [ -r /dev/tty ] || die "Keine Eingabe möglich – mit -y erneut starten."
    read -r -p "  Fortfahren? [j/N] " ans < /dev/tty
    case "$ans" in j|J|y|Y|ja|Ja) ;; *) echo "  Abgebrochen."; exit 0 ;; esac
fi

run() { if [ "$DRY" -eq 1 ]; then echo "  (Trockenlauf) $*"; else "$@"; fi; }

# 1. Repository eintragen (oder alte Adresse reparieren)
if grep -q '^\[tuxdex\]' "$CONF"; then
    if grep -q 'PyloGER/Tuxdex' "$CONF"; then
        say "Alte Repository-Adresse gefunden – wird korrigiert"
        run $SUDO cp -a "$CONF" "$CONF.tuxdex.bak"
        run $SUDO sed -i 's#github.com/PyloGER/Tuxdex#github.com/VoxellabDE/Tuxdex#g' "$CONF"
    else
        say "Repository ist schon eingetragen"
    fi
else
    say "Repository wird eingetragen"
    run $SUDO cp -a "$CONF" "$CONF.tuxdex.bak"
    if [ "$DRY" -eq 1 ]; then
        echo "  (Trockenlauf) angehängt würde:"; printf '  [tuxdex]\n  SigLevel = Optional TrustAll\n  Server = %s\n' "$SERVER"
    else
        printf '\n[tuxdex]\nSigLevel = Optional TrustAll\nServer = %s\n' "$SERVER" | $SUDO tee -a "$CONF" >/dev/null
    fi
fi

# 2. Installieren (immer zusammen mit einem System-Update, nie nur -Sy)
say "System aktualisieren und Tuxdex installieren"
run $SUDO pacman -Syu --needed tuxdex

say "Fertig! Starte Tuxdex im Anwendungsmenü oder mit:  tuxdex"
