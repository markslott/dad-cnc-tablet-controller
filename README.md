# Mach3 Tablet Pendant

Wireless jog pendant for a Mach3 mill: a Python server on the **Windows PC that runs Mach3**, and a tablet browser UI on the same Wi-Fi.

This is a pendant, not a replacement for the machine’s physical E-stop.

## What v1 does

- Live X/Y/Z DRO
- Continuous jog (press-and-hold) and step jog
- Feed-rate override
- Stop and Reset
- Server-side jog watchdog: if the tablet drops off Wi-Fi while jogging, motion is cancelled

Not in v1: Cycle Start, loading files, spindle, homing, probing, offsets.

## How it is wired

```
Tablet browser  --Wi-Fi-->  Python server on Mach3 PC  --Modbus TCP-->  Mach3 (master + Brains)  --> mill
```

Mach3 does not speak HTTP. The mill’s “IP address” is the Windows PC. The tablet never talks to Mach3 directly.

Mach3’s Ethernet path is **Modbus TCP**, and Mach3 is the **master**: it polls our Python process, which is a Modbus **slave** on this PC port **502** (Mach3 has no port box; 502 is fixed). Brains map those registers to DRO, jog, Stop, and Reset.

`MACH3_BACKEND=mock` simulates the machine so you can develop the UI on a Mac. `MACH3_BACKEND=modbus` is the shop default (`run.bat`). `MACH3_BACKEND=com` is the old OLE path and is not required.

## Safety

- The **physical E-stop** is the real E-stop. Stop/Reset in this app are software commands.
- Jog is press-and-hold in Cont mode. Releasing the button, leaving the page, or losing Wi-Fi must stop jogging.
- The server stops all jogging if no heartbeat arrives for `MACH3_WATCHDOG_MS` (default 200 ms) while an axis is jogging.
- Jog is refused unless Mach3 looks ready (not in E-stop / Reset needed / in cycle).
- Optional shop PIN (`MACH3_PIN`) so a random phone on the Wi-Fi cannot jog.
- If the Python process dies while jogging, Mach3 must treat a Modbus comms-fail as Stop (see Brain recipe below).

If 200 ms is too tight for a weak shop Wi-Fi, raise `MACH3_WATCHDOG_MS` (for example 400). Do not disable it.

## Develop on a Mac (mock)

```bash
cd dad-cnc-tablet-controller
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
MACH3_BACKEND=mock python -m src.server
```

Open http://127.0.0.1:8080 on this machine, or `http://<mac-ip>:8080` on a tablet on the same network.

```bash
pytest
```

## Shop PC (real Mach3)

1. Give the Mach3 PC a **static DHCP reservation** (or a fixed LAN IP).
2. Install Python 3.11+ on that PC (64-bit is fine).
3. Copy this folder onto the PC.
4. In a command prompt from this folder:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

5. One-time: configure Mach3 TCP Modbus and Brains (next section).
6. One-time: double-click `install-desktop-shortcut.bat`. That puts **Mach3 Pendant** on the Desktop with the app icon.
7. Daily: start Mach3, turn **TCP Modbus Run** on, then double-click **Mach3 Pendant**. It starts the server and opens the UI in the PC browser. On the tablet, same Wi-Fi, open `http://<pc-ip>:8080` (or add that page to the Home Screen). The tablet can show “waiting for Mach3” until Mach3 is polling.
8. Leave the black console window open while you use the pendant. Close it to stop the server.

Optional PIN:

```bat
set MACH3_PIN=2468
run.bat
```

### One-time Mach3 TCP Modbus

1. Config → Ports and Pins: tick **Modbus Input/Output**, **Modbus Plugin Supported**, and **TCP Modbus Support**. Restart Mach3.
2. Function Cfg’s → Setup TCP Modbus:
   - Master address `127.0.0.1` (this PC). If Test says connection timeout, try the mill PC’s LAN IP instead — never the motion-controller IP. Mach3 always uses port **502**.
   - Slave `1` if that column exists
   - Cfg #0: **Input-Holding**, 16 registers, local `0`, modbus `0`, refresh ~50 ms (Mach3 **reads** jog/Stop/Reset/FRO)
   - Cfg #1: **Output-Holding**, 16 registers, local `0`, modbus `100`, refresh ~50 ms (Mach3 **writes** DRO/LEDs)
   - Test (pendant must already be listening on 502), then TCP Modbus Run
   - If Windows refuses port 502, right-click **Mach3 Pendant** → Run as administrator
3. Start the pendant once so the slave is listening, then use the Modbus Test page if Mach3 has one, and confirm Cfg #0 / Cfg #1 are not in error.

Register map (16-bit; DRO is a signed 32-bit value × 10000, high word first):

| Cfg | Addr | Direction | Meaning |
| --- | --- | --- | --- |
| #0 | 0–2 | Mach3 reads | Jog X/Y/Z: `0` off, `1` +, `2` − |
| #0 | 3 | Mach3 reads | Stop pulse (`1` then `0`) |
| #0 | 4 | Mach3 reads | Reset pulse |
| #0 | 5 | Mach3 reads | FRO percent 0–200 |
| #0 | 6 | Mach3 reads | Jog mode: `0` cont, `1` step |
| #0 | 7 | Mach3 reads | Step size × 1000 |
| #0 | 8 | Mach3 reads | Step-jog pulse (packed axis+dir) |
| #0 | 9 | Mach3 reads | Alive: `1` while the pendant server is up |
| #1 | 100–105 | Mach3 writes | X/Y/Z DRO (two regs each) |
| #1 | 106 | Mach3 writes | FRO actual |
| #1 | 107–109 | Mach3 writes | E-stop LED, Reset OK, In cycle |

### Brain recipe

Operator → Brain Control. Build two brains in the Brain editor (`.brn` files live in `C:\Mach3\Brains`). Enable them, then Reload All Brains.

**Commands (Cfg #0):**

- If Cfg #0 comms status is **not OK**, press Stop (OEM 1003) and do not jog. This is the safety net if Python dies while an axis is jogging.
- Jog X/Y/Z holding registers `1` / `2` → hold the mill screen + / − jog buttons (same buttons you use on the Mach3 screen). `0` → release.
- Stop pulse (addr 3) → OEM button 1003. Reset pulse (addr 4) → OEM button 1021.
- FRO (addr 5) → OEM DRO 818.
- Alive (addr 9) going to `0` → Stop.

**Status (Cfg #1):**

- Work X/Y/Z DROs → addr 100–105 as integer × 10000 (high word, then low word).
- E-stop LED 12 → addr 107. Reset OK LED 825 → addr 108. In-cycle LED 11 → addr 109.
- FRO DRO 818 → addr 106.

Daily: Mach3 running, TCP Modbus Run on, brains enabled, then Mach3 Pendant.

### Shop smoke-test checklist

Do this with the mill powered but in a safe state (no cutter in cut, plenty of clearance):

- [ ] Mach3 DRO matches the tablet DRO (move an axis on Mach3, tablet follows).
- [ ] Cont jog: hold X+ / X−, motion while held, stops on release.
- [ ] Step jog: one increment per tap; step size buttons match the move.
- [ ] Feed override slider changes Mach3’s FRO display.
- [ ] Stop halts jog; jogging is blocked until Reset.
- [ ] Reset re-enables jogging.
- [ ] Watchdog: hold Cont jog, then turn tablet Wi-Fi off — axis must stop within a fraction of a second.
- [ ] Kill the pendant console while jogging (safe clearance) — Mach3 must Stop because of the comms-fail Brain.
- [ ] Physical E-stop still kills motion independently of the app.

If DRO works but jog/FRO does not, the Brain terminations may not match this screenset. See `src/mach3/oem.py` and the Mach3 Macro Programmer’s Reference.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MACH3_BACKEND` | `mock` (`modbus` in `run.bat`) | `mock`, `modbus`, or `com` |
| `MACH3_HOST` | `0.0.0.0` | HTTP bind address |
| `MACH3_PORT` | `8080` | HTTP port |
| `MACH3_MODBUS_HOST` | `0.0.0.0` | Modbus slave bind address (all interfaces so Mach3 can use 127.0.0.1 or the PC LAN IP) |
| `MACH3_MODBUS_PORT` | `502` | Modbus slave port (Mach3 has no port box; 502 may need Run as administrator) |
| `MACH3_PIN` | unset | Optional shop PIN |
| `MACH3_WATCHDOG_MS` | `200` | Jog-off if heartbeats stop |
| `MACH3_DRO_HZ` | `10` | DRO WebSocket rate |

## Layout

- `src/mach3/` — Mach3 client protocol, mock, Modbus TCP slave, optional COM adapter, OEM codes
- `src/server/` — FastAPI REST + `/ws/state`
- `src/web/` — landscape PWA pendant
- `tests/` — mock client, Modbus map, watchdog, API

## Troubleshooting

- **waiting for Mach3 TCP Modbus / connection timeout** — Mach3 is not reaching port 502. Master address must be `127.0.0.1` or this PC’s LAN IP (not the SmoothStepper/controller). Pendant must be running first; if listen failed, Run as administrator.
- **DRO stays at zero** — Cfg #1 Output-Holding / status Brain is not writing registers 100–105.
- **Jog does nothing** — Cfg #0 Input-Holding / command Brain is not reading registers 0–2, or the Brain is wired to the wrong screen buttons.
- **Axis keeps jogging after the pendant dies** — add the Cfg #0 comms-fail → Stop lobe.
- **Tablet cannot connect** — same LAN, Windows firewall allow port 8080 inbound, PC IP has not changed. Modbus stays on localhost; do not open 502 on the LAN.
- **Jog feels laggy or watchdog false-trips** — raise `MACH3_WATCHDOG_MS` slightly; keep press-and-hold jogging (never tap-to-start continuous jog).
