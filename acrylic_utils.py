import sys
import ctypes
from ctypes import wintypes

class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int)
    ]

class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t)
    ]

# Accent States
ACCENT_DISABLED = 0
ACCENT_ENABLE_GRADIENT = 1
ACCENT_ENABLE_TRANSPARENTGRADIENT = 2
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19

# DWM backdrop
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_AUTO = 0
DWMSBT_NONE = 1
DWMSBT_MAINWINDOW = 2      # Mica
DWMSBT_TRANSIENTWINDOW = 3 # Acrylic
DWMSBT_TABBEDWINDOW = 4    # Mica Alt

def apply_blur(hwnd, is_dark=True, opacity=0.85):
    """
    Applies Windows Acrylic / Blur Behind to a window handle.
    Falls back gracefully if not supported.
    """
    try:
        # Try DwmSetWindowAttribute first
        dwmapi = ctypes.windll.dwmapi
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW) # Acrylic
        res = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop)
        )
        if res == 0:
            return True
    except Exception:
        pass

    try:
        # Legacy SetWindowCompositionAttribute
        user32 = ctypes.windll.user32
        SetWindowCompositionAttribute = getattr(user32, "SetWindowCompositionAttribute", None)
        if not SetWindowCompositionAttribute:
            return False

        # Gradient color in ABGR format
        alpha = int(255 * (1.0 - opacity))
        if is_dark:
            # Dark acrylic: B=0x20, G=0x20, R=0x20
            color = (alpha << 24) | (0x20 << 16) | (0x20 << 8) | 0x20
        else:
            # Light acrylic: B=0xF0, G=0xF0, R=0xF0
            color = (alpha << 24) | (0xF0 << 16) | (0xF0 << 8) | 0xF0

        policy = ACCENT_POLICY()
        policy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        policy.AccentFlags = 2  # Draw all borders or acrylic flag
        policy.GradientColor = color

        data = WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)

        ret = SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        if ret:
            return True
        
        # Fallback to standard blurbehind
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
                ctypes.sizeof(backdrop)
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

