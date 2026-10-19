"""Ton für die Werbevideos: eigener, lizenzfreier Beat und das Tuxdex-Sound-Logo (alles synthetisch).

Eigene Musik (z. B. ein NCS-Track) geht mit promo_video.py --music DATEI – dann liegt nur das
Sound-Logo darüber. NCS verlangt bei Nutzung die Nennung des Titels in der Beschreibung.
"""
import subprocess
import wave

import numpy as np

SR = 44100
BPM = 118


def _env(n, attack=0.005, decay=0.3):
    t = np.arange(n) / SR
    a = np.minimum(t / attack, 1.0)
    return a * np.exp(-t / decay)


def _tone(freq, dur, decay=0.4, harmonics=((1, 1.0), (2, 0.35), (3, 0.12)), attack=0.004):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = sum(a * np.sin(2 * np.pi * freq * h * t) for h, a in harmonics)
    return s * _env(n, attack, decay)


def _kick(dur=0.35):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 50 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _hat(dur=0.06, rng=np.random.default_rng(7)):
    n = int(dur * SR)
    noise = rng.uniform(-1, 1, n)
    hp = np.diff(noise, prepend=0)                  # grober Hochpass
    return hp * np.exp(-np.arange(n) / SR * 60) * 0.35


def _add(buf, sig, at):
    i = int(at * SR)
    if i >= len(buf):
        return
    sig = sig[: len(buf) - i]
    buf[i:i + len(sig)] += sig


def _note(name):
    names = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2, "G#": -1, "A": 0,
             "A#": 1, "B": 2}
    return 440.0 * 2 ** ((names[name[:-1]] + 12 * (int(name[-1]) - 4)) / 12)


def jingle():
    """Tuxdex-Sound-Logo: aufsteigender Glockenakkord (A – C# – E – A), ca. 1,6 s."""
    out = np.zeros(int(2.0 * SR))
    bell = ((1, 1.0), (2.01, 0.4), (3.0, 0.18), (4.2, 0.08))
    for i, n in enumerate(("A4", "C#5", "E5", "A5")):
        _add(out, _tone(_note(n), 1.6 - i * 0.08, 0.55, bell) * 0.5, i * 0.09)
    _add(out, _tone(_note("A3"), 1.2, 0.6, ((1, 1.0), (2, 0.2))) * 0.35, 0.27)
    return out


def beat(duration):
    """Lockerer Electro-Beat (A-Moll: Am – F – C – G), 4/4, mit Ein- und Ausblenden."""
    out = np.zeros(int((duration + 1) * SR))
    beat_s = 60 / BPM
    chords = [("A2", ("A3", "C4", "E4")), ("F2", ("F3", "A3", "C4")),
              ("C3", ("G3", "C4", "E4")), ("G2", ("G3", "B3", "D4"))]
    n_beats = int(duration / beat_s) + 1
    for b in range(n_beats):
        t = b * beat_s
        bar = (b // 4) % 4
        bass, chord = chords[bar]
        if b >= 4:                                   # erst ab Takt 2 Kick und Bass – Intro bleibt ruhig
            _add(out, _kick() * 0.9, t)
            _add(out, _tone(_note(bass), beat_s * 0.9, 0.25, ((1, 1.0), (2, 0.3))) * 0.35, t + beat_s / 2)
        _add(out, _hat(), t + beat_s / 2)
        if b % 4 == 0:
            for n in chord:
                _add(out, _tone(_note(n), beat_s * 4, 1.6, ((1, 1.0), (2, 0.15)), attack=0.08) * 0.12, t)
        if b % 2 == 1:                               # kleines Arpeggio darüber
            n = chord[(b // 2) % 3]
            _add(out, _tone(_note(n) * 2, beat_s, 0.18, ((1, 1.0), (3, 0.1))) * 0.10, t + beat_s * 0.25)
    out = out[: int(duration * SR)]
    fade = int(1.2 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    out[: int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))
    return out


def write_wav(path, mono):
    peak = np.max(np.abs(mono)) or 1.0
    pcm = (mono / peak * 0.85 * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def soundtrack(path, duration, jingle_at, music=None):
    """WAV mit Beat (oder eigener Musik) und dem Sound-Logo bei jingle_at Sekunden."""
    j = np.zeros(int(duration * SR))
    _add(j, jingle(), jingle_at)
    if music is None:
        bed = beat(duration)
        bed[int(jingle_at * SR):] *= 0.55            # Beat beim Logo etwas zurücknehmen
        write_wav(path, bed * 0.8 + j * 0.9)
        return
    jpath = path + ".jingle.wav"
    write_wav(jpath, j)
    flt = (f"[0:a]atrim=0:{duration},asetpts=N/SR/TB,afade=t=in:d=0.4,"
           f"afade=t=out:st={duration - 1.5}:d=1.5,volume=0.7[m];"
           f"[m][1:a]amix=inputs=2:duration=first:normalize=0[a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", music, "-i", jpath, "-filter_complex", flt,
                    "-map", "[a]", "-ar", str(SR), path], check=True)
