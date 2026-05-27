from __future__ import annotations

import asyncio
import uuid

import typer

from bootstrap.server import BootstrapServer
from common.config import load_config
from common.logger import setup_logging
from peer.node import PeerNode

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def bootstrap(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(9000, help="Bind port"),
    heartbeat_timeout: int = typer.Option(15, help="Heartbeat timeout in seconds"),
    log_level: str = typer.Option("INFO", help="Log level"),
    log_file: str = typer.Option("", help="Optional log file path"),
    log_format: str = typer.Option("text", help="Log format: text|json"),
    config: str = typer.Option("", help="Config file (.json or .yaml)"),
    profile: str = typer.Option("", help="Config profile name"),
) -> None:
    config_data = load_config(config, profile=profile) if config else load_config(None)
    host = config_data.get("host", host)
    port = int(config_data.get("port", port))
    heartbeat_timeout = int(config_data.get("heartbeat_timeout", heartbeat_timeout))
    log_level = config_data.get("log_level", log_level)
    log_file = config_data.get("log_file", log_file) or None
    log_format = config_data.get("log_format", log_format)

    logger = setup_logging("bootstrap", log_level, log_file, log_format=log_format)
    server = BootstrapServer(host, port, heartbeat_timeout, logger)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("bootstrap stopped")


@app.command()
def peer(
    peer_id: str = typer.Option("", help="Peer ID"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    advertise_host: str = typer.Option("", help="Host to advertise to peers"),
    port: int = typer.Option(0, help="Bind port (0 for random)"),
    bootstrap_host: str = typer.Option("127.0.0.1", help="Bootstrap host"),
    bootstrap_port: int = typer.Option(9000, help="Bootstrap port"),
    seed: list[str] = typer.Option([], help="Seed peers in host:port format"),
    connect_limit: int = typer.Option(50, help="Max peers to connect"),
    heartbeat_interval: int = typer.Option(5, help="Heartbeat interval in seconds"),
    heartbeat_timeout: int = typer.Option(15, help="Peer timeout in seconds"),
    log_level: str = typer.Option("INFO", help="Log level"),
    log_file: str = typer.Option("", help="Optional log file path"),
    log_format: str = typer.Option("text", help="Log format: text|json"),
    config: str = typer.Option("", help="Config file (.json or .yaml)"),
    profile: str = typer.Option("", help="Config profile name"),
    web_host: str = typer.Option("0.0.0.0", help="Web UI bind host"),
    web_port: int = typer.Option(8080, help="Web UI port (0 to disable)"),
    no_cli: bool = typer.Option(False, help="Disable CLI prompt"),
) -> None:
    config_data = load_config(config, profile=profile) if config else load_config(None)

    peer_id = peer_id or config_data.get("peer_id") or f"peer-{uuid.uuid4().hex[:6]}"
    host = config_data.get("host", host)
    advertise_host = config_data.get("advertise_host", advertise_host)
    port = int(config_data.get("port", port))
    bootstrap_host = config_data.get("bootstrap_host", bootstrap_host)
    bootstrap_port = int(config_data.get("bootstrap_port", bootstrap_port))
    connect_limit = int(config_data.get("connect_limit", connect_limit))
    heartbeat_interval = int(config_data.get("heartbeat_interval", heartbeat_interval))
    heartbeat_timeout = int(config_data.get("heartbeat_timeout", heartbeat_timeout))
    log_level = config_data.get("log_level", log_level)
    log_file = config_data.get("log_file", log_file) or None
    log_format = config_data.get("log_format", log_format)

    seed = list(config_data.get("seeds", seed))
    web_host = config_data.get("web_host", web_host)
    web_port = int(config_data.get("web_port", web_port))
    enable_cli = config_data.get("enable_cli", not no_cli)

    logger = setup_logging(peer_id, log_level, log_file, log_format=log_format)
    node = PeerNode(
        peer_id=peer_id,
        host=host,
        advertise_host=advertise_host,
        port=port,
        bootstrap_host=bootstrap_host,
        bootstrap_port=bootstrap_port,
        seeds=seed,
        connect_limit=connect_limit,
        heartbeat_interval=heartbeat_interval,
        heartbeat_timeout=heartbeat_timeout,
        logger=logger,
        web_host=web_host,
        web_port=web_port,
        cli_enabled=enable_cli,
    )

    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        logger.info("peer stopped")


if __name__ == "__main__":
    app()
