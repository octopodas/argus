# Black Hole Overtime Effect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A KWin compositor plugin that gravitationally lenses the desktop around a growing black hole when the user's Argus work streak runs past their break threshold, plus the ~30-line Argus bridge that drives it over DBus.

**Architecture:** Two components. (1) A C++ KWin effect (`blackhole/` in this repo) modeled on the stock zoom effect: render the composited screen into an offscreen GLTexture/GLFramebuffer, draw it back through a custom lensing fragment shader; it registers DBus service `org.argus.blackhole` with one method `setStrength(double)`. (2) `tracker.py` gains a pure `blackhole_strength()` function and a daemon thread that pushes strength via `dbus-send` every 10 s.

**Tech Stack:** KWin 5.27.11 effect API (X11, OpenGL compositing), CMake + extra-cmake-modules, Qt5 DBus, GLSL 140, Python 3 stdlib only.

## Global Constraints

- Target environment: KDE Plasma 5.27.11, kwin_x11, Qt 5.15.13, Kubuntu 24.04, NVIDIA, two monitors (primary DP-0 3840x2560 at +2560+0).
- Spec: `docs/superpowers/specs/2026-07-17-blackhole-effect-design.md`.
- Python side: stdlib only — no dbus-python, no new pip deps. DBus via `dbus-send` subprocess.
- Config keys (exact names): `blackhole_enabled` (default `True`), `blackhole_ramp_min` (default `15`).
- DBus (exact names): service `org.argus.blackhole`, object path `/BlackHole`, interface `org.argus.blackhole`, method `setStrength(double)`.
- Tests follow the repo's existing pattern: assert-based `selftest()` in `tracker.py`, run with `.venv/bin/python tracker.py --selftest`. No pytest.
- API facts verified against the installed candidate `kwin-dev 4:5.27.11-0ubuntu3` headers: factory macro `KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED(className, jsonFile, supported, enabled)`; `GLTexture(GLenum internalFormat, const QSize &size)`; `GLTexture::render(const QRect &rect, qreal scale)`; `GLFramebuffer::pushFramebuffer/popFramebuffer`; `ShaderManager::generateCustomShader(ShaderTraits, QByteArray vertex, QByteArray fragment)` (custom source used verbatim — the .frag file carries its own `#version 140`); CMake packages `KWinEffects::kwineffects` + `KWinEffects::kwinglutils`.
- **PREFLIGHT:** the working tree has pre-existing uncommitted user changes in `tracker.py` and `dashboard.html`. Before Task 1, ask the user to commit or stash them. Never commit those changes as part of this plan's commits.
- Steps marked `sudo` need the user's password; if a sudo command can't run in your sandbox, ask the user to run it via the `!` prefix.

---

### Task 1: Argus bridge — strength function, config keys, selftest, sender thread

**Files:**
- Modify: `tracker.py` (DEFAULTS ~line 15, new functions after `notify` ~line 252, `selftest()` ~line 536, `main()` ~line 560)

**Interfaces:**
- Consumes: existing globals `STREAK` (int, active minutes), `CFG` (dict), `STOP` (threading.Event); existing config key `break_every_min`.
- Produces: `blackhole_strength(streak_min: int, cfg: dict) -> float` (0..1); `blackhole_worker()` daemon loop sending `dbus-send … org.argus.blackhole.setStrength double:X.XXX` every 10 s. Tasks 2–4 rely on the exact DBus names in Global Constraints.

- [ ] **Step 1: Write the failing selftest asserts**

In `tracker.py`, inside `selftest()`, insert immediately after `assert stress_score([])[0] == 0` (line 547):

```python
    cfg = dict(DEFAULTS, break_every_min=50, blackhole_ramp_min=15)
    assert blackhole_strength(0, cfg) == 0.0
    assert blackhole_strength(50, cfg) == 0.0
    assert abs(blackhole_strength(57.5, cfg) - 0.5) < 1e-9
    assert blackhole_strength(80, cfg) == 1.0
    assert blackhole_strength(80, dict(cfg, blackhole_enabled=False)) == 0.0
```

- [ ] **Step 2: Run selftest to verify it fails**

Run: `cd /home/max/dev/mine/argus && .venv/bin/python tracker.py --selftest`
Expected: `NameError: name 'blackhole_strength' is not defined`

- [ ] **Step 3: Implement config keys + functions**

In `DEFAULTS` (line 15–24), add two entries before the closing brace:

```python
    "blackhole_enabled": True,  # KWin black-hole overtime effect (see blackhole/)
    "blackhole_ramp_min": 15,   # minutes past break threshold until full size
```

After the `notify()` function (ends ~line 252), insert:

```python
def blackhole_strength(streak_min, cfg):
    """0..1: how far past the break threshold the active streak is.
    Drives the KWin black-hole effect; 0 = invisible."""
    if not cfg.get("blackhole_enabled", True):
        return 0.0
    over = streak_min - cfg["break_every_min"]
    return max(0.0, min(1.0, over / max(1, cfg["blackhole_ramp_min"])))

def blackhole_worker():
    """Push strength to the KWin effect every 10 s over DBus.
    Silently a no-op when the effect isn't installed or KWin is restarting."""
    while not STOP.is_set():
        s = blackhole_strength(STREAK, CFG)
        try:
            subprocess.run(
                ["dbus-send", "--session", "--dest=org.argus.blackhole",
                 "/BlackHole", "org.argus.blackhole.setStrength", f"double:{s:.3f}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
        STOP.wait(10)
```

In `main()`, after `threading.Thread(target=aggregator, daemon=True).start()` (line 568), add:

```python
    threading.Thread(target=blackhole_worker, daemon=True).start()
```

- [ ] **Step 4: Run selftest to verify it passes**

Run: `.venv/bin/python tracker.py --selftest`
Expected: `selftest ok`

- [ ] **Step 5: Sanity-check the dbus-send call shape (no effect installed yet)**

Run: `dbus-send --session --dest=org.argus.blackhole /BlackHole org.argus.blackhole.setStrength double:0.5; echo exit=$?`
Expected: an error naming the missing service, e.g. `The name org.argus.blackhole was not provided by any .service files` — proves the command is well-formed and fails *quietly* (nonzero exit, no hang).

- [ ] **Step 6: Commit (only the blackhole hunks — tracker.py has unrelated pre-existing edits unless the preflight cleared them)**

```bash
git add -p tracker.py   # select only the DEFAULTS/blackhole_*/selftest/main hunks
git commit -m "feat: blackhole strength bridge in tracker (DBus sender + selftest)"
```

If the preflight left the tree clean, plain `git add tracker.py` is fine.

---

### Task 2: KWin effect skeleton — builds, loads, answers DBus

**Files:**
- Create: `blackhole/CMakeLists.txt`
- Create: `blackhole/metadata.json`
- Create: `blackhole/plugin.cpp`
- Create: `blackhole/blackhole.h`
- Create: `blackhole/blackhole.cpp` (skeleton bodies; Task 3 fills rendering)
- Create: `blackhole/blackhole.frag` (placeholder comment; Task 3 fills)
- Create: `blackhole/resources.qrc`
- Modify: `.gitignore` (create if absent) — add `blackhole/build/`

**Interfaces:**
- Consumes: DBus names from Global Constraints (Task 1's sender).
- Produces: plugin id `blackhole` loadable by KWin; class `KWin::BlackHoleEffect : public Effect` with `Q_SCRIPTABLE void setStrength(double)`, members `m_target`, `m_rendered`, `m_timeSec`, `m_lastPresent`, `m_shader`, `m_texture`, `m_fbo`, `m_shaderFailed`, and methods `prePaintScreen/paintScreen/postPaintScreen/isActive/ensureResources` that Task 3 fills in.

- [ ] **Step 1: Install build dependencies (sudo)**

```bash
sudo apt install -y cmake extra-cmake-modules g++ kwin-dev qtbase5-dev \
  libkf5config-dev libkf5coreaddons-dev libkf5windowsystem-dev \
  libxcb1-dev libepoxy-dev
```

Expected: installs without unmet-dependency errors; `kwin-dev` version `4:5.27.11-0ubuntu3` (must match `kwin_x11 --version` = 5.27.11).

- [ ] **Step 2: Write `blackhole/CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.16)
project(kwin-effect-blackhole)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

find_package(ECM REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} ${ECM_MODULE_PATH})

include(KDEInstallDirs)
include(KDECMakeSettings)

find_package(Qt5 REQUIRED COMPONENTS Core Gui DBus)
find_package(KF5 REQUIRED COMPONENTS Config CoreAddons WindowSystem)
find_package(XCB REQUIRED COMPONENTS XCB)
find_package(epoxy REQUIRED)
find_package(KWinEffects REQUIRED)

add_library(kwin_effect_blackhole SHARED
    plugin.cpp
    blackhole.cpp
    resources.qrc
)
target_link_libraries(kwin_effect_blackhole
    PRIVATE
        KWinEffects::kwineffects
        KWinEffects::kwinglutils
        Qt5::Core
        Qt5::Gui
        Qt5::DBus
)
install(TARGETS kwin_effect_blackhole
        DESTINATION ${KDE_INSTALL_QTPLUGINDIR}/kwin/effects/plugins)
```

- [ ] **Step 3: Write `blackhole/metadata.json`**

```json
{
    "KPlugin": {
        "Id": "blackhole",
        "Name": "Black Hole",
        "Description": "Argus overtime black hole — lenses the desktop when you work too long",
        "Category": "Appearance",
        "EnabledByDefault": true,
        "License": "GPL",
        "ServiceTypes": [
            "KWin/Effect"
        ]
    }
}
```

- [ ] **Step 4: Write `blackhole/plugin.cpp`**

```cpp
#include "blackhole.h"

KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED(KWin::BlackHoleEffect,
                                      "metadata.json",
                                      return KWin::BlackHoleEffect::supported();,
                                      return true;)

#include "plugin.moc"
```

- [ ] **Step 5: Write `blackhole/blackhole.h`**

```cpp
#pragma once

#include <kwineffects.h>
#include <kwinglutils.h>

#include <chrono>
#include <memory>

namespace KWin {

// Fullscreen "black hole" driven over DBus by the Argus tracker: renders the
// composited desktop into an offscreen texture, then draws it back through a
// gravitational-lensing shader.
class BlackHoleEffect : public Effect
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.argus.blackhole")

public:
    BlackHoleEffect();
    ~BlackHoleEffect() override;

    void prePaintScreen(ScreenPrePaintData &data, std::chrono::milliseconds presentTime) override;
    void paintScreen(int mask, const QRegion &region, ScreenPaintData &data) override;
    void postPaintScreen() override;
    bool isActive() const override;
    // same slot as the stock zoom effect: a fullscreen transform that wraps
    // everything else in the chain
    int requestedEffectChainPosition() const override { return 10; }

    static bool supported();

public Q_SLOTS:
    Q_SCRIPTABLE void setStrength(double strength);

private:
    void ensureResources();

    std::unique_ptr<GLShader> m_shader;
    std::unique_ptr<GLTexture> m_texture;
    std::unique_ptr<GLFramebuffer> m_fbo;
    std::chrono::milliseconds m_lastPresent{0};
    double m_timeSec = 0.0;
    double m_target = 0.0;   // last strength received over DBus
    double m_rendered = 0.0; // smoothed strength actually drawn
    bool m_shaderFailed = false;
};

} // namespace KWin
```

- [ ] **Step 6: Write skeleton `blackhole/blackhole.cpp`**

DBus works, rendering is pass-through; Task 3 replaces the three paint methods, `isActive`, and `ensureResources`.

```cpp
#include "blackhole.h"

#include <kwinglplatform.h>

#include <QDBusConnection>
#include <QDebug>

#include <algorithm>

namespace KWin {

BlackHoleEffect::BlackHoleEffect()
{
    QDBusConnection bus = QDBusConnection::sessionBus();
    bus.registerService(QStringLiteral("org.argus.blackhole"));
    bus.registerObject(QStringLiteral("/BlackHole"), this,
                       QDBusConnection::ExportScriptableSlots);
}

BlackHoleEffect::~BlackHoleEffect()
{
    QDBusConnection bus = QDBusConnection::sessionBus();
    bus.unregisterObject(QStringLiteral("/BlackHole"));
    bus.unregisterService(QStringLiteral("org.argus.blackhole"));
}

bool BlackHoleEffect::supported()
{
    return effects->compositingType() == OpenGLCompositing
        && GLPlatform::instance()->glslVersion() >= kVersionNumber(1, 40);
}

void BlackHoleEffect::setStrength(double strength)
{
    m_target = std::clamp(strength, 0.0, 1.0);
    qWarning() << "blackhole: setStrength" << m_target; // skeleton proof; removed in Task 3
    if (m_target > 0.0 || m_rendered > 0.0) {
        effects->addRepaintFull();
    }
}

void BlackHoleEffect::ensureResources()
{
}

void BlackHoleEffect::prePaintScreen(ScreenPrePaintData &data, std::chrono::milliseconds presentTime)
{
    effects->prePaintScreen(data, presentTime);
}

void BlackHoleEffect::paintScreen(int mask, const QRegion &region, ScreenPaintData &data)
{
    effects->paintScreen(mask, region, data);
}

void BlackHoleEffect::postPaintScreen()
{
    effects->postPaintScreen();
}

bool BlackHoleEffect::isActive() const
{
    return false; // skeleton: never paints
}

} // namespace KWin
```

- [ ] **Step 7: Write placeholder `blackhole/blackhole.frag` and `blackhole/resources.qrc`**

`blackhole.frag`:

```glsl
// filled in by the rendering task
```

`resources.qrc`:

```xml
<RCC>
    <qresource prefix="/effects/blackhole">
        <file>blackhole.frag</file>
    </qresource>
</RCC>
```

- [ ] **Step 8: Add build dir to `.gitignore`**

Append line `blackhole/build/` to `.gitignore` (create the file if it doesn't exist).

- [ ] **Step 9: Build**

```bash
cd /home/max/dev/mine/argus/blackhole && mkdir -p build && cd build
cmake -DCMAKE_INSTALL_PREFIX=/usr .. && make
```

Expected: `[100%] Built target kwin_effect_blackhole`. If cmake fails on `XCB` or `epoxy` targets, the find_package lines cover them — check the missing package name in the error and apt-install its `-dev` package.

- [ ] **Step 10: Install and load (sudo)**

```bash
sudo make install
qdbus org.kde.KWin /KWin reconfigure
sleep 2
qdbus org.kde.KWin /Effects isEffectLoaded blackhole
```

Expected: `true`. If `false`, run `qdbus org.kde.KWin /Effects loadEffect blackhole` and re-check.

- [ ] **Step 11: Verify DBus round-trip**

```bash
qdbus org.argus.blackhole /BlackHole
dbus-send --session --dest=org.argus.blackhole /BlackHole org.argus.blackhole.setStrength double:0.5; echo exit=$?
```

Expected: first command lists `method void org.argus.blackhole.setStrength(double strength)`; second prints `exit=0`.

- [ ] **Step 12: Commit**

```bash
cd /home/max/dev/mine/argus
git add blackhole/ .gitignore
git commit -m "feat: KWin blackhole effect skeleton (builds, loads, DBus setStrength)"
```

---

### Task 3: Rendering — offscreen pass, lensing shader, smoothing, repaint loop

**Files:**
- Modify: `blackhole/blackhole.cpp` (replace `ensureResources`, the three paint methods, `isActive`, `setStrength`; add includes and helper)
- Modify: `blackhole/blackhole.frag` (full shader)

**Interfaces:**
- Consumes: class/members from Task 2 exactly as declared in `blackhole.h`.
- Produces: the working visual. Shader uniform names (must match between .cpp and .frag): `u_resolution`, `u_center`, `u_radius`, `u_strength`, `u_time`.

- [ ] **Step 1: Write the full `blackhole/blackhole.frag`**

```glsl
#version 140

uniform sampler2D sampler;   // KWin-provided: the offscreen screen texture
uniform vec2 u_resolution;   // virtual screen size, px
uniform vec2 u_center;       // hole center, px, in texcoord0's coordinate space
uniform float u_radius;      // event-horizon radius, px
uniform float u_strength;    // 0..1 smoothed strength
uniform float u_time;        // seconds since effect activation

in vec2 texcoord0;
out vec4 fragColor;

vec3 grab(vec2 px)
{
    return texture(sampler, clamp(px / u_resolution, 0.0, 1.0)).rgb;
}

void main()
{
    vec2 px = texcoord0 * u_resolution;
    vec2 d = px - u_center;
    float r = length(d);
    float rs = u_radius;

    if (r <= rs) { // inside the event horizon
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // gravitational lensing: pull the sample point toward the hole, ~1/r
    // falloff. Near the horizon r-bend goes negative and samples the far
    // side — the flipped-image look of real lensing.
    float bend = rs * rs / max(r, 1.0);

    // frame dragging: gentle rocking swirl that decays away from the hole
    float swirl = 1.2 * u_strength * exp(-(r - rs) / (2.0 * rs)) * sin(u_time * 0.35);
    float cs = cos(swirl), sn = sin(swirl);
    vec2 dir = normalize(d);
    dir = vec2(dir.x * cs - dir.y * sn, dir.x * sn + dir.y * cs);

    // chromatic aberration: each channel bends slightly differently
    vec3 col;
    col.r = grab(u_center + dir * (r - bend * 1.06)).r;
    col.g = grab(u_center + dir * (r - bend)).g;
    col.b = grab(u_center + dir * (r - bend * 0.94)).b;

    // fade to black approaching the horizon
    col *= smoothstep(rs, rs * 1.25, r);

    // photon ring hugging the horizon, gently shimmering
    float ring = exp(-pow((r - rs * 1.1) / (rs * 0.18), 2.0));
    float shimmer = 0.85 + 0.15 * sin(u_time * 1.7 + atan(d.y, d.x) * 3.0);
    col += vec3(1.0, 0.93, 0.78) * ring * shimmer * u_strength;

    fragColor = vec4(col, 1.0);
}
```

- [ ] **Step 2: Replace the skeleton bodies in `blackhole/blackhole.cpp`**

Add to the includes block (`<algorithm>` is already there from Task 2):

```cpp
#include <QFile>
#include <QVector2D>

#include <cmath>
```

Replace `setStrength` (drop the skeleton qWarning):

```cpp
void BlackHoleEffect::setStrength(double strength)
{
    m_target = std::clamp(strength, 0.0, 1.0);
    if (m_target > 0.0 || m_rendered > 0.0) {
        effects->addRepaintFull();
    }
}
```

Replace `ensureResources`:

```cpp
void BlackHoleEffect::ensureResources()
{
    const QSize vs = effects->virtualScreenSize();
    if (!m_texture || m_texture->size() != vs) {
        m_texture = std::make_unique<GLTexture>(GL_RGBA8, vs);
        m_texture->setFilter(GL_LINEAR);
        m_texture->setWrapMode(GL_CLAMP_TO_EDGE);
        m_fbo = std::make_unique<GLFramebuffer>(m_texture.get());
    }
    if (!m_shader && !m_shaderFailed) {
        QFile f(QStringLiteral(":/effects/blackhole/blackhole.frag"));
        f.open(QIODevice::ReadOnly);
        m_shader = ShaderManager::instance()->generateCustomShader(
            ShaderTrait::MapTexture, QByteArray(), f.readAll());
        if (!m_shader->isValid()) {
            qWarning() << "blackhole: shader failed to compile; effect disabled";
            m_shaderFailed = true;
            m_shader.reset();
        }
    }
}
```

Add this helper above `prePaintScreen`:

```cpp
// ponytail: "main monitor" = the largest screen; make it a config key if the
// monitor setup ever makes that ambiguous
static QRect primaryScreenGeometry()
{
    QRect best;
    const QList<EffectScreen *> screens = effects->screens();
    for (EffectScreen *s : screens) {
        const QRect g = s->geometry();
        if (g.width() * g.height() > best.width() * best.height()) {
            best = g;
        }
    }
    return best.isEmpty() ? effects->virtualScreenGeometry() : best;
}
```

Replace the three paint methods and `isActive`:

```cpp
void BlackHoleEffect::prePaintScreen(ScreenPrePaintData &data, std::chrono::milliseconds presentTime)
{
    double dt = 0.0;
    if (m_lastPresent.count() && presentTime > m_lastPresent) {
        dt = (presentTime - m_lastPresent).count() / 1000.0;
    }
    m_lastPresent = presentTime;
    m_timeSec += dt;

    // slow, continuous-looking growth (streak steps once a minute);
    // fast dramatic collapse when the target drops
    const double k = (m_target > m_rendered) ? 0.08 : 1.5;
    m_rendered += (m_target - m_rendered) * std::min(1.0, k * dt);
    if (m_target <= 0.001 && m_rendered < 0.005) {
        m_rendered = 0.0;
        m_lastPresent = std::chrono::milliseconds::zero();
    }

    if (m_rendered > 0.0) {
        data.mask |= PAINT_SCREEN_TRANSFORMED;
    }
    effects->prePaintScreen(data, presentTime);
}

void BlackHoleEffect::paintScreen(int mask, const QRegion &region, ScreenPaintData &data)
{
    if (m_rendered <= 0.0 || m_shaderFailed) {
        effects->paintScreen(mask, region, data);
        return;
    }
    ensureResources();
    if (m_shaderFailed) {
        effects->paintScreen(mask, region, data);
        return;
    }

    GLFramebuffer::pushFramebuffer(m_fbo.get());
    effects->paintScreen(mask, region, data);
    GLFramebuffer::popFramebuffer();

    const QSize vs = effects->virtualScreenSize();
    const QRect hole = primaryScreenGeometry();
    const QPointF c = QRectF(hole).center();
    const float radius = 0.16f * std::min(hole.width(), hole.height())
                       * std::pow(float(m_rendered), 0.7f);

    ShaderManager::instance()->pushShader(m_shader.get());
    m_shader->setUniform(GLShader::ModelViewProjectionMatrix, data.projectionMatrix());
    m_shader->setUniform("u_resolution", QVector2D(vs.width(), vs.height()));
    // texcoord0 has GL's bottom-left origin; screen coords are top-left
    m_shader->setUniform("u_center", QVector2D(c.x(), vs.height() - c.y()));
    m_shader->setUniform("u_radius", radius);
    m_shader->setUniform("u_strength", float(m_rendered));
    m_shader->setUniform("u_time", float(m_timeSec));
    m_texture->bind();
    m_texture->render(QRect(QPoint(0, 0), vs), 1.0);
    m_texture->unbind();
    ShaderManager::instance()->popShader();
}

void BlackHoleEffect::postPaintScreen()
{
    if (isActive()) {
        effects->addRepaintFull();
    }
    effects->postPaintScreen();
}

bool BlackHoleEffect::isActive() const
{
    return !m_shaderFailed && (m_rendered > 0.0 || m_target > 0.001);
}
```

- [ ] **Step 3: Rebuild and reinstall (sudo)**

```bash
cd /home/max/dev/mine/argus/blackhole/build && make && sudo make install
qdbus org.kde.KWin /Effects unloadEffect blackhole
qdbus org.kde.KWin /Effects loadEffect blackhole
```

Expected: builds clean; loadEffect returns `true`.

- [ ] **Step 4: Visual verification**

```bash
dbus-send --session --dest=org.argus.blackhole /BlackHole org.argus.blackhole.setStrength double:0.7
```

Check with the user (they're at the machine):
- A black hole with a warm photon ring grows in over ~30 s, centered on the DP-0 monitor, content around it visibly bent/swirling; desktop stays fully interactive (effect is post-processing only, input untouched).
- **If the hole appears at the wrong vertical position** (Y mirrored — e.g. centered on the wrong half of the virtual screen): the FBO texture wasn't Y-inverted; change the `u_center` line to `QVector2D(c.x(), c.y())` and rebuild. Exactly one of the two forms is correct; the horizontal position is already unambiguous.
- If the whole screen is black or garbled: check `journalctl --user -b | grep -i blackhole` and kwin output in `~/.xsession-errors` for the shader-compile warning.

Then collapse:

```bash
dbus-send --session --dest=org.argus.blackhole /BlackHole org.argus.blackhole.setStrength double:0
```

Expected: hole collapses in ~2 s, then `qdbus org.kde.KWin /Effects activeEffects` no longer lists `blackhole` (dormant = zero cost).

- [ ] **Step 5: Commit**

```bash
cd /home/max/dev/mine/argus
git add blackhole/blackhole.cpp blackhole/blackhole.frag
git commit -m "feat: blackhole rendering — offscreen lensing shader, smoothing, repaint loop"
```

---

### Task 4: End-to-end wiring + README

**Files:**
- Modify: `README.md` (new section after "Dashboard")
- No code changes expected

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: running system + docs.

- [ ] **Step 1: Restart the tracker service and confirm the sender loop**

```bash
systemctl --user restart activity-tracker
sleep 15
qdbus org.kde.KWin /Effects activeEffects
```

Expected: restart succeeds (`systemctl --user status activity-tracker` shows active). `activeEffects` lists `blackhole` only if the current streak is already past `break_every_min`; otherwise absent — both are correct. The real proof: no errors in `journalctl --user -u activity-tracker -n 20`.

- [ ] **Step 2: Optional fast e2e (ask the user first — it fires real notifications)**

Temporarily lower the threshold so the streak is already "over":

```bash
curl -s -X POST http://localhost:8787/api/config -d '{"break_every_min": 1}'
# wait ≤10 s for the next sender tick; hole should start growing if streak > 1 min
curl -s -X POST http://localhost:8787/api/config -d '{"break_every_min": 50}'
```

Expected: hole appears within ~10 s of the first call, collapses within ~10 s of the second.

- [ ] **Step 3: Add README section**

Insert after the "Dashboard" section of `README.md`:

```markdown
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
    qdbus org.kde.KWin /KWin reconfigure

Instant test:

    dbus-send --session --dest=org.argus.blackhole /BlackHole \
      org.argus.blackhole.setStrength double:0.7
```

- [ ] **Step 4: Final selftest + commit**

```bash
.venv/bin/python tracker.py --selftest
git add README.md
git commit -m "docs: black hole effect section"
```

Expected: `selftest ok`, then clean commit.
