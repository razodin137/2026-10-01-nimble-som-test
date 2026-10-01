"""Laya-steered music synthesis engine (stdlib only).

Public API:
    render(spec, out_dir) -> list of per-instrument results

spec = {
    "idea": str,             # English idea (seeds the RNG, logged into MIDI)
    "bpm": int,
    "num": int, "den": int,  # time signature, e.g. 4/4, 6/8
    "root": str,            # "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"
    "mode": str,            # "major" | "minor" | "dorian" | "mixolydian"
    "groove": str,          # "straight" | "swing" | "bossa clave" | "halftime" | "boom bap" | "one drop"
    "instruments": [str],   # names from INSTRUMENTS in pipeline.py
    "bars": int,            # project length in bars
}

Each result: {"name", "wav", "midi", "seconds"}; the WAV is the rendered
performance, the MIDI carries the same notes for editing in REAPER.
"""
import math
import os
import random
import struct
import wave
import zlib

SR = 44100
PPQ = 480

ROOT_PCS = {"C": 0, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "Gb": 6,
            "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "harmonic minor": [0, 2, 3, 5, 7, 8, 11],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
}

PROGRESSIONS = {  # scale degrees, one chord per bar
    "major": [0, 5, 2, 6],          # I vi ii V
    "minor": [0, 5, 2, 6],          # i VI III VII
    "dorian": [0, 3, 0, 3],         # i7 IV7 vamp
    "mixolydian": [0, 3, 6, 0],     # I IV bVII I
    "harmonic minor": [0, 3, 4, 0], # i iv V7 i
    "phrygian": [0, 1, 0, 1],       # i bII flamenco vamp
    "lydian": [0, 1, 3, 0],         # I II vi I
}

GM_PROGRAMS = {
    "Nylon Guitar": (24, False), "Clean Electric Guitar": (27, False),
    "Acoustic Upright Bass": (32, False), "Electric Bass": (33, False),
    "808 Sub Bass": (39, False), "Rhodes Piano": (4, False),
    "Grand Piano": (0, False), "Jazz Organ": (17, False),
    "Analog Pad": (88, False), "Brush Kit": (0, True),
    "Acoustic Drum Kit": (0, True), "Trap Drums": (0, True),
    "Shaker and Percussion": (0, True), "Congas and Bongo": (0, True),
    "Strings": (48, False), "Brass Section": (61, False),
    "Flute": (73, False), "Saxophone": (66, False),
    "Vibraphone": (11, False), "Synth Lead": (81, False),
    "Arp Synth": (82, False), "Vocal Guide": (53, False),
    "Turntable FX": (0, True),
    "Dusty Rhodes": (4, False), "Overdriven Guitar": (30, False),
    "Kalimba": (108, False), "Retro Drum Machine": (0, True),
}

# GM percussion note numbers
KICK, SNARE, CLAP = 36, 38, 39
HH_CLOSED, HH_OPEN, RIDE, CRASH = 42, 46, 51, 49
SHAKER, CONGA_HI, CONGA_MID, CONGA_LOW = 70, 62, 63, 64


# ----------------------------------------------------------------- theory --
def degree_midi(deg, root_pc, scale, base_oct):
    """MIDI pitch of a scale degree (negative degrees and >= 7 wrap octaves)."""
    octave = base_oct + (deg // 7 if deg >= 0 else (deg + 1) // 7 - 1)
    d = deg % 7
    return 12 * (octave + 1) + root_pc + scale[d]


def chord_midi(deg, root_pc, scale, base_oct):
    """Four-note 7th voicing stacked upward from a scale degree."""
    return [degree_midi(deg + step, root_pc, scale, base_oct) for step in (0, 2, 4, 6)]


def midi_to_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# ---------------------------------------------------------------- helpers --
def _env(n, attack, decay, tail=0.0):
    a, d = max(1, int(attack * SR)), max(1, int(decay * SR))
    end = min(n, d + int(tail * SR))
    out = [0.0] * n
    for i in range(min(a, n)):
        out[i] = i / a
    base = math.exp(-1 / d)
    p = 1.0
    for i in range(a, end):
        out[i] = p
        p *= base
    return out


def _mix(buf, start, samples):
    n = len(buf)
    for j, s in enumerate(samples):
        k = start + j
        if 0 <= k < n:
            buf[k] += s


def _lowpass(samples, w):
    """One-pole-ish smoothing over a window of w samples."""
    out = [0.0] * len(samples)
    acc = 0.0
    for i, s in enumerate(samples):
        acc += s - acc * (1.0 / w)
        out[i] = acc
    return out


def _saw(p):
    return 2.0 * (p - math.floor(p + 0.5))


# ----------------------------------------------------------------- voices --
def v_kick(freq, dur, vel, style="punch"):
    n = int(dur * SR)
    out = [0.0] * n
    decay = 0.16 if style == "punch" else 0.45
    for i in range(n):
        t = i / SR
        f = freq * (1 + 1.6 * math.exp(-t * (28 if style == "punch" else 12)))
        if i:
            ph = out_phase[0] + 2 * math.pi * f / SR
        else:
            ph = 0.0
        out_phase[0] = ph
        out[i] = math.sin(ph) * math.exp(-t / decay) * vel
    out_phase[0] = 0.0
    click = int(0.004 * SR)
    for i in range(min(click, n)):
        out[i] += (random.random() * 2 - 1) * 0.4 * (1 - i / click) * vel
    return out


out_phase = [0.0]  # phase accumulator for kicked sines (freq changes)


def v_snare(freq, dur, vel, brush=False):
    n = int(dur * SR)
    out = [0.0] * n
    tone_d, noise_d = (0.25, 0.12) if brush else (0.10, 0.08)
    for i in range(n):
        t = i / SR
        noise = random.random() * 2 - 1
        body = math.sin(2 * math.pi * 185 * t) * 0.35
        out[i] = (noise + body) * math.exp(-t / noise_d if not brush else t / tone_d) * vel
        if not brush:
            out[i] *= math.exp(-t / tone_d) + 0.2
    return _lowpass(out, 2) if brush else out


def v_hat(freq, dur, vel, open_=False):
    n = int((0.09 if open_ else 0.045) * SR)
    out = [0.0] * n
    prev = 0.0
    for i in range(n):
        s = random.random() * 2 - 1
        out[i] = (s - prev) * math.exp(-i / (0.010 if open_ else 0.005) / 1) * vel * 0.7
        prev = s
    return out


def v_clap(freq, dur, vel):
    bursts = [0.0, 0.011, 0.023]
    n = int(0.15 * SR)
    out = [0.0] * n
    for b in bursts:
        for i in range(int(0.05 * SR)):
            out[int(b * SR) + i] += (random.random() * 2 - 1) * math.exp(-i / (0.008 * SR)) * vel
    return out


def v_ride(freq, dur, vel):
    n = int(0.35 * SR)
    out = [0.0] * n
    for i in range(n):
        t = i / SR
        out[i] = ((1 if math.sin(2 * math.pi * 627 * t) > 0 else -1) * 0.3 +
                  (random.random() * 2 - 1) * 0.5) * math.exp(-t / 0.09) * vel * 0.5
    return out


def v_conga(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    for i in range(n):
        t = i / SR
        f = freq * (1 + 0.35 * math.exp(-t * 30))
        out[i] = math.sin(2 * math.pi * f * t) * math.exp(-t / 0.06) * vel
    return out


def v_shaker(freq, dur, vel):
    n = int(0.05 * SR)
    out = [0.0] * n
    for i in range(n):
        s = random.random() * 2 - 1
        out[i] = s * math.sin(i / n * math.pi) ** 2 * vel * 0.4
    return _lowpass(out, 3)


def v_pluck(freq, dur, vel):
    """Karplus-Strong string - nylon/electric guitar, short decay."""
    n = int(dur * SR)
    period = max(2, int(SR / freq))
    rng = random.random
    buf = [rng() * 2 - 1 for _ in range(period)]
    out = [0.0] * n
    idx = 0
    damp = 0.996
    for i in range(n):
        cur = buf[idx]
        nxt = buf[(idx + 1) % period]
        new = damp * 0.5 * (cur + nxt)
        buf[idx] = new
        out[i] = cur * vel * 0.8
        idx = (idx + 1) % period
    a = int(0.002 * SR)
    for i in range(min(a, n)):
        out[i] *= i / a
    return out


def v_bass(freq, dur, vel, upright=False):
    n = int(dur * SR)
    out = [0.0] * n
    att = 0.006 if not upright else 0.02
    e = _env(n, att, dur * 0.8)
    for i in range(n):
        t = i / SR
        v = _saw(freq * t) if not upright else (math.sin(2 * math.pi * freq * t) + 0.3 * _saw(freq * t))
        out[i] = v * e[i] * vel
    return _lowpass(out, 24 if upright else 10)


def v_sub(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.002, dur * 0.9)
    for i in range(n):
        t = i / SR
        f = freq * (1 + 0.5 * math.exp(-t * 18))
        out[i] = math.tanh(1.8 * math.sin(2 * math.pi * f * t)) * e[i] * vel
    return out


def v_epiano(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.004, dur * 0.7)
    for i in range(n):
        t = i / SR
        idx = 3.0 * math.exp(-t * 6)
        car = math.sin(2 * math.pi * freq * t + idx * math.sin(2 * math.pi * freq * t))
        bell = math.sin(2 * math.pi * freq * 7 * t) * 0.15 * math.exp(-t * 12)
        out[i] = (car + bell) * e[i] * vel * 0.5
    return out


def v_organ(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.01, dur * 0.95)
    harmonics = (1.0, 0.5, 0.35, 0.25, 0.12, 0.08, 0.05)
    for i in range(n):
        t = i / SR
        trem = 0.85 + 0.15 * math.sin(2 * math.pi * 5.2 * t)
        out[i] = sum(h * math.sin(2 * math.pi * freq * (k + 1) * t)
                     for k, h in enumerate(harmonics)) / 2.4 * e[i] * vel * trem
    return out


def v_pad(freq, dur, vel, strings=False):
    n = int(dur * SR)
    raw = [0.0] * n
    detunes = (0.997, 1.0, 1.004)
    for i in range(n):
        t = i / SR
        vib = 1 + 0.003 * math.sin(2 * math.pi * (4.5 if strings else 0.7) * t)
        s = sum(_saw(freq * d * vib * t) for d in detunes) / len(detunes)
        raw[i] = s
    raw = _lowpass(raw, 40 if strings else 64)
    e = _env(n, 0.25 if not strings else 0.12, dur)
    return [raw[i] * e[i] * vel * 0.35 for i in range(n)]


def v_brass(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.03, dur)
    for i in range(n):
        t = i / SR
        out[i] = math.tanh(2.2 * (_saw(freq * t) + 0.4 * math.sin(2 * math.pi * freq * t))) * e[i] * vel * 0.4
    return _lowpass(out, 30)


def v_flute(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.06, dur * 0.85)
    for i in range(n):
        t = i / SR
        vib = 1 + 0.004 * math.sin(2 * math.pi * 5.5 * t) * min(1, t * 3)
        breath = (random.random() * 2 - 1) * 0.06
        out[i] = (math.sin(2 * math.pi * freq * vib * t) + breath) * e[i] * vel * 0.5
    return out


def v_sax(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.02, dur)
    for i in range(n):
        t = i / SR
        vib = 1 + 0.005 * math.sin(2 * math.pi * 5.0 * t)
        out[i] = (_saw(freq * vib * t) + 0.3 * math.sin(2 * math.pi * freq * t)) * e[i] * vel * 0.35
    return _lowpass(out, 22)


def v_vibe(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.003, dur)
    for i in range(n):
        t = i / SR
        trem = 0.6 + 0.4 * math.sin(2 * math.pi * 4.5 * t)
        out[i] = (math.sin(2 * math.pi * freq * t) +
                  0.2 * math.sin(2 * math.pi * freq * 4 * t)) * e[i] * vel * trem * 0.5
    return out


def v_lead(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.005, dur * 0.9)
    for i in range(n):
        t = i / SR
        s = math.copysign(1, math.sin(2 * math.pi * freq * t))
        out[i] = 0.6 * (s + 0.5 * _saw(freq * 1.002 * t)) * e[i] * vel * 0.3
    return _lowpass(out, 48)


def v_vocal(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.05, dur * 0.9)
    for i in range(n):
        t = i / SR
        vib = 1 + 0.006 * math.sin(2 * math.pi * 5.5 * t)
        out[i] = (math.sin(2 * math.pi * freq * vib * t) +
                  0.5 * math.sin(2 * math.pi * freq * 2 * vib * t) +
                  0.25 * math.sin(2 * math.pi * freq * 3 * vib * t)) * e[i] * vel * 0.35
    return out


def v_vinyl(freq, dur, vel):
    n = int(dur * SR)
    out = [0.0] * n
    sweep = freq > 0
    for i in range(n):
        t = i / SR
        if sweep:
            out[i] = (random.random() * 2 - 1) * (t / (dur)) * vel * 0.3
        else:
            if random.random() < 0.002:
                out[i] = (random.random() * 2 - 1) * vel * 0.8
    return out


def v_dusty(freq, dur, vel):
    """Rhodes with tape wow/flutter - lo-fi keys."""
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.004, dur * 0.7)
    for i in range(n):
        t = i / SR
        wow = 1 + 0.005 * math.sin(2 * math.pi * 0.8 * t) + 0.0015 * math.sin(2 * math.pi * 5.3 * t)
        ph = 2 * math.pi * freq * wow * t
        idx = 3.0 * math.exp(-t * 6)
        car = math.sin(ph + idx * math.sin(ph))
        bell = math.sin(2 * math.pi * freq * 7 * t) * 0.15 * math.exp(-t * 12)
        out[i] = (car + bell) * e[i] * vel * 0.5
    return _lowpass(out, 40)


def v_dist_gtr(freq, dur, vel):
    """Saturated saw - overdriven guitar stabs."""
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.008, dur * 0.9)
    for i in range(n):
        t = i / SR
        s = _saw(freq * t) + 0.5 * math.sin(2 * math.pi * freq * t)
        out[i] = math.tanh(3.0 * s) * e[i] * vel * 0.3
    return _lowpass(out, 30)


def v_kalimba(freq, dur, vel):
    """Thumb piano - fast-decay sine with a metallic 4th partial."""
    n = int(dur * SR)
    out = [0.0] * n
    e = _env(n, 0.002, dur * 0.4)
    for i in range(n):
        t = i / SR
        out[i] = (math.sin(2 * math.pi * freq * t) +
                  0.35 * math.sin(2 * math.pi * freq * 4 * t)) * e[i] * vel * 0.6
    return out


# --------------------------------------------------------------- patterns --
# Events: (beat_q, dur_q, midi_note, velocity). Drums use GM percussion notes.

def _swing_at(t, groove):
    shift = {"straight": 0.0, "halftime": 0.0, "swing": 0.14, "bossa clave": 0.03,
             "boom bap": 0.10, "one drop": 0.0}.get(groove, 0.0)
    frac = t * 2 % 2  # 0.0 on downbeat 8ths, 1.0 on offbeat 8ths
    return t + (shift if abs(frac - 1.0) < 1e-9 else 0.0)


def chord_sequence(spec):
    pc, scale = ROOT_PCS[spec["root"]], SCALES[spec["mode"]]
    prog = PROGRESSIONS[spec["mode"]]
    seq = []
    for bar in range(spec["bars"]):
        deg = prog[bar % len(prog)]
        seq.append(chord_midi(deg, pc, scale, 3))  # octave 3 (MIDI 48-ish root)
    return seq


def pat_drums(spec):
    name, groove = spec["_inst"], spec["groove"]
    qpb, bars = spec["_qpb"], spec["bars"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        if groove == "one drop":  # reggae: kick + stick on beat 3, steady hats
            ev += [(b + qpb * 0.5, 0.5, KICK, 0.95), (b + qpb * 0.5, 0.4, SNARE, 0.5)]
            for e in range(qpb * 2):
                ev.append((_swing_at(b + e / 2, groove), 0.1, HH_CLOSED, 0.65 if e % 2 else 0.45))
            if bar % 2 == 1:
                ev.append((b + qpb * 0.75, 0.2, HH_OPEN, 0.6))
        elif groove == "boom bap":  # swung hip-hop: kicks up front, snare 2 & 4
            ev += [(b, 0.5, KICK, 1.0), (b + qpb * 0.6875, 0.35, KICK, 0.85)]
            ev += [(b + qpb * 0.25, 0.5, SNARE, 0.95), (b + qpb * 0.75, 0.5, SNARE, 0.95)]
            for e in range(qpb * 2):
                t = b + e / 2
                v = 0.75 if e % 2 == 0 else 0.55
                ev.append((_swing_at(t, groove), 0.1, HH_CLOSED, v))
            if bar % 2 == 1:
                ev.append((b + qpb * 0.875, 0.12, HH_OPEN, 0.6))
        elif name == "Retro Drum Machine":  # tight 80s electronic beat
            ev += [(b, 0.4, KICK, 1.0), (b + qpb * 0.625, 0.4, KICK, 0.9)]
            ev += [(b + qpb * 0.25, 0.4, SNARE, 0.9), (b + qpb * 0.75, 0.4, SNARE, 0.9)]
            ev += [(b + qpb * 0.25, 0.4, CLAP, 0.8), (b + qpb * 0.75, 0.4, CLAP, 0.8)]
            for e in range(qpb * 2):
                ev.append((_swing_at(b + e / 2, groove), 0.1, HH_CLOSED, 0.7 if e % 2 == 0 else 0.5))
        elif name == "Trap Drums":
            ev += [(b, 0.5, KICK, 1.0), (b + qpb * 0.75, 0.5, KICK, 0.9)]
            if bar % 2 == 1:
                ev.append((b + qpb * 0.125, 0.25, KICK, 0.8))
            ev.append((b + qpb * 0.5, 0.5, CLAP, 0.95))  # halftime snare/clap on 3
            for e in range(qpb * 2):
                t = b + e / 2
                v = 0.8 if e % 2 == 0 else 0.5
                ev.append((_swing_at(t, groove), 0.1, HH_CLOSED, v))
            if bar % 4 == 3:
                for k in range(6):
                    ev.append((b + qpb * 0.75 + k / 6, 0.05, HH_OPEN, 0.7))  # hat roll
            if bar % 4 == 0:
                ev.append((b, 1.0, CRASH, 0.8))
        elif name == "Brush Kit":
            ev += [(b, 0.5, KICK, 0.8), (b + qpb * 0.75, 0.5, KICK, 0.7)]
            ev += [(b + qpb * 0.25, 0.4, SNARE, 0.35), (b + qpb * 0.75, 0.4, SNARE, 0.3)]
            for e in range(qpb * 2):
                ev.append((_swing_at(b + e / 2, groove), 0.3, RIDE, 0.5))
        else:  # Acoustic Drum Kit
            ev += [(b, 0.5, KICK, 1.0), (b + qpb * 0.5, 0.5, KICK, 0.8)]
            ev += [(b + qpb * 0.25, 0.5, SNARE, 0.9), (b + qpb * 0.75, 0.5, SNARE, 0.9)]
            for e in range(qpb * 2):
                ev.append((_swing_at(b + e / 2, groove), 0.1, HH_CLOSED, 0.5 if e % 2 else 0.7))
    return ev


def pat_bass(spec):
    name, groove = spec["_inst"], spec["groove"]
    qpb, bars, seq = spec["_qpb"], spec["bars"], spec["_chords"]
    ev = []
    for bar in range(bars):
        oct_down = 24 if name == "808 Sub Bass" else 12
        root = seq[bar][0] - oct_down
        fifth = seq[bar][2] - oct_down
        b = bar * qpb
        if name == "808 Sub Bass":
            ev += [(b, qpb * 0.9, root, 1.0)]
            if bar % 4 == 3:
                ev.append((b + qpb * 0.75, qpb * 0.25, fifth, 0.9))
        elif name == "Acoustic Upright Bass":
            if groove in ("swing", "bossa clave"):
                beats = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5] if groove == "swing" else [0, 0.75, 1.5, 2.25, 3, 3.75]
                for i, t in enumerate(beats):
                    ev.append((_swing_at(b + t, groove), 0.45,
                               root if i % 2 == 0 else fifth, 0.9))
            else:
                ev += [(b, 0.9, root, 0.9), (b + qpb * 0.75, 0.7, fifth, 0.8)]
        else:  # Electric Bass
            if groove == "one drop" and qpb >= 4:  # reggae walk around the accent
                ev += [(b + 1.5, 0.7, root, 0.95), (b + 3.5, 0.6, fifth, 0.85)]
            elif groove == "boom bap":  # root on the one, pickup into the bar
                ev += [(b, 0.8, root, 0.95), (b + qpb * 0.875, 0.35, fifth, 0.8)]
            else:
                for e in range(qpb * 2):
                    ev.append((_swing_at(b + e / 2, groove), 0.4,
                               root if e % 4 == 0 else fifth, 0.9))
    return ev


def pat_comp(spec):
    groove, qpb, bars, seq = spec["groove"], spec["_qpb"], spec["bars"], spec["_chords"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        chord = [n + 12 for n in seq[bar]]  # up an octave
        if groove == "bossa clave":
            hits = [0.0, 1.5, 2.0, 3.5]
        elif groove == "swing":
            hits = [0.0, 1.5, 3.5] if bar % 2 else [0.0, 2.5]
        elif groove == "halftime":
            hits = [0.0, 2.0]
        elif groove == "one drop":  # skank on the 'and' of every beat
            hits = [0.5, 1.5, 2.5, 3.5]
        elif groove == "boom bap":  # sparse stabs around the pocket
            hits = [0.0, 1.5, 3.0]
        else:
            hits = [0.0, 2.0]
        for h in [h for h in hits if h < qpb]:
            for n in chord:
                ev.append((_swing_at(b + h, groove), 0.35, n, 0.8))
    return ev


def pat_pad(spec):
    qpb, bars, seq = spec["_qpb"], spec["bars"], spec["_chords"]
    ev = []
    for bar in range(bars):
        for n in seq[bar]:
            ev.append((bar * qpb, qpb * 0.98, n, 0.7))
    return ev


def pat_brass(spec):
    qpb, bars, seq = spec["_qpb"], spec["bars"], spec["_chords"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        for n in seq[bar][1:]:
            ev.append((b, 0.45, n, 0.85))
        if bar % 2 == 1:
            for n in seq[bar][1:3]:
                ev.append((b + qpb * 0.5, 0.45, n, 0.7))
    return ev


def pat_melody(spec):
    rng = spec["_rng"]
    groove, qpb, bars = spec["groove"], spec["_qpb"], spec["bars"]
    pc, scale = ROOT_PCS[spec["root"]], SCALES[spec["mode"]]
    motif = [(0.0, 1.0, rng.randint(0, 6)), (1.0, 0.5, rng.randint(0, 6)),
             (1.5, 0.5, rng.randint(0, 6)), (2.5, 1.5, rng.randint(0, 6))]
    ev = []
    for phrase_bar in (0, 4):
        b = phrase_bar * qpb
        for m in motif:
            if rng.random() < 0.85:
                deg = m[2] + rng.choice([-1, 0, 0, 1])
                ev.append((_swing_at(b + m[0], groove), m[1], degree_midi(deg, pc, scale, 4), 0.85))
    return ev


def pat_arp(spec):
    qpb, bars, seq = spec["_qpb"], spec["bars"], spec["_chords"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        notes = seq[bar] + [seq[bar][0] + 12]
        for s in range(qpb * 4):
            ev.append((_swing_at(b + s / 4, spec["groove"]), 0.22, notes[s % len(notes)] + 12, 0.7))
    return ev


def pat_perc(spec):
    name, qpb, bars = spec["_inst"], spec["_qpb"], spec["bars"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        if name == "Shaker and Percussion":
            for s in range(qpb * 4):
                ev.append((_swing_at(b + s / 4, spec["groove"]), 0.1, SHAKER,
                            0.9 if s % 4 == 0 else 0.55))
        else:  # Congas and Bongo
            tumbao = [(0.0, CONGA_LOW, 0.9), (0.75, CONGA_MID, 0.7),
                      (1.5, CONGA_HI, 0.8), (2.25, CONGA_MID, 0.6), (3.0, CONGA_LOW, 0.7)]
            for t, note, v in tumbao:
                ev.append((_swing_at(b + t, spec["groove"]), 0.15, note, v))
    return ev


def pat_turntable(spec):
    qpb, bars = spec["_qpb"], spec["bars"]
    ev = []
    for bar in range(bars):
        b = bar * qpb
        if bar % 4 == 3:
            ev.append((b + qpb * 0.5, qpb * 0.5, 27, 0.9))   # sweep: GM high-Q chirp
            ev.append((b + qpb * 0.75, 0.25, CLAP, 0.8))
        if bar % 2 == 1:
            ev.append((b, qpb * 0.98, None, 0.5))            # vinyl bed: audio only
    return ev


INSTRUMENT_BEHAVIOUR = {
    "Brush Kit": ("drums", pat_drums),
    "Acoustic Drum Kit": ("drums", pat_drums),
    "Trap Drums": ("drums", pat_drums),
    "Acoustic Upright Bass": ("bass", pat_bass),
    "Electric Bass": ("bass", pat_bass),
    "808 Sub Bass": ("bass", pat_bass),
    "Nylon Guitar": ("comp", pat_comp),
    "Clean Electric Guitar": ("comp", pat_comp),
    "Rhodes Piano": ("comp", pat_comp),
    "Grand Piano": ("comp", pat_comp),
    "Jazz Organ": ("comp", pat_comp),
    "Analog Pad": ("pad", pat_pad),
    "Strings": ("pad", pat_pad),
    "Brass Section": ("brass", pat_brass),
    "Flute": ("melody", pat_melody),
    "Saxophone": ("melody", pat_melody),
    "Vibraphone": ("melody", pat_melody),
    "Vocal Guide": ("melody", pat_melody),
    "Synth Lead": ("melody", pat_melody),
    "Arp Synth": ("arp", pat_arp),
    "Shaker and Percussion": ("perc", pat_perc),
    "Congas and Bongo": ("perc", pat_perc),
    "Turntable FX": ("perc", pat_turntable),
    "Retro Drum Machine": ("drums", pat_drums),
    "Dusty Rhodes": ("comp", pat_comp),
    "Overdriven Guitar": ("comp", pat_comp),
    "Kalimba": ("melody", pat_melody),
}

VOICE_FOR = {
    "Nylon Guitar": lambda f, d, v: v_pluck(f, min(d, 1.2), v),
    "Clean Electric Guitar": lambda f, d, v: v_pluck(f, min(d, 1.2), v),
    "Acoustic Upright Bass": lambda f, d, v: v_bass(f, d, v, upright=True),
    "Electric Bass": lambda f, d, v: v_bass(f, d, v),
    "808 Sub Bass": v_sub,
    "Rhodes Piano": v_epiano,
    "Grand Piano": v_epiano,
    "Jazz Organ": v_organ,
    "Analog Pad": v_pad,
    "Strings": lambda f, d, v: v_pad(f, d, v, strings=True),
    "Brass Section": v_brass,
    "Flute": v_flute,
    "Saxophone": v_sax,
    "Vibraphone": v_vibe,
    "Vocal Guide": v_vocal,
    "Synth Lead": v_lead,
    "Arp Synth": v_lead,
    "Dusty Rhodes": v_dusty,
    "Overdriven Guitar": v_dist_gtr,
    "Kalimba": v_kalimba,
}

DRUM_VOICES = {
    KICK: lambda v: v_kick(50, 0.5, v, style="punch"),
    SNARE: lambda v: v_snare(185, 0.3, v, brush=False),
    CLAP: lambda v: v_clap(0, 0.15, v),
    HH_CLOSED: lambda v: v_hat(0, 0.05, v),
    HH_OPEN: lambda v: v_hat(0, 0.09, v, open_=True),
    RIDE: lambda v: v_ride(0, 0.35, v),
    CRASH: lambda v: v_ride(0, 0.4, v * 1.3),
    SHAKER: lambda v: v_shaker(0, 0.05, v),
    CONGA_HI: lambda v: v_conga(300, 0.2, v),
    CONGA_MID: lambda v: v_conga(230, 0.2, v),
    CONGA_LOW: lambda v: v_conga(160, 0.25, v),
}


# ------------------------------------------------------------------- midi --
def _vlq(n):
    out = bytearray([n & 0x7F])
    n >>= 7
    while n:
        out.insert(0, 0x80 | (n & 0x7F))
        n >>= 7
    return bytes(out)


def write_midi(path, spec, events, channel):
    tempo = int(60000000 / spec["bpm"])
    meta = bytearray()
    meta += b"\x00\xff\x51\x03" + struct.pack(">I", tempo)[1:]
    meta += b"\x00\xff\x58\x04" + bytes([spec["num"], int(math.log2(spec["den"])), 24, 8])
    meta += b"\x00\xff\x03" + _vlq(len(spec["_inst"])) + spec["_inst"].encode()
    program = GM_PROGRAMS[spec["_inst"]][0]
    meta += b"\x00" + bytes([0xC0 | channel, program])
    notes = []
    for t, d, note, vel in events:
        if note is None:
            continue
        notes.append((int(t * PPQ), 0x90 | channel, note, max(1, int(vel * 110))))
        notes.append((int((t + d) * PPQ), 0x80 | channel, note, 0))
    notes.sort(key=lambda x: (x[0], x[1]))
    data = bytearray(meta)
    last = 0
    for tick, status, note, vel in notes:
        data += _vlq(tick - last) + bytes([status, note, vel])
        last = tick
    data += b"\x00\xff\x2f\x00"
    header = struct.pack(">HHH", 1, 0, PPQ)  # format 0, 1 track
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">I", 6) + header)
        f.write(b"MTrk" + struct.pack(">I", len(data)) + bytes(data))


# ------------------------------------------------------------------ render --
def _write_wav(path, buf):
    peak = max(1e-9, max(abs(s) for s in buf) if buf else 1.0)
    scale = 0.55 / peak
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s * scale)) * 32767)) for s in buf))


def render(spec, out_dir):
    """Render every chosen instrument; returns results per instrument."""
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(zlib.crc32(spec["idea"].encode()) & 0xFFFFFFFF)
    spec = dict(spec)
    spec["_qpb"] = spec["num"] * 4 // spec["den"]
    spec["_chords"] = chord_sequence(spec)
    quarter = 60.0 / spec["bpm"]
    total = spec["bars"] * spec["_qpb"] * quarter
    nsamp = int(total * SR)
    results = []
    for idx, name in enumerate(spec["instruments"]):
        if name not in INSTRUMENT_BEHAVIOUR:
            continue
        kind, pattern = INSTRUMENT_BEHAVIOUR[name]
        spec["_inst"] = name
        seed = zlib.crc32((spec["idea"] + "|" + name).encode()) & 0xFFFFFFFF
        spec["_rng"] = random.Random(seed)
        random.seed(seed)  # voices use the global RNG; seed per instrument
        events = pattern(spec)
        buf = [0.0] * nsamp
        program, is_drum = GM_PROGRAMS[name]
        channel = 9 if is_drum else (idx % 8) + 1
        for t, d, note, vel in events:
            start = int(t * quarter * SR)
            if is_drum:
                voice = DRUM_VOICES.get(note)
                if voice:
                    _mix(buf, start, voice(vel))
                else:  # turntable sweeps / vinyl beds
                    freq = 55 if note else 0
                    _mix(buf, start, v_vinyl(freq, max(d * quarter, 0.1), vel))
            else:
                hz = midi_to_hz(note)
                _mix(buf, start, VOICE_FOR[name](hz, max(d * quarter, 0.05), vel))
        wav = os.path.join(out_dir, name.replace(" ", "_") + ".wav")
        mid = os.path.join(out_dir, name.replace(" ", "_") + ".mid")
        _write_wav(wav, buf)
        write_midi(mid, spec, events, channel)
        results.append({"name": name, "wav": wav, "midi": mid,
                        "seconds": round(total, 3), "kind": kind})
    return results