import sys
import ctypes
from ctypes import wintypes


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


# Accent States
ACCENT_DISABLED = 0
ACCENT_ENABLE_GRADIENT = 1
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19

# DWM Window Attributes (Windows 11 / 10 20H1+)
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_SYSTEMBACKDROP_TYPE = 38

# DWM Window Corner Preferences
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3

# DWM System Backdrops
DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2       # Mica
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
DWMSBT_TABBEDWINDOW = 4     # Mica Alt

# Win32 COLORREF (0x00BBGGRR) matching exact dialog chrome surfaces
CAPTION_COLORREF_DARK = 0x00181311   # #111318
CAPTION_COLORREF_OLED = 0x000A0807   # #07080a
CAPTION_COLORREF_LIGHT = 0x00F8F5F3  # #f3f5f8

TEXT_COLORREF_DARK = 0x00F2F0EE      # #eef0f2
TEXT_COLORREF_LIGHT = 0x002A170F     # #0f172a


def apply_win11_dwm_chrome(hwnd: int, theme: str = "dark") -> bool:
    """Apply Windows 11 immersive dark/light mode, rounded corners, and caption/text colors to HWND."""
    if not hwnd or sys.platform != "win32":
        return False
    dwmapi = getattr(ctypes.windll, "dwmapi", None)
    if dwmapi is None:
        return False

    key = (theme or "dark").strip().lower()
    is_light = key == "light"
    dark_flag = ctypes.c_int(0 if is_light else 1)
    corner_pref = ctypes.c_int(DWMWCP_ROUND)

    if is_light:
        caption_color = ctypes.c_uint(CAPTION_COLORREF_LIGHT)
        text_color = ctypes.c_uint(TEXT_COLORREF_LIGHT)
    elif key == "oled":
        caption_color = ctypes.c_uint(CAPTION_COLORREF_OLED)
        text_color = ctypes.c_uint(TEXT_COLORREF_DARK)
    else:
        caption_color = ctypes.c_uint(CAPTION_COLORREF_DARK)
        text_color = ctypes.c_uint(TEXT_COLORREF_DARK)

    h_wnd = wintypes.HWND(int(hwnd))
    applied = False
    for attr, val in (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, dark_flag),
        (DWMWA_WINDOW_CORNER_PREFERENCE, corner_pref),
        (DWMWA_CAPTION_COLOR, caption_color),
        (DWMWA_TEXT_COLOR, text_color),
    ):
        try:
            res = dwmapi.DwmSetWindowAttribute(
                h_wnd,
                ctypes.c_int(attr),
                ctypes.byref(val),
                ctypes.sizeof(val),
            )
            if res == 0:
                applied = True
        except Exception:
            pass
    return applied


def apply_win11_window_chrome(widget, theme: str = "dark") -> bool:
    """Apply Win11 immersive DWM title bar chrome directly to a QWidget / QDialog."""
    if widget is None or sys.platform != "win32":
        return False
    try:
        if not widget.isWindow():
            return False
        hwnd = int(widget.internalWinId())
        if not hwnd:
            return False
        resolved_theme = theme
        if not isinstance(resolved_theme, str) or not resolved_theme.strip():
            prop = widget.property("theme")
            resolved_theme = prop if isinstance(prop, str) and prop.strip() else "dark"
    except Exception:
        return False
    return apply_win11_dwm_chrome(hwnd, resolved_theme)


def apply_blur(hwnd, is_dark=True, opacity=0.85):
    """
    Applies Windows Acrylic / Blur Behind to a window handle.
    Falls back gracefully if not supported.
    """
    try:
        dwmapi = ctypes.windll.dwmapi
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)  # Acrylic
        res = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop),
        )
        if res == 0:
            return True
    except Exception:
        pass

    try:
        user32 = ctypes.windll.user32
        SetWindowCompositionAttribute = getattr(user32, "SetWindowCompositionAttribute", None)
        if not SetWindowCompositionAttribute:
            return False

        # Gradient color in ABGR format
        alpha = int(255 * (1.0 - opacity))
        if is_dark:
            color = (alpha << 24) | (0x20 << 16) | (0x20 << 8) | 0x20
        else:
            color = (alpha << 24) | (0xF0 << 16) | (0xF0 << 8) | 0xF0

        policy = ACCENT_POLICY()
        policy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        policy.AccentFlags = 2
        policy.GradientColor = color

        data = WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)

        ret = SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        if ret:
            return True

        policy.AccentState = ACCENT_ENABLE_BLURBEHIND
        return bool(SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except Exception as e:
        print("apply_blur failed:", e)
        return False


def remove_blur(hwnd: int) -> bool:
    """
    Safely disables any native Windows Acrylic or Blur Behind on the window handle,
    restoring clean transparent layered rendering without DWM background fallback artifacts.
    """
    if not hwnd:
        return False
    try:
        dwmapi = getattr(ctypes.windll, "dwmapi", None)
        if dwmapi:
            backdrop = ctypes.c_int(DWMSBT_NONE)
            dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(backdrop),
                ctypes.sizeof(backdrop),
            )
    except Exception:
        pass

    try:
        user32 = getattr(ctypes.windll, "user32", None)
        if user32:
            SetWindowCompositionAttribute = getattr(user32, "SetWindowCompositionAttribute", None)
            if SetWindowCompositionAttribute:
                policy = ACCENT_POLICY()
                policy.AccentState = ACCENT_DISABLED
                policy.AccentFlags = 0
                policy.GradientColor = 0
                data = WINCOMPATTRDATA()
                data.Attribute = WCA_ACCENT_POLICY
                data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
                data.SizeOfData = ctypes.sizeof(policy)
                SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
                return True
    except Exception:
        pass
    return True


sys.modules.setdefault("acrylic_utils", sys.modules[__name__])
sys.modules.setdefault("stacky.ui.acrylic_utils", sys.modules[__name__])
