"""
grid_snap.py
Grid & Edge Magnetic Snapping Engine for Big Folder Desktop Widgets.
Provides fluid magnetic snapping to screen edges (avoiding MyFinder & MyDock)
and horizontal/vertical alignment (edges, centers, gaps) with adjacent cards.
Dynamically respects QScreen.availableGeometry() and Windows SPI_GETWORKAREA.
"""

import os
import math
from typing import List, Union, Tuple, Optional, Any
from PySide6.QtCore import QPoint, QRect, QSize


_desktop_icon_zone_cache = {"key": None, "value": None, "ts": 0.0}


def get_desktop_icons_exclusion_rect(available_geometry: Optional[QRect] = None) -> Optional[QRect]:
    """桌面图标预留区。拖拽时每次 mouseMove 会调用两次，用 1s TTL 缓存避免反复扫盘。"""
    import time

    now = time.monotonic()
    geo = available_geometry
    key = (geo.x(), geo.y(), geo.width(), geo.height()) if geo is not None else None
    cache = _desktop_icon_zone_cache
    if cache["key"] == key and now - cache["ts"] < 1.0:
        cached = cache["value"]
        return QRect(cached) if cached is not None else None

    result = None
    try:
        user_desk = os.path.expanduser(r"~\Desktop")
        pub_desk = r"C:\Users\Public\Desktop"
        items = []
        for d in [user_desk, pub_desk]:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.lower() in ["desktop.ini", "thumbs.db"]:
                        continue
                    if f.startswith("~") or f.startswith("."):
                        continue
                    items.append(f)
        # 「此电脑 / 回收站 / 网络」等系统图标不在 Desktop 目录里，
        # 但同样占据桌面第一列；桌面几乎不可能一列图标都没有。
        count = max(len(items), 1)

        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else (available_geometry or QRect(0, 0, 1920, 1080))
        if available_geometry is not None:
            avail_geo = available_geometry

        icons_per_col = max(4, avail_geo.height() // 80)
        cols = max(1, min(6, math.ceil(count / float(icons_per_col))))
        zone_w = cols * 105 + 32
        result = QRect(avail_geo.x(), avail_geo.y(), zone_w, avail_geo.height())
    except Exception:
        result = None

    cache.update(key=key, value=QRect(result) if result is not None else None, ts=now)
    return result


def get_all_desktop_card_rects(exclude_window: Optional[Any] = None, include_desktop_icons: bool = True) -> List[QRect]:
    """
    Collects the global visual card rectangles of all active desktop widgets
    (BigFolder windows, Weather widgets, etc.) for pixel-perfect card-to-card magnetic alignment.
    Also incorporates native desktop icons exclusion rect to prevent card-over-icon overlaps.
    """
    from PySide6.QtWidgets import QApplication
    rects: List[QRect] = []
    app = QApplication.instance()
    if not app:
        return rects

    for w in app.topLevelWidgets():
        if w is exclude_window or not w.isVisible():
            continue
        if hasattr(w, "card") and w.card is not None and w.card.isVisible():
            # Map the visual card's pos to global coordinates including bottom title label
            global_card_pos = w.mapToGlobal(w.card.pos())
            extra_h = 0
            if hasattr(w, "title_label") and w.title_label is not None and w.title_label.isVisible():
                extra_h = w.title_label.height() + 12  # Ensure title label below card is never overlapped!
            rects.append(QRect(global_card_pos.x(), global_card_pos.y(), w.card.width(), w.card.height() + extra_h))
        elif hasattr(w, "card_w") and hasattr(w, "card_h"):
            # BaseDesktopWidgetWindow (Clock, SystemMonitor, Memo)
            from desktop_widgets import MARGIN_SHADOW
            global_card_pos = w.mapToGlobal(QPoint(MARGIN_SHADOW, MARGIN_SHADOW))
            rects.append(QRect(global_card_pos.x(), global_card_pos.y(), w.card_w, w.card_h))

    if include_desktop_icons:
        icon_zone = get_desktop_icons_exclusion_rect()
        if icon_zone and not icon_zone.isEmpty():
            rects.append(icon_zone)

    return rects


def calculate_magnetic_snap(
    target_pos: QPoint,
    target_size: Union[QSize, Tuple[int, int]],
    other_rects: Optional[List[QRect]] = None,
    screen_rect: Optional[QRect] = None,
    snap_dist: int = 22,
    margin_top: int = 8,
    margin_bottom: int = 8,
    margin_left: int = 8,
    margin_right: int = 8,
    card_gap: int = 16,
    available_geometry: Optional[QRect] = None
) -> QPoint:
    """
    Calculates magnetic snapping for a moving desktop card.
    
    :param target_pos: Desired top-left position (before snap)
    :param target_size: Size of moving card widget (QSize or (w, h))
    :param other_rects: Geometries of other BigFolder cards on desktop
    :param screen_rect: Screen geometry to snap against (defaults to 1920x1080 if None)
    :param snap_dist: Magnetic threshold in pixels (~20-24px for snappy response)
    :param margin_top: Top desktop boundary padding (8px)
    :param margin_bottom: Bottom desktop boundary padding (8px)
    :param margin_left: Left desktop boundary padding (8px)
    :param margin_right: Right desktop boundary padding (8px)
    :param card_gap: Standard spacing between aligned desktop cards (16px)
    :param available_geometry: Actual available work area (from screen.availableGeometry())
    :return: Magnetically snapped QPoint strictly clamped within screen bounds
    """
    x = target_pos.x()
    y = target_pos.y()
    w = target_size.width() if hasattr(target_size, "width") else target_size[0]
    h = target_size.height() if hasattr(target_size, "height") else target_size[1]

    if screen_rect is None:
        screen_rect = QRect(0, 0, 1920, 1080)

    scr_left = screen_rect.x()
    scr_top = screen_rect.y()
    scr_right = screen_rect.x() + screen_rect.width()
    scr_bottom = screen_rect.y() + screen_rect.height()

    if available_geometry is not None:
        safe_top = available_geometry.y() + margin_top
        safe_bottom = available_geometry.y() + available_geometry.height() - margin_bottom
        safe_left = available_geometry.x() + margin_left
        safe_right = available_geometry.x() + available_geometry.width() - margin_right
    else:
        safe_top = scr_top + margin_top
        safe_bottom = scr_bottom - margin_bottom
        safe_left = scr_left + margin_left
        safe_right = scr_right - margin_right

    # Primary desktop icon exclusion: cards must NEVER snap or be placed into native icons area
    icon_zone = get_desktop_icons_exclusion_rect(available_geometry)
    if icon_zone and not icon_zone.isEmpty():
        if scr_left <= icon_zone.x() < scr_right:
            safe_left = max(safe_left, icon_zone.x() + icon_zone.width() + card_gap)

    # Collect X candidates
    x_candidates = [
        safe_left,
        safe_right - w,
        safe_left + max(0, (safe_right - safe_left - w) // 2)
    ]

    # Collect Y candidates
    y_candidates = [
        safe_top,
        safe_bottom - h,
        safe_top + max(0, (safe_bottom - safe_top - h) // 2)
    ]

    # Track horizontal adjacency to prioritize Top-to-Top level alignment
    near_horizontal_abutting_other = None

    if other_rects:
        for other in other_rects:
            if not isinstance(other, QRect) or other.isEmpty():
                continue
            
            ox = other.x()
            oy = other.y()
            ow = other.width()
            oh = other.height()

            # Horizontal alignment candidates
            x_candidates.append(ox)                       # Left-to-Left align
            x_candidates.append(ox + ow - w)              # Right-to-Right align
            x_candidates.append(ox + (ow - w) // 2)       # Center X align
            x_candidates.append(ox + ow + card_gap)       # Abutting right with standard gap (16px)
            x_candidates.append(ox - w - card_gap)        # Abutting left with standard gap (16px)

            # Detect if user is placing this card side-by-side with 'other'
            if abs(x - (ox + ow + card_gap)) <= snap_dist or abs(x - (ox - w - card_gap)) <= snap_dist:
                near_horizontal_abutting_other = other

            # Vertical alignment candidates
            y_candidates.append(oy)                       # Top-to-Top align (neat row)
            y_candidates.append(oy + oh - h)              # Bottom-to-Bottom align
            y_candidates.append(oy + (oh - h) // 2)       # Center Y align
            y_candidates.append(oy + oh + card_gap)       # Abutting bottom with standard gap (16px)
            y_candidates.append(oy - h - card_gap)        # Abutting top with standard gap (16px)

    # Find closest snap within threshold
    best_dx = snap_dist + 1
    best_x = x
    for cand in x_candidates:
        dx = cand - x
        if abs(dx) <= snap_dist and abs(dx) < abs(best_dx):
            best_dx = dx
            best_x = cand

    best_dy = snap_dist + 1
    best_y = y
    
    # OCD Priority: If placed side-by-side with an adjacent card, magnet-lock tops together
    if near_horizontal_abutting_other is not None:
        other_top = near_horizontal_abutting_other.y()
        if abs(y - other_top) <= int(snap_dist * 1.5):
            best_y = other_top
            best_dy = 0

    if best_dy != 0:
        for cand in y_candidates:
            dy = cand - y
            if abs(dy) <= snap_dist and abs(dy) < abs(best_dy):
                best_dy = dy
                best_y = cand

    # Strict clamp within screen safe bounds: NEVER run outside the screen!
    min_x = safe_left
    max_x = max(min_x, safe_right - w)
    min_y = safe_top
    max_y = max(min_y, safe_bottom - h)

    final_x = max(min_x, min(best_x, max_x))
    final_y = max(min_y, min(best_y, max_y))

    # 磁吸不得把两张卡片吸成完全重合：改落到相邻 16px 槽位，避免「少了一个文件夹」
    snapped_rect = QRect(int(final_x), int(final_y), w, h)
    for other in (other_rects or []):
        if not isinstance(other, QRect) or other.isEmpty():
            continue
        overlap = snapped_rect.intersected(other)
        if overlap.isEmpty():
            continue
        if overlap.width() * overlap.height() >= 0.8 * w * h:
            candidate_x = other.x() + other.width() + card_gap
            if candidate_x + w <= safe_right:
                final_x = max(min_x, candidate_x)
            else:
                final_x = max(min_x, other.x())
                final_y = min(max_y, other.y() + other.height() + card_gap)
            snapped_rect = QRect(int(final_x), int(final_y), w, h)
            break

    return QPoint(int(final_x), int(final_y))


def calculate_magnetic_resize(
    card_pos: QPoint,
    desired_size: Union[QSize, Tuple[int, int]],
    other_rects: Optional[List[QRect]] = None,
    available_geometry: Optional[QRect] = None,
    snap_dist: int = 18,
    card_gap: int = 16,
    extra_h: int = 0
) -> QSize:
    """
    Continuous (无极) Resize Snapping Engine for continuous-curvature desktop cards.
    - Clamps resize to screen work area: visual card can NEVER run outside the screen!
    - Enforces physical collision volume: cannot resize past/through adjacent cards!
    - Magnetically snaps width and height to:
      1) Heights/bottoms of adjacent desktop widgets (e.g. 248px weather/clock widget)
      2) Widths/rights of adjacent cards
      3) Abutting 16px gap to adjacent cards on the right or below
      4) Standard preset sizes (e.g. 248x248 for 2x2/3x3, 496x248 for 2x4, etc.)
    """
    raw_w = desired_size.width() if hasattr(desired_size, "width") else desired_size[0]
    raw_h = desired_size.height() if hasattr(desired_size, "height") else desired_size[1]

    if available_geometry is None:
        available_geometry = QRect(0, 0, 1920, 1080)

    safe_right = available_geometry.x() + available_geometry.width() - 8
    safe_bottom = available_geometry.y() + available_geometry.height() - 8

    # 1. Screen boundaries limit max expansion
    max_allowed_w = max(160, safe_right - card_pos.x())
    max_allowed_h = max(160, safe_bottom - card_pos.y() - extra_h)

    # 2. Collision volume limits: check if expanding into adjacent cards to the right or below
    if other_rects:
        for other in other_rects:
            if not isinstance(other, QRect) or other.isEmpty():
                continue
            # Card to the right that overlaps in vertical span
            if other.x() > card_pos.x() + 40:
                is_vert_overlap = not (other.bottom() < card_pos.y() or other.top() > card_pos.y() + raw_h)
                if is_vert_overlap:
                    limit_w = other.x() - card_pos.x() - card_gap
                    if limit_w >= 160:
                        max_allowed_w = min(max_allowed_w, limit_w)

            # Card below that overlaps in horizontal span
            if other.y() > card_pos.y() + 40:
                is_horiz_overlap = not (other.right() < card_pos.x() or other.left() > card_pos.x() + raw_w)
                if is_horiz_overlap:
                    limit_h = other.y() - card_pos.y() - card_gap - extra_h
                    if limit_h >= 160:
                        max_allowed_h = min(max_allowed_h, limit_h)

    # 3. Magnetic snap candidates for width
    w_candidates = [165, 248, 332, 415, 498]  # Standard desktop preset card sizes
    if other_rects:
        for other in other_rects:
            if not isinstance(other, QRect) or other.isEmpty():
                continue
            w_candidates.append(other.width())
            if other.x() > card_pos.x():
                w_candidates.append(other.x() + other.width() - card_pos.x())  # Align right edge
                w_candidates.append(other.x() - card_pos.x() - card_gap)       # Abutting right

    # 4. Magnetic snap candidates for height
    h_candidates = [120, 165, 248, 332, 415, 498]  # Standard desktop preset card sizes (248px widget match)
    if other_rects:
        for other in other_rects:
            if not isinstance(other, QRect) or other.isEmpty():
                continue
            h_candidates.append(other.height())
            if other.y() > card_pos.y():
                h_candidates.append(other.y() + other.height() - card_pos.y() - extra_h)  # Align bottom edge
                h_candidates.append(other.y() - card_pos.y() - card_gap - extra_h)        # Abutting bottom

    # Find best snap for width
    best_w = raw_w
    best_dw = snap_dist + 1
    for cw in w_candidates:
        if cw > max_allowed_w:
            continue
        dw = cw - raw_w
        if abs(dw) <= snap_dist and abs(dw) < abs(best_dw):
            best_dw = dw
            best_w = cw

    # Find best snap for height
    best_h = raw_h
    best_dh = snap_dist + 1
    for ch in h_candidates:
        if ch > max_allowed_h:
            continue
        dh = ch - raw_h
        if abs(dh) <= snap_dist and abs(dh) < abs(best_dh):
            best_dh = dh
            best_h = ch

    final_w = max(160, min(max_allowed_w, best_w))
    final_h = max(160, min(max_allowed_h, best_h))

    return QSize(int(final_w), int(final_h))


def resolve_card_collision(
    target_rect: QRect,
    other_rects: Optional[List[QRect]] = None,
    available_geometry: Optional[QRect] = None,
    card_gap: int = 16
) -> QPoint:
    """
    Guarantees that a desktop card never overlaps another card.
    If target_rect intersects any card in other_rects, calculates the optimal
    non-overlapping magnetic position (right, left, below, above) respecting
    card_gap (16px) and screen work area with zero off-by-one errors.
    """
    other_rects = list(other_rects) if other_rects else []

    if available_geometry is None:
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    safe_left = available_geometry.x() + 8
    safe_top = available_geometry.y() + 8
    safe_right = available_geometry.x() + available_geometry.width() - 8
    safe_bottom = available_geometry.y() + available_geometry.height() - 8

    # Prevent overlap with desktop shortcuts zone
    icon_zone = get_desktop_icons_exclusion_rect(available_geometry)
    if icon_zone and not icon_zone.isEmpty():
        if not any(o == icon_zone for o in other_rects):
            other_rects.append(icon_zone)
        if safe_left <= icon_zone.x() < safe_right:
            safe_left = max(safe_left, icon_zone.x() + icon_zone.width() + card_gap)

    colliding = [o for o in other_rects if isinstance(o, QRect) and not o.isEmpty() and target_rect.intersects(o)]
    if not colliding:
        return target_rect.topLeft()

    tw = target_rect.width()
    th = target_rect.height()

    candidates: List[QPoint] = []
    for other in colliding:
        ox = other.x()
        oy = other.y()
        ow = other.width()
        oh = other.height()

        # Candidate 1: Abutting Right (aligned top)
        candidates.append(QPoint(ox + ow + card_gap, oy))
        # Candidate 2: Abutting Left (aligned top)
        candidates.append(QPoint(ox - tw - card_gap, oy))
        # Candidate 3: Abutting Bottom (aligned left)
        candidates.append(QPoint(ox, oy + oh + card_gap))
        # Candidate 4: Abutting Top (aligned left)
        candidates.append(QPoint(ox, oy - th - card_gap))
        # Candidate 5: Abutting Right (same Y as target)
        candidates.append(QPoint(ox + ow + card_gap, target_rect.top()))
        # Candidate 6: Abutting Left (same Y as target)
        candidates.append(QPoint(ox - tw - card_gap, target_rect.top()))
        # Candidate 7: Abutting Bottom (same X as target)
        candidates.append(QPoint(target_rect.left(), oy + oh + card_gap))
        # Candidate 8: Abutting Top (same X as target)
        candidates.append(QPoint(target_rect.left(), oy - th - card_gap))

    # Score candidates
    scored = []
    for pt in candidates:
        cand_rect = QRect(pt, target_rect.size())
        # Must not intersect ANY other card
        if any(cand_rect.intersects(o) for o in other_rects):
            continue
        in_bounds = (
            cand_rect.left() >= safe_left and
            cand_rect.right() <= safe_right and
            cand_rect.top() >= safe_top and
            cand_rect.bottom() <= safe_bottom
        )
        dist = (pt - target_rect.topLeft()).manhattanLength()
        penalty = 0 if in_bounds else 10000
        scored.append((dist + penalty, pt))

    if scored:
        scored.sort(key=lambda x: x[0])
        return scored[0][1]

    # Fallback: if all immediate spots collide, push to the right or below of the bounding union
    rightmost = max(o.x() + o.width() for o in other_rects)
    cand_right = QPoint(rightmost + card_gap, target_rect.top())
    if cand_right.x() + tw <= safe_right:
        return cand_right

    bottommost = max(o.y() + o.height() for o in other_rects)
    return QPoint(target_rect.left(), bottommost + card_gap)

