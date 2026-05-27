from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional


class PeerServer:
    def __init__(
        self,
        host: str,
        port: int,
        handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
        logger,
    ) -> None:
        self.host = host
        self.port = port
        self._handler = handler
        self._server: Optional[asyncio.AbstractServer] = None
        self.logger = logger

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handler, self.host, self.port)
        sockets = self._server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        self.logger.info("peer server listening on %s:%s", self.host, self.port)
        return self.port

    async def serve_forever(self) -> None:
        if not self._server:
            raise RuntimeError("server not started")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if not self._server:
            return
        self._server.close()
        await self._server.wait_closed()
