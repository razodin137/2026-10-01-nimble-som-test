--[[
  bridge.lua - REAPER ReaScript (embedded Lua 5.4, zero dependencies)

  File-based IPC with the Laya conductor daemon:
    run/state.json    bridge -> conductor, rewritten atomically every tick (~30 Hz)
    run/cmd/%08d.json  conductor -> bridge, one command per file, consumed in order

  Commands (single JSON object per file):
    build      {spec = {bpm, timesig_num, timesig_den, key_label, regions?, tracks=[...]}}
               spec.tracks entries:
                 {name, gain, muted, r, g, b}
                 + file      -> PCM-source media items tiled across spec.regions
                 + midi_bars -> empty MIDI item (0..midi_bars*bar_sec) to write into
    play       {}
    stop       {}
    seek       {pos = seconds}
    seek_at    {at = seconds, pos = seconds}   scheduled on the playback clock
    mute       {track = name, muted = bool}
    mute_at    {at = seconds, track = name, muted = bool}
    clear_sched {}                              drop all scheduled commands
]]--
-- Root resolution: prefer the LAYA_BRIDGE_ROOT env var set by the launcher,
-- else the run/root.txt file relative to REAPER's working directory.
local function read_root()
  local env_root = os.getenv("LAYA_BRIDGE_ROOT")
  if env_root and #env_root > 0 then return env_root end
  local f = io.open("run/root.txt", "r")
  if f then
    local s = f:read("*a"):gsub("%s+", "")
    f:close()
    if #s > 0 then return s end
  end
  return "/tmp/laya-reaper-run"
end

local ROOT = read_root()
local RUN_DIR = ROOT .. "/run"
local STATE_PATH = RUN_DIR .. "/state.json"
local CMD_DIR = RUN_DIR .. "/cmd"
local LOG_PATH = RUN_DIR .. "/bridge.log"

-- Append diagnostics where the Python side can read them.
local function logf(fmt, ...)
  local f = io.open(LOG_PATH, "a")
  if f then
    f:write(string.format("[%s] " .. fmt .. "\n", os.date("%H:%M:%S"), ...))
    f:close()
  end
end

local VERSION = "bridge-1"
local STARTED_AT = os.time()
local built = false
local last_cmd = 0
local sched = {}   -- {at, kind, track, pos, muted}
local regions = {} -- {name, start, end_}
local tracks = {}  -- {name, tr}

-- ---------- tiny JSON parser (objects/arrays/strings/numbers/bools/null) ----------
local json = {}
local function fail(s) error("json: " .. s) end

local function skip(s, i)
  while i <= #s and s:sub(i, i):match("%s") do i = i + 1 end
  return i
end

function json.parse(s)
  local i = 1
  local function value()
    local c = s:sub(i, i)
    if c == "{" then
      i = i + 1
      local o = {}
      i = skip(s, i)
      if s:sub(i, i) == "}" then i = i + 1 return o end
      while true do
        i = skip(s, i)
        local k = value()
        i = skip(s, i)
        assert(s:sub(i, i) == ":", "expected :") i = i + 1
        i = skip(s, i)
        o[k] = value()
        i = skip(s, i)
        local c2 = s:sub(i, i)
        assert(c2 == "," or c2 == "}", "expected , or }") i = i + 1
        if c2 == "}" then return o end
      end
    elseif c == "[" then
      i = i + 1
      local a = {}
      i = skip(s, i)
      if s:sub(i, i) == "]" then i = i + 1 return a end
      while true do
        i = skip(s, i)
        a[#a + 1] = value()
        i = skip(s, i)
        local c2 = s:sub(i, i)
        assert(c2 == "," or c2 == "]", "expected , or ]") i = i + 1
        if c2 == "]" then return a end
      end
    elseif c == '"' then
      i = i + 1
      local out = {}
      while true do
        local c2 = s:sub(i, i)
        if c2 == "" then fail("unterminated string") end
        if c2 == '"' then i = i + 1 break end
        if c2 == "\\" then
          local e = s:sub(i + 1, i + 1)
          out[#out + 1] = e == "n" and "\n" or e == "t" and "\t" or e
          i = i + 2
        else
          out[#out + 1] = c2
          i = i + 1
        end
      end
      return table.concat(out)
    elseif s:sub(i, i + 3) == "true" then i = i + 4 return true
    elseif s:sub(i, i + 4) == "false" then i = i + 5 return false
    elseif s:sub(i, i + 3) == "null" then i = i + 4 return nil
    else
      local j = s:find("[,}%]%s]", i) or (#s + 1)
      local num = tonumber(s:sub(i, j - 1))
      assert(num, "bad number at " .. i)
      i = j
      return num
    end
  end
  i = skip(s, i)
  local v = value()
  return v
end

-- ---------- helpers ----------
local function jstr(x)
  if type(x) == "string" then
    return '"' .. x:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n') .. '"'
  elseif type(x) == "number" then
    return string.format("%.6f", x)
  elseif type(x) == "boolean" then
    return tostring(x)
  end
  return "null"
end

local function atomic_write(path, content)
  local tmp = path .. ".tmp"
  local f = io.open(tmp, "w")
  if not f then return false end
  f:write(content)
  f:close()
  os.remove(path)
  os.rename(tmp, path)
  return true
end

local function now()
  local playing = (reaper.GetPlayState() & 1) == 1
  if playing then return reaper.GetPlayPosition(), playing end
  return reaper.GetCursorPosition(), playing
end

local function track_by_name(name)
  for _, t in ipairs(tracks) do
    if t.name == name then return t.tr end
  end
  return nil
end

-- ---------- command execution ----------
local function do_mute(name, muted)
  local tr = track_by_name(name)
  if tr then reaper.SetMediaTrackInfo_Value(tr, "B_MUTE", muted and 1 or 0) end
end

local function exec(cmd)
  local kind = cmd.cmd
  if kind == "build" then
    return do_build(cmd.spec)
  elseif kind == "play" then
    reaper.OnPlayButton()
  elseif kind == "stop" then
    reaper.OnStopButton()
  elseif kind == "seek" then
    reaper.SetEditCurPos(cmd.pos, 0, 1)
  elseif kind == "seek_at" then
    sched[#sched + 1] = { at = cmd.at, kind = "seek", pos = cmd.pos }
  elseif kind == "mute" then
    do_mute(cmd.track, cmd.muted)
  elseif kind == "mute_at" then
    sched[#sched + 1] = { at = cmd.at, kind = "mute", track = cmd.track, muted = cmd.muted }
  elseif kind == "clear_sched" then
    sched = {}
  elseif kind == "ping" then
    -- handled implicitly by consumed counter
  end
end

-- ---------- project build ----------

-- One media item: WAV take active, matching MIDI file as an alternate take.
local function add_media_item(tr, t, start, length)
  local item = reaper.AddMediaItemToTrack(tr)
  reaper.SetMediaItemInfo_Value(item, "D_POSITION", start)
  reaper.SetMediaItemInfo_Value(item, "D_LENGTH", length)
  local wav_take = reaper.AddTakeToMediaItem(item)
  reaper.SetMediaItemTake_Source(wav_take, reaper.PCM_Source_CreateFromFile(t.file))
  reaper.GetSetMediaItemTakeInfo_String(wav_take, "P_NAME", t.name, 1)
  if t.midi_file then
    local mid_take = reaper.AddTakeToMediaItem(item)
    reaper.SetMediaItemTake_Source(mid_take, reaper.PCM_Source_CreateFromFile(t.midi_file))
    reaper.GetSetMediaItemTakeInfo_String(mid_take, "P_NAME", t.name .. " (midi)", 1)
  end
  reaper.SetActiveTake(wav_take)
  return item
end
function do_build(spec)
  -- wipe existing tracks + markers (build is idempotent)
  while reaper.CountTracks(0) > 0 do
    reaper.DeleteTrack(reaper.GetTrack(0, 0))
  end
  local n = 0
  while true do
    local count = reaper.CountProjectMarkers(0)
    if not count or count == 0 then break end
    reaper.DeleteProjectMarkerByIndex(0, 0)
    n = n + 1
    if n > 200 then break end
  end

  -- tempo + time signature at position 0
  local num = spec.timesig_num or 4
  local den = spec.timesig_den or 4
  reaper.SetTempoTimeSigMarker(0, -1, 0.0, -1, -1, spec.bpm, num, den, false)
  reaper.UpdateTimeline()
  if spec.key_label then
    reaper.AddProjectMarker(0, false, 0.0, 0.0, "Key: " .. spec.key_label, -1)
  end

  regions = {}
  tracks = {}
  local bar_sec = num * 60.0 / (spec.bpm * den) -- one bar in seconds
  local rgn_len = (spec.region_bars or 4) * bar_sec

  if spec.regions then
    for ri, r in ipairs(spec.regions) do
      local start = (ri - 1) * rgn_len
      reaper.AddProjectMarker4(0, true, start, start + rgn_len, r.name, -1,
                               reaper.ColorToNative(r.r or 128, r.g or 128, r.b or 128) | 0x1000000, 0)
      regions[#regions + 1] = { name = r.name, start = start, end_ = start + rgn_len }
    end
  end

  for _, t in ipairs(spec.tracks) do
    reaper.InsertTrackAtIndex(reaper.CountTracks(0), 0)
    local tr = reaper.GetTrack(0, reaper.CountTracks(0) - 1)
    reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", t.name, 1)
    reaper.SetMediaTrackInfo_Value(tr, "D_VOL", t.gain or 1.0)
    reaper.SetMediaTrackInfo_Value(tr, "I_CUSTOMCOLOR", reaper.ColorToNative(t.r or 160, t.g or 160, t.b or 160) | 0x1000000)
    if t.muted then reaper.SetMediaTrackInfo_Value(tr, "B_MUTE", 1) end
    if t.file then
      if #regions > 0 then
        for _, r in ipairs(regions) do
          add_media_item(tr, t, r.start, r.end_ - r.start)
        end
      else
        add_media_item(tr, t, 0.0, spec.total_seconds or 8 * bar_sec)
      end
    elseif t.midi_bars and t.midi_bars > 0 then
      reaper.SetOnlyTrackSelected(tr)
      reaper.CreateNewMIDIItemInProj(0.0, t.midi_bars * bar_sec, 0)
    end
    tracks[#tracks + 1] = { name = t.name, tr = tr }
  end

  for i = 0, reaper.CountTracks(0) - 1 do reaper.SetTrackSelected(reaper.GetTrack(0, i), false) end
  built = true
  return true
end

-- ---------- command spool ----------
local function poll_commands(pos, playing)
  while true do
    local path = string.format("%s/%08d.json", CMD_DIR, last_cmd + 1)
    local f = io.open(path, "r")
    if not f then break end
    local body = f:read("*a")
    f:close()
    local ok, cmd = pcall(json.parse, body)
    if not ok then
      logf("bad json in %s: %s", path, tostring(cmd))
    elseif type(cmd) ~= "table" then
      logf("non-table json in %s", path)
    else
      local ok2, err = pcall(exec, cmd)
      if ok2 then
        logf("ok   %s", tostring(cmd.cmd))
      else
        logf("FAIL %s: %s", tostring(cmd.cmd), tostring(err))
        reaper.ShowConsoleMsg("bridge: exec failed: " .. tostring(err) .. "\n")
      end
    end
    os.remove(path)
    last_cmd = last_cmd + 1
  end
end

local function run_sched(pos, playing)
  if not playing then return end
  local keep = {}
  for _, s in ipairs(sched) do
    if pos >= s.at then
      if s.kind == "seek" then
        reaper.SetEditCurPos(s.pos, 0, 1)
      elseif s.kind == "mute" then
        do_mute(s.track, s.muted)
      end
    else
      keep[#keep + 1] = s
    end
  end
  sched = keep
end

-- ---------- state ----------
local function current_region(pos)
  for i, r in ipairs(regions) do
    if pos >= r.start and pos < r.end_ then return i end
  end
  if #regions > 0 and pos >= regions[#regions].end_ then return #regions end
  return nil
end

local function write_state(pos, playing)
  local ridx = current_region(pos)
  local parts = {}
  parts[#parts + 1] = string.format('"bridge":"%s","pid":%d,"built":%s,"consumed":%d,',
    VERSION, STARTED_AT, tostring(built), last_cmd)
  parts[#parts + 1] = string.format('"playing":%s,"pos":%.6f,"bpm":%.3f,',
    tostring(playing), pos, reaper.Master_GetTempo())
  parts[#parts + 1] = string.format('"region":%d,"sched":%d,', ridx or -1, #sched)
  parts[#parts + 1] = '"regions":['
  for i, r in ipairs(regions) do
    if i > 1 then parts[#parts + 1] = "," end
    parts[#parts + 1] = string.format('{"name":"%s","start":%.6f,"end":%.6f}', r.name, r.start, r.end_)
  end
  parts[#parts + 1] = '],"tracks":['
  for i, t in ipairs(tracks) do
    if i > 1 then parts[#parts + 1] = "," end
    parts[#parts + 1] = string.format('{"name":"%s","muted":%s}', t.name,
      tostring(reaper.GetMediaTrackInfo_Value(t.tr, "B_MUTE") ~= 0))
  end
  parts[#parts + 1] = "]}"
  atomic_write(STATE_PATH, "{" .. table.concat(parts))
end

-- ---------- main loop ----------
local function tick()
  local pos, playing = now()
  poll_commands(pos, playing)
  run_sched(pos, playing)
  write_state(pos, playing)
  reaper.defer(tick)
end

os.execute("mkdir -p " .. CMD_DIR:gsub(" ", "\\ "))
logf("bridge.lua up (v%s), root=%s", VERSION, ROOT)
reaper.ShowConsoleMsg("bridge.lua up, root=" .. ROOT .. "\n")
tick()