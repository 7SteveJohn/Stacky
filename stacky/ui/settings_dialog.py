"""Stacky 设置窗口：分类、交互、外观与桌面组件配置。"""
from stacky.paths import ROOT

import os
import sys
import subprocess
from typing import Optional, Dict, Any, List

import threading
from PySide6.QtCore import Qt, QEvent, Signal, QRect, QRectF, QSize, QPointF
from PySide6.QtGui import (QColor, QIcon, QPalette, QPainter, QPen, QPixmap,
                           QFont, QPainterPath)
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget, QRadioButton,
    QCheckBox, QComboBox, QLineEdit, QSlider, QFrame, QScrollArea,
    QFileDialog, QInputDialog, QMessageBox, QApplication,
    QStyledItemDelegate, QStyle
)

from config_manager import ConfigManager
from weather_service import get_weather_service, POPULAR_CITIES, resolve_city_coordinates
from autostart_utils import is_autostart_enabled, set_autostart
from icon_utils import get_ui_chevron_icon_path, get_default_icon_cache_dir
from ui_kit import FlatCheckBox, FlatRadioButton

CACHE_DIR = get_default_icon_cache_dir()


def _nav_icon(kind: str) -> QIcon:
    """侧栏图标全部自绘，避免字符图标在不同字体下变成空心方框。"""
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(QColor("#a9aeb5"), 1.5, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "folder":
        p.drawRoundedRect(QRectF(2, 5, 16, 12), 2, 2)
        p.drawLine(QPointF(2, 8.5), QPointF(8.5, 8.5))
    elif kind == "interaction":
        arrow = QPainterPath()
        arrow.moveTo(4, 2)
        arrow.lineTo(4, 16)
        arrow.lineTo(8.5, 12)
        arrow.lineTo(11.5, 18)
        arrow.lineTo(14, 16.8)
        arrow.lineTo(11, 10.8)
        arrow.lineTo(16.5, 10.8)
        arrow.closeSubpath()
        p.drawPath(arrow)
    elif kind == "appearance":
        p.drawEllipse(QRectF(2.5, 2.5, 15, 15))
        p.drawEllipse(QRectF(6, 6, 4, 4))
        p.drawLine(QPointF(12, 5), QPointF(15.5, 5))
        p.drawLine(QPointF(12, 10), QPointF(16, 12))
    elif kind == "widgets":
        for x, y in ((2.5, 2.5), (11, 2.5), (2.5, 11), (11, 11)):
            p.drawRoundedRect(QRectF(x, y, 6.5, 6.5), 1.5, 1.5)
    p.end()
    return QIcon(pm)


class NavItemDelegate(QStyledItemDelegate):
    """Raycast / Linear 式侧栏：分组标题 + 图标 + 主标题 + 描述副行。"""

    GROUP_TITLES = {0: "桌面", 2: "个性化"}

    def paint(self, painter, option, index):
        row = index.row()
        r = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if selected:
            top_bg = r.top() + (18 if row in self.GROUP_TITLES else 2)
            bg_h = r.bottom() - 2 - top_bg
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 22))
            painter.drawRoundedRect(QRectF(r.left() + 2, top_bg, r.width() - 4, bg_h), 7, 7)
            painter.setBrush(QColor("#60a5fa"))
            painter.drawRoundedRect(QRectF(r.left() + 3, top_bg + 10, 3, bg_h - 20), 1.5, 1.5)

        top = r.top()
        if row in self.GROUP_TITLES:
            f = QFont(painter.font())
            f.setPointSize(8)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor("#6f747c"))
            painter.drawText(QRectF(r.left() + 12, top + 2, r.width() - 20, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self.GROUP_TITLES[row])
            top += 18

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            icon.paint(painter, QRect(int(r.left() + 12), int(top + 8), 20, 20))

        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        desc = index.data(Qt.ItemDataRole.UserRole) or ""
        f = QFont(painter.font())
        f.setPointSize(10)
        f.setBold(selected)
        painter.setFont(f)
        painter.setPen(QColor("#ffffff" if selected else "#d7d9dd"))
        painter.drawText(QRectF(r.left() + 40, top + 5, r.width() - 48, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(title))
        f.setPointSize(8)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor("#8b919b"))
        painter.drawText(QRectF(r.left() + 40, top + 23, r.width() - 48, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(desc))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, 64 if index.row() in self.GROUP_TITLES else 52)


class SmoothCategoryListWidget(QListWidget):
    """分类列表：滚轮事件不冒泡到外层滚动区。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def wheelEvent(self, event):
        # 滚轮事件留给列表自身，不冒泡到外层 QScrollArea
        super().wheelEvent(event)
        event.accept()


class SettingsCard(QFrame):
    """设置分组：标题、设置行与细分隔线。"""
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 4, 0, 14)
        self.box.setSpacing(8)
        if title:
            h_row = QHBoxLayout()
            h_row.setContentsMargins(0, 0, 0, 4)
            lbl = QLabel(title, self)
            lbl.setObjectName("cardHeaderTitle")
            h_row.addWidget(lbl)
            h_row.addStretch()
            self.header_action_box = h_row
            self.box.addLayout(h_row)

    def add_row(self, left_title: str, left_desc: str = "", right_widget: Optional[QWidget] = None) -> QWidget:
        """添加标准设置行：左侧标题与副标题描述，右侧对齐操作控件"""
        row = QWidget(self)
        r_layout = QHBoxLayout(row)
        r_layout.setContentsMargins(0, 4, 0, 4)
        r_layout.setSpacing(16)

        text_col = QVBoxLayout()
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
            # 固定宽度的控件区：所有行控件左边缘一致，内部右对齐
            holder = QWidget(row)
            holder.setFixedWidth(240)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(0)
            holder_layout.addStretch()
            holder_layout.addWidget(right_widget)
            r_layout.addWidget(holder)
        self.box.addWidget(row)
        return row

    def add_divider(self):
        """卡片内行间微细分割线"""
        div = QFrame(self)
        div.setFrameShape(QFrame.Shape.HLine)
        div.setObjectName("cardDivider")
        self.box.addWidget(div)


class SettingsDialog(QDialog):
    """设置对话框"""

    districts_loaded = Signal(list)
    places_found = Signal(list)

    def __init__(self, config_manager: ConfigManager, window_manager=None, initial_tab: int = 0, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        if window_manager is None:
            # 任意入口（含天气组件右键）打开设置时都自动接管窗口管理器，
            # 否则所有改动只写配置、不热生效，看起来像「改了没作用」。
            from PySide6.QtWidgets import QApplication
            for top in QApplication.topLevelWidgets():
                mgr = getattr(top, "manager", None)
                if mgr is not None and hasattr(mgr, "windows"):
                    window_manager = mgr
                    break
        self.window_manager = window_manager

        self.setWindowTitle("Stacky 灵叠设置")
        self.setFixedSize(880, 660)
        self.setWindowFlags(Qt.WindowType.Window |
                            Qt.WindowType.WindowCloseButtonHint |
                            Qt.WindowType.WindowMinimizeButtonHint)
        app_ico = os.path.join(CACHE_DIR, "app_icon.ico")
        if not os.path.exists(app_ico):
            app_ico = os.path.join(CACHE_DIR, "app_icon.png")
        if os.path.exists(app_ico):
            self.setWindowIcon(QIcon(app_ico))
        elif not QApplication.windowIcon().isNull():
            self.setWindowIcon(QApplication.windowIcon())

        self._init_styles()
        self._init_ui(initial_tab)

    def _init_styles(self):
        chev_normal = get_ui_chevron_icon_path(False)
        chev_hover = get_ui_chevron_icon_path(True)
        qss = """
            QDialog {
                background-color: #1b1c1f;
                color: #e6e7e9;
                font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
                font-size: 13px;
            }
            /* 左侧导航 */
            QFrame#sidebarFrame {
                background-color: #18191c;
                border: none;
                border-right: 1px solid #2a2c31;
            }
            QListWidget#navSidebar {
                background: transparent;
                border: none;
                outline: none;
                padding: 0px;
            }
            QListWidget#navSidebar::item {
                border: none;
                outline: none;
                padding: 0px;
                margin: 0px;
            }
            QLabel#sidebarBrand {
                color: #f0f1f2;
                font-size: 14px;
                font-weight: 700;
                padding-left: 6px;
            }
            QLabel#sidebarSub {
                color: #6f747c;
                font-size: 10px;
                padding-left: 6px;
            }
            QLineEdit#settingsSearch {
                background-color: #222429;
                border: 1px solid #33363d;
                border-radius: 8px;
                padding: 7px 10px;
                color: #e6e7e9;
                font-size: 11px;
                selection-background-color: #3b82f6;
            }
            QLineEdit#settingsSearch:focus { border-color: #60a5fa; }
            QLabel#sidebarVersion {
                color: #5f636b;
                font-size: 11px;
                padding-left: 16px;
                padding-bottom: 4px;
            }

            /* 右侧内容区标题 */
            QLabel#pageTitle {
                font-size: 17px;
                font-weight: 600;
                color: #f0f1f2;
                margin-bottom: 6px;
            }
            QLabel#pageSub {
                font-size: 11px;
                color: #7d818b;
                margin-bottom: 4px;
            }

            /* 分组卡片：无底色、无描边，仅分组标题与细分隔线 */
            QFrame#settingsCard {
                background-color: transparent;
                border: none;
                border-bottom: 1px solid #2a2c31;
                border-radius: 0px;
            }
            QLabel#cardHeaderTitle {
                font-size: 13px;
                font-weight: 600;
                color: #d6d9dd;
            }
            QLabel#rowTitle {
                font-size: 13px;
                font-weight: 400;
                color: #e6e7e9;
            }
            QLabel#rowDesc {
                font-size: 11px;
                color: #8b909a;
            }
            QFrame#cardDivider {
                border: none;
                background-color: #26282c;
                height: 1px;
                margin: 2px 0px;
            }

            /* 输入框与下拉选择框 */
            QLineEdit, QComboBox {
                background-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                padding: 6px 30px 6px 12px;
                font-size: 13px;
                min-height: 20px;
            }
            QLineEdit {
                padding-right: 12px;
            }
            QLineEdit:hover, QComboBox:hover {
                background-color: rgba(255, 255, 255, 0.09);
                border-color: rgba(255, 255, 255, 0.24);
            }
            QLineEdit:focus, QComboBox:focus {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1.5px solid #6f93bd;
            }
            QLineEdit:disabled, QComboBox:disabled {
                background-color: rgba(255, 255, 255, 0.02);
                color: #64748b;
                border-color: rgba(255, 255, 255, 0.05);
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
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
                padding: 5px;
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

            /* 5. 单选框与复选框 (消除垂直截断与行高拥挤) */
            QRadioButton, QCheckBox {
                color: #e2e8f0;
                spacing: 10px;
                font-size: 13px;
                min-height: 24px;
                padding: 2px 0px;
            }
            QRadioButton:disabled, QCheckBox:disabled {
                color: #64748b;
            }
            /* 6. 按钮族群 */
            QPushButton {
                background-color: rgba(255, 255, 255, 0.07);
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
                font-weight: 500;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.05);
            }
            QPushButton#btnPrimary {
                background-color: #456f9f;
                color: #ffffff;
                border: none;
                font-weight: 600;
                padding: 7px 20px;
            }
            QPushButton#btnPrimary:hover {
                background-color: #3c628e;
            }
            QPushButton#btnPrimary:pressed {
                background-color: #34557c;
            }

            /* 7. 分类列表控件 (杜绝文字垂直截断) */
            QListWidget#dataList {
                background-color: rgba(0, 0, 0, 0.28);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                color: #ffffff;
                padding: 4px;
                outline: none;
            }
            QListWidget#dataList::item {
                height: 36px;
                border-radius: 6px;
                padding-left: 12px;
                color: #e2e8f0;
                font-size: 13px;
            }
            QListWidget#dataList::item:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
            QListWidget#dataList::item:selected {
                background-color: #393c42;
                color: #ffffff;
                font-weight: 400;
            }

            /* 8. 滑块控件 */
            QSlider::groove:horizontal {
                height: 4px;
                background: rgba(255, 255, 255, 25);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #6f93bd;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #6f93bd;
                width: 14px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 7px;
            }

            /* 9. 极细滚动条 */
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.2);
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.4);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """.replace("__CHEV_NORMAL__", chev_normal).replace("__CHEV_HOVER__", chev_hover)
        self.setStyleSheet(qss)

    def _init_ui(self, initial_tab: int):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧导航
        sidebar_frame = QFrame(self)
        sidebar_frame.setObjectName("sidebarFrame")
        sidebar_frame.setFixedWidth(232)
        s_box = QVBoxLayout(sidebar_frame)
        s_box.setContentsMargins(12, 18, 12, 16)
        s_box.setSpacing(10)

        brand = QLabel("Stacky 灵叠", sidebar_frame)
        brand.setObjectName("sidebarBrand")
        brand_sub = QLabel("设置中心", sidebar_frame)
        brand_sub.setObjectName("sidebarSub")
        s_box.addWidget(brand)
        s_box.addWidget(brand_sub)

        self.search_edit = QLineEdit(sidebar_frame)
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setPlaceholderText("搜索设置…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_settings_search)
        s_box.addWidget(self.search_edit)

        self.nav_list = QListWidget(sidebar_frame)
        self.nav_list.setObjectName("navSidebar")
        self.nav_list.setSpacing(2)
        self.nav_list.setItemDelegate(NavItemDelegate(self.nav_list))
        nav_meta = (
            ("文件夹", "分类与应用收纳", "folder"),
            ("交互", "手势 · 快捷键 · 行为", "interaction"),
            ("外观", "主题 · 玻璃 · 显示密度", "appearance"),
            ("小组件", "天气 · 时钟 · 便签 · 搜索", "widgets"),
        )
        for title, desc, kind in nav_meta:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, desc)
            item.setIcon(_nav_icon(kind))
            self.nav_list.addItem(item)
        s_box.addWidget(self.nav_list, stretch=1)

        # 列表撑满侧栏高度，底部一行版本信息锚住，避免一长条空白
        ver = QLabel("v1.0.0", sidebar_frame)
        ver.setObjectName("sidebarVersion")
        s_box.addWidget(ver)
        main_layout.addWidget(sidebar_frame)

        # 2. 右侧内容栈区
        content_wrapper = QWidget(self)
        content_box = QVBoxLayout(content_wrapper)
        content_box.setContentsMargins(28, 22, 28, 18)
        content_box.setSpacing(14)

        self.stack = QStackedWidget(content_wrapper)
        self.stack.addWidget(self._wrap_in_scroll(self._create_folders_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_interaction_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_appearance_tab()))
        self.stack.addWidget(self._wrap_in_scroll(self._create_weather_tab()))
        content_box.addWidget(self.stack)

        # 3. 底部操作栏
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(12)
        bottom_bar.addStretch()

        btn_cancel = QPushButton("取消", content_wrapper)
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_apply = QPushButton("应用", content_wrapper)
        btn_apply.clicked.connect(self.save_and_apply)
        bottom_bar.addWidget(btn_apply)

        btn_ok = QPushButton("确定", content_wrapper)
        btn_ok.setObjectName("btnPrimary")
        btn_ok.clicked.connect(lambda: (self.save_and_apply(), self.accept()))
        bottom_bar.addWidget(btn_ok)

        content_box.addLayout(bottom_bar)
        main_layout.addWidget(content_wrapper)

        self.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        clamped_tab = max(0, min(initial_tab, self.nav_list.count() - 1))
        self.nav_list.setCurrentRow(clamped_tab)

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
                    lbl.setStyleSheet(prev_style + "; color: #93c5fd;")
                    self._search_highlight = (lbl, prev_style)
                    return

    def _wrap_in_scroll(self, page_widget: QWidget) -> QScrollArea:
        """为每个设置标签页提供原生滚动视图，杜绝截断"""
        self._protect_wheel_controls(page_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background: #1b1c1f; border: none; }")
        # viewport 与页面默认取系统 Base 色（浅色），显式统一为窗口底色，
        # 否则浅色底 + 浅色文字会造成整页几乎不可读。
        for w in (scroll.viewport(), page_widget):
            pal = w.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#1b1c1f"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#1b1c1f"))
            w.setAutoFillBackground(True)
            w.setPalette(pal)
        scroll.setWidget(page_widget)
        return scroll

    def _protect_wheel_controls(self, parent_widget: QWidget):
        """递归为所有下拉框与滑块安装滚轮过滤器，防止页面滚动时误触改变数值"""
        for child in parent_widget.findChildren(QComboBox):
            child.installEventFilter(self)
        for child in parent_widget.findChildren(QSlider):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            if isinstance(obj, QSlider):
                # 滑块独立调整数值，严格阻断外层 QScrollArea 联动，杜绝页面在光标下方乱窜
                delta = event.angleDelta().y()
                step = 2 if delta > 0 else -2
                obj.setValue(obj.value() + step)
                event.accept()
                return True
            elif isinstance(obj, QComboBox):
                # 下拉框防止页面滚动误触改变选项，阻断滚轮穿透
                event.accept()
                return True
        return super().eventFilter(obj, event)

    # =========================================================================
    # Tab 1: 文件夹与分类管理
    # =========================================================================
    def _create_folders_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 22, 0)
        box.setSpacing(14)

        title = QLabel("文件夹")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 分类管理卡片
        cat_card = SettingsCard("分类", page)
        
        # 头部新增分类按钮
        btn_add_f = QPushButton("新建分类", cat_card)
        btn_add_f.clicked.connect(self._on_add_folder)
        cat_card.header_action_box.addWidget(btn_add_f)

        # 分类列表：高度自适应，滚轮不冒泡到外层
        self.folder_list = SmoothCategoryListWidget(cat_card)
        self.folder_list.setObjectName("dataList")
        for f in self.config_manager.get_folders():
            self.folder_list.addItem(f.get("name", "未分类"))
        self.folder_list.setCurrentRow(self.config_manager.get_active_folder_index())
        self._update_folder_list_height()
        self.folder_list.itemDoubleClicked.connect(self._on_rename_folder)
        self.folder_list.currentRowChanged.connect(self._on_folder_selection_changed)
        cat_card.box.addWidget(self.folder_list)

        # 选中的分类操作底栏
        cat_action_row = QHBoxLayout()
        cat_action_row.setContentsMargins(2, 4, 2, 2)
        self.lbl_selected_cat = QLabel("单击切换，双击重命名", cat_card)
        self.lbl_selected_cat.setStyleSheet("color: #8b909a; font-size: 12px;")
        cat_action_row.addWidget(self.lbl_selected_cat)
        cat_action_row.addStretch()

        btn_ren_f = QPushButton("重命名", cat_card)
        btn_ren_f.clicked.connect(self._on_rename_folder)
        btn_del_f = QPushButton("解散文件夹", cat_card)
        btn_del_f.clicked.connect(self._on_delete_folder)
        cat_action_row.addWidget(btn_ren_f)
        cat_action_row.addWidget(btn_del_f)
        cat_card.box.addLayout(cat_action_row)

        box.addWidget(cat_card)

        # 2. 桌面大文件夹卡片管理 (多实例)
        inst_card = SettingsCard("桌面卡片", page)
        
        btn_add_inst = QPushButton("新增卡片", inst_card)
        btn_add_inst.clicked.connect(self._on_add_instance)
        inst_count = len(self.config_manager.get_instances())
        self.inst_count_row = inst_card.add_row("当前数量", f"{inst_count} 个卡片", btn_add_inst)

        inst_card.add_divider()

        is_locked = bool(self.config_manager.get("locked", False))
        self.btn_lock_all = QPushButton("解锁卡片" if is_locked else "锁定卡片", inst_card)
        self.btn_lock_all.clicked.connect(self._on_toggle_lock_all)
        inst_card.add_row("锁定位置", "锁定后卡片不可拖动", self.btn_lock_all)

        inst_card.add_divider()

        btn_tidy = QPushButton("整理图标", inst_card)
        btn_tidy.clicked.connect(self._on_tidy_desktop)
        inst_card.add_row("桌面图标", "扫描桌面快捷方式并归类", btn_tidy)

        box.addWidget(inst_card)

        # 3. 本地存储目录
        storage_card = SettingsCard("存储", page)
        
        cur_storage = self.config_manager.get_managed_storage_root()
        btn_box_storage = QWidget()
        b_s_layout = QHBoxLayout(btn_box_storage)
        b_s_layout.setContentsMargins(0, 0, 0, 0)
        b_s_layout.setSpacing(8)

        btn_browse_storage = QPushButton("更改目录", btn_box_storage)
        btn_browse_storage.clicked.connect(self._on_browse_storage)
        btn_open_storage = QPushButton("打开目录", btn_box_storage)
        btn_open_storage.clicked.connect(self._on_open_storage)
        b_s_layout.addWidget(btn_browse_storage)
        b_s_layout.addWidget(btn_open_storage)

        self.storage_row = storage_card.add_row("存放路径", cur_storage, btn_box_storage)
        self.lbl_storage_path = self.storage_row.findChild(QLabel, "rowDesc")

        storage_card.add_divider()

        btn_migrator = QPushButton("导入", storage_card)
        btn_migrator.clicked.connect(self._on_open_migrator)
        storage_card.add_row("从其他软件导入", "迁移其它启动台旧数据", btn_migrator)

        box.addWidget(storage_card)

        box.addStretch()
        return page

    # =========================================================================
    # Tab 2: 打开与交互设置
    # =========================================================================
    def _create_interaction_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 22, 0)
        box.setSpacing(14)

        title = QLabel("交互")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 打开方式卡片 (防误触)
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

        # 2. 全局呼出与生态联动
        summon_card = SettingsCard("快捷键", page)

        self.combo_summon_target = QComboBox()
        self.combo_summon_target.addItem("文件夹卡片", "card")
        self.combo_summon_target.addItem("搜索与抽屉", "drawer")
        target_val = self.config_manager.get("summon_target", "card")
        self.combo_summon_target.setCurrentIndex(1 if target_val == "drawer" else 0)
        summon_card.add_row("呼出目标", "Alt+Space 唤出卡片或抽屉", self.combo_summon_target)

        summon_card.add_divider()

        self.combo_post_launch = QComboBox()
        self.combo_post_launch.addItem("启动后自动收起", "close_drawer")
        self.combo_post_launch.addItem("启动后保持展开", "keep_open")
        p_act = self.config_manager.get_post_launch_action()
        self.combo_post_launch.setCurrentIndex(1 if p_act == "keep_open" else 0)
        summon_card.add_row("启动应用后", "从抽屉启动程序后的动作", self.combo_post_launch)

        summon_card.add_divider()

        self.combo_hotkey = QComboBox()
        for label, value in (("Alt + Space", "Alt+Space"), ("Alt + F", "Alt+F"),
                             ("Ctrl + Alt + Space", "Ctrl+Alt+Space"),
                             ("Ctrl + Alt + F", "Ctrl+Alt+F")):
            self.combo_hotkey.addItem(label, value)
        hk_idx = self.combo_hotkey.findData(self.config_manager.get("hotkey", "Alt+Space"))
        self.combo_hotkey.setCurrentIndex(max(0, hk_idx))
        summon_card.add_row("全局快捷键", "冲突时自动回退 Alt+F", self.combo_hotkey)

        summon_card.add_divider()

        btn_dock = QPushButton("生成快捷方式", summon_card)
        btn_dock.setFixedWidth(110)
        btn_dock.clicked.connect(self._on_create_dock_shortcut)
        summon_card.add_row("Dock 启动器", "生成可拖进 Dock 的快捷方式", btn_dock)

        box.addWidget(summon_card)

        # 3. Everything 全盘搜索
        everything_card = SettingsCard("全盘搜索", page)

        from everything_ipc import get_everything_client
        is_ev_running = get_everything_client().is_available()

        status_lbl = QLabel("已连接" if is_ev_running else "未运行")
        status_lbl.setStyleSheet("color: #a9b0b8; font-size: 12px;")
        everything_card.add_row("引擎状态", "启动 Everything 后可用", status_lbl)

        everything_card.add_divider()

        self.chk_everything = FlatCheckBox()
        self.chk_everything.setChecked(self.config_manager.is_everything_enabled())
        self.chk_everything.setCursor(Qt.CursorShape.PointingHandCursor)
        everything_card.add_row("检索本地文件", "搜索框同时查全盘文件名", self.chk_everything)

        everything_card.add_divider()

        lbl_tips = QLabel("Enter 打开 · Ctrl+Enter 定位文件")
        lbl_tips.setStyleSheet("color: #8b909a; font-size: 11px;")
        everything_card.box.addWidget(lbl_tips)

        box.addWidget(everything_card)

        # 4. 系统与桌面
        sys_card = SettingsCard("系统", page)

        self.chk_double_click_hide = FlatCheckBox()
        self.chk_double_click_hide.setChecked(self.config_manager.is_double_click_desktop_hide_enabled())
        self.chk_double_click_hide.setCursor(Qt.CursorShape.PointingHandCursor)
        sys_card.add_row("双击桌面空白处", "双击壁纸空白收起/恢复卡片", self.chk_double_click_hide)

        sys_card.add_divider()

        self.chk_autostart = FlatCheckBox()
        self.chk_autostart.setChecked(is_autostart_enabled())
        self.chk_autostart.setCursor(Qt.CursorShape.PointingHandCursor)
        sys_card.add_row("开机自启", "登录 Windows 后自动驻留", self.chk_autostart)
        box.addWidget(sys_card)

        box.addStretch()
        return page

    # =========================================================================
    # Tab 3: 外观质感与网格
    # =========================================================================
    def _create_appearance_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 22, 0)
        box.setSpacing(14)

        title = QLabel("外观")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 1. 风格与布局卡片
        theme_card = SettingsCard("颜色与网格", page)

        self.combo_theme = QComboBox()
        self.combo_theme.addItem("深色", "dark")
        self.combo_theme.addItem("浅色", "light")
        self.combo_theme.addItem("纯黑", "oled")
        cur_t = self.config_manager.get("theme", "dark")
        t_idx = {"dark": 0, "light": 1, "oled": 2}.get(cur_t, 0)
        self.combo_theme.setCurrentIndex(t_idx)
        theme_card.add_row("主题", "卡片与抽屉的整体配色", self.combo_theme)

        theme_card.add_divider()

        self.combo_grid = QComboBox()
        self.combo_grid.addItem("3 × 3", (3, 3))
        self.combo_grid.addItem("2 × 2", (2, 2))
        self.combo_grid.addItem("2 × 4", (2, 4))
        self.combo_grid.addItem("4 × 2", (4, 2))
        self.combo_grid.addItem("3 × 5", (3, 5))
        self.combo_grid.addItem("4 × 4", (4, 4))
        # 卡片被单独拉伸过时，默认显示「自定义」，避免下拉框谎报成 3×3
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
        # 只有用户真的动过下拉框才在保存时重置所有卡片尺寸
        self.combo_grid.currentIndexChanged.connect(lambda _i: setattr(self, "_grid_dirty", True))
        theme_card.add_row("网格", "默认行列；会重置自定义尺寸", self.combo_grid)

        box.addWidget(theme_card)

        # 2. 图标呈现模式
        display_card = SettingsCard("图标", page)

        self.combo_display_mode = QComboBox()
        # 只保留后端真正实现的两种；原先的 two_line / compact_list 从未生效
        self.combo_display_mode.addItem("图标 + 名称", "classic")
        self.combo_display_mode.addItem("仅图标", "large_icon")
        cur_disp = self.config_manager.get_app_display_mode()
        d_map = {"classic": 0, "large_icon": 1}
        self.combo_display_mode.setCurrentIndex(d_map.get(cur_disp, 0))
        display_card.add_row("图标样式", "图标下方是否显示名称", self.combo_display_mode)

        display_card.add_divider()

        badges_widget = QWidget()
        b_layout = QHBoxLayout(badges_widget)
        b_layout.setContentsMargins(0, 0, 0, 0)
        b_layout.setSpacing(16)

        self.chk_admin_badge = FlatCheckBox("管理员权限角标")
        self.chk_admin_badge.setChecked(self.config_manager.is_admin_badge_enabled())
        self.chk_admin_badge.setCursor(Qt.CursorShape.PointingHandCursor)

        # 运行状态指示点从未被渲染层消费，属于假开关，已移除
        b_layout.addWidget(self.chk_admin_badge)
        display_card.add_row("角标", "管理员程序显示盾牌标识", badges_widget)

        box.addWidget(display_card)

        # 3. 透明度与层级卡片
        layer_card = SettingsCard("显示", page)

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
        self.lbl_op_val.setStyleSheet("color: #c9ccd1;")
        self.slider_op.valueChanged.connect(lambda v: self.lbl_op_val.setText(f"{v}%"))

        op_layout.addWidget(self.slider_op)
        op_layout.addWidget(self.lbl_op_val)
        layer_card.add_row("卡片透明度", "越低越透出壁纸", op_widget)

        layer_card.add_divider()

        self.combo_layer = QComboBox()
        self.combo_layer.addItem("嵌入桌面", "bottom")
        self.combo_layer.addItem("普通窗口", "normal")
        self.combo_layer.addItem("置顶", "top")
        cur_l = self.config_manager.get("layer_mode", "bottom")
        l_idx = {"bottom": 0, "normal": 1, "top": 2}.get(cur_l, 0)
        self.combo_layer.setCurrentIndex(l_idx)
        layer_card.add_row("层级", "嵌入桌面时不遮挡其它窗口", self.combo_layer)

        self.chk_glass_blur = FlatCheckBox()
        self.chk_glass_blur.setChecked(self.config_manager.get("glass_blur_enabled", False))
        self.chk_glass_blur.setCursor(Qt.CursorShape.PointingHandCursor)
        layer_card.add_row("背景模糊", "真实 Acrylic 毛玻璃", self.chk_glass_blur)

        box.addWidget(layer_card)

        box.addStretch()
        return page

    # =========================================================================
    # Tab 4: 桌面小组件中心
    # =========================================================================
    def _on_file_shelf_toggled(self, enabled: bool):
        for w in getattr(self, "_shelf_subwidgets", []):
            w.setEnabled(enabled)
        # 即时热启停：拨动开关就从桌面显示/消失，不依赖「应用/确定」
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

    def _create_weather_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 22, 0)
        box.setSpacing(14)

        title = QLabel("小组件")
        title.setObjectName("pageTitle")
        box.addWidget(title)

        # 文件中转站：桌面组件，开关放在本页最前，关闭后子项一并禁用
        shelf_card = SettingsCard("文件中转站", page)
        shelf_cfg = self.config_manager.get_file_shelf_config()

        self.chk_file_shelf = FlatCheckBox()
        self.chk_file_shelf.setChecked(bool(shelf_cfg.get("enabled", True)))
        self.chk_file_shelf.setCursor(Qt.CursorShape.PointingHandCursor)
        shelf_card.header_action_box.addWidget(self.chk_file_shelf)

        self.combo_shelf_edge = QComboBox()
        self.combo_shelf_edge.addItem("屏幕右侧", "right")
        self.combo_shelf_edge.addItem("屏幕左侧", "left")
        self.combo_shelf_edge.setCurrentIndex(1 if shelf_cfg.get("edge", "right") == "left" else 0)
        shelf_card.add_row("吸附位置", "中转站停靠的屏幕边缘", self.combo_shelf_edge)

        shelf_card.add_divider()

        self.chk_shelf_auto_collapse = FlatCheckBox()
        self.chk_shelf_auto_collapse.setChecked(bool(shelf_cfg.get("auto_collapse", True)))
        self.chk_shelf_auto_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        shelf_card.add_row("清空后自动收起", "拖出全部文件后自动收回", self.chk_shelf_auto_collapse)

        shelf_card.add_divider()

        self.chk_shelf_hide_fullscreen = FlatCheckBox()
        self.chk_shelf_hide_fullscreen.setChecked(bool(shelf_cfg.get("hide_in_fullscreen", True)))
        self.chk_shelf_hide_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        shelf_card.add_row("全屏时隐藏", "游戏/视频全屏时让位", self.chk_shelf_hide_fullscreen)

        self._shelf_subwidgets = [
            self.combo_shelf_edge,
            self.chk_shelf_auto_collapse,
            self.chk_shelf_hide_fullscreen,
        ]
        self.chk_file_shelf.toggled.connect(self._on_file_shelf_toggled)
        self._on_file_shelf_toggled(self.chk_file_shelf.isChecked())
        box.addWidget(shelf_card)

        # ---------------------------------------------------------------------
        # 1. 桌面天气组件 (Weather)
        # ---------------------------------------------------------------------
        w_cfg = self.config_manager.get_weather_config()
        weather_card = SettingsCard("天气", page)

        self.chk_weather_enable = FlatCheckBox()
        self.chk_weather_enable.setChecked(w_cfg.get("enabled", True))
        self.chk_weather_enable.setCursor(Qt.CursorShape.PointingHandCursor)
        weather_card.header_action_box.addWidget(self.chk_weather_enable)

        self.combo_weather_size = QComboBox()
        self.combo_weather_size.addItem("2 × 2", "2x2")
        self.combo_weather_size.addItem("2 × 1", "2x1")
        self.combo_weather_size.setCurrentIndex(1 if w_cfg.get("size") == "2x1" else 0)
        weather_card.add_row("尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_weather_size)

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
        weather_card.add_row("定位", "自动定位；检测到代理不会乱跳城市", loc_type_widget)

        weather_card.add_divider()

        btn_ip_locate = QPushButton("重新定位", weather_card)
        btn_ip_locate.setFixedWidth(110)
        btn_ip_locate.clicked.connect(self._on_ip_relocate)
        weather_card.add_row("当前 IP", "按实时 IP 覆盖当前位置", btn_ip_locate)

        weather_card.add_divider()

        self.combo_city = QComboBox()
        self.combo_city.setFixedWidth(200)
        city_names = list(POPULAR_CITIES.keys())
        self.combo_city.addItems(city_names)
        cur_city = w_cfg.get("city", "成都")
        c_idx = self.combo_city.findText(cur_city)
        if c_idx >= 0:
            self.combo_city.setCurrentIndex(c_idx)
        weather_card.add_row("常用城市", "点选填充下面的城市名", self.combo_city)

        weather_card.add_divider()

        self.edit_city = QLineEdit(cur_city)
        self.edit_city.setPlaceholderText("例如: 成都、绵阳、杭州、深圳...")
        self.edit_city.setFixedWidth(200)
        self.combo_city.currentTextChanged.connect(self.edit_city.setText)
        weather_card.add_row("城市名称", "自定义城市名，保存后生效", self.edit_city)

        weather_card.add_divider()

        self.combo_district = QComboBox()
        self.combo_district.setFixedWidth(200)
        self.combo_district.addItem("市级（不细分）", None)
        self.combo_district.setEnabled(False)
        weather_card.add_row("区县", "同一城市各区温度有差异", self.combo_district)
        self.districts_loaded.connect(self._on_districts_loaded)
        self._reload_districts(cur_city, w_cfg.get("region", ""), w_cfg.get("district", ""))
        self.combo_city.currentTextChanged.connect(self._on_city_changed_for_districts)

        weather_card.add_divider()

        self.edit_place = QLineEdit()
        self.edit_place.setPlaceholderText("街道/小区/园区，如 华阳街道")
        self.edit_place.setFixedWidth(138)
        btn_search_place = QPushButton("搜索", weather_card)
        btn_search_place.setFixedWidth(56)
        btn_search_place.clicked.connect(self._on_search_place)
        place_row = QWidget()
        place_layout = QHBoxLayout(place_row)
        place_layout.setContentsMargins(0, 0, 0, 0)
        place_layout.setSpacing(8)
        place_layout.addWidget(self.edit_place)
        place_layout.addWidget(btn_search_place)
        weather_card.add_row("精确位置", "细化到街道/小区，误差更小", place_row)

        self.combo_place = QComboBox()
        self.combo_place.setFixedWidth(200)
        self.combo_place.addItem("先搜索再选择", None)
        self.combo_place.setEnabled(False)
        weather_card.add_row("候选位置", "", self.combo_place)
        self.places_found.connect(self._on_places_found)

        weather_card.add_divider()

        self.rb_auto_loc.toggled.connect(lambda checked: (self.combo_city.setEnabled(not checked), self.edit_city.setEnabled(not checked)))
        self.combo_city.setEnabled(not is_auto)
        self.edit_city.setEnabled(not is_auto)

        btn_refresh_weather = QPushButton("刷新", weather_card)
        btn_refresh_weather.setFixedWidth(120)
        btn_refresh_weather.clicked.connect(self._on_refresh_weather_clicked)
        weather_card.add_row("天气数据", "强制刷新并保存地区", btn_refresh_weather)

        box.addWidget(weather_card)

        # ---------------------------------------------------------------------
        # 2. 数字时钟与日历小组件 (Clock & Date)
        # ---------------------------------------------------------------------
        c_cfg = self.config_manager.get_clock_config()
        clock_card = SettingsCard("时钟", page)

        self.chk_clock_enable = FlatCheckBox()
        self.chk_clock_enable.setChecked(c_cfg.get("enabled", False))
        self.chk_clock_enable.setCursor(Qt.CursorShape.PointingHandCursor)
        clock_card.header_action_box.addWidget(self.chk_clock_enable)

        self.combo_clock_size = QComboBox()
        self.combo_clock_size.addItem("2 × 2", "2x2")
        self.combo_clock_size.addItem("2 × 1", "2x1")
        self.combo_clock_size.setCurrentIndex(1 if c_cfg.get("size") == "2x1" else 0)
        clock_card.add_row("尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_clock_size)

        clock_card.add_divider()

        btn_align_clock = QPushButton("对齐", clock_card)
        btn_align_clock.setFixedWidth(120)
        btn_align_clock.clicked.connect(self._on_align_clock_clicked)
        clock_card.add_row("位置", "与左侧卡片顶边对齐", btn_align_clock)

        box.addWidget(clock_card)

        # ---------------------------------------------------------------------
        # 3. 系统硬件监控 (System Resource Monitor)
        # ---------------------------------------------------------------------
        m_cfg = self.config_manager.get_monitor_config()
        monitor_card = SettingsCard("硬件", page)

        self.chk_monitor_enable = FlatCheckBox()
        self.chk_monitor_enable.setChecked(m_cfg.get("enabled", False))
        self.chk_monitor_enable.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.drive_list.setFixedHeight(94)
        self.drive_list.setStyleSheet(
            "QListWidget { background: rgba(255,255,255,0.04);"
            " border:1px solid rgba(255,255,255,0.12); border-radius:6px;"
            " color:#e2e8f0; font-size:11px; }"
            "QListWidget::item { padding:2px 6px; }"
            "QListWidget::item:selected { background: rgba(96,165,250,0.16); }"
            "QListWidget::indicator { width:13px; height:13px;"
            " border:1px solid rgba(255,255,255,0.35); border-radius:3px;"
            " background: rgba(0,0,0,0.25); }"
            "QListWidget::indicator:checked { background:#3b82f6; border-color:#60a5fa; }")
        for d in self.drives_all:
            item = QListWidgetItem(d.rstrip("\\") + " 盘")
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if d in self.selected_drives
                               else Qt.CheckState.Unchecked)
            self.drive_list.addItem(item)
        self.drive_list.itemChanged.connect(self._on_drive_item_changed)
        monitor_card.add_row("监控磁盘", "勾选要同时显示的分区（可多选）", self.drive_list)

        box.addWidget(monitor_card)

        # ---------------------------------------------------------------------
        # 4. 桌面便签 (Sticky Notes)
        # ---------------------------------------------------------------------
        memo_cfg = self.config_manager.get_memo_config()
        memo_card = SettingsCard("便签", page)

        self.chk_memo_enable = FlatCheckBox()
        self.chk_memo_enable.setChecked(memo_cfg.get("enabled", False))
        self.chk_memo_enable.setCursor(Qt.CursorShape.PointingHandCursor)
        memo_card.header_action_box.addWidget(self.chk_memo_enable)

        btn_align_memo = QPushButton("对齐", memo_card)
        btn_align_memo.setFixedWidth(120)
        btn_align_memo.clicked.connect(self._on_align_memo_clicked)
        memo_card.add_row("位置", "与左侧卡片顶边对齐", btn_align_memo)

        memo_card.add_divider()

        btn_clear_memo = QPushButton("清空", memo_card)
        btn_clear_memo.setFixedWidth(120)
        btn_clear_memo.clicked.connect(self._on_clear_memo_clicked)
        memo_card.add_row("清空便签", "清空桌面便签全部内容", btn_clear_memo)

        box.addWidget(memo_card)

        # ---------------------------------------------------------------------
        # 5. 媒体播放组件 (Now Playing)
        # ---------------------------------------------------------------------
        media_cfg = self.config_manager.get_media_config()
        media_card = SettingsCard("媒体", page)

        self.chk_media_enable = FlatCheckBox()
        self.chk_media_enable.setChecked(media_cfg.get("enabled", False))
        self.chk_media_enable.setCursor(Qt.CursorShape.PointingHandCursor)
        media_card.header_action_box.addWidget(self.chk_media_enable)

        self.combo_media_size = QComboBox()
        self.combo_media_size.addItem("2 × 2", "2x2")
        self.combo_media_size.addItem("2 × 1", "2x1")
        self.combo_media_size.setCurrentIndex(1 if media_cfg.get("size") == "2x1" else 0)
        media_card.add_row("尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_media_size)

        self.chk_media_lyrics = FlatCheckBox()
        self.chk_media_lyrics.setChecked(media_cfg.get("show_lyrics", True))
        self.chk_media_lyrics.setCursor(Qt.CursorShape.PointingHandCursor)
        media_card.add_row("显示歌词", "自动匹配并逐行滚动（小尺寸不显示）", self.chk_media_lyrics)

        media_card.add_divider()

        btn_align_media = QPushButton("对齐", media_card)
        btn_align_media.setFixedWidth(120)
        btn_align_media.clicked.connect(self._on_align_media_clicked)
        media_card.add_row("位置", "与左侧卡片顶边对齐", btn_align_media)

        box.addWidget(media_card)

        ev_cfg = self.config_manager.get_everything_config()
        ev_card = SettingsCard("全盘搜索", page)
        self.chk_everything_widget = FlatCheckBox()
        self.chk_everything_widget.setChecked(ev_cfg.get("enabled", True))
        self.chk_everything_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        ev_card.header_action_box.addWidget(self.chk_everything_widget)

        self.combo_everything_size = QComboBox()
        self.combo_everything_size.addItem("2 × 2", "2x2")
        self.combo_everything_size.addItem("2 × 1", "2x1")
        self.combo_everything_size.setCurrentIndex(1 if ev_cfg.get("size") == "2x1" else 0)
        ev_card.add_row("尺寸", "2×2 方卡 / 2×1 胶囊", self.combo_everything_size)
        box.addWidget(ev_card)

        box.addStretch()
        return page

    # =========================================================================
    # 事件与动作处理
    # =========================================================================
    def _update_folder_list_height(self):
        """自适应调整分类列表高度，杜绝内滚轮与空白截断"""
        cnt = max(1, self.folder_list.count())
        self.folder_list.setFixedHeight(cnt * 38 + 10)

    def _on_folder_selection_changed(self, row: int):
        if row >= 0 and row < self.folder_list.count():
            name = self.folder_list.item(row).text()
            if hasattr(self, "lbl_selected_cat"):
                self.lbl_selected_cat.setText(f"已选中分类：【{name}】 · 双击可直接重命名")

    def _on_add_folder(self):
        name, ok = QInputDialog.getText(self, "新建分类", "请输入新分类名称:")
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
        new_name, ok = QInputDialog.getText(self, "重命名分类", "请输入新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            self.config_manager.rename_folder(row, new_name.strip())
            self.folder_list.item(row).setText(new_name.strip())
            if hasattr(self, "lbl_selected_cat"):
                self.lbl_selected_cat.setText(f"已成功更名为：【{new_name.strip()}】")

    def _on_delete_folder(self):
        row = self.folder_list.currentRow()
        if row < 0:
            return
        if self.folder_list.count() <= 1:
            QMessageBox.warning(self, "提示", "至少需要保留一个大文件夹分类！")
            return
        f_name = self.folder_list.item(row).text()
        reply = QMessageBox.question(
            self,
            "解散大文件夹确认",
            f"确定要解散大文件夹【{f_name}】吗？\n\n分类内的所有应用与快捷方式将原封不动放回桌面。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            storage_mgr = getattr(self.window_manager, "shared_storage_engine", None) if self.window_manager else None
            restored_count = 0
            if storage_mgr:
                restored_count = storage_mgr.dissolve_folder_to_desktop(f_name)

            if self.config_manager.delete_folder(row):
                self.folder_list.takeItem(row)
                self._update_folder_list_height()
                if self.window_manager:
                    fallback_name = self.config_manager.get_active_folder_name()
                    for w in list(self.window_manager.windows.values()):
                        if getattr(w, "bound_folder_name", None) == f_name:
                            w.bound_folder_name = fallback_name
                            w.active_page = 0
                            w.rebuild_grid()
                QMessageBox.information(
                    self,
                    "解散成功",
                    f"已成功解散【{f_name}】大文件夹！\n共 {restored_count} 个应用已全部原样放回桌面。"
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
            QMessageBox.information(self, "收纳完成", "已整理桌面图标。")

    def _on_toggle_lock_all(self):
        if self.window_manager:
            self.window_manager.toggle_lock_all()
            locked = bool(self.config_manager.get("locked", False))
            if hasattr(self, "btn_lock_all"):
                self.btn_lock_all.setText("解锁卡片" if locked else "锁定卡片")

    def _on_browse_storage(self):
        cur_p = self.config_manager.get_managed_storage_root()
        dir_path = QFileDialog.getExistingDirectory(self, "选择 Stacky 物理存储目录", cur_p)
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
            self.window_manager.open_legacy_migrator()

    def _on_create_dock_shortcut(self):
        if self.window_manager:
            self.window_manager.action_create_dock_shortcut()
            QMessageBox.information(self, "生成完成", "已在桌面生成专属超椭圆快捷方式，可直接拖入 Dock！")

    def _on_align_clock_clicked(self):
        if self.window_manager and getattr(self.window_manager, "clock_window", None):
            self.window_manager.clock_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "时钟小组件已与相邻大文件夹平齐对齐！")
        else:
            QMessageBox.information(self, "提示", "请先在上方勾选启用时钟微件并点击【应用】。")

    def _on_align_media_clicked(self):
        if self.window_manager and getattr(self.window_manager, "media_window", None):
            self.window_manager.media_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "媒体播放微件已与相邻大文件夹平齐对齐！")
        else:
            QMessageBox.information(self, "提示", "请先在上方勾选启用系统媒体微件并点击【应用】。")


    def _on_align_memo_clicked(self):
        if self.window_manager and getattr(self.window_manager, "memo_window", None):
            self.window_manager.memo_window.align_to_adjacent_folder()
            QMessageBox.information(self, "对齐完成", "便签小组件已与相邻大文件夹平齐对齐！")
        else:
            QMessageBox.information(self, "提示", "请先在上方勾选启用便签微件并点击【应用】。")

    def _on_clear_memo_clicked(self):
        memo_file = os.path.join(ROOT, ".desktop_memo.txt")
        try:
            with open(memo_file, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
        if self.window_manager and getattr(self.window_manager, "memo_window", None):
            self.window_manager.memo_window.edit_memo.clear()
        QMessageBox.information(self, "提示", "桌面随手便签内容已清空！")

    def _on_refresh_weather_clicked(self):
        self._save_weather_settings()
        ws = get_weather_service()
        ws.fetch_async(force=True)
        QMessageBox.information(self, "提示", "天气地区设置已保存并已发起即时刷新！")

    def _on_city_changed_for_districts(self, city: str):
        region = POPULAR_CITIES.get(city, {}).get("region", "")
        self._reload_districts(city, region, "")

    def _reload_districts(self, city: str, region: str, select: str = ""):
        """区县列表来自阿里 DataV 行政区划（省→市→区），网络请求放后台线程。"""
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
        """强制按当前 IP 重新定位（跳过代理校验），换城市或搬家后用。"""
        from weather_service import get_weather_service
        ws = get_weather_service()
        geo = ws.resolve_ip_location(force=True)
        if not geo:
            QMessageBox.information(self, "定位失败", "拿不到当前 IP 归属地，请检查网络后重试。")
            return
        self.config_manager.set_weather_location(
            city=geo["city"], region=geo["region"], lat=geo["lat"], lon=geo["lon"],
            auto_location=True, district="")
        self.rb_auto_loc.setChecked(True)
        self.edit_city.setText(geo["city"])
        ws.set_custom_location(city=geo["city"], region=geo["region"],
                               lat=geo["lat"], lon=geo["lon"], auto_location=True)
        ws.fetch_async(force=True)
        QMessageBox.information(self, "已定位", f"当前 IP 归属：{geo['city']} · {geo['region']}")

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
            label = f"{item.get('name', '')}（{item.get('hint', '')}）" if item.get("hint") else item.get("name", "")
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

    def _save_weather_settings(self):
        is_auto = self.rb_auto_loc.isChecked()
        chosen = self.edit_city.text().strip() or self.combo_city.currentText()
        res = resolve_city_coordinates(chosen)
        if not res:
            # 解析不出来就别静默落回成都，让用户补上所属地级市
            QMessageBox.information(self, "没找到这个城市",
                                    f"无法定位「{chosen}」。\n请换成所属的地级市（如 苏州），再在区县里选择县级市。")
            return
        c_city = res["city"]
        c_reg = res.get("region", "")
        lat = res["lat"]
        lon = res["lon"]

        district = ""
        picked_place = self.combo_place.currentData() if hasattr(self, "combo_place") else None
        if isinstance(picked_place, dict) and picked_place.get("name"):
            # 地标优先：精确到街道/小区，比区中心更贴近实际位置
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
            district=district
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
            district=district
        )

    def save_and_apply(self):
        """保存所有标签页配置并即时向窗口管理器热生效"""
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

        # Everything 全盘无感检索
        if hasattr(self, "chk_everything"):
            self.config_manager.set_everything_enabled(self.chk_everything.isChecked())

        # 3. 开机自启
        set_autostart(self.chk_autostart.isChecked())

        # 4. 外观主题
        theme = self.combo_theme.currentData()
        self.config_manager.set("theme", theme)

        # 视窗呈现模式与图标排布
        if hasattr(self, "combo_display_mode"):
            self.config_manager.set_app_display_mode(self.combo_display_mode.currentData(), auto_save=False)
        if hasattr(self, "chk_admin_badge"):
            self.config_manager.set_admin_badge_enabled(self.chk_admin_badge.isChecked(), auto_save=False)

        # 5. 网格规格（只在下拉框真的被改过时才重置卡片尺寸，避免改别的设置把自定义尺寸冲掉）
        r, c = self.combo_grid.currentData()
        grid_dirty = getattr(self, "_grid_dirty", False) and bool(r) and bool(c)
        if grid_dirty:
            self.config_manager.set("grid_rows", r, auto_save=False)
            self.config_manager.set("grid_cols", c, auto_save=False)
            # 实例自带 rows/cols 覆盖全局键，而拉过尺寸的 card_width/height 又覆盖 rows/cols，
            # 三者必须一起重置，否则「网格」下拉框是个改了也不生效的开关。
            for inst in self.config_manager.get_instances():
                inst.pop("card_width", None)
                inst.pop("card_height", None)
                self.config_manager.update_instance(
                    inst.get("instance_id"), {"rows": r, "cols": c}, auto_save=False)
        self.config_manager.save()
        if grid_dirty:
            for win in getattr(self.window_manager, "windows", {}).values():
                win.apply_grid_shape(r, c, toast=False)
            self._grid_dirty = False

        # 6. 不透明度
        op = self.slider_op.value() / 100.0
        self.config_manager.set("opacity", op)
        if hasattr(self, "chk_glass_blur"):
            self.config_manager.set("glass_blur_enabled", self.chk_glass_blur.isChecked())

        # 7. 桌面层级
        layer = self.combo_layer.currentData()
        self.config_manager.set("layer_mode", layer)

        # 双击桌面空白处隐藏全部卡片 (禅模式)
        if hasattr(self, "chk_double_click_hide"):
            is_zen_on = self.chk_double_click_hide.isChecked()
            self.config_manager.set_double_click_desktop_hide_enabled(is_zen_on)
            if self.window_manager and hasattr(self.window_manager, "desktop_blank_handler"):
                self.window_manager.desktop_blank_handler.set_enabled(is_zen_on)

        # 8. 桌面小组件群组保存
        self._save_weather_settings()

        # 时钟小组件
        c_cfg = self.config_manager.get_clock_config()
        c_cfg["enabled"] = self.chk_clock_enable.isChecked()
        c_cfg["size"] = self.combo_clock_size.currentData()
        self.config_manager.set_clock_config(c_cfg)

        # 硬件管家小组件
        m_cfg = self.config_manager.get_monitor_config()
        m_cfg["enabled"] = self.chk_monitor_enable.isChecked()
        m_cfg["disk_drives"] = list(self.selected_drives)
        m_cfg["disk_drive"] = self.selected_drives[0] if self.selected_drives else "C:\\"
        self.config_manager.set_monitor_config(m_cfg)

        # 随手便签小组件
        memo_cfg = self.config_manager.get_memo_config()
        memo_cfg["enabled"] = self.chk_memo_enable.isChecked()
        self.config_manager.set_memo_config(memo_cfg)

        # 媒体播放小组件
        if hasattr(self, "chk_media_enable"):
            media_cfg = self.config_manager.get_media_config()
            media_cfg["enabled"] = self.chk_media_enable.isChecked()
            media_cfg["size"] = self.combo_media_size.currentData()
            media_cfg["show_lyrics"] = self.chk_media_lyrics.isChecked()
            self.config_manager.set_media_config(media_cfg)

        # Everything 全盘搜索小组件
        if hasattr(self, "chk_everything_widget"):
            ev_cfg = self.config_manager.get_everything_config()
            ev_cfg["enabled"] = self.chk_everything_widget.isChecked()
            ev_cfg["size"] = self.combo_everything_size.currentData()
            self.config_manager.set_everything_config(ev_cfg)

        # 桌面文件中转站 (File Shelf)
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

        # 9. 广播热生效
        if self.window_manager:
            for win in list(self.window_manager.windows.values()):
                win.card.set_card_style(
                    self.config_manager.get("border_radius", 26),
                    op,
                    theme=theme
                )
                win.rebuild_grid()
                win.apply_layer_mode()
                win.unregister_global_hotkey()
                win.register_global_hotkey()

            # 主题统一广播到所有小组件，避免只有天气/媒体跟随
            for attr in ("weather_window", "clock_window", "monitor_window",
                         "memo_window", "media_window", "everything_window"):
                widget_win = getattr(self.window_manager, attr, None)
                if widget_win is not None and hasattr(widget_win, "set_theme"):
                    widget_win.set_theme(theme)

            # 9.1 天气小组件生效
            if hasattr(self.window_manager, "weather_window") and self.window_manager.weather_window:
                w_cfg = self.config_manager.get_weather_config()
                if not w_cfg.get("enabled", True):
                    self.window_manager.weather_window.close_widget()
                    self.window_manager.weather_window = None
                else:
                    self.window_manager.weather_window._apply_geometry_mode(w_cfg.get("size", "2x2"))
                    self.window_manager.weather_window.set_theme(theme)
                    self.window_manager.weather_window.refresh_weather()
            elif self.config_manager.get_weather_config().get("enabled", True):
                self.window_manager.init_weather_widget(self.config_manager.get_weather_config())

            # 9.2 时钟小组件生效
            if hasattr(self.window_manager, "clock_window") and self.window_manager.clock_window:
                if not c_cfg.get("enabled", False):
                    self.window_manager.clock_window.close_widget()
                    self.window_manager.clock_window = None
                else:
                    self.window_manager.clock_window.set_size_mode(c_cfg.get("size", "2x2"))
            elif c_cfg.get("enabled", False):
                self.window_manager.init_clock_widget(c_cfg)

            # 9.3 硬件管家小组件生效（勾选盘符变化也要热更新，不能只在开关变化时重建）
            if hasattr(self.window_manager, "monitor_window") and self.window_manager.monitor_window:
                if not m_cfg.get("enabled", False):
                    self.window_manager.monitor_window.close_widget()
                    self.window_manager.monitor_window = None
                else:
                    self.window_manager.monitor_window.apply_drives(
                        m_cfg.get("disk_drives") or [m_cfg.get("disk_drive") or "C:\\"])
            elif m_cfg.get("enabled", False):
                self.window_manager.init_monitor_widget(m_cfg)

            # 9.4 随手便签小组件生效
            if hasattr(self.window_manager, "memo_window") and self.window_manager.memo_window:
                if not memo_cfg.get("enabled", False):
                    self.window_manager.memo_window.close_widget()
                    self.window_manager.memo_window = None
            elif memo_cfg.get("enabled", False):
                self.window_manager.init_memo_widget(memo_cfg)

            # 9.5 媒体播放小组件生效
            if hasattr(self, "chk_media_enable"):
                if hasattr(self.window_manager, "media_window") and self.window_manager.media_window:
                    if not media_cfg.get("enabled", False):
                        self.window_manager.media_window.close_widget()
                        self.window_manager.media_window = None
                    else:
                        self.window_manager.media_window.set_size_mode(media_cfg.get("size", "2x2"))
                        self.window_manager.media_window.set_theme(theme)
                        self.window_manager.media_window.set_show_lyrics(
                            media_cfg.get("show_lyrics", True))
                elif media_cfg.get("enabled", False):
                    self.window_manager.init_media_widget(media_cfg)

            # 9.6 桌面文件中转站生效
            if hasattr(self.window_manager, "init_file_shelf"):
                s_cfg = self.config_manager.get_file_shelf_config()
                if s_cfg.get("enabled", True):
                    if not getattr(self.window_manager, "file_shelf_window", None):
                        self.window_manager.init_file_shelf(s_cfg)
                    else:
                        self.window_manager.file_shelf_window.edge = s_cfg.get("edge", "right")
                        self.window_manager.file_shelf_window.auto_collapse = s_cfg.get("auto_collapse", True)
                        self.window_manager.file_shelf_window.hide_in_fullscreen = s_cfg.get("hide_in_fullscreen", True)
                        self.window_manager.file_shelf_window.update_geometry_for_state(animate=False)
                        self.window_manager.file_shelf_window.check_fullscreen_suppression()
                        if not getattr(self.window_manager.file_shelf_window, "_game_suppressed", False):
                            self.window_manager.file_shelf_window.show()
                else:
                    if getattr(self.window_manager, "file_shelf_window", None):
                        self.window_manager.file_shelf_window.close()
                        self.window_manager.file_shelf_window = None

            # 9.7 Everything 全盘搜索小组件
            ev_cfg = self.config_manager.get_everything_config()
            if hasattr(self.window_manager, "everything_window"):
                if not ev_cfg.get("enabled", True):
                    if self.window_manager.everything_window:
                        self.window_manager.everything_window.close_widget()
                        self.window_manager.everything_window = None
                elif self.window_manager.everything_window is None:
                    self.window_manager.init_everything_widget(ev_cfg)
                else:
                    self.window_manager.everything_window.set_size_mode(ev_cfg.get("size", "2x2"))

            # 9.8 背景模糊开关：重新应用/移除各窗口的 DWM 亚克力
            for win in list(self.window_manager.windows.values()):
                if hasattr(win, "apply_blur_effect"):
                    win.apply_blur_effect()
            self.window_manager.bind_all_widget_resizes()

