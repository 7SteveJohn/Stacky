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
import shutil
import ctypes
from ctypes import wintypes
from typing import List, Dict, Any, Optional

from PySide6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize, QUrl, Signal,
    QPropertyAnimation, QEasingCurve, QMimeData, QTimer
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
from icon_utils import get_app_icon_pixmap as get_app_icon, create_squircle_path


def _shelf_icon(kind: str, active: bool = False, size: int = 20,
                color: Optional[str] = None) -> QIcon:
    """纯矢量图标。emoji 字形在 Windows 上常渲染成空白，右上角会退化成空方块。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(size / 20.0, size / 20.0)
    c = QColor(color) if color else (QColor("#60a5fa") if active else QColor("#cbd5e1"))
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
        border-radius: 6px;
    }
    QPushButton:hover { background: rgba(255, 255, 255, 0.10); }
    QPushButton:pressed { background: rgba(255, 255, 255, 0.16); }
"""


class EmptyZone(QWidget):
    """空态投放区。QWidget 的 QSS 背景在 Qt6 下不保证绘制，纯 QPainter 才靠得住。"""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QBrush(QColor("#171a21")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 12, 12)
        pen = QPen(QColor(148, 163, 184, 70), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 12, 12)
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

    def __init__(self, item: StagedFileItem, parent=None):
        super().__init__(parent)
        self.item = item
        self._drag_start_pos: Optional[QPoint] = None
        self.setFixedHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            ShelfItemWidget {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
            ShelfItemWidget:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(96, 165, 250, 0.35);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # 1. File Icon (28x28)
        self.lbl_icon = QLabel(self)
        self.lbl_icon.setFixedSize(28, 28)
        self.lbl_icon.setScaledContents(True)
        if item.icon and not item.icon.isNull():
            self.lbl_icon.setPixmap(item.icon.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self.lbl_icon)

        # 2. Name & Size Information
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 2, 0, 2)
        info_col.setSpacing(1)

        self.lbl_name = QLabel(item.name, self)
        self.lbl_name.setStyleSheet("font-size: 12px; font-weight: 600; color: #f8fafc;")
        self.lbl_name.setToolTip(item.path)

        self.lbl_size = QLabel(item.size_str, self)
        self.lbl_size.setStyleSheet("font-size: 10px; color: #94a3b8;")

        info_col.addWidget(self.lbl_name)
        info_col.addWidget(self.lbl_size)
        layout.addLayout(info_col, stretch=1)

        # 3. Quick Remove Button
        self.btn_del = QPushButton("✕", self)
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setToolTip("从中转站移除 (不删除本地原文件)")
        self.btn_del.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #64748b;
                border: none;
                border-radius: 11px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.2);
                color: #ef4444;
            }
        """)
        self.btn_del.clicked.connect(lambda: self.removed.emit(self.item.path))
        layout.addWidget(self.btn_del)

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
                drag.setPixmap(self.item.icon.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
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
        self.is_expanded: bool = False

        self._drag_all_start: Optional[QPoint] = None
        self._is_animating: bool = False
        self._game_suppressed: bool = False
        self._winevent_hook = None
        self._win_event_proc = None
        self._suppress_timer: Optional[QTimer] = None

        # Window Attributes
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)

        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(220)
        self.anim.finished.connect(self._on_anim_finished)

        self._init_ui()
        self.update_geometry_for_state(animate=False)
        self._init_fullscreen_monitor()

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

        lbl_title = QLabel("文件中转站", self.expanded_container)
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffffff;")
        header.addWidget(lbl_title)

        self.lbl_badge = QLabel("0 项", self.expanded_container)
        self.lbl_badge.setStyleSheet("""
            background-color: rgba(59, 130, 246, 0.18);
            color: #60a5fa;
            font-size: 10px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 4px;
            border: 1px solid rgba(59, 130, 246, 0.35);
        """)
        header.addWidget(self.lbl_badge)
        header.addStretch()

        # 右上角操作组：同尺寸 / 同矢量图标 / 同样式，彻底告别 emoji 空白方块
        actions = QHBoxLayout()
        actions.setSpacing(2)

        self.btn_pin = QPushButton(self.expanded_container)
        self.btn_pin.setToolTip("固定展开窗口 (防止自动缩进)")
        self.btn_pin.clicked.connect(self.toggle_pin)

        btn_clear = QPushButton(self.expanded_container)
        btn_clear.setToolTip("清空中转暂存 (不删除本地原文件)")
        btn_clear.clicked.connect(self.clear_items)

        btn_close = QPushButton(self.expanded_container)
        btn_close.setToolTip("缩回屏幕边缘")
        btn_close.clicked.connect(self.collapse_shelf)

        for btn in (self.btn_pin, btn_clear, btn_close):
            btn.setFixedSize(26, 26)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(HEADER_BTN_QSS)
            actions.addWidget(btn)

        self.btn_pin.setIcon(_shelf_icon("pin", self.pinned))
        btn_clear.setIcon(_shelf_icon("trash"))
        btn_close.setIcon(_shelf_icon("close"))
        header.addLayout(actions)

        box.addLayout(header)

        # 2. Scroll Area for Staged Items
        self.scroll_area = QScrollArea(self.expanded_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 4px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                border-radius: 2px;
                min-height: 20px;
            }
        """)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)

        # Empty State Placeholder
        self.empty_placeholder = EmptyZone(self.list_container)
        self.empty_placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        empty_box = QVBoxLayout(self.empty_placeholder)
        empty_box.setContentsMargins(12, 16, 12, 16)
        empty_box.setSpacing(8)
        empty_box.addStretch(1)

        lbl_drag_hint = QLabel(self.empty_placeholder)
        lbl_drag_hint.setPixmap(_shelf_icon("inbox", size=40, color="#7c8ba3").pixmap(40, 40))
        lbl_drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_hint_title = QLabel("拖入文件暂存", self.empty_placeholder)
        lbl_hint_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_hint_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #cbd5e1;")

        lbl_hint_sub = QLabel("随时批量拖出或归档至大文件夹", self.empty_placeholder)
        lbl_hint_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_hint_sub.setStyleSheet("font-size: 10px; color: #64748b;")

        empty_box.addWidget(lbl_drag_hint)
        empty_box.addWidget(lbl_hint_title)
        empty_box.addWidget(lbl_hint_sub)
        empty_box.addStretch(1)
        self.list_layout.insertWidget(0, self.empty_placeholder)

        self.scroll_area.setWidget(self.list_container)
        box.addWidget(self.scroll_area, stretch=1)

        # 3. Bottom Action Bar (Drag All Pill + Archive Dropdown + Copy Paths)
        bottom_box = QVBoxLayout()
        bottom_box.setSpacing(8)

        # Main "Drag All" button
        self.btn_drag_all = QPushButton("⇪ 全部拖出 (按住拖拽至目标窗口)", self.expanded_container)
        self.btn_drag_all.setFixedHeight(34)
        self.btn_drag_all.setCursor(Qt.CursorShape.OpenHandCursor)
        self.btn_drag_all.setToolTip("按住此按钮拖拽，可一次性将所有暂存文件拖入微信、邮件、浏览器或文件夹")
        self.btn_drag_all.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #3b82f6);
                color: #ffffff;
                font-size: 12px;
                font-weight: 600;
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1d4ed8, stop:1 #2563eb);
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.05);
                color: #64748b;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
        """)
        self.btn_drag_all.setEnabled(False)
        self.btn_drag_all.installEventFilter(self)
        bottom_box.addWidget(self.btn_drag_all)

        # Secondary Actions Row (Archive to Category + Copy Paths)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)

        btn_archive = QPushButton("归档至大文件夹 ▾", self.expanded_container)
        btn_archive.setFixedHeight(28)
        btn_archive.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_archive.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: #e2e8f0;
                font-size: 11px;
                font-weight: 500;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                border-color: rgba(96, 165, 250, 0.3);
            }
        """)
        btn_archive.clicked.connect(lambda: self.show_archive_menu(btn_archive))
        sub_row.addWidget(btn_archive, stretch=1)

        btn_copy = QPushButton("复制路径", self.expanded_container)
        btn_copy.setFixedHeight(28)
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.setToolTip("复制所有暂存文件的绝对路径至剪贴板")
        btn_copy.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: #94a3b8;
                font-size: 11px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                color: #ffffff;
            }
        """)
        btn_copy.clicked.connect(self.copy_paths)
        sub_row.addWidget(btn_copy)

        bottom_box.addLayout(sub_row)
        box.addLayout(bottom_box)

        self.master_layout.addWidget(self.expanded_container)

    def eventFilter(self, watched, event):
        """Captures mouse press on the drag-all button to initiate batch OLE drag."""
        if watched == self.btn_drag_all:
            if event.type() == QMouseEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_all_start = event.pos()
            elif event.type() == QMouseEvent.Type.MouseMove and self._drag_all_start:
                if (event.pos() - self._drag_all_start).manhattanLength() >= 6:
                    self.start_drag_all()
                    self._drag_all_start = None
            elif event.type() == QMouseEvent.Type.MouseButtonRelease:
                self._drag_all_start = None
        return super().eventFilter(watched, event)

    # =========================================================================
    # Geometry & Edge Positioning
    # =========================================================================

    def get_target_geometry(self, expanded: bool) -> QRect:
        """Computes target geometry anchored to current screen edge."""
        screen = QApplication.primaryScreen()
        cursor_pos = QCursor.pos()
        for s in QApplication.screens():
            if s.geometry().contains(cursor_pos):
                screen = s
                break

        geo = screen.availableGeometry()
        w = self.EXPANDED_W if expanded else self.HANDLE_W
        h = self.EXPANDED_H if expanded else self.HANDLE_H
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
        self.btn_pin.setIcon(_shelf_icon("pin", self.pinned))
        cfg = self.config_manager.get_file_shelf_config()
        cfg["pinned"] = self.pinned
        self.config_manager.set_file_shelf_config(cfg)

    # =========================================================================
    # Fullscreen & Game Auto-Suppression
    # =========================================================================

    def _init_fullscreen_monitor(self):
        """
        Installs lightweight EVENT_SYSTEM_FOREGROUND hook and gentle polling
        to auto-hide the file shelf handle whenever a fullscreen game or video takes focus.
        """
        if sys.platform == "win32" and user32 and WinEventProcType:
            def _on_winevent(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
                try:
                    self.check_fullscreen_suppression()
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

        self._suppress_timer = QTimer(self)
        self._suppress_timer.setInterval(2000)
        self._suppress_timer.timeout.connect(self.check_fullscreen_suppression)
        self._suppress_timer.start()

    def check_fullscreen_suppression(self):
        """
        Detects whether current foreground window is a game or fullscreen application.
        When true, completely hides the edge handle to avoid obstructing gameplay/videos.
        When false, restores the edge handle immediately.
        """
        if not getattr(self, "hide_in_fullscreen", True):
            if getattr(self, "_game_suppressed", False):
                self._game_suppressed = False
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
                my_hwnd = int(self.winId())
            except Exception:
                pass

            if fg == my_hwnd:
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

    def closeEvent(self, event):
        if getattr(self, "_suppress_timer", None):
            self._suppress_timer.stop()
            self._suppress_timer = None
        if getattr(self, "_winevent_hook", None) and user32:
            try:
                user32.UnhookWinEvent(self._winevent_hook)
            except Exception:
                pass
            self._winevent_hook = None
        self._win_event_proc = None
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
        for idx, item in enumerate(self.staged_items):
            w = ShelfItemWidget(item, self.list_container)
            w.removed.connect(self.remove_path)
            self.list_layout.insertWidget(idx, w)

        self.items_changed.emit(count, total_size)

    # =========================================================================
    # Native OLE Drag-In & Drag-Out
    # =========================================================================

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
            if not self.is_expanded:
                self.expand_shelf()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        paths = []
        if urls:
            for u in urls:
                p = u.toLocalFile()
                if p and os.path.exists(p):
                    paths.append(p)
        elif event.mimeData().hasText():
            text = event.mimeData().text().strip()
            for line in text.splitlines():
                clean = line.strip().strip('"').strip("'")
                if os.path.exists(clean):
                    paths.append(clean)

        if paths:
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

        # Drag preview thumbnail
        preview = self._create_drag_preview(len(valid_urls))
        drag.setPixmap(preview)
        drag.setHotSpot(QPoint(preview.width() // 2, preview.height() // 2))

        result = drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    def _create_drag_preview(self, count: int) -> QPixmap:
        """Generates a stylish translucent card thumbnail for batch drag operation."""
        size = 140
        img = QImage(size, 54, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(2, 2, size - 4, 50)
        path = create_squircle_path(rect, 10)
        p.fillPath(path, QColor(30, 41, 59, 230))
        p.strokePath(path, QPen(QColor(96, 165, 250, 180), 1.5))

        # First item's icon if available
        if self.staged_items and self.staged_items[0].icon:
            p.drawPixmap(12, 11, self.staged_items[0].icon.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        p.setFont(QFont("Segoe UI Variable Display, Microsoft YaHei UI", 11, QFont.Weight.Bold))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(52, 10, size - 56, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{count} 个文件")

        p.end()
        return QPixmap.fromImage(img)

    # =========================================================================
    # Archiving & Utility Actions
    # =========================================================================

    def show_archive_menu(self, parent_btn: QWidget):
        """Opens popup menu to archive all staged items into Stacky categories."""
        if not self.staged_items:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e2029;
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                height: 28px;
                padding: 4px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
        """)

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
            handle_color = QColor(22, 25, 34, 220)
            painter.fillPath(path, handle_color)
            painter.strokePath(path, QPen(QColor(59, 130, 246, 160), 1.0))

            # Grip Dots / Chevron Indicator
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 180))
            center_x = rect.center().x()
            center_y = rect.center().y()
            for dy in (-12, -4, 4, 12):
                painter.drawEllipse(QPointF(center_x, center_y + dy), 1.5, 1.5)
        else:
            # Render Expanded Acrylic Squircle Window
            rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
            path = create_squircle_path(rect, 16)

            # Deep acrylic dark glass fill
            bg_color = QColor(18, 21, 29, 242)
            painter.fillPath(path, bg_color)

            # 1px Specular highlight border
            painter.strokePath(path, QPen(QColor(255, 255, 255, 35), 1.0))

            # Ambient inner glow
            radial = QRadialGradient(rect.center().x(), rect.top(), rect.width() * 0.8)
            radial.setColorAt(0.0, QColor(59, 130, 246, 25))
            radial.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillPath(path, radial)

            # 顶部高光 + 标题栏分隔线：让面板有层次，不再是一块纯色
            sheen = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + 60)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, sheen)
            painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
            painter.drawLine(int(rect.left()) + 12, 54, int(rect.right()) - 12, 54)


        painter.end()
