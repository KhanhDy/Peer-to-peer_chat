from __future__ import annotations

import base64
import hashlib
import os
from typing import Tuple

_PREFIX = "enc:v1:"
_NONCE_SIZE = 12


def encrypt_text(text: str, key: str) -> str:
    if not key or text == "":
        return text
    raw = text.encode("utf-8")
    nonce = os.urandom(_NONCE_SIZE)
    stream = _derive_stream(key, nonce, len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    data = nonce + cipher
    encoded = base64.urlsafe_b64encode(data).decode("ascii")
    return f"{_PREFIX}{encoded}"


def decrypt_text(text: str, key: str) -> Tuple[str, bool]:
    if not text.startswith(_PREFIX):
        return text, True
    if not key:
        return "[encrypted]", False
    payload = text[len(_PREFIX) :]
    try:
        data = base64.urlsafe_b64decode(payload.encode("ascii"))
    except (ValueError, OSError):
        return "[encrypted]", False
    if len(data) < _NONCE_SIZE:
        return "[encrypted]", False
    nonce = data[:_NONCE_SIZE]
    cipher = data[_NONCE_SIZE:]
    stream = _derive_stream(key, nonce, len(cipher))
    raw = bytes(a ^ b for a, b in zip(cipher, stream))
    try:
        return raw.decode("utf-8"), True
    except UnicodeDecodeError:
        return "[encrypted]", False


def _derive_stream(key: str, nonce: bytes, length: int) -> bytes:
    key_bytes = key.encode("utf-8")
    out = bytearray()
    counter = 0
    while len(out) < length:
        digest = hashlib.sha256(key_bytes + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(digest)
        counter += 1
    return bytes(out[:length])
