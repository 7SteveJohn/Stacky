"""
player_seek.py - QQ音乐桌面客户端进度跳转（静默，不弹出播放器窗口）。

QQ音乐把 SMTC 的 seek / fast-forward / rewind 能力全部关闭，系统接口返回 True
但播放位置不动。这里用 UI Automation 找到播放器自己的进度条控件，取真实矩形，
按目标比例算点，ScreenToClient 后向窗口发送鼠标消息。

把播放页窗口用一次 SetWindowPos 临时移到屏幕外 (-32000) 并保持
WS_VISIBLE / IsWindowEnabled。
QQ音乐 DirectUI 只在窗口 visible+enabled 时处理鼠标消息，但用户屏幕上看不到
任何东西。不激活窗口、不移动鼠标、不抢焦点、不发全局热键，完成后一次调用
把窗口放回原位并恢复原来的可见性。

绝不主动唤起/弹出播放器：播放页窗口不存在时直接返回 False，由调用方提示。
"""

import ctypes
import re
import time
from ctypes import wintypes

_TIME_TEXT = re.compile(r"\s*(\d{1,2}):(\d{2})\s*")
_INSET = 5.0
_OFFSCREEN_X = -32000
_OFFSCREEN_Y = -32000
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_SMTO_ABORTIFHUNG = 0x0002
_SMTO_ERRORONEXIT = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SWP_NOZORDER = 0x0004
_SWP_HIDEWINDOW = 0x0080

# 播放页窗口一旦被创建就会常驻（含隐藏状态），缓存后优先复用
_cached_hwnd = 0


def _seconds(text):
    m = _TIME_TEXT.fullmatch(text or "")
    if not m:
        return None
    s = int(m.group(2))
    return int(m.group(1)) * 60 + s if s < 60 else None


def _qq_pid():
    import psutil
    for p in psutil.process_iter(["name", "pid"]):
        if (p.info["name"] or "").lower() == "qqmusic.exe":
            return p.info["pid"]
    return None


def _player_windows(pid):
    """所有 TXGuiFoundation 窗口。隐藏窗口的 UIA 树仍然完整，所以不过滤可见性。"""
    import win32gui
    import win32process
    found = []

    def visit(hwnd, _):
        try:
            _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
            if owner_pid != pid or win32gui.GetClassName(hwnd) != "TXGuiFoundation":
                return
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if w < 600 or h < 300:
                return
            found.append((hwnd, bool(win32gui.GetWindowText(hwnd)),
                          bool(win32gui.IsWindowVisible(hwnd)), (r[0], r[1], w, h)))
        except Exception:
            pass

    win32gui.EnumWindows(visit, None)
    # 有标题的播放页优先（标题非空是识别播放页的关键特征）
    found.sort(key=lambda item: not item[1])
    return found


def _move_offscreen(hwnd, rect):
    """窗口可见但位于屏幕外：QQ音乐照常处理消息，用户屏幕上什么都没有。

    必须用一次 SetWindowPos 同时完成「移动 + 显示」。若先 ShowWindow 再移动，
    中间会有一帧窗口在原屏幕位置可见，用户就会看到播放器闪一下。
    """
    ctypes.windll.user32.SetWindowPos(
        hwnd, 0, _OFFSCREEN_X, _OFFSCREEN_Y, rect[2], rect[3],
        _SWP_NOACTIVATE | _SWP_SHOWWINDOW | _SWP_NOZORDER)


def _restore_window(hwnd, rect, was_visible):
    """同样一次调用完成「移回原位 + 隐藏」，避免任何中间可见态。"""
    flags = _SWP_NOACTIVATE | _SWP_NOZORDER
    if not was_visible:
        flags |= _SWP_HIDEWINDOW
    ctypes.windll.user32.SetWindowPos(
        hwnd, 0, rect[0], rect[1], rect[2], rect[3], flags)


def _uia():
    """UIA 在冻结环境下需要可写目录存放 comtypes 生成的 typelib 包装。"""
    import os
    import tempfile
    import comtypes.gen
    import comtypes.client
    gen_dir = os.path.join(tempfile.gettempdir(), "stacky_comtypes_gen")
    os.makedirs(gen_dir, exist_ok=True)
    if gen_dir not in comtypes.gen.__path__:
        comtypes.gen.__path__.append(gen_dir)
    if hasattr(comtypes.client, "gen_dir"):
        comtypes.client.gen_dir = gen_dir
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(3)
    return auto


def _progress_bar(auto, hwnd, duration):
    """找总时长文字 -> 父节点 3 子元素中的进度条 -> 返回屏幕矩形（可为负坐标）。"""
    root = auto.ControlFromHandle(hwnd)
    if not root:
        return None
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=18):
        if ctrl.ControlType != auto.ControlType.TextControl:
            continue
        total = _seconds(ctrl.Name)
        if total is None or abs(total - duration) > 1.5:
            continue
        parent = ctrl.GetParentControl()
        children = parent.GetChildren()
        if (len(children) != 3 or _seconds(children[0].Name) is None
                or _seconds(children[2].Name) != total):
            continue
        bar = children[1].BoundingRectangle
        left = children[0].BoundingRectangle
        right = children[2].BoundingRectangle
        if (bar.right - bar.left > 40 and bar.bottom - bar.top <= 60
                and bar.left >= left.right and bar.right <= right.left):
            return bar.left, bar.top, bar.right, bar.bottom
    return None


def _click(hwnd, client_x, client_y):
    u = ctypes.windll.user32
    lp = ((client_y & 0xffff) << 16) | (client_x & 0xffff)
    res = wintypes.DWORD()
    for msg, buttons in ((_WM_MOUSEMOVE, 0), (_WM_LBUTTONDOWN, 1), (_WM_LBUTTONUP, 0)):
        if not u.SendMessageTimeoutW(hwnd, msg, buttons, lp,
                                     _SMTO_ABORTIFHUNG | _SMTO_ERRORONEXIT,
                                     500, ctypes.byref(res)):
            return False
    return True


def _try_window(hwnd, rect, was_visible, auto, seconds, duration):
    """把窗口切到「屏幕外可见」-> 用 UIA 点击 -> 恢复原状态。"""
    import win32gui
    moved = False
    try:
        if not was_visible:
            _move_offscreen(hwnd, rect)
            moved = True
            time.sleep(0.12)
        if not ctypes.windll.user32.IsWindowEnabled(hwnd):
            return False
        bar = _progress_bar(auto, hwnd, duration)
        if not bar:
            return False
        left, top, right, bottom = bar
        width = right - left
        if width <= _INSET * 2 + 10:
            return False
        sx = int(round(left + _INSET + (width - 2 * _INSET) * seconds / duration))
        sy = top + (bottom - top) // 2
        cx, cy = win32gui.ScreenToClient(hwnd, (sx, sy))
        client = win32gui.GetClientRect(hwnd)
        if cx < 0 or cy < 0 or cx >= client[2] or cy >= client[3]:
            return False
        return _click(hwnd, cx, cy)
    finally:
        if moved:
            _restore_window(hwnd, rect, False)


def seek_qqmusic(seconds: float, duration: float) -> bool:
    """把 QQ音乐播放位置跳到 seconds 秒。全程不弹出/激活播放器窗口。"""
    if duration <= 0 or seconds < 0 or seconds > duration:
        return False
    pid = _qq_pid()
    if pid is None:
        return False
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # COINIT_APARTMENTTHREADED
    try:
        windows = _player_windows(pid)
        auto = _uia()
        global _cached_hwnd
        if any(w[0] == _cached_hwnd for w in windows):
            windows.sort(key=lambda item: item[0] != _cached_hwnd)
        for hwnd, titled, was_visible, rect in windows:
            if _try_window(hwnd, rect, was_visible, auto, seconds, duration):
                _cached_hwnd = hwnd
                return True
        return False
    except Exception:
        return False
    finally:
        ctypes.windll.ole32.CoUninitialize()
