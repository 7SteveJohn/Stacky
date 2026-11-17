import os
import re
import subprocess
import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Tuple

from PySide6.QtCore import (
    Qt, QPoint, QPointF, QRect, QRectF, QSize, Signal, QEvent, QVariantAnimation, QEasingCurve, QTimer, QUrl, QMimeData
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QLinearGradient, QRadialGradient,
    QPainterPath, QPixmap, QKeyEvent, QCursor, QFontMetrics, QDrag, QPalette
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QScrollArea, QPushButton, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QApplication, QMenu, QInputDialog, QFileDialog,
    QSizePolicy
)

from icon_utils import (
    get_app_icon_pixmap, resolve_actual_path, resolve_shortcut_target, resolve_url_shortcut,
    create_squircle_path, prewarm_icons_batch, parse_dropped_mime_data,
    is_drop_acceptable, extract_site_name_from_url, is_internal_item_drag,
    mark_cross_folder_drop, consume_cross_folder_drop, STACKY_ITEM_MIME
)
from search_utils import (
    search_items, safe_calculate_math, parse_web_quick_link,
    match_system_commands, execute_system_command, search_everything_files
)
from widgets import (
    run_as_admin, show_file_properties, safe_launch_target,
    _get_menu_qss, _is_admin_target, _paint_admin_shield, _is_drag_cancelled_by_user
)
from web_dialog import AddWebBookmarkDialog
from ui_kit import theme_pick
from PySide6.QtWidgets import QDialog


def _ui_font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont("Segoe UI Variable Text")
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(max(1, int(px)))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return font


def _clean_item_tooltip(name: str, path: str) -> str:
    clean_name = (name or "").strip()
    raw_path = (path or "").strip()
    url_str = ""
    if raw_path.startswith(("http://", "https://")):
        url_str = raw_path
    elif raw_path.lower().endswith(".url"):
        url_str = resolve_url_shortcut(raw_path) or ""
    host = QUrl(url_str).host() if url_str.startswith(("http://", "https://")) else ""
    if not clean_name and raw_path:
        clean_name = host or os.path.splitext(os.path.basename(raw_path))[0]
    if host and clean_name and host.lower() != clean_name.lower():
        return f"{clean_name}\n{host}"
    return clean_name


def _drawer_theme(widget) -> str:
    """抽屉自绘控件从所属顶层覆盖层读主题；不在抽屉内时按深色处理。"""
    owner = widget.window() if widget is not None else None
    return getattr(owner, "theme", "dark")


@contextmanager
def _drawer_modal_guard(widget):
    """临时挂起抽屉失焦自动收起，防止原生文件选择器 / 子弹窗激活时把抽屉在底下意外关闭。"""
    owner = widget.window() if widget is not None else None
    guard_fn = getattr(owner, "_modal_guard", None)
    if callable(guard_fn):
        with guard_fn():
            yield
        return
    yield


def _ink(widget, light, dark):
    """浅色底板上的文字/图标换成深色；其余主题原样返回，保证零回归。"""
    return theme_pick(_drawer_theme(widget), light, dark)


_INK_LIGHT = QColor(15, 23, 42, 245)
_INK_LIGHT_SUB = QColor(71, 85, 105, 235)
_INK_LIGHT_BLUE = QColor(30, 64, 175, 235)


_DRAWER_COLOR_DECL = re.compile(
    r"(?<![\w-])color\s*:\s*(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", re.I)


def recolor_qss(qss: str, color: str) -> str:
    """把 QSS 里的 color 声明整体换掉，裸声明与选择器块两种写法都能覆盖。"""
    return _DRAWER_COLOR_DECL.sub(f"color: {color}", qss)


class ModernInputDialog(QDialog):
    """
    超椭圆磨砂玻璃输入弹窗:
    - 强制 Qt.WindowType.WindowStaysOnTopHint 置顶，避免被无边框覆盖层遮挡;
    - 居中对齐至屏幕几何中心，纯 QPainter 连续曲率超椭圆磨砂底板 (自适应浅色/深色主题);
    - 弹出后立即聚焦输入框并全选文本，按回车直接提交，按 Esc 直接取消。
    """
    def __init__(self, title: str, prompt: str, text: str = "", placeholder: str = "",
                 parent=None, theme: Optional[str] = None):
        super().__init__(None)
        self._anchor_parent = parent
        self.setWindowTitle(title)
        self.setFixedSize(380, 210)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._title = title
        self._prompt = prompt
        if theme is not None:
            self.theme = theme
        elif parent is not None:
            self.theme = getattr(parent, "theme", None) or _drawer_theme(parent)
        else:
            self.theme = "dark"
        is_light = (self.theme == "light")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)

        # 标题
        lbl_title = QLabel(title, self)
        lbl_title.setFont(_ui_font(16, QFont.Weight.DemiBold))
        title_fg = "#0f172a" if is_light else "#f8fafc"
        lbl_title.setStyleSheet(
            f"color: {title_fg}; font-size: 16px; font-weight: 600;"
            " font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';"
        )
        layout.addWidget(lbl_title)

        # 说明提示文案
        lbl_prompt = QLabel(prompt, self)
        lbl_prompt.setWordWrap(True)
        lbl_prompt.setFont(_ui_font(13, QFont.Weight.Normal))
        prompt_fg = "#475569" if is_light else "#94a3b8"
        lbl_prompt.setStyleSheet(
            f"color: {prompt_fg}; font-size: 13px;"
            " font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';"
        )
        layout.addWidget(lbl_prompt)

        # 现代输入框
        self.input_edit = QLineEdit(self)
        self.input_edit.setText(text)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setFixedHeight(38)
        self.input_edit.setFont(_ui_font(14, QFont.Weight.Normal))
        if is_light:
            self.input_edit.setStyleSheet("""
                QLineEdit {
                    background-color: rgba(15, 23, 42, 0.045);
                    color: #0f172a;
                    border: 1px solid rgba(15, 23, 42, 0.14);
                    border-radius: 10px;
                    padding: 6px 12px;
                    font-size: 14px;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';
                }
                QLineEdit:hover {
                    background-color: rgba(15, 23, 42, 0.07);
                    border-color: rgba(15, 23, 42, 0.22);
                }
                QLineEdit:focus {
                    background-color: #ffffff;
                    border: 1px solid rgba(59, 130, 246, 0.65);
                }
            """)
        else:
            self.input_edit.setStyleSheet("""
                QLineEdit {
                    background-color: rgba(255, 255, 255, 0.06);
                    color: #f8fafc;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 10px;
                    padding: 6px 12px;
                    font-size: 14px;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';
                }
                QLineEdit:hover {
                    background-color: rgba(255, 255, 255, 0.09);
                    border-color: rgba(255, 255, 255, 0.20);
                }
                QLineEdit:focus {
                    background-color: rgba(255, 255, 255, 0.10);
                    border: 1px solid rgba(59, 130, 246, 0.65);
                }
            """)
        layout.addWidget(self.input_edit)

        # 按钮栏
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消", self)
        btn_cancel.setFixedSize(80, 32)
        btn_cancel.setFont(_ui_font(13, QFont.Weight.Medium))
        if is_light:
            btn_cancel.setStyleSheet("""
                QPushButton {
                    background-color: rgba(15, 23, 42, 0.05);
                    color: #334155;
                    border: 1px solid rgba(15, 23, 42, 0.10);
                    border-radius: 8px;
                    font-size: 13px;
                    font-weight: 500;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';
                }
                QPushButton:hover {
                    background-color: rgba(15, 23, 42, 0.10);
                    color: #0f172a;
                }
            """)
        else:
            btn_cancel.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.06);
                    color: #cbd5e1;
                    border: 1px solid rgba(255, 255, 255, 0.10);
                    border-radius: 8px;
                    font-size: 13px;
                    font-weight: 500;
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.12);
                    color: #ffffff;
                }
            """)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("确定", self)
        btn_ok.setFixedSize(80, 32)
        btn_ok.setFont(_ui_font(13, QFont.Weight.DemiBold))
        btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI';
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
        """)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

        # 多显示器与任务栏安全居中
        screen = None
        if parent is not None:
            try:
                p_geo = parent.window().frameGeometry()
                screen = QApplication.screenAt(p_geo.center())
            except Exception:
                screen = None
        if screen is None:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.left() + (geo.width() - self.width()) // 2,
                geo.top() + (geo.height() - self.height()) // 2,
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = create_squircle_path(rect, 20.0)

        grad = QLinearGradient(0, 0, 0, self.height())
        if self.theme == "light":
            grad.setColorAt(0.0, QColor(251, 252, 255, 252))
            grad.setColorAt(1.0, QColor(243, 246, 251, 254))
            painter.fillPath(path, grad)
            painter.strokePath(path, QPen(QColor(15, 23, 42, 24), 1.0))
        elif self.theme == "oled":
            grad.setColorAt(0.0, QColor(16, 17, 23, 252))
            grad.setColorAt(1.0, QColor(9, 10, 14, 254))
            painter.fillPath(path, grad)
            painter.strokePath(path, QPen(QColor(255, 255, 255, 32), 1.0))
        else:
            grad.setColorAt(0.0, QColor(22, 24, 31, 248))
            grad.setColorAt(1.0, QColor(15, 17, 23, 252))
            painter.fillPath(path, grad)
            painter.strokePath(path, QPen(QColor(255, 255, 255, 36), 1.0))

    def showEvent(self, event):
        super().showEvent(event)
        self.input_edit.setFocus()
        self.input_edit.selectAll()

    @classmethod
    def getText(cls, parent, title: str, prompt: str, text: str = "", placeholder: str = "",
                theme: Optional[str] = None) -> Tuple[str, bool]:
        dlg = cls(title=title, prompt=prompt, text=text, placeholder=placeholder, parent=parent, theme=theme)
        res = dlg.exec()
        if res == QDialog.DialogCode.Accepted:
            return dlg.input_edit.text().strip(), True
        return "", False


class MobileExpandedItemWidget(QWidget):
    """
    Mobile-style App Item in Expanded Folder View:
    - 52x52px High-Res Squircle App Icon
    - App Name text label centered directly below the icon (mobile launcher style)
    - Tactile press compression (0.92x) & hover squircle highlight with smooth easing
    - Full system-level right-click menu (Run as Admin, Open Location, Rename, Custom Icon, Copy Path, Properties, Remove)
    """
    clicked = Signal(dict)
    item_selected = Signal(int)
    item_changed = Signal(dict, dict)  # (orig_item, updated_item)
    item_removed = Signal(dict)
    request_move_to_desktop = Signal(dict)
    request_recycle = Signal(dict)
    request_close = Signal()

    def __init__(
        self,
        item_data: Dict[str, Any],
        is_selected: bool = False,
        open_mode: str = "double",
        display_mode: str = "classic",
        show_admin_badge: bool = True,
        show_running_badge: bool = True,
        parent=None
    ):
        super().__init__(parent)
        self.item_data = item_data
        self.open_mode = open_mode
        self.display_mode = display_mode
        self.show_admin_badge = show_admin_badge
        self.show_running_badge = show_running_badge
        self.grid_index = 0
        self.is_hovered = False
        self.is_pressed = False
        self.is_selected = is_selected
        path_str = self.item_data.get("path", "")
        name = self.item_data.get("name", "")
        custom_icon = self.item_data.get("custom_icon", "")
        self._cached_pixmap = get_app_icon_pixmap(path_str, name, target_size=128, custom_icon_path=custom_icon)
        self._scaled_pixmap = None

        self.scale_factor: float = 1.0
        self.glow_alpha: float = 0.0

        self.scale_anim = QVariantAnimation(self)
        self.scale_anim.valueChanged.connect(self._on_scale_anim_tick)

        self.glow_anim = QVariantAnimation(self)
        self.glow_anim.valueChanged.connect(self._on_glow_anim_tick)

        self.setFixedSize(92, 102)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_tooltip()

    def update_tooltip(self):
        name = self.item_data.get("name", "")
        path = self.item_data.get("path", "")
        self.setToolTip(_clean_item_tooltip(name, path))

    def _animate_scale(self, target: float, duration: int, easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic):
        self.scale_anim.stop()
        self.scale_anim.setStartValue(self.scale_factor)
        self.scale_anim.setEndValue(target)
        self.scale_anim.setDuration(duration)
        self.scale_anim.setEasingCurve(easing)
        self.scale_anim.start()

    def _animate_glow(self, target: float, duration: int, easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic):
        self.glow_anim.stop()
        self.glow_anim.setStartValue(self.glow_alpha)
        self.glow_anim.setEndValue(target)
        self.glow_anim.setDuration(duration)
        self.glow_anim.setEasingCurve(easing)
        self.glow_anim.start()

    def _on_scale_anim_tick(self, val):
        self.scale_factor = float(val)
        self.update()

    def _on_glow_anim_tick(self, val):
        self.glow_alpha = float(val)
        self.update()

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            if selected:
                self._animate_scale(1.04, 150)
                self._animate_glow(1.0, 150)
            else:
                self._animate_scale(1.0, 180)
                self._animate_glow(0.0, 180)
            self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self._animate_scale(1.07, 160, QEasingCurve.Type.OutCubic)
        self._animate_glow(1.0, 180, QEasingCurve.Type.OutCubic)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self._animate_scale(1.0, 200, QEasingCurve.Type.OutCubic)
        self._animate_glow(0.0, 200, QEasingCurve.Type.OutCubic)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_pressed = True
            self.drag_start_pos = event.pos()
            self._animate_scale(0.92, 100, QEasingCurve.Type.OutQuad)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_pressed:
            self.is_pressed = False
            target_scale = 1.07 if self.is_hovered else 1.0
            self._animate_scale(target_scale, 220, QEasingCurve.Type.OutBack)
            if hasattr(self, "drag_start_pos") and (event.pos() - self.drag_start_pos).manhattanLength() < 6:
                if self.open_mode == "single":
                    self.clicked.emit(self.item_data)
                else:
                    self.set_selected(True)
                    self.item_selected.emit(getattr(self, "grid_index", 0))
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.open_mode == "double":
                self.clicked.emit(self.item_data)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self.item_data or not self.item_data.get("path"):
            return
        if hasattr(self, "drag_start_pos") and (event.pos() - self.drag_start_pos).manhattanLength() < 10:
            return

        path = self.item_data.get("path", "")
        real_path = resolve_actual_path(path)
        if not real_path or not os.path.exists(real_path):
            real_path = path

        overlay = self.window()
        source_folder = getattr(overlay, "folder_name", "")

        drag = QDrag(self)
        mime = QMimeData()

        # 封装跨文件夹无损拖拽 payload
        import json
        import uuid
        drag_token = uuid.uuid4().hex
        payload = {
            "source_folder": source_folder,
            "source_instance_id": "",
            "source_index": getattr(self, "grid_index", 0),
            "item_data": self.item_data,
            "drag_token": drag_token
        }
        mime.setData(STACKY_ITEM_MIME, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        mime.setData("application/x-bigfolder-drawer-item", str(getattr(self, "grid_index", 0)).encode("utf-8"))
        if real_path and os.path.exists(real_path):
            mime.setUrls([QUrl.fromLocalFile(os.path.abspath(real_path))])
        drag.setMimeData(mime)

        custom_icon = self.item_data.get("custom_icon", "")
        pix = get_app_icon_pixmap(real_path, self.item_data.get("name", ""), 64, custom_icon_path=custom_icon)
        if pix and not pix.isNull():
            dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
            px_sz = max(16, int(round(48 * dpr)))
            drag_pm = pix.scaled(px_sz, px_sz, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            drag_pm.setDevicePixelRatio(dpr)
            drag.setPixmap(drag_pm)
            drag.setHotSpot(QPoint(24, 24))

        self.is_pressed = False
        self._animate_scale(1.0, 150, QEasingCurve.Type.OutCubic)

        if hasattr(overlay, "_internal_drawer_drag_handled"):
            overlay._internal_drawer_drag_handled = False

        action = drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

        was_internal = (
            consume_cross_folder_drop(drag_token)
            or getattr(overlay, "_internal_drawer_drag_handled", False)
        )
        if hasattr(overlay, "_internal_drawer_drag_handled"):
            overlay._internal_drawer_drag_handled = False

        if not was_internal:
            if action == Qt.DropAction.IgnoreAction and _is_drag_cancelled_by_user():
                return
            # Dropped outside the drawer (e.g. onto Windows Desktop or Explorer)
            sheet = getattr(overlay, "sheet", None)
            sheet_contains = sheet.geometry().contains(overlay.mapFromGlobal(QCursor.pos())) if sheet else False
            if action != Qt.DropAction.IgnoreAction or not sheet_contains:
                self.request_move_to_desktop.emit(self.item_data)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        is_light = (_drawer_theme(self) == "light")

        # 1. Selection / Hover squircle background
        back_rect = QRectF(3, 3, w - 6, h - 6)
        back_path = create_squircle_path(back_rect, 18)
        glow = max(0.0, min(1.0, float(self.glow_alpha)))

        if self.is_selected:
            painter.fillPath(back_path, QColor(37, 99, 235, 31) if is_light else QColor(59, 130, 246, 56))
            painter.strokePath(back_path, QPen(QColor(59, 130, 246, 95) if is_light else QColor(96, 165, 250, 115), 1.0))
        elif glow > 0.01 or self.is_pressed:
            if is_light:
                if self.is_pressed:
                    painter.fillPath(back_path, QColor(15, 23, 42, 22))
                    painter.strokePath(back_path, QPen(QColor(15, 23, 42, 32), 0.8))
                else:
                    painter.fillPath(back_path, QColor(255, 255, 255, int(150 * glow)))
                    painter.strokePath(back_path, QPen(QColor(15, 23, 42, int(20 * glow)), 0.8))
            else:
                if self.is_pressed:
                    painter.fillPath(back_path, QColor(255, 255, 255, 38))
                    painter.strokePath(back_path, QPen(QColor(255, 255, 255, 55), 0.8))
                else:
                    grad = QLinearGradient(0, 3, 0, h - 3)
                    grad.setColorAt(0.0, QColor(255, 255, 255, int(32 * glow)))
                    grad.setColorAt(1.0, QColor(255, 255, 255, int(14 * glow)))
                    painter.fillPath(back_path, grad)
                    painter.strokePath(back_path, QPen(QColor(255, 255, 255, int(42 * glow)), 0.8))

        # 2. 120Hz Sub-Pixel Floating-Point Icon Scaling (zero per-tick pix.scaled staircase!)
        disp_mode = getattr(self, "display_mode", "classic")
        if disp_mode == "large_icon":
            base_icon_sz = min(62.0, w - 6.0)
            cx = w / 2.0
            cy = h / 2.0
        elif disp_mode == "two_line":
            base_icon_sz = min(44.0, w - 6.0)
            cx = w / 2.0
            cy = 6.0 + base_icon_sz / 2.0
        else:  # classic
            base_icon_sz = min(52.0, w - 6.0)
            cx = w / 2.0
            cy = 8.0 + base_icon_sz / 2.0

        eff_sz = max(18.0, min(base_icon_sz * self.scale_factor, w - 4.0))
        sx = cx - eff_sz / 2.0
        sy = cy - eff_sz / 2.0

        # Ambient diffuse soft shadow under squircle icon (sub-pixel floating-point QRectF)
        base_alpha = int((14 if is_light else 24) + (10 if is_light else 16) * self.glow_alpha)
        for s_inset, s_dy, s_a in [(0.0, 3.0, int(base_alpha * 0.25)), (1.0, 2.0, int(base_alpha * 0.45)), (2.0, 1.0, base_alpha)]:
            s_rect = QRectF(sx + s_inset, sy + s_dy, eff_sz - s_inset * 2.0, eff_sz - s_inset * 2.0)
            s_path = create_squircle_path(s_rect, eff_sz * 0.22)
            painter.fillPath(s_path, QColor(15, 23, 42, s_a) if is_light else QColor(0, 0, 0, s_a))

        path_str = self.item_data.get("path", "")
        name = self.item_data.get("name", "")
        if self._cached_pixmap is None or self._cached_pixmap.isNull():
            custom_icon = self.item_data.get("custom_icon", "")
            self._cached_pixmap = get_app_icon_pixmap(path_str, name, target_size=128, custom_icon_path=custom_icon)
        pix = self._cached_pixmap
        draw_w = draw_h = base_icon_sz
        if pix and not pix.isNull():
            pw, ph = float(pix.width()), float(pix.height())
            if pw > 0 and ph > 0:
                ratio = min(base_icon_sz / pw, base_icon_sz / ph)
                draw_w = pw * ratio
                draw_h = ph * ratio
            painter.save()
            painter.translate(cx, cy)
            painter.scale(self.scale_factor, self.scale_factor)
            painter.drawPixmap(
                QRectF(-draw_w / 2.0, -draw_h / 2.0, draw_w, draw_h),
                pix,
                QRectF(pix.rect()),
            )
            painter.restore()

        # Smart Badges: Vector Two-Tone Admin Shield Badge
        if getattr(self, "show_admin_badge", True) and _is_admin_target(path_str):
            eff_w = draw_w * self.scale_factor
            eff_h = draw_h * self.scale_factor
            shield_sz = 14.0
            bx = cx + eff_w / 2.0 - shield_sz + 1.5
            by = cy + eff_h / 2.0 - shield_sz + 1.5
            _paint_admin_shield(painter, QRectF(bx, by, shield_sz, shield_sz))

        # 3. Typography Labeling
        if disp_mode == "large_icon":
            pass
        elif disp_mode == "two_line":
            painter.setFont(_ui_font(12, QFont.Weight.DemiBold))
            painter.setPen(_ink(self, _INK_LIGHT, QColor(255, 255, 255, 245)))
            fm1 = painter.fontMetrics()
            elided1 = fm1.elidedText(name, Qt.TextElideMode.ElideRight, int(w - 8))
            painter.drawText(QRectF(4, 54, w - 8, 18), Qt.AlignmentFlag.AlignCenter, elided1)

            painter.setFont(_ui_font(11, QFont.Weight.Normal))
            painter.setPen(_ink(self, _INK_LIGHT_SUB, QColor(148, 163, 184, 210)))
            sub_text = os.path.splitext(os.path.basename(path_str))[1].upper() if path_str else "APP"
            if not sub_text:
                sub_text = "APP"
            painter.drawText(QRectF(4, 74, w - 8, 16), Qt.AlignmentFlag.AlignCenter, sub_text)
        else:
            painter.setFont(_ui_font(12, QFont.Weight.Medium))
            fm = painter.fontMetrics()
            elided = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, int(w - 10))
            painter.setPen(_ink(self, _INK_LIGHT, QColor(255, 255, 255, 248)))
            painter.drawText(QRectF(4, 70, w - 8, 22), Qt.AlignmentFlag.AlignCenter, elided)

        painter.end()

    def contextMenuEvent(self, event):
        if not self.item_data or not self.item_data.get("path"):
            return

        menu = QMenu(self)
        menu.setStyleSheet(_get_menu_qss(_drawer_theme(self)))

        app_name = self.item_data.get("name", "应用")
        item_path = self.item_data.get("path", "")
        is_web = (
            self.item_data.get("type") == "web"
            or item_path.startswith("http://")
            or item_path.startswith("https://")
            or item_path.lower().endswith(".url")
        )
        is_file_search_result = (self.item_data.get("type") == "file")

        if is_web:
            act_open = menu.addAction(f"在浏览器中打开 {app_name}")
            act_open.triggered.connect(lambda: self.clicked.emit(self.item_data))

            act_copy_url = menu.addAction("复制网址")
            act_copy_url.triggered.connect(self.action_copy_path)

            act_edit_web = menu.addAction("编辑")
            act_edit_web.triggered.connect(self.action_edit_web_bookmark)

            menu.addSeparator()

            # Subgroup management menu
            self._build_subgroup_submenu(menu)

            act_custom_icon = menu.addAction("自定义图标")
            act_custom_icon.triggered.connect(self.action_custom_icon)

            menu.addSeparator()
            act_del = menu.addAction("移出视图")
            act_del.triggered.connect(lambda: self.item_removed.emit(self.item_data))

        elif is_file_search_result:
            act_open = menu.addAction(f"打开 {app_name}")
            act_open.triggered.connect(lambda: self.clicked.emit(self.item_data))

            act_location = menu.addAction("打开目录")
            act_location.triggered.connect(self.open_file_location)

            act_copy_path = menu.addAction("复制路径")
            act_copy_path.triggered.connect(self.action_copy_path)

            act_props = menu.addAction("属性")
            act_props.triggered.connect(self.action_show_properties)

        else:
            act_open = menu.addAction(f"打开 {app_name}")
            act_open.triggered.connect(lambda: self.clicked.emit(self.item_data))

            act_admin = menu.addAction("管理员运行")
            act_admin.triggered.connect(self.launch_as_admin)

            act_location = menu.addAction("打开目录")
            act_location.triggered.connect(self.open_file_location)

            act_copy_path = menu.addAction("复制路径")
            act_copy_path.triggered.connect(self.action_copy_path)

            act_props = menu.addAction("属性")
            act_props.triggered.connect(self.action_show_properties)

            menu.addSeparator()

            # Subgroup management menu
            self._build_subgroup_submenu(menu)

            act_rename = menu.addAction("重命名")
            act_rename.triggered.connect(self.action_rename_item)

            act_custom_icon = menu.addAction("自定义图标")
            act_custom_icon.triggered.connect(self.action_custom_icon)

            menu.addSeparator()

            act_to_desktop = menu.addAction("放回桌面")
            act_to_desktop.triggered.connect(lambda: self.request_move_to_desktop.emit(self.item_data))

            act_del = menu.addAction("移出视图")
            act_del.triggered.connect(lambda: self.item_removed.emit(self.item_data))

            act_recycle = menu.addAction("移入回收站")
            act_recycle.triggered.connect(lambda: self.request_recycle.emit(self.item_data))

        menu.exec(event.globalPos())
        event.accept()

    def _build_subgroup_submenu(self, menu: QMenu):
        sg_menu = menu.addMenu("子分组")
        sg_menu.setStyleSheet(_get_menu_qss(_drawer_theme(self)))
        curr_sg = (self.item_data.get("subgroup") or "").strip()

        act_none = sg_menu.addAction("全部")
        if not curr_sg:
            act_none.setText("全部 / 默认（无子分组）")
        act_none.triggered.connect(lambda: self.action_assign_subgroup(""))

        known_sgs = []
        win = self.window()
        if hasattr(win, "get_all_subgroups"):
            known_sgs = win.get_all_subgroups()

        if known_sgs:
            sg_menu.addSeparator()
            for s in known_sgs:
                if not s or s == "全部":
                    continue
                label = f"· {s}" if s == curr_sg else s
                act_s = sg_menu.addAction(label)
                act_s.triggered.connect(lambda checked=False, target_sg=s: self.action_assign_subgroup(target_sg))

        sg_menu.addSeparator()
        act_new_sg = sg_menu.addAction("新建子分组")
        act_new_sg.triggered.connect(self.action_create_and_assign_subgroup)

    def action_assign_subgroup(self, new_sg: str):
        orig = dict(self.item_data)
        updated = dict(self.item_data)
        updated["subgroup"] = new_sg
        self.item_data = updated
        self.item_changed.emit(orig, updated)

    def action_create_and_assign_subgroup(self):
        with _drawer_modal_guard(self):
            new_name, ok = ModernInputDialog.getText(self, "新建子分组", "请输入子分组名称:")
        if ok and new_name.strip():
            self.action_assign_subgroup(new_name.strip())

    def action_edit_web_bookmark(self):
        curr_url = self.item_data.get("path", "")
        curr_name = self.item_data.get("name", "")
        curr_sg = self.item_data.get("subgroup", "网页书签")
        known_sgs = []
        win = self.window()
        if hasattr(win, "get_all_subgroups"):
            known_sgs = win.get_all_subgroups()
        dlg = AddWebBookmarkDialog(
            None,
            initial_url=curr_url,
            initial_name=curr_name,
            initial_subgroup=curr_sg,
            existing_subgroups=known_sgs,
            theme=_drawer_theme(self),
        )
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        with _drawer_modal_guard(self):
            accepted = dlg.exec()
        if accepted:
            bm = dlg.get_bookmark_data()
            if bm:
                orig = dict(self.item_data)
                self.item_data = bm
                self.update_tooltip()
                self._cached_pixmap = None
                self.update()
                self.item_changed.emit(orig, bm)

    def launch_as_admin(self):
        path = self.item_data.get("path", "")
        args = self.item_data.get("args", "")
        if path:
            self.request_close.emit()
            run_as_admin(path, args)

    def open_file_location(self):
        path = self.item_data.get("path", "")
        target = resolve_shortcut_target(path)
        self.request_close.emit()
        if target and os.path.exists(target):
            subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(target)}"])
        elif os.path.exists(path):
            subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(path)}"])

    def action_rename_item(self):
        curr_name = self.item_data.get("name", "")
        with _drawer_modal_guard(self):
            new_name, ok = ModernInputDialog.getText(self, "重命名应用", "请输入应用显示名称:", text=curr_name)
        if ok and new_name.strip():
            orig = dict(self.item_data)
            updated = dict(self.item_data)
            updated["name"] = new_name.strip()
            self.item_data = updated
            self.update_tooltip()
            self.update()
            self.item_changed.emit(orig, updated)

    def action_custom_icon(self):
        curr_name = self.item_data.get("name", "应用")
        with _drawer_modal_guard(self):
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                f"为【{curr_name}】选择自定义图标",
                "",
                "图标与图片文件 (*.ico *.png *.jpg *.jpeg *.bmp *.exe *.dll);;所有文件 (*.*)"
            )
        if file_path:
            orig = dict(self.item_data)
            updated = dict(self.item_data)
            updated["custom_icon"] = file_path
            self.item_data = updated
            self._cached_pixmap = None
            self.update()
            self.item_changed.emit(orig, updated)

    def action_copy_path(self):
        path = resolve_actual_path(self.item_data.get("path", ""))
        if path:
            QApplication.clipboard().setText(path)

    def action_show_properties(self):
        path = self.item_data.get("path", "")
        if path:
            show_file_properties(path)

class SearchBannerWidget(QWidget):
    """
    Raycast / Spotlight style result banner widget:
    - 46px height with continuous curvature squircle boundary
    - High-contrast monochrome icon badge
    - Crisp primary typography with elision
    - 'Enter' keyboard action tag on the far right
    - Selection and hover tactile animations
    """
    clicked = Signal(dict)

    def __init__(self, item_data: Dict[str, Any], is_selected: bool = False, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.is_selected = is_selected
        self.is_hovered = False
        self.is_pressed = False
        self.glow_alpha: float = 0.0

        self.glow_anim = QVariantAnimation(self)
        self.glow_anim.valueChanged.connect(self._on_glow_anim_tick)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(400, 46)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 46)

    def _on_glow_anim_tick(self, val):
        self.glow_alpha = float(val)
        self.update()

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self.glow_anim.stop()
        self.glow_anim.setStartValue(self.glow_alpha)
        self.glow_anim.setEndValue(1.0)
        self.glow_anim.setDuration(150)
        self.glow_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self.glow_anim.stop()
        self.glow_anim.setStartValue(self.glow_alpha)
        self.glow_anim.setEndValue(0.0)
        self.glow_anim.setDuration(180)
        self.glow_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_pressed:
            self.is_pressed = False
            self.update()
            self.clicked.emit(self.item_data)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        rect = QRectF(2, 2, w - 4, h - 4)
        path = create_squircle_path(rect, 12)
        is_light = (_drawer_theme(self) == "light")
        glow = max(0.0, min(1.0, float(self.glow_alpha)))

        # 1. Refined Raycast-style selection & hover surface
        if self.is_selected:
            fill = QColor(37, 99, 235, 31) if is_light else QColor(59, 130, 246, 56)
            stroke = QColor(59, 130, 246, 95) if is_light else QColor(96, 165, 250, 115)
            painter.fillPath(path, fill)
            painter.strokePath(path, QPen(stroke, 1.0))
        elif glow > 0.01 or self.is_pressed:
            if is_light:
                alpha = int(14 * glow) if not self.is_pressed else 22
                stroke_alpha = int(22 * glow) if not self.is_pressed else 34
                painter.fillPath(path, QColor(15, 23, 42, alpha))
                painter.strokePath(path, QPen(QColor(15, 23, 42, stroke_alpha), 1.0))
            else:
                alpha = int(24 * glow) if not self.is_pressed else 36
                stroke_alpha = int(36 * glow) if not self.is_pressed else 54
                painter.fillPath(path, QColor(255, 255, 255, alpha))
                painter.strokePath(path, QPen(QColor(255, 255, 255, stroke_alpha), 1.0))
        else:
            if is_light:
                painter.fillPath(path, QColor(15, 23, 42, 8))
                painter.strokePath(path, QPen(QColor(15, 23, 42, 16), 0.8))
            else:
                painter.fillPath(path, QColor(255, 255, 255, 10))
                painter.strokePath(path, QPen(QColor(255, 255, 255, 20), 0.8))

        # 2. Icon / Badge on the left
        icon_str = self.item_data.get("icon", "")
        if not icon_str:
            pen = QPen(QColor(37, 99, 235, 210) if is_light else QColor(96, 165, 250, 225), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            glass_r = 4.6
            glass_cx = 25.5
            glass_cy = h / 2.0 - 1.2
            painter.drawEllipse(QRectF(glass_cx - glass_r, glass_cy - glass_r, glass_r * 2, glass_r * 2))
            p_start = QPointF(glass_cx + glass_r * 0.707, glass_cy + glass_r * 0.707)
            p_end = QPointF(p_start.x() + 4.4, p_start.y() + 4.4)
            painter.drawLine(p_start, p_end)
        else:
            painter.setFont(_ui_font(14, QFont.Weight.DemiBold))
            painter.setPen(_ink(self, _INK_LIGHT, QColor(248, 250, 252)))
            painter.drawText(QRectF(14, 0, 28, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, icon_str)

        # 3. Crisp keycap pill on far right
        badge_w = 56
        badge_h = 22
        badge_x = w - badge_w - 12
        badge_y = (h - badge_h) / 2.0
        badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)
        badge_path = create_squircle_path(badge_rect, 6)

        if is_light:
            badge_bg = QColor(37, 99, 235, 24) if self.is_selected else QColor(15, 23, 42, 14)
            badge_border = QColor(59, 130, 246, 65) if self.is_selected else QColor(15, 23, 42, 24)
            badge_fg = QColor(30, 64, 175, 235) if self.is_selected else QColor(71, 85, 105, 220)
        else:
            badge_bg = QColor(255, 255, 255, 34) if self.is_selected else QColor(255, 255, 255, 26)
            badge_border = QColor(255, 255, 255, 50) if self.is_selected else QColor(255, 255, 255, 34)
            badge_fg = QColor(255, 255, 255, 235) if self.is_selected else QColor(203, 213, 225, 210)
        painter.fillPath(badge_path, badge_bg)
        painter.strokePath(badge_path, QPen(badge_border, 0.8))

        painter.setFont(_ui_font(11, QFont.Weight.Medium))
        painter.setPen(badge_fg)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "↵ Enter")

        # 4. Main title text
        title_text = self.item_data.get("name", "")
        if icon_str and title_text.startswith(icon_str):
            title_text = title_text[len(icon_str):].lstrip()

        painter.setFont(_ui_font(14, QFont.Weight.Medium))
        painter.setPen(_ink(self, _INK_LIGHT,
                            QColor(241, 245, 249, 240) if not self.is_selected
                            else QColor(255, 255, 255, 255)))

        fm = painter.fontMetrics()
        avail_w = max(20, int(badge_x - 48 - 10))
        elided = fm.elidedText(title_text, Qt.TextElideMode.ElideRight, avail_w)
        painter.drawText(QRectF(48, 0, avail_w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

        painter.end()


class SearchFileBannerWidget(QWidget):
    """
    Raycast / Spotlight 风格全盘文件检索结果行:
    - 52px 紧凑标准高度，连续曲率圆角背景
    - Windows Shell 高清原生文件/目录图标 (32x32)
    - 第一行：文件/文件夹主名称
    - 第二行：文件所在物理路径
    - 右侧：文件后缀类别药丸标签
    """
    clicked = Signal(dict)
    open_folder_requested = Signal(dict)

    def __init__(self, item_data: Dict[str, Any], is_selected: bool = False, parent=None):
        super().__init__(parent)
        self.item_data = item_data
        self.is_selected = is_selected
        self.is_hovered = False
        self.is_pressed = False
        self.glow_alpha: float = 0.0

        self.glow_anim = QVariantAnimation(self)
        self.glow_anim.valueChanged.connect(self._on_glow_anim_tick)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_tooltip()

    def sizeHint(self) -> QSize:
        return QSize(400, 52)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 52)

    def update_tooltip(self):
        name = (self.item_data.get("name") or "").strip()
        path = (self.item_data.get("path") or "").strip()
        if name and path and path != name:
            self.setToolTip(f"{name}\n{path}")
        else:
            self.setToolTip(name or path)

    def _on_glow_anim_tick(self, val):
        self.glow_alpha = float(val)
        self.update()

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self.glow_anim.stop()
        self.glow_anim.setStartValue(self.glow_alpha)
        self.glow_anim.setEndValue(1.0)
        self.glow_anim.setDuration(150)
        self.glow_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self.glow_anim.stop()
        self.glow_anim.setStartValue(self.glow_alpha)
        self.glow_anim.setEndValue(0.0)
        self.glow_anim.setDuration(180)
        self.glow_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_pressed:
            self.is_pressed = False
            self.update()
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.open_folder_requested.emit(self.item_data)
            else:
                self.clicked.emit(self.item_data)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())
        rect = QRectF(2, 2, w - 4, h - 4)
        path = create_squircle_path(rect, 12)
        is_light = (_drawer_theme(self) == "light")
        glow = max(0.0, min(1.0, float(self.glow_alpha)))

        # 1. Refined Raycast-style selection & hover surface
        if self.is_selected:
            fill = QColor(37, 99, 235, 31) if is_light else QColor(59, 130, 246, 56)
            stroke = QColor(59, 130, 246, 95) if is_light else QColor(96, 165, 250, 115)
            painter.fillPath(path, fill)
            painter.strokePath(path, QPen(stroke, 1.0))
        elif glow > 0.01 or self.is_pressed:
            if is_light:
                alpha = int(14 * glow) if not self.is_pressed else 22
                stroke_alpha = int(22 * glow) if not self.is_pressed else 34
                painter.fillPath(path, QColor(15, 23, 42, alpha))
                painter.strokePath(path, QPen(QColor(15, 23, 42, stroke_alpha), 1.0))
            else:
                alpha = int(24 * glow) if not self.is_pressed else 36
                stroke_alpha = int(36 * glow) if not self.is_pressed else 54
                painter.fillPath(path, QColor(255, 255, 255, alpha))
                painter.strokePath(path, QPen(QColor(255, 255, 255, stroke_alpha), 1.0))
        else:
            if is_light:
                painter.fillPath(path, QColor(15, 23, 42, 7))
                painter.strokePath(path, QPen(QColor(15, 23, 42, 15), 0.8))
            else:
                painter.fillPath(path, QColor(255, 255, 255, 9))
                painter.strokePath(path, QPen(QColor(255, 255, 255, 18), 0.8))

        # 2. File / Folder native icon
        file_path = self.item_data.get("path", "")
        file_name = self.item_data.get("name", "")
        is_folder = self.item_data.get("is_folder", False)

        icon_pix = get_app_icon_pixmap(file_path, file_name, target_size=64)
        if icon_pix and not icon_pix.isNull():
            painter.drawPixmap(12, 10, 32, 32, icon_pix)
        else:
            if is_folder:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(234, 179, 8, 220))
                painter.drawRoundedRect(QRectF(14, 12, 28, 26), 4, 4)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(148, 163, 184, 220))
                painter.drawRoundedRect(QRectF(16, 12, 24, 28), 4, 4)

        # 3. Extension Badge & optional Enter keycap on the right
        ext_text = "文件夹" if is_folder else (os.path.splitext(file_name)[1].upper() or "文件")
        if len(ext_text) > 7:
            ext_text = ext_text[:7]

        font_badge = _ui_font(11, QFont.Weight.Medium)
        fm_badge = QFontMetrics(font_badge)
        right_cursor = w - 12.0

        if self.is_selected:
            kw, kh = 26.0, 22.0
            kx = right_cursor - kw
            ky = (h - kh) / 2.0
            k_rect = QRectF(kx, ky, kw, kh)
            k_path = create_squircle_path(k_rect, 6)
            if is_light:
                painter.fillPath(k_path, QColor(37, 99, 235, 24))
                painter.strokePath(k_path, QPen(QColor(59, 130, 246, 65), 0.8))
                painter.setPen(QColor(30, 64, 175, 235))
            else:
                painter.fillPath(k_path, QColor(255, 255, 255, 26))
                painter.strokePath(k_path, QPen(QColor(255, 255, 255, 42), 0.8))
                painter.setPen(QColor(248, 250, 252, 235))
            painter.setFont(font_badge)
            painter.drawText(k_rect, Qt.AlignmentFlag.AlignCenter, "↵")
            right_cursor = kx - 6.0

        bw = max(44.0, float(fm_badge.horizontalAdvance(ext_text) + 14))
        bh = 22.0
        bx = max(54.0, right_cursor - bw)
        by = (h - bh) / 2.0
        badge_rect = QRectF(bx, by, bw, bh)
        b_path = create_squircle_path(badge_rect, 6)

        if is_folder:
            if is_light:
                bg_badge = QColor(217, 119, 6, 26)
                border_badge = QColor(217, 119, 6, 70)
                text_badge = QColor(180, 83, 9, 240)
            else:
                bg_badge = QColor(234, 179, 8, 36) if self.is_selected else QColor(234, 179, 8, 24)
                border_badge = QColor(234, 179, 8, 95)
                text_badge = QColor(253, 224, 71, 230)
        else:
            if is_light:
                bg_badge = QColor(37, 99, 235, 22) if self.is_selected else QColor(15, 23, 42, 12)
                border_badge = QColor(59, 130, 246, 60) if self.is_selected else QColor(15, 23, 42, 22)
                text_badge = QColor(30, 64, 175, 235) if self.is_selected else QColor(71, 85, 105, 225)
            else:
                bg_badge = QColor(59, 130, 246, 38) if self.is_selected else QColor(255, 255, 255, 26)
                border_badge = QColor(96, 165, 250, 95) if self.is_selected else QColor(255, 255, 255, 32)
                text_badge = QColor(191, 219, 254, 235) if self.is_selected else QColor(203, 213, 225, 210)

        painter.fillPath(b_path, bg_badge)
        painter.strokePath(b_path, QPen(border_badge, 0.8))
        painter.setFont(font_badge)
        painter.setPen(text_badge)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, ext_text)

        # 4. Text (Two rows: name and directory)
        avail_w = max(20, int(bx - 52 - 8))

        # Row 1: File/Folder Name
        painter.setFont(_ui_font(14, QFont.Weight.Medium))
        painter.setPen(_ink(self, _INK_LIGHT,
                            QColor(241, 245, 249, 245) if not self.is_selected
                            else QColor(255, 255, 255, 255)))
        fm_name = painter.fontMetrics()
        elided_name = fm_name.elidedText(file_name, Qt.TextElideMode.ElideRight, avail_w)
        painter.drawText(QRectF(52, 7, avail_w, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_name)

        # Row 2: Directory Path
        dir_path = self.item_data.get("dir", "")
        if not dir_path and file_path:
            dir_path = os.path.dirname(file_path)
        painter.setFont(_ui_font(12, QFont.Weight.Normal))
        painter.setPen(_ink(self, _INK_LIGHT_SUB,
                            QColor(148, 163, 184, 205) if not self.is_selected
                            else QColor(191, 219, 254, 225)))
        fm_path = painter.fontMetrics()
        elided_path = fm_path.elidedText(dir_path, Qt.TextElideMode.ElideMiddle, avail_w)
        painter.drawText(QRectF(52, 27, avail_w, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_path)

        painter.end()

    def contextMenuEvent(self, event):
        path = self.item_data.get("path", "")
        if not path:
            return

        menu = QMenu(self)
        menu.setStyleSheet(_get_menu_qss(_drawer_theme(self)))

        fn = self.item_data.get("name", "文件")
        is_folder = self.item_data.get("is_folder", False)

        act_open = menu.addAction(f"打开 {'文件夹' if is_folder else fn} (Enter)")
        act_open.triggered.connect(lambda: self.clicked.emit(self.item_data))

        ext = os.path.splitext(path)[1].lower()
        if ext in (".exe", ".bat", ".cmd", ".ps1"):
            act_admin = menu.addAction("管理员运行")
            act_admin.triggered.connect(lambda: run_as_admin(path, ""))

        act_loc = menu.addAction("打开目录")
        act_loc.triggered.connect(lambda: self.open_folder_requested.emit(self.item_data))

        menu.addSeparator()

        act_copy = menu.addAction("复制路径")
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))

        act_props = menu.addAction("属性")
        act_props.triggered.connect(lambda: show_file_properties(path))

        # 核心功能：搜索结果直接加入当前大文件夹
        menu.addSeparator()
        overlay = self.window()
        folder_title = getattr(overlay, "folder_name", "当前大文件夹")
        act_add_to_folder = menu.addAction(f"添加到【{folder_title}】文件夹")
        if hasattr(overlay, "action_add_file_to_folder"):
            act_add_to_folder.triggered.connect(lambda: overlay.action_add_file_to_folder(self.item_data))

        menu.exec(event.globalPos())
        event.accept()


class MorphSheetWidget(QWidget):
    """
    Physical animated sheet container that morphs smoothly from desktop card geometry to expanded view.
    Renders with:
    - Continuous curvature squircle path
    - Multi-layer ambient radial bloom & liquid acrylic glass
    - 1px Specular Rim Light on upper arc
    - Large diffuse drop shadow
    """
    def __init__(self, theme: str = "dark", radius: float = 30.0, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.radius = radius
        self.opacity = 0.98
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w < 10 or h < 10:
            painter.end()
            return

        pad = 2.0
        rect = QRectF(pad, pad, w - pad * 2, h - pad * 2)
        path = create_squircle_path(rect, self.radius)
        inner_rect = rect.adjusted(1.0, 1.0, -1.0, -1.0)
        inner_path = create_squircle_path(inner_rect, max(1.0, self.radius - 1.0))

        op = max(0.05, min(1.0, float(self.opacity)))

        # 1. Refined frosted glass sheet fill (dark / oled / light)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        if self.theme == "light":
            grad.setColorAt(0.0, QColor(250, 251, 254, int(246 * op)))
            grad.setColorAt(0.45, QColor(246, 248, 252, int(244 * op)))
            grad.setColorAt(1.0, QColor(241, 245, 251, int(244 * op)))
            painter.fillPath(path, grad)

            ambient = QRadialGradient(w * 0.5, 0.0, max(w, h) * 0.72)
            ambient.setColorAt(0.0, QColor(255, 255, 255, int(90 * op)))
            ambient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, ambient)

            # Dual specular rim: crisp outer hairline + soft inner white highlight
            painter.strokePath(path, QPen(QColor(15, 23, 42, int(22 * op)), 1.0))
            inner_rim = QLinearGradient(0, inner_rect.top(), 0, inner_rect.bottom())
            inner_rim.setColorAt(0.0, QColor(255, 255, 255, int(210 * op)))
            inner_rim.setColorAt(1.0, QColor(255, 255, 255, int(75 * op)))
            painter.strokePath(inner_path, QPen(inner_rim, 1.0))
        elif self.theme == "oled":
            grad.setColorAt(0.0, QColor(15, 16, 21, int(248 * op)))
            grad.setColorAt(0.5, QColor(10, 11, 15, int(250 * op)))
            grad.setColorAt(1.0, QColor(7, 8, 11, int(252 * op)))
            painter.fillPath(path, grad)

            ambient = QRadialGradient(w * 0.5, 0.0, max(w, h) * 0.65)
            ambient.setColorAt(0.0, QColor(255, 255, 255, int(12 * op)))
            ambient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, ambient)

            painter.strokePath(path, QPen(QColor(255, 255, 255, int(28 * op)), 1.0))
            inner_rim = QLinearGradient(0, inner_rect.top(), 0, inner_rect.bottom())
            inner_rim.setColorAt(0.0, QColor(255, 255, 255, int(28 * op)))
            inner_rim.setColorAt(0.45, QColor(255, 255, 255, 0))
            inner_rim.setColorAt(1.0, QColor(0, 0, 0, int(45 * op)))
            painter.strokePath(inner_path, QPen(inner_rim, 1.0))
        else:
            grad.setColorAt(0.0, QColor(22, 24, 31, int(238 * op)))
            grad.setColorAt(0.45, QColor(18, 20, 26, int(240 * op)))
            grad.setColorAt(1.0, QColor(15, 17, 22, int(242 * op)))
            painter.fillPath(path, grad)

            ambient = QRadialGradient(w * 0.5, 0.0, max(w, h) * 0.68)
            ambient.setColorAt(0.0, QColor(255, 255, 255, int(14 * op)))
            ambient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, ambient)

            # Dual specular rim: 1px rgba(255,255,255,34) outer rim + subtle inner top sheen
            painter.strokePath(path, QPen(QColor(255, 255, 255, int(34 * op)), 1.0))
            inner_rim = QLinearGradient(0, inner_rect.top(), 0, inner_rect.bottom())
            inner_rim.setColorAt(0.0, QColor(255, 255, 255, int(30 * op)))
            inner_rim.setColorAt(0.45, QColor(255, 255, 255, 0))
            inner_rim.setColorAt(1.0, QColor(0, 0, 0, int(42 * op)))
            painter.strokePath(inner_path, QPen(inner_rim, 1.0))

        painter.end()


class SearchLineEdit(QLineEdit):
    """
    Search line edit tailored for instant Spotlight/Raycast-like launcher interaction.
    - Pressing Escape clears query if non-empty, or collapses overlay if empty.
    - Arrow keys and Enter/Return forward directly to the overlay grid navigation.
    """
    def __init__(self, overlay, parent=None):
        super().__init__(parent)
        self.overlay = overlay

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.text():
                self.clear()
                event.accept()
                return
            else:
                self.overlay.collapse_and_close()
                event.accept()
                return
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.overlay.keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)


class VectorIconButton(QPushButton):
    """
    Subpixel vector-rendered icon button with silky smooth antialiasing,
    continuous squircle hover capsule, and zero ugly emoji/font glyph artifacts.
    """
    def __init__(self, icon_type: str = "close", size: int = 32, parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.is_hovered = False
        self.is_pressed = False
        self.is_active = False
        self.hover_alpha = 0.0

        self.hover_anim = QVariantAnimation(self)
        self.hover_anim.valueChanged.connect(self._on_hover_tick)

    def _on_hover_tick(self, val):
        self.hover_alpha = float(val)
        self.update()

    def enterEvent(self, event):
        self.is_hovered = True
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self.hover_alpha)
        self.hover_anim.setEndValue(1.0)
        self.hover_anim.setDuration(150)
        self.hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self.hover_anim.stop()
        self.hover_anim.setStartValue(self.hover_alpha)
        self.hover_anim.setEndValue(0.0)
        self.hover_anim.setDuration(180)
        self.hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hover_anim.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_pressed:
            self.is_pressed = False
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w = float(self.width())
        h = float(self.height())
        pad = 1.0
        rect = QRectF(pad, pad, w - pad * 2, h - pad * 2)
        capsule_path = create_squircle_path(rect, (w - pad * 2) / 2.0)
        is_light = (_drawer_theme(self) == "light")
        hover_t = max(0.0, min(1.0, float(self.hover_alpha)))

        # 1. Restrained frosted capsule highlight
        close_hot = (self.icon_type == "close" and (self.is_hovered or self.is_pressed))
        active_on = bool(getattr(self, "is_active", False))
        if close_hot:
            red_alpha = int(195 + 30 * hover_t) if self.is_pressed else int(155 * max(0.35, hover_t))
            painter.fillPath(capsule_path, QColor(239, 68, 68, red_alpha))
            painter.strokePath(capsule_path, QPen(QColor(248, 113, 113, int(140 * max(0.35, hover_t))), 0.9))
        elif active_on:
            fill = QColor(37, 99, 235, int(34 + 18 * hover_t)) if is_light else QColor(59, 130, 246, int(60 + 24 * hover_t))
            stroke = QColor(37, 99, 235, 85) if is_light else QColor(96, 165, 250, 115)
            painter.fillPath(capsule_path, fill)
            painter.strokePath(capsule_path, QPen(stroke, 0.9))
        elif is_light:
            base_alpha = int(10 + 16 * hover_t) if not self.is_pressed else 30
            border_alpha = int(10 + 16 * hover_t) if not self.is_pressed else 32
            painter.fillPath(capsule_path, QColor(15, 23, 42, base_alpha))
            if border_alpha > 12:
                painter.strokePath(capsule_path, QPen(QColor(15, 23, 42, border_alpha), 0.8))
        else:
            base_alpha = int(12 + 24 * hover_t) if not self.is_pressed else 48
            border_alpha = int(14 + 26 * hover_t) if not self.is_pressed else 58
            painter.fillPath(capsule_path, QColor(255, 255, 255, base_alpha))
            if border_alpha > 16:
                painter.strokePath(capsule_path, QPen(QColor(255, 255, 255, border_alpha), 0.8))

        # 2. Subpixel Vector Glyph
        cx, cy = w / 2.0, h / 2.0
        if close_hot:
            glyph_fg = QColor(255, 255, 255, 248)
        elif active_on:
            glyph_fg = QColor(29, 78, 216, 245) if is_light else QColor(147, 197, 253, 250)
        else:
            glyph_fg = _ink(self, QColor(51, 65, 85, 235), QColor(226, 232, 240, 235))
        if self.icon_type == "close":
            pen = QPen(glyph_fg, 1.55)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            r = 4.2
            painter.drawLine(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
            painter.drawLine(QPointF(cx - r, cy + r), QPointF(cx + r, cy - r))

        elif self.icon_type == "search":
            pen = QPen(glyph_fg, 1.55)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            glass_r = 4.4
            glass_cx = cx - 1.4
            glass_cy = cy - 1.4
            painter.drawEllipse(QRectF(glass_cx - glass_r, glass_cy - glass_r, glass_r * 2, glass_r * 2))

            stem_len = 4.2
            p_start = QPointF(glass_cx + glass_r * 0.707, glass_cy + glass_r * 0.707)
            p_end = QPointF(p_start.x() + stem_len, p_start.y() + stem_len)
            painter.drawLine(p_start, p_end)

        elif self.icon_type == "tidy":
            pen = QPen(glyph_fg, 1.55)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            box_r = QRectF(cx - 5.8, cy - 4.8, 11.6, 9.6)
            painter.drawRoundedRect(box_r, 2.5, 2.5)
            painter.drawLine(QPointF(cx - 3.0, cy), QPointF(cx + 3.0, cy))

        elif self.icon_type == "settings":
            pen = QPen(glyph_fg, 1.45)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            y1, y2, y3 = cy - 4.4, cy, cy + 4.4
            painter.drawLine(QPointF(cx - 5.8, y1), QPointF(cx + 5.8, y1))
            painter.drawLine(QPointF(cx - 5.8, y2), QPointF(cx + 5.8, y2))
            painter.drawLine(QPointF(cx - 5.8, y3), QPointF(cx + 5.8, y3))
            painter.setBrush(glyph_fg)
            painter.drawEllipse(QRectF(cx - 3.8, y1 - 1.7, 3.4, 3.4))
            painter.drawEllipse(QRectF(cx + 0.8, y2 - 1.7, 3.4, 3.4))
            painter.drawEllipse(QRectF(cx - 2.0, y3 - 1.7, 3.4, 3.4))

        elif self.icon_type == "add":
            pen = QPen(glyph_fg, 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx - 4.5, cy), QPointF(cx + 4.5, cy))
            painter.drawLine(QPointF(cx, cy - 4.5), QPointF(cx, cy + 4.5))

        painter.end()


class SubgroupPillButton(QPushButton):
    """
    Subgroup tab pill button:
    - Renders smooth segmented squircle capsule (height 28px)
    - Active vs inactive states with subtle frosted pill fill
    - Context menu on right click for custom tabs (Rename / Delete)
    """
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, subgroup_name: str, count: int = 0, is_active: bool = False, parent=None):
        super().__init__(parent)
        self.subgroup_name = subgroup_name
        self.count = count
        self.is_active = is_active
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_appearance()

    def set_active(self, active: bool):
        self.is_active = active
        self._update_appearance()

    def _update_appearance(self):
        text = f"{self.subgroup_name} ({self.count})" if self.count >= 0 else self.subgroup_name
        self.setText(text)
        self.setFont(_ui_font(12, QFont.Weight.DemiBold if self.is_active else QFont.Weight.Medium))
        is_light = (_drawer_theme(self) == "light")
        if is_light:
            if self.is_active:
                self.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(59, 130, 246, 0.14);
                        color: #1d4ed8;
                        border: 1px solid rgba(59, 130, 246, 0.28);
                        border-radius: 13px;
                        padding: 3px 12px;
                        font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                        font-size: 12px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: rgba(59, 130, 246, 0.20);
                        border: 1px solid rgba(59, 130, 246, 0.38);
                    }
                """)
            else:
                self.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(15, 23, 42, 0.045);
                        color: #475569;
                        border: 1px solid transparent;
                        border-radius: 13px;
                        padding: 3px 12px;
                        font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                        font-size: 12px;
                        font-weight: 500;
                    }
                    QPushButton:hover {
                        background-color: rgba(15, 23, 42, 0.085);
                        color: #0f172a;
                        border: 1px solid rgba(15, 23, 42, 0.08);
                    }
                """)
        else:
            if self.is_active:
                self.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(59, 130, 246, 0.24);
                        color: #f8fafc;
                        border: 1px solid rgba(96, 165, 250, 0.42);
                        border-radius: 13px;
                        padding: 3px 12px;
                        font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                        font-size: 12px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: rgba(59, 130, 246, 0.32);
                        border: 1px solid rgba(96, 165, 250, 0.55);
                    }
                """)
            else:
                self.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.05);
                        color: rgba(203, 213, 225, 0.82);
                        border: 1px solid transparent;
                        border-radius: 13px;
                        padding: 3px 12px;
                        font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                        font-size: 12px;
                        font-weight: 500;
                    }
                    QPushButton:hover {
                        background-color: rgba(255, 255, 255, 0.10);
                        color: #f8fafc;
                        border: 1px solid rgba(255, 255, 255, 0.10);
                    }
                """)

    def contextMenuEvent(self, event):
        if self.subgroup_name in ("全部", "All"):
            return
        menu = QMenu(self)
        menu.setStyleSheet(_get_menu_qss(_drawer_theme(self)))
        act_rename = menu.addAction(f"重命名【{self.subgroup_name}】...")
        act_rename.triggered.connect(lambda: self.rename_requested.emit(self.subgroup_name))
        act_del = menu.addAction(f"删除子分组【{self.subgroup_name}】")
        act_del.triggered.connect(lambda: self.delete_requested.emit(self.subgroup_name))
        menu.exec(event.globalPos())
        event.accept()


class FolderSubgroupTabBar(QWidget):
    """
    Horizontal bar of subgroup pill tabs.
    - Tab 0 is always '全部 (N)'
    - Followed by user-created or detected tabs: '常用套件', '网页书签', etc.
    - '+' button at the end to quickly create a new subgroup
    - Emits tab_selected(str)
    """
    tab_selected = Signal(str)
    request_add_subgroup = Signal()
    request_rename_subgroup = Signal(str)
    request_delete_subgroup = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_subgroup = "全部"
        self.pill_buttons: Dict[str, SubgroupPillButton] = {}
        self.setFixedHeight(34)
        self.setStyleSheet("background: transparent;")

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 2, 0, 2)
        self.main_layout.setSpacing(6)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Container layout for pills
        self.pills_container = QWidget(self)
        self.pills_container.setStyleSheet("background: transparent;")
        self.pills_layout = QHBoxLayout(self.pills_container)
        self.pills_layout.setContentsMargins(0, 0, 0, 0)
        self.pills_layout.setSpacing(6)
        self.main_layout.addWidget(self.pills_container)

        # '+' button to add a new subgroup
        self.btn_add = VectorIconButton("add", size=26, parent=self)
        self.btn_add.setToolTip("新建子分组")
        self.btn_add.clicked.connect(self.request_add_subgroup.emit)
        self.main_layout.addWidget(self.btn_add)

        self.main_layout.addStretch()

    def set_tabs(self, subgroup_counts: Dict[str, int], total_count: int, active_subgroup: str = "全部"):
        self.active_subgroup = active_subgroup
        # Clear existing pills
        while self.pills_layout.count():
            item = self.pills_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        self.pill_buttons.clear()

        # 1. '全部' Tab
        all_btn = SubgroupPillButton("全部", count=total_count, is_active=(self.active_subgroup == "全部"), parent=self.pills_container)
        all_btn.clicked.connect(lambda: self._on_pill_clicked("全部"))
        self.pills_layout.addWidget(all_btn)
        self.pill_buttons["全部"] = all_btn

        # 2. Other Subgroups
        for sg_name, count in subgroup_counts.items():
            if not sg_name or sg_name == "全部":
                continue
            is_act = (self.active_subgroup == sg_name)
            pill = SubgroupPillButton(sg_name, count=count, is_active=is_act, parent=self.pills_container)
            pill.clicked.connect(lambda checked=False, name=sg_name: self._on_pill_clicked(name))
            pill.rename_requested.connect(self.request_rename_subgroup.emit)
            pill.delete_requested.connect(self.request_delete_subgroup.emit)
            self.pills_layout.addWidget(pill)
            self.pill_buttons[sg_name] = pill

    def _on_pill_clicked(self, name: str):
        if self.active_subgroup == name:
            return
        self.active_subgroup = name
        for sg_name, btn in self.pill_buttons.items():
            btn.set_active(sg_name == name)
        self.tab_selected.emit(name)


class HeroExpandedFolderOverlay(QWidget):
    """
    Authentic in-place Hero morphing expanded folder view:
    - Screen-spanning transparent canvas with background dimming scrim.
    - Smooth fluid expansion from the desktop folder card's exact physical coordinates (start_geo).
    - QVariantAnimation with OutBack easing for authentic spring bounce.
    - Reversible collapse animation back into origin on dismiss.
    - Dismissable via Esc, clicking on the scrim outside the card, clicking close button, or launching an app.
    - Inline editable folder title, live pinyin search, spacious 4-column squircle grid, system-level context menus.
    """
    folder_renamed = Signal(str)
    item_changed = Signal(int, dict)
    item_removed = Signal(int)
    request_move_to_desktop = Signal(str)
    request_recycle = Signal(str)
    files_dropped = Signal(list, int)
    web_items_dropped = Signal(list, int)
    status_toast = Signal(str, bool)
    request_tidy_desktop = Signal()
    request_set_open_mode = Signal(str)
    request_open_settings = Signal(int)
    closed = Signal()

    def __init__(
        self,
        folder_name: str,
        items: List[Dict[str, Any]],
        theme: str = "dark",
        start_radius: float = 26.0,
        start_geo: Optional[QRect] = None,
        target_geo: Optional[QRect] = None,
        screen_geo: Optional[QRect] = None,
        open_mode: str = "double",
        open_app_mode: str = "double",
        config_manager: Optional[Any] = None,
        parent=None
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.folder_name = folder_name
        self.open_mode = open_mode
        self.open_app_mode = open_app_mode
        real_items = [it for it in items if it and (it.get("path") or it.get("name"))]
        self.items = real_items
        self.active_subgroup = "全部"
        self.theme = theme
        self.start_radius = start_radius
        self.current_radius = start_radius
        self.selected_item_index = 0
        self.keyboard_nav_active = False
        self.special_items: List[Dict[str, Any]] = []
        self.current_display_items: List[Dict[str, Any]] = list(real_items)
        self.action_items: List[Dict[str, Any]] = list(real_items)
        self.item_widgets: List[QWidget] = []

        self.morph_progress: float = 0.0
        self.is_closing: bool = False
        self._modal_guard_count: int = 0
        self.setAcceptDrops(True)

        # Set screen viewport geometry
        if screen_geo is None:
            primary = QApplication.primaryScreen()
            screen_geo = primary.geometry() if primary else QRect(0, 0, 1920, 1080)
        self.screen_geo = screen_geo

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._target_screen_obj = QApplication.screenAt(self.screen_geo.center())
        self.setGeometry(self.screen_geo)

        # Coordinate transformation relative to overlay screen viewport
        if start_geo is None:
            cw = 240
            ch = 240
            cx = (self.screen_geo.width() - cw) // 2
            cy = (self.screen_geo.height() - ch) // 2
            self.local_start_rect = QRect(cx, cy, cw, ch)
        else:
            self.local_start_rect = QRect(
                start_geo.x() - self.screen_geo.x(),
                start_geo.y() - self.screen_geo.y(),
                start_geo.width(),
                start_geo.height()
            )

        cur_screen = QApplication.screenAt(self.screen_geo.center())
        avail = cur_screen.availableGeometry() if cur_screen else self.screen_geo
        s_top = max(avail.top() - self.screen_geo.top(), 32)
        s_bottom = min(avail.bottom() - self.screen_geo.top(), self.screen_geo.height() - 120)
        s_left = max(avail.left() - self.screen_geo.left(), 20)
        s_right = min(avail.right() - self.screen_geo.left(), self.screen_geo.width() - 20)
        s_w = max(300, s_right - s_left + 1)
        s_h = max(300, s_bottom - s_top + 1)
        n_items = len(self.items)

        # Minimum safe dimension guarantees 0 clipping across any caller
        # Item: 92px. Spacing: 10px. Layout margins: 24+36+8=68px. Scroll reserve: 14px.
        if n_items <= 2:
            min_safe_tw, min_safe_th = 336, 236
        elif n_items == 3:
            min_safe_tw, min_safe_th = 416, 236
        elif n_items <= 4:
            min_safe_tw, min_safe_th = 504, 240
        elif n_items <= 8:
            min_safe_tw, min_safe_th = 504, 356
        elif n_items <= 12:
            min_safe_tw, min_safe_th = 504, 476
        else:
            min_safe_tw, min_safe_th = 516, 560

        if target_geo is None:
            tw = min(min_safe_tw, s_w)
            th = min(min_safe_th, s_h)
            start_cx = self.local_start_rect.center().x()
            start_cy = self.local_start_rect.center().y()
            desired_tx = start_cx - tw // 2
            desired_ty = start_cy - th // 2
            tx = max(s_left, min(desired_tx, s_right - tw))
            ty = max(s_top, min(desired_ty, s_bottom - th))
            self.local_target_rect = QRect(tx, ty, tw, th)
        else:
            # Enforce safe minimum bounds so 4 columns are NEVER clipped under any caller
            orig_w = target_geo.width()
            orig_h = target_geo.height()
            tw = min(max(orig_w, min_safe_tw), s_w)
            th = min(max(orig_h, min_safe_th), s_h)

            orig_lx = target_geo.x() - self.screen_geo.x()
            orig_ly = target_geo.y() - self.screen_geo.y()
            if tw != orig_w or th != orig_h:
                center_x = orig_lx + orig_w // 2
                center_y = orig_ly + orig_h // 2
                tx = max(s_left, min(center_x - tw // 2, s_right - tw))
                ty = max(s_top, min(center_y - th // 2, s_bottom - th))
            else:
                tx = orig_lx
                ty = orig_ly
            self.local_target_rect = QRect(tx, ty, tw, th)

        # Morph sheet container widget
        self.sheet = MorphSheetWidget(theme=theme, radius=self.start_radius, parent=self)
        self.sheet.setAcceptDrops(True)
        self.sheet.setGeometry(self.local_start_rect)

        # Content container inside sheet (with opacity effect for fluid bloom)
        self.content_container = QWidget(self.sheet)
        self.content_opacity_effect = QGraphicsOpacityEffect(self.content_container)
        self.content_container.setGraphicsEffect(self.content_opacity_effect)
        self.content_opacity_effect.setOpacity(0.0)
        self.content_container.setVisible(False)

        # Animation object
        self.morph_anim = QVariantAnimation(self)
        self.morph_anim.valueChanged.connect(self._on_morph_tick)
        self._morph_anim_connection = None

        # Prewarm all icons in L1 in-memory cache before constructing grid widgets
        # Guarantees zero disk I/O during 180ms morphing animation even with 48+ items!
        prewarm_icons_batch(self.items, 128)

        self.init_ui()

    def init_ui(self):
        is_light = (self.theme == "light")
        if is_light:
            self.setStyleSheet("""
                QToolTip {
                    background-color: rgba(248, 250, 252, 246);
                    color: #0f172a;
                    border: 1px solid rgba(15, 23, 42, 26);
                    border-radius: 8px;
                    padding: 5px 10px;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 12px;
                }
            """)
        else:
            self.setStyleSheet("""
                QToolTip {
                    background-color: rgba(20, 22, 30, 242);
                    color: #f8fafc;
                    border: 1px solid rgba(255, 255, 255, 34);
                    border-radius: 8px;
                    padding: 5px 10px;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 12px;
                }
            """)

        # Sheet inner layout
        sheet_box = QVBoxLayout(self.sheet)
        sheet_box.setContentsMargins(12, 12, 12, 16)
        sheet_box.addWidget(self.content_container)

        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(18, 14, 18, 16)
        content_layout.setSpacing(10)

        # 1. Header Bar: In-place Editable Title & Action Controls
        header_bar = QHBoxLayout()
        header_bar.setSpacing(8)

        # Search icon toggle button (Vector antialiased)
        self.search_btn = VectorIconButton("search", size=32, parent=self.content_container)
        self.search_btn.setToolTip("搜索")
        self.search_btn.clicked.connect(self.toggle_search_input)
        header_bar.addWidget(self.search_btn)

        self.tidy_btn = None

        header_bar.addStretch()

        # Confident Folder Title (18px DemiBold, inline editable)
        self.title_edit = QLineEdit(self.folder_name, self.content_container)
        self.title_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.title_edit.setFont(_ui_font(18, QFont.Weight.DemiBold))
        if is_light:
            self.title_edit.setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    color: #0f172a;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 18px;
                    font-weight: 600;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 2px 14px;
                }
                QLineEdit:hover {
                    border: 1px solid rgba(15, 23, 42, 0.10);
                    background: rgba(15, 23, 42, 0.05);
                }
                QLineEdit:focus {
                    border: 1px solid rgba(59, 130, 246, 0.65);
                    background: rgba(255, 255, 255, 0.94);
                }
            """)
        else:
            self.title_edit.setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    color: #f8fafc;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 18px;
                    font-weight: 600;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 2px 14px;
                }
                QLineEdit:hover {
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    background: rgba(255, 255, 255, 0.06);
                }
                QLineEdit:focus {
                    border: 1px solid rgba(59, 130, 246, 0.65);
                    background: rgba(255, 255, 255, 0.09);
                }
            """)
        self.title_edit.editingFinished.connect(self.on_title_edited)
        header_bar.addWidget(self.title_edit)

        header_bar.addStretch()

        # Settings button (Vector antialiased control sliders)
        self.settings_btn = VectorIconButton("settings", size=32, parent=self.content_container)
        self.settings_btn.setToolTip("文件夹选项")
        self.settings_btn.clicked.connect(self.show_drawer_settings_menu)
        header_bar.addWidget(self.settings_btn)

        # Close button (Vector antialiased cross)
        close_btn = VectorIconButton("close", size=32, parent=self.content_container)
        close_btn.setToolTip("关闭 (Esc)")
        close_btn.clicked.connect(self.collapse_and_close)
        header_bar.addWidget(close_btn)

        content_layout.addLayout(header_bar)

        # 2. Sleek Slide-Down Search Bar
        self.search_input = SearchLineEdit(self, self.content_container)
        self.search_input.setPlaceholderText("搜索应用、文件、算式或网址...")
        self.search_input.setFixedHeight(36)
        self.search_input.setFont(_ui_font(13, QFont.Weight.Normal))
        if is_light:
            self.search_input.setStyleSheet("""
                QLineEdit {
                    background-color: rgba(15, 23, 42, 0.045);
                    color: #0f172a;
                    border: 1px solid rgba(15, 23, 42, 0.10);
                    border-radius: 10px;
                    padding: 7px 14px;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 13px;
                }
                QLineEdit:hover {
                    background-color: rgba(15, 23, 42, 0.065);
                    border: 1px solid rgba(15, 23, 42, 0.16);
                }
                QLineEdit:focus {
                    background-color: rgba(255, 255, 255, 0.96);
                    border: 1px solid rgba(59, 130, 246, 0.65);
                }
            """)
            pal = self.search_input.palette()
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#64748b"))
            self.search_input.setPalette(pal)
        else:
            self.search_input.setStyleSheet("""
                QLineEdit {
                    background-color: rgba(255, 255, 255, 0.06);
                    color: #f8fafc;
                    border: 1px solid rgba(255, 255, 255, 0.10);
                    border-radius: 10px;
                    padding: 7px 14px;
                    font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
                    font-size: 13px;
                }
                QLineEdit:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                    border: 1px solid rgba(255, 255, 255, 0.16);
                }
                QLineEdit:focus {
                    background-color: rgba(255, 255, 255, 0.09);
                    border: 1px solid rgba(59, 130, 246, 0.65);
                }
            """)
            pal = self.search_input.palette()
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(148, 163, 184, 185))
            self.search_input.setPalette(pal)
        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(120)
        self._search_debounce_timer.timeout.connect(self._on_search_debounced)
        self.search_input.textChanged.connect(self._on_search_input_changed)
        self.search_input.hide()
        content_layout.addWidget(self.search_input)

        # 2.5 Sleek Subgroup Tab Bar
        self.subgroup_tab_bar = FolderSubgroupTabBar(self.content_container)
        self.subgroup_tab_bar.tab_selected.connect(self.on_subgroup_tab_selected)
        self.subgroup_tab_bar.request_add_subgroup.connect(self.on_add_subgroup_requested)
        self.subgroup_tab_bar.request_rename_subgroup.connect(self.on_rename_subgroup_requested)
        self.subgroup_tab_bar.request_delete_subgroup.connect(self.on_delete_subgroup_requested)
        content_layout.addWidget(self.subgroup_tab_bar)

        # 3. Scroll Area for 4-Column Grid
        self.scroll = QScrollArea(self.content_container)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        handle_idle = "rgba(15, 23, 42, 42)" if is_light else "rgba(255, 255, 255, 45)"
        handle_hover = "rgba(15, 23, 42, 85)" if is_light else "rgba(255, 255, 255, 95)"
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 4px 1px 4px 1px;
            }}
            QScrollBar::handle:vertical {{
                background: {handle_idle};
                border-radius: 3px;
                min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {handle_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)

        self.scroll.setAcceptDrops(True)
        self.grid_container = QWidget()
        self.grid_container.setAcceptDrops(True)
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for c in range(4):
            self.grid_layout.setColumnStretch(c, 1)

        self.scroll.setWidget(self.grid_container)
        content_layout.addWidget(self.scroll)

        # Ensure all drop targets forward drag/drop events up to overlay
        for drop_target in [self.sheet, self.content_container, self.scroll, self.scroll.viewport(), self.grid_container]:
            drop_target.setAcceptDrops(True)
            drop_target.installEventFilter(self)

        # 4. Minimalist Footer
        self.count_lbl = QLabel(f"共 {len(self.items)} 项应用", self.content_container)
        self.count_lbl.setFont(_ui_font(12, QFont.Weight.Normal))
        self.count_lbl.hide()

        self.refresh_subgroups()
        self.apply_current_filters()

    def _connect_morph_finished(self, slot):
        self._disconnect_morph_finished()
        self.morph_anim.finished.connect(slot)
        self._morph_anim_connection = slot

    def _disconnect_morph_finished(self):
        if self._morph_anim_connection is not None:
            try:
                self.morph_anim.finished.disconnect(self._morph_anim_connection)
            except Exception:
                pass
            self._morph_anim_connection = None

    @contextmanager
    def _modal_guard(self):
        self._modal_guard_count = getattr(self, "_modal_guard_count", 0) + 1
        try:
            yield
        finally:
            self._modal_guard_count = max(0, getattr(self, "_modal_guard_count", 1) - 1)
            if self._modal_guard_count == 0 and self.isVisible() and not getattr(self, "is_closing", False):
                try:
                    self.raise_()
                    self.activateWindow()
                except Exception:
                    pass

    def open_animated(self):
        """Starts the in-place Hero expansion from local_start_rect to local_target_rect"""
        self.is_closing = False
        self._already_closed = False
        if not self.content_container.graphicsEffect():
            self.content_opacity_effect = QGraphicsOpacityEffect(self.content_container)
            self.content_container.setGraphicsEffect(self.content_opacity_effect)
        self.content_opacity_effect.setOpacity(0.0)
        self.content_container.setVisible(False)

        self.morph_anim.stop()
        self._connect_morph_finished(self._on_opened)

        self.morph_anim.setStartValue(0.0)
        self.morph_anim.setEndValue(1.0)
        self.morph_anim.setDuration(180)
        self.morph_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.morph_anim.start()
        self.update()

    def _on_opened(self):
        self._disconnect_morph_finished()
        if not self.is_closing:
            # Remove graphics effect once fully opened so QScrollArea renders crisply without buffer caching ghosts!
            self.content_container.setVisible(True)
            self.content_container.setGraphicsEffect(None)
            self.update()

    def collapse_and_close(self, on_finished=None):
        """Collapses smoothly back into the desktop card geometry before closing"""
        if getattr(self, "is_closing", False):
            return
        self.is_closing = True
        self._finish_callback = on_finished
        if hasattr(self, "_search_debounce_timer") and self._search_debounce_timer:
            self._search_debounce_timer.stop()
        self.morph_anim.stop()

        # Re-attach opacity effect for smooth fade out during collapse
        if not self.content_container.graphicsEffect():
            self.content_opacity_effect = QGraphicsOpacityEffect(self.content_container)
            self.content_container.setGraphicsEffect(self.content_opacity_effect)
        self.content_opacity_effect.setOpacity(max(0.0, min(1.0, self.morph_progress)))

        # If already near origin or zero, finish immediately without hanging
        if self.morph_progress <= 0.03:
            self._on_collapse_finished()
            return

        self._connect_morph_finished(self._on_collapse_finished)

        self.morph_anim.setStartValue(self.morph_progress)
        self.morph_anim.setEndValue(0.0)
        self.morph_anim.setDuration(160)
        self.morph_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.morph_anim.start()

        # Hard safety watchdog: guarantees overlay is never stuck on screen
        QTimer.singleShot(300, self._ensure_closed)

    def _on_collapse_finished(self):
        if getattr(self, "_already_closed", False):
            return
        self._already_closed = True
        self._disconnect_morph_finished()
        if hasattr(self, "_finish_callback") and self._finish_callback:
            try:
                self._finish_callback()
            except Exception:
                pass
        self.close()
        # Schedule idle working set memory trim 1.2s after closing drawer
        try:
            from desktop_layer import trim_process_working_set
            QTimer.singleShot(1200, trim_process_working_set)
        except Exception:
            pass

    def _ensure_closed(self):
        if not getattr(self, "_already_closed", False):
            self._on_collapse_finished()


    def _on_morph_tick(self, val):
        t = float(val)
        self.morph_progress = t

        # Interpolate Sheet Geometry with physical center preservation
        sx, sy, sw, sh = self.local_start_rect.x(), self.local_start_rect.y(), self.local_start_rect.width(), self.local_start_rect.height()
        tx, ty, tw, th = self.local_target_rect.x(), self.local_target_rect.y(), self.local_target_rect.width(), self.local_target_rect.height()

        start_cx = sx + sw / 2.0
        start_cy = sy + sh / 2.0
        target_cx = tx + tw / 2.0
        target_cy = ty + th / 2.0

        curr_cx = start_cx + (target_cx - start_cx) * t
        curr_cy = start_cy + (target_cy - start_cy) * t
        curr_w = sw + (tw - sw) * t
        curr_h = sh + (th - sh) * t

        cx = int(curr_cx - curr_w / 2.0)
        cy = int(curr_cy - curr_h / 2.0)
        cw = int(curr_w)
        ch = int(curr_h)
        
        old_sheet_geo = self.sheet.geometry()
        self.sheet.setGeometry(cx, cy, cw, ch)

        # Interpolate radius
        target_r = 32.0
        self.sheet.radius = self.start_radius + (target_r - self.start_radius) * min(1.0, max(0.0, t))

        # Dynamic Content Opacity:
        # Fades smoothly across the morph progress range (0.20 -> 1.00)
        if self.content_container.graphicsEffect():
            if t <= 0.20:
                c_op = 0.0
                if self.content_container.isVisible():
                    self.content_container.setVisible(False)
            else:
                c_op = min(1.0, max(0.0, (t - 0.20) / 0.80))
                if not self.content_container.isVisible():
                    self.content_container.setVisible(True)
            self.content_opacity_effect.setOpacity(c_op)

        # Dynamic Sheet Background Opacity on final collapse phase (t: 0.20 -> 0.0)
        # Allows the sheet to seamlessly melt back into the desktop card underneath!
        if self.is_closing and t < 0.20:
            self.sheet.opacity = max(0.0, min(0.95, 0.95 * (t / 0.20)))
        else:
            self.sheet.opacity = 0.95

        self.sheet.update()

        # Dirty region repainting: Only invalidate the union of old and new sheet geometry with shadow padding!
        # This reduces repainted pixels by over 95%, completely eliminating dropped frames on 2K/4K displays.
        new_sheet_geo = self.sheet.geometry()
        dirty_rect = old_sheet_geo.united(new_sheet_geo).adjusted(-48, -48, 48, 48)
        self.update(dirty_rect)

    def mousePressEvent(self, event):
        # Click outside sheet collapses and dismisses overlay
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.sheet.geometry().contains(event.pos()):
                self.collapse_and_close()
                event.accept()
                return
        super().mousePressEvent(event)

    def showEvent(self, event):
        if self.windowHandle() and getattr(self, "_target_screen_obj", None):
            try:
                self.windowHandle().setScreen(self._target_screen_obj)
            except Exception:
                pass
        super().showEvent(event)

    def changeEvent(self, event):
        """
        Detects window activation loss (e.g. user Alt+Tabs or switches to another program).
        Automatically dismisses the overlay so it never stays stuck on top of external applications.
        """
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and getattr(self, "_modal_guard_count", 0) == 0:
                if not QApplication.activeModalWidget() and not QApplication.activePopupWidget():
                    self.collapse_and_close()
        super().changeEvent(event)

    def nativeEvent(self, eventType, message):
        """
        Win32 native message pump listener:
        Handles WM_ACTIVATE (WA_INACTIVE) to immediately drop HWND_NOTOPMOST
        and collapse gracefully when user switches focus via Alt+Tab or mouse click.
        """
        if eventType == "windows_generic_MSG":
            msg = wintypes.MSG.from_address(message.__int__())
            if msg.message == 0x0006:  # WM_ACTIVATE
                wparam = msg.wParam & 0xFFFF
                if wparam == 0 and getattr(self, "_modal_guard_count", 0) == 0:  # WA_INACTIVE
                    if not QApplication.activeModalWidget() and not QApplication.activePopupWidget():
                        try:
                            # Immediately strip topmost z-order so it never obscures the switched window
                            ctypes.windll.user32.SetWindowPos(
                                int(self.winId()), -2, 0, 0, 0, 0,  # -2 is HWND_NOTOPMOST
                                0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                            )
                        except Exception:
                            pass
                        QTimer.singleShot(0, self.collapse_and_close)
        return super().nativeEvent(eventType, message)

    def _handle_overlay_drop(self, mime_data, global_pos) -> bool:
        if is_internal_item_drag(mime_data):
            import json
            try:
                payload = json.loads(bytes(mime_data.data(STACKY_ITEM_MIME)).decode("utf-8"))
            except Exception:
                return False
            src_folder = payload.get("source_folder", "")
            token = payload.get("drag_token", "")

            # 抽屉是全屏置顶 overlay，会截获本该投递给其它桌面卡片的 drop；
            # 按光标位置显式转发，绕过 Qt 的事件路由。
            try:
                from main_window import BigFolderWindow
                windows = BigFolderWindow._instances
            except Exception:
                windows = []
            for w in windows:
                card = getattr(w, "card", None)
                if card is None or not w.isVisible() or not card.isVisible():
                    continue
                card_pos = card.mapToGlobal(QPoint(0, 0))
                if not QRect(card_pos, card.size()).contains(global_pos):
                    continue
                # 落在别的卡片：标记已处理，避免源端把图标弹回桌面
                mark_cross_folder_drop(token)
                if w.get_current_folder_name() != src_folder and hasattr(w, "handle_cross_folder_drop"):
                    w.handle_cross_folder_drop(payload)
                return True

            sheet = getattr(self, "sheet", None)
            if sheet and sheet.geometry().contains(self.mapFromGlobal(global_pos)):
                # 拖回抽屉内部：不重复收纳
                mark_cross_folder_drop(token)
                return True

            # 落在 Stacky 界面之外（桌面空白 / 资源管理器）：
            # 绝不标记 token，否则源端会误判「已处理」而不执行移出到桌面。
            return False

        local_files, web_bookmarks = parse_dropped_mime_data(mime_data)
        if not local_files and not web_bookmarks:
            return False

        target_slot = -1
        pos_in_container = self.grid_container.mapFromGlobal(global_pos)
        for idx, w in enumerate(self.item_widgets):
            if isinstance(w, MobileExpandedItemWidget) and w.geometry().contains(pos_in_container):
                target_slot = getattr(w, "grid_index", idx)
                break

        if web_bookmarks:
            if self.active_subgroup != "全部":
                for wb in web_bookmarks:
                    wb["subgroup"] = self.active_subgroup
            self.web_items_dropped.emit(web_bookmarks, target_slot)

        if local_files:
            self.files_dropped.emit(local_files, target_slot)

        return True

    def eventFilter(self, watched, event):
        t = event.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if is_drop_acceptable(event.mimeData()):
                event.acceptProposedAction()
                return True
        elif t == QEvent.Type.Drop:
            pos_in_w = event.position().toPoint() if hasattr(event, "position") else event.pos()
            global_pos = watched.mapToGlobal(pos_in_w)
            if self._handle_overlay_drop(event.mimeData(), global_pos):
                self._internal_drawer_drag_handled = True
                event.acceptProposedAction()
            else:
                event.ignore()
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if is_drop_acceptable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if is_drop_acceptable(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        pos_in_w = event.position().toPoint() if hasattr(event, "position") else event.pos()
        global_pos = self.mapToGlobal(pos_in_w)
        if self._handle_overlay_drop(event.mimeData(), global_pos):
            self._internal_drawer_drag_handled = True
            event.acceptProposedAction()
            return
        event.ignore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        p = max(0.0, min(1.0, float(self.morph_progress)))

        # 1. Deep, elegant ambient scrim dimming
        # Paints ONLY the region marked dirty (event.rect()) to avoid full-screen software alpha repainting!
        scrim_alpha = 110 if self.morph_anim.state() == QVariantAnimation.State.Running else int(110 * p)
        painter.fillRect(event.rect(), QColor(0, 0, 0, scrim_alpha))

        # 2. Ambient drop shadow around sheet geometry (pure QPainter, 120 FPS, zero QGraphicsEffect lag)
        if hasattr(self, "sheet") and self.sheet.isVisible():
            sheet_geo = self.sheet.geometry()
            radius = getattr(self.sheet, "radius", 30.0)
            p_alpha = p
            # 抽屉底板自带圆角与高光边，不再画外扩实心矩形（避免整圈重影）
            pass
        painter.end()

    def closeEvent(self, event):
        if hasattr(self, "_search_debounce_timer") and self._search_debounce_timer:
            self._search_debounce_timer.stop()
        if hasattr(self, "morph_anim") and self.morph_anim:
            self.morph_anim.stop()
        self._disconnect_morph_finished()
        self.closed.emit()
        super().closeEvent(event)

    def show_search_input(self, focus: bool = True):
        if not self.search_input.isVisible():
            self.search_input.show()
            self.search_btn.is_active = True
            self.search_btn.update()
            if self.sheet.height() < 420:
                cur_geo = self.sheet.geometry()
                new_h = min(480, self.screen_geo.height() - 160)
                new_y = max(40, cur_geo.center().y() - new_h // 2)
                self.sheet.setGeometry(cur_geo.x(), new_y, cur_geo.width(), new_h)
        if focus:
            self.search_input.setFocus()

    def toggle_search_input(self):
        if self.search_input.isVisible():
            self.search_input.hide()
            self.search_input.clear()
            self.search_btn.is_active = False
            self.search_btn.update()
            self.setFocus()
            if hasattr(self, "local_target_rect"):
                cur_geo = self.sheet.geometry()
                self.sheet.setGeometry(cur_geo.x(), self.local_target_rect.y(), cur_geo.width(), self.local_target_rect.height())
        else:
            self.show_search_input(focus=True)

    def on_title_edited(self):
        new_name = self.title_edit.text().strip()
        if new_name and new_name != self.folder_name:
            self.folder_name = new_name
            self.folder_renamed.emit(new_name)
        self.title_edit.clearFocus()
        if self.search_input.isVisible():
            self.search_input.setFocus()
        else:
            self.setFocus()

    def _on_search_input_changed(self, text: str):
        if getattr(self, "is_closing", False):
            self._search_debounce_timer.stop()
            return
        if not text.strip():
            self._search_debounce_timer.stop()
            self.on_search_text_changed(text)
        else:
            self._search_debounce_timer.start()

    def _on_search_debounced(self):
        if getattr(self, "is_closing", False):
            return
        self.on_search_text_changed(self.search_input.text())

    def on_search_text_changed(self, text: str, reset_selection: bool = True):
        query = text.strip()
        special: List[Dict[str, Any]] = []
        active_pool = [it for it in self.items if self._item_matches_subgroup(it, self.active_subgroup)]

        if query:
            # 1. Safe Real-Time Math Calculator
            math_res = safe_calculate_math(query)
            if math_res:
                special.append({
                    "type": "math",
                    "name": f"计算结果: {math_res['formatted']} (按回车复制到剪贴板)",
                    "value": math_res["formatted"],
                    "raw_val": math_res["raw_val"],
                    "icon": "=",
                })

            # 2. Web Search & Quick Links
            web_res = parse_web_quick_link(query)
            if web_res:
                special.append({
                    "type": "web",
                    "name": f"{web_res['title']} (按回车直接打开)",
                    "url": web_res["url"],
                    "icon": web_res.get("icon", "网"),
                })

            # 3. Filtered Local Apps within active subgroup
            filtered = search_items(active_pool, query)

            # 4. System Commands (deduplicate if the app is already in filtered items)
            sys_cmds = match_system_commands(query)
            for sc in sys_cmds:
                cmd_str = sc.get("cmd", "")
                cmd_target = cmd_str.split()[0].lower() if cmd_str else ""
                already_has_app = any(
                    ((cmd_target and it.get("path", "").lower().endswith(cmd_target)) or it.get("name", "").lower() in (sc["name"].lower(), sc["id"].lower(), "计算器", "任务管理器", "命令提示符", "注册表编辑器", "控制面板"))
                    for it in filtered
                )
                if not already_has_app:
                    special.append({
                        "type": "sys_cmd",
                        "name": f"{sc['name']} (按回车执行)",
                        "cmd_info": sc,
                        "icon": sc.get("icon", ">"),
                    })

            # 5. Everything 全盘毫秒检索 (P3 级，当键入字符 >= 2 时流式呈现)
            ev_files = []
            is_everything_on = True
            if hasattr(self, "config_manager") and self.config_manager:
                is_everything_on = self.config_manager.is_everything_enabled()
            if is_everything_on and len(query) >= 2:
                raw_ev = search_everything_files(query, max_results=12)
                existing_paths = {it.get("path", "").lower() for it in filtered}
                for ef in raw_ev:
                    if ef.get("path", "").lower() not in existing_paths:
                        ev_files.append(ef)
        else:
            filtered = active_pool
            ev_files = []

        self.special_items = special
        self.current_display_items = filtered + ev_files
        self.action_items = special + filtered + ev_files

        if query:
            self.keyboard_nav_active = True

        if reset_selection:
            self.selected_item_index = 0
        else:
            self.selected_item_index = min(max(0, self.selected_item_index), max(0, len(self.action_items) - 1))

        parts = []
        if special:
            parts.append(f"{len(special)} 项指令")
        if filtered:
            parts.append(f"{len(filtered)} 项应用")
        if ev_files:
            parts.append(f"{len(ev_files)} 项 Everything 文件")
        
        if parts:
            self.count_lbl.setText(" · ".join(parts))
        elif query:
            self.count_lbl.setText("未找到匹配应用或文件")
        else:
            self.count_lbl.setText(f"共 {len(filtered)} 项应用")

        self.populate_grid(self.action_items)

    def get_all_subgroups(self) -> List[str]:
        sgs = set()
        for it in self.items:
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

    def _item_matches_subgroup(self, item: Dict[str, Any], subgroup: str) -> bool:
        if not item or subgroup == "全部":
            return True
        sg = (item.get("subgroup") or "").strip()
        if sg:
            return sg == subgroup
        if subgroup == "网页书签":
            p = item.get("path", "")
            return bool(
                item.get("type") == "web"
                or p.startswith("http://")
                or p.startswith("https://")
                or p.lower().endswith(".url")
            )
        return False

    def refresh_subgroups(self):
        if not hasattr(self, "subgroup_tab_bar"):
            return
        from collections import OrderedDict
        sg_counts: Dict[str, int] = OrderedDict()
        for it in self.items:
            if not it:
                continue
            sg = (it.get("subgroup") or "").strip()
            p = it.get("path", "")
            is_web = (it.get("type") == "web" or p.startswith("http://") or p.startswith("https://") or p.lower().endswith(".url"))
            if sg:
                sg_counts[sg] = sg_counts.get(sg, 0) + 1
            elif is_web:
                sg_counts["网页书签"] = sg_counts.get("网页书签", 0) + 1

        should_show = (len(sg_counts) > 0) or (len(self.items) >= 8)
        if should_show:
            if self.active_subgroup != "全部" and self.active_subgroup not in sg_counts:
                self.active_subgroup = "全部"
            self.subgroup_tab_bar.set_tabs(sg_counts, total_count=len(self.items), active_subgroup=self.active_subgroup)
            self.subgroup_tab_bar.show()
        else:
            self.subgroup_tab_bar.hide()

    def apply_current_filters(self):
        # If search bar is open with an active query, preserve full Spotlight pipeline (math/web/sys_cmd/Everything)
        query = self.search_input.text().strip() if (hasattr(self, "search_input") and not self.search_input.isHidden()) else ""
        if query:
            self.on_search_text_changed(query, reset_selection=False)
            return

        if self.active_subgroup == "全部":
            filtered = list(self.items)
        else:
            filtered = [it for it in self.items if self._item_matches_subgroup(it, self.active_subgroup)]

        self.special_items = []
        self.action_items = list(filtered)
        self.current_display_items = list(filtered)
        self.selected_item_index = min(max(0, self.selected_item_index), max(0, len(self.action_items) - 1))
        self.count_lbl.setText(f"共 {len(filtered)} 项应用")
        self.populate_grid(self.action_items)

    def on_subgroup_tab_selected(self, subgroup: str):
        self.active_subgroup = subgroup
        self.apply_current_filters()

    def on_add_subgroup_requested(self):
        with self._modal_guard():
            new_name, ok = ModernInputDialog.getText(self, "新建子分组", "请输入子分组名称 (例如: 常用套件、设计工具):")
        if ok and new_name.strip():
            self.active_subgroup = new_name.strip()
            self.refresh_subgroups()
            self.apply_current_filters()

    def on_rename_subgroup_requested(self, old_name: str):
        with self._modal_guard():
            new_name, ok = ModernInputDialog.getText(self, "重命名子分组", f"请输入【{old_name}】的新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            for idx, it in enumerate(self.items):
                if it.get("subgroup") == old_name:
                    updated = dict(it)
                    updated["subgroup"] = new_name.strip()
                    self.items[idx] = updated
                    self.item_changed.emit(idx, updated)
            if self.active_subgroup == old_name:
                self.active_subgroup = new_name.strip()
            self.refresh_subgroups()
            self.apply_current_filters()

    def action_add_file_to_folder(self, item_data: Dict[str, Any]):
        """搜索结果条目一键添加到当前大文件夹"""
        path = item_data.get("path", "")
        if not path:
            return
        name = item_data.get("name", os.path.basename(path))
        if any(it.get("path") == path for it in self.items):
            self.status_toast.emit(f"【{name}】已在当前文件夹中", False)
            return

        new_entry = {
            "name": name,
            "path": path,
            "args": "",
            "is_dir": item_data.get("is_folder", False),
            "ext": os.path.splitext(path)[1].lower()
        }
        if hasattr(self, "active_subgroup") and self.active_subgroup and self.active_subgroup != "全部":
            new_entry["subgroup"] = self.active_subgroup

        self.items.append(new_entry)
        self.apply_current_filters()
        self.files_dropped.emit([path], len(self.items) - 1)
        self.status_toast.emit(f"已将【{name}】加入【{self.folder_name}】", False)
        if hasattr(self, "search_input") and self.search_input:
            self.search_input.clear()

    def show_add_local_app_dialog(self):
        """弹出本地应用选择器，直接批量加入当前大文件夹"""
        desktop_dir = os.path.expandvars(r"%USERPROFILE%\Desktop")
        with self._modal_guard():
            files, _ = QFileDialog.getOpenFileNames(
                self,
                f"选择要添加到【{self.folder_name}】的应用或文件",
                desktop_dir,
                "常用程序与文件 (*.exe *.lnk *.url *.bat *.cmd *.pdf *.docx *.xlsx *.txt);;所有文件 (*.*)"
            )
        if files:
            added = 0
            for fp in files:
                if not any(it.get("path") == fp for it in self.items):
                    entry = {
                        "name": os.path.splitext(os.path.basename(fp))[0],
                        "path": fp,
                        "args": "",
                        "is_dir": os.path.isdir(fp),
                        "ext": os.path.splitext(fp)[1].lower()
                    }
                    if hasattr(self, "active_subgroup") and self.active_subgroup and self.active_subgroup != "全部":
                        entry["subgroup"] = self.active_subgroup
                    self.items.append(entry)
                    added += 1
            if added > 0:
                self.apply_current_filters()
                self.files_dropped.emit(files, len(self.items) - 1)
                self.status_toast.emit(f"已成功添加 {added} 项应用到【{self.folder_name}】", False)

    def on_delete_subgroup_requested(self, sg_name: str):
        for idx, it in enumerate(self.items):
            if it.get("subgroup") == sg_name:
                updated = dict(it)
                updated["subgroup"] = ""
                self.items[idx] = updated
                self.item_changed.emit(idx, updated)
        if self.active_subgroup == sg_name:
            self.active_subgroup = "全部"
        self.refresh_subgroups()
        self.apply_current_filters()

    def show_add_web_bookmark_dialog(self):
        initial_sg = self.active_subgroup if self.active_subgroup != "全部" else "网页书签"
        dlg = AddWebBookmarkDialog(
            None,
            initial_subgroup=initial_sg,
            existing_subgroups=self.get_all_subgroups(),
            theme=self.theme,
        )
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        with self._modal_guard():
            accepted = dlg.exec()
        if accepted:
            bm = dlg.get_bookmark_data()
            if bm:
                self.web_items_dropped.emit([bm], -1)

    def populate_grid(self, items_to_show: List[Dict[str, Any]]):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.item_widgets.clear()

        cols = 4
        self.grid_cols = cols
        for c in range(cols):
            self.grid_layout.setColumnStretch(c, 1)
        current_row = 0
        app_col = 0

        for idx, item in enumerate(items_to_show):
            item_type = item.get("type", "app")
            is_sel = (idx == self.selected_item_index and getattr(self, "keyboard_nav_active", False))

            if item_type in ("math", "web", "sys_cmd"):
                banner = SearchBannerWidget(
                    item,
                    is_selected=is_sel,
                    parent=self.grid_container
                )
                banner.setAcceptDrops(True)
                banner.installEventFilter(self)
                banner.clicked.connect(self.execute_item)
                if app_col > 0:
                    current_row += 1
                    app_col = 0
                self.grid_layout.addWidget(banner, current_row, 0, 1, cols)
                current_row += 1
                self.item_widgets.append(banner)
            elif item_type == "file":
                file_row = SearchFileBannerWidget(
                    item,
                    is_selected=is_sel,
                    parent=self.grid_container
                )
                file_row.setAcceptDrops(True)
                file_row.installEventFilter(self)
                file_row.clicked.connect(self.execute_item)
                file_row.open_folder_requested.connect(lambda it: self.launch_file_item(it, open_folder=True))
                if app_col > 0:
                    current_row += 1
                    app_col = 0
                self.grid_layout.addWidget(file_row, current_row, 0, 1, cols)
                current_row += 1
                self.item_widgets.append(file_row)
            else:
                disp_mode = "classic"
                show_admin = True
                show_running = True
                if hasattr(self, "config_manager") and self.config_manager:
                    disp_mode = self.config_manager.get_app_display_mode()
                    show_admin = self.config_manager.is_admin_badge_enabled()
                    show_running = self.config_manager.is_running_badge_enabled()

                widget = MobileExpandedItemWidget(
                    item,
                    is_selected=is_sel,
                    open_mode=self.open_mode,
                    display_mode=disp_mode,
                    show_admin_badge=show_admin,
                    show_running_badge=show_running,
                    parent=self.grid_container
                )
                widget.setAcceptDrops(True)
                widget.installEventFilter(self)
                widget.grid_index = idx
                widget.clicked.connect(self.launch_and_close)
                widget.item_selected.connect(self.select_item_by_index)
                widget.request_close.connect(self.collapse_and_close)
                widget.item_changed.connect(self.on_child_item_changed)
                widget.item_removed.connect(self.on_child_item_removed)
                widget.request_move_to_desktop.connect(lambda it: (self.collapse_and_close(), self.request_move_to_desktop.emit(it.get("path", ""))))
                widget.request_recycle.connect(lambda it: (self.collapse_and_close(), self.request_recycle.emit(it.get("path", ""))))

                self.grid_layout.addWidget(widget, current_row, app_col)
                app_col += 1
                if app_col >= cols:
                    app_col = 0
                    current_row += 1
                self.item_widgets.append(widget)

        self.grid_layout.setRowStretch(current_row + 1, 1)

    def update_items(self, new_items: List[Dict[str, Any]]):
        real_items = [it for it in new_items if it and (it.get("path") or it.get("name"))]
        self.items = real_items
        self.refresh_subgroups()
        if hasattr(self, "search_input") and not self.search_input.isHidden() and self.search_input.text().strip():
            self.on_search_text_changed(self.search_input.text(), reset_selection=False)
        else:
            self.apply_current_filters()

    def on_child_item_changed(self, orig_item: Dict[str, Any], updated_item: Dict[str, Any]):
        for idx, item in enumerate(self.items):
            if item.get("path") == orig_item.get("path") and item.get("name") == orig_item.get("name"):
                self.items[idx] = updated_item
                self.item_changed.emit(idx, updated_item)
                self.refresh_subgroups()
                self.apply_current_filters()
                break

    def on_child_item_removed(self, item_to_remove: Dict[str, Any]):
        for idx, item in enumerate(self.items):
            if item.get("path") == item_to_remove.get("path") and item.get("name") == item_to_remove.get("name"):
                self.items.pop(idx)
                self.item_removed.emit(idx)
                self.refresh_subgroups()
                if hasattr(self, "search_input") and not self.search_input.isHidden() and self.search_input.text().strip():
                    self.on_search_text_changed(self.search_input.text())
                else:
                    self.apply_current_filters()
                break

    def keyPressEvent(self, event: QKeyEvent):
        self.keyboard_nav_active = True
        key = event.key()
        total = len(self.action_items)
        if total > 0:
            self.selected_item_index = min(max(0, self.selected_item_index), total - 1)
        else:
            self.selected_item_index = 0

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if total > 0:
                selected_item = self.action_items[self.selected_item_index]
                if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and selected_item.get("type") == "file":
                    self.launch_file_item(selected_item, open_folder=True)
                else:
                    self.execute_item(selected_item)
            event.accept()
            return
        elif key == Qt.Key.Key_Escape:
            self.collapse_and_close()
            event.accept()
            return
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Tab):
            if total > 0:
                self.selected_item_index = (self.selected_item_index + 1) % total
                self.update_selection_highlight()
            event.accept()
            return
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backtab):
            if total > 0:
                self.selected_item_index = (self.selected_item_index - 1 + total) % total
                self.update_selection_highlight()
            event.accept()
            return
        elif key == Qt.Key.Key_Down:
            if total > 0:
                col_step = max(1, int(getattr(self, "grid_cols", 4)))
                curr_item = self.action_items[self.selected_item_index]
                if curr_item.get("type") in ("math", "web", "sys_cmd", "file"):
                    self.selected_item_index = min(total - 1, self.selected_item_index + 1)
                else:
                    next_idx = self.selected_item_index + col_step
                    if next_idx < total:
                        self.selected_item_index = next_idx
                    else:
                        first_app_idx = len(self.special_items)
                        curr_app_row = max(0, self.selected_item_index - first_app_idx) // col_step
                        last_item = self.action_items[total - 1]
                        if last_item.get("type") in ("math", "web", "sys_cmd", "file"):
                            self.selected_item_index = total - 1
                        else:
                            last_app_row = max(0, (total - 1) - first_app_idx) // col_step
                            if last_app_row > curr_app_row:
                                self.selected_item_index = total - 1
                self.update_selection_highlight()
            event.accept()
            return
        elif key == Qt.Key.Key_Up:
            if total > 0:
                col_step = max(1, int(getattr(self, "grid_cols", 4)))
                curr_item = self.action_items[self.selected_item_index]
                if curr_item.get("type") in ("math", "web", "sys_cmd", "file"):
                    self.selected_item_index = max(0, self.selected_item_index - 1)
                else:
                    first_app_idx = len(self.special_items)
                    if self.selected_item_index - col_step < first_app_idx:
                        if first_app_idx > 0:
                            self.selected_item_index = first_app_idx - 1
                        else:
                            self.selected_item_index = max(0, self.selected_item_index - col_step)
                    else:
                        self.selected_item_index = max(0, self.selected_item_index - col_step)
                self.update_selection_highlight()
            event.accept()
            return
        elif (event.modifiers() & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_F) or key == Qt.Key.Key_Slash:
            self.show_search_input(focus=True)
            event.accept()
            return

        # Auto-reveal search input when printable characters are typed
        text = event.text()
        if text and text.isprintable() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.show_search_input(focus=True)
            self.search_input.insert(text)
            event.accept()
            return

        super().keyPressEvent(event)

    def update_selection_highlight(self):
        for idx, w in enumerate(self.item_widgets):
            w.set_selected(idx == self.selected_item_index and getattr(self, "keyboard_nav_active", False))
        if 0 <= self.selected_item_index < len(self.item_widgets):
            self.scroll.ensureWidgetVisible(self.item_widgets[self.selected_item_index], 10, 10)

    def execute_item(self, item_data: Dict[str, Any]):
        if not item_data:
            return
        item_type = item_data.get("type", "app")

        if item_type == "math":
            val = item_data.get("value", "")
            if val:
                QApplication.clipboard().setText(str(val))
                self.status_toast.emit(f"已复制计算结果: {val}", False)
            self.collapse_and_close()
            return

        elif item_type == "web":
            url = item_data.get("url", "")
            if url:
                self.collapse_and_close()
                try:
                    os.startfile(url)
                    self.status_toast.emit(f"已打开: {url}", False)
                except Exception as e:
                    print("Failed to open web URL:", e)
            return

        elif item_type == "sys_cmd":
            cmd_info = item_data.get("cmd_info", {})
            self.collapse_and_close()
            if cmd_info:
                execute_system_command(cmd_info)
                self.status_toast.emit(f"已启动: {cmd_info.get('name', '')}", False)
            return

        elif item_type == "file":
            self.launch_file_item(item_data, open_folder=False)
            return

        else:
            self.launch_and_close(item_data)

    def launch_file_item(self, item_data: Dict[str, Any], open_folder: bool = False):
        if not item_data:
            return
        full_path = item_data.get("path", "")
        if not full_path:
            return
        self.collapse_and_close()
        try:
            if open_folder:
                if os.path.exists(full_path):
                    subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(full_path)}"])
                else:
                    dir_path = item_data.get("dir", "")
                    if os.path.exists(dir_path):
                        safe_launch_target(dir_path)
            else:
                if os.path.exists(full_path):
                    safe_launch_target(full_path)
                else:
                    subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(full_path)}"])
        except Exception as e:
            print("Failed to open Everything search result:", e)

    def launch_and_close(self, item_data: Dict[str, Any]):
        if not item_data:
            return
        raw_path = item_data.get("path", "")
        if not raw_path:
            return
        path = resolve_actual_path(raw_path).strip('\"')
        if not path:
            return
        args = item_data.get("args", "").strip()
        should_close = True
        if hasattr(self, "config_manager") and self.config_manager:
            should_close = (self.config_manager.get_post_launch_action() == "close_drawer")
        if should_close:
            self.collapse_and_close()
        try:
            safe_launch_target(raw_path, args)
        except Exception as e:
            print("Failed to launch from expanded folder:", e)

    def select_item_by_index(self, target_idx: int):
        self.keyboard_nav_active = True
        self.selected_item_index = target_idx
        for idx, w in enumerate(self.item_widgets):
            if hasattr(w, "set_selected"):
                w.set_selected(idx == target_idx)

    def apply_open_app_mode(self, mode: str):
        self.open_app_mode = mode
        self.open_mode = "single" if mode == "single" else "double"
        for w in self.item_widgets:
            if hasattr(w, "open_mode"):
                w.open_mode = self.open_mode
        self.request_set_open_mode.emit(mode)

    def show_drawer_settings_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(_get_menu_qss(self.theme))

        open_mode_menu = menu.addMenu("打开方式")
        open_mode_menu.setStyleSheet(menu.styleSheet())
        curr_open_mode = getattr(self, "open_app_mode", "double")
        open_modes = [
            ("double", "双击打开（推荐）"),
            ("hybrid", "双层模式（卡片单击 · 抽屉双击）"),
            ("single", "单击打开"),
        ]
        for m_k, m_name in open_modes:
            prefix = "● " if curr_open_mode == m_k else "○ "
            act = open_mode_menu.addAction(f"{prefix}{m_name}")
            act.triggered.connect(lambda checked=False, k=m_k: self.apply_open_app_mode(k))

        menu.addSeparator()

        act_add_local = menu.addAction("添加应用")
        act_add_local.triggered.connect(self.show_add_local_app_dialog)

        act_add_web = menu.addAction("添加网页")
        act_add_web.triggered.connect(self.show_add_web_bookmark_dialog)

        act_tidy = menu.addAction("归整桌面")
        act_tidy.triggered.connect(self.request_tidy_desktop.emit)

        menu.addSeparator()

        act_settings = menu.addAction("设置")
        act_settings.triggered.connect(lambda: self.request_open_settings.emit(1))

        pos = self.settings_btn.mapToGlobal(QPoint(0, self.settings_btn.height() + 4))
        menu.exec(pos)

    def contextMenuEvent(self, event):
        if self.sheet.geometry().contains(event.pos()):
            self.show_drawer_settings_menu()
            event.accept()
            return
        super().contextMenuEvent(event)


# Aliases for 100% backward compatibility
MobileExpandedFolderOverlay = HeroExpandedFolderOverlay
ExpandedFolderDrawer = HeroExpandedFolderOverlay
