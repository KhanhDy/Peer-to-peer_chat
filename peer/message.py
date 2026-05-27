from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from common.protocol import PROTOCOL_VERSION, REQUIRED_FIELDS


class MessageError(ValueError):
    pass


@dataclass
class Message:
    type: str
    from_id: str
    to: Optional[str]
    payload: Optional[Any]
    timestamp: float
    message_id: str
    version: int = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "from": self.from_id,
            "to": self.to,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
            "version": self.version,
        }


def new_message(
    message_type: str,
    from_id: str,
    to: Optional[str] = None,
    payload: Optional[Any] = None,
) -> Message:
    return Message(
        type=message_type,
        from_id=from_id,
        to=to,
        payload=payload,
        timestamp=time.time(),
        message_id=str(uuid.uuid4()),
    )


def encode_message(message: Message) -> bytes:
    data = message.to_dict()
    return (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")


def decode_message_line(line: bytes | str) -> Message:
    if isinstance(line, (bytes, bytearray)):
        text = line.decode("utf-8").strip()
    else:
        text = line.strip()

    if not text:
        raise MessageError("empty message")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MessageError("invalid json") from exc

    missing = REQUIRED_FIELDS.difference(data.keys())
    if missing:
        raise MessageError(f"missing fields: {sorted(missing)}")

    return Message(
        type=data.get("type"),
        from_id=data.get("from"),
        to=data.get("to"),
        payload=data.get("payload"),
        timestamp=float(data.get("timestamp", 0)),
        message_id=str(data.get("message_id")),
        version=int(data.get("version", PROTOCOL_VERSION)),
    )


async def send_message(writer, message: Message) -> None:
    writer.write(encode_message(message))
    await writer.drain()
