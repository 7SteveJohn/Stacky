from stacky.paths import ROOT
import os
import time
import sys
import ctypes
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Tuple

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, QTimer, Signal, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QFont, QFontMetrics, QPen, QCursor, QAction, QActionGroup, QIcon, QPixmap
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QMenu,
    QInputDialog, QFileDialog, QApplication, QMessageBox
)

from config_manager import ConfigManager
import ui_kit
from ui_kit import theme_pick
from acrylic_utils import apply_blur, remove_blur
from widgets import BigFolderCard, FolderTitleLabel, AppSlotWidget
from icon_utils import (
    create_squircle_path, parse_dropped_mime_data, is_drop_acceptable,
    is_internal_item_drag, mark_cross_folder_drop, STACKY_ITEM_MIME
)
from web_dialog import AddWebBookmarkDialog
from folder_drawer import ExpandedFolderDrawer
from shortcut_utils import generate_mydock_shortcut
import desktop_layer
from desktop_layer import DesktopWatchdog, pin_to_desktop, WM_NCHITTEST, HTTRANSPARENT, HTCLIENT
from storage_manager import ManagedStorageEngine, default_managed_storage_root
from grid_snap import calculate_magnetic_snap, get_all_desktop_card_rects, calculate_magnetic_resize, resolve_card_collision
import widget_grid
from stacky.core import layout_scale

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
WM_HOTKEY = 0x0312

HOTKEY_ID = 9527
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
VK_F = 0x46


def _toast_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.Medium)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    return font


class ToastWidget(QWidget):
    """Subtle non-blocking capsule pill HUD near card top inner margin."""

    _PILL_HEIGHT = 30
    _LEFT_PAD = 12
    _DOT_SIZE = 6
    _DOT_GAP = 7
    _RIGHT_PAD = 13

    def __init__(self, parent=None):
        super().__init__(parent)
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFont(self._toast_font())
        self.current_opacity: float = 0.0
        self.message_text: str = ""
        self._elided_text: str = ""
        self.is_error: bool = False

        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._on_opacity_tick)
        self.anim.finished.connect(self._on_anim_finished)

        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._start_fade_out)

    @staticmethod
    def _toast_font() -> QFont:
        return _toast_font()

    def _on_opacity_tick(self, val):
        self.current_opacity = float(val)
        self.update()

    def _on_anim_finished(self):
        if self.current_opacity <= 0.02:
            self.hide()

    def _start_fade_out(self):
        self.anim.stop()
        self.anim.setStartValue(self.current_opacity)
        self.anim.setEndValue(0.0)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.start()

    def _layout_pill(self):
        font = self._toast_font()
        self.setFont(font)
        fm = QFontMetrics(font)

        h = self._PILL_HEIGHT
        chrome_w = self._LEFT_PAD + self._DOT_SIZE + self._DOT_GAP + self._RIGHT_PAD

        parent = self.parentWidget()
        if parent is not None and parent.rect().width() > 32:
            parent_rect = parent.rect()
            max_w = max(chrome_w + 24, parent_rect.width() - 16)
        else:
            parent_rect = None
            max_w = 320

        avail_text_w = max(24, max_w - chrome_w)
        self._elided_text = fm.elidedText(
            self.message_text, Qt.TextElideMode.ElideRight, avail_text_w
        )
        text_w = fm.horizontalAdvance(self._elided_text)
        w = min(max_w, max(chrome_w + 16, text_w + chrome_w))
        self.resize(w, h)

        if parent_rect is not None:
            if parent_rect.width() >= w + 16:
                x = max(8, min((parent_rect.width() - w) // 2, parent_rect.width() - w - 8))
            else:
                x = max(0, (parent_rect.width() - w) // 2)
            max_y = max(0, parent_rect.height() - h - 6)
            y = max(0, min(10, max_y))
            self.move(x, y)

    def show_message(self, message: str, is_error: bool = False, duration_ms: int = 2200):
        if not message:
            self.timer.stop()
            self.anim.stop()
            self.current_opacity = 0.0
            self.hide()
            return

        self.message_text = message
        self.is_error = is_error
        self._layout_pill()

        self.anim.stop()
        self.current_opacity = 1.0
        self.show()
        self.raise_()
        self.update()

        self.timer.start(duration_ms)

    def paintEvent(self, event):
        if self.current_opacity <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        op = max(0.0, min(1.0, float(self.current_opacity)))
        theme = getattr(self.parent(), "theme", "dark") if self.parent() is not None else "dark"

        if theme == "light":
            bg_color = QColor(255, 255, 255, int(244 * op))
            border_color = QColor(15, 23, 42, int(28 * op))
            text_color = QColor(15, 23, 42, int(245 * op))
        else:
            bg_color = QColor(20, 23, 31, int(242 * op))
            border_color = QColor(255, 255, 255, int(38 * op))
            text_color = QColor(244, 246, 250, int(252 * op))

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self.height() / 2.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        p.fillPath(path, bg_color)
        p.strokePath(path, QPen(border_color, 1.0))

        dot_color = QColor("#ef4444") if self.is_error else QColor("#3b82f6")
        dot_color.setAlpha(int(255 * op))
        dot_x = float(self._LEFT_PAD)
        dot_y = (float(self.height()) - float(self._DOT_SIZE)) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot_color)
        p.drawEllipse(QRectF(dot_x, dot_y, float(self._DOT_SIZE), float(self._DOT_SIZE)))

        font = self._toast_font()
        p.setFont(font)
        p.setPen(text_color)
        text_x = float(self._LEFT_PAD + self._DOT_SIZE + self._DOT_GAP)
        text_w = max(0.0, float(self.width()) - text_x - float(self._RIGHT_PAD))
        text_rect = QRectF(text_x, 0.0, text_w, float(self.height()))
        p.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._elided_text or self.message_text,
        )
        p.end()


class _PageTurnOverlay(QWidget):
    """Lightweight 150ms OutCubic cross-fade + ±12px horizontal slide micro-transition overlay."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._old_pm: Optional[QPixmap] = None
        self._direction: int = 1
        self._progress: float = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(150)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._on_tick)
        self.anim.finished.connect(self._on_finished)
        self.hide()

    def start_transition(self, old_pm: QPixmap, direction: int):
        if old_pm is None or old_pm.isNull() or self.parentWidget() is None:
            self.stop_and_hide()
            return
        self.anim.stop()
        self._old_pm = old_pm
        self._direction = 1 if direction >= 0 else -1
        self._progress = 0.0
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(150)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.start()

    def stop_and_hide(self):
        self.anim.stop()
        self._old_pm = None
        self.hide()

    def _on_tick(self, val):
        self._progress = float(val)
        if self._progress >= 0.995:
            self.stop_and_hide()
            return
        self.update()

    def _on_finished(self):
        self._old_pm = None
        self.hide()

    def paintEvent(self, event):
        if self._old_pm is None or self._old_pm.isNull():
            return
        alpha = max(0.0, min(1.0, 1.0 - self._progress))
        if alpha <= 0.01:
            return
        pm_logical = self._old_pm.deviceIndependentSize()
        pm_w = float(pm_logical.width())
        pm_h = float(pm_logical.height())
        if pm_w <= 1.0 or pm_h <= 1.0:
            return
        w, h = float(self.width()), float(self.height())
        if abs(w - pm_w) > 2.0 or abs(h - pm_h) > 2.0:
            self.stop_and_hide()
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            parent = self.parentWidget()
            radius = float(getattr(parent, "radius", 26))
            if w > 2.0 and h > 2.0:
                p.setClipPath(create_squircle_path(QRectF(1.0, 1.0, w - 2.0, h - 2.0), radius))
            dx = -12.0 * self._direction * self._progress
            p.setOpacity(alpha)
            p.drawPixmap(QRectF(dx, 0.0, pm_w, pm_h), self._old_pm, QRectF(self._old_pm.rect()))
        finally:
            p.end()


class BigFolderWindow(QWidget):
    """
    Main Mobile-Style Big Folder (Nine-Grid) Desktop Widget & Launcher.
    Coexists peacefully with other desktop tools:
    - Zero hooking into SHELLDLL_DefView (leaves the shell untouched).
    - HWND_BOTTOM wallpaper level + MA_NOACTIVATE (never steals focus).
    - Single-click launch from a Dock launcher via IPC or Desktop Card.
    - Global Hotkey (Alt+Space / Alt+F) to summon instant search & drawer.
    - Multi-folder categories & mouse wheel pagination.
    """
    _instances: List['BigFolderWindow'] = []
    # 共享存储引擎的消息只允许一个窗口显示：拖放期间为发起窗口，其余为主窗口
    _storage_message_owner = None

    def __init__(
        self,
        config_manager: ConfigManager,
        instance_id: Optional[str] = None,
        storage_engine: Optional[ManagedStorageEngine] = None,
        manager: Optional[Any] = None
    ):
        super().__init__()
        self.config_manager = config_manager
        self.manager = manager
        # 布局以「保存时的屏幕」为画布，运行时等比换算到当前分辨率
        self.layout_scale = layout_scale.scale_for_config(config_manager)
        self.drag_position = QPoint()
        self.mouse_press_global_pos = QPoint()
        self.is_moving_window = False
        self.is_left_pressed = False
        self.drag_threshold = 7
        self._is_resizing_card = False
        self._resize_start_global = QPoint()
        self._resize_start_card_size = QSize()

        # Instance resolution & category binding
        instances = self.config_manager.get_instances()
        if instance_id:
            self.instance_id = instance_id
            inst = self.config_manager.get_instance(instance_id)
            self.bound_folder_name = inst.get("folder_name", "常用应用") if inst else "常用应用"
        else:
            if instances:
                self.instance_id = instances[0].get("instance_id", "default")
                self.bound_folder_name = instances[0].get("folder_name", "常用应用")
            else:
                self.instance_id = "default"
                self.bound_folder_name = self.config_manager.get_active_folder_name()

        # Primary window role (only primary window binds system-wide Alt+Space hotkey)
        existing_primary = next((w for w in BigFolderWindow._instances if getattr(w, "is_primary", False)), None)
        if existing_primary is None:
            self.is_primary = True
        elif self.instance_id == "default" and getattr(existing_primary, "instance_id", None) != "default":
            existing_primary.is_primary = False
            existing_primary.unregister_global_hotkey()
            self.is_primary = True
        else:
            self.is_primary = False
        BigFolderWindow._instances.append(self)

        self.active_page = 0
        self.slot_widgets: List[AppSlotWidget] = []
        self.current_drawer: Optional[ExpandedFolderDrawer] = None
        self.is_summoned_front: bool = False
        self._summon_saved_pos: Optional[QPoint] = None

        self.init_window_flags()
        self.init_ui()

        # Desktop Layer Watchdog & Physical Storage Engine
        self.desktop_watchdog = DesktopWatchdog(self, check_interval_ms=1000, parent=self)
        if storage_engine is not None:
            self.storage_engine = storage_engine
            self._owns_storage_engine = False
        else:
            self.storage_engine = ManagedStorageEngine(self.config_manager, parent=self)
            self._owns_storage_engine = True

        self.storage_engine.storage_synced.connect(self.on_storage_synced)
        self.storage_engine.storage_folders_imported.connect(self.on_storage_folders_imported)
        self.storage_engine.operation_message.connect(self._on_storage_operation_message)

        if self.config_manager.is_managed_storage_enabled():
            curr_f = self.get_current_folder()
            if curr_f and curr_f.get("storage_mapped"):
                self.storage_engine.sync_folder_from_storage(self.get_current_folder_name())

        self.apply_layer_mode()
        self.apply_blur_effect()
        self.restore_position()
        if self.is_primary:
            self.register_global_hotkey()

    def _on_storage_operation_message(self, message: str, is_error: bool = False):
        """共享存储引擎的消息只弹一次，避免每个桌面卡片各弹一条。"""
        owner = BigFolderWindow._storage_message_owner
        if owner is self or (owner is None and getattr(self, "is_primary", False)):
            self.toast.show_message(message, is_error)

    def get_current_folder(self) -> Optional[Dict[str, Any]]:
        if hasattr(self, "bound_folder_name") and self.bound_folder_name:
            f = self.config_manager.get_folder_by_name(self.bound_folder_name)
            if f:
                return f
        return self.config_manager.get_active_folder()

    def get_current_folder_name(self) -> str:
        if hasattr(self, "bound_folder_name") and self.bound_folder_name:
            return self.bound_folder_name
        return self.config_manager.get_active_folder_name()

    def get_current_folder_index(self) -> int:
        name = self.get_current_folder_name()
        for idx, f in enumerate(self.config_manager.get_folders()):
            if f.get("name") == name:
                return idx
        return self.config_manager.get_active_folder_index()

    def set_current_folder_items(self, items: list, auto_save: bool = True):
        name = self.get_current_folder_name()
        self.config_manager.set_folder_items(name, items, auto_save=auto_save)
        if self.get_current_folder_index() == self.config_manager.get_active_folder_index():
            self.config_manager.set("items", items, auto_save=False)

    def init_window_flags(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)

    def init_ui(self):
        s = self.layout_scale
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(
            *(layout_scale.to_screen(v, s) for v in (18, 16, 18, 14)))
        self.main_layout.setSpacing(layout_scale.to_screen(8, s))
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setMouseTracking(True)

        radius = layout_scale.to_screen(self.config_manager.get("border_radius", 26), s)
        opacity = self.config_manager.get("opacity", 0.88)
        theme = self.config_manager.get("theme", "dark")
        self.theme = theme

        # 1. Big Folder Card (1:1 Pure Square Mobile Squircle)
        self.card = BigFolderCard(radius=radius, opacity=opacity, theme=theme, parent=self)
        self.card.wheel_scrolled.connect(self.on_card_wheel)
        self.card.blank_double_clicked.connect(self.open_expanded_drawer)
        
        self.card_layout = QGridLayout(self.card)
        self.card_layout.setContentsMargins(16, 16, 16, 16)
        self.card_layout.setSpacing(9)

        # Toast notification overlay
        self.toast = ToastWidget(self.card)

        # 2. Folder Title Label
        title_text = self.get_display_title_text()
        self.title_label = FolderTitleLabel(title_text, parent=self)
        self.title_label.set_layout_scale(s)
        self.title_label.setToolTip("点击展开 · 右键切换分类")
        self.title_label.request_click.connect(self.open_expanded_drawer)
        self.title_label.request_open_drawer.connect(self.open_expanded_drawer)
        self.title_label.request_menu.connect(self.show_folder_switch_menu)

        self.main_layout.addWidget(self.card)
        self.main_layout.addWidget(self.title_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.update_title_visibility()
        self.rebuild_grid()

        # Drag hover auto-expansion timer (0.6s)
        self.drag_hover_timer = QTimer(self)
        self.drag_hover_timer.setSingleShot(True)
        self.drag_hover_timer.setInterval(600)
        self.drag_hover_timer.timeout.connect(self._on_drag_hover_timeout)

    def register_global_hotkey(self):
        """按 config 的 hotkey 注册全局快捷键；被占用时回退 Alt+F。"""
        try:
            spec = self.config_manager.get("hotkey", "Alt+Space") or "Alt+Space"
            parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
            mods = 0
            vk = VK_SPACE
            named = {"space": VK_SPACE, "f": VK_F}
            for part in parts:
                if part == "alt":
                    mods |= MOD_ALT
                elif part == "ctrl":
                    mods |= MOD_CONTROL
                elif part == "shift":
                    mods |= MOD_SHIFT
                elif part == "win":
                    mods |= 0x0008
                elif part in named:
                    vk = named[part]
                elif len(part) == 1 and part.isalnum():
                    vk = ord(part.upper())
            if not mods:
                mods = MOD_ALT
            mods |= MOD_NOREPEAT
            hwnd = int(self.winId())
            if not ctypes.windll.user32.RegisterHotKey(hwnd, HOTKEY_ID, mods, vk):
                ctypes.windll.user32.RegisterHotKey(
                    hwnd, HOTKEY_ID, MOD_ALT | MOD_NOREPEAT, VK_F)
        except Exception as e:
            print("Failed to register global hotkey:", e)

    def unregister_global_hotkey(self):
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.UnregisterHotKey(hwnd, HOTKEY_ID)
        except Exception:
            pass

    def get_grid_dimensions(self) -> Tuple[int, int]:
        """获取当前大文件夹卡片的行数列数规格 (优先读取实例独立记忆，保底全局默认 3x3)"""
        if hasattr(self, "instance_id") and self.instance_id:
            inst = self.config_manager.get_instance(self.instance_id)
            if inst:
                r = inst.get("rows")
                c = inst.get("cols")
                if r is not None and c is not None:
                    return max(1, min(6, int(r))), max(1, min(6, int(c)))
        r = self.config_manager.get("grid_rows", 3)
        c = self.config_manager.get("grid_cols", 3)
        return max(1, min(6, int(r))), max(1, min(6, int(c)))

    def get_layout_state(self) -> dict:
        """布局与分页的唯一数据源：渲染、翻页、标题都从这里取，避免各算一套。"""
        default_rows, default_cols = self.get_grid_dimensions()
        s = self.layout_scale
        saved_size = None
        if hasattr(self, "instance_id") and self.instance_id:
            inst = self.config_manager.get_instance(self.instance_id)
            if inst and inst.get("card_width") and inst.get("card_height"):
                saved_size = QSize(layout_scale.to_screen(inst["card_width"], s),
                                   layout_scale.to_screen(inst["card_height"], s))
        if saved_size:
            # 拉过尺寸的卡片按可用空间自动排行列，与 rebuild_grid 完全同源。
            # 尺寸、格子、内边距一起缩放，行列数才不会随分辨率改变而跳变。
            base = layout_scale.to_screen(max(24, int(self.config_manager.get("slot_size", 62))), s)
            pad = layout_scale.to_screen(32, s)
            gap = max(4, layout_scale.to_screen(9, s))
            cols = max(1, min(9, (saved_size.width() - pad + gap) // (base + gap)))
            rows = max(1, min(9, (saved_size.height() - pad + gap) // (base + gap)))
        else:
            rows, cols = default_rows, default_cols
        slots = max(1, rows * cols)
        folder = self.get_current_folder()
        items = folder.get("items", []) if folder else []
        pages = max(1, (len(items) + slots - 1) // slots)
        page = max(0, min(self.active_page, pages - 1))
        return {"rows": rows, "cols": cols, "slots": slots, "items": items,
                "pages": pages, "page": page, "saved_size": saved_size}

    def get_display_title_text(self) -> str:
        state = self.get_layout_state()
        name = self.get_current_folder_name()
        card = getattr(self, "card", None)
        if card is not None and hasattr(card, "set_pagination"):
            return name
        pages, page = state["pages"], state["page"]
        if pages > 1:
            # 页数多时圆点会拉成长串压住图标名，超过 5 页改用 1/8 计数
            if pages <= 5:
                dots = ["●" if p == page else "○" for p in range(pages)]
                return f"{name}  " + " ".join(dots)
            return f"{name}  {page + 1}/{pages}"
        return name

    def update_title_visibility(self):
        mode = self.config_manager.get("show_title", "always")
        if mode == "hide":
            self.title_label.setVisible(False)
        elif mode == "hover":
            self.title_label.setVisible(False)
        else:
            self.title_label.setVisible(True)

    def rebuild_grid(self):
        for slot in self.slot_widgets:
            self.card_layout.removeWidget(slot)
            slot.hide()
            slot.setParent(None)
            slot.deleteLater()
        self.slot_widgets.clear()
        hint = getattr(self, "_empty_hint", None)
        if hint is not None:
            self.card_layout.removeWidget(hint)
            hint.hide()
            hint.setParent(None)
            hint.deleteLater()
            self._empty_hint = None

        # 行列/页数全部来自唯一数据源，渲染与翻页不再各算一套
        state = self.get_layout_state()
        rows, cols, total_slots = state["rows"], state["cols"], state["slots"]
        s = self.layout_scale
        pad = layout_scale.to_screen(32, s)
        gap = max(4, layout_scale.to_screen(9, s))
        min_slot = max(12, layout_scale.to_screen(24, s))
        base_slot = layout_scale.to_screen(self.config_manager.get("slot_size", 62), s)
        base_icon = layout_scale.to_screen(self.config_manager.get("icon_size", 46), s)
        self.card_layout.setSpacing(gap)
        slot_size = base_slot
        saved_size = state["saved_size"]
        if saved_size:
            fit_w = (saved_size.width() - pad - (cols - 1) * gap) // cols
            fit_h = (saved_size.height() - pad - (rows - 1) * gap) // rows
            slot_size = max(min_slot, min(fit_w, fit_h))
        icon_size = max(max(10, layout_scale.to_screen(16, s)),
                        int(base_icon * slot_size / max(1, base_slot)))
        theme = self.config_manager.get("theme", "dark")
        self.theme = theme

        # 格子间距恒为 9px；多余空间放到四周内边距，避免图标被拉开
        for r_idx in range(6):
            self.card_layout.setRowStretch(r_idx, 0)
        for c_idx in range(6):
            self.card_layout.setColumnStretch(c_idx, 0)

        items = state["items"]
        self.active_page = state["page"]      # 越界页在这里统一收回
        if hasattr(self.card, "set_pagination"):
            self.card.set_pagination(state["page"], state["pages"])
        self.title_label.set_text(self.get_display_title_text())
        if getattr(self, "manager", None) is not None and hasattr(self.manager, "_sync_glass_if_changed"):
            self.manager._sync_glass_if_changed()

        # Authentic Mobile 8+1 Overflow Cluster Pattern:
        # If items > 9 in a 3x3 layout and on page 0, slot 9 becomes a 4-in-1 mini cluster preview!
        use_cluster = (rows == 3 and cols == 3 and len(items) > 9 and self.active_page == 0)
        offset = self.active_page * total_slots
        open_mode_for_card = self.config_manager.get_open_mode_for_card()
        app_disp_mode = self.config_manager.get_app_display_mode()
        show_admin_b = self.config_manager.is_admin_badge_enabled()
        show_running_b = self.config_manager.is_running_badge_enabled()

        for slot_idx in range(total_slots):
            r = slot_idx // cols
            c = slot_idx % cols

            if use_cluster and slot_idx == 8:
                # 9th slot is 4-in-1 Mini Cluster!
                cluster_items = items[8:]
                slot = AppSlotWidget(
                    index=slot_idx,
                    item_data=None,
                    cluster_items=cluster_items,
                    slot_size=slot_size,
                    icon_size=icon_size,
                    theme=theme,
                    open_mode=open_mode_for_card,
                    display_mode=app_disp_mode,
                    show_admin_badge=show_admin_b,
                    show_running_badge=show_running_b,
                    parent=self.card
                )
                slot._settings_signal_wired = True
                slot.request_open_drawer.connect(self.open_expanded_drawer)
                slot.request_open_settings.connect(lambda: self.action_open_settings(0))
                slot.request_global_menu.connect(self.show_global_menu)
                slot.status_toast.connect(self.toast.show_message)
            else:
                actual_idx = offset + slot_idx
                item_data = items[actual_idx] if actual_idx < len(items) else None
                slot = AppSlotWidget(
                    index=actual_idx,
                    item_data=item_data,
                    cluster_items=None,
                    slot_size=slot_size,
                    icon_size=icon_size,
                    theme=theme,
                    open_mode=open_mode_for_card,
                    display_mode=app_disp_mode,
                    show_admin_badge=show_admin_b,
                    show_running_badge=show_running_b,
                    parent=self.card
                )
                slot._settings_signal_wired = True
                slot.item_changed.connect(self.on_slot_item_changed)
                slot.item_removed.connect(self.on_slot_item_removed)
                slot.item_moved.connect(self.on_slot_item_moved)
                slot.request_add.connect(self.on_request_add)
                slot.request_add_web.connect(self.on_request_add_web)
                slot.request_open_drawer.connect(self.open_expanded_drawer)
                slot.request_open_settings.connect(lambda: self.action_open_settings(0))
                slot.request_global_menu.connect(self.show_global_menu)
                slot.request_move_to_desktop.connect(self.on_request_move_to_desktop)
                slot.request_recycle.connect(self.on_request_recycle)
                slot.status_toast.connect(self.toast.show_message)
                slot.item_launched.connect(self.on_slot_app_launched)

            self.card_layout.addWidget(slot, r, c)
            if self.card.isVisible():
                slot.show()
            self.slot_widgets.append(slot)

        if not items:
            # 空分类给一句轻量占位说明，避免用户误以为是空白异常
            hint = QLabel("暂无应用\n拖入快捷方式即可添加", self.card)
            hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint_color = theme_pick(self.config_manager.get("theme", "dark"),
                                   "rgba(15,23,42,0.58)", "rgba(255,255,255,0.45)")
            hint.setStyleSheet(
                'font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"; '
                f"color: {hint_color}; font-size: 12px; background: transparent;"
            )
            self.card_layout.addWidget(hint, 0, 0, max(1, rows), max(1, cols))
            if self.card.isVisible():
                hint.show()
            self._empty_hint = hint

        if saved_size:
            card_w, card_h = saved_size.width(), saved_size.height()
        else:
            card_w = cols * slot_size + (cols - 1) * gap + pad
            card_h = rows * slot_size + (rows - 1) * gap + pad
        # 余量均匀变成四周内边距，格子仍按缩放后的间距紧密排列
        used_w = cols * slot_size + (cols - 1) * gap
        used_h = rows * slot_size + (rows - 1) * gap
        extra_w, extra_h = max(0, card_w - used_w), max(0, card_h - used_h)
        ml, mt = extra_w // 2, extra_h // 2
        self.card_layout.setContentsMargins(ml, mt, extra_w - ml, extra_h - mt)
        overlay = getattr(self, "_page_turn_overlay", None)
        if overlay is not None and self.card.size() != QSize(card_w, card_h):
            overlay.stop_and_hide()
        self.card.setFixedSize(card_w, card_h)
        self.card_layout.activate()
        if hasattr(self, "toast") and self.toast is not None:
            if self.toast.isVisible():
                self.toast._layout_pill()
            self.toast.raise_()

        self.adjustSize()

    def _schedule_rebuild(self):
        if getattr(self, "_rebuild_scheduled", False):
            return
        self._rebuild_scheduled = True
        QTimer.singleShot(0, self._flush_rebuild)

    def _flush_rebuild(self):
        self._rebuild_scheduled = False
        self.rebuild_grid()

    def on_card_wheel(self, delta: int):
        """Scroll wheel over card switches page smoothly (prevents accidental category changes)"""
        state = self.get_layout_state()
        page_count = state["pages"]
        self.active_page = state["page"]

        if page_count <= 1:
            return

        direction = 0
        if delta < 0 and self.active_page < page_count - 1:
            direction = 1
        elif delta > 0 and self.active_page > 0:
            direction = -1
        else:
            return

        overlay = getattr(self, "_page_turn_overlay", None)
        if overlay is not None:
            overlay.stop_and_hide()

        old_pm = None
        if hasattr(self, "card") and self.card is not None and self.card.isVisible():
            try:
                old_pm = self.card.grab()
            except Exception:
                old_pm = None

        self.active_page += direction
        self._rebuild_scheduled = False
        self.rebuild_grid()

        if old_pm is not None and not old_pm.isNull() and self.card.isVisible():
            if overlay is None:
                overlay = _PageTurnOverlay(self.card)
                self._page_turn_overlay = overlay
            overlay.start_transition(old_pm, direction)
            if hasattr(self, "toast") and self.toast.isVisible():
                self.toast.raise_()

    def switch_folder(self, folder_index: int):
        folders = self.config_manager.get_folders()
        if 0 <= folder_index < len(folders):
            overlay = getattr(self, "_page_turn_overlay", None)
            if overlay is not None:
                overlay.stop_and_hide()
            new_name = folders[folder_index].get("name", "常用应用")
            self.bound_folder_name = new_name
            if hasattr(self, "instance_id") and self.instance_id:
                self.config_manager.update_instance(self.instance_id, {"folder_name": new_name})
            if getattr(self, "is_primary", False):
                self.config_manager.set_active_folder_index(folder_index)
            self.active_page = 0
            if hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled():
                self.storage_engine.sync_folder_from_storage(new_name)
            self.rebuild_grid()
            self.toast.show_message(f"已切换至 {new_name}")

    def show_folder_switch_menu(self, global_pos: QPoint):
        """Popup category pills menu when clicking the title label"""
        self._reset_mouse_press_state()
        menu = QMenu(self)
        theme = self.config_manager.get("theme", "dark")
        _menu_qss = getattr(ui_kit, "menu_qss", None)
        if callable(_menu_qss):
            menu.setStyleSheet(_menu_qss(theme))
        else:
            menu.setStyleSheet("""
            QMenu {
                background-color: rgba(26, 28, 36, 250);
                color: #f5f5f5;
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 12px;
                padding: 6px;
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 45);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: rgba(255, 255, 255, 25);
                margin: 4px 6px;
            }
            """)

        folders = self.config_manager.get_folders()
        curr_idx = self.get_current_folder_index()

        for idx, f in enumerate(folders):
            name = f.get("name", "未命名分类")
            count = len(f.get("items", []))
            prefix = "● " if idx == curr_idx else "○ "
            act = menu.addAction(f"{prefix}{name}")
            act.triggered.connect(lambda checked=False, i=idx: self.switch_folder(i))

        menu.addSeparator()
        act_add = menu.addAction("新建分类")
        act_add.triggered.connect(self.action_add_folder)

        act_rename = menu.addAction("重命名")
        act_rename.triggered.connect(self.action_rename_folder)

        if len(folders) > 1:
            act_del = menu.addAction("解散")
            act_del.triggered.connect(self.action_delete_folder)

        menu.addSeparator()
        act_tidy_smart = menu.addAction("智能收纳")
        act_tidy_smart.triggered.connect(lambda: self.action_tidy_desktop(auto_categorize=True))
        act_tidy_quick = menu.addAction("收纳到本分类")
        act_tidy_quick.triggered.connect(lambda: self.action_tidy_desktop(auto_categorize=False))
        act_reorg = menu.addAction("重整去重")
        act_reorg.triggered.connect(self.action_reorganize_categories)
        act_open_quick = menu.addAction("打开目录")
        act_open_quick.triggered.connect(self.action_open_storage_dir)

        menu.addSeparator()
        act_drawer = menu.addAction("展开搜索")
        act_drawer.triggered.connect(lambda: self.open_expanded_drawer(show_search=True))

        menu.exec(global_pos)

    def action_add_folder(self):
        new_name, ok = QInputDialog.getText(self, "新建分类", "请输入分类名称：")
        if ok and new_name.strip():
            new_idx = self.config_manager.add_folder(new_name.strip())
            self.switch_folder(new_idx)

    def action_delete_folder(self):
        curr_folder = self.get_current_folder()
        name = self.get_current_folder_name()
        reply = QMessageBox.question(
            self,
            "解散分类",
            f"确定要解散「{name}」吗？\n\n分类内的应用与快捷方式将放回桌面。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            idx = self.get_current_folder_index()
            restored_count = 0
            if hasattr(self, "storage_engine") and self.storage_engine:
                restored_count = self.storage_engine.dissolve_folder_to_desktop(name)

            if self.config_manager.delete_folder(idx):
                if len(BigFolderWindow._instances) > 1 and getattr(self, "bound_folder_name", None) == name:
                    if hasattr(self, "manager") and self.manager:
                        self.manager.remove_instance_window(self)
                    else:
                        self.close()
                else:
                    fallback_name = self.config_manager.get_active_folder_name()
                    for w in list(BigFolderWindow._instances):
                        if getattr(w, "bound_folder_name", None) == name:
                            w.bound_folder_name = fallback_name
                            w.active_page = 0
                            w.rebuild_grid()
                msg = f"已解散「{name}」，{restored_count} 项已放回桌面" if restored_count > 0 else f"已解散「{name}」"
                self.toast.show_message(msg, duration_ms=3000)

    def bring_to_front_and_toast(self, message: str = ""):
        """Ensures the window or launcher drawer is visible and optionally shows a toast."""
        launcher_mode = self.config_manager.get("launcher_mode", "hybrid")
        self.show()
        if launcher_mode == "summon":
            self.center_on_screen()
            self.raise_()
            self.activateWindow()
        else:
            self.apply_layer_mode()
            if message:
                self.toast.show_message(message)

    def is_drawer_open(self) -> bool:
        if self.current_drawer is None:
            return False
        try:
            return bool(self.current_drawer.isVisible() and not getattr(self.current_drawer, "is_closing", False))
        except (RuntimeError, AttributeError):
            self.current_drawer = None
            return False

    def on_drawer_closed(self, *args):
        sender = self.sender()
        if sender is not None and self.current_drawer is not None and sender is not self.current_drawer:
            return
        self.current_drawer = None
        self.show()
        self.card.show()
        self.card.setVisible(True)
        self.card.update()
        self.update_title_visibility()
        self.update()
        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.release_temporary_raise(immediate=True)
            self.apply_layer_mode()

    def close_expanded_drawer(self, immediately: bool = False):
        """Cleanly collapses and closes active drawer, disconnecting signals to prevent race conditions"""
        if self.is_drawer_open():
            d = self.current_drawer
            self.current_drawer = None
            try:
                d.closed.disconnect(self.on_drawer_closed)
            except Exception:
                pass
            if immediately:
                d.hide()
                d.close()
            else:
                d.collapse_and_close()
            self.show()
            self.card.show()
            self.card.setVisible(True)
            self.card.update()
            self.update_title_visibility()
            self.update()
            if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
                self.desktop_watchdog.release_temporary_raise(immediate=True)
                self.apply_layer_mode()

    def summon_card_to_front(self, target_screen: Optional[Any] = None):
        """
        Elevates this desktop big folder card directly to HWND_TOP.
        Presents the 3x3 big folder with its 9 app slots directly in front
        of the user on the active screen, exactly like an authentic mobile desktop launcher.
        """
        if self.is_drawer_open():
            self.close_expanded_drawer()

        self.is_summoned_front = True

        # Center on active target screen if not already within it
        if target_screen is not None:
            cur_center = self.geometry().center()
            target_geo = target_screen.geometry()
            avail_geo = target_screen.availableGeometry()
            if not target_geo.contains(cur_center):
                self._summon_saved_pos = self.pos()
                new_x = avail_geo.left() + (avail_geo.width() - self.width()) // 2
                new_y = avail_geo.top() + (avail_geo.height() - self.height()) // 2
                self.move(new_x, new_y)

        # Elevate to top
        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.acquire_summon_elevation()
        else:
            self.raise_()

        self.show()
        self.raise_()
        self.activateWindow()

    def dismiss_summon_card(self):
        """
        Seamlessly settles this desktop big folder card back to the desktop
        wallpaper layer (HWND_BOTTOM).
        """
        if not getattr(self, "is_summoned_front", False):
            return

        self.is_summoned_front = False

        # Restore original position if shifted across monitors
        if getattr(self, "_summon_saved_pos", None) is not None:
            self.move(self._summon_saved_pos)
            self._summon_saved_pos = None

        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.release_summon_elevation()
        else:
            self.apply_layer_mode()

        if hasattr(self, "toast"):
            self.toast.hide()

    def on_slot_app_launched(self, name: str):
        if getattr(self, "is_summoned_front", False):
            # Launched app! Settle folder back to wallpaper bottom after a brief 250ms feedback
            QTimer.singleShot(250, self.dismiss_summon_card)

    def toggle_summon(self, from_manager: bool = False, open_drawer: Optional[bool] = None):
        """
        Invoked via Dock launcher click (IPC) or Global Hotkey.
        Toggles quick drawer or activates launcher smoothly.
        """
        if not from_manager and getattr(self, "manager", None) is not None:
            self.manager.toggle_summon(open_drawer=open_drawer)
            return

        launcher_mode = self.config_manager.get("launcher_mode", "hybrid")

        if launcher_mode == "summon":
            if self.isVisible():
                self.hide()
            else:
                self.center_on_screen()
                self.show()
                self.raise_()
                self.activateWindow()
        else:
            # Check if drawer is open
            if self.is_drawer_open():
                self.close_expanded_drawer()
                return

            # Check if card is currently summon-elevated
            if getattr(self, "is_summoned_front", False):
                self.dismiss_summon_card()
                return

            # Detect active screen from cursor
            cursor_pos = QCursor.pos()
            active_screen = None
            for s in QApplication.screens():
                if s.geometry().contains(cursor_pos):
                    active_screen = s
                    break
            if not active_screen:
                active_screen = QApplication.screenAt(cursor_pos) or self.card.screen() or QApplication.primaryScreen()

            mode = self.config_manager.get("summon_target", "card")
            if open_drawer is True or mode == "drawer":
                self.open_expanded_drawer(show_search=True, target_screen=active_screen)
            else:
                self.summon_card_to_front(target_screen=active_screen)

    def center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2 - 40
        self.move(x, y)

    def open_expanded_drawer(self, show_search: bool = False, target_screen: Optional[Any] = None):
        """Opens authentic mobile Hero morphing expanded folder view"""
        if self.is_drawer_open():
            d = self.current_drawer
            self.current_drawer = None
            d.collapse_and_close()
            return

        # 展开时标题会透过半透明抽屉形成重影；关闭时 on_drawer_closed 会恢复
        overlay = getattr(self, "_page_turn_overlay", None)
        if overlay is not None:
            overlay.stop_and_hide()
        self.title_label.hide()

        folder = self.get_current_folder()
        f_name = self.get_current_folder_name()
        items = folder.get("items", []) if folder else []
        theme = self.config_manager.get("theme", "dark")
        radius = float(self.config_manager.get("border_radius", 26))

        # Determine target screen (cursor screen, host screen, or default)
        if target_screen is None:
            if self.card.isVisible():
                card_pos = self.card.mapToGlobal(QPoint(0, 0))
                card_center = card_pos + QPoint(self.card.width() // 2, self.card.height() // 2)
                target_screen = QApplication.screenAt(card_center) or self.card.screen() or QApplication.primaryScreen()
            else:
                target_screen = self.card.screen() or QApplication.primaryScreen()

        screen_geo = target_screen.geometry() if target_screen else QRect(0, 0, 1920, 1080)
        avail_geo = target_screen.availableGeometry() if target_screen else screen_geo

        # Calculate exact starting geometry for Hero transition
        card_on_target_screen = False
        if self.card.isVisible():
            card_pos = self.card.mapToGlobal(QPoint(0, 0))
            card_center = card_pos + QPoint(self.card.width() // 2, self.card.height() // 2)
            if screen_geo.contains(card_center):
                card_on_target_screen = True
                start_geo = QRect(card_pos, self.card.size())

        if not card_on_target_screen:
            cw = 240
            ch = 240
            cx = screen_geo.x() + (screen_geo.width() - cw) // 2
            cy = screen_geo.y() + (screen_geo.height() - ch) // 2
            start_geo = QRect(cx, cy, cw, ch)

        # Calculate safe work area geometry avoiding top MyFinder (~32px) and bottom MyDock (~120px)
        safe_top = max(avail_geo.top(), screen_geo.top() + 32)
        safe_bottom = min(avail_geo.bottom(), screen_geo.bottom() - 120)
        safe_left = max(avail_geo.left(), screen_geo.left() + 20)
        safe_right = min(avail_geo.right(), screen_geo.right() - 20)

        safe_w = max(300, safe_right - safe_left + 1)
        safe_h = max(300, safe_bottom - safe_top + 1)

        real_items_count = len([it for it in items if it and (it.get("path") or it.get("name"))])
        if show_search:
            tw = min(516, safe_w)
            th = min(540, safe_h)
        elif real_items_count <= 2:
            tw = min(336, safe_w)
            th = min(236, safe_h)
        elif real_items_count == 3:
            tw = min(416, safe_w)
            th = min(236, safe_h)
        elif real_items_count <= 4:
            tw = min(504, safe_w)
            th = min(240, safe_h)
        elif real_items_count <= 8:
            tw = min(504, safe_w)
            th = min(356, safe_h)
        elif real_items_count <= 12:
            tw = min(504, safe_w)
            th = min(476, safe_h)
        else:
            tw = min(516, safe_w)
            th = min(560, safe_h)

        if show_search:
            # Global spotlight / Raycast search centers in work area
            tx = safe_left + (safe_w - tw) // 2
            ty = safe_top + (safe_h - th) // 2
        else:
            # Authentic Mobile In-Place Hero Expansion:
            # Anchors symmetrically around the folder card's exact physical center!
            start_cx = start_geo.center().x()
            start_cy = start_geo.center().y()
            desired_tx = start_cx - tw // 2
            desired_ty = start_cy - th // 2
            tx = max(safe_left, min(desired_tx, safe_right - tw))
            ty = max(safe_top, min(desired_ty, safe_bottom - th))

        target_geo = QRect(tx, ty, tw, th)

        drawer_open_mode = self.config_manager.get_open_mode_for_drawer()
        open_app_mode = self.config_manager.get_open_app_mode()

        drawer = ExpandedFolderDrawer(
            folder_name=f_name,
            items=items,
            theme=theme,
            start_radius=radius,
            start_geo=start_geo,
            target_geo=target_geo,
            screen_geo=screen_geo,
            open_mode=drawer_open_mode,
            open_app_mode=open_app_mode,
            config_manager=self.config_manager,
            parent=None
        )
        drawer.request_set_open_mode.connect(self.set_open_app_mode)
        drawer.folder_renamed.connect(self.on_drawer_folder_renamed)
        drawer.item_changed.connect(self.on_drawer_item_changed)
        drawer.item_removed.connect(self.on_drawer_item_removed)
        drawer.request_move_to_desktop.connect(self.on_request_move_to_desktop)
        drawer.request_recycle.connect(self.on_request_recycle)
        drawer.files_dropped.connect(self.on_drawer_files_dropped)
        drawer.web_items_dropped.connect(self.handle_dropped_web_bookmarks)
        drawer.request_tidy_desktop.connect(lambda: self.action_tidy_desktop(auto_categorize=True))
        drawer.request_open_settings.connect(self.action_open_settings)
        drawer.status_toast.connect(self.toast.show_message)
        drawer.closed.connect(self.on_drawer_closed)
        self.current_drawer = drawer

        # Keep desktop card and title label resting stably underneath drawer overlay.
        # Eliminates hide/show flicker and avoids breaking Qt backing store rendering.
        self.update()

        drawer.show()
        drawer.raise_()
        drawer.activateWindow()
        drawer.open_animated()
        if show_search:
            drawer.show_search_input(focus=True)

    def on_drawer_files_dropped(self, file_paths: List[str], target_slot: int = -1):
        self.handle_dropped_files(file_paths, target_slot)

    def on_drawer_folder_renamed(self, new_name: str):
        curr_folder = self.get_current_folder()
        curr_name = self.get_current_folder_name()
        if curr_name and new_name != curr_name:
            if hasattr(self, "storage_engine") and curr_folder.get("storage_mapped"):
                success = self.storage_engine.rename_folder_storage(curr_name, new_name)
                if not success:
                    if self.is_drawer_open() and hasattr(self.current_drawer, "title_edit"):
                        self.current_drawer.title_edit.setText(curr_name)
                        self.current_drawer.folder_name = curr_name
                    return
            else:
                curr_idx = self.get_current_folder_index()
                self.config_manager.rename_folder(curr_idx, new_name)
            for w in list(BigFolderWindow._instances):
                if getattr(w, "bound_folder_name", None) == curr_name:
                    w.bound_folder_name = new_name
                    w.title_label.set_text(w.get_display_title_text())
                    w.rebuild_grid()
            self.toast.show_message(f"已重命名为 {new_name}")

    def on_drawer_item_changed(self, index: int, updated_item: dict):
        folder = self.get_current_folder()
        items = list(folder.get("items", []))
        if hasattr(self, "storage_engine") and updated_item.get("path") and self.storage_engine.is_in_managed_storage(updated_item["path"]):
            old_name = items[index].get("name", "") if index < len(items) else ""
            if old_name and updated_item.get("name") and old_name != updated_item.get("name"):
                renamed_path = self.storage_engine.rename_storage_item(updated_item["path"], updated_item["name"])
                if renamed_path:
                    return
        while len(items) <= index:
            items.append({"name": "", "path": "", "args": ""})
        items[index] = updated_item
        self.set_current_folder_items(items)
        self.rebuild_grid()

    def on_drawer_item_removed(self, index: int):
        self.on_slot_item_removed(index)

    def apply_layer_mode(self):
        mode = self.config_manager.get("layer_mode", "bottom")
        hwnd = int(self.winId())

        if mode == "bottom":
            if hasattr(self, "desktop_watchdog"):
                self.desktop_watchdog.set_enabled(True)
                self.desktop_watchdog.re_bed()
            else:
                pin_to_desktop(hwnd)
        elif mode == "top":
            if hasattr(self, "desktop_watchdog"):
                self.desktop_watchdog.set_enabled(False)
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            )
        else:
            if hasattr(self, "desktop_watchdog"):
                self.desktop_watchdog.set_enabled(False)
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            )

    def apply_blur_effect(self):
        hwnd = int(self.winId())
        # 原生矩形 DWM backdrop 在 HWND_BOTTOM + 透明边距下可能触发 #D3D3D3 灰底回退，
        # 因此默认走 QPainter 的半透明玻璃；用户主动开启「背景模糊」才调用 apply_blur。
        if self.config_manager.get("glass_blur_enabled", False):
            theme = self.config_manager.get("theme", "dark")
            apply_blur(hwnd, is_dark=(theme != "light"), opacity=0.55)
        else:
            remove_blur(hwnd)

    def restore_position(self):
        """从 config 基准坐标换算到当前屏幕，不做钳位、不写回。"""
        inst = self.config_manager.get_instance(self.instance_id) if hasattr(self, "instance_id") and self.instance_id else None
        if inst:
            x = inst.get("pos_x", 797)
            y = inst.get("pos_y", 49)
        else:
            x = self.config_manager.get("pos_x", 797)
            y = self.config_manager.get("pos_y", 49)
        self.move(layout_scale.to_screen(x, self.layout_scale),
                  layout_scale.to_screen(y, self.layout_scale))

    def set_layout_scale(self, scale: float, reposition: bool = True):
        """分辨率变化后按新比例重建卡片：尺寸与坐标都从 config 基准重算，不累积误差。"""
        self.layout_scale = scale
        self.title_label.set_layout_scale(scale)
        self.rebuild_grid()
        if reposition:
            self.restore_position()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.is_drawer_open():
            self.card.show()
            self.update_title_visibility()
        self.apply_layer_mode()
        self.apply_blur_effect()
        if hasattr(self, "desktop_watchdog"):
            self.desktop_watchdog.start()
        # Post-show reinforcement: re-attach Win32 owner after Qt's native platform loop realizes the window
        QTimer.singleShot(60, self.apply_layer_mode)
        QTimer.singleShot(220, self.apply_layer_mode)
        if getattr(self, "is_primary", False):
            self.register_global_hotkey()

    def changeEvent(self, event):
        # Win+D / Win+M Immunity: If minimized, instantly restore and pin to bottom via watchdog
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.setWindowState(Qt.WindowState.WindowNoState)
                self.show()
                if hasattr(self, "card") and not self.is_drawer_open():
                    self.card.show()
                    self.update_title_visibility()
                if self.config_manager.get("layer_mode", "bottom") == "bottom":
                    if hasattr(self, "desktop_watchdog"):
                        self.desktop_watchdog.re_bed()
                    else:
                        self.apply_layer_mode()
        elif event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and getattr(self, "is_summoned_front", False):
                # When user clicks outside the elevated card (to another window or desktop),
                # settle the card back to the wallpaper bottom (unless a popup menu/dialog is open)
                if not self.is_drawer_open() and not QApplication.activePopupWidget() and not getattr(self, "is_moving_window", False):
                    self.dismiss_summon_card()
        super().changeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if getattr(self, "is_summoned_front", False):
                self.dismiss_summon_card()
                event.accept()
                return
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_expanded_drawer(show_search=True)
            event.accept()
            return

        # Direct typing on desktop folder invokes instant search
        text = event.text()
        if text and text.isprintable() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.open_expanded_drawer(show_search=True)
            if self.current_drawer and hasattr(self.current_drawer, "search_input") and self.current_drawer.search_input.isVisible():
                self.current_drawer.search_input.setText(text)
                self.current_drawer.search_input.setCursorPosition(len(text))
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        # 卡片本体已自带圆角与描边；外扩实心矩形会在边缘形成「大一号轮廓」，
        # 还会压住下方标题，因此这里不再绘制任何外扩阴影。
        return

    def nativeEvent(self, event_type, message):
        try:
            msg = ctypes.wintypes.MSG.from_address(message.__int__())

            # Repaint sync on WM_PAINT (0x000F) or WM_ERASEBKGND (0x0014)
            if msg.message in (0x000F, 0x0014):
                if hasattr(self, "card") and self.card.isVisible() and not getattr(self, "_paint_sync_queued", False):
                    self._paint_sync_queued = True
                    QTimer.singleShot(0, self._flush_paint_sync)

            # 1. Non-client hit test: transparent margin padding passes through to Windows Desktop
            if msg.message == 0x0084:  # WM_NCHITTEST
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                global_pos = QPoint(x, y)
                local_pos = self.mapFromGlobal(global_pos)

                in_card = hasattr(self, "card") and self.card.isVisible() and self.card.geometry().contains(local_pos)
                in_title = hasattr(self, "title_label") and self.title_label.isVisible() and self.title_label.geometry().contains(local_pos)

                if not in_card and not in_title:
                    # Mouse is in the margin transparent area! Pass through to desktop for rubberband select
                    return True, -1  # HTTRANSPARENT
                else:
                    return False, 0

            # 2. Global Hotkey Trigger
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.toggle_summon()
                return True, 0

            # 3. Bottom layer & coexistence with other desktop tools
            mode = self.config_manager.get("layer_mode", "bottom")
            if mode == "bottom":
                if msg.message == WM_MOUSEACTIVATE:
                    return True, MA_NOACTIVATE
                if msg.message == WM_SYSCOMMAND and (msg.wParam & 0xFFF0) == SC_MINIMIZE:
                    return True, 0
                if msg.message == 0x0046:  # WM_WINDOWPOSCHANGING
                    try:
                        from desktop_layer import WINDOWPOS
                        pos = WINDOWPOS.from_address(msg.lParam)
                        # Strip SWP_HIDEWINDOW (0x0080) and enforce SWP_SHOWWINDOW (0x0040)
                        if pos.flags & 0x0080:
                            pos.flags &= ~0x0080
                            pos.flags |= 0x0040
                    except Exception:
                        pass
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def _flush_paint_sync(self):
        self._paint_sync_queued = False
        if hasattr(self, "card") and self.card is not None and self.card.isVisible():
            self.card.update()

    def is_locked(self) -> bool:
        if hasattr(self, "instance_id") and self.instance_id:
            inst = self.config_manager.get_instance(self.instance_id)
            if inst and "locked" in inst:
                return bool(inst["locked"])
        return bool(self.config_manager.get("locked", False))

    def _is_near_card_resize_handle(self, local_pos: QPoint) -> bool:
        """检测鼠标是否位于卡片右下角抓手响应区 (22px 触发圈)"""
        if not hasattr(self, "card"):
            return False
        card_pt = local_pos - self.card.pos()
        cw = self.card.width()
        ch = self.card.height()
        return (cw - 22 <= card_pt.x() <= cw + 4) and (ch - 22 <= card_pt.y() <= ch + 4)

    def apply_grid_shape(self, rows: int, cols: int, toast: bool = True):
        """应用形态网格排布 (2x2, 2x4, 3x3, 4x2, 4x4 等) 并持久化独立实例记忆"""
        rows = max(1, min(6, rows))
        cols = max(1, min(6, cols))
        if hasattr(self, "instance_id") and self.instance_id:
            inst = self.config_manager.get_instance(self.instance_id)
            if inst:
                inst.pop("card_width", None)
                inst.pop("card_height", None)
            self.config_manager.update_instance(self.instance_id, {"rows": rows, "cols": cols}, auto_save=False)
        self.config_manager.set("grid_rows", rows, auto_save=False)
        self.config_manager.set("grid_cols", cols, auto_save=False)
        self.config_manager.save()
        self.rebuild_grid()
        self.push_colliding_neighbors_away()
        if toast:
            self.toast.show_message(f"已设为 {rows} × {cols} 规格")

    def push_colliding_neighbors_away(self):
        """
        无极缩放防碰撞推开引擎：
        当大文件夹被拉伸扩大覆盖到相邻微件（如天气、时钟等）下方时，
        动态探测几何重叠，以物理磁斥力将受挤压的相邻微件平滑推开至右侧或下方 16px 规范槽位，
        绝对杜绝任何重叠覆盖，并即刻热落盘新坐标。
        """
        if not hasattr(self, "card") or not self.card:
            return

        card_offset = self.card.pos()
        my_card_rect = QRect(self.pos() + card_offset, self.card.size())

        avail_geo = layout_scale.screen_available_geometry_for_widget(self)
        if avail_geo is None:
            screen = self.screen() or QApplication.primaryScreen()
            avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        safe_right = avail_geo.x() + avail_geo.width() - 8
        safe_left = avail_geo.x() + 8
        safe_bottom = avail_geo.y() + avail_geo.height() - 8

        mgr = getattr(self, "manager", None)
        if not mgr:
            return

        other_widgets = []
        # 天气小组件
        if getattr(mgr, "weather_window", None) and mgr.weather_window.isVisible():
            w = mgr.weather_window
            other_widgets.append(("weather", w, w.card.pos(), QRect(w.pos() + w.card.pos(), w.card.size())))

        # 其他大文件夹多实例
        for i_id, win in getattr(mgr, "windows", {}).items():
            if win is not self and win.isVisible() and hasattr(win, "card") and win.card and win.card.isVisible():
                extra_h = 36 if hasattr(win, "title_label") and win.title_label and win.title_label.isVisible() else 0
                other_widgets.append(("folder", win, win.card.pos(), QRect(win.pos() + win.card.pos(), QSize(win.card.width(), win.card.height() + extra_h))))

        # 其他桌面微件 (时钟、硬件监视器、便签、媒体微件)
        for w_type, w_inst in [("clock", getattr(mgr, "clock_window", None)),
                               ("monitor", getattr(mgr, "monitor_window", None)),
                               ("memo", getattr(mgr, "memo_window", None)),
                               ("media", getattr(mgr, "media_window", None)),
                               ("everything", getattr(mgr, "everything_window", None))]:
            if w_inst and w_inst.isVisible():
                from desktop_widgets import MARGIN_SHADOW
                offset = QPoint(MARGIN_SHADOW, MARGIN_SHADOW)
                other_widgets.append((w_type, w_inst, offset, QRect(w_inst.pos() + offset, QSize(w_inst.card_w, w_inst.card_h))))

        card_gap = 16
        for w_type, win, offset, o_rect in other_widgets:
            if my_card_rect.intersects(o_rect):
                # 产生碰撞重叠！向右推开（顶边平齐对齐）
                cand_right = my_card_rect.x() + my_card_rect.width() + card_gap
                cand_top = my_card_rect.top()

                if cand_right + o_rect.width() <= safe_right:
                    # 右侧空间充足，直接推到右侧
                    new_win_pos = QPoint(cand_right - offset.x(), cand_top - offset.y())
                    win.move(new_win_pos)
                else:
                    # 右侧空间不足，检查能否将自身向左微调挪出空间
                    shift_needed = (cand_right + o_rect.width()) - safe_right
                    if self.pos().x() - shift_needed >= safe_left:
                        self.move(self.pos().x() - shift_needed, self.pos().y())
                        self.persist_instance_position(self.x(), self.y())
                        my_card_rect = QRect(self.pos() + card_offset, self.card.size())
                        cand_right = my_card_rect.x() + my_card_rect.width() + card_gap
                        new_win_pos = QPoint(cand_right - offset.x(), cand_top - offset.y())
                        win.move(new_win_pos)
                    else:
                        # 空间彻底不足，向下折行换位
                        cand_below_x = my_card_rect.left()
                        my_extra_h = 36 if hasattr(self, "title_label") and self.title_label and self.title_label.isVisible() else 0
                        cand_below_y = my_card_rect.y() + my_card_rect.height() + my_extra_h + card_gap
                        new_win_pos = QPoint(cand_below_x - offset.x(), cand_below_y - offset.y())
                        win.move(new_win_pos)

                # 即刻热落盘被推开组件的坐标
                if w_type == "folder":
                    win.persist_instance_position(win.x(), win.y())
                elif w_type == "weather":
                    mgr._on_weather_pos_changed(win.x(), win.y())
                elif w_type == "clock":
                    mgr._on_clock_pos_changed(win.x(), win.y())
                elif w_type == "monitor":
                    mgr._on_monitor_pos_changed(win.x(), win.y())
                elif w_type == "memo":
                    mgr._on_memo_pos_changed(win.x(), win.y())
                elif w_type == "media":
                    mgr._on_media_pos_changed(win.x(), win.y())
                elif w_type == "everything":
                    mgr._on_everything_pos_changed(win.x(), win.y())

        # 触发全局防重叠联动校验
        mgr.validate_and_separate_all_desktop_cards(from_user=True)
        mgr.separate_overlapping_cards()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.mouse_press_global_pos = event.globalPosition().toPoint()
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.is_moving_window = False
            self.is_left_pressed = True

            # 右下角无极调整检测
            if not self.is_locked() and self._is_near_card_resize_handle(event.pos()):
                self._is_resizing_card = True
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_card_size = self.card.size()
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                event.accept()
                return

            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 0. 防御按键释放丢失 (如拖拽中 Alt+Tab / Win+D / 弹窗抢占焦点)，立即清理残留白框与拖拽锁
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            if getattr(self, "is_left_pressed", False) or getattr(self, "_is_resizing_card", False) or getattr(self, "is_moving_window", False):
                self.is_left_pressed = False
                self._is_resizing_card = False
                self.is_moving_window = False
                self._pending_snap_pos = None
                self._pending_card_pos = None
                self._hide_drag_preview()

        # 1. 无极缩放拖拽中 (带屏幕防出界钳位与相邻卡片/微件尺寸物理磁吸)
        if getattr(self, "_is_resizing_card", False) and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._resize_start_global
            raw_w = self._resize_start_card_size.width() + delta.x()
            raw_h = self._resize_start_card_size.height() + delta.y()

            avail_geo = layout_scale.screen_available_geometry_for_widget(self)
            if avail_geo is None:
                screen = self.screen() or QApplication.primaryScreen()
                avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
            card_global_pos = self.pos() + self.card.pos()
            extra_h = 36 if hasattr(self, "title_label") and self.title_label and self.title_label.isVisible() else 0
            # 最小尺寸由槽位下限推导，不再硬编码 160
            rows, cols = self.get_grid_dimensions()
            min_slot = 24
            min_w = 32 + (cols - 1) * 9 + cols * min_slot
            min_h = 32 + (rows - 1) * 9 + rows * min_slot
            max_w = max(min_w, avail_geo.x() + avail_geo.width() - 8 - card_global_pos.x())
            max_h = max(min_h, avail_geo.y() + avail_geo.height() - 8 - card_global_pos.y() - extra_h)
            # 无极缩放：只钳屏幕边界，不吸附预设尺寸、不被相邻卡片限制宽高
            final_size = QSize(max(min_w, min(raw_w, max_w)), max(min_h, min(raw_h, max_h)))
            self.card.setFixedSize(final_size)
            self.adjustSize()
            self.push_colliding_neighbors_away()
            event.accept()
            return

        # 2. 窗口桌面位置拖拽中
        if getattr(self, "is_left_pressed", False) and (event.buttons() & Qt.MouseButton.LeftButton):
            cur_pos = event.globalPosition().toPoint()
            delta = (cur_pos - self.mouse_press_global_pos).manhattanLength()

            # Seamless threshold: only engage physical window dragging if moved >= 7px
            if not self.is_moving_window and delta >= getattr(self, "drag_threshold", 7):
                if not self.is_locked():
                    self.is_moving_window = True
                    self.setCursor(Qt.CursorShape.SizeAllCursor)

            if self.is_moving_window:
                raw_win_pos = cur_pos - self.drag_position
                card_offset = self.card.pos()
                raw_card_pos = raw_win_pos + card_offset

                # get_all_desktop_card_rects 默认已包含桌面图标预留区
                other_card_rects = [r for r in get_all_desktop_card_rects(exclude_window=self)
                                    if isinstance(r, QRect)]
                screen = QApplication.screenAt(cur_pos) or self.screen() or QApplication.primaryScreen()
                screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
                avail_geo = screen.availableGeometry() if screen else screen_geo

                extra_h = 36 if hasattr(self, "title_label") and self.title_label and self.title_label.isVisible() else 0
                raw_card_rect = QRect(raw_card_pos, QSize(self.card.width(), self.card.height() + extra_h))

                # 与小组件共用同一个落点入口：白框 = 避开所有碰撞体积后的最终落点
                drop_rect = widget_grid.drop_target(raw_card_rect, other_card_rects, avail_geo)
                self._pending_snap_pos = drop_rect.topLeft() - card_offset
                self._pending_card_pos = drop_rect.topLeft()
                self._show_drag_preview(drop_rect)
                self.move(raw_win_pos)
                event.accept()
                return

        # 3. 悬停状态下的光标形状检测
        if not getattr(self, "is_left_pressed", False):
            if not self.is_locked() and self._is_near_card_resize_handle(event.pos()):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and getattr(self, "is_left_pressed", False):
            self.is_left_pressed = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

            if getattr(self, "_is_resizing_card", False):
                self._is_resizing_card = False
                w, h = self.card.width(), self.card.height()
                self.persist_instance_size(w, h)
                self.rebuild_grid()
                self.push_colliding_neighbors_away()
                self.toast.show_message(f"卡片尺寸 {w} × {h}", duration_ms=600)
                event.accept()
                return

            if self.is_moving_window:
                # Mode A: User intentionally dragged the folder across the desktop
                self.is_moving_window = False
                self._hide_drag_preview()
                pending = getattr(self, "_pending_snap_pos", None)
                if pending is not None:
                    # 白框位置就是最终位置：落点已避开所有碰撞体积，不需要二次调整或推挤邻居
                    self.move(pending)
                    self._pending_snap_pos = None
                    self._pending_card_pos = None

                self.persist_instance_position(self.x(), self.y())
                # 不再调用全局自愈：白框+推挤已经给出确定落点，
                # validate 的严格相交判定会为了几像素缝隙把卡片再推走几十像素
                if self.config_manager.get("layer_mode", "bottom") == "bottom":
                    self.apply_layer_mode()
                event.accept()
                return
            else:
                # Mode B: Clean single click on empty card well, background or empty slot (< 7px movement)
                # Single-clicking the folder ALWAYS expands the folder drawer!
                self.open_expanded_drawer()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _show_drag_preview(self, rect: QRect):
        try:
            if getattr(self, "_drag_preview", None) is None:
                self._drag_preview = widget_grid.DropOutline()
            self._drag_preview.show_slot(rect, radius=widget_grid.RADIUS)
        except Exception:
            pass

    def _hide_drag_preview(self):
        preview = getattr(self, "_drag_preview", None)
        if preview is not None and preview.isVisible():
            preview.hide()

    def persist_instance_position(self, x: int, y: int):
        s = self.layout_scale
        bx, by = layout_scale.to_canvas(x, s), layout_scale.to_canvas(y, s)
        if hasattr(self, "instance_id") and self.instance_id:
            self.config_manager.update_instance(self.instance_id, {"pos_x": bx, "pos_y": by})
        else:
            self.config_manager.set("pos_x", bx, auto_save=False)
            self.config_manager.set("pos_y", by, auto_save=True)

    def persist_instance_size(self, w: int, h: int):
        """保存无极缩放后的实际像素尺寸（反算回布局画布，不再量化成固定行列格数）。"""
        s = self.layout_scale
        bw, bh = layout_scale.to_canvas(w, s), layout_scale.to_canvas(h, s)
        if hasattr(self, "instance_id") and self.instance_id:
            self.config_manager.update_instance(
                self.instance_id, {"card_width": bw, "card_height": bh})
        else:
            self.config_manager.set("card_width", bw, auto_save=False)
            self.config_manager.set("card_height", bh, auto_save=True)

    def align_to_adjacent_widget(self):
        """一键与最近的天气小组件或相邻大文件夹平齐对齐（顶边完全齐平，标准 16px 间距）"""
        card_offset = self.card.pos()
        my_card_rect = QRect(self.pos() + card_offset, self.card.size())
        other_cards = get_all_desktop_card_rects(exclude_window=self)
        if not other_cards:
            self.toast.show_message("未检测到相邻组件")
            return

        closest = min(other_cards, key=lambda r: (r.center() - my_card_rect.center()).manhattanLength())
        new_top = closest.top()

        if my_card_rect.center().x() < closest.center().x():
            new_left = closest.left() - self.card.width() - 16
        else:
            new_left = closest.x() + closest.width() + 16

        snapped_win_pos = QPoint(new_left - card_offset.x(), new_top - card_offset.y())
        self.move(snapped_win_pos)
        self.persist_instance_position(snapped_win_pos.x(), snapped_win_pos.y())
        self.toast.show_message("已对齐相邻卡片")


    def enterEvent(self, event):
        if self.config_manager.get("show_title", "always") == "hover":
            self.title_label.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.config_manager.get("show_title", "always") == "hover":
            self.title_label.setVisible(False)
        super().leaveEvent(event)

    def _reset_mouse_press_state(self):
        self.is_left_pressed = False
        self._is_resizing_card = False
        self.is_moving_window = False
        self._pending_snap_pos = None
        self._pending_card_pos = None
        self._hide_drag_preview()

    def contextMenuEvent(self, event):
        self._reset_mouse_press_state()
        self.show_global_menu(event.globalPos())
        event.accept()

    def show_global_menu(self, global_pos: QPoint):
        self._reset_mouse_press_state()
        menu = self.create_global_menu()
        menu.exec(global_pos)

    def populate_card_manage_submenu(self, target_menu: QMenu):
        act_drawer = target_menu.addAction("展开")
        act_drawer.triggered.connect(lambda: self.open_expanded_drawer(show_search=True))

        cat_menu = target_menu.addMenu("切换分类")
        cat_menu.setStyleSheet(target_menu.styleSheet())
        folders = self.config_manager.get_folders()
        curr_f_idx = self.get_current_folder_index()
        for idx, f in enumerate(folders):
            name = f.get("name", "未命名分类")
            prefix = "● " if idx == curr_f_idx else "○ "
            act = cat_menu.addAction(f"{prefix}{name}")
            act.triggered.connect(lambda checked=False, i=idx: self.switch_folder(i))
        cat_menu.addSeparator()
        act_new_cat = cat_menu.addAction("新建分类")
        act_new_cat.triggered.connect(self.action_add_folder)

        shape_menu = target_menu.addMenu("网格")
        shape_menu.setStyleSheet(target_menu.styleSheet())
        presets = [
            ("2 × 2", 2, 2),
            ("2 × 4", 2, 4),
            ("3 × 3", 3, 3),
            ("4 × 2", 4, 2),
            ("4 × 4", 4, 4),
        ]
        cur_r, cur_c = self.get_grid_dimensions()
        for label, r, c in presets:
            prefix = "● " if (cur_r == r and cur_c == c) else "○ "
            act = shape_menu.addAction(f"{prefix}{label}")
            act.triggered.connect(lambda checked=False, row=r, col=c: self.apply_grid_shape(row, col))

        act_align = target_menu.addAction("对齐")
        act_align.triggered.connect(self.align_to_adjacent_widget)

        is_cur_locked = self.is_locked()
        act_lock = target_menu.addAction("解锁" if is_cur_locked else "锁定")
        act_lock.triggered.connect(self.toggle_lock_position)

        target_menu.addSeparator()
        act_close_this = target_menu.addAction("关闭此卡片")
        act_close_this.triggered.connect(self.action_close_this_instance)

        if len(folders) > 1:
            act_dissolve = target_menu.addAction("解散")
            act_dissolve.triggered.connect(self.action_delete_folder)

    def create_global_menu(
        self,
        parent: Optional[QWidget] = None,
        settings_callback: Optional[Any] = None,
    ) -> QMenu:
        menu = QMenu(parent or self)
        theme = self.config_manager.get("theme", "dark")
        _menu_qss = getattr(ui_kit, "menu_qss", None)
        if callable(_menu_qss):
            menu.setStyleSheet(_menu_qss(theme))
        else:
            menu.setStyleSheet("""
            QMenu {
                background-color: rgba(24, 26, 32, 248);
                color: #f5f5f5;
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 14px;
                padding: 8px;
                font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                font-size: 13px;
            }
            QMenu::item {
                padding: 7px 24px 7px 14px;
                border-radius: 7px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 48);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: rgba(255, 255, 255, 25);
                margin: 5px 6px;
            }
            """)

        # 1. 核心高频动作
        act_drawer = menu.addAction("展开")
        act_drawer.triggered.connect(lambda: self.open_expanded_drawer(show_search=True))

        # 2. 快速切换分类
        cat_menu = menu.addMenu("切换分类")
        cat_menu.setStyleSheet(menu.styleSheet())
        folders = self.config_manager.get_folders()
        curr_f_idx = self.get_current_folder_index()
        for idx, f in enumerate(folders):
            name = f.get("name", "未命名分类")
            prefix = "● " if idx == curr_f_idx else "○ "
            act = cat_menu.addAction(f"{prefix}{name}")
            act.triggered.connect(lambda checked=False, i=idx: self.switch_folder(i))
        cat_menu.addSeparator()
        act_new_cat = cat_menu.addAction("新建分类")
        act_new_cat.triggered.connect(self.action_add_folder)

        # 3. 形态预设与无极缩放提示
        shape_menu = menu.addMenu("网格")
        shape_menu.setStyleSheet(menu.styleSheet())
        presets = [
            ("2 × 2", 2, 2),
            ("2 × 4", 2, 4),
            ("3 × 3", 3, 3),
            ("4 × 2", 4, 2),
            ("4 × 4", 4, 4),
        ]
        cur_r, cur_c = self.get_grid_dimensions()
        for label, r, c in presets:
            prefix = "● " if (cur_r == r and cur_c == c) else "○ "
            act = shape_menu.addAction(f"{prefix}{label}")
            act.triggered.connect(lambda checked=False, row=r, col=c: self.apply_grid_shape(row, col))

        act_align = menu.addAction("对齐")
        act_align.triggered.connect(self.align_to_adjacent_widget)

        # 4. 锁定当前位置
        is_cur_locked = self.is_locked()
        act_lock = menu.addAction("解锁" if is_cur_locked else "锁定")
        act_lock.triggered.connect(self.toggle_lock_position)

        menu.addSeparator()

        # 5. 全局 Stacky 灵叠设置中心 (一站式配置，消除杂乱)
        act_settings = menu.addAction("设置")
        if callable(settings_callback):
            act_settings.triggered.connect(settings_callback)
        else:
            act_settings.triggered.connect(lambda: self.action_open_settings(0))

        menu.addSeparator()

        # 6. 关闭当前卡片 / 解散文件夹 / 退出
        act_close_this = menu.addAction("关闭此卡片")
        act_close_this.triggered.connect(self.action_close_this_instance)

        if len(self.config_manager.get_folders()) > 1:
            act_dissolve = menu.addAction("解散")
            act_dissolve.triggered.connect(self.action_delete_folder)

        act_exit = menu.addAction("退出")
        act_exit.triggered.connect(QApplication.instance().quit)

        return menu

    def action_open_settings(self, tab_index: int = 0):
        self._reset_mouse_press_state()
        try:
            if self.is_drawer_open():
                self.close_expanded_drawer(immediately=True)
            from settings_dialog import SettingsDialog
            mgr = self.manager if hasattr(self, "manager") and self.manager else None
            # 严禁将顶级设置窗口挂为桌面底层透明窗口的子窗口 (避免 Qt6 enforceNativeChildren 把卡片升级为原生子 HWND)
            dlg = SettingsDialog(self.config_manager, window_manager=mgr, initial_tab=tab_index, parent=None)
            dlg.exec()
        except Exception as e:
            print("Failed to open settings dialog:", e)

    def action_create_dock_shortcut(self):
        if getattr(sys, "frozen", False):
            project_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            project_dir = ROOT
        desktop_dir = None
        create_desktop = True
        # Strict testing isolation: if running with isolated test config, don't pollute real Desktop
        cfg_path = getattr(self.config_manager, "config_path", "")
        if cfg_path and ("tmp" in cfg_path.lower() or "temp" in cfg_path.lower()):
            desktop_dir = os.path.dirname(cfg_path)
            project_dir = os.path.dirname(cfg_path)
            create_desktop = False

        lnk = generate_mydock_shortcut(
            project_dir=project_dir,
            desktop_dir=desktop_dir,
            create_desktop=create_desktop
        )
        self.toast.show_message("已更新快捷方式，可拖入 Dock 使用", duration_ms=2600)
        return lnk


    def set_open_app_mode(self, mode: str):
        self.config_manager.set_open_app_mode(mode)
        for w in list(BigFolderWindow._instances):
            w.rebuild_grid()
        if self.current_drawer and not self.current_drawer.is_closing:
            self.current_drawer.apply_open_app_mode(mode)
        desc = {
            "double": "打开方式：双击打开",
            "hybrid": "打开方式：卡片单击 · 抽屉双击",
            "single": "打开方式：单击打开",
        }.get(mode, "打开方式已更新")
        self.toast.show_message(desc)

    def action_rename_folder(self):
        curr_folder = self.get_current_folder()
        curr_name = self.get_current_folder_name()
        new_name, ok = QInputDialog.getText(
            self, "重命名分类", "请输入分类名称：", text=curr_name
        )
        if ok and new_name.strip() and new_name.strip() != curr_name:
            curr_idx = self.get_current_folder_index()
            target_name = new_name.strip()
            if hasattr(self, "storage_engine") and curr_folder and curr_folder.get("storage_mapped"):
                success = self.storage_engine.rename_folder_storage(curr_name, target_name)
                if not success:
                    return
            else:
                self.config_manager.rename_folder(curr_idx, target_name)
            for w in list(BigFolderWindow._instances):
                if getattr(w, "bound_folder_name", None) == curr_name:
                    w.bound_folder_name = target_name
                    w.title_label.set_text(w.get_display_title_text())
                    w.rebuild_grid()
            self.toast.show_message(f"已重命名为 {target_name}")


    def set_layer_mode(self, mode: str):
        self.config_manager.set("layer_mode", mode)
        for w in list(BigFolderWindow._instances):
            w.apply_layer_mode()
        labels = {"bottom": "桌面底层", "normal": "普通窗口", "top": "始终置顶"}
        self.toast.show_message(f"层级：{labels.get(mode, mode)}")

    def toggle_lock_position(self):
        new_locked = not self.is_locked()
        if hasattr(self, "instance_id") and self.instance_id:
            self.config_manager.update_instance(self.instance_id, {"locked": new_locked})
        else:
            self.config_manager.set("locked", new_locked)
        self.toast.show_message("已锁定卡片" if new_locked else "已解锁卡片")


    def on_slot_item_changed(self, index: int, new_item: dict):
        folder = self.get_current_folder()
        items = list(folder.get("items", []))
        if hasattr(self, "storage_engine") and new_item.get("path") and self.storage_engine.is_in_managed_storage(new_item["path"]):
            old_name = items[index].get("name", "") if index < len(items) else ""
            if old_name and new_item.get("name") and old_name != new_item.get("name"):
                renamed_path = self.storage_engine.rename_storage_item(new_item["path"], new_item["name"])
                if renamed_path:
                    return
        while len(items) <= index:
            items.append({"name": "", "path": "", "args": ""})
        items[index] = new_item
        self.set_current_folder_items(items)
        self.rebuild_grid()

    def on_slot_item_removed(self, index: int):
        folder = self.get_current_folder()
        items = list(folder.get("items", []))
        if 0 <= index < len(items):
            removed_name = items[index].get("name", "应用")
            items.pop(index)
            self.set_current_folder_items(items)
            self.rebuild_grid()
            self.toast.show_message(f"已移除 {removed_name}")

    def on_slot_item_moved(self, from_idx: int, to_idx: int):
        folder = self.get_current_folder()
        items = list(folder.get("items", []))
        max_idx = max(from_idx, to_idx)
        while len(items) <= max_idx:
            items.append({"name": "", "path": "", "args": ""})

        items[from_idx], items[to_idx] = items[to_idx], items[from_idx]
        self.set_current_folder_items(items)
        self.rebuild_grid()
        # 顺序变化肉眼可见，不再弹提示（误触拖拽时这条提示尤其吵）

    def start_drag_hover_timer(self, mime_data=None):
        """外部文件拖入才自动展开；内部图标拖拽必须直接落到目标卡片。"""
        if mime_data is not None and is_internal_item_drag(mime_data):
            self.cancel_drag_hover_timer()
            return
        if not self.is_drawer_open() and hasattr(self, "drag_hover_timer"):
            if not self.drag_hover_timer.isActive():
                self.drag_hover_timer.start(600)

    def cancel_drag_hover_timer(self):
        """Cancels hover expansion timer"""
        if hasattr(self, "drag_hover_timer"):
            self.drag_hover_timer.stop()

    def check_drag_leave(self):
        """Checks if drag cursor left the card geometry before cancelling timer and raising lease"""
        if hasattr(self, "card") and self.card.isVisible():
            cursor_pos = QCursor.pos()
            card_pos = self.card.mapToGlobal(QPoint(0, 0))
            card_rect = QRect(card_pos, self.card.size())
            if not card_rect.contains(cursor_pos):
                self.cancel_drag_hover_timer()
                if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
                    self.desktop_watchdog.release_temporary_raise(immediate=False)
        else:
            self.cancel_drag_hover_timer()

    def _on_drag_hover_timeout(self):
        """Callback when 0.6s drag hover elapsed: smoothly trigger in-place Hero expansion"""
        if not self.is_drawer_open():
            self.open_expanded_drawer()

    def handle_dropped_files(self, file_paths: List[str], target_slot: int = -1):
        """
        Unified drop handling for desktop card slots and expanded drawer overlay:
        - Moves/copies into corresponding Stacky physical category folder per storage policy
        - Or manages virtual entries in config.json
        - Triggers hot-sync and immediately refreshes UI
        """
        if not file_paths:
            return

        active_name = self.get_current_folder_name()
        folder = self.get_current_folder()

        managed = hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled()
        if managed:
            drop_action = self.config_manager.get_managed_drop_action()
            prev_owner = BigFolderWindow._storage_message_owner
            BigFolderWindow._storage_message_owner = self
            try:
                if drop_action == "move":
                    self.storage_engine.move_into_storage(file_paths, active_name)
                else:
                    for fp in file_paths:
                        self.storage_engine.add_or_copy_item_to_storage(fp, active_name)
            finally:
                BigFolderWindow._storage_message_owner = prev_owner

            if target_slot >= 0:
                updated_f = self.get_current_folder()
                if updated_f:
                    cur_items = list(updated_f.get("items", []))
                    found_idx = -1
                    for i, it in enumerate(cur_items):
                        if any(os.path.basename(fp).lower() in it.get("path", "").lower() for fp in file_paths):
                            found_idx = i
                            break
                    if found_idx >= 0 and found_idx != target_slot:
                        item_to_move = cur_items.pop(found_idx)
                        while len(cur_items) < target_slot:
                            cur_items.append({"name": "", "path": "", "args": ""})
                        cur_items.insert(target_slot, item_to_move)
                        self.set_current_folder_items(cur_items)
                        self.rebuild_grid()
        else:
            items = list(folder.get("items", [])) if folder else []
            for fp in file_paths:
                name = os.path.splitext(os.path.basename(fp))[0]
                new_item = {"name": name, "path": fp, "args": ""}
                if target_slot >= 0:
                    while len(items) <= target_slot:
                        items.append({"name": "", "path": "", "args": ""})
                    items[target_slot] = new_item
                    target_slot = -1
                else:
                    placed = False
                    for i in range(len(items)):
                        if not items[i].get("path"):
                            items[i] = new_item
                            placed = True
                            break
                    if not placed:
                        items.append(new_item)
            self.set_current_folder_items(items)
            self.rebuild_grid()

        # 托管模式下引擎已发出「已物理收纳…」，不再重复弹一条
        if not managed:
            names = [os.path.splitext(os.path.basename(fp))[0] for fp in file_paths]
            names_str = ", ".join(names[:2])
            if len(names) > 2:
                names_str += f" 等 {len(names)} 项"
            self.toast.show_message(f"已添加 {names_str}")

        # If drawer is open, refresh items immediately
        if self.is_drawer_open() and self.current_drawer:
            updated_folder = self.get_current_folder()
            if updated_folder:
                self.current_drawer.update_items(updated_folder.get("items", []))

        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.release_temporary_raise(immediate=False)

    def handle_dropped_web_bookmarks(self, web_items: List[Dict[str, Any]], target_slot: int = -1):
        """
        Unified web bookmark drop handling for desktop card slots and expanded drawer overlay:
        - Stores web bookmark entries directly in config.json
        - Categorizes with subgroup (defaults to '网页书签')
        - Refreshes desktop card and open drawer overlay
        """
        if not web_items:
            return
        folder = self.get_current_folder()
        items = list(folder.get("items", [])) if folder else []
        for w_item in web_items:
            if target_slot >= 0:
                while len(items) <= target_slot:
                    items.append({"name": "", "path": "", "args": ""})
                items[target_slot] = w_item
                target_slot = -1
            else:
                placed = False
                for i in range(len(items)):
                    if not items[i].get("path"):
                        items[i] = w_item
                        placed = True
                        break
                if not placed:
                    items.append(w_item)

        self.set_current_folder_items(items)
        self.rebuild_grid()

        names = [w.get("name", "网页书签") for w in web_items]
        names_str = ", ".join(names[:2])
        if len(names) > 2:
            names_str += f" 等 {len(names)} 项"
        self.toast.show_message(f"已添加书签 {names_str}")

        if self.is_drawer_open() and self.current_drawer:
            updated_folder = self.get_current_folder()
            if updated_folder:
                self.current_drawer.update_items(updated_folder.get("items", []))

    def on_request_add_web(self, slot_index: int = -1):
        dlg = AddWebBookmarkDialog(
            None,
            existing_subgroups=self.get_current_subgroups(),
            config_manager=self.config_manager,
        )
        if dlg.exec():
            bm = dlg.get_bookmark_data()
            if bm:
                self.handle_dropped_web_bookmarks([bm], target_slot=slot_index)

    def get_current_subgroups(self) -> List[str]:
        folder = self.get_current_folder()
        items = folder.get("items", []) if folder else []
        sgs = set()
        for it in items:
            if not it:
                continue
            sg = (it.get("subgroup") or "").strip()
            if sg:
                sgs.add(sg)
            elif it.get("type") == "web" or it.get("path", "").startswith("http://") or it.get("path", "").startswith("https://") or it.get("path", "").lower().endswith(".url"):
                sgs.add("网页书签")
        if "网页书签" not in sgs:
            sgs.add("网页书签")
        return sorted(list(sgs))

    def on_request_add(self, slot_index: int = -1):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择应用或快捷方式",
            "",
            "应用程序与快捷方式 (*.exe *.lnk *.bat *.cmd *.url);;所有文件 (*.*)"
        )
        if file_path:
            self.handle_dropped_files([file_path], target_slot=slot_index)

    def dragEnterEvent(self, event):
        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.acquire_temporary_raise("drag_enter", duration_ms=8000)
        if is_drop_acceptable(event.mimeData()):
            self.start_drag_hover_timer(event.mimeData())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if is_drop_acceptable(event.mimeData()):
            self.start_drag_hover_timer(event.mimeData())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.check_drag_leave()
        try:
            super().dragLeaveEvent(event)
        except Exception:
            pass

    def dropEvent(self, event):
        self.cancel_drag_hover_timer()
        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.acquire_temporary_raise("drop", duration_ms=4000)

        # 优先处理来自其他大文件夹卡片或全景抽屉的跨文件夹拖拽
        if event.mimeData().hasFormat(STACKY_ITEM_MIME):
            try:
                import json
                raw_bytes = bytes(event.mimeData().data(STACKY_ITEM_MIME))
                payload = json.loads(raw_bytes.decode("utf-8"))
                # 只要落在 Stacky 卡片上，源端就不能把图标弹回桌面
                mark_cross_folder_drop(payload.get("drag_token", ""))
                if self.handle_cross_folder_drop(payload, target_slot=None):
                    event.acceptProposedAction()
                    return
                # 同文件夹或无效目标：吞掉，绝不能当外部文件重新收纳一份
                event.acceptProposedAction()
                return
            except Exception:
                # 解析或迁移失败也必须吞掉，绝不能落到下面的外部文件收纳分支
                event.acceptProposedAction()
                return

        local_files, web_bookmarks = parse_dropped_mime_data(event.mimeData())
        if web_bookmarks:
            self.handle_dropped_web_bookmarks(web_bookmarks)
            event.acceptProposedAction()
            return
        if local_files:
            self.handle_dropped_files(local_files)
            event.acceptProposedAction()
            return

        if hasattr(self, "desktop_watchdog") and self.config_manager.get("layer_mode", "bottom") == "bottom":
            self.desktop_watchdog.release_temporary_raise(immediate=False)
        event.ignore()

    def handle_cross_folder_drop(self, payload: dict, target_slot: Optional[int] = None) -> bool:
        """
        跨大文件夹卡片来回拖拽处理：
        从源文件夹安全迁入当前目标文件夹，杜绝任何数据丢失或误删，100% 完整保留。
        """
        src_folder = payload.get("source_folder")
        item_data = payload.get("item_data")
        target_folder = self.get_current_folder_name()

        if not src_folder or not item_data or not target_folder:
            return False

        if src_folder == target_folder:
            return False

        target_f = self.get_current_folder()
        if not target_f:
            return False

        items = list(target_f.get("items", []))
        item_path = item_data.get("path", "")
        item_name = item_data.get("name", os.path.basename(item_path))

        # 去重检查：若目标文件夹已存在该路径，则不重复添加
        if any(it.get("path") == item_path for it in items):
            self.toast.show_message(f"「{item_name}」已在当前分类中")
            return True

        # 物理迁移（若为托管存储模式）
        final_path = item_path
        if hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled():
            new_p = self.storage_engine.move_item_between_folders(item_path, src_folder, target_folder)
            if new_p:
                final_path = new_p

        new_entry = dict(item_data)
        new_entry["path"] = final_path

        if target_slot is not None and 0 <= target_slot <= len(items):
            items.insert(target_slot, new_entry)
        else:
            items.append(new_entry)
        self.set_current_folder_items(items)
        self.rebuild_grid()
        if self.is_drawer_open() and self.current_drawer:
            self.current_drawer.update_items(items)

        # 从源窗口/配置中安全移除
        src_win = next((w for w in BigFolderWindow._instances if w.get_current_folder_name() == src_folder), None)
        if src_win:
            src_win._internal_slot_drag_handled = True
            src_f = src_win.get_current_folder()
            if src_f:
                s_items = [it for it in src_f.get("items", []) if it.get("path") != item_path]
                src_win.set_current_folder_items(s_items)
                src_win.rebuild_grid()
                if src_win.is_drawer_open() and src_win.current_drawer:
                    src_win.current_drawer.update_items(s_items)
        else:
            s_folder = self.config_manager.get_folder_by_name(src_folder)
            if s_folder:
                s_items = [it for it in s_folder.get("items", []) if it.get("path") != item_path]
                self.config_manager.set_folder_items(src_folder, s_items, auto_save=True)

        self.toast.show_message(f"已移至「{target_folder}」")
        return True

    def on_storage_synced(self, folder_name: str, items: list):
        active_name = self.get_current_folder_name()
        if folder_name == active_name:
            self.rebuild_grid()
        if self.is_drawer_open() and hasattr(self.current_drawer, "update_items"):
            drawer_folder = getattr(self.current_drawer, "folder_name", "")
            if drawer_folder == folder_name or folder_name == active_name:
                if drawer_folder != folder_name and folder_name == active_name:
                    self.current_drawer.folder_name = folder_name
                    if hasattr(self.current_drawer, "title_edit"):
                        self.current_drawer.title_edit.setText(folder_name)
                self.current_drawer.update_items(items)

    def on_storage_folders_imported(self, imported_folders: list):
        if imported_folders:
            self.title_label.set_text(self.get_display_title_text())
            self.adjustSize()
            if getattr(self, "is_primary", False):
                self.toast.show_message(f"已同步分类：{', '.join(imported_folders)}", duration_ms=2500)

    def on_request_move_to_desktop(self, item_path: str):
        if not item_path:
            return
        dest = None
        if hasattr(self, "storage_engine"):
            dest = self.storage_engine.move_out_to_desktop(item_path)
        # 必须确保 dest 不为 None（即桌面物理移动或快捷方式创建真实成功着床），才从分类中移除，严防图标凭空蒸发
        if dest is not None:
            folder = self.get_current_folder()
            if folder:
                items = list(folder.get("items", []))
                new_items = [it for it in items if it.get("path") != item_path]
                if len(new_items) != len(items):
                    self.set_current_folder_items(new_items)
                    self.rebuild_grid()
                    if self.is_drawer_open() and self.current_drawer:
                        self.current_drawer.update_items(new_items)

    def on_request_recycle(self, item_path: str):
        if item_path and hasattr(self, "storage_engine"):
            self.storage_engine.delete_item(item_path)

    def action_tidy_desktop(self, auto_categorize: bool = True):
        # 1. 若当前二级展开抽屉处于打开状态，立即收起，消除全屏置顶透明遮罩
        if self.is_drawer_open():
            self.close_expanded_drawer(immediately=True)

        active_name = self.get_current_folder_name()
        if auto_categorize:
            title = "智能收纳桌面图标"
            msg = "扫描桌面上的快捷方式与文件，按类型自动归入对应分类卡片。\n\n是否继续？"
        else:
            title = f"收纳到「{active_name}」"
            msg = f"将桌面上的图标收纳至当前分类「{active_name}」。\n\n是否继续？"

        # 2. 居中且置顶显示现代原生确认对话框，彻底杜绝被底层窗口或置顶图层遮挡锁死
        msg_box = QMessageBox(QMessageBox.Icon.Question, title, msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Yes)
        reply = msg_box.exec()
        if reply == QMessageBox.StandardButton.Yes:
            self.storage_engine.set_folder_storage_mapped(active_name, True)
            res = self.storage_engine.tidy_desktop(target_folder_name=active_name, auto_categorize=auto_categorize)
            count = res.get("moved_count", 0)
            populated = res.get("populated_categories", [])

            created_cats = []
            if auto_categorize and populated and hasattr(self, "manager") and self.manager:
                from asset_migrator import create_desktop_cards_for_categories
                created_cats = create_desktop_cards_for_categories(self.manager, populated)
                if created_cats:
                    self.manager.validate_and_separate_all_desktop_cards(from_user=True)

            if created_cats:
                msg = f"已整理 {count} 项，新增 {len(created_cats)} 张分类卡片"
            else:
                msg = f"已整理 {count} 项桌面图标"
            self.toast.show_message(msg, duration_ms=3200)

            self.rebuild_grid()
            for w in BigFolderWindow._instances:
                if w != self:
                    w.rebuild_grid()

    def action_reorganize_categories(self):
        """
        One-click automated repair & redistribution:
        Deduplicates shortcuts and creates desktop cards for all populated categories.
        """
        if self.is_drawer_open():
            self.close_expanded_drawer(immediately=True)

        res = self.storage_engine.deduplicate_and_reorganize_storage()
        populated = res.get("populated_categories", [])
        created_cats = []
        if populated and hasattr(self, "manager") and self.manager:
            from asset_migrator import create_desktop_cards_for_categories
            created_cats = create_desktop_cards_for_categories(self.manager, populated)
            if created_cats:
                self.manager.validate_and_separate_all_desktop_cards(from_user=True)

        self.rebuild_grid()
        for w in BigFolderWindow._instances:
            if w != self:
                w.rebuild_grid()

        self.toast.show_message(
            f"整理完成：移除 {res.get('deduped_count', 0)} 个重复项，新增 {len(created_cats)} 张卡片",
            duration_ms=3200
        )

    def action_open_storage_dir(self):
        active_name = self.get_current_folder_name()
        folder_dir = self.storage_engine.get_folder_storage_path(active_name)
        if not os.path.exists(folder_dir):
            try:
                os.makedirs(folder_dir, exist_ok=True)
            except Exception:
                pass
        try:
            os.startfile(folder_dir)
        except Exception:
            import subprocess
            subprocess.Popen(f'explorer "{folder_dir}"')


    def action_import_storage_folders(self):
        imported = self.storage_engine.import_existing_storage_folders()
        if imported:
            self.rebuild_grid()
            self.toast.show_message(f"已导入 {len(imported)} 个分类：{', '.join(imported)}")
        else:
            self.toast.show_message("未发现新的本地分类")


    def action_open_asset_migrator(self):
        self.manager.open_asset_migrator(parent=self)

    def action_create_desktop_instance(self):
        all_folders = self.config_manager.get_folders()
        existing_cat_names = [f.get("name", "") for f in all_folders if f.get("name")]

        if hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled():
            storage_root = self.storage_engine.get_root_path()
            if os.path.isdir(storage_root):
                try:
                    for entry in os.listdir(storage_root):
                        full_p = os.path.join(storage_root, entry)
                        if os.path.isdir(full_p) and entry not in existing_cat_names:
                            existing_cat_names.append(entry)
                except Exception:
                    pass

        active_displayed_categories = set()
        for w in BigFolderWindow._instances:
            if hasattr(w, "bound_folder_name") and w.bound_folder_name:
                active_displayed_categories.add(w.bound_folder_name)

        options = ["新建分类..."]
        option_map = {}
        for cat in existing_cat_names:
            if cat in active_displayed_categories:
                opt_str = f"{cat} (已在桌面)"
            else:
                opt_str = f"{cat}"
            options.append(opt_str)
            option_map[opt_str] = cat

        chosen, ok = QInputDialog.getItem(
            self,
            "添加桌面卡片",
            "选择要在桌面显示的分类，或新建分类：",
            options,
            0,
            False
        )
        if not ok or not chosen:
            return

        if chosen == "新建分类...":
            new_name, ok_name = QInputDialog.getText(
                self,
                "新建分类",
                "请输入新分类名称："
            )
            if not ok_name or not new_name.strip():
                return
            target_name = new_name.strip()
            if not self.config_manager.get_folder_by_name(target_name):
                self.config_manager.add_folder(target_name, set_active=False)
            if hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled():
                f_dir = self.storage_engine.get_folder_storage_path(target_name)
                os.makedirs(f_dir, exist_ok=True)
                self.storage_engine.set_folder_storage_mapped(target_name, True)
        else:
            target_name = option_map.get(chosen)
            if not target_name:
                target_name = chosen.replace(" (已在桌面)", "").strip()
            if not self.config_manager.get_folder_by_name(target_name):
                self.config_manager.add_folder(target_name, set_active=False)
            if hasattr(self, "storage_engine") and self.config_manager.is_managed_storage_enabled():
                f_dir = self.storage_engine.get_folder_storage_path(target_name)
                if os.path.isdir(f_dir):
                    self.storage_engine.set_folder_storage_mapped(target_name, True)
                    self.storage_engine.sync_folder_from_storage(target_name)

        # 若该分类已在桌面存在活动卡片，直接复用并提示，绝不生成重复卡片
        existing_card = next(
            (w for w in BigFolderWindow._instances if getattr(w, "bound_folder_name", None) == target_name),
            None,
        )
        if existing_card is not None:
            existing_card.show()
            self.toast.show_message(f"「{target_name}」卡片已在桌面")
            return

        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        avail_geo = screen.availableGeometry() if screen else screen_geo

        # Smart placement beside current window
        new_x = self.x() + self.width() + 16
        new_y = self.y()
        if new_x + self.width() > screen_geo.right() - 80:
            all_xs = [w.x() for w in BigFolderWindow._instances if w.isVisible()]
            min_left = min(all_xs) if all_xs else screen_geo.left() + 24
            new_x = min_left
            new_y = self.y() + self.height() + 16
            if new_y + self.height() > screen_geo.bottom() - 120:
                new_y = max(screen_geo.top() + 32, screen_geo.top() + 49)
                new_x = min_left + 40

        other_rects = [w.geometry() for w in BigFolderWindow._instances if w.isVisible()]
        snapped_pos = calculate_magnetic_snap(
            target_pos=QPoint(new_x, new_y),
            target_size=self.size(),
            other_rects=other_rects,
            screen_rect=screen_geo,
            available_geometry=avail_geo,
            snap_dist=14
        )
        new_x = snapped_pos.x()
        new_y = snapped_pos.y()

        new_inst = self.config_manager.add_instance(
            folder_name=target_name,
            pos_x=layout_scale.to_canvas(new_x, self.layout_scale),
            pos_y=layout_scale.to_canvas(new_y, self.layout_scale),
            locked=False
        )

        if self.manager is not None:
            new_win = self.manager.create_instance_from_data(new_inst)
        else:
            new_win = BigFolderWindow(
                config_manager=self.config_manager,
                instance_id=new_inst["instance_id"],
                storage_engine=getattr(self, "storage_engine", None),
                manager=self.manager
            )
            new_win.show()

        self.toast.show_message(f"已添加「{target_name}」卡片")

    def action_close_this_instance(self):
        target_name = self.get_current_folder_name()
        reply = QMessageBox.question(
            self,
            "关闭此卡片",
            f"确定从桌面移除「{target_name}」卡片吗？\n\n分类与本地文件仍会保留，可随时在设置中重新开启。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if hasattr(self, "instance_id") and self.instance_id:
            self.config_manager.remove_instance(self.instance_id)

        if self.manager is not None:
            self.manager.remove_instance_window(self)
        else:
            self.close()


    def closeEvent(self, event):
        if self in BigFolderWindow._instances:
            BigFolderWindow._instances.remove(self)
        if self.manager is not None and hasattr(self, "instance_id") and self.instance_id in self.manager.windows:
            self.manager.windows.pop(self.instance_id, None)
        if hasattr(self, "drag_hover_timer") and self.drag_hover_timer is not None:
            try:
                self.drag_hover_timer.stop()
            except Exception:
                pass
        overlay = getattr(self, "_page_turn_overlay", None)
        if overlay is not None:
            try:
                overlay.stop_and_hide()
            except Exception:
                pass
        preview = getattr(self, "_drag_preview", None)
        if preview is not None:
            self._drag_preview = None
            try:
                preview.hide()
                preview.close()
                preview.deleteLater()
            except Exception:
                pass
        if hasattr(self, "desktop_watchdog"):
            self.desktop_watchdog.stop()
        if hasattr(self, "storage_engine") and self.storage_engine is not None:
            for sig_name, slot_fn in (
                ("storage_synced", self.on_storage_synced),
                ("storage_folders_imported", self.on_storage_folders_imported),
                ("operation_message", self._on_storage_operation_message),
            ):
                sig = getattr(self.storage_engine, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect(slot_fn)
                    except Exception:
                        pass
            if getattr(self, "_owns_storage_engine", True):
                self.storage_engine.stop()
        self.unregister_global_hotkey()

        # Primary role handoff
        if getattr(self, "is_primary", False) and len(BigFolderWindow._instances) > 0:
            next_primary = BigFolderWindow._instances[0]
            next_primary.is_primary = True
            next_primary.register_global_hotkey()

        if self.is_drawer_open():
            self.current_drawer.close()
            self.current_drawer = None
        super().closeEvent(event)


class BigFolderWindowManager:
    """
    Coordinates multiple independent BigFolderWindow instances across the desktop.
    Handles unified lifecycle, hotkey delegation, IPC summon, and synchronized layer operations.
    """
    def _push_cards_for_target(self, target_rect: QRect, exclude_win):
        """落位被占用时，把相交的邻居卡片推到最近空位（级联，最多 4 轮），并持久化。"""
        from grid_snap import resolve_card_collision, get_desktop_icons_exclusion_rect

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        icon_zone = get_desktop_icons_exclusion_rect(avail)
        guard = target_rect.adjusted(-16, -16, 16, 16)

        def rects():
            out = []
            for w in self.windows.values():
                card = getattr(w, "card", None)
                if card is not None:
                    out.append(QRect(w.pos() + card.pos(), card.size()))
            return out

        for _ in range(4):
            blockers = []
            for inst_id, w in list(self.windows.items()):
                if w is exclude_win:
                    continue
                card = getattr(w, "card", None)
                if card is None:
                    continue
                r = QRect(w.pos() + card.pos(), card.size())
                if guard.intersects(r):
                    blockers.append((inst_id, w, r))
            if not blockers:
                return True
            all_rects = rects()
            for inst_id, w, r in blockers:
                others = [x for x in all_rects if x != r]
                if icon_zone and not icon_zone.isEmpty():
                    others.append(icon_zone)
                others.append(guard)
                new_pt = resolve_card_collision(r, others, avail, card_gap=16)
                w.move(new_pt - w.card.pos())
                self._persist_card_window_pos("folder", inst_id, w, w.pos())
                all_rects = rects()
        return False

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.windows: Dict[str, BigFolderWindow] = {}
        self.weather_window = None
        self.clock_window = None
        self.monitor_window = None
        self.memo_window = None
        self.media_window = None
        self.everything_window = None
        self.file_shelf_window = None
        self.is_zen_hidden = False
        self._zen_saved_windows = []
        self.layout_scale = 1.0
        self.desktop_blank_handler = desktop_layer.DesktopBlankDoubleClickHandler()
        self.desktop_blank_handler.desktop_blank_double_clicked.connect(self.toggle_zen_mode)
        if self.config_manager.is_double_click_desktop_hide_enabled():
            self.desktop_blank_handler.enable()
        self.shared_storage_engine = ManagedStorageEngine(self.config_manager)

    def start(self):
        # 布局画布只记录一次：即用户保存这份布局时所在的屏幕可用区域。
        # 之后任意分辨率都按它等比换算，画布本身不随远程会话漂移。
        self._ensure_layout_canvas()
        self.layout_scale = layout_scale.scale_for_config(self.config_manager)
        instances = self.config_manager.get_instances()
        if not instances:
            f_name = self.config_manager.get_active_folder_name()
            inst = self.config_manager.add_instance(f_name, 797, 49)
            instances = [inst]

        # 小组件网格：卡片矩形由管理器统一提供，组件拖动/整理共用同一套网格线
        widget_grid.set_folder_rect_provider(self._folder_card_rects)

        # Automatic deduplication & ghost instance pruning safeguard:
        # Guarantee no two active desktop cards are bound to the same folder_name,
        # and discard phantom instances whose folders were dissolved/deleted.
        valid_instances = []
        used_folders = set()
        available_folders = [f.get("name") for f in self.config_manager.get_folders() if f.get("name")]
        for inst in instances:
            f_name = inst.get("folder_name")
            if f_name not in available_folders:
                unassigned = [fn for fn in available_folders if fn not in used_folders]
                if unassigned:
                    f_name = unassigned[0]
                    inst["folder_name"] = f_name
                    self.config_manager.update_instance(inst.get("instance_id"), {"folder_name": f_name})
                    used_folders.add(f_name)
                    valid_instances.append(inst)
                else:
                    self.config_manager.remove_instance(inst.get("instance_id"))
            elif f_name in used_folders:
                unassigned = [fn for fn in available_folders if fn not in used_folders]
                if unassigned:
                    f_name = unassigned[0]
                    inst["folder_name"] = f_name
                    self.config_manager.update_instance(inst.get("instance_id"), {"folder_name": f_name})
                    used_folders.add(f_name)
                    valid_instances.append(inst)
                else:
                    self.config_manager.remove_instance(inst.get("instance_id"))
            else:
                used_folders.add(f_name)
                valid_instances.append(inst)

        if not valid_instances and available_folders:
            inst = self.config_manager.add_instance(available_folders[0], 797, 49)
            valid_instances = [inst]

        for inst in valid_instances:
            self.create_instance_from_data(inst)

        # 1. 桌面天气小组件
        weather_cfg = self.config_manager.get_weather_config()
        if weather_cfg.get("enabled", True):
            self.init_weather_widget(weather_cfg)

        # 2. 灵动时钟日历小组件
        clock_cfg = self.config_manager.get_clock_config()
        if clock_cfg.get("enabled", False):
            self.init_clock_widget(clock_cfg)

        # 3. 系统硬件管家微件
        monitor_cfg = self.config_manager.get_monitor_config()
        if monitor_cfg.get("enabled", False):
            self.init_monitor_widget(monitor_cfg)

        # 4. 桌面随手便签待办微件
        memo_cfg = self.config_manager.get_memo_config()
        if memo_cfg.get("enabled", False):
            self.init_memo_widget(memo_cfg)

        # 5. 系统媒体播放微件
        media_cfg = self.config_manager.get_media_config()
        if media_cfg.get("enabled", False):
            self.init_media_widget(media_cfg)

        # 6. 桌面文件中转站 (File Shelf · 临时集装箱)
        shelf_cfg = self.config_manager.get_file_shelf_config()
        if shelf_cfg.get("enabled", True):
            self.init_file_shelf(shelf_cfg)

        # 7. Everything 全盘搜索小组件
        ev_cfg = self.config_manager.get_everything_config()
        if ev_cfg.get("enabled", True):
            self.init_everything_widget(ev_cfg)

        # 小组件基类默认 dark，启动时显式同步玻璃参数与用户主题
        self._apply_glass_to_widgets()

        # 启动完全沿用 config 里保存的位置：自动整理会改写用户排好的桌面。
        # 只在用户主动「对齐 / 整理」时写回；屏幕变化只做临时避让（persist=False）。
        self.bind_all_widget_resizes()
        self._install_screen_watch()
        if self.layout_scale != 1.0:
            # 缩放后贴边卡片可能有几像素舍入越界，按各自所在显示器静默钳一次；不拆重叠、不写盘
            self._clamp_once()

        # 首帧渲染完成后修剪一次工作集：PySide6 / WinRT 导入期与启动解包的冷页
        # 交回系统，任务管理器里的常驻内存从 ~260MB 降到 ~30MB，且不影响状态。
        QTimer.singleShot(3000, desktop_layer.trim_process_working_set)

    def _layout_rects(self):
        """config 里所有桌面元素的包围盒素材，仅用于首次推断布局画布。"""
        cm = self.config_manager
        rects = []
        for inst in cm.get_instances():
            rects.append((inst.get("pos_x", 797), inst.get("pos_y", 49),
                          inst.get("card_width") or 320, inst.get("card_height") or 260))
        for key in ("weather", "clock", "monitor", "memo", "media", "everything"):
            getter = getattr(cm, f"get_{key}_config", None)
            if getter is None:
                continue
            c = getter()
            rects.append((c.get("x", 0), c.get("y", 0),
                          c.get("width") or 310, c.get("height") or 226))
        return rects

    def _ensure_layout_canvas(self):
        """首次运行记录布局画布，已有画布则不动，现有坐标零迁移。

        在远程小分辨率下首次启动时，布局坐标会明显超出屏幕，此时按包围盒
        还原它原本所属的画布，避免把小屏误当成基准。
        """
        cm = self.config_manager
        if cm.get("layout_screen_w", 0) and cm.get("layout_screen_h", 0):
            return
        size = layout_scale.current_screen_size()
        if size is None:
            return
        app = QApplication.instance()
        # 无头自动化环境没有真实桌面，直接以虚拟屏幕为画布
        if app is not None and app.platformName() == "offscreen":
            canvas = size
        else:
            canvas = layout_scale.infer_canvas(self._layout_rects(), size)
        cm.set("layout_screen_w", canvas[0], auto_save=False)
        cm.set("layout_screen_h", canvas[1], auto_save=True)

    def _widget_screen_pos(self, cfg, default_x: int, default_y: int):
        s = self.layout_scale
        return (layout_scale.to_screen(int(cfg.get("x", default_x)), s),
                layout_scale.to_screen(int(cfg.get("y", default_y)), s))

    def _apply_widget_glass(self, win, radius: Optional[int] = None,
                            opacity: Optional[float] = None, theme: Optional[str] = None):
        if win is None:
            return
        if radius is None:
            s = getattr(self, "layout_scale", 1.0) or 1.0
            radius = layout_scale.to_screen(self.config_manager.get("border_radius", 26), s)
        if opacity is None:
            opacity = float(self.config_manager.get("opacity", 0.88))
        if theme is None:
            theme = str(self.config_manager.get("theme", "dark"))
        if hasattr(win, "set_glass_params"):
            win.set_glass_params(radius, opacity, theme)
        elif hasattr(win, "set_theme"):
            win.set_theme(theme)

    def _apply_glass_to_widgets(self, radius: Optional[int] = None,
                                opacity: Optional[float] = None, theme: Optional[str] = None):
        s = getattr(self, "layout_scale", 1.0) or 1.0
        base_r = int(self.config_manager.get("border_radius", 26)) if radius is None else int(radius)
        eff_r = layout_scale.to_screen(base_r, s) if radius is None else int(radius)
        eff_op = float(self.config_manager.get("opacity", 0.88)) if opacity is None else float(opacity)
        eff_theme = str(self.config_manager.get("theme", "dark")) if theme is None else str(theme)
        self._last_glass_sig = (eff_r, round(eff_op, 3), eff_theme)
        for attr in ("weather_window", "clock_window", "monitor_window",
                     "memo_window", "media_window", "everything_window", "file_shelf_window"):
            w = getattr(self, attr, None)
            if w is not None:
                self._apply_widget_glass(w, eff_r, eff_op, eff_theme)

    def _sync_glass_if_changed(self):
        s = getattr(self, "layout_scale", 1.0) or 1.0
        eff_r = layout_scale.to_screen(int(self.config_manager.get("border_radius", 26)), s)
        eff_op = float(self.config_manager.get("opacity", 0.88))
        eff_theme = str(self.config_manager.get("theme", "dark"))
        sig = (eff_r, round(eff_op, 3), eff_theme)
        if getattr(self, "_last_glass_sig", None) != sig:
            self._apply_glass_to_widgets(eff_r, eff_op, eff_theme)

    def refresh_card_style(self, radius: Optional[int] = None,
                           opacity: Optional[float] = None, theme: Optional[str] = None):
        """Refreshes border_radius, opacity, and theme across all folder cards and active desktop widgets."""
        base_r = int(self.config_manager.get("border_radius", 26)) if radius is None else int(radius)
        eff_op = float(self.config_manager.get("opacity", 0.88)) if opacity is None else float(opacity)
        eff_theme = str(self.config_manager.get("theme", "dark")) if theme is None else str(theme)
        s = getattr(self, "layout_scale", 1.0) or 1.0
        eff_r = layout_scale.to_screen(base_r, s)
        for win in list(self.windows.values()):
            win.theme = eff_theme
            win_s = getattr(win, "layout_scale", s) or 1.0
            win_r = layout_scale.to_screen(base_r, win_s)
            if hasattr(win, "card") and win.card is not None:
                win.card.set_card_style(win_r, eff_op, theme=eff_theme)
            if hasattr(win, "toast") and win.toast is not None and win.toast.isVisible():
                win.toast.update()
            if hasattr(win, "apply_blur_effect"):
                win.apply_blur_effect()
            win.rebuild_grid()
        self._apply_glass_to_widgets(eff_r, eff_op, eff_theme)

    # ------------------------------------------------------------------
    # 小组件装配（天气 / 时钟 / 硬件 / 便签 / 播放器 / 中转站 / 搜索）
    # ------------------------------------------------------------------
    def init_weather_widget(self, cfg: Dict[str, Any]):
        from weather_widget import WeatherWidgetWindow
        wx, wy = self._widget_screen_pos(cfg, 614, 180)
        win = WeatherWidgetWindow(
            x=wx, y=wy,
            theme=self.config_manager.get("theme", "dark"),
            widget_size=cfg.get("size", "2x2"),
            locked=bool(cfg.get("locked", False)),
            config_manager=self.config_manager)
        win.manager = self
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_weather_pos_changed)
        win.show()
        self.weather_window = win

    def init_clock_widget(self, cfg: Dict[str, Any]):
        from desktop_widgets import ClockWidgetWindow
        win = ClockWidgetWindow(self.config_manager)
        win.manager = self
        win.move(*self._widget_screen_pos(cfg, 300, 180))
        mode = cfg.get("size", "2x2")
        if mode != "2x2" and hasattr(win, "set_size_mode"):
            try:
                win.set_size_mode(mode)
            except Exception:
                pass
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_clock_pos_changed)
        win.show()
        self.clock_window = win

    def init_monitor_widget(self, cfg: Dict[str, Any]):
        from desktop_widgets import SystemMonitorWidgetWindow
        win = SystemMonitorWidgetWindow(self.config_manager)
        win.manager = self
        win.move(*self._widget_screen_pos(cfg, 900, 180))
        mode = cfg.get("size", "2x2")
        if mode != "2x2" and hasattr(win, "set_size_mode"):
            try:
                win.set_size_mode(mode)
            except Exception:
                pass
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_monitor_pos_changed)
        win.show()
        self.monitor_window = win

    def init_memo_widget(self, cfg: Dict[str, Any]):
        from desktop_widgets import MemoWidgetWindow
        win = MemoWidgetWindow(self.config_manager)
        win.manager = self
        win.move(*self._widget_screen_pos(cfg, 600, 400))
        mode = cfg.get("size", "2x2")
        if mode != "2x2" and hasattr(win, "set_size_mode"):
            try:
                win.set_size_mode(mode)
            except Exception:
                pass
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_memo_pos_changed)
        win.show()
        self.memo_window = win

    def init_media_widget(self, cfg: Dict[str, Any]):
        from desktop_widgets import NowPlayingWidgetWindow
        win = NowPlayingWidgetWindow(self.config_manager)
        win.manager = self
        win.move(*self._widget_screen_pos(cfg, 1200, 200))
        mode = cfg.get("size", "2x2")
        if mode != "2x2" and hasattr(win, "set_size_mode"):
            try:
                win.set_size_mode(mode)
            except Exception:
                pass
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_media_pos_changed)
        win.show()
        self.media_window = win

    def init_file_shelf(self, cfg: Dict[str, Any]):
        from file_shelf import FileShelfWindow
        win = FileShelfWindow(self.config_manager)
        win.manager = self
        self._apply_widget_glass(win)
        # 中转站按 edge + y_ratio 自己贴边落位，这里再 move 会二次定位导致跳动
        win.show()
        self.file_shelf_window = win

    def toggle_file_shelf(self):
        """托盘菜单：显示/隐藏桌面文件中转站。"""
        win = getattr(self, "file_shelf_window", None)
        if win is None:
            self.init_file_shelf(self.config_manager.get_file_shelf_config())
            win = self.file_shelf_window
        if win is None:
            return
        if win.isVisible():
            win.hide()
        else:
            win.show()
            win.raise_()

    def init_everything_widget(self, cfg: Dict[str, Any]):
        from everything_widget import EverythingWidgetWindow
        win = EverythingWidgetWindow(config_manager=self.config_manager)
        win.manager = self
        win.move(*self._widget_screen_pos(cfg, 620, 420))
        mode = cfg.get("size", "2x2")
        if mode != "2x2" and hasattr(win, "set_size_mode"):
            try:
                win.set_size_mode(mode)
            except Exception:
                pass
        self._apply_widget_glass(win)
        win.position_changed.connect(self._on_everything_pos_changed)
        win.show()
        self.everything_window = win

    # --- 位置回写：显示坐标反算回布局画布 ---
    def _persist_widget_pos(self, key: str, x: int, y: int):
        s = self.layout_scale
        cfg = getattr(self.config_manager, f"get_{key}_config")()
        cfg["x"] = layout_scale.to_canvas(x, s)
        cfg["y"] = layout_scale.to_canvas(y, s)
        getattr(self.config_manager, f"set_{key}_config")(cfg)

    def _on_weather_pos_changed(self, x, y):
        self._persist_widget_pos("weather", x, y)

    def _on_clock_pos_changed(self, x, y):
        self._persist_widget_pos("clock", x, y)

    def _on_monitor_pos_changed(self, x, y):
        self._persist_widget_pos("monitor", x, y)

    def _on_memo_pos_changed(self, x, y):
        self._persist_widget_pos("memo", x, y)

    def _on_media_pos_changed(self, x, y):
        self._persist_widget_pos("media", x, y)

    def _on_everything_pos_changed(self, x, y):
        self._persist_widget_pos("everything", x, y)

    def separate_overlapping_cards(self, persist: bool = True):
        """只把真正重合的桌面卡片推到相邻槽位，不动其它卡片已保存的布局。

        persist=False 用于启动/屏幕变化时的临时避让：只调整显示，绝不改写用户排好的位置。
        """
        cards = []
        for inst_id, win in list(self.windows.items()):
            if win.isVisible() and hasattr(win, "card") and win.card and win.card.isVisible():
                cards.append((inst_id, win))
        if len(cards) < 2:
            return
        placed = []
        for inst_id, win in cards:
            win_avail = layout_scale.screen_available_geometry_for_widget(win)
            safe_right = win_avail.right() if win_avail is not None else 1920
            rect = QRect(win.pos() + win.card.pos(), win.card.size())
            for prev in placed:
                overlap = rect.intersected(prev)
                if overlap.isEmpty() or overlap.width() * overlap.height() < 0.35 * rect.width() * rect.height():
                    continue
                nx, ny = prev.x() + prev.width() + 16, rect.y()
                if nx + rect.width() > safe_right:
                    nx, ny = prev.x(), prev.y() + prev.height() + 16
                new_win_pos = QPoint(nx - win.card.pos().x(), ny - win.card.pos().y())
                win.move(new_win_pos)
                if persist:
                    self._persist_card_window_pos("folder", inst_id, win, new_win_pos)
                rect = QRect(win.pos() + win.card.pos(), win.card.size())
                break
            placed.append(rect)

    def _persist_card_window_pos(self, w_type: str, w_id: str, win: Any, pos: QPoint):
        """统一持久化桌面卡片或小组件的新坐标"""
        if w_type == "folder":
            win.persist_instance_position(pos.x(), pos.y())
        elif w_type == "weather":
            self._on_weather_pos_changed(pos.x(), pos.y())
        elif w_type == "clock":
            self._on_clock_pos_changed(pos.x(), pos.y())
        elif w_type == "monitor":
            self._on_monitor_pos_changed(pos.x(), pos.y())
        elif w_type == "memo":
            self._on_memo_pos_changed(pos.x(), pos.y())
        elif w_type == "media":
            self._on_media_pos_changed(pos.x(), pos.y())
        elif w_type == "everything":
            self._on_everything_pos_changed(pos.x(), pos.y())

    def bind_all_widget_resizes(self):
        """小组件无级缩放：恢复已保存尺寸，并把松手后的尺寸写回各自 config。"""
        for attr, key in (("weather_window", "weather"), ("clock_window", "clock"),
                          ("monitor_window", "monitor"), ("memo_window", "memo"),
                          ("media_window", "media"), ("everything_window", "everything")):
            win = getattr(self, attr, None)
            if win is None or not hasattr(win, "resized") or getattr(win, "_resize_bound", False):
                continue
            win._resize_bound = True

            def save(w, h, _key=key, _win=win):
                getter = getattr(self.config_manager, f"get_{_key}_config", None)
                setter = getattr(self.config_manager, f"set_{_key}_config", None)
                if getter and setter:
                    cfg = getter()
                    cfg["width"] = layout_scale.to_canvas(w, self.layout_scale)
                    cfg["height"] = layout_scale.to_canvas(h, self.layout_scale)
                    setter(cfg)
                    _win._layout_base_w = cfg["width"]
                    _win._layout_base_h = cfg["height"]
                # 换尺寸后卡片可能压到邻居或落在网格外，立刻按网格重新落位
                self.snap_widget_to_grid(_win, _key)

            win.resized.connect(save)
            getter = getattr(self.config_manager, f"get_{key}_config", None)
            if getter and hasattr(win, "_update_window_size"):
                cfg = getter()
                w, h = cfg.get("width"), cfg.get("height")
                # 没保存过尺寸的组件用出厂尺寸当基准，同样随分辨率缩放
                base_w, base_h = (int(w), int(h)) if (w and h) else (win.card_w, win.card_h)
                win._layout_base_w, win._layout_base_h = base_w, base_h
                win.card_w = layout_scale.to_screen(base_w, self.layout_scale)
                win.card_h = layout_scale.to_screen(base_h, self.layout_scale)
                win._update_window_size()
                # 启动恢复尺寸时也必须走一次内容重排，否则布局/档位停留在默认档
                if hasattr(win, "on_card_resized"):
                    win.on_card_resized()

    def _install_screen_watch(self):
        """监听分辨率 / DPI / 远程会话变化：可用区域变了就整桌等比换算，越界才兜底钳位。"""
        app = QApplication.instance()
        if app is None:
            return
        self._last_screen_geo = None
        for scr in app.screens():
            scr.availableGeometryChanged.connect(self._on_screen_geometry_changed)
            scr.geometryChanged.connect(self._on_screen_geometry_changed)
            scr.logicalDotsPerInchChanged.connect(self._on_screen_geometry_changed)
        app.screenAdded.connect(self._on_screen_added)
        app.primaryScreenChanged.connect(self._on_screen_geometry_changed)
        primary = app.primaryScreen()
        if primary is not None:
            self._last_screen_geo = primary.availableGeometry()
        # 远程会话/锁屏有时不发几何信号，5 秒兜底检查一次越界
        self._clamp_timer = QTimer()
        self._clamp_timer.setInterval(5000)
        self._clamp_timer.timeout.connect(self._ensure_cards_in_screen)
        self._clamp_timer.start()
        # 卡片尺寸要等 show/首帧后才稳定，延迟再检查一次
        QTimer.singleShot(1200, self._ensure_cards_in_screen)

    def _on_screen_added(self, screen):
        screen.availableGeometryChanged.connect(self._on_screen_geometry_changed)
        screen.geometryChanged.connect(self._on_screen_geometry_changed)
        screen.logicalDotsPerInchChanged.connect(self._on_screen_geometry_changed)
        self._on_screen_geometry_changed()

    def _on_screen_geometry_changed(self, *args):
        # 远程桌面/切分辨率时信号会连发，防抖到一次重算
        QTimer.singleShot(350, self._reflow_for_screen_change)

    def _widget_targets(self):
        targets = list(self.windows.values())
        for attr in ("weather_window", "clock_window", "monitor_window",
                     "memo_window", "media_window", "everything_window"):
            widget = getattr(self, attr, None)
            if widget is not None:
                targets.append(widget)
        return targets

    def _resolve_window_avail(self, win, fallback_avail: Optional[QRect] = None) -> Optional[QRect]:
        """返回窗口实际所在屏幕的 availableGeometry；仅在脱屏或副屏断开时回退主屏。"""
        app = QApplication.instance()
        primary = QApplication.primaryScreen() if app is not None else None
        primary_avail = primary.availableGeometry() if primary is not None else None
        screens = app.screens() if (app is not None and hasattr(app, "screens")) else []

        scr = None
        if app is not None and win is not None:
            try:
                if hasattr(win, "geometry") and callable(win.geometry):
                    center = win.geometry().center()
                else:
                    center = QRect(
                        int(win.x()), int(win.y()), int(win.width()), int(win.height())
                    ).center()
                scr = QApplication.screenAt(center)
            except Exception:
                scr = None

        if scr is not None and screens and scr is screens[0] and primary is not None:
            scr = primary

        if fallback_avail is not None:
            if scr is not None and primary is not None and scr is not primary:
                return scr.availableGeometry()
            if primary_avail is None or fallback_avail != primary_avail:
                return fallback_avail

        if scr is not None:
            return scr.availableGeometry()
        win_avail = layout_scale.screen_available_geometry_for_widget(win)
        if win_avail is not None:
            return win_avail
        return fallback_avail or primary_avail

    def _clamp_once(self, avail: Optional[QRect] = None):
        for win in self._widget_targets():
            try:
                win_avail = self._resolve_window_avail(win, avail)
                if win_avail is None:
                    continue
                max_x = win_avail.left() + max(0, win_avail.width() - win.width())
                max_y = win_avail.top() + max(0, win_avail.height() - win.height())
                new_x = max(win_avail.left(), min(win.x(), max_x))
                new_y = max(win_avail.top(), min(win.y(), max_y))
                if (new_x, new_y) != (win.x(), win.y()):
                    # 临时避让只改显示；emit position_changed 会把远程分辨率下的
                    # 避让坐标写回 config，用户原布局就再也回不去了。
                    win.move(new_x, new_y)
            except Exception:
                pass

    def _clamp_all_to_screen(self, avail: Optional[QRect] = None):
        """夹进各自所在屏幕可用区域。拆分重叠可能把卡片又推出边界，所以要「钳位→拆分」迭代收敛。"""
        if getattr(self, "is_zen_hidden", False):
            return
        self._clamp_once(avail)
        for _ in range(3):
            self.separate_overlapping_cards(persist=False)
            self._clamp_once(avail)
        # 卡片被重新排布后，压在上面的小组件要跟着让位（同样不写回用户布局）
        self.separate_widgets_from_cards(persist=False)

    def _any_window_outside(self, avail: Optional[QRect] = None) -> bool:
        for win in self._widget_targets():
            try:
                win_avail = self._resolve_window_avail(win, avail)
                if win_avail is None:
                    continue
                avail_right = win_avail.left() + win_avail.width()
                avail_bottom = win_avail.top() + win_avail.height()
                if (win.x() < win_avail.left() or win.y() < win_avail.top()
                        or win.x() + win.width() > avail_right
                        or win.y() + win.height() > avail_bottom):
                    return True
            except Exception:
                continue
        return False

    def _ensure_cards_in_screen(self):
        # 禅模式把卡片搬到了屏幕外，绝不能被钳位定时器拉回来
        if getattr(self, "is_zen_hidden", False):
            return
        app = QApplication.instance()
        screen = QApplication.primaryScreen() if app else None
        if screen is None:
            return
        if self._any_window_outside():
            self._clamp_all_to_screen()
            self._last_screen_geo = screen.availableGeometry()

    def apply_layout_scale(self):
        """按当前屏幕重算所有桌面元素的显示坐标与尺寸。

        一律从 config 基准重新换算，不基于上一帧累乘，反复切分辨率无漂移；
        用户在同一会话里手动摆过的位置已反算写回 config，重算不会丢。
        """
        new_s = layout_scale.scale_for_config(self.config_manager)
        old_s = getattr(self, "layout_scale", 1.0) or 1.0
        self.layout_scale = new_s
        hidden = getattr(self, "is_zen_hidden", False)

        for win in self.windows.values():
            if hasattr(win, "set_layout_scale"):
                win.set_layout_scale(new_s, reposition=not hidden)

        for key, attr in (("weather", "weather_window"), ("clock", "clock_window"),
                          ("monitor", "monitor_window"), ("memo", "memo_window"),
                          ("media", "media_window"), ("everything", "everything_window")):
            win = getattr(self, attr, None)
            if win is None:
                continue
            cfg = getattr(self.config_manager, f"get_{key}_config")()
            if not hidden:
                win.move(layout_scale.to_screen(int(cfg.get("x", 0)), new_s),
                         layout_scale.to_screen(int(cfg.get("y", 0)), new_s))
            w = cfg.get("width") or getattr(win, "_layout_base_w", 0)
            h = cfg.get("height") or getattr(win, "_layout_base_h", 0)
            if w and h and hasattr(win, "_update_window_size"):
                win.card_w = layout_scale.to_screen(int(w), new_s)
                win.card_h = layout_scale.to_screen(int(h), new_s)
                win._update_window_size()
                if hasattr(win, "on_card_resized"):
                    win.on_card_resized()

        self._apply_glass_to_widgets()

        if hidden and old_s and new_s != old_s:
            # 禅模式窗口在屏外，只换算记账坐标，退出时仍落回正确位置
            ratio = new_s / old_s
            self._zen_saved_positions = [
                (w, int(round(x * ratio)), int(round(y * ratio)))
                for w, x, y in getattr(self, "_zen_saved_positions", [])]

    def _reflow_for_screen_change(self, *args):
        app = QApplication.instance()
        screen = QApplication.primaryScreen() if app else None
        if screen is None:
            return
        avail = screen.availableGeometry()
        all_geoms = tuple(s.availableGeometry() for s in app.screens()) if hasattr(app, "screens") else (avail,)
        prev_geo = getattr(self, "_last_screen_geo", None)
        prev_all = getattr(self, "_last_all_screen_geos", None)
        if prev_geo == avail and (prev_all is None or prev_all == all_geoms):
            return
        primary_changed = (prev_geo != avail)
        self._last_screen_geo = avail
        self._last_all_screen_geos = all_geoms
        if primary_changed:
            self.apply_layout_scale()
        # 等比缩放后相对布局不变；只有真的越出各自所在屏幕才做临时钳位
        if self._any_window_outside():
            self._clamp_all_to_screen()

    @staticmethod
    def _find_free_desktop_slot(size: QSize, occupied, avail: QRect, gap: int = 16,
                                origin: Optional[QPoint] = None, margin: int = 0):
        """在可用区域里按 24px 步长扫一个不与任何卡片相交的位置。

        给了 origin 就取离它最近的那个（不按左上角优先），避免组件被甩到屏幕另一头；
        margin 是窗口阴影边距，保证卡片 ± margin 也在工作区内，否则下一轮钳位会把卡片拉走，来回抽搐。
        """
        step = 24
        left = avail.left() + margin + 8
        top = avail.top() + margin + 8
        right = avail.right() - margin - 8
        bottom = avail.bottom() - margin - 8
        best = None
        best_dist = 0
        y = top
        while y + size.height() - 1 <= bottom:
            x = left
            while x + size.width() - 1 <= right:
                cand = QRect(QPoint(x, y), size)
                if not any(cand.adjusted(-gap, -gap, gap, gap).intersects(o) for o in occupied):
                    dist = 0 if origin is None else (QPoint(x, y) - origin).manhattanLength()
                    if best is None or dist < best_dist:
                        best, best_dist = QPoint(x, y), dist
                        if dist == 0:
                            return best
                x += step
            y += step
        return best

    def validate_and_separate_all_desktop_cards(self, from_user: bool = False):
        """用户主动操作时的全局自愈：避开桌面图标区、拉回屏幕、推开重叠卡片。

        启动流程不走这里（from_user=False 直接返回），保证不改动用户已保存的布局。
        """
        if not from_user:
            return
        from grid_snap import get_desktop_icons_exclusion_rect, resolve_card_collision

        primary = QApplication.primaryScreen()
        primary_avail = primary.availableGeometry() if primary else QRect(0, 0, 1920, 1080)
        icon_zone = get_desktop_icons_exclusion_rect(primary_avail)

        placed = []
        for inst_id, win in list(self.windows.items()):
            card = getattr(win, "card", None)
            if card is None:
                continue
            avail = layout_scale.screen_available_geometry_for_widget(win) or primary_avail
            on_primary = (avail == primary_avail)
            rect = QRect(win.pos() + card.pos(), card.size())
            conflicts = list(placed)
            if on_primary and icon_zone and not icon_zone.isEmpty():
                conflicts.append(icon_zone)
            # 与卡片间的小缝隙重叠（<400px²）不算冲突，避免为了几像素把卡片推走几十像素
            meaningful = any(
                rect.intersected(o).width() * rect.intersected(o).height() > 400
                for o in conflicts if o is not icon_zone)
            needs_fix = (not avail.contains(rect)) or meaningful or (
                on_primary and icon_zone is not None and not icon_zone.isEmpty() and rect.intersects(icon_zone))
            if needs_fix and conflicts:
                new_pt = resolve_card_collision(rect, conflicts, avail, card_gap=16)
                if new_pt != rect.topLeft():
                    win.move(new_pt - card.pos())
                    self._persist_card_window_pos("folder", inst_id, win, win.pos())
                    rect = QRect(new_pt, rect.size())
            placed.append(rect)


    @staticmethod
    def _nudge_widget_out(rect: QRect, obstacles, avail: QRect, margin: int, gap: int = 16):
        """把压住卡片/越界的组件挪到最近的合法位置：先试 16px 相邻槽位，再扫最近空位。

        两条路都要求「卡片 ± margin」留在工作区内，且不与任何障碍相交。
        找不到合法位置就返回 None —— 原地不动，绝不为了避让把组件甩到别处。
        """
        safe_left = avail.left() + margin
        safe_top = avail.top() + margin
        safe_right = avail.right() - margin
        safe_bottom = avail.bottom() - margin
        if rect.width() > safe_right - safe_left or rect.height() > safe_bottom - safe_top:
            return None

        def _fits(pt: QPoint) -> bool:
            if (pt.x() < safe_left or pt.y() < safe_top
                    or pt.x() + rect.width() - 1 > safe_right
                    or pt.y() + rect.height() - 1 > safe_bottom):
                return False
            cand = QRect(pt, rect.size())
            return not any(cand.intersects(o) for o in obstacles)

        candidates = []
        for other in obstacles:
            if not rect.intersects(other):
                continue
            candidates += [
                QPoint(other.right() + 1 + gap, rect.top()),
                QPoint(other.left() - gap - rect.width(), rect.top()),
                QPoint(rect.left(), other.bottom() + 1 + gap),
                QPoint(rect.left(), other.top() - gap - rect.height()),
            ]

        best = None
        for pt in candidates:
            if not _fits(pt):
                continue
            dist = (pt - rect.topLeft()).manhattanLength()
            if best is None or dist < best[0]:
                best = (dist, pt)
        if best is not None:
            return best[1]

        return BigFolderWindowManager._find_free_desktop_slot(
            rect.size(), obstacles, avail, gap=gap, origin=rect.topLeft(), margin=margin)

    def separate_widgets_from_cards(self, persist: bool = True):
        """小组件的碰撞体积：压住卡片或越界时，挪到最近的合法空位。

        persist=False 时只调整显示位置，不写回配置（启动/屏幕变化时保护用户布局）。
        只做最小位移（相邻 16px 槽位优先），不再全屏扫描「第一个空位」——那会把组件
        甩到屏幕角落，下一轮钳位再拉回来，看起来就是到处乱飞抽搐。
        """
        from desktop_widgets import MARGIN_SHADOW

        pairs = self._widget_pairs()
        if not pairs:
            return

        cards = self._placement_obstacles()

        screen = QApplication.primaryScreen()
        fallback_avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        now = time.monotonic()
        placed = list(cards)
        for win, w_type in pairs:
            avail = layout_scale.screen_available_geometry_for_widget(win) or fallback_avail
            rect = QRect(win.pos() + QPoint(MARGIN_SHADOW, MARGIN_SHADOW),
                         QSize(win.card_w, win.card_h))
            # 正在拖 / 刚松手 2 秒内不参与自动搬运，否则会和用户抢鼠标
            if getattr(win, "_dragging", False) or (
                    now - getattr(win, "_user_moved_at", 0.0) < 2.0):
                placed.append(rect)
                continue
            out_of_bounds = (rect.left() < avail.left() + 4 or rect.top() < avail.top() + 4
                             or rect.right() > avail.right() - 4 or rect.bottom() > avail.bottom() - 4)
            def _overlap_area(other):
                inter = rect.intersected(other)
                return 0 if inter.isEmpty() else inter.width() * inter.height()

            area = max((_overlap_area(o) for o in placed), default=0)
            needs_move = out_of_bounds or area > 0.18 * rect.width() * rect.height()
            if needs_move:
                # 先落到最近的网格线（和卡片列/行对齐），扫不到再退回相邻槽位
                grid_rect = widget_grid.snap_card_rect(rect, placed, avail)
                target = grid_rect.topLeft() if grid_rect is not None else \
                    self._nudge_widget_out(rect, placed, avail, MARGIN_SHADOW)
                if target is not None and target != rect.topLeft():
                    win.move(target - QPoint(MARGIN_SHADOW, MARGIN_SHADOW))
                    win._user_moved_at = now
                    if persist:
                        self._persist_card_window_pos(w_type, w_type, win, win.pos())
                    rect = QRect(target, rect.size())
            placed.append(rect)

    def _widget_pairs(self):
        pairs = []
        for attr, w_type in (("weather_window", "weather"), ("clock_window", "clock"),
                             ("monitor_window", "monitor"), ("memo_window", "memo"),
                             ("media_window", "media"), ("everything_window", "everything")):
            win = getattr(self, attr, None)
            if win is not None and hasattr(win, "card_w"):
                pairs.append((win, w_type))
        return pairs

    def _folder_card_rects(self):
        """文件夹卡片占位（含底部标题条）：网格线与自动避让都以它为准。"""
        rects = []
        for win in self.windows.values():
            card = getattr(win, "card", None)
            if card is None or not card.isVisible():
                continue
            rects.append(QRect(win.pos() + card.pos(),
                               QSize(card.width(), card.height() + 36)))
        return rects

    def _placement_obstacles(self):
        """组件可落位的障碍集合：文件夹卡片 + 桌面图标列。不依赖全局 provider，单测也成立。"""
        return self._folder_card_rects() + widget_grid.icon_zone_rects()

    def align_widgets_to_grid(self, pack: bool = False):
        """网格式整理：按从左到右、从上到下把小组件放进网格空位，互不重叠。

        与拖拽落位共用同一套网格线，所以整理后必然和文件夹卡片同列同排。
        """
        from desktop_widgets import MARGIN_SHADOW

        pairs = self._widget_pairs()
        if not pairs:
            return 0
        avail = widget_grid.available_geometry()
        obstacles = self._placement_obstacles()
        moved = 0
        for win, w_type in pairs:
            size = QSize(win.card_w, win.card_h)
            cur = QRect(win.pos() + QPoint(MARGIN_SHADOW, MARGIN_SHADOW), size)
            target = widget_grid.next_free_slot(size, obstacles, avail) if pack else \
                widget_grid.snap_card_rect(cur, obstacles, avail)
            if target is None:
                obstacles.append(cur)
                continue
            if target.topLeft() != cur.topLeft():
                win.move(target.topLeft() - QPoint(MARGIN_SHADOW, MARGIN_SHADOW))
                win._user_moved_at = time.monotonic()
                self._persist_card_window_pos(w_type, w_type, win, win.pos())
                moved += 1
            obstacles.append(target)
        return moved

    def snap_widget_to_grid(self, win, w_type: str) -> bool:
        """单个组件（改尺寸后用）重新落到网格线上的空位。"""
        from desktop_widgets import MARGIN_SHADOW

        offset = win.card.pos() if getattr(win, "card", None) is not None else QPoint(MARGIN_SHADOW, MARGIN_SHADOW)
        cur = QRect(win.pos() + offset, QSize(win.card_w, win.card_h))
        obstacles = self._placement_obstacles()
        for other, _ in self._widget_pairs():
            if other is win:
                continue
            o_off = other.card.pos() if getattr(other, "card", None) is not None else offset
            obstacles.append(QRect(other.pos() + o_off, QSize(other.card_w, other.card_h)))
        avail = self._resolve_window_avail(win) or widget_grid.available_geometry()
        target = widget_grid.snap_card_rect(cur, obstacles, avail)
        if target is None or target.topLeft() == cur.topLeft():
            return False
        win.move(target.topLeft() - offset)
        self._persist_card_window_pos(w_type, w_type, win, win.pos())
        return True


    @staticmethod
    def _natively_visible(win) -> bool:
        """桌面层窗口常用原生 ShowWindow 显示，Qt 的 isVisible() 可能滞后；以 HWND 状态为准。"""
        try:
            hwnd = int(win.winId())
            if desktop_layer.user32 and hwnd:
                return bool(desktop_layer.user32.IsWindowVisible(hwnd))
        except Exception:
            pass
        return bool(win.isVisible())

    @staticmethod
    def _set_native_visible(win, visible: bool):
        """桌面层窗口挂在 WorkerW 下，Qt 的 hide()/show() 不一定会改到原生可见性，需直接 ShowWindow。"""
        try:
            hwnd = int(win.winId())
        except Exception:
            return
        if not hwnd or not desktop_layer.user32:
            return
        desktop_layer.user32.ShowWindow(hwnd, 4 if visible else 0)  # SW_SHOWNOACTIVATE / SW_HIDE

    def _zen_hide_window(self, win):
        """把窗口搬出屏幕。

        桌面层子窗口（挂在 WorkerW 下）清不掉 WS_VISIBLE，只藏绘制体会在桌面留下白底；
        搬出屏幕是唯一确定有效、且不会闪回的隐藏方式，坐标另外记账以便原样搬回。
        """
        if hasattr(win, "is_drawer_open") and callable(win.is_drawer_open) and win.is_drawer_open():
            try:
                win.close_expanded_drawer(immediately=True)
            except Exception:
                pass
        try:
            if not hasattr(self, "_zen_saved_positions"):
                self._zen_saved_positions = []
            self._zen_saved_positions.append((win, win.x(), win.y()))
            win.move(-12000, -9000)
        except Exception:
            pass
        card = getattr(win, "card", None)
        if card is not None:
            card.hide()
            title = getattr(win, "title_label", None)
            if title is not None:
                title.hide()

    def _zen_restore_window(self, win, x: int, y: int):
        win.move(x, y)
        win.show()
        card = getattr(win, "card", None)
        if card is not None:
            card.show()
            if hasattr(win, "update_title_visibility"):
                win.update_title_visibility()
        if win is not getattr(self, "file_shelf_window", None):
            if hasattr(win, "apply_layer_mode") and callable(win.apply_layer_mode):
                win.apply_layer_mode()
            else:
                desktop_layer.pin_to_desktop(int(win.winId()))

    def toggle_zen_mode(self, animate: bool = True):
        """
        双击桌面空白处隐藏/呼出全部桌面卡片与微件 (禅模式)。

        animate 仅为调用方参数兼容：桌面层窗口挂在 WorkerW 下，windowOpacity 动画与
        ShowWindow(SW_HIDE) 都会被着床逻辑覆盖，直接隐藏卡片的绘制体才不会闪回。
        """
        all_widgets = list(self.windows.values())
        for attr in ("weather_window", "clock_window", "monitor_window", "memo_window",
                     "media_window", "everything_window", "file_shelf_window"):
            widget = getattr(self, attr, None)
            if widget:
                all_widgets.append(widget)

        if not self.is_zen_hidden:
            # 按 HWND 实际可见性记账：桌面层窗口的 Qt isVisible() 可能滞后
            self._zen_saved_windows = [w for w in all_widgets if w.isVisible() or self._natively_visible(w)]
            self._zen_saved_positions = []
            self.is_zen_hidden = True
            for w in self._zen_saved_windows:
                if hasattr(w, "desktop_watchdog"):
                    w.desktop_watchdog.set_enabled(False)
                self._zen_hide_window(w)

            # 禅模式低功耗静默：挂起媒体轮询、放缓硬件监控并修剪工作集
            if hasattr(self, "media_service") and self.media_service:
                try:
                    self.media_service.pause_polling()
                except Exception:
                    pass
            if getattr(self, "monitor_window", None) and hasattr(self.monitor_window, "timer"):
                try:
                    self.monitor_window.timer.setInterval(5000)
                except Exception:
                    pass
            QTimer.singleShot(600, desktop_layer.trim_process_working_set)
            return

        self.is_zen_hidden = False

        if hasattr(self, "media_service") and self.media_service:
            try:
                self.media_service.resume_polling()
            except Exception:
                pass
        if getattr(self, "monitor_window", None) and hasattr(self.monitor_window, "timer"):
            try:
                self.monitor_window.timer.setInterval(1500)
            except Exception:
                pass

        saved_positions = {id(w): (x, y) for w, x, y in getattr(self, "_zen_saved_positions", [])}
        targets = [w for w in self._zen_saved_windows if w is not None] or all_widgets
        self._zen_saved_windows = []
        self._zen_saved_positions = []
        for w in targets:
            if hasattr(w, "desktop_watchdog"):
                w.desktop_watchdog.set_enabled(True)
            x, y = saved_positions.get(id(w), (w.x(), w.y()))
            self._zen_restore_window(w, x, y)


    def create_instance_from_data(self, inst_data: dict) -> BigFolderWindow:
        inst_id = inst_data.get("instance_id")
        if inst_id and inst_id in self.windows and self.windows[inst_id] is not None:
            existing = self.windows[inst_id]
            existing.show()
            return existing
        target_folder = inst_data.get("folder_name")
        if target_folder:
            for existing_win in self.windows.values():
                if existing_win is not None and getattr(existing_win, "bound_folder_name", None) == target_folder:
                    existing_win.show()
                    return existing_win
        win = BigFolderWindow(
            config_manager=self.config_manager,
            instance_id=inst_id,
            storage_engine=self.shared_storage_engine,
            manager=self
        )
        self.windows[inst_id] = win
        win.show()
        return win

    def create_instance(self, folder_name: str, pos_x: int, pos_y: int, locked: bool = False, rows: int = 3, cols: int = 3) -> BigFolderWindow:
        for existing_win in self.windows.values():
            if existing_win is not None and getattr(existing_win, "bound_folder_name", None) == folder_name:
                existing_win.show()
                return existing_win
        new_inst = self.config_manager.add_instance(folder_name, pos_x, pos_y, locked, rows=rows, cols=cols)
        return self.create_instance_from_data(new_inst)

    def remove_instance_window(self, win: BigFolderWindow):
        inst_id = getattr(win, "instance_id", None)
        if inst_id and inst_id in self.windows:
            self.windows.pop(inst_id, None)
        win.close()

    def get_primary_window(self) -> Optional[BigFolderWindow]:
        for w in self.windows.values():
            if getattr(w, "is_primary", False) and w.isVisible():
                return w
        for w in self.windows.values():
            if w.isVisible():
                return w
        return next(iter(self.windows.values())) if self.windows else None

    def open_asset_migrator(self, parent=None):
        try:
            from legacy_migrator import LegacyMigrationDialog
            # 从设置页进入时挂到设置页以保持模态层级；从桌面卡片进入时不挂底层透明窗口
            dlg_parent = parent if (parent is not None and not isinstance(parent, BigFolderWindow)) else None
            toast_host = parent if isinstance(parent, BigFolderWindow) else self.get_primary_window()
            dlg = LegacyMigrationDialog(window_manager=self, parent=dlg_parent)
            if toast_host and hasattr(toast_host, "toast"):
                dlg.matrix_created.connect(lambda cats: toast_host.toast.show_message(f"已添加 {len(cats)} 张分类卡片"))
            dlg.exec()
        except Exception as e:
            print("Failed to open asset migrator dialog:", e)

    def open_legacy_migrator(self, parent=None):
        return self.open_asset_migrator(parent=parent)

    def open_settings_dialog(self, tab_index: int = 0):
        try:
            from settings_dialog import SettingsDialog
            dlg = SettingsDialog(self.config_manager, window_manager=self, initial_tab=tab_index, parent=None)
            dlg.exec()
        except Exception as e:
            print("Failed to open settings dialog:", e)

    def toggle_summon(self, folder_name: Optional[str] = None, open_drawer: Optional[bool] = None):
        if self.is_zen_hidden:
            self.toggle_zen_mode(animate=True)

        if not self.windows:
            f_name = self.config_manager.get_active_folder_name()
            self.create_instance(f_name, 797, 49)

        cursor_pos = QCursor.pos()
        active_screen = None
        for s in QApplication.screens():
            if s.geometry().contains(cursor_pos):
                active_screen = s
                break
        if not active_screen:
            active_screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()

        # Check if any drawer is currently open on any window
        open_win = None
        for w in self.windows.values():
            if w.is_drawer_open():
                open_win = w
                break

        if open_win is not None:
            drawer = open_win.current_drawer
            drawer_screen = None
            if drawer is not None and hasattr(drawer, "screen_geo"):
                drawer_center = drawer.screen_geo.center()
                for s in QApplication.screens():
                    if s.geometry().contains(drawer_center):
                        drawer_screen = s
                        break
                if not drawer_screen:
                    drawer_screen = QApplication.screenAt(drawer_center)
            if not drawer_screen and drawer is not None:
                drawer_screen = drawer.screen()

            # If drawer is open on the SAME screen as cursor, cleanly dismiss it
            if drawer_screen == active_screen:
                open_win.close_expanded_drawer()
                return
            else:
                # If drawer is open on a DIFFERENT screen, cleanly dismiss old one
                # and continue below to summon on active cursor screen!
                open_win.close_expanded_drawer()

        # Check if any desktop card is currently summon-elevated
        summoned_win = None
        for w in self.windows.values():
            if getattr(w, "is_summoned_front", False):
                summoned_win = w
                break

        if summoned_win is not None:
            # Dismiss all summoned cards back to wallpaper bottom
            for w in self.windows.values():
                w.dismiss_summon_card()
            return

        # Locate target window to summon
        target_win = None
        if folder_name:
            for w in self.windows.values():
                if w.get_current_folder_name() == folder_name:
                    target_win = w
                    break
        else:
            for w in self.windows.values():
                card_center = w.geometry().center()
                if active_screen.geometry().contains(card_center):
                    target_win = w
                    break

        if not target_win:
            target_win = self.get_primary_window()

        if target_win:
            mode = self.config_manager.get("summon_target", "card")
            if open_drawer is True or mode == "drawer":
                target_win.open_expanded_drawer(show_search=True, target_screen=active_screen)
            else:
                target_win.summon_card_to_front(target_screen=active_screen)

    def notify_ready(self, message: str = ""):
        primary = self.get_primary_window()
        for w in self.windows.values():
            if not w.isVisible():
                w.show()
            if hasattr(w, "desktop_watchdog") and w.config_manager.get("layer_mode", "bottom") == "bottom":
                w.desktop_watchdog.re_bed()
        if primary and message:
            primary.toast.show_message(message)

    def bring_to_front_and_toast(self, message: str = ""):
        if self.is_zen_hidden:
            self.toggle_zen_mode(animate=False)

        if not self.windows:
            f_name = self.config_manager.get_active_folder_name()
            self.create_instance(f_name, 797, 49)

        primary = self.get_primary_window()
        for w in self.windows.values():
            if not w.is_drawer_open():
                w.card.show()
                w.update_title_visibility()
            w.show()
            w.apply_layer_mode()
        if primary and message:
            primary.toast.show_message(message)

    def set_all_layer_mode(self, mode: str):
        for w in self.windows.values():
            w.set_layer_mode(mode)
            w.show()

    def prompt_add_instance(self):
        primary = self.get_primary_window()
        if not primary:
            f_name = self.config_manager.get_active_folder_name()
            primary = self.create_instance(f_name, 797, 49)
            primary.toast.show_message("已恢复桌面卡片")
            return
        primary.action_create_desktop_instance()

    def lock_all_instances(self, locked: bool):
        self.config_manager.set_all_instances_locked(locked)
        for w in self.windows.values():
            w.toast.show_message("已锁定全部卡片" if locked else "已解锁全部卡片")

    def toggle_lock_all(self):
        cur = bool(self.config_manager.get("locked", False))
        self.lock_all_instances(not cur)

    def action_create_dock_shortcut(self):
        primary = self.get_primary_window()
        if primary:
            primary.action_create_dock_shortcut()
        else:
            generate_mydock_shortcut()

    def action_tidy_desktop(self, auto_categorize: bool = True):
        primary = self.get_primary_window()
        if primary:
            primary.action_tidy_desktop(auto_categorize=auto_categorize)

    def close_all(self):
        if getattr(self, "_clamp_timer", None) is not None:
            try:
                self._clamp_timer.stop()
            except Exception:
                pass
            self._clamp_timer = None

        if getattr(self, "desktop_blank_handler", None) is not None:
            try:
                self.desktop_blank_handler.disable()
            except Exception:
                pass

        if getattr(self, "shared_storage_engine", None) is not None:
            try:
                self.shared_storage_engine.stop()
            except Exception:
                pass

        for attr in ("weather_window", "clock_window", "monitor_window", "memo_window",
                     "media_window", "everything_window", "file_shelf_window"):
            widget_win = getattr(self, attr, None)
            if widget_win is not None:
                if getattr(widget_win, "desktop_watchdog", None) is not None:
                    try:
                        widget_win.desktop_watchdog.stop()
                    except Exception:
                        pass
                try:
                    widget_win.close()
                except Exception:
                    pass
                setattr(self, attr, None)

        for w in list(self.windows.values()):
            if getattr(w, "desktop_watchdog", None) is not None:
                try:
                    w.desktop_watchdog.stop()
                except Exception:
                    pass
            try:
                w.close()
            except Exception:
                pass
        self.windows.clear()

        # Shut down singleton background service executors if already instantiated
        ms_mod = sys.modules.get("media_service") or sys.modules.get("stacky.media.media_service")
        ms_cls = getattr(ms_mod, "MediaControlService", None) if ms_mod is not None else None
        if ms_cls is not None and hasattr(ms_cls, "shutdown_instance"):
            try:
                ms_cls.shutdown_instance()
            except Exception:
                pass


sys.modules.setdefault("main_window", sys.modules[__name__])
sys.modules.setdefault("stacky.core.main_window", sys.modules[__name__])
