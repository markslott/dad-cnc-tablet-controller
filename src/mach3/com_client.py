from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import TypeVar

from src.mach3.client import Dro, MachineStatus
from src.mach3.oem import (
    AXIS_NAMES,
    JOG_DIR_NEG,
    JOG_DIR_POS,
    OEM_BTN_JOG_CONT,
    OEM_BTN_JOG_INC,
    OEM_BTN_RESET,
    OEM_BTN_STOP,
    OEM_DRO_FEED_OVERRIDE,
    OEM_DRO_JOG_INCREMENT,
    OEM_LED_ESTOP,
    OEM_LED_IN_CYCLE,
    OEM_LED_RESET_OK,
    Axis,
)

T = TypeVar("T")

_VALID_STEP_SIZES = (0.001, 0.01, 0.1, 1.0)


class ComMach3Client:
    """Talk to a running Mach3 instance via OLE (Mach4.Document).

    All COM calls run on one dedicated STA thread. FastAPI's thread pool must
    not touch the COM object directly.
    """

    def __init__(self, connect_timeout_s: float = 10.0) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._connect_error: str | None = None
        self._script = None
        self._closed = False
        self._jog_rate = 50.0
        self._jog_mode = "cont"
        self._step_size = 0.01
        self._thread = threading.Thread(target=self._com_loop, name="mach3-com", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=connect_timeout_s):
            raise TimeoutError("timed out attaching to Mach3 COM")
        if self._connect_error:
            raise RuntimeError(self._connect_error)

    def _com_loop(self) -> None:
        try:
            import pythoncom
            from win32com.client import GetActiveObject
        except ImportError:
            self._connect_error = (
                "pywin32 is required for MACH3_BACKEND=com. "
                "Install it on the Windows Mach3 PC: pip install pywin32"
            )
            self._ready.set()
            return

        pythoncom.CoInitialize()
        try:
            try:
                # ProgID is Mach4.Document even when talking to Mach3.
                mach = GetActiveObject("Mach4.Document")
                self._script = mach.GetScriptDispatch()
            except Exception as exc:  # noqa: BLE001 — COM errors are opaque
                self._connect_error = (
                    f"could not attach to Mach3 ({exc}). "
                    "Start Mach3 first, then start this server."
                )
                self._ready.set()
                return
            self._ready.set()
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                func, args, kwargs, result_q = job
                try:
                    result_q.put((True, func(*args, **kwargs)))
                except Exception as exc:  # noqa: BLE001
                    result_q.put((False, exc))
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _call(self, func: Callable[..., T], *args, timeout_s: float = 2.0, **kwargs) -> T:
        if self._closed:
            raise RuntimeError("Mach3 COM client is closed")
        result_q: queue.Queue = queue.Queue()
        self._jobs.put((func, args, kwargs, result_q))
        try:
            ok, value = result_q.get(timeout=timeout_s)
        except queue.Empty as exc:
            raise TimeoutError("Mach3 COM call timed out") from exc
        if not ok:
            raise value
        return value

    def _script_call(self, name: str, *args):
        def _invoke():
            method = getattr(self._script, name)
            return method(*args)

        return self._call(_invoke)

    def get_dro(self) -> Dro:
        x = float(self._script_call("GetDRO", Axis.X))
        y = float(self._script_call("GetDRO", Axis.Y))
        z = float(self._script_call("GetDRO", Axis.Z))
        return Dro(x, y, z)

    def _led(self, code: int) -> bool:
        try:
            return bool(int(self._script_call("GetOEMLED", code)))
        except Exception:
            return False

    def get_status(self) -> MachineStatus:
        error: str | None = None
        try:
            dro = self.get_dro()
            try:
                fro = float(self._script_call("GetOEMDRO", OEM_DRO_FEED_OVERRIDE))
            except Exception:
                fro = 100.0
            estop = self._led(OEM_LED_ESTOP)
            in_cycle = self._led(OEM_LED_IN_CYCLE)
            reset_led = self._led(OEM_LED_RESET_OK)
            # Some screensets invert Reset LED meaning. Ready if Reset LED is on
            # and we are not in E-stop. If Reset LED always reads 0, treat
            # "not estop and not in cycle" as ready.
            reset_ok = reset_led or (not estop and not in_cycle)
            connected = True
        except Exception as exc:  # noqa: BLE001
            dro = Dro(0.0, 0.0, 0.0)
            fro = 100.0
            estop = True
            in_cycle = False
            reset_ok = False
            connected = False
            error = str(exc)

        return MachineStatus(
            dro=dro,
            feed_override=_clamp(fro, 0.0, 200.0),
            jog_rate=self._jog_rate,
            jog_mode=self._jog_mode,
            step_size=self._step_size,
            estop=estop,
            reset_ok=reset_ok and not estop,
            in_cycle=in_cycle,
            stopped=estop or not reset_ok,
            jogging=False,
            connected=connected,
            backend="com",
            error=error,
        )

    def can_jog(self) -> bool:
        return self.get_status().can_jog

    def jog_on(self, axis: int, direction: int) -> None:
        self._require_axis(axis)
        if direction not in (JOG_DIR_POS, JOG_DIR_NEG):
            raise ValueError(f"invalid direction {direction}")
        if not self.can_jog():
            raise PermissionError("machine not ready to jog")
        self._jog_mode = "cont"
        self._script_call("DoOEMButton", OEM_BTN_JOG_CONT)
        self._script_call("JogOn", axis, direction)

    def jog_off(self, axis: int) -> None:
        self._require_axis(axis)
        try:
            self._script_call("JogOff", axis)
        except Exception:
            pass

    def jog_off_all(self) -> None:
        for axis in (Axis.X, Axis.Y, Axis.Z):
            try:
                self._script_call("JogOff", int(axis))
            except Exception:
                pass

    def step_jog(self, axis: int, direction: int, step_size: float) -> None:
        self._require_axis(axis)
        if not self.can_jog():
            raise PermissionError("machine not ready to jog")
        self._jog_mode = "step"
        self._step_size = abs(step_size)
        self._script_call("DoOEMButton", OEM_BTN_JOG_INC)
        try:
            self._script_call("SetOEMDRO", OEM_DRO_JOG_INCREMENT, float(self._step_size))
        except Exception:
            pass
        # Incremental jog: JogOn then JogOff moves one increment on Mach3.
        self._script_call("JogOn", axis, direction)
        self._script_call("JogOff", axis)

    def set_feed_override(self, percent: float) -> None:
        value = _clamp(percent, 0.0, 200.0)
        self._script_call("SetOEMDRO", OEM_DRO_FEED_OVERRIDE, float(value))

    def set_jog_rate(self, percent: float) -> None:
        self._jog_rate = _clamp(percent, 1.0, 100.0)

    def set_jog_mode(self, mode: str) -> None:
        if mode not in ("cont", "step"):
            raise ValueError("jog mode must be 'cont' or 'step'")
        self._jog_mode = mode
        code = OEM_BTN_JOG_CONT if mode == "cont" else OEM_BTN_JOG_INC
        self._script_call("DoOEMButton", code)
        if mode == "step":
            self.jog_off_all()

    def set_step_size(self, size: float) -> None:
        nearest = min(_VALID_STEP_SIZES, key=lambda s: abs(s - size))
        self._step_size = nearest
        try:
            self._script_call("SetOEMDRO", OEM_DRO_JOG_INCREMENT, float(nearest))
        except Exception:
            pass

    def do_stop(self) -> None:
        self.jog_off_all()
        self._script_call("DoOEMButton", OEM_BTN_STOP)

    def do_reset(self) -> None:
        self.jog_off_all()
        self._script_call("DoOEMButton", OEM_BTN_RESET)

    def close(self) -> None:
        self._closed = True
        try:
            self.jog_off_all()
        except Exception:
            pass
        self._jobs.put(None)

    def _require_axis(self, axis: int) -> None:
        if axis not in (Axis.X, Axis.Y, Axis.Z):
            raise ValueError(f"invalid axis {axis}; expected 0/1/2 ({AXIS_NAMES})")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))
