# Pakete signieren

Solange kein Schlüssel hinterlegt ist, sind die Tuxdex-Pakete **unsigniert**: pacman vertraut allein der
HTTPS-Verbindung zu GitHub (`SigLevel = Optional TrustAll`). Mit den Schritten unten werden Pakete signiert, und
Installer, pacman-Repository und die Aktualisierung in Tuxdex (stabiler Kanal) installieren nur noch Pakete mit
gültiger Signatur.

Alles hier macht der Projektinhaber **einmalig auf dem eigenen Rechner**. Der private Schlüssel verlässt den Rechner
nur als GitHub-Secret und gehört niemals ins Repository.

## 1. Schlüssel erzeugen

```bash
gpg --quick-generate-key "Tuxdex Releases <contact@voxellab.de>" ed25519 sign 3y
gpg --list-secret-keys --keyid-format long      # zeigt den 40-stelligen Fingerabdruck
```

Ohne Passwort erzeugen (bei der Abfrage leer lassen) – GitHub Actions kann keins eingeben. Dafür liegt der Schlüssel
nur als verschlüsseltes Secret bei GitHub und als Sicherung offline bei dir.

## 2. Sicherung anlegen

```bash
gpg --armor --export-secret-keys <FINGERABDRUCK> > tuxdex-privat.asc
gpg --armor --gen-revoke <FINGERABDRUCK> > tuxdex-widerruf.asc
```

Beide Dateien offline aufbewahren (USB-Stick, Passwortmanager). Geht der Schlüssel verloren, können bestehende
Installationen keine Updates mehr prüfen.

## 3. Privaten Schlüssel als Secret hinterlegen

GitHub → Repository → **Settings → Secrets and variables → Actions → New repository secret**

- Name: `PACMAN_GPG_KEY`
- Wert: der komplette Inhalt von `tuxdex-privat.asc`

## 4. Öffentlichen Schlüssel ins Repository

```bash
gpg --armor --export <FINGERABDRUCK> > /tmp/tuxdex-oeffentlich.asc
tools/release.py key /tmp/tuxdex-oeffentlich.asc
tools/release.py check
```

Das legt `tuxdex-signing-key.asc` an und trägt den Fingerabdruck in `tuxdex.py` und `install.sh` ein. Vor dem Commit
den angezeigten Fingerabdruck mit `gpg --fingerprint` vergleichen.

## 5. Veröffentlichen

Die Änderung zuerst auf `beta`, dann als Release auf `main`. Der Release-Workflow signiert das Paket, prüft, dass die
Signatur vom hinterlegten Schlüssel stammt, und bricht ab, falls das Secret fehlt.

## Was sich für Nutzer ändert

| | vorher | mit Schlüssel |
|---|---|---|
| Installer (`install.sh`) | lädt das Paket über HTTPS | prüft zusätzlich Schlüssel-Fingerabdruck und Paket-Signatur |
| `/etc/pacman.conf` | `SigLevel = Optional TrustAll` | `SigLevel = Required DatabaseOptional` (der Installer stellt alte Einträge um) |
| Update in Tuxdex, Kanal **Stabil** | baut aus dem Branch `main` | lädt das Release-Paket und installiert es nur mit gültiger Signatur |
| Update in Tuxdex, Kanal **Beta** | baut aus dem Branch `beta` | unverändert – Beta-Stände sind nicht signiert, Tuxdex weist darauf hin |

Wer das Repository von Hand eingetragen hat, importiert den Schlüssel einmal selbst:

```bash
curl -fsSL https://raw.githubusercontent.com/VoxellabDE/Tuxdex/main/tuxdex-signing-key.asc -o /tmp/tuxdex.asc
gpg --show-keys /tmp/tuxdex.asc          # Fingerabdruck mit dem aus dem README vergleichen
sudo pacman-key --add /tmp/tuxdex.asc
sudo pacman-key --lsign-key <FINGERABDRUCK>
```

## Schlüssel tauschen

Neuen Schlüssel erzeugen, Schritte 3–5 wiederholen. Bestehende Installationen kennen den neuen Fingerabdruck erst
nach einem Update, das noch mit dem **alten** Schlüssel signiert ist – deshalb zuerst eine Version veröffentlichen,
die den neuen Fingerabdruck enthält, und erst danach das Secret tauschen.
