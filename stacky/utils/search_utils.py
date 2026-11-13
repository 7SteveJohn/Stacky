from typing import List, Dict, Any, Tuple, Optional
import ast
import functools
import operator
import re
import urllib.parse
import ctypes
import subprocess
import pypinyin

# ==========================================================
# 1. Chinese Pinyin & Fuzzy Matching Engine
# ==========================================================

@functools.lru_cache(maxsize=1024)
def get_pinyin_variants(text: str) -> Tuple[str, str]:
    """
    Returns (first_letters, full_pinyin) in lowercase for a given Chinese string.
    Example: "计算器" -> ("jsq", "jisuanqi")
    Cached with LRU to achieve instantaneous O(1) response during live typing.
    """
    if not text:
        return "", ""
    try:
        letters = pypinyin.lazy_pinyin(text, style=pypinyin.Style.FIRST_LETTER)
        first_letters = "".join(letters).lower()
        full = pypinyin.lazy_pinyin(text)
        full_pinyin = "".join(full).lower()
        return first_letters, full_pinyin
    except Exception:
        return "", ""

def score_match(item: Dict[str, Any], query: str) -> int:
    """
    Calculates match score between item and query.
    Higher score means better match.
    0 means no match.
    """
    if not query:
        return 1

    name = item.get("name", "").strip().lower()
    path = item.get("path", "").strip().lower()
    q = query.strip().lower()

    if not name and not path:
        return 0

    # 1. Exact name match
    if name == q:
        return 100

    # 2. Name starts with query
    if name.startswith(q):
        return 90

    # 3. Pinyin initials exact or prefix match
    initials, full_py = get_pinyin_variants(item.get("name", ""))
    if initials:
        if initials == q:
            return 85
        if initials.startswith(q):
            return 80
        if q in initials:
            return 70

    # 4. Full pinyin prefix match
    if full_py:
        if full_py.startswith(q):
            return 75
        if q in full_py:
            return 65

    # 5. Name contains query
    if q in name:
        return 60

    # 6. Path contains query (e.g. filename)
    if q in path:
        return 40

    return 0

def search_items(items: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """
    Filters and sorts items by query using pinyin and substring matching.
    """
    q = query.strip().lower()
    if not q:
        return list(items)

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for it in items:
        s = score_match(it, q)
        if s > 0:
            scored.append((s, it))

    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored]


# ==========================================================
# 2. Safe Real-Time Math Calculator (Python AST Whitelist)
# ==========================================================

SAFE_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

SAFE_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

def _eval_ast_node(node: ast.AST) -> Any:
    """
    Recursively evaluate an AST expression with a strict whitelist.
    STRICTLY NO eval() or function execution!
    """
    if isinstance(node, ast.Expression):
        return _eval_ast_node(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Non-numeric constant rejected")
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in SAFE_UNARY_OPS:
            return SAFE_UNARY_OPS[op_type](_eval_ast_node(node.operand))
        raise ValueError(f"Unsupported unary operator: {op_type}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type in SAFE_BIN_OPS:
            left = _eval_ast_node(node.left)
            right = _eval_ast_node(node.right)
            if op_type in (ast.Div, ast.FloorDiv, ast.Mod):
                if right == 0:
                    raise ZeroDivisionError("division by zero")
            if op_type == ast.Pow:
                # Security guard against CPU freezing DOS attacks
                if abs(right) > 1000 or (abs(left) > 1e6 and abs(right) > 20):
                    raise OverflowError("Exponent value exceeds safe calculation limits")
                res = left ** right
                if isinstance(res, complex):
                    raise ValueError("Complex numbers are not supported")
                return res
            return SAFE_BIN_OPS[op_type](left, right)
        raise ValueError(f"Unsupported binary operator: {op_type}")
    else:
        raise ValueError(f"Unsupported AST node type: {type(node)}")

def safe_calculate_math(expression: str) -> Optional[Dict[str, Any]]:
    """
    Detects math expressions and safely calculates the result using Python AST.
    Handles math expressions like:
    - 1024*768
    - (12+8)/4
    - 3.14*20^2
    - 2**10
    - 100/3
    Returns formatted result dict or None.
    """
    if not expression or not expression.strip():
        return None

    clean_expr = expression.strip()
    # Strip trailing equals sign if user typed e.g. "1+1="
    clean_expr = clean_expr.rstrip("=").strip()

    # Normalize full-width spaces and whitespace
    clean_expr = re.sub(r"[\s\u3000\u00a0]+", " ", clean_expr)

    # Normalize thousands separator commas (e.g. 1,000 * 2 -> 1000 * 2)
    clean_expr = re.sub(r"(\d),(\d)", r"\1\2", clean_expr)

    # Normalize mathematical symbols
    clean_expr = clean_expr.replace("×", "*").replace("÷", "/").replace("^", "**")

    # Character whitelist check
    allowed_chars = set("0123456789. +-/*%()")
    if not set(clean_expr).issubset(allowed_chars):
        return None

    # Must contain at least one digit and at least one operator
    has_digit = any(c.isdigit() for c in clean_expr)
    has_op = any(c in "+-*/%" for c in clean_expr)
    if not (has_digit and has_op):
        return None

    try:
        tree = ast.parse(clean_expr, mode="eval")
        res = _eval_ast_node(tree)
        if isinstance(res, (int, float)):
            if isinstance(res, float) and res.is_integer():
                res = int(res)

            if isinstance(res, int):
                formatted = f"{res:,}"
                raw_val = str(res)
            else:
                formatted = f"{res:,.8f}".rstrip("0").rstrip(".")
                raw_val = f"{res:.8f}".rstrip("0").rstrip(".")
                if not formatted:
                    formatted = f"{res:g}"
                if not raw_val:
                    raw_val = f"{res:g}"

            return {
                "formatted": formatted,
                "value": formatted,
                "raw_val": raw_val,
                "number": res,
            }
    except Exception:
        return None
    return None


# ==========================================================
# 3. Web Search & Quick Links Engine
# ==========================================================

def parse_web_quick_link(query: str) -> Optional[Dict[str, Any]]:
    """
    Recognizes prefix web search commands and raw URLs:
    - gh <query> -> GitHub search
    - bili <query> -> Bilibili search
    - bd <query> -> Baidu search
    - g <query> -> Google search
    - Direct URLs (http://, https://, *.com, *.cn, *.org, *.net, *.io)
    """
    q = query.strip()
    if not q:
        return None

    lower_q = q.lower()

    # Prefix search table: (prefix, name, home_url, search_prefix, icon)
    prefixes = [
        ("gh", "GitHub", "https://github.com", "https://github.com/search?q=", "GH"),
        ("bili", "哔哩哔哩", "https://www.bilibili.com", "https://search.bilibili.com/all?keyword=", "B"),
        ("bd", "百度", "https://www.baidu.com", "https://www.baidu.com/s?wd=", "百"),
        ("g", "Google", "https://www.google.com", "https://www.google.com/search?q=", "G"),
    ]

    for pfx, name, home_url, search_prefix, icon in prefixes:
        if lower_q == pfx:
            return {
                "title": f"打开 {name} 官网",
                "url": home_url,
                "prefix": pfx,
                "query": "",
                "icon": icon,
            }
        if lower_q.startswith(pfx + " ") or lower_q.startswith(pfx + "：") or lower_q.startswith(pfx + ":"):
            param = q[len(pfx) + 1:].strip()
            if param:
                encoded = urllib.parse.quote(param)
                return {
                    "title": f"{name} 搜索: {param}",
                    "url": f"{search_prefix}{encoded}",
                    "prefix": pfx,
                    "query": param,
                    "icon": icon,
                }
            else:
                return {
                    "title": f"打开 {name} 官网",
                    "url": home_url,
                    "prefix": pfx,
                    "query": "",
                    "icon": icon,
                }

    # Direct URLs starting with http:// or https://
    if lower_q.startswith("http://") or lower_q.startswith("https://"):
        return {
            "title": f"访问网址: {q}",
            "url": q,
            "prefix": "url",
            "query": q,
            "icon": "网",
        }

    # Common domain matching: *.com, *.cn, *.org, *.net, *.io, etc. (with optional port & path)
    if " " not in q and "." in q:
        tld_pattern = r"^([a-zA-Z0-9][-a-zA-Z0-9]*\.)+(com|cn|org|net|io|edu|gov|cc|me|top|xyz)(:\d+)?(\/.*)?$"
        if re.match(tld_pattern, lower_q):
            target_url = "https://" + q
            return {
                "title": f"访问网址: {q}",
                "url": target_url,
                "prefix": "url",
                "query": q,
                "icon": "网",
            }

        # Direct IPv4 address matching (e.g. 127.0.0.1:8080 or 192.168.1.1)
        ip_pattern = r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)(:\d+)?(\/.*)?$"
        if re.match(ip_pattern, lower_q):
            target_url = "http://" + q
            return {
                "title": f"访问 IP: {q}",
                "url": target_url,
                "prefix": "url",
                "query": q,
                "icon": "网",
            }

    return None


# ==========================================================
# 4. System Commands Engine
# ==========================================================


SYSTEM_COMMANDS: List[Dict[str, Any]] = [
    {
        "id": "cmd",
        "name": "命令提示符 (CMD)",
        "icon": ">",
        "keywords": ["cmd", "terminal", "command", "ml", "mingling", "powershell", "pwsh"],
        "cmd": "cmd.exe /c start cmd",
    },
    {
        "id": "taskmgr",
        "name": "任务管理器 (Task Manager)",
        "icon": "T",
        "keywords": ["taskmgr", "rwgl", "task", "renwuguanliqi"],
        "cmd": "taskmgr.exe",
    },
    {
        "id": "calc",
        "name": "系统计算器 (Calculator)",
        "icon": "=",
        "keywords": ["calc", "jsq", "calculator", "jisuanqi"],
        "cmd": "calc.exe",
    },
    {
        "id": "regedit",
        "name": "注册表编辑器 (Registry Editor)",
        "icon": "R",
        "keywords": ["regedit", "reg", "zcb", "zhucebiao"],
        "cmd": "regedit.exe",
    },
    {
        "id": "control",
        "name": "控制面板 (Control Panel)",
        "icon": "C",
        "keywords": ["control", "kzmb", "panel", "kongzhimianban"],
        "cmd": "control.exe",
    },
    {
        "id": "lock",
        "name": "锁定屏幕 (Lock Workstation)",
        "icon": "L",
        "keywords": ["lock", "suoping", "sp", "suo", "suoding"],
        "action": "lock_workstation",
    },
]

def match_system_commands(query: str) -> List[Dict[str, Any]]:
    """
    Matches query against built-in Windows system commands.
    """
    q = query.strip().lower()
    if not q:
        return []

    matched: List[Tuple[int, Dict[str, Any]]] = []
    for cmd_info in SYSTEM_COMMANDS:
        score = 0
        if cmd_info["id"] == q:
            score = 100
        elif any(kw == q for kw in cmd_info["keywords"]):
            score = 90
        elif cmd_info["id"].startswith(q):
            score = 80
        elif any(kw.startswith(q) for kw in cmd_info["keywords"]):
            score = 70
        elif q in cmd_info["name"].lower():
            score = 60

        if score > 0:
            matched.append((score, cmd_info))

    matched.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in matched]

def execute_system_command(cmd_info: Dict[str, Any]) -> bool:
    """
    Executes a system command or action.
    """
    try:
        action = cmd_info.get("action")
        if action == "lock_workstation":
            ctypes.windll.user32.LockWorkStation()
            return True
        cmd = cmd_info.get("cmd")
        if cmd:
            subprocess.Popen(cmd, shell=True)
            return True
    except Exception as e:
        print(f"Failed to execute system command {cmd_info}: {e}")
        return False
    return False


# ==========================================================
# 5. Everything Local Disk Search Engine
# ==========================================================

_EVERYTHING_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_EVERYTHING_CACHE_TTL = 6.0  # seconds

def _log_search_error(query: str, exc: BaseException):
    """搜索异常落盘，避免 except 静默吞掉导致「搜不到」无从排查。"""
    try:
        import os as _os
        import time as _t
        import traceback as _tb
        path = _os.path.join(_os.environ.get("TEMP", "."), "stacky_search.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{_t.strftime('%H:%M:%S')}] query={query!r} err={exc!r}\n")
            fh.write(_tb.format_exc() + "\n")
    except Exception:
        pass

def search_everything_files(query: str, max_results: int = 12) -> List[Dict[str, Any]]:
    """
    Queries local files and folders via Everything Win32 IPC.
    Returns standard item dicts for instant launching and folder navigation.
    Features short-TTL query caching (6s) to eliminate redundant IPC traffic during live typing/backspacing.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []

    cache_key = f"{q.lower()}|{max_results}"
    import time as _t
    now = _t.time()
    cached = _EVERYTHING_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _EVERYTHING_CACHE_TTL:
        return [dict(x) for x in cached[1]]

    try:
        from everything_ipc import search_files
        raw_results = search_files(q, max_results=max_results)
        items: List[Dict[str, Any]] = []
        for r in raw_results:
            items.append({
                "type": "file",
                "name": r["name"],
                "path": r["full_path"],
                "dir": r["path"],
                "is_folder": r["is_folder"],
                "icon": "",
            })
        # 空结果不缓存：否则 Everything 刚启动/索引未就绪时，6 秒内再怎么搜都是空
        if items:
            if len(_EVERYTHING_CACHE) > 64:
                _EVERYTHING_CACHE.clear()
            _EVERYTHING_CACHE[cache_key] = (now, items)
        return items
    except Exception as exc:
        _log_search_error(q, exc)
        return []

