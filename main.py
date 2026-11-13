import sys
import os
import time
import ctypes
import traceback

def _global_exception_handler(exctype, value, tb):
    err_str = "".join(traceback.format_exception(exctype, value, tb))
    print("Unhandled Exception:", err_str)
    try:
        log_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        log_file = os.path.join(log_dir, "BigFolderLauncher_crash.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Unhandled Exception:\n{err_str}\n")
    except Exception:
        pass
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = _global_exception_handler

IPC_SERVER_NAME = "BigFolder_Desktop_Launcher_IPC"
MUTEX_NAME = "Global\\BigFolderDesktopLauncher_SingleInstance"

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

    pipe_path = f"\\\\.\\pipe\\{IPC_SERVER_NAME}"
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80

    h_pipe = ctypes.windll.kernel32.CreateFileW(
        pipe_path,
        GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None
    )
    if h_pipe == -1 or h_pipe == 0xFFFFFFFFFFFFFFFF:
        return False

    cmd = "ACTIVATE"
    for arg in sys.argv[1:]:
        if arg == "--autostart":
            cmd = "AUTOSTART"
            break
        elif arg == "--toggle":
            cmd = "TOGGLE"
            break
        elif arg.startswith("--summon:"):
            cmd = "SUMMON:" + arg.split(":", 1)[1]
            break
        elif arg in ("--activate", "--show"):
            cmd = "ACTIVATE"
            break

    try:
        from ctypes import wintypes
        try:
            # Grant foreground privilege to the running background server
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        data = (cmd + "\n").encode("utf-8")
        written = wintypes.DWORD()
        res = ctypes.windll.kernel32.WriteFile(
            h_pipe,
            data,
            len(data),
            ctypes.byref(written),
            None
        )
        ctypes.windll.kernel32.FlushFileBuffers(h_pipe)
        return bool(res)
    finally:
        ctypes.windll.kernel32.CloseHandle(h_pipe)

if __name__ == "__main__" and _try_fast_ipc_dispatch():
    sys.exit(0)

from PySide6.QtCore import Qt, QTimer, QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtNetwork import QLocalServer, QLocalSocket

import stacky  # 把 stacky/* 子目录加入 sys.path，保持扁平导入可用

from config_manager import ConfigManager
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

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG" and self.msg_id:
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
                if msg.message == self.msg_id:
                    print("[系统托盘] 监听到 Windows Explorer TaskbarCreated 消息，正在自动恢复托盘图标...")
                    self.callback()
            except Exception:
                pass
        return False, 0

def create_tray_icon() -> QIcon:
    """
    Returns the official retina Stacky (灵叠) brand icon for System Tray and Application Window.
    """
    project_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    candidates = [
        os.path.join(project_dir, ".cache_icons", "app_icon.png"),
        os.path.join(project_dir, ".cache_icons", "app_icon.ico"),
        os.path.join(r"d:\docker", ".cache_icons", "app_icon.png"),
        os.path.join(r"d:\docker", ".cache_icons", "app_icon.ico"),
    ]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 500:
            return QIcon(c)

    try:
        from shortcut_utils import ensure_high_res_app_icon
        ico_p = ensure_high_res_app_icon(project_dir)
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

def send_ipc_command(command: str = "ACTIVATE", timeout_ms: int = 400) -> bool:
    """Tries to connect to running instance and send IPC command cleanly without zombie pipes"""
    socket = QLocalSocket()
    socket.connectToServer(IPC_SERVER_NAME)
    if socket.waitForConnected(timeout_ms):
        socket.write(f"{command}\n".encode("utf-8"))
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        if socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            socket.waitForDisconnected(timeout_ms)
        return True
    return False

def main():
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    ERROR_ALREADY_EXISTS = 183

    # If another instance is already running
    if last_error == ERROR_ALREADY_EXISTS:
        cmd = "ACTIVATE" if "--autostart" in sys.argv else "TOGGLE"
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        if send_ipc_command(cmd):
            print("[提示] Stacky (灵叠) 已在后台运行中，已为您唤醒前台窗口！")
            sys.exit(0)
        else:
            print("[提示] 检测到旧实例残留但无响应，正在清理并接管启动...")
            kernel32.CloseHandle(mutex)
            mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)

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
            if "TOGGLE" in raw:
                window_mgr.toggle_summon()
            elif "ZEN" in raw:
                window_mgr.toggle_zen_mode()
            elif raw.startswith("SUMMON:"):
                target_f = raw.split(":", 1)[1]
                window_mgr.toggle_summon(folder_name=target_f)
            else:
                # 用户二次启动快捷方式/图标：主动唤醒并呈现大文件夹，告别静默淹没
                window_mgr.bring_to_front_and_toast("Stacky 灵叠已就绪")
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
        window_mgr.notify_ready("Stacky 灵叠已就绪！")
    elif "--toggle" in sys.argv:
        window_mgr.toggle_summon()
    else:
        # User manually started app or clicked desktop shortcut: reliably present front and center!
        window_mgr.bring_to_front_and_toast("Stacky 灵叠已就绪！")

    # System Tray Icon & Window Icon
    app_brand_icon = create_tray_icon()
    app.setWindowIcon(app_brand_icon)

    tray = QSystemTrayIcon(app_brand_icon, app)
    tray.setToolTip("Stacky 灵叠 · 现代桌面视窗矩阵与流体启动台")

    tray_menu = QMenu()
    tray_menu.setStyleSheet("""
        QMenu {
            background-color: rgba(28, 30, 36, 248);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 35);
            border-radius: 10px;
            padding: 6px;
            font-family: "Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei";
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
    tray_menu.aboutToShow.connect(
        lambda: act_shelf.setChecked(bool(config_mgr.get_file_shelf_config().get("enabled", True)))
    )

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
    if mutex:
        kernel32.CloseHandle(mutex)
    ipc_server.close()
    QLocalServer.removeServer(IPC_SERVER_NAME)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
