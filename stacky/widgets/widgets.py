import os
import subprocess
import ctypes
from ctypes import wintypes
from typing import Dict, Any, Optional, List

from PySide6.QtCore import (
    Qt, QPoint, QSize, Signal, QMimeData, QRectF, QVariantAnimation, QEasingCurve, QUrl
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QPixmap, QPainterPath,
    QDrag, QCursor, QAction, QLinearGradient, QRadialGradient
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QGridLayout, QVBoxLayout, QMenu,
    QFileDialog, QInputDialog, QMessageBox, QApplication,
)

from icon_utils import (
    get_app_icon_pixmap, resolve_actual_path, resolve_shortcut_target, resolve_url_shortcut,
    create_squircle_path, parse_dropped_mime_data, is_drop_acceptable,
    extract_site_name_from_url, is_internal_item_drag,
    mark_cross_folder_drop, consume_cross_folder_drop, STACKY_ITEM_MIME,
    draw_liquid_glass
)
from web_dialog import AddWebBookmarkDialog

def run_as_admin(file_path: str, args: str = "") -> bool:
    """Executes target program with elevated Administrator privileges (Windows UAC prompt)"""
    target_path = resolve_shortcut_target(file_path).strip('\"')
    if not target_path or not os.path.exists(target_path):
        return False
    try:
        res = ctypes.windll.shell32.ShellExecuteW(None, "runas", target_path, args, None, 1)
        return res > 32
    except Exception:
        return False

def show_file_properties(file_path: str) -> bool:
    """Invokes native Windows Shell properties dialog for the specified file"""
    real_path = resolve_actual_path(file_path).strip('\"')
    if not os.path.exists(real_path):
        return False
    try:
        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]
        SEE_MASK_INVOKEIDLIST = 0x0000000C
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_INVOKEIDLIST
        sei.hwnd = None
        sei.lpVerb = "properties"
        sei.lpFile = real_path
        sei.nShow = 1
        return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)))
    except Exception:
        return False

def _wrap_label_text(fm, text: str, max_width: float, max_lines: int = 2) -> str:
    """应用名按像素宽度折成最多两行，避免全部挤成一行省略号。"""
    lines, rest = [], text
    while rest and len(lines) < max_lines:
        cut = len(rest)
        while cut > 1 and fm.horizontalAdvance(rest[:cut]) > max_width:
            cut -= 1
        # 英文优先在空格/连字符处断行，避免把单词切成两半
        if cut < len(rest):
            found_sep = False
            for sep in (" ", "-", "_"):
                pos = rest.rfind(sep, 0, cut + 1)
                if pos > 0:
                    cut = pos if sep == " " else pos + 1
                    found_sep = True
                    break
            if not found_sep:
                # 驼峰词在大小写切换处断行：CodeBuddy → Code / Buddy
                for i in range(min(cut, len(rest)) - 1, 0, -1):
                    if rest[i - 1].islower() and rest[i].isupper():
                        cut = i
                        break
        if len(lines) == max_lines - 1 and cut < len(rest):
            lines.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, int(max_width)))
            rest = ""
        else:
            lines.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
    return "\n".join(lines)


class AppSlotWidget(QWidget):
    """
    Mobile-grade App Slot within the big folder grid.
    Adheres strictly to continuous-curvature squircle design language:
    1. Single App Slot:
       - Real-time squircle icon with 0.92x tactile spring press compression.
       - Gentle 1.06x hover expansion with soft ambient squircle glow.
       - Direct single-click launching.
    2. Mini Cluster Slot (8+1 Overflow Stack):
       - If folder has > 8 apps, slot 9 renders a 2x2 mini icon cluster.
       - Clicking opens the full-screen / expanded folder view.
    3. Empty Slot:
       - Pure clean glass surface! Zero dashed lines, zero wireframe plus signs, zero dots.
       - Soft receptive glow only appears during drag-and-drop hover.
    """
    item_launched = Signal(str)
    item_changed = Signal(int, dict)
    item_removed = Signal(int)
    item_moved = Signal(int, int)
    request_add = Signal(int)
    request_add_web = Signal(int)
    request_open_drawer = Signal()
    request_global_menu = Signal(QPoint)
    request_move_to_desktop = Signal(str)
    request_recycle = Signal(str)
    status_toast = Signal(str, bool)

    def __init__(
        self,
        index: int,
        item_data: Optional[Dict[str, Any]] = None,
        cluster_items: Optional[List[Dict[str, Any]]] = None,
        slot_size: int = 62,
        icon_size: int = 46,
        theme: str = "dark",
        open_mode: str = "double",
        display_mode: str = "classic",
        show_admin_badge: bool = True,
        show_running_badge: bool = True,
        parent=None
    ):
        super().__init__(parent)
        self.index = index
        self.item_data = item_data or {}
        self.cluster_items = cluster_items  # If provided, this slot acts as a 4-in-1 cluster!
        self.slot_size = slot_size
        self.icon_size = icon_size
        self.theme = theme
        self.open_mode = open_mode
        self.display_mode = display_mode
        self.show_admin_badge = show_admin_badge
        self.show_running_badge = show_running_badge

        self.is_hovered = False
        self.is_pressed = False
        self.drag_start_pos = QPoint()
        self._cached_pixmap = None
        self._scaled_pixmap = None
        self._scaled_size = -1
        self._cluster_pixmaps = None
        self._cluster_pix_size = -1

        # Dynamic tactile spring scale and squircle ambient glow
        self.scale_factor: float = 1.0
        self.glow_alpha: float = 0.0

        self.scale_anim = QVariantAnimation(self)
        self.scale_anim.valueChanged.connect(self._on_scale_anim_tick)

        self.glow_anim = QVariantAnimation(self)
        self.glow_anim.valueChanged.connect(self._on_glow_anim_tick)

        # High-performance path caching: eliminates rebuilding bezier curves on every animation tick
        self._cached_back_path = None
        self._cached_back_size = (0.0, 0.0)
        self._cached_cluster_path = None
        self._cached_cluster_size = (0.0, 0.0)

        self.setFixedSize(slot_size, slot_size)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_tooltip()

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

    def set_theme(self, theme: str):
        self.theme = theme
        self.update()

    def update_tooltip(self):
        if self.cluster_items:
            total_overflow = len(self.cluster_items)
            self.setToolTip(f"<div style='font-family: Segoe UI, PingFang SC, Microsoft YaHei;'><b>更多应用...</b><br><span style='color:#66b3ff;'>点击展开全能启动台 ({total_overflow} 项)</span></div>")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.item_data and self.item_data.get("path"):
            name = self.item_data.get("name", "未命名应用")
            path = self.item_data.get("path", "")
            self.setToolTip(f"<div style='font-family: Segoe UI, PingFang SC, Microsoft YaHei;'><b>{name}</b><br><span style='color:#aaa; font-size:11px;'>{path}</span></div>")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setToolTip("<div style='font-family: Segoe UI, PingFang SC, Microsoft YaHei; font-size:12px;'>点击展开大文件夹 · 按住拖拽移动</div>")
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = float(self.width())
        h = float(self.height())

        # ==========================================================
        # Case A: 4-in-1 Mini Cluster (Mobile Overflow Stack)
        # ==========================================================
        if self.cluster_items:
            pad = 2.0
            if self._cached_cluster_path is None or self._cached_cluster_size != (w, h):
                rect = QRectF(pad, pad, w - pad * 2, h - pad * 2)
                self._cached_cluster_path = create_squircle_path(rect, 15)
                self._cached_cluster_size = (w, h)
            path = self._cached_cluster_path

            # Ambient squircle bloom on hover
            if self.glow_alpha > 0.01:
                glow_grad = QRadialGradient(w / 2.0, h / 2.0, w * 0.58)
                glow_grad.setColorAt(0.0, QColor(100, 180, 255, int(45 * self.glow_alpha)))
                glow_grad.setColorAt(0.7, QColor(255, 255, 255, int(20 * self.glow_alpha)))
                glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.fillRect(self.rect(), glow_grad)

            if self.is_pressed:
                painter.fillPath(path, QColor(255, 255, 255, 65))
            elif self.is_hovered:
                painter.fillPath(path, QColor(255, 255, 255, 48))
                rim_pen = QPen(QColor(255, 255, 255, 95), 1.0)
                painter.strokePath(path, rim_pen)
            else:
                painter.fillPath(path, QColor(255, 255, 255, 26))
                rim_pen = QPen(QColor(255, 255, 255, 45), 0.8)
                painter.strokePath(path, rim_pen)

            show_label = getattr(self, "display_mode", "classic") != "large_icon" and (h >= 36)
            if show_label:
                base_mini_sz = 17.0
                mini_size = max(14, int(base_mini_sz * self.scale_factor))
                spacing = 2.0
                offset_x = (w - (mini_size * 2 + spacing)) / 2.0
                offset_y = 3.5
            else:
                base_mini_sz = 22.0
                mini_size = max(16, int(base_mini_sz * self.scale_factor))
                spacing = 2.0
                offset_x = (w - (mini_size * 2 + spacing)) / 2.0
                offset_y = (h - (mini_size * 2 + spacing)) / 2.0

            coords = [
                (offset_x, offset_y),
                (offset_x + mini_size + spacing, offset_y),
                (offset_x, offset_y + mini_size + spacing),
                (offset_x + mini_size + spacing, offset_y + mini_size + spacing),
            ]
            # 缩略图只在尺寸变化时提取一次，绘制阶段不再同步取图标/缩放
            if self._cluster_pixmaps is None or self._cluster_pix_size != mini_size:
                self._cluster_pixmaps = []
                for c_item in self.cluster_items[:4]:
                    pix = get_app_icon_pixmap(
                        c_item.get("path", ""), c_item.get("name", ""),
                        target_size=48, custom_icon_path=c_item.get("custom_icon", ""))
                    self._cluster_pixmaps.append(
                        pix.scaled(mini_size, mini_size,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                        if pix and not pix.isNull() else None)
                self._cluster_pix_size = mini_size

            for idx, (p_x, p_y) in enumerate(coords[:len(self._cluster_pixmaps)]):
                sp = self._cluster_pixmaps[idx]
                if sp is None:
                    continue
                px = p_x + (mini_size - sp.width()) / 2.0
                py = p_y + (mini_size - sp.height()) / 2.0
                painter.drawPixmap(int(px), int(py), sp)

            if show_label:
                font = QFont("Segoe UI Variable Display", 8, QFont.Weight.DemiBold)
                font.setFamilies(["Segoe UI Variable Display", "Microsoft YaHei UI", "Segoe UI"])
                painter.setFont(font)
                fm = painter.fontMetrics()
                overflow_text = f"+{len(self.cluster_items)}"
                bw = min(w - 2, fm.horizontalAdvance(overflow_text) + 12)
                bh = fm.height() + 2
                badge = QRectF((w - bw) / 2.0, h - bh - 1, bw, bh)
                badge_path = create_squircle_path(badge, bh / 2.0)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.fillPath(badge_path, QColor(16, 18, 24, 210))
                painter.setPen(QColor(255, 255, 255, 220))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, overflow_text)

            painter.end()
            return

        # ==========================================================
        # Case B: Standard Single App Slot
        # ==========================================================
        path_str = self.item_data.get("path", "") if self.item_data else ""
        if path_str:
            # 1. Ambient squircle bloom glowing behind the icon on hover/press
            if self.glow_alpha > 0.01 or self.is_pressed:
                glow_grad = QRadialGradient(w / 2.0, h / 2.0, w * 0.58)
                if self.theme == "light":
                    glow_grad.setColorAt(0.0, QColor(0, 110, 255, int(45 * self.glow_alpha)))
                    glow_grad.setColorAt(0.65, QColor(255, 255, 255, int(50 * self.glow_alpha)))
                    glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                else:
                    glow_grad.setColorAt(0.0, QColor(90, 175, 255, int(60 * self.glow_alpha)))
                    glow_grad.setColorAt(0.65, QColor(255, 255, 255, int(35 * self.glow_alpha)))
                    glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.fillRect(self.rect(), glow_grad)

            # 2. Delicate translucent squircle backplate (cached path)
            if self._cached_back_path is None or self._cached_back_size != (w, h):
                back_rect = QRectF(2, 2, w - 4, h - 4)
                self._cached_back_path = create_squircle_path(back_rect, 16)
                self._cached_back_size = (w, h)
            back_path = self._cached_back_path
            if self.is_pressed:
                painter.fillPath(back_path, QColor(255, 255, 255, 42))
                painter.strokePath(back_path, QPen(QColor(255, 255, 255, 75), 0.8))
            elif self.glow_alpha > 0.01:
                grad = QLinearGradient(0, 0, 0, h)
                grad.setColorAt(0.0, QColor(255, 255, 255, int(42 * self.glow_alpha)))
                grad.setColorAt(1.0, QColor(255, 255, 255, int(16 * self.glow_alpha)))
                painter.fillPath(back_path, grad)
                painter.strokePath(back_path, QPen(QColor(255, 255, 255, int(60 * self.glow_alpha)), 0.8))

            # 3. Dynamic Tactile spring scaling (with label vs large icon support)
            name = self.item_data.get("name", "")
            show_label = getattr(self, "display_mode", "classic") != "large_icon" and (h >= 36) and bool(name)

            if show_label:
                # Classic mode: icon + readable app name label below
                base_sz = min(int(self.slot_size * 0.52), int(self.icon_size * 0.74))
                target_size = int(base_sz * self.scale_factor)
                target_size = max(16, min(target_size, int(w - 6)))
                x = (w - target_size) / 2.0
                y = 3.5
            else:
                # Large icon mode: immersive centered icon
                base_sz = self.icon_size
                if getattr(self, "display_mode", "classic") == "large_icon":
                    base_sz = min(self.slot_size - 4, int(self.icon_size * 1.20))
                target_size = int(base_sz * self.scale_factor)
                target_size = max(16, min(target_size, int(w - 2)))
                x = (w - target_size) / 2.0
                y = (h - target_size) / 2.0

            # Render Authentic App Icon
            if self._cached_pixmap is None or self._cached_pixmap.isNull():
                custom_icon = self.item_data.get("custom_icon", "")
                self._cached_pixmap = get_app_icon_pixmap(path_str, name, target_size=128, custom_icon_path=custom_icon)
            pixmap = self._cached_pixmap
            if pixmap and not pixmap.isNull():
                if self._scaled_pixmap is None or self._scaled_size != target_size:
                    self._scaled_pixmap = pixmap.scaled(
                        target_size, target_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
                    self._scaled_size = target_size
                sp = self._scaled_pixmap
                px = x + (target_size - sp.width()) / 2.0
                py = y + (target_size - sp.height()) / 2.0
                painter.drawPixmap(int(px), int(py), sp)

            # Smart Badges: Admin Shield Badge
            if getattr(self, "show_admin_badge", True):
                lower_p = path_str.lower()
                is_admin_app = any(k in lower_p for k in ["regedit", "taskmgr", "cmd.exe", "powershell", "terminal", "mmc.exe"])
                if is_admin_app:
                    bx = int(x + target_size - 13)
                    by = int(y + target_size - 13)
                    shield_rect = QRectF(bx, by, 13, 13)
                    shield_path = create_squircle_path(shield_rect, 3.5)
                    painter.fillPath(shield_path, QColor(245, 158, 11, 230))
                    painter.strokePath(shield_path, QPen(QColor(255, 255, 255, 200), 1.0))

            # 4. App Name Label underneath the icon (with text shadow for high-contrast legibility)
            if show_label and name:
                font = QFont("Microsoft YaHei UI", 9, QFont.Weight.Medium)
                font.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Segoe UI Variable Text", "Segoe UI"])
                font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
                painter.setFont(font)
                fm = painter.fontMetrics()
                label_text = _wrap_label_text(fm, name, w - 2)
                ty = y + target_size + 0.8
                th = max(float(fm.height()), h - ty)
                # 高度放不下两行时改用单行省略号，避免第二行被硬裁成半行
                if th < 2 * fm.height():
                    label_text = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(w - 4))
                label_rect = QRectF(1, ty, w - 2, th)

                # 只画一遍前景，不再叠 0.8px 阴影（小字上会形成糊边）
                # High-contrast crisp foreground text (keep clean uniform white/dark to prevent confusing blue text on hover)
                if self.theme == "light":
                    fore_color = QColor(15, 23, 42)
                else:
                    fore_color = QColor(255, 255, 255, 255) if self.is_hovered else QColor(255, 255, 255, 245)
                painter.setPen(fore_color)
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label_text)

        else:
            # ==========================================================
            # Case C: Empty Slot (Pure Clean Mobile Glass Well)
            # ==========================================================
            # Smooth receptive glow illuminates only when hovering / dragging over
            if self.glow_alpha > 0.01:
                well_rect = QRectF(4, 4, w - 8, h - 8)
                well_path = create_squircle_path(well_rect, 14)
                painter.fillPath(well_path, QColor(255, 255, 255, int(22 * self.glow_alpha)))
                painter.strokePath(well_path, QPen(QColor(255, 255, 255, int(48 * self.glow_alpha)), 1.0))

        painter.end()

    def enterEvent(self, event):
        self.is_hovered = True
        self._animate_scale(1.08, 160, QEasingCurve.Type.OutCubic)
        self._animate_glow(1.0, 180, QEasingCurve.Type.OutCubic)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.is_hovered = False
        self.is_pressed = False
        self._animate_scale(1.0, 200, QEasingCurve.Type.OutCubic)
        self._animate_glow(0.0, 200, QEasingCurve.Type.OutCubic)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if not self.cluster_items and (not self.item_data or not self.item_data.get("path")):
            # Empty slot: ignore event so parent BigFolderWindow receives it for window dragging
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_pressed = True
            self.drag_start_pos = event.pos()
            self._animate_scale(0.92, 100, QEasingCurve.Type.OutQuad)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.cluster_items and (not self.item_data or not self.item_data.get("path")):
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.is_pressed:
            self.is_pressed = False
            target_scale = 1.08 if self.is_hovered else 1.0
            self._animate_scale(target_scale, 220, QEasingCurve.Type.OutBack)
            if (event.pos() - self.drag_start_pos).manhattanLength() < 6:
                if self.cluster_items:
                    self.request_open_drawer.emit()
                elif self.item_data and self.item_data.get("path"):
                    if self.open_mode == "single":
                        self.launch_app()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.cluster_items and (not self.item_data or not self.item_data.get("path")):
                # Empty slot: double-clicking opens the folder drawer (never pop up file picker!)
                self.request_open_drawer.emit()
                event.accept()
                return
            elif self.cluster_items:
                self.request_open_drawer.emit()
                event.accept()
                return
            elif self.item_data and self.item_data.get("path"):
                if self.open_mode == "double":
                    self.launch_app()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if not self.cluster_items and (not self.item_data or not self.item_data.get("path")):
            event.ignore()
            return
        if self.cluster_items:
            return  # Clusters are not draggable directly
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self.item_data or not self.item_data.get("path"):
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < 10:
            return

        path = self.item_data.get("path", "")
        real_path = resolve_actual_path(path)
        if not real_path or not os.path.exists(real_path):
            real_path = path

        win = self.window()
        source_folder = win.get_current_folder_name() if hasattr(win, "get_current_folder_name") else ""
        source_inst = getattr(win, "instance_id", "")

        drag = QDrag(self)
        mime = QMimeData()

        # 封装全栈跨文件夹移动 payload (消除跨文件夹拖拽丢失)
        import json
        import uuid
        drag_token = uuid.uuid4().hex
        payload = {
            "source_folder": source_folder,
            "source_instance_id": source_inst,
            "source_index": self.index,
            "item_data": self.item_data,
            "drag_token": drag_token
        }
        mime.setData(STACKY_ITEM_MIME, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        mime.setData("application/x-bigfolder-slot", str(self.index).encode("utf-8"))
        if real_path and os.path.exists(real_path):
            mime.setUrls([QUrl.fromLocalFile(os.path.abspath(real_path))])
        drag.setMimeData(mime)

        custom_icon = self.item_data.get("custom_icon", "")
        pix = get_app_icon_pixmap(real_path, self.item_data.get("name", ""), 64, custom_icon_path=custom_icon)
        if pix:
            drag.setPixmap(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            drag.setHotSpot(QPoint(24, 24))

        self.is_pressed = False
        self._animate_scale(1.0, 150, QEasingCurve.Type.OutCubic)

        if hasattr(win, "_internal_slot_drag_handled"):
            win._internal_slot_drag_handled = False

        action = drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

        was_internal = (
            consume_cross_folder_drop(drag_token)
            or getattr(win, "_internal_slot_drag_handled", False)
        )
        if hasattr(win, "_internal_slot_drag_handled"):
            win._internal_slot_drag_handled = False

        if not was_internal:
            # Dropped outside this folder window (e.g. onto Windows Desktop or Explorer)
            is_outside = not win.geometry().contains(QCursor.pos()) if hasattr(win, "geometry") else True
            if action != Qt.DropAction.IgnoreAction or is_outside:
                if hasattr(win, "on_request_move_to_desktop"):
                    win.on_request_move_to_desktop(real_path)

    def dragEnterEvent(self, event):
        win = self.window()
        if hasattr(win, "start_drag_hover_timer") and is_drop_acceptable(event.mimeData()):
            win.start_drag_hover_timer(event.mimeData())
        if is_internal_item_drag(event.mimeData()) or event.mimeData().hasFormat("application/x-bigfolder-slot") or is_drop_acceptable(event.mimeData()):
            self.is_hovered = True
            self._animate_glow(1.0, 160, QEasingCurve.Type.OutCubic)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.is_hovered = False
        self._animate_glow(0.0, 180, QEasingCurve.Type.OutCubic)
        win = self.window()
        if hasattr(win, "check_drag_leave"):
            win.check_drag_leave()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.is_hovered = False
        self._animate_glow(0.0, 180, QEasingCurve.Type.OutCubic)
        win = self.window()
        if hasattr(win, "cancel_drag_hover_timer"):
            win.cancel_drag_hover_timer()

        # 优先处理结构化拖拽 payload (支持跨文件夹转移与同文件夹重排)
        if event.mimeData().hasFormat(STACKY_ITEM_MIME):
            try:
                import json
                raw_bytes = bytes(event.mimeData().data(STACKY_ITEM_MIME))
                payload = json.loads(raw_bytes.decode("utf-8"))
                mark_cross_folder_drop(payload.get("drag_token", ""))
                src_folder = payload.get("source_folder")
                curr_folder = win.get_current_folder_name() if hasattr(win, "get_current_folder_name") else ""
                if src_folder and curr_folder and src_folder != curr_folder:
                    # 跨文件夹卡片拖拽
                    if hasattr(win, "handle_cross_folder_drop"):
                        if win.handle_cross_folder_drop(payload, target_slot=self.index):
                            event.acceptProposedAction()
                            return
                elif src_folder == curr_folder:
                    # 本文件夹内部槽位互换
                    if win:
                        win._internal_slot_drag_handled = True
                    src_index = payload.get("source_index", -1)
                    if src_index != -1 and src_index != self.index:
                        self.item_moved.emit(src_index, self.index)
                    event.acceptProposedAction()
                    return
            except Exception as e:
                pass

        if event.mimeData().hasFormat("application/x-bigfolder-slot"):
            if win:
                win._internal_slot_drag_handled = True
            src_index_str = event.mimeData().data("application/x-bigfolder-slot").data().decode("utf-8")
            src_index = int(src_index_str)
            if src_index != self.index:
                self.item_moved.emit(src_index, self.index)
            event.acceptProposedAction()
            return

        local_files, web_bookmarks = parse_dropped_mime_data(event.mimeData())
        if web_bookmarks:
            if hasattr(win, "handle_dropped_web_bookmarks"):
                win.handle_dropped_web_bookmarks(web_bookmarks, target_slot=self.index)
            else:
                self.item_changed.emit(self.index, web_bookmarks[0])
                self.status_toast.emit(f"已收纳网页书签: {web_bookmarks[0].get('name')}", False)
            event.acceptProposedAction()
            return

        if local_files:
            if hasattr(win, "handle_dropped_files"):
                win.handle_dropped_files(local_files, target_slot=self.index)
            else:
                name = os.path.splitext(os.path.basename(local_files[0]))[0]
                new_item = {"name": name, "path": local_files[0], "args": ""}
                self.item_changed.emit(self.index, new_item)
                self.status_toast.emit(f"已收纳: {name}", False)
            event.acceptProposedAction()
            return

        event.ignore()

    def launch_app(self):
        path = self.item_data.get("path", "")
        if not path:
            return

        real_p = resolve_actual_path(path).strip('"')
        args = self.item_data.get("args", "").strip()
        try:
            url_target = resolve_url_shortcut(real_p)
            if url_target:
                os.startfile(url_target)
            elif real_p.startswith("http://") or real_p.startswith("https://"):
                os.startfile(real_p)
            elif args:
                subprocess.Popen(f'"{real_p}" {args}', shell=True)
            elif os.path.exists(real_p):
                os.startfile(real_p)
            else:
                subprocess.Popen(path, shell=True)
            self.item_launched.emit(self.item_data.get("name", ""))
        except Exception as e:
            print("Failed to launch application:", e)
            self.status_toast.emit(f"启动失败: {e}", True)

    def launch_as_admin(self):
        path = self.item_data.get("path", "")
        args = self.item_data.get("args", "")
        if not path:
            return
        app_name = self.item_data.get("name", "应用")
        if run_as_admin(path, args):
            self.status_toast.emit(f"已以管理员身份启动: {app_name}", False)
        else:
            self.status_toast.emit(f"以管理员身份启动失败: {app_name}", True)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
        QMenu {
            background-color: rgba(24, 26, 34, 248);
            color: #f5f5f5;
            border: 1px solid rgba(255, 255, 255, 45);
            border-radius: 12px;
            padding: 6px;
            font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei";
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

        # Case 1: 4-in-1 Cluster slot
        if self.cluster_items:
            total_overflow = len(self.cluster_items)
            act_drawer = menu.addAction(f"展开全景启动台 (更多应用 {total_overflow} 项)...")
            act_drawer.triggered.connect(self.request_open_drawer.emit)
            menu.addSeparator()
            act_global = menu.addAction("设置")
            act_global.triggered.connect(lambda: self.request_global_menu.emit(event.globalPos()))
            menu.exec(event.globalPos())
            event.accept()
            return

        # Case 2: Empty slot
        if not self.item_data or not self.item_data.get("path"):
            act_add = menu.addAction("添加应用")
            act_add.triggered.connect(lambda: self.request_add.emit(self.index))
            act_add_web = menu.addAction("添加网页")
            act_add_web.triggered.connect(lambda: self.request_add_web.emit(self.index))
            act_drawer = menu.addAction("展开")
            act_drawer.triggered.connect(self.request_open_drawer.emit)
            menu.addSeparator()
            act_global = menu.addAction("设置")
            act_global.triggered.connect(lambda: self.request_global_menu.emit(event.globalPos()))
            menu.exec(event.globalPos())
            event.accept()
            return

        # Case 3: Standard single app slot
        app_name = self.item_data.get("name", "应用")
        item_path = self.item_data.get("path", "")
        is_web = (
            self.item_data.get("type") == "web"
            or item_path.startswith("http://")
            or item_path.startswith("https://")
            or item_path.lower().endswith(".url")
        )

        if is_web:
            act_open = menu.addAction(f"在浏览器中打开 {app_name}")
            act_open.triggered.connect(self.launch_app)

            act_copy_url = menu.addAction("复制网址")
            act_copy_url.triggered.connect(self.action_copy_path)

            act_edit_web = menu.addAction("编辑")
            act_edit_web.triggered.connect(self.action_edit_web_bookmark)

            menu.addSeparator()

            self._build_subgroup_submenu(menu)

            act_custom_icon = menu.addAction("自定义图标")
            act_custom_icon.triggered.connect(self.action_custom_icon)

            menu.addSeparator()
            act_del = menu.addAction("移出视图")
            act_del.triggered.connect(lambda: self.item_removed.emit(self.index))

            menu.addSeparator()
            act_global = menu.addAction("设置")
            act_global.triggered.connect(lambda: self.request_global_menu.emit(event.globalPos()))

        else:
            act_open = menu.addAction(f"打开 {app_name}")
            act_open.triggered.connect(self.launch_app)

            act_admin = menu.addAction("管理员运行")
            act_admin.triggered.connect(self.launch_as_admin)

            act_location = menu.addAction("打开目录")
            act_location.triggered.connect(self.open_file_location)

            menu.addSeparator()

            self._build_subgroup_submenu(menu)

            act_rename = menu.addAction("重命名")
            act_rename.triggered.connect(self.action_rename_item)

            act_custom_icon = menu.addAction("自定义图标")
            act_custom_icon.triggered.connect(self.action_custom_icon)

            act_copy_path = menu.addAction("复制路径")
            act_copy_path.triggered.connect(self.action_copy_path)

            act_props = menu.addAction("属性")
            act_props.triggered.connect(self.action_show_properties)

            menu.addSeparator()

            act_to_desktop = menu.addAction("放回桌面")
            act_to_desktop.triggered.connect(lambda: self.request_move_to_desktop.emit(item_path))

            act_del = menu.addAction("移出视图")
            act_del.triggered.connect(lambda: self.item_removed.emit(self.index))

            act_recycle = menu.addAction("移入回收站")
            act_recycle.triggered.connect(lambda: self.request_recycle.emit(item_path))

            menu.addSeparator()

            act_global = menu.addAction("设置")
            act_global.triggered.connect(lambda: self.request_global_menu.emit(event.globalPos()))

        menu.exec(event.globalPos())
        event.accept()

    def _build_subgroup_submenu(self, menu: QMenu):
        sg_menu = menu.addMenu("子分组")
        curr_sg = (self.item_data.get("subgroup") or "").strip()

        act_none = sg_menu.addAction("全部")
        if not curr_sg:
            act_none.setText("全部 / 默认（无子分组）")
        act_none.triggered.connect(lambda: self.action_assign_subgroup(""))

        known_sgs = []
        win = self.window()
        if hasattr(win, "get_current_subgroups"):
            known_sgs = win.get_current_subgroups()

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
        updated = dict(self.item_data)
        updated["subgroup"] = new_sg
        self.item_changed.emit(self.index, updated)
        self.status_toast.emit(f"已归入子分组: {new_sg or '全部/默认'}", False)

    def action_create_and_assign_subgroup(self):
        new_name, ok = QInputDialog.getText(self, "新建子分组", "请输入子分组名称:")
        if ok and new_name.strip():
            self.action_assign_subgroup(new_name.strip())

    def action_edit_web_bookmark(self):
        curr_url = self.item_data.get("path", "")
        curr_name = self.item_data.get("name", "")
        curr_sg = self.item_data.get("subgroup", "网页书签")
        known_sgs = []
        win = self.window()
        if hasattr(win, "get_current_subgroups"):
            known_sgs = win.get_current_subgroups()
        dlg = AddWebBookmarkDialog(
            self,
            initial_url=curr_url,
            initial_name=curr_name,
            initial_subgroup=curr_sg,
            existing_subgroups=known_sgs
        )
        if dlg.exec():
            bm = dlg.get_bookmark_data()
            if bm:
                self.item_changed.emit(self.index, bm)
                self.status_toast.emit(f"已更新网页书签: {bm.get('name')}", False)

    def open_file_location(self):
        path = self.item_data.get("path", "")
        target = resolve_shortcut_target(path)
        if target and os.path.exists(target):
            subprocess.Popen(f'explorer /select,"{target}"')
        elif os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            self.status_toast.emit("目标路径不存在", True)

    def action_rename_item(self):
        curr_name = self.item_data.get("name", "")
        new_name, ok = QInputDialog.getText(self, "重命名应用", "请输入应用显示名称:", text=curr_name)
        if ok and new_name.strip():
            updated = dict(self.item_data)
            updated["name"] = new_name.strip()
            self.item_changed.emit(self.index, updated)
            self.status_toast.emit(f"已重命名为: {new_name.strip()}", False)

    def action_custom_icon(self):
        curr_name = self.item_data.get("name", "应用")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"为【{curr_name}】选择自定义图标",
            "",
            "图标与图片文件 (*.ico *.png *.jpg *.jpeg *.bmp *.exe *.dll);;所有文件 (*.*)"
        )
        if file_path:
            updated = dict(self.item_data)
            updated["custom_icon"] = file_path
            self.item_changed.emit(self.index, updated)
            self.status_toast.emit(f"已更新【{curr_name}】的自定义图标", False)
            self.update()

    def action_copy_path(self):
        path = resolve_actual_path(self.item_data.get("path", ""))
        if path:
            QApplication.clipboard().setText(path)
            self.status_toast.emit("已复制文件路径到剪贴板", False)

    def action_show_properties(self):
        path = self.item_data.get("path", "")
        if path and not show_file_properties(path):
            self.status_toast.emit("无法打开文件属性", True)


class FolderTitleLabel(QLabel):
    """
    Title label displayed strictly centered directly below the card.
    Pure mobile desktop typography:
    - No ugly solid pill / memo container.
    - Crisp white font with Gaussian ambient drop shadow, floating on the wallpaper.
    - Hover reveals an ultra-delicate frosted glow capsule.
    """
    request_menu = Signal(QPoint)
    request_click = Signal(QPoint)
    request_open_drawer = Signal()

    _QSS = """
        QLabel {{
            color: #ffffff;
            font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI";
            font-size: {fs}px;
            font-weight: 500;
            background-color: transparent;
            padding: {pv}px {ph}px;
            border-radius: {radius}px;
            letter-spacing: 0.3px;
        }}
        QLabel:hover {{
            background-color: rgba(255, 255, 255, 30);
            border: 1px solid rgba(255, 255, 255, 45);
        }}
    """

    def __init__(self, text: str = "常用应用", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_layout_scale(1.0)

    def set_layout_scale(self, s: float):
        """标题随桌面比例缩放，否则小屏下固定字号会把窗口撑得比卡片还宽。"""
        self.setStyleSheet(self._QSS.format(
            fs=max(8, int(round(13 * s))),
            pv=max(1, int(round(2 * s))),
            ph=max(2, int(round(14 * s))),
            radius=max(4, int(round(11 * s)))))
        self.setMinimumHeight(max(12, int(round(24 * s))))
        self.adjustSize()

    def paintEvent(self, event):
        # 亮壁纸下纯白标题几乎不可见：自绘 1px 深色投影 + 白色前景（不是多层重影）
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # 常驻深色胶囊：亮壁纸下白字才有对照，hover 时再提亮
        hover = self.underMouse()
        painter.setPen(QPen(QColor(255, 255, 255, 60 if hover else 26), 1.0))
        painter.setBrush(QColor(0, 0, 0, 132 if hover else 104))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 11, 11)
        text = self.text()
        if not text:
            return
        painter.setPen(QColor(0, 0, 0, 170))
        painter.drawText(self.rect().adjusted(0, 1, 0, 1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(255, 255, 255, 250))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def set_text(self, text: str):
        self.setToolTip(text)
        self.setText(text)
        self.adjustSize()


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.request_click.emit(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.request_open_drawer.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.request_menu.emit(event.globalPos())


class BigFolderCard(QWidget):
    """
    Mobile-grade 2x2 Squircle Liquid Frosted Glass Plate.
    Adheres strictly to modern widget aesthetics:
    - 1:1 Pure Square Squircle proportion.
    - True Translucent Liquid Acrylic: no muddy grey/blue opaque fills!
    - Dual Fresnel Specular Rim (0.8px top-left micro-reflection, bottom-right ambient occlusion).
    - Soft, air-cushioned diffuse drop shadow (32px blur).
    """
    wheel_scrolled = Signal(int)
    blank_double_clicked = Signal()

    def __init__(self, radius: int = 30, opacity: float = 0.88, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.radius = radius
        self.opacity = max(0.05, min(1.0, float(opacity)))
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Drop shadow is drawn cleanly by parent BigFolderWindow with QPainter
        # to prevent QGraphicsDropShadowEffect raster cache invalidation issues.

    def mousePressEvent(self, event):
        # Ignore event so BigFolderWindow handles unified Click-vs-Drag
        event.ignore()

    def mouseMoveEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            if child is None or child == self:
                self.blank_double_clicked.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta != 0:
            self.wheel_scrolled.emit(delta)
            event.accept()
            return
        super().wheelEvent(event)

    def set_card_style(self, radius: int, opacity: float, theme: str = None):
        self.radius = radius
        self.opacity = max(0.05, min(1.0, float(opacity)))
        if theme:
            self.theme = theme
        self._bg_cache = None
        self.update()

    def _render_background(self, key):
        """尺寸/主题变化时才重画一次，绘制阶段只做一次 drawPixmap。"""
        pix = QPixmap(self.size())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        path = create_squircle_path(QRectF(1.0, 1.0, w - 2.0, h - 2.0), self.radius)
        op = self.opacity
        grad = QLinearGradient(0, 0, 0, h)
        # 玻璃底板：低 alpha 才能透出 Wallpaper Engine 动态壁纸，透明度由设置页滑块控制
        if self.theme == "light":
            grad.setColorAt(0.0, QColor(250, 251, 253, int(150 * op)))
            grad.setColorAt(1.0, QColor(236, 239, 244, int(145 * op)))
            edge = QColor(255, 255, 255, int(120 * op))
        elif self.theme == "oled":
            grad.setColorAt(0.0, QColor(18, 19, 23, int(150 * op)))
            grad.setColorAt(1.0, QColor(8, 9, 11, int(155 * op)))
            edge = QColor(255, 255, 255, int(20 * op))
        else:
            grad.setColorAt(0.0, QColor(38, 41, 48, int(150 * op)))
            grad.setColorAt(1.0, QColor(24, 26, 31, int(155 * op)))
            edge = QColor(255, 255, 255, int(28 * op))

        p.fillPath(path, grad)
        draw_liquid_glass(p, path, QRectF(1.0, 1.0, w - 2.0, h - 2.0), self.radius, self.theme)
        p.end()
        self._bg_cache = pix
        self._bg_key = key

    def paintEvent(self, event):
        key = (self.width(), self.height(), self.radius, round(self.opacity, 3), self.theme)
        if getattr(self, "_bg_cache", None) is None or getattr(self, "_bg_key", None) != key:
            self._render_background(key)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._bg_cache)
        painter.end()
