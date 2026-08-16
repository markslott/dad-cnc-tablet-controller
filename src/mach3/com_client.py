from __future__ import annotations

import os
import queue
import struct
import subprocess
import threading
from collections.abc import Callable
from typing import Any, TypeVar

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
_MACH3_PROGIDS = (
    "Mach3.Automation",
    "Mach3.Automation.1",
    "Mach4.Document",
    "Mach4.Document.1",
    "Mach3.Document",
    "Mach3.Document.1",
)
# Common Mach3 document CLSID; real installs can differ — we also scan Mach3.exe.
_MACH3_CLSID = "{CA7992B2-2653-4342-8061-D7D385C07809}"
_CO_E_CLASSSTRING = -2147221005  # invalid class string
_REGDB_E_CLASSNOTREG = -2147221164
_MK_E_UNAVAILABLE = -2147221021  # Mach3 not running


def _hresult(exc: BaseException) -> int | None:
    if not exc.args or not isinstance(exc.args[0], int):
        return None
    code = exc.args[0]
    if code > 0x7FFFFFFF:
        return code - 0x100000000
    return code


def _registry_clsid(progid: str) -> str | None:
    """Look up ProgID in both native and 32-bit (Wow6432Node) registry views."""
    try:
        import winreg
    except ImportError:
        return None
    wow32 = int(getattr(winreg, "KEY_WOW64_32KEY", 0x0200))
    read = int(winreg.KEY_READ)
    queries = (
        (winreg.HKEY_CLASSES_ROOT, f"{progid}\\CLSID", read),
        (winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\Classes\\{progid}\\CLSID", read | wow32),
        (winreg.HKEY_CURRENT_USER, f"SOFTWARE\\Classes\\{progid}\\CLSID", read),
        (winreg.HKEY_CLASSES_ROOT, f"Wow6432Node\\{progid}\\CLSID", read),
    )
    for hive, path, access in queries:
        try:
            with winreg.OpenKey(hive, path, 0, access) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_clsid_string(name: str) -> bool:
    text = _normalize_clsid(name.strip())
    return len(text) == 38 and text[0] == "{" and text[-1] == "}"


def _normalize_clsid(name: str) -> str:
    text = name.strip()
    if len(text) == 36 and text.count("-") == 4:
        return "{" + text + "}"
    return text


def _ole_names(*extra: str | None) -> list[str]:
    """CLSIDs only. ProgID strings are not registered on this PC and only spam Invalid class string."""
    names: list[str] = []
    for name in (*extra, _MACH3_CLSID):
        if not name:
            continue
        name = _normalize_clsid(name)
        if _is_clsid_string(name) and name not in names:
            names.append(name)
    return names


def _ole_ident(name: str):
    """ProgID stays a string; {CLSID} becomes a real IID (avoids Invalid class string)."""
    if not _is_clsid_string(name):
        return name
    import pywintypes

    return pywintypes.IID(_normalize_clsid(name))


def _clsids_from_typelib(exe: str) -> list[str]:
    import pythoncom

    try:
        tlb = pythoncom.LoadTypeLib(exe)
    except Exception:
        return []
    found: list[str] = []
    tkind = int(getattr(pythoncom, "TKIND_COCLASS", 5))
    for i in range(tlb.GetTypeInfoCount()):
        try:
            info = tlb.GetTypeInfo(i)
            attr = info.GetTypeAttr()
            if int(attr.typekind) != tkind:
                continue
            guid = str(attr.guid)
            if guid and guid not in found:
                found.append(guid)
        except Exception:
            continue
    return found


def _clsids_from_mach3_local_server(exe: str | None) -> list[str]:
    """CLSIDs whose LocalServer32 path is this Mach3.exe."""
    if not exe:
        return []
    found: list[str] = []
    try:
        import winreg
    except ImportError:
        return found
    access = int(winreg.KEY_READ)
    try:
        root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID", 0, access)
    except OSError:
        return found
    i = 0
    while True:
        try:
            clsid = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        try:
            with winreg.OpenKey(root, clsid + r"\LocalServer32", 0, access) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if not isinstance(value, str) or "mach3.exe" not in value.lower():
            continue
        guid = clsid if clsid.startswith("{") else f"{{{clsid}}}"
        if guid not in found:
            found.append(guid)
    return found


def _mach3_exe_on_disk() -> str | None:
    candidates = (
        r"C:\Mach3\Mach3.exe",
        os.path.expandvars(r"%ProgramFiles(x86)%\Mach3\Mach3.exe"),
        os.path.expandvars(r"%ProgramFiles%\Mach3\Mach3.exe"),
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _mach3_exe_running() -> str | None:
    """Path of a running Mach3.exe, or a dummy string if running but path unknown."""
    try:
        import win32com.client

        wmi = win32com.client.GetObject("winmgmts:")
        for proc in wmi.ExecQuery("SELECT ExecutablePath FROM Win32_Process WHERE Name='Mach3.exe'"):
            path = getattr(proc, "ExecutablePath", None)
            if path and os.path.isfile(str(path)):
                return str(path)
            return "Mach3.exe"
    except Exception:
        pass
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Mach3.exe", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=creationflags,
        )
        if "Mach3.exe" in out:
            return _mach3_exe_on_disk() or "Mach3.exe"
    except Exception:
        pass
    return None


def _ensure_hkcu_ole(exe_path: str | None, clsid: str = _MACH3_CLSID) -> None:
    """Write per-user Mach3.Automation / Mach4.Document keys if OLE was never registered."""
    try:
        import winreg
    except ImportError:
        return

    def _set(path: str, value: str) -> None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)

    for progid in ("Mach3.Automation", "Mach4.Document"):
        _set(rf"Software\Classes\{progid}", progid)
        _set(rf"Software\Classes\{progid}\CLSID", clsid)
    _set(rf"Software\Classes\CLSID\{clsid}", "Mach3 Automation")
    _set(rf"Software\Classes\CLSID\{clsid}\ProgID", "Mach3.Automation")
    if exe_path and os.path.isfile(exe_path):
        _set(rf"Software\Classes\CLSID\{clsid}\LocalServer32", f'"{exe_path}"')


def _looks_like_mach3_rot_name(display: str) -> bool:
    text = display.lower()
    return any(
        token in text
        for token in (
            "mach3",
            "mach4",
            "mach3.automation",
            "mach4.document",
            "mach3.document",
            "ca7992b2",
        )
    )


def _com_attr(obj: Any, name: str) -> Any | None:
    """getattr() default does not catch pywin32 com_error."""
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _object_looks_like_mach3(obj: Any) -> bool:
    try:
        _script_from_document(obj)
        return True
    except Exception:
        return False


def _script_from_document(mach: Any) -> Any:
    getter = _com_attr(mach, "GetScriptDispatch")
    if callable(getter):
        try:
            script = getter()
            if script is not None:
                return script
        except Exception:
            pass
    if _com_attr(mach, "GetDRO") is not None:
        return mach
    raise RuntimeError("COM object has no GetScriptDispatch or GetDRO")


def _attach_from_rot(dispatch: Callable[[Any], Any]) -> Any | None:
    """Bind the running Mach3 without a registered ProgID."""
    import pythoncom

    try:
        rot = pythoncom.GetRunningObjectTable()
        enum = rot.EnumRunning()
        ctx = pythoncom.CreateBindCtx(0)
    except Exception as exc:
        print(f"Mach3 ROT unavailable ({exc})", flush=True)
        return None
    entries: list[tuple[str, Any]] = []
    while True:
        try:
            mons = enum.Next(1)
        except Exception:
            break
        if not mons:
            break
        mon = mons[0]
        try:
            display = str(mon.GetDisplayName(ctx, None) or "")
        except Exception:
            display = ""
        entries.append((display, mon))
    print(f"Mach3 ROT: {len(entries)} running object(s)", flush=True)
    for display, _mon in entries:
        print(f"Mach3 ROT entry: {display!r}", flush=True)
    ordered = [item for item in entries if _looks_like_mach3_rot_name(item[0])]
    ordered.extend(item for item in entries if not _looks_like_mach3_rot_name(item[0]))
    for display, mon in ordered:
        try:
            unk = rot.GetObject(mon)
            try:
                disp = unk.QueryInterface(pythoncom.IID_IDispatch)
            except Exception:
                disp = unk
            obj = dispatch(disp)
            if _object_looks_like_mach3(obj):
                print(f"Mach3 attached from ROT {display!r}", flush=True)
                return obj
            print(f"Mach3 ROT skip {display!r}: not a Mach3 script object", flush=True)
        except Exception as exc:
            print(f"Mach3 ROT skip {display!r}: {exc}", flush=True)
            continue
    return None


def _open_mach3_document(
    get_active_object: Callable[[str], Any],
    dispatch: Callable[[str], Any],
    names: list[str],
    *,
    process_running: bool = False,
    create: bool = True,
) -> Any:
    last: BaseException | None = None
    not_in_rot: BaseException | None = None
    for name in names:
        try:
            return get_active_object(name)
        except Exception as exc:  # noqa: BLE001 — try the next OLE name
            last = exc
            if _hresult(exc) == _MK_E_UNAVAILABLE:
                not_in_rot = exc
    if not_in_rot is not None and not process_running:
        raise not_in_rot
    if not create:
        if last is None:
            raise RuntimeError("no Mach3 OLE names to try")
        raise last
    for name in names:
        try:
            return dispatch(name)
        except Exception as exc:  # noqa: BLE001
            last = exc
    if last is None:
        raise RuntimeError("no Mach3 OLE names to try")
    raise last


def _attach_hint(
    exc: BaseException,
    *,
    bits: int,
    clsid: str | None,
    process_running: bool = False,
) -> str:
    if process_running:
        return (
            "Mach3 is running but is not in the OLE running-object table. "
            "Close Mach3, right-click C:\\Mach3\\Mach3.exe and Run as administrator "
            "once (or Mach3.exe /RegServer), then start Mach3 and the pendant "
            "at the same elevation."
        )
    code = _hresult(exc)
    if code == _MK_E_UNAVAILABLE:
        return "Start Mach3 first, then start this server."
    if code in (_CO_E_CLASSSTRING, _REGDB_E_CLASSNOTREG):
        return (
            f"Mach3 OLE is not registered for this {bits}-bit Python. "
            "Start Mach3 once as Administrator so it writes its class, "
            "then start the pendant with the same elevation. "
            "Confirm python -c \"import struct; print(struct.calcsize('P')*8)\" "
            "prints 32 (32-bit Python)."
        )
    return "Start Mach3 first, then start this server."


class ComMach3Client:
    """Talk to a running Mach3 instance via OLE (Mach3.Automation / Mach4.Document).

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
            from win32com.client import Dispatch
        except ImportError:
            self._connect_error = (
                "pywin32 is required for MACH3_BACKEND=com. "
                "Install it on the Windows Mach3 PC: pip install pywin32"
            )
            self._ready.set()
            return

        pythoncom.CoInitialize()
        try:
            running = _mach3_exe_running()
            exe = running if running and os.path.isfile(running) else _mach3_exe_on_disk()
            discovered: list[str] = []
            if exe and os.path.isfile(exe):
                discovered.extend(_clsids_from_typelib(exe))
                discovered.extend(_clsids_from_mach3_local_server(exe))
            for progid in _MACH3_PROGIDS:
                found = _registry_clsid(progid)
                if found:
                    discovered.append(found)
            unique_discovered: list[str] = []
            for item in discovered:
                if item and item not in unique_discovered:
                    unique_discovered.append(item)
            if exe:
                _ensure_hkcu_ole(exe, unique_discovered[0] if unique_discovered else _MACH3_CLSID)
            names = _ole_names(*unique_discovered)
            process_running = running is not None
            print(
                f"Mach3 OLE: Python {struct.calcsize('P') * 8}-bit, "
                f"Mach3 running={process_running}, CLSIDs={names}",
                flush=True,
            )
            errors: list[str] = []

            def get_active(name: str):
                try:
                    obj = pythoncom.GetActiveObject(_ole_ident(name))
                    return Dispatch(obj)
                except Exception as exc:
                    errors.append(f"GetActiveObject({name}): {exc}")
                    raise

            def dispatch(name: str):
                try:
                    ident = _ole_ident(name)
                    if _is_clsid_string(name):
                        obj = pythoncom.CoCreateInstance(
                            ident,
                            None,
                            pythoncom.CLSCTX_LOCAL_SERVER,
                            pythoncom.IID_IDispatch,
                        )
                        return Dispatch(obj)
                    return Dispatch(name)
                except Exception as exc:
                    errors.append(f"Dispatch({name}): {exc}")
                    raise

            try:
                mach = _attach_from_rot(Dispatch)
                if mach is None:
                    mach = _open_mach3_document(
                        get_active,
                        dispatch,
                        names,
                        process_running=process_running,
                        create=False,
                    )
                self._script = _script_from_document(mach)
            except Exception as exc:  # noqa: BLE001 — COM errors are opaque
                hint = _attach_hint(
                    exc,
                    bits=struct.calcsize("P") * 8,
                    clsid=unique_discovered[0] if unique_discovered else None,
                    process_running=process_running,
                )
                detail = "; ".join(errors[-6:]) if errors else str(exc)
                self._connect_error = f"could not attach to Mach3 ({detail}). {hint}"
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
