# Changelog

English version of [CHANGELOG.md](CHANGELOG.md). Entries before 1.0.0 are only available in German.

## 1.6.0.1
Drop-down lists (beta).

- **Drop-downs**: all selection lists in Tuxdex now show up to **5 entries completely** and only scroll when there are more. At the bottom edge of the screen the list opens upwards instead of being squeezed into one or two half rows. Operation as usual: mouse, arrow keys, Enter, Esc.

## 1.6.0
Drivers, diagnosis and Windows data (beta).

Three new modules from the roadmap:

- **Drivers**: detects graphics card, Wi-Fi, Bluetooth and printers and sets up missing drivers with one click (pacman). NVIDIA with advice depending on the card generation (open driver from Turing, legacy hint for older ones), kernel-matching packages (also DKMS and CachyOS), hybrid laptops (`prime-run`); AMD and Intel with Vulkan and video acceleration; Wi-Fi with hints for Broadcom, Realtek and Intel firmware and unblocking airplane mode; Bluetooth (bluez, service, block); printers (CUPS, Avahi, HP).
- **Diagnosis**: troubleshooting in plain language for "Internet / Wi-Fi doesn't work", "No sound", "Bluetooth doesn't work", "Screen / graphics", "System starts slowly", "Storage is full", "Updates fail" and "Printer doesn't print". Every finding explains understandably what's going on and offers a fix button where possible. Plus a **device manager** with the driver for each device (yellow: no driver loaded).
- **Windows data**: finds NTFS/Windows drives, mounts them read-only (with clear hints for fast startup/hibernation and BitLocker) and takes over documents, pictures, videos, music, downloads and desktop – into a folder of its own or straight into your folders. Existing files are never overwritten. Detects dual boot and points to os-prober.
- The tab bar and lists now remove old rows immediately (no overlapping when redrawing quickly).

## 1.5.17.2
Login dialog (beta).

- **Pop-ups**: adjust their size after the labels are set up. In the login window the input field and buttons could otherwise be squeezed together when the window stayed too small.

## 1.5.17.1
Tab bar (beta).

- **Arrows ‹ ›** jump exactly **one tab** at a time, with a short animation. The step follows the actual width of the tabs (language, font size) instead of a fixed pixel count.

## 1.5.17
Default apps (beta).

- **Set up → Default apps**: the selection didn't take effect on many systems, especially under KDE. There, `kde-mimeapps.list` takes precedence over `mimeapps.list`, and `xdg-mime` only writes the latter. Tuxdex now writes the default app into all relevant files (including the one of the running desktop), removes it from the "Removed Associations" and then checks what really applies. The result is shown right below the selection – including a warning if the desktop overrides it.

## 1.5.16
Renaming (beta).

- The "USB stick" module is now called **"Flash stick"** (page heading "Flash stick") – "flashing" is the usual term for writing an image to a stick.

## 1.5.15
Tab bar (beta).

- **Small windows**: when not all modules fit in the bar, the names are no longer cut off. The tabs keep their full width, the bar can be scrolled (mouse wheel or the arrows ‹ ›), and the selected tab scrolls into view automatically. The arrows only appear when needed.

## 1.5.14
Language switch without restart (beta).

- **DE | EN**: the language changes immediately – Tuxdex rebuilds the window in the new language and stays in the same place (same tab, same window size and position). If an operation is running (update, backup, scan …), nothing is switched so its display isn't lost; the language then applies from the next start or after switching again.

## 1.5.13
Beta label.

- **Maturity**: the badge at the top of the bar now says **BETA** instead of ALPHA – likewise the notice before the first root action, README, SECURITY.md and the installer. A consent already given stays valid.

## 1.5.12
Version names (beta).

- **Naming scheme**: the stable version is simply "1.5.2", the beta branch "1.5.12-beta". The former "-alpha" after every number is gone. Tuxdex determines the channel of the installed version by comparing with the versions on GitHub (newer than stable = beta) and remembers the values offline. Applies to the status bar, settings, update window and GitHub releases (releases from `main` carry no suffix).

## 1.5.11
Bug fix (beta).

- **Settings → Updating**: the installed version was always labelled "(Stable)", even on the beta. Tuxdex now shows the correct channel – "Stable", "Beta" or "development build" (newer than the beta) – by comparing with the versions on GitHub.

## 1.5.10
Language (beta).

- **Settings → Language**: a **DE | EN** toggle replaces the drop-down list.

## 1.5.9
Install display (beta).

- **"Version … available"** (bottom right): one click opens the settings and jumps straight to the update panel – no scrolling. A second click closes the settings again.
- **Progress in %**: the display no longer jumps to 99% after a few seconds. Tuxdex now calculates a real overall progress from the output of pacman (download, checks, installation package by package) and Flatpak (package i of n). The number never jumps back; 100% only comes when the operation is finished.
- **Own bar per row**: while an installation runs, the program's row is framed and shows a progress bar below the description – so it's immediately clear which program is being installed. Applies to "Popular programs" and "Replacements for Windows programs".

## 1.5.8
Bug fix (beta).

- **Popular programs**: programs installed via Flatpak are now also recognised as installed (with a "Start" button) when the catalog offers them from the Arch repositories – and the other way round. If the ID differs (e.g. fre:ac), the name of the Flatpak app is compared as well.

## 1.5.7
Programs and USB sticks (beta).

- **Software → "Popular programs"**: 23 programs by category to install directly – Discord, Signal, Thunderbird, Obsidian, Super Productivity, KeePassXC, Bitwarden, VeraCrypt, Cryptomator, Brave, qBittorrent, FileZilla, LocalSend, OBS Studio, Spotify, fre:ac, Visual Studio Code, Wireshark, FreeCAD, KiCad, GNU Octave, Prism Launcher, PeaZip. With category filter, search, source on the button (pacman/Flatpak), progress in % and then "Start".
- **New module "USB stick"**: create bootable sticks. Choose **Arch Linux** (current ISO) and **Home Assistant OS** (PC, Raspberry Pi 5/4/3, Green) – or an **own file** (.iso, .img, .img.xz, .img.gz). Downloads are checked against their checksum, then the stick is read back and compared. Only removable drives are offered, never the disk with the system; a clear confirmation comes before overwriting.

## 1.5.6
Settings (beta).

- **Settings**: the "Updating" panel (Tuxdex updates) now sits directly below "System updates".

## 1.5.5
Updates while in use (beta).

- **Update window while Tuxdex is open**: Tuxdex checks in the background every 30 seconds for a new version and shows the update window as soon as one appears – no restart needed. The query is light (only the commit ID via the Git protocol, no rate limit); each version is offered only once.
- **Settings → Updating**: the regular check can be turned off and the interval adjusted (15 to 86400 seconds).

## 1.5.4
User groups (beta).

- **Assign groups**: the checkmarks in the group list weren't visible (checked looked like unchecked) – fixed.
- **Overview**: new "Groups" column as a drop-down – the count on top, below all groups of the user (primary group first).

## 1.5.3
Group management (beta).

- **Users → "Assign groups …"**: add or remove a user's groups with checkboxes. Well-known groups (wheel, video, audio, docker, libvirt …) are explained, with search and the option "Show all system groups". The primary group stays protected; for groups with administrator rights (wheel, sudo, docker) Tuxdex asks first. The change applies from the next login.

## 1.5.2
Bug fix.

- **Pop-ups**: checkboxes in dialogs no longer sit on a black box either.

## 1.5.1
Bug fix.

- **Antivirus**: automatic signature updates can be **turned off** again – the button switches between "enable" and "disable".

## 1.5.0
User management.

- **Add user**: name, full name, password, optionally administrator (group "wheel") and "change password at first login". The user name is validated, the password never shows up in the output field.
- **Change password** for any user, optionally forcing a change at the next login. The list shows **when the password was last changed** (and whether the account is locked).
- **Storage per user**: size of the home folder and memory of all running processes of the user.
- **Active time**: sum of all logins from the system log (wtmp); running sessions count too.
- **Description**: what the user is for (e.g. "office PC", "child account") – when creating or later. Tuxdex stores it only for itself under `~/.config/tuxdex`; nothing changes on the system.

## 1.4.2
Small improvements and bug fixes.

- **Firewall (ufw)**: rules get a name/label (stored as a ufw comment, shown in the rule list).
- **Power profile**: only profiles the machine really supports are offered – "Performance" is missing on many devices and caused an error message.
- **Prompts and text windows**: a "Copy" button for the whole output here too.
- **Pop-ups**: labels and icons no longer sit on a black box (also on KDE).
- **Antivirus**: ClamAV can be installed right in the tab (pacman), with progress in %.

## 1.4.1
Bug fixes and small improvements.

- **Firewall rules with IP address**: rules can now be limited to an IP address or network (e.g. allow SSH only from 192.168.1.20). Port and IP are both optional, at least one is required.
- **Install buttons**: show progress in % ("Installing … 42%") while installing and then turn into a blue **"Start"** button – so you see right away what is already installed (for programs with a window; console tools stay "Installed").
- **Backup**: on long runs with root rights the sudo ticket no longer expires (snapshots weren't completed otherwise). System folders like /etc: Tuxdex asks for root rights, incomplete backups are marked "Incomplete".
- **Output fields**: a "Copy" button copies the whole output.
- **Updater**: missing build tools (`base-devel`) are offered for installation; the update check at startup retries if the network isn't up yet.
- **Leak test**: an invalid reply from ipapi.is no longer causes a crash.

## 1.4.0
Mount drives permanently.

- **Drives → "Mount permanently …"**: a drive is mounted automatically at every start – without editing `/etc/fstab` by hand. Tuxdex adds the entry **by UUID**, picks suitable options per file system (ext4, btrfs, xfs, f2fs, NTFS, exFAT, FAT32) and sets `nofail` so the boot doesn't hang if the drive is ever missing. A backup is made first (`/etc/fstab.tuxdex.bak`); if mounting fails, the `fstab` is reset automatically. For Linux file systems the folder can be assigned to you so you can write to it.
- **"Remove permanent mount"** deletes the entry again. System entries (/, /boot, /home, swap) stay protected; mount points inside system folders (/etc, /usr, /var …) are rejected.

## 1.3.0
System restore and setup for switchers.

- **One-command installer**: `install.sh` adds the pacman repository and installs Tuxdex – one line to paste is enough (see README → Installation). It asks first, backs up `pacman.conf`, repairs the old repository address and offers `--dry-run`.
- **Repository moved** to **github.com/VoxellabDE/Tuxdex**: updater, PKGBUILD and README point to the new address, old settings are switched over automatically. If you use the pacman repo: update the `Server` line in `/etc/pacman.conf` (see README). Install instructions fixed (`pacman -Syu` instead of `-Sy`).
- **New module "Restore"**: system snapshots with snapper (btrfs) or Timeshift. One-click setup (on btrfs with snap-pac, so a snapshot is created before and after every package change), list of all snapshots, create a snapshot now, delete, and **roll back to a state**. Before rolling back, Tuxdex creates a safety snapshot of the current state. Rollback with snapper is blocked when /home isn't on its own subvolume – otherwise your own files would be rolled back too.
- **Snapshot before every update**: once restore is set up, Tuxdex creates a snapshot right before "Start update" (can be turned off; skipped when snap-pac or timeshift-autosnap already do it).
- **New module "Setup"**: one-click basics – fonts for Office documents, audio and video codecs, power profiles with a switch (power saver, balanced, performance). Plus **"Alternatives for Windows programs"**: type "Photoshop", "Office", "Outlook" and so on and install the matching Linux alternative right away (Arch repositories or Flathub).
- **Default apps**: choose browser, email, PDF, images, videos, music and text files (applies right away, no root).
- **Gaming setup**: install Steam, GameMode, MangoHud, Lutris, Heroic, Bottles and Wine one by one or as a recommended set. If 32-bit support (multilib) is missing, Tuxdex turns it on when asked (backing up pacman.conf, followed by a full update) – otherwise Steam comes from Flathub. Plus a note that games with kernel anti-cheat (Valorant, League of Legends, Fortnite) don't run.
- **Storage: pie chart** – what the used space consists of: operating system, applications, system data, photos & videos (incl. GIFs), other personal files and other, plus free space. Approximate values, more accurate when logged in.
- **Source always visible**: every install button shows where the program comes from – "Install (pacman)", "(Flatpak)" or "(AUR)", also in the Software tab and for the security tools.
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
