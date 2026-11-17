"""
file_shelf.py - Stacky 灵叠桌面文件中转站 (Desktop File Shelf / Staging Basket)
文件中转站：
1. 边缘吸附与手势隐蔽触发：平时以超薄半透明吸附条贴合屏幕边缘，拖拽文件靠近或悬停时毫秒级平滑滑出；
2. 多批次文件临时集装箱：支持从桌面、浏览器、微信、资源管理器拖入多批次文件暂存（零移动、物理无损引用）；
3. 批量一次性拖出 (Drag All)：按住中转站底部大胶囊拖出，生成 Windows 原生 OLE CF_HDROP，一次性拖入微信、邮件或目标文件夹；
4. 一键归档至 Stacky 分类：直接将中转站文件一键分流并物理规整至“工作办公”、“媒体资源”等大文件夹；
5. 复制路径与置顶模式：支持一键复制所有文件路径、保持展开/自动收起切换；
6. 遵循系统级液态玻璃质感，纯 QPainter 亚像素超椭圆连续曲率渲染，零第三方依赖。
"""

import os
import sys
import json
import shutil
import weakref
import ctypes
from ctypes import wintypes
from typing import List, Dict, Any, Optional

from PySide6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize, QUrl, Signal,
    QPropertyAnimation, QEasingCurve, QMimeData, QTimer, QEvent
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath, QPixmap,
    QImage, QIcon, QCursor, QDrag, QMouseEvent, QDragEnterEvent,
    QDragMoveEvent, QDropEvent
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QMenu, QApplication, QSizePolicy
)

from config_manager import ConfigManager
from icon_utils import (
    get_app_icon_pixmap as get_app_icon,
    create_squircle_path,
    STACKY_ITEM_MIME,
    mark_cross_folder_drop,
)
from ui_kit import get_theme_tokens, menu_qss

_SHELF_FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
_SHELF_FONT_CSS = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'


def _shelf_icon(kind: str, active: bool = False, size: int = 20,
                color: Optional[str] = None, light: bool = False) -> QIcon:
    """纯矢量图标（2x HiDPI 采样）。emoji 字形在 Windows 上常渲染成空白，右上角会退化成空方块。"""
    px = max(2, int(size) * 2)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(size / 20.0, size / 20.0)
    if color:
        c = QColor(color)
    elif light:
        c = QColor("#2563eb") if active else QColor("#475569")
    else:
        c = QColor("#60a5fa") if active else QColor("#cbd5e1")
    p.setPen(QPen(c, 1.5, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "pin":
        p.drawEllipse(QRectF(7, 3, 6, 6))
        p.drawLine(QPointF(10, 9), QPointF(10, 16))
        if active:
            p.setBrush(QBrush(c))
            p.drawEllipse(QRectF(8.6, 4.6, 2.8, 2.8))
    elif kind == "trash":
        p.drawLine(QPointF(4, 6), QPointF(16, 6))
        p.drawLine(QPointF(8, 4), QPointF(12, 4))
        p.drawRoundedRect(QRectF(5, 6, 10, 10), 1.5, 1.5)
        p.drawLine(QPointF(8.5, 9), QPointF(8.5, 13.5))
        p.drawLine(QPointF(11.5, 9), QPointF(11.5, 13.5))
    elif kind == "close":
        p.drawLine(QPointF(6, 6), QPointF(14, 14))
        p.drawLine(QPointF(14, 6), QPointF(6, 14))
    elif kind == "inbox":
        p.drawRoundedRect(QRectF(2.5, 8, 15, 9), 2, 2)
        p.drawLine(QPointF(2.5, 12.5), QPointF(7, 12.5))
        p.drawLine(QPointF(7, 12.5), QPointF(9, 15))
        p.drawLine(QPointF(9, 15), QPointF(11, 15))
        p.drawLine(QPointF(11, 15), QPointF(13, 12.5))
        p.drawLine(QPointF(13, 12.5), QPointF(17.5, 12.5))
        p.drawLine(QPointF(10, 2), QPointF(10, 6))
        p.drawLine(QPointF(7.8, 4.2), QPointF(10, 6.4))
        p.drawLine(QPointF(12.2, 4.2), QPointF(10, 6.4))
    p.end()
    return QIcon(pm)


HEADER_BTN_QSS = """
    QPushButton {
        background: transparent;
        border: none;
        padding: 0px;
        border-radius: 6px;
    }
    QPushButton:hover { background: rgba(255, 255, 255, 0.10); }
    QPushButton:pressed { background: rgba(255, 255, 255, 0.16); }
"""

HEADER_BTN_LIGHT_QSS = """
    QPushButton {
        background: transparent;
        border: none;
        padding: 0px;
        border-radius: 6px;
    }
    QPushButton:hover { background: rgba(15, 23, 42, 0.08); }
    QPushButton:pressed { background: rgba(15, 23, 42, 0.14); }
"""


class EmptyZone(QWidget):
    """空态投放区：1px 柔和虚线细描边与微透呼吸底板，纯 QPainter 抗锯齿渲染。"""

    def __init__(self, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self.theme: str = (theme or "dark").strip().lower()

    def set_theme(self, theme: str):
        self.theme = (theme or "dark").strip().lower()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if self.theme == "light":
            fill_c = QColor(15, 23, 42, 8)
            dash_c = QColor(15, 23, 42, 38)
        elif self.theme == "oled":
            fill_c = QColor(255, 255, 255, 6)
            dash_c = QColor(148, 163, 184, 42)
        else:
            fill_c = QColor(255, 255, 255, 8)
            dash_c = QColor(148, 163, 184, 48)
        p.setBrush(QBrush(fill_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 12.0, 12.0)
        pen = QPen(dash_c, 1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 12.0, 12.0)
        p.end()

# Win32 Constants & APIs for Fullscreen / Game Auto-Suppression
user32 = ctypes.windll.user32 if sys.platform == "win32" else None
kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

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

# Keep any unhook-failed callback thunks alive so Win32 never jumps into freed memory.
_RETIRED_WINEVENT_PROCS = []

if user32 and WinEventProcType:
    try:
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            WinEventProcType,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL
    except Exception:
        pass


def is_window_fullscreen_or_game(hwnd: int) -> bool:
    """
    High-performance detection of whether hwnd represents a fullscreen application
    (game, video, presentation) or a game process.
    Guarantees FileShelf automatically steps aside during gaming immersion.
    """
    if not hwnd or not user32:
        return False

    try:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False

        # Ignore desktop shells (Progman, WorkerW, Shell_TrayWnd, SHELLDLL_DefView)
        buf = (wintypes.WCHAR * 256)()
        user32.GetClassNameW(hwnd, buf, 256)
        cls_name = buf.value
        if cls_name in ("Progman", "WorkerW", "Shell_TrayWnd", "SHELLDLL_DefView"):
            return False

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() or pid.value == 0:
            return False

        # 1. Fullscreen monitor coverage check (Standard Windows full-screen / borderless game test)
        # MONITOR_DEFAULTTONEAREST = 2
        h_mon = user32.MonitorFromWindow(hwnd, 2)
        if h_mon:
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)
                ]
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
                rc = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                    # Allow 2px tolerance for multi-monitor border overlaps
                    if (rc.left <= mi.rcMonitor.left + 2 and
                        rc.top <= mi.rcMonitor.top + 2 and
                        rc.right >= mi.rcMonitor.right - 2 and
                        rc.bottom >= mi.rcMonitor.bottom - 2):
                        return True

        # 2. Process executable name detection (even if windowed)
        if kernel32:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if h_proc:
                try:
                    name_buf = (wintypes.WCHAR * 1024)()
                    size = wintypes.DWORD(1024)
                    query_name = getattr(kernel32, "QueryFullProcessImageNameW", None)
                    if query_name and query_name(h_proc, 0, name_buf, ctypes.byref(size)):
                        p_path = name_buf.value.lower()
                        exe_name = os.path.basename(p_path)
                        known_games = {
                            "cs2.exe", "tslgame.exe", "pubg.exe", "genshinimpact.exe", "yuanshen.exe",
                            "starrail.exe", "zenlesszonezero.exe", "wuthering waves.exe", "cyberpunk2077.exe",
                            "forzahorizon4.exe", "forzahorizon5.exe", "forzahorizon6.exe", "b5csgo.exe",
                            "5eclient.exe", "5eclient.bin", "valorant.exe", "league of legends.exe", "dota2.exe",
                            "steam.exe", "epicgameslauncher.exe", "wegame.exe", "game.exe", "blackmythwukong.exe",
                            "wukong.exe", "overwatch.exe", "r5apex.exe", "apex.exe", "minecraft.exe", "javaw.exe"
                        }
                        if exe_name in known_games or any(k in exe_name for k in ["game", "steam", "genshin", "honkai", "pubg", "horizon", "wukong", "cyberpunk"]):
                            return True
                finally:
                    kernel32.CloseHandle(h_proc)

        # 3. Window title heuristic for gaming platforms
        t_buf = (wintypes.WCHAR * 512)()
        if user32.GetWindowTextW(hwnd, t_buf, 512):
            title_l = t_buf.value.lower()
            game_title_keywords = [
                "counter-strike", "cs2", "pubg", "battlegrounds", "原神", "genshin", "崩坏",
                "星穹铁道", "鸣潮", "绝区零", "异环", "影之刃", "赛博朋克", "cyberpunk", "极限竞速",
                "地平线", "forza", "5e对战平台", "完美世界竞技", "黑神话", "英雄联盟", "无畏契约"
            ]
            if any(k in title_l for k in game_title_keywords):
                return True

    except Exception:
        pass

    return False

# Windows Shell Change Notification
SHCNE_UPDATEDIR = 0x00001000
SHCNF_PATHW = 0x0005


def notify_shell_change(path: str = ""):
    """Notifies Windows Explorer Shell of file/directory changes."""
    try:
        w_path = ctypes.c_wchar_p(path) if path else None
        ctypes.windll.shell32.SHChangeNotify(SHCNE_UPDATEDIR, SHCNF_PATHW, w_path, None)
    except Exception:
        pass


def format_file_size(bytes_num: int) -> str:
    """Formats file size into human-readable format."""
    if bytes_num < 1024:
        return f"{bytes_num} B"
    elif bytes_num < 1024 * 1024:
        return f"{bytes_num / 1024:.1f} KB"
    elif bytes_num < 1024 * 1024 * 1024:
        return f"{bytes_num / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_num / (1024 * 1024 * 1024):.2f} GB"


class StagedFileItem:
    """Represents a single file or directory staged in the shelf."""
    def __init__(self, path: str):
        self.path: str = os.path.abspath(path)
        self.name: str = os.path.basename(self.path) or self.path
        self.is_dir: bool = os.path.isdir(self.path)
        self.size_bytes: int = 0
        if not self.is_dir and os.path.exists(self.path):
            try:
                self.size_bytes = os.path.getsize(self.path)
            except Exception:
                self.size_bytes = 0

        self.size_str: str = "文件夹" if self.is_dir else format_file_size(self.size_bytes)
        self.icon: QPixmap = get_app_icon(self.path, display_name=self.name, target_size=64)


class ShelfItemWidget(QFrame):
    """Visual row for an individual staged file item."""
    removed = Signal(str)  # emits file path to remove

    def __init__(self, item: StagedFileItem, parent=None, theme: str = "dark"):
        super().__init__(parent)
        self.item = item
        self.theme: str = (theme or "dark").strip().lower()
        self._drag_start_pos: Optional[QPoint] = None
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # 1. File Icon (28x28 HiDPI)
        self.lbl_icon = QLabel(self)
        self.lbl_icon.setFixedSize(28, 28)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if item.icon and not item.icon.isNull():
            dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
            px = max(28, int(round(28 * dpr)))
            icon_pm = item.icon.scaled(
                px, px,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_pm.setDevicePixelRatio(dpr)
            self.lbl_icon.setPixmap(icon_pm)
        layout.addWidget(self.lbl_icon)

        # 2. Name & Size Information
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 2, 0, 2)
        info_col.setSpacing(1)

        self.lbl_name = QLabel(item.name, self)
        self.lbl_name.setToolTip(item.path)

        self.lbl_size = QLabel(item.size_str, self)

        info_col.addWidget(self.lbl_name)
        info_col.addWidget(self.lbl_size)
        layout.addLayout(info_col, stretch=1)

        # 3. Quick Remove Button
        self.btn_del = QPushButton(self)
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setIconSize(QSize(13, 13))
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setToolTip("从中转站移除（不删除原文件）")
        self.btn_del.clicked.connect(lambda: self.removed.emit(self.item.path))
        layout.addWidget(self.btn_del)
        self.apply_theme(self.theme)

    def apply_theme(self, theme: str):
        self.theme = (theme or "dark").strip().lower()
        tok = get_theme_tokens(self.theme)
        is_light = self.theme == "light"
        self.setStyleSheet(f"""
            ShelfItemWidget {{
                background-color: {tok["well_bg"]};
                border: 1px solid {tok["well_border"]};
                border-radius: 8px;
                font-family: {_SHELF_FONT_CSS};
            }}
            ShelfItemWidget:hover {{
                background-color: {tok["well_hover_bg"]};
                border: 1px solid rgba(59, 130, 246, 0.40);
            }}
        """)
        self.lbl_name.setStyleSheet(
            f"font-family: {_SHELF_FONT_CSS}; font-size: 12px; font-weight: 600; color: {tok['text_primary']};"
        )
        self.lbl_size.setStyleSheet(
            f"font-family: {_SHELF_FONT_CSS}; font-size: 10px; color: {tok['text_muted']};"
        )
        self.btn_del.setIcon(_shelf_icon("close", size=14, color=tok["text_muted"], light=is_light))
        self.btn_del.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                padding: 0px;
                border-radius: 11px;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.16);
            }
        """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._drag_start_pos:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 10:
            return

        # Start native OLE drag for single item
        drag = QDrag(self)
        mime = QMimeData()
        if os.path.exists(self.item.path):
            mime.setUrls([QUrl.fromLocalFile(self.item.path)])
            drag.setMimeData(mime)
            if self.item.icon and not self.item.icon.isNull():
                dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
                px = max(40, int(round(40 * dpr)))
                drag_pm = self.item.icon.scaled(
                    px, px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                drag_pm.setDevicePixelRatio(dpr)
                drag.setPixmap(drag_pm)
                drag.setHotSpot(QPoint(20, 20))
            drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
        self._drag_start_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if os.path.exists(self.item.path):
                try:
                    os.startfile(self.item.path)
                except Exception:
                    pass
        super().mouseDoubleClickEvent(event)


class FileShelfWindow(QWidget):
    """
    Desktop File Shelf & Staging Basket Window:
    - Screen Edge Pinned (Left or Right)
    - State A (Collapsed Handle): 14px x 108px translucent acrylic tab.
    - State B (Expanded Staging Shelf): 310px x 400px comprehensive workbench.
    """
    items_changed = Signal(int, int)  # count, total bytes
    shelf_toggled = Signal(bool)      # is_expanded

    HANDLE_W = 14
    HANDLE_H = 108
    EXPANDED_W = 310
    EXPANDED_H = 400

    def __init__(self, config_manager: Optional[ConfigManager] = None, parent=None):
        super().__init__(parent)
        self.config_manager: ConfigManager = config_manager or ConfigManager()
        self.staged_items: List[StagedFileItem] = []

        cfg = self.config_manager.get_file_shelf_config()
        self.edge: str = cfg.get("edge", "right")             # "right" or "left"
        self.auto_collapse: bool = cfg.get("auto_collapse", True)
        self.pinned: bool = cfg.get("pinned", False)
        self.y_ratio: float = cfg.get("y_ratio", 0.45)
        self.hide_in_fullscreen: bool = cfg.get("hide_in_fullscreen", True)
        self.theme: str = str(self.config_manager.get("theme", "dark") or "dark").lower()
        self.is_expanded: bool = False

        self._drag_all_start: Optional[QPoint] = None
        self._is_animating: bool = False
        self._game_suppressed: bool = False
        self._is_closed: bool = False
        self._winevent_hook = None
        self._win_event_proc = None
        self._hook_box = [None, None]
        self._destroyed_guard_connected: bool = False
        self._suppress_timer: Optional[QTimer] = None

        # Window Attributes
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)

        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(220)
        self.anim.finished.connect(self._on_anim_finished)

        self._init_ui()
        self.set_theme(self.theme)
        self.update_geometry_for_state(animate=False)
        self._init_fullscreen_monitor()

    def _current_theme(self) -> str:
        if self.config_manager and hasattr(self.config_manager, "get"):
            return str(self.config_manager.get("theme", self.theme) or "dark").lower()
        return self.theme

    def _init_ui(self):
        # Master outer layout
        self.master_layout = QVBoxLayout(self)
        self.master_layout.setContentsMargins(0, 0, 0, 0)
        self.master_layout.setSpacing(0)

        # Container for expanded mode content
        self.expanded_container = QWidget(self)
        self.expanded_container.setObjectName("shelfContainer")
        self.expanded_container.setVisible(False)
        box = QVBoxLayout(self.expanded_container)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        # 1. Header Bar (Title, Count Badge, Pin, Close)
        header = QHBoxLayout()
        header.setSpacing(6)

        self.lbl_title = QLabel("文件中转站", self.expanded_container)
        header.addWidget(self.lbl_title)

        self.lbl_badge = QLabel("0 项", self.expanded_container)
        header.addWidget(self.lbl_badge)
        header.addStretch()

        # 右上角操作组：同尺寸 / 同矢量图标 / 同样式，彻底告别 emoji 空白方块
        actions = QHBoxLayout()
        actions.setSpacing(2)

        self.btn_pin = QPushButton(self.expanded_container)
        self.btn_pin.setToolTip("固定展开窗口 (防止自动缩进)")
        self.btn_pin.clicked.connect(self.toggle_pin)

        self.btn_clear = QPushButton(self.expanded_container)
        self.btn_clear.setToolTip("清空中转暂存 (不删除本地原文件)")
        self.btn_clear.clicked.connect(self.clear_items)

        self.btn_close = QPushButton(self.expanded_container)
        self.btn_close.setToolTip("缩回屏幕边缘")
        self.btn_close.clicked.connect(self.collapse_shelf)

        for btn in (self.btn_pin, self.btn_clear, self.btn_close):
            btn.setFixedSize(26, 26)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(btn)

        header.addLayout(actions)

        box.addLayout(header)

        # 2. Scroll Area for Staged Items
        self.scroll_area = QScrollArea(self.expanded_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)

        # Empty State Placeholder
        self.empty_placeholder = EmptyZone(self.list_container, theme=self.theme)
        self.empty_placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty_box = QVBoxLayout(self.empty_placeholder)
        empty_box.setContentsMargins(12, 16, 12, 16)
        empty_box.setSpacing(8)
        empty_box.addStretch(1)

        self.lbl_drag_hint = QLabel(self.empty_placeholder)
        self.lbl_drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_hint_title = QLabel("拖入文件暂存", self.empty_placeholder)
        self.lbl_hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_hint_sub = QLabel("支持多批次暂存与一键拖出", self.empty_placeholder)
        self.lbl_hint_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_box.addWidget(self.lbl_drag_hint)
        empty_box.addWidget(self.lbl_hint_title)
        empty_box.addWidget(self.lbl_hint_sub)
        empty_box.addStretch(1)
        self.list_layout.insertWidget(0, self.empty_placeholder)

        self.scroll_area.setWidget(self.list_container)
        box.addWidget(self.scroll_area, stretch=1)

        # 3. Bottom Action Bar (Drag All Pill + Archive Dropdown + Copy Paths)
        bottom_box = QVBoxLayout()
        bottom_box.setSpacing(8)

        # Main "Drag All" button
        self.btn_drag_all = QPushButton("按住拖出全部文件", self.expanded_container)
        self.btn_drag_all.setFixedHeight(34)
        self.btn_drag_all.setCursor(Qt.CursorShape.OpenHandCursor)
        self.btn_drag_all.setToolTip("按住此按钮拖拽，可一次性将暂存文件拖入微信、邮件、浏览器或文件夹")
        self.btn_drag_all.setEnabled(False)
        self.btn_drag_all.installEventFilter(self)
        bottom_box.addWidget(self.btn_drag_all)

        # Secondary Actions Row (Archive to Category + Copy Paths)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)

        self.btn_archive = QPushButton("归档至分类 ▾", self.expanded_container)
        self.btn_archive.setFixedHeight(28)
        self.btn_archive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_archive.clicked.connect(lambda: self.show_archive_menu(self.btn_archive))
        sub_row.addWidget(self.btn_archive, stretch=1)

        self.btn_copy = QPushButton("复制路径", self.expanded_container)
        self.btn_copy.setFixedHeight(28)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setToolTip("复制所有暂存文件的绝对路径至剪贴板")
        self.btn_copy.clicked.connect(self.copy_paths)
        sub_row.addWidget(self.btn_copy)

        bottom_box.addLayout(sub_row)
        box.addLayout(bottom_box)

        self.master_layout.addWidget(self.expanded_container)

        base_font = QFont()
        base_font.setFamilies(_SHELF_FONT_FAMILIES)
        base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        self.setFont(base_font)

    def set_theme(self, theme: str):
        self.theme = (theme or "dark").strip().lower()
        tok = get_theme_tokens(self.theme)
        is_light = self.theme == "light"

        self.lbl_title.setStyleSheet(
            f"font-family: {_SHELF_FONT_CSS}; font-size: 13px; font-weight: 600; color: {tok['text_primary']};"
        )
        badge_border = "rgba(37, 99, 235, 0.24)" if is_light else "rgba(59, 130, 246, 0.32)"
        self.lbl_badge.setStyleSheet(f"""
            background-color: {tok["badge_bg"]};
            color: {tok["badge_fg"]};
            font-family: {_SHELF_FONT_CSS};
            font-size: 10px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 9px;
            border: 1px solid {badge_border};
        """)

        btn_qss = HEADER_BTN_LIGHT_QSS if is_light else HEADER_BTN_QSS
        for btn in (self.btn_pin, self.btn_clear, self.btn_close):
            btn.setStyleSheet(btn_qss)
        self.btn_pin.setIcon(_shelf_icon("pin", self.pinned, light=is_light))
        self.btn_clear.setIcon(_shelf_icon("trash", light=is_light))
        self.btn_close.setIcon(_shelf_icon("close", light=is_light))

        sb_handle = "rgba(15, 23, 42, 0.20)" if is_light else "rgba(255, 255, 255, 0.20)"
        self.scroll_area.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; font-family: {_SHELF_FONT_CSS}; }}
            QWidget {{ background: transparent; }}
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 4px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

        self.empty_placeholder.set_theme(self.theme)
        inbox_color = "#64748b" if is_light else "#94a3b8"
        self.lbl_drag_hint.setPixmap(_shelf_icon("inbox", size=36, color=inbox_color).pixmap(36, 36))
        self.lbl_hint_title.setStyleSheet(
            f"font-family: {_SHELF_FONT_CSS}; font-size: 12px; font-weight: 600; color: {tok['text_secondary']};"
        )
        self.lbl_hint_sub.setStyleSheet(
            f"font-family: {_SHELF_FONT_CSS}; font-size: 11px; color: {tok['text_muted']};"
        )

        dis_bg = "rgba(15, 23, 42, 0.05)" if is_light else "rgba(255, 255, 255, 0.05)"
        dis_border = "rgba(15, 23, 42, 0.08)" if is_light else "rgba(255, 255, 255, 0.06)"
        self.btn_drag_all.setStyleSheet(f"""
            QPushButton {{
                background-color: #3b82f6;
                color: #ffffff;
                padding: 0px 10px;
                font-family: {_SHELF_FONT_CSS};
                font-size: 12px;
                font-weight: 600;
                border-radius: 17px;
                border: 1px solid #2563eb;
            }}
            QPushButton:hover {{
                background-color: #2563eb;
            }}
            QPushButton:pressed {{
                background-color: #1d4ed8;
            }}
            QPushButton:disabled {{
                background-color: {dis_bg};
                color: {tok["text_muted"]};
                border: 1px solid {dis_border};
            }}
        """)

        self.btn_archive.setStyleSheet(f"""
            QPushButton {{
                background: {tok["well_bg"]};
                color: {tok["text_secondary"]};
                padding: 0px 10px;
                font-family: {_SHELF_FONT_CSS};
                font-size: 11px;
                font-weight: 500;
                border-radius: 8px;
                border: 1px solid {tok["well_border"]};
            }}
            QPushButton:hover {{
                background: {tok["well_hover_bg"]};
                border-color: rgba(59, 130, 246, 0.35);
            }}
        """)
        self.btn_copy.setStyleSheet(f"""
            QPushButton {{
                background: {tok["well_bg"]};
                color: {tok["text_muted"]};
                padding: 0px 10px;
                font-family: {_SHELF_FONT_CSS};
                font-size: 11px;
                font-weight: 500;
                border-radius: 8px;
                border: 1px solid {tok["well_border"]};
            }}
            QPushButton:hover {{
                background: {tok["well_hover_bg"]};
                color: {tok["text_primary"]};
            }}
        """)

        for i in range(self.list_layout.count()):
            w = self.list_layout.itemAt(i).widget()
            if isinstance(w, ShelfItemWidget):
                w.apply_theme(self.theme)
        self.update()

    def eventFilter(self, watched, event):
        """Captures mouse press on the drag-all button to initiate batch OLE drag."""
        if watched == self.btn_drag_all:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_all_start = event.pos()
            elif etype == QEvent.Type.MouseMove and self._drag_all_start is not None:
                if not (event.buttons() & Qt.MouseButton.LeftButton):
                    self._drag_all_start = None
                elif (event.pos() - self._drag_all_start).manhattanLength() >= 6:
                    self._drag_all_start = None
                    self.start_drag_all()
            elif etype == QEvent.Type.MouseButtonRelease:
                self._drag_all_start = None
        return super().eventFilter(watched, event)

    # =========================================================================
    # Geometry & Edge Positioning
    # =========================================================================

    def get_target_geometry(self, expanded: bool) -> QRect:
        """Computes target geometry anchored to current screen edge."""
        w = self.EXPANDED_W if expanded else self.HANDLE_W
        h = self.EXPANDED_H if expanded else self.HANDLE_H
        screen = QApplication.primaryScreen()
        cursor_pos = QCursor.pos()
        for s in QApplication.screens():
            if s.geometry().contains(cursor_pos):
                screen = s
                break
        if screen is None:
            return QRect(0, 100, w, h)

        geo = screen.availableGeometry()
        y = geo.top() + int(geo.height() * self.y_ratio) - h // 2
        y = max(geo.top() + 10, min(y, geo.bottom() - h - 10))

        if self.edge == "left":
            x = geo.left() + (8 if expanded else 0)
        else:
            x = geo.right() - w + 1 - (8 if expanded else 0)

        return QRect(x, y, w, h)

    def update_geometry_for_state(self, animate: bool = True):
        target = self.get_target_geometry(self.is_expanded)
        if animate:
            self._is_animating = True
            self.anim.stop()
            self.anim.setStartValue(self.geometry())
            self.anim.setEndValue(target)
            self.anim.setEasingCurve(QEasingCurve.Type.OutCubic if self.is_expanded else QEasingCurve.Type.InOutQuad)
            self.anim.start()
        else:
            self.setGeometry(target)
            self.expanded_container.setVisible(self.is_expanded)
            self.update()

    def _on_anim_finished(self):
        self._is_animating = False
        self.expanded_container.setVisible(self.is_expanded)
        self.update()

    # =========================================================================
    # Expand / Collapse Lifecycle
    # =========================================================================

    def expand_shelf(self):
        if self.is_expanded:
            return
        self.is_expanded = True
        self.update_geometry_for_state(animate=True)
        self.shelf_toggled.emit(True)

    def collapse_shelf(self, animate: bool = True):
        if not self.is_expanded:
            return
        grabber = QWidget.mouseGrabber()
        if grabber:
            try:
                grabber.releaseMouse()
            except Exception:
                pass
        self.is_expanded = False
        if not animate:
            self.expanded_container.setVisible(False)
        self.update_geometry_for_state(animate=animate)
        self.shelf_toggled.emit(False)

    def toggle_shelf(self):
        if self.is_expanded:
            self.collapse_shelf()
        else:
            self.expand_shelf()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.btn_pin.setIcon(_shelf_icon("pin", self.pinned, light=(self._current_theme() == "light")))
        cfg = self.config_manager.get_file_shelf_config()
        cfg["pinned"] = self.pinned
        self.config_manager.set_file_shelf_config(cfg)

    # =========================================================================
    # Fullscreen & Game Auto-Suppression
    # =========================================================================

    @property
    def _win_event_hook(self):
        return self._winevent_hook

    @_win_event_hook.setter
    def _win_event_hook(self, value):
        self._winevent_hook = value

    def _init_fullscreen_monitor(self):
        """
        Installs lightweight EVENT_SYSTEM_FOREGROUND hook and gentle polling
        to auto-hide the file shelf handle whenever a fullscreen game or video takes focus.
        """
        self.unhook_fullscreen_monitor()

        if sys.platform == "win32" and user32 and WinEventProcType:
            self_ref = weakref.ref(self)

            def _on_winevent(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
                try:
                    w = self_ref()
                    if w is None or getattr(w, "_is_closed", False):
                        return
                    w.check_fullscreen_suppression()
                except Exception:
                    pass

            self._win_event_proc = WinEventProcType(_on_winevent)
            try:
                self._winevent_hook = user32.SetWinEventHook(
                    EVENT_SYSTEM_FOREGROUND,
                    EVENT_SYSTEM_FOREGROUND,
                    None,
                    self._win_event_proc,
                    0,
                    0,
                    WINEVENT_OUTOFCONTEXT
                )
            except Exception:
                self._winevent_hook = None
                self._win_event_proc = None

            self._hook_box[0] = self._winevent_hook
            self._hook_box[1] = self._win_event_proc

            if not getattr(self, "_destroyed_guard_connected", False):
                hook_box = self._hook_box

                def _unhook_on_destroy(*_args):
                    hk, proc = hook_box[0], hook_box[1]
                    if hk and user32:
                        ok = False
                        try:
                            ok = bool(user32.UnhookWinEvent(hk))
                        except Exception:
                            ok = False
                        if not ok and proc is not None:
                            _RETIRED_WINEVENT_PROCS.append(proc)
                    hook_box[0] = None
                    hook_box[1] = None

                self.destroyed.connect(_unhook_on_destroy)
                self._destroyed_guard_connected = True

        self._suppress_timer = QTimer(self)
        self._suppress_timer.setInterval(2000)
        self._suppress_timer.timeout.connect(self.check_fullscreen_suppression)
        self._suppress_timer.start()

    def unhook_fullscreen_monitor(self):
        """Safely unhooks WinEvent callback and stops background timers."""
        if getattr(self, "anim", None) is not None:
            self.anim.stop()
        self._is_animating = False

        if getattr(self, "_suppress_timer", None) is not None:
            self._suppress_timer.stop()
            self._suppress_timer = None

        hook = getattr(self, "_winevent_hook", None)
        proc = getattr(self, "_win_event_proc", None)
        if hook and user32:
            ok = False
            try:
                ok = bool(user32.UnhookWinEvent(hook))
            except Exception:
                ok = False
            if not ok and proc is not None:
                _RETIRED_WINEVENT_PROCS.append(proc)
        self._winevent_hook = None
        self._win_event_proc = None
        if hasattr(self, "_hook_box"):
            self._hook_box[0] = None
            self._hook_box[1] = None

    def check_fullscreen_suppression(self):
        """
        Detects whether current foreground window is a game or fullscreen application.
        When true, completely hides the edge handle to avoid obstructing gameplay/videos.
        When false, restores the edge handle immediately.
        """
        if getattr(self, "_is_closed", False):
            return

        if not getattr(self, "hide_in_fullscreen", True):
            if getattr(self, "_game_suppressed", False):
                self._game_suppressed = False
                cfg = self.config_manager.get_file_shelf_config()
                if cfg.get("enabled", True):
                    self.show()
            return

        if not user32:
            return

        try:
            fg = user32.GetForegroundWindow()
            if not fg:
                return

            my_hwnd = 0
            try:
                my_hwnd = int(self.internalWinId() or 0)
            except Exception:
                pass

            if my_hwnd and fg == my_hwnd:
                return

            is_game_or_fs = is_window_fullscreen_or_game(fg)

            if is_game_or_fs:
                if not getattr(self, "_game_suppressed", False):
                    self._game_suppressed = True
                    if self.is_expanded:
                        self.collapse_shelf(animate=False)
                    self.hide()
            else:
                if getattr(self, "_game_suppressed", False):
                    self._game_suppressed = False
                    cfg = self.config_manager.get_file_shelf_config()
                    if cfg.get("enabled", True):
                        self.show()
        except Exception:
            pass

    def set_game_suppressed(self, suppressed: bool):
        """Allows direct programmatic control for testing or manual overrides."""
        self._game_suppressed = suppressed
        if suppressed:
            if self.is_expanded:
                self.collapse_shelf(animate=False)
            self.hide()
        else:
            cfg = self.config_manager.get_file_shelf_config()
            if cfg.get("enabled", True):
                self.show()

    def showEvent(self, event):
        if getattr(self, "_is_closed", False):
            self._is_closed = False
            self._init_fullscreen_monitor()
        super().showEvent(event)

    def close_widget(self):
        self._is_closed = True
        self.unhook_fullscreen_monitor()
        self.hide()

    def closeEvent(self, event):
        self._is_closed = True
        self.unhook_fullscreen_monitor()
        super().closeEvent(event)

    # =========================================================================
    # Items Staging Operations
    # =========================================================================

    def add_paths(self, paths: List[str]):
        """Adds a batch of physical paths to the staging shelf."""
        added = 0
        existing = {item.path.lower() for item in self.staged_items}
        for p in paths:
            if not p or not os.path.exists(p):
                continue
            abs_p = os.path.abspath(p)
            if abs_p.lower() in existing:
                continue
            item = StagedFileItem(abs_p)
            self.staged_items.append(item)
            existing.add(abs_p.lower())
            added += 1

        if added > 0:
            self._refresh_items_ui()
            if not self.is_expanded:
                self.expand_shelf()

    def remove_path(self, path: str):
        """Removes a specific file path from the staging shelf."""
        self.staged_items = [i for i in self.staged_items if i.path != path]
        self._refresh_items_ui()
        if len(self.staged_items) == 0 and self.auto_collapse and not self.pinned:
            self.collapse_shelf()

    def clear_items(self):
        """Clears all staged items."""
        self.staged_items.clear()
        self._refresh_items_ui()
        if self.auto_collapse and not self.pinned:
            self.collapse_shelf()

    def _refresh_items_ui(self):
        """Rebuilds the list of ShelfItemWidget."""
        # Clear previous items in list layout (except placeholder and stretch)
        for i in reversed(range(self.list_layout.count())):
            item = self.list_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, ShelfItemWidget):
                w.deleteLater()

        count = len(self.staged_items)
        total_size = sum(i.size_bytes for i in self.staged_items)

        self.empty_placeholder.setVisible(count == 0)
        self.btn_drag_all.setEnabled(count > 0)

        # Update badge
        size_str = format_file_size(total_size)
        self.lbl_badge.setText(f"{count} 项 · {size_str}" if count > 0 else "0 项")

        # Insert new widgets
        cur_theme = self._current_theme()
        if cur_theme != self.theme:
            self.set_theme(cur_theme)
        for idx, item in enumerate(self.staged_items):
            w = ShelfItemWidget(item, self.list_container, theme=cur_theme)
            w.removed.connect(self.remove_path)
            self.list_layout.insertWidget(idx, w)

        self.items_changed.emit(count, total_size)

    # =========================================================================
    # Native OLE Drag-In & Drag-Out
    # =========================================================================

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText() or mime.hasFormat(STACKY_ITEM_MIME):
            event.acceptProposedAction()
            if not self.is_expanded:
                self.expand_shelf()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText() or mime.hasFormat(STACKY_ITEM_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()
        stacky_drag_token = ""
        stacky_item_path = ""
        if mime.hasFormat(STACKY_ITEM_MIME):
            try:
                raw = bytes(mime.data(STACKY_ITEM_MIME)).decode("utf-8")
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    stacky_drag_token = str(payload.get("drag_token", "") or "")
                    item_data = payload.get("item_data")
                    if isinstance(item_data, dict):
                        stacky_item_path = str(item_data.get("path", "") or "")
            except Exception:
                pass

        urls = mime.urls()
        paths = []
        if urls:
            for u in urls:
                p = u.toLocalFile()
                if p and os.path.exists(p):
                    paths.append(p)
        elif mime.hasText():
            text = mime.text().strip()
            for line in text.splitlines():
                clean = line.strip().strip('"').strip("'")
                if clean and os.path.exists(clean):
                    paths.append(clean)

        if not paths and stacky_item_path and os.path.exists(stacky_item_path):
            paths.append(stacky_item_path)

        if paths:
            if stacky_drag_token:
                mark_cross_folder_drop(stacky_drag_token)
            event.acceptProposedAction()
            self.add_paths(paths)

    def start_drag_all(self):
        """Initiates a native Windows OLE CF_HDROP drag operation for all staged files."""
        if not self.staged_items:
            return

        valid_urls = [QUrl.fromLocalFile(i.path) for i in self.staged_items if os.path.exists(i.path)]
        if not valid_urls:
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls(valid_urls)
        drag.setMimeData(mime)

        # Drag preview thumbnail (hotspot uses logical coordinates)
        preview = self._create_drag_preview(len(valid_urls))
        drag.setPixmap(preview)
        dpr = max(1.0, float(preview.devicePixelRatio() or 1.0))
        drag.setHotSpot(QPoint(int(round(preview.width() / dpr)) // 2, int(round(preview.height() / dpr)) // 2))

        result = drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    def _create_drag_preview(self, count: int) -> QPixmap:
        """Generates a stylish translucent card thumbnail for batch drag operation."""
        size = 140
        h = 54
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        img = QImage(
            max(1, int(round(size * dpr))),
            max(1, int(round(h * dpr))),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        cur_theme = self._current_theme()
        tok = get_theme_tokens(cur_theme)
        is_light = cur_theme == "light"
        rect = QRectF(2, 2, size - 4, h - 4)
        path = create_squircle_path(rect, 10)
        p.fillPath(path, QColor(248, 250, 252, 240) if is_light else QColor(30, 41, 59, 230))
        p.strokePath(path, QPen(QColor(37, 99, 235, 175) if is_light else QColor(96, 165, 250, 180), 1.5))

        # First item's icon if available
        if self.staged_items and self.staged_items[0].icon and not self.staged_items[0].icon.isNull():
            icon_px = max(32, int(round(32 * dpr)))
            icon_pm = self.staged_items[0].icon.scaled(
                icon_px, icon_px,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_pm.setDevicePixelRatio(dpr)
            p.drawPixmap(12, 11, icon_pm)

        preview_font = QFont()
        preview_font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"])
        preview_font.setPointSize(11)
        preview_font.setWeight(QFont.Weight.DemiBold)
        preview_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        p.setFont(preview_font)
        p.setPen(QColor(tok["text_primary"]))
        p.drawText(QRectF(52, 10, size - 56, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{count} 个文件")

        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm

    # =========================================================================
    # Archiving & Utility Actions
    # =========================================================================

    def show_archive_menu(self, parent_btn: QWidget):
        """Opens popup menu to archive all staged items into Stacky categories."""
        if not self.staged_items:
            return

        cur_theme = self._current_theme()
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss(cur_theme))

        # Get Stacky categories
        folders = self.config_manager.get_folders()
        categories = [f.get("name") for f in folders if f.get("name")]
        if not categories:
            categories = ["常用应用", "工作办公", "系统工具"]

        for cat_name in categories:
            action = menu.addAction(f"归档至「{cat_name}」")
            action.triggered.connect(lambda checked=False, c=cat_name: self.archive_to_category(c))

        menu.exec(parent_btn.mapToGlobal(QPoint(0, parent_btn.height() + 4)))

    def archive_to_category(self, category_name: str):
        """Moves/copies staged files into the designated Stacky storage category directory."""
        from storage_manager import default_managed_storage_root
        import shutil

        root = self.config_manager.get_managed_storage_root() or default_managed_storage_root()
        target_dir = os.path.join(root, category_name)
        os.makedirs(target_dir, exist_ok=True)

        moved_count = 0
        for item in list(self.staged_items):
            if not os.path.exists(item.path):
                continue
            dest = os.path.join(target_dir, os.path.basename(item.path))
            try:
                # If target exists with same name, make unique
                base, ext = os.path.splitext(os.path.basename(item.path))
                c = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{base}_{c}{ext}")
                    c += 1
                shutil.copy2(item.path, dest)
                moved_count += 1
            except Exception as e:
                print(f"Failed to copy {item.path} to {dest}: {e}")

        if moved_count > 0:
            notify_shell_change(target_dir)
            self.clear_items()

    def copy_paths(self):
        """Copies all staged file paths to system clipboard."""
        if not self.staged_items:
            return
        paths_text = "\n".join(i.path for i in self.staged_items)
        QApplication.clipboard().setText(paths_text)

    # =========================================================================
    # Mouse Click & Painting
    # =========================================================================

    def mousePressEvent(self, event: QMouseEvent):
        if not self.is_expanded and event.button() == Qt.MouseButton.LeftButton:
            self.expand_shelf()
            event.accept()
            return
        if self.is_expanded and event.button() == Qt.MouseButton.LeftButton and event.position().y() < 46:
            # 拖标题栏即可搬动，松手吸附到最近一边并记住纵向位置
            self._title_drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        origin = getattr(self, "_title_drag_origin", None)
        if origin is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if getattr(self, "_title_drag_origin", None) is not None:
            self._title_drag_origin = None
            self._snap_after_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _snap_after_drag(self):
        """松手后吸附到最近边缘，并把自己的纵向比例写进配置，下次原地出现。"""
        screen = (self.screen() or QApplication.primaryScreen())
        if screen is None:
            return
        geo = screen.availableGeometry()
        center_y = self.y() + self.height() // 2
        self.y_ratio = max(0.05, min(0.95, (center_y - geo.top()) / max(1, geo.height())))
        self.edge = "left" if (self.x() + self.width() // 2) < geo.center().x() else "right"
        cfg = self.config_manager.get_file_shelf_config()
        cfg["y_ratio"] = round(self.y_ratio, 4)
        cfg["edge"] = self.edge
        self.config_manager.set_file_shelf_config(cfg)
        self.update_geometry_for_state(animate=False)

    def paintEvent(self, event):
        cur_theme = self._current_theme()
        if cur_theme != self.theme:
            self.set_theme(cur_theme)

        tok = get_theme_tokens(cur_theme)
        is_light = cur_theme == "light"

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if not self.is_expanded:
            # Render Collapsed Edge Handle
            rect = QRectF(0, 0, self.width(), self.height())
            path = QPainterPath()
            r = 6.0
            if self.edge == "right":
                # Rounded on left side, flat against right edge
                path.moveTo(rect.right(), rect.top())
                path.lineTo(rect.left() + r, rect.top())
                path.quadTo(rect.left(), rect.top(), rect.left(), rect.top() + r)
                path.lineTo(rect.left(), rect.bottom() - r)
                path.quadTo(rect.left(), rect.bottom(), rect.left() + r, rect.bottom())
                path.lineTo(rect.right(), rect.bottom())
                path.closeSubpath()
            else:
                # Rounded on right side, flat against left edge
                path.moveTo(rect.left(), rect.top())
                path.lineTo(rect.right() - r, rect.top())
                path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
                path.lineTo(rect.right(), rect.bottom() - r)
                path.quadTo(rect.right(), rect.bottom(), rect.right() - r, rect.bottom())
                path.lineTo(rect.left(), rect.bottom())
                path.closeSubpath()

            # Handle body
            if is_light:
                handle_color = QColor(248, 250, 252, 236)
                border_color = QColor(37, 99, 235, 145)
                dot_color = QColor(51, 65, 85, 195)
            elif cur_theme == "oled":
                handle_color = QColor(10, 12, 16, 235)
                border_color = QColor(59, 130, 246, 160)
                dot_color = QColor(255, 255, 255, 180)
            else:
                handle_color = QColor(22, 25, 34, 220)
                border_color = QColor(59, 130, 246, 160)
                dot_color = QColor(255, 255, 255, 180)
            painter.fillPath(path, handle_color)
            painter.strokePath(path, QPen(border_color, 1.0))

            # Grip Dots / Chevron Indicator
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            center_x = rect.center().x()
            center_y = rect.center().y()
            for dy in (-12, -4, 4, 12):
                painter.drawEllipse(QPointF(center_x, center_y + dy), 1.5, 1.5)
        else:
            # Render Expanded Acrylic Squircle Window
            rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
            path = create_squircle_path(rect, 16)

            grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            c_top = QColor(tok["surface_top"])
            c_bot = QColor(tok["surface_bottom"])
            c_top.setAlpha(246 if is_light else 242)
            c_bot.setAlpha(244 if is_light else 246)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bot)
            painter.fillPath(path, grad)

            # 1px Specular highlight / slate hairline border
            border_c = QColor(15, 23, 42, 32) if is_light else QColor(255, 255, 255, 38)
            painter.strokePath(path, QPen(border_c, 1.0))

            # Ambient inner glow
            radial = QRadialGradient(rect.center().x(), rect.top(), rect.width() * 0.8)
            radial.setColorAt(0.0, QColor(59, 130, 246, 18 if is_light else 25))
            radial.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillPath(path, radial)

            # 顶部高光 + 标题栏分隔线：让面板有层次，不再是一块纯色
            sheen = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + 60)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 150 if is_light else 26))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, sheen)
            div_c = QColor(15, 23, 42, 20) if is_light else QColor(255, 255, 255, 22)
            painter.setPen(QPen(div_c, 1))
            painter.drawLine(int(rect.left()) + 12, 54, int(rect.right()) - 12, 54)

        painter.end()
