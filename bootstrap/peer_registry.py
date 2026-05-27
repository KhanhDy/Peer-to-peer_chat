from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PeerRecord:
    peer_id: str
    host: str
    port: int
    last_seen: float = field(default_factory=time.time)
    status: str = "online"


class PeerRegistry:
    def __init__(self, heartbeat_timeout: int) -> None:
        self._peers: Dict[str, PeerRecord] = {}
        self._writers: Dict[str, asyncio.StreamWriter] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_timeout = heartbeat_timeout

    async def register(
        self,
        peer_id: str,
        host: str,
        port: int,
        writer: Optional[asyncio.StreamWriter],
    ) -> PeerRecord:
        async with self._lock:
            record = self._peers.get(peer_id)
            if record:
                record.host = host
                record.port = port
                record.last_seen = time.time()
                record.status = "online"
            else:
                record = PeerRecord(peer_id=peer_id, host=host, port=port)
                self._peers[peer_id] = record
            if writer:
                self._writers[peer_id] = writer
            return record

    async def heartbeat(self, peer_id: str) -> None:
        async with self._lock:
            record = self._peers.get(peer_id)
            if record:
                record.last_seen = time.time()
                record.status = "online"

    async def unregister(self, peer_id: str) -> None:
        async with self._lock:
            self._peers.pop(peer_id, None)
            self._writers.pop(peer_id, None)

    async def remove_writer(self, peer_id: str) -> None:
        async with self._lock:
            self._writers.pop(peer_id, None)

    async def get_peers(self, exclude_id: Optional[str] = None) -> List[PeerRecord]:
        async with self._lock:
            peers = list(self._peers.values())
        if exclude_id:
            peers = [peer for peer in peers if peer.peer_id != exclude_id]
        return peers

    async def prune(self) -> List[str]:
        now = time.time()
        removed: List[str] = []
        async with self._lock:
            for peer_id, record in list(self._peers.items()):
                if now - record.last_seen > self._heartbeat_timeout:
                    removed.append(peer_id)
                    self._peers.pop(peer_id, None)
                    self._writers.pop(peer_id, None)
        return removed

    async def get_writer(self, peer_id: str) -> Optional[asyncio.StreamWriter]:
        async with self._lock:
            return self._writers.get(peer_id)

    async def get_writers(self) -> List[asyncio.StreamWriter]:
        async with self._lock:
            return list(self._writers.values())
