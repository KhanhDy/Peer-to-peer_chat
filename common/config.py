from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
def load_env_file(path: str) -> None:
    if not path:
        return
    file_path = Path(path)
    if not file_path.exists():
        return
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {"\"", "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value



@dataclass
class BootstrapConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    heartbeat_timeout: int = 15
    log_level: str = "INFO"
    log_file: str = ""
    log_format: str = "text"


@dataclass
class PeerConfig:
    peer_id: str = ""
    host: str = "0.0.0.0"
    advertise_host: str = ""
    port: int = 0
    bootstrap_host: str = "127.0.0.1"
    bootstrap_port: int = 9000
    heartbeat_interval: int = 5
    heartbeat_timeout: int = 15
    connect_limit: int = 50
    seeds: list[str] = field(default_factory=list)
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    enable_cli: bool = True
    history_enabled: bool = True
    history_db: str = ""
    encrypt_key: str = ""
    log_level: str = "INFO"
    log_file: str = ""
    log_format: str = "text"


def load_config(path: Optional[str], profile: str = "", env_prefix: str = "P2P_") -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if not path:
        return apply_env_overrides(data, env_prefix)

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    suffix = file_path.suffix.lower()
    raw = file_path.read_text(encoding="utf-8")

    if suffix in {".json"}:
        data = json.loads(raw)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyyaml is required for yaml config files") from exc
        data = yaml.safe_load(raw) or {}
    else:
        raise ValueError("Unsupported config format. Use .json or .yaml/.yml")

    data = apply_profile(data, profile)
    return apply_env_overrides(data, env_prefix)


def apply_profile(data: Dict[str, Any], profile: str) -> Dict[str, Any]:
    if not profile:
        return data
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise KeyError(f"Profile '{profile}' not found")
    profile_data = profiles.get(profile)
    if not isinstance(profile_data, dict):
        raise KeyError(f"Profile '{profile}' not found")
    merged = {k: v for k, v in data.items() if k != "profiles"}
    merged.update(profile_data)
    return merged


def apply_env_overrides(data: Dict[str, Any], env_prefix: str) -> Dict[str, Any]:
    if not env_prefix:
        return data
    overrides = {}
    for key, value in os.environ.items():
        if not key.startswith(env_prefix):
            continue
        field_name = key[len(env_prefix) :].lower()
        overrides[field_name] = value

    return merge_env_overrides(data, overrides)


def merge_env_overrides(data: Dict[str, Any], overrides: Dict[str, str]) -> Dict[str, Any]:
    merged = dict(data)
    for field_name, value in overrides.items():
        if field_name in {"port", "bootstrap_port", "heartbeat_interval", "heartbeat_timeout", "connect_limit", "web_port"}:
            merged[field_name] = int(value)
        elif field_name in {"enable_cli", "history_enabled"}:
            merged[field_name] = parse_bool(value)
        elif field_name in {"seeds"}:
            merged[field_name] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            merged[field_name] = value
    return merged


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
