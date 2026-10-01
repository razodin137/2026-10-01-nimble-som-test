"""Shared Laya -> decisions -> engine -> REAPER pipeline.

Used by setup.py (one-shot CLI) and tui.py (interactive wizard).

Flow:
    idea (English) --Laya Router.predict--> typed decisions
    decisions ----engine.render------> per-instrument WAV + MIDI
    build spec ----bridge.lua--------> REAPER project (tempo, key, tracks, items)
"""
import hashlib
import json
import os
import time

import engine

ROOT = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(ROOT, "run")
CMD_DIR = os.path.join(RUN_DIR, "cmd")
STATE_PATH = os.path.join(RUN_DIR, "state.json")
RENDER_ROOT = os.path.join(ROOT, "render")

BPM_BANDS = {
    "60-80": "ballad, slow ambient, downtempo",
    "80-100": "slow groove, head-nod, boom bap, dub",
    "100-120": "moderate groove, neo-soul, chillhop",
    "120-140": "bossa nova, classic house, pop, upbeat jazz",
    "140-160": "driving disco, rock, trap and grime",
    "160-180": "fast drum and bass, punk, polka",
}

KEY_ROOTS = {
    "C": "neutral, open, easy on most instruments",
    "Db": "spicy, jazz and soul flavour",
    "D": "guitar-friendly, bright",
    "Eb": "warm jazz and brass favourite",
    "E": "bright, rock energy",
    "F": "warm, winds and keys friendly, bossa classic",
    "Gb": "darker, soulful",
    "G": "bright, folk and country standard",
    "Ab": "lush, cinematic",
    "A": "natural, piano-friendly",
    "Bb": "warm, horns and jazz standard",
    "B": "tense, brilliant",
}

MODES = {
    "major": "open, uplifting resolution",
    "minor": "melancholy, darker tension",
    "dorian": "jazzy minor with a raised 6th, sophisticated",
    "mixolydian": "dominant blues flavour, rock and funk",
    "harmonic minor": "dramatic raised-7th minor, flamenco, tango, dark jazz",
    "phrygian": "darkest spanish minor, brooding, flamenco and metal",
    "lydian": "floating bright major with a sharpened 4th, dreamy, cinematic",
}

TIME_SIGS = {
    "4/4": "standard, nearly all pop, jazz and hip-hop",
    "3/4": "jazz waltz, folk waltz",
    "5/4": "odd jazz meter, take-five swing, progressive",
    "6/8": "ballad shuffle, bluesy sway",
    "7/4": "art-rock odd-meter groove, unsettled flow",
    "12/8": "slow blues shuffle, gospel",
}

GROOVES = {
    "straight": "even 8ths and 16ths, pop, rock, hip-hop",
    "swing": "triplet-feel 8ths, jazz, blues",
    "bossa clave": "syncopated latin clave comp, bossa nova",
    "halftime": "heavy backbeat on 3, trap, modern R&B",
    "boom bap": "swung dusty 90s hip-hop pocket, kick-snare head-nod",
    "one drop": "reggae pocket, kick and stick on beat 3, offbeat skank",
}

# name -> (family, blurb, rgb)
INSTRUMENTS = {
    "Nylon Guitar": ("strings", "fingerstyle bossa and jazz staple", (194, 145, 90)),
    "Clean Electric Guitar": ("strings", "jazz chords, funk skank, indie arpeggios", (194, 145, 90)),
    "Overdriven Guitar": ("strings", "fuzzy saturated stabs, rock and hip-hop hooks", (194, 145, 90)),
    "Acoustic Upright Bass": ("bass", "jazz and bossa walking lines", (74, 127, 181)),
    "Electric Bass": ("bass", "pop, rock, funk, neo-soul", (74, 127, 181)),
    "808 Sub Bass": ("bass", "trap and modern hip-hop sub", (74, 127, 181)),
    "Rhodes Piano": ("keys", "warm electric piano, soul and jazz", (159, 134, 192)),
    "Dusty Rhodes": ("keys", "lo-fi tape-fluttered electric piano, hip hop and chill", (159, 134, 192)),
    "Grand Piano": ("keys", "straight-ahead jazz, classical, ballads", (159, 134, 192)),
    "Jazz Organ": ("keys", "soul jazz, gospel, groove", (159, 134, 192)),
    "Analog Pad": ("keys", "ambient texture, film scoring", (159, 134, 192)),
    "Brush Kit": ("drums", "soft jazz and bossa brushes", (192, 91, 91)),
    "Acoustic Drum Kit": ("drums", "rock, funk, pop backbeat", (192, 91, 91)),
    "Trap Drums": ("drums", "808 claps, sharp hats, hip-hop", (192, 91, 91)),
    "Retro Drum Machine": ("drums", "tight 80s electronic beat, synth-pop and hip hop", (192, 91, 91)),
    "Shaker and Percussion": ("percussion", "bossa, latin, acoustic texture", (192, 162, 74)),
    "Congas and Bongo": ("percussion", "latin, salsa, afro-cuban", (192, 162, 74)),
    "Strings": ("orchestral", "cinematic layers, ballad sweeps", (111, 159, 192)),
    "Brass Section": ("orchestral", "funk stabs, cinematic swells", (111, 159, 192)),
    "Flute": ("wind", "jazzy bossa melodies, latin", (143, 191, 143)),
    "Saxophone": ("wind", "soul and jazz lead lines", (143, 191, 143)),
    "Vibraphone": ("mallets", "cool jazz, lounge, dreamy", (127, 192, 155)),
    "Kalimba": ("mallets", "thumb piano sparkle, organic lo-fi melody", (127, 192, 155)),
    "Synth Lead": ("synth", "techno, house, modern toplines", (192, 91, 159)),
    "Arp Synth": ("synth", "electronic sequence sparkle", (192, 91, 159)),
    "Vocal Guide": ("vox", "scratch lead vocal for songwriting", (191, 111, 176)),
    "Turntable FX": ("electronic", "hip-hop cuts and scratch fills", (127, 127, 192)),
}

_router = None
_router_model = None


def get_router(model=None):
    global _router, _router_model
    if _router is None or (model and model != _router_model):
        from laya import Router

        _router = Router() if not model else Router(model=model)
        _router_model = model
    return _router


# ------------------------------------------------------------- Laya layer --
def build_questions():
    q = {
        "bpm_band": {"type": "choice", "instructions": "Which tempo band in BPM fits this project?",
                     "criteria": BPM_BANDS},
        "key_root": {"type": "choice", "instructions": "Which key root fits this project?",
                     "criteria": KEY_ROOTS},
        "mode": {"type": "choice", "instructions": "Which tonal mode fits this project?",
                 "criteria": MODES},
        "time_sig": {"type": "choice", "instructions": "Which time signature fits this project?",
                     "criteria": TIME_SIGS},
        "groove": {"type": "choice", "instructions": "Which rhythmic feel fits this project?",
                   "criteria": GROOVES},
    }
    for name, (_, blurb, _) in INSTRUMENTS.items():
        q[name] = {"type": "noul",
                   "instructions": (f"Include a '{name}' track ({blurb})? "
                                    "Answer yes only if it truly fits the described style.")}
    return q


def _predict(router, state_text, questions):
    try:
        return router.predict(state_text, questions)
    except Exception as e:
        print(f"single-pass predict failed ({e}); splitting into chunks", flush=True)
        result = None
        items = list(questions.items())
        for i in range(0, len(items), 12):
            part = router.predict(state_text, dict(items[i:i + 12]))
            if result is None:
                result = part
            else:
                result["answers"].update(part["answers"])
        return result


def decide(idea, model=None, min_instruments=2, bars=8, max_instruments=None):
    """idea -> decisions dict (no audio, no REAPER). Pure routing step."""
    router = get_router(model)
    state_text = (
        f"Project idea: {idea}\n"
        "This is a request to set up a music project: tempo, key, time signature, "
        "rhythmic feel and an instrument palette that suit the idea."
    )
    t0 = time.time()
    result = _predict(router, state_text, build_questions())
    latency = time.time() - t0
    answers = result["answers"]

    band = answers["bpm_band"]["choice"]
    lo, hi = (int(x) for x in band.split("-"))
    root = answers["key_root"]["choice"]
    mode = answers["mode"]["choice"]
    sig = answers["time_sig"]["choice"]
    num, den = (int(x) for x in sig.split("/"))
    groove = answers["groove"]["choice"]

    picked, declined = [], []
    for name in INSTRUMENTS:
        p = answers[name]["noul"]
        (picked if p >= 0.6 else declined).append((name, round(p, 3)))
    if len(picked) < min_instruments:
        for name, p in sorted(declined, key=lambda x: -x[1])[:min_instruments - len(picked)]:
            picked.append((name, p))
            declined.remove((name, p))
    if max_instruments and len(picked) > max_instruments:  # route only the strongest picks
        picked.sort(key=lambda x: -x[1])
        declined = picked[max_instruments:] + declined
        picked = picked[:max_instruments]

    return {
        "idea": idea,
        "checkpoint": result["routing"]["model"],
        "latency_s": round(latency, 2),
        "bpm": int(lo + 0.4 * (hi - lo)),
        "bpm_band": band,
        "key_label": f"{root} {mode}",
        "mode": mode,
        "num": num,
        "den": den,
        "time_sig": sig,
        "groove": groove,
        "instruments": picked,
        "declined": declined,
        "answers": answers,
        "bars": bars,
    }


# -------------------------------------------------------- synthesis layer --
def slug(idea):
    h = hashlib.sha1(idea.encode()).hexdigest()[:8]
    words = [w for w in "".join(c if c.isalnum() else " " for c in idea.lower()).split()][:4]
    return "-".join(words + [h]) if words else h


def render(decisions, force=False):
    """decisions -> engine.render -> per-instrument wav/midi; memoized per idea."""
    out_dir = os.path.join(RENDER_ROOT, slug(decisions["idea"]))
    names = [n for n, _ in decisions["instruments"]]
    marker = os.path.join(out_dir, "done.json")
    if not force and os.path.exists(marker):
        with open(marker) as f:
            cached = json.load(f)
        if cached.get("instruments") == names and cached.get("decisions") == _fingerprint(decisions):
            return cached
    spec = {
        "idea": decisions["idea"],
        "bpm": decisions["bpm"],
        "num": decisions["num"],
        "den": decisions["den"],
        "root": decisions["key_label"].split()[0],
        "mode": decisions.get("mode") or decisions["key_label"].split()[1],
        "groove": decisions["groove"],
        "bars": decisions["bars"],
        "instruments": names,
    }
    results = engine.render(spec, out_dir)
    info = {
        "instruments": names,
        "decisions": _fingerprint(decisions),
        "dir": out_dir,
        "files": [{"name": r["name"], "wav": r["wav"], "midi": r["midi"],
                    "seconds": r["seconds"]} for r in results],
    }
    with open(marker, "w") as f:
        json.dump(info, f)
    return info


def _fingerprint(d):
    core = {k: d[k] for k in ("bpm", "key_label", "num", "den", "groove", "bars", "instruments")}
    return json.dumps(core, sort_keys=True)


# --------------------------------------------------------- REAPER bridge --
def read_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def state_age():
    """Seconds since the bridge last rewrote state.json (~0.03s when healthy)."""
    try:
        return max(0.0, time.time() - os.path.getmtime(STATE_PATH))
    except OSError:
        return float("inf")


def bridge_ready(timeout=0.0, max_age=5.0):
    """Live bridge state, or None. A stale state.json lingers after REAPER
    dies, so mere presence of the file must not count as 'reachable'."""
    deadline = time.time() + timeout
    while True:
        st = read_state()
        if st and "bridge" in st and state_age() <= max_age:
            return st
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def send_command(payload, timeout=30):
    if bridge_ready(timeout=timeout) is None:
        raise RuntimeError("bridge not reachable - start REAPER with start.sh")
    os.makedirs(CMD_DIR, exist_ok=True)
    for _ in range(600):
        st = read_state()
        if st is None or state_age() > 5.0:
            raise RuntimeError("bridge stalled - state.json not updating (see run/bridge.log)")
        n = int(st.get("consumed", 0)) + 1
        path = os.path.join(CMD_DIR, f"{n:08d}.json")
        if os.path.exists(path):
            time.sleep(0.05)
            continue
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.rename(tmp, path)
        return n
    raise RuntimeError("no free command slot")


def build_in_reaper(decisions, render_info, wait=30):
    tracks = []
    for r in render_info["files"]:
        fam = INSTRUMENTS.get(r["name"])
        rgb = fam[2] if fam else (160, 160, 160)
        tracks.append({"name": r["name"], "file": r["wav"], "midi_file": r["midi"],
                       "r": rgb[0], "g": rgb[1], "b": rgb[2]})
    spec = {
        "bpm": decisions["bpm"],
        "timesig_num": decisions["num"],
        "timesig_den": decisions["den"],
        "key_label": decisions["key_label"],
        "total_seconds": render_info["files"][0]["seconds"] if render_info["files"] else 8 * 4 * 60 / decisions["bpm"],
        "tracks": tracks,
    }
    try:
        send_command({"cmd": "build", "spec": spec})
    except RuntimeError as e:
        return {"error": str(e)}
    deadline = time.time() + wait
    want = len(tracks)
    while time.time() < deadline:
        st = read_state()
        if st is None or state_age() > 5.0:
            return {"error": "bridge stalled during build - see run/bridge.log"}
        if st.get("built") and len(st.get("tracks", [])) == want \
                and abs(float(st.get("bpm", 0)) - decisions["bpm"]) < 0.9:
            return st
        time.sleep(0.2)
    return {"error": f"build not confirmed within {wait}s - see run/bridge.log"}