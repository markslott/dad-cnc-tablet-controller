from __future__ import annotations

import threading
import time

from src.mach3.client import Dro, MachineStatus
from src.mach3.oem import JOG_DIR_NEG, JOG_DIR_POS, Axis

# Mock rapid in units/minute at 100% jog rate (inches or mm — display only).
_MOCK_MAX_IPM = 60.0
_VALID_STEP_SIZES = (0.001, 0.01, 0.1, 1.0)


class MockMach3Client:
    """In-memory Mach3 stand-in so the PWA can be developed off the mill PC."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._feed_override = 100.0
        self._jog_rate = 50.0
        self._jog_mode = "cont"
        self._step_size = 0.01
        self._estop = False
        self._reset_ok = True
        self._in_cycle = False
        self._stopped = False
        self._jogging: dict[int, int] = {}  # axis -> direction
        self._last_tick = time.monotonic()
        self._connected = True
        self._error: str | None = None

    def _tick_unlocked(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        if not self._jogging:
            return
        speed = _MOCK_MAX_IPM * (self._jog_rate / 100.0) * (self._feed_override / 100.0)
        delta = speed * dt / 60.0
        for axis, direction in self._jogging.items():
            sign = 1.0 if direction == JOG_DIR_POS else -1.0
            if axis == Axis.X:
                self._x += sign * delta
            elif axis == Axis.Y:
                self._y += sign * delta
            elif axis == Axis.Z:
                self._z += sign * delta

    def get_dro(self) -> Dro:
        with self._lock:
            self._tick_unlocked()
            return Dro(self._x, self._y, self._z)

    def get_status(self) -> MachineStatus:
        with self._lock:
            self._tick_unlocked()
            return MachineStatus(
                dro=Dro(self._x, self._y, self._z),
                feed_override=self._feed_override,
                jog_rate=self._jog_rate,
                jog_mode=self._jog_mode,
                step_size=self._step_size,
                estop=self._estop,
                reset_ok=self._reset_ok,
                in_cycle=self._in_cycle,
                stopped=self._stopped,
                jogging=bool(self._jogging),
                connected=self._connected,
                backend="mock",
                error=self._error,
                jogging_axes=sorted(self._jogging),
            )

    def can_jog(self) -> bool:
        return self.get_status().can_jog

    def jog_on(self, axis: int, direction: int) -> None:
        if axis not in (Axis.X, Axis.Y, Axis.Z):
            raise ValueError(f"invalid axis {axis}")
        if direction not in (JOG_DIR_POS, JOG_DIR_NEG):
            raise ValueError(f"invalid direction {direction}")
        with self._lock:
            self._tick_unlocked()
            if not self._can_jog_unlocked():
                raise PermissionError("machine not ready to jog")
            self._jog_mode = "cont"
            self._jogging[axis] = direction

    def jog_off(self, axis: int) -> None:
        with self._lock:
            self._tick_unlocked()
            self._jogging.pop(axis, None)

    def jog_off_all(self) -> None:
        with self._lock:
            self._tick_unlocked()
            self._jogging.clear()

    def step_jog(self, axis: int, direction: int, step_size: float) -> None:
        if axis not in (Axis.X, Axis.Y, Axis.Z):
            raise ValueError(f"invalid axis {axis}")
        sign = 1.0 if direction == JOG_DIR_POS else -1.0
        with self._lock:
            self._tick_unlocked()
            if not self._can_jog_unlocked():
                raise PermissionError("machine not ready to jog")
            self._jogging.pop(axis, None)
            delta = sign * abs(step_size)
            if axis == Axis.X:
                self._x += delta
            elif axis == Axis.Y:
                self._y += delta
            else:
                self._z += delta

    def set_feed_override(self, percent: float) -> None:
        with self._lock:
            self._feed_override = _clamp(percent, 0.0, 200.0)

    def set_jog_rate(self, percent: float) -> None:
        with self._lock:
            self._jog_rate = _clamp(percent, 1.0, 100.0)

    def set_jog_mode(self, mode: str) -> None:
        if mode not in ("cont", "step"):
            raise ValueError("jog mode must be 'cont' or 'step'")
        with self._lock:
            self._jog_mode = mode
            if mode == "step":
                self._jogging.clear()

    def set_step_size(self, size: float) -> None:
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - size))
        with self._lock:
            self._step_size = nearest

    def do_stop(self) -> None:
        with self._lock:
            self._tick_unlocked()
            self._jogging.clear()
            self._stopped = True
            self._in_cycle = False

    def do_reset(self) -> None:
        with self._lock:
            self._jogging.clear()
            self._stopped = False
            self._estop = False
            self._reset_ok = True
            self._in_cycle = False
            self._error = None

    def close(self) -> None:
        self.jog_off_all()

    def _can_jog_unlocked(self) -> bool:
        return (
            self._connected
            and self._reset_ok
            and not self._estop
            and not self._in_cycle
            and not self._stopped
            and self._error is None
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
