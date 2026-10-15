#!/usr/bin/env python3
"""Kurze Werbevideos (Reels, 1080×1920) aus den Tuxdex-Screenshots.

  QT_QPA_PLATFORM=offscreen python3 tools/promo_video.py --shots /tmp/shots [--out DIR]

Braucht ffmpeg. Die Screenshots erzeugt tools/screenshots.py (Beispieldaten), am besten scharf:
  QT_SCALE_FACTOR=2 QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py --out /tmp/shots
Ton gibt es keinen – auf Instagram einfach einen passenden Sound wählen.
"""
import argparse
import math
import os
import subprocess
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen

W, H, FPS = 1080, 1920, 30
BG, BG2, INK, MUTED, ACCENT, WARN, DANGER = (
    "#0e1113", "#1c2327", "#e6edf0", "#9aa9b0", "#2fb3a3", "#e8a33d", "#f0726a")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def back(x):
    """Überschwinger beim Auftauchen (Pop-Effekt)."""
    x = max(0.0, min(1.0, x))
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def font(size, weight=QFont.Bold):
    f = QFont("IBM Plex Sans", 1)
    f.setPixelSize(int(size))
    f.setWeight(weight)
    return f


class Clip:
    """Ein Video aus Szenen; jede Szene zeichnet sich für ihre lokale Zeit t (Sekunden)."""

    def __init__(self, name, scenes):
        self.name, self.scenes = name, scenes      # [(dauer, funktion(p, t, d))]

    @property
    def duration(self):
        return sum(d for d, _ in self.scenes)

    def render(self, out):
        n = int(self.duration * FPS)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        img = QImage(W, H, QImage.Format_RGBA8888)
        for i in range(n):
            t = i / FPS
            img.fill(QColor(BG))
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            glow(p, t)
            start = 0.0
            for d, fn in self.scenes:
                if start <= t < start + d or (i == n - 1 and fn is self.scenes[-1][1]):
                    fn(p, t - start, d)
                    break
                start += d
            p.end()
            ff.stdin.write(img.constBits().tobytes())
        ff.stdin.close()
        ff.wait()


# --- Bausteine ---------------------------------------------------------------

def glow(p, t):
    """Langsam wandernder Lichtschein im Hintergrund."""
    from PySide6.QtGui import QRadialGradient
    x = W * (0.5 + 0.25 * math.sin(t * 0.6))
    y = H * (0.35 + 0.1 * math.cos(t * 0.4))
    g = QRadialGradient(x, y, W * 0.9)
    c = QColor(ACCENT)
    c.setAlpha(46)
    g.setColorAt(0, c)
    g.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(0, 0, W, H, g)


def text(p, s, y, size, color=INK, t=1.0, delay=0.0, weight=QFont.Bold, pop=True, width=W - 120):
    """Zentrierter Text mit Pop-in; gibt die Höhe zurück."""
    k = (t - delay) / 0.35
    if k <= 0:
        return 0
    a = ease(k)
    sc = back(k) if pop else 1.0
    p.save()
    f = font(size, weight)
    p.setFont(f)
    rect = QRectF((W - width) / 2, y, width, size * 4)
    br = p.boundingRect(rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, s)
    cx, cy = W / 2, y + br.height() / 2
    p.translate(cx, cy + (1 - a) * 40)
    p.scale(sc, sc)
    p.translate(-cx, -cy)
    col = QColor(color)
    col.setAlphaF(a)
    p.setPen(col)
    p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, s)
    p.restore()
    return br.height()


def pill(p, s, y, t, delay=0.0, color=ACCENT):
    k = ease((t - delay) / 0.3)
    if k <= 0:
        return
    p.save()
    f = font(38, QFont.DemiBold)
    p.setFont(f)
    tw = p.fontMetrics().horizontalAdvance(s) + 64
    r = QRectF((W - tw) / 2, y, tw, 72)
    c = QColor(color)
    c.setAlphaF(0.16 * k)
    p.setPen(QPen(QColor(color), 3))
    p.setOpacity(k)
    p.setBrush(c)
    p.drawRoundedRect(r, 36, 36)
    p.setPen(QColor(color))
    p.drawText(r, Qt.AlignCenter, s)
    p.restore()


_IMGS = {}


def shot(p, path, focus, t, d, delay=0.3, top=600, start_zoom=1.6):
    """Screenshot-Ausschnitt als Karte: gleitet hoch, Kamera fährt von weiter weg auf den Ausschnitt.
    focus = (x, y, b, h) in Bildschirm-Koordinaten des 1440×900-Screenshots."""
    if path not in _IMGS:
        _IMGS[path] = QImage(path)
    img = _IMGS[path]
    dpr = img.width() / 1440
    k = ease((t - delay) / 0.45)
    if k <= 0:
        return None
    fx, fy, fw, fh = focus
    z = start_zoom + (1 - start_zoom) * ease((t - delay) / max(d - delay - 0.3, 0.1))
    sw, sh = min(fw * z, 1440), min(fh * z, 900)
    sw = min(sw, sh * fw / fh)
    sh = sw * fh / fw
    sx = min(max(fx + fw / 2 - sw / 2, 0), 1440 - sw)
    sy = min(max(fy + fh / 2 - sh / 2, 0), 900 - sh)
    tw = W - 80
    th = tw * fh / fw
    r = QRectF(40, top + (1 - k) * 160, tw, th)
    p.save()
    p.setOpacity(k)
    clip = QPainterPath()
    clip.addRoundedRect(r, 28, 28)
    p.fillPath(clip.translated(0, 18), QColor(0, 0, 0, 120))
    p.setClipPath(clip)
    p.drawImage(r, img, QRectF(sx * dpr, sy * dpr, sw * dpr, sh * dpr))
    p.setClipping(False)
    p.setPen(QPen(QColor("#2a353b"), 3))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(r, 28, 28)
    p.restore()
    return r


def fade_out(p, t, d, length=0.25):
    if t > d - length:
        p.fillRect(0, 0, W, H, QColor(14, 17, 19, int(255 * ease((t - (d - length)) / length))))


def logo(p, y, t, delay=0.0, width=760):
    path = os.path.join(ROOT, "docs/logo/wordmark-dark.png")
    if path not in _IMGS:
        _IMGS[path] = QImage(path)
    img = _IMGS[path]
    k = ease((t - delay) / 0.5)
    if k <= 0:
        return
    h = width * img.height() / img.width()
    p.save()
    p.setOpacity(k)
    sc = 0.85 + 0.15 * back((t - delay) / 0.5)
    p.translate(W / 2, y + h / 2)
    p.scale(sc, sc)
    p.drawImage(QRectF(-width / 2, -h / 2, width, h), img)
    p.restore()


def end_card(tagline):
    def scene(p, t, d):
        logo(p, 560, t)
        text(p, tagline, 860, 58, INK, t, 0.3)
        pill(p, "Kostenlos · Open Source · Deutsch & Englisch", 1080, t, 0.6)
        text(p, "Link in Bio", 1300, 64, ACCENT, t, 0.9)
        text(p, "github.com/VoxellabDE/Tuxdex", 1400, 36, MUTED, t, 1.0, QFont.Normal, pop=False)
        text(p, "Alpha – Feedback willkommen!", 1720, 34, WARN, t, 1.2, QFont.DemiBold, pop=False)
    return scene


def headline_with_shot(head, sub, path, focus, color=ACCENT, where=None):
    def scene(p, t, d):
        text(p, head, 260, 92, INK, t)
        if sub:
            text(p, sub, 400, 58, color, t, 0.25)
        r = shot(p, path, focus, t, d)
        if r is not None and where:
            pill(p, where, r.bottom() + 70, t, 0.8, MUTED)
        fade_out(p, t, d)
    return scene


# --- Die Videos ----------------------------------------------------------------

def clips(shots):
    S = lambda n: os.path.join(shots, f"{n}.png")      # noqa: E731

    def hook1(p, t, d):
        text(p, "Von Windows", 620, 110, INK, t)
        text(p, "zu Arch Linux?", 760, 110, INK, t, 0.3)
        text(p, "Ohne Terminal.", 1010, 96, ACCENT, t, 0.9)
        fade_out(p, t, d)

    v1 = Clip("tuxdex_windows_zu_arch", [
        (2.4, hook1),
        (2.8, headline_with_shot("Updates", "mit einem Klick", S("update"), (36, 440, 660, 290), where="Tab „Updates“")),
        (3.0, headline_with_shot("Photoshop?", "Hier ist dein Ersatz", S("setup_search"),
                                 (36, 305, 620, 260), where="Tab „Einrichten“")),
        (2.8, headline_with_shot("Update kaputt?", "Ein Klick zurück", S("restore"), (36, 520, 720, 360),
                                 color=WARN, where="Tab „Wiederherstellung“")),
        (2.8, headline_with_shot("Gaming?", "Steam & Co. in einem Schritt", S("setup_games"),
                                 (36, 390, 620, 330), where="Tab „Einrichten“")),
        (3.6, end_card("Dein Arch Linux in einem Fenster")),
    ])

    def hook2(p, t, d):
        text(p, "Arch-Update", 600, 112, INK, t)
        text(p, "hat dein System", 740, 96, INK, t, 0.25)
        text(p, "zerschossen?", 870, 112, DANGER, t, 0.55)
        fade_out(p, t, d)

    def calm(p, t, d):
        text(p, "Kein Problem.", 760, 110, ACCENT, t)
        fade_out(p, t, d)

    def rollback(p, t, d):
        text(p, "Snapshot auswählen", 260, 84, INK, t)
        text(p, "Zurücksetzen. Fertig.", 390, 64, ACCENT, t, 0.3)
        focus = (36, 520, 720, 360)
        r = shot(p, S("restore"), focus, t, d, 0.4, start_zoom=1.4)
        if r is not None:
            pill(p, "Vorher wird der jetzige Stand gesichert", r.bottom() + 70, t, 1.2, MUTED)
        if r is not None and t > 2.2:            # Klick-Puls auf „Auf diesen Stand zurücksetzen“
            k = (t - 2.2) % 1.0
            bx = r.left() + (150 - focus[0]) / focus[2] * r.width()
            by = r.top() + (853 - focus[1]) / focus[3] * r.height()
            p.save()
            c = QColor(DANGER)
            c.setAlphaF(max(0.0, 0.8 - k))
            p.setPen(QPen(c, 6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(bx - 30 - k * 70, by - 30 - k * 70, 60 + k * 140, 60 + k * 140))
            p.restore()
        fade_out(p, t, d)

    def auto(p, t, d):
        text(p, "Vor jedem Update", 700, 92, INK, t)
        text(p, "ein Snapshot.", 820, 92, INK, t, 0.25)
        text(p, "Automatisch.", 1000, 104, ACCENT, t, 0.6)
        fade_out(p, t, d)

    v2 = Clip("tuxdex_update_rueckgaengig", [
        (2.6, hook2),
        (1.4, calm),
        (3.6, rollback),
        (2.2, auto),
        (3.4, end_card("Systemwiederherstellung für Arch")),
    ])

    def hook3(p, t, d):
        text(p, "Wo ist mein", 640, 112, INK, t)
        text(p, "Speicher hin?", 780, 112, WARN, t, 0.3)
        fade_out(p, t, d)

    v3 = Clip("tuxdex_speicher_und_gaming", [
        (2.0, hook3),
        (3.2, headline_with_shot("Auf einen Blick", "Fotos, Apps, System", S("storage"),
                                 (36, 425, 480, 240), where="Tab „Speicher“")),
        (3.0, headline_with_shot("Live im Blick", "CPU, RAM, Grafik, Lüfter", S("tasks"),
                                 (24, 175, 470, 450), where="Tab „Taskmanager“")),
        (2.8, headline_with_shot("Spielen?", "Steam, GameMode, MangoHud", S("setup_games"),
                                 (36, 390, 620, 330), where="Tab „Einrichten“")),
        (3.4, end_card("Alles für dein Arch Linux")),
    ])
    return [v1, v2, v3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True, help="Screenshots in doppelter Auflösung: QT_SCALE_FACTOR=2 tools/screenshots.py --out DIR")
    ap.add_argument("--out", default=".")
    ap.add_argument("--only")
    a = ap.parse_args()
    QGuiApplication(sys.argv)
    os.makedirs(a.out, exist_ok=True)
    for c in clips(a.shots):
        if a.only and a.only not in c.name:
            continue
        out = os.path.join(a.out, c.name + ".mp4")
        c.render(out)
        print(f"{out}  ({c.duration:.1f} s)")


if __name__ == "__main__":
    main()
