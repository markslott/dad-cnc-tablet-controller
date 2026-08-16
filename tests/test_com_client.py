from src.mach3.com_client import (
    _CO_E_CLASSSTRING,
    _MK_E_UNAVAILABLE,
    _attach_hint,
    _ole_names,
    _open_mach3_document,
)


def test_ole_names_puts_clsid_after_progids():
    names = _ole_names("{abc}")
    assert names[0] == "Mach4.Document"
    assert names[-1] == "{abc}"


def test_ole_names_skips_blank_clsid():
    assert "Mach4.Document" in _ole_names(None)
    assert None not in _ole_names(None)


def test_open_falls_back_to_clsid_getactive():
    calls: list[str] = []

    def get_active(name: str):
        calls.append(name)
        if name != "{clsid}":
            raise OSError("nope")
        return "mach"

    def dispatch(_name: str):
        raise AssertionError("Dispatch should not run after GetActiveObject succeeds")

    assert _open_mach3_document(get_active, dispatch, _ole_names("{clsid}")) == "mach"
    assert calls == ["Mach4.Document", "Mach4.Document.1", "{clsid}"]


def test_open_falls_back_to_dispatch():
    def get_active(_name: str):
        raise OSError("not running")

    def dispatch(name: str):
        if name == "Mach4.Document":
            return "mach"
        raise OSError("skip")

    assert _open_mach3_document(get_active, dispatch, _ole_names(None)) == "mach"


def test_attach_hint_invalid_class_without_clsid():
    exc = OSError(_CO_E_CLASSSTRING, "Invalid class string")
    hint = _attach_hint(exc, bits=64, clsid=None)
    assert "64-bit Python" in hint
    assert "32-bit" in hint


def test_attach_hint_mach3_not_running():
    exc = OSError(_MK_E_UNAVAILABLE, "Operation unavailable")
    hint = _attach_hint(exc, bits=64, clsid="{abc}")
    assert hint.startswith("Start Mach3 first")
