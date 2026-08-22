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
Tablet browser  --Wi-Fi-->  Python server on Mach3 PC  <--TCP Modbus 502-->  Mach3 Brains  --> mill
```

Mach3 is the Modbus master. The pendant is the slave on port **502**. You still have to build two Brains by hand (ArtSoft never documented `.brn`). `MACH3_BACKEND=pump` is the macropump file mailbox. `MACH3_BACKEND=com` is the OLE path and is not required.

`MACH3_BACKEND=mock` simulates the machine so you can develop the UI on a Mac. `MACH3_BACKEND=modbus` is the shop default (`run.bat`).

## Safety

- The **physical E-stop** is the real E-stop. Stop/Reset in this app are software commands.
- Jog is press-and-hold in Cont mode. Releasing the button, leaving the page, or losing Wi-Fi must stop jogging.
- The server stops all jogging if no heartbeat arrives for `MACH3_WATCHDOG_MS` (default 200 ms) while an axis is jogging.
- Jog is refused unless Mach3 looks ready (not in E-stop / Reset needed / in cycle).
- Optional shop PIN (`MACH3_PIN`) so a random phone on the Wi-Fi cannot jog.
- If the Python process dies while jogging, the command file goes idle and Mach3 jogs off.

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

5. One-time: double-click `install-desktop-shortcut.bat`. That puts **Mach3 Pendant** on the Desktop with the app icon.
6. One-time in Mach3: follow **TCP Modbus + Brains** below. Build the two Brains, then enable them.
7. Daily: start **Mach3 Pendant** first (so port 502 is listening), then Mach3 with **TCP Modbus Run** on. On the tablet, same Wi-Fi, open `http://<pc-ip>:8080`. The tablet shows “waiting for Mach3” until Mach3 polls.
8. Leave the black console window open while you use the pendant. Close it to stop the server.

If Windows refuses port 502, right-click **Mach3 Pendant** → Run as administrator.

Optional PIN:

```bat
set MACH3_PIN=2468
run.bat
```

### Optional: Mach3 macropump

`mach3/macropump.m1s` is a Brain stand-in if you set `MACH3_BACKEND=pump`. Mach3 runs it when **Run Macro Pump** is ticked. Each cycle it writes DRO/LEDs to `C:\Mach3\pendant-status.txt` and reads jog/Stop/Reset from `C:\Mach3\pendant-cmd.txt`. Double-click `install-macropump.bat` once, then tick **Run Macro Pump** → OK and restart Mach3.

### TCP Modbus + Brains (shop default)

`.brn` files still cannot be generated. Build Brains by hand (register map below). `run.bat` already sets `MACH3_BACKEND=modbus`.

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

### Brain recipe (do this in Mach3)

Brains are Mach3’s visual “if this register, then press that button” editor. Files live in `C:\Mach3\Brains`. We cannot ship `.brn` files; you build two brains once.

**Local vs Modbus numbers.** In Setup TCP Modbus, Cfg #0 local `0` maps to slave register `0`, Cfg #1 local `0` maps to slave register `100`. In the Brain editor you always pick the **local** number (0–15), not 100.

**Confirm jog OEM numbers on this screenset** before wiring jog. On a stock mill 1024.set they are:

| Screen button | OEM |
| --- | --- |
| X+ / X− | 307 / 308 |
| Y+ / Y− | 309 / 310 |
| Z+ / Z− | 311 / 312 |
| Cont jog / Step jog | 204 / 205 |
| Stop / Reset | 1003 / 1021 |

If a button does nothing, open the screen designer (or Operator diagnostics) and read that button’s OEM number, then use yours.

#### Open the editor

1. Start the pendant first, then Mach3, then **TCP Modbus Run**. Cfg #0 / #1 must not be in error.
2. Operator → **Brain Control**.
3. **New**. Name it `Pendant-Cmd`. Save under `C:\Mach3\Brains`.
4. The editor is a chain: **input terminator** → optional **Compare / Formula** → **output terminator**. Right-click empty space (or use Add Function) for each piece, then click an output pin to an input pin to connect them.

#### Brain 1 — `Pendant-Cmd` (Mach3 reads Cfg #0)

Each row below is one function. Input type is **Modbus Input**, Cfg **#0**.

**Comms fail (do this first):**

- Input: Modbus Input Cfg #0, local **9** (Alive). Compare **equal to 0**. Output: OEM Button **1003** (Stop).
- Optional extra: if Mach3 shows a Modbus comms-fault LED for Cfg #0, wire that LED **on** → OEM Button 1003 as well. This is what stops the mill if the pendant console is closed while jogging.

**Stop / Reset (pulses from the tablet, ~0.25 s):**

- Input local **3**, Compare **equal to 1** → OEM Button **1003**.
- Input local **4**, Compare **equal to 1** → OEM Button **1021**.

**Feed override:**

- Input local **5** (0–200). Formula: none (or `/ 1`). Output: **OEM DRO 818**.

**Jog mode and step size:**

- Input local **6**, Compare **equal to 0** → OEM Button **204** (continuous).
- Input local **6**, Compare **equal to 1** → OEM Button **205** (incremental).
- Input local **7**. Formula: `/ 1000`. Output: **OEM DRO 3** (jog increment). Values from the tablet are 1, 10, 100, 1000 meaning 0.001 / 0.01 / 0.1 / 1.0.

**Continuous jog (hold while the tablet button is held):**

- Input local **0**, Compare **= 1** → OEM Button **307** (X+). Compare **= 2** → **308** (X−).
- Input local **1**, **= 1** → **309** (Y+). **= 2** → **310** (Y−).
- Input local **2**, **= 1** → **311** (Z+). **= 2** → **312** (Z−).

When the tablet releases, the register goes to `0` and the Compare goes false, which releases the button. That is the hold-to-jog behavior.

**Step jog (one increment per tap):**

Register 8 is packed. Compare equal to each value, output a one-shot of that axis jog button (or Incremental Jog On / Off if your editor has it):

| Local 8 | Move |
| --- | --- |
| 8 | X+ |
| 12 | X− |
| 9 | Y+ |
| 13 | Y− |
| 10 | Z+ |
| 14 | Z− |

Save. In Brain Control, tick **Enable** on `Pendant-Cmd`.

#### Brain 2 — `Pendant-Status` (Mach3 writes Cfg #1)

New brain named `Pendant-Status`. Output type is **Modbus Output**, Cfg **#1**. Local 0 is slave register 100.

**LEDs and FRO (do these first — they prove the status path):**

- Input: OEM LED **19** (Emergency). Output: Modbus Output Cfg #1 local **7** (slave 107).
- Input: OEM LED **800** (Reset). Output: local **8** (slave 108).
- Input: OEM LED **804** (Start / in cycle). Output: local **9** (slave 109).
- Input: OEM DRO **818**. Output: local **6** (slave 106).

**Work DRO (32-bit, × 10000, high word first).** Input is work DRO 0/1/2 (X/Y/Z), not an OEM DRO.

For X:

1. Formula `X * 10000`. If the editor has **high word / low word** or **HIWORD / LOWORD**, send high → local **0**, low → local **1**.
2. If it only has ordinary math: high = `FLOOR((X * 10000) / 65536)`, low = `(X * 10000) - high * 65536`. Negative X is awkward in Brains; if the tablet DRO is wrong only on negatives, the word-split formula is the usual cause.

Repeat for Y → local **2 / 3**, Z → local **4 / 5**.

Save. Enable `Pendant-Status`. Click **Reload All Brains**.

#### Prove it before jogging

1. Pendant console should print `Mach3 is polling Modbus.`
2. Move an axis on the Mach3 screen. Tablet DRO should follow.
3. Tablet Stop / Reset should click Mach3 Stop / Reset.
4. Then try Cont jog with plenty of clearance.

Daily: pendant first, TCP Modbus Run on, both brains enabled, then use the tablet.

### Shop smoke-test checklist

Do this with the mill powered but in a safe state (no cutter in cut, plenty of clearance):

- [ ] Mach3 DRO matches the tablet DRO (move an axis on Mach3, tablet follows).
- [ ] Cont jog: hold X+ / X−, motion while held, stops on release.
- [ ] Step jog: one increment per tap; step size buttons match the move.
- [ ] Feed override slider changes Mach3’s FRO display.
- [ ] Stop halts jog; jogging is blocked until Reset.
- [ ] Reset re-enables jogging.
- [ ] Watchdog: hold Cont jog, then turn tablet Wi-Fi off — axis must stop within a fraction of a second.
- [ ] Kill the pendant console while jogging (safe clearance) — Mach3 must Stop because Cfg #0 comms / Alive goes away.
- [ ] Physical E-stop still kills motion independently of the app.

If DRO works but jog/FRO does not on the optional Modbus path, the Brain terminations may not match this screenset. See `src/mach3/oem.py` and the Mach3 Macro Programmer’s Reference.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MACH3_BACKEND` | `mock` (`modbus` in `run.bat`) | `mock`, `pump`, `modbus`, or `com` |
| `MACH3_HOST` | `0.0.0.0` | HTTP bind address |
| `MACH3_PORT` | `8080` | HTTP port |
| `MACH3_MODBUS_HOST` | `0.0.0.0` | Modbus slave bind address (all interfaces so Mach3 can use 127.0.0.1 or the PC LAN IP) |
| `MACH3_MODBUS_PORT` | `502` | Modbus slave port (Mach3 has no port box; 502 may need Run as administrator) |
| `MACH3_PIN` | unset | Optional shop PIN |
| `MACH3_WATCHDOG_MS` | `200` | Jog-off if heartbeats stop |
| `MACH3_DRO_HZ` | `10` | DRO WebSocket rate |

## Layout

- `src/mach3/` — Mach3 client protocol, mock, macropump mailbox, Modbus TCP slave, optional COM adapter, OEM codes
- `mach3/` — `macropump.m1s` copied onto the mill PC
- `src/server/` — FastAPI REST + `/ws/state`
- `src/web/` — landscape PWA pendant
- `tests/` — mock client, pump, Modbus map, watchdog, API

## Troubleshooting

- **Run Macro Pump unchecks itself after restart** — either General Config was closed without **OK**, or Mach3 rejected `macropump.m1s` (syntax error / file not in this profile’s macros folder). Tick **Run Macro Pump** (third column), click **OK**, File → Exit, start Mach3 again. If it still clears, the script is not loading: run `install-macropump.bat` from the latest files, confirm the file is in `C:\Mach3\macros\<profile name from the lower-right corner>\`, then restart twice. A working pump updates `C:\Mach3\pendant-pump.log`.
- **waiting for Mach3 macropump** — the pump is not writing `C:\Mach3\pendant-status.txt`. Run `install-macropump.bat` again, tick **Run Macro Pump**, restart Mach3. Open `C:\Mach3\pendant-pump.log`: missing means the script is not in this profile’s macros folder. Then start the pendant; console should print `Mach3 macropump is talking`.
- **waiting for Mach3 TCP Modbus / connection timeout** — only if `MACH3_BACKEND=modbus`. Mach3 is not reaching port 502. Master address must be `127.0.0.1` or this PC’s LAN IP (not the SmoothStepper/controller). Pendant must be running first; if listen failed, Run as administrator.
- **DRO stays at zero** — macropump is not posting, or (modbus) Cfg #1 / status Brain is not writing registers 100–105.
- **Reset needed / RESET does nothing** — with pump, a Reset click should print `Reset command queued for macropump` and Mach3 should press OEM 1021. If Mach3 Reset LED 800 is inverted for your screenset, the tablet may stay on Reset needed even when Mach3 is ready.
- **Jog does nothing** — pump not applying JogOn, or (modbus) command Brain is not reading registers 0–2.
- **Axis keeps jogging after the pendant dies** — cmd file should go to jog-off; do not uncheck Run Macro Pump while jogging.
- **Tablet cannot connect** — same LAN, Windows firewall allow port 8080 inbound, PC IP has not changed.
- **Jog feels laggy or watchdog false-trips** — raise `MACH3_WATCHDOG_MS` slightly; keep press-and-hold jogging (never tap-to-start continuous jog).
