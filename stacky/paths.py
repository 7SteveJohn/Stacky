"""项目根目录。

源码按功能分目录后，模块不能再拿 ``__file__`` 的目录当项目根。
这里统一算：``stacky/paths.py`` 往上两级 = 项目根（开发态）或
PyInstaller 的 ``_MEIPASS``（打包态），与重构前顶层模块的语义一致。
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
