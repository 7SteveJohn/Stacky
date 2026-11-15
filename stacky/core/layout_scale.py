"""桌面布局的分辨率归一化。

config 里的坐标与尺寸属于「布局画布」(layout_screen_w / layout_screen_h)，
即用户最后一次保存布局时所在屏幕的可用区域。运行时按当前屏幕等比换算成
显示坐标；写回 config 时反向换算，保证任意分辨率下相对布局不变。
"""

from typing import Optional, Tuple

MIN_SCALE = 0.30
MAX_SCALE = 2.50
COMMON_SCREENS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080),
                  (2560, 1440), (2880, 1620), (3840, 2160))


def infer_canvas(rects, screen: Tuple[int, int]) -> Tuple[int, int]:
    """首次记录画布：当前屏幕放得下就用当前屏幕，放不下说明布局来自更大的屏幕。

    rects 为 config 里的 (x, y, w, h)。命中不了常见分辨率时按包围盒向上取整，
    因此不依赖某一个固定分辨率。
    """
    rects = list(rects)
    if not rects:
        return screen
    right = max(float(r[0]) + float(r[2]) for r in rects)
    bottom = max(float(r[1]) + float(r[3]) for r in rects)
    if right <= screen[0] and bottom <= screen[1]:
        return screen
    for w, h in COMMON_SCREENS:
        if w >= right and h >= bottom:
            return w, h
    return (int((right + 40) // 16 + 1) * 16,
            int((bottom + 40) // 16 + 1) * 16)


def compute_scale(canvas: Tuple[float, float], screen: Tuple[float, float]) -> float:
    """等比缩放：取两轴较小比例，任何宽高比都不越界、卡片不变形。"""
    cw, ch = float(canvas[0]), float(canvas[1])
    sw, sh = float(screen[0]), float(screen[1])
    if cw <= 0 or ch <= 0 or sw <= 0 or sh <= 0:
        return 1.0
    return max(MIN_SCALE, min(MAX_SCALE, min(sw / cw, sh / ch)))


def to_screen(value, scale: float) -> int:
    return int(round(float(value) * scale))


def to_canvas(value, scale: float) -> int:
    return int(round(float(value) / scale)) if scale > 0 else int(value)


def current_screen_size() -> Optional[Tuple[int, int]]:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return None
    scr = app.primaryScreen()
    if scr is None:
        return None
    geo = scr.availableGeometry()
    return geo.width(), geo.height()


def scale_for_config(config_manager) -> float:
    """未记录画布时返回 1.0：首次运行完全沿用既有绝对坐标，不改动用户布局。"""
    w = config_manager.get("layout_screen_w", 0)
    h = config_manager.get("layout_screen_h", 0)
    if not w or not h:
        return 1.0
    size = current_screen_size()
    if size is None:
        return 1.0
    return compute_scale((w, h), size)
