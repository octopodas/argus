# Black Hole Overtime Effect — Design

2026-07-17

## Goal

A compositor-level "black hole" that appears on the main monitor when an
unbroken work streak runs too long, grows with continued work, and lenses
(gravitationally distorts) the real screen content around it — any app, not
just a terminal. Taking a real break collapses it. Driven by Argus's existing
streak/away tracking.

Target environment: KDE Plasma 5.27.11, KWin/X11, OpenGL compositing,
two monitors (hole lives on primary DP-0, 3840x2560 at +2560+0).

## Architecture

Two components, one DBus float between them:

```
tracker.py (existing streak logic)
    └─ every 5 s: strength 0..1 ──DBus──▶ KWin effect "blackhole" (C++ plugin)
                                              └─ paints desktop frame → offscreen
                                                 texture → lensing shader → screen
```

### Component 1: KWin effect (`blackhole/` in this repo)

- `blackhole.cpp/.h` — KWin::Effect modeled on the stock zoom effect's
  fullscreen pattern: in `paintScreen`, render the normal scene into an
  offscreen `GLTexture`, then draw a fullscreen quad through the lensing
  shader. `postPaintScreen` schedules the next repaint while active.
- `blackhole.frag` — the shader: radial Schwarzschild-style UV pull toward
  the hole, pure-black event horizon disc, bright photon ring, slow swirl of
  the lensed region, mild chromatic aberration near the ring.
  Uniforms: `u_time`, `u_strength` (0–1, drives radius/intensity),
  `u_center` (pixels), `u_resolution`.
- Hole center = center of the primary screen, computed from KWin's screen
  list each activation (not hardcoded; survives monitor changes).
- DBus: the effect registers service `org.argus.blackhole`, object
  `/BlackHole`, method `setStrength(double)`. The effect lerps its rendered
  strength toward the last received target each frame, so growth and
  collapse are smooth regardless of update cadence.
- `isActive()` returns false when rendered strength reaches 0 → KWin skips
  the effect entirely; dormant cost is zero.
- Build: CMake + extra-cmake-modules against `kwin-dev` 4:5.27.11 (matches
  installed KWin). Installed system-wide into KWin's Qt plugin dir (needs
  sudo), enabled via KWin's Desktop Effects config or
  `qdbus org.kde.KWin /Effects loadEffect blackhole`.

### Component 2: Argus bridge (~30 lines in `tracker.py`)

Every 5 s, from the existing tracking loop:

```
overtime = current_streak_sec − break_interval_sec
strength = clamp(overtime / (blackhole_ramp_min · 60), 0, 1)
if away: strength = 0
send via DBus, ignore all errors
```

- Invisible until the streak exceeds the user's configured break interval;
  full size `blackhole_ramp_min` (default 15) minutes later.
- Away/break (existing detection) → 0 → hole collapses (~2 s ease in the
  effect); streak reset logic is untouched, we only read it.
- New `config.json` keys: `blackhole_enabled` (default true),
  `blackhole_ramp_min` (default 15). Hot-reloaded like other config.
- DBus errors (effect not installed, KWin restarting) are silently ignored;
  the 5 s push loop self-heals after KWin restarts.

## Error handling

- Effect not installed / not loaded: Argus's DBus call fails silently;
  tracker unaffected.
- KWin restart: effect reloads at strength 0; next Argus push (≤5 s)
  restores state.
- Shader compile failure on this GPU: effect refuses to load; KWin logs it;
  desktop renders normally.

## Testing

- Instant visual check: `qdbus org.argus.blackhole /BlackHole setStrength 0.7`.
- Python: small assert-based test for the strength curve (below threshold →
  0, mid-ramp → fraction, capped at 1, away → 0).
- Manual: work past break interval, confirm growth; walk away, confirm
  collapse.

## Out of scope (v1)

- Dashboard UI toggle (config.json is enough).
- Plasma 6 / Wayland port (would need `OffscreenEffect` API changes).
- Per-window whitelist, second-monitor hole, follow-focus mode.
