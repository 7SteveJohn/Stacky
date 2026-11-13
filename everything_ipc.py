"""
everything_ipc.py - Everything Win32 IPC 轻量直连驱动
基于 voidtools Everything 原生 WM_COPYDATA 消息契约，提供毫秒级全盘文件检索能力。
零第三方二进制依赖，纯原生 ctypes 实现。
采用独立工作线程模型，避开 Qt 主线程桌面绑定限制，且杜绝界面掉帧卡顿。
"""

import ctypes
from ctypes import wintypes
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WPARAM = ctypes.c_uint64
LPARAM = ctypes.c_int64
LRESULT = ctypes.c_int64

user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL

user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, WPARAM, ctypes.c_void_p]
user32.SendMessageW.restype = LRESULT

try:
    user32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
except AttributeError:
    pass

class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_ulonglong),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p),
    ]

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class EverythingIPCClient:
    """轻量级 Everything IPC 客户端 (线程隔离、安全熔断)"""

    EVERYTHING_WINDOW_CLASS = "EVERYTHING_TASKBAR_NOTIFICATION"
    EVERYTHING_IPC_COPYDATAQUERYW = 2

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="BFL_EverythingIPC")

    def _ensure_desktop_attached(self):
        """确保当前工作线程挂载到用户的交互输入桌面（每线程一次即可）"""
        if getattr(self, "_desktop_attached", False):
            return
        try:
            h_desk = user32.OpenInputDesktop(0, False, 0x10000000)
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception:
            pass
        self._desktop_attached = True

    def is_available(self) -> bool:
        """检测 Everything 是否正在后台运行且已开启 IPC"""
        try:
            future = self._executor.submit(self._check_available_worker)
            return future.result(timeout=0.5)
        except Exception:
            return False

    def _check_available_worker(self) -> bool:
        self._ensure_desktop_attached()
        hwnd = user32.FindWindowW(self.EVERYTHING_WINDOW_CLASS, None)
        return bool(hwnd)

    def search(self, query_str: str, max_results: int = 15, timeout: float = 2.0) -> List[Dict[str, Any]]:
        """
        向 Everything 检索全盘文件与目录 (线程池异步调度 + 毫秒级超时熔断)
        """
        query_str = (query_str or "").strip()
        if not query_str or len(query_str) < 2:
            return []

        try:
            future = self._executor.submit(self._do_search_worker, query_str, max_results, timeout)
            return future.result(timeout=timeout + 0.05)
        except Exception:
            return []

    def _do_search_worker(self, query_str: str, max_results: int, timeout: float) -> List[Dict[str, Any]]:
        self._ensure_desktop_attached()
        everything_hwnd = user32.FindWindowW(self.EVERYTHING_WINDOW_CLASS, None)
        if not everything_hwnd:
            return []

        results: List[Dict[str, Any]] = []

        def reply_wnd_proc(hwnd, msg, wparam, lparam):
            if msg == 0x004A:  # WM_COPYDATA
                try:
                    cds = COPYDATASTRUCT.from_address(lparam)
                    base_addr = cds.lpData
                    # 7 个 DWORD: totfolders, totfiles, totitems, numfolders, numfiles, numitems, offset
                    _, _, _, _, _, numitems, _ = struct.unpack_from("<IIIIIII", ctypes.string_at(base_addr, 28), 0)
                    
                    list_header_size = 28
                    item_size = 12  # flags, filename_offset, path_offset
                    for i in range(numitems):
                        item_offset = list_header_size + i * item_size
                        raw_item = ctypes.string_at(base_addr + item_offset, item_size)
                        flags, fn_off, path_off = struct.unpack("<III", raw_item)
                        
                        fn_str = ctypes.wstring_at(base_addr + fn_off)
                        path_str = ctypes.wstring_at(base_addr + path_off)
                        is_folder = bool(flags & 1)
                        full_path = os.path.join(path_str, fn_str)
                        
                        results.append({
                            "name": fn_str,
                            "path": path_str,
                            "full_path": full_path,
                            "is_folder": is_folder
                        })
                except Exception:
                    pass
                return 1
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wndproc = WNDPROC(reply_wnd_proc)
        cls_name = f"BFL_EV_Reply_{int(time.perf_counter() * 1000000) & 0xFFFFFF}"
        h_instance = kernel32.GetModuleHandleW(None)

        wce = WNDCLASSEXW()
        wce.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wce.lpfnWndProc = wndproc
        wce.hInstance = h_instance
        wce.lpszClassName = cls_name

        atom = user32.RegisterClassExW(ctypes.byref(wce))
        if not atom:
            return []

        reply_hwnd = user32.CreateWindowExW(0, cls_name, "BFL_Reply", 0, 0, 0, 0, 0, 0, 0, h_instance, None)
        if not reply_hwnd:
            user32.UnregisterClassW(cls_name, h_instance)
            return []

        try:
            try:
                user32.ChangeWindowMessageFilterEx(reply_hwnd, 0x004A, 1, None)
            except Exception:
                pass

            # 构建 EVERYTHING_IPC_QUERYW 结构体
            search_bytes = (query_str + "\0").encode("utf-16le")
            query_header = struct.pack("<IIIII", reply_hwnd & 0xFFFFFFFF, 12345, 0, 0, max_results)
            query_payload = query_header + search_bytes

            cds = COPYDATASTRUCT()
            cds.dwData = self.EVERYTHING_IPC_COPYDATAQUERYW
            cds.cbData = len(query_payload)
            # create_string_buffer 持有内存引用：原来用 c_char_p 临时对象，GC 后地址可能失效，
            # 表现为短英文能搜到、长词/中文搜不到
            payload_buf = ctypes.create_string_buffer(query_payload, len(query_payload))
            cds.lpData = ctypes.cast(payload_buf, ctypes.c_void_p)

            # 超时发送：Everything 无响应时不能把调用线程挂死
            try:
                smto_result = ctypes.c_size_t()
                user32.SendMessageTimeoutW(
                    everything_hwnd, 0x004A, reply_hwnd, ctypes.byref(cds),
                    0x0002, 800, ctypes.byref(smto_result))
            except AttributeError:
                user32.SendMessageW(everything_hwnd, 0x004A, reply_hwnd, ctypes.byref(cds))

            # 极速消息泵等待 Everything 结果回传
            msg = wintypes.MSG()
            start_wait = time.perf_counter()
            while (time.perf_counter() - start_wait) < timeout and not results:
                if user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    time.sleep(0.003)

            return results
        finally:
            if reply_hwnd:
                user32.DestroyWindow(reply_hwnd)
            user32.UnregisterClassW(cls_name, h_instance)


_client_instance = None

def get_everything_client() -> EverythingIPCClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = EverythingIPCClient()
    return _client_instance

def search_files(query_str: str, max_results: int = 15, timeout: float = 2.0) -> List[Dict[str, Any]]:
    return get_everything_client().search(query_str, max_results=max_results, timeout=timeout)
