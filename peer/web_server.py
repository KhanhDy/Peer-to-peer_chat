from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


class PeerWebServer:
    def __init__(self, node, host: str, port: int, logger) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.logger = logger
        self._task: Optional[asyncio.Task] = None
        self._server: Optional[uvicorn.Server] = None

        self.app = FastAPI(title="P2P Peer", docs_url=None, redoc_url=None)
        base_dir = Path(__file__).resolve().parent / "web"
        assets_dir = base_dir / "assets"

        self.app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @self.app.get("/")
        async def index() -> FileResponse:
            return FileResponse(base_dir / "index.html")

        @self.app.get("/api/peers")
        async def peers() -> JSONResponse:
            data = await self.node.get_peers_snapshot()
            return JSONResponse(data)

        @self.app.get("/api/metrics")
        async def metrics() -> JSONResponse:
            return JSONResponse(self.node.metrics.snapshot())

        @self.app.get("/api/me")
        async def me() -> JSONResponse:
            return JSONResponse({"peer_id": self.node.peer_id})

        @self.app.get("/api/history")
        async def history(chat_type: str, chat_id: str) -> JSONResponse:
            data = await self.node.get_history(chat_type, chat_id)
            return JSONResponse(data)

        @self.app.get("/api/recent")
        async def recent() -> JSONResponse:
            data = await self.node.get_recent_direct_peers()
            return JSONResponse({"direct_peers": data})

        @self.app.post("/api/chat")
        async def chat(payload: Dict[str, Any]) -> JSONResponse:
            peer_id = payload.get("peer_id")
            message = payload.get("message")
            if not peer_id or not message:
                return JSONResponse({"ok": False, "error": "peer_id and message required"}, status_code=400)
            peer_id = str(peer_id).strip()
            message = str(message).strip()
            if not peer_id or not message:
                return JSONResponse({"ok": False, "error": "peer_id and message required"}, status_code=400)
            ok = await self.node.send_chat(peer_id, message)
            if not ok:
                return JSONResponse({"ok": False, "error": "send failed"}, status_code=502)
            return JSONResponse({"ok": True})

        @self.app.post("/api/group")
        async def group(payload: Dict[str, Any]) -> JSONResponse:
            peer_ids = payload.get("peer_ids")
            message = payload.get("message")
            group_name = payload.get("group_name")
            group_id = payload.get("group_id")
            system = payload.get("system")
            kind = payload.get("kind")
            if not peer_ids or not message:
                if not system:
                    return JSONResponse({"ok": False, "error": "peer_ids and message required"}, status_code=400)
            if not isinstance(peer_ids, list):
                return JSONResponse({"ok": False, "error": "peer_ids must be list"}, status_code=400)
            if message is None:
                message = ""
            message = str(message).strip()
            if not message and not system:
                return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
            await self.node.send_group(
                [str(pid).strip() for pid in peer_ids],
                message,
                group_name=str(group_name).strip() if isinstance(group_name, str) else None,
                group_id=str(group_id).strip() if isinstance(group_id, str) else None,
                system=bool(system),
                kind=str(kind).strip() if isinstance(kind, str) else None,
            )
            return JSONResponse({"ok": True})

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            queue = await self.node.events.subscribe()
            try:
                await websocket.send_text(json.dumps({"type": "peers_snapshot", "payload": await self.node.get_peers_snapshot()}))
                await websocket.send_text(json.dumps({"type": "metrics_snapshot", "payload": self.node.metrics.snapshot()}))
                while True:
                    event = await queue.get()
                    await websocket.send_text(json.dumps(event))
            except WebSocketDisconnect:
                pass
            finally:
                await self.node.events.unsubscribe(queue)

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        self.logger.info("web ui listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if not self._server:
            return
        self._server.should_exit = True
        if self._task:
            await self._task
