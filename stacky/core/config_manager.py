from stacky.paths import ROOT
import os
import sys
import json
from typing import Dict, Any, List, Optional

def get_default_config_path() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # 用户级持久目录：卸载、重装、换安装路径都不会丢卡片与小组件位置
        appdata_dir = os.path.join(os.environ.get("APPDATA", exe_dir), "Stacky")
        os.makedirs(appdata_dir, exist_ok=True)
        user_cfg = os.path.join(appdata_dir, "config.json")
        if os.path.exists(user_cfg):
            return user_cfg
        # 首次升级：把旧的程序目录配置迁过来，现有布局无损
        import shutil
        for legacy in (
            os.path.join(exe_dir, "config.json"),
            os.path.join(exe_dir, "_internal", "config.json"),
            os.path.join(os.environ.get("APPDATA", exe_dir), "BigFolderLauncher", "config.json"),
        ):
            if os.path.exists(legacy):
                try:
                    shutil.copyfile(legacy, user_cfg)
                except Exception:
                    pass
                break
        return user_cfg
    return os.path.join(ROOT, "config.json")

CONFIG_FILE = get_default_config_path()

def get_default_items(include_desktop: bool = True) -> List[Dict[str, str]]:
    windir = os.environ.get("windir", r"C:\Windows")
    
    candidates = [
        {"name": "此电脑", "path": os.path.join(windir, "explorer.exe"), "args": ""},
        {"name": "记事本", "path": os.path.join(windir, "notepad.exe"), "args": ""},
        {"name": "计算器", "path": os.path.join(windir, "system32", "calc.exe"), "args": ""},
        {"name": "任务管理器", "path": os.path.join(windir, "system32", "Taskmgr.exe"), "args": ""},
        {"name": "控制面板", "path": os.path.join(windir, "system32", "control.exe"), "args": ""},
    ]

    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ]
    for p in edge_paths:
        if os.path.exists(p):
            candidates.insert(1, {"name": "Edge 浏览器", "path": p, "args": ""})
            break

    # 首次运行时可自动填充本机桌面快捷方式；生成分发配置时传 False，
    # 避免把打包者的桌面软件清单带进安装包。
    if include_desktop:
        desktop_dir = os.path.expandvars(r"%USERPROFILE%\Desktop")
        if os.path.exists(desktop_dir):
            for f in os.listdir(desktop_dir):
                if f.lower().endswith(".lnk") and not any(k in f.lower() for k in ["uninstall", "卸载"]):
                    name = os.path.splitext(f)[0]
                    candidates.append({"name": name, "path": os.path.join(desktop_dir, f), "args": ""})
                if len(candidates) >= 9:
                    break

    return candidates[:9]

DEFAULT_CONFIG: Dict[str, Any] = {
    "launcher_mode": "hybrid",    # "hybrid" (桌面常驻 + 快捷/MyDock呼出), "desktop" (仅桌面常驻), "summon" (仅呼出启动台)
    "summon_target": "card",      # "card" (桌面大文件夹直接浮现), "drawer" (全屏展开抽屉与全局搜索)
    "hotkey": "Alt+Space",        # 全局快捷键
    "theme": "dark",              # "dark" (星空深色磨砂), "light" (灵动浅色毛玻璃), "oled" (极夜透镜)
    "show_title": "always",       # "always" (居中显示在卡片正下方), "hover", "hide"
    "grid_rows": 3,
    "grid_cols": 3,
    "opacity": 0.88,
    "blur_enabled": True,
    "glass_blur_enabled": False,   # 真 Acrylic 背景模糊；默认关闭（避免 DWM 灰底回退），用户可在设置里开启
    "locked": False,
    "layer_mode": "normal",       # "normal" (稳定常驻可见), "bottom" (桌面底层), "top" (置顶)
    "open_app_mode": "double",    # "double" (防误触优先: 全双击打开 · 适合键鼠桌面), "hybrid" (双层模式: 外层单击/抽屉双击), "single" (全单击打开: 极速仿手机)
    "pos_x": 797,
    "pos_y": 49,
    "slot_size": 66,
    "icon_size": 48,
    "border_radius": 26,
    "active_folder_index": 0,
    "managed_storage_enabled": True,       # 是否启用真实物理目录映射
    "managed_storage_root": "",           # 物理根目录 (默认独立 Stacky 目录 %USERPROFILE%\\Stacky)
    "managed_drop_action": "move",        # "move" (物理移动桌面图标入库), "copy" (仅复制快捷方式)
    "desktop_pinned_enabled": True,       # 是否启用 Windows Shell 桌面底层常驻机制
    "everything_enabled": True,           # 是否启用 Everything 毫秒级全盘无感文件检索
    "app_display_mode": "classic",        # "classic" (图标 + 名称) / "large_icon" (仅图标)；仅这两种有实现
    "show_admin_badge": True,             # 是否显示管理员提权角标
    "show_running_badge": True,           # 是否显示运行中进程呼吸指示灯
    "post_launch_action": "close_drawer", # "close_drawer" (启动应用后收起抽屉), "keep_open" (保持展开)
    "weather_widget": {
        "enabled": True,
        "x": 614,
        "y": 180,
        "size": "2x2",
        "locked": False,
        "auto_location": False,  # 分发包默认不向第三方 IP 定位服务发请求，需用户主动开启
        "city": "成都",
        "region": "四川",
        "lat": 30.6586,
        "lon": 104.0648
    },
    "clock_widget": {
        "enabled": False,
        "x": 300,
        "y": 180,
        "size": "2x2",
        "locked": False
    },
    "monitor_widget": {
        "enabled": False,
        "x": 300,
        "y": 450,
        "size": "2x2",
        "locked": False
    },
    "memo_widget": {
        "enabled": False,
        "x": 300,
        "y": 720,
        "size": "2x2",
        "locked": False
    },
    "media_widget": {
        "enabled": True,
        "x": 300,
        "y": 450,
        "size": "2x2",
        "locked": False
    },
    "everything_widget": {
        "enabled": True,
        "x": 300,
        "y": 180,
        "size": "2x2",
        "locked": False
    },
    "file_shelf": {
        "enabled": True,
        "edge": "right",
        "auto_collapse": True,
        "pinned": False,
        "y_ratio": 0.45,
        "hide_in_fullscreen": True
    },
    "double_click_desktop_hide": True,
    "instances": [],
    "folders": [
        {
            "name": "常用应用",
            "items": []
        },
        {
            "name": "工作办公",
            "items": [
                {"name": "记事本", "path": r"C:\WINDOWS\notepad.exe", "args": ""},
                {"name": "计算器", "path": r"C:\WINDOWS\system32\calc.exe", "args": ""},
                {"name": "此电脑", "path": r"C:\WINDOWS\explorer.exe", "args": ""}
            ]
        },
        {
            "name": "系统工具",
            "items": [
                {"name": "任务管理器", "path": r"C:\WINDOWS\system32\Taskmgr.exe", "args": ""},
                {"name": "控制面板", "path": r"C:\WINDOWS\system32\control.exe", "args": ""}
            ]
        }
    ]
}

class ConfigManager:
    """
    Manages configuration for Big Folder Desktop Launcher.
    Supports multi-folder hierarchy, pagination, and backwards compatibility.
    """
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.data: Dict[str, Any] = self.load()

    def load(self) -> Dict[str, Any]:
        import copy
        data = copy.deepcopy(DEFAULT_CONFIG)
        user_has_instances = False
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                    user_has_instances = "instances" in user_data and isinstance(user_data["instances"], list) and len(user_data["instances"]) > 0
                    data.update(user_data)
            except Exception as e:
                print("Failed to read config.json, using defaults:", e)

        # Migration from legacy single-folder structure:
        # If user config has "items" or "folder_name" at root, migrate into "folders"
        legacy_items = data.get("items")
        legacy_folder_name = data.get("folder_name", "常用应用")

        if "folders" not in data or not isinstance(data["folders"], list) or len(data["folders"]) == 0:
            default_items = legacy_items if legacy_items else get_default_items()
            data["folders"] = [
                {"name": legacy_folder_name, "items": default_items},
                {"name": "工作办公", "items": []},
                {"name": "系统工具", "items": []}
            ]
        else:
            # If the first folder is empty and legacy_items is present, fill it
            if legacy_items and not data["folders"][0].get("items"):
                data["folders"][0]["items"] = legacy_items
            elif not data["folders"][0].get("items"):
                data["folders"][0]["items"] = get_default_items()

        # Ensure active folder index is valid
        if "active_folder_index" not in data or data["active_folder_index"] >= len(data["folders"]):
            data["active_folder_index"] = 0

        # Multi-instance migration: ensure "instances" list exists and is populated
        instances = data.get("instances")
        if not user_has_instances or not isinstance(instances, list) or len(instances) == 0:
            legacy_folder_name = data.get("folder_name", "常用应用")
            if "folders" in data and isinstance(data["folders"], list) and len(data["folders"]) > 0:
                act_idx = data.get("active_folder_index", 0)
                if 0 <= act_idx < len(data["folders"]):
                    legacy_folder_name = data["folders"][act_idx].get("name", legacy_folder_name)
                else:
                    legacy_folder_name = data["folders"][0].get("name", legacy_folder_name)
            data["instances"] = [
                {
                    "instance_id": "default",
                    "folder_name": legacy_folder_name,
                    "pos_x": data.get("pos_x", 797),
                    "pos_y": data.get("pos_y", 49),
                    "locked": data.get("locked", False)
                }
            ]
        else:
            for idx, inst in enumerate(data["instances"]):
                if not isinstance(inst, dict):
                    continue
                if "instance_id" not in inst or not inst["instance_id"]:
                    inst["instance_id"] = "default" if idx == 0 else f"inst_{idx}"
                if "folder_name" not in inst:
                    inst["folder_name"] = "常用应用"
                if "pos_x" not in inst:
                    inst["pos_x"] = 797
                if "pos_y" not in inst:
                    inst["pos_y"] = 49
                if "locked" not in inst:
                    inst["locked"] = False

        self.save(data)
        return data

    def save(self, data: Dict[str, Any] = None):
        if data is not None:
            self.data = data
        try:
            # 覆盖前留一份上一版：配置里含用户全部卡片布局，误覆盖过一次就很难找回
            if os.path.exists(self.config_path):
                import shutil as _shutil
                try:
                    _shutil.copyfile(self.config_path, self.config_path + ".bak")
                except Exception:
                    pass
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("Failed to save config.json:", e)

    def get(self, key: str, default: Any = None) -> Any:
        # Backwards compatibility wrappers
        if key == "items":
            folder = self.get_active_folder()
            return folder.get("items", []) if folder else []
        elif key == "folder_name":
            folder = self.get_active_folder()
            return folder.get("name", "常用应用") if folder else "常用应用"
        return self.data.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True):
        # Backwards compatibility wrappers
        if key == "items":
            idx = self.data.get("active_folder_index", 0)
            if 0 <= idx < len(self.data.get("folders", [])):
                self.data["folders"][idx]["items"] = value
        elif key == "folder_name":
            idx = self.data.get("active_folder_index", 0)
            if 0 <= idx < len(self.data.get("folders", [])):
                self.data["folders"][idx]["name"] = value
            if self.data.get("instances"):
                self.data["instances"][0]["folder_name"] = value
        elif key in ("pos_x", "pos_y", "locked"):
            self.data[key] = value
            if self.data.get("instances"):
                self.data["instances"][0][key] = value
        else:
            self.data[key] = value

        if auto_save:
            self.save()


    # --- Multi-Instance Management APIs ---

    def get_instances(self) -> List[Dict[str, Any]]:
        return self.data.get("instances", [])

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
        for inst in self.get_instances():
            if inst.get("instance_id") == instance_id:
                return inst
        return None

    def add_instance(
        self,
        folder_name: str,
        pos_x: int,
        pos_y: int,
        locked: bool = False,
        instance_id: Optional[str] = None,
        rows: int = 3,
        cols: int = 3
    ) -> Dict[str, Any]:
        if not instance_id:
            import uuid
            instance_id = f"inst_{uuid.uuid4().hex[:8]}"

        new_inst = {
            "instance_id": instance_id,
            "folder_name": folder_name.strip() if folder_name else "常用应用",
            "pos_x": int(pos_x),
            "pos_y": int(pos_y),
            "locked": bool(locked),
            "rows": int(rows),
            "cols": int(cols)
        }
        instances = self.get_instances()
        instances.append(new_inst)
        self.data["instances"] = instances
        self.save()
        return new_inst

    def update_instance(self, instance_id: str, updates: Dict[str, Any], auto_save: bool = True) -> bool:
        instances = self.get_instances()
        for idx, inst in enumerate(instances):
            if inst.get("instance_id") == instance_id:
                inst.update(updates)
                # Mirror primary instance pos/lock to top level for 100% backward compat
                if idx == 0 or instance_id == "default":
                    if "pos_x" in updates:
                        self.data["pos_x"] = updates["pos_x"]
                    if "pos_y" in updates:
                        self.data["pos_y"] = updates["pos_y"]
                    if "locked" in updates:
                        self.data["locked"] = updates["locked"]
                    if "rows" in updates:
                        self.data["grid_rows"] = updates["rows"]
                    if "cols" in updates:
                        self.data["grid_cols"] = updates["cols"]
                    if "folder_name" in updates:
                        for f_i, f in enumerate(self.get_folders()):
                            if f.get("name") == updates["folder_name"]:
                                self.data["active_folder_index"] = f_i
                                break
                if auto_save:
                    self.save()
                return True
        return False

    def remove_instance(self, instance_id: str, auto_save: bool = True) -> bool:
        instances = self.get_instances()
        initial_len = len(instances)
        new_instances = [inst for inst in instances if inst.get("instance_id") != instance_id]
        if len(new_instances) < initial_len:
            self.data["instances"] = new_instances
            if new_instances:
                first = new_instances[0]
                self.data["pos_x"] = first.get("pos_x", 797)
                self.data["pos_y"] = first.get("pos_y", 49)
                self.data["locked"] = first.get("locked", False)
                first_f = first.get("folder_name", "常用应用")
                for f_i, f in enumerate(self.get_folders()):
                    if f.get("name") == first_f:
                        self.data["active_folder_index"] = f_i
                        break
            if auto_save:
                self.save()
            return True
        return False

    def set_all_instances_locked(self, locked: bool, auto_save: bool = True):
        self.data["locked"] = locked
        for inst in self.data.get("instances", []):
            inst["locked"] = locked
        if auto_save:
            self.save()


    def get_folder_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        for f in self.get_folders():
            if f.get("name") == name:
                return f
        return None

    def get_folder_items(self, name: str) -> List[Dict[str, Any]]:
        folder = self.get_folder_by_name(name)
        return folder.get("items", []) if folder else []

    def set_folder_items(self, name: str, items: List[Dict[str, Any]], auto_save: bool = True) -> bool:
        folders = self.get_folders()
        for f in folders:
            if f.get("name") == name:
                f["items"] = items
                if auto_save:
                    self.save()
                return True
        return False

    # --- Multi-Folder Management APIs ---

    def get_folders(self) -> List[Dict[str, Any]]:
        return self.data.get("folders", [])

    def get_active_folder_index(self) -> int:
        return self.data.get("active_folder_index", 0)

    def set_active_folder_index(self, index: int, auto_save: bool = True):
        folders = self.get_folders()
        if 0 <= index < len(folders):
            self.data["active_folder_index"] = index
            if auto_save:
                self.save()

    def get_active_folder(self) -> Optional[Dict[str, Any]]:
        folders = self.get_folders()
        idx = self.get_active_folder_index()
        if 0 <= idx < len(folders):
            return folders[idx]
        if folders:
            return folders[0]
        return None

    def add_folder(self, name: str, set_active: bool = True) -> int:
        folders = self.get_folders()
        new_folder = {"name": name.strip(), "items": []}
        folders.append(new_folder)
        self.data["folders"] = folders
        new_idx = len(folders) - 1
        if set_active:
            self.set_active_folder_index(new_idx)
        return new_idx

    def rename_folder(self, index: int, new_name: str):
        folders = self.get_folders()
        if 0 <= index < len(folders):
            old_name = folders[index].get("name", "")
            folders[index]["name"] = new_name.strip()
            for inst in self.data.get("instances", []):
                if inst.get("folder_name") == old_name:
                    inst["folder_name"] = new_name.strip()
            self.save()

    def delete_folder(self, index: int, storage_manager=None) -> bool:
        folders = self.get_folders()
        if len(folders) <= 1:
            return False  # Do not allow deleting the last remaining folder
        if 0 <= index < len(folders):
            deleted_name = folders[index].get("name", "")
            if storage_manager is not None:
                try:
                    storage_manager.dissolve_folder_to_desktop(deleted_name)
                except Exception as e:
                    print(f"Error dissolving folder {deleted_name} to desktop:", e)
            folders.pop(index)
            curr = self.data.get("active_folder_index", 0)
            if curr >= len(folders):
                self.data["active_folder_index"] = len(folders) - 1
            fallback_name = folders[self.data["active_folder_index"]].get("name", "常用应用")
            
            # Prune instances bound to deleted_name if other instances exist to prevent duplicate cards
            existing_instances = self.data.get("instances", [])
            new_instances = []
            for inst in existing_instances:
                if inst.get("folder_name") == deleted_name:
                    if len(existing_instances) == 1:
                        inst["folder_name"] = fallback_name
                        new_instances.append(inst)
                    # Else prune this instance (closed upon folder dissolution)
                else:
                    new_instances.append(inst)

            if not new_instances:
                new_instances.append({
                    "instance_id": "card_01_main",
                    "folder_name": fallback_name,
                    "pos_x": 80,
                    "pos_y": 80,
                    "locked": False
                })
            self.data["instances"] = new_instances
            self.save()
            return True
        return False


    def get_active_folder_name(self) -> str:
        folder = self.get_active_folder()
        return folder.get("name", "常用应用") if folder else "常用应用"

    def get_managed_storage_root(self) -> str:
        root = self.data.get("managed_storage_root", "")
        if not root:
            user_home = os.path.expanduser("~")
            root = os.path.join(user_home, "Stacky")
            try:
                os.makedirs(root, exist_ok=True)
            except Exception:
                pass
            self.data["managed_storage_root"] = root
        return root

    def set_managed_storage_root(self, root_path: str, auto_save: bool = True):
        self.data["managed_storage_root"] = root_path
        if auto_save:
            self.save()


    def is_managed_storage_enabled(self) -> bool:
        return bool(self.data.get("managed_storage_enabled", True))

    def get_managed_drop_action(self) -> str:
        return self.data.get("managed_drop_action", "move")

    # --- Weather Widget Configuration ---

    def get_weather_config(self) -> Dict[str, Any]:
        default_weather = {
            "enabled": True,
            "x": 614,
            "y": 180,
            "size": "2x2",
            "locked": False,
            "auto_location": True,
            "city": "成都",
            "region": "四川",
            "district": "",
            "lat": 30.6586,
            "lon": 104.0648
        }
        cfg = self.data.get("weather_widget")
        if isinstance(cfg, dict):
            merged = dict(default_weather)
            merged.update(cfg)
            return merged
        return default_weather

    def set_weather_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["weather_widget"] = cfg
        if auto_save:
            self.save()


    def set_weather_location(
        self,
        city: str,
        region: str = "",
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        auto_location: bool = False,
        district: str = "",
        auto_save: bool = True
    ):
        cfg = self.get_weather_config()
        cfg["district"] = district.strip()
        cfg["auto_location"] = bool(auto_location)
        cfg["city"] = city.strip()
        cfg["region"] = region.strip()
        if lat is not None:
            cfg["lat"] = float(lat)
        if lon is not None:
            cfg["lon"] = float(lon)
        self.set_weather_config(cfg, auto_save=auto_save)

    # --- Clock, System Monitor & Memo Desktop Widgets Configuration ---

    def get_clock_config(self) -> Dict[str, Any]:
        default_clock = {"enabled": False, "x": 300, "y": 180, "size": "2x2", "locked": False}
        cfg = self.data.get("clock_widget")
        if isinstance(cfg, dict):
            merged = dict(default_clock)
            merged.update(cfg)
            return merged
        return default_clock

    def set_clock_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["clock_widget"] = cfg
        if auto_save:
            self.save()


    def get_monitor_config(self) -> Dict[str, Any]:
        default_monitor = {"enabled": False, "x": 300, "y": 450, "size": "2x2",
                           "locked": False, "disk_drive": "C:\\", "disk_drives": ["C:\\"]}
        cfg = self.data.get("monitor_widget")
        if isinstance(cfg, dict):
            merged = dict(default_monitor)
            merged.update(cfg)
            return merged
        return default_monitor

    def set_monitor_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["monitor_widget"] = cfg
        if auto_save:
            self.save()


    def get_memo_config(self) -> Dict[str, Any]:
        default_memo = {"enabled": False, "x": 300, "y": 720, "size": "2x2", "locked": False}
        cfg = self.data.get("memo_widget")
        if isinstance(cfg, dict):
            merged = dict(default_memo)
            merged.update(cfg)
            return merged
        return default_memo

    def set_memo_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["memo_widget"] = cfg
        if auto_save:
            self.save()


    def get_media_config(self) -> Dict[str, Any]:
        default_media = {"enabled": True, "x": 300, "y": 450, "size": "2x2",
                         "locked": False, "show_lyrics": True}
        cfg = self.data.get("media_widget")
        if isinstance(cfg, dict):
            merged = dict(default_media)
            merged.update(cfg)
            return merged
        return default_media

    def set_media_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["media_widget"] = cfg
        if auto_save:
            self.save()

    def get_everything_config(self) -> Dict[str, Any]:
        default_cfg = {"enabled": True, "x": 300, "y": 180, "size": "2x2", "locked": False}
        cfg = self.data.get("everything_widget")
        if isinstance(cfg, dict):
            merged = dict(default_cfg)
            merged.update(cfg)
            return merged
        return default_cfg

    def set_everything_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["everything_widget"] = cfg
        if auto_save:
            self.save()

    def is_double_click_desktop_hide_enabled(self) -> bool:
        return bool(self.data.get("double_click_desktop_hide", True))

    def set_double_click_desktop_hide_enabled(self, enabled: bool, auto_save: bool = True):
        self.data["double_click_desktop_hide"] = bool(enabled)
        if auto_save:
            self.save()

    def get_file_shelf_config(self) -> Dict[str, Any]:
        default_shelf = {"enabled": True, "edge": "right", "auto_collapse": True, "pinned": False, "y_ratio": 0.45, "hide_in_fullscreen": True}
        cfg = self.data.get("file_shelf")
        if isinstance(cfg, dict):
            merged = dict(default_shelf)
            merged.update(cfg)
            return merged
        return default_shelf

    def set_file_shelf_config(self, cfg: Dict[str, Any], auto_save: bool = True):
        self.data["file_shelf"] = cfg
        if auto_save:
            self.save()

    def is_file_shelf_enabled(self) -> bool:
        return bool(self.get_file_shelf_config().get("enabled", True))

    def set_file_shelf_enabled(self, enabled: bool, auto_save: bool = True):
        cfg = self.get_file_shelf_config()
        cfg["enabled"] = bool(enabled)
        self.set_file_shelf_config(cfg, auto_save=auto_save)


    # --- Open App Mode (Double Click Anti-Misclick vs Single Click vs Hybrid) ---

    def get_open_app_mode(self) -> str:
        return self.data.get("open_app_mode", "double")

    def get_open_mode_for_card(self) -> str:
        if "open_app_mode_card" in self.data:
            return self.data["open_app_mode_card"]
        mode = self.get_open_app_mode()
        return "single" if mode in ("single", "hybrid") else "double"

    def get_open_mode_for_drawer(self) -> str:
        if "open_app_mode_drawer" in self.data:
            return self.data["open_app_mode_drawer"]
        mode = self.get_open_app_mode()
        return "single" if mode == "single" else "double"

    def set_open_app_mode(self, mode: str, auto_save: bool = True):
        self.data["open_app_mode"] = mode
        if mode == "double":
            self.data["open_app_mode_card"] = "double"
            self.data["open_app_mode_drawer"] = "double"
        elif mode == "hybrid":
            self.data["open_app_mode_card"] = "single"
            self.data["open_app_mode_drawer"] = "double"
        elif mode == "single":
            self.data["open_app_mode_card"] = "single"
            self.data["open_app_mode_drawer"] = "single"
        if auto_save:
            self.save()

    # --- Everything Integration ---

    def is_everything_enabled(self) -> bool:
        return bool(self.data.get("everything_enabled", True))

    def set_everything_enabled(self, enabled: bool, auto_save: bool = True):
        self.data["everything_enabled"] = bool(enabled)
        if auto_save:
            self.save()

    # --- Display Mode & High-Value Launcher Settings ---

    def get_app_display_mode(self) -> str:
        return self.data.get("app_display_mode", "classic")

    def set_app_display_mode(self, mode: str, auto_save: bool = True):
        self.data["app_display_mode"] = mode
        if auto_save:
            self.save()

    def is_admin_badge_enabled(self) -> bool:
        return bool(self.data.get("show_admin_badge", True))

    def set_admin_badge_enabled(self, enabled: bool, auto_save: bool = True):
        self.data["show_admin_badge"] = bool(enabled)
        if auto_save:
            self.save()

    def is_running_badge_enabled(self) -> bool:
        return bool(self.data.get("show_running_badge", True))

    def set_running_badge_enabled(self, enabled: bool, auto_save: bool = True):
        self.data["show_running_badge"] = bool(enabled)
        if auto_save:
            self.save()

    def get_post_launch_action(self) -> str:
        return self.data.get("post_launch_action", "close_drawer")

    def set_post_launch_action(self, action: str, auto_save: bool = True):
        self.data["post_launch_action"] = action
        if auto_save:
            self.save()


