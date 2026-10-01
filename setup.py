"""CLI: English idea -> Laya decisions -> rendered audio -> REAPER project.

    .venv/bin/python setup.py "bossa nova project for a rainy afternoon" [--play] [--max-instruments N]

The interactive prompt loop (tui.py) exposes the same pipeline:
`.venv/bin/python tui.py`
"""
import argparse
import json
import os
import sys

import pipeline

LOG_PATH = os.path.join(pipeline.ROOT, "decisions.log")


def main():
    ap = argparse.ArgumentParser(description="Laya-driven REAPER project setup")
    ap.add_argument("idea", help='English project idea, e.g. "bossa nova for a rainy afternoon"')
    ap.add_argument("--model", default=None, help="laya checkpoint override (e.g. 'typed')")
    ap.add_argument("--bars", type=int, default=8, help="project length in bars")
    ap.add_argument("--min-instruments", type=int, default=2)
    ap.add_argument("--max-instruments", type=int, default=None, help="cap on routed tracks; top scores win (default: all above threshold)")
    ap.add_argument("--render-only", action="store_true", help="generate audio + MIDI, skip REAPER")
    ap.add_argument("--play", action="store_true", help="start transport after building")
    args = ap.parse_args()

    print("loading laya ...", flush=True)
    decisions = pipeline.decide(args.idea, model=args.model,
                               min_instruments=args.min_instruments, bars=args.bars,
                               max_instruments=args.max_instruments)
    print(f"decisions in {decisions['latency_s']}s via checkpoint={decisions['checkpoint']}")
    print(f"  tempo      : {decisions['bpm']} BPM  (band {decisions['bpm_band']})")
    print(f"  key        : {decisions['key_label']}")
    print(f"  time sig   : {decisions['time_sig']}")
    print(f"  groove     : {decisions['groove']}")
    print(f"  instruments: {', '.join(n for n, _ in decisions['instruments'])}")
    if decisions["declined"]:
        print("  declined   : " + ", ".join(f"{n} {p:.2f}" for n, p in decisions["declined"][:5]))

    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(decisions) + "\n")

    print("rendering audio + MIDI ...", flush=True)
    info = pipeline.render(decisions)
    for r in info["files"]:
        print(f"  {r['name']:26s} {r['seconds']}s {os.path.basename(r['wav'])}")

    if args.render_only:
        return
    print("building REAPER project ...", flush=True)
    st = pipeline.build_in_reaper(decisions, info)
    if "error" in st:
        sys.exit(st["error"])
    print(f"REAPER ready: {len(st['tracks'])} tracks @ {st['bpm']:.1f} BPM, "
          f"key marker '{decisions['key_label']}', items with WAV + MIDI takes")
    if args.play:
        pipeline.send_command({"cmd": "play"})
        print("transport started")


if __name__ == "__main__":
    main()