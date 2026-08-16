from fastapi.testclient import TestClient

from src.mach3.factory import create_client
from src.mach3.oem import JOG_DIR_POS
from src.mach3.pump_client import PumpMach3Client, format_commands, parse_report
from src.server.app import create_app
from tests.conftest import make_settings


def test_factory_pump():
    client = create_client("pump")
    assert isinstance(client, PumpMach3Client)
    client.close()


def test_disconnected_until_pump_posts():
    client = PumpMach3Client()
    status = client.get_status()
    assert status.connected is False
    assert status.can_jog is False
    assert "macropump" in (status.error or "")
    client.close()


def test_exchange_sets_dro_and_allows_jog_when_ready():
    client = PumpMach3Client()
    reply = client.exchange_pump("12345|-50000|10|0|1|0|100")
    assert reply.startswith("0|0|0|")
    status = client.get_status()
    assert status.connected is True
    assert status.dro.x == 1.2345
    assert status.dro.y == -5.0
    assert status.can_jog is True
    client.jog_on(0, JOG_DIR_POS)
    reply = client.exchange_pump("12345|-50000|10|0|1|0|100")
    assert reply.split("|")[0] == "1"
    client.close()


def test_reset_pulse_appears_then_clears():
    client = PumpMach3Client()
    client.exchange_pump("0|0|0|0|1|0|100")
    client.do_reset()
    reply = client.exchange_pump("0|0|0|0|1|0|100")
    assert reply.split("|")[4] == "1"
    client.close()


def test_parse_and_format_roundtrip():
    x, y, z, estop, reset_ok, in_cycle, fro = parse_report("10000|0|-2500|1|0|1|80")
    assert x == 1.0
    assert z == -0.25
    assert estop is True
    assert reset_ok is False
    assert in_cycle is True
    assert fro == 80
    line = format_commands(1, 0, 2, 0, 1, 110, 0, (1, 0, 0.01))
    assert line == "1|0|2|0|1|110|0|1|0|10"


def test_pump_http_localhost_updates_status():
    mach3 = PumpMach3Client()
    app = create_app(mach3=mach3, settings=make_settings(backend="pump"))
    with TestClient(app) as client:
        denied = client.get("/api/status")
        assert denied.json()["connected"] is False
        posted = client.post("/api/mach3/pump", content="25000|0|0|0|1|0|100")
        assert posted.status_code == 200
        assert posted.text.split("|")[0] == "0"
        status = client.get("/api/status").json()
        assert status["connected"] is True
        assert status["dro"]["x"] == 2.5
        assert status["backend"] == "pump"
