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
    QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient, QPainterPath,
    QMouseEvent, QContextMenuEvent, QAction, QCursor
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
from ui_kit import FlatCheckBox


_EMOJI_FONT_OK = None


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
    if "🌫" in e or "☁" in e:
        return "cloud"
    return "partly"


def draw_weather_icon(painter: QPainter, rect: QRectF, src, color: QColor):
    """天气图标优先用彩色 emoji（直观），字体缺失时才回退到纯矢量线稿。"""
    global _EMOJI_FONT_OK
    if _EMOJI_FONT_OK is None:
        from PySide6.QtGui import QFontDatabase
        _EMOJI_FONT_OK = "Segoe UI Emoji" in QFontDatabase.families()
    if _EMOJI_FONT_OK:
        glyph = WMO_WEATHER_MAP.get(src, ("", "⛅", ""))[1] if isinstance(src, int) else str(src)
        painter.save()
        painter.setFont(QFont("Segoe UI Emoji",
                              max(10, int(min(rect.width(), rect.height()) * 0.78))))
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
        painter.restore()
        return

    kind = _weather_kind(src)
    s = min(rect.width(), rect.height())
    if s <= 2:
        return
    cx, cy = rect.center().x(), rect.center().y()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(color, max(1.1, s * 0.055), Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def cloud_path(ox=0.0, oy=0.0, scale=1.0):
        w, h = s * 0.78 * scale, s * 0.42 * scale
        x, y = cx - w / 2 + ox, cy - h * 0.10 + oy
        path = QPainterPath()
        path.moveTo(x + w * 0.16, y + h * 0.78)
        path.cubicTo(x - w * 0.02, y + h * 0.78, x - w * 0.02, y + h * 0.28,
                     x + w * 0.22, y + h * 0.28)
        path.cubicTo(x + w * 0.20, y - h * 0.20, x + w * 0.60, y - h * 0.24,
                     x + w * 0.62, y + h * 0.16)
        path.cubicTo(x + w * 0.92, y + h * 0.08, x + w * 1.02, y + h * 0.52,
                     x + w * 0.82, y + h * 0.76)
        path.closeSubpath()
        return path

    if kind == "sun":
        r = s * 0.17
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (0.707, 0.707), (-0.707, 0.707),
                       (0.707, -0.707), (-0.707, -0.707)):
            a, b = r * 1.45, r * 2.05
            painter.drawLine(QPointF(cx + dx * a, cy + dy * a),
                             QPointF(cx + dx * b, cy + dy * b))
    elif kind == "partly":
        r = s * 0.13
        sx, sy = cx - s * 0.20, cy - s * 0.22
        painter.drawEllipse(QRectF(sx - r, sy - r, r * 2, r * 2))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = r * 1.4, r * 1.9
            painter.drawLine(QPointF(sx + dx * a, sy + dy * a),
                             QPointF(sx + dx * b, sy + dy * b))
        painter.drawPath(cloud_path(ox=s * 0.10, oy=s * 0.10, scale=0.78))
    else:
        painter.drawPath(cloud_path(oy=-s * 0.06))
        if kind == "rain":
            for i in (-1, 0, 1):
                x = cx + i * s * 0.20
                painter.drawLine(QPointF(x, cy + s * 0.22),
                                 QPointF(x - s * 0.05, cy + s * 0.40))
        elif kind == "snow":
            for i in (-1, 0, 1):
                x, y = cx + i * s * 0.20, cy + s * 0.32
                painter.drawLine(QPointF(x - s * 0.05, y), QPointF(x + s * 0.05, y))
                painter.drawLine(QPointF(x, y - s * 0.05), QPointF(x, y + s * 0.05))
        elif kind == "storm":
            painter.drawLine(QPointF(cx + s * 0.06, cy + s * 0.18),
                             QPointF(cx - s * 0.02, cy + s * 0.32))
            painter.drawLine(QPointF(cx - s * 0.02, cy + s * 0.32),
                             QPointF(cx + s * 0.10, cy + s * 0.32))
            painter.drawLine(QPointF(cx + s * 0.10, cy + s * 0.32),
                             QPointF(cx + s * 0.02, cy + s * 0.44))
        elif kind == "fog":
            for i in range(3):
                y = cy + s * (0.24 + i * 0.10)
                painter.drawLine(QPointF(cx - s * 0.28, y), QPointF(cx + s * 0.28, y))
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


class CityPickerDialog(QDialog):
    """
    城市与地区切换对话框:
    - 快速选择热门城市 (成都、北京、上海、广州、深圳、重庆、杭州、武汉、南京、西安等)
    - 任意城市/区县中文输入与在线地理编码
    - 自动网络定位 (IP) 恢复开关
    """
    districts_ready = Signal(list)

    def __init__(self, current_city: str = "成都", auto_location: bool = True,
                 current_district: str = "", current_lat=None, current_lon=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("切换天气城市与地区")
        self.setFixedSize(380, 336)
        self._pending_district = current_district
        self._pending_lat = current_lat
        self._pending_lon = current_lon
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        chev_normal = get_ui_chevron_icon_path(False)
        chev_hover = get_ui_chevron_icon_path(True)
        qss = """
            QDialog {
                background-color: #181920;
                color: #ffffff;
                font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI";
            }
            QLabel {
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 500;
            }
            QComboBox, QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 32px 6px 12px;
                font-size: 13px;
                min-height: 20px;
            }
            QLineEdit {
                padding-right: 12px;
            }
            QComboBox:hover, QLineEdit:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QComboBox:focus, QLineEdit:focus {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.32);
            }
            QComboBox:disabled, QLineEdit:disabled {
                background-color: rgba(255, 255, 255, 0.02);
                color: #64748b;
                border-color: rgba(255, 255, 255, 0.06);
            }
            QComboBox {
                combobox-popup: 0;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                image: url("__CHEV_NORMAL__");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }
            QComboBox::down-arrow:hover {
                image: url("__CHEV_HOVER__");
            }
            QComboBox QAbstractItemView {
                background-color: #1e2029;
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                padding: 4px;
                outline: none;
                selection-background-color: #456f9f;
                selection-color: #ffffff;
            }
            QComboBox QAbstractItemView::item {
                height: 30px;
                padding: 0 8px;
                border-radius: 5px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QCheckBox {
                color: #e2e8f0;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1.5px solid rgba(255, 255, 255, 0.35);
                background-color: rgba(255, 255, 255, 0.05);
                border-radius: 4px;
            }
            QCheckBox::indicator:hover {
                border-color: rgba(255, 255, 255, 0.65);
                background-color: rgba(255, 255, 255, 0.1);
            }
            QCheckBox::indicator:checked {
                border-color: #8ab0d8;
                background-color: #8ab0d8;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.06);
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 7px 18px;
                font-size: 13px;
                font-weight: 500;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton#btnCancel {
                background-color: rgba(255, 255, 255, 0.06);
                color: #cbd5e1;
            }
            QPushButton#btnCancel:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton#btnPrimary {
                background-color: #456f9f;
                color: #ffffff;
                border: none;
                font-weight: 600;
            }
            QPushButton#btnPrimary:hover {
                background-color: #3a5f88;
            }
        """.replace("__CHEV_NORMAL__", chev_normal).replace("__CHEV_HOVER__", chev_hover)
        self.setStyleSheet(qss)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        lbl_hint = QLabel("请选择预设城市或输入任意城市名称：")
        layout.addWidget(lbl_hint)

        self.combo = QComboBox(self)
        city_names = list(POPULAR_CITIES.keys())
        self.combo.addItems(city_names)
        idx = self.combo.findText(current_city)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)

        lbl_custom = QLabel("自定义城市 / 区县：")
        layout.addWidget(lbl_custom)
        self.input_city = QLineEdit(self)
        self.input_city.setPlaceholderText("例如: 绵阳、乐山、顺德、昆山、杭州...")
        self.input_city.setText(current_city)
        self.combo.currentTextChanged.connect(self.input_city.setText)
        self.input_city.editingFinished.connect(self._reload_districts)
        layout.addWidget(self.input_city)

        lbl_district = QLabel("区县（可选，温度更贴合本地）：")
        layout.addWidget(lbl_district)
        self.combo_district = QComboBox(self)
        self.combo_district.addItem("加载中…", None)
        self.combo_district.setEnabled(False)
        layout.addWidget(self.combo_district)
        self.combo.currentTextChanged.connect(lambda _t: self._reload_districts())
        self.districts_ready.connect(self._apply_districts)
        self._reload_districts()

        self.chk_auto = FlatCheckBox("自动根据网络 IP 识别定位", self)
        self.chk_auto.setChecked(auto_location)
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self.chk_auto)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        btn_box.addStretch()

        btn_cancel = QPushButton("取消", self)
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_ok = QPushButton("保存生效", self)
        btn_ok.setObjectName("btnPrimary")
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_ok)

        layout.addLayout(btn_box)

    def _reload_districts(self):
        city = self.input_city.text().strip() or self.combo.currentText()
        if not city:
            return
        region = POPULAR_CITIES.get(city, {}).get("region", "")
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
            self.districts_ready.emit(items)

        threading.Thread(target=work, daemon=True).start()

    def _apply_districts(self, items):
        self.combo_district.blockSignals(True)
        self.combo_district.clear()
        self.combo_district.addItem("市级（不细分）", None)
        for item in items:
            self.combo_district.addItem(item.get("name", ""), item)
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
        if False:
            for i in range(self.combo_district.count()):
                data = self.combo_district.itemData(i)
                if isinstance(data, dict) and data.get("name") == self._pending_district:
                    self.combo_district.setCurrentIndex(i)
                    break
        self.combo_district.blockSignals(False)

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
        self.radius = 26.0

        self.service: WeatherService = get_weather_service()
        self.weather_data: Dict[str, Any] = self.service.get_cached_data()

        self.service.weather_updated.connect(self.on_weather_updated)
        self.setMouseTracking(True)

    def on_weather_updated(self, data: Dict[str, Any]):
        self.weather_data = data
        self.update()

    def set_theme(self, theme: str):
        self.theme = theme
        self.update()

    def set_widget_size(self, size_mode: str):
        self.widget_size = size_mode
        self.update()

    def _paint_compact(self, painter, w, h, cls):
        """mini / tall 档独立排版：只保留该尺寸下最重要的信息，不做整体缩放。"""
        data = self.weather_data or {}
        city = data.get("city", "成都")
        region = data.get("region", "")
        district = data.get("district", "")
        short_city = city[:-1] if city.endswith("市") else city
        loc = f"{short_city}·{district}" if district else (f"{city}" if not region or region == city else f"{city}·{region}")
        temp = int(round(data.get("temp", 22.0)))
        icon = data.get("icon", "⛅")
        cond = data.get("condition", "多云")
        t_max = int(round(data.get("temp_max", 25.0)))
        t_min = int(round(data.get("temp_min", 18.0)))
        humidity = int(data.get("humidity", 60))
        feels_color = QColor(180, 83, 9, 235) if self.theme == "light" else QColor(251, 191, 36, 235)

        if self.theme == "light":
            fill_top, fill_bottom = QColor(255, 255, 255, 182), QColor(240, 245, 255, 172)
            text_pri, text_sec = QColor(15, 23, 42, 245), QColor(51, 65, 85, 220)
        elif self.theme in ("black", "oled"):
            fill_top, fill_bottom = QColor(25, 25, 30, 166), QColor(10, 10, 14, 182)
            text_pri, text_sec = QColor(255, 255, 255, 245), QColor(210, 215, 230, 190)
        else:
            fill_top, fill_bottom = QColor(44, 48, 64, 156), QColor(16, 18, 26, 176)
            text_pri, text_sec = QColor(255, 255, 255, 245), QColor(210, 218, 235, 190)

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
            painter.setFont(QFont("Microsoft YaHei", max(8, int(min(w, h) * 0.085)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, pad, w - pad * 2, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, city)
            painter.setPen(text_pri)
            painter.setFont(QFont("Segoe UI", max(18, int(min(w, h) * 0.34)), QFont.Weight.Light))
            painter.drawText(QRect(pad, int(h * 0.30), int(w * 0.62), int(h * 0.42)),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{temp}°")
            draw_weather_icon(painter, QRectF(w - pad - int(w * 0.30), int(h * 0.30),
                                              int(w * 0.30), int(h * 0.36)), icon, text_pri)
        else:
            painter.setPen(text_sec)
            painter.setFont(QFont("Microsoft YaHei", max(9, int(w * 0.085)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, pad, w - pad * 2, 20),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, loc)
            painter.setPen(text_pri)
            painter.setFont(QFont("Segoe UI", max(22, int(w * 0.36)), QFont.Weight.Light))
            painter.drawText(QRect(pad, int(h * 0.20), w - pad * 2, int(h * 0.30)),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{temp}°")
            draw_weather_icon(painter, QRectF(w - pad - int(w * 0.34), int(h * 0.19),
                                              int(w * 0.34), int(h * 0.28)), icon, text_pri)
            painter.setPen(text_sec)
            painter.setFont(QFont("Microsoft YaHei", max(9, int(w * 0.075)), QFont.Weight.Medium))
            painter.drawText(QRect(pad, int(h * 0.56), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cond)
            painter.setFont(QFont("Segoe UI", max(9, int(w * 0.075))))
            painter.drawText(QRect(pad, int(h * 0.66), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"↑ {t_max}°   ↓ {t_min}°")
            painter.drawText(QRect(pad, int(h * 0.76), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"湿度 {humidity}%")
            # 窄档把体感单列一行并强调，避免和湿度挤在同一行被右边界裁掉
            painter.setFont(QFont("Microsoft YaHei", max(9, int(w * 0.075)), QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(pad, int(h * 0.86), w - pad * 2, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"体感 {int(round(data.get('feels_like', temp)))}°")

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
        card_rect = full
        squircle = create_squircle_path(full, self.radius / s)

        # 1. 液态玻璃主体渐变底色
        grad = QLinearGradient(full.left(), full.top(), full.right(), full.bottom())
        if self.theme == "light":
            # 纯正白润液态亚克力，底色通透不透黑，杜绝深色壁纸透光发脏
            grad.setColorAt(0.0, QColor(255, 255, 255, 178))
            grad.setColorAt(1.0, QColor(240, 245, 255, 168))
            # 强化色阶深度与文字不透明度，符合 WCAG AAA 级视网膜高对比度
            text_pri = QColor(15, 23, 42, 245)      # Slate 900 极深石板黑
            text_sec = QColor(30, 41, 59, 220)      # Slate 800 深石板灰
            text_muted = QColor(71, 85, 105, 230)   # Slate 600 高对比度中深石板灰 (告别浅色看不清)
            border_hi = QColor(255, 255, 255, 245)
            border_lo = QColor(148, 163, 184, 90)
            divider_color = QColor(15, 23, 42, 32)
        elif self.theme in ("black", "oled"):
            grad.setColorAt(0.0, QColor(25, 25, 30, 162))
            grad.setColorAt(1.0, QColor(10, 10, 14, 178))
            text_pri = QColor(255, 255, 255, 245)
            text_sec = QColor(220, 225, 240, 190)
            text_muted = QColor(188, 198, 220, 215)
            border_hi = QColor(255, 255, 255, 60)
            border_lo = QColor(255, 255, 255, 15)
            divider_color = QColor(255, 255, 255, 25)
        else:  # dark (默认星空深色)
            grad.setColorAt(0.0, QColor(44, 48, 64, 152))
            grad.setColorAt(1.0, QColor(16, 18, 26, 172))
            text_pri = QColor(255, 255, 255, 245)
            text_sec = QColor(215, 222, 240, 190)
            text_muted = QColor(196, 206, 226, 215)
            border_hi = QColor(255, 255, 255, 80)
            border_lo = QColor(255, 255, 255, 20)
            divider_color = QColor(255, 255, 255, 25)

        painter.fillPath(squircle, grad)

        # 2. 迎光面 0.75px 菲涅尔细腻高光微边线
        pen_grad = QLinearGradient(full.left(), full.top(), full.right(), full.bottom())
        pen_grad.setColorAt(0.0, border_hi)
        pen_grad.setColorAt(0.4, border_hi)
        pen_grad.setColorAt(1.0, border_lo)
        border_pen = QPen(pen_grad, 0.75)
        draw_liquid_glass(painter, squircle, full, self.radius / s, self.theme)

        # 3. 绘制气象数据排版
        data = self.weather_data
        city = data.get("city", "成都")
        region = data.get("region", "")
        district = data.get("district", "")
        short_city = city[:-1] if city.endswith("市") else city
        if district:
            loc_str = f"{short_city} · {district}"
        elif region and region != city:
            loc_str = f"{city} · {region}"
        else:
            loc_str = city
        temp = data.get("temp", 22.0)
        temp_str = f"{int(round(temp))}°"
        cond = data.get("condition", "多云")
        icon = data.get("icon", "⛅")
        t_max = int(round(data.get("temp_max", 25.0)))
        t_min = int(round(data.get("temp_min", 18.0)))
        humidity = data.get("humidity", 60)
        wind = data.get("wind", 10.0)
        feels = data.get("feels_like", temp)
        precip_prob = data.get("precip_prob")
        updated = data.get("updated_at", "")

        weight_secondary = QFont.Weight.Medium if self.theme == "light" else QFont.Weight.Normal
        # 体感是穿衣判断依据：暖色强调，在所有常规规格里固定完整显示
        feels_color = QColor(180, 83, 9, 235) if self.theme == "light" else QColor(251, 191, 36, 235)

        if use_wide:
            # 宽扁档：横向排版（2x1 或用户把 2x2 拉成宽条）
            # 左侧：大字号气温与图符
            painter.setFont(QFont("Segoe UI", 32, QFont.Weight.DemiBold))
            painter.setPen(text_pri)
            painter.drawText(QRect(20, 16, 100, 50), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_str)

            draw_weather_icon(painter, QRectF(96, 20, 44, 44), icon, text_pri)

            # 右侧：城市与状态
            painter.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
            painter.setPen(text_pri)
            fm_loc_w = QFontMetrics(painter.font())
            painter.drawText(QRect(145, 20, w - 165, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm_loc_w.elidedText(loc_str, Qt.TextElideMode.ElideRight, max(60, w - 165)))

            painter.setFont(QFont("Microsoft YaHei", 10, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(145, 46, w - 165, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cond)

            # 底部栏三段：高低温可省略，体感与更新时间各占固定区域，互不挤压
            painter.setFont(QFont("Microsoft YaHei", 9, weight_secondary))
            painter.setPen(text_muted)
            wide_extra = f"降水 {int(precip_prob)}%" if precip_prob is not None else f"风 {int(wind)}级"
            feels_text = f"体感 {int(round(feels))}°"
            update_text = f"{updated} 更新" if updated else ""
            fm = QFontMetrics(painter.font())
            feels_w = fm.horizontalAdvance(feels_text) + 10
            upd_w = fm.horizontalAdvance(update_text) + 10 if update_text else 0
            left_w = max(56, w - 40 - feels_w - upd_w)
            left_base = f"↑{t_max}°  ↓{t_min}°"
            left_full = f"{left_base}  ·  {wide_extra}"
            bot_text = left_full if fm.horizontalAdvance(left_full) <= left_w else left_base
            bot_text = fm.elidedText(bot_text, Qt.TextElideMode.ElideRight, left_w)
            painter.drawText(QRect(20, 80, left_w, 24),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, bot_text)

            painter.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(20, 80, w - 40 - upd_w, 24),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, feels_text)
            if update_text:
                painter.setFont(QFont("Microsoft YaHei", 9, weight_secondary))
                painter.setPen(text_muted)
                painter.drawText(QRect(20, 80, w - 40, 24),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, update_text)
        else:
            # 紧凑排版：区名▾ / 大温度+今高低温 / 逐时 / 三日温度条
            hours_data = data.get("hours") or []
            days_data = data.get("days") or []

            painter.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
            painter.setPen(text_pri)
            fm_head = painter.fontMetrics()
            head_w = min(int(fm_head.horizontalAdvance(loc_str)), max(60, w - 132))
            painter.drawText(QRect(20, 14, head_w, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, loc_str)
            # 下拉箭头自绘：▾ 字符在部分字体下会退化成空心方框
            cx_chev = 20 + head_w + 9
            painter.setPen(QPen(text_muted, 1.4, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(cx_chev - 3, 22), QPointF(cx_chev, 25))
            painter.drawLine(QPointF(cx_chev, 25), QPointF(cx_chev + 3, 22))
            self._chevron_rect = QRect(14, 8, head_w + 28, 32)

            painter.setFont(QFont("Microsoft YaHei", 9, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(w - 104, 14, 84, 22),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, cond)

            painter.setFont(QFont("Segoe UI", 40, QFont.Weight.Light))
            painter.setPen(text_pri)
            painter.drawText(QRect(18, 38, 132, 56),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, temp_str)

            # 右侧三行成组：最高 / 最低 / 体感，体感暖色收尾，不侵占逐时区
            painter.setFont(QFont("Segoe UI", 9, weight_secondary))
            painter.setPen(text_sec)
            painter.drawText(QRect(w - 108, 42, 88, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"最高 {t_max}°")
            painter.drawText(QRect(w - 108, 60, 88, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"最低 {t_min}°")
            painter.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
            painter.setPen(feels_color)
            painter.drawText(QRect(w - 108, 78, 88, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"体感 {int(round(feels))}°")

            if hours_data:
                cols = hours_data[:6]
                col_w = (w - 40) / len(cols)
                for i, hr in enumerate(cols):
                    cx = 20 + col_w * i
                    label = str(hr.get("t", "")).replace(":00", "时")
                    painter.setFont(QFont("Segoe UI", 8))
                    painter.setPen(text_muted)
                    painter.drawText(QRectF(cx, 102, col_w, 14), Qt.AlignmentFlag.AlignCenter, label)
                    draw_weather_icon(painter, QRectF(cx, 118, col_w, 20),
                                      hr.get("code", 0), text_pri)
                    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
                    painter.setPen(text_pri)
                    painter.drawText(QRectF(cx, 140, col_w, 18), Qt.AlignmentFlag.AlignCenter,
                                     f"{int(round(hr.get('temp', 0)))}°")

            painter.setPen(QPen(divider_color, 0.75))
            painter.drawLine(20, 164, w - 20, 164)

            if days_data:
                base_min = min(float(d.get("min", 0)) for d in days_data)
                base_max = max(float(d.get("max", 1)) for d in days_data)
                span = max(1.0, base_max - base_min)
                labels = ("今天", "明天", "后天")
                track_dark = self.theme != "light"
                for i, day in enumerate(days_data[:3]):
                    y = 172 + i * 22
                    painter.setFont(QFont("Microsoft YaHei", 9, weight_secondary))
                    painter.setPen(text_sec)
                    painter.drawText(QRect(20, y, 40, 20),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                     labels[i] if i < len(labels) else "")
                    draw_weather_icon(painter, QRectF(60, y, 24, 20),
                                      day.get("code", 0), text_sec)
                    painter.setFont(QFont("Segoe UI", 9))
                    painter.setPen(text_muted)
                    painter.drawText(QRect(86, y, 28, 20),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                     f"{int(round(day.get('min', 0)))}°")
                    track = QRectF(120, y + 8, 62, 5)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 28) if track_dark else QColor(0, 0, 0, 22))
                    painter.drawRoundedRect(track, 2.5, 2.5)
                    lo = track.left() + (float(day.get("min", 0)) - base_min) / span * track.width()
                    hi = track.left() + (float(day.get("max", 0)) - base_min) / span * track.width()
                    painter.setBrush(QColor(126, 176, 255) if track_dark else QColor(72, 132, 224))
                    painter.drawRoundedRect(QRectF(lo, track.top(), max(6.0, hi - lo), track.height()), 2.5, 2.5)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                    painter.setPen(text_pri)
                    painter.drawText(QRect(w - 56, y, 36, 20),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                     f"{int(round(day.get('max', 0)))}°")
            else:
                painter.setFont(QFont("Microsoft YaHei", 10))
                painter.setPen(text_sec)
                painter.drawText(QRect(20, 176, w - 40, 22),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 f"湿度 {humidity}%   体感 {int(round(feels))}°")

from desktop_widgets import ResizableWidgetMixin, widget_size_class, add_common_widget_actions


class WeatherDetailPopup(QWidget):
    """点击天气卡弹出的详情面板：逐时预报、未来三天、空气与日出日落。"""

    W, H = 468, 344

    def __init__(self, data: dict, theme: str = "dark", parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.W, self.H)
        self.data = data or {}
        self.theme = theme

    def paintEvent(self, event):
        d = self.data
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(1, 1, self.W - 2, self.H - 2)
        path = create_squircle_path(rect, 22)
        if self.theme == "light":
            bg, border, pri, sec, muted = QColor(250, 250, 252, 246), QColor(0, 0, 0, 28), QColor(28, 30, 34), QColor(90, 96, 105), QColor(132, 138, 148)
        else:
            bg, border, pri, sec, muted = QColor(22, 24, 30, 244), QColor(255, 255, 255, 32), QColor(240, 242, 246), QColor(176, 182, 192), QColor(130, 136, 146)
        p.fillPath(path, bg)
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        city = d.get("city", "")
        district = d.get("district", "")
        short_city = city[:-1] if city.endswith("市") else city
        loc = f"{short_city} · {district}" if district else (city or "未知地区")

        p.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.Bold)); p.setPen(pri)
        p.drawText(QRect(22, 16, 300, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, loc)

        p.setFont(QFont("Segoe UI", 30, QFont.Weight.Light)); p.setPen(pri)
        p.drawText(QRect(22, 44, 140, 46), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{int(round(d.get('temp', 22)))}°")
        p.setFont(QFont("Microsoft YaHei", 11)); p.setPen(sec)
        p.drawText(QRect(150, 52, 220, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{d.get('condition', '')}   体感 {int(round(d.get('feels_like', d.get('temp', 22))))}°")

        aqi_text = f"空气 {d.get('aqi_level')} {d.get('aqi')}" if d.get("aqi_level") else "空气 —"
        p.setFont(QFont("Microsoft YaHei", 9)); p.setPen(muted)
        p.drawText(QRect(150, 74, 300, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{aqi_text}    湿度 {d.get('humidity', 0)}%    风 {int(d.get('wind', 0))}级")

        # 逐时（最多 10 个小时）
        hours = (d.get("hours") or [])[:10]
        p.setPen(QPen(border, 1)); p.drawLine(22, 104, self.W - 22, 104)
        if hours:
            col_w = (self.W - 44) / len(hours)
            for i, h in enumerate(hours):
                cx = 22 + col_w * i + col_w / 2
                p.setFont(QFont("Segoe UI", 8)); p.setPen(muted)
                p.drawText(QRectF(cx - col_w / 2, 112, col_w, 16), Qt.AlignmentFlag.AlignCenter, h.get("t", ""))
                draw_weather_icon(p, QRectF(cx - col_w / 2, 130, col_w, 22),
                                  h.get("code", 0), pri)
                p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold)); p.setPen(pri)
                p.drawText(QRectF(cx - col_w / 2, 152, col_w, 20), Qt.AlignmentFlag.AlignCenter,
                           f"{int(round(h.get('temp', 0)))}°")
                prob = h.get("prob")
                p.setFont(QFont("Segoe UI", 8))
                p.setPen(QColor(90, 150, 240) if prob and prob >= 30 else muted)
                p.drawText(QRectF(cx - col_w / 2, 172, col_w, 16), Qt.AlignmentFlag.AlignCenter,
                           f"{int(prob)}%" if prob is not None else "")

        # 未来三天
        days = (d.get("days") or [])[:3]
        p.setPen(QPen(border, 1)); p.drawLine(22, 198, self.W - 22, 198)
        labels = ("今天", "明天", "后天")
        for i, day in enumerate(days):
            y = 208 + i * 30
            p.setFont(QFont("Microsoft YaHei", 9)); p.setPen(sec)
            p.drawText(QRect(24, y, 60, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, labels[i])
            draw_weather_icon(p, QRectF(86, y, 30, 22), day.get("code", 0), pri)
            p.setFont(QFont("Microsoft YaHei", 9)); p.setPen(pri)
            p.drawText(QRect(120, y, 120, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{int(round(day.get('min', 0)))}° ~ {int(round(day.get('max', 0)))}°")
            prob = day.get("prob")
            p.setFont(QFont("Segoe UI", 9)); p.setPen(QColor(90, 150, 240) if prob and prob >= 30 else muted)
            p.drawText(QRect(self.W - 120, y, 96, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"降水 {int(prob)}%" if prob is not None else "")

        # 底部：紫外线与日出日落
        p.setPen(QPen(border, 1)); p.drawLine(22, 300, self.W - 22, 300)
        uv = d.get("uv")
        p.setFont(QFont("Microsoft YaHei", 9)); p.setPen(muted)
        p.drawText(QRect(22, 308, self.W - 44, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"紫外线 {round(uv, 1) if uv is not None else '—'}    日出 {d.get('sunrise') or '—'}    日落 {d.get('sunset') or '—'}    {d.get('updated_at', '')} 更新")
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
        self._dragging: bool = False
        self._drag_start_pos: QPoint = QPoint()
        self._window_start_pos: QPoint = QPoint()
        self._has_moved: bool = False
        self.card_w = CARD_W_2X2
        self.card_h = CARD_H_2X2
        self._init_resize_state()
        self._snap_init()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 内部卡片
        self.card = WeatherWidgetCard(self, theme=self.theme, widget_size=self.widget_size)
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

    def set_theme(self, theme: str):
        self.theme = theme
        self.card.set_theme(theme)
        self.update()

    def paintEvent(self, event):
        """卡片本体自带圆角与高光边，不画外扩阴影（避免边缘重影）。"""
        return

    def showEvent(self, event):
        super().showEvent(event)
        self._place_resize_grip()
        # 稳固锚定桌面图层
        desktop_layer.pin_to_desktop(int(self.winId()))
        QTimer.singleShot(60, lambda: desktop_layer.pin_to_desktop(int(self.winId())))
        QTimer.singleShot(220, lambda: desktop_layer.pin_to_desktop(int(self.winId())))

    def hideEvent(self, event):
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

    def mousePressEvent(self, event: QMouseEvent):
        if self.resize_handle_press(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._has_moved = False
            self._snap_target = None
            self._drag_start_pos = event.globalPosition().toPoint()
            self._window_start_pos = self.pos()
            event.accept()

    def _snap_card_rect(self) -> QRect:
        return QRect(self.pos() + self.card.pos(), self.card.size())

    def _show_detail_popup(self):
        """左键单击（未拖动）打开详情面板，展示逐时/三天/空气与日出日落。"""
        data = self.weather_data or {}
        if not data.get("hours") and not data.get("days"):
            return
        popup = WeatherDetailPopup(data, self.theme, self)
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        card_x = self.x() + self.card.pos().x()
        card_y = self.y() + self.card.pos().y()
        x = max(avail.left() + 8, min(card_x, avail.right() - popup.W - 8))
        y = card_y - popup.H - 12
        if y < avail.top() + 8:
            y = card_y + self.card.height() + 12
        popup.move(x, y)
        self._detail_popup = popup
        popup.show()

    def mouseMoveEvent(self, event: QMouseEvent):
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
        """一键与最近的大文件夹卡片平齐对齐（顶边完全齐平，标准 16px 间距）"""
        card_offset = self.card.pos()
        my_card_rect = QRect(self.pos() + card_offset, self.card.size())
        other_cards = get_all_desktop_card_rects(exclude_window=self)
        if not other_cards:
            return

        # 寻找距离最近的相邻卡片
        closest = min(other_cards, key=lambda r: (r.center() - my_card_rect.center()).manhattanLength())
        new_top = closest.top()

        if my_card_rect.center().x() < closest.center().x():
            new_left = closest.left() - self.card.width() - 16
        else:
            new_left = closest.x() + closest.width() + 16

        snapped_win_pos = QPoint(new_left - card_offset.x(), new_top - card_offset.y())
        self.move(snapped_win_pos)
        self.position_changed.emit(snapped_win_pos.x(), snapped_win_pos.y())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.resize_handle_release(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                if not self._has_moved:
                    local = event.position().toPoint() - self.card.pos()
                    chev = getattr(self.card, "_chevron_rect", None)
                    if chev and chev.contains(local):
                        self.prompt_change_location()
                    else:
                        self._show_detail_popup()
                if self._has_moved:
                    # 松手落到网格预览位置；没有合法格点才退回原位
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
                    # 单击卡片触发立即刷新
                    self.refresh_weather()
            event.accept()

    def create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(28, 30, 40, 240);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 12px;
                padding: 6px;
                font-family: 'Microsoft YaHei', sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 30);
            }
            QMenu::separator {
                height: 1px;
                background-color: rgba(255, 255, 255, 20);
                margin: 4px 8px;
            }
        """)

        act_refresh = menu.addAction("立即刷新天气数据")
        act_refresh.triggered.connect(self.refresh_weather)

        act_city = menu.addAction("切换城市与地区...")
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
                dlg = SettingsDialog(self.config_manager, initial_tab=3, parent=self)
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
        if self.config_manager:
            cfg = self.config_manager.get_weather_config()
            cur_city = cfg.get("city", "成都")
            cur_district = cfg.get("district", "")
            cur_lat = cfg.get("lat")
            cur_lon = cfg.get("lon")
            auto_loc = cfg.get("auto_location", True)
        else:
            cur_city = "成都"
            cur_lat = None
            cur_lon = None
            auto_loc = True
            if self.card.weather_data:
                cur_city = self.card.weather_data.get("city", "成都")
                cur_district = self.card.weather_data.get("district", "")

        dlg = CityPickerDialog(current_city=cur_city, auto_location=auto_loc,
                               current_district=cur_district,
                               current_lat=locals().get("cur_lat"), current_lon=locals().get("cur_lon"),
                               parent=self)
        if dlg.exec():
            res = dlg.get_result()
            if res.get("lat") is None:
                QMessageBox.information(self, "没找到这个城市",
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

    def close_widget(self):
        t = getattr(self, "refresh_timer", None)
        if t is not None and t.isActive():
            t.stop()
        self.hide()
        self.closed.emit()
