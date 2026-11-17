import os
import sys
import shlex
import subprocess
import ctypes
from contextlib import contextmanager
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

import icon_utils
from icon_utils import (
    get_app_icon_pixmap, resolve_actual_path, resolve_shortcut_target, resolve_url_shortcut,
    create_squircle_path, parse_dropped_mime_data, is_drop_acceptable,
    extract_site_name_from_url, is_internal_item_drag,
    mark_cross_folder_drop, consume_cross_folder_drop, STACKY_ITEM_MIME,
    draw_liquid_glass
)
import ui_kit
from web_dialog import AddWebBookmarkDialog


_FALLBACK_ADMIN_EXES = {"regedit.exe", "taskmgr.exe", "mmc.exe", "dism++.exe"}


def _is_drag_cancelled_by_user() -> bool:
    """Return True if an OLE drag ended because the user cancelled via Esc or Right-Click."""
    if sys.platform != "win32" or not hasattr(ctypes, "windll"):
        return False
    try:
        u32 = ctypes.windll.user32
        # Esc pressed, right mouse button pressed, or left button still physically held when DoDragDrop aborted
        if (u32.GetAsyncKeyState(0x1B) & 0x8000) or (u32.GetAsyncKeyState(0x02) & 0x8000):
            return True
        if u32.GetAsyncKeyState(0x01) & 0x8000:
            return True
    except Exception:
        pass
    return False


def _ui_font(px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont("Segoe UI Variable Text")
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(max(1, int(px)))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return font


def _clean_app_tooltip(name: str, path: str) -> str:
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


def _get_menu_qss(theme: str = "dark") -> str:
    fn = getattr(ui_kit, "menu_qss", None)
    if callable(fn):
        return fn(theme)
    if theme == "light":
        return """
        QMenu {
            background-color: rgba(248, 250, 252, 248);
            color: #0f172a;
            border: 1px solid rgba(15, 23, 42, 28);
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
            background-color: rgba(15, 23, 42, 18);
            color: #0f172a;
        }
        QMenu::separator {
            height: 1px;
            background-color: rgba(15, 23, 42, 20);
            margin: 4px 6px;
        }
        """
    return """
    QMenu {
        background-color: rgba(24, 26, 34, 248);
        color: #f5f5f7;
        border: 1px solid rgba(255, 255, 255, 42);
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
        background-color: rgba(255, 255, 255, 40);
        color: #ffffff;
    }
    QMenu::separator {
        height: 1px;
        background-color: rgba(255, 255, 255, 25);
        margin: 4px 6px;
    }
    """


def _is_admin_target(path_str: str) -> bool:
    if not path_str:
        return False
    checker = getattr(icon_utils, "has_lnk_run_as_admin", None)
    if callable(checker):
        try:
            return bool(checker(path_str))
        except Exception:
            pass
    base = os.path.basename(path_str.strip('"')).lower()
    if base in _FALLBACK_ADMIN_EXES:
        return True
    resolved = resolve_shortcut_target(path_str).strip('"')
    if resolved and os.path.basename(resolved).lower() in _FALLBACK_ADMIN_EXES:
        return True
    return False


def _paint_admin_shield(painter: QPainter, badge_rect: QRectF):
    drawer = getattr(icon_utils, "draw_admin_shield_badge", None)
    if callable(drawer):
        drawer(painter, badge_rect)
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x, y, w, h = badge_rect.x(), badge_rect.y(), badge_rect.width(), badge_rect.height()
    cx = x + w * 0.5
    shield = QPainterPath()
    shield.moveTo(cx, y + h * 0.06)
    shield.lineTo(x + w * 0.90, y + h * 0.22)
    shield.lineTo(x + w * 0.86, y + h * 0.54)
    shield.quadTo(x + w * 0.80, y + h * 0.82, cx, y + h * 0.95)
    shield.quadTo(x + w * 0.20, y + h * 0.82, x + w * 0.14, y + h * 0.54)
    shield.lineTo(x + w * 0.10, y + h * 0.22)
    shield.closeSubpath()

    painter.fillPath(shield, QColor(37, 99, 235, 245))
    painter.save()
    painter.setClipPath(shield)
    painter.fillRect(QRectF(cx, y, w * 0.5, h * 0.5), QColor(245, 158, 11, 245))
    painter.fillRect(QRectF(x, y + h * 0.5, cx - x, h * 0.5), QColor(245, 158, 11, 245))
    painter.restore()
    painter.strokePath(shield, QPen(QColor(255, 255, 255, 225), 1.0))
    painter.restore()


def _compute_clean_child_path(raw_path_env: str) -> str:
    """Strip PyInstaller _MEIPASS / _internal directories from PATH so launched 3rd-party apps never map Stacky's private DLLs (e.g. opengl32sw.dll, VCRUNTIME140.dll)."""
    if not getattr(sys, "frozen", False):
        return raw_path_env
    private_prefixes = []
    exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "executable", None) else ""
    meipass = getattr(sys, "_MEIPASS", "") or ""
    for candidate in (meipass, os.path.join(exe_dir, "_internal") if exe_dir else "", exe_dir):
        if candidate:
            norm = os.path.normcase(os.path.normpath(os.path.abspath(candidate)))
            private_prefixes.append(norm)
    if not private_prefixes:
        return raw_path_env
    kept = []
    for entry in (raw_path_env or "").split(os.pathsep):
        stripped = entry.strip().strip('"')
        if not stripped:
            continue
        norm_entry = os.path.normcase(os.path.normpath(stripped))
        if any(norm_entry == p or norm_entry.startswith(p + os.sep) for p in private_prefixes):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


@contextmanager
def _clean_child_env_context():
    if not getattr(sys, "frozen", False):
        yield
        return
    orig_path = os.environ.get("PATH", "")
    clean_path = _compute_clean_child_path(orig_path)
    if clean_path == orig_path:
        yield
        return
    try:
        os.environ["PATH"] = clean_path
        if hasattr(ctypes, "windll"):
            try:
                ctypes.windll.kernel32.SetEnvironmentVariableW("PATH", clean_path)
            except Exception:
                pass
        yield
    finally:
        os.environ["PATH"] = orig_path
        if hasattr(ctypes, "windll"):
            try:
                ctypes.windll.kernel32.SetEnvironmentVariableW("PATH", orig_path)
            except Exception:
                pass


def _shell_open_url_or_file(target: str, args: Optional[str] = None, workdir: Optional[str] = None) -> bool:
    if hasattr(ctypes, "windll"):
        try:
            res = ctypes.windll.shell32.ShellExecuteW(None, "open", target, args, workdir, 1)
            if res > 32:
                return True
        except Exception:
            pass
    return False


def safe_launch_target(raw_path: str, args: str = "") -> None:
    """Safely launch an application, shortcut, file, or URL via Win32 ShellExecuteW without shell=True."""
    if not raw_path:
        return
    real_p = resolve_actual_path(raw_path).strip('"')
    clean_args = (args or "").strip()

    with _clean_child_env_context():
        url_target = resolve_url_shortcut(real_p) if real_p else ""
        if url_target:
            if not _shell_open_url_or_file(url_target):
                os.startfile(url_target)
            return

        if real_p.startswith(("http://", "https://")) or "://" in real_p:
            if not _shell_open_url_or_file(real_p):
                os.startfile(real_p)
            return

        target = real_p if real_p else raw_path.strip('"')
        workdir = os.path.dirname(target) if os.path.isfile(target) else None
        if not workdir:
            workdir = None

        if _shell_open_url_or_file(target, clean_args or None, workdir):
            return

        if not clean_args and os.path.exists(target) and hasattr(os, "startfile"):
            os.startfile(target)
            return

        parsed_args = [a.strip('"') for a in shlex.split(clean_args, posix=False)] if clean_args else []
        subprocess.Popen([target, *parsed_args], cwd=workdir)

def run_as_admin(file_path: str, args: str = "") -> bool:
    """Executes target program with elevated Administrator privileges (Windows UAC prompt)"""
    target_path = resolve_shortcut_target(file_path).strip('\"')
    if not target_path or not os.path.exists(target_path):
        return False
    try:
        with _clean_child_env_context():
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
    request_open_settings = Signal()
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
        self._settings_signal_wired = False
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
            self.setToolTip(f"更多应用（{total_overflow} 项）")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.item_data and self.item_data.get("path"):
            name = self.item_data.get("name", "")
            path = self.item_data.get("path", "")
            self.setToolTip(_clean_app_tooltip(name, path))
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setToolTip("")
            self.setCursor(Qt.CursorShape.ArrowCursor)

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
            is_light = (self.theme == "light")
            glow = max(0.0, min(1.0, float(self.glow_alpha)))

            # Neutral luminance bloom on hover
            if glow > 0.01:
                glow_grad = QRadialGradient(w / 2.0, h / 2.0, w * 0.56)
                if is_light:
                    glow_grad.setColorAt(0.0, QColor(15, 23, 42, int(10 * glow)))
                    glow_grad.setColorAt(1.0, QColor(15, 23, 42, 0))
                else:
                    glow_grad.setColorAt(0.0, QColor(255, 255, 255, int(22 * glow)))
                    glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.fillRect(self.rect(), glow_grad)

            if is_light:
                if self.is_pressed:
                    painter.fillPath(path, QColor(15, 23, 42, 28))
                    painter.strokePath(path, QPen(QColor(15, 23, 42, 38), 0.9))
                elif self.is_hovered or glow > 0.01:
                    painter.fillPath(path, QColor(255, 255, 255, int(150 * max(0.4, glow))))
                    painter.strokePath(path, QPen(QColor(15, 23, 42, 26), 0.9))
                else:
                    painter.fillPath(path, QColor(15, 23, 42, 16))
                    painter.strokePath(path, QPen(QColor(15, 23, 42, 24), 0.8))
            else:
                if self.is_pressed:
                    painter.fillPath(path, QColor(255, 255, 255, 50))
                elif self.is_hovered or glow > 0.01:
                    grad = QLinearGradient(0, pad, 0, h - pad)
                    grad.setColorAt(0.0, QColor(255, 255, 255, 42))
                    grad.setColorAt(1.0, QColor(255, 255, 255, 20))
                    painter.fillPath(path, grad)
                    painter.strokePath(path, QPen(QColor(255, 255, 255, 58), 0.9))
                else:
                    painter.fillPath(path, QColor(255, 255, 255, 22))
                    painter.strokePath(path, QPen(QColor(255, 255, 255, 36), 0.8))

            show_label = getattr(self, "display_mode", "classic") != "large_icon" and (h >= 36)
            base_mini_sz = 17.0 if show_label else 22.0
            spacing = 2.0
            grid_span = base_mini_sz * 2.0 + spacing
            cluster_cx = w / 2.0
            cluster_cy = (3.5 + grid_span / 2.0) if show_label else (h / 2.0)

            # High-res thumbnails extracted once; sub-pixel floating-point transform avoids per-tick scaling
            if self._cluster_pixmaps is None:
                self._cluster_pixmaps = []
                for c_item in self.cluster_items[:4]:
                    pix = get_app_icon_pixmap(
                        c_item.get("path", ""), c_item.get("name", ""),
                        target_size=128, custom_icon_path=c_item.get("custom_icon", ""))
                    self._cluster_pixmaps.append(pix if pix and not pix.isNull() else None)

            half_step = (base_mini_sz + spacing) / 2.0
            cell_centers = [
                (-half_step, -half_step),
                (half_step, -half_step),
                (-half_step, half_step),
                (half_step, half_step),
            ]
            painter.save()
            painter.translate(cluster_cx, cluster_cy)
            painter.scale(self.scale_factor, self.scale_factor)
            for idx, (rcx, rcy) in enumerate(cell_centers[:len(self._cluster_pixmaps)]):
                sp = self._cluster_pixmaps[idx]
                if sp is None or sp.isNull():
                    continue
                pw, ph = float(sp.width()), float(sp.height())
                if pw <= 0 or ph <= 0:
                    continue
                ratio = min(base_mini_sz / pw, base_mini_sz / ph)
                dw, dh = pw * ratio, ph * ratio
                painter.drawPixmap(
                    QRectF(rcx - dw / 2.0, rcy - dh / 2.0, dw, dh),
                    sp,
                    QRectF(sp.rect()),
                )
            painter.restore()

            if show_label:
                painter.setFont(_ui_font(11, QFont.Weight.DemiBold))
                fm = painter.fontMetrics()
                overflow_text = f"+{len(self.cluster_items)}"
                bw = min(w - 2, fm.horizontalAdvance(overflow_text) + 12)
                bh = fm.height() + 2
                badge = QRectF((w - bw) / 2.0, h - bh - 1, bw, bh)
                badge_path = create_squircle_path(badge, bh / 2.0)
                if is_light:
                    painter.fillPath(badge_path, QColor(255, 255, 255, 232))
                    painter.strokePath(badge_path, QPen(QColor(15, 23, 42, 32), 0.8))
                    painter.setPen(QColor(15, 23, 42, 238))
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.fillPath(badge_path, QColor(16, 18, 24, 210))
                    painter.setPen(QColor(255, 255, 255, 225))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, overflow_text)

            painter.end()
            return

        # ==========================================================
        # Case B: Standard Single App Slot
        # ==========================================================
        path_str = self.item_data.get("path", "") if self.item_data else ""
        if path_str:
            is_light = (self.theme == "light")
            glow = max(0.0, min(1.0, float(self.glow_alpha)))
            # 1. Neutral subtle luminance bloom behind the icon on hover/press
            if glow > 0.01 or self.is_pressed:
                eff_glow = max(glow, 0.75) if self.is_pressed else glow
                glow_grad = QRadialGradient(w / 2.0, h / 2.0, w * 0.56)
                if is_light:
                    glow_grad.setColorAt(0.0, QColor(15, 23, 42, int(10 * eff_glow)))
                    glow_grad.setColorAt(1.0, QColor(15, 23, 42, 0))
                else:
                    glow_grad.setColorAt(0.0, QColor(255, 255, 255, int(22 * eff_glow)))
                    glow_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.fillRect(self.rect(), glow_grad)

            # 2. Delicate neutral frosted squircle backplate (cached path)
            if self._cached_back_path is None or self._cached_back_size != (w, h):
                back_rect = QRectF(2, 2, w - 4, h - 4)
                self._cached_back_path = create_squircle_path(back_rect, 16)
                self._cached_back_size = (w, h)
            back_path = self._cached_back_path
            if is_light:
                if self.is_pressed:
                    painter.fillPath(back_path, QColor(15, 23, 42, 20))
                    painter.strokePath(back_path, QPen(QColor(15, 23, 42, 30), 0.8))
                elif glow > 0.01:
                    painter.fillPath(back_path, QColor(255, 255, 255, int(150 * glow)))
                    painter.strokePath(back_path, QPen(QColor(15, 23, 42, int(20 * glow)), 0.8))
            else:
                if self.is_pressed:
                    painter.fillPath(back_path, QColor(255, 255, 255, 36))
                    painter.strokePath(back_path, QPen(QColor(255, 255, 255, 56), 0.8))
                elif glow > 0.01:
                    grad = QLinearGradient(0, 2, 0, h - 2)
                    grad.setColorAt(0.0, QColor(255, 255, 255, int(34 * glow)))
                    grad.setColorAt(1.0, QColor(255, 255, 255, int(14 * glow)))
                    painter.fillPath(back_path, grad)
                    painter.strokePath(back_path, QPen(QColor(255, 255, 255, int(44 * glow)), 0.8))

            # 3. 120Hz Sub-Pixel Floating-Point Icon Scaling (zero per-tick pixmap.scaled staircase!)
            name = self.item_data.get("name", "")
            show_label = getattr(self, "display_mode", "classic") != "large_icon" and (h >= 36) and bool(name)

            if show_label:
                base_sz = float(min(self.slot_size * 0.52, self.icon_size * 0.74))
                base_sz = max(16.0, min(base_sz, w - 6.0))
                cx = w / 2.0
                cy = 3.5 + base_sz / 2.0
            else:
                base_sz = float(self.icon_size)
                if getattr(self, "display_mode", "classic") == "large_icon":
                    base_sz = min(float(self.slot_size - 4), float(self.icon_size) * 1.20)
                base_sz = max(16.0, min(base_sz, w - 2.0))
                cx = w / 2.0
                cy = h / 2.0

            # Render Authentic App Icon from cached 128x128 high-res pixmap
            if self._cached_pixmap is None or self._cached_pixmap.isNull():
                custom_icon = self.item_data.get("custom_icon", "")
                self._cached_pixmap = get_app_icon_pixmap(path_str, name, target_size=128, custom_icon_path=custom_icon)
            pixmap = self._cached_pixmap
            draw_w = draw_h = base_sz
            if pixmap and not pixmap.isNull():
                pw, ph = float(pixmap.width()), float(pixmap.height())
                if pw > 0 and ph > 0:
                    ratio = min(base_sz / pw, base_sz / ph)
                    draw_w = pw * ratio
                    draw_h = ph * ratio
                painter.save()
                painter.translate(cx, cy)
                painter.scale(self.scale_factor, self.scale_factor)
                painter.drawPixmap(
                    QRectF(-draw_w / 2.0, -draw_h / 2.0, draw_w, draw_h),
                    pixmap,
                    QRectF(pixmap.rect()),
                )
                painter.restore()

            # Smart Badges: Vector Two-Tone Admin Shield Badge
            if getattr(self, "show_admin_badge", True) and _is_admin_target(path_str):
                eff_w = draw_w * self.scale_factor
                eff_h = draw_h * self.scale_factor
                shield_sz = 13.0
                bx = cx + eff_w / 2.0 - shield_sz + 1.5
                by = cy + eff_h / 2.0 - shield_sz + 1.5
                _paint_admin_shield(painter, QRectF(bx, by, shield_sz, shield_sz))

            # 4. App Name Label underneath the icon
            if show_label and name:
                painter.setFont(_ui_font(12, QFont.Weight.Medium))
                fm = painter.fontMetrics()
                label_text = _wrap_label_text(fm, name, w - 2)
                ty = 3.5 + base_sz + 1.2
                th = max(float(fm.height()), h - ty)
                # 高度放不下两行时改用单行省略号，避免第二行被硬裁成半行
                if th < 2 * fm.height():
                    label_text = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(w - 4))
                label_rect = QRectF(1, ty, w - 2, th)

                if is_light:
                    fore_color = QColor(15, 23, 42)
                else:
                    fore_color = QColor(255, 255, 255, 255) if self.is_hovered else QColor(255, 255, 255, 245)
                painter.setPen(fore_color)
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label_text)

        else:
            # ==========================================================
            # Case C: Empty Slot (Pure Clean Mobile Glass Well)
            # ==========================================================
            if self.glow_alpha > 0.01:
                well_rect = QRectF(4, 4, w - 8, h - 8)
                well_path = create_squircle_path(well_rect, 14)
                if self.theme == "light":
                    painter.fillPath(well_path, QColor(15, 23, 42, int(14 * self.glow_alpha)))
                    painter.strokePath(well_path, QPen(QColor(15, 23, 42, int(28 * self.glow_alpha)), 1.0))
                else:
                    painter.fillPath(well_path, QColor(255, 255, 255, int(18 * self.glow_alpha)))
                    painter.strokePath(well_path, QPen(QColor(255, 255, 255, int(38 * self.glow_alpha)), 1.0))

        painter.end()

    def enterEvent(self, event):
        if not self.cluster_items and (not self.item_data or not self.item_data.get("path")):
            super().enterEvent(event)
            return
        self.is_hovered = True
        self._animate_scale(1.06, 160, QEasingCurve.Type.OutCubic)
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
            target_scale = 1.06 if self.is_hovered else 1.0
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
        if pix and not pix.isNull():
            dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
            px_sz = max(16, int(round(48 * dpr)))
            drag_pm = pix.scaled(px_sz, px_sz, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            drag_pm.setDevicePixelRatio(dpr)
            drag.setPixmap(drag_pm)
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
            if action == Qt.DropAction.IgnoreAction and _is_drag_cancelled_by_user():
                return
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
            try:
                src_index_str = bytes(event.mimeData().data("application/x-bigfolder-slot")).decode("utf-8")
                src_index = int(src_index_str)
            except (ValueError, UnicodeDecodeError):
                event.ignore()
                return
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

        args = self.item_data.get("args", "").strip()
        try:
            safe_launch_target(path, args)
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

    def _reset_host_press_state(self):
        self.is_pressed = False
        win = self.window()
        if win is not None and win is not self:
            if hasattr(win, "_reset_mouse_press_state"):
                win._reset_mouse_press_state()
            elif hasattr(win, "is_left_pressed"):
                win.is_left_pressed = False

    def _emit_open_settings(self):
        self._reset_host_press_state()
        self.request_open_settings.emit()
        win = self.window()
        if (
            not getattr(self, "_settings_signal_wired", False)
            and win is not None
            and win is not self
            and hasattr(win, "action_open_settings")
        ):
            win.action_open_settings(0)

    def build_context_menu(self) -> QMenu:
        self._reset_host_press_state()
        win = self.window()
        has_host = win is not None and win is not self

        # Case 1: 4-in-1 Cluster slot
        if self.cluster_items:
            menu = QMenu(self)
            menu.setStyleSheet(_get_menu_qss(self.theme))
            total_overflow = len(self.cluster_items)
            act_drawer = menu.addAction(f"展开全部（{total_overflow} 项）")
            act_drawer.triggered.connect(self.request_open_drawer.emit)
            if has_host and hasattr(win, "populate_card_manage_submenu"):
                card_sub = menu.addMenu("卡片选项")
                card_sub.setStyleSheet(_get_menu_qss(self.theme))
                win.populate_card_manage_submenu(card_sub)
            menu.addSeparator()
            act_settings = menu.addAction("设置")
            act_settings.triggered.connect(self._emit_open_settings)
            return menu

        # Case 2: Empty slot (visually blank card space -> unified card menu + Add App/Web at top)
        if not self.item_data or not self.item_data.get("path"):
            if has_host and hasattr(win, "create_global_menu"):
                menu = win.create_global_menu(
                    parent=self,
                    settings_callback=self._emit_open_settings,
                )
                first_act = menu.actions()[0] if menu.actions() else None
                act_add = QAction("添加应用", menu)
                act_add.triggered.connect(lambda: self.request_add.emit(self.index))
                act_add_web = QAction("添加网页", menu)
                act_add_web.triggered.connect(lambda: self.request_add_web.emit(self.index))
                if first_act is not None:
                    menu.insertAction(first_act, act_add)
                    menu.insertAction(first_act, act_add_web)
                    menu.insertSeparator(first_act)
                else:
                    menu.addAction(act_add)
                    menu.addAction(act_add_web)
                return menu

            menu = QMenu(self)
            menu.setStyleSheet(_get_menu_qss(self.theme))
            act_add = menu.addAction("添加应用")
            act_add.triggered.connect(lambda: self.request_add.emit(self.index))
            act_add_web = menu.addAction("添加网页")
            act_add_web.triggered.connect(lambda: self.request_add_web.emit(self.index))
            act_drawer = menu.addAction("展开")
            act_drawer.triggered.connect(self.request_open_drawer.emit)
            menu.addSeparator()
            act_settings = menu.addAction("设置")
            act_settings.triggered.connect(self._emit_open_settings)
            return menu

        # Case 3: Standard single app / web slot
        menu = QMenu(self)
        menu.setStyleSheet(_get_menu_qss(self.theme))
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
            if has_host and hasattr(win, "populate_card_manage_submenu"):
                card_sub = menu.addMenu("卡片选项")
                card_sub.setStyleSheet(_get_menu_qss(self.theme))
                win.populate_card_manage_submenu(card_sub)
            act_settings = menu.addAction("设置")
            act_settings.triggered.connect(self._emit_open_settings)

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

            if has_host and hasattr(win, "populate_card_manage_submenu"):
                card_sub = menu.addMenu("卡片选项")
                card_sub.setStyleSheet(_get_menu_qss(self.theme))
                win.populate_card_manage_submenu(card_sub)
            act_settings = menu.addAction("设置")
            act_settings.triggered.connect(self._emit_open_settings)

        return menu

    def contextMenuEvent(self, event):
        menu = self.build_context_menu()
        menu.exec(event.globalPos())
        event.accept()

    def _build_subgroup_submenu(self, menu: QMenu):
        sg_menu = menu.addMenu("子分组")
        sg_menu.setStyleSheet(_get_menu_qss(self.theme))
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
            None,
            initial_url=curr_url,
            initial_name=curr_name,
            initial_subgroup=curr_sg,
            existing_subgroups=known_sgs,
            theme=self.theme,
        )
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        if dlg.exec():
            bm = dlg.get_bookmark_data()
            if bm:
                self.item_changed.emit(self.index, bm)
                self.status_toast.emit(f"已更新网页书签: {bm.get('name')}", False)

    def open_file_location(self):
        path = self.item_data.get("path", "")
        target = resolve_shortcut_target(path)
        if target and os.path.exists(target):
            subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(target)}"])
        elif os.path.exists(path):
            subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(path)}"])
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
            self.item_data = updated
            self._cached_pixmap = None
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
    - Airy idle look: crisp 12px Medium Windows CJK font with soft drop shadow & subtle text halo.
    - Hover smoothly reveals a frosted glow capsule.
    """
    request_menu = Signal(QPoint)
    request_click = Signal(QPoint)
    request_open_drawer = Signal()

    _QSS = """
        QLabel {{
            color: #ffffff;
            font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
            font-size: {fs}px;
            font-weight: 500;
            background-color: transparent;
            padding: {pv}px {ph}px;
            border-radius: {radius}px;
        }}
    """

    def __init__(self, text: str = "常用应用", parent=None):
        super().__init__(text, parent)
        self._layout_scale: float = 1.0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover_alpha: float = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(150)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_tick)
        self.set_layout_scale(1.0)

    def _on_hover_tick(self, val):
        self._hover_alpha = float(val)
        self.update()

    def enterEvent(self, event):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_alpha)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_alpha)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def set_layout_scale(self, s: float):
        """标题随桌面比例缩放，否则小屏下固定字号会把窗口撑得比卡片还宽。"""
        self._layout_scale = float(s)
        px = max(8, int(round(12 * s)))
        self.setFont(_ui_font(px, QFont.Weight.Medium))
        self.setStyleSheet(self._QSS.format(
            fs=px,
            pv=max(1, int(round(2 * s))),
            ph=max(2, int(round(12 * s))),
            radius=max(4, int(round(11 * s)))))
        self.setMinimumHeight(max(12, int(round(22 * s))))
        self.adjustSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        s = getattr(self, "_layout_scale", 1.0)
        px = max(8, int(round(12 * s)))
        painter.setFont(_ui_font(px, QFont.Weight.Medium))

        hover_t = max(self._hover_alpha, 1.0 if self.underMouse() else 0.0)
        pill_rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        pill_radius = min(11.0, max(4.0, pill_rect.height() / 2.0))

        text = self.text()
        if hover_t > 0.01:
            painter.setPen(QPen(QColor(255, 255, 255, int(62 * hover_t)), 0.9))
            painter.setBrush(QColor(15, 20, 28, int(88 * hover_t)))
            painter.drawRoundedRect(pill_rect, pill_radius, pill_radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(22 * hover_t)))
            painter.drawRoundedRect(pill_rect, pill_radius, pill_radius)

        if not text:
            painter.end()
            return

        base_rect = QRectF(self.rect())
        painter.setPen(QColor(0, 0, 0, 55))
        painter.drawText(base_rect.adjusted(0.0, 1.8, 0.0, 1.8), Qt.AlignmentFlag.AlignCenter, text)
        painter.drawText(base_rect.adjusted(-0.7, 0.9, -0.7, 0.9), Qt.AlignmentFlag.AlignCenter, text)
        painter.drawText(base_rect.adjusted(0.7, 0.9, 0.7, 0.9), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(0, 0, 0, 135))
        painter.drawText(base_rect.adjusted(0.0, 1.0, 0.0, 1.0), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(255, 255, 255, 252))
        painter.drawText(base_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

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
    - Sleek mobile capsule pagination dots near inner bottom edge.
    """
    wheel_scrolled = Signal(int)
    blank_double_clicked = Signal()

    def __init__(self, radius: int = 30, opacity: float = 0.88, theme: str = "dark", parent=None):
        if isinstance(radius, QWidget) and parent is None:
            parent = radius
            radius = 30
        super().__init__(parent)
        self.radius = radius
        self.opacity = max(0.05, min(1.0, float(opacity)))
        self.theme = theme
        self._current_page: int = 0
        self._total_pages: int = 1
        self.pill_pos: float = 0.0

        self._pill_anim = QVariantAnimation(self)
        self._pill_anim.setDuration(200)
        self._pill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pill_anim.valueChanged.connect(self._on_pill_anim_tick)

        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _on_pill_anim_tick(self, val):
        self.pill_pos = float(val)
        self.update()

    def set_pagination(self, page: int, total_pages: int):
        total = max(1, int(total_pages))
        clamped_page = max(0, min(int(page), total - 1))
        total_changed = (self._total_pages != total)
        self._total_pages = total

        if self._current_page != clamped_page:
            self._pill_anim.stop()
            if total_changed or not self.isVisible():
                self.pill_pos = float(clamped_page)
            else:
                self._pill_anim.setStartValue(float(self.pill_pos))
                self._pill_anim.setEndValue(float(clamped_page))
                self._pill_anim.start()
            self._current_page = clamped_page
        elif self._pill_anim.state() != QVariantAnimation.State.Running:
            self.pill_pos = float(clamped_page)

        self.update()

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

    def _render_background(self, key, dpr: float = 1.0):
        """尺寸/主题/DPR 变化时才重画一次，绘制阶段只做一次亚像素对齐的 drawPixmap。"""
        w, h = float(self.width()), float(self.height())
        dpr = max(1.0, float(dpr or 1.0))
        card_rect = QRectF(1.0, 1.0, max(1.0, w - 2.0), max(1.0, h - 2.0))
        render_fn = getattr(icon_utils, "render_squircle_card_with_shadow", None)
        if callable(render_fn):
            try:
                self._bg_cache = render_fn(
                    self.size(),
                    card_rect,
                    float(self.radius),
                    float(self.opacity),
                    self.theme,
                    draw_shadow=False,
                    dpr=dpr,
                )
                self._bg_key = key
                return
            except TypeError:
                if dpr <= 1.001:
                    self._bg_cache = render_fn(
                        self.size(),
                        card_rect,
                        float(self.radius),
                        float(self.opacity),
                        self.theme,
                        draw_shadow=False,
                    )
                    self._bg_key = key
                    return

        phys_w = max(1, int(round(w * dpr)))
        phys_h = max(1, int(round(h * dpr)))
        pix = QPixmap(phys_w, phys_h)
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = create_squircle_path(card_rect, self.radius)
        op = self.opacity
        grad = QLinearGradient(0, 0, 0, h)
        if self.theme == "light":
            grad.setColorAt(0.0, QColor(250, 251, 253, int(150 * op)))
            grad.setColorAt(1.0, QColor(236, 239, 244, int(145 * op)))
        elif self.theme == "oled":
            grad.setColorAt(0.0, QColor(18, 19, 23, int(150 * op)))
            grad.setColorAt(1.0, QColor(8, 9, 11, int(155 * op)))
        else:
            grad.setColorAt(0.0, QColor(38, 41, 48, int(150 * op)))
            grad.setColorAt(1.0, QColor(24, 26, 31, int(155 * op)))

        p.fillPath(path, grad)
        draw_liquid_glass(p, path, card_rect, self.radius, self.theme)
        p.end()
        self._bg_cache = pix
        self._bg_key = key

    def _paint_pagination_dots(self, painter: QPainter):
        n = int(self._total_pages)
        w = float(self.width())
        h = float(self.height())
        if n <= 1 or w < 72.0 or h < 56.0:
            return

        compact = w < 120.0
        if compact:
            dot_h = 3.5
            dot_base_w = 3.5
            dot_active_extra = 6.5  # 3.5 + 6.5 = 10x3.5px compact capsule
            gap = 3.5
            max_vis = 3 if w < 90.0 else 5
        else:
            dot_h = 4.0
            dot_base_w = 4.0
            dot_active_extra = 9.0  # 4 + 9 = 13x4px capsule pill
            gap = 5.0
            max_vis = 8

        avail_w = max(18.0, w - 32.0)
        fit_vis = max(2, int((avail_w - (dot_base_w + dot_active_extra) + gap) // (dot_base_w + gap)) + 1)
        vis_n = min(n, max_vis, fit_vis)
        if vis_n < 2:
            return

        pos = max(0.0, min(float(n - 1), float(self.pill_pos)))
        if n > vis_n:
            start_idx = max(0, min(n - vis_n, int(round(pos)) - (vis_n // 2)))
        else:
            start_idx = 0
        local_pos = max(0.0, min(float(vis_n - 1), pos - float(start_idx)))

        dots = []
        for j in range(vis_n):
            weight = max(0.0, 1.0 - abs(local_pos - float(j)))
            has_left_more = (start_idx > 0 and j == 0 and weight < 0.25)
            has_right_more = (start_idx + vis_n < n and j == vis_n - 1 and weight < 0.25)
            taper = 0.72 if (has_left_more or has_right_more) else 1.0
            w_j = (dot_base_w + dot_active_extra * weight) * taper
            h_j = dot_h * taper
            dots.append((w_j, h_j, weight))

        total_w = sum(d[0] for d in dots) + gap * (vis_n - 1)
        if total_w > avail_w and total_w > 1.0:
            shrink = avail_w / total_w
            dots = [(w_j * shrink, max(2.0, h_j * shrink), wt) for w_j, h_j, wt in dots]
            gap *= shrink
            total_w = sum(d[0] for d in dots) + gap * (vis_n - 1)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cur_x = (w - total_w) / 2.0
        cy = h - (6.5 if compact else 7.5)

        is_light = (self.theme == "light")
        painter.setPen(Qt.PenStyle.NoPen)
        for w_j, h_j, weight in dots:
            if is_light:
                alpha = int(68 + 145 * weight)
                color = QColor(15, 23, 42, alpha)
            else:
                alpha = int(78 + 157 * weight)
                color = QColor(255, 255, 255, alpha)
            painter.setBrush(color)
            top_y = cy - h_j / 2.0
            rad = h_j / 2.0
            painter.drawRoundedRect(QRectF(cur_x, top_y, w_j, h_j), rad, rad)
            cur_x += w_j + gap
        painter.restore()

    def paintEvent(self, event):
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        key = (self.width(), self.height(), self.radius, round(self.opacity, 3), self.theme, round(dpr, 3))
        if getattr(self, "_bg_cache", None) is None or getattr(self, "_bg_key", None) != key:
            self._render_background(key, dpr=dpr)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._bg_cache is not None and not self._bg_cache.isNull():
            painter.drawPixmap(
                QRectF(0.0, 0.0, float(self.width()), float(self.height())),
                self._bg_cache,
                QRectF(0.0, 0.0, float(self._bg_cache.width()), float(self._bg_cache.height())),
            )
        self._paint_pagination_dots(painter)
        painter.end()
