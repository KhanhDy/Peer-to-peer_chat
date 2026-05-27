from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from bootstrap.peer_registry import PeerRegistry
from common.protocol import (
    BOOTSTRAP_ID,
    MSG_ACK,
    MSG_GET_PEERS,
    MSG_HEARTBEAT,
    MSG_PEER_JOINED,
    MSG_PEER_LEFT,
    MSG_PEER_LIST,
    MSG_REGISTER,
    MSG_UNREGISTER,
)
from peer.message import Message, MessageError, decode_message_line, new_message, send_message


class BootstrapServer:
    def __init__(self, host: str, port: int, heartbeat_timeout: int, logger) -> None:
        self.host = host
        self.port = port
        self.logger = logger
        self.registry = PeerRegistry(heartbeat_timeout)
        self._server: Optional[asyncio.AbstractServer] = None
        self._prune_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sockets = self._server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        self.logger.info("bootstrap listening on %s:%s", self.host, self.port)
        self._prune_task = asyncio.create_task(self._prune_loop())

        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer_id: Optional[str] = None
        unregistered = False
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message_line(line)
                except MessageError as exc:
                    self.logger.warning("invalid message: %s", exc)
                    continue

                if msg.type == MSG_REGISTER:
                    payload = msg.payload or {}
                    peer_id = msg.from_id
                    host = payload.get("host")
                    port = payload.get("port")
                    if not host or not port:
                        await self._send_ack(writer, msg.message_id, "ERROR", "missing host/port")
                        continue
                    await self.registry.register(peer_id, host, int(port), writer)
                    await self._send_ack(writer, msg.message_id, "OK")
                    await self._notify_peers(MSG_PEER_JOINED, peer_id, {"id": peer_id, "host": host, "port": int(port)})
                    continue

                if msg.type == MSG_GET_PEERS:
                    peers = await self.registry.get_peers(exclude_id=msg.from_id)
                    payload = [
                        {"id": peer.peer_id, "host": peer.host, "port": peer.port}
                        for peer in peers
                    ]
                    response = new_message(MSG_PEER_LIST, BOOTSTRAP_ID, payload=payload)
                    await send_message(writer, response)
                    continue

                if msg.type == MSG_HEARTBEAT:
                    await self.registry.heartbeat(msg.from_id)
                    await self._send_ack(writer, msg.message_id, "OK")
                    continue

                if msg.type == MSG_UNREGISTER:
                    await self.registry.unregister(msg.from_id)
                    await self._send_ack(writer, msg.message_id, "OK")
                    await self._notify_peers(MSG_PEER_LEFT, msg.from_id, {"id": msg.from_id})
                    unregistered = True
                    continue

                await self._send_ack(writer, msg.message_id, "ERROR", "unsupported message type")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.exception("bootstrap client error: %s", exc)
        finally:
            if peer_id and not unregistered:
                await self.registry.unregister(peer_id)
                await self._notify_peers(MSG_PEER_LEFT, peer_id, {"id": peer_id})
            if peer_id:
                await self.registry.remove_writer(peer_id)
            writer.close()
            await writer.wait_closed()

    async def _send_ack(
        self,
        writer: asyncio.StreamWriter,
        message_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {"status": status, "message_id": message_id}
        if error:
            payload["error"] = error
        msg = new_message(MSG_ACK, BOOTSTRAP_ID, payload=payload)
        await send_message(writer, msg)

    async def _notify_peers(self, msg_type: str, peer_id: str, payload: Dict[str, Any]) -> None:
        writers = await self.registry.get_writers()
        skip_writer = await self.registry.get_writer(peer_id)
        msg = new_message(msg_type, BOOTSTRAP_ID, payload=payload)
        for writer in writers:
            if writer is skip_writer:
                continue
            try:
                await send_message(writer, msg)
            except Exception:
                continue

    async def _prune_loop(self) -> None:
        while True:
            removed = await self.registry.prune()
            for peer_id in removed:
                await self._notify_peers(MSG_PEER_LEFT, peer_id, {"id": peer_id})
            await asyncio.sleep(2)
