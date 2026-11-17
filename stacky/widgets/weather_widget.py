"""
weather_widget.py - 现代超椭圆桌面天气卡片小组件 (Weather Widget)
纯原生 QPainter 绘制连续曲率超椭圆液态玻璃卡片。
支持 2x2 正方与 2x1 紧凑双模形态，原生 Win32 桌面层锚定，16px 智能网格磁吸。
"""

import math
import os
import sys
import time
import threading
from typing import Optional, Tuple, Dict, Any, List

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, Signal, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient, QRadialGradient,
    QPainterPath, QPolygonF, QMouseEvent, QContextMenuEvent, QAction, QCursor
)
from PySide6.QtWidgets import (
    QWidget, QMenu, QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QLineEdit, QCheckBox, QPushButton, QMessageBox
)

from weather_service import (get_weather_service, WeatherService, POPULAR_CITIES,
                             resolve_city_coordinates, WMO_WEATHER_MAP)
import desktop_layer
from grid_snap import get_all_desktop_card_rects
from widget_grid import SnapDragMixin
from icon_utils import create_squircle_path, get_ui_chevron_icon_path, draw_liquid_glass
import ui_kit
from ui_kit import CenteredDialog, FlatCheckBox


_DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"]
_TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
_UI_FONT_CSS = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'


def _wfont(pt_size: int, weight: QFont.Weight = QFont.Weight.Normal, display: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(_DISPLAY_FAMILIES if display else _TEXT_FAMILIES)
    f.setPointSize(max(6, int(pt_size)))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return f


def _safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return float(default)
    try:
        res = float(val)
        return float(default) if math.isnan(res) or math.isinf(res) else res
    except (TypeError, ValueError):
        return float(default)


def _safe_int(val, default: int = 0) -> int:
    return int(round(_safe_float(val, float(default))))


def _safe_str(val, default: str = "") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def _weather_kind(src) -> str:
    """把 WMO code 或旧的 emoji 字符串归一成图标类型。"""
    if isinstance(src, int):
        if src == 0:
            return "sun"
        if src in (1, 2):
            return "partly"
        if src == 3:
            return "cloud"
        if src in (45, 48):
            return "fog"
        if 51 <= src <= 67 or src in (80, 81):
            return "rain"
        if 71 <= src <= 77 or src in (85, 86):
            return "snow"
        if src in (82, 95, 96, 99):
            return "storm"
        return "cloud"
    e = str(src)
    if "☀" in e:
        return "sun"
    if "⛅" in e or "🌤" in e or "🌦" in e:
        return "partly"
    if "🌧" in e:
        return "rain"
    if "🌨" in e or "❄" in e:
        return "snow"
    if "⛈" in e:
        return "storm"
    if "🌫" in e:
        return "fog"
    if "☁" in e:
        return "cloud"
    return "partly"


def _make_cloud_path(cx: float, cy: float, w: float) -> QPainterPath:
    """平滑超拟态云朵轮廓：由三枚正圆蓬顶与切线平底布尔并集构成，任意字号下零折角。"""
    h = w * 0.64
    x = cx - w * 0.5
    bot = cy + h * 0.46

    r_l = w * 0.215
    cx_l, cy_l = x + w * 0.265, bot - r_l

    r_r = w * 0.185
    cx_r, cy_r = x + w * 0.765, bot - r_r

    r_t = w * 0.250
    cx_t, cy_t = x + w * 0.505, bot - w * 0.365

    p_l = QPainterPath()
    p_l.addEllipse(QPointF(cx_l, cy_l), r_l, r_l)
    p_r = QPainterPath()
    p_r.addEllipse(QPointF(cx_r, cy_r), r_r, r_r)
    p_t = QPainterPath()
    p_t.addEllipse(QPointF(cx_t, cy_t), r_t, r_t)
    p_b = QPainterPath()
    bh = min(r_l, r_r) * 1.65
    p_b.addRect(QRectF(cx_l, bot - bh, cx_r - cx_l, bh))
    return p_l.united(p_r).united(p_t).united(p_b).simplified()


def draw_weather_icon(painter: QPainter, rect: QRectF, src, color: QColor):
    """自绘多层抗锯齿渐变矢量天气图标（适配深色与浅色磨砂底板，彻底移除系统平面 Emoji）。"""
    kind = _weather_kind(src)
    s = float(min(rect.width(), rect.height()))
    if s <= 2.0:
        return
    cx, cy = float(rect.center().x()), float(rect.center().y())
    is_light = color.lightness() < 128

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def paint_sun(sx: float, sy: float, r_disc: float, angles=None, r_in: float = 1.36, r_out: float = 1.80):
        # 1. 柔和暖金环境晕光
        halo_r = r_disc * 2.15
        halo = QRadialGradient(QPointF(sx, sy), halo_r)
        halo.setColorAt(0.0, QColor(251, 191, 36, 58 if is_light else 78))
        halo.setColorAt(0.62, QColor(245, 158, 11, 16 if is_light else 24))
        halo.setColorAt(1.0, QColor(245, 158, 11, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(sx, sy), halo_r, halo_r)

        # 2. 圆润放射芒线
        if angles:
            ray_col = QColor("#f59e0b") if is_light else QColor("#fbbf24")
            painter.setPen(QPen(ray_col, max(1.15, r_disc * 0.27),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for ang in angles:
                ca, sa = math.cos(ang), math.sin(ang)
                painter.drawLine(
                    QPointF(sx + ca * r_disc * r_in, sy + sa * r_disc * r_in),
                    QPointF(sx + ca * r_disc * r_out, sy + sa * r_disc * r_out),
                )

        # 3. 暖金径向渐变太阳圆盘
        disc_grad = QRadialGradient(QPointF(sx - r_disc * 0.28, sy - r_disc * 0.28), r_disc * 1.48)
        disc_grad.setColorAt(0.0, QColor("#fef08a"))
        disc_grad.setColorAt(0.40, QColor("#fde047"))
        disc_grad.setColorAt(1.0, QColor("#f59e0b"))
        rim_col = QColor(217, 119, 6, 95) if is_light else QColor(254, 249, 195, 115)
        painter.setPen(QPen(rim_col, max(0.7, s * 0.025)))
        painter.setBrush(disc_grad)
        painter.drawEllipse(QPointF(sx, sy), r_disc, r_disc)

    def paint_cloud(ccx: float, ccy: float, cw: float, top_hex: str, bot_hex: str,
                    rim_col: QColor, alpha: int = 255):
        path = _make_cloud_path(ccx, ccy, cw)
        c_top = QColor(top_hex)
        c_top.setAlpha(alpha)
        c_bot = QColor(bot_hex)
        c_bot.setAlpha(max(0, alpha - 14))
        grad = QLinearGradient(ccx, ccy - cw * 0.36, ccx, ccy + cw * 0.32)
        grad.setColorAt(0.0, c_top)
        grad.setColorAt(1.0, c_bot)
        painter.setPen(QPen(rim_col, max(0.75, s * 0.028)))
        painter.setBrush(grad)
        painter.drawPath(path)

    if kind == "sun":
        all_rays = [i * (math.pi / 4.0) for i in range(8)]
        paint_sun(cx, cy, s * 0.22, angles=all_rays, r_in=1.36, r_out=1.80)

    elif kind == "partly":
        fan_rays = [math.radians(a) for a in (140, 185, 230, 275, 320)]
        paint_sun(cx - s * 0.16, cy - s * 0.14, s * 0.165, angles=fan_rays, r_in=1.34, r_out=1.74)
        if is_light:
            paint_cloud(cx + s * 0.07, cy + s * 0.09, s * 0.78,
                        "#ffffff", "#cbd5e1", QColor(100, 116, 139, 98), alpha=250)
        else:
            paint_cloud(cx + s * 0.07, cy + s * 0.09, s * 0.78,
                        "#ffffff", "#dbeafe", QColor(255, 255, 255, 185), alpha=246)

    elif kind == "cloud":
        if is_light:
            paint_cloud(cx + s * 0.13, cy - s * 0.10, s * 0.60,
                        "#cbd5e1", "#94a3b8", QColor(100, 116, 139, 75), alpha=196)
            paint_cloud(cx - s * 0.04, cy + s * 0.08, s * 0.80,
                        "#ffffff", "#cbd5e1", QColor(100, 116, 139, 102), alpha=252)
        else:
            paint_cloud(cx + s * 0.13, cy - s * 0.10, s * 0.60,
                        "#cbd5e1", "#94a3b8", QColor(255, 255, 255, 72), alpha=165)
            paint_cloud(cx - s * 0.04, cy + s * 0.08, s * 0.80,
                        "#ffffff", "#dbeafe", QColor(255, 255, 255, 185), alpha=248)

    elif kind == "rain":
        if is_light:
            paint_cloud(cx, cy - s * 0.10, s * 0.78,
                        "#f8fafc", "#94a3b8", QColor(71, 85, 105, 105), alpha=248)
            drop_col = QColor("#0284c7")
        else:
            paint_cloud(cx, cy - s * 0.10, s * 0.78,
                        "#f1f5f9", "#94a3b8", QColor(255, 255, 255, 148), alpha=242)
            drop_col = QColor("#38bdf8")
        painter.setPen(QPen(drop_col, max(1.35, s * 0.072),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for idx, ox in enumerate((-0.19, 0.0, 0.19)):
            dy = s * 0.035 if idx == 1 else 0.0
            x0 = cx + ox * s
            y0 = cy + s * 0.20 + dy
            painter.drawLine(QPointF(x0, y0), QPointF(x0 - s * 0.055, y0 + s * 0.15))

    elif kind == "snow":
        if is_light:
            paint_cloud(cx, cy - s * 0.10, s * 0.78,
                        "#ffffff", "#cbd5e1", QColor(100, 116, 139, 96), alpha=250)
            flake_col = QColor("#0ea5e9")
        else:
            paint_cloud(cx, cy - s * 0.10, s * 0.78,
                        "#ffffff", "#dbeafe", QColor(255, 255, 255, 168), alpha=244)
            flake_col = QColor("#bae6fd")
        for idx, ox in enumerate((-0.19, 0.0, 0.19)):
            fx = cx + ox * s
            fy = cy + s * (0.31 if idx == 1 else 0.26)
            if s >= 26.0:
                arm = s * 0.068
                painter.setPen(QPen(flake_col, max(1.1, s * 0.046),
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for deg in (0, 60, 120):
                    rad = math.radians(deg)
                    dx, dy = math.cos(rad) * arm, math.sin(rad) * arm
                    painter.drawLine(QPointF(fx - dx, fy - dy), QPointF(fx + dx, fy + dy))
            else:
                r_dot = max(1.25, s * 0.052)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(flake_col)
                painter.drawEllipse(QPointF(fx, fy), r_dot, r_dot)

    elif kind == "storm":
        if is_light:
            paint_cloud(cx, cy - s * 0.11, s * 0.78,
                        "#cbd5e1", "#64748b", QColor(51, 65, 85, 115), alpha=248)
            drop_col = QColor("#0284c7")
        else:
            paint_cloud(cx, cy - s * 0.11, s * 0.78,
                        "#cbd5e1", "#475569", QColor(226, 232, 240, 115), alpha=245)
            drop_col = QColor("#38bdf8")

        # 两侧斜雨滴点缀
        painter.setPen(QPen(drop_col, max(1.2, s * 0.064),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for ox in (-0.22, 0.21):
            x0 = cx + ox * s
            y0 = cy + s * 0.21
            painter.drawLine(QPointF(x0, y0), QPointF(x0 - s * 0.048, y0 + s * 0.13))

        # 琥珀金锐利闪电多边形
        bolt = QPolygonF([
            QPointF(cx + s * 0.04, cy + s * 0.08),
            QPointF(cx - s * 0.09, cy + s * 0.27),
            QPointF(cx + s * 0.00, cy + s * 0.27),
            QPointF(cx - s * 0.05, cy + s * 0.45),
            QPointF(cx + s * 0.11, cy + s * 0.23),
            QPointF(cx + s * 0.02, cy + s * 0.23),
        ])
        bolt_grad = QLinearGradient(cx, cy + s * 0.08, cx, cy + s * 0.45)
        bolt_grad.setColorAt(0.0, QColor("#fde047"))
        bolt_grad.setColorAt(1.0, QColor("#f59e0b"))
        painter.setPen(QPen(QColor("#fbbf24"), max(0.6, s * 0.02),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(bolt_grad)
        painter.drawPolygon(bolt)

    else:  # "fog"
        if is_light:
            paint_cloud(cx, cy - s * 0.12, s * 0.74,
                        "#ffffff", "#cbd5e1", QColor(100, 116, 139, 92), alpha=238)
            base_mist = QColor(100, 116, 139)
        else:
            paint_cloud(cx, cy - s * 0.12, s * 0.74,
                        "#f1f5f9", "#cbd5e1", QColor(255, 255, 255, 135), alpha=224)
            base_mist = QColor(203, 213, 225)
        for i, (half_w, alpha) in enumerate(((0.28, 200), (0.23, 145), (0.17, 95))):
            mc = QColor(base_mist)
            mc.setAlpha(alpha)
            painter.setPen(QPen(mc, max(1.3, s * 0.062),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            y = cy + s * (0.18 + i * 0.105)
            painter.drawLine(QPointF(cx - half_w * s, y), QPointF(cx + half_w * s, y))

    painter.restore()

# 尺寸规格常量
MARGIN_SHADOW = 24  # 纯 QPainter 高斯软阴影呼吸边距

# 2x2 规格 (同 BigFolderCard 100% 绝对一致)
CARD_W_2X2 = 248
CARD_H_2X2 = 248
WIN_W_2X2 = CARD_W_2X2 + MARGIN_SHADOW * 2  # 296
WIN_H_2X2 = CARD_H_2X2 + MARGIN_SHADOW * 2  # 296

# 2x1 紧凑规格
CARD_W_2X1 = 248
CARD_H_2X1 = 120
WIN_W_2X1 = CARD_W_2X1 + MARGIN_SHADOW * 2  # 296
WIN_H_2X1 = CARD_H_2X1 + MARGIN_SHADOW * 2  # 168


class CityPickerDialog(CenteredDialog):
    """
    城市与地区切换对话框:
    - 快速选择热门城市 (成都、北京、上海、广州、深圳、重庆、杭州、武汉、南京、西安等)
    - 任意城市/区县中文输入与在线地理编码
    - 自动网络定位 (IP) 恢复开关
    """
    districts_ready = Signal(int, list)

    def __init__(self, current_city: str = "成都", auto_location: bool = True,
                 current_district: str = "", current_lat=None, current_lon=None,
                 parent=None, theme: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("切换城市与地区")
        self.setFixedSize(384, 344)
        self._pending_district = current_district
        self._pending_lat = current_lat
        self._pending_lon = current_lon
        self._district_req_seq = 0
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        raw_theme = theme
        if not raw_theme and parent is not None:
            raw_theme = getattr(parent, "theme", None)
            if not raw_theme and getattr(parent, "config_manager", None) is not None:
                try:
                    raw_theme = parent.config_manager.get("theme", "dark")
                except Exception:
                    raw_theme = None
        if not raw_theme:
            try:
                from config_manager import ConfigManager
                raw_theme = ConfigManager().get("theme", "dark")
            except Exception:
                raw_theme = "dark"
        t_norm = str(raw_theme or "dark").strip().lower()
        self.theme = "oled" if t_norm == "black" else (t_norm if t_norm in ("light", "dark", "oled") else "dark")
        self.setProperty("theme", self.theme)

        self._apply_dialog_theme(self.theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        lbl_hint = QLabel("预设热门城市")
        layout.addWidget(lbl_hint)

        self.combo = QComboBox(self)
        city_names = list(POPULAR_CITIES.keys())
        self.combo.addItems(city_names)
        idx = self.combo.findText(current_city)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)

        lbl_custom = QLabel("自定义城市 / 县市")
        layout.addWidget(lbl_custom)
        self.input_city = QLineEdit(self)
        self.input_city.setPlaceholderText("例如：绵阳、乐山、顺德、昆山、杭州…")
        self.input_city.setText(current_city)
        self.combo.currentTextChanged.connect(self.input_city.setText)
        self.input_city.editingFinished.connect(self._reload_districts)
        layout.addWidget(self.input_city)

        lbl_district = QLabel("所属区县（可选）")
        layout.addWidget(lbl_district)
        self.combo_district = QComboBox(self)
        self.combo_district.addItem("加载中…", None)
        self.combo_district.setEnabled(False)
        layout.addWidget(self.combo_district)
        self.combo.currentTextChanged.connect(lambda _t: self._reload_districts())
        self.districts_ready.connect(self._apply_districts)
        self._reload_districts()

        self.chk_auto = FlatCheckBox("自动根据网络 IP 识别定位", self)
        self.chk_auto.setProperty("theme", self.theme)
        self.chk_auto.setChecked(auto_location)
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self.chk_auto)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.addStretch()

        btn_cancel = QPushButton("取消", self)
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("保存", self)
        btn_ok.setObjectName("btnPrimary")
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def showEvent(self, event):
        super().showEvent(event)
        if self.internalWinId():
            getattr(ui_kit, "apply_win11_window_chrome", lambda *a: None)(self, self.theme)

    def _apply_dialog_theme(self, theme: str):
        is_light = theme == "light"
        is_oled = theme == "oled"
        chev_normal = get_ui_chevron_icon_path(False).replace("\\", "/")
        chev_hover = get_ui_chevron_icon_path(True).replace("\\", "/")

        if is_light:
            dlg_bg = "#f3f5f8"
            fg = "#0f172a"
            lbl_fg = "#334155"
            field_bg = "#ffffff"
            field_hover_bg = "#ffffff"
            field_border = "rgba(15, 23, 42, 0.14)"
            field_hover_border = "rgba(15, 23, 42, 0.26)"
            dis_bg = "rgba(15, 23, 42, 0.04)"
            dis_fg = "#94a3b8"
            dis_border = "rgba(15, 23, 42, 0.08)"
            popup_bg = "#ffffff"
            popup_fg = "#0f172a"
            popup_border = "rgba(15, 23, 42, 0.14)"
            item_hover = "rgba(59, 130, 246, 0.10)"
            btn_bg = "#ffffff"
            btn_fg = "#334155"
            btn_border = "rgba(15, 23, 42, 0.14)"
            btn_hover = "#f8fafc"
        else:
            dlg_bg = "#07080a" if is_oled else "#111318"
            fg = "#f8fafc"
            lbl_fg = "#cbd5e1"
            field_bg = "rgba(255, 255, 255, 0.06)"
            field_hover_bg = "rgba(255, 255, 255, 0.09)"
            field_border = "rgba(255, 255, 255, 0.14)"
            field_hover_border = "rgba(255, 255, 255, 0.26)"
            dis_bg = "rgba(255, 255, 255, 0.02)"
            dis_fg = "#64748b"
            dis_border = "rgba(255, 255, 255, 0.06)"
            popup_bg = "#111318" if is_oled else "#171a21"
            popup_fg = "#f1f5f9"
            popup_border = "rgba(255, 255, 255, 0.14)"
            item_hover = "rgba(255, 255, 255, 0.08)"
            btn_bg = "rgba(255, 255, 255, 0.06)"
            btn_fg = "#e2e8f0"
            btn_border = "rgba(255, 255, 255, 0.12)"
            btn_hover = "rgba(255, 255, 255, 0.12)"

        qss = f"""
            QDialog {{
                background-color: {dlg_bg};
                color: {fg};
                font-family: {_UI_FONT_CSS};
            }}
            QLabel {{
                color: {lbl_fg};
                font-family: {_UI_FONT_CSS};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QComboBox, QLineEdit {{
                background-color: {field_bg};
                color: {fg};
                border: 1px solid {field_border};
                border-radius: 8px;
                padding: 6px 32px 6px 12px;
                font-family: {_UI_FONT_CSS};
                font-size: 13px;
                min-height: 20px;
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
            }}
            QLineEdit {{
                padding-right: 12px;
            }}
            QComboBox:hover, QLineEdit:hover {{
                background-color: {field_hover_bg};
                border-color: {field_hover_border};
            }}
            QComboBox:focus, QLineEdit:focus {{
                background-color: {field_hover_bg};
                border: 1px solid #3b82f6;
            }}
            QComboBox:disabled, QLineEdit:disabled {{
                background-color: {dis_bg};
                color: {dis_fg};
                border-color: {dis_border};
            }}
            QComboBox {{
                combobox-popup: 0;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: url("{chev_normal}");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }}
            QComboBox::down-arrow:hover {{
                image: url("{chev_hover}");
            }}
            QComboBox QAbstractItemView {{
                background-color: {popup_bg};
                color: {popup_fg};
                border: 1px solid {popup_border};
                border-radius: 8px;
                padding: 4px;
                outline: none;
                font-family: {_UI_FONT_CSS};
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
            }}
            QComboBox QAbstractItemView::item {{
                height: 30px;
                padding: 0 8px;
                border-radius: 6px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {item_hover};
            }}
            QCheckBox {{
                color: {lbl_fg};
                font-family: {_UI_FONT_CSS};
                font-size: 13px;
                spacing: 8px;
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {btn_border};
                border-radius: 8px;
                padding: 7px 18px;
                font-family: {_UI_FONT_CSS};
                font-size: 13px;
                font-weight: 500;
                min-height: 18px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton#btnCancel {{
                background-color: {btn_bg};
                color: {btn_fg};
            }}
            QPushButton#btnCancel:hover {{
                background-color: {btn_hover};
            }}
            QPushButton#btnPrimary {{
                background-color: #3b82f6;
                color: #ffffff;
                border: 1px solid #2563eb;
                font-weight: 600;
            }}
            QPushButton#btnPrimary:hover {{
                background-color: #2563eb;
            }}
            QPushButton#btnPrimary:pressed {{
                background-color: #1d4ed8;
            }}
        """
        self.setStyleSheet(qss)

    def _reload_districts(self):
        city = self.input_city.text().strip() or self.combo.currentText()
        if not city:
            return
        region = POPULAR_CITIES.get(city, {}).get("region", "")
        self._district_req_seq += 1
        req_seq = self._district_req_seq
        self.combo_district.blockSignals(True)
        self.combo_district.clear()
        self.combo_district.addItem("加载中…", None)
        self.combo_district.setEnabled(False)
        self.combo_district.blockSignals(False)

        def work():
            try:
                from weather_service import fetch_city_districts, resolve_city_coordinates
                city_name, region_name = city, region
                if not region_name:
                    # 手输的非预设城市：先解析出省份，否则行政区划三级查询无法定位
                    resolved = resolve_city_coordinates(city)
                    if resolved:
                        city_name = resolved.get("city", city)
                        region_name = resolved.get("region", "")
                items = fetch_city_districts(city_name, region_name)
            except Exception:
                items = []
            try:
                self.districts_ready.emit(req_seq, items or [])
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _apply_districts(self, req_seq: int, items):
        if req_seq != getattr(self, "_district_req_seq", 0):
            return
        try:
            self.combo_district.blockSignals(True)
            self.combo_district.clear()
            self.combo_district.addItem("市级（不细分）", None)
            for item in (items or []):
                if isinstance(item, dict):
                    self.combo_district.addItem(_safe_str(item.get("name"), ""), item)
            self.combo_district.setEnabled(bool(items))
            if not items:
                self.combo_district.clear()
                self.combo_district.addItem("未查到该城市，可改填所属地级市", None)
            matched = False
            if self._pending_district:
                for i in range(self.combo_district.count()):
                    data = self.combo_district.itemData(i)
                    if isinstance(data, dict) and data.get("name") == self._pending_district:
                        self.combo_district.setCurrentIndex(i)
                        matched = True
                        break
            # 天府新区这类非标准行政区不在 DataV 列表里，用已保存坐标补一条，避免「当前选择」丢失
            if self._pending_district and not matched and self._pending_lat is not None:
                self.combo_district.insertItem(1, self._pending_district,
                                               {"name": self._pending_district,
                                                "lat": self._pending_lat, "lon": self._pending_lon})
                self.combo_district.setCurrentIndex(1)
            self.combo_district.blockSignals(False)
        except RuntimeError:
            pass

    def _on_auto_toggled(self, checked: bool):
        self.combo.setEnabled(not checked)
        self.input_city.setEnabled(not checked)

    def get_result(self) -> Dict[str, Any]:
        is_auto = self.chk_auto.isChecked()
        chosen = self.input_city.text().strip() or self.combo.currentText()
        res = resolve_city_coordinates(chosen)
        city = res["city"] if res else chosen
        region = res.get("region", "") if res else ""
        lat = res["lat"] if res else None
        lon = res["lon"] if res else None

        district = ""
        picked = self.combo_district.currentData() if hasattr(self, "combo_district") else None
        if isinstance(picked, dict) and picked.get("name"):
            district = picked["name"]
            lat = picked.get("lat", lat)
            lon = picked.get("lon", lon)

        return {
            "auto_location": is_auto,
            "city": city,
            "region": region,
            "district": district,
            "lat": lat,
            "lon": lon,
        }


class WeatherWidgetCard(QWidget):
    """超椭圆液态玻璃气象卡片绘制部件"""

    _chevron_rect = None

    def __init__(self, parent_window, theme: str = "dark", widget_size: str = "2x2"):
        super().__init__(parent_window)
        self.win = parent_window
        self.theme = theme
        self.widget_size = widget_size
        self.radius = float(getattr(parent_window, "radius", 26.0))
        self.opacity = float(getattr(parent_window, "opacity", 0.88))
        if parent_window is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.service: WeatherService = get_weather_service()
        self.weather_data: Dict[str, Any] = self.service.get_cached_data()

        self._signals_connected = False
        self._connect_service_signals()
        self.setMouseTracking(True)

    def _connect_service_signals(self):
        if getattr(self, "_signals_connected", False):
            return
        self._signals_connected = True
        if getattr(self, "service", None) is not None:
            self.service.weather_updated.connect(self.on_weather_updated)

    def _disconnect_service_signals(self):
        if not getattr(self, "_signals_connected", False):
            return
        self._signals_connected = False
        if getattr(self, "service", None) is not None:
            try:
                self.service.weather_updated.disconnect(self.on_weather_updated)
            except Exception:
                pass

    def on_weather_updated(self, data: Dict[str, Any]):
        self.weather_data = data
        self.update()

    def set_glass_params(
        self,
        radius: Optional[float] = None,
        opacity: Optional[float] = None,
        theme: Optional[str] = None,
    ):
        if radius is not None:
            self.radius = float(radius)
        if opacity is not None:
            self.opacity = max(0.05, min(1.0, float(opacity)))
        if theme is not None:
            self.theme = theme
        self.update()

    def set_theme(self, theme: str):
        self.theme = theme
        self.update()

    def set_widget_size(self, size_mode: str):
        self.widget_size = size_mode
        self.update()

    def _paint_compact(self, painter, w, h, cls):
        """mini / tall 档独立排版：只保留该尺寸下最重要的信息，不做整体缩放。"""
        data = self.weather_data if isinstance(self.weather_data, dict) else {}
        city = _safe_str(data.get("city"), "成都")
        region = _safe_str(data.get("region"), "")
        district = _safe_str(data.get("district"), "")
        short_city = city[:-1] if city.endswith("市") else city
        loc = f"{short_city}·{district}" if district else (f"{city}" if not region or region == city else f"{city}·{region}")
        temp = _safe_int(data.get("temp"), 22)
        icon = data.get("icon") if data.get("icon") is not None else "⛅"
        cond = _safe_str(data.get("condition"), "多云")
        t_max = _safe_int(data.get("temp_max"), 25)
        t_min = _safe_int(data.get("temp_min"), 18)
        humidity = _safe_int(data.get("humidity"), 60)
        feels_val = _safe_int(data.get("feels_like"), temp)
        feels_color = QColor(180, 83, 9, 235) if self.theme == "light" else QColor(251, 191, 36, 235)

        op = max(0.05, min(1.0, float(self.opacity)))
        if self.theme == "light":
            fill_top, fill_bottom = QColor(250, 251, 253, int(150 * op)), QColor(236, 239, 244, int(145 * op))
            text_pri, text_sec = QColor(15, 23, 42, 245), QColor(51, 65, 85, 220)
        elif self.theme in ("black", "oled"):
            fill_top, fill_bottom = QColor(18, 19, 23, int(150 * op)), QColor(8, 9, 11, int(155 * op))
            text_pri, text_sec = QColor(255, 255, 255, 245), QColor(210, 215, 230, 190)
        else:
            fill_top, fill_bottom = QColor(38, 41, 48, int(150 * op)), QColor(24, 26, 31, int(155 * op))
            text_pri, text_sec = QColor(255, 255, 255, 245), QColor(210, 218, 235, 190)

        if self.win is None:
            rect = QRect(0, 0, w, h)
            radius = min(self.radius, min(w, h) / 3.0)
            path = create_squircle_path(rect, radius)
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, fill_top)
            grad.setColorAt(1.0, fill_bottom)
            painter.fillPath(path, grad)
            draw_liquid_glass(painter, path, rect, radius, self.theme)

        pad = max(10, int(min(w, h) * 0.10))
        if cls == "mini":
            painter.setPen(text_sec)
            painter.setFont(_wfont(max(8, int(min(w, h) * 0.085)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, pad, w - pad * 2, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, city)
            painter.setPen(text_pri)
            painter.setFont(_wfont(max(18, int(min(w, h) * 0.34)), QFont.Weight.DemiBold, display=True))
            painter.drawText(QRect(pad, int(h * 0.30), int(w * 0.62), int(h * 0.42)),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{temp}°")
            draw_weather_icon(painter, QRectF(w - pad - int(w * 0.30), int(h * 0.30),
                                              int(w * 0.30), int(h * 0.36)), icon, text_pri)
        else:
            painter.setPen(text_sec)
            painter.setFont(_wfont(max(9, int(w * 0.085)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, pad, w - pad * 2, 20),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, loc)
            painter.setPen(text_pri)
            painter.setFont(_wfont(max(22, int(w * 0.36)), QFont.Weight.DemiBold, display=True))
            painter.drawText(QRect(pad, int(h * 0.20), w - pad * 2, int(h * 0.30)),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{temp}°")
            draw_weather_icon(painter, QRectF(w - pad - int(w * 0.34), int(h * 0.19),
                                              int(w * 0.34), int(h * 0.28)), icon, text_pri)
            painter.setPen(text_sec)
            painter.setFont(_wfont(max(9, int(w * 0.075)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, int(h * 0.56), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cond)
            painter.setFont(_wfont(max(9, int(w * 0.075)), QFont.Weight.Medium, display=True))
            painter.drawText(QRect(pad, int(h * 0.66), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"↑ {t_max}°   ↓ {t_min}°")
            painter.setFont(_wfont(max(9, int(w * 0.075))))
            painter.drawText(QRect(pad, int(h * 0.76), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"湿度 {humidity}%")
            # 窄档把体感单列一行并强调，避免和湿度挤在同一行被右边界裁掉
            painter.setFont(_wfont(max(9, int(w * 0.075)), QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(pad, int(h * 0.86), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"体感 {feels_val}°")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # 内容按 248×248 / 248×120 基准等比缩放并居中；x/y 分别缩放会把行距压塌、文字重叠。
        # 底板单独用「真实卡片矩形」绘制，保证玻璃始终铺满整个卡片。
        real_w, real_h = self.width(), self.height()
        cls = widget_size_class(real_w, real_h)
        if cls in ("mini", "tall"):
            self._paint_compact(painter, real_w, real_h, cls)
            return
        use_wide = cls == "wide" or (cls == "regular" and self.widget_size == "2x1")
        base_w = 248.0
        base_h = 120.0 if use_wide else 248.0
        s = min(real_w / base_w, real_h / base_h) if real_w > 0 and real_h > 0 else 1.0
        s = max(0.35, s)
        ox, oy = (real_w - base_w * s) / 2.0, (real_h - base_h * s) / 2.0
        painter.translate(ox, oy)
        painter.scale(s, s)
        w, h = int(base_w), int(base_h)
        full = QRect(int(-ox / s), int(-oy / s), int(real_w / s), int(real_h / s))
        squircle = create_squircle_path(full, self.radius / s)

        # 1. 液态玻璃主体底色与文字色阶 (有宿主窗口时底板与外圈接触阴影由宿主统一缓存绘制，避免叠双层底色)
        op = max(0.05, min(1.0, float(self.opacity)))
        grad = QLinearGradient(full.left(), full.top(), full.left(), full.bottom())
        if self.theme == "light":
            grad.setColorAt(0.0, QColor(250, 251, 253, int(150 * op)))
            grad.setColorAt(1.0, QColor(236, 239, 244, int(145 * op)))
            text_pri = QColor(15, 23, 42, 248)
            text_sec = QColor(30, 41, 59, 224)
            text_muted = QColor(71, 85, 105, 220)
            divider_color = QColor(15, 23, 42, 26)
        elif self.theme in ("black", "oled"):
            grad.setColorAt(0.0, QColor(18, 19, 23, int(150 * op)))
            grad.setColorAt(1.0, QColor(8, 9, 11, int(155 * op)))
            text_pri = QColor(248, 250, 252, 248)
            text_sec = QColor(215, 222, 238, 205)
            text_muted = QColor(176, 188, 210, 196)
            divider_color = QColor(255, 255, 255, 22)
        else:
            grad.setColorAt(0.0, QColor(38, 41, 48, int(150 * op)))
            grad.setColorAt(1.0, QColor(24, 26, 31, int(155 * op)))
            text_pri = QColor(248, 250, 252, 248)
            text_sec = QColor(215, 222, 240, 205)
            text_muted = QColor(182, 194, 216, 196)
            divider_color = QColor(255, 255, 255, 24)

        if self.win is None:
            painter.fillPath(squircle, grad)
            draw_liquid_glass(painter, squircle, full, self.radius / s, self.theme)

        # 3. 绘制气象数据排版
        data = self.weather_data if isinstance(self.weather_data, dict) else {}
        city = _safe_str(data.get("city"), "成都")
        region = _safe_str(data.get("region"), "")
        district = _safe_str(data.get("district"), "")
        short_city = city[:-1] if city.endswith("市") else city
        if district:
            loc_str = f"{short_city} · {district}"
        elif region and region != city:
            loc_str = f"{city} · {region}"
        else:
            loc_str = city
        temp = _safe_float(data.get("temp"), 22.0)
        temp_str = f"{int(round(temp))}°"
        cond = _safe_str(data.get("condition"), "多云")
        icon = data.get("icon") if data.get("icon") is not None else "⛅"
        t_max = _safe_int(data.get("temp_max"), 25)
        t_min = _safe_int(data.get("temp_min"), 18)
        humidity = _safe_int(data.get("humidity"), 60)
        wind = _safe_float(data.get("wind"), 10.0)
        feels = _safe_float(data.get("feels_like"), temp)
        raw_precip = data.get("precip_prob")
        precip_prob = _safe_int(raw_precip, 0) if raw_precip is not None else None
        updated = _safe_str(data.get("updated_at"), "")

        weight_secondary = QFont.Weight.Medium if self.theme == "light" else QFont.Weight.Normal
        feels_color = QColor(180, 83, 9, 235) if self.theme == "light" else QColor(251, 191, 36, 235)

        if use_wide:
            # 宽扁档（2x1）：左侧大数字温度 + 主矢量天气图标，右侧城市与状态，底部三段概览
            painter.setFont(_wfont(32, QFont.Weight.DemiBold, display=True))
            painter.setPen(text_pri)
            fm_t = painter.fontMetrics()
            tw = min(92, fm_t.horizontalAdvance(temp_str) + 4)
            painter.drawText(QRect(20, 15, tw, 50),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_str)

            icon_x = max(92, min(102, 20 + tw + 4))
            draw_weather_icon(painter, QRectF(icon_x, 19, 42, 42), icon, text_pri)

            painter.setFont(_wfont(11, QFont.Weight.DemiBold))
            painter.setPen(text_pri)
            fm_loc_w = painter.fontMetrics()
            rx = icon_x + 48
            rw = max(56, w - rx - 18)
            painter.drawText(QRect(rx, 19, rw, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm_loc_w.elidedText(loc_str, Qt.TextElideMode.ElideRight, rw))

            painter.setFont(_wfont(9, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(rx, 44, rw, 20),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cond)

            painter.setPen(QPen(divider_color, 0.75))
            painter.drawLine(20, 74, w - 20, 74)

            painter.setFont(_wfont(9, weight_secondary))
            painter.setPen(text_muted)
            wide_extra = f"降水 {int(precip_prob)}%" if precip_prob is not None else f"风 {int(wind)}级"
            feels_text = f"体感 {int(round(feels))}°"
            update_text = f"{updated}" if updated else ""
            fm = painter.fontMetrics()
            feels_w = fm.horizontalAdvance(feels_text) + 12
            upd_w = fm.horizontalAdvance(update_text) + 10 if update_text else 0
            left_w = max(56, w - 40 - feels_w - upd_w)
            left_base = f"↑{t_max}°  ↓{t_min}°"
            left_full = f"{left_base} · {wide_extra}"
            bot_text = left_full if fm.horizontalAdvance(left_full) <= left_w else left_base
            bot_text = fm.elidedText(bot_text, Qt.TextElideMode.ElideRight, left_w)
            painter.drawText(QRect(20, 81, left_w, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, bot_text)

            painter.setFont(_wfont(9, QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(20, 81, w - 40 - upd_w, 22),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, feels_text)
            if update_text:
                painter.setFont(_wfont(8, weight_secondary, display=True))
                painter.setPen(text_muted)
                painter.drawText(QRect(20, 81, w - 40, 22),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, update_text)
        else:
            # 2x2 正方排版：位置▾ + 实况短语 / 醒目大温度 + 主天气图标 + 高低体感 / 6 时段逐时 / 3 日温度带
            hours_data = data.get("hours") or []
            days_data = data.get("days") or []

            painter.setFont(_wfont(11, QFont.Weight.DemiBold))
            painter.setPen(text_pri)
            fm_head = painter.fontMetrics()
            max_head_w = max(60, w - 130)
            elided_loc = fm_head.elidedText(loc_str, Qt.TextElideMode.ElideRight, max_head_w)
            head_w = min(int(fm_head.horizontalAdvance(elided_loc)), max_head_w)
            painter.drawText(QRect(20, 14, head_w + 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_loc)

            cx_chev = 20 + head_w + 9
            painter.setPen(QPen(text_muted, 1.4, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(cx_chev - 3, 23.5), QPointF(cx_chev, 26.5))
            painter.drawLine(QPointF(cx_chev, 26.5), QPointF(cx_chev + 3, 23.5))
            self._chevron_rect = QRect(14, 8, head_w + 28, 32)

            painter.setFont(_wfont(9, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(w - 104, 14, 84, 22),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, cond)

            painter.setFont(_wfont(38, QFont.Weight.Light, display=True))
            painter.setPen(text_pri)
            fm_big = painter.fontMetrics()
            big_w = fm_big.horizontalAdvance(temp_str)
            painter.drawText(QRect(18, 38, 128, 56),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_str)

            # 主天气矢量图标嵌在温度与右侧高低温之间，呼吸感充沛且绝不压字
            hero_x = 18 + big_w + 6
            if hero_x + 36 <= w - 88:
                draw_weather_icon(painter, QRectF(hero_x, 47, 38, 38), icon, text_pri)

            painter.setFont(_wfont(9, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(w - 104, 42, 84, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"最高 {t_max}°")
            painter.drawText(QRect(w - 104, 60, 84, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"最低 {t_min}°")
            painter.setFont(_wfont(9, QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(w - 104, 78, 84, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"体感 {int(round(feels))}°")

            if hours_data:
                cols = [hr for hr in hours_data if isinstance(hr, dict)][:6]
                if cols:
                    col_w = (w - 40) / len(cols)
                    for i, hr in enumerate(cols):
                        cx = 20 + col_w * i
                        label = _safe_str(hr.get("t"), "").replace(":00", "时")
                        painter.setFont(_wfont(8, weight_secondary))
                        painter.setPen(text_muted)
                        painter.drawText(QRectF(cx, 102, col_w, 14), Qt.AlignmentFlag.AlignCenter, label)
                        draw_weather_icon(painter, QRectF(cx + (col_w - 20) * 0.5, 118, 20, 20),
                                          hr.get("code", 0), text_pri)
                        painter.setFont(_wfont(9, QFont.Weight.DemiBold, display=True))
                        painter.setPen(text_pri)
                        painter.drawText(QRectF(cx, 140, col_w, 18), Qt.AlignmentFlag.AlignCenter,
                                         f"{_safe_int(hr.get('temp'), 0)}°")

            painter.setPen(QPen(divider_color, 0.75))
            painter.drawLine(20, 164, w - 20, 164)

            valid_days = [d for d in days_data if isinstance(d, dict)] if days_data else []
            if valid_days:
                base_min = min(_safe_float(d.get("min"), 0.0) for d in valid_days)
                base_max = max(_safe_float(d.get("max"), 1.0) for d in valid_days)
                span = max(1.0, base_max - base_min)
                labels = ("今天", "明天", "后天")
                track_dark = self.theme != "light"
                for i, day in enumerate(valid_days[:3]):
                    d_min = _safe_float(day.get("min"), 0.0)
                    d_max = _safe_float(day.get("max"), d_min)
                    y = 172 + i * 22
                    painter.setFont(_wfont(9, weight_secondary))
                    painter.setPen(text_sec)
                    painter.drawText(QRect(20, y, 38, 20),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     labels[i] if i < len(labels) else "")
                    draw_weather_icon(painter, QRectF(60, y + 1, 22, 18),
                                      day.get("code", 0), text_sec)
                    painter.setFont(_wfont(9, weight_secondary, display=True))
                    painter.setPen(text_muted)
                    painter.drawText(QRect(84, y, 30, 20),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                     f"{int(round(d_min))}°")
                    track = QRectF(120, y + 8, 62, 4.5)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 26) if track_dark else QColor(15, 23, 42, 20))
                    painter.drawRoundedRect(track, 2.25, 2.25)
                    lo = track.left() + (d_min - base_min) / span * track.width()
                    hi = track.left() + (d_max - base_min) / span * track.width()
                    bar_rect = QRectF(lo, track.top(), max(6.0, hi - lo), track.height())
                    bar_grad = QLinearGradient(bar_rect.left(), 0, bar_rect.right(), 0)
                    if d_max >= 24.0:
                        bar_grad.setColorAt(0.0, QColor("#38bdf8") if track_dark else QColor("#0284c7"))
                        bar_grad.setColorAt(1.0, QColor("#fbbf24") if track_dark else QColor("#f59e0b"))
                    else:
                        bar_grad.setColorAt(0.0, QColor("#60a5fa") if track_dark else QColor("#2563eb"))
                        bar_grad.setColorAt(1.0, QColor("#38bdf8") if track_dark else QColor("#0ea5e9"))
                    painter.setBrush(bar_grad)
                    painter.drawRoundedRect(bar_rect, 2.25, 2.25)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setFont(_wfont(9, QFont.Weight.DemiBold, display=True))
                    painter.setPen(text_pri)
                    painter.drawText(QRect(w - 56, y, 36, 20),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                     f"{int(round(d_max))}°")
            else:
                painter.setFont(_wfont(9, weight_secondary))
                painter.setPen(text_sec)
                painter.drawText(QRect(20, 176, w - 40, 22),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 f"湿度 {humidity}%   体感 {int(round(feels))}°")

from desktop_widgets import (
    ResizableWidgetMixin, widget_size_class, add_common_widget_actions,
    _get_widget_menu_qss, _render_widget_glass_bg, _left_button_held,
)


class WeatherDetailPopup(QWidget):
    """点击天气卡弹出的详情面板：逐时预报、未来三天、空气与日出日落。"""

    W, H = 468, 344

    def __init__(self, data: dict, theme: str = "dark", parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(self.W, self.H)
        self.data = data if isinstance(data, dict) else {}
        self.theme = theme

    def paintEvent(self, event):
        d = self.data if isinstance(self.data, dict) else {}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(1, 1, self.W - 2, self.H - 2)
        path = create_squircle_path(rect, 22)
        if self.theme == "light":
            bg = QColor(248, 250, 252, 248)
            border = QColor(15, 23, 42, 28)
            pri = QColor(15, 23, 42)
            sec = QColor(51, 65, 85)
            muted = QColor(100, 116, 139)
        else:
            bg = QColor(17, 19, 24, 246)
            border = QColor(255, 255, 255, 34)
            pri = QColor(248, 250, 252)
            sec = QColor(203, 213, 225)
            muted = QColor(148, 163, 184)
        p.fillPath(path, bg)
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        city = _safe_str(d.get("city"), "")
        district = _safe_str(d.get("district"), "")
        short_city = city[:-1] if city.endswith("市") else city
        loc = f"{short_city} · {district}" if district else (city or "未知地区")

        temp_val = _safe_int(d.get("temp"), 22)
        feels_val = _safe_int(d.get("feels_like"), temp_val)
        cond_str = _safe_str(d.get("condition"), "—")
        icon_src = d.get("icon") if d.get("icon") is not None else "⛅"

        p.setFont(_wfont(12, QFont.Weight.DemiBold)); p.setPen(pri)
        p.drawText(QRect(22, 16, 320, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, loc)

        p.setFont(_wfont(30, QFont.Weight.Light, display=True)); p.setPen(pri)
        p.drawText(QRect(22, 44, 120, 46), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{temp_val}°")
        draw_weather_icon(p, QRectF(self.W - 72, 26, 48, 48), icon_src, pri)

        p.setFont(_wfont(10, QFont.Weight.Medium)); p.setPen(sec)
        p.drawText(QRect(142, 50, 240, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{cond_str}  ·  体感 {feels_val}°")

        aqi_lvl = _safe_str(d.get("aqi_level"), "")
        aqi_num = d.get("aqi")
        aqi_text = f"空气 {aqi_lvl} {_safe_int(aqi_num, 0)}".strip() if (aqi_lvl and aqi_num is not None) else (f"空气 {aqi_lvl}" if aqi_lvl else "空气 —")
        p.setFont(_wfont(9)); p.setPen(muted)
        p.drawText(QRect(142, 73, 290, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{aqi_text}    湿度 {_safe_int(d.get('humidity'), 0)}%    风 {_safe_int(d.get('wind'), 0)}级")

        # 逐时（最多 10 个小时）
        raw_hours = d.get("hours") or []
        hours = [h for h in raw_hours if isinstance(h, dict)][:10]
        p.setPen(QPen(border, 1)); p.drawLine(22, 104, self.W - 22, 104)
        if hours:
            col_w = (self.W - 44) / len(hours)
            for i, h in enumerate(hours):
                cx = 22 + col_w * i + col_w / 2
                p.setFont(_wfont(8, display=True)); p.setPen(muted)
                p.drawText(QRectF(cx - col_w / 2, 112, col_w, 16), Qt.AlignmentFlag.AlignCenter, _safe_str(h.get("t"), ""))
                draw_weather_icon(p, QRectF(cx - 11, 130, 22, 22),
                                  h.get("code", 0), pri)
                p.setFont(_wfont(10, QFont.Weight.DemiBold, display=True)); p.setPen(pri)
                p.drawText(QRectF(cx - col_w / 2, 153, col_w, 20), Qt.AlignmentFlag.AlignCenter,
                           f"{_safe_int(h.get('temp'), 0)}°")
                raw_prob = h.get("prob")
                prob = _safe_int(raw_prob, 0) if raw_prob is not None else None
                p.setFont(_wfont(8, display=True))
                p.setPen(QColor("#3b82f6") if (prob is not None and prob >= 30) else muted)
                p.drawText(QRectF(cx - col_w / 2, 173, col_w, 16), Qt.AlignmentFlag.AlignCenter,
                           f"{prob}%" if prob is not None else "")

        # 未来三天
        raw_days = d.get("days") or []
        days = [day for day in raw_days if isinstance(day, dict)][:3]
        p.setPen(QPen(border, 1)); p.drawLine(22, 198, self.W - 22, 198)
        labels = ("今天", "明天", "后天")
        for i, day in enumerate(days):
            y = 208 + i * 30
            p.setFont(_wfont(9, QFont.Weight.Medium)); p.setPen(sec)
            p.drawText(QRect(24, y, 60, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       labels[i] if i < len(labels) else "")
            draw_weather_icon(p, QRectF(88, y, 24, 22), day.get("code", 0), pri)
            p.setFont(_wfont(9, QFont.Weight.DemiBold, display=True)); p.setPen(pri)
            p.drawText(QRect(122, y, 120, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{_safe_int(day.get('min'), 0)}° ~ {_safe_int(day.get('max'), 0)}°")
            raw_prob = day.get("prob")
            prob = _safe_int(raw_prob, 0) if raw_prob is not None else None
            p.setFont(_wfont(9)); p.setPen(QColor("#3b82f6") if (prob is not None and prob >= 30) else muted)
            p.drawText(QRect(self.W - 120, y, 96, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"降水 {prob}%" if prob is not None else "")

        # 底部：紫外线与日出日落
        p.setPen(QPen(border, 1)); p.drawLine(22, 300, self.W - 22, 300)
        raw_uv = d.get("uv")
        uv_str = str(round(_safe_float(raw_uv, 0.0), 1)) if raw_uv is not None else "—"
        sunrise_str = _safe_str(d.get("sunrise"), "—")
        sunset_str = _safe_str(d.get("sunset"), "—")
        upd_str = _safe_str(d.get("updated_at"), "")
        upd_tail = f"    {upd_str} 更新" if upd_str else ""
        p.setFont(_wfont(9)); p.setPen(muted)
        p.drawText(QRect(22, 308, self.W - 44, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"紫外线 {uv_str}    日出 {sunrise_str}    日落 {sunset_str}{upd_tail}")
        p.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)




class WeatherWidgetWindow(ResizableWidgetMixin, SnapDragMixin, QWidget):
    """
    桌面超椭圆天气小组件宿主窗口:
    - 拥有与 BigFolderWindow 完全一致的透明背景、纯 QPainter 软阴影与 Win32 桌面层锚定;
    - Win+D 绝对免疫，WM_NCHITTEST 透明边缘 100% 穿透;
    - 拖拽跟手 + 松手落到桌面网格（轮廓预览）。
    """

    position_changed = Signal(int, int)
    size_changed = Signal(str)
    resized = Signal(int, int)
    closed = Signal()

    def __init__(
        self,
        x: int = 614,
        y: int = 180,
        theme: str = "dark",
        widget_size: str = "2x2",
        locked: bool = False,
        config_manager: Optional[Any] = None,
        parent=None
    ):
        super().__init__(parent)
        self.theme = theme
        self.widget_size = widget_size
        self.locked = locked
        self.config_manager = config_manager
        self.radius = 26
        self.opacity = 0.88
        if self.config_manager is not None and hasattr(self.config_manager, "get"):
            try:
                self.opacity = max(0.05, min(1.0, float(self.config_manager.get("opacity", 0.88))))
                self.radius = int(self.config_manager.get("border_radius", 26))
            except Exception:
                pass
        self._bg_cache = None
        self._bg_key = None
        self._dragging: bool = False
        self._drag_start_pos: QPoint = QPoint()
        self._window_start_pos: QPoint = QPoint()
        self._has_moved: bool = False
        self._detail_popup: Optional[WeatherDetailPopup] = None
        self.card_w = CARD_W_2X2
        self.card_h = CARD_H_2X2
        self._drag_watch = QTimer(self)
        self._drag_watch.setInterval(120)
        self._drag_watch.timeout.connect(self._drag_watch_tick)
        self._init_resize_state()
        self._snap_init()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 内部卡片
        self.card = WeatherWidgetCard(self, theme=self.theme, widget_size=self.widget_size)
        self.card.set_glass_params(self.radius, self.opacity, self.theme)
        self._apply_geometry_mode(widget_size, initial_pos=QPoint(x, y))

        # 若提供了 config_manager，恢复自定义城市与定位偏好
        if self.config_manager:
            w_cfg = self.config_manager.get_weather_config()
            auto_loc = w_cfg.get("auto_location", True)
            city = w_cfg.get("city", "成都")
            reg = w_cfg.get("region", "四川")
            lat = w_cfg.get("lat", 30.6586)
            lon = w_cfg.get("lon", 104.0648)
            district = w_cfg.get("district", "")
            ws = get_weather_service()
            ws.set_custom_location(city=city, region=reg, lat=lat, lon=lon,
                                   auto_location=auto_loc, district=district)
            # 卡片构造在位置注入之前，先用已固定的区县刷新一次，不等网络回来
            self.card.weather_data = ws.get_cached_data()
            self.card.update()

        # 启动后台天气刷新轮询 (每 30 分钟)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_weather)
        self.refresh_timer.start(1800000)  # 30 min

        # 首次启动异步拉取
        QTimer.singleShot(500, self.refresh_weather)

    def _apply_geometry_mode(self, size_mode: str, initial_pos: Optional[QPoint] = None):
        """应用 2x2 正方或 2x1 紧凑规格"""
        self.widget_size = size_mode
        cur_pos = initial_pos or self.pos()

        if size_mode == "2x1":
            self.card_w, self.card_h = CARD_W_2X1, CARD_H_2X1
        else:
            self.card_w, self.card_h = CARD_W_2X2, CARD_H_2X2
        self._update_window_size()

        self.card.set_widget_size(size_mode)
        self.move(cur_pos)
        self.update()

    def _update_window_size(self):
        self._bg_cache = None
        self._bg_key = None
        self.setFixedSize(self.card_w + MARGIN_SHADOW * 2, self.card_h + MARGIN_SHADOW * 2)
        self.card.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.update()

    def get_card_rect(self):
        return QRect(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)

    def on_card_resized(self):
        super().on_card_resized()
        self.card.update()

    def refresh_weather(self):
        """触发天气服务异步刷新"""
        get_weather_service().fetch_async(force=True)

    def set_glass_params(
        self,
        radius: Optional[int] = None,
        opacity: Optional[float] = None,
        theme: Optional[str] = None,
    ):
        if radius is not None:
            self.radius = int(radius)
        if opacity is not None:
            self.opacity = max(0.05, min(1.0, float(opacity)))
        self._bg_cache = None
        self._bg_key = None
        if theme is not None and theme != self.theme:
            self.theme = theme
        self.card.set_glass_params(self.radius, self.opacity, self.theme)
        self.update()

    def set_theme(self, theme: str):
        self.theme = theme
        self._bg_cache = None
        self._bg_key = None
        self.card.set_theme(theme)
        self.update()

    def paintEvent(self, event):
        if self.card is not None and self.card.isHidden():
            return
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        key = (self.width(), self.height(), int(self.radius), round(float(self.opacity), 3), self.theme, round(dpr, 3))
        if self._bg_cache is None or self._bg_key != key:
            self._bg_cache = _render_widget_glass_bg(
                self.size(),
                QRectF(self.get_card_rect()),
                float(self.radius),
                float(self.opacity),
                self.theme,
                draw_shadow=True,
                dpr=dpr,
            )
            self._bg_key = key
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._bg_cache is not None and not self._bg_cache.isNull():
            painter.drawPixmap(
                QRectF(0.0, 0.0, float(self.width()), float(self.height())),
                self._bg_cache,
                QRectF(0.0, 0.0, float(self._bg_cache.width()), float(self._bg_cache.height())),
            )
        painter.end()

    def showEvent(self, event):
        super().showEvent(event)
        if self.card is not None and hasattr(self.card, "_connect_service_signals"):
            self.card._connect_service_signals()
        t = getattr(self, "refresh_timer", None)
        if t is not None and not t.isActive():
            t.start(1800000)
        self._place_resize_grip()
        # 稳固锚定桌面图层
        desktop_layer.pin_to_desktop(int(self.winId()))
        QTimer.singleShot(60, lambda: desktop_layer.pin_to_desktop(int(self.winId())) if self.isVisible() else None)
        QTimer.singleShot(220, lambda: desktop_layer.pin_to_desktop(int(self.winId())) if self.isVisible() else None)

    def hideEvent(self, event):
        self._dragging = False
        self._resizing = False
        if hasattr(self, "_drag_watch"):
            self._drag_watch.stop()
        self._snap_hide()
        super().hideEvent(event)

    def nativeEvent(self, eventType, message):
        """Win32 原生消息过滤：拦截 WM_NCHITTEST 保证透明阴影边缘 100% 穿透"""
        try:
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))

            # 拦截 WM_NCHITTEST (0x0084)
            if msg.message == 0x0084:
                # 获取光标屏幕物理坐标
                x_pos = ctypes.c_short(msg.lParam & 0xFFFF).value
                y_pos = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                local_pt = self.mapFromGlobal(QPoint(x_pos, y_pos))

                # 若在卡片实体内响应交互，若在阴影边缘留白区则向系统返回透明穿透
                if self.card.geometry().contains(local_pt):
                    return True, 1  # HTCLIENT
                else:
                    return True, -1  # HTTRANSPARENT

            # 拦截 WM_SYSCOMMAND SC_MINIMIZE 免疫 Win+D 最小化
            elif msg.message == 0x0112:
                if (msg.wParam & 0xFFF0) == 0xF020:  # SC_MINIMIZE
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _finish_drag_if_moved(self):
        if not self._dragging:
            return
        self._dragging = False
        if self._has_moved:
            snapped = self._snap_commit()
            my_card_rect = QRect(self.pos() + self.card.pos(), self.card.size())
            if not snapped:
                other_cards = get_all_desktop_card_rects(exclude_window=self)
                if other_cards and any(my_card_rect.intersects(r) for r in other_cards):
                    self.move(getattr(self, "_window_start_pos", self.pos()))
            self._user_moved_at = time.monotonic()
            self.position_changed.emit(self.x(), self.y())
        else:
            self._snap_hide()

    def _drag_watch_tick(self):
        if (self._dragging or getattr(self, "_resizing", False)) and not _left_button_held():
            self._finish_drag_if_moved()
            self._resizing = False
        if not (self._dragging or getattr(self, "_resizing", False)):
            self._drag_watch.stop()

    def mousePressEvent(self, event: QMouseEvent):
        if self.resize_handle_press(event):
            self._drag_watch.start()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._has_moved = False
            self._snap_target = None
            self._drag_start_pos = event.globalPosition().toPoint()
            self._window_start_pos = self.pos()
            self._drag_watch.start()
            event.accept()

    def _snap_card_rect(self) -> QRect:
        return QRect(self.pos() + self.card.pos(), self.card.size())

    @property
    def weather_data(self) -> Dict[str, Any]:
        return getattr(self.card, "weather_data", None) or {}

    def _close_detail_popup(self):
        old_popup = getattr(self, "_detail_popup", None)
        self._detail_popup = None
        if old_popup is not None:
            try:
                old_popup.close()
            except Exception:
                pass

    def _show_detail_popup(self):
        """左键单击（未拖动）打开详情面板，展示逐时/三天/空气与日出日落。"""
        data = self.weather_data or {}
        if not data.get("hours") and not data.get("days"):
            return
        self._close_detail_popup()
        popup = WeatherDetailPopup(data, self.theme, None)
        card_x = self.x() + self.card.pos().x()
        card_y = self.y() + self.card.pos().y()
        card_center = QPoint(card_x + self.card.width() // 2, card_y + self.card.height() // 2)
        screen = QApplication.screenAt(card_center) or self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        max_x = max(avail.left() + 8, avail.right() - popup.W - 8)
        max_y = max(avail.top() + 8, avail.bottom() - popup.H - 8)
        x = max(avail.left() + 8, min(card_x, max_x))
        y = card_y - popup.H - 12
        if y < avail.top() + 8:
            y = card_y + self.card.height() + 12
        y = max(avail.top() + 8, min(y, max_y))
        popup.move(x, y)
        self._detail_popup = popup
        popup.show()

    def mouseMoveEvent(self, event: QMouseEvent):
        if (self._dragging or getattr(self, "_resizing", False)) and not (event.buttons() & Qt.MouseButton.LeftButton):
            self._finish_drag_if_moved()
            self._resizing = False
            self._drag_watch.stop()
            event.accept()
            return
        if self.resize_handle_move(event):
            event.accept()
            return
        if not getattr(self, "_dragging", False):
            self.resize_handle_cursor(event)
        if self._dragging and not self.locked:
            delta = event.globalPosition().toPoint() - self._drag_start_pos
            if delta.manhattanLength() >= 7:
                self._has_moved = True

            if self._has_moved:
                raw_win_pos = self._window_start_pos + delta
                # 窗口 1:1 跟手；落点由网格轮廓预览给出，松手才落位
                self.move(raw_win_pos)
                self._snap_update()

                event.accept()

    def align_to_adjacent_folder(self):
        """一键与最近的大文件夹卡片平齐对齐（顶边完全齐平，标准 16px 间距，多屏边界钳位）"""
        card_offset = self.card.pos()
        my_card_rect = QRect(self.pos() + card_offset, self.card.size())
        other_cards = get_all_desktop_card_rects(exclude_window=self)
        if not other_cards:
            return

        screen = QApplication.screenAt(my_card_rect.center()) or self.screen() or QApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        safe_left = avail_geo.left() + 8
        safe_right = avail_geo.right() - 8
        safe_top = avail_geo.top() + 8
        safe_bottom = avail_geo.bottom() - 8

        # 寻找距离最近的相邻卡片
        closest = min(other_cards, key=lambda r: (r.center() - my_card_rect.center()).manhattanLength())
        new_top = max(safe_top, min(safe_bottom - self.card.height(), closest.top()))

        if my_card_rect.center().x() < closest.center().x():
            new_left = closest.left() - self.card.width() - 16
            if new_left < safe_left:
                new_left = closest.x() + closest.width() + 16
        else:
            new_left = closest.x() + closest.width() + 16
            if new_left + self.card.width() > safe_right:
                new_left = closest.left() - self.card.width() - 16

        new_left = max(safe_left, min(safe_right - self.card.width(), new_left))
        snapped_win_pos = QPoint(new_left - card_offset.x(), new_top - card_offset.y())
        self.move(snapped_win_pos)
        self.position_changed.emit(snapped_win_pos.x(), snapped_win_pos.y())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.resize_handle_release(event):
            self._drag_watch.stop()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_watch.stop()
            if self._dragging:
                if not self._has_moved:
                    self._dragging = False
                    self._snap_hide()
                    local = event.position().toPoint() - self.card.pos()
                    chev = getattr(self.card, "_chevron_rect", None)
                    if chev and chev.contains(local):
                        self.prompt_change_location()
                    else:
                        self._show_detail_popup()
                    self.refresh_weather()
                else:
                    self._finish_drag_if_moved()
            event.accept()

    def create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))

        act_refresh = menu.addAction("刷新天气")
        act_refresh.triggered.connect(self.refresh_weather)

        act_city = menu.addAction("切换城市…")
        act_city.triggered.connect(self.prompt_change_location)

        common = add_common_widget_actions(menu, self)
        common["align"].triggered.connect(self.align_to_adjacent_folder)
        common["lock"].triggered.connect(self._toggle_lock)
        common["settings"].triggered.connect(self._open_settings)
        common["close"].triggered.connect(self.close_widget)

        return menu

    def _open_settings(self):
        try:
            from settings_dialog import SettingsDialog
            if self.config_manager:
                dlg = SettingsDialog(
                    self.config_manager,
                    window_manager=getattr(self, "manager", None),
                    initial_tab=3,
                    parent=None,
                )
                dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                dlg.exec()
        except Exception as e:
            print("Failed to open settings from weather:", e)

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = self.create_context_menu()
        menu.exec(event.globalPos())
        event.accept()

    def prompt_change_location(self):
        """弹出切换天气城市与地区对话框"""
        cur_city = "成都"
        auto_loc = True
        cur_district = ""
        cur_lat = None
        cur_lon = None
        if self.config_manager:
            cfg = self.config_manager.get_weather_config()
            cur_city = cfg.get("city", "成都")
            cur_district = cfg.get("district", "")
            cur_lat = cfg.get("lat")
            cur_lon = cfg.get("lon")
            auto_loc = cfg.get("auto_location", True)
        elif self.card.weather_data:
            cur_city = _safe_str(self.card.weather_data.get("city"), "成都")
            cur_district = _safe_str(self.card.weather_data.get("district"), "")

        dlg = CityPickerDialog(current_city=cur_city, auto_location=auto_loc,
                               current_district=cur_district,
                               current_lat=cur_lat, current_lon=cur_lon,
                               parent=None, theme=self.theme)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        if dlg.exec():
            res = dlg.get_result()
            if res.get("lat") is None:
                QMessageBox.information(None, "没找到这个城市",
                                        f"无法定位「{res.get('city', '')}」。\n请换成所属的地级市（如 苏州），再在区县里选择县级市。")
                return
            if self.config_manager:
                self.config_manager.set_weather_location(
                    city=res["city"],
                    region=res["region"],
                    district=res.get("district", ""),
                    lat=res["lat"],
                    lon=res["lon"],
                    auto_location=res["auto_location"]
                )
            ws = get_weather_service()
            ws.set_custom_location(
                city=res["city"],
                region=res["region"],
                district=res.get("district", ""),
                lat=res["lat"],
                lon=res["lon"],
                auto_location=res["auto_location"]
            )
            ws.fetch_async(force=True)

    def _switch_size(self, new_size: str):
        self._apply_geometry_mode(new_size)
        self.size_changed.emit(new_size)

    def _toggle_lock(self):
        self.locked = not self.locked
        if self.config_manager and hasattr(self.config_manager, "get_weather_config") and hasattr(self.config_manager, "set_weather_config"):
            cfg = self.config_manager.get_weather_config()
            cfg["locked"] = self.locked
            self.config_manager.set_weather_config(cfg)

    def _cleanup_resources(self):
        self._dragging = False
        self._resizing = False
        for name in ("refresh_timer", "_drag_watch"):
            t = getattr(self, name, None)
            if t is not None and t.isActive():
                t.stop()
        self._close_detail_popup()
        if getattr(self, "card", None) is not None and hasattr(self.card, "_disconnect_service_signals"):
            self.card._disconnect_service_signals()
        self._snap_hide()

    def close_widget(self):
        self._cleanup_resources()
        self.hide()
        self.closed.emit()

    def closeEvent(self, event):
        self._cleanup_resources()
        super().closeEvent(event)
