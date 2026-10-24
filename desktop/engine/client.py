"""
模组端连接层（WebSocket 客户端）。

职责：把「指令 dict」按契约序列化后发给 tModLoader 模组，并把模组回传的
4 类状态消息（InstructionReceived / ChunkProgress / AllCompleted /
ExecutionError）回调给 UI。

设计要点：
- asyncio 跑在独立后台线程，UI 主线程（tkinter）不会被阻塞；
- 发送严格使用 dump_command 的序列化口径，保证与校验和一致；
- 所有回调都在后台线程触发，UI 侧须自行切回主线程（如 queue + after）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable, Optional

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None


class BuildClient:
    """与模组通信的 WebSocket 客户端（线程安全的外壳）。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        on_status: Optional[Callable[[dict], None]] = None,
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.host = host
        self.port = port
        self.on_status = on_status
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_log = on_log
        self.on_error = on_error

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._ws = None
        self._connected = False
        self._lock = threading.Lock()

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        """启动后台事件循环线程（不建立连接）。

        必须等到事件循环真的在跑再返回：否则紧接着的 connect() 会被 _submit 丢掉，
        表现为点「连接游戏」后永远停在"连接中…"（真实踩过的坑）。
        """
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="BuildClientLoop", daemon=True)
        self._thread.start()
        # 必须等到事件循环真的在跑再返回：否则紧接的 connect() 会被 _submit 丢掉，
        # 表现为点「连接游戏」后永远停在"连接中…"（真实踩过的坑）
        for _ in range(200):
            if self._loop is not None and self._loop.is_running():
                return
            threading.Event().wait(0.01)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)
            self._loop.call_later(0.2, self._loop.stop)

    async def _close(self) -> None:
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self._session is not None and not self._session.closed:
                await self._session.close()
        except Exception:
            pass
        self._ws = None
        self._session = None
        self._connected = False

    # ---------------- 连接 ----------------

    def connect(self) -> None:
        self._submit(self._connect())

    async def _connect(self) -> None:
        if aiohttp is None:
            self._log("缺少 aiohttp，无法建立 WebSocket 连接（打包发行版时必须把 aiohttp 一起打进去）")
            if self.on_error:
                self.on_error("缺少 aiohttp 库，无法连接游戏")
            return
        if self._connected:
            return
        url = f"ws://{self.host}:{self.port}/"
        try:
            # 必须带超时：否则连不上时会永远停在"连接中…"，用户看不到任何原因
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
            self._ws = await self._session.ws_connect(url, heartbeat=30,
                                                     max_msg_size=64 * 1024 * 1024)
            self._connected = True
            self._log(f"已连接 {url}")
            if self.on_connected:
                self.on_connected()
            asyncio.create_task(self._recv_loop())
        except asyncio.TimeoutError:
            self._log(f"连接超时：{url} 8 秒内无响应")
            self._connected = False
            if self.on_error:
                self.on_error(f"连接超时：{self.host}:{self.port} 无响应")
        except Exception as exc:
            self._log(f"连接失败：{exc}")
            self._connected = False
            if self.on_error:
                self.on_error(str(exc))
            if self._session is not None:
                try:
                    await self._session.close()
                except Exception:
                    pass
            self._session = None

    async def _recv_loop(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        self._log(f"收到非法 JSON：{msg.data[:120]}")
                        continue
                    if self.on_status:
                        self.on_status(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as exc:
            self._log(f"接收异常：{exc}")
        finally:
            was_connected = self._connected
            self._connected = False
            self._ws = None
            if was_connected:
                self._log("连接已断开")
                if self.on_disconnected:
                    self.on_disconnected()

    # ---------------- 发送 ----------------

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def send_command(self, command: dict) -> bool:
        """按契约序列化并发送一条指令；未连接时返回 False。"""
        if not self.is_connected():
            self._log("未连接，指令未发送")
            return False
        text = json.dumps(command, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            asyncio.run_coroutine_threadsafe(self._ws.send_str(text), self._loop)
            return True
        except Exception as exc:
            self._log(f"发送失败：{exc}")
            return False

    # ---------------- 内部 ----------------

    def _submit(self, coro) -> None:
        # 循环可能刚好还在启动：等一小会儿再判定失败，避免把用户的点击吞掉
        for _ in range(200):
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self._loop)
                return
            threading.Event().wait(0.01)
        self._log("事件循环未启动，请先调用 start()")
        if self.on_error:
            self.on_error("内部事件循环未启动，连接请求被丢弃")

    def _log(self, text: str) -> None:
        if self.on_log:
            self.on_log(text)
