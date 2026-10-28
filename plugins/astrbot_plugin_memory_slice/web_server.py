"""Optional HTTP adapter on the host event loop; never owns MemoryService."""

import asyncio
import socket
from contextlib import contextmanager
from pathlib import Path


class WebServer:
    def __init__(self, runtime, config, frontend_dir=None):
        self.runtime, self.config = runtime, config
        self.frontend_dir = Path(frontend_dir or Path(__file__).parent / "frontend")
        self.server = self.task = self.socket = None

    async def start(self):
        token = str(self.config.get("web_token", "")).strip()
        if len(token) < 16 or not token.isascii():
            raise ValueError("管理页面访问令牌必须是至少 16 位的 ASCII 字符")
        host = str(self.config.get("web_host", "127.0.0.1")).strip()
        port = int(self.config.get("web_port", 8765))
        if not 1 <= port <= 65535:
            raise ValueError("管理页面端口必须在 1–65535 之间")
        if (
            not (self.frontend_dir / "index.html").is_file()
            or not (self.frontend_dir / "assets").is_dir()
        ):
            raise ValueError("插件缺少前端构建产物，请使用完整安装包")
        # Imports are optional when the dashboard is disabled.
        import uvicorn
        from .memory_slice.web import create_app

        class EmbeddedServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                # AstrBot owns process signals and the event loop.
                yield

            def install_signal_handlers(self):
                pass

        app = create_app(
            service=self.runtime.core,
            seed=False,
            host_mode="astrbot",
            frontend_dir=self.frontend_dir,
            access_token=token,
            scope_info=self.runtime.scope_info,
        )
        # Bind ourselves so port conflicts raise OSError instead of Uvicorn's SystemExit.
        self.socket = socket.socket(
            socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM
        )
        try:
            self.socket.bind((host, port))
            self.socket.listen(128)
            self.socket.setblocking(False)
            self.server = EmbeddedServer(
                uvicorn.Config(
                    app,
                    host=host,
                    port=port,
                    log_config=None,
                    access_log=False,
                    lifespan="on",
                    timeout_graceful_shutdown=5,
                )
            )
            self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
            for _ in range(200):
                if self.task.done():
                    await self.task
                    raise RuntimeError("管理页面未能启动")
                if self.server.started:
                    return
                await asyncio.sleep(0.05)
            raise RuntimeError("管理页面启动超时")
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 8)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            finally:
                self.task = None
        if self.socket:
            self.socket.close()
            self.socket = None
        self.server = None
