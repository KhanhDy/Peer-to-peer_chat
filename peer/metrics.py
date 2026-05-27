from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PeerMetrics:
    started_at: float = field(default_factory=time.time)
    messages_sent: int = 0
    messages_received: int = 0
    ack_success: int = 0
    ack_timeout: int = 0
    ack_latency_ms_total: float = 0.0
    ack_latency_ms_last: float = 0.0
    connect_failures: int = 0

    def record_send(self) -> None:
        self.messages_sent += 1

    def record_receive(self) -> None:
        self.messages_received += 1

    def record_ack_latency(self, latency_ms: float) -> None:
        self.ack_success += 1
        self.ack_latency_ms_total += latency_ms
        self.ack_latency_ms_last = latency_ms

    def record_ack_timeout(self) -> None:
        self.ack_timeout += 1

    def record_connect_failure(self) -> None:
        self.connect_failures += 1

    def snapshot(self) -> Dict[str, float | int]:
        avg_latency = 0.0
        if self.ack_success:
            avg_latency = self.ack_latency_ms_total / self.ack_success
        return {
            "uptime_sec": int(time.time() - self.started_at),
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "ack_success": self.ack_success,
            "ack_timeout": self.ack_timeout,
            "ack_latency_ms_avg": round(avg_latency, 2),
            "ack_latency_ms_last": round(self.ack_latency_ms_last, 2),
            "connect_failures": self.connect_failures,
        }
