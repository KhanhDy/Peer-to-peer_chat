from __future__ import annotations

import asyncio


class HeartbeatManager:
    def __init__(self, node, interval: int, timeout: int, logger) -> None:
        self.node = node
        self.interval = interval
        self.timeout = timeout
        self.logger = logger
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._bootstrap_loop()),
            asyncio.create_task(self._peer_loop()),
            asyncio.create_task(self._prune_loop()),
        ]

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _bootstrap_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.node.send_bootstrap_heartbeat()
            await asyncio.sleep(self.interval)

    async def _peer_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.node.send_peer_heartbeats()
            await asyncio.sleep(self.interval)

    async def _prune_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.node.prune_offline_peers(self.timeout)
            await asyncio.sleep(2)
