import os
import sys
import re
import glob
import hashlib
from collections import OrderedDict
import ctypes
import winreg
from ctypes import wintypes
from typing import Union, Optional, Tuple, List, Dict, Any
from PySide6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QFont, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath, QIcon
)
from PySide6.QtCore import Qt, QRectF, QRect, QPointF

class SHFILEINFO(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]

IID_IImageList = GUID(0x46EB5926, 0x582E, 0x4017, (ctypes.c_byte * 8)(0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50))

SHGFI_SYSICONINDEX = 0x000004000
SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHIL_EXTRALARGE = 0x2  # 48x48
SHIL_JUMBO = 0x4       # 256x256
ILD_TRANSPARENT = 0x00000001

def get_default_icon_cache_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        portable_cache = os.path.join(exe_dir, ".cache_icons")
        if os.path.exists(portable_cache) or os.access(exe_dir, os.W_OK):
            return portable_cache
        appdata_dir = os.path.join(os.environ.get("LOCALAPPDATA", exe_dir), "BigFolderLauncher")
        return os.path.join(appdata_dir, ".cache_icons")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache_icons")

CACHE_DIR = get_default_icon_cache_dir()

def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass

def resolve_actual_path(file_path: str) -> str:
    """Expands environment variables, resolves common system paths, and auto-locates moved files on Desktop"""
    if not file_path:
        return ""
    expanded = os.path.expandvars(file_path)
    if os.path.exists(expanded):
        return expanded
    
    # Check User Desktop if moved during folder dissolution
    desktop_candidate = os.path.join(os.path.expanduser(r"~\Desktop"), os.path.basename(file_path))
    if os.path.exists(desktop_candidate):
        return desktop_candidate

    # Check Stacky storage root and category subdirectories
    stacky_root = os.path.expandvars(r"%USERPROFILE%\Stacky")
    if os.path.exists(stacky_root):
        base_f = os.path.basename(file_path)
        for cat in os.listdir(stacky_root):
            cat_f = os.path.join(stacky_root, cat, base_f)
            if os.path.exists(cat_f):
                return cat_f

    windir = os.environ.get("windir", r"C:\Windows")
    p1 = os.path.join(windir, file_path)
    if os.path.exists(p1):
        return p1
    p2 = os.path.join(windir, "system32", file_path)
    if os.path.exists(p2):
        return p2

    return expanded

def resolve_url_shortcut(file_path: str) -> str:
    """读取 .url 里的 URL 协议。

    os.startfile(托管目录内的 .url 文件) 常常无反应，而 Explorer 双击同一个文件能打开；
    把 URL 直接交给 ShellExecute 才与资源管理器行为一致（Steam / 网页快捷方式）。
    """
    if not file_path.lower().endswith(".url"):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if s.lower().startswith("url="):
                    return s[4:].strip()
    except Exception:
        pass
    return ""


def resolve_shortcut_target(file_path: str) -> str:
    """
    If file_path is a Windows shortcut (.lnk), resolves the target executable path via WScript.Shell.
    Returns target executable if found, otherwise returns resolved actual path.
    """
    if not file_path:
        return ""
    real_path = resolve_actual_path(file_path)
    if real_path.lower().endswith(".lnk") and os.path.exists(real_path):
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(real_path)
            target = shortcut.TargetPath
            if target and os.path.exists(target):
                return target
        except Exception:
            pass
    return real_path

def resolve_shortcut_icon_location(file_path: str) -> Optional[Tuple[str, int]]:
    """
    If file_path is a Windows shortcut (.lnk), extracts IconLocation via WScript.Shell.
    Returns (icon_file, icon_index) if specified and exists, otherwise None.
    """
    if not file_path or not file_path.lower().endswith(".lnk"):
        return None
    real_path = resolve_actual_path(file_path)
    if not os.path.exists(real_path):
        return None
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(real_path)
        loc = shortcut.IconLocation
        if loc:
            parts = loc.split(",")
            icon_file = parts[0].strip()
            icon_idx = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            icon_file_resolved = resolve_actual_path(icon_file)
            if icon_file_resolved and os.path.exists(icon_file_resolved):
                return icon_file_resolved, icon_idx
    except Exception:
        pass
    return None


def _read_shortcut_properties(lnk_path: str) -> Optional[Dict[str, Any]]:
    """读取 .lnk 的原始属性，不对路径做存在性过滤。"""
    try:
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk_path)
        return {
            "target": sc.TargetPath or "",
            "args": sc.Arguments or "",
            "workdir": sc.WorkingDirectory or "",
            "desc": sc.Description or "",
            "icon": sc.IconLocation or "",
            "hotkey": sc.Hotkey or "",
            "style": sc.WindowStyle,
        }
    except Exception:
        return None


def _sibling_icon_for_exe(exe_path: str) -> str:
    """Electron / Tauri 类应用 exe 常无内嵌图标，真图标放在同目录 app.ico / icon.ico。"""
    folder = os.path.dirname(exe_path)
    for name in ("app.ico", "icon.ico"):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _relocate_stale_icon(icon_file: str, target: str, args: str = "") -> str:
    """图标路径失效时（应用升级换版本目录或改文件名），重新定位同一应用的图标。"""
    base = os.path.basename(icon_file)
    if not base:
        return ""
    names = {base}
    # 启动器常用 --game=hkrpg_cn 之类参数区分产品；升级后 ico 改名也能按 token 找回
    for token in re.findall(r"[A-Za-z0-9_-]{3,}", args or ""):
        if token.lower() not in ("game", "app", "run", "launch", "true", "false"):
            names.add(token + ".ico")
            names.add(token + ".png")
    roots = []
    if target and os.path.isfile(target):
        roots.append(os.path.dirname(target))
    version_dir = os.path.dirname(os.path.dirname(icon_file))
    parent_root = os.path.dirname(version_dir)
    if parent_root and parent_root not in roots:
        roots.append(parent_root)
    candidates = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for name in names:
            for pattern in (os.path.join(root, "*", "ico", name),
                            os.path.join(root, "*", name),
                            os.path.join(root, "ico", name)):
                candidates.extend(p for p in glob.glob(pattern) if os.path.isfile(p))
    if not candidates:
        return ""
    return max(set(candidates), key=os.path.getmtime)


def heal_shortcut_icon(lnk_path: str, allow_target_fallback: bool = False) -> str:
    """自愈 .lnk 的 IconLocation，返回写入的新值；无需修改或失败返回 ''。

    - 图标文件失效（应用升级换了版本目录/文件名）：重新定位并写回。
    - 目标 exe 无内嵌图标：改用同目录 app.ico / icon.ico。
    - allow_target_fallback：空 IconLocation 时按统一解析补一个**确实存在**的图标源
      （仅在把文件放回桌面时使用；无可靠图标时宁可留空，绝不补白纸图标）。
    """
    if not lnk_path or not lnk_path.lower().endswith(".lnk") or not os.path.exists(lnk_path):
        return ""
    props = _read_shortcut_properties(lnk_path)
    if not props:
        return ""
    loc = (props["icon"] or "").strip()
    icon_file = loc.split(",")[0].strip()
    target = (props["target"] or "").strip()
    new_file = ""

    if icon_file and not os.path.exists(icon_file):
        new_file = _relocate_stale_icon(icon_file, target, props.get("args", ""))
    elif not icon_file and target and os.path.exists(target):
        try:
            icon_count = ctypes.windll.shell32.ExtractIconExW(
                ctypes.c_wchar_p(target), -1, None, None, 0)
        except Exception:
            icon_count = 1
        if icon_count == 0:
            new_file = _sibling_icon_for_exe(target)
        if not new_file and allow_target_fallback:
            inferred = "" if target.lower().endswith(".lnk") else resolve_icon_location_for_path(target)
            new_file = inferred.split(",")[0].strip() if inferred else ""

    if not new_file or not os.path.exists(new_file):
        return ""
    tail = loc.split(",", 1)[1].strip() if "," in loc else ""
    idx = int(tail) if tail.isdigit() else 0
    new_loc = f"{new_file},{idx}"
    if new_loc == loc:
        return ""

    try:
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk_path)
        sc.IconLocation = new_loc
        sc.Save()
        return new_loc
    except Exception:
        pass

    # 只读/ACL 限制下原子重建，保留原有全部字段
    backup = lnk_path + ".tmp_heal"
    try:
        os.replace(lnk_path, backup)
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk_path)
        sc.TargetPath = props["target"]
        sc.Arguments = props["args"]
        sc.WorkingDirectory = props["workdir"]
        sc.Description = props["desc"]
        sc.Hotkey = props["hotkey"]
        sc.WindowStyle = props["style"]
        sc.IconLocation = new_loc
        sc.Save()
        if os.path.exists(backup):
            os.remove(backup)
        return new_loc
    except Exception:
        if os.path.exists(backup):
            try:
                os.replace(backup, lnk_path)
            except Exception:
                pass
    return ""


def resolve_icon_location_for_path(path: str) -> str:
    """统一的图标源解析入口：返回可直接写入 .lnk IconLocation 的字符串。

    显示层、快捷方式生成层、自愈层都走这里，避免各写一套导致图标不一致。
    找不到可靠图标时返回 ''（绝不把无内嵌图标的 exe 补成白纸图标）。
    """
    if not path:
        return ""
    real = resolve_actual_path(path)
    if not os.path.exists(real):
        return ""
    low = real.lower()
    if low.endswith(".lnk"):
        props = _read_shortcut_properties(real)
        if not props:
            return ""
        loc = (props["icon"] or "").strip()
        icon_file = loc.split(",")[0].strip()
        if icon_file and not os.path.exists(icon_file):
            return heal_shortcut_icon(real)
        if icon_file:
            return loc
        target = (props["target"] or "").strip()
        if target and not target.lower().endswith(".lnk") and os.path.exists(target):
            return resolve_icon_location_for_path(target)
        return ""
    if low.endswith((".ico", ".png", ".bmp")):
        return f"{real},0"
    if low.endswith((".exe", ".dll")):
        try:
            count = ctypes.windll.shell32.ExtractIconExW(
                ctypes.c_wchar_p(real), -1, None, None, 0)
        except Exception:
            count = 1
        if count > 0:
            return f"{real},0"
        sibling = _sibling_icon_for_exe(real)
        return f"{sibling},0" if sibling else ""
    return ""


def resolve_shortcut_icon_pixmap(lnk_path: str, target_size: int):
    """按 .lnk 的 IconLocation 取图。

    - .ico 直接用（米哈游各游戏快捷方式把专属 ico 写在这里）
    - .exe/.dll 无内嵌图标时回退同目录 app.ico（Ollama 等 Electron 应用）
    - 取不到返回 None，交给原来的 UWP / Shell 流程
    """
    info = resolve_shortcut_icon_location(lnk_path)
    if not info:
        healed = heal_shortcut_icon(lnk_path)
        if healed:
            info = (healed.split(",")[0].strip(), 0)
    if not info:
        return None
    icon_file, _ = info
    if not icon_file or not os.path.exists(icon_file):
        return None

    if icon_file.lower().endswith(".ico"):
        pm = QIcon(icon_file).pixmap(target_size, target_size)
        if pm.isNull():
            pm = QPixmap(icon_file)
        return None if pm.isNull() else pm

    def sibling_ico():
        candidate = _sibling_icon_for_exe(icon_file)
        if not candidate:
            return None
        pm = QIcon(candidate).pixmap(target_size, target_size)
        if pm.isNull():
            pm = QPixmap(candidate)
        return None if pm.isNull() else pm

    try:
        icon_count = ctypes.windll.shell32.ExtractIconExW(
            ctypes.c_wchar_p(icon_file), -1, None, None, 0)
    except Exception:
        icon_count = 1
    if icon_count == 0:
        return sibling_ico()

    hicon = _get_hicon_from_shell(icon_file, SHIL_JUMBO) or _get_hicon_fallback(icon_file)
    pm = None
    if hicon:
        try:
            img = QImage.fromHICON(hicon)
            if not img.isNull():
                pm = QPixmap.fromImage(_crop_to_visible_icon(img))
        finally:
            ctypes.windll.user32.DestroyIcon(hicon)
    if pm is None or pm.isNull():
        return sibling_ico()
    return pm


def create_squircle_path(rect: Union[QRect, QRectF], radius: float, smoothness: float = 0.55228475) -> QPainterPath:
    """
    Constructs a continuous-curvature squircle path.
    Uses cubic Bézier transitions per corner with G1/G2 tangent continuity,
    eliminating all corner burrs, notches, and jagged aliasing artifacts.
    """
    path = QPainterPath()
    x = float(rect.x())
    y = float(rect.y())
    w = float(rect.width())
    h = float(rect.height())
    max_r = min(w, h) / 2.0
    r = min(radius, max_r)
    if r <= 0:
        path.addRect(rect)
        return path

    c = smoothness * r

    path.moveTo(x + r, y)
    path.lineTo(x + w - r, y)
    path.cubicTo(x + w - r + c, y, x + w, y + r - c, x + w, y + r)
    path.lineTo(x + w, y + h - r)
    path.cubicTo(x + w, y + h - r + c, x + w - r + c, y + h, x + w - r, y + h)
    path.lineTo(x + r, y + h)
    path.cubicTo(x + r - c, y + h, x, y + h - r + c, x, y + h - r)
    path.lineTo(x, y + r)
    path.cubicTo(x, y + r - c, x + r - c, y, x + r, y)
    path.closeSubpath()
    return path

def _crop_to_visible_icon(img: QImage) -> QImage:
    """
    Detects small icons (such as 32x32 or 48x48) returned by Windows Shell in a 256x256
    Jumbo ImageList canvas placed at (0, 0) with extensive transparency.
    Crops to the non-transparent bounding box so the icon does not become a tiny speck.
    """
    if img.isNull() or img.width() < 128 or img.height() < 128:
        return img

    if img.format() != QImage.Format.Format_ARGB32:
        conv_img = img.convertToFormat(QImage.Format.Format_ARGB32)
    else:
        conv_img = img

    w = conv_img.width()
    h = conv_img.height()
    ptr = conv_img.constBits()
    data = bytes(ptr)

    # 1. Top alpha boundary
    top = 0
    while top < h:
        row = data[top * w * 4 : (top + 1) * w * 4]
        if any(a > 10 for a in row[3::4]):
            break
        top += 1

    if top >= h:
        return img  # Entirely blank

    # 2. Bottom alpha boundary
    bottom = h - 1
    while bottom >= top:
        row = data[bottom * w * 4 : (bottom + 1) * w * 4]
        if any(a > 10 for a in row[3::4]):
            break
        bottom -= 1

    # 3. Left alpha boundary
    left = 0
    while left < w:
        col_has = False
        for y in range(top, bottom + 1):
            if data[y * w * 4 + left * 4 + 3] > 10:
                col_has = True
                break
        if col_has:
            break
        left += 1

    # 4. Right alpha boundary
    right = w - 1
    while right >= left:
        col_has = False
        for y in range(top, bottom + 1):
            if data[y * w * 4 + right * 4 + 3] > 10:
                col_has = True
                break
        if col_has:
            break
        right -= 1

    bw = right - left + 1
    bh = bottom - top + 1

    # If the visible icon occupies less than 65% of canvas width or height,
    # or is positioned at (0, 0) as an unscaled sub-icon, crop it to visible bounds
    if (bw < w * 0.65 and bh < h * 0.65) or (left == 0 and top == 0 and bw <= 64 and bh <= 64):
        return conv_img.copy(left, top, bw, bh)

    return img


def normalize_icon_pixmap(pixmap: QPixmap, target_size: int) -> QPixmap:
    """统一图标画布：裁掉透明边，等比缩放并居中放入正方形画布。

    不同来源（Shell JUMBO / UWP PNG / ico）的透明留白不同，直接绘制会显得
    大小不一、上下错位。这里只处理透明边，保留图标自身的形状与配色。
    """
    if pixmap is None or pixmap.isNull() or target_size <= 0:
        return pixmap
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return pixmap
    data = bytes(img.constBits())
    top, bottom = h, -1
    for y in range(h):
        row = data[y * w * 4:(y + 1) * w * 4]
        if any(a > 10 for a in row[3::4]):
            top = min(top, y)
            bottom = y
    if bottom < top:
        return pixmap
    left, right = w, -1
    for x in range(w):
        for y in range(top, bottom + 1):
            if data[(y * w + x) * 4 + 3] > 10:
                left = min(left, x)
                right = max(right, x)
                break
    if right < left:
        return pixmap
    bw, bh = right - left + 1, bottom - top + 1
    if bw < w * 0.92 or bh < h * 0.92:
        img = img.copy(left, top, bw, bh)

    canvas = QImage(target_size, target_size, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    scaled = QPixmap.fromImage(img).scaled(
        target_size, target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    painter = QPainter(canvas)
    painter.drawPixmap((target_size - scaled.width()) // 2,
                       (target_size - scaled.height()) // 2, scaled)
    painter.end()
    return QPixmap.fromImage(canvas)


def round_pixmap_corners(pixmap: QPixmap, radius_ratio: float = 0.22) -> QPixmap:
    """
    Rounds sharp-cornered PC icons into a squircle silhouette
    using subpixel alpha compositing (CompositionMode_SourceIn) to eliminate all aliasing burrs.
    """
    if not pixmap or pixmap.isNull():
        return pixmap
    w = pixmap.width()
    h = pixmap.height()
    if w <= 0 or h <= 0:
        return pixmap

    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    inset = 0.5
    rect = QRectF(inset, inset, w - inset * 2, h - inset * 2)
    r = min(rect.width(), rect.height()) * radius_ratio
    path = create_squircle_path(rect, r)

    # 1. Antialiased squircle mask
    painter.fillPath(path, Qt.GlobalColor.white)

    # 2. SourceIn composite - clips with true subpixel fractional alpha
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.drawPixmap(0, 0, pixmap)

    # 3. Specular micro-rim on top edge (subtle 0.75px)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    rim_grad = QLinearGradient(0, 0, 0, h)
    rim_grad.setColorAt(0.0, QColor(255, 255, 255, 60))
    rim_grad.setColorAt(0.4, QColor(255, 255, 255, 25))
    rim_grad.setColorAt(1.0, QColor(255, 255, 255, 10))
    painter.strokePath(path, QPen(rim_grad, 0.75))

    # Outer micro-edge for crisp contrast
    painter.strokePath(path, QPen(QColor(0, 0, 0, 25), 0.5))

    painter.end()
    return QPixmap.fromImage(image)

def is_generic_blank_document_icon(img: Union[QImage, QPixmap]) -> bool:
    """
    Detects whether an extracted icon is Windows Shell's generic empty white document icon.
    Windows Shell fallback icons have a distinct portrait aspect ratio (e.g. 98x128)
    and predominantly pure white paper content with a folded upper-right corner.
    """
    if img is None:
        return True
    if isinstance(img, QPixmap):
        img = img.toImage()
    if img.isNull():
        return True
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return True
    ratio = w / float(h)
    # Generic document icon is tall and narrow (approx 98/128 ~ 0.76)
    if ratio < 0.85:
        white_hits = 0
        samples = 0
        for y in range(int(h * 0.2), int(h * 0.8), max(1, int(h * 0.1))):
            for x in range(int(w * 0.2), int(w * 0.8), max(1, int(w * 0.1))):
                c = img.pixelColor(x, y)
                if c.alpha() > 200 and c.red() > 235 and c.green() > 235 and c.blue() > 235:
                    white_hits += 1
                samples += 1
        if samples > 0 and (white_hits / float(samples)) > 0.70:
            return True
    return False

def extract_uwp_icon_from_lnk(lnk_path: str) -> str:
    """
    Extracts high-resolution official package assets (PNG) for modern Windows UWP / Microsoft Store,
    MSIX packages (e.g. Kazumi, Terminal), and Edge/Chrome PWA shortcuts (.lnk).
    Supports both direct WindowsApps paths and AUMID / PackageFamilyName registry mappings.
    """
    if not lnk_path or not os.path.exists(lnk_path) or not lnk_path.lower().endswith(".lnk"):
        return ""
    try:
        with open(lnk_path, "rb") as f:
            data = f.read()
    except Exception:
        return ""

    texts = [
        data.decode("utf-16le", errors="ignore"),
        data[1:].decode("utf-16le", errors="ignore"),
        data.decode("ascii", errors="ignore")
    ]

    uwp_dirs = []
    # 1. Match direct WindowsApps paths if present
    pattern_path = re.compile(r'[A-Za-z]:\\Program Files\\WindowsApps\\[a-zA-Z0-9\._\-]+')
    for t in texts:
        for m in pattern_path.finditer(t):
            d = m.group(0)
            if os.path.isdir(d) and d not in uwp_dirs:
                uwp_dirs.append(d)

    # 2. Match AUMID / PackageFamilyName: e.g. com.flutter.kazumi_wbnnev551gwxy!kazumi
    pattern_aumid = re.compile(r'([a-zA-Z0-9\.\-]+_[a-zA-Z0-9]{5,})!([a-zA-Z0-9\.\-_]+)')
    pkg_prefixes = []
    for t in texts:
        for m in pattern_aumid.finditer(t):
            fam = m.group(1)
            pfx = fam.split('_')[0].lower()
            if pfx not in pkg_prefixes:
                pkg_prefixes.append(pfx)

    # 3. Query Windows AppModel Registry to discover physical PackageRootFolder
    if pkg_prefixes:
        reg_roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\PackageRepository\Packages")
        ]
        for hkey, subkey in reg_roots:
            try:
                with winreg.OpenKey(hkey, subkey) as k:
                    idx = 0
                    while True:
                        try:
                            key_name = winreg.EnumKey(k, idx)
                            idx += 1
                            key_lower = key_name.lower()
                            for pfx in pkg_prefixes:
                                if key_lower.startswith(pfx):
                                    with winreg.OpenKey(k, key_name) as sub_k:
                                        root_folder, _ = winreg.QueryValueEx(sub_k, "PackageRootFolder")
                                        if os.path.isdir(root_folder) and root_folder not in uwp_dirs:
                                            uwp_dirs.append(root_folder)
                        except OSError:
                            break
            except Exception:
                pass

    for udir in uwp_dirs:
        candidates = []
        for root, dirs, files in os.walk(udir):
            for fname in files:
                if fname.lower().endswith(".png"):
                    full = os.path.join(root, fname)
                    try:
                        candidates.append((full, os.path.getsize(full), fname.lower()))
                    except Exception:
                        pass

        best_icon = ""
        best_score = -1
        for full, sz, fn in candidates:
            score = sz
            if "square150" in fn or "150x150" in fn:
                score += 100000
            elif "256" in fn:
                score += 80000
            elif "large" in fn:
                score += 60000
            elif "logo" in fn:
                score += 50000
            elif "square44" in fn:
                score += 20000

            if "scale-400" in fn or "scale-200" in fn:
                score += 10000
            if "targetsize-256" in fn:
                score += 15000

            if score > best_score:
                best_score = score
                best_icon = full

        if best_icon:
            return best_icon

    return ""

def _get_hicon_from_shell(file_path, size_type=SHIL_JUMBO):
    """Extracts high resolution HICON (256x256) via Windows Shell IImageList"""
    try:
        sfi = SHFILEINFO()
        res = ctypes.windll.shell32.SHGetFileInfoW(
            file_path,
            0,
            ctypes.byref(sfi),
            ctypes.sizeof(sfi),
            SHGFI_SYSICONINDEX
        )
        # sfi.iIcon == 0 is the generic blank document (unassociated folded paper) in Windows Shell
        if not res or sfi.iIcon == 0:
            return None

        class IImageList(ctypes.c_void_p):
            pass

        iimage_list = IImageList()
        # 1. Try 256x256 Jumbo
        hr = ctypes.windll.shell32.SHGetImageList(size_type, ctypes.byref(IID_IImageList), ctypes.byref(iimage_list))
        if hr != 0 or not iimage_list.value:
            # 2. Fallback to 48x48 Extra Large
            hr = ctypes.windll.shell32.SHGetImageList(SHIL_EXTRALARGE, ctypes.byref(IID_IImageList), ctypes.byref(iimage_list))
            if hr != 0 or not iimage_list.value:
                return None

        vtable = ctypes.cast(ctypes.cast(iimage_list, ctypes.POINTER(ctypes.c_void_p)).contents, ctypes.POINTER(ctypes.c_void_p))
        GetIcon_func_type = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.UINT,
            ctypes.POINTER(wintypes.HICON)
        )
        GetIcon = GetIcon_func_type(vtable[10])

        hIcon = wintypes.HICON()
        hr = GetIcon(iimage_list, sfi.iIcon, ILD_TRANSPARENT, ctypes.byref(hIcon))

        Release_func_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        Release = Release_func_type(vtable[2])
        Release(iimage_list)

        if hr == 0 and hIcon.value:
            return hIcon.value
    except Exception:
        pass
    return None

def _get_hicon_fallback(file_path):
    try:
        sfi = SHFILEINFO()
        res = ctypes.windll.shell32.SHGetFileInfoW(
            file_path,
            0,
            ctypes.byref(sfi),
            ctypes.sizeof(sfi),
            SHGFI_ICON | SHGFI_LARGEICON | SHGFI_SYSICONINDEX
        )
        if res and sfi.hIcon and sfi.iIcon != 0:
            return sfi.hIcon
    except Exception:
        pass
    return None

def create_modern_squircle_pixmap(name: str, size: int = 128) -> QPixmap:
    """
    Renders a luxury squircle app tile with multi-stop
    curated gradient, top-down radial sheen, inner micro-frame, and sleek typography.
    """
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    radius = size * 0.22
    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, radius)

    # Curated luxury aesthetic palettes: (top, bottom, accent)
    palettes = [
        (QColor(42, 48, 64), QColor(22, 24, 34), QColor(90, 145, 255)),   # Obsidian Slate
        (QColor(28, 52, 98), QColor(14, 25, 52), QColor(95, 170, 255)),   # Midnight Sapphire
        (QColor(24, 60, 50), QColor(12, 32, 26), QColor(85, 215, 170)),   # Deep Forest Emerald
        (QColor(60, 36, 88), QColor(30, 16, 48), QColor(190, 130, 255)),  # Cosmic Amethyst
        (QColor(56, 62, 75), QColor(32, 35, 44), QColor(170, 185, 210)),  # Titanium Graphite
        (QColor(18, 68, 112), QColor(10, 36, 65), QColor(75, 195, 255)),  # Oceanic Deep
        (QColor(84, 46, 30), QColor(44, 22, 14), QColor(255, 160, 105)),  # Amber Bronze
        (QColor(76, 26, 42), QColor(40, 14, 22), QColor(255, 115, 145)),  # Crimson Velvet
    ]
    h_idx = int(hashlib.md5(name.encode("utf-8", "ignore")).hexdigest()[:6], 16) % len(palettes)
    c_top, c_bottom, c_accent = palettes[h_idx]

    # 1. Base gradient fill
    base_grad = QLinearGradient(0, pad, 0, size - pad)
    base_grad.setColorAt(0.0, c_top)
    base_grad.setColorAt(1.0, c_bottom)
    painter.fillPath(path, base_grad)

    # 2. Top-down soft radial specular highlight
    radial_sheen = QRadialGradient(size * 0.5, pad + size * 0.15, size * 0.75)
    radial_sheen.setColorAt(0.0, QColor(255, 255, 255, 45))
    radial_sheen.setColorAt(0.55, QColor(255, 255, 255, 12))
    radial_sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, radial_sheen)

    # 3. Inner concentric micro-frame
    inner_pad = max(3.5, size * 0.055)
    inner_rect = rect.adjusted(inner_pad, inner_pad, -inner_pad, -inner_pad)
    inner_path = create_squircle_path(inner_rect, radius * 0.75)
    painter.strokePath(inner_path, QPen(QColor(255, 255, 255, 18), 0.8))

    # 4. Specular rim highlight on top contour
    rim_grad = QLinearGradient(0, pad, 0, size)
    rim_grad.setColorAt(0.0, QColor(255, 255, 255, 110))
    rim_grad.setColorAt(0.35, QColor(255, 255, 255, 45))
    rim_grad.setColorAt(1.0, QColor(255, 255, 255, 15))
    painter.strokePath(path, QPen(rim_grad, 1.0))

    # 5. Modern Subpixel Typography
    char = (name[0] if name else "★").upper()
    font_size = int(size * 0.38)
    font = QFont("Segoe UI Variable Display", font_size, QFont.Weight.DemiBold)
    font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI"])
    painter.setFont(font)

    # Ambient text shadow
    painter.setPen(QColor(0, 0, 0, 95))
    painter.drawText(QRectF(0, size * 0.025, size, size), Qt.AlignmentFlag.AlignCenter, char)

    # Crisp glyph foreground
    painter.setPen(QColor(255, 255, 255, 248))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, char)

    painter.end()
    return QPixmap.fromImage(image)


def is_cutout_pixmap(pixmap: QPixmap) -> bool:
    """Checks if a pixmap is a cutout (empty corners) rather than a solid full-bleed tile."""
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w = img.width()
    h = img.height()
    if w < 32 or h < 32:
        return True

    offset_x = max(4, int(w * 0.14))
    offset_y = max(4, int(h * 0.14))
    samples = [
        img.pixelColor(offset_x, offset_y).alpha(),
        img.pixelColor(w - offset_x, offset_y).alpha(),
        img.pixelColor(offset_x, h - offset_y).alpha(),
        img.pixelColor(w - offset_x, h - offset_y).alpha(),
    ]
    trans_corners = sum(1 for a in samples if a < 35)
    return trans_corners >= 2


def wrap_in_squircle_canvas(raw_pixmap: QPixmap, target_size: int = 128) -> QPixmap:
    """
    Normalizes freeform / cutout app logos onto a luxury
    frosted dark-glass squircle canvas so all desktop icons share unified G2 silhouette and depth.
    """
    img = QImage(target_size, target_size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, target_size - pad * 2, target_size - pad * 2)
    radius = target_size * 0.22
    path = create_squircle_path(rect, radius)

    # 1. Dark Frosted Glass / Titanium Canvas Gradient
    grad = QLinearGradient(0, pad, 0, target_size - pad)
    grad.setColorAt(0.0, QColor(36, 42, 56, 240))
    grad.setColorAt(1.0, QColor(18, 22, 30, 245))
    p.fillPath(path, grad)

    # 2. Specular Top Radial Sheen
    radial = QRadialGradient(target_size * 0.5, pad + target_size * 0.15, target_size * 0.75)
    radial.setColorAt(0.0, QColor(255, 255, 255, 38))
    radial.setColorAt(0.65, QColor(255, 255, 255, 8))
    radial.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(path, radial)

    # 3. Specular Rim Highlight
    rim_grad = QLinearGradient(0, pad, 0, target_size)
    rim_grad.setColorAt(0.0, QColor(255, 255, 255, 85))
    rim_grad.setColorAt(0.4, QColor(255, 255, 255, 30))
    rim_grad.setColorAt(1.0, QColor(255, 255, 255, 10))
    p.strokePath(path, QPen(rim_grad, 1.0))

    # 4. Render raw glyph centered at ~74% scale with subtle ambient shadow
    inner_size = int(target_size * 0.72)
    scaled_glyph = raw_pixmap.scaled(
        inner_size, inner_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )
    gx = (target_size - scaled_glyph.width()) / 2.0
    gy = (target_size - scaled_glyph.height()) / 2.0

    p.drawPixmap(int(gx), int(gy), scaled_glyph)
    p.end()
    return QPixmap.fromImage(img)


def draw_system_notepad_icon(size: int = 128) -> QPixmap:
    """Modern system-style Notepad squircle icon with folded corner & azure gradient."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, QColor(0, 120, 215))
    grad.setColorAt(1.0, QColor(0, 84, 166))
    p.fillPath(path, grad)

    radial = QRadialGradient(size * 0.5, pad + size * 0.15, size * 0.8)
    radial.setColorAt(0.0, QColor(255, 255, 255, 70))
    radial.setColorAt(0.6, QColor(255, 255, 255, 15))
    radial.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(path, radial)
    p.strokePath(path, QPen(QColor(255, 255, 255, 80), 1.0))

    sheet_left = size * 0.24
    sheet_top = size * 0.20
    sheet_w = size * 0.52
    sheet_h = size * 0.60

    paper_path = QPainterPath()
    paper_path.moveTo(sheet_left, sheet_top + 4)
    paper_path.quadTo(sheet_left, sheet_top, sheet_left + 4, sheet_top)
    paper_path.lineTo(sheet_left + sheet_w - 12, sheet_top)
    paper_path.lineTo(sheet_left + sheet_w, sheet_top + 12)
    paper_path.lineTo(sheet_left + sheet_w, sheet_top + sheet_h - 4)
    paper_path.quadTo(sheet_left + sheet_w, sheet_top + sheet_h, sheet_left + sheet_w - 4, sheet_top + sheet_h)
    paper_path.lineTo(sheet_left + 4, sheet_top + sheet_h)
    paper_path.quadTo(sheet_left, sheet_top + sheet_h, sheet_left, sheet_top + sheet_h - 4)
    paper_path.closeSubpath()

    p.fillPath(paper_path, QColor(250, 252, 255))

    fold = QPainterPath()
    fold.moveTo(sheet_left + sheet_w - 12, sheet_top)
    fold.lineTo(sheet_left + sheet_w - 12, sheet_top + 12)
    fold.lineTo(sheet_left + sheet_w, sheet_top + 12)
    fold.closeSubpath()
    p.fillPath(fold, QColor(200, 215, 235))

    p.setPen(QPen(QColor(140, 175, 215), max(1.5, size * 0.018), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    step = size * 0.10
    start_y = sheet_top + size * 0.16
    for i in range(4):
        ly = start_y + i * step
        if ly < sheet_top + sheet_h - 6:
            p.drawLine(QPointF(sheet_left + 10, ly), QPointF(sheet_left + sheet_w - 10, ly))

    p.fillRect(QRectF(sheet_left + 8, sheet_top, max(3.0, size * 0.05), sheet_h * 0.45), QColor(0, 180, 215))
    p.end()
    return QPixmap.fromImage(img)


def draw_system_calc_icon(size: int = 128) -> QPixmap:
    """Modern system-style Calculator squircle icon with LCD screen & keypad."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, QColor(36, 42, 56))
    grad.setColorAt(1.0, QColor(20, 24, 32))
    p.fillPath(path, grad)

    radial = QRadialGradient(size * 0.5, pad + size * 0.15, size * 0.8)
    radial.setColorAt(0.0, QColor(255, 255, 255, 50))
    radial.setColorAt(0.6, QColor(255, 255, 255, 10))
    radial.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillPath(path, radial)
    p.strokePath(path, QPen(QColor(255, 255, 255, 60), 1.0))

    screen_rect = QRectF(size * 0.20, size * 0.18, size * 0.60, size * 0.20)
    p.fillPath(create_squircle_path(screen_rect, 6), QColor(14, 18, 24))
    p.strokePath(create_squircle_path(screen_rect, 6), QPen(QColor(255, 255, 255, 25), 0.8))

    p.setPen(QColor(52, 211, 153))
    p.setFont(QFont("Consolas", int(size * 0.11), QFont.Weight.Bold))
    p.drawText(screen_rect.adjusted(0, 0, -6, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "128")

    kp_left = size * 0.20
    kp_top = size * 0.44
    kp_w = size * 0.60
    kp_h = size * 0.40
    btn_w = (kp_w - 8) / 3.0
    btn_h = (kp_h - 6) / 3.0

    p.setFont(QFont("Segoe UI", int(size * 0.08), QFont.Weight.DemiBold))
    symbols = [["7", "8", "9"], ["4", "5", "6"], ["1", "2", "="]]
    for r in range(3):
        for c in range(3):
            bx = kp_left + c * (btn_w + 4)
            by = kp_top + r * (btn_h + 3)
            b_rect = QRectF(bx, by, btn_w, btn_h)
            b_path = create_squircle_path(b_rect, 4)
            if r == 2 and c == 2:
                p.fillPath(b_path, QColor(14, 165, 233))
                p.setPen(QColor(255, 255, 255))
            else:
                p.fillPath(b_path, QColor(255, 255, 255, 28))
                p.setPen(QColor(220, 230, 245))
            p.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, symbols[r][c])

    p.end()
    return QPixmap.fromImage(img)


def draw_system_explorer_icon(size: int = 128) -> QPixmap:
    """Modern system-style File Explorer / This PC squircle icon."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, QColor(28, 56, 110))
    grad.setColorAt(1.0, QColor(14, 28, 58))
    p.fillPath(path, grad)
    p.strokePath(path, QPen(QColor(255, 255, 255, 75), 1.0))

    tab_rect = QRectF(size * 0.20, size * 0.22, size * 0.28, size * 0.16)
    p.fillPath(create_squircle_path(tab_rect, 5), QColor(225, 145, 0))

    f_rect = QRectF(size * 0.18, size * 0.28, size * 0.64, size * 0.50)
    p.fillPath(create_squircle_path(f_rect, 8), QColor(255, 185, 0))

    front_rect = QRectF(size * 0.18, size * 0.40, size * 0.64, size * 0.38)
    front_grad = QLinearGradient(0, front_rect.top(), 0, front_rect.bottom())
    front_grad.setColorAt(0.0, QColor(255, 210, 80, 240))
    front_grad.setColorAt(1.0, QColor(240, 165, 0, 250))
    p.fillPath(create_squircle_path(front_rect, 8), front_grad)
    p.strokePath(create_squircle_path(front_rect, 8), QPen(QColor(255, 255, 255, 90), 1.0))

    p.fillRect(QRectF(size * 0.42, size * 0.52, size * 0.16, size * 0.08), QColor(0, 120, 215))
    p.end()
    return QPixmap.fromImage(img)


def draw_system_taskmgr_icon(size: int = 128) -> QPixmap:
    """Modern system-style Task Manager squircle icon."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, QColor(14, 52, 42))
    grad.setColorAt(1.0, QColor(8, 28, 22))
    p.fillPath(path, grad)
    p.strokePath(path, QPen(QColor(255, 255, 255, 75), 1.0))

    p.setPen(QPen(QColor(52, 211, 153, 35), 1.0))
    for gy in [size * 0.35, size * 0.50, size * 0.65]:
        p.drawLine(QPointF(size * 0.20, gy), QPointF(size * 0.80, gy))
    for gx in [size * 0.35, size * 0.50, size * 0.65]:
        p.drawLine(QPointF(gx, size * 0.25), QPointF(gx, size * 0.75))

    pulse = QPainterPath()
    pulse.moveTo(size * 0.20, size * 0.50)
    pulse.lineTo(size * 0.35, size * 0.50)
    pulse.lineTo(size * 0.42, size * 0.30)
    pulse.lineTo(size * 0.50, size * 0.70)
    pulse.lineTo(size * 0.58, size * 0.40)
    pulse.lineTo(size * 0.65, size * 0.50)
    pulse.lineTo(size * 0.80, size * 0.50)

    p.strokePath(pulse, QPen(QColor(52, 211, 153, 100), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.strokePath(pulse, QPen(QColor(167, 243, 208), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    p.end()
    return QPixmap.fromImage(img)


def draw_system_control_icon(size: int = 128) -> QPixmap:
    """Modern system-style Control Panel / Settings squircle icon."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, QColor(48, 54, 72))
    grad.setColorAt(1.0, QColor(24, 28, 38))
    p.fillPath(path, grad)
    p.strokePath(path, QPen(QColor(255, 255, 255, 75), 1.0))

    cx = size * 0.50
    cy = size * 0.50
    sw_w = size * 0.50

    p.setPen(QPen(QColor(255, 255, 255, 80), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(cx - sw_w/2, cy - 14), QPointF(cx + sw_w/2, cy - 14))
    p.drawLine(QPointF(cx - sw_w/2, cy + 14), QPointF(cx + sw_w/2, cy + 14))

    p.setBrush(QColor(168, 85, 247))
    p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
    p.drawEllipse(QPointF(cx - 10, cy - 14), 7, 7)

    p.setBrush(QColor(56, 189, 248))
    p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
    p.drawEllipse(QPointF(cx + 12, cy + 14), 7, 7)

    p.end()
    return QPixmap.fromImage(img)


def resolve_url_shortcut(file_path: str) -> Optional[str]:
    """Extracts target URL from a Windows .url internet shortcut file."""
    if not file_path or not file_path.lower().endswith(".url"):
        return None
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("url="):
                    return line[4:].strip()
    except Exception:
        pass
    return None


def resolve_url_shortcut_icon(file_path: str) -> Optional[Tuple[str, int]]:
    """Extracts IconFile and IconIndex from a Windows .url internet shortcut file."""
    if not file_path or not file_path.lower().endswith(".url") or not os.path.exists(file_path):
        return None
    icon_file = ""
    icon_index = 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("iconfile="):
                    icon_file = line.split("=", 1)[1].strip()
                elif line.lower().startswith("iconindex="):
                    try:
                        icon_index = int(line.split("=", 1)[1].strip())
                    except Exception:
                        pass
        if icon_file and os.path.exists(icon_file):
            return icon_file, icon_index
    except Exception:
        pass
    return None


def extract_site_name_from_url(url: str) -> str:
    """Extracts a clean, human-readable site title or domain name from a URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        domain_mapping = {
            "github.com": "GitHub",
            "bilibili.com": "哔哩哔哩",
            "youtube.com": "YouTube",
            "google.com": "Google",
            "baidu.com": "百度",
            "figma.com": "Figma",
            "notion.so": "Notion",
            "chatgpt.com": "ChatGPT",
            "openai.com": "OpenAI",
            "zhihu.com": "知乎",
            "v2ex.com": "V2EX",
            "x.com": "X (Twitter)",
            "twitter.com": "Twitter",
            "taobao.com": "淘宝",
            "jd.com": "京东",
            "weibo.com": "微博",
            "gitee.com": "Gitee 码云",
        }
        for k, name in domain_mapping.items():
            if k in domain:
                return name
        return domain or "网页书签"
    except Exception:
        return "网页书签"


STACKY_ITEM_MIME = "application/x-stacky-item-payload"

# 跨文件夹拖放回执。QMimeData 以字节序列化传递，目标窗口反序列化出的 payload
# 与源窗口持有的 dict 不是同一个对象，无法靠改字典回传「已接走」，故用一次性 token。
_last_completed_drag_token = None


def is_internal_item_drag(mime_data) -> bool:
    """内部项目拖拽（卡片/抽屉里的图标），区别于从资源管理器拖入的文件。"""
    return bool(mime_data and mime_data.hasFormat(STACKY_ITEM_MIME))


def mark_cross_folder_drop(token: str):
    global _last_completed_drag_token
    if token:
        _last_completed_drag_token = token


def consume_cross_folder_drop(token: str) -> bool:
    global _last_completed_drag_token
    if token and _last_completed_drag_token == token:
        _last_completed_drag_token = None
        return True
    return False


def is_drop_acceptable(mime_data) -> bool:
    """Checks whether the QMimeData payload contains valid files, URLs, or web bookmarks."""
    if not mime_data:
        return False
    if mime_data.hasFormat(STACKY_ITEM_MIME):
        return True
    if mime_data.hasFormat("application/x-bigfolder-slot"):
        return True
    if mime_data.hasUrls():
        return True
    if mime_data.hasText():
        t = mime_data.text().strip()
        if t.startswith("http://") or t.startswith("https://"):
            return True
    return False


def parse_dropped_mime_data(mime_data) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Unified parser for dropped QMimeData:
    - Resolves Windows .url Internet Shortcut files into native web items
    - Extracts browser URL drops (Chrome, Edge, Firefox address bar drags)
    - Extracts plain-text URLs
    - Keeps standard local files (.exe, .lnk, docs, etc.)
    Returns (local_file_paths, web_bookmark_items).
    """
    local_files: List[str] = []
    web_bookmarks: List[Dict[str, Any]] = []

    if not mime_data:
        return local_files, web_bookmarks

    if mime_data.hasUrls():
        for u in mime_data.urls():
            local_path = u.toLocalFile()
            if local_path:
                if local_path.lower().endswith(".url"):
                    target_url = resolve_url_shortcut(local_path)
                    is_web_url = bool(target_url and (target_url.lower().startswith("http://") or target_url.lower().startswith("https://")))
                    if is_web_url:
                        base_title = os.path.splitext(os.path.basename(local_path))[0]
                        web_bookmarks.append({
                            "name": base_title,
                            "path": target_url,
                            "args": "",
                            "type": "web",
                            "subgroup": "网页书签"
                        })
                    else:
                        local_files.append(local_path)
                else:
                    local_files.append(local_path)
            else:
                # Browser address bar URL or hyperlink drag
                url_str = u.toString().strip()
                if url_str.startswith("http://") or url_str.startswith("https://"):
                    title = extract_site_name_from_url(url_str)
                    web_bookmarks.append({
                        "name": title,
                        "path": url_str,
                        "args": "",
                        "type": "web",
                        "subgroup": "网页书签"
                    })

    if not local_files and not web_bookmarks and mime_data.hasText():
        text = mime_data.text().strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("http://") or line.startswith("https://"):
                title = extract_site_name_from_url(line)
                web_bookmarks.append({
                    "name": title,
                    "path": line,
                    "args": "",
                    "type": "web",
                    "subgroup": "网页书签"
                })

    return local_files, web_bookmarks


def draw_bookmark_icon(url: str, title: str = "", size: int = 128) -> QPixmap:
    """
    Renders an ultra-luxurious system-style squircle icon for web bookmarks:
    - Auto-detects famous domains with native brand styling (GitHub, Bilibili, YouTube, Figma, Notion, ChatGPT...)
    - Generic domains rendered with high-contrast dual-tone monogram + subtle orbital sphere
    - High-density vector anti-aliased with 1px Fresnel light boundary
    """
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = 2.0
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    path = create_squircle_path(rect, size * 0.22)

    lower_url = (url or "").lower()
    lower_title = (title or "").lower()

    if "github" in lower_url or "github" in lower_title:
        c1, c2 = QColor(36, 41, 46), QColor(18, 20, 24)
        monogram = "GH"
        glyph_color = QColor(255, 255, 255)
    elif "bilibili" in lower_url or "bili" in lower_title or "哔哩" in lower_title:
        c1, c2 = QColor(251, 114, 153), QColor(0, 161, 214)
        monogram = "Bili"
        glyph_color = QColor(255, 255, 255)
    elif "youtube" in lower_url:
        c1, c2 = QColor(239, 68, 68), QColor(185, 28, 28)
        monogram = "YT"
        glyph_color = QColor(255, 255, 255)
    elif "figma" in lower_url:
        c1, c2 = QColor(30, 30, 30), QColor(18, 18, 18)
        monogram = "F"
        glyph_color = QColor(242, 78, 30)
    elif "notion" in lower_url:
        c1, c2 = QColor(248, 250, 252), QColor(226, 232, 240)
        monogram = "N"
        glyph_color = QColor(15, 23, 42)
    elif "chatgpt" in lower_url or "openai" in lower_url:
        c1, c2 = QColor(16, 163, 127), QColor(10, 110, 85)
        monogram = "AI"
        glyph_color = QColor(255, 255, 255)
    elif "zhihu" in lower_url or "知乎" in lower_title:
        c1, c2 = QColor(2, 132, 199), QColor(3, 105, 161)
        monogram = "知"
        glyph_color = QColor(255, 255, 255)
    elif "baidu" in lower_url or "百度" in lower_title:
        c1, c2 = QColor(41, 50, 225), QColor(20, 28, 160)
        monogram = "度"
        glyph_color = QColor(255, 255, 255)
    else:
        c1, c2 = QColor(30, 64, 175), QColor(15, 23, 42)
        if lower_url.startswith("steam://"):
            name = title if title and not title.lower().startswith("steam") else "Steam Game"
            monogram = name[:2].upper() if len(name) >= 2 else "SG"
        else:
            name = extract_site_name_from_url(url) if url else (title or "W")
            monogram = (name[:2] if len(name) >= 2 else (name[:1] or "W")).upper()
        if monogram == "RU" and "steam://" in lower_url:
            monogram = "SG"
        glyph_color = QColor(240, 246, 255)

    grad = QLinearGradient(0, pad, 0, size - pad)
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, c2)
    p.fillPath(path, grad)
    p.strokePath(path, QPen(QColor(255, 255, 255, 70), 1.0))

    radial = QRadialGradient(size * 0.5, pad + size * 0.15, size * 0.8)
    radial.setColorAt(0.0, QColor(255, 255, 255, 40))
    radial.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.fillPath(path, radial)

    font_size = int(size * (0.34 if len(monogram) > 1 else 0.42))
    font = QFont("Segoe UI Variable Display", font_size, QFont.Weight.Bold)
    font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI"])
    p.setFont(font)

    p.setPen(QColor(0, 0, 0, 80))
    p.drawText(QRectF(0, size * 0.03, size, size), Qt.AlignmentFlag.AlignCenter, monogram)

    p.setPen(glyph_color)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, monogram)

    p.end()
    return QPixmap.fromImage(img)


_IN_MEMORY_PIXMAP_CACHE = OrderedDict()
_MAX_PIXMAP_CACHE_ENTRIES = 512

def _store_in_memory_pixmap(key: str, pixmap: QPixmap):
    """Stores pixmap in L1 in-memory cache with LRU eviction to prevent unbounded memory growth."""
    cache = _IN_MEMORY_PIXMAP_CACHE
    if key in cache:
        cache.move_to_end(key)
    elif len(cache) >= _MAX_PIXMAP_CACHE_ENTRIES:
        cache.popitem(last=False)
    cache[key] = pixmap

def clear_in_memory_pixmap_cache():
    """Clears L1 in-memory pixmap cache to release GDI/GPU bitmap memory during idle state."""
    _IN_MEMORY_PIXMAP_CACHE.clear()

def get_app_icon_pixmap(file_path: str, display_name: str = "", target_size: int = 128, custom_icon_path: str = "") -> QPixmap:
    """
    Returns high-res 256x256 QPixmap with local cache, custom icon support,
    modern system-style icons for system utilities, and luxury squircle canvas harmonization.
    Features ultra-fast L1 in-memory caching to guarantee zero disk I/O during 60/120 FPS animations.
    """
    mem_key = f"{file_path}|{custom_icon_path}|{display_name}|{target_size}"
    cached_mem = _IN_MEMORY_PIXMAP_CACHE.get(mem_key)
    if cached_mem is not None and not cached_mem.isNull():
        _IN_MEMORY_PIXMAP_CACHE.move_to_end(mem_key)
        return cached_mem

    ensure_cache_dir()
    real_path = resolve_actual_path(file_path)
    custom_real = resolve_actual_path(custom_icon_path) if custom_icon_path else ""

    d_name = (display_name or "").strip().lower()
    b_name = os.path.splitext(os.path.basename(real_path))[0].lower()
    t_target = (resolve_shortcut_target(real_path) or "").lower() if real_path.lower().endswith(".lnk") else ""

    # .lnk 的 IconLocation 优先于缓存与 Shell 取图：SHGetFileInfo(.lnk) 往往只返回
    # 目标 exe 的图标（米哈游启动器 / Electron 通用方块），会盖掉快捷方式指定的专属 ico。
    if not custom_real and real_path.lower().endswith(".lnk"):
        lnk_pm = normalize_icon_pixmap(resolve_shortcut_icon_pixmap(real_path, target_size), target_size)
        if lnk_pm is not None and not lnk_pm.isNull():
            _store_in_memory_pixmap(mem_key, lnk_pm)
            return lnk_pm

    cache_key = hashlib.md5((real_path + "|" + custom_real + "|" + d_name + "|" + str(target_size) + "|v5_native").encode("utf-8", "ignore")).hexdigest()
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.png")

    if os.path.exists(cache_file):
        pm = QPixmap(cache_file)
        if not pm.isNull() and not is_generic_blank_document_icon(pm):
            pm = normalize_icon_pixmap(pm, target_size)
            _store_in_memory_pixmap(mem_key, pm)
            return pm
        elif not pm.isNull() and is_generic_blank_document_icon(pm):
            try:
                os.remove(cache_file)
            except Exception:
                pass

    pixmap = None

    # 0. Modern system-style icons (Notepad, Calculator, File Explorer, Task Manager, Control Panel)
    if not custom_real:
        if any(k in d_name or k == b_name or t_target.endswith(k + ".exe") for k in ["记事本", "notepad"]):
            pixmap = draw_system_notepad_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith(k + ".exe") for k in ["计算器", "calc", "calculator"]):
            pixmap = draw_system_calc_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("explorer.exe") for k in ["此电脑", "文件资源管理器", "资源管理器", "explorer", "this pc"]):
            pixmap = draw_system_explorer_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("taskmgr.exe") for k in ["任务管理器", "taskmgr", "task manager"]):
            pixmap = draw_system_taskmgr_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("control.exe") for k in ["控制面板", "control panel", "control"]):
            pixmap = draw_system_control_icon(target_size)

    # 1. Custom Icon file takes highest priority if specified and exists
    if custom_real and os.path.exists(custom_real):
        ext = os.path.splitext(custom_real)[1].lower()
        if ext == ".ico":
            icon = QIcon(custom_real)
            raw_pm = icon.pixmap(target_size, target_size)
            if raw_pm.isNull():
                raw_pm = QPixmap(custom_real)
            if not raw_pm.isNull():
                pixmap = raw_pm
        elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".webp"]:
            raw_pm = QPixmap(custom_real)
            if not raw_pm.isNull():
                pixmap = raw_pm
        elif ext in [".exe", ".dll"]:
            hicon = _get_hicon_from_shell(custom_real, SHIL_JUMBO) or _get_hicon_fallback(custom_real)
            if hicon:
                try:
                    img = QImage.fromHICON(hicon)
                    if not img.isNull():
                        img = _crop_to_visible_icon(img)
                        pixmap = QPixmap.fromImage(img)
                except Exception:
                    pass
                finally:
                    ctypes.windll.user32.DestroyIcon(hicon)

    # 2. Authentic Windows Shell Icon Extraction for Local Files, .lnk and .url
    if (not pixmap or pixmap.isNull()) and os.path.exists(real_path):
        # 2.1 Modern Windows UWP / Microsoft Store / PWA app shortcuts (.lnk)
        if real_path.lower().endswith(".lnk"):
            uwp_png = extract_uwp_icon_from_lnk(real_path)
            if uwp_png and os.path.exists(uwp_png):
                raw_pm = QPixmap(uwp_png)
                if not raw_pm.isNull():
                    pixmap = raw_pm

        # 2.2 Windows .url Shortcuts (e.g. Steam Games, Web Links)
        if (not pixmap or pixmap.isNull()) and real_path.lower().endswith(".url"):
            # Priority 1: Read authentic IconFile / IconIndex directly from .url INI
            # (Essential for Steam games where IconFile points to the real high-res .ico or game .exe)
            icon_info = resolve_url_shortcut_icon(real_path)
            if icon_info:
                icon_file, icon_index = icon_info
                if icon_file.lower().endswith(".ico") and os.path.exists(icon_file):
                    raw_pm = QIcon(icon_file).pixmap(target_size, target_size)
                    if not raw_pm.isNull():
                        pixmap = raw_pm
                elif icon_file.lower().endswith((".exe", ".dll")) and os.path.exists(icon_file):
                    hicon = _get_hicon_from_shell(icon_file, SHIL_JUMBO) or _get_hicon_fallback(icon_file)
                    if hicon:
                        try:
                            img = QImage.fromHICON(hicon)
                            if not img.isNull() and not is_generic_blank_document_icon(img):
                                pixmap = QPixmap.fromImage(_crop_to_visible_icon(img))
                        except Exception:
                            pass
                        finally:
                            ctypes.windll.user32.DestroyIcon(hicon)

            # Priority 2: Shell Jumbo icon from .url file ONLY IF NOT a generic browser/globe fallback for Steam
            if not pixmap or pixmap.isNull():
                target_url = resolve_url_shortcut(real_path) or ""
                is_steam = target_url.lower().startswith("steam://")
                
                hicon = _get_hicon_from_shell(real_path, SHIL_JUMBO) or _get_hicon_fallback(real_path)
                if hicon:
                    try:
                        img = QImage.fromHICON(hicon)
                        if not img.isNull():
                            img = _crop_to_visible_icon(img)
                            raw_pix = QPixmap.fromImage(img)
                            # If it's a Steam game, strictly do NOT accept a generic web globe icon from url.dll!
                            if not is_generic_blank_document_icon(raw_pix) and not is_steam:
                                pixmap = raw_pix
                    except Exception:
                        pass
                    finally:
                        ctypes.windll.user32.DestroyIcon(hicon)

            # Priority 3: Target URL specific handling
            if not pixmap or pixmap.isNull():
                target_url = resolve_url_shortcut(real_path) or ""
                if target_url and (target_url.lower().startswith("http://") or target_url.lower().startswith("https://")):
                    pixmap = draw_bookmark_icon(target_url, display_name or b_name, target_size)
                elif target_url and target_url.lower().startswith("steam://"):
                    # Steam game fallback: Render game title squircle, NEVER "RU" monogram!
                    game_title = display_name or os.path.splitext(os.path.basename(real_path))[0]
                    pixmap = create_modern_squircle_pixmap(game_title, target_size)

        # 2.3 General Windows Shell Icon (.exe, .lnk target, folders, documents)
        if not pixmap or pixmap.isNull():
            hicon = None
            if real_path.lower().endswith(".lnk"):
                # 2.3.1 Priority 1: Shortcut IconLocation if specified (e.g. Gemini.exe,0 or custom docs.ico)
                icon_info = resolve_shortcut_icon_location(real_path)
                if icon_info:
                    icon_file, icon_idx = icon_info
                    if icon_file.lower().endswith(".ico") and os.path.exists(icon_file):
                        raw_pm = QIcon(icon_file).pixmap(target_size, target_size)
                        if not raw_pm.isNull():
                            pixmap = raw_pm
                    elif icon_file.lower().endswith((".exe", ".dll")) and os.path.exists(icon_file):
                        base_icon_f = os.path.basename(icon_file).lower()
                        script_hosts = {
                            "wscript.exe", "cscript.exe", "cmd.exe", "powershell.exe",
                            "pwsh.exe", "conhost.exe", "python.exe", "pythonw.exe"
                        }
                        if base_icon_f not in script_hosts:
                            hicon = _get_hicon_from_shell(icon_file, SHIL_JUMBO) or _get_hicon_fallback(icon_file)
                            if hicon:
                                try:
                                    img = QImage.fromHICON(hicon)
                                    if not img.isNull() and not is_generic_blank_document_icon(img):
                                        pixmap = QPixmap.fromImage(_crop_to_visible_icon(img))
                                except Exception:
                                    pass
                                finally:
                                    ctypes.windll.user32.DestroyIcon(hicon)
                                    hicon = None

                # 2.3.2 Priority 2: Query .lnk shortcut directly via Windows Shell Jumbo
                # (Windows Explorer native path - BUT strictly block script host targets like wscript.exe)
                if not pixmap or pixmap.isNull():
                    target_exec = resolve_shortcut_target(real_path)
                    base_t = os.path.basename(target_exec or "").lower()
                    script_hosts = {
                        "wscript.exe", "cscript.exe", "cmd.exe", "powershell.exe",
                        "pwsh.exe", "conhost.exe", "python.exe", "pythonw.exe"
                    }
                    is_script_host = base_t in script_hosts or base_t.endswith(".bat") or base_t.endswith(".vbs")

                    if not is_script_host:
                        hicon = _get_hicon_from_shell(real_path, SHIL_JUMBO) or _get_hicon_fallback(real_path)
                        if hicon:
                            try:
                                img = QImage.fromHICON(hicon)
                                if not img.isNull() and not is_generic_blank_document_icon(img):
                                    pixmap = QPixmap.fromImage(_crop_to_visible_icon(img))
                            except Exception:
                                pass
                            finally:
                                ctypes.windll.user32.DestroyIcon(hicon)
                                hicon = None

                # 2.3.3 Priority 3: Fallback to target executable ONLY if not a generic script engine
                if not pixmap or pixmap.isNull():
                    if target_exec and target_exec != real_path and os.path.exists(target_exec):
                        if not is_script_host:
                            hicon = _get_hicon_from_shell(target_exec, SHIL_JUMBO) or _get_hicon_fallback(target_exec)

            # 2.3.4 Direct shell icon for non-shortcut files or fallback
            if not pixmap or pixmap.isNull():
                if not hicon:
                    hicon = _get_hicon_from_shell(real_path, SHIL_JUMBO) or _get_hicon_fallback(real_path)

                if hicon:
                    try:
                        img = QImage.fromHICON(hicon)
                        if not img.isNull():
                            img = _crop_to_visible_icon(img)
                            raw_pix = QPixmap.fromImage(img)
                            if not is_generic_blank_document_icon(raw_pix):
                                pixmap = raw_pix
                    except Exception:
                        pass
                    finally:
                        ctypes.windll.user32.DestroyIcon(hicon)

    # 3. Web URL fallback (raw URLs without local file)
    if (not pixmap or pixmap.isNull()) and (real_path.startswith("http://") or real_path.startswith("https://")):
        pixmap = draw_bookmark_icon(real_path, display_name, target_size)

    # 4. System Utility Vector Fallback (for bare names in test suites / virtual shortcuts)
    if not pixmap or pixmap.isNull():
        if any(k in d_name or k == b_name or t_target.endswith(k + ".exe") for k in ["记事本", "notepad"]):
            pixmap = draw_system_notepad_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith(k + ".exe") for k in ["计算器", "calc", "calculator"]):
            pixmap = draw_system_calc_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("explorer.exe") for k in ["此电脑", "文件资源管理器", "资源管理器", "explorer", "this pc"]):
            pixmap = draw_system_explorer_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("taskmgr.exe") for k in ["任务管理器", "taskmgr", "task manager"]):
            pixmap = draw_system_taskmgr_icon(target_size)
        elif any(k in d_name or k == b_name or t_target.endswith("control.exe") for k in ["控制面板", "control panel", "control"]):
            pixmap = draw_system_control_icon(target_size)

    # 5. Clean Title Glyph Fallback
    if not pixmap or pixmap.isNull() or is_generic_blank_document_icon(pixmap):
        name = display_name or os.path.splitext(os.path.basename(real_path))[0]
        pixmap = create_modern_squircle_pixmap(name, target_size)

    # 6. 统一画布：所有来源的图标按同一比例居中，卡片里大小一致、基线对齐
    if pixmap and not pixmap.isNull():
        pixmap = normalize_icon_pixmap(pixmap, target_size)

    try:
        pixmap.save(cache_file, "PNG")
    except Exception:
        pass

    if pixmap and not pixmap.isNull():
        _store_in_memory_pixmap(mem_key, pixmap)

    return pixmap


def prewarm_icons_batch(items: list, target_size: int = 128):
    """
    Prewarms a collection of items into L1 in-memory cache.
    Guarantees zero disk I/O when expanding folders with 40+ items for locked 120 FPS performance.
    """
    if not items:
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        p = it.get("path", "")
        n = it.get("name", "")
        c = it.get("custom_icon", "")
        if p or n:
            get_app_icon_pixmap(p, n, target_size=target_size, custom_icon_path=c)


def get_ui_chevron_icon_path(hover: bool = False) -> str:
    """返回用于 QComboBox / 折叠菜单的高清抗锯齿向下箭头图标路径 (规避 Win32 原生黑方块与CSS三角形瑕疵)"""
    ensure_cache_dir()
    fname = "ui_chevron_down_hover.png" if hover else "ui_chevron_down.png"
    fpath = os.path.join(CACHE_DIR, fname)
    if not os.path.exists(fpath):
        img = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(255, 255, 255, 240) if hover else QColor(148, 163, 184, 200)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(4, 6, 8, 10)
        p.drawLine(8, 10, 12, 6)
        p.end()
        img.save(fpath, "PNG")
    return fpath.replace("\\", "/")


def draw_liquid_glass(painter, path, rect, radius, theme="dark"):
    """在已填充底色的 squircle 上叠加折射带、顶部高光与菲涅尔双边缘。

    纯矢量、不采样背景：桌面底层窗口截屏会截到自己而产生递归重影，
    原生 DWM Acrylic 又会画出直角矩形背板，因此玻璃质感只做光学层。
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setClipPath(path)

    band = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
    if theme == "light":
        band.setColorAt(0.0, QColor(255, 255, 255, 105))
        band.setColorAt(0.22, QColor(255, 255, 255, 12))
        band.setColorAt(0.55, QColor(255, 255, 255, 0))
        band.setColorAt(0.85, QColor(255, 255, 255, 45))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        hi = 170
    else:
        band.setColorAt(0.0, QColor(255, 255, 255, 70))
        band.setColorAt(0.20, QColor(255, 255, 255, 8))
        band.setColorAt(0.58, QColor(255, 255, 255, 0))
        band.setColorAt(0.88, QColor(160, 190, 255, 32))
        band.setColorAt(1.0, QColor(255, 255, 255, 0))
        hi = 90
    painter.fillRect(rect, band)

    top = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.35)
    top.setColorAt(0.0, QColor(255, 255, 255, hi // 2))
    top.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.35), top)
    painter.restore()

    painter.strokePath(path, QPen(QColor(255, 255, 255, hi), 1.0))
    inner = create_squircle_path(rect.adjusted(2, 2, -2, -2), max(2.0, radius - 2.0))
    painter.strokePath(inner, QPen(QColor(0, 0, 0, 22), 0.7))

