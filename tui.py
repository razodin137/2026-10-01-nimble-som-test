"""TUI wizard: prompt for an idea, route + render + build in REAPER, repeat.

    .venv/bin/python tui.py

Prompts: idea, bars, max tracks (all = uncapped), play-when-done. Blank idea quits.
"""
import json
import os

import pipeline

LOG_PATH = os.path.join(pipeline.ROOT, "decisions.log")


def _ask(prompt, default=""):
    tail = f" [{default}]" if default else ""
    val = input(f"{prompt}:{tail} ").strip()
    return val or default


def main():
    up = pipeline.bridge_ready() is not None
    print(f"Laya TUI - blank idea quits. REAPER bridge: {'up' if up else 'DOWN'}")
    while True:
        idea = _ask("\nIdea")
        if not idea:
            print("bye")
            return
        bars = int(_ask("Bars", "8"))
        cap_raw = _ask("Max tracks", "all")
        cap = None if cap_raw.lower() in ("", "all", "a") else int(cap_raw)
        play = _ask("Play when done?", "y").lower() in ("y", "yes")
        print("routing ...", flush=True)
        d = pipeline.decide(idea, bars=bars, max_instruments=cap)
        print(f"  {d['bpm']} BPM {d['key_label']} {d['time_sig']} {d['groove']}")
        print("  tracks : " + ", ".join(n for n, _ in d["instruments"]))
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(d) + "\n")
        print("rendering ...", flush=True)
        info = pipeline.render(d)
        print("  stems  : " + ", ".join(f"{r['name']} {r['seconds']}s" for r in info["files"]))
        st = pipeline.build_in_reaper(d, info)
        if "error" in st:
            print("  build failed:", st["error"])
            continue
        print(f"  built {len(st['tracks'])} tracks in REAPER @ {st['bpm']:.1f} BPM")
        if play:
            try:
                pipeline.send_command({"cmd": "play"})
                print("  playing")
            except RuntimeError as e:
                print("  play failed:", e)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nbye")