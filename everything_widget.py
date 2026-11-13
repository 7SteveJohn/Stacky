"""everything_widget.py - 常驻桌面的 Everything 全盘搜索小组件 (2x2 / 2x1)。"""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QObject, QSize, QRect, QEvent, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QStyledItemDelegate, QStyle,
)

from desktop_widgets import (
    BaseDesktopWidgetWindow, CARD_W_2X2, CARD_H_2X2,
    CARD_W_2X1, CARD_H_2X1, MARGIN_SHADOW, finish_widget_menu,
)
from icon_utils import create_squircle_path, draw_liquid_glass
import search_utils

_HINTS = {
    "idle": "输入 2 个字开始搜索",
    "empty": "无匹配结果",
    "unavailable": "Everything 未运行或 IPC 无响应",
    "error": "检索异常，详见 %TEMP%\\stacky_search.log",
}


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
        base = 22 if self.owner._compact else 34
        return QSize(0, max(20, int(base * self._scale())))

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            super().paint(painter, option, index)
            return

        painter.save()
        r = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(r, QColor(59, 130, 246, 55))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(r, QColor(128, 128, 128, 22))

        colors = self.owner._colors
        name = data.get("name", "") or ""
        dirp = data.get("dir", "") or os.path.dirname(data.get("path", "") or "")
        compact = self.owner._compact

        s = self._scale()
        # 字号设下限：缩放到 0.5 时 9px 会小到不可读
        name_font = QFont()
        name_font.setPixelSize(max(11, int((11 if compact else 13) * s)))
        dir_font = QFont()
        dir_font.setPixelSize(max(9, int((9 if compact else 10) * s)))

        painter.setPen(QColor(colors["name"]))
        painter.setFont(name_font)
        if compact:
            nw = int(r.width() * 0.55)
            nr = QRect(r.left() + 6, r.top(), nw - 8, r.height())
            dr = QRect(r.left() + nw, r.top(), r.width() - nw - 6, r.height())
            align = Qt.AlignmentFlag.AlignVCenter
            painter.drawText(nr, align | Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(name_font).elidedText(name, Qt.TextElideMode.ElideMiddle, nr.width()))
            painter.setPen(QColor(colors["dir"]))
            painter.setFont(dir_font)
            painter.drawText(dr, align | Qt.AlignmentFlag.AlignRight,
                             QFontMetrics(dir_font).elidedText(dirp, Qt.TextElideMode.ElideMiddle, dr.width()))
        else:
            nr = QRect(r.left() + 8, r.top() + 2, r.width() - 16, r.height() // 2)
            dr = QRect(r.left() + 8, r.top() + r.height() // 2, r.width() - 16, r.height() // 2)
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
        super().__init__(CARD_W_2X2, CARD_H_2X2, parent)
        self.config_manager = config_manager
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
        self.search_edit.setPlaceholderText("搜索全盘文件")
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
                return False          # 交给子控件，保留选中/双击
            if kind == QEvent.Type.MouseMove and getattr(self, "_child_dragging", False):
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
                if self.pos() != self._child_drag_win:
                    self._snap_commit()
                    self._user_moved_at = time.monotonic()
                else:
                    self._snap_hide()
                self.position_changed.emit(self.x(), self.y())
                return False
        return super().eventFilter(obj, event)

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
        self.root_layout.setContentsMargins(14, 12, 14, 12)
        self.root_layout.setSpacing(4 if self._compact else 6)
        self.search_edit.setFixedHeight(26 if self._compact else 30)
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
        self._rerender()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = create_squircle_path(self.get_card_rect(), self.radius)
        if self.theme == "light":
            fill, pen = QColor(250, 250, 252, 208), QColor(255, 255, 255, 90)
        elif self.theme == "oled":
            fill, pen = QColor(10, 11, 14, 200), QColor(255, 255, 255, 34)
        else:
            fill, pen = QColor(24, 27, 36, 208), QColor(255, 255, 255, 46)
        painter.fillPath(path, fill)
        draw_liquid_glass(painter, path, self.get_card_rect(), self.radius, self.theme)

    def _apply_styles(self):
        light = self.theme == "light"
        self._colors = {
            "name": "#0f172a" if light else "#f8fafc",
            "dir": "#475569" if light else "#a8b6c8",
            "hint": "#334155" if light else "#e2e8f0",
        }
        edit_bg = "rgba(255,255,255,0.90)" if light else "rgba(255,255,255,0.13)"
        edit_fg = "#0f172a" if light else "#f8fafc"
        edit_border = "rgba(0,0,0,0.12)" if light else "rgba(255,255,255,0.22)"
        placeholder = "#64748b" if light else "#94a3b8"
        self.search_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {edit_bg}; color: {edit_fg};"
            f" border: 1px solid {edit_border}; border-radius: 10px;"
            f" padding: 0 12px; font-size: 13px; selection-background-color: #3b82f6; }}"
            f" QLineEdit:focus {{ border: 1px solid #3b82f6; }}"
            f" QLineEdit::placeholder {{ color: {placeholder}; }}"
        )
        self.list_widget.setStyleSheet(
            "QListWidget { background: transparent; border: none; padding: 1px 0; }"
            "QListWidget::item { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 4px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(128,128,128,0.35); border-radius: 2px; }"
        )

    def _render(self, status, items):
        self._last_status = status
        self._last_items = list(items or [])
        self.list_widget.clear()
        if status in _HINTS:
            hint = QListWidgetItem(_HINTS[status])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
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
            signals.finished.emit(seq, status, res)

        self._executor.submit(task)

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
        menu.setStyleSheet(
            "QMenu { background-color: #1e212b; color: #f1f5f9;"
            " border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px; }"
            " QMenu::item { padding: 6px 18px; border-radius: 6px; }"
            " QMenu::item:selected { background-color: rgba(255,255,255,0.12); }"
        )
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

    def close_widget(self):
        self.debounce_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().close_widget()
