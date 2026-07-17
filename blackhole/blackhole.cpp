#include "blackhole.h"

#include <kwinglplatform.h>

#include <QDBusConnection>
#include <QDebug>
#include <QFile>
#include <QVector2D>

#include <algorithm>
#include <cmath>

namespace KWin {

BlackHoleEffect::BlackHoleEffect()
{
    QDBusConnection bus = QDBusConnection::sessionBus();
    if (!bus.registerService(QStringLiteral("org.argus.blackhole"))) {
        qWarning() << "blackhole: DBus registerService failed — tracker cannot reach the effect";
    }
    if (!bus.registerObject(QStringLiteral("/BlackHole"), this,
                            QDBusConnection::ExportScriptableSlots)) {
        qWarning() << "blackhole: DBus registerObject failed — tracker cannot reach the effect";
    }
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
    // 0..1 = normal growth; 1..3 = overtime escalation (size only)
    m_target = std::clamp(strength, 0.0, 3.0);
    if (m_target > 0.0 || m_rendered > 0.0) {
        effects->addRepaintFull();
    }
}

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
        if (!f.open(QIODevice::ReadOnly)) {
            qWarning() << "blackhole: blackhole.frag resource missing";
            m_shaderFailed = true;
            return;
        }
        m_shader = ShaderManager::instance()->generateCustomShader(
            ShaderTrait::MapTexture, QByteArray(), f.readAll());
        if (!m_shader->isValid()) {
            qWarning() << "blackhole: shader failed to compile; effect disabled";
            m_shaderFailed = true;
            m_shader.reset();
        }
    }
}

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
    const float radius = 0.16f * std::min(hole.width(), hole.height())
                       * std::pow(float(m_rendered), 0.7f);
    // wandering drift: incommensurate Lissajous periods give a slow,
    // never-repeating path; the margin keeps the hole plus its ring glow on
    // the primary screen, so a bigger hole naturally roams a smaller area
    const float margin = radius * 1.4f + 40.0f;
    const float ax = std::max(0.0f, hole.width() / 2.0f - margin);
    const float ay = std::max(0.0f, hole.height() / 2.0f - margin);
    const QPointF c = QRectF(hole).center()
        + QPointF(ax * std::sin(m_timeSec * 2.0 * M_PI / 210.0),
                  ay * std::sin(m_timeSec * 2.0 * M_PI / 151.0));

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

} // namespace KWin
