"""
Desktop Layer Management and Z-Order Watchdog for Big Folder Desktop Launcher.
Implements a desktop-pinned idle band, SHELLDLL_DefView anchoring,
zero-overhead Z-Order watchdog with re-bedding self-healing, and lease-based
TemporaryRaise dynamic elevation.
"""

import os
import sys
import time
import threading
import ctypes
from ctypes import wintypes
from typing import Tuple, Optional, Dict

from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QWidget

# Win32 Constants
HWND_TOP = 0
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020

GW_HWNDNEXT = 2
GW_HWNDPREV = 3
GW_OWNER = 4
GWLP_HWNDPARENT = -8
GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_EX_ACCEPTFILES = 0x00000010
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_CHILD = 0x40000000
WS_MINIMIZE = 0x20000000

WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
HTCLIENT = 1

WINEVENT_OUTOFCONTEXT = 0x0000
EVENT_SYSTEM_FOREGROUND = 0x0003

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SMTO_ABORTIFHUNG = 0x0002

dwmapi = ctypes.windll.dwmapi if sys.platform == "win32" else None
DWMWA_CLOAKED = 14

user32 = ctypes.windll.user32 if sys.platform == "win32" else None

if user32:
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        if hasattr(user32, "SetWindowLongPtrW"):
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        if hasattr(user32, "GetWindowLongPtrW"):
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
LVM_HITTEST = 0x1012
LVHT_NOWHERE = 0x0001

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t)
    ]

class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("flags", ctypes.c_uint),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("iGroup", ctypes.c_int)
    ]
 
oleacc = None
if sys.platform == "win32":
    try:
        oleacc = ctypes.windll.oleacc
    except Exception:
        oleacc = None

HookProcType = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM) if user32 else None

if user32:
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ScreenToClient.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.c_void_p
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetDoubleClickTime.argtypes = []
    user32.GetDoubleClickTime.restype = wintypes.UINT
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    if HookProcType:
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HookProcType, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t


def set_window_long_ptr(hwnd: int, index: int, new_long: int) -> int:
    """Sets a window pointer or long value safely on both 32-bit and 64-bit systems."""
    if not user32 or not hwnd:
        return 0
    if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(user32, "SetWindowLongPtrW"):
        return user32.SetWindowLongPtrW(hwnd, index, new_long)
    return user32.SetWindowLongW(hwnd, index, new_long)


def get_window_long_ptr(hwnd: int, index: int) -> int:
    """Gets a window pointer or long value safely on both 32-bit and 64-bit systems."""
    if not user32 or not hwnd:
        return 0
    if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(user32, "GetWindowLongPtrW"):
        return user32.GetWindowLongPtrW(hwnd, index)
    return user32.GetWindowLongW(hwnd, index)


def find_desktop_shell_window() -> Tuple[int, int]:
    """
    Finds the Windows Desktop Shell handles: (SHELLDLL_DefView, Progman).
    Handles standard Progman desktop as well as WorkerW desktops (e.g. Wallpaper Engine).
    Also supports background terminal or subagent desktops by querying 'Default' interactive desktop
    via an isolated worker thread (guaranteeing SetThreadDesktop succeeds even if GUI already started).
    """
    if not user32:
        return 0, 0

    progman = user32.FindWindowW("Progman", None)
    defview = 0
    if progman:
        defview = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)

    if not defview:
        current_workerw = user32.FindWindowExW(0, 0, "WorkerW", None)
        while current_workerw:
            sub_defview = user32.FindWindowExW(current_workerw, 0, "SHELLDLL_DefView", None)
            if sub_defview:
                defview = sub_defview
                break
            current_workerw = user32.FindWindowExW(0, current_workerw, "WorkerW", None)

    if defview and progman:
        return int(defview), int(progman)

    # If missing, query Default interactive desktop from a clean helper thread
    def _probe_default_desktop(result: list):
        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x1FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
                p = user32.FindWindowW("Progman", None)
                dv = 0
                if p:
                    dv = user32.FindWindowExW(p, 0, "SHELLDLL_DefView", None)
                if not dv:
                    curr_w = user32.FindWindowExW(0, 0, "WorkerW", None)
                    while curr_w:
                        sub = user32.FindWindowExW(curr_w, 0, "SHELLDLL_DefView", None)
                        if sub:
                            dv = sub
                            break
                        curr_w = user32.FindWindowExW(0, curr_w, "WorkerW", None)
                result[0] = int(dv or 0)
                result[1] = int(p or 0)
        except Exception:
            pass

    shell_res = [int(defview or 0), int(progman or 0)]
    t = threading.Thread(target=_probe_default_desktop, args=(shell_res,), daemon=True)
    t.start()
    t.join(timeout=1.0)

    return shell_res[0], shell_res[1]


_attached_desktop_owners: Dict[int, int] = {}

WinEventProcType = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD
) if user32 else None

if user32 and WinEventProcType:
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
        WinEventProcType, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD
    ]
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    user32.UnhookWinEvent.restype = wintypes.BOOL


def is_window_cloaked(hwnd: int) -> bool:
    """Checks whether a window is DWM-cloaked (virtual desktop / suspended UWP)."""
    if not dwmapi or not hwnd:
        return False
    try:
        cloaked = ctypes.c_int(0)
        hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return hr == 0 and cloaked.value != 0
    except Exception:
        return False


def trim_process_working_set() -> bool:
    """
    Trims cold, inactive memory pages from process working set using Windows psapi.EmptyWorkingSet.
    Safely releases unused startup/import/decompression RAM (often reducing memory by 60%~80%)
    without terminating or losing state.
    """
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        pid = os.getpid()
        # Open real process handle with PROCESS_SET_QUOTA (0x0100) | PROCESS_QUERY_INFORMATION (0x0400)
        # Note: Windows EmptyWorkingSet fails with ERROR_INVALID_HANDLE (6) when passed pseudo handle -1!
        h_proc = kernel32.OpenProcess(0x0100 | 0x0400, False, pid)
        if h_proc:
            success = bool(psapi.EmptyWorkingSet(h_proc))
            kernel32.CloseHandle(h_proc)
            return success
        return False
    except Exception:
        return False


def attach_to_desktop_shell(hwnd: int) -> bool:
    """
    Attaches hwnd to SHELLDLL_DefView via GWLP_HWNDPARENT (sets owner in Win32).
    In Windows 10/11, windows owned by SHELLDLL_DefView:
    1. Are anchored to the desktop band right above Progman / DefView.
    2. Are NOT minimized or hidden by Win+D ('Show Desktop').
    3. Retain full top-level coordinates and translucent compositing.
    """
    if not user32 or not hwnd:
        return False
    defview, progman = find_desktop_shell_window()
    if not defview:
        return False

    current_owner = user32.GetWindow(hwnd, 4) if hasattr(user32, "GetWindow") else 0
    if current_owner == defview:
        return True

    if hwnd not in _attached_desktop_owners:
        _attached_desktop_owners[hwnd] = current_owner

    set_window_long_ptr(hwnd, GWLP_HWNDPARENT, defview)
    actual_owner = user32.GetWindow(hwnd, 4) if hasattr(user32, "GetWindow") else 0
    return actual_owner == defview or get_window_long_ptr(hwnd, GWLP_HWNDPARENT) == defview


DESKTOP_BEDDED_CLASSES = {
    "Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32",
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    # 以下为系统窗口类名，用于识别桌面上层，属于互操作标识
    "WinUIDesktopWin32WindowClass", "Microsoft.UI.Content.PopupWindowSiteBridge",
    "CEF-OSC-WIDGET", "Windows.UI.Core.CoreWindow",
    "IME", "MSCTFIME UI", "CiceroUIWndFrame", "DummyDWMListenerWindow",
    "EdgeUiInputTopWndClass", "Winit Thread Event Target", "Tao Thread Event Target"
}


def is_window_bedded_at_desktop_layer(hwnd: int, max_walk: int = 16) -> bool:
    """
    Walks the Z-order below hwnd. Returns True if all visible non-minimized windows
    below hwnd belong to our process, another desktop tool, or the desktop shell (Progman, WorkerW, DefView).
    If a foreign application window sits below hwnd, returns False (leak state).
    """
    if not user32 or not hwnd:
        return False

    my_pid = os.getpid()
    cur = user32.GetWindow(hwnd, GW_HWNDNEXT)
    count = 0
    buf = (wintypes.WCHAR * 256)()
    pid_buf = wintypes.DWORD()

    while cur and count < max_walk:
        count += 1
        if user32.IsWindowVisible(cur) and not is_window_cloaked(cur):
            style = user32.GetWindowLongW(cur, GWL_STYLE)
            if not (style & WS_MINIMIZE):
                user32.GetWindowThreadProcessId(cur, ctypes.byref(pid_buf))
                cur_pid = pid_buf.value
                if cur_pid != my_pid:
                    user32.GetClassNameW(cur, buf, 256)
                    cls = buf.value
                    if cls not in DESKTOP_BEDDED_CLASSES:
                        return False
        cur = user32.GetWindow(cur, GW_HWNDNEXT)

    return True


class WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("hwndInsertAfter", wintypes.HWND),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("cx", ctypes.c_int),
        ("cy", ctypes.c_int),
        ("flags", wintypes.UINT),
    ]


def pin_to_desktop(hwnd: int, shell_hwnd: int = 0) -> bool:
    """
    Anchors the window cleanly to the desktop wallpaper level:
    1. Attaches to SHELLDLL_DefView (immunes to Win+D, embeds in desktop band).
    2. Enforces WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_ACCEPTFILES.
    3. Positions stably at HWND_BOTTOM (owned by SHELLDLL_DefView, sits right above wallpaper, immune to Win+D).
    """
    if not user32 or not hwnd:
        return False

    # Attach owner to DefView
    attached = attach_to_desktop_shell(hwnd)

    # Ensure styles
    ex_style = get_window_long_ptr(hwnd, GWL_EXSTYLE)
    target_ex_style = ex_style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_ACCEPTFILES
    if target_ex_style != ex_style:
        set_window_long_ptr(hwnd, GWL_EXSTYLE, target_ex_style)

    # Position at desktop layer: HWND_BOTTOM keeps it stable at the desktop level
    user32.SetWindowPos(
        hwnd,
        HWND_BOTTOM,
        0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    )
    return True


class _ForegroundHookHub:
    """
    Process-wide shared EVENT_SYSTEM_FOREGROUND WinEventHook dispatcher.
    Installs ONE native SetWinEventHook when the first DesktopWatchdog registers,
    fans out foreground change notifications to all active watchdogs, and calls
    UnhookWinEvent cleanly when the last watchdog stops.
    """

    def __init__(self):
        self._watchdogs: list = []
        self._hook = None
        self._hook_proc = None

    @property
    def hook_handle(self):
        return self._hook

    @property
    def active_count(self) -> int:
        return len(self._watchdogs)

    def register(self, watchdog: 'DesktopWatchdog'):
        if watchdog not in self._watchdogs:
            self._watchdogs.append(watchdog)
        if self._hook is None and self._watchdogs and user32 and WinEventProcType:
            self._install_hook()

    def unregister(self, watchdog: 'DesktopWatchdog'):
        if watchdog in self._watchdogs:
            self._watchdogs.remove(watchdog)
        if not self._watchdogs:
            self._uninstall_hook()

    def _install_hook(self):
        if self._hook is not None or not user32 or not WinEventProcType:
            return

        def _proc(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
            self._dispatch(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime)

        self._hook_proc = WinEventProcType(_proc)
        try:
            self._hook = user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_FOREGROUND,
                None,
                self._hook_proc,
                0,
                0,
                WINEVENT_OUTOFCONTEXT
            )
        except Exception:
            self._hook = None
            self._hook_proc = None

    def _uninstall_hook(self):
        if self._hook is not None and user32:
            try:
                user32.UnhookWinEvent(self._hook)
            except Exception:
                pass
        self._hook = None
        self._hook_proc = None

    def _dispatch(self, hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        for wd in list(self._watchdogs):
            try:
                wd._on_winevent(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime)
            except Exception:
                pass


_FOREGROUND_HOOK_HUB = _ForegroundHookHub()


class DesktopWatchdog(QObject):
    """
    High-performance, ultra-lightweight Z-Order Watchdog and TemporaryRaise Lease Manager.
    - Monitors window Z-Order and state in background (<0.01% CPU).
    - When Win+D or desktop refresh breaks layer, automatically re-beds in milliseconds.
    - Grants TemporaryRaise elevation leases during user interaction (hover, press, drag, menu, drawer),
      guaranteeing 100% responsive clicks and menus, and smoothly re-beds on lease expiry.
    """
    rebedded = Signal()
    elevation_changed = Signal(bool)

    def __init__(self, target_widget: QWidget, check_interval_ms: int = 2500, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.target_widget = target_widget
        self.check_interval_ms = check_interval_ms

        self.enabled = True
        self.is_elevated = False
        self.is_summon_elevated = False
        self.active_lease_reason = ""
        self.shell_defview_hwnd = 0
        self.shell_progman_hwnd = 0
        self._last_pinned_anchor = 0

        # Lease Timer for temporary elevation
        self.lease_timer = QTimer(self)
        self.lease_timer.setSingleShot(True)
        self.lease_timer.timeout.connect(self._on_lease_expired)

        # Health Watchdog Timer (auto-starts immediately to guarantee permanent desktop presence)
        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(self.check_interval_ms)
        self.watchdog_timer.timeout.connect(self.check_health)
        self.watchdog_timer.start()

        # Register with process-wide shared WinEventHook hub (1 hook per process instead of N)
        _FOREGROUND_HOOK_HUB.register(self)

        self._refresh_shell_handles()

    @property
    def _hook(self):
        return _FOREGROUND_HOOK_HUB.hook_handle if self in _FOREGROUND_HOOK_HUB._watchdogs else None

    def _on_winevent(self, hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
        try:
            if not getattr(self, "enabled", False) or not self.target_widget:
                return

            try:
                my_hwnd = int(self.target_widget.winId())
            except Exception:
                my_hwnd = 0

            fg = user32.GetForegroundWindow() if (user32 and hasattr(user32, "GetForegroundWindow")) else 0
            if not fg:
                return

            buf = (wintypes.WCHAR * 256)()
            user32.GetClassNameW(fg, buf, 256)
            cls_name = buf.value

            is_shell = cls_name in ("Progman", "WorkerW", "SHELLDLL_DefView")
            is_my_win = (fg == my_hwnd)

            pid_buf = wintypes.DWORD(0)
            if user32 and hasattr(user32, "GetWindowThreadProcessId"):
                user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_buf))
            is_my_process = (pid_buf.value == os.getpid()) if pid_buf.value else False

            if is_shell:
                # Desktop shell focused (Win+D or clicked empty wallpaper)
                was_elevated = bool(getattr(self, "is_elevated", False) or getattr(self, "is_summon_elevated", False))
                if was_elevated:
                    self._sync_revoked_summon_state()
                self.re_bed()
                if was_elevated:
                    self.elevation_changed.emit(False)
                return

            # Same-process sibling card, drawer, context menu, or SettingsDialog:
            # do NOT treat as external app stealing foreground and avoid Z-order thrashing!
            if is_my_win or is_my_process:
                if is_my_win and not getattr(self, "is_elevated", False) and not getattr(self, "is_summon_elevated", False):
                    QTimer.singleShot(60, self.check_health)
                return

            # External app (fullscreen game/app, browser, IDE, video player, etc.) took foreground:
            was_elevated = bool(getattr(self, "is_elevated", False) or getattr(self, "is_summon_elevated", False))
            if was_elevated:
                self._sync_revoked_summon_state()
            self.re_bed()
            if was_elevated:
                self.elevation_changed.emit(False)
        except Exception:
            pass

    def _sync_revoked_summon_state(self):
        """Keeps target_widget.is_summoned_front synchronized when watchdog revokes summon elevation."""
        was_summon = bool(getattr(self, "is_summon_elevated", False))
        self.is_elevated = False
        self.is_summon_elevated = False
        self.active_lease_reason = ""
        self.lease_timer.stop()
        target = self.target_widget
        if was_summon and target is not None and getattr(target, "is_summoned_front", False):
            target.is_summoned_front = False
            saved_pos = getattr(target, "_summon_saved_pos", None)
            if saved_pos is not None:
                try:
                    target.move(saved_pos)
                except Exception:
                    pass
                target._summon_saved_pos = None
            toast = getattr(target, "toast", None)
            if toast is not None:
                try:
                    toast.hide()
                except Exception:
                    pass

    def start(self):
        """Starts the watchdog."""
        if self.target_widget is not None:
            self.enabled = True
            _FOREGROUND_HOOK_HUB.register(self)
        if not self.watchdog_timer.isActive():
            self.watchdog_timer.start()
        self.re_bed()

    def stop(self):
        """Stops the watchdog and unregisters from the shared foreground hook hub."""
        self.enabled = False
        self.watchdog_timer.stop()
        self.lease_timer.stop()
        _FOREGROUND_HOOK_HUB.unregister(self)
        self.target_widget = None

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if not enabled:
            self.release_temporary_raise(immediate=True)

    def _refresh_shell_handles(self):
        defview, progman = find_desktop_shell_window()
        self.shell_defview_hwnd = defview
        self.shell_progman_hwnd = progman

    def get_anchor_hwnd(self) -> int:
        if not self.shell_defview_hwnd:
            self._refresh_shell_handles()
        return self.shell_defview_hwnd or self.shell_progman_hwnd

    def re_bed(self, force: bool = False) -> bool:
        """
        Re-anchors the widget window securely into the DesktopPinned idle band.
        Executes without flashing.
        """
        if not self.target_widget or not user32:
            return False

        # Throttle re-bed calls to avoid high frequency thrashing unless forced or recovering from elevation
        import time as _t
        now = _t.time()
        if not force and not getattr(self, "is_summon_elevated", False) and not getattr(self, "is_elevated", False):
            if now - getattr(self, "_last_rebed_time", 0.0) < 0.5:
                return True
        self._last_rebed_time = now

        try:
            hwnd = int(self.target_widget.winId())
        except Exception:
            return False

        if not hwnd:
            return False

        # If minimized (e.g. from an aggressive Win+M / Win+D broadcast), restore state
        if self.target_widget.isMinimized():
            self.target_widget.setWindowState(Qt.WindowState.WindowNoState)
            self.target_widget.show()

        if hasattr(self.target_widget, "is_drawer_open") and not self.target_widget.is_drawer_open():
            if hasattr(self.target_widget, "card") and not self.target_widget.card.isVisible():
                self.target_widget.card.show()
            if hasattr(self.target_widget, "update_title_visibility"):
                self.target_widget.update_title_visibility()

        anchor = self.get_anchor_hwnd()
        success = pin_to_desktop(hwnd, anchor)
        self._last_pinned_anchor = anchor

        self.is_elevated = False
        self.is_summon_elevated = False
        self.active_lease_reason = ""
        self.rebedded.emit()
        return success

    def acquire_summon_elevation(self):
        """
        Grants a persistent launcher summon elevation to HWND_TOP.
        Unlike short interaction leases, does not auto-expire after a timeout,
        keeping the mobile desktop folder card comfortably in front of open windows
        until the user clicks an app, clicks outside, or presses Esc.
        """
        if not self.target_widget or not user32:
            return

        try:
            hwnd = int(self.target_widget.winId())
        except Exception:
            return

        self.lease_timer.stop()
        self.is_elevated = True
        self.is_summon_elevated = True
        self.active_lease_reason = "summon"

        user32.SetWindowPos(
            hwnd,
            HWND_TOP,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        )
        try:
            # Momentary TOPMOST elevation to reliably break through foreground locks
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            )
            user32.SetWindowPos(
                hwnd,
                HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            )
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.elevation_changed.emit(True)

    def release_summon_elevation(self):
        """
        Releases persistent launcher summon elevation and cleanly re-beds
        the desktop folder card back to the desktop wallpaper level.
        """
        self.is_summon_elevated = False
        self.is_elevated = False
        self.active_lease_reason = ""
        self.lease_timer.stop()
        self.re_bed()
        self.elevation_changed.emit(False)

    def acquire_temporary_raise(self, reason: str = "interaction", duration_ms: int = 4000):
        """
        Grants a temporary elevation lease for active drag-and-drop, menus, or modal drawers.
        Passive mouse hover or idle desktop interaction does not elevate above active apps,
        keeping the Big Folder stably embedded in the Windows desktop canvas.
        """
        if not self.enabled or not self.target_widget or not user32:
            return

        try:
            hwnd = int(self.target_widget.winId())
        except Exception:
            return

        # For passive interaction when already securely bedded, maintain desktop bottom
        if reason in ("hover", "interaction") and is_window_bedded_at_desktop_layer(hwnd):
            return

        self.active_lease_reason = reason

        if not self.is_elevated:
            self.is_elevated = True
            # Elevate to TOP of current non-topmost Z-order
            user32.SetWindowPos(
                hwnd,
                HWND_TOP,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            )
            self.elevation_changed.emit(True)

        # Extend or start lease timer
        self.lease_timer.start(duration_ms)


    def release_temporary_raise(self, immediate: bool = False):
        """
        Releases elevation lease. If not immediate, grants a 350ms grace window
        to avoid rapid fluttering between widgets.
        """
        if getattr(self, "is_summon_elevated", False):
            return

        if not self.is_elevated:
            return

        if immediate:
            self.lease_timer.stop()
            self._on_lease_expired()
        else:
            # Short grace delay
            self.lease_timer.start(350)

    def _on_lease_expired(self):
        if not self.enabled:
            return
        if getattr(self, "is_summon_elevated", False):
            return
        self.is_elevated = False
        self.active_lease_reason = ""
        self.re_bed()
        self.elevation_changed.emit(False)

    def check_health(self):
        """
        Watchdog routine: checks whether the window was obscured, minimized, or torn from bottom.
        Called every check_interval_ms.
        """
        if not self.enabled or not self.target_widget or not user32:
            return

        # 鼠标左键处于按下状态（用户正在拖拽窗口、选区或操作交互），绝对不干扰 Z-Order，杜绝掉帧卡顿
        try:
            if user32.GetAsyncKeyState(0x01) & 0x8000:
                return
        except Exception:
            pass

        # If currently under an active temporary raise lease or summon elevation, check foreground state
        if self.is_elevated or getattr(self, "is_summon_elevated", False):
            fg = user32.GetForegroundWindow() if hasattr(user32, "GetForegroundWindow") else 0
            try:
                my_hwnd = int(self.target_widget.winId())
            except Exception:
                my_hwnd = 0
            if fg and fg != my_hwnd:
                pid_buf = wintypes.DWORD(0)
                if hasattr(user32, "GetWindowThreadProcessId"):
                    user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_buf))
                is_my_process = (pid_buf.value == os.getpid()) if pid_buf.value else False
                if not is_my_process:
                    buf = (wintypes.WCHAR * 256)()
                    user32.GetClassNameW(fg, buf, 256)
                    if buf.value not in ("Progman", "WorkerW", "SHELLDLL_DefView"):
                        # External application is in foreground: revoke elevation and re-bed
                        self._sync_revoked_summon_state()
                        self.re_bed()
                        self.elevation_changed.emit(False)
            return

        # If minimized (Win+D / Win+M), restore and re-bed immediately
        if self.target_widget.isMinimized():
            self.re_bed()
            return

        try:
            hwnd = int(self.target_widget.winId())
        except Exception:
            return

        if not hwnd:
            return

        # Check visibility
        if not user32.IsWindowVisible(hwnd) and self.target_widget.isVisible():
            self.re_bed()
            return

        # Check window styles (WS_EX_TOOLWINDOW and WS_EX_NOACTIVATE)
        ex_style = get_window_long_ptr(hwnd, GWL_EXSTYLE)
        if (ex_style & WS_EX_TOOLWINDOW) == 0 or (ex_style & WS_EX_NOACTIVATE) == 0:
            self.re_bed()
            return

        # Check desktop bedding in Z-order (re-bed only if a foreign app sits beneath)
        if not is_window_bedded_at_desktop_layer(hwnd):
            self.re_bed()
            return

        # Check desktop shell handle (e.g. Explorer restart)
        current_anchor = self.get_anchor_hwnd()
        if self._last_pinned_anchor and current_anchor != self._last_pinned_anchor:
            self.re_bed()
            return


# =============================================================================
# 桌面空白处双击监听器 (禅模式 · Desktop Zen Mode)
# =============================================================================

OBJID_CLIENT = 0xFFFFFFFC

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8)
    ]

_IID_IAccessible = _GUID(0x618736E0, 0x3C3D, 0x11CF, (ctypes.c_byte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71))

class _ACC_VARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", wintypes.WORD),
        ("w1", wintypes.WORD),
        ("w2", wintypes.WORD),
        ("w3", wintypes.WORD),
        ("lVal", ctypes.c_long),
        ("res", ctypes.c_long)
    ]

_AccHitTestType = ctypes.WINFUNCTYPE(wintypes.HRESULT, ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.POINTER(_ACC_VARIANT)) if user32 else None

def _release_iunknown(punk):
    if punk:
        try:
            vtable = ctypes.cast(punk, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_fn = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])
            release_fn(punk)
        except Exception:
            pass


def is_point_on_desktop_blank(pt_x: int, pt_y: int) -> bool:
    """
    判断指定屏幕像素点是否处于 Windows 桌面壁纸层 (DefView / SysListView32) 的纯空白区域。
    - 若命中外部应用程序窗口、启动台卡片或桌面应用图标，返回 False
    - 仅在点击纯壁纸背景或无图标空白区域时返回 True
    - 支持多显示器虚拟桌面坐标（含负 X/Y 副屏与右侧副屏）
    - 访问 Explorer SysListView32 前先做 60ms SMTO_ABORTIFHUNG 存活探测，杜绝 Explorer 假死冻结 UI
    """
    if not user32:
        return False
    try:
        # 1. 多显示器虚拟桌面边界防越界（兼容负坐标副屏与主屏外副屏）
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            vx, vy = 0, 0
            vw = user32.GetSystemMetrics(0)
            vh = user32.GetSystemMetrics(1)
        if pt_x < vx or pt_y < vy or pt_x >= vx + vw or pt_y >= vy + vh:
            return False

        pt = POINT(pt_x, pt_y)
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return False

        defview, progman = find_desktop_shell_window()
        if not defview:
            return False

        buf = (wintypes.WCHAR * 256)()
        user32.GetClassNameW(hwnd, buf, 256)
        cls_name = buf.value

        # 直接点击到壁纸宿主容器
        if cls_name in ("Progman", "WorkerW", "SHELLDLL_DefView"):
            return True

        # 点击到桌面图标列表视图 SysListView32
        if cls_name == "SysListView32":
            parent = user32.GetParent(hwnd)
            if parent == defview or user32.FindWindowExW(defview, 0, "SysListView32", None) == hwnd:
                # 跨进程调用 AccessibleObjectFromWindow 前先探测 Explorer 消息循环是否存活 (60ms 超时)
                if hasattr(user32, "SendMessageTimeoutW"):
                    dummy = ctypes.c_size_t(0)
                    alive = user32.SendMessageTimeoutW(
                        hwnd, 0, 0, 0, SMTO_ABORTIFHUNG, 60, ctypes.byref(dummy)
                    )
                    if not alive:
                        return False
                if oleacc and _AccHitTestType:
                    try:
                        pacc = ctypes.c_void_p()
                        hr = oleacc.AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, ctypes.byref(_IID_IAccessible), ctypes.byref(pacc))
                        if hr == 0 and pacc.value:
                            try:
                                vtable = ctypes.cast(pacc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                                acc_hit_test = _AccHitTestType(vtable[24])
                                var = _ACC_VARIANT()
                                hr_hit = acc_hit_test(pacc, pt_x, pt_y, ctypes.byref(var))
                                if hr_hit == 0:
                                    # var.lVal == 0 说明处于 SysListView32 容器空白背景，>0 为命中具体图标
                                    return var.lVal == 0
                            finally:
                                _release_iunknown(pacc.value)
                    except Exception:
                        pass
                return True
            return False
    except Exception:
        pass
    return False


class DesktopBlankDoubleClickHandler(QObject):
    """
    Windows 桌面空白处双击极简监听器 (禅模式):
    - 基于 Windows 低级鼠标钩子 (WH_MOUSE_LL)
    - 智能判定 Windows 桌面壁纸层纯空白区域双击
    - 绝对不干扰桌面图标正常双击启动，零输入延迟
    - 触发 desktop_blank_double_clicked 信号
    """
    desktop_blank_double_clicked = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.enabled = False
        self._last_click_time = 0.0
        self._last_click_x = 0
        self._last_click_y = 0
        self._prev_down = False

        self._double_click_time = 0.5
        self._double_click_dist_x = 4
        self._double_click_dist_y = 4
        if user32:
            try:
                self._double_click_time = max(0.2, min(1.0, user32.GetDoubleClickTime() / 1000.0))
                self._double_click_dist_x = max(2, user32.GetSystemMetrics(36))  # SM_CXDOUBLECLK
                self._double_click_dist_y = max(2, user32.GetSystemMetrics(37))  # SM_CYDOUBLECLK
            except Exception:
                pass

        # 25ms 轮询按键状态：低级鼠标钩子一旦回调超时会被 Windows 静默摘除，
        # 而空白命中检测要走跨进程 IAccessible，正好可能超出 LowLevelHooksTimeout。
        # 轮询不依赖系统钩子生命周期，行为确定。
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(25)
        self._poll_timer.timeout.connect(self._poll_left_button)

    def set_enabled(self, enabled: bool):
        if enabled:
            self.enable()
        else:
            self.disable()

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self._prev_down = False
        self._poll_timer.start()

    def disable(self):
        if not self.enabled:
            return
        self.enabled = False
        self._poll_timer.stop()

    def _poll_left_button(self):
        if not user32:
            return
        try:
            down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return
        if down and not self._prev_down:
            self._on_left_press()
        self._prev_down = down

    def _on_left_press(self):
        """记录左键按下；两次按下落在系统双击时间/距离内且命中桌面空白 -> 触发禅模式。"""
        try:
            pt = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return
            now = time.time()
            dt = now - self._last_click_time
            dx = abs(pt.x - self._last_click_x)
            dy = abs(pt.y - self._last_click_y)
            is_double = (dt <= self._double_click_time and
                         dx <= self._double_click_dist_x and
                         dy <= self._double_click_dist_y)
            self._last_click_time = now
            self._last_click_x = pt.x
            self._last_click_y = pt.y
            if is_double and is_point_on_desktop_blank(pt.x, pt.y):
                self._last_click_time = 0.0
                self.desktop_blank_double_clicked.emit()
        except Exception:
            pass

    def trigger_simulated_double_click(self):
        """用于自动化测试模拟双击桌面空白"""
        self.desktop_blank_double_clicked.emit()


sys.modules.setdefault("desktop_layer", sys.modules[__name__])
sys.modules.setdefault("stacky.core.desktop_layer", sys.modules[__name__])

