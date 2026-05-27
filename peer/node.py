from __future__ import annotations

import asyncio
import shlex
import socket
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import DynamicCompleter, WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from common.protocol import (
    BOOTSTRAP_ID,
    MSG_ACK,
    MSG_CHAT,
    MSG_GET_PEERS,
    MSG_GROUP_CHAT,
    MSG_HEARTBEAT,
    MSG_PEER_EXCHANGE,
    MSG_PEER_HELLO,
    MSG_PEER_JOINED,
    MSG_PEER_LEFT,
    MSG_PEER_LIST,
    MSG_SYNC_REQUEST,
    MSG_SYNC_RESPONSE,
    MSG_REGISTER,
    MSG_UNREGISTER,
)
from peer.client import open_peer_connection, send_peer_hello
from peer.events import EventHub
from peer.heartbeat import HeartbeatManager
from peer.message import Message, MessageError, decode_message_line, new_message, send_message
from peer.metrics import PeerMetrics
from peer.peer_list import PeerList
from peer.server import PeerServer
from peer.web_server import PeerWebServer
from peer.crypto import decrypt_text, encrypt_text
from peer.history_store import HistoryStore


class PeerNode:
    def __init__(
        self,
        peer_id: str,
        host: str,
        advertise_host: str,
        port: int,
        bootstrap_host: str,
        bootstrap_port: int,
        seeds: list[str],
        connect_limit: int,
        heartbeat_interval: int,
        heartbeat_timeout: int,
        logger,
        web_host: str,
        web_port: int,
        cli_enabled: bool,
        history_enabled: bool,
        history_db: str,
        encrypt_key: str,
    ) -> None:
        self.peer_id = peer_id
        self.host = host
        self.port = port
        self.bootstrap_host = bootstrap_host
        self.bootstrap_port = bootstrap_port
        self.seeds = seeds
        self.advertise_host = self._resolve_advertise_host(host, advertise_host, bootstrap_host, seeds)
        self.connect_limit = connect_limit
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.logger = logger
        self.web_host = web_host
        self.web_port = web_port
        self.cli_enabled = cli_enabled
        self.encrypt_key = encrypt_key
        self.console = Console()

        self.peer_list = PeerList()
        self.metrics = PeerMetrics()
        self.events = EventHub()
        self.server = PeerServer(self.host, self.port, self._handle_peer_connection, logger)
        self.heartbeat = HeartbeatManager(self, heartbeat_interval, heartbeat_timeout, logger)
        self.web_server: Optional[PeerWebServer] = None
        self.history: Optional[HistoryStore] = None

        if history_enabled:
            db_path = history_db or str(Path("data") / f"{self.peer_id}.db")
            self.history = HistoryStore(db_path, encrypt_key, logger)

        self._stop_event = asyncio.Event()
        self._server_task: Optional[asyncio.Task] = None
        self._input_task: Optional[asyncio.Task] = None
        self._bootstrap_task: Optional[asyncio.Task] = None
        self._bootstrap_reader: Optional[asyncio.StreamReader] = None
        self._bootstrap_writer: Optional[asyncio.StreamWriter] = None
        self._bootstrap_lock = asyncio.Lock()

        self._pending_acks: Dict[str, asyncio.Future] = {}
        self._pending_ack_times: Dict[str, float] = {}
        self._seen_messages: Dict[str, float] = {}
        self._seen_ttl = 300
        self._peer_id_cache: set[str] = set()

    async def run(self) -> None:
        await self._start()
        await self._stop_event.wait()
        await self._stop()

    async def _start(self) -> None:
        self.port = await self.server.start()
        self._server_task = asyncio.create_task(self.server.serve_forever())
        await self.heartbeat.start()
        if self.web_port > 0:
            self.web_server = PeerWebServer(self, self.web_host, self.web_port, self.logger)
            await self.web_server.start()
        if self.cli_enabled:
            self._input_task = asyncio.create_task(self._input_loop())
        self._bootstrap_task = asyncio.create_task(self._bootstrap_connect_loop())
        await self._connect_seeds()

    async def _stop(self) -> None:
        await self.heartbeat.stop()
        if self._input_task:
            self._input_task.cancel()
        if self._bootstrap_task:
            self._bootstrap_task.cancel()
        if self._server_task:
            self._server_task.cancel()
        await self._send_unregister()
        await self.server.stop()
        if self.web_server:
            await self.web_server.stop()
        if self.history:
            await self.history.close()
        if self._bootstrap_writer:
            self._bootstrap_writer.close()
            await self._bootstrap_writer.wait_closed()

    async def _input_loop(self) -> None:
        self._print_help()
        session = self._build_prompt_session()
        with patch_stdout():
            while not self._stop_event.is_set():
                try:
                    text = await session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    continue

                text = text.strip()
                if not text:
                    continue

                if text in {"/exit", "/quit"}:
                    self._stop_event.set()
                    return

                if text in {"/help", "help"}:
                    self._print_help()
                    continue

                if text.startswith("/peers"):
                    await self._show_peers()
                    continue

                if text.startswith("/metrics"):
                    self._show_metrics()
                    continue

                if text.startswith("/web"):
                    self._show_web()
                    continue

                if text.startswith("/chat "):
                    await self._handle_chat_command(text)
                    continue

                if text.startswith("/group "):
                    await self._handle_group_command(text)
                    continue

                if text.startswith("/connect "):
                    await self._handle_connect_command(text)
                    continue

                self.console.print("Unknown command. Type /help")

    def _print_help(self) -> None:
        self.console.print("Commands:")
        self.console.print("  /peers                          - list peers")
        self.console.print("  /metrics                        - show metrics")
        self.console.print("  /web                            - show web ui address")
        self.console.print("  /chat <peer_id> <message>       - send direct message")
        self.console.print("  /group <id1,id2> <message>      - send group message")
        self.console.print("  /connect <peer_id> <host> <port> - add and connect peer")
        self.console.print("  /exit                           - quit")

    def _build_prompt_session(self) -> PromptSession:
        history_path = self._history_path()
        completer = DynamicCompleter(lambda: WordCompleter(self._completion_words(), ignore_case=True))
        return PromptSession(history=FileHistory(history_path), completer=completer)

    def _history_path(self) -> str:
        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        return str(logs_dir / f"{self.peer_id}.history")

    def _completion_words(self) -> list[str]:
        commands = ["/help", "/peers", "/metrics", "/web", "/chat", "/group", "/connect", "/exit"]
        return commands + sorted(self._peer_id_cache)

    def _encrypt(self, text: str) -> str:
        return encrypt_text(text, self.encrypt_key)

    def _decrypt(self, text: str) -> tuple[str, bool]:
        return decrypt_text(text, self.encrypt_key)

    async def _store_message(
        self,
        message_id: str,
        chat_type: str,
        peer_id: Optional[str],
        group_id: Optional[str],
        group_name: Optional[str],
        sender_id: str,
        recipients: Optional[list[str]],
        direction: str,
        timestamp: float,
        text: str,
        system: bool,
        kind: Optional[str],
    ) -> None:
        if not self.history:
            return
        await self.history.add_message(
            message_id=message_id,
            chat_type=chat_type,
            peer_id=peer_id,
            group_id=group_id,
            group_name=group_name,
            sender_id=sender_id,
            recipients=recipients,
            direction=direction,
            timestamp=timestamp,
            text=text,
            system=system,
            kind=kind,
        )

    async def get_history(self, chat_type: str, chat_id: str) -> list[Dict[str, object]]:
        if not self.history:
            return []
        return await self.history.list_messages(chat_type, chat_id)

    async def get_recent_direct_peers(self) -> list[str]:
        if not self.history:
            return []
        return await self.history.list_recent_direct_peers()

    async def _touch_sync_state(self, peer_id: str, timestamp: float) -> None:
        if not self.history or not peer_id:
            return
        current = await self.history.get_last_sync(peer_id)
        if timestamp > current:
            await self.history.set_last_sync(peer_id, timestamp)

    async def _show_peers(self) -> None:
        peers = await self.peer_list.list_peers()
        if not peers:
            self.console.print("No peers known")
            return
        for peer in peers:
            status = peer.status
            self.console.print(f"{peer.peer_id} {peer.host}:{peer.port} {status}")

    def _show_metrics(self) -> None:
        snapshot = self.metrics.snapshot()
        for key, value in snapshot.items():
            self.console.print(f"{key}: {value}")

    def _show_web(self) -> None:
        if self.web_port <= 0:
            self.console.print("Web UI disabled")
            return
        self.console.print(f"Web UI: http://{self.web_host}:{self.web_port}")

    def _track_peer_id(self, peer_id: str) -> None:
        if peer_id and peer_id != self.peer_id:
            self._peer_id_cache.add(peer_id)

    def _publish_event(self, event_type: str, payload: Dict[str, object]) -> None:
        event = {"type": event_type, "payload": payload, "timestamp": time.time()}
        asyncio.create_task(self.events.publish(event))

    def _publish_metrics(self) -> None:
        self._publish_event("metrics_snapshot", self.metrics.snapshot())

    async def get_peers_snapshot(self) -> list[Dict[str, object]]:
        return await self.peer_list.snapshot()

    async def _handle_chat_command(self, text: str) -> None:
        parts = text.split(" ", 2)
        if len(parts) < 3:
            self.console.print("Usage: /chat <peer_id> <message>")
            return
        peer_id = parts[1]
        message = parts[2]
        ok = await self.send_chat(peer_id, message)
        if not ok:
            self.console.print("Send failed")

    async def _handle_group_command(self, text: str) -> None:
        parts = text.split(" ", 2)
        if len(parts) < 3:
            self.console.print("Usage: /group <id1,id2> <message>")
            return
        peer_ids = [item.strip() for item in parts[1].split(",") if item.strip()]
        message = parts[2]
        await self.send_group(peer_ids, message)

    async def _handle_connect_command(self, text: str) -> None:
        try:
            args = shlex.split(text)
        except ValueError:
            self.console.print("Invalid command")
            return
        if len(args) != 4:
            self.console.print("Usage: /connect <peer_id> <host> <port>")
            return
        peer_id, host, port_str = args[1], args[2], args[3]
        try:
            port = int(port_str)
        except ValueError:
            self.console.print("Port must be a number")
            return
        await self.peer_list.upsert_peer(peer_id, host, port)
        self._track_peer_id(peer_id)
        ok = await self._connect_peer(peer_id, host, port)
        if not ok:
            self.console.print("Connection failed")

    async def send_chat(self, peer_id: str, text: str) -> bool:
        encrypted = self._encrypt(text)
        msg = new_message(MSG_CHAT, self.peer_id, to=peer_id)
        payload = {"text": encrypted, "event_id": msg.message_id}
        msg.payload = payload
        self.metrics.record_send()
        self._publish_metrics()
        ok = await self._send_with_ack(peer_id, msg)
        if ok:
            await self._store_message(
                message_id=msg.message_id,
                chat_type="direct",
                peer_id=peer_id,
                group_id=None,
                group_name=None,
                sender_id=self.peer_id,
                recipients=None,
                direction="out",
                timestamp=msg.timestamp,
                text=encrypted,
                system=False,
                kind=None,
            )
        self._publish_event(
            "message_sent" if ok else "message_failed",
            {
                "from": self.peer_id,
                "to": peer_id,
                "text": text,
                "timestamp": msg.timestamp,
                "chat_type": "direct",
                "message_id": msg.message_id,
            },
        )
        return ok

    async def send_group(
        self,
        peer_ids: list[str],
        text: str,
        group_name: Optional[str] = None,
        group_id: Optional[str] = None,
        system: bool = False,
        kind: Optional[str] = None,
    ) -> None:
        unique_peer_ids: list[str] = []
        seen = set()
        for peer_id in peer_ids:
            peer_id = peer_id.strip()
            if not peer_id or peer_id == self.peer_id or peer_id in seen:
                continue
            seen.add(peer_id)
            unique_peer_ids.append(peer_id)

        if not unique_peer_ids:
            return

        members = sorted({*unique_peer_ids, self.peer_id})
        encrypted = self._encrypt(text) if not system else text
        event_id = str(uuid.uuid4())
        event_ts = time.time()
        payload = {"text": encrypted, "recipients": members, "event_id": event_id}
        if group_name:
            payload["group_name"] = group_name
        if group_id:
            payload["group_id"] = group_id
        if system:
            payload["system"] = True
            if kind:
                payload["kind"] = kind
        tasks = []
        for peer_id in unique_peer_ids:
            msg = new_message(MSG_GROUP_CHAT, self.peer_id, to=peer_id, payload=payload)
            msg.timestamp = event_ts
            self.metrics.record_send()
            self._publish_metrics()
            tasks.append(self._send_with_ack(peer_id, msg))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failed: list[str] = []
            success = False
            for peer_id, ok in zip(unique_peer_ids, results, strict=False):
                if isinstance(ok, Exception) or not ok:
                    failed.append(peer_id)
                    continue
                success = True
            if success:
                await self._store_message(
                    message_id=event_id,
                    chat_type="group",
                    peer_id=None,
                    group_id=group_id,
                    group_name=group_name,
                    sender_id=self.peer_id,
                    recipients=members,
                    direction="out",
                    timestamp=event_ts,
                    text=encrypted,
                    system=system,
                    kind=kind,
                )
            self._publish_event(
                "message_sent" if success else "message_failed",
                {
                    "from": self.peer_id,
                    "to": unique_peer_ids,
                    "text": text,
                    "timestamp": event_ts,
                    "failed": failed,
                    "chat_type": "group",
                    "recipients": members,
                    "group_name": group_name,
                    "group_id": group_id,
                    "system": system,
                    "kind": kind,
                    "message_id": event_id,
                },
            )

    async def _send_with_ack(self, peer_id: str, msg: Message) -> bool:
        for _ in range(3):
            writer = await self._ensure_peer_connection(peer_id)
            if not writer:
                await asyncio.sleep(1)
                continue
            future = asyncio.get_running_loop().create_future()
            self._pending_acks[msg.message_id] = future
            self._pending_ack_times[msg.message_id] = time.time()
            await send_message(writer, msg)
            try:
                await asyncio.wait_for(future, timeout=5)
                return True
            except asyncio.TimeoutError:
                self._pending_acks.pop(msg.message_id, None)
                self._pending_ack_times.pop(msg.message_id, None)
                self.metrics.record_ack_timeout()
                self._publish_metrics()
        return False

    async def _ensure_peer_connection(self, peer_id: str) -> Optional[asyncio.StreamWriter]:
        peer = await self.peer_list.get_peer(peer_id)
        if not peer:
            return None
        if peer.writer and not peer.writer.is_closing():
            return peer.writer
        ok = await self._connect_peer(peer_id, peer.host, peer.port)
        if not ok:
            return None
        peer = await self.peer_list.get_peer(peer_id)
        return peer.writer if peer else None

    async def _connect_peer(self, peer_id: str, host: str, port: int) -> bool:
        try:
            reader, writer = await open_peer_connection(host, port)
            await send_peer_hello(writer, self.peer_id, self.advertise_host, self.port)
            await self.peer_list.upsert_peer(peer_id, host, port)
            await self.peer_list.set_writer(peer_id, writer)
            asyncio.create_task(self._handle_peer_connection(reader, writer, expected_peer_id=peer_id))
            return True
        except Exception as exc:
            self.logger.warning("connect failed to %s:%s: %s", host, port, exc)
            self.metrics.record_connect_failure()
            self._publish_event("connect_failed", {"host": host, "port": port, "peer_id": peer_id})
            return False

    async def _handle_peer_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        expected_peer_id: Optional[str] = None,
    ) -> None:
        peer_id: Optional[str] = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message_line(line)
                except MessageError as exc:
                    self.logger.warning("invalid peer message: %s", exc)
                    continue

                if msg.type == MSG_PEER_HELLO:
                    peer_id = msg.from_id
                    if expected_peer_id and expected_peer_id != peer_id:
                        await self.peer_list.remove_peer(expected_peer_id)
                        expected_peer_id = None
                    payload = msg.payload or {}
                    host = payload.get("host") or "127.0.0.1"
                    port = int(payload.get("port") or 0)
                    await self.peer_list.upsert_peer(peer_id, host, port)
                    await self.peer_list.set_writer(peer_id, writer)
                    self._track_peer_id(peer_id)
                    self._publish_event("peer_update", {"peer_id": peer_id, "host": host, "port": port, "status": "online"})
                    await self._request_sync(peer_id)
                    continue

                await self.peer_list.update_last_seen(msg.from_id)

                if msg.type in {MSG_CHAT, MSG_GROUP_CHAT}:
                    payload = msg.payload if isinstance(msg.payload, dict) else {}
                    event_id = payload.get("event_id") if isinstance(payload, dict) else None
                    event_id = event_id or msg.message_id
                    if not self._record_seen(event_id):
                        continue
                    text_raw = payload.get("text") if isinstance(payload, dict) else msg.payload
                    text_raw = "" if text_raw is None else str(text_raw)
                    is_system = bool(payload.get("system")) if isinstance(payload, dict) else False
                    system_kind = payload.get("kind") if isinstance(payload, dict) else None
                    text, ok = self._decrypt(text_raw)
                    if not ok and not is_system:
                        text = "[encrypted]"
                    if not is_system:
                        self.console.print(f"{msg.from_id}: {text}")
                        self.metrics.record_receive()
                        self._publish_metrics()
                    event_payload = {
                        "from": msg.from_id,
                        "to": msg.to,
                        "text": text,
                        "timestamp": msg.timestamp,
                        "chat_type": "group" if msg.type == MSG_GROUP_CHAT else "direct",
                        "system": is_system,
                        "kind": system_kind,
                        "message_id": event_id,
                    }
                    recipients = None
                    group_name = None
                    group_id = None
                    if msg.type == MSG_GROUP_CHAT and isinstance(payload, dict):
                        recipients = payload.get("recipients")
                        if isinstance(recipients, list):
                            event_payload["recipients"] = recipients
                        else:
                            recipients = None
                        group_name = payload.get("group_name")
                        if isinstance(group_name, str):
                            event_payload["group_name"] = group_name
                        else:
                            group_name = None
                        group_id = payload.get("group_id")
                        if isinstance(group_id, str):
                            event_payload["group_id"] = group_id
                        else:
                            group_id = None
                    await self._store_message(
                        message_id=event_id,
                        chat_type="group" if msg.type == MSG_GROUP_CHAT else "direct",
                        peer_id=msg.from_id if msg.type == MSG_CHAT else None,
                        group_id=group_id,
                        group_name=group_name,
                        sender_id=msg.from_id,
                        recipients=recipients if isinstance(recipients, list) else None,
                        direction="in",
                        timestamp=msg.timestamp,
                        text=text_raw,
                        system=is_system,
                        kind=system_kind if isinstance(system_kind, str) else None,
                    )
                    await self._touch_sync_state(msg.from_id, msg.timestamp)
                    self._publish_event("message_received", event_payload)
                    await self._send_ack(writer, msg.message_id, msg.from_id)
                    continue

                if msg.type == MSG_HEARTBEAT:
                    await self._send_ack(writer, msg.message_id, msg.from_id)
                    continue

                if msg.type == MSG_ACK:
                    payload = msg.payload or {}
                    ack_id = payload.get("message_id")
                    start = self._pending_ack_times.pop(ack_id, None)
                    if start:
                        latency_ms = (time.time() - start) * 1000
                        self.metrics.record_ack_latency(latency_ms)
                        self._publish_metrics()
                    future = self._pending_acks.pop(ack_id, None)
                    if future and not future.done():
                        future.set_result(True)
                    continue

                if msg.type == MSG_PEER_EXCHANGE:
                    await self._handle_peer_exchange(writer)
                    continue

                if msg.type == MSG_PEER_LIST:
                    await self._apply_peer_list(msg.payload)
                    continue

                if msg.type == MSG_SYNC_REQUEST:
                    await self._handle_sync_request(msg)
                    await self._send_ack(writer, msg.message_id, msg.from_id)
                    continue

                if msg.type == MSG_SYNC_RESPONSE:
                    await self._handle_sync_response(msg)
                    await self._send_ack(writer, msg.message_id, msg.from_id)
                    continue

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("peer connection error: %s", exc)
        finally:
            if peer_id:
                await self.peer_list.mark_offline(peer_id)
                self._publish_event("peer_update", {"peer_id": peer_id, "status": "offline"})
            writer.close()
            await writer.wait_closed()

    async def _send_ack(self, writer: asyncio.StreamWriter, message_id: str, to_peer: str) -> None:
        payload = {"status": "OK", "message_id": message_id}
        msg = new_message(MSG_ACK, self.peer_id, to=to_peer, payload=payload)
        await send_message(writer, msg)

    async def _handle_peer_exchange(self, writer: asyncio.StreamWriter) -> None:
        peers = await self.peer_list.list_peers()
        payload = [
            {"id": peer.peer_id, "host": peer.host, "port": peer.port}
            for peer in peers
        ]
        msg = new_message(MSG_PEER_LIST, self.peer_id, payload=payload)
        await send_message(writer, msg)

    async def _apply_peer_list(self, payload) -> None:
        if not isinstance(payload, list):
            return
        for peer in payload:
            peer_id = peer.get("id")
            host = peer.get("host")
            port = peer.get("port")
            if not peer_id or peer_id == self.peer_id:
                continue
            if host and port:
                await self.peer_list.upsert_peer(peer_id, host, int(port))
                self._track_peer_id(peer_id)
                self._publish_event("peer_update", {"peer_id": peer_id, "host": host, "port": int(port), "status": "online"})

    async def _request_sync(self, peer_id: str) -> None:
        if not self.history or not peer_id:
            return
        writer = await self._ensure_peer_connection(peer_id)
        if not writer:
            return
        since = await self.history.get_last_sync(peer_id)
        msg = new_message(MSG_SYNC_REQUEST, self.peer_id, to=peer_id, payload={"since": since})
        await send_message(writer, msg)

    async def _handle_sync_request(self, msg: Message) -> None:
        if not self.history:
            return
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        since = payload.get("since", 0)
        if not isinstance(since, (int, float)):
            since = 0
        messages = await self.history.get_outgoing_since(msg.from_id, float(since))
        writer = await self._ensure_peer_connection(msg.from_id)
        if not writer:
            return
        response = new_message(
            MSG_SYNC_RESPONSE,
            self.peer_id,
            to=msg.from_id,
            payload={"messages": messages, "since": float(since)},
        )
        await send_message(writer, response)

    async def _handle_sync_response(self, msg: Message) -> None:
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        items = payload.get("messages")
        if not isinstance(items, list):
            return
        max_ts = 0.0
        for item in items:
            ts = await self._ingest_synced_message(item)
            if ts and ts > max_ts:
                max_ts = ts
        if max_ts:
            await self._touch_sync_state(msg.from_id, max_ts)

    async def _ingest_synced_message(self, item) -> Optional[float]:
        if not isinstance(item, dict):
            return None
        message_id = str(item.get("message_id") or "").strip()
        if not message_id:
            return None
        if not self._record_seen(message_id):
            return None
        chat_type = item.get("chat_type")
        if chat_type not in {"direct", "group"}:
            return None
        sender_id = str(item.get("sender_id") or "").strip()
        if not sender_id:
            return None
        text_raw = str(item.get("text") or "")
        text, ok = self._decrypt(text_raw)
        system = bool(item.get("system"))
        kind = item.get("kind")
        if not ok and not system:
            text = "[encrypted]"
        recipients = item.get("recipients")
        if not isinstance(recipients, list):
            recipients = None
        group_id = item.get("group_id") if chat_type == "group" else None
        group_name = item.get("group_name") if chat_type == "group" else None
        peer_id = item.get("peer_id") if chat_type == "direct" else None
        timestamp = float(item.get("timestamp") or 0)

        await self._store_message(
            message_id=message_id,
            chat_type=chat_type,
            peer_id=sender_id if chat_type == "direct" else None,
            group_id=group_id if isinstance(group_id, str) else None,
            group_name=group_name if isinstance(group_name, str) else None,
            sender_id=sender_id,
            recipients=recipients,
            direction="in",
            timestamp=timestamp,
            text=text_raw,
            system=system,
            kind=kind if isinstance(kind, str) else None,
        )

        self._track_peer_id(sender_id)
        if not system:
            self.metrics.record_receive()
            self._publish_metrics()
        event_payload = {
            "from": sender_id,
            "to": peer_id,
            "text": text,
            "timestamp": timestamp,
            "chat_type": chat_type,
            "system": system,
            "kind": kind,
            "message_id": message_id,
            "synced": True,
        }
        if chat_type == "group":
            if recipients:
                event_payload["recipients"] = recipients
            if isinstance(group_name, str):
                event_payload["group_name"] = group_name
            if isinstance(group_id, str):
                event_payload["group_id"] = group_id
        self._publish_event("message_received", event_payload)
        return timestamp

    def _record_seen(self, message_id: str) -> bool:
        if message_id in self._seen_messages:
            return False
        self._seen_messages[message_id] = time.time()
        return True

    async def send_bootstrap_heartbeat(self) -> None:
        if not self._bootstrap_writer or self._bootstrap_writer.is_closing():
            return
        msg = new_message(MSG_HEARTBEAT, self.peer_id)
        await self._send_bootstrap_message(msg)

    async def send_peer_heartbeats(self) -> None:
        peers = await self.peer_list.list_peers(status="online")
        for peer in peers:
            if not peer.writer or peer.writer.is_closing():
                continue
            msg = new_message(MSG_HEARTBEAT, self.peer_id, to=peer.peer_id)
            await send_message(peer.writer, msg)

    async def prune_offline_peers(self, timeout: int) -> None:
        now = time.time()
        peers = await self.peer_list.list_peers()
        for peer in peers:
            if now - peer.last_seen > timeout:
                await self.peer_list.mark_offline(peer.peer_id)
                self._publish_event("peer_update", {"peer_id": peer.peer_id, "status": "offline"})
        self._prune_seen()

    def _prune_seen(self) -> None:
        now = time.time()
        for message_id, seen_at in list(self._seen_messages.items()):
            if now - seen_at > self._seen_ttl:
                self._seen_messages.pop(message_id, None)

    async def _bootstrap_connect_loop(self) -> None:
        delay = 2
        while not self._stop_event.is_set():
            if not self._bootstrap_writer or self._bootstrap_writer.is_closing():
                ok = await self._connect_bootstrap_once()
                if ok:
                    delay = 2
                else:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
            await asyncio.sleep(1)

    async def _connect_bootstrap_once(self) -> bool:
        try:
            reader, writer = await asyncio.open_connection(self.bootstrap_host, self.bootstrap_port)
        except Exception as exc:
            self.logger.warning("bootstrap unavailable: %s", exc)
            return False

        self._bootstrap_reader = reader
        self._bootstrap_writer = writer

        await self._send_bootstrap_message(
            new_message(
                MSG_REGISTER,
                self.peer_id,
                payload={"host": self.advertise_host, "port": self.port},
            )
        )
        ack = await self._read_bootstrap_until({MSG_ACK})
        if not ack:
            return False

        await self._send_bootstrap_message(new_message(MSG_GET_PEERS, self.peer_id))
        await self._read_bootstrap_until({MSG_PEER_LIST})

        asyncio.create_task(self._bootstrap_listener())
        return True

    async def _send_bootstrap_message(self, msg: Message) -> None:
        if not self._bootstrap_writer or self._bootstrap_writer.is_closing():
            return
        async with self._bootstrap_lock:
            await send_message(self._bootstrap_writer, msg)

    async def _read_bootstrap_until(self, types: set[str]) -> Optional[Message]:
        if not self._bootstrap_reader:
            return None
        while True:
            line = await self._bootstrap_reader.readline()
            if not line:
                return None
            try:
                msg = decode_message_line(line)
            except MessageError:
                continue
            await self._handle_bootstrap_message(msg)
            if msg.type in types:
                return msg

    async def _bootstrap_listener(self) -> None:
        if not self._bootstrap_reader:
            return
        try:
            while True:
                line = await self._bootstrap_reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message_line(line)
                except MessageError:
                    continue
                await self._handle_bootstrap_message(msg)
        except asyncio.CancelledError:
            raise
        finally:
            if self._bootstrap_writer:
                self._bootstrap_writer.close()
                await self._bootstrap_writer.wait_closed()
            self._bootstrap_writer = None
            self._bootstrap_reader = None

    async def _handle_bootstrap_message(self, msg: Message) -> None:
        if msg.type == MSG_PEER_LIST:
            await self._apply_peer_list(msg.payload)
            await self._connect_known_peers()
            return
        if msg.type == MSG_PEER_JOINED:
            payload = msg.payload or {}
            peer_id = payload.get("id")
            host = payload.get("host")
            port = payload.get("port")
            if peer_id and host and port:
                await self.peer_list.upsert_peer(peer_id, host, int(port))
                self._track_peer_id(peer_id)
                self._publish_event("peer_update", {"peer_id": peer_id, "host": host, "port": int(port), "status": "online"})
            return
        if msg.type == MSG_PEER_LEFT:
            payload = msg.payload or {}
            peer_id = payload.get("id")
            if peer_id:
                await self.peer_list.mark_offline(peer_id)
                self._publish_event("peer_update", {"peer_id": peer_id, "status": "offline"})
            return

    async def _connect_known_peers(self) -> None:
        peers = await self.peer_list.list_peers(status="online")
        for peer in peers[: self.connect_limit]:
            if peer.peer_id == self.peer_id:
                continue
            if peer.writer and not peer.writer.is_closing():
                continue
            await self._connect_peer(peer.peer_id, peer.host, peer.port)

    async def _connect_seeds(self) -> None:
        for seed in self.seeds:
            if ":" not in seed:
                continue
            host, port_str = seed.split(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                continue
            temp_id = f"seed-{host}-{port}"
            await self.peer_list.upsert_peer(temp_id, host, port)
            await self._connect_peer(temp_id, host, port)
        await self._broadcast_peer_exchange()

    async def _broadcast_peer_exchange(self) -> None:
        peers = await self.peer_list.list_peers(status="online")
        for peer in peers:
            if not peer.writer or peer.writer.is_closing():
                continue
            msg = new_message(MSG_PEER_EXCHANGE, self.peer_id)
            await send_message(peer.writer, msg)

    async def _send_unregister(self) -> None:
        if not self._bootstrap_writer or self._bootstrap_writer.is_closing():
            return
        msg = new_message(MSG_UNREGISTER, self.peer_id)
        await self._send_bootstrap_message(msg)

    @staticmethod
    def _resolve_advertise_host(host: str, advertise_host: str, bootstrap_host: str, seeds: list[str]) -> str:
        if advertise_host:
            return advertise_host
        if host not in {"0.0.0.0", "::", ""}:
            return host

        candidates: list[str] = []
        if bootstrap_host and bootstrap_host not in {"127.0.0.1", "localhost"}:
            candidates.append(bootstrap_host)
        for seed in seeds:
            if ":" not in seed:
                continue
            seed_host, _ = seed.split(":", 1)
            if seed_host and seed_host not in {"127.0.0.1", "localhost"}:
                candidates.append(seed_host)

        for target in candidates:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect((target, 1))
                    local_ip = sock.getsockname()[0]
                    if local_ip and local_ip != "127.0.0.1":
                        return local_ip
            except OSError:
                continue

        return "127.0.0.1"
