"""Stand-in for the two shop Brains (Pendant-Cmd and Pendant-Status).

This is not Mach3. It applies the same Modbus-local → OEM mappings the README
tells you to wire, so tests can prove the pendant contract without a mill.
"""

from __future__ import annotations

from src.mach3.modbus_map import (
    CMD_COUNT,
    HR_ALIVE,
    HR_FRO,
    HR_JOG,
    HR_JOG_MODE,
    HR_RESET,
    HR_STEP_PULSE,
    HR_STEP_SIZE,
    HR_STOP,
    JOG_NEG,
    JOG_POS,
    MODE_STEP,
    STATUS_COUNT,
    STEP_SCALE,
    encode_dro,
)
from src.mach3.oem import (
    OEM_BTN_JOG_CONT,
    OEM_BTN_JOG_INC,
    OEM_BTN_RESET,
    OEM_BTN_STOP,
    OEM_DRO_FEED_OVERRIDE,
    OEM_DRO_JOG_INCREMENT,
    OEM_LED_ESTOP,
    OEM_LED_IN_CYCLE,
    OEM_LED_RESET_OK,
)

# Stock mill 1024.set jog buttons from the Brain recipe.
OEM_JOG_HOLD = {
    (0, JOG_POS): 307,
    (0, JOG_NEG): 308,
    (1, JOG_POS): 309,
    (1, JOG_NEG): 310,
    (2, JOG_POS): 311,
    (2, JOG_NEG): 312,
}
OEM_STEP_PULSE = {8: 307, 12: 308, 9: 309, 13: 310, 10: 311, 14: 312}


class FakeMach3Brain:
    def __init__(self) -> None:
        self.held_buttons: set[int] = set()
        self.pulsed_buttons: list[int] = []
        self.oem_dro = {OEM_DRO_FEED_OVERRIDE: 100.0, OEM_DRO_JOG_INCREMENT: 0.01}
        self.oem_led = {OEM_LED_ESTOP: 0, OEM_LED_RESET_OK: 1, OEM_LED_IN_CYCLE: 0}
        self.work = [0.0, 0.0, 0.0]

    def apply_commands(self, cmd: list[int]) -> None:
        if len(cmd) < CMD_COUNT:
            cmd = list(cmd) + [0] * (CMD_COUNT - len(cmd))
        self.pulsed_buttons.clear()
        self.held_buttons.clear()

        if cmd[HR_ALIVE] == 0 or cmd[HR_STOP] == 1:
            self.pulsed_buttons.append(OEM_BTN_STOP)
        if cmd[HR_RESET] == 1:
            self.pulsed_buttons.append(OEM_BTN_RESET)

        self.oem_dro[OEM_DRO_FEED_OVERRIDE] = float(cmd[HR_FRO])
        if cmd[HR_STEP_SIZE]:
            self.oem_dro[OEM_DRO_JOG_INCREMENT] = cmd[HR_STEP_SIZE] / STEP_SCALE

        if cmd[HR_JOG_MODE] == MODE_STEP:
            self.held_buttons.add(OEM_BTN_JOG_INC)
        else:
            self.held_buttons.add(OEM_BTN_JOG_CONT)

        for axis, addr in enumerate(HR_JOG):
            oem = OEM_JOG_HOLD.get((axis, cmd[addr]))
            if oem is not None:
                self.held_buttons.add(oem)

        step_oem = OEM_STEP_PULSE.get(cmd[HR_STEP_PULSE])
        if step_oem is not None:
            self.pulsed_buttons.append(step_oem)

    def status_words(self) -> list[int]:
        words: list[int] = []
        for value in self.work:
            words.extend(encode_dro(value))
        words.append(int(round(self.oem_dro[OEM_DRO_FEED_OVERRIDE])))
        words.append(int(self.oem_led[OEM_LED_ESTOP]))
        words.append(int(self.oem_led[OEM_LED_RESET_OK]))
        words.append(int(self.oem_led[OEM_LED_IN_CYCLE]))
        if len(words) < STATUS_COUNT:
            words.extend([0] * (STATUS_COUNT - len(words)))
        return words[:STATUS_COUNT]

    def tick_registers(self, registers) -> None:
        self.apply_commands(registers.get_range(0, CMD_COUNT))
        registers.set_range(100, self.status_words())
        registers.note_poll()
