"""Move Window Dock to main monitor - taskbar shortcut"""
import ctypes
import ctypes.wintypes
import json
from pathlib import Path

user32 = ctypes.windll.user32

def find_dock_window():
    """Find the Window Dock window handle"""
    result = []

    def callback(hwnd, lParam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            if buff.value == "HopperDock":
                result.append(hwnd)
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return result[0] if result else None

def get_primary_monitor():
    """Get primary monitor info (the one at 0,0)"""
    monitors = []

    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        r = lprcMonitor.contents
        monitors.append({
            'left': r.left,
            'top': r.top,
            'right': r.right,
            'bottom': r.bottom,
            'width': r.right - r.left,
            'height': r.bottom - r.top,
        })
        return True

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.wintypes.RECT),
        ctypes.c_double
    )
    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)

    # Find primary (at 0,0)
    for m in monitors:
        if m['left'] == 0 and m['top'] == 0:
            return m
    return monitors[0] if monitors else None

def move_dock_to_main():
    hwnd = find_dock_window()
    if not hwnd:
        return

    monitor = get_primary_monitor()
    if not monitor:
        return

    # Get current window size
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    # Calculate centered position at top of primary monitor
    x = monitor['left'] + (monitor['width'] - width) // 2
    y = monitor['top'] + 5

    # Move the window
    user32.SetWindowPos(hwnd, None, x, y, width, height, 0x0004 | 0x0040)

    # Update settings file so position persists
    settings_file = Path.home() / "WindowDock" / "settings.json"
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
            settings['x'] = x
            settings['y'] = y
            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
        except:
            pass

if __name__ == '__main__':
    move_dock_to_main()
