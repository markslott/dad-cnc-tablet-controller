import time

import pytest
from pymodbus.client import ModbusTcpClient

from src.mach3.modbus_client import ModbusMach3Client
from src.mach3.modbus_map import (
    HR_ESTOP,
    HR_FRO_ACTUAL,
    HR_IN_CYCLE,
    HR_JOG,
    HR_RESET_OK,
    HR_STOP,
    JOG_POS,
    encode_dro,
)
from src.mach3.oem import JOG_DIR_NEG, JOG_DIR_POS


def _ready(client: ModbusMach3Client, x: float = 1.25, y: float = -0.5, z: float = 0.0) -> None:
    xh, xl = encode_dro(x)
    yh, yl = encode_dro(y)
    zh, zl = encode_dro(z)
    client._registers.set_range(100, [xh, xl, yh, yl, zh, zl])
    client._registers.set(HR_FRO_ACTUAL, 100)
    client._registers.set(HR_ESTOP, 0)
    client._registers.set(HR_RESET_OK, 1)
    client._registers.set(HR_IN_CYCLE, 0)


def test_disconnected_until_status_write():
    client = ModbusMach3Client(start_server=False)
    status = client.get_status()
    assert status.connected is False
    assert status.can_jog is False
    assert "waiting for Mach3 TCP Modbus" in (status.error or "")
    client.close()


def test_jog_on_sets_registers_and_jog_off_clears():
    client = ModbusMach3Client(start_server=False)
    _ready(client)
    assert client.get_status().can_jog
    client.jog_on(0, JOG_DIR_POS)
    assert client._registers.get(HR_JOG[0]) == JOG_POS
    client.jog_off(0)
    assert client._registers.get(HR_JOG[0]) == 0
    client.jog_on(1, JOG_DIR_NEG)
    client.jog_off_all()
    assert all(client._registers.get(addr) == 0 for addr in HR_JOG)
    client.close()


def test_get_status_decodes_scaled_dro():
    client = ModbusMach3Client(start_server=False)
    _ready(client, x=12.3456, y=-3.0, z=0.001)
    dro = client.get_dro()
    assert dro.x == pytest.approx(12.3456, abs=1e-4)
    assert dro.y == pytest.approx(-3.0, abs=1e-4)
    assert dro.z == pytest.approx(0.001, abs=1e-4)
    client.close()


def test_jog_on_refused_when_disconnected():
    client = ModbusMach3Client(start_server=False)
    with pytest.raises(PermissionError):
        client.jog_on(0, JOG_DIR_POS)
    client.close()


def test_stop_pulse_clears():
    client = ModbusMach3Client(start_server=False, pulse_s=0.05)
    _ready(client)
    client.do_stop()
    assert client._registers.get(HR_STOP) == 1
    time.sleep(0.12)
    assert client._registers.get(HR_STOP) == 0
    client.close()


def test_localhost_roundtrip():
    port = 15021
    client = ModbusMach3Client(host="127.0.0.1", port=port, start_server=True)
    master = ModbusTcpClient("127.0.0.1", port=port)
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if master.connect():
                break
            time.sleep(0.05)
        else:
            pytest.fail("modbus slave did not accept a connection")
        xh, xl = encode_dro(4.5)
        yh, yl = encode_dro(0.0)
        zh, zl = encode_dro(0.0)
        wr = master.write_registers(100, [xh, xl, yh, yl, zh, zl, 100, 0, 1, 0])
        assert not wr.isError()
        time.sleep(0.05)
        assert client.get_status().connected
        assert client.get_dro().x == pytest.approx(4.5, abs=1e-4)
        client.jog_on(0, JOG_DIR_POS)
        rr = master.read_holding_registers(0, 3)
        assert not rr.isError()
        assert rr.registers[0] == JOG_POS
    finally:
        master.close()
        client.close()
