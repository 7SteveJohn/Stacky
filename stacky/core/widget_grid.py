"""widget_grid.py — 桌面组件网格。

桌面组件的摆放规则：
1. 组件落在一条隐形网格上，网格间距固定，绝不互相压叠；
2. 拖动时只给出「下一个合法格点」的白色轮廓预览，组件本体跟手，松手才落位；
3. 靠近其它组件时优先贴到它的左边/右边/上边/下边，保持整齐的等距间隙；
4. 双击组件＝自动放进从左到右第一个空位。

这里把这套规则移植到「大文件夹卡片 + 小组件」共用的桌面：
- 网格线来自文件夹卡片的边缘（卡片左/右 + gap、上/下 + gap）与屏幕留白；
- 组件尺寸保持原样，只对齐边缘，因此 2x2 与 2x1 能和卡片列对齐；
- 拖拽期只算预览、不挪窗口，从根上避免每帧吸附造成的抽搐。
"""

import sys
from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from icon_utils import create_squircle_path

GAP = 16          # 卡片 / 组件之间的标准间距
SHADOW = 24       # 组件窗口四周的软阴影边距
RADIUS = 26       # 卡片圆角，预览轮廓与卡片保持一致

_folder_rect_provider = None


def set_folder_rect_provider(provider):
    """由 BigFolderWindowManager 注册：返回所有文件夹卡片的全局矩形（含标题条）。"""
    global _folder_rect_provider
    _folder_rect_provider = provider


def folder_card_rects() -> List[QRect]:
    if _folder_rect_provider is None:
        return []
    try:
        return [r for r in _folder_rect_provider() if isinstance(r, QRect) and not r.isEmpty()]
    except Exception:
        return []


def widget_card_rects(exclude_window=None) -> List[QRect]:
    """所有已显示小组件的全局卡片矩形（不含阴影边距）。"""
    rects = []
    app = QApplication.instance()
    if app is None:
        return rects
    for w in app.topLevelWidgets():
        if w is exclude_window or not w.isVisible():
            continue
        if hasattr(w, "card_w") and hasattr(w, "card_h"):
            rects.append(QRect(w.pos() + QPoint(SHADOW, SHADOW), QSize(w.card_w, w.card_h)))
    return rects


def obstacles_for(exclude_window=None) -> List[QRect]:
    return static_obstacles() + widget_card_rects(exclude_window)


def static_obstacles() -> List[QRect]:
    """桌面上不归小组件用的区域：文件夹卡片 + 原生桌面图标列。"""
    return folder_card_rects() + icon_zone_rects()


def icon_zone_rects() -> List[QRect]:
    try:
        from grid_snap import get_desktop_icons_exclusion_rect
        zone = get_desktop_icons_exclusion_rect(available_geometry())
        if zone is not None and not zone.isEmpty():
            return [zone]
    except Exception:
        pass
    return []


def available_geometry() -> QRect:
    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)


def _inside_card(rect: QRect, avail: QRect) -> bool:
    return (rect.left() >= avail.left() + SHADOW
            and rect.top() >= avail.top() + SHADOW
            and rect.right() <= avail.right() - SHADOW
            and rect.bottom() <= avail.bottom() - SHADOW)


def _x_lines(obstacles: List[QRect], avail: QRect, w: int) -> List[int]:
    xs = {avail.left() + SHADOW, avail.right() - SHADOW - w + 1}
    for o in obstacles:
        xs.add(o.left())
        xs.add(o.right() + 1 + GAP)
        xs.add(o.left() - GAP - w)
    return sorted(xs)


def _y_lines(obstacles: List[QRect], avail: QRect, h: int) -> List[int]:
    ys = {avail.top() + SHADOW, avail.bottom() - SHADOW - h + 1}
    for o in obstacles:
        ys.add(o.top())
        ys.add(o.bottom() + 1 + GAP)
        ys.add(o.top() - GAP - h)
    return sorted(ys)


def _fits(rect: QRect, obstacles: List[QRect], avail: QRect) -> bool:
    if not _inside_card(rect, avail):
        return False
    return not any(rect.intersects(o) for o in obstacles)


def snap_card_rect(card_rect: QRect, obstacles: List[QRect],
                   avail: Optional[QRect] = None) -> Optional[QRect]:
    """把卡片矩形吸到最近的合法格点；没有任何合法位置时返回 None。

    只挪动位置、不改尺寸：X 只对齐列线，Y 只对齐行线，两条轴互不牵连，
    所以组件永远不会被斜着甩出去。
    """
    if avail is None:
        avail = available_geometry()
    w, h = card_rect.width(), card_rect.height()

    best = None
    for x in _x_lines(obstacles, avail, w):
        for y in _y_lines(obstacles, avail, h):
            cand = QRect(x, y, w, h)
            if not _fits(cand, obstacles, avail):
                continue
            dist = abs(x - card_rect.x()) + abs(y - card_rect.y())
            if best is None or dist < best[0]:
                best = (dist, cand)
                if dist == 0:
                    return cand
    if best is not None:
        return best[1]
    return _nearest_free_scan(card_rect, obstacles, avail)


def _nearest_free_scan(card_rect: QRect, obstacles: List[QRect],
                       avail: QRect) -> Optional[QRect]:
    """格线全被占满时，按 16px 步长找离当前位置最近的可放置点。

    大文件夹卡片在密集桌面上常找不到空网格；没有这层兜底，预览会直接消失，
    用户就看不到「碰撞后我会去哪里」。
    """
    w, h = card_rect.width(), card_rect.height()
    x0, y0 = card_rect.x(), card_rect.y()
    best = None
    for x in range(avail.left() + SHADOW, avail.right() - SHADOW - w + 2, GAP):
        for y in range(avail.top() + SHADOW, avail.bottom() - SHADOW - h + 2, GAP):
            dist = abs(x - x0) + abs(y - y0)
            if best is not None and dist >= best[0]:
                continue
            cand = QRect(x, y, w, h)
            if _fits(cand, obstacles, avail):
                best = (dist, cand)
    return best[1] if best else None


def drop_target(card_rect: QRect, obstacles: List[QRect],
                avail: Optional[QRect] = None) -> QRect:
    """唯一的拖动落点入口：文件夹卡片与小组件共用，永远给出一个确定落点。

    先取最近合法格点；网格全被占满时退化到连续空间贴边避让。
    白框画在返回的矩形上，松手也落到这里 —— 不返回 None，预览不会凭空消失。
    """
    if avail is None:
        avail = available_geometry()
    target = snap_card_rect(card_rect, obstacles, avail)
    if target is not None:
        return target
    from grid_snap import resolve_card_collision
    return QRect(resolve_card_collision(card_rect, obstacles, avail), card_rect.size())


def next_free_slot(size: QSize, obstacles: List[QRect],
                   avail: Optional[QRect] = None) -> Optional[QRect]:
    """从左到右、从上到下第一个放得下的格点（双击组件自动入位）。"""
    if avail is None:
        avail = available_geometry()
    for y in _y_lines(obstacles, avail, size.height()):
        for x in _x_lines(obstacles, avail, size.width()):
            cand = QRect(x, y, size.width(), size.height())
            if _fits(cand, obstacles, avail):
                return cand
    return None


class SnapDragMixin:
    """拖动期的网格预览 + 松手落位。宿主窗口需要提供 _snap_card_rect()。"""

    def _snap_init(self):
        self._snap_ghost = None
        self._snap_target = None

    def _snap_card_rect(self) -> QRect:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _snap_update(self):
        """拖动中：只更新白色轮廓预览，窗口本身不动，避免每帧吸附抽搐。"""
        card = self._snap_card_rect()
        target = drop_target(card, obstacles_for(exclude_window=self))
        self._snap_target = target
        if self._snap_ghost is None:
            self._snap_ghost = DropOutline()
        self._snap_ghost.show_slot(target, RADIUS)

    def _snap_commit(self) -> bool:
        """松手：落到预览位置。返回是否移动过。"""
        target = self._snap_target
        self._snap_hide()
        self._snap_target = None
        if target is None:
            return False
        if target.topLeft() == self._snap_card_rect().topLeft():
            return False
        self.move(target.topLeft() - QPoint(SHADOW, SHADOW))
        return True

    def _snap_hide(self):
        if self._snap_ghost is not None:
            self._snap_ghost.hide()


class DropOutline(QWidget):
    """唯一的拖动落位白框：文件夹卡片与小组件共用，语义恒为「松手就落这里」。"""

    PAD = 2  # 抗锯齿缓冲，防止 2px 轮廓被窗口边界裁掉

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._radius = RADIUS
        self._make_click_through()

    def _make_click_through(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def show_slot(self, card_rect: QRect, radius: int = RADIUS):
        self._radius = int(radius)
        self.setGeometry(card_rect)
        # 只在首次显示时置顶。每帧 raise_ 会反复改变 Z 序，容易让宿主窗口
        # 丢失 mouseReleaseEvent，表现为「松手了组件还粘在光标上」。
        if not self.isVisible():
            self.show()
            self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p = self.PAD
        card = self.rect().adjusted(p, p, -p - 1, -p - 1)
        if card.width() <= 4 or card.height() <= 4:
            painter.end()
            return
        path = create_squircle_path(card, self._radius)
        painter.fillPath(path, QColor(255, 255, 255, 16))
        painter.setPen(QPen(QColor(255, 255, 255, 165), 2))
        painter.drawPath(path)
        painter.end()
