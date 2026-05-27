from __future__ import annotations

import asyncio

from common.protocol import MSG_PEER_HELLO
from peer.message import new_message, send_message


async def open_peer_connection(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


async def send_peer_hello(
    writer: asyncio.StreamWriter,
    peer_id: str,
    host: str,
    port: int,
) -> None:
    payload = {"host": host, "port": port}
    msg = new_message(MSG_PEER_HELLO, peer_id, payload=payload)
    await send_message(writer, msg)
