"""Mach3 OEM button, DRO, and LED codes.

These numbers come from the Mach3 V3 Macro Programmer's Reference and common
remote-control implementations. Confirm on the shop PC if a control does not
match the Mach3 screen — codes can differ slightly by screenset/version.

GetDRO(0/1/2) is used for X/Y/Z work coordinates (not OEM DROs).
"""

from enum import IntEnum


class Axis(IntEnum):
    X = 0
    Y = 1
    Z = 2


# JogOn direction: 0 = positive, 1 = negative (Mach3 scripter convention).
JOG_DIR_POS = 0
JOG_DIR_NEG = 1

# OEM buttons
OEM_BTN_JOG_CONT = 204
OEM_BTN_JOG_INC = 205
OEM_BTN_CYCLE_START = 1000  # not used in v1 pendant
OEM_BTN_FEED_HOLD = 1001
OEM_BTN_STOP = 1003
OEM_BTN_RESET = 1021

# OEM DROs — verify on this Mach3 build if FRO/increment look wrong.
OEM_DRO_JOG_INCREMENT = 3
OEM_DRO_FEED_OVERRIDE = 818

# OEM LEDs — Macro Programmer's Reference: Emergency 19, Reset 800, Start 804.
OEM_LED_ESTOP = 19
OEM_LED_IN_CYCLE = 804
OEM_LED_RESET_OK = 800

AXIS_NAMES = {Axis.X: "X", Axis.Y: "Y", Axis.Z: "Z"}
