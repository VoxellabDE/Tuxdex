#!/usr/bin/env python3
"""Tom-Reel: Cartoon-Werbevideo mit Sprecher (Kokoro-TTS), Lippensynchronisation, Wort-Untertiteln
und Soundeffekten. Persona: Tom – braune Haare, aus Deutschland, Nerd.

  QT_QPA_PLATFORM=offscreen python3 tools/promo_tom.py --shots DIR --kokoro DIR [--voice am_michael] [--out DIR]

--shots   Screenshots in doppelter Auflösung (QT_SCALE_FACTOR=2 tools/screenshots.py --lang en --out DIR)
--kokoro  Ordner mit kokoro-v1.0.onnx und voices-v1.0.bin (Apache-2.0,
          github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0); pip install kokoro-onnx
"""
import argparse
import math
import os
import random
import re
import subprocess
import sys

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import promo_audio as pa          # noqa: E402
import promo_cartoon as pc        # noqa: E402
import promo_video as pv          # noqa: E402

W, H, FPS = 1080, 1920, 30
SR = pa.SR
CX, HEAD_Y = pc.CX, pc.HEAD_Y

# --- Tom ----------------------------------------------------------------------------
SKIN, SKIN_SH, OUT = "#f1c7a3", "#dca883", "#1b232c"
HAIR, HAIR_HI = "#6b4226", "#8a5a36"
HOODIE, HOODIE_SH, HOODIE_D = "#2d3e5c", "#24324b", "#1e2a40"
pc.SKIN, pc.HOODIE_D = SKIN, HOODIE_SH            # Arme/Hände aus promo_cartoon passend einfärben


def _pen(c, w):
    pen = QPen(QColor(c), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def tom(p, t, mood, talk=0.0):
    """Tom hinter dem Laptop. talk = Mundöffnung 0…1 (aus der Stimme)."""
    bob = pc._bob(t, mood)
    hx, hy = CX, HEAD_Y + bob
    tilt = {"despair": math.sin(t * 9) * 4, "joy": math.sin(t * 3) * 3}.get(mood, math.sin(t * 1.3) * 1.5)
    p.save()
    # Körper: Hoodie mit Schatten, Kordeln, Kopfhörer um den Hals
    body = QPainterPath()
    body.moveTo(CX - 215, 1262)
    body.cubicTo(CX - 228, 955, CX - 150, 872 + bob, CX, 872 + bob)
    body.cubicTo(CX + 150, 872 + bob, CX + 228, 955, CX + 215, 1262)
    body.closeSubpath()
    p.setPen(_pen(OUT, 6))
    p.setBrush(QColor(HOODIE))
    p.drawPath(body)
    p.save()
    p.setClipPath(body)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(HOODIE_SH))
    p.drawRect(QRectF(CX + 60, 800, 260, 500))
    p.restore()
    p.setPen(_pen("#d9e2ea", 5))
    p.drawLine(QPointF(CX - 28, 920 + bob), QPointF(CX - 34, 1010 + bob))
    p.drawLine(QPointF(CX + 28, 920 + bob), QPointF(CX + 34, 1010 + bob))
    # Hals
    p.setPen(_pen(OUT, 5))
    p.setBrush(QColor(SKIN_SH))
    p.drawRect(QRectF(CX - 36, hy + 78, 72, 70))
    # Kopfhörer um den Hals
    p.setPen(_pen("#111820", 16))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(CX - 120, 830 + bob, 240, 150), 200 * 16, 140 * 16)
    p.setPen(_pen(OUT, 5))
    p.setBrush(QColor(pv.ACCENT))
    p.drawRoundedRect(QRectF(CX - 140, 880 + bob, 46, 66), 16, 16)
    p.drawRoundedRect(QRectF(CX + 94, 880 + bob, 46, 66), 16, 16)
    # Kopf (leicht geneigt)
    p.translate(hx, hy + 110)
    p.rotate(tilt)
    p.translate(-hx, -(hy + 110))
    p.setPen(_pen(OUT, 6))
    p.setBrush(QColor(SKIN))
    for side in (-1, 1):
        p.drawEllipse(QPointF(hx + side * 120, hy + 12), 22, 30)
    head = QPainterPath()
    head.addEllipse(QPointF(hx, hy), 122, 132)
    p.drawPath(head)
    p.save()
    p.setClipPath(head)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(SKIN_SH))
    p.drawEllipse(QPointF(hx + 150, hy + 40), 110, 170)
    p.restore()
    _hair(p, hx, hy)
    if mood in ("happy", "joy", "amazed"):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(240, 120, 120, 70))
        p.drawEllipse(QPointF(hx - 72, hy + 44), 22, 12)
        p.drawEllipse(QPointF(hx + 72, hy + 44), 22, 12)
    _eyes(p, t, hx, hy, mood)
    _glasses(p, hx, hy)
    _mouth(p, t, hx, hy, mood, talk)
    if mood in ("worried", "despair"):
        pc._sweat(p, t, hx, hy, heavy=mood == "despair")
    p.restore()


def _hair(p, hx, hy):
    p.setPen(_pen(OUT, 6))
    p.setBrush(QColor(HAIR))
    hair = QPainterPath()
    hair.moveTo(hx - 126, hy + 10)
    hair.cubicTo(hx - 150, hy - 120, hx - 60, hy - 175, hx + 10, hy - 165)
    hair.cubicTo(hx + 110, hy - 168, hx + 150, hy - 90, hx + 126, hy + 5)
    # Pony mit Strähnen
    hair.cubicTo(hx + 110, hy - 50, hx + 80, hy - 70, hx + 60, hy - 62)
    hair.lineTo(hx + 40, hy - 92)
    hair.lineTo(hx + 18, hy - 60)
    hair.lineTo(hx - 12, hy - 96)
    hair.lineTo(hx - 34, hy - 58)
    hair.lineTo(hx - 66, hy - 88)
    hair.cubicTo(hx - 90, hy - 60, hx - 112, hy - 30, hx - 126, hy + 10)
    p.drawPath(hair)
    p.setPen(_pen(HAIR_HI, 6))
    p.setBrush(Qt.NoBrush)
    hi = QPainterPath(QPointF(hx - 70, hy - 140))
    hi.quadTo(QPointF(hx - 10, hy - 162), QPointF(hx + 50, hy - 146))
    p.drawPath(hi)


def _eyes(p, t, hx, hy, mood):
    blink = (t % 3.3) < 0.11 and mood not in ("despair", "amazed")
    ey = hy - 4
    for side in (-1, 1):
        ex = hx + side * 46
        if mood == "despair":
            p.setPen(_pen("#2b2b2b", 7))
            p.drawLine(QPointF(ex - 14 * side, ey - 11), QPointF(ex + 11 * side, ey))
            p.drawLine(QPointF(ex + 11 * side, ey), QPointF(ex - 14 * side, ey + 11))
        elif blink:
            p.setPen(_pen("#2b2b2b", 6))
            p.drawLine(QPointF(ex - 14, ey), QPointF(ex + 14, ey))
        else:
            big = mood == "amazed"
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("white"))
            p.drawEllipse(QPointF(ex, ey), 18 if big else 16, 21 if big else 18)
            p.setBrush(QColor("#3b2a1e"))
            pr = 7 if mood == "worried" else 10
            look = 4 if mood in ("happy", "joy", "neutral") else 0
            p.drawEllipse(QPointF(ex, ey + look), pr, pr)
            p.setBrush(QColor("white"))
            p.drawEllipse(QPointF(ex + 3, ey + look - 3), 3, 3)
        # Augenbrauen
        p.setPen(_pen(HAIR, 8))
        if mood in ("worried", "despair"):
            p.drawLine(QPointF(ex - 18 * side, ey - 44), QPointF(ex + 16 * side, ey - 54))
        elif mood == "amazed":
            p.drawLine(QPointF(ex - 18, ey - 62), QPointF(ex + 18, ey - 62))
        else:
            p.drawLine(QPointF(ex - 18, ey - 48), QPointF(ex + 18, ey - 50))


def _glasses(p, hx, hy):
    ey = hy - 4
    p.setPen(_pen("#151b22", 7))
    p.setBrush(QColor(200, 230, 255, 28))
    for side in (-1, 1):
        p.drawRoundedRect(QRectF(hx + side * 46 - 36, ey - 30, 72, 58), 22, 22)
    p.drawLine(QPointF(hx - 10, ey - 6), QPointF(hx + 10, ey - 6))
    p.drawLine(QPointF(hx - 82, ey - 8), QPointF(hx - 118, ey - 2))
    p.drawLine(QPointF(hx + 82, ey - 8), QPointF(hx + 118, ey - 2))
    p.setPen(_pen(QColor(255, 255, 255, 120), 4))
    for side in (-1, 1):
        x = hx + side * 46
        p.drawLine(QPointF(x - 22, ey - 16), QPointF(x - 10, ey - 24))


def _mouth(p, t, hx, hy, mood, talk):
    my = hy + 64
    open_h = 6 + 34 * talk
    smile = {"happy": 14, "joy": 22, "neutral": 2, "worried": -6, "despair": -14, "amazed": 0}[mood]
    if mood == "despair":
        open_h = max(open_h, 26 + 4 * math.sin(t * 12))
    if mood == "amazed" and talk < 0.15:
        open_h = 30
    w = 30 if mood == "amazed" else 46
    path = QPainterPath(QPointF(hx - w, my - smile * 0.4))
    path.quadTo(QPointF(hx, my - smile * 0.4 - 4 - open_h * 0.15), QPointF(hx + w, my - smile * 0.4))
    path.quadTo(QPointF(hx, my + smile + open_h), QPointF(hx - w, my - smile * 0.4))
    p.setPen(_pen("#5a2a20", 5))
    p.setBrush(QColor("#6e2e25"))
    p.drawPath(path)
    if open_h > 16:                                   # Zunge/Zähne andeuten
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f4f1ec"))
        p.drawRect(QRectF(hx - w * 0.55, my - smile * 0.4 - 2, w * 1.1, 6))
        p.setBrush(QColor("#d9645a"))
        p.drawEllipse(QPointF(hx, my + smile * 0.6 + open_h * 0.55), w * 0.45, open_h * 0.25)


# --- Raum -----------------------------------------------------------------------------

def room(p, t, mood_dark=0.0, led=None):
    """Nerd-Zimmer: Wand, Regal mit Büchern, Poster, RGB-Leiste, Tasse, Quietscheente."""
    g = QLinearGradient(0, 0, 0, 1920)
    g.setColorAt(0, QColor("#1a2230"))
    g.setColorAt(1, QColor("#10151d"))
    p.fillRect(QRectF(-400, -400, 1880, 2800), g)
    hue = (t * 40) % 360 if led is None else led
    c = QColor.fromHsv(int(hue), 200, 255)
    rg = QRadialGradient(CX, 1240, 760)
    cc = QColor(c)
    cc.setAlpha(70)
    rg.setColorAt(0, cc)
    rg.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(-400, 300, 1880, 1000), rg)
    # Regal
    p.setPen(_pen(OUT, 5))
    p.setBrush(QColor("#4a3a30"))
    p.drawRect(QRectF(80, 520, 300, 18))
    rnd = random.Random(3)
    x = 96
    while x < 360:
        w = rnd.randint(18, 30)
        h = rnd.randint(70, 110)
        p.setBrush(QColor(rnd.choice(["#e87fa8", "#5aa6f0", "#e9c46a", "#7fd1b9", "#a98bf0", "#f07c5a"])))
        p.drawRect(QRectF(x, 520 - h, w, h))
        x += w + 4
    # Poster
    p.setBrush(QColor("#22303f"))
    p.drawRoundedRect(QRectF(740, 430, 230, 300), 10, 10)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(pv.ACCENT))
    p.drawEllipse(QPointF(855, 540), 60, 60)
    p.setBrush(QColor("#22303f"))
    p.drawEllipse(QPointF(855, 540), 34, 34)
    f = QFont("IBM Plex Mono", 1)
    f.setPixelSize(26)
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.setPen(QColor("#d9e2ea"))
    p.drawText(QRectF(740, 640, 230, 40), Qt.AlignCenter, "sudo make me")
    p.drawText(QRectF(740, 672, 230, 40), Qt.AlignCenter, "a sandwich")
    # Schreibtisch mit RGB-Leiste
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3a2f2a"))
    p.drawRect(QRectF(-400, 1260, 1880, 60))
    p.setBrush(c)
    p.drawRect(QRectF(-400, 1256, 1880, 6))
    p.setBrush(QColor("#2c2420"))
    p.drawRect(QRectF(-400, 1320, 1880, 1200))
    # Tasse mit Dampf
    p.setPen(_pen(OUT, 5))
    p.setBrush(QColor("#e9edf0"))
    p.drawRoundedRect(QRectF(930, 1180, 70, 82), 10, 10)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(985, 1196, 40, 44), -90 * 16, 180 * 16)
    p.setPen(_pen(QColor(255, 255, 255, 90), 5))
    for i in range(3):
        x0 = 950 + i * 16
        path = QPainterPath(QPointF(x0, 1170))
        ph = t * 3 + i
        path.cubicTo(x0 + 10 * math.sin(ph), 1145, x0 - 10 * math.sin(ph), 1120, x0 + 6 * math.sin(ph), 1095)
        p.drawPath(path)
    # Quietscheente
    p.setPen(_pen(OUT, 5))
    p.setBrush(QColor("#ffd166"))
    p.drawEllipse(QPointF(140, 1224), 44, 34)
    p.drawEllipse(QPointF(170, 1180), 26, 26)
    p.setBrush(QColor("#f08a4b"))
    p.drawEllipse(QPointF(196, 1184), 14, 7)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1b232c"))
    p.drawEllipse(QPointF(176, 1174), 4, 4)
    if mood_dark > 0:
        p.fillRect(QRectF(-400, -400, 1880, 2800), QColor(5, 8, 12, int(150 * mood_dark)))


# --- Untertitel -----------------------------------------------------------------------

def caption(p, words, t_line):
    """Wort-Untertitel im TikTok-Stil: Gruppen à 3 Wörter, aktuelles Wort farbig."""
    if not words:
        return
    idx = None
    for i, (_, a, b) in enumerate(words):
        if a <= t_line < b + 0.25:
            idx = i
    if idx is None:
        return
    f = QFont("IBM Plex Sans", 1)
    f.setPixelSize(80)
    f.setWeight(QFont.Black)
    p.setFont(f)
    fm = p.fontMetrics()
    # Gruppen bilden: höchstens 3 Wörter und nicht breiter als 940 px
    groups, cur, cw = [], [], 0
    for i, (w, _, _) in enumerate(words):
        ww = fm.horizontalAdvance(w + " ")
        if cur and (len(cur) == 3 or cw + ww > 940):
            groups.append(cur)
            cur, cw = [], 0
        cur.append(i)
        cw += ww
    groups.append(cur)
    gi = next(g for g in groups if idx in g)
    g0 = gi[0]
    group = [words[i] for i in gi]
    space = fm.horizontalAdvance(" ")
    widths = [fm.horizontalAdvance(w) for w, _, _ in group]
    total = sum(widths) + space * (len(group) - 1)
    x = (W - total) / 2
    y = 1530
    for i, (w, a, _) in enumerate(group):
        k = pv.back((t_line - a) / 0.18) if t_line >= a else 0
        if k <= 0:
            x += widths[i] + space
            continue
        path = QPainterPath()
        path.addText(0, 0, f, w)
        p.save()
        p.translate(x + widths[i] / 2, y)
        p.scale(k, k)
        p.translate(-widths[i] / 2, 0)
        p.setPen(_pen("#0b0f14", 14))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pv.ACCENT if g0 + i == idx else "#ffffff"))
        p.drawPath(path)
        p.restore()
        x += widths[i] + space


def word_times(line, start, end):
    """Wortzeiten schätzen: Länge der Wörter plus Pausen an Satzzeichen, verteilt auf start…end."""
    words = line.split()
    weights = [len(re.sub(r"\W", "", w)) + 2 + (4 if re.search(r"[.!?…]$", w) else 2 if w.endswith(",") else 0)
               for w in words]
    total = sum(weights) or 1
    out, acc = [], start
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append((w, acc, acc + d))
        acc += d
    return out


# --- Ton ------------------------------------------------------------------------------

def tts(kokoro, text, voice, speed=1.05):
    samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-gb" if voice.startswith("b") else "en-us")
    x = np.asarray(samples, dtype=np.float64)
    n = int(len(x) * SR / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)


def speech_bounds(x, thr=0.02):
    idx = np.where(np.abs(x) > thr)[0]
    return (idx[0] / SR, idx[-1] / SR) if len(idx) else (0, len(x) / SR)


def sfx_whoosh(dur=0.35):
    n = int(dur * SR)
    noise = np.random.default_rng(1).uniform(-1, 1, n)
    k = np.linspace(0, 1, n)
    env = np.sin(np.pi * k) ** 2
    out = np.zeros(n)
    a = 0.0
    for i in range(n):                                   # Tiefpass, der sich öffnet
        alpha = 0.02 + 0.25 * k[i]
        a += alpha * (noise[i] - a)
        out[i] = a
    return out * env * 0.9


def sfx_clicks(dur, rate=11):
    rng = np.random.default_rng(5)
    out = np.zeros(int(dur * SR))
    t = 0.0
    while t < dur - 0.05:
        n = int(0.012 * SR)
        click = rng.uniform(-1, 1, n) * np.exp(-np.arange(n) / SR * 400) * rng.uniform(0.15, 0.3)
        pa._add(out, click, t)
        t += rng.uniform(0.6, 1.4) / rate
    return out


def sfx_buzz(dur=0.28, f=110):
    tt = np.arange(int(dur * SR)) / SR
    sq = np.sign(np.sin(2 * np.pi * f * tt)) * 0.25 + np.sign(np.sin(2 * np.pi * f * 1.5 * tt)) * 0.12
    return sq * np.minimum(1, (dur - tt) * 20)


def sfx_ding():
    return pa._tone(pa._note("E6"), 1.2, 0.5, ((1, 1.0), (2.76, 0.3), (5.4, 0.1))) * 0.5


def sfx_pop():
    n = int(0.08 * SR)
    tt = np.arange(n) / SR
    return np.sin(2 * np.pi * (600 + 900 * np.exp(-tt * 60)) * tt) * np.exp(-tt * 45) * 0.4


# --- Szenen ---------------------------------------------------------------------------

def build(shots, kokoro, voice):
    S = lambda n: os.path.join(shots, f"{n}.png")     # noqa: E731
    logo_path = os.path.join(pv.ROOT, "docs/logo/wordmark-dark.png")
    beats = [
        # (Bildschirm, Stimmung, Pose, Sprechtext, Mindestdauer, Extras)
        ("windows", "happy", "desk", "I'm Tom. On Windows, everything just worked.", 3.0, {"typing"}),
        ("terminal", "worried", "desk", "Then I switched to Arch Linux. How hard can it be?", 3.0, {"typing"}),
        ("panic", "despair", "head", "Turns out, pretty hard. Errors everywhere!", 3.0, {"panic"}),
        ("logo", "amazed", "desk", "Then I found Tux dex.", 2.4, {"ding"}),
        ("tuxdex", "joy", "thumbs", "Updates, apps, backups and snapshots. All with one click.", 3.4, {"confetti"}),
        ("end", None, None, "Tux dex. Free and open source. Download now, link in bio.", 4.2, {"jingle"}),
    ]
    lines, t0 = [], 0.0
    for scr, mood, pose, text, mind, extra in beats:
        audio = tts(kokoro, text, voice)
        # Endkarte: erst das Sound-Logo (1,6 s), dann Tom, danach 1,5 s Ruhe
        lead = 1.9 if scr == "end" else 0.25
        a, b = speech_bounds(audio)
        dur = max(mind, lead + len(audio) / SR + (1.5 if scr == "end" else 0.35))
        shown = text.replace("Tux dex", "Tuxdex")
        lines.append({"scr": scr, "mood": mood, "pose": pose, "start": t0, "dur": dur, "lead": lead,
                      "audio": audio, "extra": extra,
                      "words": word_times(shown, lead + a, lead + b)})
        t0 += dur
    total = t0

    # Stimme als Gesamtspur + Hüllkurve für die Mundbewegung
    voice_track = np.zeros(int(total * SR) + SR)
    for ln in lines:
        pa._add(voice_track, ln["audio"], ln["start"] + ln["lead"])
    hop = SR // FPS
    env = np.array([np.sqrt(np.mean(voice_track[i:i + hop] ** 2)) for i in range(0, len(voice_track), hop)])
    env = env / (np.percentile(env[env > 0.003], 90) if np.any(env > 0.003) else 1)
    env = np.clip(np.convolve(env, [0.25, 0.5, 0.25], mode="same") * 1.1, 0, 1)

    def screen_fn(scr):
        return {"windows": pc.screen_windows, "terminal": pc.screen_terminal,
                "panic": pc.screen_terminal_panic,
                "logo": lambda q, r, tt: pc.screen_logo(q, r, tt, logo_path),
                "tuxdex": lambda q, r, tt: pc.screen_tuxdex(q, r, tt, S("update"))}[scr]

    end_line = lines[-1]
    dl_at = next((a for w, a, _ in end_line["words"] if w.startswith("Download")), 2.5)
    end = pv.end_card("Arch Linux ohne Terminal-Stress", cta=("Download now", dl_at))

    def frame(p, t, i):
        ln = next((x for x in lines if x["start"] <= t < x["start"] + x["dur"]), lines[-1])
        lt = t - ln["start"]
        talk = float(env[min(i, len(env) - 1)])
        if ln["scr"] == "end":
            pv.glow(p, t)
            end(p, lt, ln["dur"])
            return
        mood = ln["mood"]
        if ln["scr"] == "terminal" and lt < 0.8:
            mood = "neutral"
        # Kamera: langsamer Zoom, Wackeln bei Panik
        z = 1.0 + 0.05 * pv.ease(lt / ln["dur"])
        shake = (math.sin(t * 47) * 6, math.cos(t * 39) * 6) if ln["scr"] == "panic" else (0, 0)
        p.save()
        p.translate(W / 2 + shake[0], H / 2 + shake[1])
        p.scale(z, z)
        p.translate(-W / 2, -H / 2)
        p.save()                                          # Szene etwas kleiner und höher als im Cartoon-Reel,
        p.translate(540, 1080)                            # damit unten Platz für die Untertitel bleibt
        p.scale(1.15, 1.15)
        p.translate(-540, -1000)
        dark = min(1, lt / 0.5) if ln["scr"] == "panic" else max(0, 1 - lt / 0.8) if ln["scr"] == "logo" else 0
        led = 0 if ln["scr"] == "panic" else None
        room(p, t, dark, led)
        tom(p, t, mood, talk)
        pc.arms(p, t, mood, ln["pose"] if not (ln["pose"] == "thumbs" and lt < 0.6) else "desk")
        glow = pv.DANGER if ln["scr"] == "panic" else pv.ACCENT if ln["scr"] in ("logo", "tuxdex") else None
        pc.laptop_front(p, screen_fn(ln["scr"]), lt, glow=glow)
        pc.hands(p, t, mood, ln["pose"] if not (ln["pose"] == "thumbs" and lt < 0.6) else "desk")
        if ln["scr"] == "panic":
            pc.rain_cloud(p, lt)
            pc.question_marks(p, lt)
        if ln["scr"] == "logo":
            pc.lightbulb(p, lt)
        p.restore()
        p.restore()
        if ln["scr"] == "tuxdex":
            pc.confetti(p, lt)
        if ln is lines[0]:                               # Hook ganz oben
            pv.text(p, "POV:", 150, 64, pv.MUTED, lt, 0.0)
            pv.text(p, "You switch to Arch Linux", 230, 72, "#ffffff", lt, 0.15)
        caption(p, ln["words"], lt)
        if lt < 0.12 and ln is not lines[0]:             # kurzer Blitz beim Szenenwechsel
            p.fillRect(0, 0, W, H, QColor(255, 255, 255, int(110 * (1 - lt / 0.12))))

    # Soundeffekte + Musik
    fx = np.zeros_like(voice_track)
    for ln in lines:
        st = ln["start"]
        if st > 0:
            pa._add(fx, sfx_whoosh() * 0.5, st - 0.15)
        if "typing" in ln["extra"]:
            pa._add(fx, sfx_clicks(ln["dur"] - 0.3) * 0.8, st + 0.1)
        if "panic" in ln["extra"]:
            for k in (0.2, 0.9, 1.6):
                pa._add(fx, sfx_buzz() * 0.35, st + k)
        if "ding" in ln["extra"]:
            pa._add(fx, sfx_ding(), st + 0.15)
        if "confetti" in ln["extra"]:
            for k in range(6):
                pa._add(fx, sfx_pop() * 0.6, st + 0.1 + k * 0.07)
        if "jingle" in ln["extra"]:
            pa._add(fx, pa.jingle() * 0.8, st)
    bed = pa.beat(total)
    bed = np.pad(bed, (0, len(voice_track) - len(bed)))
    duck = np.repeat(np.clip(env * 2, 0, 1), hop)[:len(bed)]
    duck = np.pad(duck, (0, len(bed) - len(duck)))
    mix = bed * (0.35 - 0.2 * duck) + voice_track * 1.0 + fx * 0.6
    mix = mix[: int(total * SR)]
    return total, frame, mix


def render(out, total, frame, mix):
    n = int(total * FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-pix_fmt", "yuv420p", out + ".video.mp4"]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    img = QImage(W, H, QImage.Format_RGBA8888)
    for i in range(n):
        img.fill(QColor(pv.BG))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        frame(p, i / FPS, i)
        p.end()
        ff.stdin.write(img.constBits().tobytes())
    ff.stdin.close()
    ff.wait()
    wav = out + ".wav"
    pa.write_wav(wav, mix)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out + ".video.mp4", "-i", wav, "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], check=True)
    for f in (wav, out + ".video.mp4"):
        os.remove(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True)
    ap.add_argument("--kokoro", required=True)
    ap.add_argument("--voice", default="am_michael")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    from kokoro_onnx import Kokoro
    QGuiApplication(sys.argv)
    pv.LANG = "en"
    k = Kokoro(os.path.join(a.kokoro, "kokoro-v1.0.onnx"), os.path.join(a.kokoro, "voices-v1.0.bin"))
    os.makedirs(a.out, exist_ok=True)
    out = os.path.join(a.out, f"tuxdex_tom_{a.voice}.mp4")
    total, frame, mix = build(a.shots, k, a.voice)
    render(out, total, frame, mix)
    print(f"{out}  ({total:.1f} s)")


if __name__ == "__main__":
    main()
