"""全局控件视觉：统一的输入/按钮/复选样式，以及抗锯齿的单选/复选控件。

各窗口自己的 setStyleSheet 优先级更高；这里的规则用于覆盖那些没有局部 QSS 的
对话框与弹窗（城市选择、输入对话框、文件对话框按钮等）。
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QCheckBox, QRadioButton


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
