from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from peer.crypto import decrypt_text


class HistoryStore:
    def __init__(self, db_path: str, encrypt_key: str, logger) -> None:
        self.db_path = Path(db_path)
        self.encrypt_key = encrypt_key
        self.logger = logger
        self._lock = asyncio.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                chat_type TEXT NOT NULL,
                peer_id TEXT,
                group_id TEXT,
                group_name TEXT,
                sender_id TEXT NOT NULL,
                recipients TEXT,
                direction TEXT NOT NULL,
                timestamp REAL NOT NULL,
                text TEXT NOT NULL,
                system INTEGER DEFAULT 0,
                kind TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_peer ON messages(peer_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id, timestamp);

            CREATE TABLE IF NOT EXISTS sync_state (
                peer_id TEXT PRIMARY KEY,
                last_sync REAL NOT NULL
            );
            """
        )
        self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()

    async def add_message(
        self,
        message_id: str,
        chat_type: str,
        peer_id: Optional[str],
        group_id: Optional[str],
        group_name: Optional[str],
        sender_id: str,
        recipients: Optional[Iterable[str]],
        direction: str,
        timestamp: float,
        text: str,
        system: bool,
        kind: Optional[str],
    ) -> None:
        recipients_text = json.dumps(list(recipients)) if recipients else None
        async with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO messages (
                        message_id, chat_type, peer_id, group_id, group_name, sender_id,
                        recipients, direction, timestamp, text, system, kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        chat_type,
                        peer_id,
                        group_id,
                        group_name,
                        sender_id,
                        recipients_text,
                        direction,
                        timestamp,
                        text,
                        1 if system else 0,
                        kind,
                    ),
                )
                self._conn.commit()
            except sqlite3.DatabaseError as exc:
                self.logger.warning("history insert failed: %s", exc)

    async def list_messages(self, chat_type: str, chat_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        if chat_type not in {"direct", "group"}:
            return []
        query = (
            "SELECT * FROM messages WHERE chat_type = ? AND peer_id = ? AND system = 0 "
            if chat_type == "direct"
            else "SELECT * FROM messages WHERE chat_type = ? AND group_id = ? AND system = 0 "
        )
        query += "ORDER BY timestamp ASC LIMIT ?"
        async with self._lock:
            rows = self._conn.execute(query, (chat_type, chat_id, limit)).fetchall()
        return [self._row_to_message(row) for row in rows]

    async def list_recent_direct_peers(self) -> List[str]:
        async with self._lock:
            rows = self._conn.execute(
                """
                SELECT peer_id, MAX(timestamp) AS last_ts
                FROM messages
                WHERE chat_type = 'direct' AND direction = 'in'
                GROUP BY peer_id
                ORDER BY last_ts DESC
                """
            ).fetchall()
        return [row[0] for row in rows if row[0]]

    async def get_last_sync(self, peer_id: str) -> float:
        async with self._lock:
            row = self._conn.execute("SELECT last_sync FROM sync_state WHERE peer_id = ?", (peer_id,)).fetchone()
        return float(row[0]) if row else 0.0

    async def set_last_sync(self, peer_id: str, timestamp: float) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO sync_state (peer_id, last_sync) VALUES (?, ?) "
                "ON CONFLICT(peer_id) DO UPDATE SET last_sync = excluded.last_sync",
                (peer_id, timestamp),
            )
            self._conn.commit()

    async def get_outgoing_since(self, peer_id: str, since: float, limit: int = 500) -> List[Dict[str, Any]]:
        async with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM messages
                WHERE direction = 'out'
                  AND timestamp > ?
                  AND (
                    (chat_type = 'direct' AND peer_id = ?)
                    OR (chat_type = 'group' AND recipients LIKE ?)
                  )
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (since, peer_id, f'%"{peer_id}"%', limit),
            ).fetchall()
        return [self._row_to_wire(row) for row in rows]

    def _row_to_message(self, row: sqlite3.Row) -> Dict[str, Any]:
        recipients = _parse_recipients(row["recipients"])
        text, _ = decrypt_text(row["text"], self.encrypt_key)
        return {
            "message_id": row["message_id"],
            "chat_type": row["chat_type"],
            "from": row["sender_id"],
            "to": row["peer_id"],
            "group_id": row["group_id"],
            "group_name": row["group_name"],
            "recipients": recipients,
            "timestamp": row["timestamp"],
            "text": text,
            "system": bool(row["system"]),
            "kind": row["kind"],
            "direction": row["direction"],
        }

    def _row_to_wire(self, row: sqlite3.Row) -> Dict[str, Any]:
        recipients = _parse_recipients(row["recipients"])
        return {
            "message_id": row["message_id"],
            "chat_type": row["chat_type"],
            "peer_id": row["peer_id"],
            "group_id": row["group_id"],
            "group_name": row["group_name"],
            "recipients": recipients,
            "sender_id": row["sender_id"],
            "timestamp": row["timestamp"],
            "text": row["text"],
            "system": bool(row["system"]),
            "kind": row["kind"],
        }


def _parse_recipients(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]
