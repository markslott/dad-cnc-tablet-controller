from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    backend: str
    host: str
    port: int
    pin: str | None
    watchdog_ms: int
    dro_hz: float
    modbus_host: str = "127.0.0.1"
    modbus_port: int = 1502

    @property
    def pin_required(self) -> bool:
        return bool(self.pin)

    @property
    def watchdog_s(self) -> float:
        return self.watchdog_ms / 1000.0


def load_settings() -> Settings:
    pin = os.environ.get("MACH3_PIN", "").strip()
    return Settings(
        backend=os.environ.get("MACH3_BACKEND", "mock").strip().lower() or "mock",
        host=os.environ.get("MACH3_HOST", "0.0.0.0"),
        port=_env_int("MACH3_PORT", 8080),
        pin=pin or None,
        watchdog_ms=_env_int("MACH3_WATCHDOG_MS", 200),
        dro_hz=float(os.environ.get("MACH3_DRO_HZ", "10")),
        modbus_host=os.environ.get("MACH3_MODBUS_HOST", "127.0.0.1").strip() or "127.0.0.1",
        modbus_port=_env_int("MACH3_MODBUS_PORT", 1502),
    )
