# Maintainer: PyloGER (Voxellab) <contact@voxellab.de>
pkgname=tuxdex
pkgver=1.3.0
epoch=1
pkgrel=1
pkgdesc="Grafische Systemverwaltung für Arch Linux: Updates, Software, Backup, Flatpak-Rechte, Datenträger, Speicher, Taskmanager, Antivirus, Sicherheit & Mullvad VPN"
arch=('any')
url="https://github.com/PyloGER/Tuxdex"
license=('MIT')
depends=('python' 'pyside6' 'sudo' 'util-linux' 'iproute2' 'pciutils' 'hwdata' 'pacman-contrib' 'ttf-ibm-plex' 'rsync')
optdepends=(
    'paru: AUR-Pakete aktualisieren und installieren'
    'flatpak: Flatpak-Apps verwalten'
    'udisks2: USB-Sticks ohne Passwort einhängen und sicher entfernen'
    'dosfstools: FAT32 formatieren'
    'exfatprogs: exFAT formatieren'
    'ntfs-3g: NTFS formatieren und umbenennen'
    'btrfs-progs: btrfs formatieren'
    'xfsprogs: xfs formatieren'
    'clamav: Virenscanner'
    'ufw: Firewall'
    'mullvad-vpn-daemon: Mullvad VPN'
    'sbctl: Secure Boot einrichten'
    'arch-audit: Pakete auf bekannte Sicherheitslücken prüfen'
    'zstd: Backup-Archive mit zstd komprimieren'
    'gnupg: Backup-Archive mit Passwort verschlüsseln'
    'pigz: schnellere gzip-Backups'
)
# früherer Projektname – wird beim Installieren automatisch ersetzt
conflicts=('arch-manager')
replaces=('arch-manager')
source=(
        tuxdex.py
        tuxdex
        tuxdex.desktop
        tuxdex.svg
        tuxdex-16.png
        tuxdex-32.png
        tuxdex-48.png
        tuxdex-64.png
        tuxdex-128.png
        tuxdex-256.png
        tuxdex-512.png
        LICENSE)
sha256sums=('e79e4bc8002669cda2ffbd934c641456c41396343d7ee11368c4b7e91ae99540'
            '27c40e4efe990d8cc8e5e3484caab4dfb6c4577e04c9dc1ae903344440f19519'
            '992bab031982cb434b9a6ba4648cf29187ee8b956a0ca7aefe2909c62dc62fe6'
            '8f26609520915020bbd6da6d90bf3cbf59b8d8eb223072ad53cee0e0d2bdb010'
            'c8b407b7e7a1d1bbfca5e8dc0100e2e56fe53a8ec126fbfa1d91c69b589a4a6b'
            '642904d06d46938c0dbebe6e5d60ec303cd0cf3f3cc5d7d07f73da1b715b8eb8'
            '995e46e56bea4a17be8e90825329a8e707260034166c2d496bc4f79e1808362b'
            '5c90315e0c77778e0222b2afcbce3807eed8841c90274085955f9e8b157f2863'
            'b9c22246ea2913006a81bd665a186cfb79c9f57c31dae090a693b9cd2052ab9f'
            'ab1a61c6f5ab235478564c3e0860f817ef4d89a6298dfcb491356adc54342a1d'
            'f97e0af03c351e904d75bccd38dafa82df884c9bbc37e571540b0687eacbc8ac'
            '4b90291970687641ff6d103277075acbb5824cc0f3c7e120609f78044c2d3228')

package() {
    install -Dm755 tuxdex.py "$pkgdir/usr/lib/$pkgname/tuxdex.py"
    install -Dm755 tuxdex "$pkgdir/usr/bin/tuxdex"
    install -Dm644 tuxdex.desktop "$pkgdir/usr/share/applications/tuxdex.desktop"
    install -Dm644 tuxdex.svg "$pkgdir/usr/share/icons/hicolor/scalable/apps/tuxdex.svg"
    for s in 16 32 48 64 128 256 512; do
        install -Dm644 "tuxdex-$s.png" "$pkgdir/usr/share/icons/hicolor/${s}x${s}/apps/tuxdex.png"
    done
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
