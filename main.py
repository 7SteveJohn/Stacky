import sys
import os
import time
import ctypes
import traceback

def _global_exception_handler(exctype, value, tb):
    err_str = "".join(traceback.format_exception(exctype, value, tb))
    print("Unhandled Exception:", err_str)
    candidate_dirs = []
    try:
        from stacky.paths import get_user_data_dir
        candidate_dirs.append(get_user_data_dir())
    except Exception:
        pass
    try:
        import tempfile
        candidate_dirs.append(tempfile.gettempdir())
    except Exception:
        pass
    for log_dir in candidate_dirs:
        if not log_dir:
            continue
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "BigFolderLauncher_crash.log")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Unhandled Exception:\n{err_str}\n")
            break
        except Exception:
            continue
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = _global_exception_handler

IPC_SERVER_NAME = "BigFolder_Desktop_Launcher_IPC"
MUTEX_NAME = "Global\\BigFolderDesktopLauncher_SingleInstance"

def _determine_ipc_command(argv=None) -> str:
    args = sys.argv[1:] if argv is None else list(argv)[1:]
    cmd = "ACTIVATE"
    for arg in args:
        if arg == "--quit":
            return "QUIT"
        elif arg == "--autostart":
            return "AUTOSTART"
        elif arg == "--toggle":
            return "TOGGLE"
        elif arg == "--zen":
            return "ZEN"
        elif arg.startswith("--summon:"):
            return "SUMMON:" + arg.split(":", 1)[1]
        elif arg in ("--activate", "--show"):
            return "ACTIVATE"
    return cmd


def _write_win32_ipc_pipe(cmd: str, wait_ms: int = 1500, max_attempts: int = 3) -> bool:
    """Connects to the QLocalServer Win32 named pipe, handling ERROR_PIPE_BUSY (231) cleanly."""
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    pipe_path = f"\\\\.\\pipe\\{IPC_SERVER_NAME}"
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    ERROR_PIPE_BUSY = 231
    invalid_handles = (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF, None, 0)

    h_pipe = None
    for _ in range(max(1, int(max_attempts))):
        h = k32.CreateFileW(
            pipe_path,
            GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if h not in invalid_handles:
            h_pipe = h
            break
        err = k32.GetLastError()
        if err == ERROR_PIPE_BUSY:
            k32.WaitNamedPipeW(pipe_path, max(100, int(wait_ms)))
            continue
        break

    if h_pipe is None:
        return False

    try:
        if cmd != "QUIT":
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
        data = (cmd + "\n").encode("utf-8")
        written = wintypes.DWORD(0)
        res = k32.WriteFile(h_pipe, data, len(data), ctypes.byref(written), None)
        k32.FlushFileBuffers(h_pipe)
        return bool(res)
    finally:
        k32.CloseHandle(h_pipe)


def _try_fast_ipc_dispatch() -> bool:
    """
    Zero-Import Ultra-Low Latency (<10ms) Win32 Named Pipe Client.
    Executes before ANY heavy modules (PySide6, GUI, SQLite, etc.) are imported.
    When invoked via desktop/dock shortcut (--toggle) or hotkey, connects directly
    to the active server pipe and exits in under 80ms total process execution time,
    completely eliminating the cold-boot blank gap.
    """
    if "--force-start" in sys.argv:
        return False
    cmd = _determine_ipc_command(sys.argv)
    return _write_win32_ipc_pipe(cmd, wait_ms=1500, max_attempts=3)

if __name__ == "__main__" and _try_fast_ipc_dispatch():
    sys.exit(0)

from PySide6.QtCore import Qt, QTimer, QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtNetwork import QLocalServer, QLocalSocket

import stacky  # 把 stacky/* 子目录加入 sys.path，保持扁平导入可用
from stacky.paths import ROOT, get_user_cache_dir

from config_manager import ConfigManager
import ui_kit
from ui_kit import apply_global_style
from main_window import BigFolderWindow, BigFolderWindowManager
from shortcut_utils import generate_mydock_shortcut
from autostart_utils import clean_legacy_autostart

class TaskbarCreatedFilter(QAbstractNativeEventFilter):
    """
    Listens for the Windows shell broadcast message RegisterWindowMessage('TaskbarCreated').
    When explorer.exe restarts or the taskbar initializes after boot, automatically restores
    the system tray icon so it never permanently disappears.
    """
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        try:
            self.msg_id = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
        except Exception:
            self.msg_id = 0

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                from ctypes import wintypes
                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND),
                        ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM),
                        ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD),
                        ("pt", wintypes.POINT)
                    ]
                msg = MSG.from_address(message.__int__())
                if msg.message == self.WM_QUERYENDSESSION:
                    return True, 1
                if msg.message == self.WM_ENDSESSION:
                    if msg.wParam:
                        inst = QApplication.instance()
                        if inst is not None:
                            QTimer.singleShot(0, inst.quit)
                    return True, 0
                if self.msg_id and msg.message == self.msg_id:
                    print("[系统托盘] 监听到 Windows Explorer TaskbarCreated 消息，正在自动恢复托盘图标...")
                    self.callback()
            except Exception:
                pass
        return False, 0

def create_tray_icon() -> QIcon:
    """
    Returns the official retina Stacky (灵叠) brand icon for System Tray and Application Window.
    """
    cache_dir = get_user_cache_dir()
    project_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [
        os.path.join(cache_dir, "app_icon.png"),
        os.path.join(cache_dir, "app_icon.ico"),
        os.path.join(project_dir, ".cache_icons", "app_icon.png"),
        os.path.join(project_dir, ".cache_icons", "app_icon.ico"),
        os.path.join(ROOT, ".cache_icons", "app_icon.png"),
        os.path.join(ROOT, ".cache_icons", "app_icon.ico"),
    ]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 500:
            return QIcon(c)

    try:
        from shortcut_utils import ensure_high_res_app_icon
        ico_base = os.path.dirname(cache_dir) if os.path.basename(cache_dir) == ".cache_icons" else project_dir
        ico_p = ensure_high_res_app_icon(ico_base)
        if os.path.exists(ico_p):
            return QIcon(ico_p)
    except Exception:
        pass

    # High-quality fallback vector icon
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(15, 23, 42))
    painter.setPen(QColor(56, 189, 248, 180))
    painter.drawRoundedRect(1, 1, 30, 30, 8, 8)
    painter.setBrush(QColor(56, 189, 248))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(7, 7, 8, 8, 2, 2)
    painter.setBrush(QColor(99, 102, 241))
    painter.drawRoundedRect(17, 7, 8, 8, 2, 2)
    painter.setBrush(QColor(16, 185, 129))
    painter.drawRoundedRect(7, 17, 8, 8, 2, 2)
    painter.setBrush(QColor(245, 158, 11))
    painter.drawRoundedRect(17, 17, 8, 8, 2, 2)
    painter.end()
    return QIcon(pm)

def send_ipc_command(command: str = "ACTIVATE", timeout_ms: int = 1500, retries: int = 2) -> bool:
    """Tries to connect to running instance and send IPC command cleanly without zombie pipes."""
    eff_timeout = max(200, int(timeout_ms))
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        if _write_win32_ipc_pipe(command, wait_ms=eff_timeout, max_attempts=2):
            return True
        if QApplication.instance() is not None:
            socket = QLocalSocket()
            socket.connectToServer(IPC_SERVER_NAME)
            if socket.waitForConnected(eff_timeout):
                socket.write(f"{command}\n".encode("utf-8"))
                socket.waitForBytesWritten(eff_timeout)
                socket.disconnectFromServer()
                if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
                    socket.waitForDisconnected(min(400, eff_timeout))
                return True
        if attempt + 1 < attempts:
            time.sleep(0.15)
    return False


def _terminate_hung_peer_instances() -> int:
    """Terminates hung older BigFolderLauncher.exe processes (pid != os.getpid()) before mutex takeover."""
    if sys.platform != "win32":
        return 0
    try:
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_TERMINATE = 0x0001
        PROCESS_SYNCHRONIZE = 0x00100000
        my_pid = os.getpid()
        target_names = {"bigfolderlauncher.exe"}
        if getattr(sys, "frozen", False):
            target_names.add(os.path.basename(sys.executable).lower())

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h_snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        invalid = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
        if h_snap in (None, 0, -1, invalid):
            return 0

        terminated = 0
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(h_snap, ctypes.byref(pe))
            while ok:
                pid = int(pe.th32ProcessID)
                exe_name = (pe.szExeFile or "").strip().lower()
                if pid and pid != my_pid and exe_name in target_names:
                    h_proc = k32.OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, False, pid)
                    if h_proc:
                        try:
                            if k32.TerminateProcess(h_proc, 1):
                                k32.WaitForSingleObject(h_proc, 1500)
                                terminated += 1
                        finally:
                            k32.CloseHandle(h_proc)
                ok = k32.Process32NextW(h_snap, ctypes.byref(pe))
        finally:
            k32.CloseHandle(h_snap)
        return terminated
    except Exception:
        return 0


def main():
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    ERROR_ALREADY_EXISTS = 183
    is_quit = "--quit" in sys.argv[1:]

    # If --quit was passed and no instance holds the mutex, exit 0 immediately without launching GUI
    if is_quit and last_error != ERROR_ALREADY_EXISTS:
        if mutex:
            kernel32.CloseHandle(mutex)
        sys.exit(0)

    # If another instance is already running
    if last_error == ERROR_ALREADY_EXISTS:
        if is_quit:
            if not send_ipc_command("QUIT") and getattr(sys, "frozen", False):
                _terminate_hung_peer_instances()
            if mutex:
                kernel32.CloseHandle(mutex)
            sys.exit(0)
        cmd = _determine_ipc_command(sys.argv)
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        if send_ipc_command(cmd):
            print("[Stacky] 已唤醒运行中的实例")
            if mutex:
                kernel32.CloseHandle(mutex)
            sys.exit(0)
        else:
            print("[Stacky] 检测到旧实例无响应，正在清理残留进程并接管启动...")
            if mutex:
                kernel32.CloseHandle(mutex)
            if getattr(sys, "frozen", False):
                _terminate_hung_peer_instances()
            mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)

    # Prevent any child winId() call from promoting sibling widgets (e.g. BigFolderCard) to native WS_CHILD HWNDs
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    apply_global_style(app)

    # Clean up any dead socket from prior crash
    QLocalServer.removeServer(IPC_SERVER_NAME)
    ipc_server = QLocalServer()
    if not ipc_server.listen(IPC_SERVER_NAME):
        print("Warning: Could not bind IPC server:", ipc_server.errorString())

    config_mgr = ConfigManager()
    window_mgr = BigFolderWindowManager(config_mgr)
    window_mgr.start()

    # Auto-heal shortcuts and purge legacy autostart on startup
    try:
        clean_legacy_autostart()
    except Exception:
        pass
    try:
        generate_mydock_shortcut(heal_only=True)
    except Exception:
        pass

    # Handle incoming IPC messages from Dock launcher clicks or second instance launches
    def handle_ipc_connection():
        while ipc_server.hasPendingConnections():
            client = ipc_server.nextPendingConnection()
            if not client:
                continue
            client.readyRead.connect(lambda c=client: on_ipc_read(c))
            client.disconnected.connect(client.deleteLater)
            if client.bytesAvailable() > 0:
                on_ipc_read(client)

    def on_ipc_read(client: QLocalSocket):
        try:
            raw = client.readAll().data().decode("utf-8", errors="ignore").strip()
            if "QUIT" in raw:
                window_mgr.close_all()
                app.quit()
            elif "TOGGLE" in raw:
                window_mgr.toggle_summon()
            elif "ZEN" in raw:
                window_mgr.toggle_zen_mode()
            elif raw.startswith("SUMMON:"):
                target_f = raw.split(":", 1)[1]
                window_mgr.toggle_summon(folder_name=target_f)
            elif "AUTOSTART" in raw:
                window_mgr.notify_ready("")
            else:
                window_mgr.bring_to_front_and_toast("")
        except Exception as e:
            print("Error handling IPC payload:", e)
        finally:
            try:
                client.disconnectFromServer()
            except Exception:
                pass

    ipc_server.newConnection.connect(handle_ipc_connection)

    # Cold start launch behavior:
    # If launched on boot via --autostart: keep cleanly on desktop wallpaper bottom, zero focus stealing
    if "--autostart" in sys.argv:
        window_mgr.notify_ready("")
    elif "--toggle" in sys.argv:
        window_mgr.toggle_summon()
    else:
        # User manually started app or clicked desktop shortcut: present cleanly without startup toast
        window_mgr.bring_to_front_and_toast("")

    # System Tray Icon & Window Icon
    app_brand_icon = create_tray_icon()
    app.setWindowIcon(app_brand_icon)

    tray = QSystemTrayIcon(app_brand_icon, app)
    tray.setToolTip("Stacky 灵叠")

    tray_menu = QMenu()
    tray_menu.setStyleSheet("""
        QMenu {
            background-color: rgba(28, 30, 36, 248);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 35);
            border-radius: 10px;
            padding: 6px;
            font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI";
            font-size: 13px;
        }
        QMenu::item {
            padding: 6px 20px;
            border-radius: 5px;
        }
        QMenu::item:selected {
            background-color: rgba(255, 255, 255, 45);
        }
        QMenu::separator {
            height: 1px;
            background-color: rgba(255, 255, 255, 25);
            margin: 4px 6px;
        }
    """)
    act_summon = tray_menu.addAction("呼出")
    act_summon.triggered.connect(window_mgr.toggle_summon)

    act_bottom = tray_menu.addAction("恢复底层")
    act_bottom.triggered.connect(lambda: window_mgr.set_all_layer_mode("bottom"))

    act_zen = tray_menu.addAction("隐藏/显示卡片")
    act_zen.triggered.connect(lambda: window_mgr.toggle_zen_mode(animate=True))

    act_grid = tray_menu.addAction("对齐小组件网格")
    act_grid.triggered.connect(lambda: window_mgr.align_widgets_to_grid())

    act_shelf = tray_menu.addAction("文件中转站")
    act_shelf.setCheckable(True)
    act_shelf.setChecked(bool(config_mgr.get_file_shelf_config().get("enabled", True)))
    act_shelf.triggered.connect(window_mgr.toggle_file_shelf)

    def _refresh_tray_menu_state():
        act_shelf.setChecked(bool(config_mgr.get_file_shelf_config().get("enabled", True)))
        if hasattr(ui_kit, "menu_qss"):
            tray_menu.setStyleSheet(ui_kit.menu_qss(config_mgr.get("theme", "dark")))

    _refresh_tray_menu_state()
    tray_menu.aboutToShow.connect(_refresh_tray_menu_state)

    tray_menu.addSeparator()

    act_settings = tray_menu.addAction("设置")
    act_settings.triggered.connect(lambda: window_mgr.open_settings_dialog(0))

    tray_menu.addSeparator()

    act_exit = tray_menu.addAction("退出")
    act_exit.triggered.connect(app.quit)

    tray.setContextMenu(tray_menu)
    tray.activated.connect(lambda reason: window_mgr.toggle_summon() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    # Retain strong global references to prevent garbage collection
    app._tray = tray
    app._tray_menu = tray_menu

    # Install Explorer taskbar restart listener
    def on_taskbar_recreated():
        try:
            print("[系统托盘] 正在重新注册托盘图标...")
            tray.hide()
            tray.show()
        except Exception as e:
            print("[系统托盘] 重载托盘图标失败:", e)

    native_filter = TaskbarCreatedFilter(on_taskbar_recreated)
    app.installNativeEventFilter(native_filter)
    app._native_filter = native_filter

    # Early-boot retry guard: if notification area is not ready yet during boot, retry until shown
    def ensure_tray_visible(retries=10):
        if not tray.isVisible():
            tray.show()
        if not tray.isVisible() and retries > 0:
            QTimer.singleShot(1500, lambda: ensure_tray_visible(retries - 1))

    ensure_tray_visible()

    exit_code = app.exec()

    window_mgr.close_all()
    for mod_name, cls_name in (("media_service", "MediaControlService"), ("stacky.media.media_service", "MediaControlService")):
        mod = sys.modules.get(mod_name)
        cls = getattr(mod, cls_name, None) if mod is not None else None
        inst = getattr(cls, "_instance", None) if cls is not None else None
        if inst is not None:
            for fn_name in ("shutdown", "stop"):
                fn = getattr(inst, fn_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
                    break
    if mutex:
        kernel32.CloseHandle(mutex)
    ipc_server.close()
    QLocalServer.removeServer(IPC_SERVER_NAME)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
