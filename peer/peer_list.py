from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PeerInfo:
    peer_id: str
    host: str
    port: int
    status: str = "online"
    last_seen: float = field(default_factory=time.time)
    writer: Optional[asyncio.StreamWriter] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "peer_id": self.peer_id,
            "host": self.host,
            "port": self.port,
            "status": self.status,
            "last_seen": self.last_seen,
        }


class PeerList:
    def __init__(self) -> None:
        self._peers: Dict[str, PeerInfo] = {}
        self._lock = asyncio.Lock()

    async def upsert_peer(self, peer_id: str, host: str, port: int, status: str = "online") -> PeerInfo:
        async with self._lock:
            peer = self._peers.get(peer_id)
            if peer:
                peer.host = host
                peer.port = port
                peer.status = status
                peer.last_seen = time.time()
                return peer
            peer = PeerInfo(peer_id=peer_id, host=host, port=port, status=status)
            self._peers[peer_id] = peer
            return peer

    async def set_writer(self, peer_id: str, writer: Optional[asyncio.StreamWriter]) -> None:
        async with self._lock:
            peer = self._peers.get(peer_id)
            if not peer:
                return
            old_writer = peer.writer
            peer.writer = writer
            peer.last_seen = time.time()
            peer.status = "online"
        if old_writer and old_writer is not writer:
            try:
                old_writer.close()
            except Exception:
                pass

    async def list_peers(self, status: Optional[str] = None) -> List[PeerInfo]:
        async with self._lock:
            peers = list(self._peers.values())
        if status:
            peers = [peer for peer in peers if peer.status == status]
        return peers

    async def snapshot(self) -> List[Dict[str, object]]:
        peers = await self.list_peers()
        return [peer.to_dict() for peer in peers]

    async def get_peer(self, peer_id: str) -> Optional[PeerInfo]:
        async with self._lock:
            return self._peers.get(peer_id)

    async def remove_peer(self, peer_id: str) -> None:
        async with self._lock:
            self._peers.pop(peer_id, None)

    async def mark_offline(self, peer_id: str) -> None:
        async with self._lock:
            peer = self._peers.get(peer_id)
            if peer:
                peer.status = "offline"
                peer.writer = None

    async def update_last_seen(self, peer_id: str) -> None:
        async with self._lock:
            peer = self._peers.get(peer_id)
            if peer:
                peer.last_seen = time.time()
                peer.status = "online"
