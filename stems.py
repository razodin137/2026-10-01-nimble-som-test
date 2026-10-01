"""Generate 4-bar stem loops for the demo project.

126 BPM, 4/4 -> exactly 21000 samples per beat @ 44.1 kHz, so a 16-beat
(4-bar) stem is 336000 samples and tiles seamlessly.

Stdlib only (wave + math) - runs in any Python.
"""
import math
import os
import random
import struct
import wave

SR = 44100
BPM = 126
SPB = int(SR * 60 / BPM)  # samples per beat = 21000
BEATS = 16  # 4 bars
N = SPB * BEATS
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio")


def write_wav(name, samples):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".wav")
    peak = max(1e-9, max(abs(s) for s in samples))
    scale = 0.5 / peak  # ~ -6 dBFS
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s * scale)) * 32767)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    print("wrote", path, f"({len(samples)/SR:.3f}s)")
    return path


def env(i, attack, decay, length=None):
    a, d = int(attack * SR), int(decay * SR)
    if length is None:
        length = d
    if i < a:
        return i / max(1, a)
    if i < length:
        return math.exp(-(i - a) / max(1.0, d))
    return 0.0


def add(buf, start, samples):
    for j, s in enumerate(samples):
        k = start + j
        if 0 <= k < len(buf):
            buf[k] += s


def kick(beat):
    out = [0.0] * int(SPB * 0.45)
    for i in range(len(out)):
        t = i / SR
        f = 95 * math.exp(-t * 22) + 42
        ph = 2 * math.pi * (42 * t + (95 - f) * (1 - math.exp(-t * 22)) / 22 / (2 * math.pi))
        out[i] = math.sin(ph) * env(i, 0.001, 0.16) * 1.0 + (random.random() * 2 - 1) * 0.05 * math.exp(-i / (0.004 * SR))
    return out


def snare(beat, accent=1.0):
    out = [0.0] * int(SPB * 0.4)
    for i in range(len(out)):
        t = i / SR
        n = (random.random() * 2 - 1)
        body = math.sin(2 * math.pi * 185 * t) * 0.4
        out[i] = (n * 0.8 + body) * env(i, 0.0008, 0.09) * accent
    return out


def hat(beat, accent=1.0, length=0.05):
    out = [0.0] * int(SPB * length)
    prev = 0.0
    for i in range(len(out)):
        n = random.random() * 2 - 1
        hp = n - prev  # crude high-pass
        prev = n
        out[i] = hp * env(i, 0.0004, 0.025) * accent * 0.7
    return out


def note(freq, dur, attack=0.004, decay=0.25, harmonics=(1.0, 0.5, 0.25, 0.12)):
    out = [0.0] * int(dur * SR)
    for i in range(len(out)):
        t = i / SR
        s = sum(h * math.sin(2 * math.pi * freq * k * t) for k, h in zip(range(1, len(harmonics) + 1), harmonics))
        out[i] = s * env(i, attack, decay, len(out))
    return out


def stem_drums():
    """Four-on-the-floor kick, snare on 2 & 4, ghost snare in bar 4."""
    buf = [0.0] * N
    for b in range(BEATS):
        add(buf, b * SPB, kick(b))
    for b in (1, 3, 5, 7, 9, 11, 13, 15):
        add(buf, b * SPB, snare(b, 0.9))
    for s in (12, 13, 14, 15):  # beat 13-16 sixteenth ghost rolls in bar 4
        for j in range(4):
            add(buf, s * SPB + j * (SPB // 4), snare(s, 0.28))
    return buf


def stem_bass():
    """Driving offbeat 8ths: A1 with a G1 turn in bar 4."""
    buf = [0.0] * N
    a1, g1, c2 = 55.0, 49.0, 65.41
    pattern = [a1] * 24 + [c2] * 4 + [g1] * 4 + [a1] * 4
    for e, f in enumerate(pattern):
        add(buf, e * SPB // 2, note(f, 0.22, decay=0.18, harmonics=(1.0, 0.6, 0.35, 0.2, 0.1)))
    return buf


def stem_hats():
    """16th hats, accented offbeats, open hat on the 'and' of 4."""
    buf = [0.0] * N
    for i in range(BEATS * 4):
        b = i // 4
        accent = 0.6 if i % 4 == 0 else (1.0 if i % 4 == 2 else 0.45)
        length = 0.12 if (i % 16 == 14) else 0.05
        add(buf, i * SPB // 4, hat(i, accent, length))
    return buf


def stem_pad():
    """A-minor pad, slow attack, gentle tremolo, Cmaj color in bar 3-4."""
    buf = [0.0] * N
    for freqs, start_beat, beats in (
        ((220.0, 261.63, 329.63), 0, 8),  # Am
        ((196.0, 246.94, 329.63), 8, 4),  # G
        ((261.63, 329.63, 392.0), 12, 4),  # C
    ):
        start, length = start_beat * SPB, beats * SPB
        for i in range(length):
            t = i / SR
            e = min(1.0, i / (0.8 * SR)) * min(1.0, (length - i) / (0.9 * SR) + 0.2)
            trem = 0.85 + 0.15 * math.sin(2 * math.pi * 0.7 * t)
            s = sum(math.sin(2 * math.pi * f * t + math.sin(2 * math.pi * f * 0.7 * t) * 0.15) for f in freqs)
            buf[start + i] += s / len(freqs) * e * trem * 0.6
    return buf


def stem_lead():
    """Sparse hook: call in bars 1-2, answer in bars 3-4."""
    buf = [0.0] * N
    a3, b3, c4, e4, g4 = 220.0, 246.94, 261.63, 329.63, 392.0
    phrase = [(0, a3, 0.75), (1.5, c4, 0.5), (2, e4, 1.0), (4.5, e4, 0.5),
              (6, c4, 1.5), (8, b3, 0.5), (9, c4, 0.5), (10, e4, 2.0),
              (13, g4, 1.5), (14.5, e4, 1.5)]
    for beat, f, dur in phrase:
        add(buf, int(beat * SPB), note(f, dur * 60 / BPM * 0.95, attack=0.01, decay=0.5,
                                      harmonics=(1.0, 0.33, 0.11)))
    return buf


def stem_fill():
    """Snare roll + noise riser across bar 4 (fires pre-jump)."""
    buf = [0.0] * N
    for b in range(12):  # silence bars 1-3, light ticks to keep loop alive
        add(buf, b * SPB, hat(b, 0.12, 0.03))
    for i, (start8, accent) in enumerate([(0.0, 0.35), (1.0, 0.4), (2.0, 0.5), (3.0, 0.6),
                                          (4.0, 0.7), (5.0, 0.8), (6.0, 0.9), (7.0, 1.0)]):
        add(buf, int((12 + start8) * SPB), snare(0, accent))
    for i in range(4 * SPB):  # riser noise swell
        t = i / (4 * SPB)
        add(buf, 12 * SPB + i, [(random.random() * 2 - 1) * 0.35 * t * t])
    # 32nd roll in the last half beat
    for j in range(8):
        add(buf, 15 * SPB + j * (SPB // 8), snare(0, 0.8))
    return buf


def main():
    random.seed(126)  # deterministic stems
    write_wav("drums", stem_drums())
    write_wav("bass", stem_bass())
    write_wav("hats", stem_hats())
    write_wav("pad", stem_pad())
    write_wav("lead", stem_lead())
    write_wav("fill", stem_fill())


if __name__ == "__main__":
    main()