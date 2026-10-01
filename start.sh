#!/bin/sh
# Launch REAPER with the Laya bridge script attached.
#   ./start.sh              # foreground (Ctrl-C exits REAPER)
#   ./start.sh --daemon     # background, detached from this terminal, logs to run/reaper.log
REAPER=${REAPER_BIN:-/home/mrjohn/Applications/reaper781_linux_x86_64/reaper_linux_x86_64/REAPER/reaper}
ROOT="$(cd "$(dirname "$0")" && pwd)"

if ! [ -x "$REAPER" ]; then
  echo "start.sh: REAPER binary not found at $REAPER" >&2
  echo "  set REAPER_BIN=/path/to/reaper and retry" >&2
  exit 1
fi

if pgrep -x reaper >/dev/null 2>&1; then
  echo "start.sh: REAPER already running (pid $(pgrep -x reaper | head -1)); not launching another" >&2
  echo "  a second launch gets forwarded into the running instance and kills the bridge" >&2
  exit 1
fi

mkdir -p "$ROOT/run/cmd"
echo -n "$ROOT" > "$ROOT/run/root.txt"

if [ "$1" = "--daemon" ]; then
  LAYA_BRIDGE_ROOT="$ROOT" setsid "$REAPER" -new "$ROOT/bridge.lua" >>"$ROOT/run/reaper.log" 2>&1 &
  echo "REAPER started (pid $!), bridge root=$ROOT"
else
  exec env LAYA_BRIDGE_ROOT="$ROOT" "$REAPER" -new "$ROOT/bridge.lua"
fi