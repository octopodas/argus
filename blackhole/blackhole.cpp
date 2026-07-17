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
