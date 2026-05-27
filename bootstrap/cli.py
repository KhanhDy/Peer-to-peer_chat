from __future__ import annotations

import asyncio

import typer

from bootstrap.server import BootstrapServer
from common.config import load_config
from common.logger import setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Bootstrap server commands."""


@app.command()
def run(
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
