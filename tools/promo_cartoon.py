"""Cartoon-Werbevideo: Figur am Laptop – Windows, dann Arch-Verzweiflung, dann Tuxdex.

Alles mit QPainter gezeichnet (flacher Cartoon-Stil). Wird von promo_video.py eingebunden.
"""
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainterPath, QPen

SKIN, SKIN_D = "#f2c29b", "#d9a27a"
HAIR = "#3b2a20"
HOODIE, HOODIE_D = "#5b6ee1", "#4656c4"
DESK, DESK_D = "#3a2f2a", "#2c2420"
LAPTOP, LAPTOP_D = "#c9d1d6", "#9aa5ab"
INK, MUTED, ACCENT, WARN, DANGER = "#e6edf0", "#9aa9b0", "#2fb3a3", "#e8a33d", "#f0726a"

CX = 540            # Bildmitte
HEAD_Y = 760        # Kopfmitte
SCREEN = QRectF(CX - 300, 860, 600, 380)


def _ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _pen(color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


# --- Raum & Laptop -------------------------------------------------------------

def stage(p):
    """Szene vergrößert und nach unten gerückt, damit die Figur das Hochformat füllt."""
    p.save()
    p.translate(540, 1180)
    p.scale(1.32, 1.32)
    p.translate(-540, -1000)


def room(p, mood_dark=0.0):
    """Wand mit Fenster und Schreibtisch; mood_dark dunkelt den Raum ab (Verzweiflung)."""
    g = QLinearGradient(0, 0, 0, 1920)
    g.setColorAt(0, QColor("#1b2430"))
    g.setColorAt(1, QColor("#121820"))
    p.fillRect(0, 0, 1080, 1920, g)
    # Fenster mit Mond
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#22303f"))
    p.drawRoundedRect(QRectF(700, 470, 260, 300), 18, 18)
    p.setBrush(QColor("#2c3e52"))
    p.drawRoundedRect(QRectF(716, 486, 228, 268), 12, 12)
    p.setBrush(QColor("#f4e9c8"))
    p.drawEllipse(QPointF(880, 550), 26, 26)
    p.setBrush(QColor("#2c3e52"))
    p.drawEllipse(QPointF(892, 542), 22, 22)
    p.setPen(_pen("#22303f", 8))
    p.drawLine(QPointF(830, 486), QPointF(830, 754))
    p.drawLine(QPointF(716, 620), QPointF(944, 620))
    # Pflanze
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#8a5a3c"))
    p.drawRoundedRect(QRectF(120, 1170, 90, 90), 12, 12)
    p.setBrush(QColor("#4cc38a"))
    for a, l in ((-0.5, 120), (0.0, 150), (0.5, 120), (-0.25, 100), (0.25, 100)):
        path = QPainterPath(QPointF(165, 1175))
        tip = QPointF(165 + math.sin(a) * l, 1175 - math.cos(a) * l)
        path.quadTo(QPointF(165 + math.sin(a) * l * 0.2 - 30, 1175 - l * 0.6), tip)
        path.quadTo(QPointF(165 + math.sin(a) * l * 0.2 + 30, 1175 - l * 0.6), QPointF(165, 1175))
        p.drawPath(path)
    # Schreibtisch
    p.setBrush(QColor(DESK))
    p.drawRect(QRectF(0, 1260, 1080, 60))
    p.setBrush(QColor(DESK_D))
    p.drawRect(QRectF(0, 1320, 1080, 600))
    if mood_dark > 0:
        p.fillRect(0, 0, 1080, 1920, QColor(5, 8, 12, int(140 * mood_dark)))


def laptop_front(p, screen_fn, t, glow=None):
    """Laptop mit dem Bildschirm zum Zuschauer; screen_fn(p, rect, t) malt den Inhalt."""
    r = SCREEN
    if glow:
        c = QColor(glow)
        for i in range(6, 0, -1):
            c.setAlpha(14)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r.adjusted(-i * 14, -i * 14, i * 14, i * 14), 30 + i * 6, 30 + i * 6)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#20262b"))
    p.drawRoundedRect(r.adjusted(-22, -22, 22, 22), 24, 24)
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(r, 8, 8)
    p.setClipPath(clip)
    screen_fn(p, r, t)
    p.restore()
    # Unterteil
    base = QPainterPath()
    base.moveTo(r.left() - 70, 1262)
    base.lineTo(r.right() + 70, 1262)
    base.lineTo(r.right() + 30, r.bottom() + 22)
    base.lineTo(r.left() - 30, r.bottom() + 22)
    base.closeSubpath()
    p.setBrush(QColor(LAPTOP))
    p.drawPath(base)
    p.setBrush(QColor(LAPTOP_D))
    p.drawRoundedRect(QRectF(CX - 70, 1240, 140, 14), 6, 6)


# --- Bildschirm-Inhalte -----------------------------------------------------------

def screen_windows(p, r, t):
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#2a7de1"))
    g.setColorAt(1, QColor("#5fb0ff"))
    p.fillRect(r, g)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawEllipse(QPointF(r.center().x() + 120, r.top() + 120), 160, 90)
    for i, (x, y, w, h) in enumerate(((40, 40, 250, 170), (230, 110, 300, 180))):
        a = _ease((t - 0.2 - i * 0.25) / 0.4)
        if a <= 0:
            continue
        p.setOpacity(a)
        win = QRectF(r.left() + x, r.top() + y + (1 - a) * 30, w, h)
        p.setBrush(QColor("#f4f6f8"))
        p.drawRoundedRect(win, 8, 8)
        p.setBrush(QColor("#dfe5ea"))
        p.drawRoundedRect(QRectF(win.left(), win.top(), w, 26), 8, 8)
        p.setBrush(QColor("#c4ced6"))
        for k in range(3):
            p.drawRoundedRect(QRectF(win.left() + 18, win.top() + 46 + k * 30, w * (0.7 - k * 0.15), 12), 6, 6)
        p.setOpacity(1)
    p.setBrush(QColor(20, 30, 50, 200))
    p.drawRect(QRectF(r.left(), r.bottom() - 36, r.width(), 36))
    p.setBrush(QColor("#5fb0ff"))
    for k in range(4):
        p.drawRoundedRect(QRectF(r.center().x() - 70 + k * 36, r.bottom() - 28, 22, 22), 5, 5)


TERM_LINES = [
    ("$ sudo pacman -Syu", INK),
    (":: Synchronising package databases...", MUTED),
    ("error: failed to commit transaction", DANGER),
    ("(conflicting files)", DANGER),
    ("$ nano /etc/pacman.conf", INK),
    ("$ mkinitcpio -P", INK),
    ("==> ERROR: module not found", DANGER),
    ("$ grub-mkconfig -o ...", INK),
    ("warning: /etc/fstab installed as .pacnew", WARN),
    ("error: target not found: help", DANGER),
    ("$ ???", WARN),
]


def screen_terminal(p, r, t, speed=7.0, start=0):
    p.fillRect(r, QColor("#0b0d0f"))
    f = QFont("IBM Plex Mono", 1)
    f.setPixelSize(22)
    p.setFont(f)
    n = start + int(t * speed)
    lines = TERM_LINES[:max(1, min(n, len(TERM_LINES)))]
    lines = lines[-11:]
    for i, (s, c) in enumerate(lines):
        p.setPen(QColor(c))
        p.drawText(QPointF(r.left() + 22, r.top() + 40 + i * 32), s)
    if int(t * 3) % 2 == 0:
        p.fillRect(QRectF(r.left() + 22, r.top() + 22 + len(lines) * 32, 12, 24), QColor(INK))


def screen_terminal_panic(p, r, t):
    screen_terminal(p, r, t + 2, speed=9.0)
    if int(t * 4) % 2 == 0:                       # rotes Flackern
        p.fillRect(r, QColor(240, 114, 106, 40))


_TUX = {}


def screen_logo(p, r, t, logo_path):
    p.fillRect(r, QColor("#0e1113"))
    if logo_path not in _TUX:
        _TUX[logo_path] = QImage(logo_path)
    img = _TUX[logo_path]
    a = _ease(t / 0.6)
    w = 420 * (0.8 + 0.2 * a)
    h = w * img.height() / img.width()
    p.setOpacity(a)
    p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), img)
    p.setOpacity(1)


def screen_tuxdex(p, r, t, shot_path):
    if shot_path not in _TUX:
        _TUX[shot_path] = QImage(shot_path)
    img = _TUX[shot_path]
    # langsamer Schwenk über die Oberfläche
    k = min(1.0, t / 3.0)
    sw = img.width() * 0.62
    sh = sw * r.height() / r.width()
    sx = img.width() * 0.02 + (img.width() - sw - img.width() * 0.04) * k * 0.35
    sy = img.height() * 0.0 + (img.height() - sh) * k * 0.25
    p.drawImage(r, img, QRectF(sx, sy, sw, sh))


# --- Figur ------------------------------------------------------------------------

def _bob(t, mood):
    return math.sin(t * (9 if mood == "despair" else 2.2)) * (6 if mood == "despair" else 3)


def person(p, t, mood):
    """Körper und Kopf (hinter dem Laptop). mood: happy, neutral, worried, despair, amazed, joy."""
    bob = _bob(t, mood)
    hx, hy = CX, HEAD_Y + bob
    # Körper (Hoodie) hinter dem Laptop
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(HOODIE))
    body = QPainterPath()
    body.moveTo(CX - 210, 1262)
    body.cubicTo(CX - 220, 960, CX - 150, 880 + bob, CX, 880 + bob)
    body.cubicTo(CX + 150, 880 + bob, CX + 220, 960, CX + 210, 1262)
    body.closeSubpath()
    p.drawPath(body)
    p.setBrush(QColor(HOODIE_D))
    p.drawEllipse(QPointF(CX, 895 + bob), 70, 26)          # Kragen
    p.setBrush(QColor(SKIN_D))
    p.drawRect(QRectF(CX - 34, hy + 80, 68, 60))           # Hals
    # Kopf
    p.setBrush(QColor(SKIN))
    p.drawEllipse(QPointF(hx - 118, hy + 10), 22, 30)      # Ohren
    p.drawEllipse(QPointF(hx + 118, hy + 10), 22, 30)
    p.drawEllipse(QPointF(hx, hy), 120, 128)
    # Haare
    p.setBrush(QColor(HAIR))
    hair = QPainterPath()
    hair.moveTo(hx - 124, hy - 10)
    hair.cubicTo(hx - 140, hy - 150, hx + 140, hy - 170, hx + 124, hy - 20)
    hair.cubicTo(hx + 90, hy - 80, hx + 20, hy - 95, hx - 40, hy - 70)
    hair.cubicTo(hx - 80, hy - 60, hx - 110, hy - 40, hx - 124, hy - 10)
    p.drawPath(hair)
    # Wangen
    if mood in ("happy", "joy", "amazed"):
        p.setBrush(QColor(240, 120, 120, 70))
        p.drawEllipse(QPointF(hx - 70, hy + 40), 22, 12)
        p.drawEllipse(QPointF(hx + 70, hy + 40), 22, 12)
    _face(p, t, hx, hy, mood)
    if mood in ("worried", "despair"):
        _sweat(p, t, hx, hy, heavy=mood == "despair")


def arms(p, t, mood, kind="desk"):
    """Arme (hinter dem Laptop zeichnen): desk (tippen), head (Hände am Kopf), up (jubeln), thumbs."""
    _arms(p, t, kind, CX, HEAD_Y + _bob(t, mood), "arm")


def hands(p, t, mood, kind="desk"):
    """Hände (vor dem Laptop zeichnen) – dieselben Posen wie arms()."""
    _arms(p, t, kind, CX, HEAD_Y + _bob(t, mood), "hand")


def _face(p, t, hx, hy, mood):
    blink = (t % 3.1) < 0.12 and mood not in ("despair", "amazed")
    ey = hy - 10
    for side in (-1, 1):
        ex = hx + side * 44
        if mood == "despair":                      # zugekniffen: > <
            p.setPen(_pen("#2b2b2b", 7))
            p.drawLine(QPointF(ex - 16 * side, ey - 12), QPointF(ex + 12 * side, ey))
            p.drawLine(QPointF(ex + 12 * side, ey), QPointF(ex - 16 * side, ey + 12))
            continue
        if blink:
            p.setPen(_pen("#2b2b2b", 6))
            p.drawLine(QPointF(ex - 16, ey), QPointF(ex + 16, ey))
            continue
        big = mood == "amazed"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(ex, ey), 22 if big else 19, 26 if big else 22)
        p.setBrush(QColor("#2b2b2b"))
        pr = 8 if mood == "worried" else 12
        look = 6 if mood in ("happy", "joy", "neutral") else 0
        p.drawEllipse(QPointF(ex, ey + look), pr, pr)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(ex + 4, ey + look - 4), 3.5, 3.5)
    # Augenbrauen
    p.setPen(_pen(HAIR, 8))
    for side in (-1, 1):
        ex = hx + side * 44
        if mood in ("worried", "despair"):
            p.drawLine(QPointF(ex - 20 * side, ey - 36), QPointF(ex + 18 * side, ey - 46))
        elif mood == "amazed":
            p.drawLine(QPointF(ex - 18, ey - 52), QPointF(ex + 18, ey - 52))
        else:
            p.drawLine(QPointF(ex - 18, ey - 40), QPointF(ex + 18, ey - 42))
    # Mund
    my = hy + 62
    p.setPen(_pen("#7a3b2e", 7))
    p.setBrush(Qt.NoBrush)
    if mood in ("happy", "joy"):
        path = QPainterPath(QPointF(hx - 40, my - 6))
        path.quadTo(QPointF(hx, my + 34 if mood == "joy" else my + 24), QPointF(hx + 40, my - 6))
        if mood == "joy":
            p.setBrush(QColor("#7a3b2e"))
            path.closeSubpath()
        p.drawPath(path)
    elif mood == "neutral":
        p.drawLine(QPointF(hx - 26, my + 2), QPointF(hx + 26, my + 2))
    elif mood == "worried":
        path = QPainterPath(QPointF(hx - 34, my + 4))
        for k in range(1, 5):
            path.lineTo(QPointF(hx - 34 + k * 17, my + (-6 if k % 2 else 4)))
        p.drawPath(path)
    elif mood == "despair":
        p.setBrush(QColor("#7a3b2e"))
        p.drawEllipse(QPointF(hx, my + 8), 30, 24 + 4 * math.sin(t * 12))
    elif mood == "amazed":
        p.setBrush(QColor("#7a3b2e"))
        p.drawEllipse(QPointF(hx, my + 6), 18, 22)


def _sweat(p, t, hx, hy, heavy=False):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#7fc8ff"))
    drops = [(hx + 112, hy - 70, 0.0)] + ([(hx - 112, hy - 60, 0.4), (hx + 92, hy - 110, 0.8)] if heavy else [])
    for x, y, off in drops:
        k = ((t + off) * (1.4 if heavy else 0.8)) % 1.0
        yy = y + k * 60
        path = QPainterPath(QPointF(x, yy - 18))
        path.quadTo(QPointF(x + 14, yy + 2), QPointF(x, yy + 10))
        path.quadTo(QPointF(x - 14, yy + 2), QPointF(x, yy - 18))
        p.setOpacity(1 - k * 0.6)
        p.drawPath(path)
    p.setOpacity(1)


def _arms(p, t, arms, hx, hy, part="arm"):
    sh_l, sh_r = QPointF(CX - 170, 990), QPointF(CX + 170, 990)
    if arms == "desk":
        tap = math.sin(t * 18) * 6
        targets = (QPointF(CX - 160, 1240 + tap), QPointF(CX + 160, 1240 - tap))
    elif arms == "head":
        wob = math.sin(t * 10) * 8
        targets = (QPointF(hx - 120, hy - 60 + wob), QPointF(hx + 120, hy - 60 - wob))
    elif arms == "up":
        wav = math.sin(t * 8) * 20
        targets = (QPointF(CX - 300 + wav, 640), QPointF(CX + 300 - wav, 640))
    else:  # thumbs
        targets = (QPointF(CX - 160, 1240), QPointF(CX + 352, 930 + math.sin(t * 6) * 10))
    for sh, tg in zip((sh_l, sh_r), targets):
        if part == "hand":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(SKIN))
            p.drawEllipse(tg, 30, 30)
            continue
        mid = QPointF((sh.x() + tg.x()) / 2 + (40 if tg.x() > CX else -40), (sh.y() + tg.y()) / 2 + 40)
        path = QPainterPath(sh)
        path.quadTo(mid, tg)
        p.setPen(_pen(HOODIE_D, 58))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    if arms == "thumbs" and part == "hand":
        tg = targets[1]
        p.setBrush(QColor(SKIN))
        p.drawRoundedRect(QRectF(tg.x() - 10, tg.y() - 66, 22, 50), 11, 11)


# --- Effekte ----------------------------------------------------------------------

def question_marks(p, t, hx=CX, hy=HEAD_Y):
    f = QFont("IBM Plex Sans", 1)
    f.setPixelSize(86)
    f.setWeight(QFont.Bold)
    p.setFont(f)
    for i, (dx, s, c) in enumerate(((-230, "?", WARN), (210, "?!", DANGER), (-150, "#!@", DANGER),
                                    (170, "?", WARN))):
        k = ((t * 0.7) + i * 0.27) % 1.0
        p.setOpacity(min(1, (1 - k) * 2) * _ease(t / 0.3))
        p.setPen(QColor(c))
        p.drawText(QPointF(hx + dx - 30, hy - 120 - k * 160 + (i % 2) * 40), s)
    p.setOpacity(1)


def rain_cloud(p, t, hx=CX, hy=HEAD_Y):
    a = _ease(t / 0.4)
    y = hy - 330 + (1 - a) * -80
    p.setOpacity(a)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#6d7a86"))
    for dx, dy, r in ((-90, 10, 60), (-30, -20, 75), (50, 0, 65), (110, 20, 50)):
        p.drawEllipse(QPointF(hx + dx, y + dy), r, r * 0.8)
    p.setPen(_pen("#7fc8ff", 5))
    for i in range(9):
        x = hx - 130 + i * 32
        k = ((t * 2.2) + i * 0.37) % 1.0
        p.drawLine(QPointF(x, y + 60 + k * 120), QPointF(x - 6, y + 84 + k * 120))
    p.setOpacity(1)


def lightbulb(p, t, hx=CX, hy=HEAD_Y):
    a = _ease(t / 0.35)
    if a <= 0:
        return
    y = hy - 290
    p.save()
    p.translate(hx, y)
    s = 0.6 + 0.4 * a
    p.scale(s, s)
    p.setOpacity(a)
    p.setPen(_pen("#ffd166", 8))
    for i in range(10):
        ang = i * math.pi / 5 + t
        r1, r2 = 90, 130 + 10 * math.sin(t * 8 + i)
        p.drawLine(QPointF(math.cos(ang) * r1, math.sin(ang) * r1), QPointF(math.cos(ang) * r2, math.sin(ang) * r2))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffd166"))
    p.drawEllipse(QPointF(0, 0), 62, 62)
    p.setBrush(QColor("#c9d1d6"))
    p.drawRoundedRect(QRectF(-28, 52, 56, 40), 8, 8)
    p.restore()


_CONF = [(random.Random(i).uniform(0, 1080), random.Random(i * 7).uniform(-400, 0),
          random.Random(i * 13).choice([ACCENT, WARN, "#e87fa8", "#5aa6f0", "#b5d86b"]),
          random.Random(i * 3).uniform(0.6, 1.4)) for i in range(70)]


def confetti(p, t):
    p.setPen(Qt.NoPen)
    for x, y0, c, sp in _CONF:
        y = y0 + t * 520 * sp
        if y > 1900:
            continue
        p.save()
        p.translate(x + math.sin(t * 3 + x) * 30, y)
        p.rotate(t * 200 * sp + x)
        p.setBrush(QColor(c))
        p.drawRect(QRectF(-9, -5, 18, 10))
        p.restore()
