"""Mach3Client that exchanges state with macropump.m1s over localhost HTTP.

ArtSoft never documented the .brn Brain format, so this is the loadable
stand-in: Mach3 runs the pump script; the script POSTs DRO/LEDs and applies
the command line we return.
"""

from __future__ import annotations

import threading
import time

from src.mach3.client import Dro, MachineStatus
from src.mach3.oem import AXIS_NAMES, JOG_DIR_NEG, JOG_DIR_POS, Axis

DRO_SCALE = 10000
STEP_SCALE = 1000
JOG_OFF = 0
JOG_POS = 1
JOG_NEG = 2
MODE_CONT = 0
MODE_STEP = 1
PULSE_S = 0.4
CONNECTED_TIMEOUT_S = 1.5
_VALID_STEP_SIZES = (0.001, 0.01, 0.1, 1.0)
_WAIT = (
    "waiting for Mach3 macropump. Copy mach3\\macropump.m1s into this profile's "
    "macros folder, then Config → General Config → tick Run Macro Pump."
)


class PumpMach3Client:
    """Command/status mailbox for Mach3's macropump script."""

    def __init__(self, *, connected_timeout_s: float = CONNECTED_TIMEOUT_S) -> None:
        self._lock = threading.Lock()
        self._timeout_s = connected_timeout_s
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._estop = False
        self._reset_ok = False
        self._in_cycle = False
        self._fro_actual = 100.0
        self._fro_cmd = 100.0
        self._jog_rate = 50.0
        self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
        self._mode = MODE_CONT
        self._step_size = 0.01
        self._stop_until = 0.0
        self._reset_until = 0.0
        self._step_pulse: tuple[int, int, float] | None = None
        self._last_pump_at = 0.0
        self._announced = False

    def get_dro(self) -> Dro:
        return self.get_status().dro

    def get_status(self) -> MachineStatus:
        with self._lock:
            connected = self._last_pump_at > 0 and (
                time.monotonic() - self._last_pump_at
            ) <= self._timeout_s
            jogging_axes = [i for i, v in enumerate(self._jog) if v != JOG_OFF]
            reset_ok = self._reset_ok and not self._estop
            return MachineStatus(
                dro=Dro(self._x, self._y, self._z),
                feed_override=self._fro_actual if connected else self._fro_cmd,
                jog_rate=self._jog_rate,
                jog_mode="step" if self._mode == MODE_STEP else "cont",
                step_size=self._step_size,
                estop=self._estop,
                reset_ok=reset_ok,
                in_cycle=self._in_cycle,
                stopped=self._estop or not reset_ok,
                jogging=bool(jogging_axes),
                connected=connected,
                backend="pump",
                error=None if connected else _WAIT,
                jogging_axes=jogging_axes,
            )

    def can_jog(self) -> bool:
        return self.get_status().can_jog

    def jog_on(self, axis: int, direction: int) -> None:
        self._require_axis(axis)
        if direction not in (JOG_DIR_POS, JOG_DIR_NEG):
            raise ValueError(f"invalid direction {direction}")
        if not self.can_jog():
            raise PermissionError("machine not ready to jog")
        with self._lock:
            self._mode = MODE_CONT
            self._jog[axis] = JOG_POS if direction == JOG_DIR_POS else JOG_NEG

    def jog_off(self, axis: int) -> None:
        self._require_axis(axis)
        with self._lock:
            self._jog[axis] = JOG_OFF

    def jog_off_all(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None

    def step_jog(self, axis: int, direction: int, step_size: float) -> None:
        self._require_axis(axis)
        if not self.can_jog():
            raise PermissionError("machine not ready to jog")
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - step_size))
        with self._lock:
            self._mode = MODE_STEP
            self._step_size = nearest
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = (axis, direction, nearest)

    def set_feed_override(self, percent: float) -> None:
        with self._lock:
            self._fro_cmd = _clamp(percent, 0.0, 200.0)

    def set_jog_rate(self, percent: float) -> None:
        with self._lock:
            self._jog_rate = _clamp(percent, 1.0, 100.0)

    def set_jog_mode(self, mode: str) -> None:
        if mode not in ("cont", "step"):
            raise ValueError("jog mode must be 'cont' or 'step'")
        with self._lock:
            self._mode = MODE_STEP if mode == "step" else MODE_CONT
            if mode == "step":
                self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]

    def set_step_size(self, size: float) -> None:
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - size))
        with self._lock:
            self._step_size = nearest

    def do_stop(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None
            self._stop_until = time.monotonic() + PULSE_S

    def do_reset(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None
            self._reset_until = time.monotonic() + PULSE_S
        print("Reset command queued for macropump (OEM 1021).", flush=True)

    def close(self) -> None:
        self.jog_off_all()

    def exchange_pump(self, body: str) -> str:
        report = parse_report(body)
        now = time.monotonic()
        with self._lock:
            first = self._last_pump_at <= 0
            self._last_pump_at = now
            self._x, self._y, self._z = report[0], report[1], report[2]
            self._estop = report[3]
            self._reset_ok = report[4]
            self._in_cycle = report[5]
            self._fro_actual = report[6]
            stop = 1 if now < self._stop_until else 0
            reset = 1 if now < self._reset_until else 0
            pulse = self._step_pulse
            self._step_pulse = None
            line = format_commands(
                self._jog[0],
                self._jog[1],
                self._jog[2],
                stop,
                reset,
                int(round(self._fro_cmd)),
                self._mode,
                pulse,
            )
        if first:
            print("Mach3 macropump is talking to the pendant.", flush=True)
        return line

    def _require_axis(self, axis: int) -> None:
        if axis not in (Axis.X, Axis.Y, Axis.Z):
            raise ValueError(f"invalid axis {axis}; expected 0/1/2 ({AXIS_NAMES})")


def parse_report(body: str) -> tuple[float, float, float, bool, bool, bool, float]:
    parts = [p.strip() for p in (body or "").split("|")]
    if len(parts) < 7:
        raise ValueError("pump report needs x|y|z|estop|resetok|incycle|fro")
    x = int(parts[0]) / DRO_SCALE
    y = int(parts[1]) / DRO_SCALE
    z = int(parts[2]) / DRO_SCALE
    estop = int(parts[3]) != 0
    reset_ok = int(parts[4]) != 0
    in_cycle = int(parts[5]) != 0
    fro = _clamp(float(parts[6]), 0.0, 200.0)
    return x, y, z, estop, reset_ok, in_cycle, fro


def format_commands(
    jx: int,
    jy: int,
    jz: int,
    stop: int,
    reset: int,
    fro: int,
    mode: int,
    step: tuple[int, int, float] | None,
) -> str:
    if step is None:
        spaxis, spdir, spsize = -1, 0, 0
    else:
        spaxis, spdir, step_size = step
        spsize = int(round(step_size * STEP_SCALE))
    return (
        f"{jx}|{jy}|{jz}|{stop}|{reset}|{fro}|{mode}|{spaxis}|{spdir}|{spsize}"
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))
