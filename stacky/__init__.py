"""Stacky 源码包。

模块按功能分在 core / widgets / media / services / ui / utils 子目录，
但历史代码一直使用扁平导入（``import config_manager``）。这里把各子目录
加入 ``sys.path``，保持原有导入语句不变，避免大规模改写引入回归。

新代码建议使用显式包路径（``from stacky.core import config_manager``）。
"""

import os
import sys

__version__ = "1.0.3"

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUBDIRS = ("core", "widgets", "media", "services", "ui", "utils")

for _sub in _SUBDIRS:
    _path = os.path.join(_HERE, _sub)
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)
