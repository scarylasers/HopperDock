"""
HopperDock - A minimal floating toolbar for window management
Cute Bunny Theme Edition with Shortcut Categories
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import ctypes
import ctypes.wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import logging
from datetime import datetime

# tkinterdnd2 — OLE-based DnD that works on overrideredirect windows where
# the legacy WM_DROPFILES path silently no-ops. Optional: dock falls back
# to the older code path if the package isn't bundled.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except Exception as _dnd_err:
    TkinterDnD = None
    DND_FILES = '*'
    _DND_AVAILABLE = False

__version__ = "1.6.0"

# Where the user's settings, shortcuts, icon cache, logs and starter scripts
# live — never next to the app, so replacing the .exe to upgrade can't take
# the config with it.
CONFIG_DIR_NAME = "HopperDock"
LEGACY_CONFIG_DIR_NAME = "WindowDock"  # pre-1.6, when the app had the old name


def _repoint_config_paths(config_dir, old_dir, new_dir):
    """Rewrite absolute paths in the config JSON that point into `old_dir`.

    The folder rename moves the files but not the *references to them*: icons
    for dropped apps are cached inside the config folder and stored as
    absolute paths, so after the rename a pinned app silently loses its icon.
    Rewrites the path prefix in every spelling that can appear in these files
    — JSON-escaped backslashes, plain backslashes, and forward slashes.
    """
    forms = [(str(old_dir).replace('\\', s), str(new_dir).replace('\\', s))
             for s in ('\\\\', '\\', '/')]
    for path in config_dir.glob('*.json'):
        try:
            text = original = path.read_text(encoding='utf-8-sig')
            for stale, fresh in forms:
                text = re.sub(re.escape(stale), fresh.replace('\\', '\\\\'),
                              text, flags=re.IGNORECASE)
            if text != original:
                path.write_text(text, encoding='utf-8')
        except Exception:
            # A single unreadable/locked file must not abort the migration —
            # the folder move itself has already succeeded by this point.
            pass


def _resolve_config_dir():
    """`~/HopperDock`, migrating a pre-1.6 `~/WindowDock` folder into it once.

    A plain rename keeps the folder's contents intact; `_repoint_config_paths`
    then fixes the stored references that still name the old folder. If the
    rename can't happen — both folders exist, or another instance still holds
    the log file open — keep using the legacy folder rather than silently
    stranding the user's shortcuts in a directory nothing reads.
    """
    new = Path.home() / CONFIG_DIR_NAME
    old = Path.home() / LEGACY_CONFIG_DIR_NAME
    if new.is_dir() or not old.is_dir():
        return new
    try:
        old.rename(new)
    except Exception:
        return old
    _repoint_config_paths(new, old, new)
    return new


CONFIG_DIR = _resolve_config_dir()

# Setup logging
LOG_DIR = CONFIG_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"window_dock.log"

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ]
)
logger = logging.getLogger('HopperDock')

# Log startup
logger.info("=" * 50)
logger.info(f"HopperDock starting - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Windows API setup
user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32

# Set explicit argtypes so 8-byte HWNDs aren't truncated to 32-bit (silent
# no-op or wrong window targeting on x64).
user32.GetParent.argtypes = [ctypes.wintypes.HWND]
user32.GetParent.restype = ctypes.wintypes.HWND
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.wintypes.HWND
user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_void_p
user32.RedrawWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.wintypes.UINT]
user32.RedrawWindow.restype = ctypes.wintypes.BOOL
shell32.DragAcceptFiles.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.BOOL]
shell32.DragAcceptFiles.restype = None

# Constants
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_SHOWMINNOACTIVE = 7  # minimize without stealing focus
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020
WPF_RESTORETOMAXIMIZED = 0x0002
GW_OWNER = 4


# Separators apps use between document and app name: "Friends - Discord",
# "#general | Server - Discord".
_TITLE_SEPARATORS = (' - ', ' — ', ' – ', ' | ', ' • ')


def _title_segments(title):
    """Split a window caption into its separator-delimited parts."""
    parts = [title]
    for sep in _TITLE_SEPARATORS:
        parts = [chunk for part in parts for chunk in part.split(sep)]
    return [p.strip().lower() for p in parts if p.strip()]


def _find_app_window(title):
    """HWND for the window named `title`, else 0.

    Exact match first, then by caption segment. Apps that retitle themselves
    per view never satisfy FindWindowW's exact compare — Discord is "Friends
    - Discord", then "#channel | Server - Discord" — so a pin configured with
    "Discord" would relaunch forever instead of toggling.

    Matching is per segment, not substring: a plain `in` test also matches
    unrelated windows that merely mention the app ("Create homepage linking
    to Discord community"). A trailing segment outranks any other because
    "<document> - <app>" is the Windows convention, which keeps a browser tab
    about Discord from outranking Discord itself.

    Hidden windows count — hiding one is how the toggle works, so it has to
    stay findable to be unhidden — but visible ones are preferred.
    """
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return hwnd

    needle = title.strip().lower()
    # [trailing-visible, trailing-hidden, any-visible, any-hidden]
    tiers = ([], [], [], [])

    def callback(hwnd, _lparam):
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex_style & WS_EX_TOOLWINDOW or user32.GetWindow(hwnd, GW_OWNER):
            return True  # owned popups and tool windows aren't the main window
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        segments = _title_segments(buff.value)
        if needle not in segments:
            return True
        tier = 0 if segments[-1] == needle else 2
        if not user32.IsWindowVisible(hwnd):
            tier += 1
        tiers[tier].append(hwnd)
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                              ctypes.wintypes.LPARAM)(callback)
    user32.EnumWindows(proc, 0)
    for tier in tiers:
        if tier:
            return tier[0]
    return 0


# Scripts that talk to a terminal need a terminal to talk to. See _spawn.
CONSOLE_SUFFIXES = ('.bat', '.cmd', '.ps1')


def _launch_argv(path, launch_cmd=''):
    """argv to launch `path`, or None to hand it to ShellExecute.

    Scripts get an explicit interpreter rather than relying on the file
    association: a missing or hijacked .py/.pyw handler turns an otherwise
    fine click into Windows' "How do you want to open this file?" picker.
    """
    if launch_cmd:
        return [launch_cmd, path]
    low = path.lower()
    if low.endswith(('.py', '.pyw')):
        return ['pythonw', path]
    if low.endswith('.ps1'):
        return ['powershell', '-ExecutionPolicy', 'Bypass', '-File', path]
    if low.endswith(('.bat', '.cmd')):
        return ['cmd', '/c', path]
    return None


REPO_URL = "https://github.com/scarylasers/HopperDock"
RELEASES_URL = f"{REPO_URL}/releases/latest"
RELEASE_API_URL = "https://api.github.com/repos/scarylasers/HopperDock/releases/latest"
GUIDE_URL = "https://scarylasers.github.io/HopperDock/"
KOFI_URL = "https://ko-fi.com/scarylasers_"


def _version_tuple(v):
    """"1.6.0" -> (1, 6, 0), for comparing releases. Unparseable parts sort
    lowest so a malformed tag can never masquerade as an upgrade."""
    parts = []
    for chunk in str(v or '').strip().lstrip('vV').split('.'):
        digits = re.match(r'\d+', chunk)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))[:3]


MIN_TILEABLE = 200  # px; below this a trimmed axis isn't worth tiling into


def _carve_dock_out(work, dock):
    """`work` rect minus the strip the dock occupies. Both are (l, t, r, b).

    A monitor's work area already excludes the taskbar, and excludes a
    *registered* appbar too — but the dock is only an appbar when Windows
    accepted ABM_NEW, and never while it's floating. Without this, tiled
    windows slide underneath the dock and whatever landed behind it looks
    like it was never tiled at all.
    """
    left, top, right, bottom = work
    d_left, d_top, d_right, d_bottom = dock

    # No overlap → nothing to carve. This is the case when the dock *is* a
    # registered appbar: the work area already stops at its edge, and trimming
    # again would cost a second dock's width for no reason.
    if d_right <= left or d_left >= right or d_bottom <= top or d_top >= bottom:
        return work

    # Bite the edge the dock hugs — the side it sits closest to. Choosing the
    # nearest edge keeps this right for a dock pinned left, or dragged to the
    # bottom, without a separate case for each.
    gaps = {'left': d_right - left, 'right': right - d_left,
            'top': d_bottom - top, 'bottom': bottom - d_top}
    edge = min(gaps, key=gaps.get)
    if edge == 'left':
        left = max(left, d_right)
    elif edge == 'right':
        right = min(right, d_left)
    elif edge == 'top':
        top = max(top, d_bottom)
    else:
        bottom = min(bottom, d_top)

    # A dock covering most of the screen would leave nothing to tile into.
    # Better to tile under it than to hand out zero-width tiles.
    if right - left < MIN_TILEABLE or bottom - top < MIN_TILEABLE:
        return work
    return left, top, right, bottom


def _spawn(path, launch_cmd=''):
    """Launch `path` the way double-clicking it in Explorer would.

    Never pass shell=True here. CPython's Windows implementation sets
    STARTF_USESHOWWINDOW/SW_HIDE for shell=True, so a .bat launched that way
    runs completely invisibly — and one that ends in `pause` then sits there
    forever as an orphaned hidden cmd.exe. HopperDock is a windowed app with
    no console of its own to lend the child either, so console scripts have to
    be given one explicitly with CREATE_NEW_CONSOLE.

    Everything else is detached, so closing the dock never kills what it
    launched. cwd is the script's own folder, matching Explorer, so relative
    paths inside a user's script resolve the way they expect.
    """
    argv = _launch_argv(path, launch_cmd)
    if argv is None:
        os.startfile(path)
        return
    cwd = None
    try:
        parent = Path(path).parent
        if parent.is_dir():
            cwd = str(parent)
    except Exception:
        pass
    console = path.lower().endswith(CONSOLE_SUFFIXES)
    flags = (subprocess.CREATE_NEW_CONSOLE if console
             else subprocess.DETACHED_PROCESS)
    subprocess.Popen(argv, creationflags=flags, cwd=cwd, close_fds=True)


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", ctypes.wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", ctypes.wintypes.DWORD),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.wintypes.BOOL),
        ("xHotspot", ctypes.wintypes.DWORD),
        ("yHotspot", ctypes.wintypes.DWORD),
        ("hbmMask", ctypes.wintypes.HBITMAP),
        ("hbmColor", ctypes.wintypes.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.wintypes.WORD),
        ("biBitCount", ctypes.wintypes.WORD),
        ("biCompression", ctypes.wintypes.DWORD),
        ("biSizeImage", ctypes.wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.wintypes.DWORD),
        ("biClrImportant", ctypes.wintypes.DWORD),
    ]


SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_ICONLOCATION = 0x000001000
SHGFI_SYSICONINDEX = 0x000004000
DIB_RGB_COLORS = 0

# Microsoft Store apps are launched through a zero-byte "app execution alias"
# in WindowsApps, which is a reparse point rather than a real executable —
# so there's no icon in it to extract until it's resolved.
FSCTL_GET_REPARSE_POINT = 0x000900A8
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
IO_REPARSE_TAG_APPEXECLINK = 0x8000001B
OPEN_EXISTING = 3

# Explicit signatures — on x64 the ctypes default of c_int truncates handles
# and pointers, which silently yields garbage rather than an error.
shell32.SHGetFileInfoW.argtypes = [ctypes.c_wchar_p, ctypes.wintypes.DWORD,
                                   ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p
user32.PrivateExtractIconsW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.POINTER(ctypes.wintypes.HICON), ctypes.POINTER(ctypes.c_uint),
    ctypes.c_uint, ctypes.c_uint]
user32.PrivateExtractIconsW.restype = ctypes.c_uint
user32.GetIconInfo.argtypes = [ctypes.wintypes.HICON, ctypes.c_void_p]
user32.GetIconInfo.restype = ctypes.wintypes.BOOL
user32.DestroyIcon.argtypes = [ctypes.wintypes.HICON]
user32.DestroyIcon.restype = ctypes.wintypes.BOOL

# GDI handles are pointer-sized. Without these the default c_int truncates
# them and raises "int too long to convert" for any handle above 2^31.
gdi32 = ctypes.windll.gdi32
gdi32.CreateCompatibleDC.argtypes = [ctypes.wintypes.HDC]
gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
gdi32.DeleteDC.argtypes = [ctypes.wintypes.HDC]
gdi32.DeleteDC.restype = ctypes.wintypes.BOOL
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.restype = ctypes.wintypes.BOOL
gdi32.GetDIBits.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HBITMAP,
                            ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int


class WINDOWPLACEMENT(ctypes.Structure):
    """Lets us read a minimized window's *restore* rect — its live
    GetWindowRect is a bogus off-screen (-32000) placeholder."""
    _fields_ = [
        ("length", ctypes.wintypes.UINT),
        ("flags", ctypes.wintypes.UINT),
        ("showCmd", ctypes.wintypes.UINT),
        ("ptMinPosition", ctypes.wintypes.POINT),
        ("ptMaxPosition", ctypes.wintypes.POINT),
        ("rcNormalPosition", ctypes.wintypes.RECT),
    ]

# Keyboard input constants
VK_LWIN = 0x5B
VK_SHIFT = 0x10
VK_LEFT = 0x25
VK_RIGHT = 0x27
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("ki", KEYBDINPUT),
        ("padding", ctypes.c_ubyte * 8),
    ]

# AppBar constants
ABM_NEW = 0x00
ABM_REMOVE = 0x01
ABM_QUERYPOS = 0x02
ABM_SETPOS = 0x03
ABM_SETAUTOHIDEBAR = 0x08
ABE_LEFT = 0
ABE_TOP = 1
ABE_RIGHT = 2
ABE_BOTTOM = 3

class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("hWnd", ctypes.wintypes.HWND),
        ("uCallbackMessage", ctypes.wintypes.UINT),
        ("uEdge", ctypes.wintypes.UINT),
        ("rc", ctypes.wintypes.RECT),
        ("lParam", ctypes.wintypes.LPARAM),
    ]

# Magenta chroma key — pixels of this exact RGB are made fully transparent
# via wm_attributes('-transparentcolor', ...) in collapsed peek mode so only
# the bunny image, accent stripe, and colored peek tabs show.
COLLAPSED_CHROMA = '#ff00ff'

# Theme registry. THEME is the live alias all UI code reads from; switching
# calls set_theme(name) which mutates THEME in place so existing references
# pick up the new colors on the next _create_ui() rebuild.
THEMES = {
    # Neon Bunny — dark grey w/ neon pink+teal accents
    'dark': {
        'bg': '#1e1e1e',
        'bg_mid': '#2d2d2d',
        'bg_light': '#3d3d3d',
        'pink': '#ff2e97',
        'pink_dark': '#cc2579',
        'pink_glow': '#ff6eb8',
        'teal': '#00f5d4',
        'teal_dark': '#00c4aa',
        'teal_glow': '#7fffd4',
        'purple': '#b537f2',
        'yellow': '#f5d300',
        'orange': '#ff6b35',
        'text': '#ffffff',
        'text_dim': '#888888',
        'border': '#ff2e97',
    },
    # Pastel Bunny — soft off-white w/ darker accents tuned for contrast on light bg
    'light': {
        'bg': '#fafafa',
        'bg_mid': '#ececec',
        'bg_light': '#d8d8d8',
        'pink': '#d6266b',
        'pink_dark': '#a01d50',
        'pink_glow': '#ff85b5',
        'teal': '#0a8b78',
        'teal_dark': '#06695a',
        'teal_glow': '#3fc4b1',
        'purple': '#8230b5',
        'yellow': '#a88300',
        'orange': '#d94e1f',
        'text': '#1a1a1a',
        'text_dim': '#666666',
        'border': '#d6266b',
    },
}

# Active theme — mutated in place by set_theme() so all THEME[...] lookups stay valid
THEME = dict(THEMES['dark'])

def set_theme(name):
    """Switch to a named theme by mutating the THEME dict in place."""
    new_theme = THEMES.get(name, THEMES['dark'])
    THEME.clear()
    THEME.update(new_theme)

# Default shortcut categories
# Number of layout slot buttons on the dock
LAYOUT_SLOTS = 4

# Icon pixel size for dock buttons. Shared by pinned apps, categories and
# layout slots so the whole strip reads as one consistent stack — these used
# to drift (pinned apps 38px vs categories 18px, which looked broken).
DOCK_ICON_SIZE_VERTICAL = 38
DOCK_ICON_SIZE_HORIZONTAL = 20

# Matching character widths for the text fallback, so a text button occupies
# roughly the same slot as an icon button.
DOCK_TEXT_WIDTH_VERTICAL = 6   # fits the default "SCRIPT" label
DOCK_TEXT_WIDTH_HORIZONTAL = 4

# Preferred starting folder for icon pickers. Falls back to the user's home
# when it doesn't exist, so this is just a convenience — not a requirement.
_ICON_DIR_CANDIDATES = (r"C:\icons",)


def _default_icon_dir():
    for candidate in _ICON_DIR_CANDIDATES:
        if Path(candidate).is_dir():
            return candidate
    return str(Path.home())


def _app_dir():
    """Folder HopperDock lives in — the .exe's folder in a frozen build,
    this script's folder when running from source."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _examples_dir():
    """Folder holding the starter scripts, as a path safe to save in a shortcut.

    Running from source that's examples/ next to the script. In a frozen
    one-file build the bundled copy lives in a _MEIPASS temp dir that is
    deleted and re-created with a different name on every launch — saving that
    path into shortcuts.json would give a dead shortcut on the second run — so
    seed a stable copy under the user's config folder and point there instead.
    """
    local = _app_dir() / 'examples'
    if not getattr(sys, 'frozen', False) and local.is_dir():
        return local

    target = CONFIG_DIR / "examples"
    bundled = Path(getattr(sys, '_MEIPASS', _app_dir())) / 'examples'
    try:
        target.mkdir(parents=True, exist_ok=True)
        if bundled.is_dir():
            for item in bundled.iterdir():
                dest = target / item.name
                if item.is_file() and not dest.exists():
                    shutil.copy2(item, dest)
    except Exception as e:
        logger.warning(f"Could not seed examples folder: {e}")
    return target


def _example_script(filename):
    """Path to a starter script, for the default Scripts category."""
    return str(_examples_dir() / filename)


# Meta/Oculus desktop tools, if the user has the runtime installed. Added to
# the starter VR category only when present — a shortcut to a file that isn't
# there is worse than no shortcut.
_OPTIONAL_VR_TOOLS = (
    # Mirror casts the headset view to a desktop window — the thing you
    # capture when streaming VR, and a pain to find by hand every session.
    ("Oculus Mirror", r"C:\Program Files\Oculus\Support\oculus-diagnostics\OculusMirror.exe"),
    ("Oculus Debug", r"C:\Program Files\Oculus\Support\oculus-diagnostics\OculusDebugTool.exe"),
)


def default_categories():
    """Starter categories for a fresh install.

    Built fresh on every call (rather than being a module-level constant that
    gets shallow-copied) so editing one install's shortcuts can never mutate
    the defaults. Every entry works on a stock Windows box: bare exe names and
    URI schemes go through ShellExecute, and the Scripts entries point at the
    bundled examples/ folder.
    """
    vr = [
        {"name": "SteamVR", "path": "steam://run/250820"},
        {"name": "Sound Settings", "path": "ms-settings:sound"},
        {"name": "Display Settings", "path": "ms-settings:display"},
    ]
    vr += [{"name": name, "path": path}
           for name, path in _OPTIONAL_VR_TOOLS if Path(path).exists()]
    vr.append({"name": "Kill Stuck Game",
               "path": _example_script("kill-stuck-vr-app.bat")})

    return [
        {"name": "VR", "icon": "VR", "color": "purple", "shortcuts": vr},
        {"name": "Audio", "icon": "MIX", "color": "teal", "shortcuts": [
            {"name": "Volume Mixer", "path": "ms-settings:apps-volume"},
            {"name": "Sound Devices", "path": "ms-settings:sound"},
        ]},
        {"name": "Apps", "icon": "APPS", "color": "orange", "shortcuts": [
            {"name": "Notepad", "path": "notepad.exe"},
            {"name": "Calculator", "path": "calc.exe"},
            {"name": "Task Manager", "path": "taskmgr.exe"},
        ]},
        {"name": "Scripts", "icon": "SCRIPT", "color": "yellow", "shortcuts": [
            {"name": "Hello Hopper", "path": _example_script("hello-hopper.bat")},
            {"name": "Scripts Folder", "path": _example_script("open-scripts-folder.bat")},
        ]},
    ]


class WindowManager:
    """Handles all Windows API interactions"""

    @staticmethod
    def get_monitors():
        """Get list of all monitors with work area (excludes taskbar)"""
        monitors = []

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            r = lprcMonitor.contents

            # MONITORINFO structure: cbSize(4) + rcMonitor(16) + rcWork(16) + dwFlags(4) = 40 bytes
            info = ctypes.create_string_buffer(40)
            info[0:4] = (40).to_bytes(4, 'little')
            ctypes.windll.user32.GetMonitorInfoA(hMonitor, info)

            # Parse work area from MONITORINFO (bytes 20-36)
            work_left = int.from_bytes(info[20:24], 'little', signed=True)
            work_top = int.from_bytes(info[24:28], 'little', signed=True)
            work_right = int.from_bytes(info[28:32], 'little', signed=True)
            work_bottom = int.from_bytes(info[32:36], 'little', signed=True)

            monitors.append({
                'handle': hMonitor,
                'left': r.left,
                'top': r.top,
                'right': r.right,
                'bottom': r.bottom,
                'width': r.right - r.left,
                'height': r.bottom - r.top,
                # Work area excludes taskbar
                'work_left': work_left,
                'work_top': work_top,
                'work_right': work_right,
                'work_bottom': work_bottom,
                'name': f"Monitor {len(monitors) + 1}"
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
        # Sort monitors left-to-right for keyboard navigation to work correctly
        monitors.sort(key=lambda m: (m['left'], m['top']))
        # Update names after sorting
        for i, m in enumerate(monitors):
            m['name'] = f"Monitor {i + 1}"
        return monitors

    # System windows to always exclude
    _EXCLUDED_TITLES = frozenset([
        'Program Manager', 'Windows Input Experience',
        'Microsoft Text Input Application', 'HopperDock',
    ])

    @staticmethod
    def get_visible_windows(include_minimized=False, move_all=False):
        """Get all windows with titles.
        include_minimized: also return minimized windows
        move_all: use broader matching to catch tool windows (for move-all-to-monitor)
        """
        windows = []

        def callback(hwnd, lParam):
            is_minimized = bool(user32.IsIconic(hwnd))

            # For non-move-all mode, skip invisible windows (but check minimized first)
            if not user32.IsWindowVisible(hwnd) and not is_minimized:
                return True
            if is_minimized and not include_minimized:
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value

                if title and title not in WindowManager._EXCLUDED_TITLES:
                    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    is_app_window = bool(ex_style & WS_EX_APPWINDOW)
                    is_tool_window = bool(ex_style & WS_EX_TOOLWINDOW)
                    owner = user32.GetWindow(hwnd, 4)  # GW_OWNER

                    # Standard taskbar algorithm
                    on_taskbar = is_app_window or (not owner and not is_tool_window)

                    # In move_all mode, also grab ownerless tool windows
                    # (catches Voicemeeter, terminal windows, etc.)
                    if move_all and not on_taskbar:
                        on_taskbar = not owner and is_tool_window

                    if on_taskbar:
                        rect = ctypes.wintypes.RECT()
                        user32.GetWindowRect(hwnd, ctypes.byref(rect))

                        windows.append({
                            'hwnd': hwnd,
                            'title': title,
                            'x': rect.left,
                            'y': rect.top,
                            'width': rect.right - rect.left,
                            'height': rect.bottom - rect.top,
                            'maximized': user32.IsZoomed(hwnd),
                            'minimized': is_minimized
                        })
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return windows

    @staticmethod
    def _send_key(vk, up=False):
        """Send a single key press or release"""
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = vk
        inp.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    @staticmethod
    def _get_window_monitor_index(hwnd, monitors):
        """Get which monitor index a window is currently on"""
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        win_center_x = (rect.left + rect.right) // 2
        win_center_y = (rect.top + rect.bottom) // 2

        for i, mon in enumerate(monitors):
            if (mon['left'] <= win_center_x < mon['right'] and
                mon['top'] <= win_center_y < mon['bottom']):
                return i
        return 0

    @staticmethod
    def move_window_with_keyboard(hwnd, target_monitor_idx, monitors):
        """Move window to target monitor using Win+Shift+Arrow keyboard simulation"""
        current_idx = WindowManager._get_window_monitor_index(hwnd, monitors)
        if current_idx == target_monitor_idx:
            return True

        # Bring window to foreground first
        user32.SetForegroundWindow(hwnd)
        ctypes.windll.kernel32.Sleep(50)

        # Calculate direction and number of moves needed
        moves_needed = target_monitor_idx - current_idx
        direction = VK_RIGHT if moves_needed > 0 else VK_LEFT
        moves_needed = abs(moves_needed)

        for _ in range(moves_needed):
            # Press Win+Shift+Arrow
            WindowManager._send_key(VK_LWIN)
            WindowManager._send_key(VK_SHIFT)
            WindowManager._send_key(direction)
            ctypes.windll.kernel32.Sleep(30)
            # Release in reverse order
            WindowManager._send_key(direction, up=True)
            WindowManager._send_key(VK_SHIFT, up=True)
            WindowManager._send_key(VK_LWIN, up=True)
            ctypes.windll.kernel32.Sleep(100)

        return True

    @staticmethod
    def move_window(hwnd, x, y, width=None, height=None, restore_max=True):
        """Move a window to specified position"""
        was_minimized = user32.IsIconic(hwnd)
        was_maximized = user32.IsZoomed(hwnd)

        if was_minimized or was_maximized:
            user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.kernel32.Sleep(100)

        if width is None or height is None:
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top

        user32.SetWindowPos(hwnd, None, x, y, width, height,
                           SWP_NOZORDER | SWP_SHOWWINDOW | SWP_FRAMECHANGED)

        if was_maximized and restore_max:
            ctypes.windll.kernel32.Sleep(50)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)

    @staticmethod
    def get_restore_placement(hwnd):
        """Return (x, y, width, height, restores_maximized) from the window's
        saved restore rect. Valid even while the window is minimized."""
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return (r.left, r.top, r.right - r.left, r.bottom - r.top,
                bool(wp.flags & WPF_RESTORETOMAXIMIZED))

    @staticmethod
    def move_all_to_monitor(monitor, monitors=None):
        """Move all windows to a specific monitor using keyboard simulation for reliability"""
        if monitors is None:
            monitors = WindowManager.get_monitors()

        # Find target monitor index
        target_idx = 0
        for i, m in enumerate(monitors):
            if m['left'] == monitor['left'] and m['top'] == monitor['top']:
                target_idx = i
                break

        windows = WindowManager.get_visible_windows(include_minimized=True, move_all=True)
        offset_x = 30
        offset_y = 30

        logger.info(f"Moving {len(windows)} windows to {monitor['name']} (index {target_idx})")
        for i, win in enumerate(windows):
            logger.debug(f"Moving window '{win['title']}' (minimized={win.get('minimized')}) using keyboard simulation")
            # Restore minimized windows first so keyboard simulation works
            if win.get('minimized'):
                user32.ShowWindow(win['hwnd'], SW_RESTORE)
                ctypes.windll.kernel32.Sleep(100)

            # First use keyboard simulation to get window to correct monitor
            WindowManager.move_window_with_keyboard(win['hwnd'], target_idx, monitors)

            # Then fine-tune position with SetWindowPos
            x = monitor['left'] + offset_x + (i % 5) * 40
            y = monitor['top'] + offset_y + (i // 5) * 40
            WindowManager.move_window(win['hwnd'], x, y, restore_max=False)

    @staticmethod
    def get_primary_monitor(monitors):
        """Get the primary monitor (typically at 0,0)"""
        for monitor in monitors:
            if monitor['left'] == 0 and monitor['top'] == 0:
                return monitor
        # Fallback to first monitor if no primary found
        return monitors[0] if monitors else None


class LayoutManager:
    """Manages saving and restoring window layouts and shortcuts"""

    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.config_dir.mkdir(exist_ok=True)
        self.layouts_file = self.config_dir / "layouts.json"
        self.settings_file = self.config_dir / "settings.json"
        self.shortcuts_file = self.config_dir / "shortcuts.json"
        self.layouts = self._load_layouts()
        self.settings = self._load_settings()
        self.categories = self._load_shortcuts()

    # Vertical is the default orientation: a right-edge strip costs the least
    # useful screen space (windows are wider than they are tall) and leaves
    # room for the category labels to be readable.
    DEFAULT_SETTINGS = {'vertical': True, 'x': None, 'y': None}

    @staticmethod
    def _load_json(path, fallback, label):
        """Read a config file, defending the user's data.

        Two things matter here. First, `utf-8-sig`: a BOM (which several
        editors and PowerShell's `-Encoding utf8` add) is not corruption, and
        must not be treated as such. Second, if the file genuinely won't
        parse, the original is copied aside before we fall back to defaults —
        otherwise the next routine save silently overwrites it and the user's
        shortcuts/layouts are gone for good.
        """
        if not path.exists():
            return fallback()
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception as e:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = path.with_name(f"{path.name}.corrupt-{stamp}")
            try:
                shutil.copy2(path, backup)
                logger.error(f"{label} could not be read ({e}). "
                             f"Original preserved at {backup}; using defaults.")
            except Exception as copy_err:
                logger.error(f"{label} could not be read ({e}) AND the rescue "
                             f"copy failed ({copy_err}); using defaults.")
            return fallback()

    @staticmethod
    def _write_json(path, data):
        """Write via a temp file + atomic replace, so an interrupted or failed
        save can never leave a truncated config behind."""
        tmp = path.with_name(f"{path.name}.tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def _load_layouts(self):
        return self._load_json(self.layouts_file, dict, "layouts.json")

    def _save_layouts(self):
        self._write_json(self.layouts_file, self.layouts)

    def _load_settings(self):
        return self._load_json(self.settings_file,
                               lambda: dict(self.DEFAULT_SETTINGS),
                               "settings.json")

    def save_settings(self, settings):
        self.settings = settings
        self._write_json(self.settings_file, settings)

    def _load_shortcuts(self):
        return self._load_json(self.shortcuts_file, default_categories,
                               "shortcuts.json")

    def save_shortcuts(self):
        self._write_json(self.shortcuts_file, self.categories)

    def save_layout(self, name):
        """Snapshot every window's geometry, including minimized ones. A
        minimized window is stored with its restore rect so the layout can put
        it back in the right place when it's un-minimized later."""
        windows = WindowManager.get_visible_windows(include_minimized=True)
        layout = []
        for win in windows:
            x, y = win['x'], win['y']
            width, height = win['width'], win['height']
            maximized = bool(win['maximized'])
            minimized = bool(win.get('minimized'))
            if minimized or maximized:
                # Live rect is either the -32000 minimized placeholder or the
                # maximized rect — neither is what we want to restore to.
                placement = WindowManager.get_restore_placement(win['hwnd'])
                if placement:
                    x, y, width, height, restores_max = placement
                    if minimized:
                        # IsZoomed is False while minimized; the placement
                        # flag is what tells us it'll come back maximized.
                        maximized = restores_max
            layout.append({
                'title': win['title'],
                'x': x,
                'y': y,
                'width': width,
                'height': height,
                'maximized': maximized,
                'minimized': minimized,
            })
        self.layouts[name] = layout
        self._save_layouts()
        return len(layout)

    def restore_layout(self, name):
        if name not in self.layouts:
            return 0
        layout = self.layouts[name]
        current_windows = WindowManager.get_visible_windows(include_minimized=True)
        restored = 0
        for saved in layout:
            for win in current_windows:
                if win['title'] == saved['title']:
                    hwnd = win['hwnd']
                    # Un-minimize first — SetWindowPos on a minimized window
                    # updates the restore rect but nothing visible happens.
                    if user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, SW_RESTORE)
                        ctypes.windll.kernel32.Sleep(60)
                    WindowManager.move_window(
                        hwnd,
                        saved['x'],
                        saved['y'],
                        saved['width'],
                        saved['height'],
                        restore_max=False
                    )
                    if saved.get('maximized'):
                        user32.ShowWindow(hwnd, SW_MAXIMIZE)
                    # Re-minimize last, so the geometry above is what it
                    # restores to. SHOWMINNOACTIVE keeps focus where it is.
                    if saved.get('minimized'):
                        user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
                    restored += 1
                    break
        return restored

    def get_layout_names(self):
        return list(self.layouts.keys())

    def delete_layout(self, name):
        if name in self.layouts:
            del self.layouts[name]
            self._save_layouts()
            return True
        return False

    # ---- per-slot display metadata (label / icon) ------------------------
    # Kept in settings.json rather than layouts.json so the layout file stays
    # a pure name->windows map and the export/import bundle is unaffected.
    def get_layout_meta(self, slot):
        return self.settings.get('layout_meta', {}).get(slot, {})

    def set_layout_meta(self, slot, **fields):
        meta = self.settings.setdefault('layout_meta', {})
        entry = meta.setdefault(slot, {})
        for key, value in fields.items():
            if value:
                entry[key] = value
            else:
                entry.pop(key, None)
        if not entry:
            meta.pop(slot, None)
        self.save_settings(self.settings)

    def layout_label(self, slot):
        """Display name for a slot — the custom label, else the slot name."""
        return self.get_layout_meta(slot).get('label') or slot


class ShortcutPopup(tk.Toplevel):
    """Chrome-style slide-out tab for shortcuts - connects to dock"""

    open_popups = {}
    popup_order = []  # Track order for stacking
    TAB_WIDTH = 200
    TAB_HEIGHT = 26
    BASE_Y_OFFSET = 10

    def __init__(self, parent, category, layout_manager, dock_x, dock_y):
        super().__init__(parent)
        # Hide the popup until it's fully built and positioned to avoid
        # the first-paint flash at (0,0).
        self.withdraw()
        self.parent = parent
        self.category = category
        self.lm = layout_manager
        self.category_name = category['name']
        self.edit_mode = False # delete X's + add btn hidden until pen toggled
        self.color = THEME.get(category.get('color', 'pink'), THEME['pink'])
        self.dock_x = dock_x
        self.base_dock_y = dock_y

        ShortcutPopup.open_popups[self.category_name] = self
        ShortcutPopup.popup_order.append(self.category_name)

        self.title(f"{category['name']}")
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(bg=THEME['bg'])

        self._create_ui()
        ShortcutPopup._reposition_all_popups()
        # Now reveal the popup atomically — built + positioned in one paint
        self.deiconify()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @classmethod
    def _reposition_all_popups(cls, parent_dock=None):
        """Reposition all open popups. Vertical dock: stack to the left of the
        dock, vertically. Horizontal dock: drop below the dock, side-by-side
        starting at the dock's left edge."""
        # Pick orientation from any popup's parent (or passed arg)
        dock = parent_dock
        if dock is None and cls.popup_order:
            first = cls.open_popups.get(cls.popup_order[0])
            dock = getattr(first, 'parent', None) if first else None
        is_vertical = bool(getattr(dock, 'vertical', True)) if dock else True

        if is_vertical:
            # Vertical dock: stack popups to the left of the dock
            current_y = cls.BASE_Y_OFFSET
            for name in cls.popup_order:
                if name in cls.open_popups:
                    popup = cls.open_popups[name]
                    popup.update_idletasks()
                    # Prefer reqheight (natural content height) so toggling
                    # edit mode doesn't leave the popup clipped at the old size
                    height = popup.winfo_reqheight() or popup.winfo_height() or cls.TAB_HEIGHT
                    try:
                        dock_x = popup.parent.winfo_x()
                    except Exception:
                        dock_x = popup.dock_x
                    width = cls.TAB_WIDTH
                    x = dock_x - width
                    popup.geometry(f"{width}x{height}+{x}+{popup.base_dock_y + current_y}")
                    current_y += height + 2
        else:
            # Horizontal dock: drop popups directly below the dock
            try:
                dock.update_idletasks()
                base_x = dock.winfo_x()
                base_y = dock.winfo_y() + dock.winfo_height()
            except Exception:
                base_x = 0
                base_y = 60
            current_x = base_x
            for name in cls.popup_order:
                if name in cls.open_popups:
                    popup = cls.open_popups[name]
                    popup.update_idletasks()
                    width = popup.winfo_reqwidth() or cls.TAB_WIDTH
                    height = popup.winfo_reqheight() or popup.winfo_height()
                    popup.geometry(f"{width}x{height}+{current_x}+{base_y}")
                    current_x += width + 2

    def _on_close(self):
        if self.category_name in ShortcutPopup.open_popups:
            del ShortcutPopup.open_popups[self.category_name]
        if self.category_name in ShortcutPopup.popup_order:
            ShortcutPopup.popup_order.remove(self.category_name)
        parent = self.parent
        self.destroy()
        ShortcutPopup._reposition_all_popups(parent)
        # Un-highlight the dock button so it reads as "closed" and can reopen
        try:
            parent._update_category_button_state(self.category_name)
        except Exception:
            pass

    def _apply_popup_rounded_corners(self, radius=8):
        """Apply rounded corners on left side (right side connects to dock)"""
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            gdi32 = ctypes.windll.gdi32
            w = self.winfo_width()
            h = self.winfo_height()
            # Extend right side past window to keep right corners sharp
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + radius + 1, h + 1, radius, radius)
            user32.SetWindowRgn(hwnd, rgn, True)
        except Exception as e:
            logger.error(f"Popup rounded corners failed: {e}")

    def _create_ui(self):
        # Suppress flicker during rebuild
        hwnd = None
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW False
        except Exception:
            hwnd = None
        try:
            for widget in self.winfo_children():
                widget.destroy()

            self._create_expanded_ui()
            self.update_idletasks()
        finally:
            if hwnd:
                try:
                    user32.SendMessageW(hwnd, 0x000B, 1, 0)  # WM_SETREDRAW True
                    user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
                except Exception:
                    pass

    def _create_expanded_ui(self):
        """Full panel with shortcuts — header click closes the popout."""
        wrapper = tk.Frame(self, bg=THEME['bg'])
        wrapper.pack(fill=tk.BOTH, expand=True)

        # Right accent strip (visual connection to dock)
        right_accent = tk.Frame(wrapper, bg=THEME['pink'], width=2)
        right_accent.pack(side=tk.RIGHT, fill=tk.Y)

        main = tk.Frame(wrapper, bg=THEME['bg'])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Clickable header tab — left part collapses, right pen toggles edit mode
        header = tk.Frame(main, bg=self.color)
        header.pack(fill=tk.X)

        icon = self.category.get('icon', '?')
        header_label = tk.Label(header, text=f" {icon}  {self.category_name}",
                               font=('Segoe UI', 9, 'bold'),
                               bg=self.color, fg=THEME['bg'], pady=5, padx=8, anchor='w',
                               cursor='hand2')
        header_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # Clicking the header closes the popout entirely — the dock's category
        # button reopens it. (It used to collapse into a slide-out tab.)
        def _on_header(e):
            self.after_idle(self._on_close)
            return "break"
        header_label.bind('<Button-1>', _on_header)

        # Pencil icon — right-justified, toggles edit mode (✓ when active)
        pen_text = "✓" if self.edit_mode else "✏"
        pen_label = tk.Label(header, text=pen_text,
                            font=('Segoe UI Emoji', 11, 'bold'),
                            bg=self.color, fg=THEME['bg'], padx=10, pady=4, cursor='hand2')
        pen_label.pack(side=tk.RIGHT)
        # after_idle defers the destroy/rebuild until tkinter finishes
        # delivering THIS click event — prevents the click from hitting the
        # window underneath when the pen widget is destroyed mid-dispatch.
        # Returning "break" stops further propagation.
        def _on_pen(e):
            self.after_idle(self._toggle_edit_mode)
            return "break"
        pen_label.bind('<Button-1>', _on_pen)

        # Content area
        content_frame = tk.Frame(main, bg=THEME['bg_mid'])
        content_frame.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(content_frame, bg=THEME['bg'], padx=10, pady=8)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Shortcuts
        shortcuts = self.category.get('shortcuts', [])
        if shortcuts:
            for shortcut in shortcuts:
                self._create_shortcut_row(inner, shortcut)
        else:
            tk.Label(inner, text="No shortcuts yet", font=('Segoe UI', 8),
                    bg=THEME['bg'], fg=THEME['text_dim']).pack(pady=10)

        # Bottom add button — only shown in edit mode
        if self.edit_mode:
            btn_frame = tk.Frame(inner, bg=THEME['bg'])
            btn_frame.pack(fill=tk.X, pady=(10, 0))
            add_btn = tk.Label(btn_frame, text="+ Add Shortcut", font=('Segoe UI', 8),
                              bg=THEME['bg_light'], fg=THEME['teal'],
                              padx=10, pady=3, cursor='hand2')
            add_btn.pack(side=tk.LEFT)
            add_btn.bind('<Button-1>', lambda e: self._add_shortcut())
            add_btn.bind('<Enter>', lambda e: add_btn.config(bg=THEME['teal'], fg=THEME['bg']))
            add_btn.bind('<Leave>', lambda e: add_btn.config(bg=THEME['bg_light'], fg=THEME['teal']))

        self.after(50, lambda: self._apply_popup_rounded_corners(8))

    def _toggle_edit_mode(self):
        # Suppress paints across the entire toggle (rebuild + resize +
        # reposition) so the popup doesn't visibly flicker.
        hwnd = None
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            user32.SendMessageW(hwnd, 0x000B, 0, 0)
        except Exception:
            hwnd = None
        try:
            self.edit_mode = not self.edit_mode
            for widget in self.winfo_children():
                widget.destroy()
            self._create_expanded_ui()
            self.geometry("")
            self.update_idletasks()
            ShortcutPopup._reposition_all_popups(self.parent)
        finally:
            if hwnd:
                try:
                    user32.SendMessageW(hwnd, 0x000B, 1, 0)
                    user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
                except Exception:
                    pass

    def _create_shortcut_row(self, parent, shortcut):
        """Create a shortcut button row. Delete X only shows in edit mode."""
        frame = tk.Frame(parent, bg=THEME['bg'])
        frame.pack(fill=tk.X, pady=1)

        btn = tk.Label(frame, text=shortcut.get('name', '?'),
                      font=('Segoe UI', 9), bg=THEME['bg_mid'],
                      fg=THEME['text'], padx=6, pady=3,
                      anchor='w', cursor='hand2')
        btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        btn.bind('<Button-1>', lambda e, s=shortcut: self._run_shortcut(s))
        btn.bind('<Enter>', lambda e, b=btn: b.config(bg=THEME['bg_light']))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=THEME['bg_mid']))

        if self.edit_mode:
            del_btn = tk.Label(frame, text="×", font=('Segoe UI', 11, 'bold'),
                              bg=THEME['bg_mid'], fg=THEME['pink_dark'],
                              padx=6, cursor='hand2')
            del_btn.pack(side=tk.RIGHT)
            del_btn.bind('<Button-1>', lambda e, s=shortcut: self._delete_shortcut(s))
            del_btn.bind('<Enter>', lambda e, b=del_btn: b.config(fg=THEME['pink']))
            del_btn.bind('<Leave>', lambda e, b=del_btn: b.config(fg=THEME['pink_dark']))

    def _run_shortcut(self, shortcut):
        path = shortcut.get('path', '')
        if path:
            try:
                _spawn(path, shortcut.get('launch_cmd', ''))
                self.parent._flash_feedback("Launched!")
            except Exception as e:
                logger.error(f"Failed to launch {path}: {e}")
                self.parent._flash_feedback("Error!")

    def _add_shortcut(self):
        file_path = filedialog.askopenfilename(
            title="Select file or app",
            filetypes=[("All files", "*.*"), ("Executables", "*.exe"),
                      ("Scripts", "*.py;*.ps1;*.bat;*.cmd")]
        )
        if file_path:
            name = simpledialog.askstring("Name", "Shortcut name:",
                                         initialvalue=Path(file_path).stem)
            if name:
                self.category['shortcuts'].append({'name': name, 'path': file_path})
                self.lm.save_shortcuts()
                self._create_ui()

    def _delete_shortcut(self, shortcut):
        if messagebox.askyesno("Delete", f"Delete '{shortcut.get('name')}'?"):
            self.category['shortcuts'].remove(shortcut)
            self.lm.save_shortcuts()
            self._create_ui()


_DOCK_BASE = TkinterDnD.Tk if _DND_AVAILABLE else tk.Tk


class HopperDock(_DOCK_BASE):
    """Main floating dock application - Cute Bunny Theme"""

    def __init__(self):
        super().__init__()
        logger.info("Initializing HopperDock UI...")

        self.wm = WindowManager()
        self.lm = LayoutManager()
        self.monitors = WindowManager.get_monitors()
        logger.info(f"Detected {len(self.monitors)} monitor(s): {[(m['width'], m['height'], m['left'], m['top']) for m in self.monitors]}")

        # Apply saved theme before any UI is built
        set_theme(self.lm.settings.get('theme', 'dark'))

        self.vertical = self.lm.settings.get('vertical', True)
        self.is_appbar = False
        self.is_pinned = False
        self.dock_expanded = True
        self.appbar_data = None
        self.pinned_monitor = None

        self._setup_window()
        # Keep the window hidden until we've placed it at the correct
        # edge — avoids the visible flash on the wrong side at startup
        self.withdraw()
        self._create_ui()
        self._make_draggable()

        # Restore position or center
        self.update_idletasks()
        saved_x = self.lm.settings.get('x')
        saved_y = self.lm.settings.get('y')

        if saved_x is not None and saved_y is not None:
            logger.info(f"Saved position: ({saved_x}, {saved_y})")
            if self._is_position_visible(saved_x, saved_y):
                logger.info("Position is visible, restoring...")
                self.geometry(f"+{saved_x}+{saved_y}")
            else:
                logger.warning("Saved position is OFF-SCREEN, moving to primary monitor")
                self._move_to_primary_monitor()
        else:
            logger.info("No saved position, moving to primary monitor")
            self._move_to_primary_monitor()

        # Restore pinned state (default: pinned collapsed)
        saved_pinned = self.lm.settings.get('pinned', True)
        saved_expanded = self.lm.settings.get('dock_expanded', False)
        def _finalize_startup():
            self._restore_pin_state(saved_pinned, saved_expanded)
            # Reveal window only after final position is set so the user
            # never sees the dock flash on the wrong edge
            self.deiconify()
            # Set up drag-drop AFTER the window is visible — DragAcceptFiles
            # and the wndproc subclass apply more reliably on a shown window
            self._setup_drag_drop()
        self.after(200, _finalize_startup)

        # Start system tray icon in background
        self.after(500, self._start_tray_icon)

    def _is_position_visible(self, x, y):
        """Check if a position is on any currently connected monitor"""
        for monitor in self.monitors:
            if (monitor['left'] <= x < monitor['right'] and
                monitor['top'] <= y < monitor['bottom']):
                return True
        return False

    def _move_to_primary_monitor(self):
        """Default starting position: vertical → right edge, horizontal →
        top center of the primary monitor."""
        primary = WindowManager.get_primary_monitor(self.monitors)
        if primary:
            self.update_idletasks()
            natural_w = self.winfo_reqwidth() or self.winfo_width() or 60
            if self.vertical:
                work_right = primary.get('work_right', primary['right'])
                x = work_right - natural_w - 5
                y = primary.get('work_top', primary['top']) + 100
            else:
                x = primary['left'] + (primary['width'] - self.winfo_width()) // 2
                y = primary.get('work_top', primary['top']) + 5
            self.geometry(f"+{x}+{y}")

    def _setup_window(self):
        self.title("HopperDock")
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        # Note: WS_EX_LAYERED (set by `-alpha < 1`) breaks OLE drag-drop
        # delivery on overrideredirect windows, so keep it fully opaque.
        self.attributes('-alpha', 1.0)
        # No border - sleek flush edge look
        self.configure(bg=THEME['bg'], highlightthickness=0)
        # Icon goes on LAST: `overrideredirect` makes Tk tear down and rebuild
        # the Win32 window, and the rebuilt one comes back with Tk's own
        # feather class icon and no WM_SETICON — so anything set earlier is
        # thrown away and the taskbar draws the feather.
        self._apply_window_icon()

    def _apply_window_icon(self):
        """Set the taskbar / alt-tab icon from the bundled .ico."""
        ico = _resolve_ico_path()
        if not ico:
            logger.warning("Window icon skipped: no .ico resolved")
            return
        try:
            self.iconbitmap(default=str(ico))
        except Exception as e:
            logger.warning(f"Window icon load failed: {e}")
        # `iconbitmap` alone doesn't stick on an overrideredirect window, so
        # push the icon onto the HWND ourselves once the window exists.
        self.after(0, lambda: self._set_win32_icon(ico))

    def _set_win32_icon(self, ico):
        """Push `ico` onto our real top-level HWND via WM_SETICON."""
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        SM_CXICON, SM_CXSMICON = 11, 49
        try:
            # A private user32 handle — setting argtypes on the shared
            # `ctypes.windll.user32` would rewrite the prototypes every other
            # call site in this file relies on.
            u32 = ctypes.WinDLL('user32')
            u32.LoadImageW.restype = ctypes.c_void_p
            u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_void_p, ctypes.c_void_p]

            # Tk's toplevel sits inside a wrapper window; the taskbar tracks
            # the outermost one.
            hwnd = self.winfo_id()
            parent = u32.GetParent(hwnd)
            while parent:
                hwnd = parent
                parent = u32.GetParent(hwnd)

            # Keep the handles alive for the life of the process — Windows
            # frees LoadImage icons when the process exits.
            self._win32_icon_handles = getattr(self, '_win32_icon_handles', [])
            for which, metric in ((ICON_BIG, SM_CXICON),
                                  (ICON_SMALL, SM_CXSMICON)):
                px = u32.GetSystemMetrics(metric)
                handle = u32.LoadImageW(None, str(ico), IMAGE_ICON, px, px,
                                        LR_LOADFROMFILE)
                if not handle:
                    handle = u32.LoadImageW(None, str(ico), IMAGE_ICON, 0, 0,
                                            LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if not handle:
                    continue
                self._win32_icon_handles.append(handle)
                u32.SendMessageW(hwnd, WM_SETICON, which, handle)
            logger.info(f"Window icon set on hwnd {hwnd} from {ico.name}")
        except Exception as e:
            logger.warning(f"WM_SETICON failed: {e}")

    def _setup_drag_drop(self):
        """Enable drag-drop. Uses tkinterdnd2 (OLE-based) when available
        because legacy WM_DROPFILES is silently dropped on overrideredirect
        windows. Falls back to WM_DROPFILES if tkinterdnd2 isn't bundled."""
        if _DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind('<<Drop>>', self._on_dnd_drop)
                self.dnd_bind('<<DropEnter>>',
                              lambda e: logger.info(f"DropEnter (data type={e.data_type}, types={e.types})"))
                self.dnd_bind('<<DropLeave>>',
                              lambda e: logger.info("DropLeave"))
                # The UI already exists by now; cover its children too.
                self._register_dnd_targets()
                logger.info("OLE drag-drop registered via tkinterdnd2 "
                            f"({self._count_dnd_targets()} widgets)")
                return
            except Exception as e:
                logger.error(f"tkinterdnd2 registration failed: {e}", exc_info=True)
        # Fallback: legacy WM_DROPFILES (won't work on overrideredirect)
        self.after(100, self._enable_drag_drop)

    def _count_dnd_targets(self, widget=None):
        """How many widgets are currently registered — logged so a silent
        'drop does nothing' is diagnosable from the log alone."""
        if widget is None:
            widget = self
        n = 1 if getattr(widget, '_hd_dnd', False) else 0
        for child in widget.winfo_children():
            if not isinstance(child, tk.Toplevel):
                n += self._count_dnd_targets(child)
        return n

    def _register_dnd_targets(self, widget=None):
        """Register the dock AND every child widget as a drop target.

        tkinterdnd2 drop targets are per-widget, and the toplevel is completely
        covered by frames, labels and buttons — so registering only the
        toplevel means a drop onto any actual visible pixel of the dock is
        never delivered, and nothing happens. Walk the tree instead.
        """
        if not _DND_AVAILABLE:
            return
        if widget is None:
            widget = self
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_dnd_drop)
            widget._hd_dnd = True
        except Exception:
            pass  # menus and a few widget classes can't be drop targets
        for child in widget.winfo_children():
            if isinstance(child, tk.Toplevel):
                continue  # popups manage themselves
            self._register_dnd_targets(child)

    def _on_dnd_drop(self, event):
        """Handler for tkinterdnd2 <<Drop>> events."""
        try:
            files = self.tk.splitlist(event.data)
            files = [f.strip('{}') for f in files if f]
            logger.info(f"OLE drop received: {files}")
            if files:
                self._add_dropped_shortcuts(files)
        except Exception as e:
            logger.error(f"OLE drop handler error: {e}", exc_info=True)

    def _enable_drag_drop(self):
        """Enable Windows drag-drop on the window"""
        try:
            # Set explicit argtypes — ctypes' default int is 32-bit, which
            # would truncate HWND on x64 and silently no-op DragAcceptFiles.
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            user32.FindWindowW.restype = ctypes.wintypes.HWND
            user32.GetParent.argtypes = [ctypes.wintypes.HWND]
            user32.GetParent.restype = ctypes.wintypes.HWND
            shell32.DragAcceptFiles.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.BOOL]
            shell32.DragAcceptFiles.restype = None

            self.update_idletasks()

            hwnd = user32.FindWindowW(None, "HopperDock")
            if not hwnd:
                hwnd = user32.GetParent(self.winfo_id())

            if not hwnd:
                logger.error("Could not get window handle for drag-drop")
                return

            self._dock_hwnd = hwnd
            logger.info(f"Got HWND for drag-drop: {hwnd}")

            shell32.DragAcceptFiles(hwnd, True)
            self._setup_drop_hook(hwnd)
            logger.info("Drag-drop enabled successfully")
        except Exception as e:
            logger.error(f"Drag-drop setup failed: {e}", exc_info=True)

    def _setup_drop_hook(self, hwnd):
        """Set up a hook to catch file drop messages"""
        # Store original window procedure
        GWLP_WNDPROC = -4
        WM_DROPFILES = 0x0233

        # Define callback type for 64-bit Windows
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong,  # return type
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM
        )

        # Get original window procedure
        GetWindowLongPtrW = user32.GetWindowLongPtrW
        GetWindowLongPtrW.restype = ctypes.c_void_p
        GetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]

        SetWindowLongPtrW = user32.SetWindowLongPtrW
        SetWindowLongPtrW.restype = ctypes.c_void_p
        SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

        CallWindowProcW = user32.CallWindowProcW
        CallWindowProcW.restype = ctypes.c_longlong
        CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]

        self._original_wndproc = GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
        logger.info(f"Original WndProc captured: {self._original_wndproc}")

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                logger.info(f"WndProc caught WM_DROPFILES, hdrop={wparam}")
                try:
                    self._handle_drop(wparam)
                except Exception as e:
                    logger.error(f"Drop handler raised: {e}", exc_info=True)
                return 0
            return CallWindowProcW(self._original_wndproc, hwnd, msg, wparam, lparam)

        # Keep reference to prevent garbage collection
        self._wndproc = WNDPROC(wndproc)
        SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(self._wndproc, ctypes.c_void_p))
        logger.info(f"WndProc subclassed on HWND {hwnd}")

    def _handle_drop(self, hdrop):
        """Handle dropped files"""
        logger.info(f"WM_DROPFILES received, hdrop={hdrop}")
        try:
            # Configure DragQueryFileW
            DragQueryFileW = shell32.DragQueryFileW
            DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_wchar_p, ctypes.c_uint]
            DragQueryFileW.restype = ctypes.c_uint

            # Get number of files dropped
            num_files = DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)

            dropped_files = []
            for i in range(num_files):
                # Get file path length
                length = DragQueryFileW(hdrop, i, None, 0)
                # Get file path
                buffer = ctypes.create_unicode_buffer(length + 1)
                DragQueryFileW(hdrop, i, buffer, length + 1)
                dropped_files.append(buffer.value)

            shell32.DragFinish(hdrop)

            # Process dropped files
            if dropped_files:
                logger.info(f"Files dropped: {dropped_files}")
                self._add_dropped_shortcuts(dropped_files)
        except Exception as e:
            logger.error(f"Drop handling error: {e}", exc_info=True)

    @staticmethod
    def _hicon_to_image(hicon):
        """Convert an HICON into a PIL RGBA image, or None."""
        from PIL import Image
        info = ICONINFO()
        if not user32.GetIconInfo(hicon, ctypes.byref(info)):
            return None
        try:
            bmp = BITMAPINFOHEADER()
            bmp.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            hdc = gdi32.CreateCompatibleDC(0)
            try:
                # Query the colour bitmap's real dimensions first
                if not gdi32.GetDIBits(hdc, info.hbmColor, 0, 0, None,
                                       ctypes.byref(bmp), DIB_RGB_COLORS):
                    return None
                w, h = bmp.biWidth, abs(bmp.biHeight)
                if not w or not h:
                    return None
                # Negative height => top-down rows, so no flip needed after
                bmp.biHeight = -h
                bmp.biBitCount = 32
                bmp.biCompression = 0
                bmp.biPlanes = 1
                buf = ctypes.create_string_buffer(w * h * 4)
                if not gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf,
                                       ctypes.byref(bmp), DIB_RGB_COLORS):
                    return None
                img = Image.frombuffer('RGBA', (w, h), buf.raw, 'raw', 'BGRA', 0, 1)
                # Icons with no alpha channel come back fully transparent;
                # fall back to the mask bitmap in that case.
                if img.getchannel('A').getextrema() == (0, 0):
                    img.putalpha(255)
                return img.copy()
            finally:
                gdi32.DeleteDC(hdc)
        finally:
            for h_bmp in (info.hbmColor, info.hbmMask):
                if h_bmp:
                    gdi32.DeleteObject(h_bmp)

    @staticmethod
    def _cache_icon(img, source_path):
        """Save an extracted icon under the user's config folder and return
        its path, so shortcuts.json references a stable file."""
        cache = CONFIG_DIR / "icons"
        cache.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(source_path).stem)[:48] or 'icon'
        dest = cache / f"{safe}.png"
        img.save(dest)
        logger.info(f"Extracted icon for {Path(source_path).name} -> {dest}")
        return str(dest)

    @staticmethod
    def _store_app_icon(path):
        """For a Store app's execution alias, return its packaged logo PNG.

        The alias is a zero-byte reparse point with no icon of its own; the
        real artwork lives in the package's Assets folder. Returns None for
        anything that isn't a Store alias.
        """
        try:
            if Path(path).stat().st_size:
                return None  # a real file — normal extraction will handle it
        except OSError:
            return None

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            str(path), 0, 3, None, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, None)
        if handle in (-1, ctypes.c_void_p(-1).value):
            return None
        try:
            buf = ctypes.create_string_buffer(16384)
            written = ctypes.wintypes.DWORD()
            if not kernel32.DeviceIoControl(
                    ctypes.c_void_p(handle), FSCTL_GET_REPARSE_POINT, None, 0,
                    buf, ctypes.sizeof(buf), ctypes.byref(written), None):
                return None
            if int.from_bytes(buf.raw[0:4], 'little') != IO_REPARSE_TAG_APPEXECLINK:
                return None
            # After the 8-byte header and a version DWORD: NUL-separated
            # UTF-16 strings — package family, app id, target exe, flags.
            parts = [s for s in buf.raw[12:written.value]
                     .decode('utf-16-le', errors='ignore').split('\x00') if s]
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

        target = next((s for s in parts if s.lower().endswith('.exe')), None)
        if not target:
            return None
        assets = Path(target).parent / 'Assets'
        if not assets.is_dir():
            return None
        # Prefer the largest unplated square logo — that's the transparent
        # artwork, without the coloured tile Windows puts behind Start icons.
        best, best_px = None, 0
        for candidate in assets.glob('*Square*Logo*.png'):
            name = candidate.name.lower()
            if 'contrast' in name:
                continue  # high-contrast variants are monochrome
            try:
                from PIL import Image
                with Image.open(candidate) as im:
                    px = im.size[0] * im.size[1]
            except Exception:
                continue
            if 'unplated' in name:
                px *= 2  # tie-break toward transparent artwork
            if px > best_px:
                best, best_px = candidate, px
        return best

    def _extract_icon_for(self, path, size=64):
        """Pull the icon Windows shows for `path` and cache it as a PNG.

        Works for .exe, .lnk, .url and anything else with a shell icon. Tries
        PrivateExtractIcons at a large size first (so a modern app's 256px
        icon isn't upscaled from 32px), then falls back to SHGetFileInfo.
        Returns a PNG path, or '' if nothing could be extracted.
        """
        try:
            from PIL import Image  # noqa: F401 - fail fast if Pillow is absent
        except Exception:
            return ''

        hicon = None
        try:
            # Store apps first — their alias has no extractable icon at all.
            packaged = self._store_app_icon(path)
            if packaged:
                img = Image.open(packaged).convert('RGBA')
                if img.size != (size, size):
                    img = img.resize((size, size), Image.LANCZOS)
                return self._cache_icon(img, path)

            # Candidate icon sources, best quality first. PrivateExtractIcons
            # reads a PE's icon resources at whatever size we ask for, but only
            # works on files that HAVE resources (.exe/.dll/.ico) — a .lnk has
            # none, so SHGFI_ICONLOCATION is asked where the real icon lives.
            candidates = []
            shfi = SHFILEINFOW()
            shell32.SHGetFileInfoW(str(path), 0, ctypes.byref(shfi),
                                   ctypes.sizeof(shfi), SHGFI_ICONLOCATION)
            if shfi.szDisplayName:
                candidates.append((shfi.szDisplayName, shfi.iIcon))
            candidates.append((str(path), 0))

            nid = ctypes.c_uint()
            for src, index in candidates:
                for want in (256, 128, 64, 48, 32):
                    h = ctypes.wintypes.HICON()
                    if user32.PrivateExtractIconsW(src, index, want, want,
                                                   ctypes.byref(h),
                                                   ctypes.byref(nid), 1, 0) and h.value:
                        hicon = h.value
                        break
                if hicon:
                    break

            # Shell fallback — the one that works for .lnk, .url and folders.
            if not hicon:
                shfi = SHFILEINFOW()
                shell32.SHGetFileInfoW(str(path), 0, ctypes.byref(shfi),
                                       ctypes.sizeof(shfi),
                                       SHGFI_ICON | SHGFI_LARGEICON)
                hicon = shfi.hIcon

            if not hicon:
                return ''

            img = self._hicon_to_image(hicon)
            if img is None:
                return ''
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            return self._cache_icon(img, path)
        except Exception as e:
            logger.warning(f"Icon extraction failed for {path}: {e}")
            return ''
        finally:
            if hicon:
                try:
                    user32.DestroyIcon(hicon)
                except Exception:
                    pass

    def _add_dropped_shortcuts(self, file_paths):
        """File → pinned app on the dock. Folder → new category whose
        shortcuts are the files inside that folder (recursing one level)."""
        if not file_paths:
            return
        apps = self._get_pinned_apps()
        added_apps = 0
        new_cats = 0
        palette = ['teal', 'pink', 'purple', 'yellow', 'orange']

        for raw_path in file_paths:
            try:
                p = Path(raw_path)
                if p.is_dir():
                    shortcuts = []
                    for child in sorted(p.iterdir()):
                        if child.is_file():
                            shortcuts.append({
                                'name': child.stem or child.name,
                                'path': str(child),
                            })
                    if not shortcuts:
                        self._flash_feedback(f"{p.name}: empty")
                        continue
                    color = palette[len(self.lm.categories) % len(palette)]
                    self.lm.categories.append({
                        'name': p.name,
                        'icon': p.name[:3].upper(),
                        'color': color,
                        'shortcuts': shortcuts,
                    })
                    new_cats += 1
                else:
                    if any(a.get('path') == str(p) for a in apps):
                        continue
                    ext = p.suffix.lower()
                    launch_cmd = "pythonw" if ext in ('.py', '.pyw') else ""
                    apps.append({
                        "name": p.stem or p.name,
                        "path": str(p),
                        # Use the app's own icon rather than dropping in a
                        # bare text label — that's what makes a dropped app
                        # look like it belongs on the dock.
                        "icon": self._extract_icon_for(p),
                        "window_title": p.stem,
                        "launch_cmd": launch_cmd,
                    })
                    added_apps += 1
            except Exception as e:
                logger.error(f"Failed to add dropped {raw_path}: {e}")

        if added_apps:
            self._save_pinned_apps(apps)
        if new_cats:
            self.lm.save_shortcuts()
        if added_apps or new_cats:
            self._create_ui()
            parts = []
            if added_apps:
                parts.append(f"{added_apps} pinned")
            if new_cats:
                parts.append(f"{new_cats} category")
            self._flash_feedback(", ".join(parts))
        else:
            self._flash_feedback("Already added")

    def _show_category_picker(self, file_paths):
        """Show dialog to pick category for dropped files"""
        picker = tk.Toplevel(self)
        picker.title("Add to Category")
        picker.overrideredirect(True)
        picker.attributes('-topmost', True)
        picker.configure(bg=THEME['bg'], highlightbackground=THEME['teal'],
                        highlightthickness=2)

        # Position near dock
        x = self.winfo_x() - 150
        y = self.winfo_y() + 50
        picker.geometry(f"+{x}+{y}")

        tk.Label(picker, text="Add to which category?",
                font=('Segoe UI', 10, 'bold'),
                bg=THEME['bg'], fg=THEME['teal']).pack(padx=15, pady=(10, 5))

        file_label = f"{len(file_paths)} file(s)" if len(file_paths) > 1 else Path(file_paths[0]).name
        tk.Label(picker, text=file_label,
                font=('Segoe UI', 8),
                bg=THEME['bg'], fg=THEME['text_dim']).pack(padx=15, pady=(0, 10))

        for category in self.lm.categories:
            color = THEME.get(category.get('color', 'pink'), THEME['pink'])
            btn = tk.Button(picker, text=category['name'],
                          font=('Segoe UI', 9, 'bold'),
                          bg=THEME['bg_mid'], fg=color,
                          activebackground=THEME['bg_light'],
                          relief=tk.FLAT, cursor='hand2', width=15,
                          command=lambda c=category, p=picker: self._add_to_category(c, file_paths, p))
            btn.pack(padx=15, pady=2)

        # Cancel button
        tk.Button(picker, text="Cancel",
                 font=('Segoe UI', 8),
                 bg=THEME['bg_light'], fg=THEME['text_dim'],
                 relief=tk.FLAT, cursor='hand2',
                 command=picker.destroy).pack(padx=15, pady=(5, 10))

    def _add_to_category(self, category, file_paths, picker):
        """Add files to the selected category"""
        for path in file_paths:
            name = Path(path).stem
            category['shortcuts'].append({'name': name, 'path': path})
        self.lm.save_shortcuts()
        picker.destroy()
        self._flash_feedback(f"Added {len(file_paths)}!")

    def _create_ui(self):
        # Suppress paints during the rebuild via WM_SETREDRAW so the dock
        # doesn't visibly tear/flash as widgets destroy and re-pack
        hwnd = self._get_hwnd()
        if hwnd:
            try:
                user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW False
            except Exception:
                hwnd = None

        try:
            self._destroy_all_tooltips()
            for widget in self.winfo_children():
                if isinstance(widget, tk.Toplevel):
                    continue
                widget.destroy()

            self._disable_collapsed_transparency()

            if self.vertical:
                self._create_vertical_ui()
            else:
                self._create_horizontal_ui()

            # Children are recreated on every rebuild, so drop targets have to
            # be re-registered with them.
            self._register_dnd_targets()
            self.update_idletasks()
        finally:
            if hwnd:
                try:
                    user32.SendMessageW(hwnd, 0x000B, 1, 0)  # WM_SETREDRAW True
                    # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
                    user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
                except Exception:
                    pass

        if self.is_pinned:
            self.after(50, lambda: self._apply_rounded_corners(12))
        else:
            self.after(50, self._clear_rounded_corners)

    def _get_hwnd(self):
        """Cached HWND of the main dock window."""
        h = getattr(self, '_cached_hwnd', None)
        if h:
            return h
        try:
            h = ctypes.windll.user32.GetParent(self.winfo_id())
            self._cached_hwnd = h
            return h
        except Exception:
            return None

    def _enable_collapsed_transparency(self):
        """Render COLLAPSED_CHROMA pixels fully transparent so only the bunny,
        accent stripe, and colored peek tabs show. Also drop the rounded
        corner region (the chroma already gives us a tight visible shape)."""
        try:
            self.attributes('-transparentcolor', COLLAPSED_CHROMA)
            self.configure(bg=COLLAPSED_CHROMA)
        except Exception as e:
            logger.warning(f"Enable collapsed transparency failed: {e}")

    def _disable_collapsed_transparency(self):
        try:
            self.attributes('-transparentcolor', '')
            self.configure(bg=THEME['bg'])
        except Exception as e:
            logger.warning(f"Disable collapsed transparency failed: {e}")

    def _create_horizontal_ui(self):
        # Main container
        main_frame = tk.Frame(self, bg=THEME['bg'], padx=8, pady=4)
        main_frame.pack(fill=tk.BOTH, expand=True)
        self._bind_drag_to(main_frame)

        # Single row: bunny | M1 M2 TILE | LAYOUTS | SHORTCUTS | pinned apps
        row = tk.Frame(main_frame, bg=THEME['bg'])
        row.pack(fill=tk.X, pady=2)
        self._bind_drag_to(row)

        # Bunny inline left
        bunny_photo = self._get_bunny_photo(32)
        if bunny_photo:
            bunny_label = tk.Label(row, image=bunny_photo, bg=THEME['bg'], cursor='hand2')
            bunny_label._photo_ref = bunny_photo
        else:
            bunny_label = tk.Label(row, text="🐰", font=('Segoe UI Emoji', 18),
                                   bg=THEME['bg'], fg=THEME['pink'], cursor='hand2')
        bunny_label.pack(side=tk.LEFT, padx=(0, 8))
        self._bind_drag_to(bunny_label)
        bunny_label.bind('<Button-3>', self._show_bunny_menu)
        self._add_tooltip(bunny_label, "Drag to move (when unpinned)  |  Right-click: menu")

        # MOVE label + monitor buttons + TILE (right of M2)
        tk.Label(row, text="MOVE", font=('Segoe UI', 7, 'bold'),
                fg=THEME['teal'], bg=THEME['bg']).pack(side=tk.LEFT, padx=(0, 4))
        for i, monitor in enumerate(self.monitors):
            btn = self._create_button(row, f"M{i+1}", THEME['teal_dark'],
                                     THEME['teal'], width=3,
                                     command=lambda m=monitor: self._move_all_to(m))
            btn.pack(side=tk.LEFT, padx=1)
            self._add_tooltip(btn, f"Move all to Monitor {i+1}\n({monitor['width']}x{monitor['height']})")

        tile_btn = self._create_button(row, "TILE", THEME['purple'],
                                       THEME['pink'], width=4,
                                       command=self._show_tile_menu)
        tile_btn.pack(side=tk.LEFT, padx=(4, 1))
        self._add_tooltip(tile_btn, "Tile windows in a grid")

        self._create_separator(row, vertical=True)

        # LAYOUTS
        tk.Label(row, text="LAYOUTS", font=('Segoe UI', 7, 'bold'),
                fg=THEME['pink'], bg=THEME['bg']).pack(side=tk.LEFT, padx=(0, 4))
        self.layout_buttons = []
        for i in range(1, LAYOUT_SLOTS + 1):
            btn = self._create_layout_button(row, i, horizontal=True)
            btn.pack(side=tk.LEFT, padx=1)

        self._create_separator(row, vertical=True)

        # Categories — compact icon buttons inline; click pops out shortcuts
        self._render_categories(row)

        self._create_separator(row, vertical=True)

        # Pinned apps
        for idx, app in enumerate(self._get_pinned_apps()):
            icon_path = app.get('icon', '')
            app_icon = self._load_app_icon(icon_path, size=self._dock_icon_size()) if icon_path else None
            if app_icon:
                app_btn = tk.Label(row, image=app_icon, bg=THEME['bg'],
                                  cursor='hand2', padx=2)
                app_btn._icon_ref = app_icon
                app_btn.pack(side=tk.LEFT, padx=(4, 0))
                app_btn.bind('<Button-1>', lambda e, a=app: self._toggle_pinned_app(a))
                app_btn.bind('<Enter>', lambda e, b=app_btn: b.config(bg=THEME['bg_light']))
                app_btn.bind('<Leave>', lambda e, b=app_btn: b.config(bg=THEME['bg']))
            else:
                app_btn = self._create_button(row, app.get('name', '?')[:3], THEME['bg_mid'],
                                             THEME['teal'], width=3,
                                             command=lambda a=app: self._toggle_pinned_app(a))
                app_btn.pack(side=tk.LEFT, padx=(4, 0))
            app_btn.bind('<Button-3>', lambda e, i=idx: self._pinned_app_context(e, i))
            self._add_tooltip(app_btn, f"Toggle {app.get('name', 'App')}")

    def _create_vertical_ui(self):
        # Outer container with accent stripe on left
        outer = tk.Frame(self, bg=THEME['bg'])
        outer.pack(fill=tk.BOTH, expand=True)
        self._bind_drag_to(outer)

        # Pink accent stripe on left edge
        accent = tk.Frame(outer, bg=THEME['pink'], width=2)
        accent.pack(side=tk.LEFT, fill=tk.Y)

        main_frame = tk.Frame(outer, bg=THEME['bg'], padx=4, pady=4)
        main_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._bind_drag_to(main_frame)

        # Bunny logo — large, centered above the bar
        bunny_photo = self._get_bunny_photo(40)
        if bunny_photo:
            bunny_label = tk.Label(main_frame, image=bunny_photo, bg=THEME['bg'], cursor='hand2')
            bunny_label._photo_ref = bunny_photo
        else:
            bunny_label = tk.Label(main_frame, text="🐰", font=('Segoe UI Emoji', 24),
                                   bg=THEME['bg'], fg=THEME['pink'], cursor='hand2')
        bunny_label.pack(pady=(2, 4))
        self._bind_drag_to(bunny_label)
        bunny_label.bind('<Button-3>', self._show_bunny_menu)
        self._add_tooltip(bunny_label, "Drag to move (when unpinned)  |  Right-click: menu")
        # Separator under the bunny
        tk.Frame(main_frame, bg=THEME['pink'], height=1).pack(fill=tk.X, padx=2, pady=(0, 4))

        # Monitor section
        for i, monitor in enumerate(self.monitors):
            btn = self._create_button(main_frame, f"M{i+1}", THEME['teal_dark'],
                                     THEME['teal'], width=3,
                                     command=lambda m=monitor: self._move_all_to(m))
            btn.pack(pady=1)
            self._add_tooltip(btn, f"Move all to Monitor {i+1}")

        sep1 = tk.Frame(main_frame, bg=THEME['pink'], height=1)
        sep1.pack(fill=tk.X, padx=1, pady=3)

        # Layouts
        self.layout_buttons = []
        for i in range(1, LAYOUT_SLOTS + 1):
            btn = self._create_layout_button(main_frame, i, horizontal=False)
            btn.pack(pady=1)

        sep2 = tk.Frame(main_frame, bg=THEME['pink'], height=1)
        sep2.pack(fill=tk.X, padx=1, pady=3)

        # Tile button
        tile_btn = self._create_button(main_frame, "TILE", THEME['purple'],
                                       THEME['pink'],
                                       width=self._dock_text_width(),
                                       command=self._show_tile_menu)
        tile_btn.pack(pady=1)
        self._add_tooltip(tile_btn, "Tile windows in a grid")

        sep_cat = tk.Frame(main_frame, bg=THEME['pink'], height=1)
        sep_cat.pack(fill=tk.X, padx=1, pady=3)

        # Inline categories — collapsible sections
        self._render_categories(main_frame)

        # Spacer to push pinned apps to bottom
        spacer = tk.Frame(main_frame, bg=THEME['bg'])
        spacer.pack(fill=tk.BOTH, expand=True)

        # Pinned apps section at bottom
        sep_pinned = tk.Frame(main_frame, bg=THEME['pink'], height=1)
        sep_pinned.pack(fill=tk.X, padx=1, pady=3)

        for idx, app in enumerate(self._get_pinned_apps()):
            icon_img = self._load_app_icon(app.get('icon', ''), self._dock_icon_size())
            if icon_img:
                btn = tk.Label(main_frame, image=icon_img, bg=THEME['bg'],
                              cursor='hand2', padx=4, pady=4)
                btn._icon_ref = icon_img
                btn.pack(pady=2)
                btn.bind('<Button-1>', lambda e, a=app: self._toggle_pinned_app(a))
                btn.bind('<Enter>', lambda e, b=btn: b.config(bg=THEME['bg_light']))
                btn.bind('<Leave>', lambda e, b=btn: b.config(bg=THEME['bg']))
            else:
                btn = self._create_button(main_frame, app.get('name', '?')[:4],
                                         THEME['bg_mid'], THEME['teal'], width=5,
                                         command=lambda a=app: self._toggle_pinned_app(a))
                btn.pack(pady=2)
            btn.bind('<Button-3>', lambda e, i=idx: self._pinned_app_context(e, i))
            self._add_tooltip(btn, app.get('name', 'App'))

        # (Bottom "—" minimize button removed — click the bunny at top to minimize.)

    def _create_pinned_tab_ui(self):
        """Collapsed dock view — orientation aware. Same Canvas-based rounded
        tabs in both modes. Vertical: column, tabs flush to right edge with
        rounded LEFT corners. Horizontal: row, tabs hang below dock with
        rounded BOTTOM corners. Background uses chroma magenta which the
        window-level transparentcolor renders fully transparent — only the
        bunny image, accent stripe, and colored peek tabs are visible."""
        bg = COLLAPSED_CHROMA
        accent_color = THEME['pink']
        outer = tk.Frame(self, bg=bg)
        outer.pack(fill=tk.BOTH, expand=True)

        if self.vertical:
            accent = tk.Frame(outer, bg=accent_color, width=2)
            accent.pack(side=tk.LEFT, fill=tk.Y)
            inner = tk.Frame(outer, bg=bg)
            inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            bunny_photo = self._get_bunny_photo(40)
            if bunny_photo:
                bunny = tk.Label(inner, image=bunny_photo, bg=bg, cursor='hand2')
                bunny._photo_ref = bunny_photo
            else:
                bunny = tk.Label(inner, text="🐰", font=('Segoe UI Emoji', 22),
                                bg=bg, fg=THEME['pink'], cursor='hand2')
            bunny.pack(pady=(4, 4), padx=4)
            bunny.bind('<Button-1>', lambda e: self._toggle_dock_visibility())
            bunny.bind('<Button-3>', self._show_bunny_menu)

            tk.Frame(inner, bg=accent_color, height=1).pack(fill=tk.X, pady=(0, 4))

            # Tabs fill full dock width (no padding) — rounded LEFT corners,
            # sharp right (flush w/ screen edge). Polygon redrawn on Configure
            # so it tracks the actual canvas width.
            TAB_H = 88
            r = 10
            text_fill = THEME['bg']
            def _redraw_v(canvas, color, label):
                canvas.delete('all')
                w = canvas.winfo_width()
                h = canvas.winfo_height() or TAB_H
                points = [
                    w, 0, w, 0, w, 0,
                    w, h, w, h, w, h,
                    r, h,
                    0, h,
                    0, h - r,
                    0, r,
                    0, 0,
                    r, 0,
                ]
                canvas.create_polygon(points, smooth=True, fill=color, outline='')
                canvas.create_text(w // 2 + 2, h // 2, text=label,
                                   angle=90, font=('Segoe UI', 9, 'bold'),
                                   fill=text_fill)
            for category in self.lm.categories:
                color = THEME.get(category.get('color', 'pink'), THEME['pink'])
                # width=1 overrides Canvas's default 378 — fill=tk.X expands to parent
                canvas = tk.Canvas(inner, bg=bg, width=1, height=TAB_H,
                                  highlightthickness=0, bd=0, cursor='hand2')
                canvas.pack(fill=tk.X, pady=0, padx=0)
                label_text = f"{category.get('icon', '?')}  {category['name']}"
                canvas.bind('<Configure>',
                            lambda e, c=canvas, col=color, lt=label_text: _redraw_v(c, col, lt))
                canvas.bind('<Button-1>',
                            lambda e, cat=category: self._expand_and_open(cat))
                self._add_tooltip(canvas, f"{category['name']} — click to open")

            for w in [outer, inner]:
                w.bind('<Button-1>', lambda e: self._toggle_dock_visibility())
                w.bind('<Button-3>', self._show_bunny_menu)
        else:
            accent = tk.Frame(outer, bg=accent_color, height=2)
            accent.pack(side=tk.BOTTOM, fill=tk.X)
            inner = tk.Frame(outer, bg=bg)
            inner.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

            bunny_photo = self._get_bunny_photo(28)
            if bunny_photo:
                bunny = tk.Label(inner, image=bunny_photo, bg=bg, cursor='hand2',
                                padx=6, pady=2)
                bunny._photo_ref = bunny_photo
            else:
                bunny = tk.Label(inner, text="🐰", font=('Segoe UI Emoji', 16),
                                bg=bg, fg=THEME['pink'], cursor='hand2',
                                padx=6, pady=2)
            bunny.pack(side=tk.LEFT)
            bunny.bind('<Button-1>', lambda e: self._toggle_dock_visibility())
            bunny.bind('<Button-3>', self._show_bunny_menu)

            # Tabs hang from top dock, rounded BOTTOM corners
            TAB_W, TAB_H = 88, 36
            r = 10
            text_fill = THEME['bg']
            def _redraw_h(canvas, color, label):
                canvas.delete('all')
                w = canvas.winfo_width() or TAB_W
                h = canvas.winfo_height() or TAB_H
                points = [
                    0, 0, 0, 0, 0, 0,
                    w, 0, w, 0, w, 0,
                    w, h - r,
                    w, h,
                    w - r, h,
                    r, h,
                    0, h,
                    0, h - r,
                ]
                canvas.create_polygon(points, smooth=True, fill=color, outline='')
                canvas.create_text(w // 2, h // 2 - 1, text=label,
                                   font=('Segoe UI', 8, 'bold'), fill=text_fill)
            for category in self.lm.categories:
                color = THEME.get(category.get('color', 'pink'), THEME['pink'])
                canvas = tk.Canvas(inner, bg=bg, width=TAB_W, height=TAB_H,
                                  highlightthickness=0, bd=0, cursor='hand2')
                canvas.pack(side=tk.LEFT, padx=0)
                label_text = f"{category.get('icon', '?')}  {category['name']}"
                canvas.bind('<Configure>',
                            lambda e, c=canvas, col=color, lt=label_text: _redraw_h(c, col, lt))
                canvas.bind('<Button-1>',
                            lambda e, cat=category: self._expand_and_open(cat))
                self._add_tooltip(canvas, f"{category['name']} — click to open")

            for w in [outer, inner]:
                w.bind('<Button-1>', lambda e: self._toggle_dock_visibility())
                w.bind('<Button-3>', self._show_bunny_menu)

    def _expand_and_open(self, category):
        """Click on a peek tab — open ONLY that category's popup. The dock
        stays collapsed; popup floats next to the peek strip."""
        self._toggle_shortcut_sidebar(category)

    def _save_sidebar_state(self):
        """Save which sidebars are open to settings"""
        open_names = list(getattr(self, '_open_sidebars', {}).keys())
        self.lm.settings['open_sidebars'] = open_names
        self.lm.settings.pop('sidebar_collapsed', None)
        self.lm.save_settings(self.lm.settings)

    def _restore_sidebar_state(self):
        """Restore sidebars that were previously open"""
        open_names = self.lm.settings.get('open_sidebars', [])
        if not open_names:
            return
        # Find matching categories and open their sidebars
        for cat_name in open_names:
            for category in self.lm.categories:
                if category['name'] == cat_name:
                    self._build_shortcut_sidebar(category)
                    break

    def _toggle_shortcut_sidebar(self, category):
        """Toggle a sidebar — multiple can be open at once"""
        self._open_sidebars = getattr(self, '_open_sidebars', {})
        cat_name = category['name']

        if cat_name in self._open_sidebars:
            # Close this one
            try:
                self._open_sidebars[cat_name].destroy()
            except:
                pass
            del self._open_sidebars[cat_name]
            self._reposition_sidebars()
            self._create_ui()
            return

        self._build_shortcut_sidebar(category)

    def _close_sidebar(self, category):
        """Close a sidebar outright — the dock button reopens it."""
        if category['name'] in getattr(self, '_open_sidebars', {}):
            self._toggle_shortcut_sidebar(category)

    def _reposition_sidebars(self):
        """Stack all open sidebars vertically, flush to left of dock"""
        self._open_sidebars = getattr(self, '_open_sidebars', {})
        self.update_idletasks()
        dock_x = self.winfo_x()
        dock_y = self.winfo_y()
        current_y = dock_y

        for cat_name in list(self._open_sidebars.keys()):
            sidebar = self._open_sidebars[cat_name]
            try:
                sidebar.update_idletasks()
                sidebar_w = sidebar.winfo_width()
                sidebar_h = sidebar.winfo_height()
                sidebar.geometry(f"+{dock_x - sidebar_w}+{current_y}")
                current_y += sidebar_h + 2
            except:
                pass

    def _build_shortcut_sidebar(self, category):
        """Build (or rebuild) the shortcut sidebar for a category. Styled to
        match horizontal popup tabs: solid category-colored header + content
        area. Clicking the header closes it — the dock button reopens it."""
        cat_name = category['name']
        self._open_sidebars = getattr(self, '_open_sidebars', {})
        if not hasattr(self, '_sidebar_edit_mode'):
            self._sidebar_edit_mode = {}
        edit_on = self._sidebar_edit_mode.get(cat_name, False)
        color = THEME.get(category.get('color', 'pink'), THEME['pink'])

        # Destroy existing sidebar if rebuilding
        if cat_name in self._open_sidebars:
            try:
                self._open_sidebars[cat_name].destroy()
            except Exception:
                pass

        sidebar = tk.Toplevel(self)
        sidebar.overrideredirect(True)
        sidebar.attributes('-topmost', True)
        sidebar.configure(bg=THEME['bg'])

        main = tk.Frame(sidebar, bg=THEME['bg'])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Colored header with category icon + name (clickable to close)
        header = tk.Frame(main, bg=color)
        header.pack(fill=tk.X)
        icon = category.get('icon', '?')
        header_label = tk.Label(header, text=f" {icon}  {cat_name}",
                               font=('Segoe UI', 9, 'bold'),
                               bg=color, fg=THEME['bg'], pady=5, padx=8,
                               anchor='w', cursor='hand2')
        header_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # after_idle so the destroy happens after tkinter finishes delivering
        # THIS click — otherwise it lands on whatever is underneath.
        def _on_header(e, c=category):
            self.after_idle(lambda: self._close_sidebar(c))
            return "break"
        header_label.bind('<Button-1>', _on_header)

        # Pencil toggle right-justified in colored header
        pen_text = "✓" if edit_on else "✏"
        pen_btn = tk.Label(header, text=pen_text,
                          font=('Segoe UI Emoji', 11, 'bold'),
                          bg=color, fg=THEME['bg'], padx=10, pady=4,
                          cursor='hand2')
        pen_btn.pack(side=tk.RIGHT)
        pen_btn.bind('<Button-1>', lambda e, c=category: self._toggle_sidebar_edit(c))

        # Content area
        content = tk.Frame(main, bg=THEME['bg_mid'])
        content.pack(fill=tk.BOTH, expand=True)
        inner = tk.Frame(content, bg=THEME['bg'], padx=10, pady=8)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        shortcuts = category.get('shortcuts', [])
        if shortcuts:
            for sc in shortcuts:
                row = tk.Frame(inner, bg=THEME['bg_mid'])
                row.pack(fill=tk.X, pady=1)
                sc_btn = tk.Label(row, text=sc.get('name', '?'),
                                 font=('Segoe UI', 9), bg=THEME['bg_mid'],
                                 fg=THEME['text'], padx=8, pady=4,
                                 anchor='w', cursor='hand2', width=18)
                sc_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
                sc_btn.bind('<Button-1>', lambda e, s=sc: self._run_sidebar_shortcut(s))
                sc_btn.bind('<Enter>', lambda e, b=sc_btn: b.config(bg=THEME['bg_light']))
                sc_btn.bind('<Leave>', lambda e, b=sc_btn: b.config(bg=THEME['bg_mid']))

                if edit_on:
                    del_btn = tk.Label(row, text="×", font=('Segoe UI', 11, 'bold'),
                                      bg=THEME['bg_mid'], fg=THEME['pink_dark'],
                                      padx=6, cursor='hand2')
                    del_btn.pack(side=tk.RIGHT)
                    del_btn.bind('<Button-1>', lambda e, s=sc, c=category: self._sidebar_delete_shortcut(s, c))
                    del_btn.bind('<Enter>', lambda e, b=del_btn: b.config(fg=THEME['pink']))
                    del_btn.bind('<Leave>', lambda e, b=del_btn: b.config(fg=THEME['pink_dark']))
        else:
            tk.Label(inner, text="No shortcuts", font=('Segoe UI', 8),
                    bg=THEME['bg'], fg=THEME['text_dim']).pack(pady=6)

        if edit_on:
            add_btn = tk.Label(inner, text="+ Add Shortcut", font=('Segoe UI', 8),
                              bg=THEME['bg_light'], fg=THEME['teal'],
                              padx=8, pady=4, cursor='hand2')
            add_btn.pack(fill=tk.X, pady=(6, 0))
            add_btn.bind('<Button-1>', lambda e, c=category: self._sidebar_add_shortcut(c))
            add_btn.bind('<Enter>', lambda e: add_btn.config(bg=THEME['teal'], fg=THEME['bg']))
            add_btn.bind('<Leave>', lambda e: add_btn.config(bg=THEME['bg_light'], fg=THEME['teal']))

        self._open_sidebars[cat_name] = sidebar
        self._reposition_sidebars()
        self._create_ui()

    def _toggle_sidebar_edit(self, category):
        """Toggle edit-mode (✕ delete buttons + add btn visibility) for a sidebar."""
        if not hasattr(self, '_sidebar_edit_mode'):
            self._sidebar_edit_mode = {}
        cat_name = category['name']
        self._sidebar_edit_mode[cat_name] = not self._sidebar_edit_mode.get(cat_name, False)
        self._build_shortcut_sidebar(category)

    def _sidebar_add_shortcut(self, category):
        """Add a shortcut from the sidebar"""
        file_path = filedialog.askopenfilename(
            title="Select file or app",
            filetypes=[("All files", "*.*"), ("Executables", "*.exe"),
                      ("Scripts", "*.py;*.ps1;*.bat;*.cmd")]
        )
        if file_path:
            name = simpledialog.askstring("Name", "Shortcut name:",
                                         initialvalue=Path(file_path).stem)
            if name:
                category['shortcuts'].append({'name': name, 'path': file_path})
                self.lm.save_shortcuts()
                self._build_shortcut_sidebar(category)

    def _sidebar_shortcut_context(self, event, shortcut, category):
        """Right-click context menu for sidebar shortcuts"""
        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['bg'],
                      font=('Segoe UI', 9), relief=tk.FLAT, bd=0)
        menu.add_command(label=f"✕ Delete '{shortcut.get('name')}'",
                        command=lambda: self._sidebar_delete_shortcut(shortcut, category))
        menu.tk_popup(event.x_root, event.y_root)

    def _sidebar_delete_shortcut(self, shortcut, category):
        """Delete a shortcut from sidebar"""
        if messagebox.askyesno("Delete", f"Delete '{shortcut.get('name')}'?"):
            category['shortcuts'].remove(shortcut)
            self.lm.save_shortcuts()
            self._build_shortcut_sidebar(category)

    def _run_sidebar_shortcut(self, shortcut):
        """Run a shortcut from the sidebar"""
        path = shortcut.get('path', '')
        if path:
            try:
                _spawn(path, shortcut.get('launch_cmd', ''))
                self._flash_feedback("Launched!")
            except Exception as e:
                logger.error(f"Failed to launch {path}: {e}")
                self._flash_feedback("Error!")

    def _update_category_button_state(self, cat_name):
        """Refresh a category header's bg/fg based on whether its popup is open."""
        btn = getattr(self, '_category_buttons', {}).get(cat_name)
        if btn is None or not btn.winfo_exists():
            return
        is_active = cat_name in ShortcutPopup.open_popups
        btn._is_active = is_active
        try:
            if is_active:
                btn.config(bg=btn._active_bg, fg=btn._active_fg)
            else:
                btn.config(bg=btn._inactive_bg, fg=btn._inactive_fg)
        except Exception:
            pass

    def _show_shortcut_popup(self, category):
        """Toggle a popup for `category`. Vertical: attaches to dock's left
        edge. Horizontal: drops down below the dock under the clicked button."""
        cat_name = category['name']
        # Toggle off if already open
        if cat_name in ShortcutPopup.open_popups:
            try:
                existing = ShortcutPopup.open_popups[cat_name]
                existing.destroy()
            except Exception:
                pass
            ShortcutPopup.open_popups.pop(cat_name, None)
            if cat_name in ShortcutPopup.popup_order:
                ShortcutPopup.popup_order.remove(cat_name)
            ShortcutPopup._reposition_all_popups(self)
            self._update_category_button_state(cat_name)
            return

        self.update_idletasks()
        dock_x = self.winfo_x()
        dock_y = self.winfo_y()
        ShortcutPopup(self, category, self.lm, dock_x, dock_y)
        self._update_category_button_state(cat_name)

    def _add_category(self):
        """Add a new shortcut category"""
        name = simpledialog.askstring("New Category", "Enter category name:")
        if name:
            icon = simpledialog.askstring("Category Icon",
                                         "Enter short icon text (3-4 chars):",
                                         initialvalue=name[:3].upper())
            if icon:
                self.lm.categories.append({
                    "name": name,
                    "icon": icon[:4],
                    "color": "teal",
                    "shortcuts": []
                })
                self.lm.save_shortcuts()
                self._create_ui()
                self._flash_feedback(f"Added {name}!")

    def _create_button(self, parent, text, bg_color, hover_color, width=3, command=None):
        btn = tk.Button(
            parent,
            text=text,
            font=('Segoe UI', 8, 'bold'),
            bg=bg_color,
            fg=THEME['text'],
            activebackground=hover_color,
            activeforeground=THEME['text'],
            relief=tk.FLAT,
            width=width,
            padx=2,
            cursor='hand2',
            command=command
        )
        btn.bind('<Enter>', lambda e, b=btn, c=hover_color: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn, c=bg_color: b.config(bg=c))
        return btn

    def _dock_icon_size(self):
        """Pixel size for icons on the dock strip, per orientation."""
        return (DOCK_ICON_SIZE_HORIZONTAL if not self.vertical
                else DOCK_ICON_SIZE_VERTICAL)

    def _dock_text_width(self):
        """Character width for text buttons on the dock strip."""
        return (DOCK_TEXT_WIDTH_HORIZONTAL if not self.vertical
                else DOCK_TEXT_WIDTH_VERTICAL)

    # ---- layout slots ----------------------------------------------------
    def _create_layout_button(self, parent, index, horizontal):
        """One layout slot button. Shows its custom icon image if set, else its
        custom name, else the slot number. Left-click restores, right-click
        opens the save/rename/icon menu."""
        slot = f"Layout {index}"
        meta = self.lm.get_layout_meta(slot)
        has_layout = slot in self.lm.get_layout_names()
        bg = THEME['pink_dark'] if has_layout else THEME['bg_light']
        hover = THEME['pink'] if has_layout else THEME['pink_dark']

        icon_img = self._load_app_icon(meta.get('icon', ''), size=self._dock_icon_size())
        common = dict(bg=bg, fg=THEME['text'], activebackground=hover,
                      activeforeground=THEME['text'], relief=tk.FLAT,
                      cursor='hand2')
        if icon_img:
            btn = tk.Button(parent, image=icon_img, bd=0, highlightthickness=0,
                            padx=2, pady=2, **common)
            btn.image = icon_img  # keep a reference alive
        else:
            label = meta.get('label', '')
            width = self._dock_text_width()
            text = label[:width] if label else str(index)
            btn = tk.Button(parent, text=text, font=('Segoe UI', 8, 'bold'),
                            width=width, padx=2, **common)

        btn.bind('<Enter>', lambda e, b=btn, c=hover: b.config(bg=c))
        btn.bind('<Leave>', lambda e, b=btn, c=bg: b.config(bg=c))
        btn.bind('<Button-1>', lambda e, n=slot: self._restore_layout(n))
        btn.bind('<Button-3>',
                 lambda e, n=slot, b=btn: self._show_layout_context(e, n, b))

        self.layout_buttons.append((btn, slot))
        count = len(self.lm.layouts.get(slot, []))
        status = f"{count} windows saved" if has_layout else "Empty"
        self._add_tooltip(btn, f"{self.lm.layout_label(slot)} ({status})\n"
                               f"Click: Restore  |  Right-click: Menu")
        return btn

    def _show_layout_context(self, event, slot, button):
        """Right-click menu on a layout slot: save / rename / icon / clear."""
        meta = self.lm.get_layout_meta(slot)
        has_layout = slot in self.lm.get_layout_names()
        m = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                    activebackground=THEME['pink'], activeforeground=THEME['bg'],
                    font=('Segoe UI', 9))
        m.add_command(label="💾 Save Layout Here",
                      command=lambda: self._save_layout(slot, button))
        m.add_separator()
        m.add_command(label="✎ Rename…",
                      command=lambda: self._rename_layout(slot))
        m.add_command(label="🖼 Set Icon…",
                      command=lambda: self._set_layout_icon(slot))
        if meta.get('icon'):
            m.add_command(label="🖼 Clear Icon",
                          command=lambda: self._set_layout_meta(slot, icon=''))
        if meta.get('label'):
            m.add_command(label="↺ Reset Name",
                          command=lambda: self._set_layout_meta(slot, label=''))
        m.add_separator()
        m.add_command(label="✕ Clear Saved Windows",
                      state=tk.NORMAL if has_layout else tk.DISABLED,
                      command=lambda: self._clear_layout(slot))
        m.tk_popup(event.x_root, event.y_root)

    def _set_layout_meta(self, slot, **fields):
        self.lm.set_layout_meta(slot, **fields)
        self._create_ui()
        if self.is_pinned:
            self._update_appbar_pos()

    def _rename_layout(self, slot):
        from tkinter import simpledialog as _sd
        current = self.lm.get_layout_meta(slot).get('label', '')
        new_name = _sd.askstring("Rename Layout",
                                 f"Display name for {slot}\n"
                                 "(blank resets to the slot number):",
                                 initialvalue=current)
        if new_name is None:
            return
        self._set_layout_meta(slot, label=new_name.strip()[:24])

    def _set_layout_icon(self, slot):
        path = filedialog.askopenfilename(
            title=f"Icon for {self.lm.layout_label(slot)}",
            initialdir=_default_icon_dir(),
            filetypes=[("Images", "*.png;*.ico;*.jpg;*.jpeg;*.gif;*.bmp"),
                       ("All files", "*.*")])
        if path:
            self._set_layout_meta(slot, icon=path)

    def _clear_layout(self, slot):
        name = self.lm.layout_label(slot)
        if messagebox.askyesno("Clear Layout",
                               f"Delete the saved windows in '{name}'?\n"
                               "Its name and icon are kept."):
            self.lm.delete_layout(slot)
            self._flash_feedback("Cleared!")
            self._create_ui()

    def _render_categories(self, parent):
        """Render compact category buttons matching the M1/M2 size.
        Each category has: icon_path (optional image file — wins over text),
        display_mode ('icon' or 'text', default 'icon'), color (theme key for
        bg), font_color (optional theme key for fg).
        Click → popup. Right-click → context menu (rename / display /
        colors / remove). Add Category lives in the bunny menu."""
        self._category_buttons = {}
        is_horizontal = not self.vertical
        # Truncate to the same width the button is drawn at, so a label never
        # renders wider than its slot and gets visually clipped.
        max_chars = self._dock_text_width()

        for category in self.lm.categories:
            cat_name = category['name']
            color = THEME.get(category.get('color', 'pink'), THEME['pink'])
            font_color = THEME.get(category.get('font_color', ''), None)
            display_mode = category.get('display_mode', 'icon')
            is_active = cat_name in ShortcutPopup.open_popups

            if display_mode == 'text':
                label_text = cat_name[:max_chars]
            else:
                label_text = (category.get('icon') or cat_name[:max_chars] or '?')[:max_chars]

            # Category buttons always wear their color — popup-open state
            # is shown via a darker shade rather than a gray inactive state.
            active_bg = color
            active_fg = font_color if font_color else THEME['bg']
            inactive_bg = color
            inactive_fg = font_color if font_color else THEME['bg']

            # Compact button — narrow enough to keep the dock slim while
            # still showing 3-4 chars of icon
            cat_w = self._dock_text_width()
            pad_x = 1 if not is_horizontal else 0
            cat_icon = self._load_app_icon(category.get('icon_path', ''),
                                           size=self._dock_icon_size())
            if cat_icon:
                header = tk.Label(parent, image=cat_icon,
                                 bg=active_bg if is_active else inactive_bg,
                                 bd=0, highlightthickness=0,
                                 padx=4, pady=4, cursor='hand2')
                header.image = cat_icon  # keep a reference alive
            else:
                header = tk.Label(parent, text=label_text,
                                 font=('Segoe UI', 8, 'bold'),
                                 bg=active_bg if is_active else inactive_bg,
                                 fg=active_fg if is_active else inactive_fg,
                                 width=cat_w,
                                 padx=pad_x, pady=3,
                                 anchor='center', cursor='hand2')
            if is_horizontal:
                header.pack(side=tk.LEFT, padx=1)
            else:
                header.pack(pady=1)

            header._inactive_bg = inactive_bg
            header._inactive_fg = inactive_fg
            header._active_bg = active_bg
            header._active_fg = active_fg
            header._is_active = is_active
            header.bind('<Button-1>',
                        lambda e, c=category: self._show_shortcut_popup(c))
            header.bind('<Button-3>',
                        lambda e, c=category: self._show_category_context(e, c))
            header.bind('<Enter>',
                        lambda e, h=header: h.config(
                            bg=h._active_bg, fg=h._active_fg))
            header.bind('<Leave>',
                        lambda e, h=header: h.config(
                            bg=h._active_bg if h._is_active else h._inactive_bg,
                            fg=h._active_fg if h._is_active else h._inactive_fg))
            self._category_buttons[cat_name] = header
            self._add_tooltip(header, f"{cat_name}\nLeft: open  |  Right: edit")

    def _toggle_category_expanded(self, cat_name):
        self._cat_expanded = getattr(self, '_cat_expanded', {})
        self._cat_expanded[cat_name] = not self._cat_expanded.get(cat_name, True)
        self._create_ui()
        if self.is_pinned:
            self._update_appbar_pos()

    def _toggle_category_edit(self, cat_name):
        self._cat_edit = getattr(self, '_cat_edit', {})
        self._cat_edit[cat_name] = not self._cat_edit.get(cat_name, False)
        self._create_ui()
        if self.is_pinned:
            self._update_appbar_pos()

    def _run_shortcut(self, shortcut):
        path = shortcut.get('path', '')
        if not path:
            return
        try:
            _spawn(path, shortcut.get('launch_cmd', ''))
            self._flash_feedback("Launched")
        except Exception as e:
            logger.error(f"Failed to launch {path}: {e}")
            self._flash_feedback("Launch failed")

    def _delete_shortcut(self, category, shortcut):
        try:
            category.get('shortcuts', []).remove(shortcut)
            self.lm.save_shortcuts()
            self._create_ui()
        except Exception as e:
            logger.error(f"Delete shortcut failed: {e}")

    def _add_shortcut_to(self, category):
        from tkinter import filedialog as _fd, simpledialog as _sd
        file_path = _fd.askopenfilename(
            title=f"Add shortcut to {category['name']}",
            filetypes=[("All files", "*.*")])
        if not file_path:
            return
        name = _sd.askstring("Name", "Shortcut name:",
                             initialvalue=Path(file_path).stem)
        if not name:
            return
        category.setdefault('shortcuts', []).append(
            {'name': name, 'path': file_path})
        self.lm.save_shortcuts()
        self._create_ui()

    def _remove_category(self, category):
        from tkinter import messagebox as _mb
        if not _mb.askyesno("Remove Category",
                            f"Remove '{category['name']}' and all its shortcuts?"):
            return
        try:
            self.lm.categories.remove(category)
            self.lm.save_shortcuts()
            self._create_ui()
        except Exception as e:
            logger.error(f"Remove category failed: {e}")

    # Theme color keys offered as palette options for categories
    _CATEGORY_PALETTE = ['pink', 'teal', 'purple', 'yellow', 'orange',
                         'pink_dark', 'teal_dark', 'pink_glow', 'teal_glow',
                         'text', 'text_dim']

    def _show_category_context(self, event, category):
        """Right-click menu on a category header: rename / display mode /
        bg color / font color / remove."""
        m = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                    activebackground=THEME['pink'], activeforeground=THEME['bg'],
                    font=('Segoe UI', 9))

        m.add_command(label=f"✎ Rename '{category['name']}'",
                      command=lambda: self._rename_category(category))

        cur_mode = category.get('display_mode', 'icon')
        next_mode = 'text' if cur_mode == 'icon' else 'icon'
        m.add_command(label=f"⇆ Show {next_mode.title()}",
                      command=lambda: self._set_category_display_mode(category, next_mode))

        m.add_command(label="✎ Edit Icon Text",
                      command=lambda: self._edit_category_icon(category))

        m.add_command(label="🖼 Set Icon Image…",
                      command=lambda: self._set_category_icon_image(category))
        if category.get('icon_path'):
            m.add_command(label="🖼 Clear Icon Image",
                          command=lambda: self._set_category_icon_image(category, ''))

        # Category background color
        bg_menu = tk.Menu(m, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                          activebackground=THEME['teal'], activeforeground=THEME['bg'],
                          font=('Segoe UI', 9))
        cur_bg = category.get('color', 'pink')
        for key in self._CATEGORY_PALETTE:
            if key not in THEME:
                continue
            mark = "● " if key == cur_bg else "   "
            bg_menu.add_command(label=f"{mark}{key}",
                                command=lambda k=key: self._set_category_color(category, k))
        m.add_cascade(label="🎨 Button Color", menu=bg_menu)

        # Font color
        fg_menu = tk.Menu(m, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                          activebackground=THEME['teal'], activeforeground=THEME['bg'],
                          font=('Segoe UI', 9))
        cur_fg = category.get('font_color', '')
        fg_menu.add_command(label=f"{'● ' if not cur_fg else '   '}auto (matches button)",
                            command=lambda: self._set_category_font_color(category, ''))
        for key in self._CATEGORY_PALETTE:
            if key not in THEME:
                continue
            mark = "● " if key == cur_fg else "   "
            fg_menu.add_command(label=f"{mark}{key}",
                                command=lambda k=key: self._set_category_font_color(category, k))
        m.add_cascade(label="🅰 Font Color", menu=fg_menu)

        m.add_separator()
        m.add_command(label="✕ Remove Category",
                      command=lambda: self._remove_category(category))

        m.tk_popup(event.x_root, event.y_root)

    def _rename_category(self, category):
        from tkinter import simpledialog as _sd
        new_name = _sd.askstring("Rename Category",
                                 "New name:",
                                 initialvalue=category.get('name', ''))
        if not new_name or new_name == category.get('name'):
            return
        # If a popup for this category is open, close it first (it indexes by name)
        old_name = category['name']
        if old_name in ShortcutPopup.open_popups:
            try:
                ShortcutPopup.open_popups[old_name].destroy()
            except Exception:
                pass
            ShortcutPopup.open_popups.pop(old_name, None)
            if old_name in ShortcutPopup.popup_order:
                ShortcutPopup.popup_order.remove(old_name)
        category['name'] = new_name
        self.lm.save_shortcuts()
        self._create_ui()

    def _edit_category_icon(self, category):
        from tkinter import simpledialog as _sd
        new_icon = _sd.askstring("Category Icon",
                                 "Short text icon (1-6 chars):",
                                 initialvalue=category.get('icon', ''))
        if new_icon is None:
            return
        category['icon'] = new_icon[:6]
        self.lm.save_shortcuts()
        self._refresh_category_button(category)

    def _set_category_icon_image(self, category, path=None):
        """Point a category at an image file (or clear it with path='').
        An image icon overrides the text/emoji icon on the dock button."""
        if path is None:
            path = filedialog.askopenfilename(
                title=f"Icon for {category['name']}",
                initialdir=_default_icon_dir(),
                filetypes=[("Images", "*.png;*.ico;*.jpg;*.jpeg;*.gif;*.bmp"),
                           ("All files", "*.*")])
            if not path:
                return
        if path:
            category['icon_path'] = path
        else:
            category.pop('icon_path', None)
        self.lm.save_shortcuts()
        # Full rebuild — swapping between an image and a text label changes
        # the widget type, which _refresh_category_button can't do in place.
        self._create_ui()
        if self.is_pinned:
            self._update_appbar_pos()

    def _set_category_display_mode(self, category, mode):
        category['display_mode'] = mode
        self.lm.save_shortcuts()
        self._refresh_category_button(category)

    def _set_category_color(self, category, color_key):
        category['color'] = color_key
        self.lm.save_shortcuts()
        self._refresh_category_button(category)

    def _set_category_font_color(self, category, color_key):
        if color_key:
            category['font_color'] = color_key
        else:
            category.pop('font_color', None)
        self.lm.save_shortcuts()
        self._refresh_category_button(category)

    def _refresh_category_button(self, category):
        """In-place update of a single category header — avoids the full
        _create_ui rebuild flicker for color/text-only changes."""
        cat_name = category['name']
        btn = getattr(self, '_category_buttons', {}).get(cat_name)
        if btn is None or not btn.winfo_exists():
            self._create_ui()
            return
        color = THEME.get(category.get('color', 'pink'), THEME['pink'])
        font_key = category.get('font_color', '')
        font_color = THEME.get(font_key, '') if font_key else None
        inactive_bg = THEME['bg_mid']
        inactive_fg = font_color if font_color else color
        active_bg = color
        active_fg = font_color if font_color else THEME['bg']
        btn._inactive_bg = inactive_bg
        btn._inactive_fg = inactive_fg
        btn._active_bg = active_bg
        btn._active_fg = active_fg

        display_mode = category.get('display_mode', 'icon')
        max_chars = self._dock_text_width()
        if display_mode == 'text':
            new_text = cat_name[:max_chars]
        else:
            new_text = (category.get('icon') or cat_name[:max_chars] or '?')[:max_chars]

        is_active = btn._is_active
        btn.config(bg=active_bg if is_active else inactive_bg,
                   fg=active_fg if is_active else inactive_fg)
        # An image button carries no text — pushing text onto it would leave
        # a stale label behind the image once the image is later cleared.
        if not category.get('icon_path'):
            btn.config(text=new_text)

    def _destroy_all_tooltips(self):
        """Kill any orphaned tooltip Toplevels (called before UI rebuilds)."""
        if not hasattr(self, '_active_tooltips'):
            self._active_tooltips = []
        for tt in self._active_tooltips:
            try:
                tt.destroy()
            except Exception:
                pass
        self._active_tooltips = []

    def _add_tooltip(self, widget, text):
        if not self.lm.settings.get('tooltips_enabled', True):
            return
        if not hasattr(self, '_active_tooltips'):
            self._active_tooltips = []
        state = {'tooltip': None}

        def show_tooltip(event=None):
            # Kill any prior tooltip from this widget first
            if state['tooltip']:
                try: state['tooltip'].destroy()
                except Exception: pass
                state['tooltip'] = None
            tooltip = tk.Toplevel(self)
            tooltip.wm_overrideredirect(True)
            tooltip.attributes('-topmost', True)

            frame = tk.Frame(tooltip, bg=THEME['pink'], padx=1, pady=1)
            frame.pack()

            label = tk.Label(frame, text=text, font=('Segoe UI', 8),
                           bg=THEME['bg'], fg=THEME['text'],
                           padx=8, pady=4, justify=tk.LEFT)
            label.pack()

            tooltip.update_idletasks()
            tip_w = tooltip.winfo_reqwidth()
            x = widget.winfo_rootx() - tip_w - 5
            y = widget.winfo_rooty()
            if x < 0:
                x = widget.winfo_rootx() + widget.winfo_width() + 5
            tooltip.wm_geometry(f"+{x}+{y}")

            state['tooltip'] = tooltip
            self._active_tooltips.append(tooltip)

        def hide_tooltip(event=None):
            if state['tooltip']:
                try:
                    self._active_tooltips.remove(state['tooltip'])
                except ValueError:
                    pass
                try:
                    state['tooltip'].destroy()
                except Exception:
                    pass
                state['tooltip'] = None

        widget.bind('<Enter>', show_tooltip, add='+')
        widget.bind('<Leave>', hide_tooltip, add='+')
        widget.bind('<Destroy>', hide_tooltip, add='+')
        widget.bind('<Unmap>', hide_tooltip, add='+')
        widget.bind('<Button-1>', hide_tooltip, add='+')
        widget.bind('<Button-3>', hide_tooltip, add='+')

    def _create_separator(self, parent, vertical=True):
        if vertical:
            sep = tk.Frame(parent, bg=THEME['pink'], width=2)
            sep.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=2)
        else:
            sep = tk.Frame(parent, bg=THEME['pink'], height=2)
            sep.pack(fill=tk.X, padx=2, pady=6)

    def _make_draggable(self):
        """Initialize drag-state. Drag handlers are also bound to specific
        widgets (bunny, outer/main frames) inside the UI builders so the
        whole dock surface acts as a drag handle when unpinned."""
        self._drag_data = {'x': 0, 'y': 0}

    def _drag_start(self, event):
        if self.is_pinned:
            return
        self._drag_data['x'] = event.x
        self._drag_data['y'] = event.y

    def _drag_move(self, event):
        if self.is_pinned:
            return
        x = self.winfo_x() + (event.x - self._drag_data['x'])
        y = self.winfo_y() + (event.y - self._drag_data['y'])
        self.geometry(f"+{x}+{y}")
        # Persist the user's chosen position so the dock comes back here
        # on next launch (taskbar-style — stays where you put it).
        self.lm.settings['x'] = x
        self.lm.settings['y'] = y
        # Throttle disk writes via after_idle
        if not getattr(self, '_drag_save_pending', False):
            self._drag_save_pending = True
            self.after(300, self._save_drag_position)

    def _save_drag_position(self):
        self._drag_save_pending = False
        try:
            self.lm.save_settings(self.lm.settings)
        except Exception as e:
            logger.error(f"Save drag position failed: {e}")

    def _bind_drag_to(self, widget):
        """Make `widget` a drag handle for the dock when unpinned."""
        widget.bind('<Button-1>', self._drag_start)
        widget.bind('<B1-Motion>', self._drag_move)

    def _toggle_orientation(self):
        """Flip orientation. If pinned, re-pin on the new edge."""
        was_pinned = self.is_pinned or self.is_appbar
        if self.is_appbar:
            self._unregister_appbar()

        self.vertical = not self.vertical

        if was_pinned:
            self.is_pinned = True
            self.dock_expanded = True
            self.geometry("")
            self._create_ui()
            self.update_idletasks()
            self._register_appbar()
            self._update_appbar_pos()
            self.lm.settings.update({
                'vertical': self.vertical,
                'pinned': True,
                'dock_expanded': True,
            })
            self.lm.save_settings(self.lm.settings)
            self._flash_feedback("Horizontal!" if not self.vertical else "Vertical!")
            return

        # Floating — recompute natural size and position
        self.is_pinned = False
        self._create_ui()
        self.update_idletasks()
        natural_w = self.winfo_reqwidth()
        natural_h = self.winfo_reqheight()
        primary = WindowManager.get_primary_monitor(self.monitors)
        if self.vertical:
            if primary:
                x = primary['work_right'] - natural_w - 5
                y = primary['work_top'] + 50
            else:
                x = self.winfo_screenwidth() - natural_w - 5
                y = 50
        else:
            if primary:
                x = primary['left'] + (primary['width'] - natural_w) // 2
                y = primary['work_top'] + 5
            else:
                x = (self.winfo_screenwidth() - natural_w) // 2
                y = 5
        self.geometry(f"{natural_w}x{natural_h}+{x}+{y}")
        self.lm.settings.update({'vertical': self.vertical, 'x': x, 'y': y})
        self.lm.save_settings(self.lm.settings)
        # Re-open popups that were open in the previous orientation
        self.after(120, lambda: self._reopen_popups(open_cat_names))
        self._flash_feedback("Horizontal!" if not self.vertical else "Vertical!")

    def _reopen_popups(self, cat_names):
        """Re-open popups for the given category names. Uses the same
        ShortcutPopup mechanism as a category-button click, so we don't
        spawn a parallel sidebar that would double up the next time a
        button is clicked."""
        if not cat_names:
            return
        for cat_name in cat_names:
            # Only reopen if not already open (defensive — the snapshot was
            # taken before the destroy pass)
            if cat_name in ShortcutPopup.open_popups:
                continue
            for category in self.lm.categories:
                if category['name'] == cat_name:
                    try:
                        self._show_shortcut_popup(category)
                    except Exception as e:
                        logger.error(f"Failed to reopen popup {cat_name}: {e}")
                    break

    def _move_all_to(self, monitor):
        WindowManager.move_all_to_monitor(monitor, self.monitors)
        self._flash_feedback("Moved!")

    def _move_dock_to_other_monitor(self):
        """Cycle the dock to the next monitor. If the dock is appbar-pinned,
        re-pin it on the destination monitor's edge (right when vertical, top
        when horizontal). With one monitor connected, no-op."""
        if not self.monitors or len(self.monitors) < 2:
            self._flash_feedback("Only one monitor")
            return

        # Find the monitor the dock currently sits on
        self.update_idletasks()
        dock_x = self.winfo_x()
        dock_y = self.winfo_y()
        current_idx = 0
        for i, m in enumerate(self.monitors):
            if (m['left'] <= dock_x < m['right'] and
                m['top'] <= dock_y < m['bottom']):
                current_idx = i
                break

        target = self.monitors[(current_idx + 1) % len(self.monitors)]
        was_pinned = self.is_pinned or self.is_appbar

        if was_pinned:
            # Unregister current appbar slot, change target monitor, re-register
            if self.is_appbar:
                self._unregister_appbar()
            self.pinned_monitor = target
            self.is_pinned = True
            # Force one geometry move into the target monitor first so the
            # appbar registration picks up the right HMONITOR
            seed_x = target['left'] + 50
            seed_y = target.get('work_top', target['top']) + 50
            self.geometry(f"+{seed_x}+{seed_y}")
            self.update_idletasks()
            self._register_appbar()
            self._update_appbar_pos()
        else:
            # Floating: place natural-size at edge of target monitor
            natural_w = self.winfo_reqwidth()
            natural_h = self.winfo_reqheight()
            if self.vertical:
                x = target.get('work_right', target['right']) - natural_w - 5
                y = target.get('work_top', target['top']) + 50
            else:
                x = target['left'] + (target['right'] - target['left'] - natural_w) // 2
                y = target.get('work_top', target['top']) + 5
            self.geometry(f"{natural_w}x{natural_h}+{x}+{y}")
            self.lm.settings.update({'vertical': self.vertical, 'x': x, 'y': y})
            self.lm.save_settings(self.lm.settings)

        self._flash_feedback(f"Monitor {(current_idx + 1) % len(self.monitors) + 1}")

    # Back-compat alias in case any older shortcut still references the old name
    _move_dock_to_main = _move_dock_to_other_monitor

    def _save_layout(self, name, button):
        count = self.lm.save_layout(name)
        try:
            button.config(bg=THEME['pink_dark'])
            button.bind('<Leave>', lambda e, b=button: b.config(bg=THEME['pink_dark']))
        except Exception:
            pass
        self._flash_feedback(f"Saved {count}")

    def _restore_layout(self, name):
        if name in self.lm.get_layout_names():
            count = self.lm.restore_layout(name)
            self._flash_feedback(f"Restored {count}")
        else:
            self._flash_feedback("Empty! R-click save")

    def _show_tile_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['text'],
                      font=('Segoe UI', 9))

        for i, monitor in enumerate(self.monitors):
            submenu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                             activebackground=THEME['teal'], activeforeground=THEME['bg'],
                             font=('Segoe UI', 9))
            submenu.add_command(label=" 2 Columns",
                              command=lambda m=monitor: self._tile_windows(m, 2, 1))
            submenu.add_command(label=" 3 Columns",
                              command=lambda m=monitor: self._tile_windows(m, 3, 1))
            submenu.add_command(label=" 2x2 Grid",
                              command=lambda m=monitor: self._tile_windows(m, 2, 2))
            submenu.add_command(label=" 3x2 Grid",
                              command=lambda m=monitor: self._tile_windows(m, 3, 2))
            menu.add_cascade(label=f" Monitor {i+1}", menu=submenu)

        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _tileable_rect(self, monitor):
        """The area tiles may occupy on `monitor` — see `_carve_dock_out`."""
        work = (monitor.get('work_left', monitor['left']),
                monitor.get('work_top', monitor['top']),
                monitor.get('work_right', monitor['right']),
                monitor.get('work_bottom', monitor['bottom']))
        try:
            d_x, d_y = self.winfo_rootx(), self.winfo_rooty()
            dock = (d_x, d_y, d_x + self.winfo_width(), d_y + self.winfo_height())
        except Exception:
            return work
        return _carve_dock_out(work, dock)

    def _tile_windows(self, monitor, cols, rows):
        windows = WindowManager.get_visible_windows()
        if not windows:
            return
        left, top, right, bottom = self._tileable_rect(monitor)
        tile_w = (right - left) // cols
        tile_h = (bottom - top) // rows
        for i, win in enumerate(windows[:cols * rows]):
            col = i % cols
            row = i // cols
            x = left + col * tile_w
            y = top + row * tile_h
            WindowManager.move_window(win['hwnd'], x, y, tile_w, tile_h, restore_max=False)
        self._flash_feedback(f"Tiled {min(len(windows), cols*rows)}")

    def _refresh_monitors(self):
        self.monitors = WindowManager.get_monitors()
        self._create_ui()
        self._flash_feedback(f"Found {len(self.monitors)}")

    def _flash_feedback(self, message):
        label = tk.Label(self, text=f" {message} ", font=('Segoe UI', 9, 'bold'),
                        bg=THEME['teal'], fg=THEME['bg'], padx=8, pady=4)
        label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.after(800, label.destroy)

    def _get_bunny_photo(self, size):
        """Return the new HopperDock bunny logo as a PhotoImage at `size`px (cached)."""
        if not hasattr(self, '_bunny_cache'):
            self._bunny_cache = {}
        if size in self._bunny_cache:
            return self._bunny_cache[size]
        try:
            logo_path = _resolve_bunny_path()
            if not logo_path:
                return None
            from PIL import Image, ImageTk
            img = Image.open(logo_path).convert('RGBA').resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._bunny_cache[size] = photo
            return photo
        except Exception as e:
            logger.warning(f"Bunny logo load failed: {e}")
            return None

    def _load_app_icon(self, icon_path_str, size=38):
        """Load an app icon from a path, with caching"""
        if not icon_path_str:
            return None
        if not hasattr(self, '_app_icon_cache'):
            self._app_icon_cache = {}
        cache_key = f"{icon_path_str}_{size}"
        if cache_key not in self._app_icon_cache:
            self._app_icon_cache[cache_key] = None
            try:
                from PIL import Image, ImageTk
                icon_path = Path(icon_path_str)
                if icon_path.exists():
                    img = Image.open(icon_path)
                    img = img.resize((size, size), Image.LANCZOS)
                    self._app_icon_cache[cache_key] = ImageTk.PhotoImage(img)
            except Exception as e:
                logger.error(f"Failed to load icon {icon_path_str}: {e}")
        return self._app_icon_cache.get(cache_key)

    def _get_pinned_apps(self):
        """Get list of pinned apps from settings. Defaults to empty for clean fresh installs."""
        return self.lm.settings.get('pinned_apps', [])

    def _save_pinned_apps(self, apps):
        """Save pinned apps to settings"""
        self.lm.settings['pinned_apps'] = apps
        self.lm.save_settings(self.lm.settings)

    def _toggle_pinned_app(self, app):
        """Toggle a pinned app — show/hide if running, launch if not"""
        window_title = app.get('window_title', '')
        path = app.get('path', '')
        launch_cmd = app.get('launch_cmd', '')

        # Try to find existing window by title
        if window_title:
            hwnd = _find_app_window(window_title)
            if hwnd:
                SW_HIDE = 0
                SW_SHOW = 5
                if user32.IsWindowVisible(hwnd):
                    user32.ShowWindow(hwnd, SW_HIDE)
                    self._flash_feedback(f"{app['name']} Hidden")
                else:
                    user32.ShowWindow(hwnd, SW_SHOW)
                    # Move window to primary monitor if it's on another screen
                    primary = WindowManager.get_primary_monitor(self.monitors)
                    if primary:
                        mon_idx = WindowManager._get_window_monitor_index(hwnd, self.monitors)
                        primary_idx = next((i for i, m in enumerate(self.monitors)
                                           if m['left'] == 0 and m['top'] == 0), 0)
                        if mon_idx != primary_idx:
                            rect = ctypes.wintypes.RECT()
                            user32.GetWindowRect(hwnd, ctypes.byref(rect))
                            win_w = rect.right - rect.left
                            win_h = rect.bottom - rect.top
                            # Center on primary monitor work area
                            x = primary['work_left'] + (primary['work_right'] - primary['work_left'] - win_w) // 2
                            y = primary['work_top'] + (primary['work_bottom'] - primary['work_top'] - win_h) // 2
                            WindowManager.move_window(hwnd, x, y, win_w, win_h, restore_max=False)
                    user32.SetForegroundWindow(hwnd)
                    self._flash_feedback(f"{app['name']} Shown")
                return

        # Not running — launch it
        if path:
            try:
                _spawn(path, launch_cmd)
                self._flash_feedback(f"{app['name']} Launched")
            except Exception as e:
                logger.error(f"Failed to launch {app['name']}: {e}")
                self._flash_feedback("Error!")

    def _add_pinned_app(self):
        """Add a new pinned app via file picker"""
        file_path = filedialog.askopenfilename(
            title="Select app to pin",
            filetypes=[("All files", "*.*"), ("Executables", "*.exe"),
                      ("Scripts", "*.py;*.pyw;*.ps1;*.bat;*.cmd")]
        )
        if not file_path:
            return
        name = simpledialog.askstring("Name", "App name:",
                                     initialvalue=Path(file_path).stem)
        if not name:
            return

        icon_path = filedialog.askopenfilename(
            title="Select icon (optional — cancel to skip)",
            filetypes=[("Images", "*.png;*.ico;*.jpg"), ("All files", "*.*")]
        )

        window_title = simpledialog.askstring("Window Title",
            "Window title for show/hide (optional — cancel to skip):",
            initialvalue=name)

        launch_cmd = ""
        if file_path.endswith(('.py', '.pyw')):
            launch_cmd = "pythonw"

        apps = self._get_pinned_apps()
        apps.append({
            "name": name,
            "path": file_path,
            "icon": icon_path or "",
            "window_title": window_title or "",
            "launch_cmd": launch_cmd
        })
        self._save_pinned_apps(apps)
        self._app_icon_cache = {}  # clear cache
        self._create_ui()
        self._flash_feedback(f"Pinned {name}!")

    def _remove_pinned_app(self):
        """Show menu to remove a pinned app"""
        apps = self._get_pinned_apps()
        if not apps:
            self._flash_feedback("No pinned apps")
            return

        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['bg'],
                      font=('Segoe UI', 9), relief=tk.FLAT, bd=0)
        for i, app in enumerate(apps):
            menu.add_command(label=f"✕ {app['name']}",
                           command=lambda idx=i: self._do_remove_pinned_app(idx))
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _do_remove_pinned_app(self, index):
        apps = self._get_pinned_apps()
        if 0 <= index < len(apps):
            removed = apps.pop(index)
            self._save_pinned_apps(apps)
            self._app_icon_cache = {}
            self._create_ui()
            self._flash_feedback(f"Removed {removed['name']}")

    def _edit_pinned_app(self):
        """Show menu to pick a pinned app to edit"""
        apps = self._get_pinned_apps()
        if not apps:
            self._flash_feedback("No pinned apps")
            return
        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['bg'],
                      font=('Segoe UI', 9), relief=tk.FLAT, bd=0)
        for i, app in enumerate(apps):
            menu.add_command(label=f"✎ {app['name']}",
                           command=lambda idx=i: self._show_edit_pinned_dialog(idx))
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _pinned_app_context(self, event, index):
        """Right-click context menu for a specific pinned app icon"""
        apps = self._get_pinned_apps()
        if not (0 <= index < len(apps)):
            return
        app = apps[index]
        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['bg'],
                      font=('Segoe UI', 9), relief=tk.FLAT, bd=0)
        menu.add_command(label=f"✎ Edit '{app.get('name', '?')}'",
                        command=lambda: self._show_edit_pinned_dialog(index))
        menu.add_separator()
        menu.add_command(label=f"✕ Remove '{app.get('name', '?')}'",
                        command=lambda: self._remove_pinned_app_at(index))
        menu.tk_popup(event.x_root, event.y_root)

    def _remove_pinned_app_at(self, index):
        """Remove a pinned app at a specific index, with confirmation"""
        apps = self._get_pinned_apps()
        if not (0 <= index < len(apps)):
            return
        name = apps[index].get('name', '?')
        if messagebox.askyesno("Remove Pinned App", f"Remove '{name}' from pinned apps?"):
            removed = apps.pop(index)
            self._save_pinned_apps(apps)
            self._app_icon_cache = {}
            self._create_ui()
            self._flash_feedback(f"Removed {removed.get('name', 'app')}")

    def _show_edit_pinned_dialog(self, index):
        """Show dialog to edit a pinned app's properties"""
        apps = self._get_pinned_apps()
        if not (0 <= index < len(apps)):
            return
        app = apps[index]

        dlg = tk.Toplevel(self)
        dlg.title(f"Edit: {app['name']}")
        dlg.configure(bg=THEME['bg'])
        dlg.resizable(False, False)
        dlg.attributes('-topmost', True)
        dlg.grab_set()

        pad = {'padx': 10, 'pady': 4}

        # Name
        tk.Label(dlg, text="Name:", bg=THEME['bg'], fg=THEME['text'],
                font=('Segoe UI', 10)).grid(row=0, column=0, sticky='w', **pad)
        name_var = tk.StringVar(value=app.get('name', ''))
        tk.Entry(dlg, textvariable=name_var, bg=THEME['bg_mid'], fg=THEME['text'],
                insertbackground=THEME['text'], font=('Segoe UI', 10),
                width=30).grid(row=0, column=1, columnspan=2, **pad)

        # Path
        tk.Label(dlg, text="Path:", bg=THEME['bg'], fg=THEME['text'],
                font=('Segoe UI', 10)).grid(row=1, column=0, sticky='w', **pad)
        path_var = tk.StringVar(value=app.get('path', ''))
        tk.Entry(dlg, textvariable=path_var, bg=THEME['bg_mid'], fg=THEME['text'],
                insertbackground=THEME['text'], font=('Segoe UI', 10),
                width=30).grid(row=1, column=1, **pad)
        tk.Button(dlg, text="...", bg=THEME['bg_light'], fg=THEME['text'],
                 font=('Segoe UI', 9), width=3,
                 command=lambda: self._browse_file(path_var, "Select app")
                 ).grid(row=1, column=2, **pad)

        # Icon
        tk.Label(dlg, text="Icon:", bg=THEME['bg'], fg=THEME['text'],
                font=('Segoe UI', 10)).grid(row=2, column=0, sticky='w', **pad)
        icon_var = tk.StringVar(value=app.get('icon', ''))
        tk.Entry(dlg, textvariable=icon_var, bg=THEME['bg_mid'], fg=THEME['text'],
                insertbackground=THEME['text'], font=('Segoe UI', 10),
                width=30).grid(row=2, column=1, **pad)
        tk.Button(dlg, text="...", bg=THEME['bg_light'], fg=THEME['text'],
                 font=('Segoe UI', 9), width=3,
                 command=lambda: self._browse_file(icon_var, "Select icon",
                     [("Images", "*.png;*.ico;*.jpg"), ("All files", "*.*")])
                 ).grid(row=2, column=2, **pad)

        # Window Title
        tk.Label(dlg, text="Window Title:", bg=THEME['bg'], fg=THEME['text'],
                font=('Segoe UI', 10)).grid(row=3, column=0, sticky='w', **pad)
        title_var = tk.StringVar(value=app.get('window_title', ''))
        tk.Entry(dlg, textvariable=title_var, bg=THEME['bg_mid'], fg=THEME['text'],
                insertbackground=THEME['text'], font=('Segoe UI', 10),
                width=30).grid(row=3, column=1, columnspan=2, **pad)

        # Launch Command
        tk.Label(dlg, text="Launch Cmd:", bg=THEME['bg'], fg=THEME['text'],
                font=('Segoe UI', 10)).grid(row=4, column=0, sticky='w', **pad)
        cmd_var = tk.StringVar(value=app.get('launch_cmd', ''))
        tk.Entry(dlg, textvariable=cmd_var, bg=THEME['bg_mid'], fg=THEME['text'],
                insertbackground=THEME['text'], font=('Segoe UI', 10),
                width=30).grid(row=4, column=1, columnspan=2, **pad)

        # Reorder buttons
        order_frame = tk.Frame(dlg, bg=THEME['bg'])
        order_frame.grid(row=5, column=0, columnspan=3, pady=8)

        tk.Button(order_frame, text="▲ Move Up", bg=THEME['bg_light'], fg=THEME['teal'],
                 font=('Segoe UI', 9),
                 command=lambda: self._move_pinned_app(index, -1, dlg)
                 ).pack(side=tk.LEFT, padx=6)
        tk.Button(order_frame, text="▼ Move Down", bg=THEME['bg_light'], fg=THEME['teal'],
                 font=('Segoe UI', 9),
                 command=lambda: self._move_pinned_app(index, 1, dlg)
                 ).pack(side=tk.LEFT, padx=6)

        # Save / Cancel
        btn_frame = tk.Frame(dlg, bg=THEME['bg'])
        btn_frame.grid(row=6, column=0, columnspan=3, pady=10)

        def save():
            app['name'] = name_var.get() or app['name']
            app['path'] = path_var.get()
            app['icon'] = icon_var.get()
            app['window_title'] = title_var.get()
            app['launch_cmd'] = cmd_var.get()
            self._save_pinned_apps(apps)
            self._app_icon_cache = {}
            self._create_ui()
            self._flash_feedback(f"Updated {app['name']}")
            dlg.destroy()

        tk.Button(btn_frame, text="Save", bg=THEME['pink'], fg=THEME['text'],
                 font=('Segoe UI', 10, 'bold'), width=10,
                 command=save).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_frame, text="Cancel", bg=THEME['bg_light'], fg=THEME['text'],
                 font=('Segoe UI', 10), width=10,
                 command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _browse_file(self, string_var, title, filetypes=None):
        """Open file dialog and set the result into a StringVar"""
        if filetypes is None:
            filetypes = [("All files", "*.*"), ("Executables", "*.exe"),
                        ("Scripts", "*.py;*.pyw;*.ps1;*.bat;*.cmd")]
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            string_var.set(path)

    def _move_pinned_app(self, index, direction, dlg):
        """Move a pinned app up or down in the list"""
        apps = self._get_pinned_apps()
        new_index = index + direction
        if not (0 <= new_index < len(apps)):
            return
        apps[index], apps[new_index] = apps[new_index], apps[index]
        self._save_pinned_apps(apps)
        self._app_icon_cache = {}
        self._create_ui()
        dlg.destroy()
        self._show_edit_pinned_dialog(new_index)

    # ---- system tray ----------------------------------------------------
    def _start_tray_icon(self):
        """Spawn a pystray system-tray icon in a background thread."""
        try:
            import pystray
            from PIL import Image
            import threading
        except Exception as e:
            logger.warning(f"Tray import failed: {e}")
            return
        if getattr(self, '_tray_icon', None) is not None:
            return
        logo_path = _resolve_logo_path()
        if not logo_path:
            logger.warning("Tray icon skipped: no logo resolved")
            return
        try:
            icon_img = Image.open(logo_path)
        except Exception as e:
            logger.warning(f"Tray icon image load failed: {e}")
            return

        def show_hide(icon, item):
            def _toggle_window():
                try:
                    if self.state() == 'withdrawn':
                        self.deiconify()
                    else:
                        self.withdraw()
                except Exception:
                    self.deiconify()
            self.after(0, _toggle_window)

        def toggle_login(icon, item):
            self.after(0, self._toggle_startup_on_login)

        def toggle_pin(icon, item):
            self.after(0, self._toggle_appbar)

        def quit_dock(icon, item):
            try:
                icon.stop()
            except Exception:
                pass
            self.after(0, self._close_app)

        menu = pystray.Menu(
            pystray.MenuItem('Show / Hide HopperDock', show_hide, default=True),
            pystray.MenuItem(
                'Start on Login',
                toggle_login,
                checked=lambda item: self._is_startup_on_login(),
            ),
            pystray.MenuItem(
                'Pin to Edge',
                toggle_pin,
                checked=lambda item: bool(getattr(self, 'is_pinned', False)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f'HopperDock v{__version__}', None, enabled=False),
            pystray.MenuItem('Quit', quit_dock),
        )
        try:
            self._tray_icon = pystray.Icon('HopperDock', icon_img, 'HopperDock', menu)
            threading.Thread(target=self._tray_icon.run, daemon=True).start()
            logger.info("System tray icon running")
        except Exception as e:
            logger.exception(f"Tray icon start failed: {e}")
            self._tray_icon = None

    def _stop_tray_icon(self):
        if getattr(self, '_tray_icon', None) is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    # ---- startup-on-login -----------------------------------------------
    _RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
    _RUN_VALUE = 'HopperDock'

    def _startup_target(self):
        """Path to register for autostart — frozen exe path or pythonw + script."""
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        return f'pythonw "{Path(__file__).resolve()}"'

    def _is_startup_on_login(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_READ) as k:
                winreg.QueryValueEx(k, self._RUN_VALUE)
                return True
        except (FileNotFoundError, OSError):
            return False

    def _toggle_startup_on_login(self):
        import winreg
        if self._is_startup_on_login():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, self._RUN_VALUE)
                self._flash_feedback("Won't start on login")
                logger.info("Removed Run key entry")
            except OSError as e:
                logger.exception(f"Remove Run key failed: {e}")
                messagebox.showerror("Start on Login", str(e))
        else:
            target = self._startup_target()
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, self._RUN_VALUE, 0, winreg.REG_SZ, target)
                self._flash_feedback("Will start on login")
                logger.info(f"Set Run key: {target}")
            except OSError as e:
                logger.exception(f"Set Run key failed: {e}")
                messagebox.showerror("Start on Login", str(e))

    def _toggle_tooltips(self):
        cur = self.lm.settings.get('tooltips_enabled', True)
        self.lm.settings['tooltips_enabled'] = not cur
        self.lm.save_settings(self.lm.settings)
        self._destroy_all_tooltips()
        self._create_ui()
        self._flash_feedback("Tooltips off" if cur else "Tooltips on")

    def _switch_theme(self, name):
        """Switch active theme, persist, and rebuild UI"""
        if name not in THEMES:
            return
        set_theme(name)
        self.lm.settings['theme'] = name
        self.lm.save_settings(self.lm.settings)
        self._app_icon_cache = {}
        self.configure(bg=THEME['bg'])
        self._create_ui()
        self._flash_feedback(f"Theme: {name.title()}")

    def _export_config(self):
        """Export settings + shortcuts + layouts to a single JSON bundle"""
        path = filedialog.asksaveasfilename(
            title="Export HopperDock config",
            defaultextension=".json",
            initialfile=f"hopperdock-config-{datetime.now().strftime('%Y%m%d')}.json",
            filetypes=[("HopperDock Config", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        bundle = {
            "app": "HopperDock",
            "version": __version__,
            "exported_at": datetime.now().isoformat(timespec='seconds'),
            "settings": self.lm.settings,
            "shortcuts": self.lm.categories,
            "layouts": self.lm.layouts,
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(bundle, f, indent=2)
            self._flash_feedback("Exported!")
            logger.info(f"Config exported to {path}")
        except Exception as e:
            logger.exception(f"Export failed: {e}")
            messagebox.showerror("Export failed", str(e))

    def _import_config(self):
        """Import a previously-exported config bundle. Asks before overwriting."""
        path = filedialog.askopenfilename(
            title="Import HopperDock config",
            filetypes=[("HopperDock Config", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                bundle = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed", f"Could not read file:\n{e}")
            return
        if not isinstance(bundle, dict) or bundle.get('app') != 'HopperDock':
            messagebox.showerror("Import failed", "This file isn't a HopperDock config bundle.")
            return
        if not messagebox.askyesno("Import config",
                f"Replace current settings, shortcuts, and layouts with the bundle from\n"
                f"{bundle.get('exported_at', 'unknown')} (v{bundle.get('version', '?')})?\n\n"
                "Your current config will be overwritten."):
            return
        try:
            if 'settings' in bundle:
                self.lm.settings = bundle['settings']
                self.lm.save_settings(self.lm.settings)
            if 'shortcuts' in bundle:
                self.lm.categories = bundle['shortcuts']
                self.lm.save_shortcuts()
            if 'layouts' in bundle:
                self.lm.layouts = bundle['layouts']
                self.lm._save_layouts()
            # Reapply theme + rebuild UI
            set_theme(self.lm.settings.get('theme', 'dark'))
            self.configure(bg=THEME['bg'])
            self._app_icon_cache = {}
            self._create_ui()
            self._flash_feedback("Imported!")
            logger.info(f"Config imported from {path}")
        except Exception as e:
            logger.exception(f"Import failed: {e}")
            messagebox.showerror("Import failed", str(e))

    def _show_bunny_menu(self, event):
        """Show settings menu when bunny is clicked"""
        menu = tk.Menu(self, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                      activebackground=THEME['pink'], activeforeground=THEME['bg'],
                      font=('Segoe UI', 9), relief=tk.FLAT, bd=0)

        # Orientation toggle
        orient_text = "⇄ Switch to Horizontal" if self.vertical else "⇅ Switch to Vertical"
        menu.add_command(label=orient_text, command=self._toggle_orientation)

        menu.add_separator()

        # Pin/Unpin
        pin_text = "📌 Unpin from Edge" if self.is_pinned else "📌 Pin to Edge"
        menu.add_command(label=pin_text, command=self._toggle_appbar)

        # Home
        menu.add_command(label="🖥 Move Dock to Other Monitor", command=self._move_dock_to_other_monitor)

        # Refresh monitors
        menu.add_command(label="🔄 Refresh Monitors", command=self._refresh_monitors)

        menu.add_separator()

        # Pinned apps submenu
        pinned_menu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                             activebackground=THEME['teal'], activeforeground=THEME['bg'],
                             font=('Segoe UI', 9))
        pinned_menu.add_command(label="+ Add Pinned App", command=self._add_pinned_app)
        pinned_menu.add_command(label="✎ Edit Pinned App", command=self._edit_pinned_app)
        pinned_menu.add_command(label="- Remove Pinned App", command=self._remove_pinned_app)
        menu.add_cascade(label="📌 Pinned Apps", menu=pinned_menu)

        # Categories submenu
        cats_menu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                           activebackground=THEME['teal'], activeforeground=THEME['bg'],
                           font=('Segoe UI', 9))
        cats_menu.add_command(label="+ Add Category", command=self._add_category)
        menu.add_cascade(label="📂 Categories", menu=cats_menu)

        menu.add_separator()

        # Theme submenu
        theme_menu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                            activebackground=THEME['teal'], activeforeground=THEME['bg'],
                            font=('Segoe UI', 9))
        active_theme = self.lm.settings.get('theme', 'dark')
        theme_menu.add_command(
            label=f"{'● ' if active_theme == 'dark' else '   '}🐰 Neon Bunny (Dark)",
            command=lambda: self._switch_theme('dark'))
        theme_menu.add_command(
            label=f"{'● ' if active_theme == 'light' else '   '}🌸 Pastel Bunny (Light)",
            command=lambda: self._switch_theme('light'))
        menu.add_cascade(label="🎨 Themes", menu=theme_menu)

        menu.add_separator()

        # Start on login toggle
        startup_text = ("✓ Start on Login" if self._is_startup_on_login()
                        else "   Start on Login")
        menu.add_command(label=startup_text, command=self._toggle_startup_on_login)

        # Tooltip toggle
        tooltips_on = self.lm.settings.get('tooltips_enabled', True)
        tooltips_text = "✓ Tooltips" if tooltips_on else "   Tooltips"
        menu.add_command(label=tooltips_text, command=self._toggle_tooltips)

        menu.add_separator()

        # Config import/export
        config_menu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                             activebackground=THEME['teal'], activeforeground=THEME['bg'],
                             font=('Segoe UI', 9))
        config_menu.add_command(label="📤 Export…", command=self._export_config)
        config_menu.add_command(label="📥 Import…", command=self._import_config)
        menu.add_cascade(label="⚙ Config", menu=config_menu)

        menu.add_separator()

        # Help / about
        help_menu = tk.Menu(menu, tearoff=0, bg=THEME['bg'], fg=THEME['text'],
                            activebackground=THEME['teal'], activeforeground=THEME['bg'],
                            font=('Segoe UI', 9))
        help_menu.add_command(label=f"HopperDock v{__version__}", state=tk.DISABLED)
        help_menu.add_separator()
        help_menu.add_command(label="⬆ Check for Updates…",
                              command=self._check_for_updates)
        help_menu.add_command(label="📖 Guide",
                              command=lambda: self._open_url(GUIDE_URL))
        help_menu.add_command(label="☕ Support HopperDock",
                              command=lambda: self._open_url(KOFI_URL))
        help_menu.add_command(label="📁 Open Config Folder",
                              command=self._open_config_folder)
        menu.add_cascade(label="❔ Help", menu=help_menu)

        menu.add_separator()

        # Quit
        menu.add_command(label="✕ Quit HopperDock", command=self._close_app)

        # Show menu at cursor
        menu.tk_popup(event.x_root, event.y_root)

    # ---- help / updates --------------------------------------------------
    def _open_url(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"Could not open {url}: {e}")
            self._flash_feedback("Error!")

    def _open_config_folder(self):
        try:
            os.startfile(str(CONFIG_DIR))
        except Exception as e:
            logger.error(f"Could not open config folder: {e}")
            self._flash_feedback("Error!")

    def _check_for_updates(self):
        """Ask GitHub for the latest release and offer to open it.

        The download is deliberately *not* automated: a running .exe can't
        overwrite itself on Windows, so self-updating means shipping a second
        helper process to do the swap after exit — more moving parts than a
        35 MB single file is worth. Config lives outside the app, so upgrading
        really is just replacing the file.
        """
        self._flash_feedback("Checking…")
        import threading

        def work():
            try:
                import urllib.request
                req = urllib.request.Request(
                    RELEASE_API_URL,
                    headers={'Accept': 'application/vnd.github+json',
                             'User-Agent': f'HopperDock/{__version__}'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.load(resp)
                latest = str(data.get('tag_name', '')).lstrip('vV')
                url = data.get('html_url') or RELEASES_URL
            except Exception as e:
                logger.warning(f"Update check failed: {e}")
                self.after(0, lambda: self._update_check_done(None, None, str(e)))
                return
            self.after(0, lambda: self._update_check_done(latest, url, None))

        threading.Thread(target=work, daemon=True).start()

    def _update_check_done(self, latest, url, error):
        if error:
            messagebox.showwarning(
                "Check for Updates",
                "Couldn't reach GitHub to check for updates.\n\n"
                f"{error}\n\nYou can always look at:\n{RELEASES_URL}")
            return
        if _version_tuple(latest) > _version_tuple(__version__):
            if messagebox.askyesno(
                    "Update Available",
                    f"HopperDock {latest} is out — you're on {__version__}.\n\n"
                    "Open the download page?\n\n"
                    "Upgrading is just replacing HopperDock.exe (or re-running "
                    "the installer). Your settings, shortcuts and layouts live "
                    "in your config folder and are left alone."):
                self._open_url(url)
        else:
            messagebox.showinfo(
                "Up to Date",
                f"You're on the latest version ({__version__}).")

    def _apply_rounded_corners(self, radius=12):
        """Apply rounded corners on left side only (right is flush with screen edge)"""
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            gdi32 = ctypes.windll.gdi32
            w = self.winfo_width()
            h = self.winfo_height()
            # Extend right side past window bounds so right corners stay sharp
            rgn = gdi32.CreateRoundRectRgn(0, 0, w + radius + 1, h + 1, radius, radius)
            user32.SetWindowRgn(hwnd, rgn, True)
        except Exception as e:
            logger.error(f"Rounded corners failed: {e}")

    def _clear_rounded_corners(self):
        """Remove rounded corner window region"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            user32.SetWindowRgn(hwnd, 0, True)
        except:
            pass

    def _close_all_popups(self):
        """Close all open shortcut popups and sidebars"""
        for name in list(ShortcutPopup.open_popups.keys()):
            try:
                ShortcutPopup.open_popups[name].destroy()
            except:
                pass
        ShortcutPopup.open_popups.clear()
        ShortcutPopup.popup_order.clear()
        # Close sidebars too
        for name in list(getattr(self, '_open_sidebars', {}).keys()):
            try:
                self._open_sidebars[name].destroy()
            except:
                pass
        if hasattr(self, '_open_sidebars'):
            self._open_sidebars.clear()

    def _position_collapsed_tab(self):
        """Position the collapsed dock to fill the same rect the AppBar has
        reserved on the desktop. The chroma-transparent background hides the
        empty parts; only the bunny + colored peek tabs render visibly."""
        # Prefer the appbar's reserved rect — guarantees the visible peek
        # strip lines up exactly with the reserved desktop area
        if self.is_appbar and self.appbar_data:
            rc = self.appbar_data.rc
            tab_w = rc.right - rc.left
            tab_h = rc.bottom - rc.top
            x = rc.left
            y = rc.top
            self.geometry(f"{tab_w}x{tab_h}+{x}+{y}")
            return

        monitor = self.pinned_monitor
        if not monitor:
            monitor = WindowManager.get_primary_monitor(self.monitors)
        if not monitor:
            monitor = {'work_right': 1920, 'work_top': 0,
                       'left': 0, 'right': 1920, 'top': 0, 'bottom': 1080}

        m_left = monitor.get('left', 0)
        m_right = monitor.get('right', 1920)
        m_top = monitor.get('top', 0)
        work_top = monitor.get('work_top', m_top)
        work_bottom = monitor.get('work_bottom', monitor.get('bottom', 1080))
        work_right = monitor.get('work_right', m_right)

        self.update_idletasks()
        if self.vertical:
            tab_w = max(self.winfo_reqwidth(), 60)
            tab_h = work_bottom - work_top
            x = work_right - tab_w
            y = work_top
        else:
            tab_w = m_right - m_left
            tab_h = max(self.winfo_reqheight(), 40)
            x = m_left
            y = m_top
        self.geometry(f"{tab_w}x{tab_h}+{x}+{y}")

    def _position_expanded_dock(self):
        """Position expanded dock flush against right edge of monitor"""
        self.update_idletasks()

        monitor = self.pinned_monitor
        if not monitor:
            monitor = WindowManager.get_primary_monitor(self.monitors)
        if not monitor:
            monitor = {'work_right': 1920, 'work_top': 0, 'work_bottom': 1040}

        work_right = monitor.get('work_right', monitor.get('right', 1920))
        work_top = monitor.get('work_top', monitor.get('top', 0))
        work_bottom = monitor.get('work_bottom', monitor.get('bottom', 1080))
        work_height = work_bottom - work_top

        width = self.winfo_width()
        x = work_right - width
        self.geometry(f"{width}x{work_height}+{x}+{work_top}")

    def _toggle_dock_visibility(self):
        """No-op. The collapse-to-peek-strip feature was removed; the dock
        stays fully expanded while pinned."""
        return

    def _register_appbar(self):
        """Register as an AppBar to reserve screen space"""
        if self.is_appbar:
            return

        logger.info("Registering as AppBar...")
        self.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())

        self.appbar_data = APPBARDATA()
        self.appbar_data.cbSize = ctypes.sizeof(APPBARDATA)
        self.appbar_data.hWnd = hwnd

        # Register the appbar
        result = shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(self.appbar_data))
        if result:
            self.is_appbar = True
            logger.info("AppBar registered successfully")
            # Re-affirm DragAcceptFiles — appbar registration sometimes
            # resets DnD acceptance on the window.
            try:
                shell32.DragAcceptFiles(hwnd, True)
            except Exception as e:
                logger.warning(f"Re-enable DragAcceptFiles after appbar failed: {e}")
        else:
            logger.warning("AppBar registration returned no result")
            self._update_appbar_pos()

    def _unregister_appbar(self):
        """Unregister the AppBar to release screen space"""
        if not self.is_appbar or not self.appbar_data:
            return

        logger.info("Unregistering AppBar...")
        shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self.appbar_data))
        self.is_appbar = False
        self.appbar_data = None
        logger.info("AppBar unregistered")

    def _update_appbar_pos(self):
        """Update AppBar position. Pins to RIGHT edge in vertical mode, TOP edge
        in horizontal mode. Uses target monitor (self.pinned_monitor or where
        the dock currently sits)."""
        if not self.is_appbar or not self.appbar_data:
            return

        self.update_idletasks()
        # Use REQUESTED size from layout, not the current window size — the
        # latter still carries the previous orientation's dimensions when
        # called right after a `_create_ui()` rebuild (which causes the dock
        # to span the full screen on orientation switch).
        width = self.winfo_reqwidth() or self.winfo_width()
        height = self.winfo_reqheight() or self.winfo_height()

        # Prefer an explicitly-set target monitor (e.g. from "Move to Other Monitor"),
        # otherwise pick by current dock position, otherwise primary, otherwise default.
        target = getattr(self, 'pinned_monitor', None)
        if not target:
            dock_x = self.winfo_x()
            dock_y = self.winfo_y()
            for monitor in self.monitors:
                if (monitor['left'] <= dock_x < monitor['right'] and
                    monitor['top'] <= dock_y < monitor['bottom']):
                    target = monitor
                    break
        if not target:
            target = WindowManager.get_primary_monitor(self.monitors)
        if not target:
            target = {
                'left': 0, 'top': 0, 'right': 1920, 'bottom': 1080,
                'work_left': 0, 'work_top': 0, 'work_right': 1920, 'work_bottom': 1040
            }
        self.pinned_monitor = target

        m_left = target['left']
        m_top = target['top']
        m_right = target['right']
        work_top = target.get('work_top', m_top)
        work_bottom = target.get('work_bottom', target['bottom'])
        work_right = target.get('work_right', m_right)
        work_height = work_bottom - work_top
        m_width = m_right - m_left

        logger.info(f"AppBar target monitor: left={m_left} top={m_top} right={m_right} "
                    f"work_top={work_top} work_bottom={work_bottom} vertical={self.vertical}")

        if self.vertical:
            # Pin flush to the MONITOR's right edge, spanning the work area
            # vertically. Deliberately uses m_right (physical edge) rather than
            # work_right: work_right already has our own reserved strip
            # subtracted from it, so measuring from it walks the dock one
            # width further inward on every re-pin.
            self.appbar_data.uEdge = ABE_RIGHT
            self.appbar_data.rc.left = m_right - width
            self.appbar_data.rc.top = work_top
            self.appbar_data.rc.right = m_right
            self.appbar_data.rc.bottom = work_bottom
            shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(self.appbar_data))
            # QUERYPOS shoves us inward if anything else claims the edge —
            # including a ghost registration left behind by an instance that
            # was killed without unregistering. Re-assert flush-to-edge before
            # committing, otherwise "Pin to Edge" lands short of the edge.
            self.appbar_data.rc.left = m_right - width
            self.appbar_data.rc.top = work_top
            self.appbar_data.rc.right = m_right
            self.appbar_data.rc.bottom = work_bottom
            shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(self.appbar_data))
            bar_h = self.appbar_data.rc.bottom - self.appbar_data.rc.top or work_height
            self.geometry(f"{width}x{bar_h}+{m_right - width}+{self.appbar_data.rc.top}")
            # Flush the geometry request NOW. Left pending, it loses a race
            # with the shell's own post-SETPOS repositioning and the dock
            # settles at its pre-pin spot instead — which is why re-pinning
            # after an unpin landed short of the edge and too far down.
            self.update_idletasks()
        else:
            # Pin to top edge, full monitor width.
            # Use REQUESTED height (natural content height) — current winfo_height()
            # may still be the tall vertical-mode size and would reserve the whole
            # screen as appbar real estate.
            natural_h = self.winfo_reqheight()
            if natural_h < 30:  # safety floor for tk's idle pre-layout state
                natural_h = 56
            # Resize first so subsequent appbar query uses the correct height
            self.geometry(f"{m_width}x{natural_h}+{m_left}+{work_top}")
            self.update_idletasks()
            # work_top, not m_top: it already sits below a top-mounted Windows
            # taskbar, and unlike work_right it isn't shrunk by our own strip
            # (a top appbar reserves vertically, and we span horizontally).
            self.appbar_data.uEdge = ABE_TOP
            self.appbar_data.rc.left = m_left
            self.appbar_data.rc.top = work_top
            self.appbar_data.rc.right = m_right
            self.appbar_data.rc.bottom = work_top + natural_h
            shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(self.appbar_data))
            # Same re-assert as the vertical branch — see the comment there.
            self.appbar_data.rc.left = m_left
            self.appbar_data.rc.top = work_top
            self.appbar_data.rc.right = m_right
            self.appbar_data.rc.bottom = work_top + natural_h
            shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(self.appbar_data))
            bar_h = self.appbar_data.rc.bottom - self.appbar_data.rc.top or natural_h
            self.geometry(f"{m_width}x{bar_h}+{m_left}+{work_top}")
            self.update_idletasks()  # see the vertical branch — same race

    def _restore_pin_state(self, pinned, expanded):
        """Restore pinned state on startup. Dock always renders fully
        expanded (peek-collapse feature was removed)."""
        if not pinned:
            return  # Stay floating

        self.is_pinned = True
        self.dock_expanded = True
        # Re-pin on the monitor the dock was last on, not blindly on the
        # primary — otherwise restarting yanks a second-monitor dock back to
        # monitor 1.
        self.pinned_monitor = None
        saved_x = self.lm.settings.get('x')
        saved_y = self.lm.settings.get('y')
        if saved_x is not None and saved_y is not None:
            for monitor in self.monitors:
                if (monitor['left'] <= saved_x < monitor['right'] and
                        monitor['top'] <= saved_y < monitor['bottom']):
                    self.pinned_monitor = monitor
                    break
        if not self.pinned_monitor:
            self.pinned_monitor = WindowManager.get_primary_monitor(self.monitors)

        self.geometry("")
        self._create_ui()
        self.update_idletasks()
        self._register_appbar()
        self._update_appbar_pos()

        logger.info(f"Restored pin state: pinned={pinned}")

        if expanded:
            self.after(500, self._restore_sidebar_state)

    def _toggle_appbar(self):
        """Toggle pin mode on/off. Respects current orientation: vertical pins
        to right edge (collapsible peek tab), horizontal pins to top edge as a
        full-width bar."""
        if self.is_pinned:
            # Unpin - go back to floating
            if self.is_appbar:
                self._unregister_appbar()
            self._clear_rounded_corners()
            self._close_all_popups()
            self.is_pinned = False
            self.dock_expanded = True
            self.geometry("")
            self._create_ui()
            self.update_idletasks()
            self._move_to_primary_monitor()
            self.lm.settings['pinned'] = False
            self.lm.save_settings(self.lm.settings)
            self._flash_feedback("Floating")
        else:
            # Pin — preserve current orientation
            self.is_pinned = True
            self.pinned_monitor = None
            dock_x, dock_y = self.winfo_x(), self.winfo_y()
            for monitor in self.monitors:
                if (monitor['left'] <= dock_x < monitor['right'] and
                    monitor['top'] <= dock_y < monitor['bottom']):
                    self.pinned_monitor = monitor
                    break
            if not self.pinned_monitor:
                self.pinned_monitor = WindowManager.get_primary_monitor(self.monitors)

            # Phase 1: build expanded UI, register appbar at expanded size
            self.dock_expanded = True
            self.geometry("")
            self._create_ui()
            self.update_idletasks()
            self._register_appbar()
            self._update_appbar_pos()

            self.lm.settings['pinned'] = True
            self.lm.save_settings(self.lm.settings)
            self._flash_feedback("Pinned!")

        # Update settings
        self.lm.settings['appbar'] = self.is_pinned
        self.lm.settings['vertical'] = self.vertical
        self.lm.settings['pinned'] = self.is_pinned
        self.lm.settings['dock_expanded'] = self.dock_expanded
        self.lm.save_settings(self.lm.settings)

    def _close_app(self):
        # Save sidebar state before closing
        self._save_sidebar_state()
        # Stop tray icon
        self._stop_tray_icon()
        # Unregister appbar before closing
        if self.is_appbar:
            self._unregister_appbar()
        self._close_all_popups()

        self.lm.settings.update({
            'vertical': self.vertical,
            'x': self.winfo_x(),
            'y': self.winfo_y(),
            'appbar': False,
            'pinned': self.is_pinned,
            'dock_expanded': self.dock_expanded,
        })
        self.lm.save_settings(self.lm.settings)
        self.quit()


# Logo asset filenames, most-preferred first. Both live alongside this script
# (and get bundled into _MEIPASS in a frozen build) so installs on other
# machines stay self-contained — never read from C:\icons at runtime.
_LOGO_NAMES = (
    'hopper-dock square logo with background.png',
    'hopper-dock square logo.png',
)
# The in-dock bunny sits on the themed dock background, so it wants the
# transparent cut-out — the "with background" plate would show as a square.
_BUNNY_NAMES = (
    'hopper-dock square logo.png',
    'hopper-dock square logo with background.png',
)
_ICO_NAMES = (
    'hopper-dock square logo with background.ico',
    'hopper.ico',
)


def _resolve_asset(names):
    """Return the first existing asset from `names`, or None."""
    base = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(__file__).parent
    for name in names:
        p = base / name
        if p.exists():
            return p
    return None


def _resolve_logo_path():
    """Bitmap logo for the splash screen and tray icon."""
    return _resolve_asset(_LOGO_NAMES)


def _resolve_bunny_path():
    """Transparent bunny for the dock itself."""
    return _resolve_asset(_BUNNY_NAMES)


def _resolve_ico_path():
    """Windows .ico for the taskbar / alt-tab / window title icon."""
    return _resolve_asset(_ICO_NAMES)


def show_splash(duration_ms=3000):
    """Show a borderless splash screen for ~duration_ms then return."""
    try:
        root = tk.Tk()
    except Exception:
        return
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    bg, accent, text, dim = '#1e1e1e', '#ff2e97', '#ffffff', '#888888'
    root.configure(bg=bg)

    # Pink border frame
    border = tk.Frame(root, bg=accent, padx=2, pady=2)
    border.pack(fill='both', expand=True)
    inner = tk.Frame(border, bg=bg, padx=24, pady=20)
    inner.pack(fill='both', expand=True)

    logo_path = _resolve_logo_path()
    if logo_path is not None:
        try:
            from PIL import Image, ImageTk
            img = Image.open(logo_path).convert('RGBA')
            img.thumbnail((180, 180), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            logo_label = tk.Label(inner, image=photo, bg=bg)
            logo_label.image = photo  # keep reference
            logo_label.pack(pady=(0, 10))
        except Exception as e:
            logger.warning(f"Splash logo load failed: {e}")

    tk.Label(inner, text='HopperDock', bg=bg, fg=accent,
             font=('Segoe UI', 18, 'bold')).pack()
    tk.Label(inner, text=f'v{__version__}  —  Loading…', bg=bg, fg=text,
             font=('Segoe UI', 10)).pack(pady=(4, 2))
    tk.Label(inner, text='courtesy of @scarylasers', bg=bg, fg=dim,
             font=('Segoe UI', 8, 'italic')).pack(pady=(2, 0))

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')

    root.after(duration_ms, root.destroy)
    root.mainloop()


def main():
    # Tell the taskbar this process belongs to a specific AUMID — must be set
    # BEFORE any window is created. This lets pinned-shortcut indicators link
    # to the running window when its EXE matches the shortcut's AUMID.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ScaryLasers.HopperDock.Bunny.1"
        )
    except Exception as _aumid_err:
        logger.warning(f"AUMID set failed: {_aumid_err}")

    # Single-instance gate. Hold the mutex handle for the lifetime of main().
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    ERROR_ALREADY_EXISTS = 183
    SW_RESTORE = 9
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\HopperDockSingleInstance_v1")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        logger.info("Another HopperDock instance is already running; surfacing existing window and exiting")
        existing = user32.FindWindowW(None, "HopperDock")
        if existing:
            user32.ShowWindow(existing, SW_RESTORE)
            user32.SetForegroundWindow(existing)
        if _instance_mutex:
            kernel32.CloseHandle(_instance_mutex)
        sys.exit(0)

    try:
        show_splash()
        logger.info("Initializing HopperDock application")
        app = HopperDock()
        logger.info("HopperDock initialized successfully, starting main loop")
        app.mainloop()
        logger.info("HopperDock closed normally")
    except Exception as e:
        logger.exception(f"Fatal error in HopperDock: {e}")
        raise


if __name__ == '__main__':
    main()
