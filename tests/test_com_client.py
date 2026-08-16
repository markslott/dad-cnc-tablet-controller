import pytest

from src.mach3.com_client import (
    _CO_E_CLASSSTRING,
    _MACH3_CLSID,
    _MK_E_UNAVAILABLE,
    _attach_hint,
    _ole_names,
    _open_mach3_document,
)


def test_ole_names_tries_known_clsid_first():
    names = _ole_names(None)
    assert names[0] == _MACH3_CLSID
    assert "Mach4.Document" in names


def test_ole_names_dedupes_registry_clsid():
    names = _ole_names(_MACH3_CLSID)
    assert names.count(_MACH3_CLSID) == 1


def test_open_attaches_via_known_clsid():
    def get_active(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("nope")

    def dispatch(_name: str):
        raise AssertionError("Dispatch should not run after GetActiveObject succeeds")

    assert _open_mach3_document(get_active, dispatch, _ole_names(None)) == "mach"


def test_open_falls_back_to_dispatch_when_progid_missing():
    def get_active(_name: str):
        raise OSError(_CO_E_CLASSSTRING, "Invalid class string")

    def dispatch(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("skip")

    assert _open_mach3_document(get_active, dispatch, _ole_names(None)) == "mach"


def test_open_does_not_dispatch_if_mach3_not_running():
    def get_active(_name: str):
        raise OSError(_MK_E_UNAVAILABLE, "Operation unavailable")

    def dispatch(_name: str):
        raise AssertionError("must not launch a second Mach3")

    with pytest.raises(OSError) as excinfo:
        _open_mach3_document(get_active, dispatch, _ole_names(None), process_running=False)
    assert excinfo.value.args[0] == _MK_E_UNAVAILABLE


def test_open_dispatches_when_mach3_is_running_but_not_in_rot():
    def get_active(_name: str):
        raise OSError(_MK_E_UNAVAILABLE, "Operation unavailable")

    def dispatch(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("skip")

    assert (
        _open_mach3_document(get_active, dispatch, _ole_names(None), process_running=True)
        == "mach"
    )


def test_attach_hint_invalid_class_without_clsid():
    exc = OSError(_CO_E_CLASSSTRING, "Invalid class string")
    hint = _attach_hint(exc, bits=64, clsid=None)
    assert "64-bit Python" in hint
    assert "32-bit" in hint


def test_attach_hint_mach3_not_running():
    exc = OSError(_MK_E_UNAVAILABLE, "Operation unavailable")
    hint = _attach_hint(exc, bits=64, clsid=_MACH3_CLSID, process_running=False)
    assert hint.startswith("Start Mach3 first")


def test_attach_hint_when_process_running_but_ole_blocked():
    exc = OSError(_MK_E_UNAVAILABLE, "Operation unavailable")
    hint = _attach_hint(exc, bits=64, clsid=_MACH3_CLSID, process_running=True)
    assert "Administrator" in hint
