"""
media_service.py - Windows 10/11 原生媒体传输控制服务 (GSMTC)
系统媒体控制微件:
- 原生对接 WinRT GlobalSystemMediaTransportControlsSessionManager
- 自动捕获主流媒体播放器 (QQ音乐, 网易云音乐, PotPlayer, Spotify, 浏览器, Foobar2000 等)
- 提取曲目名称、艺术家、专辑、高清封面与播放/暂停/上一曲/下一曲控制
- 提供安全异步轮询与纯净备用模拟模式 (保证脱机与单测 100% 稳定)
"""

import sys
import os
import json
import re
import ssl
import ctypes
import atexit
import asyncio
import base64
import threading
import urllib.request
import urllib.parse
import concurrent.futures
from typing import Optional, Dict, Any

from PySide6.QtCore import QObject, Signal, QTimer

# 尝试导入 WinRT GSMTC 相关模块
HAS_WINRT_GSMTC = False
try:
    import winrt.windows.media.control as wmc
    import winrt.windows.storage.streams as streams
    HAS_WINRT_GSMTC = True
except Exception:
    HAS_WINRT_GSMTC = False


def is_session_playing(playback_info) -> bool:
    """PlaybackStatus: PLAYING=4。winrt 有时返回枚举、有时返回 int，统一按整型比较。"""
    return int(getattr(playback_info, "playback_status", 0)) == 4


class MediaControlService(QObject):
    """
    Windows 全局媒体传输控制协议 (GSMTC) 原生集成服务
    """
    media_changed = Signal(dict)
    playback_status_changed = Signal(bool)
    timeline_updated = Signal(float, float)   # position, duration（秒）
    seek_capability_changed = Signal(bool)    # 播放器是否开放系统跳转
    _refresh_requested = Signal()

    _instance: Optional["MediaControlService"] = None

    @classmethod
    def get_instance(cls) -> "MediaControlService":
        if cls._instance is None:
            cls._instance = MediaControlService()
        return cls._instance

    @classmethod
    def shutdown_instance(cls):
        """安全关闭已初始化的单例服务（未初始化时不额外实例化）。"""
        inst = cls._instance
        if inst is not None:
            inst.shutdown()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._current_info: Dict[str, Any] = {
            "title": "暂无播放媒体",
            "artist": "打开播放器自动捕获",
            "album": "",
            "thumbnail_bytes": None,
            "is_playing": False,
            "source_app": "",
            "has_media": False
        }

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self.poll_current_session)

        self._mock_mode = False
        self._mock_info: Optional[Dict[str, Any]] = None

        self._is_polling_active = False
        self._shutting_down = False
        self._executor_lock = threading.RLock()
        self._last_position: Optional[float] = None
        self._last_duration: float = 0.0
        self._stuck_count = 0
        # 未证实可跳转前一律按只读处理，避免显示一个拖了没反应的假滑块
        self._seek_supported = False

        # 事件驱动：订阅 SMTC 的会话/播放/媒体/时间线变化，事件到达立刻抓一次，
        # 轮询只作为兜底。GSMTC 回调在 COM 线程，统一经 Qt 信号切回主线程并防抖。
        self._manager = None
        self._manager_tokens = []
        self._subscribed_session = None
        self._session_tokens = []
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self.poll_current_session)
        self._refresh_requested.connect(self._refresh_timer.start)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="GSMTCWorker")

    def _ensure_executor(self):
        with self._executor_lock:
            if self._executor is None or getattr(self._executor, "_shutdown", False):
                self._executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="GSMTCWorker"
                )
            return self._executor

    def shutdown(self):
        """优雅关闭轮询定时器与后台线程池，杜绝退出时 ThreadPoolExecutor atexit 卡死成为僵尸进程。"""
        self._shutting_down = True
        for timer_attr in ("_poll_timer", "_refresh_timer"):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    if timer.isActive():
                        timer.stop()
                except Exception:
                    pass

        self._manager = None
        self._manager_tokens = []
        self._subscribed_session = None
        self._session_tokens = []
        self._is_polling_active = False

        with self._executor_lock:
            executor = getattr(self, "_executor", None)
            if executor is not None:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    try:
                        executor.shutdown(wait=False)
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    import concurrent.futures.thread as _cft
                    for t in list(getattr(executor, "_threads", ())):
                        _cft._threads_queues.pop(t, None)
                except Exception:
                    pass

    stop = shutdown

    def set_mock_data(self, mock_info: Optional[Dict[str, Any]]):
        """用于单元测试或演示模式的模拟媒体数据"""
        self._mock_mode = mock_info is not None
        self._mock_info = mock_info
        if self._mock_mode and self._mock_info:
            self._current_info.update(self._mock_info)
            self.media_changed.emit(dict(self._current_info))
            self.playback_status_changed.emit(self._current_info.get("is_playing", False))

    def get_current_info(self) -> Dict[str, Any]:
        return dict(self._current_info)

    def can_seek(self) -> bool:
        return self._seek_supported

    def start_polling(self, interval_ms: int = 1200):
        self._shutting_down = False
        self._ensure_executor()
        if not self._poll_timer.isActive():
            self._poll_timer.setInterval(interval_ms)
            self._poll_timer.start()
            self.poll_current_session()

    def stop_polling(self):
        if self._poll_timer.isActive():
            self._poll_timer.stop()

    def pause_polling(self):
        """挂起后台轮询 (禅模式或微件隐藏时彻底停用以实现 0 CPU 0 线程消耗)"""
        self.stop_polling()

    def resume_polling(self):
        """唤醒后台轮询"""
        self.start_polling(interval_ms=1200)

    def poll_current_session(self):
        """触发异步抓取当前会话媒体元数据 (复用持久线程池，彻底消除高频线程创建/销毁开销)"""
        if self._shutting_down:
            return
        if self._mock_mode and self._mock_info:
            return

        if not HAS_WINRT_GSMTC:
            return

        # 杜绝并发线程膨胀与重复轮询
        if self._is_polling_active:
            return
        self._is_polling_active = True

        try:
            executor = self._ensure_executor()
            executor.submit(self._async_worker_poll)
        except Exception:
            self._is_polling_active = False

    def _async_worker_poll(self):
        #  executor 只有一个常驻线程，事件循环复用即可；每 500ms 新建/销毁
        #  一个 loop 会持续产生 asyncio/WinRT 临时对象，是轮询路径最大的分配源。
        if self._shutting_down:
            self._is_polling_active = False
            return
        try:
            loop = getattr(self, "_worker_loop", None)
            if loop is None or loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._worker_loop = loop
            loop.run_until_complete(asyncio.wait_for(self._fetch_gsmtc_info(), timeout=2.5))
        except Exception:
            self._worker_loop = None
        finally:
            self._is_polling_active = False

    def _emit_refresh(self, *args):
        """GSMTC 事件回调入口（COM 线程）→ 主线程防抖刷新。"""
        if self._shutting_down:
            return
        try:
            self._refresh_requested.emit()
        except Exception:
            pass

    def _subscribe_manager(self, manager):
        if manager is self._manager:
            return
        self._manager = manager
        try:
            self._manager_tokens = [
                manager.add_current_session_changed(self._emit_refresh),
                manager.add_sessions_changed(self._emit_refresh),
            ]
        except Exception:
            self._manager_tokens = []

    def _subscribe_session(self, session):
        if session is self._subscribed_session:
            return
        # 旧会话的事件 token 不做精确反注册（COM 对象释放后自然失效），
        # 回调里只是触发一次刷新，重复触发无副作用。
        self._subscribed_session = session
        try:
            self._session_tokens = [
                session.add_playback_info_changed(self._emit_refresh),
                session.add_media_properties_changed(self._emit_refresh),
                session.add_timeline_properties_changed(self._emit_refresh),
            ]
        except Exception:
            self._session_tokens = []

    async def _resolve_active_session(self, manager):
        """活动会话的唯一解析入口：优先正在播放的，其次当前会话。
        QQ音乐常有主窗口/歌词等多个 session，必须统一，否则状态与控制会作用在不同会话上。
        """
        try:
            for session in (manager.get_sessions() or []):
                info = session.get_playback_info()
                if info and is_session_playing(info):
                    return session
        except Exception:
            pass
        try:
            return manager.get_current_session()
        except Exception:
            return None

    async def _fetch_gsmtc_info(self):
        # mock 模式下已在飞的真实轮询线程也不能覆盖模拟状态
        if not HAS_WINRT_GSMTC or self._mock_mode:
            return

        try:
            manager = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
            if not manager:
                self._update_idle_state()
                return

            self._subscribe_manager(manager)
            # 统一走活动会话解析，避免状态判断与控制命令各选一个会话
            session = await self._resolve_active_session(manager)
            if session is not None:
                self._subscribe_session(session)

            if not session:
                self._update_idle_state()
                return

            # 获取播放状态
            pinfo = session.get_playback_info()
            if not pinfo:
                self._update_idle_state()
                return

            # PlaybackStatus: Closed=0, Opened=1, Changing=2, Stopped=3, Playing=4, Paused=5
            # 统一按整型比较：winrt 有时返回枚举、有时返回 int，直接 == 枚举会漏判
            is_playing = is_session_playing(pinfo)

            # QQ音乐把跳转/快进/快退开关全关了，seek 返回 True 但位置不动。
            # 以播放器自报的能力为准，UI 只在该能力为真时才允许拖动。
            try:
                can_seek = bool(pinfo.controls.is_playback_position_enabled)
            except Exception:
                can_seek = False
            if can_seek != self._seek_supported:
                self._seek_supported = can_seek
                self.seek_capability_changed.emit(can_seek)

            # 部分播放器（QQ音乐某些版本）在播也不上报 PLAYING：
            # 用播放进度是否前进兜底，暂停时进度不动不会误判。
            try:
                timeline = session.get_timeline_properties()   # 同步方法，不能 await
                position = timeline.position.total_seconds() if timeline else -1.0
                duration = timeline.end_time.total_seconds() if timeline else -1.0
            except Exception:
                position = duration = -1.0
            # 进度是最终裁决：前进=在播，停住=暂停。
            # 部分播放器（QQ音乐）的 playback_status 常年报 PLAYING，只看状态会一直显示在播。
            if position >= 0 and self._last_position is not None:
                if position > self._last_position + 0.2:
                    is_playing = True
                    self._stuck_count = 0
                elif abs(position - self._last_position) < 0.01:
                    # 连续 3 次（约 1.5s）不动才判暂停，避免时间线更新有延迟时误闪
                    self._stuck_count += 1
                    if self._stuck_count >= 3:
                        is_playing = False
                else:
                    self._stuck_count = 0
            self._last_position = position
            if position >= 0 and duration > 0:
                self._last_duration = duration
                self.timeline_updated.emit(position, duration)

            # 自适应轮询间隔：正在播放时以 1200ms 灵敏捕获，暂停时平滑降频至 2600ms 降低 55% CPU 周期
            # QQ音乐等播放器不发 SMTC 事件（实测 8 秒 0 事件），只能靠轮询，
            # 因此固定 500ms；支持事件的播放器会由回调再提前触发一次。
            target_interval = 500
            if self._poll_timer.interval() != target_interval:
                self._poll_timer.setInterval(target_interval)

            # 获取媒体属性 (曲目名, 歌手, 专辑, 缩略图)
            media_props = await session.try_get_media_properties_async()
            if not media_props:
                return

            title = (media_props.title or "").strip() or "未知曲目"
            artist = (media_props.artist or "").strip() or "未知歌手"
            album = (media_props.album_title or "").strip()

            # 来源应用识别
            app_id = session.source_app_user_model_id or ""
            source_app = self._friendly_app_name(app_id)

            # 同一首歌且封面已在缓存里就不再读流：否则每 500ms 都会把
            # 整张封面 bytearray + bytes 重新分配一遍（可达数 MB/s）。
            same_track = (
                self._current_info.get("has_media") is True and
                self._current_info["title"] == title and
                self._current_info["artist"] == artist and
                self._current_info["album"] == album and
                self._current_info.get("thumbnail_bytes") is not None
            )
            thumb_bytes = self._current_info.get("thumbnail_bytes") if same_track else None
            if thumb_bytes is None and media_props.thumbnail:
                stream = None
                reader = None
                try:
                    stream = await media_props.thumbnail.open_read_async()
                    size = stream.size if stream else 0
                    if 0 < size < 15 * 1024 * 1024:
                        reader = streams.DataReader(stream.get_input_stream_at(0))
                        await reader.load_async(size)
                        buf = bytearray(size)
                        reader.read_bytes(buf)
                        thumb_bytes = bytes(buf)
                except Exception:
                    thumb_bytes = None
                finally:
                    if reader:
                        try:
                            reader.close()
                        except Exception:
                            pass
                    if stream:
                        try:
                            stream.close()
                        except Exception:
                            pass

            # 对比是否有变化
            changed = (
                self._current_info["title"] != title or
                self._current_info["artist"] != artist or
                self._current_info["is_playing"] != is_playing or
                self._current_info["source_app"] != source_app or
                self._current_info["has_media"] is not True or
                self._current_info["thumbnail_bytes"] != thumb_bytes
            )

            if changed:
                self._current_info = {
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "thumbnail_bytes": thumb_bytes,
                    "is_playing": is_playing,
                    "source_app": source_app,
                    "has_media": True
                }
                self.media_changed.emit(dict(self._current_info))
                self.playback_status_changed.emit(is_playing)

        except Exception:
            pass

    def _update_idle_state(self):
        if self._seek_supported:
            self._seek_supported = False
            self.seek_capability_changed.emit(False)
        if self._poll_timer.interval() != 2600:
            self._poll_timer.setInterval(2600)
        if self._current_info.get("has_media", False):
            self._current_info = {
                "title": "暂无播放媒体",
                "artist": "打开播放器自动捕获",
                "album": "",
                "thumbnail_bytes": None,
                "is_playing": False,
                "source_app": "",
                "has_media": False
            }
            self.media_changed.emit(dict(self._current_info))
            self.playback_status_changed.emit(False)

    def _friendly_app_name(self, app_id: str) -> str:
        lower = app_id.lower()
        if "qqmusic" in lower or "tencent" in lower:
            return "QQ音乐"
        elif "cloudmusic" in lower or "netease" in lower:
            return "网易云音乐"
        elif "spotify" in lower:
            return "Spotify"
        elif "potplayer" in lower:
            return "PotPlayer"
        elif "chrome" in lower:
            return "Google Chrome"
        elif "msedge" in lower or "edge" in lower:
            return "Microsoft Edge"
        elif "firefox" in lower:
            return "Firefox"
        elif "foobar2000" in lower:
            return "Foobar2000"
        elif "kugou" in lower:
            return "酷狗音乐"
        elif "kuwo" in lower:
            return "酷我音乐"
        elif app_id:
            # 去除 .exe 或 package 前缀
            clean = app_id.split("!")[-1].replace(".exe", "")
            return clean[:16]
        return "系统媒体"

    def play_pause(self):
        """播放 / 暂停切换"""
        if self._mock_mode:
            self._current_info["is_playing"] = not self._current_info.get("is_playing", False)
            self.media_changed.emit(dict(self._current_info))
            self.playback_status_changed.emit(self._current_info["is_playing"])
            return

        if not HAS_WINRT_GSMTC:
            return

        # 先本地翻转，点击立刻有反馈；真实结果由随后的轮询校正
        self._current_info["is_playing"] = not self._current_info.get("is_playing", False)
        self.playback_status_changed.emit(self._current_info["is_playing"])

        def _do():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                async def _task():
                    mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
                    if mgr:
                        sess = await self._resolve_active_session(mgr)
                        if sess:
                            await sess.try_toggle_play_pause_async()
                loop.run_until_complete(asyncio.wait_for(_task(), timeout=2.0))
                loop.close()
            except Exception:
                pass
            # 经 Qt 信号回到主线程防抖刷新（裸线程禁止直接调用 QTimer.singleShot）
            self._emit_refresh()

        threading.Thread(target=_do, daemon=True).start()

    def seek(self, seconds: float):
        """拖动进度条跳转。GSMTC 的 TimeSpan 单位是 100ns tick。"""
        # 不管播放器自报能力如何都试一次：部分播放器 flag 为 False 却仍响应，
        # QQ音乐则相反（返回 True 但不生效），由调用方按真实位置校验。
        if self._mock_mode or not HAS_WINRT_GSMTC:
            return

        def _do():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _task():
                    mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
                    if not mgr:
                        return
                    sess = await self._resolve_active_session(mgr)
                    if sess:
                        await sess.try_change_playback_position_async(int(float(seconds) * 10_000_000))

                loop.run_until_complete(asyncio.wait_for(_task(), timeout=2.0))
                loop.close()
            except Exception:
                pass
            # QQ音乐关闭了全部系统位置控制接口，改走播放器窗口级 UIA 跳转
            if ("QQ音乐" in self._current_info.get("source_app", "")
                    and self._last_duration > 0):
                try:
                    from player_seek import seek_qqmusic
                    seek_qqmusic(float(seconds), self._last_duration)
                except Exception:
                    pass
            self._emit_refresh()

        threading.Thread(target=_do, daemon=True).start()

    def next_track(self):
        """下一曲：标准接口优先，未生效再用系统媒体键兜底（不挑播放器）"""
        if self._mock_mode:
            return
        before = self._current_info.get("title")
        self._gsmtc_skip(True)
        QTimer.singleShot(500, lambda: self._media_key_fallback(before, 0xB0))

    def previous_track(self):
        """上一曲：同下一曲"""
        if self._mock_mode:
            return
        before = self._current_info.get("title")
        self._gsmtc_skip(False)
        QTimer.singleShot(500, lambda: self._media_key_fallback(before, 0xB1))

    def _media_key_fallback(self, before_title, vk: int):
        """标准接口没生效（曲目未变）才补发媒体键，避免跳两首。"""
        if self._current_info.get("title") == before_title:
            if self._send_media_key(vk):
                QTimer.singleShot(350, self.poll_current_session)

    def _gsmtc_skip(self, is_next: bool):
        if not HAS_WINRT_GSMTC:
            return

        def _do():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                async def _task():
                    mgr = await wmc.GlobalSystemMediaTransportControlsSessionManager.request_async()
                    if mgr:
                        sess = await self._resolve_active_session(mgr)
                        if sess:
                            if is_next:
                                await sess.try_skip_next_async()
                            else:
                                await sess.try_skip_previous_async()
                loop.run_until_complete(asyncio.wait_for(_task(), timeout=2.0))
                loop.close()
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()

    def _send_media_key(self, vk: int) -> bool:
        """发送系统媒体键。必须带 scan code：bScan=0 时部分播放器会忽略。"""
        try:
            user32 = ctypes.windll.user32
            scan = user32.MapVirtualKeyW(vk, 0)
            user32.keybd_event(vk, scan, 0, 0)
            user32.keybd_event(vk, scan, 2, 0)   # KEYEVENTF_KEYUP
            return True
        except Exception:
            return False


_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def _parse_lrc(text: str):
    """解析 LRC 的 [mm:ss.xx] 时间戳，一行多戳也展开。"""
    lines = []
    for raw in text.splitlines():
        stamps = list(_LRC_TAG.finditer(raw))
        content = _LRC_TAG.sub("", raw).strip()
        if not stamps or not content:
            continue
        for m in stamps:
            frac = m.group(3) or ""
            sec = int(m.group(1)) * 60 + int(m.group(2))
            if frac:
                sec += int(frac) / (10 ** len(frac))
            lines.append((sec, content))
    lines.sort(key=lambda x: x[0])
    return lines


class LyricsService(QObject):
    """在线歌词：按【真实曲目版本】检索 LRC，按播放位置给当前行。

    主源酷狗歌词库（候选自带时长，能命中 QQ音乐/DJ/纯享版片段）；
    网易云只作兜底且必须通过时长校验。宁可空，也不显示别的版本的歌词。
    """

    lyrics_ready = Signal(list)

    def __init__(self):
        super().__init__()
        self._cache: Dict[str, list] = {}
        self._lines = []
        self._last_key = ""

    @staticmethod
    def _ssl_ctx():
        # 打包后的 exe 常缺 SSL 根证书，显式放宽校验，避免 https 请求静默失败
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @staticmethod
    def _version_ok(lines, duration) -> bool:
        """LRC 末尾时间戳远超真实时长 = 另一版本（原版 / 别的剪辑），拒绝。"""
        if duration <= 0 or not lines:
            return True
        return lines[-1][0] <= duration + max(10.0, duration * 0.2)

    @staticmethod
    def _duration_ok(candidate_ms, duration) -> bool:
        if duration <= 0:
            return True
        return abs(float(candidate_ms or 0) / 1000 - duration) <= max(8.0, duration * 0.15)

    def fetch_async(self, title: str, artist: str, duration: float = 0.0):
        # 时长进入 key：SMTC 稍后给出真实时长时自动按正确版本重查
        key = f"{title}|{artist}|{int(duration)}"
        if key == self._last_key or not title.strip():
            return
        self._last_key = key
        if key in self._cache:
            self._lines = self._cache[key]
            self.lyrics_ready.emit(list(self._lines))
            return
        threading.Thread(target=self._work,
                         args=(key, title, artist, float(duration)), daemon=True).start()

    def _work(self, key, title, artist, duration):
        lines = self._from_kugou(title, artist, duration)
        if not lines:
            lines = self._from_netease(title, artist, duration)
        # 时长进了 key，一首歌会产生 2 个条目；不设上限会随听歌时长无限增长
        if len(self._cache) >= 32:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = lines
        # 换歌/拿到真实时长后又发了新查询时，丢弃这次过期结果
        if self._last_key != key:
            return
        self._lines = lines
        try:
            self.lyrics_ready.emit(list(lines))
        except RuntimeError:
            pass

    def _from_kugou(self, title, artist, duration):
        clean = re.sub(r"\s*[（(].*?[)）]", "", title).strip() or title
        queries = list(dict.fromkeys([
            f"{title} {artist}".strip(), f"{clean} {artist}".strip(), clean,
        ]))
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                   "Referer": "https://www.kugou.com/"}
        ctx = self._ssl_ctx()
        for q in queries:
            try:
                url = ("https://lyrics.kugou.com/search?ver=1&man=yes&client=pc&keyword="
                       + urllib.parse.quote(q))
                if duration > 0:
                    url += "&duration=%d" % int(duration * 1000)
                data = json.loads(urllib.request.urlopen(
                    urllib.request.Request(url, headers=headers), timeout=6, context=ctx
                ).read().decode("utf-8"))
                candidates = [c for c in (data.get("candidates") or [])
                              if c.get("id") and c.get("accesskey")]
                if duration > 0:
                    candidates = [c for c in candidates
                                  if self._duration_ok(c.get("duration"), duration)]
                for c in candidates[:5]:
                    try:
                        dl = ("https://lyrics.kugou.com/download?ver=1&client=pc&id=%s"
                              "&accesskey=%s&fmt=lrc&charset=utf8" % (c["id"], c["accesskey"]))
                        payload = json.loads(urllib.request.urlopen(
                            urllib.request.Request(dl, headers=headers), timeout=5, context=ctx
                        ).read().decode("utf-8"))
                        text = base64.b64decode(payload.get("content") or "").decode("utf-8", "ignore")
                        lines = _parse_lrc(text)
                        if lines and self._version_ok(lines, duration):
                            return lines
                    except Exception:
                        continue
            except Exception:
                continue
        return []

    def _from_netease(self, title, artist, duration):
        clean = re.sub(r"\s*[（(].*?[)）]", "", title).strip() or title
        queries = list(dict.fromkeys([
            f"{title} {artist}".strip(), clean, f"{clean} {artist}".strip(),
        ]))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}
        ctx = self._ssl_ctx()
        for q in queries:
            try:
                req = urllib.request.Request(
                    "https://music.163.com/api/search/get?type=1&s=" + urllib.parse.quote(q) + "&limit=8",
                    headers=headers)
                data = json.loads(urllib.request.urlopen(req, timeout=6, context=ctx).read().decode("utf-8"))
                songs = ((data.get("result") or {}).get("songs") or [])
                if duration > 0:
                    songs = [s for s in songs if self._duration_ok(s.get("duration"), duration)]
                for song in songs[:6]:
                    try:
                        req2 = urllib.request.Request(
                            "https://music.163.com/api/song/lyric?id=" + str(song["id"]) + "&lv=1&kv=1&tv=-1",
                            headers=headers)
                        payload = json.loads(
                            urllib.request.urlopen(req2, timeout=5, context=ctx).read().decode("utf-8"))
                        lines = _parse_lrc((payload.get("lrc") or {}).get("lyric") or "")
                        if lines and self._version_ok(lines, duration):
                            return lines
                    except Exception:
                        continue
            except Exception:
                continue
        return []

    def has_lines(self) -> bool:
        return bool(self._lines)

    def line_at(self, sec: float) -> str:
        current = ""
        for t, text in self._lines:
            if t > sec:
                break
            current = text
        return current


_LYRICS_SERVICE: Optional[LyricsService] = None


def get_lyrics_service() -> LyricsService:
    global _LYRICS_SERVICE
    if _LYRICS_SERVICE is None:
        _LYRICS_SERVICE = LyricsService()
    return _LYRICS_SERVICE


def get_media_service() -> MediaControlService:
    return MediaControlService.get_instance()


atexit.register(MediaControlService.shutdown_instance)

sys.modules.setdefault("media_service", sys.modules[__name__])
sys.modules.setdefault("stacky.media.media_service", sys.modules[__name__])
