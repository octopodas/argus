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
