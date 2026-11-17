"""
desktop_widgets.py - 超椭圆桌面小组件矩阵（移动端大厂风格）
包含:
1. ClockWidgetWindow: 灵动数字时钟与公历/农历日历小组件 (2x2 / 2x1)
2. SystemMonitorWidgetWindow: 硬件管家系统资源监测微件 (CPU / RAM / 磁盘) (2x2)
3. MemoWidgetWindow: 桌面随手便签待办微件 (2x2)

特性:
- 100% 连续曲率超椭圆 (Squircle) 纯 QPainter 矢量绘制
- 3 阶同心羽化衰减的环境软阴影 (零 QGraphicsEffect 脏矩形)
- SHELLDLL_DefView 桌面所有者锁定与 Win+D 绝对免疫
- 16px 智能磁吸网格对接 (与大文件夹平齐对齐)
- 边缘 100% 透明穿透 (WM_NCHITTEST)
"""
import stacky.paths
from stacky.paths import ROOT

import os
import re
import sys
import time
import datetime
import psutil
import json
from typing import Optional, Dict, Any, List

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QPointF, QRect, QRectF, QSize, QEvent
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QPainterPath, QLinearGradient, QRadialGradient,
    QMouseEvent, QContextMenuEvent, QAction, QPixmap, QFontMetrics, QIcon, QPalette
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QMenu, QApplication, QFrame,
    QPushButton, QLineEdit, QScrollArea, QStackedWidget, QCheckBox, QSlider
)

import desktop_layer
from grid_snap import get_all_desktop_card_rects
from widget_grid import SnapDragMixin
import icon_utils
from icon_utils import create_squircle_path, draw_liquid_glass
from media_service import get_media_service, get_lyrics_service
import ui_kit
from ui_kit import theme_pick, light_text

# 只匹配独立的 color 声明，跳过 background-color / border-color 等
_COLOR_DECL = re.compile(
    r"(?<![\w-])color\s*:\s*(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))", re.I)

TYPO_STEPS = (10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 36, 42)
_HERO_TYPO_STEPS = TYPO_STEPS + (48, 52, 54, 58, 64)

_FONT_FAMILIES_TEXT = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
_FONT_FAMILIES_DISPLAY = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"]
_FONT_TEXT_CSS = "'Segoe UI Variable Text', 'Segoe UI', 'Microsoft YaHei UI', sans-serif"
_FONT_DISPLAY_CSS = "'Segoe UI Variable Display', 'Segoe UI', 'Microsoft YaHei UI', sans-serif"

# 2000..2050 农历年表（基准 2000-02-05 = 庚辰年正月初一），纯本地零依赖秒算农历与二十四节气
_LUNAR_2000_2050 = (
    0x0C960, 0x0D954, 0x0D4A0, 0x0DA50, 0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5,
    0x0A950, 0x0B4A0, 0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260, 0x0EA65, 0x0D530,
    0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0, 0x1D0B6, 0x0D250, 0x0D520, 0x0DD45,
    0x0B5A0, 0x056D0, 0x055B2, 0x049B0, 0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0,
    0x14B63,
)
_LUNAR_MONTHS = ("正月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "冬月", "腊月")
_LUNAR_DAYS = (
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
)
# 21 世纪二十四节气 C 值常量表（每月两个节气）
_SOLAR_TERM_NAMES = (
    "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
    "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
    "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
)
_SOLAR_TERM_C21 = (
    5.4055, 20.12, 3.87, 18.73, 5.63, 20.646, 4.81, 20.1,
    5.52, 21.04, 5.678, 21.37, 7.108, 22.83, 7.5, 23.13,
    7.646, 23.042, 8.318, 23.438, 7.438, 22.36, 7.18, 21.94,
)


def _format_lunar_and_term(dt: datetime.date) -> str:
    """生成精炼的『农历十月初十 · 立冬』徽标文案。"""
    lunar_part = ""
    base = datetime.date(2000, 2, 5)
    offset = (dt - base).days
    if 0 <= offset < 18600:
        rem = offset
        for code in _LUNAR_2000_2050:
            leap_m = code & 0xF
            ydays = sum(30 if (code & (0x10000 >> m)) else 29 for m in range(1, 13))
            if leap_m:
                ydays += 30 if (code & 0x10000) else 29
            if rem < ydays:
                is_leap = False
                m_idx = 1
                for m in range(1, 13):
                    mdays = 30 if (code & (0x10000 >> m)) else 29
                    if rem < mdays:
                        m_idx = m
                        break
                    rem -= mdays
                    if leap_m == m:
                        ldays = 30 if (code & 0x10000) else 29
                        if rem < ldays:
                            m_idx = m
                            is_leap = True
                            break
                        rem -= ldays
                prefix = "闰" if is_leap else ""
                m_name = _LUNAR_MONTHS[(m_idx - 1) % 12]
                d_name = _LUNAR_DAYS[min(29, max(0, rem))]
                lunar_part = f"农历{prefix}{m_name}{d_name}"
                break
            rem -= ydays

    term_part = ""
    if 2000 <= dt.year <= 2099:
        y = dt.year % 100
        idx1 = (dt.month - 1) * 2
        idx2 = idx1 + 1
        leap_adj1 = int((y - 1) / 4) if idx1 in (0, 1, 2, 3) else int(y / 4)
        leap_adj2 = int((y - 1) / 4) if idx2 in (0, 1, 2, 3) else int(y / 4)
        d1 = int(y * 0.2422 + _SOLAR_TERM_C21[idx1]) - leap_adj1
        d2 = int(y * 0.2422 + _SOLAR_TERM_C21[idx2]) - leap_adj2
        if dt.day == d1:
            term_part = _SOLAR_TERM_NAMES[idx1]
        elif dt.day == d2:
            term_part = _SOLAR_TERM_NAMES[idx2]
        elif dt.day > d2:
            term_part = _SOLAR_TERM_NAMES[idx2]
        elif dt.day > d1:
            term_part = _SOLAR_TERM_NAMES[idx1]
        else:
            prev_idx = (idx1 - 1) % 24
            term_part = _SOLAR_TERM_NAMES[prev_idx]

    if lunar_part and term_part:
        return f"{lunar_part} · {term_part}"
    return lunar_part or term_part or "今日宜专注"


def _get_user_data_dir() -> str:
    getter = getattr(stacky.paths, "get_user_data_dir", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return ROOT


def _get_widget_menu_qss(theme: str = "dark") -> str:
    fn = getattr(ui_kit, "menu_qss", None)
    if callable(fn):
        return fn(theme)
    if theme == "light":
        return f"""
            QMenu {{
                background-color: rgba(248, 250, 252, 248);
                border: 1px solid rgba(15, 23, 42, 28);
                border-radius: 10px;
                padding: 6px;
                color: #0f172a;
                font-family: {_FONT_TEXT_CSS};
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 7px 18px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: rgba(15, 23, 42, 18);
                color: #0f172a;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: rgba(15, 23, 42, 20);
                margin: 4px 6px;
            }}
        """
    return f"""
        QMenu {{
            background-color: rgba(24, 26, 34, 248);
            border: 1px solid rgba(255, 255, 255, 42);
            border-radius: 10px;
            padding: 6px;
            color: #f5f5f7;
            font-family: {_FONT_TEXT_CSS};
            font-size: 12px;
        }}
        QMenu::item {{
            padding: 7px 18px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background-color: rgba(255, 255, 255, 40);
            color: #ffffff;
        }}
        QMenu::separator {{
            height: 1px;
            background-color: rgba(255, 255, 255, 25);
            margin: 4px 6px;
        }}
    """


def _render_widget_glass_bg(
    canvas_size: QSize,
    card_rect: QRectF,
    radius: float,
    opacity: float = 0.88,
    theme: str = "dark",
    draw_shadow: bool = True,
    dpr: float = 1.0,
) -> QPixmap:
    dpr = max(1.0, float(dpr or 1.0))
    render_fn = getattr(icon_utils, "render_squircle_card_with_shadow", None)
    if callable(render_fn):
        try:
            return render_fn(
                canvas_size,
                card_rect,
                float(radius),
                float(opacity),
                theme,
                draw_shadow=draw_shadow,
                dpr=dpr,
            )
        except TypeError:
            if dpr <= 1.001:
                return render_fn(canvas_size, card_rect, float(radius), float(opacity), theme, draw_shadow=draw_shadow)

    phys_w = max(1, int(round(canvas_size.width() * dpr)))
    phys_h = max(1, int(round(canvas_size.height() * dpr)))
    pix = QPixmap(phys_w, phys_h)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    op = max(0.05, min(1.0, float(opacity)))

    has_margin = (
        card_rect.left() >= 6.0
        and card_rect.top() >= 6.0
        and (canvas_size.width() - card_rect.right()) >= 6.0
        and (canvas_size.height() - card_rect.bottom()) >= 6.0
    )
    if draw_shadow and has_margin:
        is_light = (theme == "light")
        rings = [
            (10.0, 4.5, 4 if is_light else 7),
            (7.0, 3.5, 6 if is_light else 11),
            (4.5, 2.5, 9 if is_light else 16),
            (2.5, 1.5, 13 if is_light else 22),
            (1.0, 0.8, 18 if is_light else 28),
        ]
        for expand, dy, alpha in rings:
            sr = card_rect.adjusted(-expand, -expand + dy, expand, expand + dy)
            sp = create_squircle_path(sr, float(radius) + expand * 0.65)
            p.fillPath(sp, QColor(15, 23, 42, alpha) if is_light else QColor(0, 0, 0, alpha))

        # Hollow out interior with DestinationOut so shadow never darkens translucent glass
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        cut_path = create_squircle_path(card_rect, float(radius))
        p.fillPath(cut_path, QColor(0, 0, 0, 255))
        p.restore()

    card_path = create_squircle_path(card_rect, float(radius))
    grad = QLinearGradient(card_rect.left(), card_rect.top(), card_rect.left(), card_rect.bottom())
    if theme == "light":
        grad.setColorAt(0.0, QColor(250, 251, 253, int(150 * op)))
        grad.setColorAt(1.0, QColor(236, 239, 244, int(145 * op)))
    elif theme in ("oled", "black"):
        grad.setColorAt(0.0, QColor(18, 19, 23, int(150 * op)))
        grad.setColorAt(1.0, QColor(8, 9, 11, int(155 * op)))
    else:
        grad.setColorAt(0.0, QColor(38, 41, 48, int(150 * op)))
        grad.setColorAt(1.0, QColor(24, 26, 31, int(155 * op)))

    p.fillPath(card_path, grad)
    draw_liquid_glass(p, card_path, card_rect, float(radius), theme)
    p.end()
    return pix


def add_common_widget_actions(menu, win):
    """所有桌面小组件共用的右键公共区：顺序与文案完全一致，只此一处定义。"""
    menu.addSeparator()
    return {
        "align": menu.addAction("对齐网格"),
        "lock": menu.addAction("解锁位置" if win.locked else "锁定位置"),
        "settings": menu.addAction("设置…"),
        "close": menu.addAction("关闭小组件"),
    }


def _left_button_held() -> bool:
    """物理左键状态。GetAsyncKeyState 比 Qt 内部的 mouseButtons 更权威：
    release 事件丢失时 Qt 仍可能认为键按着，而系统状态不会骗人。"""
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            pass
    return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)


def finish_widget_menu(win, menu, pos, config_key, extra_handler=None):
    """小组件右键统一入口：补公共区、exec、处理公共动作，专属项交回调用方。"""
    common = add_common_widget_actions(menu, win)
    act = menu.exec(pos)
    if act is None:
        return
    if act is common["align"]:
        win.align_to_adjacent_folder()
    elif act is common["lock"]:
        win.locked = not win.locked
        cm = getattr(win, "config_manager", None)
        getter = getattr(cm, f"get_{config_key}_config", None) if cm else None
        setter = getattr(cm, f"set_{config_key}_config", None) if cm else None
        if getter and setter:
            cfg = getter()
            cfg["locked"] = win.locked
            setter(cfg)
    elif act is common["settings"]:
        try:
            from settings_dialog import SettingsDialog
            if getattr(win, "config_manager", None):
                dlg = SettingsDialog(
                    win.config_manager,
                    window_manager=getattr(win, "manager", None),
                    initial_tab=3,
                    parent=None,
                )
                dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                dlg.exec()
        except Exception as e:
            print("Failed to open settings from widget:", e)
    elif act is common["close"]:
        win.close_widget()
    elif extra_handler:
        extra_handler(act)


# 边距与规格常量
MARGIN_SHADOW = 24

# 2x2 正方规格 (248x248)
CARD_W_2X2 = 248
CARD_H_2X2 = 248
WIN_W_2X2 = CARD_W_2X2 + MARGIN_SHADOW * 2
WIN_H_2X2 = CARD_H_2X2 + MARGIN_SHADOW * 2

# 2x1 胶囊规格 (248x120)
CARD_W_2X1 = 248
CARD_H_2X1 = 120
WIN_W_2X1 = CARD_W_2X1 + MARGIN_SHADOW * 2
WIN_H_2X1 = CARD_H_2X1 + MARGIN_SHADOW * 2


def _media_button_icon(kind: str, size: int = 18, color: QColor = None) -> QIcon:
    """自绘播放控制图标（2x HiDPI 采样）：emoji 在部分系统字体会渲染成空方块。"""
    pm = QPixmap(max(2, size * 2), max(2, size * 2))
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color if color is not None else QColor(255, 255, 255, 240))
    s = float(size)
    if kind in ("play", "prev", "next"):
        path = QPainterPath()
        if kind == "play":
            path.moveTo(s * 0.30, s * 0.16)
            path.lineTo(s * 0.82, s * 0.50)
            path.lineTo(s * 0.30, s * 0.84)
        elif kind == "prev":
            path.moveTo(s * 0.76, s * 0.16)
            path.lineTo(s * 0.34, s * 0.50)
            path.lineTo(s * 0.76, s * 0.84)
        else:
            path.moveTo(s * 0.24, s * 0.16)
            path.lineTo(s * 0.66, s * 0.50)
            path.lineTo(s * 0.24, s * 0.84)
        path.closeSubpath()
        p.drawPath(path)
        if kind != "play":
            bar_x = s * 0.20 if kind == "prev" else s * 0.70
            p.drawRoundedRect(QRectF(bar_x, s * 0.16, s * 0.10, s * 0.68), 1, 1)
    else:  # pause
        p.drawRoundedRect(QRectF(s * 0.30, s * 0.16, s * 0.14, s * 0.68), 1, 1)
        p.drawRoundedRect(QRectF(s * 0.56, s * 0.16, s * 0.14, s * 0.68), 1, 1)
    p.end()
    return QIcon(pm)


def widget_size_class(w: int, h: int) -> str:
    """尺寸档位：参考移动端组件的做法，按档换布局而不是缩放。"""
    if w < 170 and h < 170:
        return "mini"
    ratio = w / max(1, h)
    # 桌面小组件普遍偏宽：1.35 以上就该切横排，否则 330×200 会被当成竖排挤爆
    # 高度不足时竖排必然溢出，直接横排（248×150 / 200×170 这类）
    if ratio >= 1.35 or (h < 190 and ratio >= 1.15):
        return "wide"
    if ratio <= 0.72:
        return "tall"
    return "regular"


class ResizableWidgetMixin:
    """右下角无级缩放 + 内容响应式排版。宿主需提供 card_w/card_h、_update_window_size()、get_card_rect()。"""
    MIN_CARD_W, MIN_CARD_H = 140, 110

    def _init_resize_state(self):
        self._resizing = False
        self._resize_start_global = QPoint()
        self._resize_start_size = QSize(self.card_w, self.card_h)
        self.setMouseTracking(True)
        # 独立置顶把手：子控件（QLineEdit / QListWidget）会吞掉鼠标事件，
        # 只靠宿主 mousePressEvent 在拉小后会彻底拖不动。
        self._resize_grip = QWidget(self)
        self._resize_grip.setFixedSize(24, 24)
        self._resize_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._resize_grip.setStyleSheet("background: transparent;")
        self._resize_grip.installEventFilter(self)
        self._place_resize_grip()

    def _place_resize_grip(self):
        grip = getattr(self, "_resize_grip", None)
        if grip is None:
            return
        r = self.get_card_rect()
        grip.move(r.right() - 23, r.bottom() - 23)
        grip.raise_()

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_resize_grip", None):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress:
                self._resizing = True
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_size = QSize(self.card_w, self.card_h)
                if hasattr(self, "_drag_watch"):
                    self._drag_watch.start()
                return True
            if et == QEvent.Type.MouseMove:
                if self._resizing and not (event.buttons() & Qt.MouseButton.LeftButton):
                    self.resize_handle_release(event)
                    if hasattr(self, "_drag_watch"):
                        self._drag_watch.stop()
                    return True
                return self.resize_handle_move(event)
            if et == QEvent.Type.MouseButtonRelease:
                self.resize_handle_release(event)
                if hasattr(self, "_drag_watch"):
                    self._drag_watch.stop()
                return True
        return super().eventFilter(obj, event)

    def size_class(self) -> str:
        return widget_size_class(self.card_w, self.card_h)

    def _responsive_scale(self):
        mode = getattr(self, "mode", "") or getattr(self, "widget_size", "")
        base_h = 120.0 if mode == "2x1" else 248.0
        return max(0.55, min(2.5, min(self.card_w / 248.0, self.card_h / base_h)))

    @staticmethod
    def _snap_typo_px(base_px: int, scale: float) -> int:
        steps = _HERO_TYPO_STEPS if base_px > 42 else TYPO_STEPS
        target = float(base_px) * float(scale)
        return min(steps, key=lambda st: abs(st - target))

    @staticmethod
    def _collect_nested_layouts(root_layout, seen_ids, out_list):
        if root_layout is None or id(root_layout) in seen_ids:
            return
        seen_ids.add(id(root_layout))
        out_list.append(root_layout)
        for i in range(root_layout.count()):
            item = root_layout.itemAt(i)
            if item is not None and item.layout() is not None:
                ResizableWidgetMixin._collect_nested_layouts(item.layout(), seen_ids, out_list)

    def _apply_responsive_scale(self):
        """按离散排印阶梯重设子控件字号，并按比例缩放固定尺寸与布局呼吸边距；原始值只记一次。"""
        cw = getattr(self, "content_widget", None)
        if cw is None:
            return
        s = self._responsive_scale()
        all_widgets = [cw] + cw.findChildren(QWidget)

        seen_layouts = set()
        layouts = []
        for w in all_widgets:
            if not hasattr(w, "_resp_base"):
                w._resp_base = (w.styleSheet(), w.minimumWidth(), w.minimumHeight(),
                                w.maximumWidth(), w.maximumHeight())
            base_qss, min_w, min_h, max_w, max_h = w._resp_base
            if base_qss and "font-size" in base_qss:
                new_qss = re.sub(
                    r"font-size:\s*(\d+)px",
                    lambda m: "font-size: %dpx" % self._snap_typo_px(int(m.group(1)), s),
                    base_qss,
                )
                if new_qss != w.styleSheet():
                    w.setStyleSheet(new_qss)
            floor = 22 if isinstance(w, (QPushButton, QLineEdit)) else 8
            if 0 < min_w == max_w < 10000:
                w.setFixedWidth(max(floor, int(min_w * s)))
            if 0 < min_h == max_h < 10000:
                w.setFixedHeight(max(floor, int(min_h * s)))
            w_lay = QWidget.layout(w)
            if w_lay is not None:
                self._collect_nested_layouts(w_lay, seen_layouts, layouts)

        for lay in layouts:
            if not hasattr(lay, "_resp_layout_base"):
                m = lay.contentsMargins()
                lay._resp_layout_base = ((m.left(), m.top(), m.right(), m.bottom()), lay.spacing())
            (ml, mt, mr, mb), base_sp = lay._resp_layout_base
            scale_m = lambda v: max(2, int(round(v * s))) if v > 0 else 0
            lay.setContentsMargins(scale_m(ml), scale_m(mt), scale_m(mr), scale_m(mb))
            if base_sp >= 0:
                lay.setSpacing(max(1, int(round(base_sp * s))) if base_sp > 0 else 0)

    def _near_resize_handle(self, pos: QPoint) -> bool:
        r = self.get_card_rect()
        return (r.right() - 22 <= pos.x() <= r.right() + 4 and
                r.bottom() - 22 <= pos.y() <= r.bottom() + 4)

    def resize_handle_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or getattr(self, "locked", False):
            return False
        if not self._near_resize_handle(event.position().toPoint()):
            return False
        self._resizing = True
        self._resize_start_global = event.globalPosition().toPoint()
        self._resize_start_size = QSize(self.card_w, self.card_h)
        # 处于标准规格时把基准对齐到该规格，切换过 2x2/2x1 之后幅度限制依然正确
        if (self.card_w, self.card_h) in {(CARD_W_2X2, CARD_H_2X2), (CARD_W_2X1, CARD_H_2X1)}:
            self._base_card_w, self._base_card_h = self.card_w, self.card_h
        return True

    def resize_handle_move(self, event) -> bool:
        if not getattr(self, "_resizing", False):
            return False
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return False
        delta = event.globalPosition().toPoint() - self._resize_start_global
        base_w = max(self.MIN_CARD_W, getattr(self, "_base_card_w", self._resize_start_size.width()))
        base_h = max(self.MIN_CARD_H, getattr(self, "_base_card_h", self._resize_start_size.height()))
        min_w, max_w = int(base_w * 0.9), int(base_w * 1.25)
        min_h, max_h = int(base_h * 0.9), int(base_h * 1.25)
        self.card_w = max(min_w, min(max_w, self._resize_start_size.width() + delta.x()))
        self.card_h = max(min_h, min(max_h, self._resize_start_size.height() + delta.y()))
        self._update_window_size()
        # 布局挂在 content_widget 上；部分子类还把 QVBoxLayout 存成 self.layout 遮蔽方法
        cw0 = getattr(self, "content_widget", None)
        layout = QWidget.layout(cw0 if cw0 is not None else self)
        if layout is not None:
            layout.invalidate()
        if hasattr(self, "on_card_resized"):
            self.on_card_resized()
        self.update()
        return True

    def on_card_resized(self):
        """尺寸变化后：内容容器跟随、字号与固定尺寸等比缩放、把手重新置顶。"""
        cw = getattr(self, "content_widget", None)
        if cw is not None and hasattr(cw, "setGeometry"):
            cw.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
            lay = QWidget.layout(cw)
            if lay is not None:
                lay.invalidate()
        self._apply_responsive_scale()
        self._place_resize_grip()
        self.update()

    def resize_handle_release(self, event) -> bool:
        if not getattr(self, "_resizing", False):
            return False
        self._resizing = False
        signal = getattr(self, "resized", None)
        if signal is not None:
            signal.emit(self.card_w, self.card_h)
        return True

    def resize_handle_cursor(self, event):
        if getattr(self, "_dragging", False) or getattr(self, "_resizing", False):
            return
        self.setCursor(Qt.CursorShape.SizeFDiagCursor
                       if self._near_resize_handle(event.position().toPoint())
                       else Qt.CursorShape.ArrowCursor)


class BaseDesktopWidgetWindow(ResizableWidgetMixin, SnapDragMixin, QWidget):
    """桌面小组件基类：提供 Win32 图层着床、纯 QPainter 阴影、网格吸附拖拽与透明边缘穿透"""
    closed = Signal()
    position_changed = Signal(int, int)
    resized = Signal(int, int)

    def __init__(self, card_w=CARD_W_2X2, card_h=CARD_H_2X2, parent=None, config_manager=None):
        super().__init__(parent)
        if config_manager is not None and getattr(self, "config_manager", None) is None:
            self.config_manager = config_manager
        self.card_w = card_w
        self.card_h = card_h
        self._base_card_w = card_w
        self._base_card_h = card_h
        self.radius = 26
        self.opacity = 0.88
        self.locked = False
        self._dragging = False
        self._has_moved = False
        self.theme = "dark"
        self.card = None
        self._bg_cache: Optional[QPixmap] = None
        self._bg_key = None
        self._sync_glass_from_config()

        # release 事件偶尔会丢失（置顶预览框每帧 raise_ 时尤甚）。
        # 拖拽期间用短周期巡检物理左键，松开就立即收尾，绝不粘在光标上。
        self._drag_watch = QTimer(self)
        self._drag_watch.setInterval(120)
        self._drag_watch.timeout.connect(self._drag_watch_tick)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.SubWindow
        )
        if parent is None:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        base_font = QFont()
        base_font.setFamilies(_FONT_FAMILIES_TEXT)
        base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        self.setFont(base_font)

        self._init_resize_state()
        self._snap_init()
        self._update_window_size()

    def _sync_glass_from_config(self):
        cm = getattr(self, "config_manager", None)
        if cm is not None and hasattr(cm, "get"):
            try:
                self.opacity = max(0.05, min(1.0, float(cm.get("opacity", 0.88))))
                self.radius = int(cm.get("border_radius", self.radius))
            except Exception:
                pass
        self._bg_cache = None
        self._bg_key = None

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
            self.set_theme(theme)
        else:
            self.update()

    def set_theme(self, theme: str):
        self.theme = theme
        self._bg_cache = None
        self._bg_key = None
        self._apply_text_colors()
        self._on_theme_changed()
        self.update()

    def _on_theme_changed(self):
        """子类在此重刷那些由定时器/播放状态动态改色的标签。"""
        pass

    def _tint(self, widget, light_color: str):
        """浅色底板上把透明承载区的文字压成深色；其余主题恢复原 QSS。

        用「原 QSS 末尾追加一条 color」而不是重写样式，深色主题逐属性不变。
        """
        if not hasattr(widget, "_dark_qss"):
            widget._dark_qss = widget.styleSheet()
        if self.theme == "light":
            widget.setStyleSheet(_COLOR_DECL.sub(f"color: {light_color}", widget._dark_qss))
        else:
            widget.setStyleSheet(widget._dark_qss)

    def _apply_text_colors(self):
        """只处理标了 light_text 的控件：那些文字直接落在透明/浅色底板上。"""
        for w in self.findChildren(QWidget):
            light_color = w.property("light_text")
            if light_color:
                self._tint(w, light_color)

    def _dynamic_color(self, dark_color: str) -> str:
        return theme_pick(self.theme, light_text(dark_color), dark_color)

    def _update_window_size(self):
        w = self.card_w + MARGIN_SHADOW * 2
        h = self.card_h + MARGIN_SHADOW * 2
        self._bg_cache = None
        self._bg_key = None
        self.setFixedSize(w, h)

    def get_card_rect(self) -> QRect:
        return QRect(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)

    def _snap_card_rect(self) -> QRect:
        """当前卡片在桌面上的全局矩形，供网格预览/落位计算。"""
        return QRect(self.pos() + QPoint(MARGIN_SHADOW, MARGIN_SHADOW),
                     QSize(self.card_w, self.card_h))

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
                getattr(self, "theme", "dark"),
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
        t = getattr(self, "timer", None)
        if t is not None and hasattr(t, "isActive") and not t.isActive():
            t.start()
        # 首次显示时按已恢复的尺寸适配一次，并把把手提到所有内容控件之上
        self._apply_responsive_scale()
        self._place_resize_grip()
        desktop_layer.pin_to_desktop(int(self.winId()))
        QTimer.singleShot(60, lambda: desktop_layer.pin_to_desktop(int(self.winId())) if self.isVisible() else None)
        QTimer.singleShot(220, lambda: desktop_layer.pin_to_desktop(int(self.winId())) if self.isVisible() else None)

    def nativeEvent(self, eventType, message):
        try:
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                x_pos = ctypes.c_short(msg.lParam & 0xFFFF).value
                y_pos = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                local_pt = self.mapFromGlobal(QPoint(x_pos, y_pos))
                if self.get_card_rect().contains(local_pt):
                    return True, 1  # HTCLIENT
                else:
                    return True, -1  # HTTRANSPARENT
            elif msg.message == 0x0112:  # SC_MINIMIZE
                if (msg.wParam & 0xFFF0) == 0xF020:
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _finish_drag(self):
        """统一的拖拽收尾：release 与自愈路径共用，保证只提交一次。"""
        if not self._dragging:
            return
        self._dragging = False
        if getattr(self, "_has_moved", False):
            # 松手落到网格预览位置；没有合法格点才退回原位
            snapped = self._snap_commit()
            my_rect = QRect(self.pos() + QPoint(MARGIN_SHADOW, MARGIN_SHADOW),
                            QSize(self.card_w, self.card_h))
            if not snapped:
                others = get_all_desktop_card_rects(exclude_window=self)
                if others and any(my_rect.intersects(r) for r in others):
                    self.move(getattr(self, "_window_start_pos", self.pos()))
            # 松手后 2 秒内不参与自动避让，否则巡检会和用户抢方向
            self._user_moved_at = time.monotonic()
            self.position_changed.emit(self.x(), self.y())
        else:
            self._snap_hide()

    def _drag_watch_tick(self):
        """物理左键已松开就立刻收尾；release 事件丢失也不会粘在光标上。"""
        if (self._dragging or self._resizing) and not _left_button_held():
            self._finish_drag()
            self._resizing = False
        if not (self._dragging or self._resizing):
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

    def mouseMoveEvent(self, event: QMouseEvent):
        # release 丢失时的第一道自愈：还处于拖拽态但左键已松开
        if (self._dragging or self._resizing) and not (
                event.buttons() & Qt.MouseButton.LeftButton):
            self._finish_drag()
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

                # 窗口 1:1 跟手；落点由网格预览承担（只亮轮廓，不拽窗口）
                self.move(raw_win_pos)
                self._snap_update()
                event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.resize_handle_release(event):
            self._drag_watch.stop()
            event.accept()
            return
        # 任何按键的释放都结束拖拽，不再只认 LeftButton
        self._finish_drag()
        self._resizing = False
        self._drag_watch.stop()
        event.accept()

    def hideEvent(self, event):
        self._dragging = False
        self._resizing = False
        if hasattr(self, "_child_dragging"):
            self._child_dragging = False
        self._drag_watch.stop()
        self._snap_hide()
        super().hideEvent(event)

    def align_to_adjacent_folder(self):
        """一键与最近的大文件夹或相邻微件平齐对齐"""
        my_card_rect = QRect(self.pos() + QPoint(MARGIN_SHADOW, MARGIN_SHADOW), QSize(self.card_w, self.card_h))
        other_cards = get_all_desktop_card_rects(exclude_window=self)
        if not other_cards:
            return

        screen = QApplication.screenAt(my_card_rect.center()) or self.screen() or QApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        safe_left = avail_geo.left() + 8
        safe_right = avail_geo.right() - 8
        safe_top = avail_geo.top() + 8
        safe_bottom = avail_geo.bottom() - 8

        closest = min(other_cards, key=lambda r: (r.center() - my_card_rect.center()).manhattanLength())
        new_top = max(safe_top, min(safe_bottom - self.card_h, closest.top()))

        if my_card_rect.center().x() < closest.center().x():
            new_left = closest.left() - self.card_w - 16
            if new_left < safe_left:
                new_left = closest.x() + closest.width() + 16
        else:
            new_left = closest.x() + closest.width() + 16
            if new_left + self.card_w > safe_right:
                new_left = closest.left() - self.card_w - 16

        new_left = max(safe_left, min(safe_right - self.card_w, new_left))
        snapped_win_pos = QPoint(new_left - MARGIN_SHADOW, new_top - MARGIN_SHADOW)
        self.move(snapped_win_pos)
        self.position_changed.emit(snapped_win_pos.x(), snapped_win_pos.y())

    def _cleanup_resources(self):
        self._dragging = False
        self._resizing = False
        if hasattr(self, "_child_dragging"):
            self._child_dragging = False
        for name in ("timer", "stats_timer", "save_timer", "_drag_watch", "_smooth_timer", "debounce_timer"):
            t = getattr(self, name, None)
            if t is not None and hasattr(t, "isActive") and t.isActive():
                t.stop()
        self._snap_hide()

    def close_widget(self):
        self._cleanup_resources()
        self.hide()
        self.closed.emit()

    def closeEvent(self, event):
        self._cleanup_resources()
        super().closeEvent(event)


# =============================================================================
# 1. 灵动数字时钟与日历小组件 (ClockWidgetWindow)
# =============================================================================
class ClockWidgetWindow(BaseDesktopWidgetWindow):
    """
    灵动时钟日历微件:
    - 醒目大数字时间 (HH:mm 等宽排印)
    - 公历日期、星期与农历节气胶囊徽标
    - 2x2 正方与 2x1 胶囊双模切换
    """
    def __init__(self, config_manager=None, parent=None):
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent, config_manager=config_manager)
        self.config_manager = config_manager
        self._sync_glass_from_config()
        self.mode = "2x2"
        self._colon_visible = True

        self._init_ui()

        # 1 秒定时器刷新时钟
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1000)
        self._on_tick()

    def _init_ui(self):
        self.content_widget = QWidget(self)
        self.card = self.content_widget
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(4)

        # 顶部微标
        top_row = QHBoxLayout()
        self.lbl_tag = QLabel("时钟", self.content_widget)
        self.lbl_tag.setStyleSheet(
            f"color: #60a5fa; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        self.lbl_tag.setProperty("light_text", "#2563eb")
        self.lbl_sec = QLabel("", self.content_widget)
        self.lbl_sec.setStyleSheet(
            f"color: #94a3b8; font-family: {_FONT_DISPLAY_CSS}; font-size: 11px; font-weight: 500;"
        )
        self.lbl_sec.setProperty("light_text", "#64748b")
        top_row.addWidget(self.lbl_tag)
        top_row.addStretch()
        top_row.addWidget(self.lbl_sec)
        self.layout.addLayout(top_row)

        self.layout.addStretch(1)

        # 大时间展示 (HH:mm 等宽感 DemiBold 数字)
        self.lbl_time = QLabel("00:00", self.content_widget)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_time.setStyleSheet(
            f"color: #f8fafc; font-size: 54px; font-weight: 600;"
            f" font-family: {_FONT_DISPLAY_CSS}; letter-spacing: -1px;"
        )
        self.lbl_time.setProperty("light_text", "#0f172a")
        self.layout.addWidget(self.lbl_time)

        # 日期与星期
        self.lbl_date = QLabel("", self.content_widget)
        self.lbl_date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_date.setStyleSheet(
            f"color: #e2e8f0; font-family: {_FONT_TEXT_CSS}; font-size: 13px; font-weight: 500;"
        )
        self.lbl_date.setProperty("light_text", "#334155")
        self.layout.addWidget(self.lbl_date)

        # 底部农历与二十四节气精致胶囊徽标
        self.lbl_status = QLabel("", self.content_widget)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addSpacing(4)
        self.layout.addWidget(self.lbl_status, 0, Qt.AlignmentFlag.AlignHCenter)

        self.layout.addStretch(1)

        self._style_status_badge()
        self._apply_text_colors()

    def _style_status_badge(self):
        if not hasattr(self, "lbl_status"):
            return
        if self.theme == "light":
            qss = (
                f"color: #475569; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
                " background-color: rgba(15, 23, 42, 0.06);"
                " border: 1px solid rgba(15, 23, 42, 0.10);"
                " border-radius: 10px; padding: 3px 12px;"
            )
        else:
            qss = (
                f"color: #cbd5e1; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
                " background-color: rgba(255, 255, 255, 0.07);"
                " border: 1px solid rgba(255, 255, 255, 0.12);"
                " border-radius: 10px; padding: 3px 12px;"
            )
        self.lbl_status.setStyleSheet(qss)
        if hasattr(self.lbl_status, "_resp_base"):
            delattr(self.lbl_status, "_resp_base")

    def _on_theme_changed(self):
        self._style_status_badge()

    def _on_tick(self):
        now = datetime.datetime.now()
        self._colon_visible = not self._colon_visible
        self.lbl_time.setText(f"{now.strftime('%H')}:{now.strftime('%M')}")
        self.lbl_sec.setText(f"{now.strftime('%S')}s")

        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        w_text = weekdays[now.weekday()]
        self.lbl_date.setText(f"{now.month}月{now.day}日 · {w_text}")
        self.lbl_status.setText(_format_lunar_and_term(now.date()))

    def set_size_mode(self, mode: str):
        if mode == "2x1":
            self.mode = "2x1"
            self.card_w = CARD_W_2X1
            self.card_h = CARD_H_2X1
            size = 38
            self.layout.setContentsMargins(18, 10, 18, 10)
            self.layout.setSpacing(2)
            self.lbl_status.hide()
        else:
            self.mode = "2x2"
            self.card_w = CARD_W_2X2
            self.card_h = CARD_H_2X2
            size = 54
            self.layout.setContentsMargins(18, 16, 18, 16)
            self.layout.setSpacing(4)
            self.lbl_status.show()

        if hasattr(self.layout, "_resp_layout_base"):
            delattr(self.layout, "_resp_layout_base")

        qss = (
            f"color: #f8fafc; font-size: {size}px; font-weight: 600;"
            f" font-family: {_FONT_DISPLAY_CSS}; letter-spacing: -1px;"
        )
        self.lbl_time.setStyleSheet(qss)
        self.lbl_time._dark_qss = qss
        if hasattr(self.lbl_time, "_resp_base"):
            delattr(self.lbl_time, "_resp_base")
        self._style_status_badge()
        self._apply_text_colors()

        self._update_window_size()
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.update()

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))
        m_toggle = menu.addAction("切换为 2×1 规格" if self.mode == "2x2" else "切换为 2×2 规格")
        finish_widget_menu(
            self, menu, event.globalPos(), "clock",
            lambda a: self.set_size_mode("2x1" if self.mode == "2x2" else "2x2")
            if a is m_toggle else None)


# =============================================================================
# 2. 系统硬件管家微件 (SystemMonitorWidgetWindow)
# =============================================================================
class HardwareProgressBar(QWidget):
    """
    轻量级矢量胶囊进度槽控件。
    支持深浅色底槽自适应与 >85% 红色告警色阶切换。
    """
    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self.color_hex = color_hex
        self.progress_pct = 0.0
        self.setFixedHeight(6)

    def set_progress(self, pct: float):
        p = max(0.0, min(100.0, float(pct)))
        if abs(self.progress_pct - p) >= 0.5 or p in (0.0, 100.0):
            self.progress_pct = p
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        r = h * 0.5
        is_light = getattr(self.window(), "theme", "dark") == "light"

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 20) if is_light else QColor(255, 255, 255, 24))
        painter.drawRoundedRect(QRectF(0.0, 0.0, w, h), r, r)

        fill_w = (w * self.progress_pct) / 100.0
        if fill_w >= 1.0:
            active_hex = "#ef4444" if self.progress_pct > 85.0 else self.color_hex
            c_end = QColor(active_hex)
            c_start = c_end.lighter(114) if not is_light else c_end
            grad = QLinearGradient(0.0, 0.0, max(h, fill_w), 0.0)
            grad.setColorAt(0.0, c_start)
            grad.setColorAt(1.0, c_end)
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(0.0, 0.0, max(h, fill_w), h), r, r)
        painter.end()


class SystemMonitorWidgetWindow(BaseDesktopWidgetWindow):
    """
    系统资源监测微件:
    - 实时监测 CPU (#3b82f6)、RAM (#10b981) 与磁盘分区 (#8b5cf6 / #f59e0b；>85% 告警 #ef4444)
    - 右对齐等宽数字百分比，低噪克制标签层级
    """
    _DISK_PALETTE = ("#8b5cf6", "#f59e0b", "#06b6d4", "#ec4899")

    def __init__(self, config_manager=None, parent=None):
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent, config_manager=config_manager)
        self.config_manager = config_manager
        self._sync_glass_from_config()
        disk_cfg = config_manager.get_monitor_config() if config_manager else {}
        drives = disk_cfg.get("disk_drives") or [disk_cfg.get("disk_drive") or "C:\\"]
        self.disk_drives = [str(d) for d in drives if d] or ["C:\\"]

        self.cpu_pct = 0.0
        self.ram_pct = 0.0

        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_stats)
        self.timer.start(1500)
        self._refresh_stats()

    def _init_ui(self):
        self.content_widget = QWidget(self)
        self.card = self.content_widget
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(10)

        # 头部
        top_row = QHBoxLayout()
        lbl_tag = QLabel("系统监测", self.content_widget)
        lbl_tag.setStyleSheet(
            f"color: #60a5fa; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;"
        )
        lbl_tag.setProperty("light_text", "#2563eb")
        self.lbl_health = QLabel("● 系统流畅", self.content_widget)
        self.lbl_health.setStyleSheet(
            f"color: #10b981; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
        )
        top_row.addWidget(lbl_tag)
        top_row.addStretch()
        top_row.addWidget(self.lbl_health)
        self.layout.addLayout(top_row)

        # CPU / RAM 两行 + 多盘并排区
        self.lbl_cpu_val = QLabel("0%", self.content_widget)
        self.lbl_cpu_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_ram_val = QLabel("0%", self.content_widget)
        self.lbl_ram_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.layout.addWidget(self._create_metric_row("处理器 CPU", self.lbl_cpu_val, "#3b82f6"))
        self.layout.addWidget(self._create_metric_row("运行内存 RAM", self.lbl_ram_val, "#10b981"))

        self.disk_widget = QWidget(self.content_widget)
        self.disk_box = QHBoxLayout(self.disk_widget)
        self.disk_box.setContentsMargins(0, 2, 0, 2)
        self.disk_box.setSpacing(10)
        self.disk_rows = {}
        self._rebuild_disk_rows()
        self.layout.addWidget(self.disk_widget)

        self.layout.addStretch(1)

        # 底部硬件摘要
        self.lbl_desc = QLabel(self._hardware_caption(), self.content_widget)
        self.lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_desc.setStyleSheet(
            f"color: #94a3b8; font-family: {_FONT_TEXT_CSS}; font-size: 11px;"
        )
        self.lbl_desc.setProperty("light_text", "#64748b")
        self.layout.addWidget(self.lbl_desc)

        self._apply_text_colors()

    @staticmethod
    def _hardware_caption() -> str:
        try:
            cores = psutil.cpu_count(logical=True) or 0
            mem_gb = int(round(psutil.virtual_memory().total / (1024 ** 3)))
            if cores > 0 and mem_gb > 0:
                return f"{cores} 线程  ·  {mem_gb} GB 内存"
        except Exception:
            pass
        return "实时资源监测"

    def _create_metric_row(self, title: str, val_label: QLabel, color_hex: str) -> QWidget:
        row = QWidget(self.content_widget)
        r_box = QVBoxLayout(row)
        r_box.setContentsMargins(0, 2, 0, 2)
        r_box.setSpacing(5)

        t_row = QHBoxLayout()
        lbl_t = QLabel(title, row)
        lbl_t.setStyleSheet(
            f"color: #cbd5e1; font-family: {_FONT_TEXT_CSS}; font-size: 12px; font-weight: 500;"
        )
        lbl_t.setProperty("light_text", "#475569")
        val_label.setStyleSheet(
            f"color: #f8fafc; font-family: {_FONT_DISPLAY_CSS}; font-size: 12px; font-weight: 600;"
        )
        val_label.setProperty("light_text", "#0f172a")
        t_row.addWidget(lbl_t)
        t_row.addStretch()
        t_row.addWidget(val_label)
        r_box.addLayout(t_row)

        bar_bg = HardwareProgressBar(color_hex, row)
        r_box.addWidget(bar_bg)

        row.bar_bg = bar_bg
        row.color_hex = color_hex
        return row

    def apply_drives(self, drives):
        """设置页改了勾选后热更新，不重建窗口、不改变用户摆好的位置。"""
        self.disk_drives = [str(d) for d in drives if d] or ["C:\\"]
        self._rebuild_disk_rows()
        self._refresh_stats()

    def _rebuild_disk_rows(self):
        """按勾选的分区生成并排小卡片，不增加组件整体高度。"""
        while self.disk_box.count():
            item = self.disk_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.disk_rows.clear()
        for idx, drive in enumerate(self.disk_drives):
            bar_color = self._DISK_PALETTE[idx % len(self._DISK_PALETTE)]
            cell = QWidget(self.disk_widget)
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            head = QHBoxLayout()
            t = QLabel(f"磁盘 {drive.rstrip(chr(92))}" if len(self.disk_drives) == 1 else drive.rstrip("\\"), cell)
            t.setStyleSheet(
                f"color: #cbd5e1; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
            )
            t.setProperty("light_text", "#475569")
            val = QLabel("0%", cell)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setStyleSheet(
                f"color: #f8fafc; font-family: {_FONT_DISPLAY_CSS}; font-size: 11px; font-weight: 600;"
            )
            val.setProperty("light_text", "#0f172a")
            head.addWidget(t)
            head.addStretch()
            head.addWidget(val)
            v.addLayout(head)
            bar = HardwareProgressBar(bar_color, cell)
            bar.setFixedHeight(6)
            v.addWidget(bar)
            self.disk_box.addWidget(cell, stretch=1)
            self.disk_rows[drive] = (val, bar)
        # 兼容既有调用：第一个分区的数值标签
        first = self.disk_drives[0]
        self.lbl_disk_val = self.disk_rows[first][0]
        self._apply_text_colors()

    def _on_theme_changed(self):
        for _, (_, bar) in self.disk_rows.items():
            bar.update()
        for row_idx in (0, 1):
            w = self.layout.itemAt(row_idx + 1).widget()
            if w is not None and hasattr(w, "bar_bg"):
                w.bar_bg.update()
        self._refresh_stats()

    def _style_pct_label(self, lbl: QLabel, pct: float, size_px: int = 12):
        warn = pct > 85.0
        if warn:
            c = self._dynamic_color("#ef4444")
        else:
            c = "#0f172a" if self.theme == "light" else "#f8fafc"
        lbl.setStyleSheet(
            f"color: {c}; font-family: {_FONT_DISPLAY_CSS}; font-size: {size_px}px; font-weight: 600;"
        )

    def _refresh_stats(self):
        try:
            self.cpu_pct = psutil.cpu_percent(interval=None)
            self.ram_pct = psutil.virtual_memory().percent

            self.lbl_cpu_val.setText(f"{int(round(self.cpu_pct))}%")
            self.lbl_ram_val.setText(f"{int(round(self.ram_pct))}%")
            self._style_pct_label(self.lbl_cpu_val, self.cpu_pct, 12)
            self._style_pct_label(self.lbl_ram_val, self.ram_pct, 12)

            max_disk_pct = 0.0
            for drive, (val_lbl, bar) in self.disk_rows.items():
                try:
                    pct = psutil.disk_usage(drive).percent
                except Exception:
                    pct = 0.0
                max_disk_pct = max(max_disk_pct, pct)
                val_lbl.setText(f"{int(round(pct))}%")
                self._style_pct_label(val_lbl, pct, 11)
                bar.set_progress(pct)

            if self.cpu_pct > 85 or self.ram_pct > 85 or max_disk_pct > 90:
                self.lbl_health.setText("● 负载偏高")
                c, weight = "#ef4444", 600
            elif self.cpu_pct > 65 or self.ram_pct > 75:
                self.lbl_health.setText("● 运行繁忙")
                c, weight = "#f59e0b", 500
            else:
                self.lbl_health.setText("● 系统流畅")
                c, weight = "#10b981", 500
            self.lbl_health.setStyleSheet(
                f"color: {self._dynamic_color(c)}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: {weight};"
            )

            self._update_row_bar(0, self.cpu_pct, "#3b82f6")
            self._update_row_bar(1, self.ram_pct, "#10b981")
        except Exception:
            pass

    def _update_row_bar(self, row_idx: int, pct: float, color: str):
        widget = self.layout.itemAt(row_idx + 1).widget()
        if hasattr(widget, "bar_bg"):
            if isinstance(widget.bar_bg, HardwareProgressBar):
                widget.bar_bg.set_progress(pct)
            else:
                p = max(2, min(100, int(pct)))
                qss = f"""
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop: 0 {color}, stop: {p/100.0:.2f} {color},
                        stop: {min(1.0, p/100.0 + 0.001):.3f} rgba(255, 255, 255, 0.08),
                        stop: 1 rgba(255, 255, 255, 0.08));
                    border-radius: 3px;
                """
                widget.bar_bg.setStyleSheet(qss)

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))
        m_refresh = menu.addAction("刷新状态")
        finish_widget_menu(self, menu, event.globalPos(), "monitor",
                           lambda a: self._refresh_stats() if a is m_refresh else None)


# =============================================================================
# 3. 桌面随手便签与待办清单微件 (MemoWidgetWindow)
# =============================================================================

class BlurAwareTextEdit(QTextEdit):
    """支持失焦即时保存的增强文本框"""
    def __init__(self, on_blur=None, parent=None):
        super().__init__(parent)
        self.on_blur = on_blur

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if callable(self.on_blur):
            self.on_blur()


class _TodoCheckBox(QCheckBox):
    """自绘抗锯齿圆形待办复选框：柔和细环与勾选对勾，彻底告别生硬直角像素框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        is_light = getattr(self.window(), "theme", "dark") == "light"
        circle = QRectF(1.5, 1.5, 15.0, 15.0)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#f59e0b"))
            p.drawEllipse(circle)
            pen = QPen(QColor("#ffffff"), 1.75, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            chk = QPainterPath()
            chk.moveTo(5.2, 9.2)
            chk.lineTo(7.9, 11.9)
            chk.lineTo(12.8, 6.6)
            p.drawPath(chk)
        else:
            hovered = self.underMouse()
            if hovered:
                ring = QColor("#f59e0b")
            elif is_light:
                ring = QColor(15, 23, 42, 72)
            else:
                ring = QColor(255, 255, 255, 88)
            p.setPen(QPen(ring, 1.35))
            p.setBrush(QColor(245, 158, 11, 20) if hovered else Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle)
        p.end()


class MemoWidgetWindow(BaseDesktopWidgetWindow):
    """
    桌面随手便签待办微件:
    - 随手便签 (自由文本) 与 待办清单 (交互打勾) 双模无缝切换
    - 失焦 0ms 自动保存持久化，支持深色 / 浅色双主题自适应
    """
    def __init__(self, config_manager=None, parent=None):
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent, config_manager=config_manager)
        self.config_manager = config_manager
        self._sync_glass_from_config()
        data_dir = _get_user_data_dir()
        self.memo_file = os.path.join(data_dir, ".desktop_memo.txt")
        self.todos_file = os.path.join(data_dir, ".desktop_todos.json")
        self.todos_data: List[Dict[str, Any]] = []

        self._init_ui()
        self._load_memo()
        self._load_todos()

    def _init_ui(self):
        self.content_widget = QWidget(self)
        self.card = self.content_widget
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.layout = QVBoxLayout(self.content_widget)
        self.layout.setContentsMargins(18, 16, 18, 16)
        self.layout.setSpacing(8)

        # 头部：分段胶囊切换器 + 状态副标
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.btn_tab_memo = QPushButton("便签", self.content_widget)
        self.btn_tab_memo.setCheckable(True)
        self.btn_tab_memo.setChecked(True)
        self.btn_tab_memo.setFixedHeight(24)
        self.btn_tab_memo.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_tab_todo = QPushButton("待办", self.content_widget)
        self.btn_tab_todo.setCheckable(True)
        self.btn_tab_todo.setChecked(False)
        self.btn_tab_todo.setFixedHeight(24)
        self.btn_tab_todo.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_tab_memo.clicked.connect(lambda: self._switch_tab(0))
        self.btn_tab_todo.clicked.connect(lambda: self._switch_tab(1))

        top_row.addWidget(self.btn_tab_memo)
        top_row.addWidget(self.btn_tab_todo)
        top_row.addStretch()

        self.lbl_hint = QLabel("自动保存", self.content_widget)
        top_row.addWidget(self.lbl_hint)
        self.layout.addLayout(top_row)

        # 核心多页容器
        self.stack = QStackedWidget(self.content_widget)

        # ---------------- Page 0: 便签页面 ----------------
        page_memo = QWidget()
        layout_memo = QVBoxLayout(page_memo)
        layout_memo.setContentsMargins(0, 0, 0, 0)

        self.edit_memo = BlurAwareTextEdit(on_blur=self._save_memo_to_disk, parent=page_memo)
        self.edit_memo.setPlaceholderText("记录灵感、备忘或临时要事…")
        self.edit_memo.textChanged.connect(self._save_memo_debounced)
        layout_memo.addWidget(self.edit_memo)
        self.stack.addWidget(page_memo)

        # ---------------- Page 1: 待办清单页面 ----------------
        page_todo = QWidget()
        layout_todo = QVBoxLayout(page_todo)
        layout_todo.setContentsMargins(0, 0, 0, 0)
        layout_todo.setSpacing(7)

        # 添加任务输入胶囊栏
        add_bar = QHBoxLayout()
        add_bar.setSpacing(6)
        self.edit_new_todo = QLineEdit(page_todo)
        self.edit_new_todo.setPlaceholderText("新建待办，回车添加…")
        self.edit_new_todo.setFixedHeight(28)
        self.edit_new_todo.returnPressed.connect(self._add_new_todo)

        self.btn_add_todo = QPushButton("+", page_todo)
        self.btn_add_todo.setFixedSize(28, 28)
        self.btn_add_todo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_todo.clicked.connect(self._add_new_todo)

        add_bar.addWidget(self.edit_new_todo)
        add_bar.addWidget(self.btn_add_todo)
        layout_todo.addLayout(add_bar)

        # 待办滚动列表
        self.todo_scroll = QScrollArea(page_todo)
        self.todo_scroll.setWidgetResizable(True)
        self.todo_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.todo_container = QWidget()
        self.todo_container.setStyleSheet("background: transparent;")
        self.todo_items_layout = QVBoxLayout(self.todo_container)
        self.todo_items_layout.setContentsMargins(4, 4, 4, 4)
        self.todo_items_layout.setSpacing(4)
        self.todo_items_layout.addStretch()
        self.todo_scroll.setWidget(self.todo_container)
        layout_todo.addWidget(self.todo_scroll, stretch=1)

        # 底部摘要与清空已完成
        todo_bottom = QHBoxLayout()
        self.lbl_todo_summary = QLabel("已完成 0/0", page_todo)
        self.btn_clear_done = QPushButton("清空已完成", page_todo)
        self.btn_clear_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_done.clicked.connect(self._clear_done_todos)
        todo_bottom.addWidget(self.lbl_todo_summary)
        todo_bottom.addStretch()
        todo_bottom.addWidget(self.btn_clear_done)
        layout_todo.addLayout(todo_bottom)

        self.stack.addWidget(page_todo)
        self.layout.addWidget(self.stack, stretch=1)

        # 便签保存防抖定时器 (500ms)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self._save_memo_to_disk)

        self._apply_memo_theme()
        self._apply_text_colors()

    def _apply_memo_theme(self):
        is_light = self.theme == "light"
        if is_light:
            tab_bg = "rgba(15, 23, 42, 0.05)"
            tab_fg = "#475569"
            tab_border = "rgba(15, 23, 42, 0.10)"
            tab_hover = "rgba(15, 23, 42, 0.09)"
            tab_chk_bg = "rgba(245, 158, 11, 0.16)"
            tab_chk_fg = "#b45309"
            tab_chk_border = "rgba(217, 119, 6, 0.36)"
            well_bg = "rgba(255, 255, 255, 0.80)"
            well_focus = "rgba(255, 255, 255, 0.94)"
            well_border = "rgba(15, 23, 42, 0.12)"
            text_fg = "#0f172a"
            muted_fg = "#64748b"
            ph_col = "#94a3b8"
            sb_handle = "rgba(15, 23, 42, 0.20)"
            scroll_border = "rgba(15, 23, 42, 0.08)"
        else:
            tab_bg = "rgba(255, 255, 255, 0.06)"
            tab_fg = "#cbd5e1"
            tab_border = "rgba(255, 255, 255, 0.12)"
            tab_hover = "rgba(255, 255, 255, 0.12)"
            tab_chk_bg = "rgba(245, 158, 11, 0.22)"
            tab_chk_fg = "#fbbf24"
            tab_chk_border = "rgba(245, 158, 11, 0.50)"
            well_bg = "rgba(12, 15, 22, 0.48)"
            well_focus = "rgba(12, 15, 22, 0.68)"
            well_border = "rgba(255, 255, 255, 0.14)"
            text_fg = "#f8fafc"
            muted_fg = "#94a3b8"
            ph_col = "#94a3b8"
            sb_handle = "rgba(255, 255, 255, 0.22)"
            scroll_border = "rgba(255, 255, 255, 0.08)"

        tab_style = f"""
            QPushButton {{
                background-color: {tab_bg};
                color: {tab_fg};
                border: 1px solid {tab_border};
                border-radius: 12px;
                padding: 0 11px;
                font-family: {_FONT_TEXT_CSS};
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {tab_hover};
                color: {text_fg};
            }}
            QPushButton:checked {{
                background-color: {tab_chk_bg};
                color: {tab_chk_fg};
                border: 1px solid {tab_chk_border};
            }}
        """
        self.btn_tab_memo.setStyleSheet(tab_style)
        self.btn_tab_todo.setStyleSheet(tab_style)
        self.lbl_hint.setStyleSheet(
            f"color: {muted_fg}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; background: transparent;"
        )

        self.edit_memo.setStyleSheet(f"""
            QTextEdit {{
                background-color: {well_bg};
                color: {text_fg};
                border: 1px solid {well_border};
                border-radius: 10px;
                padding: 8px 10px;
                font-size: 13px;
                font-family: {_FONT_TEXT_CSS};
                selection-background-color: #f59e0b;
                selection-color: #ffffff;
            }}
            QTextEdit:focus {{
                background-color: {well_focus};
                border: 1px solid #f59e0b;
            }}
            QScrollBar:vertical {{
                width: 4px;
                background: transparent;
                margin: 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        _memo_pal = self.edit_memo.palette()
        _memo_pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(ph_col))
        self.edit_memo.setPalette(_memo_pal)

        self.edit_new_todo.setStyleSheet(f"""
            QLineEdit {{
                background-color: {well_bg};
                color: {text_fg};
                border: 1px solid {well_border};
                border-radius: 8px;
                padding: 0 10px;
                font-family: {_FONT_TEXT_CSS};
                font-size: 12px;
                selection-background-color: #f59e0b;
                selection-color: #ffffff;
            }}
            QLineEdit:focus {{
                border: 1px solid #f59e0b;
                background-color: {well_focus};
            }}
        """)
        _todo_pal = self.edit_new_todo.palette()
        _todo_pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(ph_col))
        self.edit_new_todo.setPalette(_todo_pal)

        self.btn_add_todo.setStyleSheet(f"""
            QPushButton {{
                background-color: #f59e0b;
                color: #ffffff;
                padding: 0px;
                font-family: {_FONT_DISPLAY_CSS};
                font-size: 16px;
                font-weight: 600;
                border-radius: 8px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #fbbf24;
            }}
            QPushButton:pressed {{
                background-color: #d97706;
            }}
        """)

        self.todo_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: 1px solid {scroll_border};
                border-radius: 8px;
            }}
            QScrollBar:vertical {{
                width: 4px;
                background: transparent;
                margin: 3px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

        self.lbl_todo_summary.setStyleSheet(
            f"color: {muted_fg}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; background: transparent;"
        )
        self.btn_clear_done.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {muted_fg};
                border: none;
                padding: 0px;
                font-family: {_FONT_TEXT_CSS};
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                color: #ef4444;
            }}
        """)
        for w in (self.btn_tab_memo, self.btn_tab_todo, self.lbl_hint, self.edit_memo,
                  self.edit_new_todo, self.btn_add_todo, self.lbl_todo_summary, self.btn_clear_done):
            if hasattr(w, "_resp_base"):
                delattr(w, "_resp_base")

    def _on_theme_changed(self):
        self._apply_memo_theme()
        self._render_todos()

    def _switch_tab(self, index: int):
        if hasattr(self, "save_timer") and self.save_timer.isActive():
            self.save_timer.stop()
            self._save_memo_to_disk()
        self.stack.setCurrentIndex(index)
        self.btn_tab_memo.setChecked(index == 0)
        self.btn_tab_todo.setChecked(index == 1)
        self.lbl_hint.setText("自动保存" if index == 0 else "即点即记")

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "save_timer") and self.save_timer.isActive():
            self.save_timer.stop()
            self._save_memo_to_disk()

    def _cleanup_resources(self):
        if hasattr(self, "save_timer") and self.save_timer.isActive():
            self.save_timer.stop()
            self._save_memo_to_disk()
        super()._cleanup_resources()

    def close_widget(self):
        self._cleanup_resources()
        self.hide()
        self.closed.emit()

    def _load_memo(self):
        if os.path.exists(self.memo_file):
            try:
                with open(self.memo_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if "仿手机大文件夹" in content:
                    content = content.replace("仿手机大文件夹", "Stacky 灵叠")
                    try:
                        with open(self.memo_file, "w", encoding="utf-8") as fw:
                            fw.write(content)
                    except Exception:
                        pass
                self.edit_memo.setPlainText(content)
            except Exception:
                pass
        else:
            self.edit_memo.setPlainText("欢迎使用 Stacky 灵叠\n\n· 整理常用应用与快捷方式\n· 体验天气与系统监测小组件\n· Alt + Space 快速唤出")

    def _save_memo_debounced(self):
        self.save_timer.start()

    def _save_memo_to_disk(self):
        try:
            with open(self.memo_file, "w", encoding="utf-8") as f:
                f.write(self.edit_memo.toPlainText())
        except Exception:
            pass

    def _load_todos(self):
        if os.path.exists(self.todos_file):
            try:
                with open(self.todos_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.todos_data = [it for it in loaded if isinstance(it, dict)] if isinstance(loaded, list) else []
            except Exception:
                self.todos_data = []
        else:
            self.todos_data = [
                {"text": "整理常用应用分类", "done": False},
                {"text": "体验系统媒体播放小组件", "done": False},
                {"text": "双击桌面空白隐藏卡片", "done": True},
            ]
        self._render_todos()

    def _save_todos_to_disk(self):
        try:
            with open(self.todos_file, "w", encoding="utf-8") as f:
                json.dump(self.todos_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self._update_todo_summary()

    def _add_new_todo(self):
        text = self.edit_new_todo.text().strip()
        if not text:
            return
        self.todos_data.append({"text": text, "done": False})
        self.edit_new_todo.clear()
        self._save_todos_to_disk()
        self._render_todos()

    def _toggle_todo(self, index: int, done: bool):
        if 0 <= index < len(self.todos_data):
            self.todos_data[index]["done"] = done
            self._save_todos_to_disk()
            self._update_todo_item_style(index, done)
            self._update_todo_summary()

    def _todo_label_style(self, done: bool) -> str:
        is_light = self.theme == "light"
        if done:
            c = "#94a3b8" if is_light else "#64748b"
        else:
            c = "#0f172a" if is_light else "#f8fafc"
        return f"color: {c}; font-family: {_FONT_TEXT_CSS}; font-size: 12px; background: transparent;"

    def _update_todo_item_style(self, index: int, done: bool):
        if 0 <= index < self.todo_items_layout.count() - 1:
            item = self.todo_items_layout.itemAt(index)
            if item and item.widget():
                lbl = getattr(item.widget(), "_todo_label", None)
                if lbl:
                    font = lbl.font()
                    font.setStrikeOut(done)
                    lbl.setFont(font)
                    lbl.setStyleSheet(self._todo_label_style(done))

    def _delete_todo(self, index: int):
        if 0 <= index < len(self.todos_data):
            self.todos_data.pop(index)
            self._save_todos_to_disk()
            self._render_todos()

    def _clear_done_todos(self):
        self.todos_data = [t for t in self.todos_data if not t.get("done", False)]
        self._save_todos_to_disk()
        self._render_todos()

    def _render_todos(self):
        while self.todo_items_layout.count() > 1:
            item = self.todo_items_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        is_light = self.theme == "light"
        row_bg = "rgba(255, 255, 255, 0.65)" if is_light else "rgba(255, 255, 255, 0.05)"
        row_hover = "rgba(255, 255, 255, 0.90)" if is_light else "rgba(255, 255, 255, 0.10)"
        del_fg = "#94a3b8" if is_light else "#94a3b8"

        for idx, item in enumerate(self.todos_data):
            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{
                    background-color: {row_bg};
                    border-radius: 7px;
                }}
                QWidget:hover {{
                    background-color: {row_hover};
                }}
            """)
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(8, 5, 6, 5)
            r_lay.setSpacing(7)

            chk = _TodoCheckBox(row)
            chk.setChecked(item.get("done", False))
            chk.toggled.connect(lambda d, i=idx: self._toggle_todo(i, d))
            r_lay.addWidget(chk)

            done_flag = bool(item.get("done", False))
            lbl = QLabel(item.get("text", ""), row)
            lbl_font = QFont()
            lbl_font.setFamilies(_FONT_FAMILIES_TEXT)
            lbl_font.setStrikeOut(done_flag)
            lbl.setFont(lbl_font)
            lbl.setStyleSheet(self._todo_label_style(done_flag))
            r_lay.addWidget(lbl, stretch=1)
            row._todo_label = lbl

            btn_del = QPushButton("×", row)
            btn_del.setFixedSize(18, 18)
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {del_fg};
                    border: none;
                    padding: 0px;
                    border-radius: 9px;
                    font-family: {_FONT_DISPLAY_CSS};
                    font-size: 13px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: rgba(239, 68, 68, 0.16);
                    color: #ef4444;
                }}
            """)
            btn_del.clicked.connect(lambda checked=False, i=idx: self._delete_todo(i))
            r_lay.addWidget(btn_del)

            self.todo_items_layout.insertWidget(self.todo_items_layout.count() - 1, row)

        self._update_todo_summary()

    def _update_todo_summary(self):
        total = len(self.todos_data)
        done = sum(1 for t in self.todos_data if t.get("done", False))
        self.lbl_todo_summary.setText(f"已完成 {done}/{total}")

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))
        m_toggle_tab = menu.addAction("切换为待办清单" if self.stack.currentIndex() == 0 else "切换为随手便签")
        m_clear = menu.addAction("清空便签内容" if self.stack.currentIndex() == 0 else "清空已完成待办")

        def handle_extra(a):
            if a is m_toggle_tab:
                self._switch_tab(1 if self.stack.currentIndex() == 0 else 0)
            elif a is m_clear:
                if self.stack.currentIndex() == 0:
                    self.edit_memo.clear()
                    self._save_memo_to_disk()
                else:
                    self._clear_done_todos()

        finish_widget_menu(self, menu, event.globalPos(), "memo", handle_extra)


# =============================================================================
# 4. 系统媒体播放微件 (NowPlayingWidgetWindow / MediaWidgetWindow)
# =============================================================================

class HoverSlider(QSlider):
    """可拖动的真实 4px 胶囊进度条：单击定位、按住拖动，悬停平滑浮现圆点滑块。"""

    def __init__(self, parent=None, theme: str = "dark"):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._dragging = False
        self._hover = False
        self._theme = theme
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_handle(False)

    def refresh_theme(self, theme: str):
        self._theme = theme
        self._apply_handle(self._hover)

    def enterEvent(self, event):
        self._hover = True
        self._apply_handle(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._apply_handle(False)
        super().leaveEvent(event)

    def _set_from_x(self, x):
        from PySide6.QtWidgets import QStyle
        val = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(x), self.width())
        self.blockSignals(True)
        self.setValue(val)
        self.blockSignals(False)
        self.sliderMoved.emit(val)

    def _end_drag(self):
        if not self._dragging:
            return
        self._dragging = False
        self.setSliderDown(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setSliderDown(True)
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and not (event.buttons() & Qt.MouseButton.LeftButton):
            self._end_drag()
            event.accept()
            return
        if self._dragging:
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._end_drag()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _apply_handle(self, on: bool):
        is_light = (self._theme == "light") or (getattr(self.window(), "theme", "dark") == "light")
        groove = "rgba(15, 23, 42, 0.14)" if is_light else "rgba(255, 255, 255, 0.18)"
        fill = "#2563eb" if is_light else "#3b82f6"
        thumb_bg = "#2563eb" if is_light else "#ffffff"
        base = (
            f"QSlider::groove:horizontal {{ height: 4px; background: {groove}; border-radius: 2px; }}"
            f" QSlider::sub-page:horizontal {{ background: {fill}; border-radius: 2px; }}"
        )
        handle = (
            f" QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0;"
            f" background: {thumb_bg}; border-radius: 6px; }}"
            if on else
            " QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0;"
            " background: transparent; border: none; }"
        )
        self.setStyleSheet(base + handle)


class RoundMediaButton(QPushButton):
    """自绘抗锯齿圆形媒体控制键：精致悬停光圈与高对比图标/字标。"""

    def __init__(self, primary: bool = False, parent=None):
        super().__init__(parent)
        self.accent = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_accent(self, on: bool):
        self.accent = bool(on)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        light = getattr(self.window(), "theme", "dark") == "light"
        hovered = self.underMouse()
        if self.accent:
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            if hovered:
                grad.setColorAt(0.0, QColor("#60a5fa"))
                grad.setColorAt(1.0, QColor("#3b82f6"))
            else:
                grad.setColorAt(0.0, QColor("#3b82f6"))
                grad.setColorAt(1.0, QColor("#2563eb"))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(QColor(255, 255, 255, 58), 0.8))
        elif light:
            p.setBrush(QColor(15, 23, 42, 24 if hovered else 11))
            p.setPen(QPen(QColor(15, 23, 42, 32 if hovered else 18), 0.85))
        else:
            p.setBrush(QColor(255, 255, 255, 42 if hovered else 18))
            p.setPen(QPen(QColor(255, 255, 255, 36 if hovered else 16), 0.85))
        p.drawEllipse(r)
        if not self.icon().isNull():
            sz = max(10, min(self.width(), self.height()) - 12)
            self.icon().paint(p, QRect(int((self.width() - sz) / 2),
                                       int((self.height() - sz) / 2), sz, sz))
        elif self.text():
            f = QFont()
            f.setFamilies(_FONT_FAMILIES_TEXT)
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
            p.setFont(f)
            if self.accent:
                p.setPen(QColor("#ffffff"))
            else:
                p.setPen(QColor("#334155") if light else QColor("#e2e8f0"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class NowPlayingWidgetWindow(BaseDesktopWidgetWindow):
    """
    超椭圆系统媒体播放微件:
    - 纯原生对接 Windows 10/11 系统媒体控制协议 (GSMTC)
    - 实时捕获网易云音乐、QQ音乐、Spotify、PotPlayer、浏览器等播放元数据与高清专辑封面
    - 2x2 正方全景 (248x248) 与 2x1 胶囊规格 (248x120) 动态切换
    - 纯 QPainter 矢量超椭圆与拟态黑胶唱片，播放/暂停/上一曲/下一曲全触控
    """
    size_changed = Signal(str)

    def __init__(self, config_manager=None, parent=None):
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent, config_manager=config_manager)
        self.config_manager = config_manager
        self._sync_glass_from_config()
        self.mode = "2x2"

        self._track_title = "暂无播放媒体"
        self._artist_name = "打开播放器自动捕获"
        self._source_app = "系统媒体"
        self._is_playing = False
        self._cover_pixmap: Optional[QPixmap] = None
        self._cached_cover_pixmap: Optional[QPixmap] = None
        self._seeking = False
        self._position = 0.0
        self._duration = 0.0
        self._anchor_pos = 0.0
        self._anchor_at = time.monotonic()
        self._seek_supported = False   # 建 UI 前先定：默认只读，能力探测后再放开
        self._show_lyrics = (bool(config_manager.get_media_config().get("show_lyrics", True))
                             if config_manager is not None else True)

        self._init_ui()

        self.lyrics = get_lyrics_service()
        self.service = get_media_service()
        self._seek_supported = self.service.can_seek()
        self._signals_connected = False
        self._connect_service_signals()
        self.service.start_polling(1200)

        # 轮询 500ms 之间按墙上时钟插值，进度条连续推进而不是一秒一跳
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setInterval(100)
        self._smooth_timer.timeout.connect(self._on_smooth_tick)
        self._smooth_timer.start()
        self._on_seek_capability(self._seek_supported)

        # 初始同步媒体信息
        info = self.service.get_current_info()
        if info.get("has_media", False):
            self._on_media_info_changed(info)

    def _connect_service_signals(self):
        if getattr(self, "_signals_connected", False):
            return
        self._signals_connected = True
        if hasattr(self, "lyrics") and self.lyrics is not None:
            self.lyrics.lyrics_ready.connect(self._on_lyrics_ready)
        if hasattr(self, "service") and self.service is not None:
            self.service.media_changed.connect(self._on_media_info_changed)
            self.service.playback_status_changed.connect(self._on_playback_status_changed)
            self.service.timeline_updated.connect(self._on_timeline_updated)
            self.service.seek_capability_changed.connect(self._on_seek_capability)

    def _disconnect_service_signals(self):
        if not getattr(self, "_signals_connected", False):
            return
        self._signals_connected = False
        for sig, slot in (
            (getattr(getattr(self, "lyrics", None), "lyrics_ready", None), self._on_lyrics_ready),
            (getattr(getattr(self, "service", None), "media_changed", None), self._on_media_info_changed),
            (getattr(getattr(self, "service", None), "playback_status_changed", None), self._on_playback_status_changed),
            (getattr(getattr(self, "service", None), "timeline_updated", None), self._on_timeline_updated),
            (getattr(getattr(self, "service", None), "seek_capability_changed", None), self._on_seek_capability),
        ):
            if sig is not None:
                try:
                    sig.disconnect(slot)
                except Exception:
                    pass

    def showEvent(self, event):
        super().showEvent(event)
        self._connect_service_signals()
        if hasattr(self, "service") and self.service:
            self.service.start_polling(1200)
        if hasattr(self, "_smooth_timer"):
            self._smooth_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "service") and self.service:
            self.service.stop_polling()
        if hasattr(self, "_smooth_timer"):
            self._smooth_timer.stop()

    def _cleanup_resources(self):
        self._disconnect_service_signals()
        if hasattr(self, "service") and self.service:
            self.service.stop_polling()
        super()._cleanup_resources()

    def close_widget(self):
        self._cleanup_resources()
        self.hide()
        self.closed.emit()

    def on_card_resized(self):
        super().on_card_resized()
        # 宽扁档切横排、其余竖排：换布局而不是把同一套排版拉变形
        cls = self.size_class()
        want = "2x1" if cls == "wide" else "2x2"
        is_mini = (cls == "mini")
        if getattr(self, "_auto_mode", None) != want or getattr(self, "_is_mini", False) != is_mini:
            self._auto_mode = want
            self._is_mini = is_mini
            cur_w, cur_h = self.card_w, self.card_h
            self.set_size_mode(want)          # 重建布局（内部会清理旧控件）
            self.card_w, self.card_h = cur_w, cur_h   # 保留用户拉出的尺寸
            self._update_window_size()
            self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, cur_w, cur_h)
            if is_mini:
                self._apply_mini_mode()
            self._apply_responsive_scale()
        self._rebuild_cached_cover()

    def _apply_mini_mode(self):
        """150px 小卡只留：歌名一行 + 歌词一行 + 细进度 + 三个小圆钮。"""
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(2)
        self.content_layout.insertStretch(0, 1)
        self.cover_spacer.setFixedHeight(0)
        for name in ("lbl_source", "lbl_status", "lbl_pos", "lbl_dur",
                     "lbl_lyric", "btn_lyrics"):
            w = getattr(self, name, None)
            if w is not None:
                w.hide()
        for btn in (self.btn_prev, self.btn_play_pause, self.btn_next):
            btn.setFixedSize(20, 20)
        self.content_layout.addStretch(1)

    def _cover_size(self) -> int:
        """封面随卡片尺寸等比缩放，避免拉伸后溢出或缩成一小块。"""
        if getattr(self, "_is_mini", False):
            return max(32, max(self.card_w, self.card_h))
        base = 100 if self.mode == "2x2" else 80
        base_h = CARD_H_2X2 if self.mode == "2x2" else CARD_H_2X1
        scale = min(self.card_w / CARD_W_2X2, self.card_h / base_h)
        return max(32, int(base * scale))

    def _rebuild_cached_cover(self):
        if not self._cover_pixmap or self._cover_pixmap.isNull():
            self._cached_cover_pixmap = None
            return
        cover_size = self._cover_size()
        scaled = self._cover_pixmap.scaled(
            QSize(cover_size, cover_size),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        ox = (scaled.width() - cover_size) // 2
        oy = (scaled.height() - cover_size) // 2
        self._cached_cover_pixmap = scaled.copy(ox, oy, cover_size, cover_size)

    def _init_ui(self):
        self.content_widget = QWidget(self)
        self.card = self.content_widget
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self._build_layout()

    def _clear_layout_deep(self, layout):
        """递归清理：多层嵌套布局里的旧控件也必须立即摘除，否则会和新的叠在一起。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout_deep(item.layout())

    def _build_layout(self):
        # 清除现有子控件
        if hasattr(self, "content_layout") and self.content_layout:
            self._clear_layout_deep(self.content_layout)
            QWidget().setLayout(self.content_layout)

        if self.mode == "2x1":
            self._build_2x1_layout()
        else:
            self._build_2x2_layout()
        self._apply_text_colors()

    def _on_theme_changed(self):
        if hasattr(self, "progress_slider"):
            self.progress_slider.refresh_theme(self.theme)
        self._lyric_color = None
        self._refresh_lyric(self._position)
        if hasattr(self, "lbl_status"):
            self._on_playback_status_changed(self._is_playing)
        if hasattr(self, "btn_prev"):
            self._apply_control_icons()
        for name in ("btn_prev", "btn_play_pause", "btn_next", "btn_lyrics"):
            b = getattr(self, name, None)
            if b is not None:
                b.update()

    def _apply_control_icons(self):
        """三个控制键统一用自绘图标，避免 emoji 在缺字体环境下变空方块。"""
        side = theme_pick(self.theme, QColor(15, 23, 42, 235), QColor(255, 255, 255, 240))
        self.btn_prev.setIcon(_media_button_icon("prev", color=side))
        self.btn_next.setIcon(_media_button_icon("next", color=side))
        self.btn_play_pause.setIcon(_media_button_icon("pause" if self._is_playing else "play"))
        for btn in (self.btn_prev, self.btn_play_pause, self.btn_next):
            btn.setText("")
            btn.setIconSize(QSize(14, 14))

    def _create_lyrics_button(self, parent, size):
        """组件上直接可点的歌词开关（开=蓝色高亮，关=灰）。"""
        btn = RoundMediaButton(False, parent)
        btn.setText("词")
        btn.setFixedSize(size, size)
        btn.setToolTip("关闭歌词" if self._show_lyrics else "显示歌词")
        btn.set_accent(self._show_lyrics)
        btn.clicked.connect(lambda: self.set_show_lyrics(not self._show_lyrics))
        return btn

    def _create_lyric_label(self, parent):
        lbl = QLabel("", parent)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setMinimumHeight(15)
        lbl.setStyleSheet(
            f"color:{self._dynamic_color('#93c5fd')}; font-family: {_FONT_TEXT_CSS};"
            " font-size:11px; font-weight:500; background:transparent; padding:1px 6px; margin-bottom:2px;"
        )
        return lbl

    def _on_lyrics_ready(self, lines):
        self._refresh_lyric(self._position)

    def set_show_lyrics(self, on: bool):
        """歌词开关：立即生效并写回 config，设置页与右键菜单共用。"""
        self._show_lyrics = bool(on)
        if self.config_manager is not None:
            cfg = self.config_manager.get_media_config()
            cfg["show_lyrics"] = self._show_lyrics
            self.config_manager.set_media_config(cfg)
        if hasattr(self, "btn_lyrics"):
            self.btn_lyrics.set_accent(self._show_lyrics)
            self.btn_lyrics.setToolTip("关闭歌词" if self._show_lyrics else "显示歌词")
        if self._show_lyrics and not getattr(self, "_is_mini", False):
            self.lyrics.fetch_async(self._track_title, self._artist_name, self._duration)
        self._refresh_lyric(self._position)

    def _refresh_lyric(self, sec):
        if not hasattr(self, "lbl_lyric"):
            return
        visible = self._show_lyrics and not getattr(self, "_is_mini", False)
        self.lbl_lyric.setVisible(visible)
        if not visible:
            return
        has_lines = self.lyrics.has_lines()
        text = self.lyrics.line_at(sec) if has_lines else "暂无匹配歌词"
        if self.lbl_lyric.text() != text:
            self.lbl_lyric.setText(text)
        # 100ms 插值会高频走到这里；QSS 重解析很贵，只在颜色变化时设一次
        color = self._dynamic_color("#93c5fd" if has_lines else "#8b919b")
        if getattr(self, "_lyric_color", None) != color:
            self._lyric_color = color
            self.lbl_lyric.setStyleSheet(
                f"color:{color}; font-family: {_FONT_TEXT_CSS}; font-size:11px; font-weight:500;"
                " background:transparent; padding:1px 6px; margin-bottom:2px;")

    def _create_progress_row(self, parent):
        """当前时间 / 进度条 / 剩余时间 单行排布，两种规格共用。"""
        box = QHBoxLayout()
        box.setContentsMargins(4, 3, 4, 0)
        box.setSpacing(6)
        self.progress_slider = HoverSlider(parent, theme=self.theme)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        self.progress_slider.setFixedHeight(12)
        self.progress_slider.sliderPressed.connect(self._on_progress_pressed)
        self.progress_slider.sliderMoved.connect(self._on_progress_moved)
        self.progress_slider.sliderReleased.connect(self._on_progress_released)

        self.lbl_pos = QLabel("00:00", parent)
        self.lbl_dur = QLabel("00:00", parent)
        self.lbl_dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for lbl in (self.lbl_pos, self.lbl_dur):
            lbl.setFixedWidth(34)
            lbl.setStyleSheet(
                f"color: #94a3b8; font-family: {_FONT_DISPLAY_CSS}; font-size: 10px; font-weight: 500; background: transparent;"
            )
            lbl.setProperty("light_text", "#64748b")
        box.addWidget(self.lbl_pos)
        box.addWidget(self.progress_slider, stretch=1)
        box.addWidget(self.lbl_dur)
        return box

    @staticmethod
    def _fmt_time(sec: float) -> str:
        s = int(max(0, sec))
        return f"{s // 60:02d}:{s % 60:02d}"

    def _can_seek(self) -> bool:
        # QQ音乐关闭了系统接口，但 player_seek 会用 UIA 走播放器自己的进度条
        return self._seek_supported or "QQ音乐" in self._source_app

    def _status_text(self, base: str) -> str:
        return base if self._can_seek() else base + " · 不可跳转"

    def _on_seek_capability(self, can_seek: bool):
        self._seek_supported = bool(can_seek)
        if hasattr(self, "progress_slider"):
            self.progress_slider.setToolTip(
                "单击或拖动跳转播放位置" if self._can_seek()
                else "可拖动预览；该播放器未提供系统跳转接口")
        self._restore_status()

    def _render_progress(self, p: float):
        if not hasattr(self, "progress_slider"):
            return
        d = max(1.0, self._duration)
        v = int(max(0.0, min(d, p)) / d * 1000)
        if self.progress_slider.value() != v:
            self.progress_slider.blockSignals(True)
            self.progress_slider.setValue(v)
            self.progress_slider.blockSignals(False)
        # 时间文字每秒才变一次，不必每 100ms 重新分配 QString
        if hasattr(self, "lbl_pos"):
            pos_text = self._fmt_time(p)
            dur_text = "-" + self._fmt_time(max(0.0, d - p))
            if self.lbl_pos.text() != pos_text:
                self.lbl_pos.setText(pos_text)
            if self.lbl_dur.text() != dur_text:
                self.lbl_dur.setText(dur_text)
        self._refresh_lyric(p)

    def _on_timeline_updated(self, position, duration):
        if self._seeking or not hasattr(self, "progress_slider"):
            return
        d = max(1.0, float(duration or 0))
        p = max(0.0, min(d, float(position or 0)))
        prev_d = self._duration
        self._position, self._duration = p, d
        self._anchor_pos, self._anchor_at = p, time.monotonic()
        self._render_progress(p)
        # 首次拿到（或换了曲目）真实时长：按版本重新匹配歌词
        if abs(d - prev_d) > 0.5 and self._show_lyrics and not getattr(self, "_is_mini", False):
            self.lyrics.fetch_async(self._track_title, self._artist_name, d)

    def _on_smooth_tick(self):
        """两次 SMTC 轮询之间按墙上时钟插值，始终以真实时间线为锚点。"""
        if not self._is_playing or self._seeking or self._duration <= 0:
            return
        p = min(self._duration, self._anchor_pos + (time.monotonic() - self._anchor_at))
        if p > self._position:
            self._position = p
            self._render_progress(p)

    def _on_progress_pressed(self):
        self._seeking = True

    def _on_progress_moved(self, value):
        """拖动时实时显示目标时间，让点击/拖动立刻有反馈。"""
        if self._duration <= 0 or not hasattr(self, "lbl_pos"):
            return
        target = value / 1000.0 * self._duration
        self.lbl_pos.setText(self._fmt_time(target))
        self.lbl_dur.setText("-" + self._fmt_time(max(0.0, self._duration - target)))

    def _on_progress_released(self):
        self._seeking = False
        if self._duration <= 0:
            return
        target = self.progress_slider.value() / 1000.0 * self._duration
        self._seek_target = target
        # 总是尝试：支持系统跳转的播放器（Edge/Spotify/PotPlayer 等）会真跳；
        # QQ音乐返回 True 但不生效，由 _verify_seek 给出明确反馈并回滚。
        self.service.seek(target)
        self._on_progress_moved(self.progress_slider.value())
        # 等真实时间线回流一次（轮询 500ms + UIA 约 0.3s）
        QTimer.singleShot(1600, self._verify_seek)

    def _verify_seek(self):
        """1.6s 后比对真实位置；没真跳就说明原因并回滚，不假装成功。"""
        target = getattr(self, "_seek_target", None)
        if target is None or self._seeking:
            return
        self._seek_target = None
        if abs(self._position - target) > 4:
            self._render_progress(self._position)
            if hasattr(self, "lbl_status"):
                hint = ("跳转未生效·请打开一次主界面" if "QQ音乐" in self._source_app
                        else f"{self._source_app} 不开放进度跳转")
                self.lbl_status.setText(hint)
                self.lbl_status.setStyleSheet(
                    f"color:{self._dynamic_color('#f59e0b')}; font-family: {_FONT_TEXT_CSS}; font-size:10px; font-weight:600;")
                QTimer.singleShot(2600, self._restore_status)

    def _restore_status(self):
        if hasattr(self, "lbl_status"):
            self.lbl_status.setText(self._status_text("● 正在播放" if self._is_playing else "○ 待机"))
            color = self._dynamic_color("#10b981") if self._is_playing else self._dynamic_color("#64748b")
            self.lbl_status.setStyleSheet(
                f"color: {color}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
            )

    def _build_2x2_layout(self):
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(16, 10, 16, 10)
        self.content_layout.setSpacing(3)

        # 1. 顶部状态栏 (来源应用与播放指示灯)
        top_bar = QHBoxLayout()
        self.lbl_source = QLabel(self._source_app, self.content_widget)
        self.lbl_source.setStyleSheet(
            f"color: #60a5fa; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 600; letter-spacing: 0.4px;"
        )
        self.lbl_source.setProperty("light_text", "#2563eb")

        self.lbl_status = QLabel("● 正在播放" if self._is_playing else "○ 待机", self.content_widget)
        st_color = self._dynamic_color("#10b981") if self._is_playing else self._dynamic_color("#64748b")
        self.lbl_status.setStyleSheet(
            f"color: {st_color}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
        )

        top_bar.addWidget(self.lbl_source)
        top_bar.addStretch()
        top_bar.addWidget(self.lbl_status)
        self.content_layout.addLayout(top_bar)

        # 2. 封面独占区：paintEvent 按这个 spacer 的实际几何绘制，文字绝不压在封面上
        self.cover_spacer = QWidget(self.content_widget)
        self.cover_spacer.setFixedHeight(94)
        self.cover_spacer.setStyleSheet("background: transparent;")
        self.content_layout.addWidget(self.cover_spacer)

        # 3. 曲目名称与艺术家
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        self.lbl_title = QLabel(self._track_title, self.content_widget)
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setStyleSheet(f"""
            QLabel {{
                color: #f8fafc;
                font-size: 14px;
                font-weight: 600;
                font-family: {_FONT_TEXT_CSS};
                background: transparent;
                padding: 0 4px;
            }}
        """)
        self.lbl_title.setProperty("light_text", "#0f172a")

        self.lbl_artist = QLabel(self._artist_name, self.content_widget)
        self.lbl_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_artist.setStyleSheet(
            f"color: #94a3b8; font-family: {_FONT_TEXT_CSS}; font-size: 11px; background: transparent; padding: 0 4px;"
        )
        self.lbl_artist.setProperty("light_text", "#475569")

        info_col.addWidget(self.lbl_title)
        info_col.addWidget(self.lbl_artist)
        self.lbl_lyric = self._create_lyric_label(self.content_widget)
        info_col.addWidget(self.lbl_lyric)
        self.content_layout.addLayout(info_col)

        # 可拖动播放进度
        self.content_layout.addLayout(self._create_progress_row(self.content_widget))

        # 4. 控制按钮行 (上一曲, 播放/暂停, 下一曲)
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(14)
        ctrl_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_prev = RoundMediaButton(False, self.content_widget)
        self.btn_prev.setFixedSize(30, 30)
        self.btn_prev.clicked.connect(self._on_prev_clicked)

        self.btn_play_pause = RoundMediaButton(True, self.content_widget)
        self.btn_play_pause.setFixedSize(34, 34)
        self.btn_play_pause.clicked.connect(self._on_play_clicked)

        self.btn_next = RoundMediaButton(False, self.content_widget)
        self.btn_next.setFixedSize(30, 30)
        self.btn_next.clicked.connect(self._on_next_clicked)

        ctrl_bar.addWidget(self.btn_prev)
        ctrl_bar.addWidget(self.btn_play_pause)
        ctrl_bar.addWidget(self.btn_next)
        self.btn_lyrics = self._create_lyrics_button(self.content_widget, 22)
        ctrl_bar.addWidget(self.btn_lyrics)
        self._apply_control_icons()
        self.content_layout.addLayout(ctrl_bar)

    def _build_2x1_layout(self):
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(14, 12, 14, 12)
        self.content_layout.setSpacing(12)

        # 左侧留空 (留给超椭圆封面或黑胶音符插画绘制)
        self.cover_spacer = QWidget(self.content_widget)
        self.cover_spacer.setFixedSize(84, 84)
        self.cover_spacer.setStyleSheet("background: transparent;")
        self.content_layout.addWidget(self.cover_spacer)

        # 右侧信息与控制
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 2, 0, 2)
        right_col.setSpacing(3)

        top_row = QHBoxLayout()
        self.lbl_source = QLabel(self._source_app, self.content_widget)
        self.lbl_source.setStyleSheet(
            f"color: #60a5fa; font-family: {_FONT_TEXT_CSS}; font-size: 10px; font-weight: 600;"
        )
        self.lbl_source.setProperty("light_text", "#2563eb")
        self.lbl_status = QLabel("● 正在播放" if self._is_playing else "○ 待机", self.content_widget)
        st_color = self._dynamic_color("#10b981") if self._is_playing else self._dynamic_color("#64748b")
        self.lbl_status.setStyleSheet(
            f"color: {st_color}; font-family: {_FONT_TEXT_CSS}; font-size: 10px; font-weight: 500;"
        )
        top_row.addWidget(self.lbl_source)
        top_row.addStretch()
        top_row.addWidget(self.lbl_status)
        right_col.addLayout(top_row)

        self.lbl_title = QLabel(self._track_title, self.content_widget)
        self.lbl_title.setStyleSheet(
            f"color: #f8fafc; font-family: {_FONT_TEXT_CSS}; font-size: 13px; font-weight: 600;"
            " background: transparent; padding: 0 4px;"
        )
        self.lbl_title.setProperty("light_text", "#0f172a")
        self.lbl_artist = QLabel(self._artist_name, self.content_widget)
        self.lbl_artist.setStyleSheet(
            f"color: #94a3b8; font-family: {_FONT_TEXT_CSS}; font-size: 10px;"
            " background: transparent; padding: 0 4px;"
        )
        self.lbl_artist.setProperty("light_text", "#475569")
        right_col.addWidget(self.lbl_title)
        right_col.addWidget(self.lbl_artist)
        self.lbl_lyric = self._create_lyric_label(self.content_widget)
        right_col.addWidget(self.lbl_lyric)
        right_col.addLayout(self._create_progress_row(self.content_widget))

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self.btn_prev = RoundMediaButton(False, self.content_widget)
        self.btn_prev.setFixedSize(24, 24)
        self.btn_prev.clicked.connect(self._on_prev_clicked)

        self.btn_play_pause = RoundMediaButton(True, self.content_widget)
        self.btn_play_pause.setFixedSize(26, 26)
        self.btn_play_pause.clicked.connect(self._on_play_clicked)

        self.btn_next = RoundMediaButton(False, self.content_widget)
        self.btn_next.setFixedSize(24, 24)
        self.btn_next.clicked.connect(self._on_next_clicked)

        ctrl_row.addWidget(self.btn_prev)
        ctrl_row.addWidget(self.btn_play_pause)
        ctrl_row.addWidget(self.btn_next)
        self.btn_lyrics = self._create_lyrics_button(self.content_widget, 20)
        ctrl_row.addWidget(self.btn_lyrics)
        self._apply_control_icons()
        ctrl_row.addStretch()

        right_col.addLayout(ctrl_row)
        self.content_layout.addLayout(right_col, stretch=1)

    def set_size_mode(self, mode: str):
        if mode not in ("2x2", "2x1"):
            mode = "2x2"
        self.mode = mode
        self.card_w = CARD_W_2X2
        self.card_h = CARD_H_2X1 if mode == "2x1" else CARD_H_2X2
        self._update_window_size()
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self._build_layout()
        self._rebuild_cached_cover()
        self.update()
        self.size_changed.emit(mode)

    def _paint_mini_background(self, painter, card_rect):
        """小卡：封面铺满做背景 + 深色 scrim，文字不再和封面抢空间。"""
        path = create_squircle_path(QRectF(card_rect), max(8.0, self.radius * 0.7))
        painter.save()
        painter.setClipPath(path)
        if self._cached_cover_pixmap and not self._cached_cover_pixmap.isNull():
            painter.drawPixmap(card_rect, self._cached_cover_pixmap)
        else:
            # 无封面时给一层柔和渐变，避免小卡变成纯黑板
            ambient = QLinearGradient(0, card_rect.top(), card_rect.right(), card_rect.bottom())
            ambient.setColorAt(0.0, QColor(46, 50, 72))
            ambient.setColorAt(1.0, QColor(12, 14, 22))
            painter.fillRect(card_rect, ambient)
        scrim = QLinearGradient(0, card_rect.top(), 0, card_rect.bottom())
        scrim.setColorAt(0.0, QColor(8, 10, 16, 145))
        scrim.setColorAt(1.0, QColor(8, 10, 16, 230))
        painter.fillRect(card_rect, scrim)
        painter.restore()

    def paintEvent(self, event):
        if self.card is not None and self.card.isHidden():
            return
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        card_rect = self.get_card_rect()
        if getattr(self, "_is_mini", False):
            self._paint_mini_background(painter, card_rect)
            painter.end()
            return

        if self.mode == "2x2":
            # 封面严格画在 cover_spacer 里，文字从 spacer 下方开始，绝不重叠
            geo = self.cover_spacer.geometry()
            size = max(32, min(geo.height() - 2, self._cover_size()))
            left = card_rect.left() + geo.left() + (geo.width() - size) // 2
            top = card_rect.top() + geo.top() + (geo.height() - size) // 2
            cover_rect = QRect(left, top, size, size)
            radius = 16
        else:
            cover_size = self._cover_size()
            cover_x = card_rect.left() + 14
            cover_y = card_rect.top() + (card_rect.height() - cover_size) // 2
            cover_rect = QRect(cover_x, cover_y, cover_size, cover_size)
            radius = 14

        is_light = self.theme == "light"
        sleeve_path = create_squircle_path(QRectF(cover_rect), radius)
        shadow_path = create_squircle_path(QRectF(cover_rect).adjusted(0.0, 2.0, 0.0, 3.0), radius)
        painter.fillPath(shadow_path, QColor(15, 23, 42, 24) if is_light else QColor(0, 0, 0, 52))

        if self._cached_cover_pixmap and not self._cached_cover_pixmap.isNull():
            painter.save()
            painter.setClipPath(sleeve_path)
            painter.drawPixmap(cover_rect, self._cached_cover_pixmap)
            painter.restore()

            rim_col = QColor(15, 23, 42, 28) if is_light else QColor(255, 255, 255, 48)
            painter.strokePath(sleeve_path, QPen(rim_col, 1.0))
        else:
            # 无封面时：超椭圆磨砂唱片封套 + 精细阳极黑胶盘 + 矢量双八分音符插画
            sleeve_grad = QLinearGradient(cover_rect.left(), cover_rect.top(),
                                          cover_rect.right(), cover_rect.bottom())
            if is_light:
                sleeve_grad.setColorAt(0.0, QColor("#e2e8f0"))
                sleeve_grad.setColorAt(1.0, QColor("#cbd5e1"))
                sleeve_rim = QColor(15, 23, 42, 24)
            else:
                sleeve_grad.setColorAt(0.0, QColor("#1e2433"))
                sleeve_grad.setColorAt(1.0, QColor("#111520"))
                sleeve_rim = QColor(255, 255, 255, 34)
            painter.fillPath(sleeve_path, sleeve_grad)
            painter.strokePath(sleeve_path, QPen(sleeve_rim, 1.0))

            cx_f = float(cover_rect.center().x())
            cy_f = float(cover_rect.center().y())
            rad = float(cover_rect.width()) * 0.39

            disc_grad = QRadialGradient(QPointF(cx_f - rad * 0.25, cy_f - rad * 0.25), rad * 1.45)
            disc_grad.setColorAt(0.0, QColor(42, 47, 61))
            disc_grad.setColorAt(0.55, QColor(20, 23, 31))
            disc_grad.setColorAt(1.0, QColor(11, 13, 18))
            painter.setBrush(QBrush(disc_grad))
            painter.setPen(QPen(QColor(255, 255, 255, 30), 0.9))
            painter.drawEllipse(QPointF(cx_f, cy_f), rad, rad)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            for ratio in (0.83, 0.70, 0.57):
                painter.setPen(QPen(QColor(255, 255, 255, 16), 0.75))
                painter.drawEllipse(QPointF(cx_f, cy_f), rad * ratio, rad * ratio)

            lbl_rad = rad * 0.42
            lbl_grad = QLinearGradient(cx_f - lbl_rad, cy_f - lbl_rad, cx_f + lbl_rad, cy_f + lbl_rad)
            lbl_grad.setColorAt(0.0, QColor("#60a5fa"))
            lbl_grad.setColorAt(1.0, QColor("#2563eb"))
            painter.setBrush(QBrush(lbl_grad))
            painter.setPen(QPen(QColor(255, 255, 255, 70), 0.75))
            painter.drawEllipse(QPointF(cx_f, cy_f), lbl_rad, lbl_rad)

            # 中心矢量连音八分音符 (♫)
            ns = lbl_rad * 0.92
            note_col = QColor(255, 255, 255, 245)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(note_col)
            painter.drawEllipse(QPointF(cx_f - ns * 0.34, cy_f + ns * 0.26), ns * 0.21, ns * 0.16)
            painter.drawEllipse(QPointF(cx_f + ns * 0.22, cy_f + ns * 0.16), ns * 0.21, ns * 0.16)
            stem_pen = QPen(note_col, max(1.2, ns * 0.14), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(stem_pen)
            x1, y1 = cx_f - ns * 0.16, cy_f + ns * 0.22
            x2, y2 = cx_f + ns * 0.40, cy_f + ns * 0.12
            top1 = cy_f - ns * 0.34
            top2 = cy_f - ns * 0.46
            painter.drawLine(QPointF(x1, y1), QPointF(x1, top1))
            painter.drawLine(QPointF(x2, y2), QPointF(x2, top2))
            beam_pen = QPen(note_col, max(1.5, ns * 0.20), Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(beam_pen)
            painter.drawLine(QPointF(x1, top1), QPointF(x2, top2))

    def _on_media_info_changed(self, info: dict):
        self._track_title = info.get("title") or "暂无播放媒体"
        self._artist_name = info.get("artist") or "打开播放器自动捕获"
        self._source_app = info.get("source_app") or "系统媒体"
        self._is_playing = bool(info.get("is_playing", False))
        self._duration = 0.0   # 换歌先清空旧时长，避免拿上一首的时长去匹配歌词

        thumb_bytes = info.get("thumbnail_bytes")
        if thumb_bytes:
            pm = QPixmap()
            if pm.loadFromData(thumb_bytes):
                self._cover_pixmap = pm
            else:
                self._cover_pixmap = None
        else:
            self._cover_pixmap = None
        self._rebuild_cached_cover()

        if hasattr(self, "lbl_title"):
            self.lbl_title.setToolTip(self._track_title)
            fm = self.lbl_title.fontMetrics()
            max_w = 216 if self.mode == "2x2" else 130
            elided = fm.elidedText(self._track_title, Qt.TextElideMode.ElideRight, max_w)
            self.lbl_title.setText(elided)
        if hasattr(self, "lbl_artist"):
            self.lbl_artist.setToolTip(self._artist_name)
            fm = self.lbl_artist.fontMetrics()
            max_w = 216 if self.mode == "2x2" else 130
            elided = fm.elidedText(self._artist_name, Qt.TextElideMode.ElideRight, max_w)
            self.lbl_artist.setText(elided)
        if hasattr(self, "lbl_source"):
            self.lbl_source.setText(self._source_app)
        if hasattr(self, "lbl_status"):
            st_text = self._status_text("● 正在播放" if self._is_playing else ("○ 已暂停" if info.get("has_media") else "○ 待机"))
            st_dark = "#10b981" if self._is_playing else ("#f59e0b" if info.get("has_media") else "#64748b")
            st_color = self._dynamic_color(st_dark)
            self.lbl_status.setText(st_text)
            self.lbl_status.setStyleSheet(
                f"color: {st_color}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
            )
        if hasattr(self, "btn_play_pause"):
            self.btn_play_pause.setIcon(_media_button_icon("pause" if self._is_playing else "play"))

        if self._show_lyrics and not getattr(self, "_is_mini", False):
            self.lyrics.fetch_async(self._track_title, self._artist_name, self._duration)
        self.update()

    def _on_playback_status_changed(self, is_playing: bool):
        self._is_playing = is_playing
        if hasattr(self, "btn_play_pause"):
            self.btn_play_pause.setIcon(_media_button_icon("pause" if self._is_playing else "play"))
        if hasattr(self, "lbl_status"):
            st_text = self._status_text("● 正在播放" if self._is_playing else "○ 已暂停")
            st_color = self._dynamic_color("#10b981") if self._is_playing else self._dynamic_color("#f59e0b")
            self.lbl_status.setText(st_text)
            self.lbl_status.setStyleSheet(
                f"color: {st_color}; font-family: {_FONT_TEXT_CSS}; font-size: 11px; font-weight: 500;"
            )

    def _on_play_clicked(self):
        self.service.play_pause()

    def _on_prev_clicked(self):
        self.service.previous_track()

    def _on_next_clicked(self):
        self.service.next_track()

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))
        m_toggle_size = menu.addAction("切换为 2×1 规格" if self.mode == "2x2" else "切换为 2×2 规格")
        menu.addSeparator()
        m_prev = menu.addAction("上一曲")
        m_play = menu.addAction("暂停" if self._is_playing else "播放")
        m_next = menu.addAction("下一曲")
        m_refresh = menu.addAction("刷新媒体状态")
        m_lyrics = menu.addAction("显示歌词")
        m_lyrics.setCheckable(True)
        m_lyrics.setChecked(self._show_lyrics)

        def handle_extra(a):
            if a is m_toggle_size:
                new_mode = "2x1" if self.mode == "2x2" else "2x2"
                self.set_size_mode(new_mode)
                if self.config_manager:
                    cfg = self.config_manager.get_media_config()
                    cfg["size"] = new_mode
                    self.config_manager.set_media_config(cfg)
            elif a is m_prev:
                self._on_prev_clicked()
            elif a is m_play:
                self._on_play_clicked()
            elif a is m_next:
                self._on_next_clicked()
            elif a is m_refresh:
                self.service.poll_current_session()
            elif a is m_lyrics:
                self.set_show_lyrics(not self._show_lyrics)

        finish_widget_menu(self, menu, event.globalPos(), "media", handle_extra)


# 导出别名
MediaWidgetWindow = NowPlayingWidgetWindow

