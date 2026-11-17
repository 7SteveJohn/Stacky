"""
Managed Storage Engine for Stacky Desktop Launcher.
Provides native physical directory mapping (dedicated %USERPROFILE%\\Stacky directory),
real-time physical folder synchronization (QFileSystemWatcher),
drag-and-drop physical file moving (managedDropAction: Move),
and Desktop One-Click Tidy wizard.
"""

import os
import sys
import shutil
import re
import ctypes
from ctypes import wintypes
from typing import List, Dict, Any, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QFileSystemWatcher, QTimer

from config_manager import ConfigManager
from shortcut_utils import create_shortcut_file

# Win32 Shell Recycle Bin operation
FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004


def is_valid_fs_name(name: str) -> bool:
    """Validates that a file or folder name contains no illegal Windows characters or device names."""
    if not name or not name.strip():
        return False
    clean = name.strip()
    illegal_chars = set(r'\/:*?"<>|')
    if any(c in illegal_chars for c in clean):
        return False
    if any(ord(c) < 32 for c in clean):
        return False
    if clean.endswith(".") or clean.endswith(" "):
        return False
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
    }
    base, _ = os.path.splitext(clean)
    if clean.upper() in reserved_names or base.upper() in reserved_names:
        return False
    return True


_SELF_AND_CONTAINER_EXCLUDES = frozenset({
    "stacky",
    "stacky.lnk",
    "stacky 灵叠",
    "stacky 灵叠.lnk",
    "stacky 灵叠启动台",
    "stacky 灵叠启动台.lnk",
    "stacky files",
    "stacky files.lnk",
    "仿手机大文件夹",
    "仿手机大文件夹.lnk",
    "仿手机大文件夹启动台",
    "仿手机大文件夹启动台.lnk",
    "deskbox files",
    "deskbox files.lnk",
})


def default_managed_storage_root() -> str:
    """
    Returns the dedicated physical storage root for Stacky:
    Strictly uses %USERPROFILE%\\Stacky (creating it automatically if missing).
    If %USERPROFILE%\\Stacky is brand new/empty and a legacy desktop-tool folder exists,
    automatically copies assets non-destructively for seamless zero-loss onboarding.
    """
    user_home = os.path.expanduser("~")
    stacky_path = os.path.join(user_home, "Stacky")

    # Ensure dedicated Stacky directory exists
    try:
        os.makedirs(stacky_path, exist_ok=True)
    except Exception:
        pass

    # If Stacky directory is completely empty, check the legacy desktop-tool directory to seed assets
    try:
        if os.path.exists(stacky_path) and not os.listdir(stacky_path):
            legacy_dir = os.path.join(user_home, "DeskBox")  # 旧版固定目录名，互操作标识
            skip_seed = _SELF_AND_CONTAINER_EXCLUDES | {"desktop.ini", "thumbs.db", ".ds_store", ".git", "deskbox.lnk"}
            if os.path.exists(legacy_dir) and os.path.isdir(legacy_dir):
                for item in os.listdir(legacy_dir):
                    if item.lower() in skip_seed or item.startswith(".") or item.startswith("~"):
                        continue
                    s_src = os.path.join(legacy_dir, item)
                    s_dst = os.path.join(stacky_path, item)
                    if os.path.isdir(s_src):
                        shutil.copytree(
                            s_src,
                            s_dst,
                            dirs_exist_ok=True,
                            ignore=lambda _d, names: [n for n in names if n.lower() in skip_seed],
                        )
                    else:
                        shutil.copy2(s_src, s_dst)
    except Exception:
        pass

    return stacky_path


def get_desktop_dir() -> str:
    """Returns the user's actual Windows Desktop directory, resolving OneDrive / shell redirection."""
    if sys.platform == "win32":
        try:
            buf = (wintypes.WCHAR * 260)()
            # CSIDL_DESKTOPDIRECTORY = 0x0010
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:
                if os.path.exists(buf.value):
                    return buf.value
        except Exception:
            pass
    p = os.path.expandvars(r"%USERPROFILE%\Desktop")
    if os.path.exists(p):
        return p
    return os.path.expanduser("~/Desktop")


def get_public_desktop_dir() -> str:
    """Returns the Windows Common/Public Desktop directory if it exists."""
    pub = os.environ.get("PUBLIC", r"C:\Users\Public")
    p = os.path.join(pub, "Desktop")
    if os.path.exists(p):
        return p
    return ""


def notify_shell_change(path: Optional[str] = None):
    """Notifies Windows Shell of file system change so desktop icons refresh instantly without F5."""
    if sys.platform == "win32":
        try:
            SHCNE_ALLEVENTS = 0x7FFFFFFF
            SHCNF_PATHW = 0x0005
            ctypes.windll.shell32.SHChangeNotify(SHCNE_ALLEVENTS, SHCNF_PATHW, path, None)
        except Exception:
            pass


def is_protected_system_path(path: str) -> bool:
    """Guards against accidentally deleting or moving critical Windows or Program Files binaries."""
    if not path:
        return True
    try:
        path_abs = os.path.abspath(path).lower()
        real_abs = os.path.realpath(path).lower()
        system_roots = [
            os.environ.get("windir", r"C:\Windows").lower(),
            os.environ.get("ProgramFiles", r"C:\Program Files").lower(),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower(),
            r"c:\windows",
            r"c:\program files",
            r"c:\program files (x86)"
        ]
        for candidate in (path_abs, real_abs):
            for sr in system_roots:
                if sr and (candidate == sr or candidate.startswith(sr + os.sep)):
                    return True
    except Exception:
        pass
    return False


def send_to_recycle_bin(path: str) -> bool:
    """Sends a file or folder safely to Windows Recycle Bin using SHFileOperationW."""
    if sys.platform != "win32" or not os.path.exists(path):
        return False

    try:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)
            ]

        # Double-null terminated string required by SHFileOperation
        double_null_path = os.path.abspath(path) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = double_null_path
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return (res == 0 and not op.fAnyOperationsAborted)
    except Exception as e:
        print("Recycle bin operation error:", e)
        return False


def scan_directory_items(dir_path: str) -> List[Dict[str, Any]]:
    """
    Scans a physical directory and returns structured launcher item dicts.
    Extracts display names and metadata while skipping system/hidden clutter.
    """
    if not os.path.exists(dir_path) or not os.path.isdir(dir_path):
        return []

    items: List[Dict[str, Any]] = []
    ignored_exact = {"desktop.ini", "thumbs.db", ".ds_store", ".git"} | _SELF_AND_CONTAINER_EXCLUDES

    try:
        entries = os.listdir(dir_path)
    except Exception as e:
        print(f"Error reading directory {dir_path}:", e)
        return []

    for entry in entries:
        entry_low = entry.lower()
        if (entry_low in ignored_exact or entry.startswith(".")
                or entry.startswith("~") or entry_low.endswith((".tmp", ".tmp_heal", ".part", ".partial", ".crdownload"))):
            continue

        full_path = os.path.join(dir_path, entry)
        is_dir = os.path.isdir(full_path)
        base_name, ext = os.path.splitext(entry)
        ext_lower = ext.lower()

        # Format display name
        if ext_lower in [".lnk", ".url"]:
            display_name = base_name
        else:
            display_name = entry

        items.append({
            "name": display_name,
            "path": full_path,
            "args": "",
            "is_dir": is_dir,
            "ext": ext_lower
        })

    # Sort items: shortcuts & apps first, then alphabetically
    def sort_key(it):
        p = it["path"].lower()
        if p.endswith(".lnk") or p.endswith(".exe"):
            return (0, it["name"].lower())
        if it.get("is_dir"):
            return (2, it["name"].lower())
        return (1, it["name"].lower())

    items.sort(key=sort_key)
    return items


def normalize_fs_path(path: str) -> str:
    """Normalizes path by resolving short 8.3 names and standardizing case."""
    if not path:
        return ""
    try:
        real = os.path.realpath(path)
        return os.path.normcase(os.path.abspath(real))
    except Exception:
        return os.path.normcase(os.path.abspath(path))


def resolve_shortcut_target(path: str) -> str:
    """Safely extracts target path from Windows .lnk or URL from .url, including arguments to avoid proxy/wrapper collisions."""
    if not path or not os.path.exists(path):
        return ""
    lower = path.lower()
    if lower.endswith(".lnk"):
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            sc = shell.CreateShortcut(path)
            t = sc.TargetPath
            args = sc.Arguments.strip() if sc.Arguments else ""
            if t:
                norm_t = os.path.normpath(t).lower()
                if args:
                    return f"{norm_t} {args.lower()}"
                return norm_t
        except Exception:
            pass
    elif lower.endswith(".url"):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().lower().startswith("url="):
                        return line.strip()[4:].strip().lower()
        except Exception:
            pass
    return os.path.normcase(os.path.abspath(path)).lower()


def clean_shortcut_name(base_name: str) -> str:
    """Strips ' (1)', ' (2)', ' - 快捷方式', etc. for deduplication comparison."""
    name, ext = os.path.splitext(base_name)
    name = re.sub(r"\s*\(\d+\)$", "", name)
    name = re.sub(r"\s*-\s*(快捷方式|shortcut)$", "", name, flags=re.IGNORECASE)
    return name.strip().lower() + ext.lower()


_ASCII_SHORT_KW = re.compile(r"^[a-z0-9]{1,3}$")


def _has_kw(name_l: str, keywords) -> bool:
    """短 ASCII 词要求词边界，避免 tim/qq/git/gal 误伤 Ultimate / Galaxy / Digital。"""
    for k in keywords:
        if _ASCII_SHORT_KW.match(k):
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", name_l):
                return True
        elif k in name_l:
            return True
    return False


def classify_desktop_item(file_path: str, available_categories: Optional[List[str]] = None) -> str:
    """
    Intelligently categorizes desktop files, shortcuts and applications into
    distinct, high-clarity thematic categories:
    - 常用应用 (Core web browsers, primary entrypoints)
    - 社交沟通 (WeChat, QQ, TIM, Oopz, 钉钉, 飞书...)
    - 工作办公 (Office docs, Obsidian, PDFgear, AI assistants like 豆包, Kimi, Gemini, DeepSeek...)
    - 游戏 / 游戏娱乐 (Steam, WeGame, 赛博朋克, 异环, 影之刃, CS2, PUBG, 5E对战平台, 完美世界, 模拟器...)
    - 影音娱乐 (PotPlayer, QQ音乐, PiliPlus, 唧唧Down, TikTok, Instagram, Wallpaper Engine壁纸引擎...)
    - 开发设计 (Cursor, Antigravity IDE, TraeWork, Qoder, Termius, Blender, RPG Maker, 画梦 AI...)
    - 系统工具 (Everything, 火绒, Bandizip, 网盘类, 外设中心驱动, 任务管理器, 控制面板, 计算器, 记事本...)
    - 加速 (Dedicated accelerator category when present: 外星仔, 迅游, Watt Toolkit, Clash Verge, FlClash...)
    """
    name_with_ext = os.path.basename(file_path)
    name_l, ext_l = os.path.splitext(name_with_ext.lower())

    # Dynamically match game category name ('游戏' vs '游戏娱乐') based on environment context
    game_cat = "游戏" if (available_categories and "游戏" in available_categories and "游戏娱乐" not in available_categories) else "游戏娱乐"

    # 1. Documents, Office & Backup Archives
    doc_exts = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".txt", ".md", ".csv", ".rtf"}
    if ext_l in doc_exts or _has_kw(name_l, ["policy-backup", "org-policy", "backup"]):
        return "工作办公"

    # 2. Network Accelerators & Proxies (Checked early so accelerators are never swallowed by games or office)
    accelerator_keywords = [
        "外星仔", "迅游", "uu加速", "雷神加速", "薄荷加速", "奇游", "加速器",
        "watt toolkit", "steam++", "clash", "flclash", "v2ray", "sing-box", "hysteria", "shadowsocks", "vpn", "proxy", "proxifier"
    ]
    if _has_kw(name_l, accelerator_keywords):
        if available_categories and ("加速" in available_categories or "网络加速" in available_categories):
            return "加速" if "加速" in available_categories else "网络加速"
        return "系统工具"

    # 3. Steam Game URL & Steam Protocol Detection (Precision Steam game identification)
    if ext_l == ".url" and os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "steam://rungameid/" in content.lower():
                m = re.search(r'steam://rungameid/(\d+)', content, re.IGNORECASE)
                if m:
                    appid = m.group(1)
                    if appid == "431960":
                        return "影音娱乐"  # Wallpaper Engine
                    elif appid in ("365670", "1096900"):
                        return "开发设计"  # Blender, RPG Maker
                    else:
                        return game_cat  # All other Steam IDs are games (Spirit City, 放松时光, CS2, PUBG, etc.)
        except Exception:
            pass

    # 4. Raw Media Files
    media_exts = {".mp4", ".mkv", ".avi", ".flv", ".mov", ".mp3", ".flac", ".wav", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
    if ext_l in media_exts:
        return "影音娱乐"

    # 5. Social & Chat
    social_keywords = [
        "微信", "wechat", "qq", "tim", "oopz", "钉钉", "dingtalk", "飞书", "feishu",
        "telegram", "discord", "skype", "旺旺", "千牛", "yy语音", "yy.exe"
    ]
    if _has_kw(name_l, social_keywords):
        exclusions = ["qq音乐", "qqplayer", "音乐", "播放", "浏览器",
                      "飞车", "炫舞", "qq游戏", "开发者", "sdk"]
        if not any(ex in name_l for ex in exclusions):
            return "社交沟通"

    # 6. Development, IDEs & Creative Design (Evaluated before generic matches to protect 3D/dev tools)
    dev_keywords = [
        "cursor", "antigravity", "traework", "trae", "qoder", "termius", "code", "vscode",
        "visual studio", "pycharm", "intellij", "idea", "webstorm", "android studio", "clion",
        "finalshell", "xshell", "mobaxterm", "git", "github", "docker", "postman", "dbeaver",
        "navicat", "blender", "photoshop", "illustrator", "premiere", "after effects", "c4d",
        "cinema 4d", "figma", "pixso", "sketch", "画梦", "出图", "rpg maker",
        "cockpit", "codebuddy", "opencode", "workbuddy", "zcode",
        "开发者", "sdk", "编译", "集成开发"
    ]
    if _has_kw(name_l, dev_keywords):
        return "开发设计"

    # 7. Media, Video, Music & Desktop Wallpaper
    media_keywords = [
        "potplayer", "vlc", "player", "播放器", "qq音乐", "网易云音乐", "music", "音乐",
        "酷狗", "酷我", "汽水音乐", "spotify", "foobar", "bilibili", "哔哩哔哩", "piliplus",
        "唧唧down", "爱奇艺", "腾讯视频", "优酷", "芒果tv", "抖音", "tiktok", "instagram",
        "wallpaper engine", "壁纸", "live wallpaper"
    ]
    if _has_kw(name_l, media_keywords):
        return "影音娱乐"

    # 8. Gaming, Platforms & Emulators (Cleaned: strictly gaming platforms and games; no accelerators, dev tools or wallpapers)
    game_keywords = [
        "game", "steam", "wegame", "epic", "battle.net", "战网", "5e", "对战", "对战平台",
        "竞技平台", "模拟器", "mumu", "雷电", "夜神", "逍遥", "小黑盒", "bettergi",
        "原神", "genshin", "崩坏", "honkai", "starrail", "星穹铁道", "鸣潮", "wuthering",
        "绝区零", "zzz", "异环", "影之刃", "赛博朋克", "cyberpunk", "2077", "cs2", "counter-strike",
        "pubg", "battlegrounds", "永劫无间", "naraka", "lol", "英雄联盟", "league of legends",
        "瓦罗兰特", "valorant", "无畏契约", "dota", "minecraft", "我的世界", "地平线", "forza",
        "黑神话", "wukong", "gta", "galgame", "米哈游", "mihoyo", "自在星球", "拍案",
        "spirit city", "lofi sessions", "放松时光", "lo-fi生活", "gal", "wannabe galgame",
        "飞车", "炫舞", "qq游戏"
    ]
    if _has_kw(name_l, game_keywords):
        return game_cat

    # 9. Office, Knowledge & AI Assistants
    office_keywords = [
        "docs", "sheets", "slides", "drive", "obsidian", "notion", "语雀", "xmind",
        "思维导图", "有道云笔记", "印象笔记", "pdfgear", "word", "excel", "powerpoint", "wps",
        "office", "outlook", "foxmail", "邮箱", "腾讯会议", "meeting", "自考", "学习工作台",
        "豆包", "kimi", "gemini", "deepseek", "chatgpt", "claude", "cherry studio", "copilot"
    ]
    if _has_kw(name_l, office_keywords):
        return "工作办公"

    # 10. System Tools, Hardware, Peripherals & Cloud Drives (Cleaned: contains cloud drives, peripheral hubs, system applets)
    tool_keywords = [
        "cmd", "terminal", "powershell", "任务管理器", "控制面板", "此电脑", "计算器", "记事本",
        "everything", "火绒", "bandizip", "7-zip", "winrar", "hibit", "geek", "dism++",
        "ccleaner", "杀毒", "安全软件", "akko", "mchose", "hecate", "razer", "synapse", "雷蛇", "罗技",
        "logitech", "g hub", "达尔优", "corsair", "icue", "驱动", "外设", "hub", "sound card",
        "nvidia", "fancontrol", "soundswitch", "rammap", "图吧工具箱", "aida64", "cpu-z", "gpu-z",
        "百度网盘", "阿里云盘", "123云盘", "夸克网盘", "天翼云盘", "网盘", "迅雷", "idm", "download manager",
        "localsend", "uu远程", "phone mirror", "winhance", "context menu", "piepie", "picview", "tiez",
        # 旧版桌面工具的遗留名称，仅用于把它们的快捷方式归到「系统工具」
        "filebutler", "mydockfinder", "deskbox"
    ]
    if _has_kw(name_l, tool_keywords):
        return "系统工具"

    # 11. Web Browsers & Core Default
    browser_keywords = ["chrome", "edge", "firefox", "360", "夸克", "brave", "opera", "browser", "浏览器", "aurin", "fluxion", "kazumi", "mihome", "小米"]
    if _has_kw(name_l, browser_keywords):
        return "常用应用"

    return "常用应用"


class ManagedStorageEngine(QObject):
    """
    Physical directory mapping and asset manager.
    Syncs physical folders with BigFolder categories in real-time,
    handles physical drag-and-drop moves, and provides one-click desktop tidy.
    """
    storage_synced = Signal(str, list)          # folder_name, items
    storage_folders_imported = Signal(list)    # list of newly imported folder names
    file_moved = Signal(str, str)               # src_path, dst_path
    operation_message = Signal(str, bool)      # message, is_error

    def __init__(
        self,
        config_manager: ConfigManager,
        root_path: Optional[str] = None,
        parent: Optional[QObject] = None
    ):
        super().__init__(parent)
        self.config_manager = config_manager

        # Load or detect storage root
        if root_path and os.path.exists(root_path):
            self.root_path = os.path.abspath(root_path)
            self.config_manager.set("managed_storage_root", self.root_path, auto_save=True)
        else:
            configured_root = self.config_manager.get("managed_storage_root", "")
            if configured_root and os.path.exists(configured_root):
                self.root_path = os.path.abspath(configured_root)
            else:
                self.root_path = os.path.abspath(default_managed_storage_root())
                self.config_manager.set("managed_storage_root", self.root_path, auto_save=True)

        # File system watcher
        self.fs_watcher = QFileSystemWatcher(self)
        self.fs_watcher.directoryChanged.connect(self._on_directory_changed)

        # Debounce timer to avoid multiple syncs on burst file moves (50-100ms window)
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(80)
        self.debounce_timer.timeout.connect(self._on_debounce_sync)

        self._pending_sync_folders: set = set()
        self._pending_root_changed: bool = False

        # Auto-import Stacky storage folders if present and initialize watchers
        self.import_existing_storage_folders()
        self.refresh_watched_directories()

    def stop(self):
        """Stops the debounce timer and unhooks all watched directories."""
        self.debounce_timer.stop()
        self._pending_sync_folders.clear()
        self._pending_root_changed = False
        for d in list(self.fs_watcher.directories()):
            self._unwatch_directory(d)

    def get_root_path(self) -> str:
        return self.root_path

    def set_root_path(self, new_root: str):
        if not os.path.exists(new_root):
            try:
                os.makedirs(new_root, exist_ok=True)
            except Exception as e:
                self.operation_message.emit(f"创建托管存储目录失败: {e}", True)
                return

        # Unwatch existing directories
        for d in list(self.fs_watcher.directories()):
            self._unwatch_directory(d)

        self.root_path = os.path.abspath(new_root)
        self.config_manager.set("managed_storage_root", self.root_path, auto_save=True)
        self.import_existing_storage_folders()
        self.refresh_watched_directories()
        self.operation_message.emit(f"托管存储目录已设置为: {self.root_path}", False)

    def get_folder_storage_path(self, folder_name: str) -> str:
        """Returns the physical directory path for a given category name."""
        return os.path.join(self.root_path, folder_name)

    def ensure_folder_storage_dir(self, folder_name: str) -> str:
        """Ensures physical subdirectory exists and starts watching it."""
        folder_dir = self.get_folder_storage_path(folder_name)
        if not os.path.exists(folder_dir):
            try:
                os.makedirs(folder_dir, exist_ok=True)
            except Exception as e:
                print(f"Failed to create folder storage dir {folder_dir}:", e)
        self._watch_directory(folder_dir)
        return folder_dir

    def resolve_category_name(self, category: str) -> str:
        """
        Resolves a canonical category name against existing physical directories and config folders.
        For example: maps '游戏娱乐' to '游戏' if '游戏' exists on disk or in config,
        or maps '加速' if present.
        """
        if not category:
            return "常用应用"

        existing_config_names = [f.get("name") for f in self.config_manager.get_folders() if f.get("name")]
        existing_disk_names = []
        if self.root_path and os.path.exists(self.root_path):
            try:
                existing_disk_names = [d for d in os.listdir(self.root_path) if os.path.isdir(os.path.join(self.root_path, d))]
            except Exception:
                pass

        all_existing = set(existing_config_names + existing_disk_names)

        if category in all_existing:
            return category

        if category in ("游戏", "游戏娱乐"):
            if "游戏" in all_existing and "游戏娱乐" not in all_existing:
                return "游戏"
            if "游戏娱乐" in all_existing:
                return "游戏娱乐"
            return category

        if category in ("加速", "网络加速", "加速器"):
            if "加速" in all_existing:
                return "加速"
            if "网络加速" in all_existing:
                return "网络加速"
            if category in ("加速", "网络加速"):
                return category
            if "系统工具" in all_existing:
                return "系统工具"

        return category

    def refresh_watched_directories(self):
        """
        Ensures managed storage root and all active mapped subdirectories are actively watched.
        Cleans up dead watched paths that no longer exist.
        """
        if not self.root_path or not os.path.exists(self.root_path):
            return

        # Watch root directory itself
        self._watch_directory(self.root_path)

        # Watch all active mapped folder directories from config
        for f in self.config_manager.get_folders():
            if f.get("storage_mapped"):
                sub_dir = self.get_folder_storage_path(f.get("name", ""))
                if os.path.exists(sub_dir) and os.path.isdir(sub_dir):
                    self._watch_directory(sub_dir)

        # Watch all physical subdirectories under root
        try:
            for entry in os.listdir(self.root_path):
                full_p = os.path.join(self.root_path, entry)
                if os.path.isdir(full_p) and not entry.startswith("."):
                    self._watch_directory(full_p)
        except Exception:
            pass

        # Cleanup dead paths from watcher
        try:
            for watched in list(self.fs_watcher.directories()):
                if not os.path.exists(watched):
                    self._unwatch_directory(watched)
        except Exception:
            pass

    def _watch_directory(self, dir_path: str):
        if not dir_path or not os.path.exists(dir_path) or not os.path.isdir(dir_path):
            return
        abs_p = os.path.abspath(dir_path)
        norm_target = normalize_fs_path(abs_p)
        existing_norm = [normalize_fs_path(p) for p in self.fs_watcher.directories()]
        if norm_target not in existing_norm:
            try:
                self.fs_watcher.addPath(abs_p)
            except Exception as e:
                print(f"Failed to watch directory {abs_p}:", e)

    def _unwatch_directory(self, dir_path: str):
        if not dir_path:
            return
        norm_target = normalize_fs_path(dir_path)
        for p in list(self.fs_watcher.directories()):
            if normalize_fs_path(p) == norm_target:
                try:
                    self.fs_watcher.removePath(p)
                except Exception:
                    pass

    def import_existing_storage_folders(self) -> List[str]:
        """
        Scans root_path for existing physical subdirectories (e.g. Stacky subfolders
        like '我的桌面', '游戏', '常用应用'), adding them as BigFolder categories if not present.
        """
        if not self.root_path or not os.path.exists(self.root_path) or not os.path.isdir(self.root_path):
            return []

        imported = []
        existing_folders_map = {
            f.get("name", "").lower(): f.get("name", "")
            for f in self.config_manager.get_folders()
        }
        ignored_exact = {"desktop.ini", "thumbs.db", ".ds_store", ".git", "$recycle.bin", "system volume information"}

        try:
            subdirs = [
                d for d in os.listdir(self.root_path)
                if os.path.isdir(os.path.join(self.root_path, d))
                and not d.startswith(".")
                and d.lower() not in ignored_exact
            ]
        except Exception:
            return []

        orig_active = self.config_manager.get_active_folder_index()
        for sub in subdirs:
            if sub.lower() not in existing_folders_map:
                # Add category without hijacking active folder, mark as storage_mapped
                idx = self.config_manager.add_folder(sub, set_active=False)
                folders = self.config_manager.get_folders()
                folders[idx]["storage_mapped"] = True
                self.config_manager.save()
                self.sync_folder_from_storage(sub, force=True)
                imported.append(sub)
                existing_folders_map[sub.lower()] = sub
            else:
                real_name = existing_folders_map[sub.lower()]
                sub_dir = self.get_folder_storage_path(real_name)
                self._watch_directory(sub_dir)
                # If directory exists and folder is mapped but empty, sync to restore items
                if self.is_folder_storage_mapped(real_name):
                    folders = self.config_manager.get_folders()
                    for f in folders:
                        if f.get("name") == real_name and not f.get("items"):
                            self.sync_folder_from_storage(real_name, force=True)
                            break

        if self.config_manager.get_active_folder_index() != orig_active:
            self.config_manager.set_active_folder_index(orig_active)
        if imported:
            self.storage_folders_imported.emit(imported)
        return imported

    def is_folder_storage_mapped(self, folder_name: str) -> bool:
        folders = self.config_manager.get_folders()
        for f in folders:
            if f.get("name") == folder_name:
                return bool(f.get("storage_mapped", False))
        return False

    def set_folder_storage_mapped(self, folder_name: str, mapped: bool = True):
        folders = self.config_manager.get_folders()
        for idx, f in enumerate(folders):
            if f.get("name") == folder_name:
                folders[idx]["storage_mapped"] = mapped
                self.config_manager.save()
                if mapped:
                    self.ensure_folder_storage_dir(folder_name)
                    self.sync_folder_from_storage(folder_name, force=True)
                else:
                    self._unwatch_directory(self.get_folder_storage_path(folder_name))
                break

    @staticmethod
    def _is_virtual_bookmark_item(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("type") == "web":
            return True
        p = str(item.get("path") or "").strip().lower()
        return p.startswith(("http://", "https://")) and not p.endswith(".url")

    def sync_folder_from_storage(self, folder_name: str, force: bool = False) -> List[Dict[str, Any]]:
        """
        Scans physical directory and updates the items in ConfigManager.
        Preserves custom metadata (subgroup, custom_icon, args, type, custom name),
        virtual web bookmarks, and manual drag-and-drop item ordering across syncs.
        Emits storage_synced signal.
        """
        if not force and not self.is_folder_storage_mapped(folder_name):
            return []

        target_f = next(
            (f for f in self.config_manager.get_folders() if f.get("name", "").lower() == folder_name.lower()),
            None,
        )
        was_already_mapped = bool(target_f and target_f.get("storage_mapped"))

        self._pending_sync_folders = {
            fn for fn in self._pending_sync_folders if fn.lower() != folder_name.lower()
        }
        folder_dir = self.get_folder_storage_path(folder_name)
        if not os.path.exists(folder_dir):
            scanned_items = []
        else:
            self._watch_directory(folder_dir)
            scanned_items = scan_directory_items(folder_dir)

            # Auto-seed protection on initial folder mapping: if a newly mapped directory is empty,
            # seed from existing unmapped items (or defaults for 常用应用) instead of wiping to zero.
            if not scanned_items and not was_already_mapped:
                existing_seed = target_f.get("items", []) if target_f else []
                if not existing_seed and folder_name == "常用应用":
                    try:
                        from config_manager import get_default_items
                        existing_seed = get_default_items()
                    except Exception:
                        existing_seed = []

                if existing_seed:
                    for it in existing_seed:
                        if self._is_virtual_bookmark_item(it):
                            continue
                        p = it.get("path")
                        if p and os.path.exists(p):
                            try:
                                base = os.path.basename(p)
                                name_no_ext, ext = os.path.splitext(base)
                                if ext.lower() in [".lnk", ".url"]:
                                    dst = os.path.join(folder_dir, base)
                                    if not os.path.exists(dst):
                                        shutil.copy2(p, dst)
                                else:
                                    dst = os.path.join(folder_dir, f"{it.get('name', name_no_ext)}.lnk")
                                    if not os.path.exists(dst):
                                        create_shortcut_file(target_path=p, output_lnk_path=dst)
                            except Exception:
                                pass
                    scanned_items = scan_directory_items(folder_dir)

        merged_items = list(scanned_items)
        folders = self.config_manager.get_folders()
        for idx, f in enumerate(folders):
            if f.get("name", "").lower() == folder_name.lower():
                scanned_by_norm: Dict[str, int] = {}
                scanned_by_base: Dict[str, List[int]] = {}
                for s_idx, s_it in enumerate(scanned_items):
                    p_norm = normalize_fs_path(s_it["path"])
                    scanned_by_norm[p_norm] = s_idx
                    b_norm = os.path.basename(p_norm)
                    if b_norm:
                        scanned_by_base.setdefault(b_norm, []).append(s_idx)

                consumed_indices = set()
                ordered_merged: List[Dict[str, Any]] = []

                for old_it in f.get("items", []):
                    if not isinstance(old_it, dict):
                        continue
                    if self._is_virtual_bookmark_item(old_it):
                        ordered_merged.append(dict(old_it))
                        continue

                    old_path = old_it.get("path", "")
                    if not old_path:
                        continue
                    old_norm = normalize_fs_path(old_path)
                    matched_s_idx = None
                    exact_path_match = False

                    cand_idx = scanned_by_norm.get(old_norm)
                    if cand_idx is not None and cand_idx not in consumed_indices:
                        matched_s_idx = cand_idx
                        exact_path_match = True
                    elif not os.path.exists(old_path):
                        b_old = os.path.basename(old_norm)
                        for b_idx in scanned_by_base.get(b_old, []):
                            if b_idx not in consumed_indices:
                                matched_s_idx = b_idx
                                break

                    if matched_s_idx is not None:
                        consumed_indices.add(matched_s_idx)
                        new_it = dict(scanned_items[matched_s_idx])
                        for meta_key in ("custom_icon", "args", "subgroup", "type"):
                            if old_it.get(meta_key):
                                new_it[meta_key] = old_it[meta_key]
                        if exact_path_match and old_it.get("name"):
                            new_it["name"] = old_it["name"]
                        elif not exact_path_match and old_it.get("name"):
                            new_it["name"] = old_it["name"]
                        ordered_merged.append(new_it)

                for s_idx, s_it in enumerate(scanned_items):
                    if s_idx not in consumed_indices:
                        ordered_merged.append(dict(s_it))

                merged_items = ordered_merged
                folders[idx]["name"] = folder_name
                folders[idx]["items"] = merged_items
                folders[idx]["storage_mapped"] = True
                self.config_manager.save()
                break

        self.storage_synced.emit(folder_name, merged_items)
        return merged_items

    def rename_storage_item(self, item_path: str, new_name: str) -> Optional[str]:
        """
        Renames a file or shortcut located inside managed storage physically.
        Preserves original extension and user metadata (custom_icon, args).
        Returns the new path if renamed, or None if skipped/failed.
        """
        if not item_path or not os.path.exists(item_path):
            return None
        if not self.is_in_managed_storage(item_path, allow_root=False) or is_protected_system_path(item_path):
            return None

        clean_name = new_name.strip()
        if not is_valid_fs_name(clean_name):
            self.operation_message.emit("文件名包含非法字符", True)
            return None

        dir_name = os.path.dirname(item_path)
        base, ext = os.path.splitext(os.path.basename(item_path))
        target_name = f"{clean_name}{ext}"
        new_path = os.path.join(dir_name, target_name)

        if os.path.exists(new_path) and normalize_fs_path(new_path) != normalize_fs_path(item_path):
            self.operation_message.emit(f"目标文件已存在: {target_name}", True)
            return None

        try:
            os.rename(item_path, new_path)
            notify_shell_change(dir_name)
            self.file_moved.emit(item_path, new_path)

            # Update item in config_manager first so metadata carries over smoothly
            folders = self.config_manager.get_folders()
            norm_old = normalize_fs_path(item_path)
            for f in folders:
                for it in f.get("items", []):
                    if it.get("path") and normalize_fs_path(it["path"]) == norm_old:
                        it["path"] = new_path
                        it["name"] = clean_name
                        break
            self.config_manager.save()

            parent_folder_name = os.path.basename(dir_name)
            self.sync_folder_from_storage(parent_folder_name, force=True)
            self.operation_message.emit(f"已重命名为: {clean_name}", False)
            return new_path
        except Exception as e:
            self.operation_message.emit(f"重命名文件失败: {e}", True)
            return None

    def rename_folder_storage(self, old_name: str, new_name: str) -> bool:
        """
        Renames physical directory when a mapped category is renamed.
        Updates ConfigManager and syncs items while preserving custom metadata.
        """
        if not old_name or not new_name or old_name.strip() == new_name.strip():
            return False
        clean_name = new_name.strip()
        if not is_valid_fs_name(clean_name):
            self.operation_message.emit("分类名称包含非法字符", True)
            return False

        old_dir = self.get_folder_storage_path(old_name)
        new_dir = self.get_folder_storage_path(clean_name)

        if os.path.exists(new_dir) and normalize_fs_path(new_dir) != normalize_fs_path(old_dir):
            self.operation_message.emit(f"目标分类目录已存在: {clean_name}", True)
            return False

        try:
            if os.path.exists(old_dir):
                self._unwatch_directory(old_dir)
                os.rename(old_dir, new_dir)
                self._watch_directory(new_dir)
            else:
                os.makedirs(new_dir, exist_ok=True)
                self._watch_directory(new_dir)

            notify_shell_change(new_dir)

            # Rename folder in config_manager so sync finds it under clean_name
            folders = self.config_manager.get_folders()
            for idx, f in enumerate(folders):
                if f.get("name", "").lower() == old_name.lower():
                    self.config_manager.rename_folder(idx, clean_name)
                    break

            self.sync_folder_from_storage(clean_name, force=True)
            return True
        except Exception as e:
            print(f"Failed to rename physical storage folder {old_name} -> {clean_name}:", e)
            self.operation_message.emit(f"重命名分类目录失败: {e}", True)
            return False

    def is_in_managed_storage(self, path: str, allow_root: bool = False) -> bool:
        """Checks if a given path is located inside managed storage root."""
        if not self.root_path or not path:
            return False
        try:
            norm_path = normalize_fs_path(path)
            norm_root = normalize_fs_path(self.root_path)
            if not norm_root or not norm_path:
                return False
            if os.path.splitdrive(norm_path)[0] != os.path.splitdrive(norm_root)[0]:
                return False
            if not allow_root and norm_path == norm_root:
                return False
            common = os.path.commonpath([norm_root, norm_path])
            return common == norm_root
        except Exception:
            return False

    def _on_directory_changed(self, path: str):
        if not path:
            return
        norm_path = normalize_fs_path(path)
        norm_root = normalize_fs_path(self.root_path) if self.root_path else ""

        if norm_root and norm_path == norm_root:
            self._pending_root_changed = True
        else:
            matched_folder = None
            for f in self.config_manager.get_folders():
                f_name = f.get("name", "")
                f_dir = self.get_folder_storage_path(f_name)
                if normalize_fs_path(f_dir) == norm_path:
                    matched_folder = f_name
                    break

            if matched_folder:
                self._pending_sync_folders.add(matched_folder)
            else:
                self._pending_root_changed = True

        self.debounce_timer.start(80)

    def _on_debounce_sync(self):
        if self._pending_root_changed:
            self._pending_root_changed = False
            # import_existing_storage_folders handles auto-import and emits storage_folders_imported
            self.import_existing_storage_folders()
            self.refresh_watched_directories()

            for f in self.config_manager.get_folders():
                if f.get("storage_mapped"):
                    f_name = f.get("name", "")
                    f_dir = self.get_folder_storage_path(f_name)
                    # Sync if directory disappeared and it previously had items
                    if not os.path.exists(f_dir) and f.get("items"):
                        self._pending_sync_folders.add(f_name)
                    # Or if directory reappeared and it previously was empty
                    elif os.path.exists(f_dir) and not f.get("items"):
                        self._pending_sync_folders.add(f_name)

        folders_to_sync = list(self._pending_sync_folders)
        self._pending_sync_folders.clear()

        for folder_name in folders_to_sync:
            self.sync_folder_from_storage(folder_name, force=True)

    def add_or_copy_item_to_storage(self, file_path: str, target_folder_name: str) -> Optional[str]:
        """
        Adds a file or application to managed storage category:
        - If file is already inside target_dir, returns it directly.
        - If file is a .lnk or .url, copies it into target_dir.
        - If file is an executable or document, creates a .lnk shortcut in target_dir.
        """
        if not file_path or not os.path.exists(file_path):
            return None
        target_dir = self.ensure_folder_storage_dir(target_folder_name)
        if not os.path.exists(target_dir):
            return None

        norm_file = normalize_fs_path(file_path)
        norm_target_dir = normalize_fs_path(target_dir)

        if os.path.dirname(norm_file) == norm_target_dir:
            self.sync_folder_from_storage(target_folder_name, force=True)
            return file_path

        base_name = os.path.basename(file_path)
        name_no_ext, ext = os.path.splitext(base_name)
        ext_lower = ext.lower()

        try:
            if ext_lower in [".lnk", ".url"]:
                dest = os.path.join(target_dir, base_name)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{name_no_ext} ({counter}){ext}")
                    counter += 1
                shutil.copy2(file_path, dest)
            else:
                dest = os.path.join(target_dir, f"{name_no_ext}.lnk")
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{name_no_ext} ({counter}).lnk")
                    counter += 1
                success = create_shortcut_file(target_path=file_path, output_lnk_path=dest)
                if not success:
                    dest = os.path.join(target_dir, base_name)
                    shutil.copy2(file_path, dest)

            self.sync_folder_from_storage(target_folder_name, force=True)
            notify_shell_change(target_dir)
            return dest
        except Exception as e:
            self.operation_message.emit(f"添加项目到托管目录失败: {e}", True)
            return None

    def move_into_storage(self, source_paths: List[str], target_folder_name: str) -> List[str]:
        """
        Physically moves files/folders from Desktop or external Explorer into
        the target managed storage directory.
        Includes intelligent shortcut deduplication to prevent 'xxx (1).lnk' duplicates.
        """
        target_dir = self.ensure_folder_storage_dir(target_folder_name)
        target_abs = os.path.abspath(target_dir)
        moved_files = []

        for src in source_paths:
            if not os.path.exists(src):
                continue

            src_abs = os.path.abspath(src)
            # Avoid moving directory into itself, its subdirectories, or sweeping Stacky self/container items
            if src_abs == target_abs or target_abs.startswith(src_abs + os.sep):
                continue
            if os.path.dirname(src_abs) == target_abs:
                continue

            base_name = os.path.basename(src_abs)
            if base_name.lower() in _SELF_AND_CONTAINER_EXCLUDES:
                continue
            name_part, ext_part = os.path.splitext(base_name)
            is_shortcut = ext_part.lower() in [".lnk", ".url"]

            # Shortcut duplicate avoidance: check if an equivalent shortcut is already in target_dir
            if is_shortcut:
                src_target = resolve_shortcut_target(src_abs)
                src_clean_name = clean_shortcut_name(base_name)

                already_exists = False
                for existing_file in os.listdir(target_dir):
                    existing_path = os.path.join(target_dir, existing_file)
                    if clean_shortcut_name(existing_file) == src_clean_name:
                        already_exists = True
                        break
                    if src_target and resolve_shortcut_target(existing_path) == src_target:
                        already_exists = True
                        break

                if already_exists:
                    # Redundant shortcut already exists in destination! Delete redundant desktop copy and skip
                    try:
                        os.remove(src_abs)
                    except Exception:
                        pass
                    continue

            dest = os.path.join(target_dir, base_name)

            # Handle destination name collision
            if os.path.exists(dest):
                if is_shortcut:
                    # Destination file exists and is a shortcut, skip rather than create (1).lnk
                    continue
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(target_dir, f"{name_part} ({counter}){ext_part}")
                    counter += 1

            try:
                shutil.move(src_abs, dest)
                moved_files.append(dest)
                self.file_moved.emit(src_abs, dest)
            except Exception as e:
                self.operation_message.emit(f"移动文件失败 ({base_name}): {e}", True)

        if moved_files:
            self.set_folder_storage_mapped(target_folder_name, True)
            self.sync_folder_from_storage(target_folder_name, force=True)
            desktop_dir = get_desktop_dir()
            notify_shell_change(desktop_dir)
            notify_shell_change(target_dir)
            self.operation_message.emit(f"已物理收纳 {len(moved_files)} 个项目入【{target_folder_name}】", False)

        return moved_files

    def move_out_to_desktop(self, item_path: str) -> Optional[str]:
        """
        Physically moves a file from managed storage back to the Windows Desktop.
        """
        if not item_path or not os.path.exists(item_path):
            return None

        desktop_dir = get_desktop_dir()
        if not os.path.exists(desktop_dir):
            return None

        src_abs = os.path.abspath(item_path)
        desktop_abs = os.path.abspath(desktop_dir)
        if os.path.dirname(src_abs) == desktop_abs:
            return item_path

        base_name = os.path.basename(item_path)

        if not self.is_in_managed_storage(item_path, allow_root=False):
            # 非托管应用/程序：绝不丢失！安全在用户桌面上创建对应的快捷方式 (.lnk)
            try:
                from shortcut_utils import create_shortcut_file, refresh_windows_icon_cache
                raw_name = os.path.splitext(base_name)[0] if base_name.lower().endswith((".exe", ".lnk", ".url", ".bat", ".cmd")) else base_name
                dest_lnk = os.path.join(desktop_dir, f"{raw_name}.lnk")
                counter = 1
                while os.path.exists(dest_lnk):
                    dest_lnk = os.path.join(desktop_dir, f"{raw_name} ({counter}).lnk")
                    counter += 1

                icon_loc = f"{src_abs},0" if os.path.exists(src_abs) else ""
                created = create_shortcut_file(
                    target_path=src_abs,
                    output_lnk_path=dest_lnk,
                    icon_location=icon_loc,
                    description=f"{raw_name} 快捷方式"
                )
                if created and os.path.exists(dest_lnk):
                    notify_shell_change(desktop_dir)
                    refresh_windows_icon_cache()
                    self.operation_message.emit(f"已将【{raw_name}】安全移出至桌面", False)
                    return dest_lnk
                else:
                    self.operation_message.emit(f"创建桌面快捷方式失败: {raw_name}", True)
                    return None
            except Exception as e:
                self.operation_message.emit(f"移出至桌面异常: {e}", True)
                return None

        # Guard: refuse to move protected system files
        if is_protected_system_path(item_path):
            self.operation_message.emit(f"系统保护文件禁止移动: {os.path.basename(item_path)}", True)
            return None

        dest = os.path.join(desktop_dir, base_name)

        if os.path.exists(dest):
            name_part, ext_part = os.path.splitext(base_name)
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(desktop_dir, f"{name_part} ({counter}){ext_part}")
                counter += 1

        try:
            shutil.move(item_path, dest)
            self.file_moved.emit(item_path, dest)
            parent_folder_name = os.path.basename(os.path.dirname(item_path))
            self.sync_folder_from_storage(parent_folder_name, force=True)
            if dest.lower().endswith(".lnk"):
                try:
                    from icon_utils import heal_shortcut_icon
                    heal_shortcut_icon(dest, allow_target_fallback=True)
                except Exception:
                    pass
                try:
                    from shortcut_utils import refresh_windows_icon_cache
                    refresh_windows_icon_cache(force=True)
                except Exception:
                    pass
            notify_shell_change(desktop_dir)
            notify_shell_change(item_path)
            self.operation_message.emit(f"已将【{base_name}】放回桌面", False)
            return dest
        except Exception as e:
            self.operation_message.emit(f"移回桌面失败: {e}", True)
            return None

    def move_item_between_folders(self, item_path: str, src_folder: str, target_folder: str) -> Optional[str]:
        """
        Physically migrates a managed file from one category folder to another across distinct BigFolder instances.
        """
        if not item_path or not os.path.exists(item_path):
            return item_path

        if not self.is_in_managed_storage(item_path, allow_root=False):
            return item_path  # Non-managed file references are updated in config directly

        # ensure_folder_storage_dir 会建目录并注册监听；旧代码调的 get_category_dir 并不存在
        target_dir = self.ensure_folder_storage_dir(target_folder)
        if not os.path.isdir(target_dir):
            return item_path

        base_name = os.path.basename(item_path)
        dest = os.path.join(target_dir, base_name)
        if os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(item_path):
            name_part, ext_part = os.path.splitext(base_name)
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(target_dir, f"{name_part} ({counter}){ext_part}")
                counter += 1

        try:
            shutil.move(item_path, dest)
            self.file_moved.emit(item_path, dest)
            self.sync_folder_from_storage(src_folder, force=True)
            self.sync_folder_from_storage(target_folder, force=True)
            return dest
        except Exception as e:
            self.operation_message.emit(f"跨分类物理迁移失败: {e}", True)
            return item_path

    def dissolve_folder_to_desktop(self, folder_name: str) -> int:
        """
        Dissolves a managed folder and physically restores all contained files,
        shortcuts, and items back to the user's Windows Desktop in their authentic state.
        Returns the number of restored items.
        """
        desktop_dir = get_desktop_dir()
        if not os.path.exists(desktop_dir):
            return 0

        self._pending_sync_folders = {
            fn for fn in self._pending_sync_folders if fn.lower() != folder_name.lower()
        }
        folder_dir = self.get_folder_storage_path(folder_name)
        restored_count = 0

        # 1. Physically move all files from managed storage folder to Desktop
        if os.path.exists(folder_dir) and os.path.isdir(folder_dir):
            self._unwatch_directory(folder_dir)
            try:
                entries = list(os.listdir(folder_dir))
            except Exception:
                entries = []

            for entry in entries:
                src_file = os.path.join(folder_dir, entry)
                if not os.path.exists(src_file) or is_protected_system_path(src_file):
                    continue

                base_name = os.path.basename(src_file)
                dest = os.path.join(desktop_dir, base_name)
                if os.path.exists(dest):
                    name_part, ext_part = os.path.splitext(base_name)
                    counter = 1
                    while os.path.exists(dest):
                        dest = os.path.join(desktop_dir, f"{name_part} ({counter}){ext_part}")
                        counter += 1
                try:
                    shutil.move(src_file, dest)
                    restored_count += 1
                    # Auto-heal .lnk IconLocation to prevent Windows Explorer blank white paper icons
                    if dest.lower().endswith(".lnk"):
                        try:
                            from icon_utils import heal_shortcut_icon
                            heal_shortcut_icon(dest, allow_target_fallback=True)
                        except Exception:
                            pass
                    notify_shell_change(dest)
                    self.file_moved.emit(src_file, dest)
                except Exception as e:
                    print(f"Error moving {src_file} to desktop:", e)

            # Clean up the physical folder
            try:
                if os.path.exists(folder_dir) and not os.listdir(folder_dir):
                    os.rmdir(folder_dir)
            except Exception:
                pass

        # 2. Also check if any items in config_manager for this folder point outside folder_dir
        folders = self.config_manager.get_folders()
        target_f = next((f for f in folders if f.get("name", "").lower() == folder_name.lower()), None)
        if target_f:
            for it in target_f.get("items", []):
                p = it.get("path")
                if p and os.path.exists(p) and not self.is_in_managed_storage(p):
                    dest_lnk = os.path.join(desktop_dir, f"{it.get('name', os.path.basename(p))}.lnk")
                    if not os.path.exists(dest_lnk):
                        try:
                            create_shortcut_file(target_path=p, output_lnk_path=dest_lnk)
                            restored_count += 1
                        except Exception:
                            pass

        notify_shell_change(desktop_dir)
        notify_shell_change(folder_dir)
        # Force flush Windows Explorer icon cache for the entire desktop
        try:
            from shortcut_utils import refresh_windows_icon_cache
            refresh_windows_icon_cache(force=True)
        except Exception:
            pass
        self.operation_message.emit(f"已解散【{folder_name}】，{restored_count} 个应用已放回桌面", False)
        return restored_count

    def delete_item(self, item_path: str) -> bool:
        """
        Sends an item from managed storage safely to the Recycle Bin.
        Only items located inside managed storage can be deleted.
        """
        if not os.path.exists(item_path):
            return False

        if not self.is_in_managed_storage(item_path):
            self.operation_message.emit(f"非托管存储文件禁止物理删除: {os.path.basename(item_path)}", True)
            return False

        if is_protected_system_path(item_path):
            self.operation_message.emit(f"系统保护文件禁止删除: {os.path.basename(item_path)}", True)
            return False

        base_name = os.path.basename(item_path)
        parent_folder_name = os.path.basename(os.path.dirname(item_path))
        success = send_to_recycle_bin(item_path)

        if success:
            self.sync_folder_from_storage(parent_folder_name, force=True)
            notify_shell_change(os.path.dirname(item_path))
            self.operation_message.emit(f"已将【{base_name}】移入回收站", False)
        else:
            self.operation_message.emit(f"删除【{base_name}】失败", True)
        return success

    def tidy_desktop(
        self,
        target_folder_name: str = "常用应用",
        auto_categorize: bool = True
    ) -> Dict[str, Any]:
        """
        Desktop One-Click Tidy wizard:
        Scans Windows Desktop (user and common/public desktop) for scattered shortcuts,
        documents, and files, physically gathering them into managed Big Folder categories.
        Performs full deduplication and returns populated_categories.
        """
        desktop_dirs = []
        user_desk = get_desktop_dir()
        if os.path.exists(user_desk):
            desktop_dirs.append(user_desk)
        pub_desk = get_public_desktop_dir()
        if pub_desk and os.path.exists(pub_desk) and pub_desk != user_desk:
            desktop_dirs.append(pub_desk)

        if not desktop_dirs:
            return {"moved_count": 0, "moved_files": [], "populated_categories": []}

        # Items strictly excluded from tidying (system icons, Stacky self shortcuts, container folders)
        excluded_names = {
            "desktop.ini",
            "thumbs.db",
            ".ds_store",
            "recycle bin.lnk",
            "回收站.lnk",
            "此电脑.lnk",
            "网络.lnk",
            "控制面板.lnk",
            "deskbox.lnk",
        } | set(_SELF_AND_CONTAINER_EXCLUDES)
        # Incomplete / temporary suffixes to skip
        temp_suffixes = {".tmp", ".temp", ".tmp_heal", ".part", ".partial", ".crdownload", ".download"}

        candidates = []
        root_abs = os.path.abspath(self.root_path)
        norm_root_abs = normalize_fs_path(root_abs)
        norm_legacy_root = normalize_fs_path(os.path.join(os.path.expanduser("~"), "DeskBox"))

        for d in desktop_dirs:
            try:
                entries = os.listdir(d)
            except Exception:
                continue

            for entry in entries:
                entry_low = entry.lower()
                if entry_low in excluded_names or entry.startswith("~") or entry.startswith("."):
                    continue
                # Also skip backup dumps like policy-backup-*, org-policy-*, tmp*
                if entry_low.startswith(("policy-backup", "org-policy", "tmp")):
                    continue
                _, ext = os.path.splitext(entry)
                if ext.lower() in temp_suffixes:
                    continue
                full_path = os.path.join(d, entry)
                norm_full = normalize_fs_path(full_path)
                if norm_full in (norm_root_abs, norm_legacy_root):
                    continue
                if ext.lower() == ".lnk":
                    sc_target = resolve_shortcut_target(full_path)
                    if sc_target:
                        sc_base = sc_target.split(" ")[0]
                        norm_sc_base = normalize_fs_path(sc_base) if os.path.exists(sc_base) else sc_base
                        if norm_sc_base in (norm_root_abs, norm_legacy_root):
                            continue
                        if ("--activate" in sc_target or "--toggle" in sc_target) and (
                            "main.py" in sc_target or "bigfolderlauncher" in sc_target or "stacky" in sc_target
                        ):
                            continue
                candidates.append(full_path)

        # Deduplicate candidates across user desktop and public desktop
        unique_candidates = []
        seen_identities = set()
        for cand in candidates:
            bname = os.path.basename(cand)
            _, ext = os.path.splitext(bname)
            if ext.lower() in [".lnk", ".url"]:
                target_ident = resolve_shortcut_target(cand) or clean_shortcut_name(bname)
                if target_ident in seen_identities:
                    continue
                seen_identities.add(target_ident)
            unique_candidates.append(cand)

        moved_summary = []
        populated_categories = set()

        if not auto_categorize:
            moved_summary = self.move_into_storage(unique_candidates, target_folder_name)
            if moved_summary:
                populated_categories.add(target_folder_name)
        else:
            all_existing_cats = [d for d in os.listdir(root_abs) if os.path.isdir(os.path.join(root_abs, d))] if os.path.exists(root_abs) else []
            for cand in unique_candidates:
                cat_raw = classify_desktop_item(cand, available_categories=all_existing_cats)
                cat = self.resolve_category_name(cat_raw)
                moved = self.move_into_storage([cand], cat)
                if moved:
                    populated_categories.add(cat)
                moved_summary.extend(moved)

        # Include any existing non-empty categories in storage
        if os.path.exists(root_abs):
            for entry in os.listdir(root_abs):
                c_p = os.path.join(root_abs, entry)
                if os.path.isdir(c_p) and len(os.listdir(c_p)) > 0:
                    populated_categories.add(entry)

        for d in desktop_dirs:
            notify_shell_change(d)

        return {
            "scanned_count": len(candidates),
            "moved_count": len(moved_summary),
            "moved_files": moved_summary,
            "populated_categories": list(populated_categories)
        }

    def deduplicate_and_reorganize_storage(self) -> Dict[str, Any]:
        """
        Scans all categories in managed storage:
        1. Removes duplicate shortcuts (e.g. 'xxx (1).lnk' where 'xxx.lnk' exists, or identical targets).
        2. Redistributes misplaced items from '常用应用' into proper thematic categories.
        3. Restores 'Stacky 灵叠.lnk' back to desktop if present.
        4. Re-syncs all affected categories.
        """
        root_dir = self.get_root_path()
        if not os.path.exists(root_dir):
            return {"deduped_count": 0, "redistributed_count": 0, "populated_categories": []}

        deduped_count = 0
        redistributed_count = 0
        populated_cats = set()

        # Step 1: Deduplicate shortcuts in all category directories
        dup_pattern = re.compile(r"^(.*?)\s*\(\d+\)(\.[^.]+)$")
        for cat_dir_name in os.listdir(root_dir):
            cat_path = os.path.join(root_dir, cat_dir_name)
            if not os.path.isdir(cat_path):
                continue

            files = os.listdir(cat_path)
            # Check for (1).lnk duplicates
            for f in files:
                m = dup_pattern.match(f)
                if m:
                    clean_name = m.group(1) + m.group(2)
                    clean_path = os.path.join(cat_path, clean_name)
                    dup_path = os.path.join(cat_path, f)
                    if os.path.exists(clean_path):
                        try:
                            os.remove(dup_path)
                            deduped_count += 1
                        except Exception:
                            pass

            # Also check by target executable
            seen_targets = {}
            for f in os.listdir(cat_path):
                f_path = os.path.join(cat_path, f)
                if f.lower().endswith((".lnk", ".url")):
                    t = resolve_shortcut_target(f_path)
                    if t:
                        if t in seen_targets:
                            # Redundant duplicate of same target! Remove this one
                            try:
                                os.remove(f_path)
                                deduped_count += 1
                            except Exception:
                                pass
                        else:
                            seen_targets[t] = f_path

        # Step 2: Restore self-launcher shortcuts back to Desktop from any category if swept in
        desk_dir = get_desktop_dir()
        for cat_dir_name in os.listdir(root_dir):
            c_p = os.path.join(root_dir, cat_dir_name)
            if os.path.isdir(c_p):
                for self_lnk_name in ("Stacky 灵叠.lnk", "仿手机大文件夹.lnk", "仿手机大文件夹启动台.lnk", "DeskBox Files.lnk"):
                    swept_lnk = os.path.join(c_p, self_lnk_name)
                    if os.path.exists(swept_lnk):
                        try:
                            if self_lnk_name == "Stacky 灵叠.lnk" and desk_dir and os.path.exists(desk_dir):
                                desktop_p = os.path.join(desk_dir, "Stacky 灵叠.lnk")
                                if not os.path.exists(desktop_p):
                                    shutil.move(swept_lnk, desktop_p)
                                    continue
                            os.remove(swept_lnk)
                        except Exception:
                            pass

        # Step 3: Thoroughly scan ALL categories and redistribute misplaced items into proper thematic categories
        all_categories = [d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))]
        for cat_name in all_categories:
            cat_path = os.path.join(root_dir, cat_name)
            for f in list(os.listdir(cat_path)):
                src_f = os.path.join(cat_path, f)
                target_raw = classify_desktop_item(src_f, available_categories=all_categories)
                target_cat = self.resolve_category_name(target_raw)

                if target_cat != cat_name:
                    target_dir = self.ensure_folder_storage_dir(target_cat)
                    dst_f = os.path.join(target_dir, f)
                    if os.path.exists(dst_f):
                        try:
                            os.remove(src_f)
                            deduped_count += 1
                        except Exception:
                            pass
                    else:
                        try:
                            shutil.move(src_f, dst_f)
                            redistributed_count += 1
                            populated_cats.add(target_cat)
                            populated_cats.add(cat_name)
                        except Exception:
                            pass

        # Step 4: Sync all managed categories (both non-empty and emptied)
        for cat_name in os.listdir(root_dir):
            c_path = os.path.join(root_dir, cat_name)
            if os.path.isdir(c_path):
                items = os.listdir(c_path)
                if items:
                    populated_cats.add(cat_name)
                self.set_folder_storage_mapped(cat_name, True)
                self.sync_folder_from_storage(cat_name, force=True)

        return {
            "deduped_count": deduped_count,
            "redistributed_count": redistributed_count,
            "populated_categories": list(populated_cats)
        }


sys.modules.setdefault("storage_manager", sys.modules[__name__])
sys.modules.setdefault("stacky.core.storage_manager", sys.modules[__name__])
