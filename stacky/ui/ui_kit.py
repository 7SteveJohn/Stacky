"""全局控件视觉：统一的输入/按钮/复选样式，以及抗锯齿的单选/复选控件。

各窗口自己的 setStyleSheet 优先级更高；这里的规则用于覆盖那些没有局部 QSS 的
对话框与弹窗（城市选择、输入对话框、文件对话框按钮等）。
"""
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPainterPath
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QRadioButton


class CenteredDialog(QDialog):
    """二级对话框基类：首次显示时居中于父窗口并置前。

    直接从桌面主窗口派生 parent 会让对话框被上一级设置页/抽屉盖住，
    必须按真正的调用方定位，并主动 raise + activate。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._centered_once = False

    def showEvent(self, event):
        super().showEvent(event)
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

        # 父窗口比对话框还小时（例如从桌面小卡片打开设置），
        # 以父窗口为中心会把对话框推出屏幕，此时直接居中到屏幕。
        if parent_geo is not None and parent_geo.width() >= self.width() and parent_geo.height() >= self.height():
            target = parent_geo.center() - self.rect().center()
        else:
            target = avail.center() - self.rect().center()

        # 最后再钳一次，保证标题栏和底部按钮都在可用区域内
        x = min(max(target.x(), avail.left() + 8), avail.right() - self.width() - 8)
        y = min(max(target.y(), avail.top() + 8), avail.bottom() - self.height() - 8)
        self.move(x, y)
        self.raise_()
        self.activateWindow()


GLOBAL_QSS = """
QToolTip {
    background-color: rgba(22, 24, 32, 238);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 38);
    border-radius: 8px;
    padding: 6px 10px;
    font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI";
    font-size: 12px;
}
QLineEdit, QComboBox {
    background-color: rgba(255, 255, 255, 0.06);
    color: #f0f1f2;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 6px;
    padding: 6px 30px 6px 12px;
    font-size: 13px;
    selection-background-color: #456f9f;
}
QLineEdit { padding-right: 12px; }
QLineEdit:hover, QComboBox:hover { border-color: rgba(255, 255, 255, 0.24); }
QLineEdit:focus, QComboBox:focus { border-color: rgba(255, 255, 255, 0.32); }
QLineEdit:disabled, QComboBox:disabled {
    background-color: rgba(255, 255, 255, 0.02);
    color: #6b7280;
    border-color: rgba(255, 255, 255, 0.06);
}
QPushButton {
    background-color: rgba(255, 255, 255, 0.07);
    color: #e6e7e9;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
}
QPushButton:hover { background-color: rgba(255, 255, 255, 0.12); }
QPushButton:pressed { background-color: rgba(255, 255, 255, 0.05); }
QPushButton:disabled { color: #6b7280; }
QLabel { color: #d6d9dd; }
QCheckBox, QRadioButton {
    color: #e6e7e9;
    spacing: 10px;
    font-size: 13px;
    min-height: 24px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1.25px solid #7d818b;
    background-color: transparent;
    border-radius: 4px;
}
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    border-color: #8ab0d8;
    background-color: rgba(138, 176, 216, 0.18);
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #a9aeb5; }
"""

# 这些对话框不自绘底色，背景取自系统调色板。GLOBAL_QSS 却把它们的标签与按钮
# 前景写死成浅色，系统切浅色后就成了白底白字，必须整套指定背景+前景。
_NATIVE_DIALOGS = ("QMessageBox", "QInputDialog", "QProgressDialog", "QErrorMessage")


def _scoped(selector: str) -> str:
    """限定在原生对话框内部，避免改动应用自己窗口里的同类型控件。"""
    return ", ".join(f"{d} {selector}" for d in _NATIVE_DIALOGS)


def native_dialog_qss(light: bool) -> str:
    if light:
        bg, fg = "#f7f8fa", "#1b2029"
        btn_bg, btn_hover, border = "#e7eaf0", "#d9dee7", "#c5ccd8"
        field_bg, muted = "#ffffff", "#5c6472"
    else:
        bg, fg = "#1c1e26", "#eef0f4"
        btn_bg, btn_hover, border = "#2c303b", "#3a4050", "#454b59"
        field_bg, muted = "#23262f", "#aeb4bf"

    windows = ", ".join(_NATIVE_DIALOGS)
    fields = ", ".join(f"{d} {w}" for d in _NATIVE_DIALOGS for w in ("QLineEdit", "QComboBox"))
    return f"""
{windows} {{ background-color: {bg}; color: {fg}; }}
{_scoped('QLabel')} {{ color: {fg}; background: transparent; }}
{_scoped('QPushButton')} {{
    background-color: {btn_bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 16px;
}}
{_scoped('QPushButton:hover')} {{ background-color: {btn_hover}; }}
{_scoped('QPushButton:pressed')} {{ background-color: {btn_bg}; }}
{_scoped('QPushButton:disabled')} {{ color: {muted}; }}
{_scoped('QCheckBox')}, {_scoped('QRadioButton')} {{ color: {fg}; background: transparent; }}
{fields} {{
    background-color: {field_bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 12px;
    selection-background-color: #456f9f;
}}
"""


def theme_pick(theme: str, light_value, dark_value):
    """按应用主题取值：浅色底板必须配深色字，其余主题保持原色不变。

    调用点保留自己原来的深色主题颜色，只在 light 分支替换，保证默认主题零回归。
    """
    return light_value if theme == "light" else dark_value


# 深色主题下的前景色 -> 浅色底板上等价的高对比深色字。
_LIGHT_TEXT_COLORS = {
    "#ffffff": "#0f172a", "#f8fafc": "#0f172a", "#f1f5f9": "#0f172a",
    "#e2e8f0": "#334155", "#cbd5e1": "#334155", "#a9b6c8": "#334155",
    "#94a3b8": "#475569", "#64748b": "#475569", "#8b919b": "#475569",
    "#60a5fa": "#1d4ed8", "#38bdf8": "#0369a1", "#93c5fd": "#1e40af",
    "#10b981": "#047857", "#f59e0b": "#b45309", "#ef4444": "#b91c1c",
    "#a855f7": "#7e22ce",
}


def light_text(dark_color: str) -> str:
    return _LIGHT_TEXT_COLORS.get(dark_color.lower(), "#0f172a")


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
    def refresh(*_):
        app.setStyleSheet(GLOBAL_QSS + native_dialog_qss(system_is_light()))

    refresh()
    changed = getattr(QGuiApplication.styleHints(), "colorSchemeChanged", None)
    if changed is not None:
        changed.connect(refresh)


def _paint_flat_checkable(widget, is_radio: bool):
    """细描边 + 选中标记 + 文字。QSS 的 border-radius 无抗锯齿，会留锯齿和毛边。"""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    checked, enabled = widget.isChecked(), widget.isEnabled()
    if not enabled:
        color = QColor("#4a4e55")
    elif checked:
        color = QColor("#8ab0d8")
    elif widget.underMouse():
        color = QColor("#a9aeb5")
    else:
        color = QColor("#7d818b")

    side = 16.0
    box = QRectF(0.0, (widget.height() - side) / 2.0, side, side)
    outer = box.adjusted(1.0, 1.0, -1.0, -1.0)

    p.setPen(QPen(color, 1.25))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if is_radio:
        p.drawEllipse(outer)
    else:
        p.drawRoundedRect(outer, 3.0, 3.0)

    if checked:
        if is_radio:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(outer.adjusted(4.0, 4.0, -4.0, -4.0))
        else:
            pen = QPen(color, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath()
            path.moveTo(outer.left() + 3.0, outer.center().y())
            path.lineTo(outer.left() + 6.0, outer.bottom() - 3.0)
            path.lineTo(outer.right() - 2.5, outer.top() + 3.0)
            p.drawPath(path)

    p.setPen(QColor("#e6e7e9") if enabled else QColor("#64748b"))
    p.setFont(widget.font())
    p.drawText(
        QRectF(side + 10.0, 0.0, widget.width() - side - 10.0, float(widget.height())),
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        widget.text(),
    )
    p.end()


class FlatRadioButton(QRadioButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event):
        _paint_flat_checkable(self, True)


class FlatCheckBox(QCheckBox):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event):
        _paint_flat_checkable(self, False)
