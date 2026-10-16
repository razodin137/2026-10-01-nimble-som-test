# nimble-som-test — Laya → REAPER music pipeline

Prompt with an English music idea — "gritty 90s boom bap for a late-night
basement cipher" — and get a routed, rendered, multi-track REAPER project:
Laya decides tempo, key, time signature, groove and the instrument palette;
the engine renders per-instrument WAV + MIDI stems; `bridge.lua` builds the
REAPER project and drives transport.

## Layout

    engine.py     stdlib synth: decisions -> per-instrument WAV + MIDI
    pipeline.py   Laya routing, render memoization, REAPER bridge client
    setup.py      one-shot CLI: idea -> decisions -> render -> REAPER
    tui.py        interactive prompt loop (idea, bars, max tracks, autoplay)
    bridge.lua    REAPER ReaScript; file IPC: run/state.json (~30 Hz heartbeat),
                  run/cmd/%08d.json commands (build/play/stop/seek/mute...)
    start.sh      launch REAPER with the bridge attached
    stems.py      standalone 4-bar demo stem generator (stdlib only, no deps)
    smoke_laya.py router smoke test (no REAPER needed)
    decisions.log one JSON line per routing run
    render/       generated stems (gitignored, .gitkeep keeps the folder)
    run/          bridge state/commands/logs (gitignored; start.sh recreates it)

## Prerequisites

- **Python 3.14**
- **REAPER for Linux** — expected at
  `/home/mrjohn/Applications/reaper781_linux_x86_64/reaper_linux_x86_64/REAPER/reaper`,
  or override with `REAPER_BIN=/path/to/reaper`
- **JACK** (on modern distros: `pipewire-jack`)
- A clean clone ships **no `.venv`** (it's gitignored) — create one below.
  The system `python` does not have `laya` installed, so always use
  `.venv/bin/python`.

## Setup

    python -m venv .venv
    .venv/bin/pip install -r requirements.txt

The first routing run downloads the Laya checkpoint from Hugging Face
(`convaiinnovations/laya`) into `~/.cache/huggingface`.

## Run

    ./start.sh --daemon          # REAPER + bridge, detached; do this first
    .venv/bin/python tui.py      # interactive: type genre ideas, repeat

One-shot:

    .venv/bin/python setup.py "spooky ghost music for a spooky party" --play

Routing + rendering without REAPER: append `--render-only` (needs no JACK).
Every instrument scoring >= 0.6 confidence is routed by default; cap with
`--max-instruments N` (or the TUI's "Max tracks" prompt) and cut down by
hand in REAPER.

## Troubleshooting

- `ModuleNotFoundError: No module named 'laya'` — you ran system `python`;
  use `.venv/bin/python` (see Setup).
- `bridge not reachable - start REAPER with start.sh` — REAPER isn't running
  with the bridge attached. `state.json` must be ticking (~fresh, a stale
  file doesn't count). Run `./start.sh --daemon` first.
- A second REAPER launch gets forwarded into the running instance and kills
  the bridge; `start.sh` refuses to launch when one is already up.
- `./start.sh` in the foreground exits REAPER on Ctrl-C. `--daemon`
  (`setsid`-detached) survives closing the terminal.
- First `decide()` call takes ~50 s (checkpoint load + routing). Rendering
  is pure stdlib and takes seconds per stem.