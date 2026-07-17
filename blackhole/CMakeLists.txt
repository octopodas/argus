cmake_minimum_required(VERSION 3.16)
project(kwin-effect-blackhole)

set(CMAKE_CXX_STANDARD 20)
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
