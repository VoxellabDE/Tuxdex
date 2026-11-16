# Sicherheit / Security

**Deutsch:** Sicherheitslücken bitte **nicht** als öffentliches Issue melden, sondern per Mail an **contact@voxellab.de** – mit Version, Schritten zum Nachstellen und möglichen Folgen. Du bekommst eine Antwort, sobald die Meldung geprüft ist.

**English:** Please do **not** report security issues as public issues. Send an email to **contact@voxellab.de** with the version, steps to reproduce and the possible impact. You'll get a reply once the report has been reviewed.

Tuxdex ist in der Beta-Phase. Aktionen mit root-Rechten laufen nur nach einmaliger Zustimmung und immer sichtbar im Ausgabefeld.
Tuxdex is in beta. Actions with root privileges only run after a one-time consent and are always shown in the output box.

## Stand der Absicherung / Current state

- **Pakete:** noch unsigniert (`SigLevel = Optional TrustAll`). Signaturprüfung in Installer, pacman-Repository und Aktualisierung ist eingebaut und wird aktiv, sobald der Schlüssel hinterlegt ist – siehe [docs/SIGNING.md](docs/SIGNING.md).
- **Beta-Kanal:** wird direkt aus dem Branch `beta` gebaut und ist nicht signiert.
- **Eigenes Update-Repository:** Wer in den Einstellungen ein anderes GitHub-Repository einträgt, lässt dessen Code bauen und mit root-Rechten installieren. Tuxdex fragt in diesem Fall vor jedem Update ausdrücklich nach.
- **Prüfungen:** Jeder Push läuft durch Linter, Tests und Paketbau ([CI](https://github.com/VoxellabDE/Tuxdex/actions/workflows/ci.yml)); ohne grüne Prüfungen entsteht kein Release.

*Packages are not signed yet; signature verification is built in and activates once the key is published. The beta channel is built from the `beta` branch and is unsigned. Using a custom update repository means building and installing its code as root – Tuxdex asks explicitly before each such update.*
