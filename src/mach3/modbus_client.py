from __future__ import annotations

import threading

from src.mach3.client import Dro, MachineStatus
from src.mach3.modbus_map import (
    CONNECTED_TIMEOUT_S,
    HR_ALIVE,
    HR_ESTOP,
    HR_FRO,
    HR_FRO_ACTUAL,
    HR_IN_CYCLE,
    HR_JOG,
    HR_JOG_MODE,
    HR_RESET,
    HR_RESET_OK,
    HR_STEP_PULSE,
    HR_STEP_SIZE,
    HR_STOP,
    HR_X_HI,
    HR_Y_HI,
    HR_Z_HI,
    JOG_NEG,
    JOG_OFF,
    JOG_POS,
    MODE_CONT,
    MODE_STEP,
    PULSE_S,
    STEP_SCALE,
    HoldingRegisters,
    decode_dro,
    pack_step_jog,
)
from src.mach3.oem import AXIS_NAMES, JOG_DIR_NEG, JOG_DIR_POS, Axis

_VALID_STEP_SIZES = (0.001, 0.01, 0.1, 1.0)


class ModbusMach3Client:
    """Mach3Client backed by a Modbus TCP slave Mach3 polls as master."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 1502,
        *,
        registers: HoldingRegisters | None = None,
        start_server: bool = True,
        pulse_s: float = PULSE_S,
        connected_timeout_s: float = CONNECTED_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._port = port
        self._registers = registers or HoldingRegisters()
        self._pulse_s = pulse_s
        self._connected_timeout_s = connected_timeout_s
        self._jog_rate = 50.0
        self._pulse_timers: dict[int, threading.Timer] = {}
        self._closed = False
        self._server_error: str | None = None
        self._thread: threading.Thread | None = None
        self._registers.set(HR_ALIVE, 1)
        if start_server:
            self._start_server()

    def _start_server(self) -> None:
        try:
            from pymodbus.datastore import (
                ModbusSequentialDataBlock,
                ModbusServerContext,
                ModbusSlaveContext,
            )
            from pymodbus.server import StartTcpServer
        except ImportError as exc:
            self._server_error = (
                f"pymodbus is required for MACH3_BACKEND=modbus ({exc})"
            )
            return

        block = _SharedHoldingBlock(self._registers)
        empty = ModbusSequentialDataBlock(0, [0] * 1)
        store = ModbusSlaveContext(di=empty, co=empty, ir=empty, hr=block, zero_mode=True)
        context = ModbusServerContext(slaves=store, single=True)

        def _serve() -> None:
            try:
                StartTcpServer(context=context, address=(self._host, self._port))
            except Exception as exc:  # noqa: BLE001
                self._server_error = f"Modbus TCP listen failed on {self._host}:{self._port} ({exc})"

        self._thread = threading.Thread(target=_serve, name="mach3-modbus", daemon=True)
        self._thread.start()

    def get_dro(self) -> Dro:
        return self.get_status().dro

    def get_status(self) -> MachineStatus:
        regs = self._registers
        x = decode_dro(regs.get(HR_X_HI), regs.get(HR_X_HI + 1))
        y = decode_dro(regs.get(HR_Y_HI), regs.get(HR_Y_HI + 1))
        z = decode_dro(regs.get(HR_Z_HI), regs.get(HR_Z_HI + 1))
        connected = self._registers.connected(self._connected_timeout_s) and not self._server_error
        jogging_axes = [axis for axis, addr in enumerate(HR_JOG) if regs.get(addr) != JOG_OFF]
        estop = bool(regs.get(HR_ESTOP))
        reset_ok = bool(regs.get(HR_RESET_OK))
        in_cycle = bool(regs.get(HR_IN_CYCLE))
        fro = float(regs.get(HR_FRO_ACTUAL) if connected else regs.get(HR_FRO))
        mode = "step" if regs.get(HR_JOG_MODE) == MODE_STEP else "cont"
        step = regs.get(HR_STEP_SIZE) / STEP_SCALE if regs.get(HR_STEP_SIZE) else 0.01
        error = self._server_error
        if not connected and error is None:
            error = f"waiting for Mach3 TCP Modbus on {self._host}:{self._port}"
        return MachineStatus(
            dro=Dro(x, y, z),
            feed_override=_clamp(fro, 0.0, 200.0),
            jog_rate=self._jog_rate,
            jog_mode=mode,
            step_size=step,
            estop=estop,
            reset_ok=reset_ok and not estop,
            in_cycle=in_cycle,
            stopped=estop or not reset_ok,
            jogging=bool(jogging_axes),
            connected=connected,
            backend="modbus",
            error=error,
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
        self._registers.set(HR_JOG_MODE, MODE_CONT)
        self._registers.set(HR_JOG[axis], JOG_POS if direction == JOG_DIR_POS else JOG_NEG)

    def jog_off(self, axis: int) -> None:
        self._require_axis(axis)
        self._registers.set(HR_JOG[axis], JOG_OFF)

    def jog_off_all(self) -> None:
        for addr in HR_JOG:
            self._registers.set(addr, JOG_OFF)
        self._registers.set(HR_STEP_PULSE, 0)

    def step_jog(self, axis: int, direction: int, step_size: float) -> None:
        self._require_axis(axis)
        if not self.can_jog():
            raise PermissionError("machine not ready to jog")
        self.set_jog_mode("step")
        self.set_step_size(step_size)
        self._pulse(HR_STEP_PULSE, pack_step_jog(axis, direction))

    def set_feed_override(self, percent: float) -> None:
        self._registers.set(HR_FRO, int(round(_clamp(percent, 0.0, 200.0))))

    def set_jog_rate(self, percent: float) -> None:
        self._jog_rate = _clamp(percent, 1.0, 100.0)

    def set_jog_mode(self, mode: str) -> None:
        if mode not in ("cont", "step"):
            raise ValueError("jog mode must be 'cont' or 'step'")
        self._registers.set(HR_JOG_MODE, MODE_STEP if mode == "step" else MODE_CONT)
        if mode == "step":
            self.jog_off_all()

    def set_step_size(self, size: float) -> None:
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - size))
        self._registers.set(HR_STEP_SIZE, int(round(nearest * STEP_SCALE)))

    def do_stop(self) -> None:
        self.jog_off_all()
        self._pulse(HR_STOP, 1)

    def do_reset(self) -> None:
        self.jog_off_all()
        self._pulse(HR_RESET, 1)

    def close(self) -> None:
        self._closed = True
        self.jog_off_all()
        self._registers.set(HR_ALIVE, 0)
        for timer in list(self._pulse_timers.values()):
            timer.cancel()
        self._pulse_timers.clear()
        if self._thread is not None:
            try:
                from pymodbus.server import ServerStop

                ServerStop()
            except Exception:
                pass
            self._thread.join(timeout=2.0)
            self._thread = None

    def _pulse(self, addr: int, value: int) -> None:
        old = self._pulse_timers.pop(addr, None)
        if old is not None:
            old.cancel()
        self._registers.set(addr, value)

        def _clear(a: int = addr) -> None:
            self._registers.set(a, 0)
            self._pulse_timers.pop(a, None)

        timer = threading.Timer(self._pulse_s, _clear)
        timer.daemon = True
        self._pulse_timers[addr] = timer
        timer.start()

    def _require_axis(self, axis: int) -> None:
        if axis not in (Axis.X, Axis.Y, Axis.Z):
            raise ValueError(f"invalid axis {axis}; expected 0/1/2 ({AXIS_NAMES})")


class _SharedHoldingBlock:
    """pymodbus sequential block that shares HoldingRegisters.values."""

    def __init__(self, registers: HoldingRegisters) -> None:
        self.address = 0
        self.values = registers.values
        self.default_value = 0
        self._registers = registers

    def validate(self, address: int, count: int = 1) -> bool:
        return 0 <= address and (address + count) <= self._registers.size

    def getValues(self, address: int, count: int = 1) -> list[int]:
        return self._registers.get_range(address, count)

    def setValues(self, address: int, values: list[int] | int) -> None:
        if not isinstance(values, list):
            values = [values]
        self._registers.set_range(address, [int(v) for v in values])

    def reset(self) -> None:
        self._registers.set_range(0, [0] * self._registers.size)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))
