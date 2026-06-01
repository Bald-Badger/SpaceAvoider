"""Runtime event types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeyPressedEvent:
    key: str
    timestamp: float


@dataclass(frozen=True)
class SensorFaultEvent:
    worker: str
    message: str
    timestamp: float


RuntimeEvent = KeyPressedEvent | SensorFaultEvent

