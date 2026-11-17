"""全局视觉设计系统：CJK 字体栈工厂、Win11 沉浸式标题栏、胶囊开关与主题语义 Token。

各窗口自己的 setStyleSheet 优先级更高；这里的规则用于覆盖那些没有局部 QSS 的
对话框与弹窗（城市选择、输入对话框、文件对话框按钮等）。
"""
import sys
import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt, QRectF, QSize, QTimer, QEasingCurve, QVariantAnimation
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QRadioButton


# =========================================================================
# 1. Windows CJK 优先清晰字体栈
# =========================================================================
UI_FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
UI_FONT_CSS = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'


def ui_font(pixel_size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """构建抗锯齿、西文数字与中文笔画均衡的全局 UI 字体。"""
    font = QFont()
    font.setFamilies(UI_FONT_FAMILIES)
    font.setPixelSize(max(8, int(pixel_size)))
    font.setWeight(weight)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
    )
    return font


# =========================================================================
# 2. Windows 11 DWM 沉浸式暗色/浅色标题栏与圆角
# =========================================================================
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWCP_ROUND = 2


def apply_win11_window_chrome(widget, theme: str = "dark") -> bool:
    """为原生顶级窗口/对话框同步 Win11 DWM 标题栏底色、文字色、暗黑模式与圆角。"""
    if widget is None or sys.platform != "win32":
        return False

    prop_theme = theme
    if not isinstance(prop_theme, str) or not prop_theme.strip():
        raw_prop = widget.property("theme")
        if isinstance(raw_prop, str) and raw_prop.strip():
            prop_theme = raw_prop
        else:
            attr_theme = getattr(widget, "theme", None)
            prop_theme = attr_theme if isinstance(attr_theme, str) and attr_theme.strip() else "dark"

    key = prop_theme.strip().lower()
    is_light = key == "light"

    try:
        if not widget.isWindow():
            return False
        hwnd = int(widget.internalWinId())
    except Exception:
        return False
    if not hwnd:
        return False

    dwmapi = getattr(ctypes.windll, "dwmapi", None)
    if dwmapi is None:
        return False

    # Win32 COLORREF 格式为 0x00BBGGRR
    if is_light:
        caption_ref = 0x00F8F5F3  # #f3f5f8
        text_ref = 0x002A170F     # #0f172a
    elif key == "oled":
        caption_ref = 0x000A0807  # #07080a
        text_ref = 0x00F2F0EE     # #eef0f2
    else:
        caption_ref = 0x00181311  # #111318
        text_ref = 0x00F2F0EE     # #eef0f2

    dark_val = ctypes.c_int(0 if is_light else 1)
    corner_val = ctypes.c_int(DWMWCP_ROUND)
    caption_val = ctypes.c_uint(caption_ref)
    text_val = ctypes.c_uint(text_ref)

    h_wnd = wintypes.HWND(hwnd)
    ok = False
    for attr, c_val in (
        (DWMWA_USE_IMMERSIVE_DARK_MODE, dark_val),
        (DWMWA_WINDOW_CORNER_PREFERENCE, corner_val),
        (DWMWA_CAPTION_COLOR, caption_val),
        (DWMWA_TEXT_COLOR, text_val),
    ):
        try:
            hr = dwmapi.DwmSetWindowAttribute(
                h_wnd,
                ctypes.c_int(attr),
                ctypes.byref(c_val),
                ctypes.sizeof(c_val),
            )
            if hr == 0:
                ok = True
        except Exception:
            pass
    return ok


# =========================================================================
# 3. 居中二级对话框基类
# =========================================================================
class CenteredDialog(QDialog):
    """二级对话框基类：首次显示时居中于父窗口并置前，同时自动同步 Win11 沉浸式标题栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._centered_once = False

    def showEvent(self, event):
        super().showEvent(event)
        prop_theme = self.property("theme")
        if not isinstance(prop_theme, str) or not prop_theme.strip():
            attr_t = getattr(self, "theme", None)
            if isinstance(attr_t, str) and attr_t.strip():
                prop_theme = attr_t
            else:
                prop_theme = "light" if system_is_light() else "dark"
        apply_win11_window_chrome(self, prop_theme)
        if not self._centered_once:
            self._centered_once = True
            QTimer.singleShot(0, self._center_on_parent)

    def _center_on_parent(self):
        parent = self.parentWidget()
        screen = QApplication.primaryScreen()
        if parent is not None:
            parent_geo = parent.window().frameGeometry()
            screen = QApplication.screenAt(parent_geo.center()) or screen
        else:
            parent_geo = None
        avail = screen.availableGeometry() if screen else self.geometry()

        if parent_geo is not None and parent_geo.width() >= self.width() and parent_geo.height() >= self.height():
            target = parent_geo.center() - self.rect().center()
        else:
            target = avail.center() - self.rect().center()

        max_x = max(avail.left(), avail.right() - self.width() - 8)
        min_x = min(avail.left() + 8, max_x)
        max_y = max(avail.top(), avail.bottom() - self.height() - 8)
        min_y = min(avail.top() + 8, max_y)
        x = max(min_x, min(target.x(), max_x))
        y = max(min_y, min(target.y(), max_y))
        self.move(x, y)
        self.raise_()
        self.activateWindow()


# =========================================================================
# 4. 全局样式与系统原生弹窗适配
# =========================================================================
GLOBAL_QSS = f"""
QToolTip {{
    background-color: rgba(17, 19, 24, 244);
    color: #f4f6fa;
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 8px;
    padding: 6px 10px;
    font-family: {UI_FONT_CSS};
    font-size: 12px;
}}
QLineEdit, QComboBox {{
    background-color: rgba(255, 255, 255, 0.06);
    color: #f1f5f9;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 7px;
    padding: 6px 30px 6px 12px;
    font-family: {UI_FONT_CSS};
    font-size: 13px;
    selection-background-color: #3b82f6;
}}
QLineEdit {{ padding-right: 12px; }}
QLineEdit:hover, QComboBox:hover {{ border-color: rgba(255, 255, 255, 0.22); }}
QLineEdit:focus, QComboBox:focus {{ border-color: #3b82f6; }}
QLineEdit:disabled, QComboBox:disabled {{
    background-color: rgba(255, 255, 255, 0.02);
    color: #64748b;
    border-color: rgba(255, 255, 255, 0.06);
}}
QPushButton {{
    background-color: rgba(255, 255, 255, 0.07);
    color: #eef2f6;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 7px;
    padding: 6px 16px;
    font-family: {UI_FONT_CSS};
    font-size: 13px;
}}
QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.12); }}
QPushButton:pressed {{ background-color: rgba(255, 255, 255, 0.05); }}
QPushButton:disabled {{ color: #64748b; }}
QLabel {{
    color: #dbe2ea;
    font-family: {UI_FONT_CSS};
}}
QCheckBox, QRadioButton {{
    color: #eef2f6;
    spacing: 10px;
    font-family: {UI_FONT_CSS};
    font-size: 13px;
    min-height: 24px;
}}
ToggleSwitch {{
    min-height: 20px;
    max-height: 22px;
    padding: 0px;
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1.25px solid #64748b;
    background-color: transparent;
    border-radius: 4px;
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    border-color: #3b82f6;
    background-color: rgba(59, 130, 246, 0.20);
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: #94a3b8; }}
"""

_NATIVE_DIALOGS = ("QMessageBox", "QInputDialog", "QProgressDialog", "QErrorMessage")


def _scoped(selector: str) -> str:
    """限定在原生对话框内部，避免改动应用自己窗口里的同类型控件。"""
    return ", ".join(f"{d} {selector}" for d in _NATIVE_DIALOGS)


def native_dialog_qss(light: bool) -> str:
    if light:
        bg, fg = "#f3f5f8", "#0f172a"
        btn_bg, btn_hover, border = "#ffffff", "#ebeef4", "#cbd5e1"
        field_bg, muted = "#ffffff", "#64748b"
    else:
        bg, fg = "#111318", "#f1f5f9"
        btn_bg, btn_hover, border = "#1e222d", "#272d3b", "#333a4a"
        field_bg, muted = "#171a22", "#94a3b8"

    windows = ", ".join(_NATIVE_DIALOGS)
    fields = ", ".join(f"{d} {w}" for d in _NATIVE_DIALOGS for w in ("QLineEdit", "QComboBox"))
    return f"""
{windows} {{ background-color: {bg}; color: {fg}; font-family: {UI_FONT_CSS}; }}
{_scoped('QLabel')} {{ color: {fg}; background: transparent; font-family: {UI_FONT_CSS}; }}
{_scoped('QPushButton')} {{
    background-color: {btn_bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 6px 16px;
    min-width: 72px;
    font-family: {UI_FONT_CSS};
}}
{_scoped('QPushButton:hover')} {{ background-color: {btn_hover}; }}
{_scoped('QPushButton:pressed')} {{ background-color: {btn_bg}; }}
{_scoped('QPushButton:disabled')} {{ color: {muted}; }}
{_scoped('QCheckBox')}, {_scoped('QRadioButton')} {{ color: {fg}; background: transparent; }}
{fields} {{
    background-color: {field_bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 7px;
    padding: 6px 12px;
    selection-background-color: #3b82f6;
    font-family: {UI_FONT_CSS};
}}
"""


def theme_pick(theme: str, light_value, dark_value):
    """按应用主题取值：浅色底板必须配深色字，其余主题保持原色不变。"""
    return light_value if theme == "light" else dark_value


_LIGHT_TEXT_COLORS = {
    "#ffffff": "#0f172a", "#f8fafc": "#0f172a", "#f1f5f9": "#0f172a",
    "#e2e8f0": "#334155", "#cbd5e1": "#334155", "#a9b6c8": "#334155",
    "#94a3b8": "#475569", "#64748b": "#475569", "#8b919b": "#475569",
    "#60a5fa": "#2563eb", "#38bdf8": "#0284c7", "#93c5fd": "#1d4ed8",
    "#10b981": "#047857", "#f59e0b": "#b45309", "#ef4444": "#b91c1c",
    "#a855f7": "#7e22ce",
}


def light_text(dark_color: str) -> str:
    return _LIGHT_TEXT_COLORS.get(dark_color.lower(), "#0f172a")


# =========================================================================
# 5. 主题语义 Token 与统一菜单样式
# =========================================================================
_THEME_TOKENS = {
    "dark": {
        "surface_top": "#171a21",
        "surface_bottom": "#111318",
        "border_hi": "rgba(255, 255, 255, 38)",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#94a3b8",
        "well_bg": "rgba(255, 255, 255, 16)",
        "well_hover_bg": "rgba(255, 255, 255, 28)",
        "well_border": "rgba(255, 255, 255, 26)",
        "badge_bg": "rgba(59, 130, 246, 42)",
        "badge_fg": "#60a5fa",
        "dialog_top": "#171a21",
        "dialog_bottom": "#111318",
        "accent": "#3b82f6",
    },
    "light": {
        "surface_top": "#ffffff",
        "surface_bottom": "#f3f5f8",
        "border_hi": "rgba(15, 23, 42, 24)",
        "text_primary": "#0f172a",
        "text_secondary": "#334155",
        "text_muted": "#64748b",
        "well_bg": "rgba(15, 23, 42, 12)",
        "well_hover_bg": "rgba(15, 23, 42, 22)",
        "well_border": "rgba(15, 23, 42, 24)",
        "badge_bg": "rgba(59, 130, 246, 28)",
        "badge_fg": "#2563eb",
        "dialog_top": "#ffffff",
        "dialog_bottom": "#f3f5f8",
        "accent": "#3b82f6",
    },
    "oled": {
        "surface_top": "#0e1015",
        "surface_bottom": "#07080a",
        "border_hi": "rgba(255, 255, 255, 32)",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#8b919b",
        "well_bg": "rgba(255, 255, 255, 14)",
        "well_hover_bg": "rgba(255, 255, 255, 24)",
        "well_border": "rgba(255, 255, 255, 22)",
        "badge_bg": "rgba(59, 130, 246, 40)",
        "badge_fg": "#60a5fa",
        "dialog_top": "#0e1015",
        "dialog_bottom": "#07080a",
        "accent": "#3b82f6",
    },
}


def get_theme_tokens(theme: str = "dark") -> dict:
    """返回指定主题（dark / light / oled）的语义化视觉 Token 字典副本。"""
    key = (theme or "dark").strip().lower()
    return dict(_THEME_TOKENS.get(key, _THEME_TOKENS["dark"]))


def menu_qss(theme: str = "dark") -> str:
    """返回适配浅色云母或深色黑曜石质感的圆角右键菜单 QSS。"""
    key = (theme or "dark").strip().lower()
    if key == "light":
        bg = "#f8fafc"
        fg = "#0f172a"
        border = "rgba(15, 23, 42, 28)"
        hover_bg = "rgba(15, 23, 42, 18)"
        hover_fg = "#0f172a"
        disabled_fg = "#94a3b8"
        sep = "rgba(15, 23, 42, 20)"
    elif key == "oled":
        bg = "rgba(14, 15, 20, 252)"
        fg = "#f5f5f7"
        border = "rgba(255, 255, 255, 42)"
        hover_bg = "rgba(255, 255, 255, 40)"
        hover_fg = "#ffffff"
        disabled_fg = "#64748b"
        sep = "rgba(255, 255, 255, 24)"
    else:
        bg = "rgba(24, 26, 34, 248)"
        fg = "#f5f5f7"
        border = "rgba(255, 255, 255, 42)"
        hover_bg = "rgba(255, 255, 255, 40)"
        hover_fg = "#ffffff"
        disabled_fg = "#6b7280"
        sep = "rgba(255, 255, 255, 26)"

    return f"""
QMenu {{
    background-color: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 6px;
    font-family: {UI_FONT_CSS};
    font-size: 13px;
}}
QMenu::item {{
    background-color: transparent;
    color: {fg};
    padding: 7px 18px;
    border-radius: 6px;
    margin: 1px 2px;
}}
QMenu::item:selected {{
    background-color: {hover_bg};
    color: {hover_fg};
}}
QMenu::item:disabled {{
    color: {disabled_fg};
    background-color: transparent;
}}
QMenu::separator {{
    height: 1px;
    background: {sep};
    margin: 4px 8px;
}}
"""


def _is_widget_light_theme(widget) -> bool:
    """判断自绘控件是否处于浅色主题表面（优先读自身 property('theme')，其次回溯父链）。"""
    cur = widget
    while cur is not None:
        prop = cur.property("theme")
        if isinstance(prop, str) and prop:
            return prop.strip().lower() == "light"
        cur = cur.parentWidget()
    return False


def system_is_light() -> bool:
    """取系统明暗；平台不提供时退回窗口底色亮度判断。"""
    hints = QGuiApplication.styleHints()
    getter = getattr(hints, "colorScheme", None)
    enum = getattr(Qt, "ColorScheme", None)
    if getter and enum:
        try:
            scheme = getter()
            if scheme == enum.Light:
                return True
            if scheme == enum.Dark:
                return False
        except Exception:
            pass
    return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() > 128


def apply_global_style(app):
    """启动时按系统明暗装好全局样式，运行中切换系统主题也同步刷新。"""
    app.setFont(ui_font(13))

    def refresh(*_):
        app.setStyleSheet(GLOBAL_QSS + native_dialog_qss(system_is_light()))

    refresh()
    if not getattr(app, "_stacky_color_scheme_connected", False):
        changed = getattr(QGuiApplication.styleHints(), "colorSchemeChanged", None)
        if changed is not None:
            changed.connect(refresh)
            setattr(app, "_stacky_color_scheme_connected", True)


# =========================================================================
# 6. 抗锯齿单选框 / 复选框 与 现代动画胶囊开关 ToggleSwitch
# =========================================================================
def _paint_flat_checkable(widget, is_radio: bool):
    """细描边 + 选中标记 + 文字。QSS 的 border-radius 无抗锯齿，会留锯齿和毛边。"""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    is_light = _is_widget_light_theme(widget)
    checked, enabled = widget.isChecked(), widget.isEnabled()
    accent = QColor("#2563eb" if is_light else "#3b82f6")

    if is_light:
        if not enabled:
            border_c = QColor("#cbd5e1")
        elif checked:
            border_c = accent
        elif widget.underMouse():
            border_c = QColor("#475569")
        else:
            border_c = QColor("#94a3b8")
    else:
        if not enabled:
            border_c = QColor("#475569")
        elif checked:
            border_c = accent
        elif widget.underMouse():
            border_c = QColor("#94a3b8")
        else:
            border_c = QColor("#64748b")

    side = 16.0
    box = QRectF(0.0, (widget.height() - side) / 2.0, side, side)
    outer = box.adjusted(0.8, 0.8, -0.8, -0.8)

    if is_radio:
        p.setPen(QPen(border_c, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(outer)
        if checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(border_c)
            p.drawEllipse(outer.adjusted(3.6, 3.6, -3.6, -3.6))
    else:
        p.setPen(QPen(border_c, 1.4))
        p.setBrush(border_c if checked else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(outer, 4.0, 4.0)
        if checked:
            pen = QPen(QColor("#ffffff"), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath()
            path.moveTo(outer.left() + 3.2, outer.center().y() + 0.2)
            path.lineTo(outer.left() + 6.0, outer.bottom() - 3.2)
            path.lineTo(outer.right() - 2.8, outer.top() + 3.4)
            p.drawPath(path)

    if widget.text():
        if is_light:
            text_color = QColor("#0f172a") if enabled else QColor("#94a3b8")
        else:
            text_color = QColor("#eef2f6") if enabled else QColor("#64748b")
        p.setPen(text_color)
        p.setFont(widget.font())
        p.drawText(
            QRectF(side + 9.0, 0.0, widget.width() - side - 9.0, float(widget.height())),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            widget.text(),
        )
    p.end()


class FlatRadioButton(QRadioButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(13))

    def paintEvent(self, event):
        _paint_flat_checkable(self, True)


class FlatCheckBox(QCheckBox):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(13))

    def paintEvent(self, event):
        _paint_flat_checkable(self, False)


def _lerp_color(c0: QColor, c1: QColor, t: float) -> QColor:
    k = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(c0.red() + (c1.red() - c0.red()) * k)),
        int(round(c0.green() + (c1.green() - c0.green()) * k)),
        int(round(c0.blue() + (c1.blue() - c0.blue()) * k)),
        int(round(c0.alpha() + (c1.alpha() - c0.alpha()) * k)),
    )


class ToggleSwitch(QCheckBox):
    """现代胶囊拨动开关：100% 兼容 QCheckBox 接口与信号，带 140ms OutCubic 滑块动画。"""

    TRACK_W = 38
    TRACK_H = 20
    THUMB_D = 16

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(13))

        self._thumb_pos: float = 1.0 if self.isChecked() else 0.0
        self._force_animate: bool = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)
        self.toggled.connect(self._on_toggled_state)

        self._sync_fixed_geometry()

    @property
    def thumb_position(self) -> float:
        return self._thumb_pos

    def _sync_fixed_geometry(self):
        txt = self.text()
        if not txt:
            self.setFixedSize(self.TRACK_W, self.TRACK_H)
        else:
            fm = QFontMetrics(self.font())
            w = self.TRACK_W + 8 + fm.horizontalAdvance(txt) + 2
            h = max(self.TRACK_H, fm.height() + 2)
            self.setFixedSize(w, h)

    def setText(self, text: str):
        super().setText(text)
        self._sync_fixed_geometry()
        self.update()

    def sizeHint(self) -> QSize:
        txt = self.text()
        if not txt:
            return QSize(self.TRACK_W, self.TRACK_H)
        fm = QFontMetrics(self.font())
        return QSize(
            self.TRACK_W + 8 + fm.horizontalAdvance(txt) + 2,
            max(self.TRACK_H, fm.height() + 2),
        )

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def nextCheckState(self):
        self._force_animate = True
        super().nextCheckState()

    def setChecked(self, checked: bool):
        changed = bool(checked) != self.isChecked()
        super().setChecked(checked)
        if not changed:
            target = 1.0 if checked else 0.0
            if self._anim.state() != QVariantAnimation.State.Running:
                self._thumb_pos = target
                self.update()

    def _on_toggled_state(self, checked: bool):
        target = 1.0 if checked else 0.0
        should_anim = self._force_animate or self.isVisible()
        self._force_animate = False
        self._anim.stop()
        if should_anim and abs(self._thumb_pos - target) > 0.001:
            self._anim.setStartValue(float(self._thumb_pos))
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._thumb_pos = target
            self.update()

    def _on_anim_tick(self, value):
        self._thumb_pos = float(value)
        self.update()

    def closeEvent(self, event):
        self._anim.stop()
        super().closeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        is_light = _is_widget_light_theme(self)
        enabled = self.isEnabled()
        hovered = self.underMouse() and enabled
        t = max(0.0, min(1.0, float(self._thumb_pos)))

        tw = float(self.TRACK_W)
        th = float(self.TRACK_H)
        ty = (float(self.height()) - th) / 2.0
        track_rect = QRectF(0.0, ty, tw, th)
        radius = th / 2.0

        # 1. 胶囊底轨颜色（随动画进度平滑过渡）
        on_color = QColor("#2563eb" if hovered else "#3b82f6")
        if not enabled:
            on_color.setAlpha(115)

        if is_light:
            off_color = QColor(15, 23, 42, 44 if hovered else 32)
            off_border = QColor(15, 23, 42, 54 if hovered else 36)
            if not enabled:
                off_color = QColor(15, 23, 42, 16)
                off_border = QColor(15, 23, 42, 18)
        else:
            off_color = QColor(255, 255, 255, 50 if hovered else 36)
            off_border = QColor(255, 255, 255, 68 if hovered else 44)
            if not enabled:
                off_color = QColor(255, 255, 255, 16)
                off_border = QColor(255, 255, 255, 20)

        fill_c = _lerp_color(off_color, on_color, t)
        border_c = _lerp_color(off_border, on_color, t)

        inner_track = track_rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border_c, 1.0))
        p.setBrush(fill_c)
        p.drawRoundedRect(inner_track, radius - 0.5, radius - 0.5)

        # 2. 纯白圆形滑块 + 柔和接触阴影
        pad = (th - float(self.THUMB_D)) / 2.0
        travel = tw - float(self.THUMB_D) - pad * 2.0
        thumb_x = track_rect.left() + pad + travel * t
        thumb_y = track_rect.top() + pad
        d = float(self.THUMB_D)

        if enabled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 55))
            p.drawEllipse(QRectF(thumb_x, thumb_y + 1.0, d, d))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 255 if enabled else 155))
        p.drawEllipse(QRectF(thumb_x, thumb_y, d, d))

        # 3. 右侧可选文字标签
        txt = self.text()
        if txt:
            if is_light:
                txt_c = QColor("#0f172a") if enabled else QColor("#94a3b8")
            else:
                txt_c = QColor("#eef2f6") if enabled else QColor("#64748b")
            p.setPen(txt_c)
            p.setFont(self.font())
            p.drawText(
                QRectF(tw + 8.0, 0.0, float(self.width()) - tw - 8.0, float(self.height())),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                txt,
            )

        p.end()


sys.modules.setdefault("ui_kit", sys.modules[__name__])
sys.modules.setdefault("stacky.ui.ui_kit", sys.modules[__name__])
