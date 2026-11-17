"""项目根目录。

源码按功能分目录后，模块不能再拿 ``__file__`` 的目录当项目根。
这里统一算：``stacky/paths.py`` 往上两级 = 项目根（开发态）或
PyInstaller 的 ``_MEIPASS``（打包态），与重构前顶层模块的语义一致。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_user_data_dir() -> str:
    """返回用户可写数据目录（配置、天气缓存、崩溃日志、便签等）。

    - 源码/测试态 (``not sys.frozen``)：返回 ``ROOT``，与原有开发态行为一致。
    - 打包态 (``sys.frozen``)：若 ``sys.executable`` 同级存在 ``portable.flag``，
      使用可执行文件所在目录；否则使用 ``%APPDATA%\\Stacky``。
    """
    if not getattr(sys, "frozen", False):
        return ROOT

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.exists(os.path.join(exe_dir, "portable.flag")):
        os.makedirs(exe_dir, exist_ok=True)
        return exe_dir

    appdata = os.environ.get("APPDATA") or exe_dir
    data_dir = os.path.join(appdata, "Stacky")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_user_cache_dir() -> str:
    """返回用户可写图标缓存目录（``.cache_icons``）。

    - 源码/测试态 (``not sys.frozen``)：返回 ``ROOT/.cache_icons``。
    - 打包态 (``sys.frozen``)：若 ``portable.flag`` 存在则使用 ``exe_dir/.cache_icons``；
      否则使用 ``%LOCALAPPDATA%\\Stacky\\.cache_icons``（回退 ``%APPDATA%\\Stacky\\.cache_icons``）。
    """
    if not getattr(sys, "frozen", False):
        cache_dir = os.path.join(ROOT, ".cache_icons")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.exists(os.path.join(exe_dir, "portable.flag")):
        cache_dir = os.path.join(exe_dir, ".cache_icons")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or exe_dir
    cache_dir = os.path.join(base, "Stacky", ".cache_icons")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir
