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
Tablet browser  --Wi-Fi-->  Python server on Mach3 PC  --COM/OLE-->  Mach3  --> mill
```

Mach3 does not speak HTTP. The mill’s “IP address” is the Windows PC. The tablet never talks to Mach3 directly.

`MACH3_BACKEND=mock` simulates the machine so you can develop the UI on a Mac. `MACH3_BACKEND=com` is the shop mode and only works on the Mach3 PC (`pywin32` + Mach3 already running).

## Safety

- The **physical E-stop** is the real E-stop. Stop/Reset in this app are software commands.
- Jog is press-and-hold in Cont mode. Releasing the button, leaving the page, or losing Wi-Fi must stop jogging.
- The server stops all jogging if no heartbeat arrives for `MACH3_WATCHDOG_MS` (default 200 ms) while an axis is jogging.
- Jog is refused unless Mach3 looks ready (not in E-stop / Reset needed / in cycle).
- Optional shop PIN (`MACH3_PIN`) so a random phone on the Wi-Fi cannot jog.

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
2. Install **32-bit** Python 3.11+ on that PC (Mach3 is 32-bit; 64-bit Python often cannot see its OLE class).
3. Copy this folder onto the PC.
4. In a command prompt from this folder:

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-windows.txt
```

5. Start **Mach3 first** and Reset so the machine is ready.
6. One-time: double-click `install-desktop-shortcut.bat`. That puts **Mach3 Pendant** on the Desktop with the app icon.
7. Daily: start Mach3, then double-click **Mach3 Pendant**. It starts the server and opens the UI in the PC browser. On the tablet, same Wi-Fi, open `http://<pc-ip>:8080` (or add that page to the Home Screen).
8. Leave the black console window open while you use the pendant. Close it to stop the server.

Optional PIN:

```bat
set MACH3_PIN=2468
run.bat
```

### Shop smoke-test checklist

Do this with the mill powered but in a safe state (no cutter in cut, plenty of clearance):

- [ ] Mach3 DRO matches the tablet DRO (move an axis on Mach3, tablet follows).
- [ ] Cont jog: hold X+ / X−, motion while held, stops on release.
- [ ] Step jog: one increment per tap; step size buttons match the move.
- [ ] Feed override slider changes Mach3’s FRO display.
- [ ] Stop halts jog; jogging is blocked until Reset.
- [ ] Reset re-enables jogging.
- [ ] Watchdog: hold Cont jog, then turn tablet Wi-Fi off — axis must stop within a fraction of a second.
- [ ] Physical E-stop still kills motion independently of the app.

If DRO works but jog/FRO does not, OEM codes may not match this Mach3 screenset. See `src/mach3/oem.py` and the Mach3 Macro Programmer’s Reference.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MACH3_BACKEND` | `mock` (`com` in `run.bat`) | `mock` or `com` |
| `MACH3_HOST` | `0.0.0.0` | Bind address |
| `MACH3_PORT` | `8080` | HTTP port |
| `MACH3_PIN` | unset | Optional shop PIN |
| `MACH3_WATCHDOG_MS` | `200` | Jog-off if heartbeats stop |
| `MACH3_DRO_HZ` | `10` | DRO WebSocket rate |

## Layout

- `src/mach3/` — Mach3 client protocol, mock, Windows COM adapter, OEM codes
- `src/server/` — FastAPI REST + `/ws/state`
- `src/web/` — landscape PWA pendant
- `tests/` — mock client, watchdog, API

## Troubleshooting

- **could not attach to Mach3 / Invalid class string** — many Mach3 installs never write the OLE name `Mach4.Document`. The server attaches with Mach3’s known document CLSID instead. Mach3 must already be running. If it still fails, start Mach3 once as Administrator, or install **32-bit** Python 3.11 and recreate `.venv`.
- **pywin32 is required** — use `requirements-windows.txt` on the mill PC, not the Mac requirements file.
- **Tablet cannot connect** — same LAN, Windows firewall allow port 8080 inbound, PC IP has not changed.
- **Jog feels laggy or watchdog false-trips** — raise `MACH3_WATCHDOG_MS` slightly; keep press-and-hold jogging (never tap-to-start continuous jog).
