"""Stacky 设置窗口：分类、交互、外观与桌面组件配置。"""
import stacky.paths as _stacky_paths
from stacky.paths import ROOT

import os
import json
import time
import shutil
import threading
from typing import Optional, Dict, Any, List

from PySide6.QtCore import Qt, QEvent, QTimer, Signal, QRect, QRectF, QSize, QPointF
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QComboBox,
    QLineEdit,
    QSlider,
    QFrame,
    QScrollArea,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QApplication,
    QStyledItemDelegate,
    QStyle,
    QSizePolicy,
)

from config_manager import ConfigManager, DEFAULT_CONFIG
from weather_service import get_weather_service, POPULAR_CITIES, resolve_city_coordinates
from autostart_utils import is_autostart_enabled, set_autostart
from icon_utils import get_ui_chevron_icon_path, get_default_icon_cache_dir
from ui_kit import (
    CenteredDialog,
    FlatCheckBox,
    FlatRadioButton,
    ToggleSwitch,
    UI_FONT_CSS,
    ui_font,
    apply_win11_window_chrome,
    get_theme_tokens,
    menu_qss,
)

CACHE_DIR = get_default_icon_cache_dir()


def _draw_nav_glyph(p: QPainter, box: QRectF, kind: str, color: QColor):
    """在 18x18 逻辑框内绘制 1.6px 精细矢量侧栏图标。"""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(
        color,
        1.6,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    x0 = box.left()
    y0 = box.top()

    if kind == "folder":
        folder_path = QPainterPath()
        folder_path.moveTo(x0 + 2.0, y0 + 5.2)
        folder_path.quadTo(x0 + 2.0, y0 + 3.6, x0 + 3.6, y0 + 3.6)
        folder_path.lineTo(x0 + 7.2, y0 + 3.6)
        folder_path.lineTo(x0 + 8.8, y0 + 5.5)
        folder_path.lineTo(x0 + 14.4, y0 + 5.5)
        folder_path.quadTo(x0 + 16.0, y0 + 5.5, x0 + 16.0, y0 + 7.1)
        folder_path.lineTo(x0 + 16.0, y0 + 13.4)
        folder_path.quadTo(x0 + 16.0, y0 + 15.0, x0 + 14.4, y0 + 15.0)
        folder_path.lineTo(x0 + 3.6, y0 + 15.0)
        folder_path.quadTo(x0 + 2.0, y0 + 15.0, x0 + 2.0, y0 + 13.4)
        folder_path.closeSubpath()
        p.drawPath(folder_path)
    elif kind == "interaction":
        arrow = QPainterPath()
        arrow.moveTo(x0 + 4.2, y0 + 2.4)
        arrow.lineTo(x0 + 4.2, y0 + 14.2)
        arrow.lineTo(x0 + 7.9, y0 + 10.9)
        arrow.lineTo(x0 + 10.4, y0 + 15.6)
        arrow.lineTo(x0 + 12.5, y0 + 14.6)
        arrow.lineTo(x0 + 10.0, y0 + 9.9)
        arrow.lineTo(x0 + 14.6, y0 + 9.9)
        arrow.closeSubpath()
        p.drawPath(arrow)
    elif kind == "appearance":
        p.drawRoundedRect(QRectF(x0 + 2.2, y0 + 2.4, 8.6, 8.6), 2.2, 2.2)
        p.drawRoundedRect(QRectF(x0 + 7.2, y0 + 7.0, 8.6, 8.6), 2.2, 2.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(x0 + 5.1, y0 + 5.3, 2.2, 2.2))
    elif kind == "widgets":
        for gx, gy in ((2.3, 2.3), (10.1, 2.3), (2.3, 10.1), (10.1, 10.1)):
            p.drawRoundedRect(QRectF(x0 + gx, y0 + gy, 5.6, 5.6), 1.6, 1.6)
    elif kind == "backup":
        p.drawRoundedRect(QRectF(x0 + 2.4, y0 + 4.4, 13.2, 10.8), 2.2, 2.2)
        p.drawLine(QPointF(x0 + 2.6, y0 + 8.0), QPointF(x0 + 15.4, y0 + 8.0))
        p.drawLine(QPointF(x0 + 7.0, y0 + 11.2), QPointF(x0 + 11.0, y0 + 11.2))
    p.restore()


def _nav_icon(kind: str, light: bool = False, active: bool = False) -> QIcon:
    """生成高分屏抗锯齿侧栏图标，供 QListWidgetItem 使用。"""
    dpr = 2.0
    pm = QPixmap(int(18 * dpr), int(18 * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    if active:
        stroke_c = QColor("#2563eb" if light else "#60a5fa")
    else:
        stroke_c = QColor("#64748b" if light else "#94a3b8")
    _draw_nav_glyph(p, QRectF(0.0, 0.0, 18.0, 18.0), kind, stroke_c)
    p.end()
    return QIcon(pm)


def _search_magnifier_icon(light: bool = False) -> QIcon:
    """绘制 14x14 搜索放大镜矢量图标。"""
    dpr = 2.0
    pm = QPixmap(int(14 * dpr), int(14 * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    c = QColor("#64748b" if light else "#8b95a5")
    pen = QPen(c, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(1.8, 1.8, 7.8, 7.8))
    p.drawLine(QPointF(8.4, 8.4), QPointF(12.2, 12.2))
    p.end()
    return QIcon(pm)


def _sidebar_brand_pixmap(size: int = 28) -> QPixmap:
    """加载或自绘 28x28 高清超椭圆品牌图标。"""
    dpr = 2.0
    px = int(size * dpr)
    for fname in ("app_icon.png", "app_icon.ico"):
        candidate = os.path.join(CACHE_DIR, fname)
        if os.path.isfile(candidate):
            src = QPixmap(candidate)
            if not src.isNull():
                scaled = src.scaled(
                    px,
                    px,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(dpr)
                return scaled

    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(1.0, 1.0, float(size - 2), float(size - 2))
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, QColor("#3b82f6"))
    grad.setColorAt(1.0, QColor("#1d4ed8"))
    p.setPen(QPen(QColor(255, 255, 255, 55), 0.8))
    p.setBrush(grad)
    p.drawRoundedRect(rect, 7.5, 7.5)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 135))
    p.drawRoundedRect(QRectF(7.5, 6.2, 13.0, 7.5), 2.4, 2.4)
    p.setBrush(QColor(255, 255, 255, 245))
    p.drawRoundedRect(QRectF(6.2, 11.0, 15.6, 10.2), 3.0, 3.0)
    p.end()
    return pm


class NavItemDelegate(QStyledItemDelegate):
    """Linear / Win11 Fluent 式单行 40px 圆角胶囊侧栏项。"""

    def paint(self, painter, option, index):
        r = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_light = False
        if option.widget is not None:
            is_light = str(option.widget.property("theme") or "").lower() == "light"

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        pill = r.adjusted(2.0, 2.0, -2.0, -2.0)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(59, 130, 246, 34 if is_light else 38))
            painter.drawRoundedRect(pill, 8.0, 8.0)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 23, 42, 12) if is_light else QColor(255, 255, 255, 13))
            painter.drawRoundedRect(pill, 8.0, 8.0)

        if selected:
            fg_color = QColor("#2563eb" if is_light else "#60a5fa")
            icon_color = fg_color
        elif is_light:
            fg_color = QColor("#0f172a" if hovered else "#334155")
            icon_color = QColor("#334155" if hovered else "#64748b")
        else:
            fg_color = QColor("#f1f5f9" if hovered else "#cbd5e1")
            icon_color = QColor("#cbd5e1" if hovered else "#94a3b8")

        kind = index.data(Qt.ItemDataRole.UserRole + 1)
        icon_rect = QRectF(pill.left() + 11.0, pill.center().y() - 9.0, 18.0, 18.0)
        if isinstance(kind, str) and kind:
            _draw_nav_glyph(painter, icon_rect, kind, icon_color)
        else:
            icon = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(icon, QIcon):
                icon.paint(painter, QRect(int(icon_rect.left()), int(icon_rect.top()), 18, 18))

        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        weight = QFont.Weight.DemiBold if selected else QFont.Weight.Medium
        painter.setFont(ui_font(13, weight))
        painter.setPen(fg_color)
        text_rect = QRectF(pill.left() + 38.0, pill.top(), pill.width() - 46.0, pill.height())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(160, 40)


class ThemePreviewCard(QWidget):
    """外观页顶部可视化主题预览卡片（深色磨砂 / 浅色云母 / 纯黑 OLED）。"""

    clicked = Signal(str)

    _APP_DOT_COLORS = (
        "#3b82f6",
        "#10b981",
        "#f59e0b",
        "#8b5cf6",
        "#ec4899",
        "#38bdf8",
    )

    def __init__(self, theme_key: str, label: str, parent=None):
        super().__init__(parent)
        self.theme_key = theme_key
        self.label = label
        self.is_active = False
        self.host_theme = "dark"
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(112)
        self.setMaximumWidth(184)
        self.setFixedHeight(78)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, active: bool, host_theme: str):
        self.is_active = bool(active)
        self.host_theme = (host_theme or "dark").strip().lower()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.theme_key)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        is_host_light = self.host_theme == "light"
        hovered = self.underMouse()

        thumb = QRectF(1.5, 1.5, w - 3.0, 52.0)
        thumb_radius = 9.0

        # 1. 微缩桌面壁纸底板裁剪绘制
        clip = QPainterPath()
        clip.addRoundedRect(thumb, thumb_radius, thumb_radius)
        p.save()
        p.setClipPath(clip)

        if self.theme_key == "light":
            wp = QLinearGradient(thumb.topLeft(), thumb.bottomRight())
            wp.setColorAt(0.0, QColor("#dbeafe"))
            wp.setColorAt(0.55, QColor("#eef2ff"))
            wp.setColorAt(1.0, QColor("#f8fafc"))
            p.fillRect(thumb, wp)
            glow = QRadialGradient(thumb.right() - 16.0, thumb.top() + 10.0, 42.0)
            glow.setColorAt(0.0, QColor(96, 165, 250, 75))
            glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(thumb, glow)
            glass_fill = QColor(255, 255, 255, 224)
            glass_rim = QColor(15, 23, 42, 26)
            pill_bar = QColor(15, 23, 42, 48)
        elif self.theme_key == "oled":
            p.fillRect(thumb, QColor("#040507"))
            glow = QRadialGradient(thumb.left() + 22.0, thumb.bottom() - 8.0, 44.0)
            glow.setColorAt(0.0, QColor(59, 130, 246, 36))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(thumb, glow)
            glass_fill = QColor(14, 16, 22, 242)
            glass_rim = QColor(255, 255, 255, 48)
            pill_bar = QColor(255, 255, 255, 90)
        else:
            wp = QLinearGradient(thumb.topLeft(), thumb.bottomRight())
            wp.setColorAt(0.0, QColor("#1e293b"))
            wp.setColorAt(1.0, QColor("#0f172a"))
            p.fillRect(thumb, wp)
            glow = QRadialGradient(thumb.right() - 18.0, thumb.top() + 12.0, 46.0)
            glow.setColorAt(0.0, QColor(59, 130, 246, 82))
            glow.setColorAt(1.0, QColor(15, 23, 42, 0))
            p.fillRect(thumb, glow)
            glass_fill = QColor(30, 35, 46, 214)
            glass_rim = QColor(255, 255, 255, 56)
            pill_bar = QColor(255, 255, 255, 110)

        # 微缩毛玻璃文件夹卡片
        cw, ch = 54.0, 34.0
        cx = thumb.center().x() - cw / 2.0
        cy = thumb.top() + 7.0
        mini_card = QRectF(cx, cy, cw, ch)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 35 if self.theme_key == "light" else 75))
        p.drawRoundedRect(mini_card.translated(0.0, 1.6), 7.0, 7.0)

        p.setPen(QPen(glass_rim, 0.9))
        p.setBrush(glass_fill)
        p.drawRoundedRect(mini_card, 7.0, 7.0)

        # 3x2 彩色微缩应用图标阵列
        dot_sz = 8.4
        gap_x, gap_y = 5.2, 4.0
        grid_w = dot_sz * 3 + gap_x * 2
        grid_h = dot_sz * 2 + gap_y
        gx0 = mini_card.center().x() - grid_w / 2.0
        gy0 = mini_card.center().y() - grid_h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        for idx, hex_c in enumerate(self._APP_DOT_COLORS):
            r_i, c_i = divmod(idx, 3)
            dx = gx0 + c_i * (dot_sz + gap_x)
            dy = gy0 + r_i * (dot_sz + gap_y)
            p.setBrush(QColor(hex_c))
            p.drawRoundedRect(QRectF(dx, dy, dot_sz, dot_sz), 2.4, 2.4)

        # 底部微缩标题条
        p.setBrush(pill_bar)
        p.drawRoundedRect(QRectF(thumb.center().x() - 11.0, mini_card.bottom() + 3.6, 22.0, 2.4), 1.2, 1.2)
        p.restore()

        # 2. 外框选中环与右下角对勾角标
        if self.is_active:
            p.setPen(QPen(QColor("#3b82f6"), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(thumb, thumb_radius, thumb_radius)

            badge = QRectF(thumb.right() - 19.5, thumb.bottom() - 19.5, 15.0, 15.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#3b82f6"))
            p.drawEllipse(badge)

            ck_pen = QPen(QColor("#ffffff"), 1.7)
            ck_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            ck_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(ck_pen)
            ck = QPainterPath()
            ck.moveTo(badge.left() + 3.8, badge.center().y() + 0.2)
            ck.lineTo(badge.left() + 6.4, badge.bottom() - 4.0)
            ck.lineTo(badge.right() - 3.4, badge.top() + 4.4)
            p.drawPath(ck)
        else:
            if is_host_light:
                b_col = QColor(15, 23, 42, 58 if hovered else 30)
            else:
                b_col = QColor(255, 255, 255, 62 if hovered else 28)
            p.setPen(QPen(b_col, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(thumb, thumb_radius, thumb_radius)

        # 3. 底部主题文案
        if self.is_active:
            lbl_c = QColor("#2563eb" if is_host_light else "#60a5fa")
            lbl_w = QFont.Weight.DemiBold
        elif is_host_light:
            lbl_c = QColor("#0f172a" if hovered else "#475569")
            lbl_w = QFont.Weight.Medium
        else:
            lbl_c = QColor("#f1f5f9" if hovered else "#94a3b8")
            lbl_w = QFont.Weight.Medium

        p.setFont(ui_font(12, lbl_w))
        p.setPen(lbl_c)
        p.drawText(
            QRectF(0.0, 56.0, w, 21.0),
            Qt.AlignmentFlag.AlignCenter,
            self.label,
        )
        p.end()


class SmoothCategoryListWidget(QListWidget):
    """分类列表：滚轮事件不冒泡到外层滚动区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class SettingsCard(QFrame):
    """现代圆角悬浮设置分组卡片：标题、设置行与微细分隔线。"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(18, 12, 18, 12)
        self.box.setSpacing(5)
        if title:
            h_row = QHBoxLayout()
            h_row.setContentsMargins(0, 0, 0, 2)
            lbl = QLabel(title, self)
            lbl.setObjectName("cardHeaderTitle")
            h_row.addWidget(lbl)
            h_row.addStretch()
            self.header_action_box = h_row
            self.box.addLayout(h_row)

    def add_row(
        self,
        left_title: str,
        left_desc: str = "",
        right_widget: Optional[QWidget] = None,
    ) -> QWidget:
        """添加标准设置行：左侧标题与副标题描述，右侧对齐操作控件。"""
        row = QWidget(self)
        r_layout = QHBoxLayout(row)
        r_layout.setContentsMargins(0, 4, 0, 4)
        r_layout.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        lbl_t = QLabel(left_title, row)
        lbl_t.setObjectName("rowTitle")
        text_col.addWidget(lbl_t)
        if left_desc:
            lbl_d = QLabel(left_desc, row)
            lbl_d.setObjectName("rowDesc")
            lbl_d.setWordWrap(True)
            text_col.addWidget(lbl_d)

        r_layout.addLayout(text_col, stretch=1)
        if right_widget:
            if isinstance(right_widget, QComboBox) and right_widget.minimumWidth() < 120:
                right_widget.setMinimumWidth(164)
            r_layout.addWidget(
                right_widget,
                0,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self.box.addWidget(row)
        return row

    def add_divider(self):
        """卡片内行间微细分割线。"""
        div = QFrame(self)
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setObjectName("cardDivider")
        self.box.addWidget(div)


class SettingsDialog(CenteredDialog):
    """Stacky 设置控制中心"""

    districts_loaded = Signal(list)
    places_found = Signal(list)

    def __init__(self, config_manager: ConfigManager, window_manager=None, initial_tab: int = 0, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        if window_manager is None:
            for top in QApplication.topLevelWidgets():
                mgr = getattr(top, "manager", None)
                if mgr is not None and hasattr(mgr, "windows"):
                    window_manager = mgr
                    break
        self.window_manager = window_manager

        self.setWindowTitle("Stacky 灵叠设置")
        self.setMinimumSize(640, 480)
        _screen = QApplication.primaryScreen()
        if _screen is not None:
            _avail = _screen.availableGeometry()
            self.resize(
                min(880, max(640, _avail.width() - 16)),
                min(688, max(480, _avail.height() - 32)),
            )
        else:
            self.resize(880, 688)

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setFont(ui_font(13))

        app_ico = os.path.join(CACHE_DIR, "app_icon.ico")
        if not os.path.exists(app_ico):
            app_ico = os.path.join(CACHE_DIR, "app_icon.png")
        if os.path.exists(app_ico):
            self.setWindowIcon(QIcon(app_ico))
        elif not QApplication.windowIcon().isNull():
            self.setWindowIcon(QApplication.windowIcon())

        self.theme: str = str(self.config_manager.get("theme", "dark") or "dark").lower()
        self._initial_theme: str = self.theme
        self._initial_opacity: float = float(self.config_manager.get("opacity", 0.88))
        self._initial_file_shelf_enabled: bool = bool(
            self.config_manager.get_file_shelf_config().get("enabled", True)
        )
        self._saved_once: bool = False
        self._accepted_and_saved: bool = False
        self._rolled_back: bool = False
        self._reloading_ui: bool = False
        self._ui_initialized: bool = False
        self._syncing_theme_ui: bool = False
        self._theme_swatch_cards: Dict[str, ThemePreviewCard] = {}
        self._search_icon_action: Optional[QAction] = None

        self.setProperty("theme", self.theme)
        self._nav_kinds: List[str] = []

        self._init_styles(self.theme)
        self._init_ui(initial_tab)
        self.apply_theme(self.theme)
        self._ui_initialized = True

    def showEvent(self, event):
        self._rolled_back = False
        super().showEvent(event)
        apply_win11_window_chrome(self, self.theme)

    def _content_bg_color(self) -> str:
        if self.theme == "light":
            return "#f3f5f8"
        if self.theme == "oled":
            return "#07080a"
        return "#111318"

    def apply_theme(self, theme: str):
        self.theme = (theme or "dark").strip().lower()
        self.setProperty("theme", self.theme)
        self._init_styles(self.theme)

        is_light = self.theme == "light"
        if hasattr(self, "nav_list") and self.nav_list is not None:
            self.nav_list.setProperty("theme", self.theme)
            cur_row = self.nav_list.currentRow()
            for i, kind in enumerate(self._nav_kinds):
                item = self.nav_list.item(i)
                if item is not None:
                    item.setIcon(_nav_icon(kind, light=is_light, active=(i == cur_row)))
            self.nav_list.viewport().update()

        if self._search_icon_action is not None:
            self._search_icon_action.setIcon(_search_magnifier_icon(light=is_light))

        bg_hex = self._content_bg_color()
        for scroll in self.findChildren(QScrollArea):
            scroll.setStyleSheet(f"QScrollArea {{ background: {bg_hex}; border: none; }}")
            for w in (scroll.viewport(), scroll.widget()):
                if w is not None:
                    pal = w.palette()
                    pal.setColor(QPalette.ColorRole.Window, QColor(bg_hex))
                    pal.setColor(QPalette.ColorRole.Base, QColor(bg_hex))
                    w.setAutoFillBackground(True)
                    w.setPalette(pal)

        for sw in self.findChildren(ToggleSwitch):
            sw.setProperty("theme", self.theme)
            sw.update()
        for chk in self.findChildren(FlatCheckBox):
            chk.setProperty("theme", self.theme)
            chk.update()
        for rb in self.findChildren(FlatRadioButton):
            rb.setProperty("theme", self.theme)
            rb.update()

        for t_key, card in self._theme_swatch_cards.items():
            card.set_state(t_key == self.theme, self.theme)

        if hasattr(self, "combo_theme") and not self._syncing_theme_ui:
            self._syncing_theme_ui = True
            try:
                idx = {"dark": 0, "light": 1, "oled": 2}.get(self.theme, 0)
                if self.combo_theme.currentIndex() != idx:
                    self.combo_theme.setCurrentIndex(idx)
            finally:
                self._syncing_theme_ui = False

        if self.isVisible() or bool(self.internalWinId()):
            apply_win11_window_chrome(self, self.theme)
        if getattr(self, "_ui_initialized", False) and not getattr(self, "_reloading_ui", False):
            self._accepted_and_saved = False
            self._rolled_back = False
            cur_op = (
                max(0.10, min(1.0, self.slider_op.value() / 100.0))
                if hasattr(self, "slider_op")
                else self._initial_opacity
            )
            self._preview_appearance_on_desktop(self.theme, cur_op)

    def _init_styles(self, theme: Optional[str] = None):
        t_key = (theme or getattr(self, "theme", "dark") or "dark").strip().lower()
        is_light = t_key == "light"
        tok = get_theme_tokens(t_key)
        chev_normal = get_ui_chevron_icon_path(False, light=is_light)
        chev_hover = get_ui_chevron_icon_path(True, light=is_light)

        if is_light:
            dlg_bg = "#f3f5f8"
            fg = tok["text_primary"]
            sidebar_bg = "#ebeef4"
            sidebar_border = "rgba(15, 23, 42, 0.075)"
            brand_fg = "#0f172a"
            badge_bg = "rgba(59, 130, 246, 0.14)"
            badge_fg = "#2563eb"
            search_bg = "#ffffff"
            search_border = "rgba(15, 23, 42, 0.11)"
            search_focus = "#3b82f6"
            page_title_fg = "#0f172a"
            card_bg = "#ffffff"
            card_border = "rgba(15, 23, 42, 0.075)"
            card_header_fg = "#64748b"
            row_title_fg = "#0f172a"
            row_desc_fg = "#64748b"
            div_bg = "rgba(15, 23, 42, 0.055)"
            field_bg = "#f8fafc"
            field_fg = "#0f172a"
            field_border = "rgba(15, 23, 42, 0.13)"
            field_hover_bg = "#ffffff"
            field_hover_border = "rgba(15, 23, 42, 0.24)"
            field_focus_bg = "#ffffff"
            field_focus_border = "#3b82f6"
            field_dis_bg = "rgba(15, 23, 42, 0.03)"
            field_dis_fg = "#94a3b8"
            field_dis_border = "rgba(15, 23, 42, 0.07)"
            popup_bg = "#ffffff"
            popup_fg = "#0f172a"
            popup_border = "rgba(15, 23, 42, 0.12)"
            popup_hover = "rgba(15, 23, 42, 0.05)"
            popup_sel = "#3b82f6"
            btn_bg = "#ffffff"
            btn_fg = "#1e293b"
            btn_border = "rgba(15, 23, 42, 0.13)"
            btn_hover_bg = "#f8fafc"
            btn_hover_border = "rgba(15, 23, 42, 0.23)"
            btn_press_bg = "#ebeef4"
            primary_bg = "#3b82f6"
            primary_hover = "#2563eb"
            primary_press = "#1d4ed8"
            list_bg = "#f8fafc"
            list_border = "rgba(15, 23, 42, 0.09)"
            list_item_fg = "#1e293b"
            list_hover = "rgba(15, 23, 42, 0.045)"
            list_sel_bg = "rgba(59, 130, 246, 0.13)"
            list_sel_fg = "#2563eb"
            groove_bg = "rgba(15, 23, 42, 0.14)"
            slider_accent = "#3b82f6"
            sb_handle = "rgba(15, 23, 42, 0.18)"
            sb_hover = "rgba(15, 23, 42, 0.34)"
            ind_bg = "#ffffff"
            ind_border = "rgba(15, 23, 42, 0.28)"
            footer_border = "rgba(15, 23, 42, 0.07)"
        else:
            is_oled = t_key == "oled"
            dlg_bg = "#07080a" if is_oled else "#111318"
            fg = "#f1f5f9"
            sidebar_bg = "#040507" if is_oled else "#0c0e13"
            sidebar_border = "rgba(255, 255, 255, 0.065)" if is_oled else "rgba(255, 255, 255, 0.06)"
            brand_fg = "#f8fafc"
            badge_bg = "rgba(59, 130, 246, 0.14)"
            badge_fg = "#60a5fa"
            search_bg = "#10131a" if is_oled else "#161a23"
            search_border = "rgba(255, 255, 255, 0.08)"
            search_focus = "#3b82f6"
            page_title_fg = "#f8fafc"
            card_bg = "#0e1015" if is_oled else "#171a22"
            card_border = "rgba(255, 255, 255, 0.075)" if is_oled else "rgba(255, 255, 255, 0.065)"
            card_header_fg = "#94a3b8"
            row_title_fg = "#eef2f6"
            row_desc_fg = "#8b95a5"
            div_bg = "rgba(255, 255, 255, 0.05)" if is_oled else "rgba(255, 255, 255, 0.045)"
            field_bg = "#151922" if is_oled else "#1e222d"
            field_fg = "#f1f5f9"
            field_border = "rgba(255, 255, 255, 0.10)"
            field_hover_bg = "#1a1f2b" if is_oled else "#242937"
            field_hover_border = "rgba(255, 255, 255, 0.19)"
            field_focus_bg = "#161a24" if is_oled else "#1f2430"
            field_focus_border = "#3b82f6"
            field_dis_bg = "rgba(255, 255, 255, 0.02)"
            field_dis_fg = "#64748b"
            field_dis_border = "rgba(255, 255, 255, 0.05)"
            popup_bg = "#10131a" if is_oled else "#181c26"
            popup_fg = "#f1f5f9"
            popup_border = "rgba(255, 255, 255, 0.12)"
            popup_hover = "rgba(255, 255, 255, 0.08)"
            popup_sel = "#3b82f6"
            btn_bg = "#161a24" if is_oled else "#1f2430"
            btn_fg = "#eef2f6"
            btn_border = "rgba(255, 255, 255, 0.10)"
            btn_hover_bg = "#1e2330" if is_oled else "#272d3c"
            btn_hover_border = "rgba(255, 255, 255, 0.19)"
            btn_press_bg = "#12151e" if is_oled else "#191d27"
            primary_bg = "#3b82f6"
            primary_hover = "#2563eb"
            primary_press = "#1d4ed8"
            list_bg = "#0a0c10" if is_oled else "#12151c"
            list_border = "rgba(255, 255, 255, 0.075)"
            list_item_fg = "#e2e8f0"
            list_hover = "rgba(255, 255, 255, 0.05)"
            list_sel_bg = "rgba(59, 130, 246, 0.18)"
            list_sel_fg = "#93c5fd"
            groove_bg = "rgba(255, 255, 255, 0.14)"
            slider_accent = "#3b82f6"
            sb_handle = "rgba(255, 255, 255, 0.16)"
            sb_hover = "rgba(255, 255, 255, 0.32)"
            ind_bg = "rgba(255, 255, 255, 0.04)"
            ind_border = "rgba(255, 255, 255, 0.30)"
            footer_border = "rgba(255, 255, 255, 0.06)"

        qss = f"""
            QDialog {{
                background-color: {dlg_bg};
                color: {fg};
                font-family: {UI_FONT_CSS};
                font-size: 13px;
            }}
            /* 左侧导航 */
            QFrame#sidebarFrame {{
                background-color: {sidebar_bg};
                border: none;
                border-right: 1px solid {sidebar_border};
            }}
            QListWidget#navSidebar {{
                background: transparent;
                border: none;
                outline: none;
                padding: 0px;
            }}
            QListWidget#navSidebar::item {{
                border: none;
                outline: none;
                padding: 0px;
                margin: 0px;
            }}
            QLabel#sidebarBrand {{
                color: {brand_fg};
                font-family: {UI_FONT_CSS};
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#sidebarVersionBadge {{
                background-color: {badge_bg};
                color: {badge_fg};
                font-family: {UI_FONT_CSS};
                font-size: 11px;
                font-weight: 600;
                border-radius: 9px;
                padding: 2px 7px;
            }}
            QLineEdit#settingsSearch {{
                background-color: {search_bg};
                border: 1px solid {search_border};
                border-radius: 8px;
                padding: 0px 10px 0px 6px;
                color: {fg};
                font-family: {UI_FONT_CSS};
                font-size: 12px;
                min-height: 32px;
                max-height: 34px;
                selection-background-color: #3b82f6;
            }}
            QLineEdit#settingsSearch:focus {{
                border: 1.5px solid {search_focus};
                background-color: {field_focus_bg};
            }}

            /* 右侧内容区标题 */
            QLabel#pageTitle {{
                font-family: {UI_FONT_CSS};
                font-size: 18px;
                font-weight: 700;
                color: {page_title_fg};
                margin-bottom: 2px;
                background: transparent;
            }}
            QLabel#pageSub {{
                font-family: {UI_FONT_CSS};
                font-size: 12px;
                color: {row_desc_fg};
                margin-bottom: 4px;
                background: transparent;
            }}

            /* 悬浮圆角分组卡片 */
            QFrame#settingsCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 12px;
            }}
            QLabel#cardHeaderTitle {{
                font-family: {UI_FONT_CSS};
                font-size: 12px;
                font-weight: 600;
                color: {card_header_fg};
                background: transparent;
            }}
            QLabel#rowTitle {{
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                font-weight: 500;
                color: {row_title_fg};
                background: transparent;
            }}
            QLabel#rowDesc {{
                font-family: {UI_FONT_CSS};
                font-size: 12px;
                color: {row_desc_fg};
                background: transparent;
            }}
            QFrame#cardDivider {{
                border: none;
                background-color: {div_bg};
                min-height: 1px;
                max-height: 1px;
                margin: 2px 0px;
            }}

            /* 底部操作栏 */
            QFrame#settingsFooter {{
                background-color: {dlg_bg};
                border: none;
                border-top: 1px solid {footer_border};
            }}

            /* 输入框与下拉选择框 */
            QLineEdit, QComboBox {{
                background-color: {field_bg};
                color: {field_fg};
                border: 1px solid {field_border};
                border-radius: 7px;
                padding: 5px 30px 5px 11px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                min-height: 20px;
                selection-background-color: #3b82f6;
            }}
            QLineEdit {{
                padding-right: 11px;
            }}
            QLineEdit:hover, QComboBox:hover {{
                background-color: {field_hover_bg};
                border-color: {field_hover_border};
            }}
            QLineEdit:focus, QComboBox:focus {{
                background-color: {field_focus_bg};
                border: 1.5px solid {field_focus_border};
            }}
            QLineEdit:disabled, QComboBox:disabled {{
                background-color: {field_dis_bg};
                color: {field_dis_fg};
                border-color: {field_dis_border};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
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
                selection-background-color: {popup_sel};
                selection-color: #ffffff;
            }}
            QComboBox QAbstractItemView::item {{
                height: 30px;
                padding: 0 8px;
                border-radius: 5px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {popup_hover};
            }}

            /* 单选框与胶囊拨动开关 */
            QRadioButton, QCheckBox {{
                color: {row_title_fg};
                spacing: 10px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                min-height: 24px;
                padding: 2px 0px;
                background: transparent;
            }}
            ToggleSwitch {{
                min-height: 20px;
                max-height: 22px;
                padding: 0px;
                spacing: 8px;
            }}
            QRadioButton:disabled, QCheckBox:disabled {{
                color: {field_dis_fg};
            }}

            /* 按钮族群 */
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {btn_border};
                border-radius: 7px;
                padding: 0px 14px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                font-weight: 500;
                min-height: 30px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover_bg};
                border-color: {btn_hover_border};
            }}
            QPushButton:pressed {{
                background-color: {btn_press_bg};
            }}
            QPushButton#btnFooter {{
                min-width: 88px;
                min-height: 32px;
                max-height: 32px;
                border-radius: 7px;
                padding: 0px 14px;
            }}
            QPushButton#btnFooter[applied="true"] {{
                color: {"#059669" if is_light else "#34d399"};
                border-color: {"rgba(5, 150, 105, 0.32)" if is_light else "rgba(52, 211, 153, 0.30)"};
                background-color: {"rgba(16, 185, 129, 0.08)" if is_light else "rgba(16, 185, 129, 0.10)"};
                font-weight: 600;
            }}
            QPushButton#btnPrimary {{
                background-color: {primary_bg};
                color: #ffffff;
                border: none;
                border-radius: 7px;
                font-weight: 600;
                min-width: 84px;
                min-height: 32px;
                max-height: 32px;
                padding: 0px 18px;
            }}
            QPushButton#btnPrimary:hover {{
                background-color: {primary_hover};
            }}
            QPushButton#btnPrimary:pressed {{
                background-color: {primary_press};
            }}

            /* 分类列表与磁盘勾选列表 */
            QListWidget#dataList {{
                background-color: {list_bg};
                border: 1px solid {list_border};
                border-radius: 8px;
                color: {list_item_fg};
                padding: 4px;
                outline: none;
            }}
            QListWidget#dataList::item {{
                height: 36px;
                border-radius: 6px;
                padding-left: 12px;
                color: {list_item_fg};
                font-size: 13px;
            }}
            QListWidget#dataList::item:hover {{
                background-color: {list_hover};
            }}
            QListWidget#dataList::item:selected {{
                background-color: {list_sel_bg};
                color: {list_sel_fg};
                font-weight: 600;
            }}
            QListWidget#driveList {{
                background-color: {list_bg};
                border: 1px solid {list_border};
                border-radius: 8px;
                color: {list_item_fg};
                font-size: 12px;
                padding: 3px;
                outline: none;
            }}
            QListWidget#driveList::item {{
                padding: 3px 6px;
                border-radius: 5px;
                color: {list_item_fg};
            }}
            QListWidget#driveList::item:selected {{
                background: rgba(59, 130, 246, 0.14);
                color: {list_sel_fg};
            }}
            QListWidget#driveList::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {ind_border};
                border-radius: 4px;
                background: {ind_bg};
            }}
            QListWidget#driveList::indicator:checked {{
                background: #3b82f6;
                border-color: #3b82f6;
            }}

            /* 滑块控件 */
            QSlider::groove:horizontal {{
                height: 4px;
                background: {groove_bg};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {slider_accent};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #ffffff;
                border: 2px solid {slider_accent};
                width: 12px;
                height: 12px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 8px;
            }}

            /* 极细滚动条 */
            QScrollBar:vertical {{
                border: none;
                background: transparent;
                width: 6px;
                margin: 2px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {sb_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
            {menu_qss(t_key)}
        """
        self.setStyleSheet(qss)

    def _init_ui(self, initial_tab: int):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 左侧精炼导航栏 (212px)
        sidebar_frame = QFrame(self)
        sidebar_frame.setObjectName("sidebarFrame")
        sidebar_frame.setFixedWidth(212)
        s_box = QVBoxLayout(sidebar_frame)
        s_box.setContentsMargins(12, 18, 12, 16)
        s_box.setSpacing(12)

        # 品牌头部行：28x28 图标 + Stacky + v1.0.3 胶囊角标
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 4, 2)
        brand_row.setSpacing(9)

        brand_icon_lbl = QLabel(sidebar_frame)
        brand_icon_lbl.setFixedSize(28, 28)
        brand_icon_lbl.setPixmap(_sidebar_brand_pixmap(28))
        brand_row.addWidget(brand_icon_lbl)

        brand = QLabel("Stacky", sidebar_frame)
        brand.setObjectName("sidebarBrand")
        brand_row.addWidget(brand)
        brand_row.addStretch()

        ver = QLabel("v1.0.3", sidebar_frame)
        ver.setObjectName("sidebarVersionBadge")
        brand_row.addWidget(ver)
        s_box.addLayout(brand_row)

        # 34px 圆角搜索框 + 矢量放大镜图标
        self.search_edit = QLineEdit(sidebar_frame)
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setFixedHeight(34)
        self.search_edit.setPlaceholderText("搜索设置…")
        self.search_edit.setClearButtonEnabled(True)
        self._search_icon_action = self.search_edit.addAction(
            _search_magnifier_icon(light=(self.theme == "light")),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.textChanged.connect(self._on_settings_search)
        s_box.addWidget(self.search_edit)

        self.nav_list = QListWidget(sidebar_frame)
        self.nav_list.setObjectName("navSidebar")
        self.nav_list.setProperty("theme", self.theme)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setMouseTracking(True)
        self.nav_list.setSpacing(2)
        self.nav_list.setItemDelegate(NavItemDelegate(self.nav_list))
        nav_meta = (
            ("文件夹", "分类与应用收纳", "folder"),
            ("交互", "手势 · 快捷键 · 行为", "interaction"),
            ("外观", "主题 · 玻璃 · 显示密度", "appearance"),
            ("小组件", "天气 · 时钟 · 便签 · 搜索", "widgets"),
            ("备份", "配置导入 · 导出 · 恢复", "backup"),
        )
        self._nav_kinds = [kind for _, _, kind in nav_meta]
        for title, desc, kind in nav_meta:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, desc)
            item.setData(Qt.ItemDataRole.UserRole + 1, kind)
            item.setIcon(_nav_icon(kind, light=(self.theme == "light")))
            self.nav_list.addItem(item)
        s_box.addWidget(self.nav_list, stretch=1)
        main_layout.addWidget(sidebar_frame)

        # 2. 右侧内容栈区 + 独立细腻底栏
        content_wrapper = QWidget(self)
        content_box = QVBoxLayout(content_wrapper)
        content_box.setContentsMargins(0, 0, 0, 0)
        content_box.setSpacing(0)

        self.stack = QStackedWidget(content_wrapper)
        self.stack.addWidget(self._wrap_in_scroll(self._create_folders_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_interaction_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_appearance_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_weather_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_backup_tab()))
        content_box.addWidget(self.stack, stretch=1)

        # 3. 底部操作栏（1px 顶部分隔线 + 均衡 84x32 圆角按钮）
        footer_frame = QFrame(content_wrapper)
        footer_frame.setObjectName("settingsFooter")
        bottom_bar = QHBoxLayout(footer_frame)
        bottom_bar.setContentsMargins(26, 12, 26, 12)
        bottom_bar.setSpacing(10)
        bottom_bar.addStretch()

        self._apply_feedback_timer = QTimer(self)
        self._apply_feedback_timer.setSingleShot(True)
        self._apply_feedback_timer.timeout.connect(self._reset_apply_button_feedback)

        self.btn_cancel = QPushButton("取消", footer_frame)
        self.btn_cancel.setObjectName("btnFooter")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setToolTip("放弃未保存的修改并关闭窗口")
        self.btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("应用", footer_frame)
        self.btn_apply.setObjectName("btnFooter")
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.setToolTip("保存修改并保持窗口打开")
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        bottom_bar.addWidget(self.btn_apply)

        self.btn_ok = QPushButton("确定", footer_frame)
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok.setToolTip("保存修改并关闭窗口")
        self.btn_ok.clicked.connect(self._on_ok_clicked)
        bottom_bar.addWidget(self.btn_ok)

        content_box.addWidget(footer_frame)
        main_layout.addWidget(content_wrapper, stretch=1)

        self._bind_dirty_feedback_reset()
        self.nav_list.currentRowChanged.connect(self._on_nav_row_changed)
        clamped_tab = max(0, min(initial_tab, self.nav_list.count() - 1))
        self.nav_list.setCurrentRow(clamped_tab)

    def _bind_dirty_feedback_reset(self):
        """任意表单控件发生修改时，立即解除「已应用 ✓」置灰态，允许再次即时应用。"""
        for combo in self.stack.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self._reset_apply_button_feedback)
        for slider in self.stack.findChildren(QSlider):
            slider.valueChanged.connect(self._reset_apply_button_feedback)
        for btn in self.stack.findChildren(QAbstractButton):
            if isinstance(btn, (ToggleSwitch, FlatRadioButton, FlatCheckBox)):
                btn.toggled.connect(self._reset_apply_button_feedback)
        for edit in self.stack.findChildren(QLineEdit):
            edit.textEdited.connect(self._reset_apply_button_feedback)

    def _reset_apply_button_feedback(self, *_args):
        timer = getattr(self, "_apply_feedback_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        btn = getattr(self, "btn_apply", None)
        if btn is not None and (not btn.isEnabled() or btn.text() != "应用"):
            btn.setText("应用")
            btn.setProperty("applied", False)
            btn.setEnabled(True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_apply_clicked(self):
        """保存并即时热生效所有修改，保持设置窗口打开并给出清晰视觉反馈。"""
        self.save_and_apply()
        btn = getattr(self, "btn_apply", None)
        if btn is not None:
            btn.setText("已应用 ✓")
            btn.setProperty("applied", True)
            btn.setEnabled(False)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            self._apply_feedback_timer.start(1200)

    def _on_ok_clicked(self):
        """保存所有修改并确保 100% 关闭设置窗口。"""
        try:
            self.save_and_apply()
        finally:
            self.accept()

    def _on_nav_row_changed(self, row: int):
        self.stack.setCurrentIndex(row)
        is_light = self.theme == "light"
        for i, kind in enumerate(self._nav_kinds):
            item = self.nav_list.item(i)
            if item is not None:
                item.setIcon(_nav_icon(kind, light=is_light, active=(i == row)))
        self.nav_list.viewport().update()

    def _reconcile_file_shelf_runtime(self, enabled: bool):
        mgr = self.window_manager
        if not mgr or not hasattr(mgr, "init_file_shelf"):
            return
        win = getattr(mgr, "file_shelf_window", None)
        if enabled:
            if win is None:
                mgr.init_file_shelf(self.config_manager.get_file_shelf_config())
        elif win is not None:
            win.hide()
            win.close()
            mgr.file_shelf_window = None

    def _rollback_unsaved_changes(self):
        """取消或点击标题栏 × 关闭时，完整回滚未保存的主题、不透明度及文件中转站运行态预览。"""
        if self._accepted_and_saved or self._rolled_back:
            return
        self._rolled_back = True
        orig_op = self._initial_opacity
        orig_theme = self._initial_theme
        orig_shelf = self._initial_file_shelf_enabled

        self._reloading_ui = True
        try:
            if hasattr(self, "slider_op"):
                val_pct = int(round(orig_op * 100))
                self.slider_op.blockSignals(True)
                self.slider_op.setValue(val_pct)
                self.slider_op.blockSignals(False)
                if hasattr(self, "lbl_op_val"):
                    self.lbl_op_val.setText(f"{val_pct}%")
            if self.theme != orig_theme:
                self.apply_theme(orig_theme)
            if hasattr(self, "chk_file_shelf"):
                self.chk_file_shelf.blockSignals(True)
                self.chk_file_shelf.setChecked(orig_shelf)
                self.chk_file_shelf.blockSignals(False)
                for w in getattr(self, "_shelf_subwidgets", []):
                    w.setEnabled(orig_shelf)
        finally:
            self._reloading_ui = False

        if self.window_manager is not None:
            self._preview_appearance_on_desktop(orig_theme, orig_op)
            self._reconcile_file_shelf_runtime(orig_shelf)

    def reject(self):
        """取消未保存的实时预览，恢复上次已保存的卡片、小组件与文件中转站状态。"""
        self._rollback_unsaved_changes()
        super().reject()

    def closeEvent(self, event):
        """点击窗口右上角 × 关闭或直接调用 close() 时，确保未保存的实时预览一致回滚。"""
        self._rollback_unsaved_changes()
        super().closeEvent(event)

    def _on_drive_item_changed(self, item):
        d = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            if d not in self.selected_drives:
                self.selected_drives.append(d)
        elif d in self.selected_drives:
            self.selected_drives.remove(d)
        if not self.selected_drives:
            self.drive_list.blockSignals(True)
            self.drive_list.item(0).setCheckState(Qt.CheckState.Checked)
            self.drive_list.blockSignals(False)
            self.selected_drives = [self.drives_all[0]]

    def _on_settings_search(self, text: str):
        """全局设置搜索：命中页面就切页，命中具体设置行则滚动过去并高亮。"""
        keyword = text.strip().lower()
        prev_pair = getattr(self, "_search_highlight", None)
        if prev_pair:
            lbl, prev_style = prev_pair
            lbl.setStyleSheet(prev_style)
            self._search_highlight = None
        if not keyword:
            return

        for i in range(self.nav_list.count()):
            item = self.nav_list.item(i)
            hay = f"{item.text()} {item.data(Qt.ItemDataRole.UserRole) or ''}".lower()
            if keyword in hay:
                self.nav_list.setCurrentRow(i)
                return

        for page_idx in range(self.stack.count()):
            scroll = self.stack.widget(page_idx)
            page = scroll.widget()
            if page is None:
                continue
            for lbl in page.findChildren(QLabel):
                if lbl.objectName() not in ("rowTitle", "rowDesc"):
                    continue
                if keyword in lbl.text().lower():
                    self.nav_list.setCurrentRow(page_idx)
                    QApplication.processEvents()
                    scroll.ensureWidgetVisible(lbl)
                    prev_style = lbl.styleSheet()
                    hi_color = "#2563eb" if self.theme == "light" else "#60a5fa"
                    lbl.setStyleSheet(prev_style + f"; color: {hi_color};")
                    self._search_highlight = (lbl, prev_style)
                    return

    def _wrap_in_scroll(self, page_widget: QWidget) -> QScrollArea:
        """为每个设置标签页提供原生滚动视图，杜绝截断。"""
        self._protect_wheel_controls(page_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        bg_hex = self._content_bg_color()
        scroll.setStyleSheet(f"QScrollArea {{ background: {bg_hex}; border: none; }}")
        for w in (scroll.viewport(), page_widget):
            pal = w.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor(bg_hex))
            pal.setColor(QPalette.ColorRole.Base, QColor(bg_hex))
            w.setAutoFillBackground(True)
            w.setPalette(pal)
        scroll.setWidget(page_widget)
        return scroll

    def _protect_wheel_controls(self, parent_widget: QWidget):
        """递归为所有下拉框与滑块安装滚轮过滤器，防止页面滚动时误触改变数值。"""
        for child in parent_widget.findChildren(QComboBox):
            child.installEventFilter(self)
        for child in parent_widget.findChildren(QSlider):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QSlider):
                delta = event.angleDelta().y()
                step = 2 if delta > 0 else -2
                obj.setValue(obj.value() + step)
                event.accept()
                return True
            elif isinstance(obj, QComboBox):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    # =========================================================================
    # Tab 1: 文件夹与分类管理
    # =========================================================================
    def _create_folders_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(26, 22, 26, 20)
        box.setSpacing(14)

        title = QLabel("文件夹")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 分类管理卡片
        cat_card = SettingsCard("分类管理", page)

        btn_add_f = QPushButton("新建分类", cat_card)
        btn_add_f.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_f.clicked.connect(self._on_add_folder)
        cat_card.header_action_box.addWidget(btn_add_f)

        self.folder_list = SmoothCategoryListWidget(cat_card)
        self.folder_list.setObjectName("dataList")
        for f in self.config_manager.get_folders():
            self.folder_list.addItem(f.get("name", "未分类"))
        self.folder_list.setCurrentRow(self.config_manager.get_active_folder_index())
        self._update_folder_list_height()
        self.folder_list.itemDoubleClicked.connect(self._on_rename_folder)
        self.folder_list.currentRowChanged.connect(self._on_folder_selection_changed)
        cat_card.box.addWidget(self.folder_list)

        cat_action_row = QHBoxLayout()
        cat_action_row.setContentsMargins(2, 4, 2, 0)
        cat_action_row.setSpacing(8)
        self.lbl_selected_cat = QLabel("单击切换，双击重命名", cat_card)
        self.lbl_selected_cat.setObjectName("rowDesc")
        cat_action_row.addWidget(self.lbl_selected_cat)
        cat_action_row.addStretch()

        btn_ren_f = QPushButton("重命名", cat_card)
        btn_ren_f.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ren_f.clicked.connect(self._on_rename_folder)
        btn_del_f = QPushButton("解散分类", cat_card)
        btn_del_f.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del_f.clicked.connect(self._on_delete_folder)
        cat_action_row.addWidget(btn_ren_f)
        cat_action_row.addWidget(btn_del_f)
        cat_card.box.addLayout(cat_action_row)

        box.addWidget(cat_card)

        # 2. 桌面卡片管理 (多实例)
        inst_card = SettingsCard("桌面卡片", page)

        btn_add_inst = QPushButton("新增卡片", inst_card)
        btn_add_inst.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_inst.clicked.connect(self._on_add_instance)
        inst_count = len(self.config_manager.get_instances())
        self.inst_count_row = inst_card.add_row("当前数量", f"{inst_count} 个卡片", btn_add_inst)

        inst_card.add_divider()

        is_locked = bool(self.config_manager.get("locked", False))
        self.btn_lock_all = QPushButton("解锁卡片" if is_locked else "锁定卡片", inst_card)
        self.btn_lock_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lock_all.clicked.connect(self._on_toggle_lock_all)
        inst_card.add_row("锁定位置", "锁定后卡片不可拖动与缩放", self.btn_lock_all)

        inst_card.add_divider()

        btn_tidy = QPushButton("整理图标", inst_card)
        btn_tidy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_tidy.clicked.connect(self._on_tidy_desktop)
        inst_card.add_row("桌面图标", "扫描桌面快捷方式并自动归类", btn_tidy)

        box.addWidget(inst_card)

        # 3. 本地存储目录
        storage_card = SettingsCard("存储与迁移", page)

        cur_storage = self.config_manager.get_managed_storage_root()
        btn_box_storage = QWidget()
        b_s_layout = QHBoxLayout(btn_box_storage)
        b_s_layout.setContentsMargins(0, 0, 0, 0)
        b_s_layout.setSpacing(8)

        btn_browse_storage = QPushButton("更改目录", btn_box_storage)
        btn_browse_storage.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_storage.clicked.connect(self._on_browse_storage)
        btn_open_storage = QPushButton("打开目录", btn_box_storage)
        btn_open_storage.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_storage.clicked.connect(self._on_open_storage)
        b_s_layout.addWidget(btn_browse_storage)
        b_s_layout.addWidget(btn_open_storage)

        self.storage_row = storage_card.add_row("存放路径", cur_storage, btn_box_storage)
        self.lbl_storage_path = self.storage_row.findChild(QLabel, "rowDesc")

        storage_card.add_divider()

        btn_migrator = QPushButton("导入旧数据", storage_card)
        btn_migrator.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_migrator.clicked.connect(self._on_open_migrator)
        storage_card.add_row("数据迁移", "从其他桌面收纳软件导入分类", btn_migrator)

        box.addWidget(storage_card)
        box.addStretch()
        return page

    # =========================================================================
    # Tab 2: 打开与交互设置
    # =========================================================================
    def _create_interaction_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(26, 22, 26, 20)
        box.setSpacing(14)

        title = QLabel("交互")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 打开方式卡片
        mode_card = SettingsCard("启动方式", page)

        self.rb_double = FlatRadioButton("双击打开", mode_card)
        self.rb_hybrid = FlatRadioButton("卡片单击、抽屉双击", mode_card)
        self.rb_single = FlatRadioButton("单击打开", mode_card)

        curr_mode = self.config_manager.get_open_app_mode()
        if curr_mode == "single":
            self.rb_single.setChecked(True)
        elif curr_mode == "hybrid":
            self.rb_hybrid.setChecked(True)
        else:
            self.rb_double.setChecked(True)

        mode_card.box.addWidget(self.rb_double)
        mode_card.box.addWidget(self.rb_hybrid)
        mode_card.box.addWidget(self.rb_single)
        box.addWidget(mode_card)

        # 2. 全局呼出与快捷键
        summon_card = SettingsCard("快捷键与启动", page)

        self.combo_summon_target = QComboBox()
        self.combo_summon_target.addItem("文件夹卡片", "card")
        self.combo_summon_target.addItem("搜索与抽屉", "drawer")
        target_val = self.config_manager.get("summon_target", "card")
        self.combo_summon_target.setCurrentIndex(1 if target_val == "drawer" else 0)
        summon_card.add_row("呼出目标", "按下全局快捷键时唤出卡片或抽屉", self.combo_summon_target)

        summon_card.add_divider()

        self.combo_post_launch = QComboBox()
        self.combo_post_launch.addItem("启动后自动收起", "close_drawer")
        self.combo_post_launch.addItem("启动后保持展开", "keep_open")
        p_act = self.config_manager.get_post_launch_action()
        self.combo_post_launch.setCurrentIndex(1 if p_act == "keep_open" else 0)
        summon_card.add_row("启动应用后", "从抽屉启动程序后的窗口动作", self.combo_post_launch)

        summon_card.add_divider()

        self.combo_hotkey = QComboBox()
        for label, value in (
            ("Alt + Space", "Alt+Space"),
            ("Alt + F", "Alt+F"),
            ("Ctrl + Alt + Space", "Ctrl+Alt+Space"),
            ("Ctrl + Alt + F", "Ctrl+Alt+F"),
        ):
            self.combo_hotkey.addItem(label, value)
        hk_idx = self.combo_hotkey.findData(self.config_manager.get("hotkey", "Alt+Space"))
        self.combo_hotkey.setCurrentIndex(max(0, hk_idx))
        summon_card.add_row("全局快捷键", "热键被占用时自动回退至 Alt+F", self.combo_hotkey)

        summon_card.add_divider()

        btn_dock = QPushButton("生成快捷方式", summon_card)
        btn_dock.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dock.clicked.connect(self._on_create_dock_shortcut)
        summon_card.add_row("Dock 启动器", "在桌面生成可拖入任务栏或 Dock 的快捷方式", btn_dock)

        box.addWidget(summon_card)

        # 3. Everything 全盘搜索
        everything_card = SettingsCard("全盘搜索", page)

        from everything_ipc import get_everything_client
        is_ev_running = get_everything_client().is_available()

        status_lbl = QLabel("已连接" if is_ev_running else "未运行")
        status_lbl.setObjectName("rowDesc")
        everything_card.add_row("引擎状态", "保持 Everything 后台运行即可秒级检索", status_lbl)

        everything_card.add_divider()

        self.chk_everything = ToggleSwitch()
        self.chk_everything.setChecked(self.config_manager.is_everything_enabled())
        everything_card.add_row("检索本地文件", "在抽屉搜索框内同步检索全盘文件", self.chk_everything)

        everything_card.add_divider()

        lbl_tips = QLabel("快捷键提示：Enter 直接打开 · Ctrl+Enter 定位所在文件夹")
        lbl_tips.setObjectName("rowDesc")
        everything_card.box.addWidget(lbl_tips)

        box.addWidget(everything_card)

        # 4. 系统与桌面
        sys_card = SettingsCard("系统行为", page)

        self.chk_double_click_hide = ToggleSwitch()
        self.chk_double_click_hide.setChecked(
            self.config_manager.is_double_click_desktop_hide_enabled()
        )
        sys_card.add_row("双击桌面空白处隐藏", "双击壁纸空白区域一键收起或恢复桌面卡片", self.chk_double_click_hide)

        sys_card.add_divider()

        self.chk_autostart = ToggleSwitch()
        self.chk_autostart.setChecked(is_autostart_enabled())
        sys_card.add_row("开机自动启动", "登录 Windows 后静默驻留桌面", self.chk_autostart)
        box.addWidget(sys_card)

        box.addStretch()
        return page

    # =========================================================================
    # Tab 3: 外观质感与网格
    # =========================================================================
    def _preview_appearance_on_desktop(self, theme: str, opacity: float):
        if self.window_manager is None:
            return
        op = max(0.10, min(1.0, float(opacity)))
        t_key = (theme or "dark").strip().lower()
        radius = self.config_manager.get("border_radius", 26)
        for win in list(getattr(self.window_manager, "windows", {}).values()):
            if hasattr(win, "theme"):
                win.theme = t_key
            card = getattr(win, "card", None)
            if card is not None and hasattr(card, "set_card_style"):
                card.set_card_style(radius, op, theme=t_key)
            title_lbl = getattr(win, "title_label", None)
            if title_lbl is not None and hasattr(title_lbl, "set_theme"):
                title_lbl.set_theme(t_key)
        for attr in (
            "weather_window",
            "clock_window",
            "monitor_window",
            "memo_window",
            "media_window",
            "everything_window",
            "file_shelf_window",
        ):
            w_win = getattr(self.window_manager, attr, None)
            if w_win is None:
                continue
            if hasattr(w_win, "set_theme"):
                w_win.set_theme(t_key)
            if hasattr(w_win, "set_glass_params"):
                w_win.set_glass_params(radius=radius, opacity=op, theme=t_key)
            elif hasattr(w_win, "opacity"):
                w_win.opacity = op
                if hasattr(w_win, "_bg_cache"):
                    w_win._bg_cache = None
                w_win.update()

    def _on_theme_swatch_clicked(self, theme_key: str):
        idx = {"dark": 0, "light": 1, "oled": 2}.get(theme_key, 0)
        self.combo_theme.setCurrentIndex(idx)

    def _on_combo_theme_changed(self, _index: int):
        chosen = self.combo_theme.currentData() or "dark"
        if not self._syncing_theme_ui:
            self._syncing_theme_ui = True
            try:
                self.apply_theme(chosen)
            finally:
                self._syncing_theme_ui = False

    def _on_opacity_slider_changed(self, val: int):
        self.lbl_op_val.setText(f"{val}%")
        if self._reloading_ui:
            return
        self._accepted_and_saved = False
        self._rolled_back = False
        op = max(0.10, min(1.0, val / 100.0))
        self._preview_appearance_on_desktop(self.theme, op)

    def _create_appearance_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(26, 18, 26, 16)
        box.setSpacing(11)

        title = QLabel("外观")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 主题预览卡片 + 网格规格
        theme_card = SettingsCard("主题与网格", page)

        swatch_row = QHBoxLayout()
        swatch_row.setContentsMargins(0, 4, 0, 6)
        swatch_row.setSpacing(14)
        cur_t = str(self.config_manager.get("theme", "dark") or "dark").lower()
        for t_key, t_label in (
            ("dark", "深色磨砂"),
            ("light", "浅色云母"),
            ("oled", "纯黑 OLED"),
        ):
            sw_card = ThemePreviewCard(t_key, t_label, theme_card)
            sw_card.set_state(t_key == cur_t, cur_t)
            sw_card.clicked.connect(self._on_theme_swatch_clicked)
            self._theme_swatch_cards[t_key] = sw_card
            swatch_row.addWidget(sw_card)
        swatch_row.addStretch()
        theme_card.box.addLayout(swatch_row)

        # 保持 self.combo_theme 双向同步并隐藏视觉重复行，确保外部调用与测试 100% 兼容
        self.combo_theme = QComboBox(theme_card)
        self.combo_theme.addItem("深色", "dark")
        self.combo_theme.addItem("浅色", "light")
        self.combo_theme.addItem("纯黑", "oled")
        t_idx = {"dark": 0, "light": 1, "oled": 2}.get(cur_t, 0)
        self.combo_theme.setCurrentIndex(t_idx)
        self.combo_theme.setVisible(False)
        self.combo_theme.currentIndexChanged.connect(self._on_combo_theme_changed)

        theme_card.add_divider()

        self.combo_grid = QComboBox()
        self.combo_grid.addItem("3 × 3", (3, 3))
        self.combo_grid.addItem("2 × 2", (2, 2))
        self.combo_grid.addItem("2 × 4", (2, 4))
        self.combo_grid.addItem("4 × 2", (4, 2))
        self.combo_grid.addItem("3 × 5", (3, 5))
        self.combo_grid.addItem("4 × 4", (4, 4))
        has_custom_size = any(i.get("card_width") for i in self.config_manager.get_instances())
        if has_custom_size:
            self.combo_grid.insertItem(0, "自定义（当前卡片）", (0, 0))
            self.combo_grid.setCurrentIndex(0)
        cur_r = self.config_manager.get("grid_rows", 3)
        cur_c = self.config_manager.get("grid_cols", 3)
        if not has_custom_size:
            for i in range(self.combo_grid.count()):
                if self.combo_grid.itemData(i) == (cur_r, cur_c):
                    self.combo_grid.setCurrentIndex(i)
                    break
        self.combo_grid.currentIndexChanged.connect(lambda _i: setattr(self, "_grid_dirty", True))
        theme_card.add_row("默认网格", "更改后会统一重置卡片的自定义拉伸尺寸", self.combo_grid)

        box.addWidget(theme_card)

        # 2. 图标呈现模式
        display_card = SettingsCard("图标样式", page)

        self.combo_display_mode = QComboBox()
        self.combo_display_mode.addItem("图标 + 名称", "classic")
        self.combo_display_mode.addItem("仅图标", "large_icon")
        cur_disp = self.config_manager.get_app_display_mode()
        d_map = {"classic": 0, "large_icon": 1}
        self.combo_display_mode.setCurrentIndex(d_map.get(cur_disp, 0))
        display_card.add_row("显示密度", "控制卡片与抽屉中应用图标下方是否显示名称", self.combo_display_mode)

        display_card.add_divider()

        self.chk_admin_badge = ToggleSwitch()
        self.chk_admin_badge.setChecked(self.config_manager.is_admin_badge_enabled())
        display_card.add_row("管理员角标", "为需要管理员权限的程序显示盾牌标识", self.chk_admin_badge)

        box.addWidget(display_card)

        # 3. 透明度与层级卡片
        layer_card = SettingsCard("材质与窗口层级", page)

        cur_op = int(self.config_manager.get("opacity", 0.88) * 100)
        op_widget = QWidget()
        op_layout = QHBoxLayout(op_widget)
        op_layout.setContentsMargins(0, 0, 0, 0)
        op_layout.setSpacing(10)

        self.slider_op = QSlider(Qt.Orientation.Horizontal)
        self.slider_op.setRange(10, 100)
        self.slider_op.setValue(cur_op)
        self.slider_op.setFixedWidth(160)

        self.lbl_op_val = QLabel(f"{cur_op}%")
        self.lbl_op_val.setFixedWidth(40)
        self.lbl_op_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_op_val.setObjectName("rowTitle")
        self.slider_op.valueChanged.connect(self._on_opacity_slider_changed)

        op_layout.addWidget(self.slider_op)
        op_layout.addWidget(self.lbl_op_val)
        layer_card.add_row("卡片不透明度", "拖动实时预览卡片对桌面壁纸的透光度", op_widget)

        layer_card.add_divider()

        self.combo_layer = QComboBox()
        self.combo_layer.addItem("嵌入桌面", "bottom")
        self.combo_layer.addItem("普通窗口", "normal")
        self.combo_layer.addItem("始终置顶", "top")
        cur_l = self.config_manager.get("layer_mode", "bottom")
        l_idx = {"bottom": 0, "normal": 1, "top": 2}.get(cur_l, 0)
        self.combo_layer.setCurrentIndex(l_idx)
        layer_card.add_row("窗口层级", "嵌入桌面时免疫 Win+D 且不遮挡普通窗口", self.combo_layer)

        layer_card.add_divider()

        self.chk_glass_blur = ToggleSwitch()
        self.chk_glass_blur.setChecked(self.config_manager.get("glass_blur_enabled", False))
        layer_card.add_row("系统亚克力模糊", "启用 Windows 原生 DWM 背景毛玻璃折射", self.chk_glass_blur)

        box.addWidget(layer_card)
        box.addStretch()
        return page

    # =========================================================================
    # Tab 4 & Tab 5: 桌面小组件中心与配置备份
    # =========================================================================
    def _on_file_shelf_toggled(self, enabled: bool):
        for w in getattr(self, "_shelf_subwidgets", []):
            w.setEnabled(enabled)
        if not getattr(self, "_ui_initialized", False) or getattr(self, "_reloading_ui", False):
            return
        self._accepted_and_saved = False
        self._rolled_back = False
        self._reconcile_file_shelf_runtime(enabled)

    def _create_backup_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(26, 22, 26, 20)
        box.setSpacing(14)

        title = QLabel("备份")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        desc = QLabel("导出当前的分类、卡片布局、主题与小组件位置。导入前会自动创建本地备份。")
        desc.setWordWrap(True)
        desc.setObjectName("rowDesc")
        box.addWidget(desc)

        card = SettingsCard("配置文件", page)

        btn_export = QPushButton("导出…")
        btn_export.setFixedWidth(96)
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.clicked.connect(self._export_config)
        card.add_row("导出配置", "保存为 JSON 文件，可用于备份或换机迁移", btn_export)

        card.add_divider()

        btn_import = QPushButton("导入…")
        btn_import.setFixedWidth(96)
        btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_import.clicked.connect(self._import_config)
        card.add_row("导入配置", "恢复已有配置文件，覆盖前自动生成备份", btn_import)

        card.add_divider()

        btn_dir = QPushButton("打开目录")
        btn_dir.setFixedWidth(96)
        btn_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dir.clicked.connect(self._open_config_dir)
        card.add_row("配置目录", "在资源管理器中查看 config.json", btn_dir)

        box.addWidget(card)

        path_lbl = QLabel()
        path_lbl.setObjectName("rowDesc")
        _path = self.config_manager.config_path
        path_lbl.setText(
            path_lbl.fontMetrics().elidedText(_path, Qt.TextElideMode.ElideMiddle, 520)
        )
        path_lbl.setToolTip(_path)
        box.addWidget(path_lbl)

        tip = QLabel("说明：配置文件仅记录界面与分类索引，不包含原文件本体。导入配置后重启 Stacky 即可完整生效。")
        tip.setWordWrap(True)
        tip.setObjectName("rowDesc")
        box.addWidget(tip)
        box.addStretch()
        return page

    def _export_config(self):
        desk_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        try:
            from storage_manager import get_desktop_dir
            resolved_desk = get_desktop_dir()
            if resolved_desk and os.path.isdir(resolved_desk):
                desk_dir = resolved_desk
        except Exception:
            pass
        default = os.path.join(
            desk_dir,
            "stacky-config-%s.json" % time.strftime("%Y%m%d"),
        )
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", default, "JSON 配置 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        tmp_path = f"{path}.{os.getpid()}.tmp"
        try:
            payload = json.dumps(self.config_manager.data, ensure_ascii=False, indent=2)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            QMessageBox.information(self, "导出完成", "配置已保存到：\n" + path)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _reload_controls_from_config(self):
        """导入配置或外部重载后，无副作用地把设置窗口全部控件同步到最新 ConfigManager 状态。"""
        self._reloading_ui = True
        try:
            # 1. 文件夹与实例
            if hasattr(self, "folder_list"):
                self.folder_list.blockSignals(True)
                self.folder_list.clear()
                for f in self.config_manager.get_folders():
                    self.folder_list.addItem(f.get("name", "未分类"))
                self.folder_list.setCurrentRow(self.config_manager.get_active_folder_index())
                self.folder_list.blockSignals(False)
                self._update_folder_list_height()
            if hasattr(self, "inst_count_row"):
                desc = self.inst_count_row.findChild(QLabel, "rowDesc")
                if desc is not None:
                    desc.setText(f"{len(self.config_manager.get_instances())} 个卡片")
            if hasattr(self, "btn_lock_all"):
                locked = bool(self.config_manager.get("locked", False))
                self.btn_lock_all.setText("解锁卡片" if locked else "锁定卡片")
            if hasattr(self, "lbl_storage_path") and self.lbl_storage_path is not None:
                self.lbl_storage_path.setText(self.config_manager.get_managed_storage_root())

            # 2. 交互
            curr_mode = self.config_manager.get_open_app_mode()
            if curr_mode == "single" and hasattr(self, "rb_single"):
                self.rb_single.setChecked(True)
            elif curr_mode == "hybrid" and hasattr(self, "rb_hybrid"):
                self.rb_hybrid.setChecked(True)
            elif hasattr(self, "rb_double"):
                self.rb_double.setChecked(True)

            if hasattr(self, "combo_summon_target"):
                target_val = self.config_manager.get("summon_target", "card")
                self.combo_summon_target.setCurrentIndex(1 if target_val == "drawer" else 0)
            if hasattr(self, "combo_post_launch"):
                p_act = self.config_manager.get_post_launch_action()
                self.combo_post_launch.setCurrentIndex(1 if p_act == "keep_open" else 0)
            if hasattr(self, "combo_hotkey"):
                hk_idx = self.combo_hotkey.findData(self.config_manager.get("hotkey", "Alt+Space"))
                self.combo_hotkey.setCurrentIndex(max(0, hk_idx))
            if hasattr(self, "chk_everything"):
                self.chk_everything.setChecked(self.config_manager.is_everything_enabled())
            if hasattr(self, "chk_double_click_hide"):
                self.chk_double_click_hide.setChecked(
                    self.config_manager.is_double_click_desktop_hide_enabled()
                )

            # 3. 外观与层级
            new_theme = str(self.config_manager.get("theme", "dark") or "dark").lower()
            self.apply_theme(new_theme)

            if hasattr(self, "combo_grid"):
                self.combo_grid.blockSignals(True)
                self.combo_grid.clear()
                self.combo_grid.addItem("3 × 3", (3, 3))
                self.combo_grid.addItem("2 × 2", (2, 2))
                self.combo_grid.addItem("2 × 4", (2, 4))
                self.combo_grid.addItem("4 × 2", (4, 2))
                self.combo_grid.addItem("3 × 5", (3, 5))
                self.combo_grid.addItem("4 × 4", (4, 4))
                has_custom = any(i.get("card_width") for i in self.config_manager.get_instances())
                if has_custom:
                    self.combo_grid.insertItem(0, "自定义（当前卡片）", (0, 0))
                    self.combo_grid.setCurrentIndex(0)
                else:
                    cur_r = self.config_manager.get("grid_rows", 3)
                    cur_c = self.config_manager.get("grid_cols", 3)
                    for i in range(self.combo_grid.count()):
                        if self.combo_grid.itemData(i) == (cur_r, cur_c):
                            self.combo_grid.setCurrentIndex(i)
                            break
                self.combo_grid.blockSignals(False)
                self._grid_dirty = False

            if hasattr(self, "combo_display_mode"):
                cur_disp = self.config_manager.get_app_display_mode()
                self.combo_display_mode.setCurrentIndex(1 if cur_disp == "large_icon" else 0)
            if hasattr(self, "chk_admin_badge"):
                self.chk_admin_badge.setChecked(self.config_manager.is_admin_badge_enabled())

            if hasattr(self, "slider_op"):
                cur_op = int(round(float(self.config_manager.get("opacity", 0.88)) * 100))
                self.slider_op.blockSignals(True)
                self.slider_op.setValue(cur_op)
                self.slider_op.blockSignals(False)
                if hasattr(self, "lbl_op_val"):
                    self.lbl_op_val.setText(f"{cur_op}%")

            if hasattr(self, "combo_layer"):
                cur_l = self.config_manager.get("layer_mode", "bottom")
                self.combo_layer.setCurrentIndex({"bottom": 0, "normal": 1, "top": 2}.get(cur_l, 0))
            if hasattr(self, "chk_glass_blur"):
                self.chk_glass_blur.setChecked(bool(self.config_manager.get("glass_blur_enabled", False)))

            # 4. 小组件与文件中转站
            shelf_cfg = self.config_manager.get_file_shelf_config()
            if hasattr(self, "chk_file_shelf"):
                s_en = bool(shelf_cfg.get("enabled", True))
                self.chk_file_shelf.blockSignals(True)
                self.chk_file_shelf.setChecked(s_en)
                self.chk_file_shelf.blockSignals(False)
                for w in getattr(self, "_shelf_subwidgets", []):
                    w.setEnabled(s_en)
            if hasattr(self, "combo_shelf_edge"):
                self.combo_shelf_edge.setCurrentIndex(1 if shelf_cfg.get("edge", "right") == "left" else 0)
            if hasattr(self, "chk_shelf_auto_collapse"):
                self.chk_shelf_auto_collapse.setChecked(bool(shelf_cfg.get("auto_collapse", True)))
            if hasattr(self, "chk_shelf_hide_fullscreen"):
                self.chk_shelf_hide_fullscreen.setChecked(bool(shelf_cfg.get("hide_in_fullscreen", True)))

            w_cfg = self.config_manager.get_weather_config()
            if hasattr(self, "chk_weather_enable"):
                self.chk_weather_enable.setChecked(bool(w_cfg.get("enabled", True)))
            if hasattr(self, "combo_weather_size"):
                self.combo_weather_size.setCurrentIndex(1 if w_cfg.get("size") == "2x1" else 0)
            is_auto = bool(w_cfg.get("auto_location", True))
            if hasattr(self, "rb_auto_loc") and hasattr(self, "rb_manual_loc"):
                self.rb_auto_loc.setChecked(is_auto)
                self.rb_manual_loc.setChecked(not is_auto)
            cur_city = w_cfg.get("city", "成都")
            if hasattr(self, "combo_city"):
                self.combo_city.blockSignals(True)
                c_idx = self.combo_city.findText(cur_city)
                if c_idx >= 0:
                    self.combo_city.setCurrentIndex(c_idx)
                self.combo_city.blockSignals(False)
                self.combo_city.setEnabled(not is_auto)
            if hasattr(self, "edit_city"):
                self.edit_city.setText(cur_city)
                self.edit_city.setEnabled(not is_auto)
            self._reload_districts(cur_city, w_cfg.get("region", ""), w_cfg.get("district", ""))

            c_cfg = self.config_manager.get_clock_config()
            if hasattr(self, "chk_clock_enable"):
                self.chk_clock_enable.setChecked(bool(c_cfg.get("enabled", False)))
            if hasattr(self, "combo_clock_size"):
                self.combo_clock_size.setCurrentIndex(1 if c_cfg.get("size") == "2x1" else 0)

            m_cfg = self.config_manager.get_monitor_config()
            if hasattr(self, "chk_monitor_enable"):
                self.chk_monitor_enable.setChecked(bool(m_cfg.get("enabled", False)))
            if hasattr(self, "drive_list"):
                cur_drives = m_cfg.get("disk_drives") or [m_cfg.get("disk_drive") or "C:\\"]
                self.selected_drives = [d for d in cur_drives if d] or ["C:\\"]
                self.drive_list.blockSignals(True)
                for i in range(self.drive_list.count()):
                    it = self.drive_list.item(i)
                    d_val = it.data(Qt.ItemDataRole.UserRole)
                    it.setCheckState(
                        Qt.CheckState.Checked if d_val in self.selected_drives else Qt.CheckState.Unchecked
                    )
                self.drive_list.blockSignals(False)

            memo_cfg = self.config_manager.get_memo_config()
            if hasattr(self, "chk_memo_enable"):
                self.chk_memo_enable.setChecked(bool(memo_cfg.get("enabled", False)))

            media_cfg = self.config_manager.get_media_config()
            if hasattr(self, "chk_media_enable"):
                self.chk_media_enable.setChecked(bool(media_cfg.get("enabled", False)))
            if hasattr(self, "combo_media_size"):
                self.combo_media_size.setCurrentIndex(1 if media_cfg.get("size") == "2x1" else 0)
            if hasattr(self, "chk_media_lyrics"):
                self.chk_media_lyrics.setChecked(bool(media_cfg.get("show_lyrics", True)))

            ev_cfg = self.config_manager.get_everything_config()
            if hasattr(self, "chk_everything_widget"):
                self.chk_everything_widget.setChecked(bool(ev_cfg.get("enabled", True)))
            if hasattr(self, "combo_everything_size"):
                self.combo_everything_size.setCurrentIndex(1 if ev_cfg.get("size") == "2x1" else 0)
        finally:
            self._reloading_ui = False

    def _import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 配置 (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", "无法读取 JSON：\n%s" % exc)
            return
        if not isinstance(data, dict) or not ({"folders", "instances", "theme"} & set(data)):
            QMessageBox.warning(self, "导入失败", "这不是有效的 Stacky 配置文件。")
            return
        if (
            QMessageBox.question(
                self,
                "导入配置",
                "导入会覆盖当前的卡片、布局与主题。\n导入前会自动备份现有配置，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            import copy

            backup = "%s.bak-%s" % (
                self.config_manager.config_path,
                time.strftime("%Y%m%d-%H%M%S"),
            )
            if os.path.exists(self.config_manager.config_path):
                shutil.copy2(self.config_manager.config_path, backup)
            merged = copy.deepcopy(DEFAULT_CONFIG)
            merged.update(data)
            self.config_manager.save(merged)
            self.config_manager.load()
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return

        self._reload_controls_from_config()
        self._initial_theme = str(self.config_manager.get("theme", "dark") or "dark").lower()
        self._initial_opacity = float(self.config_manager.get("opacity", 0.88))
        self._initial_file_shelf_enabled = bool(
            self.config_manager.get_file_shelf_config().get("enabled", True)
        )
        self._saved_once = True
        self._accepted_and_saved = True
        self._rolled_back = False

        if self.window_manager is not None:
            self._sync_imported_instances_on_desktop()
            self._broadcast_runtime_sync()

        QMessageBox.information(
            self,
            "导入完成",
            "配置已导入并同步至设置面板与桌面。",
        )

    def _sync_imported_instances_on_desktop(self):
        if self.window_manager is None or not hasattr(self.window_manager, "windows"):
            return
        inst_map = {
            inst.get("instance_id"): inst
            for inst in self.config_manager.get_instances()
            if isinstance(inst, dict) and inst.get("instance_id")
        }
        for inst_id, win in list(self.window_manager.windows.items()):
            if inst_id not in inst_map:
                try:
                    if getattr(win, "desktop_watchdog", None) is not None:
                        win.desktop_watchdog.stop()
                    win.close()
                except Exception:
                    pass
                self.window_manager.windows.pop(inst_id, None)
            else:
                inst_cfg = inst_map[inst_id]
                new_f = inst_cfg.get("folder_name")
                if new_f and getattr(win, "bound_folder_name", None) != new_f:
                    win.bound_folder_name = new_f
                    win.active_page = 0
                r_i = inst_cfg.get("rows")
                c_i = inst_cfg.get("cols")
                if r_i and c_i and hasattr(win, "apply_grid_shape"):
                    win.apply_grid_shape(int(r_i), int(c_i), toast=False)
        if hasattr(self.window_manager, "create_instance"):
            for inst_id, inst_cfg in inst_map.items():
                if inst_id not in self.window_manager.windows:
                    try:
                        self.window_manager.create_instance(
                            inst_cfg.get("folder_name", "常用应用"),
                            int(inst_cfg.get("pos_x", 797)),
                            int(inst_cfg.get("pos_y", 49)),
                            locked=bool(inst_cfg.get("locked", False)),
                            instance_id=inst_id,
                        )
                    except TypeError:
                        try:
                            self.window_manager.create_instance(
                                inst_cfg.get("folder_name", "常用应用"),
                                int(inst_cfg.get("pos_x", 797)),
                                int(inst_cfg.get("pos_y", 49)),
                                locked=bool(inst_cfg.get("locked", False)),
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass

    def _open_config_dir(self):
        folder = os.path.dirname(self.config_manager.config_path)
        if folder and os.path.isdir(folder):
            os.startfile(folder)

    def _create_weather_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(26, 22, 26, 20)
        box.setSpacing(14)

        title = QLabel("小组件")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 0. 文件中转站
        shelf_card = SettingsCard("文件中转站", page)
        shelf_cfg = self.config_manager.get_file_shelf_config()

        self.chk_file_shelf = ToggleSwitch()
        self.chk_file_shelf.setChecked(bool(shelf_cfg.get("enabled", True)))
        shelf_card.header_action_box.addWidget(self.chk_file_shelf)

        self.combo_shelf_edge = QComboBox()
        self.combo_shelf_edge.addItem("屏幕右侧", "right")
        self.combo_shelf_edge.addItem("屏幕左侧", "left")
        self.combo_shelf_edge.setCurrentIndex(1 if shelf_cfg.get("edge", "right") == "left" else 0)
        shelf_card.add_row("吸附边缘", "中转站停靠的屏幕侧边", self.combo_shelf_edge)

        shelf_card.add_divider()

        self.chk_shelf_auto_collapse = ToggleSwitch()
        self.chk_shelf_auto_collapse.setChecked(bool(shelf_cfg.get("auto_collapse", True)))
        shelf_card.add_row("清空后自动收起", "拖出全部暂存文件后自动贴边隐藏", self.chk_shelf_auto_collapse)

        shelf_card.add_divider()

        self.chk_shelf_hide_fullscreen = ToggleSwitch()
        self.chk_shelf_hide_fullscreen.setChecked(bool(shelf_cfg.get("hide_in_fullscreen", True)))
        shelf_card.add_row("全屏应用时隐藏", "运行全屏游戏或视频时自动避让", self.chk_shelf_hide_fullscreen)

        self._shelf_subwidgets = [
            self.combo_shelf_edge,
            self.chk_shelf_auto_collapse,
            self.chk_shelf_hide_fullscreen,
        ]
        for w in self._shelf_subwidgets:
            w.setEnabled(self.chk_file_shelf.isChecked())
        self.chk_file_shelf.toggled.connect(self._on_file_shelf_toggled)
        box.addWidget(shelf_card)

        # 1. 桌面天气组件
        w_cfg = self.config_manager.get_weather_config()
        weather_card = SettingsCard("天气", page)

        self.chk_weather_enable = ToggleSwitch()
        self.chk_weather_enable.setChecked(w_cfg.get("enabled", True))
        weather_card.header_action_box.addWidget(self.chk_weather_enable)

        self.combo_weather_size = QComboBox()
        self.combo_weather_size.addItem("2 × 2 方卡", "2x2")
        self.combo_weather_size.addItem("2 × 1 胶囊", "2x1")
        self.combo_weather_size.setCurrentIndex(1 if w_cfg.get("size") == "2x1" else 0)
        weather_card.add_row("卡片尺寸", "选择标准方卡或紧凑横向胶囊", self.combo_weather_size)

        weather_card.add_divider()

        self.rb_auto_loc = FlatRadioButton("自动定位")
        self.rb_manual_loc = FlatRadioButton("手动指定")
        is_auto = w_cfg.get("auto_location", True)
        self.rb_auto_loc.setChecked(is_auto)
        self.rb_manual_loc.setChecked(not is_auto)

        loc_type_widget = QWidget()
        loc_type_layout = QHBoxLayout(loc_type_widget)
        loc_type_layout.setContentsMargins(0, 0, 0, 0)
        loc_type_layout.setSpacing(16)
        loc_type_layout.addWidget(self.rb_auto_loc)
        loc_type_layout.addWidget(self.rb_manual_loc)
        weather_card.add_row("定位模式", "自动定位检测到代理时保留上次有效城市", loc_type_widget)

        weather_card.add_divider()

        btn_ip_locate = QPushButton("重新定位", weather_card)
        btn_ip_locate.setFixedWidth(108)
        btn_ip_locate.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ip_locate.clicked.connect(self._on_ip_relocate)
        weather_card.add_row("实时 IP 校准", "按当前网络归属地覆盖位置", btn_ip_locate)

        weather_card.add_divider()

        self.combo_city = QComboBox()
        self.combo_city.setFixedWidth(200)
        city_names = list(POPULAR_CITIES.keys())
        self.combo_city.addItems(city_names)
        cur_city = w_cfg.get("city", "成都")
        c_idx = self.combo_city.findText(cur_city)
        if c_idx >= 0:
            self.combo_city.setCurrentIndex(c_idx)
        weather_card.add_row("常用城市", "快速点选填充下方城市名称", self.combo_city)

        weather_card.add_divider()

        self.edit_city = QLineEdit(cur_city)
        self.edit_city.setPlaceholderText("例如：成都、绵阳、杭州、深圳…")
        self.edit_city.setFixedWidth(200)
        self.combo_city.currentTextChanged.connect(self.edit_city.setText)
        weather_card.add_row("城市名称", "输入任意城市名称，保存后生效", self.edit_city)

        weather_card.add_divider()

        self.combo_district = QComboBox()
        self.combo_district.setFixedWidth(200)
        self.combo_district.addItem("市级（不细分）", None)
        self.combo_district.setEnabled(False)
        weather_card.add_row("所属区县", "细化到区县以获取更准确的实况气温", self.combo_district)
        self.districts_loaded.connect(self._on_districts_loaded)
        self._reload_districts(cur_city, w_cfg.get("region", ""), w_cfg.get("district", ""))
        self.combo_city.currentTextChanged.connect(self._on_city_changed_for_districts)

        weather_card.add_divider()

        self.edit_place = QLineEdit()
        self.edit_place.setPlaceholderText("街道/园区，如 华阳街道")
        self.edit_place.setFixedWidth(136)
        btn_search_place = QPushButton("搜索", weather_card)
        btn_search_place.setFixedWidth(56)
        btn_search_place.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_search_place.clicked.connect(self._on_search_place)
        place_row = QWidget()
        place_layout = QHBoxLayout(place_row)
        place_layout.setContentsMargins(0, 0, 0, 0)
        place_layout.setSpacing(8)
        place_layout.addWidget(self.edit_place)
        place_layout.addWidget(btn_search_place)
        weather_card.add_row("精确地点", "搜索具体街道或社区坐标", place_row)

        self.combo_place = QComboBox()
        self.combo_place.setFixedWidth(200)
        self.combo_place.addItem("先搜索再选择", None)
        self.combo_place.setEnabled(False)
        weather_card.add_row("候选地点", "", self.combo_place)
        self.places_found.connect(self._on_places_found)

        weather_card.add_divider()

        self.rb_auto_loc.toggled.connect(
            lambda checked: (
                self.combo_city.setEnabled(not checked),
                self.edit_city.setEnabled(not checked),
            )
        )
        self.combo_city.setEnabled(not is_auto)
        self.edit_city.setEnabled(not is_auto)

        btn_refresh_weather = QPushButton("立即刷新", weather_card)
        btn_refresh_weather.setFixedWidth(108)
        btn_refresh_weather.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh_weather.clicked.connect(self._on_refresh_weather_clicked)
        weather_card.add_row("天气数据", "保存当前地区并立即拉取最新天气", btn_refresh_weather)

        box.addWidget(weather_card)

        # 2. 数字时钟与日历小组件
        c_cfg = self.config_manager.get_clock_config()
        clock_card = SettingsCard("数字时钟", page)

        self.chk_clock_enable = ToggleSwitch()
        self.chk_clock_enable.setChecked(c_cfg.get("enabled", False))
        clock_card.header_action_box.addWidget(self.chk_clock_enable)

        self.combo_clock_size = QComboBox()
        self.combo_clock_size.addItem("2 × 2 方卡", "2x2")
        self.combo_clock_size.addItem("2 × 1 胶囊", "2x1")
        self.combo_clock_size.setCurrentIndex(1 if c_cfg.get("size") == "2x1" else 0)
        clock_card.add_row("卡片尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_clock_size)

        clock_card.add_divider()

        btn_align_clock = QPushButton("对齐网格", clock_card)
        btn_align_clock.setFixedWidth(108)
        btn_align_clock.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_align_clock.clicked.connect(self._on_align_clock_clicked)
        clock_card.add_row("位置校准", "与相邻卡片顶边平齐对齐", btn_align_clock)

        box.addWidget(clock_card)

        # 3. 系统硬件监控
        m_cfg = self.config_manager.get_monitor_config()
        monitor_card = SettingsCard("系统监控", page)

        self.chk_monitor_enable = ToggleSwitch()
        self.chk_monitor_enable.setChecked(m_cfg.get("enabled", False))
        monitor_card.header_action_box.addWidget(self.chk_monitor_enable)

        self.drives_all = []
        try:
            import psutil

            for part in psutil.disk_partitions(all=False):
                dev = (part.device or "").rstrip("\\")
                if dev and "cdrom" not in (part.opts or "").lower():
                    self.drives_all.append(dev + "\\")
        except Exception:
            pass
        if not self.drives_all:
            self.drives_all = ["C:\\"]
        cur = m_cfg.get("disk_drives") or [m_cfg.get("disk_drive") or "C:\\"]
        for d in cur:
            if d not in self.drives_all:
                self.drives_all.insert(0, d)
        self.selected_drives = [d for d in cur if d]

        self.drive_list = QListWidget()
        self.drive_list.setObjectName("driveList")
        self.drive_list.setFixedSize(200, 92)
        for d in self.drives_all:
            item = QListWidgetItem(d.rstrip("\\") + " 盘")
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if d in self.selected_drives else Qt.CheckState.Unchecked
            )
            self.drive_list.addItem(item)
        self.drive_list.itemChanged.connect(self._on_drive_item_changed)
        monitor_card.add_row("监控磁盘", "勾选要同时展示的磁盘分区", self.drive_list)

        box.addWidget(monitor_card)

        # 4. 桌面便签
        memo_cfg = self.config_manager.get_memo_config()
        memo_card = SettingsCard("便签与待办", page)

        self.chk_memo_enable = ToggleSwitch()
        self.chk_memo_enable.setChecked(memo_cfg.get("enabled", False))
        memo_card.header_action_box.addWidget(self.chk_memo_enable)

        btn_align_memo = QPushButton("对齐网格", memo_card)
        btn_align_memo.setFixedWidth(108)
        btn_align_memo.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_align_memo.clicked.connect(self._on_align_memo_clicked)
        memo_card.add_row("位置校准", "与相邻卡片顶边平齐对齐", btn_align_memo)

        memo_card.add_divider()

        btn_clear_memo = QPushButton("清空内容", memo_card)
        btn_clear_memo.setFixedWidth(108)
        btn_clear_memo.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_memo.clicked.connect(self._on_clear_memo_clicked)
        memo_card.add_row("清空便签", "清除桌面便签中的文本草稿", btn_clear_memo)

        box.addWidget(memo_card)

        # 5. 媒体播放组件
        media_cfg = self.config_manager.get_media_config()
        media_card = SettingsCard("媒体播放", page)

        self.chk_media_enable = ToggleSwitch()
        self.chk_media_enable.setChecked(media_cfg.get("enabled", False))
        media_card.header_action_box.addWidget(self.chk_media_enable)

        self.combo_media_size = QComboBox()
        self.combo_media_size.addItem("2 × 2 方卡", "2x2")
        self.combo_media_size.addItem("2 × 1 胶囊", "2x1")
        self.combo_media_size.setCurrentIndex(1 if media_cfg.get("size") == "2x1" else 0)
        media_card.add_row("卡片尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_media_size)

        media_card.add_divider()

        self.chk_media_lyrics = ToggleSwitch()
        self.chk_media_lyrics.setChecked(media_cfg.get("show_lyrics", True))
        media_card.add_row("同步歌词", "2×2 模式下自动匹配并滚动歌词", self.chk_media_lyrics)

        media_card.add_divider()

        btn_align_media = QPushButton("对齐网格", media_card)
        btn_align_media.setFixedWidth(108)
        btn_align_media.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_align_media.clicked.connect(self._on_align_media_clicked)
        media_card.add_row("位置校准", "与相邻卡片顶边平齐对齐", btn_align_media)

        box.addWidget(media_card)

        # 6. 全盘搜索小组件
        ev_cfg = self.config_manager.get_everything_config()
        ev_card = SettingsCard("桌面搜索框", page)

        self.chk_everything_widget = ToggleSwitch()
        self.chk_everything_widget.setChecked(ev_cfg.get("enabled", True))
        ev_card.header_action_box.addWidget(self.chk_everything_widget)

        self.combo_everything_size = QComboBox()
        self.combo_everything_size.addItem("2 × 2 方卡", "2x2")
        self.combo_everything_size.addItem("2 × 1 胶囊", "2x1")
        self.combo_everything_size.setCurrentIndex(1 if ev_cfg.get("size") == "2x1" else 0)
        ev_card.add_row("卡片尺寸", "2×2 结果面板 / 2×1 搜索胶囊", self.combo_everything_size)
        box.addWidget(ev_card)

        box.addStretch()
        return page

    # =========================================================================
    # 事件与动作处理
    # =========================================================================
    def _update_folder_list_height(self):
        cnt = max(1, self.folder_list.count())
        self.folder_list.setFixedHeight(cnt * 38 + 10)

    def _on_folder_selection_changed(self, row: int):
        if 0 <= row < self.folder_list.count():
            name = self.folder_list.item(row).text()
            if hasattr(self, "lbl_selected_cat"):
                self.lbl_selected_cat.setText(f"当前选中：「{name}」 · 双击可重命名")

    def _on_add_folder(self):
        name, ok = QInputDialog.getText(self, "新建分类", "请输入新分类名称：")
        if ok and name.strip():
            self.config_manager.add_folder(name.strip())
            self.folder_list.addItem(name.strip())
            self._update_folder_list_height()
            self.folder_list.setCurrentRow(self.folder_list.count() - 1)

    def _on_rename_folder(self):
        row = self.folder_list.currentRow()
        if row < 0:
            return
        old_name = self.folder_list.item(row).text()
        new_name, ok = QInputDialog.getText(self, "重命名分类", "请输入新名称：", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            self.config_manager.rename_folder(row, new_name.strip())
            self.folder_list.item(row).setText(new_name.strip())
            if hasattr(self, "lbl_selected_cat"):
                self.lbl_selected_cat.setText(f"已更名为：「{new_name.strip()}」")

    def _on_delete_folder(self):
        row = self.folder_list.currentRow()
        if row < 0:
            return
        if self.folder_list.count() <= 1:
            QMessageBox.warning(self, "提示", "至少需要保留一个分类。")
            return
        f_name = self.folder_list.item(row).text()
        reply = QMessageBox.question(
            self,
            "解散分类确认",
            f"确定要解散分类「{f_name}」吗？\n\n分类内的应用与快捷方式将原样放回桌面。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            storage_mgr = (
                getattr(self.window_manager, "shared_storage_engine", None)
                if self.window_manager
                else None
            )
            restored_count = 0
            if storage_mgr:
                restored_count = storage_mgr.dissolve_folder_to_desktop(f_name)

            if self.config_manager.delete_folder(row):
                self.folder_list.takeItem(row)
                self._update_folder_list_height()
                if self.window_manager:
                    remaining_ids = {
                        inst.get("instance_id")
                        for inst in self.config_manager.get_instances()
                        if isinstance(inst, dict)
                    }
                    fallback_name = self.config_manager.get_active_folder_name()
                    for inst_id, w in list(self.window_manager.windows.items()):
                        if inst_id not in remaining_ids:
                            try:
                                if getattr(w, "desktop_watchdog", None) is not None:
                                    w.desktop_watchdog.stop()
                                w.close()
                            except Exception:
                                pass
                            self.window_manager.windows.pop(inst_id, None)
                        elif getattr(w, "bound_folder_name", None) == f_name:
                            w.bound_folder_name = fallback_name
                            w.active_page = 0
                            w.rebuild_grid()
                    if hasattr(self, "inst_count_row"):
                        desc = self.inst_count_row.findChild(QLabel, "rowDesc")
                        if desc is not None:
                            desc.setText(f"{len(self.config_manager.get_instances())} 个卡片")
                QMessageBox.information(
                    self,
                    "解散完成",
                    f"已解散分类「{f_name}」，共 {restored_count} 个项目已放回桌面。",
                )

    def _on_add_instance(self):
        if self.window_manager:
            self.window_manager.prompt_add_instance()
            count = len(self.config_manager.get_instances())
            desc = self.inst_count_row.findChild(QLabel, "rowDesc")
            if desc:
                desc.setText(f"{count} 个卡片")

    def _on_tidy_desktop(self):
        if self.window_manager:
            self.window_manager.action_tidy_desktop(auto_categorize=True)
            QMessageBox.information(self, "整理完成", "已完成桌面快捷方式归类。")

    def _on_toggle_lock_all(self):
        if self.window_manager:
            self.window_manager.toggle_lock_all()
            locked = bool(self.config_manager.get("locked", False))
            if hasattr(self, "btn_lock_all"):
                self.btn_lock_all.setText("解锁卡片" if locked else "锁定卡片")

    def _on_browse_storage(self):
        cur_p = self.config_manager.get_managed_storage_root()
        dir_path = QFileDialog.getExistingDirectory(self, "选择 Stacky 存储目录", cur_p)
        if dir_path:
            self.config_manager.set_managed_storage_root(dir_path)
            if hasattr(self, "lbl_storage_path") and self.lbl_storage_path:
                self.lbl_storage_path.setText(dir_path)

    def _on_open_storage(self):
        path = self.config_manager.get_managed_storage_root()
        if path and os.path.exists(path):
            os.startfile(path)

    def _on_open_migrator(self):
        if self.window_manager:
            self.window_manager.open_legacy_migrator(parent=self)

    def _on_create_dock_shortcut(self):
        if self.window_manager:
            self.window_manager.action_create_dock_shortcut()
            QMessageBox.information(self, "生成完成", "已在桌面生成 Stacky 快捷方式，可直接拖入任务栏或 Dock。")

    def _on_align_clock_clicked(self):
        if self.window_manager and getattr(self.window_manager, "clock_window", None):
            self.window_manager.clock_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "时钟小组件已与相邻卡片对齐。")
        else:
            QMessageBox.information(self, "提示", "请先开启时钟小组件并点击「应用」。")

    def _on_align_media_clicked(self):
        if self.window_manager and getattr(self.window_manager, "media_window", None):
            self.window_manager.media_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "媒体小组件已与相邻卡片对齐。")
        else:
            QMessageBox.information(self, "提示", "请先开启媒体小组件并点击「应用」。")

    def _on_align_memo_clicked(self):
        if self.window_manager and getattr(self.window_manager, "memo_window", None):
            self.window_manager.memo_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "便签小组件已与相邻卡片对齐。")
        else:
            QMessageBox.information(self, "提示", "请先开启便签小组件并点击「应用」。")

    def _on_clear_memo_clicked(self):
        data_dir = getattr(_stacky_paths, "get_user_data_dir", lambda: ROOT)()
        for d in {data_dir, ROOT}:
            memo_file = os.path.join(d, ".desktop_memo.txt")
            if os.path.exists(memo_file) or d == data_dir:
                try:
                    with open(memo_file, "w", encoding="utf-8") as f:
                        f.write("")
                except Exception:
                    pass
        if self.window_manager and getattr(self.window_manager, "memo_window", None):
            self.window_manager.memo_window.edit_memo.clear()
        QMessageBox.information(self, "提示", "桌面便签内容已清空。")

    def _on_refresh_weather_clicked(self):
        self._save_weather_settings(explicit_refresh=True)
        ws = get_weather_service()
        ws.fetch_async(force=True)
        QMessageBox.information(self, "提示", "天气地区已保存并正在刷新最新数据。")

    def _on_city_changed_for_districts(self, city: str):
        region = POPULAR_CITIES.get(city, {}).get("region", "")
        self._reload_districts(city, region, "")

    def _reload_districts(self, city: str, region: str, select: str = ""):
        if not hasattr(self, "combo_district"):
            return
        self._district_select = select
        self.combo_district.blockSignals(True)
        self.combo_district.clear()
        self.combo_district.addItem("加载中…", None)
        self.combo_district.setEnabled(False)
        self.combo_district.blockSignals(False)
        if not city:
            return

        def work():
            try:
                from weather_service import fetch_city_districts, resolve_city_coordinates

                city_name, region_name = city, region
                if not region_name:
                    resolved = resolve_city_coordinates(city)
                    if resolved:
                        city_name = resolved.get("city", city)
                        region_name = resolved.get("region", "")
                items = fetch_city_districts(city_name, region_name)
            except Exception:
                items = []
            self.districts_loaded.emit(items)

        threading.Thread(target=work, daemon=True).start()

    def _on_ip_relocate(self):
        from weather_service import get_weather_service

        ws = get_weather_service()
        geo = ws.resolve_ip_location(force=True)
        if not geo:
            QMessageBox.information(self, "定位失败", "无法获取当前 IP 归属地，请检查网络后重试。")
            return
        self.config_manager.set_weather_location(
            city=geo["city"],
            region=geo["region"],
            lat=geo["lat"],
            lon=geo["lon"],
            auto_location=True,
            district="",
        )
        self.rb_auto_loc.setChecked(True)
        self.edit_city.setText(geo["city"])
        ws.set_custom_location(
            city=geo["city"],
            region=geo["region"],
            lat=geo["lat"],
            lon=geo["lon"],
            auto_location=True,
        )
        ws.fetch_async(force=True)
        QMessageBox.information(self, "已定位", f"当前位置：{geo['city']} · {geo['region']}")

    def _on_search_place(self):
        query = self.edit_place.text().strip()
        if not query:
            return
        city = self.edit_city.text().strip() or self.combo_city.currentText()
        region = POPULAR_CITIES.get(city, {}).get("region", "")
        self.combo_place.clear()
        self.combo_place.addItem("搜索中…", None)
        self.combo_place.setEnabled(False)

        def work():
            try:
                from weather_service import search_places

                items = search_places(query, city, region)
            except Exception:
                items = []
            self.places_found.emit(items)

        threading.Thread(target=work, daemon=True).start()

    def _on_places_found(self, items):
        if not hasattr(self, "combo_place"):
            return
        self.combo_place.clear()
        if not items:
            self.combo_place.addItem("未找到匹配地点", None)
            self.combo_place.setEnabled(False)
            return
        for item in items:
            label = (
                f"{item.get('name', '')}（{item.get('hint', '')}）"
                if item.get("hint")
                else item.get("name", "")
            )
            self.combo_place.addItem(label, item)
        self.combo_place.setEnabled(True)

    def _on_districts_loaded(self, items):
        if not hasattr(self, "combo_district"):
            return
        self.combo_district.blockSignals(True)
        self.combo_district.clear()
        self.combo_district.addItem("市级（不细分）", None)
        for item in items:
            self.combo_district.addItem(item.get("name", ""), item)
        self.combo_district.setEnabled(bool(items))
        if not items:
            self.combo_district.clear()
            self.combo_district.addItem("未查到该城市，可改填所属地级市", None)
        target = getattr(self, "_district_select", "")
        if target:
            for i in range(self.combo_district.count()):
                data = self.combo_district.itemData(i)
                if isinstance(data, dict) and data.get("name") == target:
                    self.combo_district.setCurrentIndex(i)
                    break
        self.combo_district.blockSignals(False)

    def _save_weather_settings(self, explicit_refresh: bool = False):
        w_cfg = self.config_manager.get_weather_config()
        w_cfg["enabled"] = self.chk_weather_enable.isChecked()
        w_cfg["size"] = self.combo_weather_size.currentData()
        self.config_manager.set_weather_config(w_cfg)

        is_auto = self.rb_auto_loc.isChecked()
        chosen = self.edit_city.text().strip() or self.combo_city.currentText()

        res = None
        clean_chosen = chosen.rstrip("市").rstrip("区").rstrip("县")
        saved_city = str(w_cfg.get("city", "") or "").strip()
        if (
            saved_city
            and clean_chosen == saved_city.rstrip("市").rstrip("区").rstrip("县")
            and w_cfg.get("lat") is not None
            and w_cfg.get("lon") is not None
            and clean_chosen not in POPULAR_CITIES
        ):
            res = {
                "city": saved_city,
                "region": w_cfg.get("region", ""),
                "lat": float(w_cfg["lat"]),
                "lon": float(w_cfg["lon"]),
            }
        else:
            res = resolve_city_coordinates(chosen)

        if not res:
            if not is_auto or explicit_refresh:
                QMessageBox.information(
                    self,
                    "未找到该城市",
                    f"无法定位「{chosen}」。\n请填写所属地级市（如 苏州），再在区县列表中细分。",
                )
            return

        c_city = res["city"]
        c_reg = res.get("region", "")
        lat = res["lat"]
        lon = res["lon"]

        district = ""
        picked_place = self.combo_place.currentData() if hasattr(self, "combo_place") else None
        if isinstance(picked_place, dict) and picked_place.get("name"):
            c_city = picked_place.get("city") or c_city
            c_reg = picked_place.get("region") or c_reg
            district = picked_place.get("district") or picked_place["name"]
            lat = picked_place.get("lat", lat)
            lon = picked_place.get("lon", lon)
        else:
            picked = self.combo_district.currentData() if hasattr(self, "combo_district") else None
            if isinstance(picked, dict) and picked.get("name"):
                district = picked["name"]
                lat = picked.get("lat", lat)
                lon = picked.get("lon", lon)

        self.config_manager.set_weather_location(
            city=c_city,
            region=c_reg,
            lat=lat,
            lon=lon,
            auto_location=is_auto,
            district=district,
        )

        w_cfg = self.config_manager.get_weather_config()
        w_cfg["enabled"] = self.chk_weather_enable.isChecked()
        w_cfg["size"] = self.combo_weather_size.currentData()
        self.config_manager.set_weather_config(w_cfg)

        ws = get_weather_service()
        ws.set_custom_location(
            city=c_city,
            region=c_reg,
            lat=lat,
            lon=lon,
            auto_location=is_auto,
            district=district,
        )

    def save_and_apply(self):
        """保存所有标签页配置并即时向窗口管理器热生效。"""
        # 1. 打开方式
        if self.rb_single.isChecked():
            mode = "single"
        elif self.rb_hybrid.isChecked():
            mode = "hybrid"
        else:
            mode = "double"
        self.config_manager.set_open_app_mode(mode)

        # 2. 呼出目标与启动后行为
        self.config_manager.set("summon_target", self.combo_summon_target.currentData())
        if hasattr(self, "combo_hotkey"):
            self.config_manager.set("hotkey", self.combo_hotkey.currentData())
        if hasattr(self, "combo_post_launch"):
            self.config_manager.set_post_launch_action(self.combo_post_launch.currentData())

        if hasattr(self, "chk_everything"):
            self.config_manager.set_everything_enabled(self.chk_everything.isChecked())

        # 3. 开机自启
        try:
            set_autostart(self.chk_autostart.isChecked())
        except Exception as exc:
            print("Failed to update autostart setting:", exc)

        # 4. 外观主题
        theme = self.combo_theme.currentData() or self.theme
        self.config_manager.set("theme", theme)
        self._reloading_ui = True
        try:
            self.apply_theme(theme)
        finally:
            self._reloading_ui = False
        self._initial_theme = theme

        if hasattr(self, "combo_display_mode"):
            self.config_manager.set_app_display_mode(
                self.combo_display_mode.currentData(), auto_save=False
            )
        if hasattr(self, "chk_admin_badge"):
            self.config_manager.set_admin_badge_enabled(
                self.chk_admin_badge.isChecked(), auto_save=False
            )

        # 5. 网格规格
        grid_pair = self.combo_grid.currentData()
        r, c = grid_pair if (isinstance(grid_pair, (tuple, list)) and len(grid_pair) == 2) else (0, 0)
        grid_dirty = getattr(self, "_grid_dirty", False) and bool(r) and bool(c)
        if grid_dirty:
            self.config_manager.set("grid_rows", r, auto_save=False)
            self.config_manager.set("grid_cols", c, auto_save=False)
            for inst in self.config_manager.get_instances():
                inst.pop("card_width", None)
                inst.pop("card_height", None)
                self.config_manager.update_instance(
                    inst.get("instance_id"), {"rows": r, "cols": c}, auto_save=False
                )
        self.config_manager.save()
        if grid_dirty:
            for win in list(getattr(self.window_manager, "windows", {}).values()):
                if hasattr(win, "apply_grid_shape"):
                    win.apply_grid_shape(r, c, toast=False)
            self._grid_dirty = False

        # 6. 不透明度
        op = self.slider_op.value() / 100.0
        self.config_manager.set("opacity", op)
        self._initial_opacity = op
        if hasattr(self, "chk_glass_blur"):
            self.config_manager.set("glass_blur_enabled", self.chk_glass_blur.isChecked())

        # 7. 桌面层级
        layer = self.combo_layer.currentData()
        self.config_manager.set("layer_mode", layer)

        if hasattr(self, "chk_double_click_hide"):
            is_zen_on = self.chk_double_click_hide.isChecked()
            self.config_manager.set_double_click_desktop_hide_enabled(is_zen_on)
            if self.window_manager and hasattr(self.window_manager, "desktop_blank_handler"):
                self.window_manager.desktop_blank_handler.set_enabled(is_zen_on)

        # 8. 桌面小组件保存
        self._save_weather_settings()

        c_cfg = self.config_manager.get_clock_config()
        c_cfg["enabled"] = self.chk_clock_enable.isChecked()
        c_cfg["size"] = self.combo_clock_size.currentData()
        self.config_manager.set_clock_config(c_cfg)

        m_cfg = self.config_manager.get_monitor_config()
        m_cfg["enabled"] = self.chk_monitor_enable.isChecked()
        m_cfg["disk_drives"] = list(self.selected_drives)
        m_cfg["disk_drive"] = self.selected_drives[0] if self.selected_drives else "C:\\"
        self.config_manager.set_monitor_config(m_cfg)

        memo_cfg = self.config_manager.get_memo_config()
        memo_cfg["enabled"] = self.chk_memo_enable.isChecked()
        self.config_manager.set_memo_config(memo_cfg)

        if hasattr(self, "chk_media_enable"):
            media_cfg = self.config_manager.get_media_config()
            media_cfg["enabled"] = self.chk_media_enable.isChecked()
            media_cfg["size"] = self.combo_media_size.currentData()
            media_cfg["show_lyrics"] = self.chk_media_lyrics.isChecked()
            self.config_manager.set_media_config(media_cfg)

        if hasattr(self, "chk_everything_widget"):
            ev_cfg = self.config_manager.get_everything_config()
            ev_cfg["enabled"] = self.chk_everything_widget.isChecked()
            ev_cfg["size"] = self.combo_everything_size.currentData()
            self.config_manager.set_everything_config(ev_cfg)

        if hasattr(self, "chk_file_shelf"):
            shelf_cfg = self.config_manager.get_file_shelf_config()
            shelf_cfg["enabled"] = self.chk_file_shelf.isChecked()
            if hasattr(self, "combo_shelf_edge"):
                shelf_cfg["edge"] = self.combo_shelf_edge.currentData()
            if hasattr(self, "chk_shelf_auto_collapse"):
                shelf_cfg["auto_collapse"] = self.chk_shelf_auto_collapse.isChecked()
            if hasattr(self, "chk_shelf_hide_fullscreen"):
                shelf_cfg["hide_in_fullscreen"] = self.chk_shelf_hide_fullscreen.isChecked()
            self.config_manager.set_file_shelf_config(shelf_cfg)
            self._initial_file_shelf_enabled = bool(shelf_cfg["enabled"])

        # 9. 先标记当前窗口状态已持久化（避免任何运行态同步异常误触发 closeEvent 回滚），再广播热生效
        self._saved_once = True
        self._accepted_and_saved = True
        self._rolled_back = False
        self._broadcast_runtime_sync()

    def _broadcast_runtime_sync(self):
        """把当前 ConfigManager 中的主题、不透明度、层级与各小组件配置热同步到桌面。"""
        if not self.window_manager:
            return
        theme = str(self.config_manager.get("theme", "dark") or "dark").lower()
        op = float(self.config_manager.get("opacity", 0.88))
        radius = int(self.config_manager.get("border_radius", 26))
        c_cfg = self.config_manager.get_clock_config()
        m_cfg = self.config_manager.get_monitor_config()
        memo_cfg = self.config_manager.get_memo_config()
        media_cfg = self.config_manager.get_media_config()

        for win in list(getattr(self.window_manager, "windows", {}).values()):
            try:
                if hasattr(win, "theme"):
                    win.theme = theme
                if hasattr(win, "card") and win.card is not None:
                    win.card.set_card_style(
                        radius,
                        op,
                        theme=theme,
                    )
                if hasattr(win, "rebuild_grid"):
                    win.rebuild_grid()
                if hasattr(win, "apply_layer_mode"):
                    win.apply_layer_mode()
                if hasattr(win, "unregister_global_hotkey"):
                    win.unregister_global_hotkey()
                if hasattr(win, "register_global_hotkey"):
                    win.register_global_hotkey()
            except Exception as exc:
                print("Failed to sync folder window runtime state:", exc)

        for attr in (
            "weather_window",
            "clock_window",
            "monitor_window",
            "memo_window",
            "media_window",
            "everything_window",
            "file_shelf_window",
        ):
            widget_win = getattr(self.window_manager, attr, None)
            if widget_win is not None:
                try:
                    if hasattr(widget_win, "set_theme"):
                        widget_win.set_theme(theme)
                    if hasattr(widget_win, "set_glass_params"):
                        widget_win.set_glass_params(radius=radius, opacity=op, theme=theme)
                except Exception as exc:
                    print(f"Failed to sync glass params on {attr}:", exc)

        if hasattr(self.window_manager, "weather_window") and self.window_manager.weather_window:
            w_cfg = self.config_manager.get_weather_config()
            if not w_cfg.get("enabled", True):
                self.window_manager.weather_window.close_widget()
                self.window_manager.weather_window = None
            else:
                self.window_manager.weather_window._apply_geometry_mode(w_cfg.get("size", "2x2"))
                self.window_manager.weather_window.set_theme(theme)
                self.window_manager.weather_window.refresh_weather()
        elif self.config_manager.get_weather_config().get("enabled", True) and hasattr(
            self.window_manager, "init_weather_widget"
        ):
            self.window_manager.init_weather_widget(self.config_manager.get_weather_config())

        if hasattr(self.window_manager, "clock_window") and self.window_manager.clock_window:
            if not c_cfg.get("enabled", False):
                self.window_manager.clock_window.close_widget()
                self.window_manager.clock_window = None
            else:
                self.window_manager.clock_window.set_size_mode(c_cfg.get("size", "2x2"))
        elif c_cfg.get("enabled", False) and hasattr(self.window_manager, "init_clock_widget"):
            self.window_manager.init_clock_widget(c_cfg)

        if hasattr(self.window_manager, "monitor_window") and self.window_manager.monitor_window:
            if not m_cfg.get("enabled", False):
                self.window_manager.monitor_window.close_widget()
                self.window_manager.monitor_window = None
            else:
                self.window_manager.monitor_window.apply_drives(
                    m_cfg.get("disk_drives") or [m_cfg.get("disk_drive") or "C:\\"]
                )
        elif m_cfg.get("enabled", False) and hasattr(self.window_manager, "init_monitor_widget"):
            self.window_manager.init_monitor_widget(m_cfg)

        if hasattr(self.window_manager, "memo_window") and self.window_manager.memo_window:
            if not memo_cfg.get("enabled", False):
                self.window_manager.memo_window.close_widget()
                self.window_manager.memo_window = None
        elif memo_cfg.get("enabled", False) and hasattr(self.window_manager, "init_memo_widget"):
            self.window_manager.init_memo_widget(memo_cfg)

        if hasattr(self, "chk_media_enable"):
            if hasattr(self.window_manager, "media_window") and self.window_manager.media_window:
                if not media_cfg.get("enabled", False):
                    self.window_manager.media_window.close_widget()
                    self.window_manager.media_window = None
                else:
                    self.window_manager.media_window.set_size_mode(media_cfg.get("size", "2x2"))
                    self.window_manager.media_window.set_theme(theme)
                    self.window_manager.media_window.set_show_lyrics(
                        media_cfg.get("show_lyrics", True)
                    )
            elif media_cfg.get("enabled", False) and hasattr(self.window_manager, "init_media_widget"):
                self.window_manager.init_media_widget(media_cfg)

        if hasattr(self.window_manager, "init_file_shelf"):
            s_cfg = self.config_manager.get_file_shelf_config()
            if s_cfg.get("enabled", True):
                if not getattr(self.window_manager, "file_shelf_window", None):
                    self.window_manager.init_file_shelf(s_cfg)
                else:
                    self.window_manager.file_shelf_window.edge = s_cfg.get("edge", "right")
                    self.window_manager.file_shelf_window.auto_collapse = s_cfg.get(
                        "auto_collapse", True
                    )
                    self.window_manager.file_shelf_window.hide_in_fullscreen = s_cfg.get(
                        "hide_in_fullscreen", True
                    )
                    self.window_manager.file_shelf_window.update_geometry_for_state(
                        animate=False
                    )
                    self.window_manager.file_shelf_window.check_fullscreen_suppression()
                    if not getattr(
                        self.window_manager.file_shelf_window, "_game_suppressed", False
                    ):
                        self.window_manager.file_shelf_window.show()
            else:
                if getattr(self.window_manager, "file_shelf_window", None):
                    self.window_manager.file_shelf_window.close()
                    self.window_manager.file_shelf_window = None

        ev_cfg = self.config_manager.get_everything_config()
        if hasattr(self.window_manager, "everything_window"):
            if not ev_cfg.get("enabled", True):
                if self.window_manager.everything_window:
                    self.window_manager.everything_window.close_widget()
                    self.window_manager.everything_window = None
            elif self.window_manager.everything_window is None and hasattr(
                self.window_manager, "init_everything_widget"
            ):
                self.window_manager.init_everything_widget(ev_cfg)
            elif self.window_manager.everything_window is not None:
                self.window_manager.everything_window.set_size_mode(ev_cfg.get("size", "2x2"))

        for win in list(self.window_manager.windows.values()):
            if hasattr(win, "apply_blur_effect"):
                win.apply_blur_effect()
        if hasattr(self.window_manager, "bind_all_widget_resizes"):
            self.window_manager.bind_all_widget_resizes()


import sys as _sys  # noqa: E402
_sys.modules.setdefault("settings_dialog", _sys.modules[__name__])
_sys.modules.setdefault("stacky.ui.settings_dialog", _sys.modules[__name__])
