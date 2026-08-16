import pytest

from src.mach3.com_client import (
    _CO_E_CLASSSTRING,
    _MACH3_CLSID,
    _MK_E_UNAVAILABLE,
    _attach_hint,
    _is_clsid_string,
    _ole_names,
    _open_mach3_document,
)


def test_ole_names_tries_known_clsid_first():
    names = _ole_names()
    assert names[0] == _MACH3_CLSID
    assert all(_is_clsid_string(name) for name in names)
    assert "Mach3.Automation" not in names
    assert "Mach4.Document" not in names


def test_ole_names_dedupes_registry_clsid():
    names = _ole_names(_MACH3_CLSID, "Mach3.Automation")
    assert names.count(_MACH3_CLSID) == 1
    assert "Mach3.Automation" not in names


def test_open_attaches_via_known_clsid():
    def get_active(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("nope")

    def dispatch(_name: str):
        raise AssertionError("Dispatch should not run after GetActiveObject succeeds")

    assert _open_mach3_document(get_active, dispatch, _ole_names()) == "mach"


def test_open_falls_back_to_dispatch_when_progid_missing():
    def get_active(_name: str):
        raise OSError(_CO_E_CLASSSTRING, "Invalid class string")

    def dispatch(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("skip")

    assert _open_mach3_document(get_active, dispatch, _ole_names()) == "mach"


def test_open_does_not_dispatch_if_mach3_not_running():
    def get_active(_name: str):
        raise OSError(_MK_E_UNAVAILABLE, "Operation unavailable")

    def dispatch(_name: str):
        raise AssertionError("must not launch a second Mach3")

    with pytest.raises(OSError) as excinfo:
        _open_mach3_document(get_active, dispatch, _ole_names(), process_running=False)
    assert excinfo.value.args[0] == _MK_E_UNAVAILABLE


def test_open_dispatches_when_mach3_is_running_but_not_in_rot():
    def get_active(_name: str):
        raise OSError(_MK_E_UNAVAILABLE, "Operation unavailable")

    def dispatch(name: str):
        if name == _MACH3_CLSID:
            return "mach"
        raise OSError("skip")

    assert (
        _open_mach3_document(get_active, dispatch, _ole_names(), process_running=True)
        == "mach"
    )


def test_open_skips_dispatch_when_create_false():
    def get_active(_name: str):
        raise OSError(_CO_E_CLASSSTRING, "Invalid class string")

    def dispatch(_name: str):
        raise AssertionError("must not Dispatch unregistered ProgIDs")

    with pytest.raises(OSError) as excinfo:
        _open_mach3_document(
            get_active, dispatch, _ole_names(), process_running=True, create=False
        )
    assert excinfo.value.args[0] == _CO_E_CLASSSTRING


def test_looks_like_mach3_rot_name():
    from src.mach3.com_client import _looks_like_mach3_rot_name, _object_looks_like_mach3

    assert _looks_like_mach3_rot_name("Mach3.Automation") is True
    assert _looks_like_mach3_rot_name("Mach4.Document") is True
    assert _looks_like_mach3_rot_name("{CA7992B2-2653-4342-8061-D7D385C07809}") is True
    assert _looks_like_mach3_rot_name("Excel.Application") is False

    class _Fake:
        def GetDRO(self, _axis: int) -> float:
            return 0.0

    assert _object_looks_like_mach3(_Fake()) is True
    assert _object_looks_like_mach3(object()) is False
    assert _is_clsid_string(_MACH3_CLSID) is True
    assert _is_clsid_string("Mach4.Document") is False


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
    assert "Operation unavailable" in hint
    assert "Leave this pendant window open" in hint
