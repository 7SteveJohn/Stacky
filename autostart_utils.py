import os
import sys
import winreg

APP_NAME = "Stacky"
# 旧版本/旧产品遗留的自启项名称，仅用于清理，属于互操作标识
LEGACY_APP_NAMES = ["BigFolderDesktopWidget", "DeskBox"]

def get_launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    # Use pythonw if running directly with python to prevent console popping up on reboot
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pythonw):
            exe = pythonw
    main_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
    return f'"{exe}" "{main_script}" --autostart'

def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, APP_NAME)
                if val:
                    return True
            except FileNotFoundError:
                pass
            for leg in LEGACY_APP_NAMES:
                try:
                    val, _ = winreg.QueryValueEx(key, leg)
                    if val:
                        return True
                except FileNotFoundError:
                    pass
            return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

def clean_legacy_autostart():
    """Removes any obsolete or competing autostart registry entries."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            for leg in LEGACY_APP_NAMES:
                try:
                    winreg.DeleteValue(key, leg)
                except FileNotFoundError:
                    pass
    except Exception:
        pass

def set_autostart(enable: bool) -> bool:
    try:
        clean_legacy_autostart()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                cmd = get_launch_command()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        print("Failed to set autostart:", e)
        return False
