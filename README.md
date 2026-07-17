# Argus

*The hundred-eyed watchman — never all eyes closed at once.*

Local, private activity tracker for Linux/X11. Tracks keyboard/mouse activity,
camera-based wellbeing signals (MediaPipe FaceLandmarker), and per-app time;
reminds you to take breaks; and serves a live dashboard with focus/stress
scores and LLM-generated insights (local ollama).

**Everything stays on this machine.** Only *counts* of keystrokes are stored
(never which keys — except Backspace/Delete, tallied as a correction-rate
signal). Camera frames are processed in memory and discarded; only derived
numbers (blinks, tension, presence, head-pose flags) reach the database.

## Quick start

The app runs as a systemd user service (already installed):

```bash
systemctl --user status activity-tracker     # check it's running
```

Dashboard: **http://localhost:8787**

## Dashboard

- **Stat tiles** — active time, away time, focus %, stress index, blink rate,
  keystrokes, longest unbroken streak (with breaks taken vs reminders sent),
  eye rubs, chewing-gum time. Tile colors match the activity timeline.
- **Last 14 days** — bar per day (height = active time, color = stress level),
  blue line = focus %. Hover for details, click a day to inspect it. The
  ‹ › header buttons also step through days.
- **Activity timeline** — per-minute bars for the selected day: height = input
  intensity, green = present at screen (camera), amber = input without camera
  presence, blue = away from the computer, red top dots = facial-tension
  spikes, violet top dots = chewing gum. Scroll to zoom, drag to pan,
  double-click to reset, hover for per-minute details.
- **Analysis** — auto-generated observations, refreshed with the data
- **Top apps** — time per application (window class) for the day
- **Camera switch** (header, red = live) — toggles camera tracking AND
  releases `/dev/video0` while off. Webcams are exclusive on Linux, so flip
  it off before a video call (the call app can't open the camera otherwise);
  input/app tracking continues meanwhile. Flip back after the call — if the
  call app still holds the camera, the tracker retries every 10 s.
- **Settings** — break interval, snooze, camera on/off, notifications on/off;
  saved to `config.json` immediately, no restart needed
- **AI insights** — sends the selected day's stats to your local ollama model
  for coaching-style analysis; falls back to built-in heuristics if ollama is
  down. Nothing leaves the machine.

The whole page auto-refreshes every 20 s (and on tab focus); the green
"live · updated Ns ago" indicator in the header shows data freshness.

## Black hole

When an unbroken active streak runs past `break_every_min`, a black hole
opens in the middle of the main monitor and grows for the next
`blackhole_ramp_min` minutes (default 15), gravitationally lensing
everything on screen — the longer you refuse to take a break, the more of
your desktop it eats. Take a real break (idle past `idle_reset_min`) and it
collapses. Purely visual: input and windows are untouched.

Implementation: a KWin compositor effect (`blackhole/`) fed by the tracker
over DBus (`org.argus.blackhole`). Config: `blackhole_enabled`,
`blackhole_ramp_min` in `config.json`.

Build/install (once, after KWin upgrades rebuild the same way):

    sudo apt install -y cmake extra-cmake-modules g++ kwin-dev qtbase5-dev \
      libkf5config-dev libkf5coreaddons-dev libkf5windowsystem-dev \
      libxcb1-dev libepoxy-dev
    cd blackhole && mkdir -p build && cd build
    cmake -DCMAKE_INSTALL_PREFIX=/usr .. && make && sudo make install
    systemctl --user restart plasma-kwin_x11   # KWin holds the old .so — reconfigure alone won't pick up a rebuild

Instant test:

    dbus-send --session --type=method_call --dest=org.argus.blackhole \
      /BlackHole org.argus.blackhole.setStrength double:0.7

## Break reminders

After `break_every_min` (default 50) consecutive active minutes you get a
desktop notification, repeated every `snooze_min` (default 5) minutes until
you actually step away. Being idle/absent for `idle_reset_min` (default 3)
minutes counts as a break taken. "Active" = any input this minute, or face
present on camera.

## Signals collected (per minute, `activity.db`)

| Signal | Source | Used for |
|---|---|---|
| keys, clicks, scrolls, mouse distance | pynput | activity, intensity |
| backspace/delete count | pynput | correction rate (stress) |
| face presence | camera | time at screen, active detection |
| blinks | camera blendshapes | eye strain / stress |
| brow + mouth-press tension | camera blendshapes | stress |
| looking-away, leaning-too-close | camera head pose | focus, posture |
| chew cycles (rhythmic jaw motion) | camera blendshapes | chewing-gum time |
| active window class, app switches | xdotool/xprop | per-app time, focus |

## Detected events (calibrated against the real user, 2026-07-16)

- **Eye rub** (glasses off, rubbing eyes/face) — hands over the face make
  face detection flicker. Fires on ≥6 presence toggles within 20 s, spanning
  ≥8 s, with the face absent in ≥40 % of the window (a real rub hides the
  face; marginal detection with the head turned to a side monitor flickers
  while the face stays mostly visible — that's rejected). 60 s cooldown.
- **Chewing gum** — rhythmic `jawOpen` blendshape oscillation (~70 cycles/min
  facing the camera; amplitude drops ~40 % with the head turned, thresholds
  account for that). A minute with ≥15 detected cycles counts as chewing.
  Known ceiling: long talking sessions can mimic chewing.

Blink thresholds are similarly tuned low so blinks still register with the
head turned toward a side monitor.

## Scores (transparent heuristics, not medicine)

- **Focus** — % of active minutes with ≤2 app switches and attention on screen
- **Stress index (0–100)** — face tension 35 % + blink-rate deviation from
  normal (15–20/min) 25 % + correction rate 20 % + typing burstiness 20 %.
  Weights live in `stress_score()` in `tracker.py`.

## Configuration (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `break_every_min` | 50 | active minutes before a break reminder |
| `snooze_min` | 5 | re-remind interval until break taken |
| `idle_reset_min` | 3 | idle minutes that count as a break |
| `camera_enabled` | true | camera signal collection |
| `camera_fps` | 8 | camera sampling rate |
| `port` | 8787 | dashboard port (localhost only) |
| `notify` | true | desktop notifications |
| `ollama_model` | "" | insights model; empty = first installed |

Most settings are editable from the dashboard. `port`, `camera_fps` and
`ollama_model` require editing the file and restarting the service.

## System service

Unit file: `~/.config/systemd/user/activity-tracker.service` — starts on
login, restarts on failure.

```bash
systemctl --user status  activity-tracker   # health + PID
systemctl --user restart activity-tracker   # after code/config-file changes
systemctl --user stop    activity-tracker   # pause tracking
systemctl --user disable --now activity-tracker   # remove from autostart
journalctl --user -u activity-tracker -f    # live logs
```

To run manually instead (stop the service first): `.venv/bin/python tracker.py`

## Files

| File | Purpose |
|---|---|
| `tracker.py` | the entire app (collectors, scoring, reminders, web server) |
| `dashboard.html` | the dashboard page |
| `config.json` | settings (created/updated via dashboard) |
| `activity.db` | SQLite data — delete it to wipe all history |
| `face_landmarker.task` | MediaPipe face model (local, no network use) |
| `tracker.log` | only used when run manually; service logs go to journald |

Self-check: `.venv/bin/python tracker.py --selftest`

## Troubleshooting

- **Dashboard down** → `systemctl --user status activity-tracker`; port
  conflict? something else on 8787 — change `port` in `config.json`.
- **No camera signals** → another app may hold `/dev/video0`; check
  `journalctl --user -u activity-tracker` for "camera failed to open".
  Presence/blink/tension tiles stay empty but input tracking continues.
- **No keystrokes counted** → requires X11 (`echo $XDG_SESSION_TYPE`);
  Wayland would need an evdev-based rewrite of the input listeners.
- **Insights say "ollama unavailable"** → `ollama serve` isn't running or no
  model is pulled; heuristic analysis still works.
- **Days with zero data** → the service only records while your session is
  logged in and the service is running.
