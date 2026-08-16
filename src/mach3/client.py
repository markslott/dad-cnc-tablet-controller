from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Dro:
    x: float
    y: float
    z: float

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass
class MachineStatus:
    dro: Dro
    feed_override: float
    jog_rate: float
    jog_mode: str  # "cont" | "step"
    step_size: float
    estop: bool
    reset_ok: bool
    in_cycle: bool
    stopped: bool
    jogging: bool
    connected: bool
    backend: str
    error: str | None = None
    jogging_axes: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dro": self.dro.as_dict(),
            "feed_override": self.feed_override,
            "jog_rate": self.jog_rate,
            "jog_mode": self.jog_mode,
            "step_size": self.step_size,
            "estop": self.estop,
            "reset_ok": self.reset_ok,
            "in_cycle": self.in_cycle,
            "stopped": self.stopped,
            "jogging": self.jogging,
            "connected": self.connected,
            "backend": self.backend,
            "error": self.error,
            "jogging_axes": self.jogging_axes,
            "can_jog": self.can_jog,
        }

    @property
    def can_jog(self) -> bool:
        return (
            self.connected
            and self.reset_ok
            and not self.estop
            and not self.in_cycle
            and not self.stopped
            and self.error is None
        )


@runtime_checkable
class Mach3Client(Protocol):
    def get_dro(self) -> Dro: ...

    def get_status(self) -> MachineStatus: ...

    def jog_on(self, axis: int, direction: int) -> None: ...

    def jog_off(self, axis: int) -> None: ...

    def jog_off_all(self) -> None: ...

    def step_jog(self, axis: int, direction: int, step_size: float) -> None: ...

    def set_feed_override(self, percent: float) -> None: ...

    def set_jog_rate(self, percent: float) -> None: ...

    def set_jog_mode(self, mode: str) -> None: ...

    def set_step_size(self, size: float) -> None: ...

    def do_stop(self) -> None: ...

    def do_reset(self) -> None: ...

    def can_jog(self) -> bool: ...

    def close(self) -> None: ...
