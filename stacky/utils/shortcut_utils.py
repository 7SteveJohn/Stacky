from stacky.paths import ROOT
import os
import sys
import struct
import win32com.client
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import (
    QImage, QPainter, QColor, QLinearGradient, QRadialGradient,
    QPen, QPainterPath, QTransform
)
from PySide6.QtCore import Qt, QRectF, QPointF, QByteArray, QBuffer

from icon_utils import create_squircle_path, resolve_icon_location_for_path

def generate_squircle_app_icon(output_ico_path: str) -> str:
    """
    Renders an authentic retina Stacky (灵叠) super symbol icon:
    Obsidian titanium continuous-curvature squircle base + 3D isometric floating crystal slabs.
    Compiles a multi-resolution Windows .ico (256, 128, 64, 48, 32, 24, 16px) and companion PNG.
    """
    _ = QApplication.instance() or QApplication(sys.argv if sys.argv else [])

    size = 512
    img512 = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img512.fill(Qt.GlobalColor.transparent)

    painter = QPainter(img512)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    s = size / 512.0

    # 1. Base Squircle: Deep Space Obsidian Titanium Canvas
    margin = 28.0 * s
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    r = 104.0 * s
    base_path = QPainterPath()
    base_path.addRoundedRect(rect, r, r)

    # Base Fill
    base_grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
    base_grad.setColorAt(0.0, QColor(19, 24, 38))    # Deep space navy
    base_grad.setColorAt(0.45, QColor(10, 12, 20))   # Midnight obsidian
    base_grad.setColorAt(1.0, QColor(16, 20, 32))    # Titanium slate
    painter.fillPath(base_path, base_grad)

    # Inner Cosmic Radial Flare
    nebula = QRadialGradient(rect.center().x(), rect.center().y(), 220 * s)
    nebula.setColorAt(0.0, QColor(99, 102, 241, 55))   # Royal Indigo core
    nebula.setColorAt(0.5, QColor(6, 182, 212, 32))    # Aurora Cyan wash
    nebula.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillPath(base_path, nebula)

    # 1.5px Outer Aerospace Bevel Rim Light
    rim = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
    rim.setColorAt(0.0, QColor(255, 255, 255, 220))    # Specular corner catch
    rim.setColorAt(0.35, QColor(56, 189, 248, 160))   # Ice-cyan edge
    rim.setColorAt(0.7, QColor(99, 102, 241, 100))    # Indigo glow
    rim.setColorAt(1.0, QColor(255, 255, 255, 40))     # Soft bottom reflection
    painter.strokePath(base_path, QPen(rim, 3.2 * s))

    # Inner dark groove
    groove_rect = rect.adjusted(3.5 * s, 3.5 * s, -3.5 * s, -3.5 * s)
    groove_path = QPainterPath()
    groove_path.addRoundedRect(groove_rect, r - 2 * s, r - 2 * s)
    painter.strokePath(groove_path, QPen(QColor(0, 0, 0, 150), 1.6 * s))

    # 2. Seamless 3D Isometric Stack Slabs (灵叠空间悬浮晶体)
    slab_w = 194.0 * s
    slab_h = 194.0 * s
    slab_corner = 44.0 * s
    cx, cy = 256.0 * s, 256.0 * s

    slabs = [
        # (offset_y, top_gradient_stops, rim_color, shadow_dy, shadow_alpha, extrusion_h, side_dark_factor)
        (
            66.0 * s,
            [(0.0, QColor(129, 140, 248, 245)), (0.5, QColor(99, 102, 241, 255)), (1.0, QColor(67, 56, 202, 255))],
            QColor(199, 210, 254, 230),
            18.0 * s, 0.45, 14.0 * s, 140
        ),
        (
            0.0 * s,
            [(0.0, QColor(34, 211, 238, 250)), (0.45, QColor(14, 165, 233, 255)), (1.0, QColor(2, 132, 199, 255))],
            QColor(224, 242, 254, 240),
            18.0 * s, 0.45, 14.0 * s, 145
        ),
        (
            -66.0 * s,
            [(0.0, QColor(255, 255, 255, 255)), (0.4, QColor(241, 245, 249, 245)), (1.0, QColor(207, 250, 254, 235))],
            QColor(255, 255, 255, 255),
            22.0 * s, 0.5, 15.0 * s, 130
        )
    ]

    p2d = QPainterPath()
    p2d.addRoundedRect(QRectF(-slab_w/2, -slab_h/2, slab_w, slab_h), slab_corner, slab_corner)

    for offset_y, grad_stops, rim_col, s_dy, s_alpha, ext_h, side_dark in slabs:
        # Drop Shadow
        painter.save()
        painter.setOpacity(s_alpha)
        t_sh = QTransform()
        t_sh.translate(cx, cy + offset_y + ext_h + s_dy)
        t_sh.scale(1.0, 0.58)
        t_sh.rotate(45)
        painter.fillPath(t_sh.map(p2d), QColor(0, 0, 0, 255))
        painter.restore()

        # Seamless extruded 3D side walls using dense step slices
        num_slices = int(max(8, ext_h * 2))
        base_side_col = grad_stops[-1][1].darker(side_dark)
        for i in range(num_slices):
            slice_y = offset_y + (i / float(num_slices)) * ext_h
            t_slice = QTransform()
            t_slice.translate(cx, cy + slice_y)
            t_slice.scale(1.0, 0.58)
            t_slice.rotate(45)
            frac = i / float(num_slices)
            cur_col = base_side_col.darker(int(100 + frac * 35))
            painter.fillPath(t_slice.map(p2d), cur_col)

        # Top Surface
        t_top = QTransform()
        t_top.translate(cx, cy + offset_y)
        t_top.scale(1.0, 0.58)
        t_top.rotate(45)
        iso_top_path = t_top.map(p2d)

        top_grad = QLinearGradient(cx, cy + offset_y - 60*s, cx, cy + offset_y + 60*s)
        for stop_pos, col in grad_stops:
            top_grad.setColorAt(stop_pos, col)
        painter.fillPath(iso_top_path, top_grad)

        # Specular Top Rim Chamfer
        top_rim = QLinearGradient(cx - 80*s, cy + offset_y - 40*s, cx + 80*s, cy + offset_y + 40*s)
        top_rim.setColorAt(0.0, rim_col)
        top_rim.setColorAt(0.5, QColor(255, 255, 255, 240))
        top_rim.setColorAt(1.0, QColor(rim_col.red(), rim_col.green(), rim_col.blue(), 90))
        painter.strokePath(iso_top_path, QPen(top_rim, 2.6 * s))

    # 3. Top Prism Optical Lens: Luminous 4-Point Aurora Star
    top_cy = cy - 66.0 * s
    star = QPainterPath()
    star_r = 24.0 * s
    star.moveTo(cx, top_cy - star_r)
    star.quadTo(cx, top_cy, cx + star_r * 1.35, top_cy)
    star.quadTo(cx, top_cy, cx, top_cy + star_r)
    star.quadTo(cx, top_cy, cx - star_r * 1.35, top_cy)
    star.quadTo(cx, top_cy, cx, top_cy - star_r)

    # Ambient radial cyan star bloom
    painter.save()
    painter.setOpacity(0.55)
    star_glow = QPainterPath()
    sg_r = star_r * 1.65
    star_glow.moveTo(cx, top_cy - sg_r)
    star_glow.quadTo(cx, top_cy, cx + sg_r * 1.35, top_cy)
    star_glow.quadTo(cx, top_cy, cx, top_cy + sg_r)
    star_glow.quadTo(cx, top_cy, cx - sg_r * 1.35, top_cy)
    star_glow.quadTo(cx, top_cy, cx - sg_r * 1.35, top_cy)
    star_glow.quadTo(cx, top_cy, cx, top_cy - sg_r)
    painter.fillPath(star_glow, QColor(56, 189, 248, 255))
    painter.restore()

    # Core star: Brilliant cyan-sapphire gradient
    star_grad = QLinearGradient(cx - star_r, top_cy - star_r, cx + star_r, top_cy + star_r)
    star_grad.setColorAt(0.0, QColor(34, 211, 238, 255))
    star_grad.setColorAt(1.0, QColor(14, 165, 233, 255))
    painter.fillPath(star, star_grad)
    painter.strokePath(star, QPen(QColor(255, 255, 255, 255), 1.8 * s))

    # Micro optical diamond flare at exact center
    flare = QPainterPath()
    flare_r = 5.5 * s
    flare.addEllipse(QPointF(cx, top_cy), flare_r, flare_r)
    painter.fillPath(flare, QColor(255, 255, 255, 255))

    painter.end()

    # Downsample to master 256x256 for standard high-dpi display
    img256 = img512.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)

    os.makedirs(os.path.dirname(os.path.abspath(output_ico_path)), exist_ok=True)
    png_companion_path = os.path.splitext(output_ico_path)[0] + ".png"
    img256.save(png_companion_path, "PNG")

    # Use Pillow to compile authentic Windows ICO with standard multi-resolution bitmap/PNG frames
    try:
        from PIL import Image
        pil_im = Image.open(png_companion_path)
        ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        pil_im.save(output_ico_path, format="ICO", sizes=ico_sizes)
        return output_ico_path
    except Exception as e:
        print("Pillow ICO export failed, falling back to binary packer:", e)

    # Fallback multi-resolution binary ICO structure
    target_sizes = [256, 128, 64, 48, 32, 16]
    images_png = []
    for s_sz in target_sizes:
        if s_sz == 256:
            scaled = img256
        else:
            scaled = img256.scaled(s_sz, s_sz, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        scaled.save(buf, "PNG")
        buf.close()
        images_png.append((s_sz, bytes(ba.data())))

    num_images = len(images_png)
    header = struct.pack("<HHH", 0, 1, num_images)
    entries = []
    offset = 6 + 16 * num_images

    for s_sz, png_data in images_png:
        w_b = 0 if s_sz == 256 else s_sz
        h_b = 0 if s_sz == 256 else s_sz
        entry = struct.pack("<BBBBHHII", w_b, h_b, 0, 0, 1, 32, len(png_data), offset)
        entries.append(entry)
        offset += len(png_data)

    with open(output_ico_path, "wb") as f:
        f.write(header)
        for e in entries:
            f.write(e)
        for _, png_data in images_png:
            f.write(png_data)

    return output_ico_path

def refresh_windows_icon_cache(force: bool = False):
    """
    Notifies Windows Shell to flush icon cache and redraw desktop shortcuts immediately.
    force=True 额外重建 Explorer 图标缓存数据库（仅在用户主动把文件放回桌面时使用）。
    """
    try:
        import ctypes
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass
    if force:
        try:
            import subprocess
            subprocess.run(["C:\\Windows\\System32\\ie4uinit.exe", "-show"], timeout=3)
        except Exception:
            pass

def ensure_high_res_app_icon(project_dir: str = None, force: bool = False) -> str:
    """
    Ensures a valid high-resolution Stacky squircle icon exists in .cache_icons/app_icon.ico.
    """
    if not project_dir:
        if getattr(sys, "frozen", False):
            project_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            project_dir = ROOT
    icon_path = os.path.join(project_dir, ".cache_icons", "app_icon.ico")
    png_path = os.path.join(project_dir, ".cache_icons", "app_icon.png")
    if force or not os.path.exists(icon_path) or not os.path.exists(png_path) or os.path.getsize(icon_path) < 10000:
        generate_squircle_app_icon(icon_path)
    return icon_path

def create_shortcut_file(
    target_path: str,
    output_lnk_path: str,
    args: str = "",
    working_dir: str = "",
    icon_location: str = "",
    description: str = "Stacky 灵叠 - 现代化桌面灵动空间与大文件夹组织器"
) -> bool:
    """
    Creates or rewrites a Windows .lnk shortcut using WScript.Shell COM object.
    """
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(output_lnk_path)
        shortcut.TargetPath = target_path
        shortcut.Arguments = args
        shortcut.WorkingDirectory = working_dir or os.path.dirname(target_path)
        shortcut.Description = description
        # 收纳/迁移/解散时新建的快捷方式必须继承原快捷方式的图标，
        # 否则 Windows 会画白纸，用户手动设过的图标也会丢。
        if not icon_location:
            icon_location = resolve_icon_location_for_path(target_path)
        icon_file = icon_location.split(",")[0].strip() if icon_location else ""
        if icon_file and os.path.exists(icon_file):
            shortcut.IconLocation = icon_location
        shortcut.Save()
        return True
    except Exception as e:
        print(f"Error creating shortcut {output_lnk_path}:", e)
        return False

def get_desktop_dir() -> str:
    """
    Safely resolves the current user's actual Desktop directory across
    all Windows installations (including OneDrive backup desktop).
    """
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        d = shell.SpecialFolders("Desktop")
        if d and os.path.exists(d):
            return d
    except Exception:
        pass
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0000
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf) == 0:
            if buf.value and os.path.exists(buf.value):
                return buf.value
    except Exception:
        pass
    fallback = os.path.expandvars(r"%USERPROFILE%\Desktop")
    return fallback if os.path.exists(fallback) else ""

def auto_heal_shortcut(
    lnk_path: str,
    expected_target: str,
    expected_args: str,
    expected_dir: str,
    expected_icon: str
) -> bool:
    """
    Inspects an existing .lnk shortcut. If it points to a moved directory, outdated
    arguments, or invalid icon, heals it in place.
    """
    if not os.path.exists(lnk_path):
        return False
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(lnk_path)
        cur_target = shortcut.TargetPath or ""
        cur_args = shortcut.Arguments or ""
        cur_dir = shortcut.WorkingDirectory or ""
        cur_icon = (shortcut.IconLocation or "").split(",")[0]

        needs_heal = False
        if not os.path.exists(cur_target) or os.path.normpath(cur_target).lower() != os.path.normpath(expected_target).lower():
            needs_heal = True
        elif cur_args.strip().lower() != expected_args.strip().lower():
            needs_heal = True
        elif not os.path.exists(cur_dir) or os.path.normpath(cur_dir).lower() != os.path.normpath(expected_dir).lower():
            needs_heal = True
        elif expected_icon and (not os.path.exists(cur_icon) or os.path.normpath(cur_icon).lower() != os.path.normpath(expected_icon).lower()):
            needs_heal = True

        if needs_heal:
            shortcut.TargetPath = expected_target
            shortcut.Arguments = expected_args
            shortcut.WorkingDirectory = expected_dir
            if expected_icon and os.path.exists(expected_icon):
                shortcut.IconLocation = expected_icon
            shortcut.Save()
            return True
        return False
    except Exception as e:
        print(f"Error checking/healing shortcut {lnk_path}:", e)
        return False

def generate_mydock_shortcut(
    project_dir: str = None,
    heal_only: bool = False,
    desktop_dir: str = None,
    create_desktop: bool = True
) -> str:
    """
    Generates and/or self-heals a dedicated shortcut for a Dock launcher / Desktop.
    Points to pythonw.exe main.py with --toggle and attaches a high-res squircle icon.
    Returns the path of the project .lnk.
    """
    if not project_dir:
        if getattr(sys, "frozen", False):
            project_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            project_dir = ROOT

    if getattr(sys, "frozen", False):
        target = sys.executable
        arguments = "--toggle"
        working_dir = project_dir
    else:
        py_executable = sys.executable
        py_dir = os.path.dirname(py_executable)
        pyw_candidate = os.path.join(py_dir, "pythonw.exe")
        if os.path.exists(pyw_candidate):
            pyw_executable = pyw_candidate
        else:
            pyw_executable = py_executable

        main_py = os.path.join(project_dir, "main.py")
        target = pyw_executable
        arguments = f'"{main_py}" --toggle'
        working_dir = project_dir

    # High-res squircle icon auto-gen
    icon_path = ensure_high_res_app_icon(project_dir)

    lnk_name = "Stacky 灵叠启动台.lnk"
    output_project_lnk = os.path.join(project_dir, lnk_name)

    target_desktop = desktop_dir if desktop_dir is not None else get_desktop_dir()
    desktop_lnk = os.path.join(target_desktop, "Stacky 灵叠.lnk") if target_desktop and os.path.exists(target_desktop) else ""

    # Clean up legacy shortcuts on desktop
    if target_desktop and os.path.exists(target_desktop):
        for legacy_name in ["仿手机大文件夹.lnk", "仿手机大文件夹启动台.lnk"]:
            legacy_p = os.path.join(target_desktop, legacy_name)
            if os.path.exists(legacy_p):
                try:
                    os.remove(legacy_p)
                except Exception:
                    pass

    if heal_only:
        if os.path.exists(output_project_lnk):
            auto_heal_shortcut(output_project_lnk, target, arguments, working_dir, icon_path)
        if create_desktop and desktop_lnk and os.path.exists(desktop_lnk):
            # Desktop shortcut triggers idempotent active summon & bring-to-front on launch
            desktop_target = target
            desktop_args = "--activate" if getattr(sys, "frozen", False) else f'"{main_py}" --activate'
            auto_heal_shortcut(desktop_lnk, desktop_target, desktop_args, working_dir, icon_path)
        return output_project_lnk

    # Create / update project .lnk (for a Dock launcher)
    create_shortcut_file(
        target_path=target,
        output_lnk_path=output_project_lnk,
        args=arguments,
        working_dir=working_dir,
        icon_location=icon_path,
        description="Stacky 灵叠 (拖入 Dock 点击直接唤醒)"
    )

    # Create / update Desktop .lnk
    if create_desktop and desktop_lnk:
        desktop_target = target
        desktop_args = "--activate" if getattr(sys, "frozen", False) else f'"{main_py}" --activate'
        create_shortcut_file(
            target_path=desktop_target,
            output_lnk_path=desktop_lnk,
            args=desktop_args,
            working_dir=working_dir,
            icon_location=icon_path,
            description="Stacky 灵叠 · 现代桌面视窗矩阵与流体启动台"
        )

    refresh_windows_icon_cache()
    return output_project_lnk
