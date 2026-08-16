"""Mach3Client mailbox for macropump.m1s.

ArtSoft never documented the .brn format. Mach3 VB also cannot reliably
POST HTTP, so the pump exchanges two text files under C:\\Mach3.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

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
STATUS_NAME = "pendant-status.txt"
CMD_NAME = "pendant-cmd.txt"
_VALID_STEP_SIZES = (0.001, 0.01, 0.1, 1.0)
_WAIT = (
    "waiting for Mach3 macropump. Tick Run Macro Pump, restart Mach3, "
    "then check C:\\Mach3\\pendant-pump.log — if that file is missing, "
    "macropump.m1s is not in this profile's macros folder."
)


def default_pump_dir() -> Path:
    env = os.environ.get("MACH3_PUMP_DIR", "").strip()
    if env:
        return Path(env)
    win = Path("C:/Mach3")
    if win.is_dir():
        return win
    return Path(os.environ.get("TMPDIR", "/tmp")) / "mach3-pendant-pump"


class PumpMach3Client:
    """Command/status mailbox for Mach3's macropump script."""

    def __init__(
        self,
        *,
        pump_dir: Path | str | None = None,
        connected_timeout_s: float = CONNECTED_TIMEOUT_S,
    ) -> None:
        self._lock = threading.Lock()
        self._timeout_s = connected_timeout_s
        self._dir = Path(pump_dir) if pump_dir is not None else default_pump_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
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
        self._step_until = 0.0
        self._last_pump_at = 0.0
        self._announced = False
        self._write_cmd_unlocked()

    def get_dro(self) -> Dro:
        return self.get_status().dro

    def get_status(self) -> MachineStatus:
        self._read_status_file()
        with self._lock:
            self._write_cmd_unlocked()
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
            self._write_cmd_unlocked()

    def jog_off(self, axis: int) -> None:
        self._require_axis(axis)
        with self._lock:
            self._jog[axis] = JOG_OFF
            self._write_cmd_unlocked()

    def jog_off_all(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None
            self._write_cmd_unlocked()

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
            self._step_until = time.monotonic() + PULSE_S
            self._write_cmd_unlocked()

    def set_feed_override(self, percent: float) -> None:
        with self._lock:
            self._fro_cmd = _clamp(percent, 0.0, 200.0)
            self._write_cmd_unlocked()

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
            self._write_cmd_unlocked()

    def set_step_size(self, size: float) -> None:
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - size))
        with self._lock:
            self._step_size = nearest
            self._write_cmd_unlocked()

    def do_stop(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None
            self._stop_until = time.monotonic() + PULSE_S
            self._write_cmd_unlocked()

    def do_reset(self) -> None:
        with self._lock:
            self._jog = [JOG_OFF, JOG_OFF, JOG_OFF]
            self._step_pulse = None
            self._reset_until = time.monotonic() + PULSE_S
            self._write_cmd_unlocked()
        print("Reset command queued for macropump (OEM 1021).", flush=True)

    def close(self) -> None:
        self.jog_off_all()

    def exchange_pump(self, body: str) -> str:
        report = parse_report(body)
        now = time.monotonic()
        with self._lock:
            self._apply_report_unlocked(report, now)
            line = self._command_line_unlocked(now)
        return line

    def _apply_report_unlocked(
        self,
        report: tuple[float, float, float, bool, bool, bool, float],
        now: float,
    ) -> None:
        first = self._last_pump_at <= 0
        self._last_pump_at = now
        self._x, self._y, self._z = report[0], report[1], report[2]
        self._estop = report[3]
        self._reset_ok = report[4]
        self._in_cycle = report[5]
        self._fro_actual = report[6]
        if first and not self._announced:
            self._announced = True
            print(
                f"Mach3 macropump is talking ({self._dir / STATUS_NAME}).",
                flush=True,
            )

    def _read_status_file(self) -> None:
        path = self._dir / STATUS_NAME
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return
        if age > self._timeout_s:
            return
        try:
            text = path.read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            return
        line = text.splitlines()[0] if text else ""
        if not line:
            return
        try:
            report = parse_report(line)
        except ValueError:
            return
        with self._lock:
            self._apply_report_unlocked(report, time.monotonic())

    def _write_cmd_unlocked(self) -> None:
        line = self._command_line_unlocked(time.monotonic())
        path = self._dir / CMD_NAME
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(line + "\n", encoding="ascii")
            os.replace(tmp, path)
        except OSError:
            pass

    def _command_line_unlocked(self, now: float) -> str:
        stop = 1 if now < self._stop_until else 0
        reset = 1 if now < self._reset_until else 0
        pulse = self._step_pulse if now < self._step_until else None
        return format_commands(
            self._jog[0],
            self._jog[1],
            self._jog[2],
            stop,
            reset,
            int(round(self._fro_cmd)),
            self._mode,
            pulse,
        )

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
