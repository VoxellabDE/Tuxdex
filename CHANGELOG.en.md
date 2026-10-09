# Changelog

English version of [CHANGELOG.md](CHANGELOG.md). Entries before 1.0.0 are only available in German.

## 1.3.0
System restore and setup for switchers.

- **New module "Restore"**: system snapshots with snapper (btrfs) or Timeshift. One-click setup (on btrfs with snap-pac, so a snapshot is created before and after every package change), list of all snapshots, create a snapshot now, delete, and **roll back to a state**. Before rolling back, Tuxdex creates a safety snapshot of the current state. Rollback with snapper is blocked when /home isn't on its own subvolume – otherwise your own files would be rolled back too.
- **Snapshot before every update**: once restore is set up, Tuxdex creates a snapshot right before "Start update" (can be turned off; skipped when snap-pac or timeshift-autosnap already do it).
- **New module "Setup"**: one-click basics – fonts for Office documents, audio and video codecs, power profiles with a switch (power saver, balanced, performance). Plus **"Alternatives for Windows programs"**: type "Photoshop", "Office", "Outlook" and so on and install the matching Linux alternative right away (Arch repositories or Flathub).
- **Default apps**: choose browser, email, PDF, images, videos, music and text files (applies right away, no root).
- **Gaming setup**: install Steam, GameMode, MangoHud, Lutris, Heroic, Bottles and Wine one by one or as a recommended set. If 32-bit support (multilib) is missing, Tuxdex turns it on when asked (backing up pacman.conf, followed by a full update) – otherwise Steam comes from Flathub. Plus a note that games with kernel anti-cheat (Valorant, League of Legends, Fortnite) don't run.
- **CachyOS**: CachyOS kernels (linux-cachyos, -bore, -lts, -hardened …) are recognized as kernels – for update marking, restart notice, version status and checklist.

## 1.2.0
Modules as a toolkit, clearer audience, roadmap.

- **Settings → Modules**: you can now put Tuxdex together yourself. Every module except Updates can be removed and added back; removed modules disappear from the bar and aren't loaded.
- **Swap, Antivirus and Users are hidden at first** – they're more for advanced users, and ClamAV is of little use on Linux desktops. If you use them: add them back under Settings → Modules. Links from other modules (e.g. from the security check) still open them.
- **README**: new section "Who is Tuxdex for?" (people coming from Windows, GUI fans) and a **roadmap** with goals – learning software for Arch commands, ready-made ISOs with a simple installer, an own security tool instead of ClamAV, a module market.

## 1.1.3
Maturity shown.

- Tuxdex now shows its maturity with the version: **1.1.3-alpha** (status bar, updater, settings, GitHub release, README). The version number itself stays without a suffix so updates and pacman compare correctly.
- Update channel "full version" is now called **"Stable"** – fits an alpha better. Beta stays beta.

## 1.1.2
English changelog.

- **Updater**: with English selected, the list of changes is now shown in English (`CHANGELOG.en.md`), no longer as a half-translated mix.
- GitHub releases contain the notes in German and English.
- "Installed: … (full version)" in the updater is translated too.

## 1.1.1
Bug fixes.

- **Checklist stopped loading** after using "To 500 MB / 1 month" for the system log: with a restrictive umask, Tuxdex created the folder `/etc/systemd/journald.conf.d` without read permission, and the checklist failed with "Permission denied". Unreadable config folders are now skipped, and Tuxdex always creates folders and files readable (755/644), also for core dumps and the I/O scheduler. If you already have the error: just press the button once more and the permissions get repaired.
- **Language switch**: the restart prompt is no longer half German, half English.

## 1.1.0
Security, English and a pacman repository of its own.

**Security**
- **Alpha notice**: before the first action with root rights, a notice appears once ("alpha stage, use at your own risk"). Tuxdex only runs root commands after you tick the box and click "Accept" – without consent every root action is cancelled, internally too. Plus an "ALPHA" badge in the top bar and a note in the password dialog.
- **Security review of all root actions:**
  - Creating a swapfile no longer overwrites existing files (before, a typo in the path could have overwritten any file with zeros as root). System folders (/etc, /usr, /boot …), symlinks and paths with ".." are blocked; an existing file must verifiably be a swapfile. On btrfs the swapfile is created correctly with `btrfs filesystem mkswapfile`.
  - Removing swap only deletes the fstab line whose first field is exactly the path (before: every line containing the text) and creates `/etc/fstab.tuxdex.bak` first.
  - Formatting: the last check before `mkfs` now also detects mounted partitions, open LUKS containers, LVM and active swap below the selected device.
  - Quarantine: restored system files get their original owner and permissions back (never with setuid bits); the target must not exist.
  - Helper files no longer live in a predictable folder in /tmp, but in /run/user/<uid> or ~/.cache.
  - Drive labels must not start with "-" (would otherwise have been read as a command option).
- Please report security issues by email – see `SECURITY.md`.

**English**
- Tuxdex is now available in English. The default is the system language; switch under Settings → Language (takes effect after a restart). Command output stays as it is.
- English README (`README.en.md`) with a switch at the top of both READMEs.

**Installation & project**
- **pacman repository of its own**: every release contains the finished package and a repo database. With `[tuxdex]` in `/etc/pacman.conf`, Tuxdex installs and updates via `pacman -Syu` – no AUR needed (instructions in the README).
- Issue templates for bug reports and ideas (German/English).
- New screenshots (German and English), generated with sample data via `tools/screenshots.py`.
- When updating, the updater only shows real new versions, no pre-releases from the changelog.

## 1.0.0
First official release. Version numbering starts over – earlier entries are the pre-releases up to 1.6.0-beta.12.

- **11 modules** in one window: Updates, Software, Flatpak (permissions via switches), Drives, Storage, Backup, Swap, Task manager, Antivirus (ClamAV), Security, Users.
- **Backup**: snapshots, mirrors or archives to several targets at once, custom names with date, schedule, restore, package list in the backup.
- **Task manager**: processes grouped by program, performance tiles with details on click (CPU, RAM, drives, graphics, network, battery, fans), autostart and boot time.
- **Security**: security check, checklist for maintenance, privacy & performance, DNS leak test, open ports, Mullvad VPN, firewall.
- Tuxdex design throughout, in all pop-ups too; its own color palette independent of the desktop theme.
- Note for installations before 1.0.0: because numbering starts over, the built-in updater does not offer 1.0.0 by itself. Reinstall once (see README → Installation); after that updates work normally again. The PKGBUILD sets `epoch=1` so pacman doesn't treat 1.0.0 as a downgrade.
