"""Smoke test: Laya typed decisions on CPU + Reaper state-shaped input."""
import json
import sys
import time

t0 = time.time()
import torch

print("python", sys.version.split()[0])
print("torch", torch.__version__, "| threads", torch.get_num_threads())

from laya import Router

print(f"imports: {time.time() - t0:.1f}s")

t0 = time.time()
router = Router()
print(f"Router(): {time.time() - t0:.1f}s")

state = (
    "Live electronic set. Currently playing region 'Verse 1', bar 12 of 16. "
    "Tempo 126 BPM. Tracks: Drums, Bass, Hats, Pad, Lead. "
    "Regions: Intro, Verse 1, Chorus, Break, Drop, Outro. "
    "Crowd energy is high; last section was sparse."
)
questions = {
    "next_section": {
        "type": "choice",
        "instructions": "Which region should play next to keep the set flowing?",
        "criteria": {
            "Intro": "beginning, sparse, long build-up",
            "Chorus": "full energy, all tracks playing",
            "Break": "stripped down, builds tension",
            "Drop": "maximum energy climax",
            "Outro": "winds the set down",
        },
    },
    "fire_fill": {
        "type": "noul",
        "instructions": "Should a drum fill trigger at the end of the current bar?",
    },
    "intensity": {
        "type": "score",
        "instructions": "How intense should the next section be?",
        "criteria": ["very low", "low", "medium", "high", "maximum"],
    },
}

for name, kwargs in (("auto", {}), ("typed", {"model": "typed"})):
    try:
        for i in range(2):
            t0 = time.time()
            r = router.predict(state, questions, **kwargs)
            print(f"predict[{name}] #{i}: {time.time() - t0:.2f}s model={r['routing']['model']}")
            print(json.dumps(r["answers"], indent=1))
    except Exception as e:
        print(f"predict[{name}] failed: {type(e).__name__}: {e}")