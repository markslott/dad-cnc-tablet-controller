"""Holding-register map for Mach3 TCP Modbus (Mach3 is the master).

Mach3 Cfg #0 Input-Holding reads commands starting at 0 (16 regs).
Mach3 Cfg #1 Output-Holding writes status starting at 100 (16 regs).
DRO values are signed 32-bit ints scaled by 10000, high word first.
"""

from __future__ import annotations

import threading
import time

HR_SIZE = 116
CMD_START = 0
CMD_COUNT = 16
STATUS_START = 100
STATUS_COUNT = 16

HR_JOG = (0, 1, 2)  # X, Y, Z
HR_STOP = 3
HR_RESET = 4
HR_FRO = 5
HR_JOG_MODE = 6
HR_STEP_SIZE = 7  # step × 1000
HR_STEP_PULSE = 8
HR_ALIVE = 9

HR_X_HI, HR_X_LO = 100, 101
HR_Y_HI, HR_Y_LO = 102, 103
HR_Z_HI, HR_Z_LO = 104, 105
HR_FRO_ACTUAL = 106
HR_ESTOP = 107
HR_RESET_OK = 108
HR_IN_CYCLE = 109

JOG_OFF = 0
JOG_POS = 1
JOG_NEG = 2

DRO_SCALE = 10000
STEP_SCALE = 1000
MODE_CONT = 0
MODE_STEP = 1

CONNECTED_TIMEOUT_S = 1.0
PULSE_S = 0.25


def encode_i32(value: int) -> tuple[int, int]:
    u = value & 0xFFFFFFFF
    return (u >> 16) & 0xFFFF, u & 0xFFFF


def decode_i32(high: int, low: int) -> int:
    u = ((high & 0xFFFF) << 16) | (low & 0xFFFF)
    if u >= 0x80000000:
        return u - 0x100000000
    return u


def encode_dro(value: float) -> tuple[int, int]:
    n = int(round(float(value) * DRO_SCALE))
    n = max(-2_147_483_648, min(2_147_483_647, n))
    return encode_i32(n)


def decode_dro(high: int, low: int) -> float:
    return decode_i32(high, low) / DRO_SCALE


def pack_step_jog(axis: int, direction: int) -> int:
    return (axis & 0x3) | ((direction & 0x1) << 2) | 0x8


def unpack_step_jog(value: int) -> tuple[int, int] | None:
    if not value:
        return None
    return value & 0x3, (value >> 2) & 0x1


def touches_status(address: int, count: int) -> bool:
    end = address + count
    return address < STATUS_START + STATUS_COUNT and end > STATUS_START


class HoldingRegisters:
    def __init__(self, size: int = HR_SIZE) -> None:
        self.size = size
        self.values = [0] * size
        self.lock = threading.Lock()
        self.status_written_at: float = 0.0
        self.last_poll_at: float = 0.0

    def get(self, addr: int) -> int:
        with self.lock:
            return int(self.values[addr]) & 0xFFFF

    def set(self, addr: int, value: int) -> None:
        with self.lock:
            self.values[addr] = int(value) & 0xFFFF
            if STATUS_START <= addr < STATUS_START + STATUS_COUNT:
                self.status_written_at = time.monotonic()

    def get_range(self, addr: int, count: int) -> list[int]:
        with self.lock:
            return [int(v) & 0xFFFF for v in self.values[addr : addr + count]]

    def set_range(self, addr: int, values: list[int]) -> None:
        with self.lock:
            for i, value in enumerate(values):
                self.values[addr + i] = int(value) & 0xFFFF
            if touches_status(addr, len(values)):
                self.status_written_at = time.monotonic()

    def note_poll(self) -> None:
        """Mach3 read (Input-Holding). Do not call this from our own get()."""
        with self.lock:
            first = self.last_poll_at <= 0
            self.last_poll_at = time.monotonic()
        if first:
            print("Mach3 is polling Modbus.", flush=True)

    def connected(self, timeout_s: float = CONNECTED_TIMEOUT_S) -> bool:
        with self.lock:
            seen = max(self.status_written_at, self.last_poll_at)
            if seen <= 0:
                return False
            return (time.monotonic() - seen) <= timeout_s
