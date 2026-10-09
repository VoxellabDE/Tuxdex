# Changelog

## 1.3.0
Systemwiederherstellung und Einrichten für Umsteiger.

- **Neues Modul „Wiederherstellung“**: Snapshots des Systems mit snapper (btrfs) oder Timeshift. Einrichten per Klick (auf btrfs mit snap-pac, dann entsteht vor und nach jeder Paketänderung ein Snapshot), Liste aller Snapshots, Snapshot jetzt erstellen, löschen und **auf einen Stand zurücksetzen**. Vor dem Zurücksetzen legt Tuxdex einen Sicherheits-Snapshot des jetzigen Stands an. Zurücksetzen mit snapper ist gesperrt, wenn /home nicht in einem eigenen Subvolume liegt – sonst würden eigene Dateien mit zurückgesetzt.
- **Snapshot vor jedem Update**: Ist die Wiederherstellung eingerichtet, legt Tuxdex direkt vor „Update starten“ einen Snapshot an (abschaltbar; entfällt, wenn snap-pac oder timeshift-autosnap das schon tun).
- **Neues Modul „Einrichten“**: Basics mit einem Klick – Schriften für Office-Dokumente, Audio- und Video-Codecs, Energieprofile mit Umschalter (Energiesparen, Ausgewogen, Leistung). Dazu **„Ersatz für Windows-Programme“**: „Photoshop“, „Office“, „Outlook“ & Co. eingeben und die passende Linux-Alternative direkt installieren (Arch-Paketquellen oder Flathub).
- **Standard-Apps**: Browser, E-Mail, PDF, Bilder, Videos, Musik und Textdateien per Auswahl festlegen (gilt sofort, ohne root).
- **Spiele-Setup**: Steam, GameMode, MangoHud, Lutris, Heroic, Bottles und Wine einzeln oder als Empfehlung installieren. Fehlt die 32-Bit-Unterstützung (multilib), schaltet Tuxdex sie auf Wunsch ein (mit Sicherung der pacman.conf und anschließendem vollem Update) – sonst kommt Steam von Flathub. Dazu ein Hinweis, dass Spiele mit Kernel-Anti-Cheat (Valorant, League of Legends, Fortnite) nicht laufen.
- **CachyOS**: CachyOS-Kernel (linux-cachyos, -bore, -lts, -hardened …) werden als Kernel erkannt – für Update-Markierung, Neustart-Hinweis, Versionsstand und Checkliste.

## 1.2.0
Module als Baukasten, klarere Zielgruppe, Roadmap.

- **Einstellungen → Module**: Tuxdex lässt sich jetzt selbst zusammenstellen. Jedes Modul außer Updates kann entfernt und wieder hinzugefügt werden; abgewählte Module verschwinden aus der Leiste und werden nicht geladen.
- **Swap, Antivirus und Benutzer sind anfangs ausgeblendet** – sie sind eher für Fortgeschrittene, und ClamAV bringt auf Linux-Desktops wenig. Wer sie nutzt: unter Einstellungen → Module wieder hinzufügen. Links aus anderen Modulen (z. B. aus dem Sicherheits-Check) öffnen sie weiterhin.
- **README**: neuer Abschnitt „Für wen ist Tuxdex?“ (Umsteiger von Windows, GUI-Fans) und eine **Roadmap** mit Zielen – Lernsoftware für Arch-Befehle, fertige ISOs mit einfachem Installer, eigenes Sicherheits-Werkzeug statt ClamAV, Modul-Markt.

## 1.1.3
Reifegrad sichtbar.

- Tuxdex zeigt seinen Reifegrad jetzt an der Version: **1.1.3-alpha** (Statusleiste, Updater, Einstellungen, GitHub-Release, README). Die Versionsnummer selbst bleibt ohne Zusatz, damit Updates und pacman richtig vergleichen.
- Update-Kanal „Vollversion“ heißt jetzt **„Stabil“** – passt besser zu einer Alpha. Beta bleibt Beta.

## 1.1.2
Englischer Changelog.

- **Updater**: Ist Englisch eingestellt, erscheint die Liste der Änderungen jetzt auf Englisch (`CHANGELOG.en.md`) statt als halb übersetzte Mischung.
- GitHub-Releases enthalten die Notizen auf Deutsch und Englisch.
- „Installiert: … (Vollversion)“ im Updater wird ebenfalls übersetzt.

## 1.1.1
Fehlerbehebungen.

- **Checkliste lud nicht mehr**, nachdem „Auf 500 MB / 1 Monat“ beim System-Protokoll benutzt wurde: Bei restriktiver umask legte Tuxdex den Ordner `/etc/systemd/journald.conf.d` ohne Leserecht an, und die Checkliste brach mit „Keine Berechtigung“ ab. Nicht lesbare Konfig-Ordner werden jetzt übersprungen, und Tuxdex legt Ordner und Dateien immer lesbar an (755/644), auch bei Speicherabbildern und I/O-Scheduler. Wer den Fehler schon hat: den Knopf einfach noch einmal drücken, dann werden die Rechte repariert.
- **Sprachwechsel**: Die Rückfrage nach dem Neustart ist nicht mehr halb Deutsch, halb Englisch.

## 1.1.0
Sicherheit, Englisch und ein eigenes pacman-Repository.

**Sicherheit**
- **Alpha-Hinweis**: Vor der ersten Aktion mit root-Rechten erscheint einmal ein Hinweis („Alpha-Phase, Nutzung auf eigenes Risiko“). Erst nach Haken und „Akzeptieren“ führt Tuxdex root-Befehle aus – ohne Zustimmung wird jede root-Aktion abgebrochen, auch intern. Dazu ein „ALPHA“-Abzeichen in der Kopfleiste und ein Hinweis im Passwort-Fenster.
- **Sicherheits-Review aller root-Aktionen:**
  - Swapfile anlegen überschreibt keine vorhandenen Dateien mehr (vorher hätte ein Tippfehler im Pfad eine beliebige Datei als root mit Nullen überschrieben). Gesperrt sind Systemordner (/etc, /usr, /boot …), Verknüpfungen und Pfade mit „..“; eine vorhandene Datei muss nachweislich ein Swapfile sein. Auf btrfs wird das Swapfile korrekt mit `btrfs filesystem mkswapfile` angelegt.
  - Swap entfernen löscht nur noch die fstab-Zeile, deren erstes Feld genau der Pfad ist (vorher jede Zeile, die den Text enthielt), und legt vorher `/etc/fstab.tuxdex.bak` an.
  - Formatieren: Die letzte Prüfung vor `mkfs` erkennt jetzt auch eingehängte Partitionen, geöffnete LUKS-Container, LVM und aktiven Swap unterhalb des gewählten Geräts.
  - Quarantäne: Wiederhergestellte Systemdateien bekommen ihren ursprünglichen Besitzer und ihre Rechte zurück (nie mit setuid-Bits); das Ziel darf nicht existieren.
  - Hilfsdateien liegen nicht mehr in einem vorhersagbaren Ordner in /tmp, sondern in /run/user/<uid> bzw. ~/.cache.
  - Datenträger-Bezeichnungen dürfen nicht mit „-“ beginnen (wäre sonst als Befehlsoption gelesen worden).
- Sicherheitslücken bitte per Mail melden – siehe `SECURITY.md`.

**Englisch**
- Tuxdex gibt es jetzt auch auf Englisch. Standard ist die Systemsprache; umstellen unter Einstellungen → Sprache · Language (wirkt nach einem Neustart). Befehlsausgaben bleiben, wie sie sind.
- Englisches README (`README.en.md`) mit Umschalter oben in beiden READMEs.

**Installation & Projekt**
- **Eigenes pacman-Repository**: Jedes Release enthält das fertige Paket und eine Repo-Datenbank. Mit `[tuxdex]` in `/etc/pacman.conf` installiert und aktualisiert sich Tuxdex über `pacman -Syu` – ohne AUR (Anleitung im README).
- Issue-Vorlagen für Fehlerberichte und Ideen (deutsch/englisch).
- Neue Screenshots (deutsch und englisch), erzeugt mit Beispieldaten über `tools/screenshots.py`.
- Der Updater zeigt beim Aktualisieren nur echte neue Versionen, keine Vorabversionen aus dem Changelog.

## 1.0.0
Erstes offizielles Release. Die Versionszählung beginnt neu – die Einträge darunter sind die Vorabversionen bis 1.6.0-beta.12.

- **11 Module** in einem Fenster: Updates, Software, Flatpak (Rechte per Schalter), Datenträger, Speicher, Backup, Swap, Taskmanager, Antivirus (ClamAV), Sicherheit, Benutzer.
- **Backup**: Snapshots, Spiegel oder Archive auf mehrere Ziele gleichzeitig, eigene Namen mit Datum, Zeitplan, Wiederherstellen, Paketliste im Backup.
- **Taskmanager**: Prozesse nach Programm gruppiert, Leistungs-Kacheln mit Details per Klick (CPU, RAM, Datenträger, Grafik, Netzwerk, Akku, Lüfter), Autostart und Bootzeit.
- **Sicherheit**: Sicherheits-Check, Checkliste für Wartung, Datenschutz & Performance, DNS-Leak-Test, offene Ports, Mullvad VPN, Firewall.
- Durchgehend im Tuxdex-Design, auch in allen Pop-ups; eigene Farbpalette unabhängig vom Desktop-Theme.
- Hinweis für Installationen vor 1.0.0: Weil die Zählung neu beginnt, bietet der eingebaute Updater 1.0.0 nicht von selbst an. Einmal neu installieren (siehe README → Installation); danach funktionieren Updates wieder normal. Das PKGBUILD setzt `epoch=1`, damit pacman 1.0.0 nicht als Downgrade ansieht.

## Vorabversionen (vor 1.0.0)

Entwicklungsstände mit der alten Zählung, zusammengefasst in 1.0.0.

### Vorabversion 1.6.0-beta.12
- Speicher → Typische Platzfresser & Aufräumen: **(i) neben jedem Eintrag** – beim Drüberfahren oder per Klick steht, was dort liegt, welcher Befehl beim Knopf läuft (z. B. `paccache -rk2`, `pacman -Rns`, `journalctl --vacuum-size=200M`) und was erhalten bleibt. Bei „nur Anzeige“ steht, wie man selbst aufräumt.

### Vorabversion 1.6.0-beta.11
- Dialoge unter KDE: Texte stehen nicht mehr auf dunkleren Kästen. Tuxdex setzt jetzt eine eigene Farbpalette, statt die Fensterfarben des Desktop-Themes (Breeze) zu übernehmen – gilt auch für Menüs, Tooltips und Auswahlfarben.

### Vorabversion 1.6.0-beta.10
- Sicherheit: neue **Checkliste für Wartung, Datenschutz & Performance** – prüft automatisch und bietet, wo sinnvoll, einen Knopf zum Beheben:
  - **Pakete & Updates**: Paketsignaturen (SigLevel), Alter der Mirrorliste (reflector), Fehler/Warnungen beim letzten Update in pacman.log, Neustart nach Kernel-Update.
  - **Zugriff**: sudo-Regeln mit NOPASSWD, riskante Gruppen (docker, disk …), Bildschirmsperre (KDE/GNOME), AppArmor, usbguard.
  - **Datenschutz**: Größe des System-Protokolls (auf 500 MB / 1 Monat begrenzen), Core Dumps abschalten, Passwörter/Tokens im Shell-Verlauf (nur gezählt), Telemetrie in VS Code.
  - **Kernel**: ASLR; der Kernel-Schutz setzt jetzt auch `dmesg_restrict` und `kptr_restrict`.
  - **Backup**: Alter des letzten Backups, ob /etc und Home gesichert werden, Paketliste, Btrfs-Snapshots.
  - **Performance**: TRIM-Timer, I/O-Scheduler je Laufwerkstyp, Swap/Swappiness, CPU-Regler, /tmp als tmpfs.
  - **Laufende Wartung**: Fehler im System-Protokoll seit dem Start, fehlgeschlagene Dienste, NTP, Reste alter Kernel-Module, verwaiste Pakete und Paket-Cache.
- Backup: speichert vor jedem Lauf die **Paketliste** (`~/.config/tuxdex/pakete.txt`, AUR getrennt) – Neuinstallation mit `pacman -S --needed - < pakete.txt`.

### Vorabversion 1.6.0-beta.9
- Backup: **Eigener Name für Sicherungen** mit Datum – z. B. `Laptop_yyyy-mm-dd` → `Laptop_2026-09-27`. Platzhalter `yyyy mm dd HH MM SS`, Text davor oder danach frei wählbar; Vorschau direkt unter dem Feld. Gilt für Snapshots und Archive, auch für geplante Backups. Doppelte Namen bekommen `_2`, `_3` …
- Sortieren und Aufräumen alter Versionen nach dem echten Sicherungszeitpunkt, auch bei frei gewählten Namen.
- Anleitung in der README unter „Backups benennen“.

### Vorabversion 1.6.0-beta.8
- Mullvad: Kontonummer ist jetzt **komplett verdeckt**; das Auge daneben blendet sie ein und wieder aus.
- Backup → Fortschritt: Statusfeld je Ziel passt sich dem Text an (war fest 120 px breit); Tempo lesbar als „16.45 MB/s“.

### Vorabversion 1.6.0-beta.7
- Taskmanager → Leistung: **Kacheln anklicken für Details** (live, alle 2 s; erneut klicken oder „Schließen“ blendet aus):
  - **Prozessor**: Geschwindigkeit, Temperatur, Betriebszeit, Prozesse/Threads/Handles, Last; Modell, Basis- und Maximaltakt, Sockel, Kerne, virtuelle Prozessoren, Virtualisierung (KVM / AMD-V / VT-x), virtuelle Maschine, L1/L2/L3-Cache, CPUfreq-Treiber und -Regler, Energiemodus, Boost.
  - **Arbeitsspeicher**: in Verwendung, verfügbar, zugesichert, im Cache, Swap, zram komprimiert/Ersparnis; Takt (MT/s), belegte Steckplätze, Formfaktor, Typ (z. B. LPDDR5).
  - **Datenträger** (Auswahl je Laufwerk): Lese-/Schreibtempo, aktive Zeit, Antwortzeit, Summen seit Start, Temperatur; Modell, Kapazität, formatiert, Systemdatenträger, Typ, WWN, Seriennummer, Partitionen mit Belegung.
  - **Grafik** (Auswahl je Karte): Auslastung, Takt, Leistungsaufnahme, VRAM, Speichertakt, Video kodieren/dekodieren (NVIDIA), Temperatur, Lüfter; Treiber, OpenGL-/Vulkan-Version, PCIe-Geschwindigkeit, PCI-Adresse.
  - **Netzwerk** (je Schnittstelle), **Akku** (Zyklen, Zustand, Spannung, Ladegrenze), **Swap** (Geräte, Priorität, Swappiness) und **System & Lüfter** (Drehzahlen aller Lüfter, Temperaturen, Kernel, Startzeit).
- Alles ohne root; Werte, die das System nicht meldet, stehen als „—“.

### Vorabversion 1.6.0-beta.6
- **Pop-ups überarbeitet**: kein schwarz hinterlegter Text mehr in Hinweis-, Warn- und Rückfrage-Fenstern (trat unter KDE auf).
- Flache Symbole in den Tuxdex-Farben statt der Symbole des System-Themes; Buttons in allen Pop-ups im Tuxdex-Stil (Hauptaktion farbig, Abbrechen links, Aktion rechts), mehr Innenabstand.
- Verschlüsseltes Backup wiederherstellen: Passwort-Abfrage im Tuxdex-Stil und auf Deutsch statt des englischen Standardfensters.

### Vorabversion 1.6.0-beta.5
- Taskmanager → **Autostart**:
  - Autostart-Programme per Schalter an/aus, eigene Einträge entfernen, installierte Programme hinzufügen. System-Einträge bleiben unangetastet – Tuxdex legt nur eine eigene Einstellung in `~/.config/autostart` an.
  - Hintergrunddienste des Benutzers (`systemd --user`) an/aus.
  - **Bootzeit**: Dauer des letzten Starts, aufgeteilt in Firmware, Bootloader, Kernel, Initramfs und Dienste, dazu die langsamsten Dienste. Bekannte Bremsen wie `NetworkManager-wait-online` lassen sich per Knopf deaktivieren.

### Vorabversion 1.6.0-beta.4
- Taskmanager: **Prozesse nach Programm gruppiert** – jedes Programm ist ein aufklappbarer Ordner mit Summe für CPU, Arbeitsspeicher und Datenträger (z. B. „Spotify (3)“). „Alle beenden“ / „Alle erzwingen“ beendet alle Prozesse eines Ordners auf einmal, auch Priorität gilt für alle. Aufgeklappte Ordner bleiben beim Aktualisieren offen; abschaltbar über „Nach Programm gruppieren“.

### Vorabversion 1.6.0-beta.3
- **Weniger RAM**: Tabs werden erst beim ersten Öffnen gebaut und nach 5 Minuten ohne Nutzung wieder abgebaut (nie während ein Scan, Backup oder Befehl läuft). Freigegebener Speicher geht ans System zurück. Start: ~83 statt ~118 MB.
- **Keine verwaisten Scans mehr**: Beim Schließen oder Neustart beendet Tuxdex alle gestarteten Hintergrundprozesse (vorher Rückfrage, wenn noch etwas läuft). Läuft beim Start noch ein Virenscan oder Backup aus einer früheren Sitzung, bietet Tuxdex an, ihn zu beenden.
- Taskmanager: root-Prozesse, die Tuxdex gestartet hat, heißen jetzt z. B. „clamscan · gestartet von Tuxdex“ – ihr Speicher wird nicht mehr Tuxdex selbst zugerechnet.
- Software-Liste: Tooltips nur noch in der Beschreibungsspalte (weniger Speicher bei vielen Paketen).

### Vorabversion 1.6.0-beta.2
- Vollversion/Beta als Umschalter mit Versionsanzeige (aus 1.5.6).

### Vorabversion 1.6.0-beta.1
- **Neues Modul „Backup“**:
  - Mehrere Ziele gleichzeitig (USB-Platten, interne Laufwerke, Ordner) – jedes mit eigenem Fortschritt, Tempo und Restzeit.
  - **Snapshots**: jede Sicherung eine eigene Version, unveränderte Dateien kosten keinen Platz (Hardlinks, wie Time Machine).
  - **Spiegel**: 1:1-Kopie, überträgt nur Änderungen.
  - **Archiv**: komprimiert mit zstd, xz oder gzip (3 Stärken), optional mit Passwort (AES-256). Wird einmal gepackt und parallel auf alle Ziele geschrieben, mit Prüfsumme und Prüfung nach dem Schreiben. Auf FAT32 automatisch in 4-GB-Teile geteilt.
  - Ausnahmen (z. B. `~/.cache`), Versionen behalten (3–50), root-Modus für Systemordner.
  - Vorhandene Backups je Ziel anzeigen, öffnen, löschen und wiederherstellen – in einen Ordner oder an den Originalort.
  - Zeitplan täglich/wöchentlich per systemd-Timer (`tuxdex --backup`), läuft auch ohne Fenster und holt verpasste Termine nach.
- Taskmanager → System: **Versionsstand** von Grafiktreiber (NVIDIA/Mesa/Vulkan), CPU-Microcode, Mainboard/BIOS (mit Alter), Kernel und Firmware (fwupd) – inkl. „Update da“ und „Neustart nötig“.
- Sicherheit: **Leak-Test & VPN-Erkennung** – DNS-Leak-Test, welche DNS-Server Webseiten sehen, ob die IP als VPN (mit Anbieter), Proxy, Tor oder Rechenzentrum erkannt wird, und ob sie auf Sperrlisten steht.
- Sicherheits-Check zeigt einen eingestellten **Proxy** (Umgebungsvariablen, GNOME, KDE).
- Sicherheits-Check zeigt den aktuellen **DNS-Server**: Anbieter (z. B. Router, Cloudflare, Mullvad), Verbindung und ob die Anfragen verschlüsselt (DNS-over-TLS) oder durch den VPN-Tunnel laufen.
- Sicherheits-Check: **Bekannte Sicherheitslücken** über `arch-audit` (optional) – zeigt, welche Pakete ein Update mit Fix haben.
- Mullvad verbunden, aber Kill-Switch aus: Hinweis mit Knopf „Kill-Switch an“.
- Virenscan: Hochrechnung ohne die Ladezeit der Signaturen, dazu voraussichtliches Ende (Uhrzeit) und Gesamtdauer.
- Speicher → „Größen ermitteln“: misst parallel, zeigt jeden Wert sofort und einen Status wie beim Virenscan (läuft/fertig, was gerade gemessen wird, Fortschritt). Ordner, die länger als 2 Minuten brauchen, werden als „zu viele Dateien“ markiert statt alles zu blockieren.

### Vorabversion 1.5.6
- Einstellungen → Aktualisierung: Vollversion/Beta als Umschalter statt Aufklappmenü. Daneben steht, welche Version es jeweils gibt und welche installiert ist.

### Vorabversion 1.5.5
- Einstellungen → Aktualisierung: Auswahl **Vollversion** oder **Beta** – Beta-Versionen bekommen neue Funktionen früher. Zurück zur Vollversion geht jederzeit.

### Vorabversion 1.5.4
- Sicherheits-Check prüft zusätzlich:
  - **CPU-Microcode** (`intel-ucode`/`amd-ucode`) – fehlt er, per Klick installieren.
  - **Swap-Verschlüsselung** – warnt, wenn Swap unverschlüsselt auf der Platte liegt (zram und Swap auf LUKS gelten als sicher).
  - **Kernel-Schutz** – sperrt per Klick kexec und SysRq (`/etc/sysctl.d/90-tuxdex-hardening.conf`), ohne Nachteile im Alltag.

### Vorabversion 1.5.3
- Aktualisierung startet erst nach der Admin-Anmeldung (sudo-Passwort). Ohne Anmeldung wird nichts heruntergeladen oder verändert – gilt für GitHub, Datei und Ordner.
- Neuer Fortschrittsbalken mit Schritt-Anzeige: Dateien laden (x/n), Paket bauen, prüfen, installieren, fertig. Bei Fehlern zeigt er, an welcher Stelle es hakt.

### Vorabversion 1.5.2
- VPN-Erkennung korrigiert: Eine pausierte oder gestoppte Verbindung (z. B. `tailscale down`) gilt nicht mehr als „verbunden“, auch wenn die Schnittstelle noch existiert.
- Tailscale wird über seinen Status erkannt: ohne Exit-Node als „Tailscale an“ (Internet läuft direkt), mit Exit-Node als aktives VPN.
- Andere VPNs (WireGuard, OpenVPN …) zählen nur mit Adresse; Split-Tunnel wird erkannt und angezeigt.
- Der VPN-Status im Tab „Sicherheit“ aktualisiert sich alle 10 Sekunden.

### Vorabversion 1.5.1
- Beim Start sucht Tuxdex automatisch nach **System-Updates** (pacman, AUR, Flatpak). Die Anzahl erscheint am Tab „Updates“ und unten rechts – rot, wenn wichtige Updates dabei sind; ein Klick öffnet den Tab.
- Einstellungen → System-Updates: automatische Prüfung beim Start an/aus. Installiert wird weiterhin nur auf Klick.

### Vorabversion 1.5.0
- Beim Start sucht Tuxdex automatisch nach Updates und bietet eine neue Version in einem Fenster an – mit den Neuerungen und „Jetzt aktualisieren“ / „Später“.
- Einstellungen → Aktualisierung: automatische Prüfung beim Start an/aus, Update-Fenster an/aus (sonst nur Hinweis unten rechts).
- ClamAV und Mullvad VPN sind rein optional: Tuxdex bietet keine Installation und keine Links mehr an. Fehlen sie, zeigt der Bereich nur einen neutralen Hinweis.

### Vorabversion 1.4.2
- Updater erkennt neue Versionen sofort: Tuxdex fragt den neuesten Commit direkt ab, statt die bis zu 5 Minuten zwischengespeicherten Dateien von raw.githubusercontent.com zu lesen.
- Update über GitHub lädt die Paketdateien gezielt vom neuesten Commit (robuster als das Archiv).
- Neuerungen im Updater zeigen `Code` und Sonderzeichen korrekt an.

### Vorabversion 1.4.1
- Update-Quelle ist fest auf github.com/PyloGER/Tuxdex eingestellt – der Updater findet neue Versionen jetzt ohne Einrichtung.
- Einstellungen: Autor PyloGER, Unternehmen Voxellab, Kontakt contact@voxellab.de, Link zur Projektseite, Hinweis „AI made – mit Claude“.
- Pfade im Home-Ordner werden als `~/…` angezeigt – ohne Benutzernamen.

### Vorabversion 1.4.0
- **Updater** in den Einstellungen: nach Updates auf GitHub suchen (auch automatisch beim Start), Neuerungen anzeigen, per Klick aktualisieren und neu starten.
- Lokal aktualisieren aus einem Archiv (tuxdex-X.Y.Z.tar.gz) oder einem Ordner mit PKGBUILD – Git-Klone werden vorher mit `git pull` aktualisiert.
- Hinweis „Version X verfügbar“ unten rechts in der Statusleiste.

### Vorabversion 1.3.0
- Neuer Tab **Flatpak**: Rechte jeder App per Schalter einstellen (wie Flatseal) – Netzwerk, Dateien, Geräte, Ton/Bildschirm, Umgebungsvariablen, Portal-Freigaben, globale Regeln; riskante Rechte markiert.
- **Software**: Auswahl per Kästchen (kein Strg mehr), Icons, Version, Größe, Quelle/Ort, Installationsdatum, Details mit Abhängigkeiten.
- **ClamAV**: Live-Fortschritt mit Dateien, Datenmenge, Tempo und Restzeit; Status „läuft / arbeitet / hängt?“.
- **Updates**: AUR steht in den Quellen jetzt unten.

### Vorabversion 1.2.0
- Neuer Name: **Tuxdex** (vorher „Arch Linux Manager“ / `arch-manager`). Das Paket ersetzt die alte Version automatisch, gespeicherte Daten und die Quarantäne werden übernommen.
- Offene Ports lassen sich im Tab *Sicherheit* per Knopf sperren oder freigeben (ufw, firewalld).
- Einstellungen (Zahnrad unten links) mit Infos zu Projekt, Autor und Technik.
- ClamAV: sichtbarer Fortschritt beim Laden der Signaturen, Fehler des Download-Servers werden sofort gemeldet.

### Vorabversion 1.0.1
- ClamAV-Signaturen werden bei deutscher Systemsprache korrekt erkannt.

### Vorabversion 1.0.0
- Erste Version als installierbares Paket: Updates, Software, Datenträger, Speicher, Swap, Taskmanager, Antivirus, Sicherheit (Mullvad VPN, Firewall), Benutzer.
