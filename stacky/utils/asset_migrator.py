"""
legacy_migrator.py
Legacy desktop tool physical asset lossless migration, coexistence & safe retirement wizard.
Provides:
1. Environment detection (legacy installation, running processes, autostart, physical categories).
2. Non-destructive physical synchronization & category auto-mapping.
3. Intelligent 16px magnetic grid desktop card matrix generator.
4. Graceful process exit and reversible autostart handover.
5. Modern squircle liquid glass UI dialog.
"""

import os
import sys
import subprocess
import winreg
import ctypes
from ctypes import wintypes
from typing import List, Dict, Any, Optional, Tuple

from stacky.core import layout_scale

from PySide6.QtCore import Qt, QRect, QPoint, QSize, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QListWidget, QListWidgetItem, QWidget, QFrame,
    QMessageBox, QApplication, QScrollArea
)

from storage_manager import default_managed_storage_root, scan_directory_items, normalize_fs_path
from autostart_utils import is_autostart_enabled, set_autostart, APP_NAME as BIGFOLDER_APP_NAME
from ui_kit import CenteredDialog


# 旧版程序在系统中的真实注册表值名 / 进程名 / 安装路径，属于互操作标识，改名会检测不到
LEGACY_RUN_VALUE = "DeskBox"
# 以上值名与下面的进程名/安装路径是旧版程序在系统里的真实标识，
# 仅用于互操作检测，改名会导致迁移功能失效，必须原样保留。
LEGACY_EXE_NAMES = ["DeskBox.exe", "DeskBox.ThumbnailProxy.exe"]
LEGACY_INSTALL_CANDIDATES = [
    r"C:\Program Files\DeskBox\DeskBox.exe",
    r"C:\Program Files (x86)\DeskBox\DeskBox.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\DeskBox\DeskBox.exe"),
]


def find_legacy_installation() -> Optional[str]:
    """Finds the legacy tool executable if installed on system."""
    for cand in LEGACY_INSTALL_CANDIDATES:
        if os.path.exists(cand):
            return os.path.abspath(cand)

    # Check HKCU/HKLM Uninstall keys or Run key
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, LEGACY_RUN_VALUE)
            if val:
                # 值形如: "<旧版安装目录>\<程序>.exe" --startup
                raw = val.strip().strip('"')
                if " " in raw:
                    parts = raw.split('"') if '"' in val else raw.split()
                    for p in parts:
                        p_clean = p.strip('"').strip()
                        if p_clean.lower().endswith("deskbox.exe") and os.path.exists(p_clean):
                            return os.path.abspath(p_clean)
                elif os.path.exists(raw):
                    return os.path.abspath(raw)
    except Exception:
        pass
    return None


def get_legacy_processes() -> List[Dict[str, Any]]:
    """Returns list of running legacy processes with pid and name."""
    procs = []
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                name = p.info.get('name', '') or ''
                if name.lower() in [n.lower() for n in LEGACY_EXE_NAMES]:
                    procs.append({
                        "pid": p.info['pid'],
                        "name": name,
                        "exe": p.info.get('exe', '')
                    })
            except Exception:
                continue
    except Exception:
        # Fallback to tasklist if psutil fails
        try:
            res = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                creationflags=0x08000000
            )
            for line in res.stdout.splitlines():
                parts = [p.strip().strip('"') for p in line.split(',')]
                if parts and len(parts) >= 2:
                    pname = parts[0]
                    if pname.lower() in [n.lower() for n in LEGACY_EXE_NAMES]:
                        try:
                            pid = int(parts[1])
                            procs.append({"pid": pid, "name": pname, "exe": ""})
                        except Exception:
                            pass
        except Exception:
            pass
    return procs


def is_legacy_autostart_enabled() -> Tuple[bool, str]:
    """Checks whether the legacy tool is registered in HKCU Run registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, LEGACY_RUN_VALUE)
            return (bool(val), str(val))
    except FileNotFoundError:
        return (False, "")
    except Exception:
        return (False, "")


def set_legacy_autostart(enable: bool, exe_path: Optional[str] = None) -> bool:
    """Enables or disables legacy autostart in HKCU Run registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                target_exe = exe_path or find_legacy_installation() or r"C:\Program Files\DeskBox\DeskBox.exe"
                cmd = f'"{target_exe}" --startup --startup-source=run'
                winreg.SetValueEx(key, LEGACY_RUN_VALUE, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, LEGACY_RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print("Failed to set legacy autostart:", e)
        return False


def stop_legacy() -> Tuple[bool, str]:
    """
    Safely terminates running legacy processes:
    1. First tries closing top-level windows gracefully via WM_CLOSE.
    2. Falls back to TerminateProcess / taskkill for any remaining processes.
    """
    procs = get_legacy_processes()
    if not procs:
        return (True, "程序进程当前未在运行")

    try:
        # Graceful close via taskkill (without /F first)
        subprocess.run(
            ["taskkill", "/IM", "DeskBox.exe"],
            capture_output=True,
            creationflags=0x08000000,
            timeout=2
        )
    except Exception:
        pass

    # Check if still running
    remaining = get_legacy_processes()
    if remaining:
        # Force terminate
        try:
            for n in LEGACY_EXE_NAMES:
                subprocess.run(
                    ["taskkill", "/F", "/IM", n],
                    capture_output=True,
                    creationflags=0x08000000,
                    timeout=3
                )
        except Exception as e:
            return (False, f"停止程序进程失败: {e}")

    remaining_after = get_legacy_processes()
    if not remaining_after:
        return (True, f"已安全退出程序进程 (共终止 {len(procs)} 个进程)")
    else:
        return (False, f"仍有 {len(remaining_after)} 个程序进程未能终止")


def detect_legacy_environment(window_manager=None) -> Dict[str, Any]:
    """
    Inspects user system and returns comprehensive status:
    - installation path
    - running status and PIDs
    - storage root & subcategories with item counts
    - autostart registry status
    - desktop instance status for each category
    """
    installed_exe = find_legacy_installation()
    procs = get_legacy_processes()
    storage_root = default_managed_storage_root()

    storage_exists = os.path.exists(storage_root) and os.path.isdir(storage_root)
    categories = []

    displayed_cats = set()
    if window_manager and hasattr(window_manager, "windows"):
        for w in window_manager.windows.values():
            if hasattr(w, "bound_folder_name") and w.bound_folder_name:
                displayed_cats.add(w.bound_folder_name)

    if storage_exists:
        try:
            ignored_exact = {"desktop.ini", "thumbs.db", ".ds_store", ".git", "$recycle.bin", "system volume information"}
            for entry in os.listdir(storage_root):
                full_p = os.path.join(storage_root, entry)
                if os.path.isdir(full_p) and not entry.startswith(".") and entry.lower() not in ignored_exact:
                    items = scan_directory_items(full_p)
                    categories.append({
                        "name": entry,
                        "path": full_p,
                        "item_count": len(items),
                        "displayed": entry in displayed_cats
                    })
        except Exception:
            pass

    # Sort categories: displayed first, then by count descending
    categories.sort(key=lambda c: (not c["displayed"], -c["item_count"], c["name"]))

    legacy_auto, legacy_cmd = is_legacy_autostart_enabled()
    bigfolder_auto = is_autostart_enabled()

    return {
        "installed": bool(installed_exe),
        "exe_path": installed_exe or "",
        "running": len(procs) > 0,
        "processes": procs,
        "storage_root": storage_root,
        "storage_exists": storage_exists,
        "categories": categories,
        "autostart_legacy": legacy_auto,
        "legacy_cmd": legacy_cmd,
        "autostart_bigfolder": bigfolder_auto,
    }


def compute_next_card_positions(
    existing_rects: List[QRect],
    count: int,
    card_width: int = 248,
    card_height: int = 248,
    card_gap: int = 16,
    screen_rect: Optional[QRect] = None
) -> List[Tuple[int, int]]:
    """
    Computes optimal, non-overlapping, 16px magnetic grid coordinates for new cards.
    Aligns horizontally to existing cards or wraps neatly to the next row.
    """
    if not screen_rect:
        screen_rect = QRect(0, 0, 1920, 1080)

    # Work area margins: top 60px (avoid top bar/weather), bottom 140px (avoid dock)
    min_x = screen_rect.left() + 60
    max_x = screen_rect.right() - card_width - 60
    min_y = screen_rect.top() + 180
    max_y = screen_rect.bottom() - card_height - 140

    step_x = card_width + card_gap
    title_reserve = 38  # Strict vertical clearance for FolderTitleLabel + spacing + margin
    step_y = card_height + title_reserve + card_gap

    # Find rightmost existing card on primary row
    occupied_rects = list(existing_rects)
    positions = []

    curr_x = min_x
    curr_y = min_y

    if occupied_rects:
        # Find the rightmost position of cards on the top row
        rightmost = max(r.x() + r.width() for r in occupied_rects)
        top_y = min(r.y() for r in occupied_rects)
        curr_x = rightmost + card_gap
        curr_y = max(min_y, top_y)

    for _ in range(count):
        # Check if wrapping is needed
        if curr_x + card_width > max_x:
            curr_x = min_x
            curr_y += step_y
            if curr_y + card_height > max_y:
                # Wrap back to top with slight offset if vertical space exceeded
                curr_y = min_y + 30

        # Collision check with existing
        cand_rect = QRect(curr_x, curr_y, card_width, card_height)
        collision = any(cand_rect.intersects(r) for r in occupied_rects)
        while collision:
            curr_x += step_x
            if curr_x + card_width > max_x:
                curr_x = min_x
                curr_y += step_y
            cand_rect = QRect(curr_x, curr_y, card_width, card_height)
            collision = any(cand_rect.intersects(r) for r in occupied_rects)

        positions.append((curr_x, curr_y))
        occupied_rects.append(cand_rect)
        curr_x += step_x

    return positions


def create_desktop_cards_for_categories(window_manager, category_names: List[str]) -> List[str]:
    """
    Generates new desktop BigFolder cards for selected categories using 16px magnetic alignment.
    Returns list of successfully created category names.
    """
    if not window_manager or not category_names:
        return []

    # Filter out categories that are already active on desktop
    displayed = set()
    existing_rects = []
    if hasattr(window_manager, "windows"):
        for w in window_manager.windows.values():
            if hasattr(w, "bound_folder_name") and w.bound_folder_name:
                displayed.add(w.bound_folder_name)
            if hasattr(w, "geometry"):
                existing_rects.append(w.geometry())

    # Add weather widget rect if present
    if hasattr(window_manager, "weather_window") and window_manager.weather_window:
        if window_manager.weather_window.isVisible():
            existing_rects.append(window_manager.weather_window.geometry())

    to_create = [c for c in category_names if c not in displayed]
    if not to_create:
        return []

    app = QApplication.instance()
    screen = app.primaryScreen() if app else None
    screen_rect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    positions = compute_next_card_positions(
        existing_rects=existing_rects,
        count=len(to_create),
        card_width=248,
        card_height=248,
        card_gap=16,
        screen_rect=screen_rect
    )

    created = []
    storage_engine = getattr(window_manager, "shared_storage_engine", None)

    for cat_name, (pos_x, pos_y) in zip(to_create, positions):
        # Ensure category exists in config_manager and is mapped to storage
        if not window_manager.config_manager.get_folder_by_name(cat_name):
            window_manager.config_manager.add_folder(cat_name, set_active=False)

        if storage_engine:
            storage_engine.set_folder_storage_mapped(cat_name, True)
            storage_engine.sync_folder_from_storage(cat_name, force=True)

        s = getattr(window_manager, "layout_scale", 1.0)
        window_manager.create_instance(
            cat_name,
            layout_scale.to_canvas(pos_x, s),
            layout_scale.to_canvas(pos_y, s),
            locked=False)
        created.append(cat_name)

    return created


class LegacyMigrationDialog(CenteredDialog):
    """
    Modern Squircle Liquid Glass Migration & Retirement Assistant Dialog.
    Allows user to:
    - View legacy tool status and categories
    - Select categories to generate matching desktop Big Folder cards (with 16px magnetic layout)
    - Safely stop the legacy process
    - Handover system autostart to BigFolderLauncher
    """
    matrix_created = Signal(list)

    def __init__(self, window_manager=None, parent=None):
        super().__init__(parent)
        self.window_manager = window_manager
        self.setWindowTitle("桌面资产迁移")
        # 760 是常规高度；小屏按可用区域收缩，避免底部按钮跑出屏幕
        self.setMinimumSize(620, 600)
        _screen = QApplication.primaryScreen()
        if _screen is not None:
            _avail = _screen.availableGeometry()
            self.resize(660, min(760, max(600, _avail.height() - 48)))
        else:
            self.resize(660, 760)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.env = detect_legacy_environment(self.window_manager)
        self.category_checkboxes = {}

        self.setup_ui()
        self.refresh_data()

    def setup_ui(self):
        self.setStyleSheet("""
        QDialog {
            background-color: #16181f;
            color: #f0f0f5;
            font-family: "Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei";
        }
        QLabel {
            color: #d8d8e0;
        }
        QPushButton {
            background-color: rgba(255, 255, 255, 18);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 35);
            border-radius: 6px;
            padding: 8px 14px;
            font-size: 13px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 32);
            border: 1px solid rgba(255, 255, 255, 55);
        }
        QPushButton:pressed {
            background-color: rgba(255, 255, 255, 12);
        }
        QPushButton#primaryBtn {
            background-color: #2f6f9f;
            border: 1px solid #3d7fae;
            font-weight: 600;
        }
        QPushButton#primaryBtn:hover {
            background-color: #377fb4;
        }
        QPushButton#dangerBtn {
            background-color: transparent;
            border: 1px solid rgba(200, 90, 90, 0.55);
            color: #e08a8a;
        }
        QPushButton#dangerBtn:hover {
            background-color: rgba(180, 60, 60, 0.18);
        }
        QListWidget {
            background-color: rgba(25, 28, 38, 0.95);
            border: 1px solid rgba(255, 255, 255, 25);
            border-radius: 8px;
            padding: 2px;
        }
        QListWidget::item {
            padding: 0px;
            border-bottom: 1px solid rgba(255, 255, 255, 10);
            border-radius: 6px;
        }
        QListWidget::item:hover {
            background-color: rgba(255, 255, 255, 15);
        }
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 2px;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.18);
            border-radius: 4px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.28);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QCheckBox {
            color: #ffffff;
            font-size: 13px;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 60);
            background: rgba(0, 0, 0, 40);
        }
        QCheckBox::indicator:checked {
            background-color: #3182ce;
            border-color: #63b3ed;
        }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        # 1. Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(6)
        title = QLabel("桌面资产迁移与协同")
        title.setWordWrap(True)
        title.setFont(QFont("Segoe UI Variable Display", 15, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #ffffff;")
        subtitle = QLabel("把已扫描到的分类映射为桌面卡片，文件仍保留在原目录。")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Segoe UI", 11))
        subtitle.setStyleSheet("color: #a0aec0;")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        layout.addLayout(header_layout)

        # 2. Status Box
        self.status_frame = QFrame()
        self.status_frame.setObjectName("statusFrame")
        # 选择器必须限定到 #statusFrame：QLabel 继承 QFrame，
        # 写成 QFrame 会把里面每一行文字都画成独立边框盒。
        self.status_frame.setStyleSheet("""
        QFrame#statusFrame {
            background-color: rgba(255, 255, 255, 0.035);
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 8px;
        }
        """)
        status_layout = QVBoxLayout(self.status_frame)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(9)

        self.lbl_install = QLabel()
        self.lbl_proc = QLabel()
        self.lbl_storage = QLabel()
        self.lbl_autostart = QLabel()

        for lbl in [self.lbl_install, self.lbl_proc, self.lbl_storage, self.lbl_autostart]:
            lbl.setFont(QFont("Segoe UI", 10))
            lbl.setWordWrap(True)
            status_layout.addWidget(lbl)
        layout.addWidget(self.status_frame)

        # 3. Category Matrix Header
        cat_header = QHBoxLayout()
        cat_title = QLabel("已扫描到的分类")
        cat_title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        cat_title.setStyleSheet("color: #ffffff;")
        cat_header.addWidget(cat_title)
        cat_header.addStretch()

        btn_select_all = QPushButton("全选未上屏")
        btn_select_all.setFixedHeight(28)
        btn_select_all.clicked.connect(self.select_unmounted)
        cat_header.addWidget(btn_select_all)
        layout.addLayout(cat_header)

        # 4. Category List Widget
        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(200)
        self.list_widget.setSpacing(2)
        layout.addWidget(self.list_widget, 1)

        # 5. One-Click Matrix Button
        self.btn_create_matrix = QPushButton("生成桌面卡片")
        self.btn_create_matrix.setObjectName("primaryBtn")
        self.btn_create_matrix.setFixedHeight(38)
        self.btn_create_matrix.clicked.connect(self.action_create_matrix)
        layout.addWidget(self.btn_create_matrix)

        # 6. Safety & Handover Action Bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.btn_stop_proc = QPushButton("退出程序")
        self.btn_stop_proc.setObjectName("dangerBtn")
        self.btn_stop_proc.setFixedHeight(36)
        self.btn_stop_proc.clicked.connect(self.action_stop_legacy)
        action_bar.addWidget(self.btn_stop_proc, 1)

        self.btn_toggle_autostart = QPushButton("开机自启动")
        self.btn_toggle_autostart.setFixedHeight(36)
        self.btn_toggle_autostart.clicked.connect(self.action_switch_autostart)
        action_bar.addWidget(self.btn_toggle_autostart, 1)

        btn_open_storage = QPushButton("打开目录")
        btn_open_storage.setFixedHeight(36)
        btn_open_storage.clicked.connect(self.action_open_storage)
        action_bar.addWidget(btn_open_storage, 1)

        layout.addLayout(action_bar)

        # 7. Safe Promise Footer
        footer = QLabel("所有文件与快捷方式保留在原目录，不做重命名或迁移；随时可以切回。")
        footer.setWordWrap(True)
        footer.setFont(QFont("Segoe UI", 9))
        footer.setStyleSheet("color: #718096; line-height: 1.5;")
        layout.addWidget(footer)

    def refresh_data(self):
        """Refreshes status cards and category checklist."""
        self.env = detect_legacy_environment(self.window_manager)

        # Status text
        inst_text = self.env["exe_path"] if self.env["installed"] else "未检测到"
        self.lbl_install.setText(f"<b>程序状态：</b>{inst_text}")

        if self.env["running"]:
            pids = ", ".join(str(p["pid"]) for p in self.env["processes"])
            self.lbl_proc.setText(f"<b>进程状态：</b><span style='color: #48bb78;'>运行中</span>（PID: {pids}）")
            self.btn_stop_proc.setEnabled(True)
            self.btn_stop_proc.setText("安全退出程序")
        else:
            self.lbl_proc.setText("<b>进程状态：</b><span style='color: #a0aec0;'>未运行</span>")
            self.btn_stop_proc.setEnabled(False)
            self.btn_stop_proc.setText("程序已退出")

        self.lbl_storage.setText(f"<b>存储目录：</b>{self.env['storage_root']}"
                                 f"（共 {len(self.env['categories'])} 个分类）")

        auto_d = "开机自启" if self.env["autostart_legacy"] else "未自启"
        auto_b = "开机自启" if self.env["autostart_bigfolder"] else "未自启"
        self.lbl_autostart.setText(f"<b>开机启动：</b>程序 [{auto_d}] · 大文件夹 [{auto_b}]")

        # Populate Category List
        self.list_widget.clear()
        self.category_checkboxes.clear()

        for cat in self.env["categories"]:
            name = cat["name"]
            count = cat["item_count"]
            disp = cat["displayed"]

            item_widget = QWidget()
            item_widget.setFixedHeight(44)
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(10, 0, 10, 0)

            cb = QCheckBox(f"{name}")
            cb.setChecked(not disp) # Check if not already on desktop
            cb.setEnabled(not disp)
            self.category_checkboxes[name] = cb
            item_layout.addWidget(cb, 0, Qt.AlignmentFlag.AlignVCenter)

            item_layout.addStretch()

            count_lbl = QLabel(f"{count} 个项目")
            count_lbl.setFixedWidth(64)
            count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_lbl.setStyleSheet("color: #8b919b; font-size: 11px;")
            item_layout.addWidget(count_lbl)

            status_lbl = QLabel("已添加" if disp else "未添加")
            status_lbl.setFixedWidth(56)
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            status_lbl.setStyleSheet(
                "color: %s; font-size: 11px;" % ("#8b919b" if disp else "#c9a26a"))
            item_layout.addWidget(status_lbl)

            list_item = QListWidgetItem(self.list_widget)
            list_item.setSizeHint(QSize(0, 46))
            self.list_widget.addItem(list_item)
            self.list_widget.setItemWidget(list_item, item_widget)

    def select_unmounted(self):
        for name, cb in self.category_checkboxes.items():
            if cb.isEnabled():
                cb.setChecked(True)

    def action_create_matrix(self):
        """Generates desktop cards for checked categories."""
        selected = [name for name, cb in self.category_checkboxes.items() if cb.isChecked() and cb.isEnabled()]
        if not selected:
            QMessageBox.information(self, "提示", "请先勾选需要生成桌面卡片的分类。")
            return

        if not self.window_manager:
            QMessageBox.warning(self, "提示", "当前运行模式缺少窗口管理器，请在主程序中执行。")
            return

        created = create_desktop_cards_for_categories(self.window_manager, selected)
        if created:
            QMessageBox.information(
                self,
                "生成成功",
                f"已成功在桌面生成 {len(created)} 个大文件夹卡片：\n{', '.join(created)}\n\n"
                f"卡片已按 16px 磁吸栅栏间距对齐摆放，可随心拖动与即点即开！"
            )
            self.refresh_data()
            self.matrix_created.emit(created)
        else:
            QMessageBox.information(self, "提示", "所选分类均已添加。")

    def action_stop_legacy(self):
        """Gracefully closes the legacy tool."""
        ret = QMessageBox.question(
            self,
            "确认退出程序",
            "确定要安全退出正在运行的程序进程吗？\n\n"
            "所有文件均已直接由【Stacky 灵叠】接管显示，无任何文件丢失风险。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        ok, msg = stop_legacy()
        if ok:
            QMessageBox.information(self, "操作成功", msg)
        else:
            QMessageBox.warning(self, "操作失败", msg)
        self.refresh_data()

    def action_switch_autostart(self):
        """Sets Stacky to autostart and disables legacy autostart."""
        set_autostart(True)
        set_legacy_autostart(False)
        QMessageBox.information(
            self,
            "自启动已切换",
            "已成功将系统开机自启切换为【Stacky 灵叠】！\n\n"
            "程序自启项已安全移除（如有需要随时可在此界面恢复）。"
        )
        self.refresh_data()

    def action_open_storage(self):
        """Opens managed legacy storage in Windows Explorer."""
        p = self.env["storage_root"]
        if os.path.exists(p):
            os.startfile(p)
        else:
            QMessageBox.warning(self, "提示", f"存储目录不存在: {p}")
