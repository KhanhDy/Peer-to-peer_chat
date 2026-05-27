# P2P Chat System

Production-oriented P2P chat system with a bootstrap registry and asyncio-based peers.

## Features
- Bootstrap server for peer discovery
- Peer nodes with bidirectional TCP connections
- Direct and group chat with ACK and retries
- Heartbeats and offline detection
- Peer exchange when bootstrap is unavailable
- CLI with helpful commands

## Install

```bash
pip install -r requirements.txt
```

## Run

Start the bootstrap server:

```bash
python -m bootstrap run --host 0.0.0.0 --port 9000
```

Start peers:

```bash
python -m peer run --peer-id peer_a --host 0.0.0.0 --port 5001 --advertise-host <your_device_ip> --bootstrap-host <bootstrap_server_ip> --web-host 0.0.0.0 --web-port 8081
```

Notes:
- `advertise_host` must be reachable by other peers (LAN or public IP). If omitted, the peer will try to auto-detect using the bootstrap/seed target.
- Open the peer port (e.g. 5001) and web port (e.g. 8081) in the firewall.
- If you use `.env`, set `P2P_ADVERTISE_HOST` and `P2P_BOOTSTRAP_HOST` per machine.


## Web UI

Open the peer UI in a browser:

```
http://<web_host>:<web_port>
```

## Configuration

Optional JSON or YAML config file:

```json
{
  "peer_id": "peer_a",
  "host": "0.0.0.0",
  "advertise_host": "127.0.0.1",
  "port": 5001,
  "bootstrap_host": "127.0.0.1",
  "bootstrap_port": 9000,
  "heartbeat_interval": 5,
  "heartbeat_timeout": 15,
  "connect_limit": 50,
  "seeds": ["127.0.0.1:5002"],
  "web_host": "0.0.0.0",
  "web_port": 8081,
  "log_level": "INFO",
  "log_file": "logs/peer_a.log",
  "log_format": "text"
}
```

Run with:

```bash
python -m peer run --config config.json
```

Profile example (config.json):

```json
{
  "host": "0.0.0.0",
  "profiles": {
    "lab": {"bootstrap_host": "192.168.1.10"}
  }
}
```

```bash
python -m peer run --config config.json --profile lab
```