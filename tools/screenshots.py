#!/usr/bin/env python3
"""Erzeugt die README-Screenshots mit Beispieldaten – nichts davon stammt vom echten System.

    QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py [--lang en] [--out docs/screenshots]

Tuxdex läuft dabei mit einem eigenen, leeren Home-Ordner; Befehle wie pacman, flatpak, lsblk oder df
werden abgefangen und liefern feste Beispielausgaben. Braucht die Schrift IBM Plex (ttf-ibm-plex).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "de"
OUT = os.path.abspath(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv
                      else os.path.join(ROOT, "docs", "screenshots"))
W, H = 1440, 900

# ---- eigener Home-Ordner mit Beispiel-Einstellungen -------------------------------------------
HOME = tempfile.mkdtemp(prefix="tuxdex-demo-")
os.environ["HOME"] = HOME
os.environ.pop("XDG_DATA_HOME", None)
os.environ["XDG_RUNTIME_DIR"] = HOME
os.environ["LANG"] = "de_DE.UTF-8" if LANG == "de" else "en_US.UTF-8"
os.environ.pop("https_proxy", None)
os.environ.pop("http_proxy", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
for d in (".config/tuxdex", ".cache/tuxdex", ".local/share/tuxdex"):
    os.makedirs(os.path.join(HOME, d), exist_ok=True)
NOW = time.time()


def jwrite(rel, data):
    with open(os.path.join(HOME, rel), "w") as f:
        json.dump(data, f)


jwrite(".config/tuxdex/settings.json", {"lang": LANG, "alpha_accepted": "2026-09-27T10:00:00",
                                        "auto_check": False, "sys_check_on_start": False})
UPDATES = [
    {"name": "python", "source": "Pacman", "old": "3.12.6-1", "new": "3.13.0-1", "kind": "major"},
    {"name": "linux", "source": "Pacman", "old": "6.10.9.arch1-1", "new": "6.11.1.arch1-1", "kind": "system"},
    {"name": "systemd", "source": "Pacman", "old": "256.6-1", "new": "256.7-1", "kind": "system"},
    {"name": "firefox", "source": "Pacman", "old": "130.0.1-1", "new": "131.0-1", "kind": ""},
    {"name": "htop", "source": "Pacman", "old": "3.3.0-3", "new": "3.3.0-4", "kind": ""},
    {"name": "org.gimp.GIMP", "source": "Flatpak", "old": "—", "new": "2.10.38", "kind": ""},
    {"name": "visual-studio-code-bin", "source": "AUR", "old": "1.93.1-1", "new": "1.94.0-1", "kind": ""},
]
jwrite(".cache/tuxdex/updates.json", {"checked_at": NOW - 600, "updates": UPDATES})

# Backup-Ziel mit ein paar Snapshots
TARGET = os.path.join(HOME, "run", "media", "user", "BACKUP")
jwrite(".config/tuxdex/backup.json", {
    "sources": [HOME, "/etc"], "excludes": ["~/.cache", "~/.local/share/Trash"], "targets": [TARGET],
    "disabled": [], "mode": "snapshot", "keep": 10, "root": True, "name_pattern": "Laptop_yyyy-mm-dd",
    "schedule": "weekly",
    "history": [{"at": NOW - 86400 * 2, "mode": "snapshot", "targets": [TARGET], "ok": 1, "size": 96 * 2 ** 30,
                 "dur": 1260}]})

sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0]]
import tuxdex as t  # noqa: E402

t.backup_host = lambda: "archbook"
t.N_CPU = 16
_uname = os.uname()
os.uname = lambda: os.uname_result((_uname.sysname, "archbook", "6.11.1-arch1-1", _uname.version, "x86_64"))
base = t.backup_dir(TARGET)
snaps = os.path.join(base, "snapshots")
idx = {}
for i, day in enumerate((27, 20, 13, 6)):
    name = f"Laptop_2026-09-{day:02d}"
    os.makedirs(os.path.join(snaps, name), exist_ok=True)
    idx[name] = f"2026-09-{day:02d}T10:15:00"
with open(os.path.join(base, t.BACKUP_INDEX), "w") as f:
    json.dump(idx, f)

# ---- abgefangene Befehle -----------------------------------------------------------------------
LSBLK = {"blockdevices": [
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 512110190592, "fstype": None, "label": None,
     "mountpoints": [None], "model": "Samsung SSD 980 PRO 512GB", "rm": False, "hotplug": False, "fsused": None,
     "fssize": None, "tran": "nvme", "ro": False, "uuid": None, "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "size": 1073741824, "fstype": "vfat",
          "label": "EFI", "mountpoints": ["/boot"], "model": None, "rm": False, "hotplug": False,
          "fsused": "214748365", "fssize": "1071624192", "tran": None, "ro": False, "uuid": "A1B2-C3D4"},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "size": 511035375616, "fstype": "crypto_LUKS",
          "label": None, "mountpoints": [None], "model": None, "rm": False, "hotplug": False, "fsused": None,
          "fssize": None, "tran": None, "ro": False, "uuid": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", "children": [
              {"name": "root", "path": "/dev/mapper/root", "type": "crypt", "size": 511018598400, "fstype": "btrfs",
               "label": "arch", "mountpoints": ["/", "/home"], "model": None, "rm": False, "hotplug": False,
               "fsused": "152471339008", "fssize": "511018598400", "tran": None, "ro": False,
               "uuid": "5e6f7a8b-9c0d-1e2f-3a4b-5c6d7e8f9a0b"}]}]},
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 32015679488, "fstype": None, "label": None,
     "mountpoints": [None], "model": "SanDisk Ultra", "rm": True, "hotplug": True, "fsused": None, "fssize": None,
     "tran": "usb", "ro": False, "uuid": None, "children": [
         {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 32014630912, "fstype": "exfat",
          "label": "STICK", "mountpoints": ["/run/media/user/STICK"], "model": None, "rm": True, "hotplug": True,
          "fsused": "12240656384", "fssize": "32014630912", "tran": None, "ro": False, "uuid": "1234-ABCD"}]}]}
DF = """Filesystem        Type       1B-blocks         Used        Avail Mounted on
/dev/mapper/root  btrfs   548682072064 152471339008 396210733056 /
/dev/nvme0n1p1    vfat      1071624192    214748365    856875827 /boot
/dev/sda1         exfat    32014630912  12240656384  19773974528 /run/media/user/STICK
"""
PKGS = ["base", "firefox", "gimp", "htop", "vlc", "git", "neovim", "thunderbird", "obs-studio", "steam",
        "libreoffice-fresh", "kdenlive", "ttf-liberation", "noto-fonts", "noto-fonts-emoji", "ttf-dejavu",
        "gst-plugins-good", "gst-libav", "power-profiles-daemon", "snapper", "snap-pac"]
SNAPS = {"root": [{"number": 0, "type": "single", "date": "", "description": "current"}] + [
    {"number": n, "type": k, "pre-number": None, "date": d, "description": desc}
    for n, k, d, desc in [
        (41, "single", "2026-09-20 18:02:11", "Tuxdex: erster Snapshot"),
        (52, "pre", "2026-09-24 20:14:55", "pacman -Syu"),
        (53, "post", "2026-09-24 20:16:31", "linux mesa python firefox"),
        (60, "single", "2026-09-25 09:30:02", "Tuxdex: manuell"),
        (61, "pre", "2026-09-26 19:40:12", "pacman -Syu"),
        (62, "post", "2026-09-26 19:42:48", "linux systemd python htop")]]}
FLATPAK_LIST = "\n".join("\t".join(c) for c in [
    ("org.gimp.GIMP", "GNU Image Manipulation Program", "2.10.38", "stable", "flathub", "system", "297.9 MB",
     "Create images and edit photographs"),
    ("com.spotify.Client", "Spotify", "1.2.47", "stable", "flathub", "system", "311.2 MB", "Online music streaming"),
    ("org.telegram.desktop", "Telegram Desktop", "5.5.5", "stable", "flathub", "user", "143.6 MB",
     "Fast and secure messaging")])
FLATPAK_META = """[Application]
name=org.gimp.GIMP
[Context]
shared=network;ipc;
sockets=x11;wayland;pulseaudio;fallback-x11;
devices=dri;
filesystems=xdg-pictures;host;
"""
MULLVAD = {"status": "Connected to ch-zrh-wg-004 in Zurich, Switzerland\nVisible location: Switzerland, Zurich. "
                     "IPv4: 193.32.127.66",
           "account get": "Mullvad account: 1234567812345678\nExpires at: 2027-01-23 10:00:00 UTC\n"
                          "Device name: Great Ghost",
           "lockdown-mode get": "Block traffic when the VPN is disconnected: off",
           "auto-connect get": "Autoconnect: on", "lan get": "Local network sharing setting: allow",
           "dns get": "Block ads: on\nBlock trackers: on\nBlock malware: on"}


class CP:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


_real_run = subprocess.run


def fake_run(args, *a, **k):
    cmd = list(args) if isinstance(args, (list, tuple)) else [args]
    while cmd and cmd[0] == "sudo":
        cmd = [c for c in cmd[1:] if c not in ("-n",)] if len(cmd) > 1 else []
    s = " ".join(map(str, cmd))
    text = k.get("text") or k.get("universal_newlines")

    def r(out, rc=0):
        return CP(out if text else out.encode(), rc)
    if s.startswith("lsblk -J -b"):
        return r(json.dumps(LSBLK))
    if s.startswith("lsblk"):
        return r(json.dumps(LSBLK))
    if s.startswith("df "):
        cols = next((c.split("=", 1)[1].split(",") for c in cmd if c.startswith("--output=")), None)
        if cols and cols != ["source", "fstype", "size", "used", "avail", "target"]:
            rows = [ln.split() for ln in DF.splitlines()[1:]]
            keys = ["source", "fstype", "size", "used", "avail", "target"]
            return r("header\n" + "\n".join(" ".join(rw[keys.index(c)] for c in cols) for rw in rows) + "\n")
        return r(DF)
    if s.startswith("snapper --jsonout"):
        return r(json.dumps(SNAPS))
    if s.startswith("powerprofilesctl get"):
        return r("balanced\n")
    if s.startswith("pacman -Qeq") or s.startswith("pacman -Qq"):
        return r("\n".join(PKGS))
    if s.startswith("pacman -Qmq"):
        return r("visual-studio-code-bin\nparu")
    if s.startswith("flatpak list"):
        return r(FLATPAK_LIST)
    if s.startswith("flatpak remotes"):
        return r("flathub\n")
    if s.startswith("flatpak info --show-metadata"):
        return r(FLATPAK_META)
    if s.startswith("flatpak permission-show"):
        return r("")
    if s.startswith("sh -c cat /etc/sudoers"):
        return r("root ALL=(ALL:ALL) ALL\n%wheel ALL=(ALL:ALL) ALL\n")
    if s.startswith("mullvad "):
        return r(MULLVAD.get(s[8:], ""))
    if s.startswith("systemctl is-active"):
        return r("active" if cmd[-1] in ("ufw", "sshd", "clamav-freshclam", "mullvad-daemon") else "inactive")
    if s.startswith("systemctl is-enabled"):
        return r("enabled" if cmd[-1] in ("ufw", "fstrim.timer", "clamav-freshclam") else "disabled")
    if s.startswith("systemctl --failed"):
        return r("")
    if s.startswith("ufw status"):
        return r("Status: active\n\nTo Action From\n-- ------ ----\n22/tcp ALLOW IN Anywhere\n")
    if s.startswith("clamscan --version"):
        return r("ClamAV 1.4.1/27411/Fri Sep 26 08:23:02 2026")
    if s.startswith("timedatectl show -p NTPSynchronized"):
        return r("yes")
    if s.startswith("timedatectl show -p NTP"):
        return r("yes")
    if s.startswith("journalctl --disk-usage"):
        return r("Archived and active journals take up 412.0M in the file system.")
    if s.startswith("journalctl -p 3"):
        return r("")
    if s.startswith("findmnt -n -o FSTYPE /tmp"):
        return r("tmpfs")
    if s.startswith("findmnt -n -o FSTYPE"):
        return r("btrfs")
    if s.startswith("id -nG"):
        return r("user wheel")
    if s.startswith(("kreadconfig", "gsettings")):
        return r("true")
    if s.startswith("systemd-detect-virt"):
        return r("none", 1)
    if s.startswith("udevadm info"):
        return r("MEMORY_ARRAY_NUM_DEVICES=4\nMEMORY_DEVICE_0_PRESENT=1\nMEMORY_DEVICE_0_SIZE=8589934592\n"
                 "MEMORY_DEVICE_0_CONFIGURED_SPEED_MTS=5500\nMEMORY_DEVICE_0_TYPE=LPDDR5\n"
                 "MEMORY_DEVICE_0_FORM_FACTOR=Other\n")
    if s.startswith(("ip ", "resolvectl", "ss ", "checkupdates", "paru ", "arch-audit", "fwupdmgr",
                     "systemd-analyze", "nvidia-smi", "glxinfo", "vulkaninfo", "pgrep", "lastlog")):
        return r("")
    return _real_run(args, *a, **k)


subprocess.run = fake_run

# ---- Python-Funktionen mit festen Beispielwerten -----------------------------------------------
TOOLS = {"pacman", "paru", "flatpak", "mullvad", "ufw", "clamscan", "freshclam", "udisksctl", "makepkg", "arch-audit",
         "reflector", "paccache", "checkupdates", "systemctl", "journalctl", "timedatectl", "lsblk", "findmnt",
         "gpg", "zstd", "rsync", "sbctl", "snapper", "powerprofilesctl"}
_which = t.which
t.which = lambda c: c in TOOLS or _which(c)
t.kernel_modules_missing = lambda: False
t.alpha_accepted = lambda: True
t.PrivilegeManager.is_authenticated = lambda self: True
t.PrivilegeManager.is_authenticated_nonblocking = lambda self: True
t.pacman_repos = lambda: {p: ("extra" if p not in ("base",) else "core") for p in PKGS}
t.pacman_desktop_icons = lambda: {}
_sizes = {"base": 12.0, "firefox": 245.3, "gimp": 112.8, "htop": 0.45, "vlc": 68.1, "git": 27.4, "neovim": 24.9,
          "thunderbird": 231.7, "obs-studio": 64.2, "steam": 12.3, "libreoffice-fresh": 453.9, "kdenlive": 71.6}
_desc = {"base": "Minimal package set to define a basic Arch Linux installation",
         "firefox": "Fast, Private & Safe Web Browser", "gimp": "GNU Image Manipulation Program",
         "htop": "Interactive process viewer", "vlc": "Free and open source cross-platform multimedia player",
         "git": "the fast distributed version control system", "neovim": "Fork of Vim aiming to improve user experience",
         "thunderbird": "Standalone mail and news reader from mozilla.org", "obs-studio": "Free and open source "
         "software for video recording and live streaming", "steam": "Valve's digital software delivery system",
         "libreoffice-fresh": "LibreOffice branch which contains new features", "kdenlive": "A non-linear video editor"}
_vers = {"base": "3-2", "firefox": "131.0-1", "gimp": "2.10.38-3", "htop": "3.3.0-4", "vlc": "3.0.21-9",
         "git": "2.46.2-1", "neovim": "0.10.1-4", "thunderbird": "128.2.3-1", "obs-studio": "30.2.3-3",
         "steam": "1.0.0.81-2", "libreoffice-fresh": "24.8.1-1", "kdenlive": "24.08.1-1"}
t.pacman_infos = lambda names=None: {p: {"version": _vers[p],
                                         "size": int(_sizes[p] * 2 ** 20), "date": NOW - 86400 * (15 + len(p)),
                                         "desc": _desc[p], "url": f"https://example.org/{p}", "reason": "",
                                         "required_by": [], "depends": []} for p in PKGS}
t.load_update_cache = None
t.vpn_state = lambda ifaces=None: {"mullvad": MULLVAD["status"], "mv_lockdown": False, "tailscale": None, "others": [],
                                   "route_dev": "wg0-mullvad"}
t.dns_state = lambda: {"server": "10.64.0.1", "provider": "Mullvad", "link": "wg0-mullvad", "dot": False}
t.proxy_state = lambda: []
t.net_interfaces = lambda: ([{"name": "wlan0", "kind": "WLAN", "state": "UP", "ipv4": ["192.168.1.42/24"], "ipv6": [],
                              "mac": "aa:bb:cc:dd:ee:ff"},
                             {"name": "wg0-mullvad", "kind": "VPN", "state": "UNKNOWN", "ipv4": ["10.66.12.34/32"],
                              "ipv6": [], "mac": ""}], "192.168.1.1 über wlan0", ["10.64.0.1"])
t.internet_route_dev = lambda: "wg0-mullvad"
t.luks_state = lambda: (True, [("/dev/nvme0n1p2", True, "/")])
t.secure_boot_state = lambda: False
t.microcode_state = lambda: ("amd-ucode", True)
t.swap_state = lambda: [("/dev/zram0", True)]
t.swap_devices = lambda: [("/dev/zram0", "partition", 16 * 2 ** 30, 1.13 * 2 ** 30, "100")]
t.zram_stats = lambda: (int(1.40 * 2 ** 30), int(0.38 * 2 ** 30))
t.sysctl_missing = lambda: []
t.arch_audit_state = lambda: []
t.listening_ports = lambda: [("tcp", "0.0.0.0", "22", "sshd"), ("udp", "0.0.0.0", "5353", "avahi-daemon"),
                             ("tcp", "0.0.0.0", "1716", "kdeconnectd")]
t._history_hits = lambda: {}
t._vscode_telemetry = lambda: []
t._stale_modules = lambda: []
t._pacman_log_issues = lambda: ("2026-09-26T19:42:11", [])
t._pacman_siglevel = lambda: []
t._disks_io = lambda: [("nvme0n1", False, "none", ["none", "mq-deadline"]),
                       ("sda", False, "mq-deadline", ["none", "mq-deadline"])]
t.cpu_model = lambda: "AMD Ryzen 7 7840HS w/ Radeon 780M Graphics"
t.cpu_static = lambda: {"model": "AMD Ryzen 7 7840HS w/ Radeon 780M Graphics", "base": 3800000, "max": 5137000,
                        "sockets": 1, "cores": 8, "threads": 16, "caches": {"L1": 512 * 1024, "L2": 8 * 2 ** 20,
                                                                            "L3": 16 * 2 ** 20},
                        "virt": "KVM / AMD-V", "vm": "Nein", "driver": "amd-pstate-epp"}
t.cpu_live = lambda: {"governor": "powersave", "epp": "balance_performance", "handles": 15408, "boost": "an"}
t.memory_hw = lambda: {"slots": "4 von 4", "speed": "5500 MT/s", "type": "LPDDR5", "form": "Sonstige (meist verlötet)"}
_checklist = t.checklist_state


def fake_checklist():
    c = _checklist()
    c.update({"mirror_age": 12.0, "reflector": True, "reflector_timer": False, "reboot": False,
              "kernel": "6.11.1-arch1-1", "kernel_pkgs": ["linux"], "jerr": [], "failed": [],
              "orphans": ["libfoo", "python-oldlib"], "cache": int(2.4 * 2 ** 30), "apparmor": False,
              "usbguard": None, "lock": True, "groups": ["user", "wheel"], "core_storage": None,
              "core_pattern": "|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h", "core_files": 2,
              "journal_max": None, "journal_use": "412.0M", "root_fs": "btrfs", "snap_tool": None,
              "swappiness": "180", "mem": 32 * 2 ** 30, "fstrim": True, "governor": "powersave",
              "tmp_fs": "tmpfs", "ntp": "yes", "ntp_on": "yes", "aslr": "2", "history": {},
              "telemetry": [], "ignorespace": True})
    return c


t.checklist_state = fake_checklist
t.fans = lambda: [("cpu_fan", "asus", 4100), ("gpu_fan", "asus", 2900)]

# Taskmanager: gleichmäßig laufende Beispiel-Messwerte
_tick = [0]
_N_CPU = 16


_idle = [0]


def fake_cpu_times():
    _tick[0] += 1
    busy = 0.18 + 0.08 * ((_tick[0] * 7) % 5) / 5
    _idle[0] += int(200 * _N_CPU * (1 - busy))
    return _tick[0] * 200 * _N_CPU, _idle[0]


PROCS = [("firefox", 1000, 1843, "Firefox", 1.4 * 2 ** 30, 38), ("plasmashell", 1000, 1122, "", 420 * 2 ** 20, 21),
         ("code", 1000, 2210, "Visual Studio Code", 910 * 2 ** 20, 44), ("spotify", 1000, 2440, "Spotify",
                                                                           380 * 2 ** 20, 30),
         ("kwin_wayland", 1000, 1050, "", 260 * 2 ** 20, 12), ("pipewire", 1000, 1011, "", 28 * 2 ** 20, 4),
         ("thunderbird", 1000, 2610, "Thunderbird", 520 * 2 ** 20, 52), ("systemd", 0, 1, "", 14 * 2 ** 20, 1),
         ("NetworkManager", 0, 612, "", 22 * 2 ** 20, 3), ("mullvad-daemon", 0, 640, "", 45 * 2 ** 20, 18)]


def fake_procs():
    out = {}
    for name, uid, pid, app, rss, thr in PROCS:
        out[pid] = {"name": name, "uid": uid, "user": "user" if uid else "root", "state": "S",
                    "ticks": _tick[0] * (pid % 7 + 1) * 3, "rss": int(rss), "cmd": f"/usr/bin/{name}", "nice": 0,
                    "threads": thr}
    return out


t.cpu_times = fake_cpu_times
t.read_processes = fake_procs
t.app_for_process = lambda pid, name, cmd: next(((n.lower(), a) for n, u, p, a, r, th in PROCS if p == pid and a),
                                                None)
t.meminfo = lambda: {"MemTotal": 32 * 2 ** 30, "MemAvailable": int(25.2 * 2 ** 30), "Cached": int(5.9 * 2 ** 30),
                     "Buffers": int(0.3 * 2 ** 30), "SReclaimable": int(0.7 * 2 ** 30), "SwapTotal": 16 * 2 ** 30,
                     "SwapFree": int(14.87 * 2 ** 30), "Committed_AS": 20 * 2 ** 30, "CommitLimit": 32 * 2 ** 30,
                     "Shmem": 900 * 2 ** 20, "Slab": 610 * 2 ** 20, "Dirty": 2 * 2 ** 20}
t.net_counters = lambda: {"wlan0": (int(4.1e9 + _tick[0] * 2.6e6), int(9.2e8 + _tick[0] * 3.1e5))}
t.disk_counters = lambda: (int(107e9 + _tick[0] * 15.7e6), int(40e9 + _tick[0] * 0.4e6))
t.batteries = lambda: ([{"name": "BAT0", "capacity": 84, "status": "Discharging", "watts": 14.6, "hours": 4.3,
                         "health": 96.0, "model": "ASUS A32-K55"}], False)
t.temperatures = lambda: {"cpu": 54.0, "gpu": 49.0, "nvme": 41.0}
t.cpu_mhz = lambda: (3140.0, 4850.0)
t.gpus = lambda: [{"name": "AMD Radeon 780M", "card": "card1", "busy": 34, "vram_used": 444 * 2 ** 20,
                   "vram_total": 4 * 2 ** 30}]
t.diskstats = lambda: {"nvme0n1": (int(107e9 + _tick[0] * 15.7e6), int(40e9 + _tick[0] * 0.4e6), _tick[0] * 25,
                                   _tick[0] * 150, _tick[0] * 90)}
t.disk_static = lambda name: {"size": 512110190592, "model": "Samsung SSD 980 PRO 512GB", "serial": "S5GXNF0T123456",
                              "wwn": "eui.002538b111b22222", "type": "NVMe", "system": True,
                              "formatted": 512090214400,
                              "parts": [{"path": "/dev/nvme0n1p1", "fs": "vfat", "mount": "/boot", "used": 214748365,
                                         "fssize": 1071624192, "size": 1073741824},
                                        {"path": "/dev/mapper/root", "fs": "btrfs", "mount": "/",
                                         "used": 152471339008, "fssize": 511018598400, "size": 511018598400}]}

# feste Werte statt Container-/Rechnerdaten
PACLOG = os.path.join(HOME, "pacman.log")
with open(PACLOG, "w") as f:
    f.write("[2026-09-26T19:42:11+0200] [PACMAN] starting full system upgrade\n")
_real_read = t._read


def fake_read(path, default=""):
    return {"/proc/uptime": "302412.51 4012345.10", "/proc/loadavg": "1.12 0.94 0.87 2/1450 23456",
            "/proc/sys/vm/swappiness": "180", "/proc/sys/kernel/randomize_va_space": "2"}.get(path) or \
        _real_read(path, default)


t._read = fake_read
_real_open = open


def fake_open(path, *a, **k):
    return _real_open(PACLOG if path == "/var/log/pacman.log" else path, *a, **k)


t.open = fake_open
_short = t.short_path
t.short_path = lambda p: _short(str(p).replace(TARGET, "/run/media/user/BACKUP"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

t.init_language()
app = QApplication(sys.argv)
t._INVOKER = t._Invoker()
t.apply_theme(app)
win = t.MainWindow()
win.resize(W, H)
win.show()
KEYS = [m[0] for m in t.MODULES]
os.makedirs(OUT, exist_ok=True)


def shot(name):
    app.processEvents()
    win.grab().save(os.path.join(OUT, f"{name}.png"))
    print("gespeichert:", name)


def page(key):
    win.select(KEYS.index(key))
    return win.pages[key]


def _status():
    win.set_status("Bereit.")


def prep_update():
    p = page("update")
    p._load_cache()


def _last_times(self):
    while self.info_grid.count():
        lay = self.info_grid.takeAt(0).layout()
        while lay and lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()
    self._info_item(0, "Letztes vollständiges Update", "2026-09-26 19:42")
    self._info_item(1, "Zuletzt geprüft", t.fmt_ago(self.checked_at) if self.checked_at else "noch nie")
    self._info_item(2, "Letzte Änderung Flatpak (Näherung)", "2026-09-25 21:07")


t.UpdaterTab.load_last_update_times = _last_times


def prep_software():
    page("software")


def prep_flatpak():
    p = page("flatpak")
    QTimer.singleShot(1200, lambda: p.list.setCurrentRow(1))


def prep_disks():
    p = page("disks")
    QTimer.singleShot(600, lambda: p.tree.setCurrentItem(p.tree.topLevelItem(1).child(0))
                      if p.tree.topLevelItemCount() > 1 else None)


def prep_storage():
    p = page("storage")
    QTimer.singleShot(800, lambda: p._show("/", [("home", 71 * 2 ** 30, "/home", True),
                                                 ("usr", 38 * 2 ** 30, "/usr", True),
                                                 ("var", 21 * 2 ** 30, "/var", True),
                                                 ("opt", 6 * 2 ** 30, "/opt", True),
                                                 ("swapfile", 4 * 2 ** 30, "/swapfile", False),
                                                 ("root", int(1.2 * 2 ** 30), "/root", True)],
                                           142 * 2 ** 30))


def prep_backup():
    page("backup")


def prep_tasks():
    p = page("tasks")
    p.seg.set(1)
    p._switch(1)
    QTimer.singleShot(4500, lambda: p.toggle_detail("cpu"))
    QTimer.singleShot(6500, lambda: p.verticalScrollBar().setValue(0))


def prep_security():
    page("security")


def prep_checklist():
    p = page("security")
    p.verticalScrollBar().setValue(0)
    p.ensureWidgetVisible(p.cl_rows["sig"], 0, 400)
    QTimer.singleShot(300, lambda: p.verticalScrollBar().setValue(p.cl_rows["sig"].parentWidget().y() - 8))


t.snapshot_env = lambda: {"fs": "btrfs", "home_separate": True, "tool": "snapper", "configured": True,
                          "snap_pac": True, "autosnap": False, "grub_btrfs": False}


t.multilib_enabled = lambda: True
_APPS = {"browser": ("firefox.desktop", [("Chromium", "chromium.desktop"), ("Firefox", "firefox.desktop")]),
         "mail": ("thunderbird.desktop", [("Thunderbird", "thunderbird.desktop")]),
         "pdf": ("okularApplication_pdf.desktop", [("Firefox", "firefox.desktop"),
                                                   ("Okular", "okularApplication_pdf.desktop")]),
         "image": ("org.kde.gwenview.desktop", [("GIMP", "gimp.desktop"), ("Gwenview", "org.kde.gwenview.desktop")]),
         "video": ("vlc.desktop", [("Haruna", "org.kde.haruna.desktop"), ("VLC", "vlc.desktop")]),
         "audio": ("", [("Elisa", "org.kde.elisa.desktop"), ("VLC", "vlc.desktop")]),
         "text": ("org.kde.kate.desktop", [("Kate", "org.kde.kate.desktop"), ("Neovim", "nvim.desktop")])}
t.default_apps_state = lambda: _APPS


def prep_setup_games():
    p = page("setup")
    QTimer.singleShot(300, lambda: p.verticalScrollBar().setValue(p.defaults_panel.y() - 8))


def prep_restore():
    page("restore")


def prep_setup():
    page("setup")


def prep_setup_search():
    p = page("setup")
    p.search.setText("Photoshop")


def prep_modules():
    win.open_settings()
    sp = win.settings_page
    panel = sp.mod_panel
    QTimer.singleShot(300, lambda: sp.verticalScrollBar().setValue(panel.y() - 8))


PLAN = [("update", prep_update, 2500), ("software", prep_software, 3000), ("flatpak", prep_flatpak, 3000),
        ("disks", prep_disks, 2500), ("storage", prep_storage, 2500), ("backup", prep_backup, 3000),
        ("tasks", prep_tasks, 9000), ("security", prep_security, 4500), ("checklist", prep_checklist, 1500),
        ("restore", prep_restore, 2500), ("setup", prep_setup, 2500), ("setup_search", prep_setup_search, 1000),
        ("setup_games", prep_setup_games, 1200),
        ("modules", prep_modules, 1500)]


def run(i=0):
    if i >= len(PLAN):
        app.quit()
        return
    name, prep, wait = PLAN[i]
    prep()
    QTimer.singleShot(wait, lambda: (_status(), shot(name), run(i + 1)))


QTimer.singleShot(1500, run)
app.exec()
