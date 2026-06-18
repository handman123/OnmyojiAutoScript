from module.device.env import IS_WINDOWS, IS_MACINTOSH

if IS_WINDOWS:
    from module.device.platform2.platform_windows import PlatformWindows as Platform
elif IS_MACINTOSH:
    from module.device.platform2.platform_macos import PlatformMacOS as Platform
else:
    from module.device.platform2.platform_base import PlatformBase as Platform
