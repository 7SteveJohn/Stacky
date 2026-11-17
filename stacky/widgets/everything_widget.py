"""everything_widget.py - 常驻桌面的 Everything 全盘搜索小组件 (2x2 / 2x1)。"""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject, QSize, QRect, QRectF, QEvent, QPoint, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPixmap, QIcon, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QStyledItemDelegate, QStyle,
)

from desktop_widgets import (
    BaseDesktopWidgetWindow, CARD_W_2X2, CARD_H_2X2,
    CARD_W_2X1, CARD_H_2X1, MARGIN_SHADOW, finish_widget_menu,
    _get_widget_menu_qss,
)
from icon_utils import create_squircle_path, draw_liquid_glass
import search_utils

_UI_FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
_UI_FONT_CSS = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", sans-serif'

_HINTS = {
    "idle": "输入关键词搜索文件",
    "empty": "未找到匹配文件",
    "unavailable": "请先启动 Everything 服务",
    "error": "搜索暂时不可用",
}


def _ew_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(_UI_FONT_FAMILIES)
    f.setPixelSize(max(9, int(pixel_size)))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    return f


def _make_search_icon(light: bool = False) -> QIcon:
    """自绘 14x14 抗锯齿矢量放大镜图标（2x HiDPI 采样），随深浅主题自适应着色。"""
    pm = QPixmap(28, 28)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    tint = QColor("#64748b") if light else QColor("#94a3b8")
    pen = QPen(tint, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(2.2, 2.2, 7.4, 7.4))
    p.drawLine(QPointF(8.5, 8.5), QPointF(11.9, 11.9))
    p.end()
    return QIcon(pm)


def _everything_available() -> bool:
    try:
        from everything_ipc import get_everything_client
        return get_everything_client().is_available()
    except Exception:
        return False


class _QuerySignals(QObject):
    finished = Signal(int, str, object)  # seq, status, items


class _ResultDelegate(QStyledItemDelegate):
    """直接绘制「文件名 + 灰色小字目录」，避免 item 子控件拦截鼠标/右键事件。"""

    def __init__(self, owner):
        super().__init__(owner.list_widget)
        self.owner = owner

    def _scale(self):
        return getattr(self.owner, "_responsive_scale", lambda: 1.0)()

    def sizeHint(self, option, index):
        base = 24 if self.owner._compact else 36
        return QSize(0, max(20, int(base * self._scale())))

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        r = option.rect
        pill_rect = QRectF(r.adjusted(3, 1, -3, -1))
        if option.state & QStyle.StateFlag.State_Selected:
            path = QPainterPath()
            path.addRoundedRect(pill_rect, 7.0, 7.0)
            painter.fillPath(path, QColor(59, 130, 246, 48))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            path = QPainterPath()
            path.addRoundedRect(pill_rect, 7.0, 7.0)
            painter.fillPath(path, QColor(148, 163, 184, 24))

        colors = self.owner._colors
        name = data.get("name", "") or ""
        dirp = data.get("dir", "") or os.path.dirname(data.get("path", "") or "")
        compact = self.owner._compact

        s = self._scale()
        # 字号设下限：缩放到 0.5 时 9px 会小到不可读
        name_font = _ew_font(max(11, int((11 if compact else 13) * s)), QFont.Weight.Medium)
        dir_font = _ew_font(max(9, int((9 if compact else 10) * s)), QFont.Weight.Normal)

        painter.setPen(QColor(colors["name"]))
        painter.setFont(name_font)
        if compact:
            nw = int(r.width() * 0.55)
            nr = QRect(r.left() + 9, r.top(), nw - 11, r.height())
            dr = QRect(r.left() + nw, r.top(), r.width() - nw - 9, r.height())
            align = Qt.AlignmentFlag.AlignVCenter
            painter.drawText(nr, align | Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideMiddle, nr.width()))
            painter.setPen(QColor(colors["dir"]))
            painter.setFont(dir_font)
            painter.drawText(dr, align | Qt.AlignmentFlag.AlignRight,
                             QFontMetrics(dir_font).elidedText(dirp, Qt.TextElideMode.ElideMiddle, dr.width()))
        else:
            nr = QRect(r.left() + 10, r.top() + 2, r.width() - 20, r.height() // 2)
            dr = QRect(r.left() + 10, r.top() + r.height() // 2, r.width() - 20, r.height() // 2 - 1)
            painter.drawText(nr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideMiddle, nr.width()))
            painter.setPen(QColor(colors["dir"]))
            painter.setFont(dir_font)
            painter.drawText(dr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(dir_font).elidedText(dirp, Qt.TextElideMode.ElideMiddle, dr.width()))
        painter.restore()


class EverythingWidgetWindow(BaseDesktopWidgetWindow):
    """常驻桌面的 Everything 全盘搜索微件。查询在后台线程执行，结果经 Qt 信号回主线程。"""

    size_changed = Signal(str)

    def __init__(self, config_manager=None, parent=None):
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent, config_manager=config_manager)
        self.config_manager = config_manager
        self._sync_glass_from_config()
        self.mode = self._read_size_mode(config_manager)
        self._compact = self.mode == "2x1"
        self.max_results = 4 if self._compact else 12
        if self._compact:
            self.card_w, self.card_h = CARD_W_2X1, CARD_H_2X1
            self._update_window_size()

        self._query_seq = 0
        self._last_status = "idle"
        self._last_items = []
        self._colors = {}

        # 结果回传强制走队列连接，保证槽函数在 GUI 线程执行。
        self._signals = _QuerySignals()
        self._signals.finished.connect(self._on_query_finished, Qt.ConnectionType.QueuedConnection)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="EverythingWidget")
        self._executor_shutdown = False

        self._init_ui()

    @staticmethod
    def _read_size_mode(config_manager):
        if config_manager is None:
            return "2x2"
        cfg = None
        getter = getattr(config_manager, "get_everything_config", None)
        if callable(getter):
            try:
                cfg = getter()
            except Exception:
                cfg = None
        if not isinstance(cfg, dict):
            data = getattr(config_manager, "data", None)
            cfg = data.get("everything_widget") if isinstance(data, dict) else None
        if isinstance(cfg, dict) and cfg.get("size") in ("2x2", "2x1"):
            return cfg["size"]
        return "2x2"

    def _init_ui(self):
        self.content_widget = QWidget(self)
        self.card = self.content_widget
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        self.root_layout = QVBoxLayout(self.content_widget)

        self.search_edit = QLineEdit(self.content_widget)
        self.search_edit.setPlaceholderText("搜索全盘文件…")
        self._search_action = self.search_edit.addAction(
            _make_search_icon(self.theme == "light"),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.textChanged.connect(self._on_text_changed)
        self.search_edit.returnPressed.connect(self._on_return_pressed)
        self.root_layout.addWidget(self.search_edit)

        self.list_widget = QListWidget(self.content_widget)
        self.list_widget.setMouseTracking(True)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.setItemDelegate(_ResultDelegate(self))
        self.root_layout.addWidget(self.list_widget, 1)

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(40)
        self.debounce_timer.timeout.connect(self._trigger_query)

        self._rebuild_layout()
        self._install_drag_forwarding()

    def _install_drag_forwarding(self):
        """输入框和列表会吞掉鼠标事件，导致只有空白处能拖窗口。"""
        self._child_dragging = False
        # 输入框不参与窗口拖动：点输入框就是为了聚焦/选中文本，拖它很违和
        targets = [self.list_widget]
        try:
            targets.append(self.list_widget.viewport())
        except Exception:
            pass
        for obj in targets:
            obj.installEventFilter(self)

    def eventFilter(self, obj, event):
        handled = {getattr(self, "list_widget", None)}
        try:
            handled.add(self.list_widget.viewport())
        except Exception:
            pass
        if obj in handled:
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._child_drag_origin = event.globalPosition().toPoint()
                self._child_drag_win = self.pos()
                self._child_dragging = True
                self._snap_target = None
                # 让 5s 巡检知道用户正在拖，别在拖动中途把窗口搬走
                self._dragging = True
                self._drag_watch.start()
                return False          # 交给子控件，保留选中/双击
            if kind == QEvent.Type.MouseMove and getattr(self, "_child_dragging", False):
                if not (event.buttons() & Qt.MouseButton.LeftButton):
                    self._child_dragging = False
                    self._dragging = False
                    self._drag_watch.stop()
                    self._snap_hide()
                    return False
                if getattr(self, "locked", False):
                    return False
                delta = event.globalPosition().toPoint() - self._child_drag_origin
                if delta.manhattanLength() >= 10:
                    self.move(self._child_drag_win + delta)
                    self._snap_update()
                    return True
                return False
            if kind == QEvent.Type.MouseButtonRelease and getattr(self, "_child_dragging", False):
                self._child_dragging = False
                self._dragging = False
                self._drag_watch.stop()
                if self.pos() != self._child_drag_win:
                    self._snap_commit()
                    self._user_moved_at = time.monotonic()
                else:
                    self._snap_hide()
                self.position_changed.emit(self.x(), self.y())
                return False
        return super().eventFilter(obj, event)

    def _check_drag_released(self):
        was_child_dragging = getattr(self, "_child_dragging", False)
        super()._check_drag_released()
        if was_child_dragging and not self._dragging:
            self._child_dragging = False

    def on_card_resized(self):
        super().on_card_resized()
        # mini 只留搜索框；wide 用紧凑单行结果；regular/tall 用完整列表
        cls = self.size_class()
        # 是否紧凑只看高度：比例宽但够高的卡片不该被压成小字
        compact = self.card_h < 190
        if compact != getattr(self, "_compact", False):
            self._compact = compact
            self.max_results = 4 if compact else 12
            self._rebuild_layout()
            # 重建布局会重写 QSS，必须再缩放一次，否则字号被重置回基准值
            self._apply_responsive_scale()
        self.list_widget.setVisible(cls != "mini")

    def _rebuild_layout(self):
        self.content_widget.setGeometry(MARGIN_SHADOW, MARGIN_SHADOW, self.card_w, self.card_h)
        sp = 4 if self._compact else 6
        self.root_layout.setContentsMargins(14, 12, 14, 12)
        self.root_layout.setSpacing(sp)
        self.root_layout._resp_layout_base = ((14, 12, 14, 12), sp)
        self.search_edit.setFixedHeight(26 if self._compact else 30)
        if hasattr(self.search_edit, "_resp_base"):
            delattr(self.search_edit, "_resp_base")
        self._apply_styles()
        self._rerender()

    def set_size_mode(self, mode: str):
        if mode not in ("2x2", "2x1"):
            mode = "2x2"
        self.mode = mode
        self._compact = mode == "2x1"
        self.max_results = 4 if self._compact else 12
        if self._compact:
            self.card_w, self.card_h = CARD_W_2X1, CARD_H_2X1
        else:
            self.card_w, self.card_h = CARD_W_2X2, CARD_H_2X2
        self._update_window_size()
        self._rebuild_layout()
        self.update()
        self.size_changed.emit(mode)

    def set_theme(self, theme: str):
        super().set_theme(theme)
        self._apply_styles()
        if hasattr(self.search_edit, "_resp_base"):
            delattr(self.search_edit, "_resp_base")
        self._rerender()

    def _apply_styles(self):
        light = self.theme == "light"
        self._colors = {
            "name": "#0f172a" if light else "#f8fafc",
            "dir": "#64748b" if light else "#94a3b8",
            "hint": "#64748b" if light else "#94a3b8",
        }
        if hasattr(self, "_search_action") and self._search_action is not None:
            self._search_action.setIcon(_make_search_icon(light))
        edit_bg = "rgba(255, 255, 255, 0.88)" if light else "rgba(255, 255, 255, 0.09)"
        edit_fg = "#0f172a" if light else "#f8fafc"
        edit_border = "rgba(15, 23, 42, 0.12)" if light else "rgba(255, 255, 255, 0.16)"
        placeholder = "#64748b" if light else "#94a3b8"
        sb_handle = "rgba(15, 23, 42, 0.22)" if light else "rgba(255, 255, 255, 0.24)"
        self.search_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {edit_bg}; color: {edit_fg};"
            f" border: 1px solid {edit_border}; border-radius: 9px;"
            f" padding: 0 10px 0 4px; font-family: {_UI_FONT_CSS}; font-size: 13px;"
            f" selection-background-color: #3b82f6; selection-color: #ffffff; }}"
            f" QLineEdit:focus {{ border: 1px solid #3b82f6; }}"
            f" QLineEdit::placeholder {{ color: {placeholder}; }}"
        )
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; padding: 2px 0;"
            f" font-family: {_UI_FONT_CSS}; outline: none; }}"
            "QListWidget::item { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 4px; background: transparent; margin: 2px 0; }"
            f"QScrollBar::handle:vertical {{ background: {sb_handle}; border-radius: 2px; min-height: 18px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )

    def _render(self, status, items):
        self._last_status = status
        self._last_items = list(items or [])
        self.list_widget.clear()
        if status in _HINTS:
            hint = QListWidgetItem(_HINTS[status])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setFont(_ew_font(12, QFont.Weight.Normal))
            hint.setForeground(QBrush(QColor(self._colors["hint"])))
            self.list_widget.addItem(hint)
            return
        for data in self._last_items[:self.max_results]:
            if not isinstance(data, dict):
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.list_widget.addItem(item)

    def _rerender(self):
        self._render(self._last_status, self._last_items)

    def _on_text_changed(self, text):
        if len(text.strip()) < 2:
            self.debounce_timer.stop()
            self._query_seq += 1  # 作废在途查询，避免旧结果覆盖
            self._render("idle", [])
            return
        self.debounce_timer.start(40)

    def _trigger_query(self):
        if getattr(self, "_executor_shutdown", False):
            return
        query = self.search_edit.text().strip()
        if len(query) < 2:
            return
        self._query_seq += 1
        seq = self._query_seq
        limit = self.max_results
        signals = self._signals

        def task():
            try:
                res = list(search_utils.search_everything_files(query, max_results=limit) or [])
                status = "ok" if res else ("unavailable" if not _everything_available() else "empty")
            except Exception:
                res, status = [], "error"
            try:
                signals.finished.emit(seq, status, res)
            except RuntimeError:
                pass

        try:
            self._executor.submit(task)
        except RuntimeError:
            pass

    @Slot(int, str, object)
    def _on_query_finished(self, seq, status, items):
        if seq != self._query_seq:
            return
        self._render(status, items)

    @staticmethod
    def _item_data(item):
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _on_return_pressed(self):
        item = self.list_widget.currentItem() or self.list_widget.item(0)
        data = self._item_data(item)
        if data is None:
            return
        reveal = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self._open_item(data, reveal)

    def _on_item_double_clicked(self, item):
        self._open_item(self._item_data(item))

    @staticmethod
    def _open_item(data, reveal=False):
        if not isinstance(data, dict):
            return
        path = data.get("path", "") or ""
        if not path:
            return
        try:
            if reveal:
                subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(path)}"])
            else:
                os.startfile(path)
        except Exception:
            pass

    @staticmethod
    def _open_folder(data):
        folder = data.get("dir") or os.path.dirname(data.get("path", "") or "")
        if not folder:
            return
        try:
            os.startfile(folder)
        except Exception:
            try:
                subprocess.Popen(["explorer.exe", folder])
            except Exception:
                pass

    def _on_context_menu(self, pos, global_pos=None):
        data = self._item_data(self.list_widget.itemAt(pos))
        menu = QMenu(self.list_widget)
        menu.setStyleSheet(_get_widget_menu_qss(self.theme))
        act_copy = act_folder = None
        if data is not None:
            menu.addSeparator()
            act_copy = menu.addAction("复制完整路径")
            act_folder = menu.addAction("打开所在文件夹")

        def handle_extra(a):
            if a is act_copy and data is not None:
                QApplication.clipboard().setText(data.get("path", "") or "")
            elif a is act_folder and data is not None:
                self._open_folder(data)

        finish_widget_menu(self, menu,
                           global_pos or self.list_widget.viewport().mapToGlobal(pos),
                           "everything", handle_extra)

    def contextMenuEvent(self, event):
        # 卡片任意位置右键都能锁定，不必先点到结果项
        self._on_context_menu(QPoint(-1, -1), self.mapToGlobal(event.pos()))
        event.accept()

    def _cleanup_resources(self):
        self._query_seq += 1
        if hasattr(self, "debounce_timer") and self.debounce_timer is not None:
            self.debounce_timer.stop()
        if not getattr(self, "_executor_shutdown", False):
            self._executor_shutdown = True
            try:
                self._signals.finished.disconnect(self._on_query_finished)
            except Exception:
                pass
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
        self._child_dragging = False
        super()._cleanup_resources()

    def showEvent(self, event):
        if getattr(self, "_executor_shutdown", False):
            self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="EverythingWidget")
            try:
                self._signals.finished.disconnect(self._on_query_finished)
            except Exception:
                pass
            self._signals.finished.connect(self._on_query_finished, Qt.ConnectionType.QueuedConnection)
            self._executor_shutdown = False
        super().showEvent(event)

    def close_widget(self):
        self._cleanup_resources()
        super().close_widget()

    def closeEvent(self, event):
        self._cleanup_resources()
        super().closeEvent(event)
