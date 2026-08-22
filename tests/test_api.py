import time

import pytest
from fastapi.testclient import TestClient

from src.mach3.modbus_client import ModbusMach3Client
from src.mach3.modbus_map import HR_ESTOP, encode_dro
from src.server.app import create_app
from src.server.config import Settings
from tests.conftest import make_app


def test_config_and_index():
    app, _ = make_app()
    with TestClient(app) as client:
        cfg = client.get("/api/config").json()
        assert cfg["backend"] == "mock"
        assert cfg["pin_required"] is False
        home = client.get("/")
        assert home.status_code == 200
        assert "Mach3 Pendant" in home.text


def test_status_and_feed_override_and_reset_stop():
    app, mach3 = make_app()
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["can_jog"] is True
        assert status["dro"]["x"] == 0

        assert client.post("/api/feed-override", json={"percent": 80}).status_code == 200
        assert client.get("/api/status").json()["feed_override"] == 80

        assert client.post("/api/stop", json={}).status_code == 200
        stopped = client.get("/api/status").json()
        assert stopped["stopped"] is True
        assert stopped["can_jog"] is False

        jog = client.post("/api/jog/on", json={"axis": 0, "direction": 0})
        assert jog.status_code == 409

        assert client.post("/api/reset", json={}).status_code == 200
        assert client.get("/api/status").json()["can_jog"] is True


def test_step_jog_via_api():
    app, mach3 = make_app()
    with TestClient(app) as client:
        client.post("/api/jog/step-size", json={"size": 0.1})
        client.post("/api/jog/step", json={"axis": 2, "direction": 0, "step_size": 0.1})
        z = client.get("/api/status").json()["dro"]["z"]
        assert abs(z - 0.1) < 1e-9
        assert abs(mach3.get_dro().z - 0.1) < 1e-9


def test_continuous_jog_and_off():
    app, mach3 = make_app()
    with TestClient(app) as client:
        client.post("/api/jog/rate", json={"percent": 100})
        assert client.post("/api/jog/on", json={"axis": 0, "direction": 0}).status_code == 200
        time.sleep(0.04)
        x = client.get("/api/status").json()["dro"]["x"]
        assert x > 0
        assert client.post("/api/jog/off", json={"axis": 0, "direction": 0}).status_code == 200
        assert client.get("/api/status").json()["jogging"] is False


def test_websocket_streams_dro():
    app, _ = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/state") as ws:
            msg = ws.receive_json()
            assert "dro" in msg
            assert msg["backend"] == "mock"
            ws.send_json({"type": "heartbeat"})
            msg2 = ws.receive_json()
            assert "can_jog" in msg2


def test_watchdog_stops_jog_without_heartbeat():
    app, mach3 = make_app(watchdog_ms=80)
    with TestClient(app) as client:
        assert client.post("/api/jog/on", json={"axis": 1, "direction": 0}).status_code == 200
        assert mach3.get_status().jogging
        time.sleep(0.2)
        assert not mach3.get_status().jogging


def test_debug_endpoint_empty_on_mock():
    app, _ = make_app()
    with TestClient(app) as client:
        data = client.get("/api/debug").json()
        assert data["available"] is False
        assert data["backend"] == "mock"


def test_debug_endpoint_shows_modbus_words():
    mach3 = ModbusMach3Client(start_server=False)
    xh, xl = encode_dro(1.5)
    mach3._registers.set_range(100, [xh, xl, 0, 0, 0, 0, 80, 1, 0, 0])
    mach3._registers.set(HR_ESTOP, 1)
    mach3._registers.note_poll()
    settings = Settings(
        backend="modbus",
        host="127.0.0.1",
        port=8080,
        pin=None,
        watchdog_ms=200,
        dro_hz=10,
    )
    app = create_app(mach3=mach3, settings=settings)
    with TestClient(app) as client:
        data = client.get("/api/debug").json()
        assert data["available"] is True
        assert data["cfg0"][9]["name"] == "Alive"
        assert data["cfg0"][9]["value"] == 1
        assert data["cfg1"][7]["slave"] == 107
        assert data["cfg1"][7]["value"] == 1
        assert data["decoded"]["estop"] is True
        assert data["decoded"]["x"] == pytest.approx(1.5, abs=1e-4)
        assert data["poll_age_s"] is not None
    mach3.close()


def test_pin_protects_commands():
    app, _ = make_app(pin="2468")
    with TestClient(app) as client:
        assert client.get("/api/config").json()["pin_required"] is True
        denied = client.get("/api/status")
        assert denied.status_code == 401
        ok = client.get("/api/status", headers={"X-Shop-Pin": "2468"})
        assert ok.status_code == 200
        bad_auth = client.post("/api/auth", json={"pin": "0000"})
        assert bad_auth.status_code == 401
        good_auth = client.post("/api/auth", json={"pin": "2468"})
        assert good_auth.status_code == 200
