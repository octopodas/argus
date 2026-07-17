#include "blackhole.h"

KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED(KWin::BlackHoleEffect,
                                      "metadata.json",
                                      return KWin::BlackHoleEffect::supported();,
                                      return true;)

#include "plugin.moc"
