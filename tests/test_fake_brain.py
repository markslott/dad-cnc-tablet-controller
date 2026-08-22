import time

import pytest
from pymodbus.client import ModbusTcpClient

from src.mach3.modbus_client import ModbusMach3Client
from src.mach3.modbus_map import (
    CMD_COUNT,
    HR_ALIVE,
    JOG_NEG,
    JOG_POS,
    HoldingRegisters,
    pack_step_jog,
)
from src.mach3.oem import (
    JOG_DIR_NEG,
    JOG_DIR_POS,
    OEM_BTN_JOG_CONT,
    OEM_BTN_JOG_INC,
    OEM_BTN_RESET,
    OEM_BTN_STOP,
    OEM_DRO_FEED_OVERRIDE,
    OEM_DRO_JOG_INCREMENT,
)
from tests.fake_mach3_brain import OEM_JOG_HOLD, FakeMach3Brain


def test_brain_holds_stock_jog_buttons():
    brain = FakeMach3Brain()
    cmd = [0] * CMD_COUNT
    cmd[HR_ALIVE] = 1
    cmd[0] = JOG_POS
    cmd[2] = JOG_NEG
    brain.apply_commands(cmd)
    assert OEM_JOG_HOLD[(0, JOG_POS)] in brain.held_buttons
    assert OEM_JOG_HOLD[(2, JOG_NEG)] in brain.held_buttons
    assert OEM_JOG_HOLD[(1, JOG_POS)] not in brain.held_buttons
    assert OEM_BTN_JOG_CONT in brain.held_buttons


def test_brain_stop_reset_and_dead_pendant():
    brain = FakeMach3Brain()
    stop = [0] * CMD_COUNT
    stop[HR_ALIVE] = 1
    stop[3] = 1
    brain.apply_commands(stop)
    assert OEM_BTN_STOP in brain.pulsed_buttons

    reset = [0] * CMD_COUNT
    reset[HR_ALIVE] = 1
    reset[4] = 1
    brain.apply_commands(reset)
    assert OEM_BTN_RESET in brain.pulsed_buttons

    dead = [0] * CMD_COUNT
    brain.apply_commands(dead)
    assert OEM_BTN_STOP in brain.pulsed_buttons


def test_brain_fro_step_size_and_step_pulse():
    brain = FakeMach3Brain()
    cmd = [0] * CMD_COUNT
    cmd[HR_ALIVE] = 1
    cmd[5] = 80
    cmd[6] = 1
    cmd[7] = 100
    cmd[8] = pack_step_jog(1, JOG_DIR_NEG)
    brain.apply_commands(cmd)
    assert brain.oem_dro[OEM_DRO_FEED_OVERRIDE] == 80
    assert brain.oem_dro[OEM_DRO_JOG_INCREMENT] == pytest.approx(0.1)
    assert OEM_BTN_JOG_INC in brain.held_buttons
    assert OEM_JOG_HOLD[(1, JOG_NEG)] in brain.pulsed_buttons


def test_pendant_commands_drive_fake_brain():
    client = ModbusMach3Client(start_server=False, pulse_s=0.2)
    brain = FakeMach3Brain()
    brain.work = [1.25, -0.5, 0.01]
    brain.oem_led[19] = 0
    brain.oem_led[800] = 1
    brain.tick_registers(client._registers)
    assert client.get_status().can_jog
    assert client.get_dro().x == pytest.approx(1.25, abs=1e-4)
    assert client.get_dro().y == pytest.approx(-0.5, abs=1e-4)

    client.set_feed_override(75)
    client.jog_on(0, JOG_DIR_POS)
    brain.tick_registers(client._registers)
    assert OEM_JOG_HOLD[(0, JOG_POS)] in brain.held_buttons
    assert brain.oem_dro[OEM_DRO_FEED_OVERRIDE] == 75

    client.jog_off(0)
    client.do_stop()
    brain.tick_registers(client._registers)
    assert OEM_BTN_STOP in brain.pulsed_buttons
    assert OEM_JOG_HOLD[(0, JOG_POS)] not in brain.held_buttons
    client.close()


def test_fake_brain_over_tcp_like_mach3():
    port = 15022
    client = ModbusMach3Client(host="127.0.0.1", port=port, start_server=True)
    master = ModbusTcpClient("127.0.0.1", port=port)
    brain = FakeMach3Brain()
    brain.work = [4.5, 0.0, 0.0]
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if master.connect():
                break
            time.sleep(0.05)
        else:
            pytest.fail("modbus slave did not accept a connection")

        wr = master.write_registers(100, brain.status_words())
        assert not wr.isError()
        time.sleep(0.05)
        assert client.get_status().connected
        assert client.get_dro().x == pytest.approx(4.5, abs=1e-4)

        client.jog_on(1, JOG_DIR_NEG)
        client.set_feed_override(110)
        rr = master.read_holding_registers(0, CMD_COUNT)
        assert not rr.isError()
        brain.apply_commands(list(rr.registers))
        assert OEM_JOG_HOLD[(1, JOG_NEG)] in brain.held_buttons
        assert brain.oem_dro[OEM_DRO_FEED_OVERRIDE] == 110
    finally:
        master.close()
        client.close()


def test_registers_roundtrip_without_tcp():
    regs = HoldingRegisters()
    brain = FakeMach3Brain()
    brain.work = [-3.0, 0.0, 0.0]
    brain.tick_registers(regs)
    client = ModbusMach3Client(registers=regs, start_server=False)
    assert client.get_dro().x == pytest.approx(-3.0, abs=1e-4)
    client.close()
