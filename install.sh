#!/usr/bin/env bash
# Tuxdex installer: adds the pacman repository and installs Tuxdex.
#   curl -fsSL https://raw.githubusercontent.com/VoxellabDE/Tuxdex/main/install.sh | bash
# Options: -y (don't ask), --dry-run (only show, change nothing), --de (Deutsch)
set -euo pipefail

SERVER="https://github.com/VoxellabDE/Tuxdex/releases/latest/download"
CONF="${TUXDEX_PACMAN_CONF:-/etc/pacman.conf}"
YES=0
DRY=0
DE=0
[ "${TUXDEX_LANG:-}" = "de" ] && DE=1
# t "english" "deutsch": Englisch ist Standard
t() { if [ "$DE" -eq 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
for a in "$@"; do
    case "$a" in
        -y|--yes) YES=1 ;;
        --dry-run) DRY=1 ;;
        --de) DE=1 ;;
        -h|--help) sed -n '2,5p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unknown option: $a" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m%s\033[0m %s\n' "$(t Error: Fehler:)" "$*" >&2; exit 1; }

command -v pacman >/dev/null || die "$(t "This is not an Arch system (pacman missing). Tuxdex only runs on Arch Linux and Arch-based systems." "Dies ist kein Arch-System (pacman fehlt). Tuxdex läuft nur auf Arch Linux und Arch-basierten Systemen.")"
[ "$(id -u)" -eq 0 ] && SUDO="" || { command -v sudo >/dev/null || die "$(t "sudo is missing – run as root or install sudo." "sudo fehlt – als root ausführen oder sudo installieren.")"; SUDO="sudo"; }

if [ "$DE" -eq 1 ]; then
cat <<MSG

  Tuxdex – Installer  (Beta: Nutzung auf eigenes Risiko, vorher ein Backup anlegen)

  Das passiert:
    1. Das Tuxdex-Repository wird in $CONF eingetragen (vorher Sicherung als $CONF.tuxdex.bak)
    2. Dein System wird aktualisiert und Tuxdex installiert:  pacman -Syu tuxdex
  Entfernen später:  sudo pacman -R tuxdex

MSG
else
cat <<MSG

  Tuxdex – Installer  (beta: use at your own risk, make a backup first)

  What happens:
    1. The Tuxdex repository is added to $CONF (backup saved as $CONF.tuxdex.bak first)
    2. Your system is updated and Tuxdex is installed:  pacman -Syu tuxdex
  Remove later:  sudo pacman -R tuxdex

MSG
fi

if [ "$YES" -eq 0 ] && [ "$DRY" -eq 0 ]; then
    [ -r /dev/tty ] || die "$(t "No input possible – run again with -y." "Keine Eingabe möglich – mit -y erneut starten.")"
    read -r -p "  $(t "Continue? [y/N]" "Fortfahren? [j/N]") " ans < /dev/tty
    case "$ans" in j|J|y|Y|ja|Ja) ;; *) echo "  $(t Aborted. Abgebrochen.)"; exit 0 ;; esac
fi

run() { if [ "$DRY" -eq 1 ]; then echo "  (dry run) $*"; else "$@"; fi; }

# 1. Repository eintragen (oder alte Adresse reparieren)
if grep -q '^\[tuxdex\]' "$CONF"; then
    if grep -q 'PyloGER/Tuxdex' "$CONF"; then
        say "$(t "Old repository address found – fixing it" "Alte Repository-Adresse gefunden – wird korrigiert")"
        run $SUDO cp -a "$CONF" "$CONF.tuxdex.bak"
        run $SUDO sed -i 's#github.com/PyloGER/Tuxdex#github.com/VoxellabDE/Tuxdex#g' "$CONF"
    else
        say "$(t "Repository is already configured" "Repository ist schon eingetragen")"
    fi
else
    say "$(t "Adding repository" "Repository wird eingetragen")"
    run $SUDO cp -a "$CONF" "$CONF.tuxdex.bak"
    if [ "$DRY" -eq 1 ]; then
        echo "  (dry run) would append:"; printf '  [tuxdex]\n  SigLevel = Optional TrustAll\n  Server = %s\n' "$SERVER"
    else
        printf '\n[tuxdex]\nSigLevel = Optional TrustAll\nServer = %s\n' "$SERVER" | $SUDO tee -a "$CONF" >/dev/null
    fi
fi

# 2. Installieren (immer zusammen mit einem System-Update, nie nur -Sy)
say "$(t "Updating the system and installing Tuxdex" "System aktualisieren und Tuxdex installieren")"
run $SUDO pacman -Syu --needed tuxdex

say "$(t "Done! Start Tuxdex from the application menu or with:  tuxdex" "Fertig! Starte Tuxdex im Anwendungsmenü oder mit:  tuxdex")"
