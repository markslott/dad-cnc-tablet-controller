from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.mach3.client import Mach3Client
from src.mach3.factory import create_client
from src.server.config import Settings, load_settings
from src.server.watchdog import JogWatchdog

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PIN_HEADER = "x-shop-pin"
PUMP_PATH = "/api/mach3/pump"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    h = host.lower().split("%")[0]
    return h in _LOOPBACK_HOSTS or h.endswith("127.0.0.1")


class JogAxisBody(BaseModel):
    axis: int = Field(ge=0, le=2)
    direction: int = Field(ge=0, le=1)


class StepJogBody(BaseModel):
    axis: int = Field(ge=0, le=2)
    direction: int = Field(ge=0, le=1)
    step_size: float | None = None


class PercentBody(BaseModel):
    percent: float


class ModeBody(BaseModel):
    mode: str


class StepSizeBody(BaseModel):
    size: float


class PinBody(BaseModel):
    pin: str


def _pin_ok(settings: Settings, provided: str | None) -> bool:
    if not settings.pin_required:
        return True
    return provided is not None and provided == settings.pin


def create_app(
    mach3: Mach3Client | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    state: dict[str, Any] = {
        "client": mach3,
        "watchdog": None,
        "settings": settings,
        "ws_clients": set(),
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = state["client"] or create_client(
            settings.backend,
            modbus_host=settings.modbus_host,
            modbus_port=settings.modbus_port,
        )
        state["client"] = client
        watchdog = JogWatchdog(client, timeout_s=settings.watchdog_s)
        state["watchdog"] = watchdog
        loop_task = asyncio.create_task(_watchdog_loop(state), name="jog-watchdog")
        try:
            yield
        finally:
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
            try:
                await asyncio.to_thread(client.jog_off_all)
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    app = FastAPI(title="Mach3 Tablet Pendant", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def pin_gate(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        open_paths = {"/api/config", "/api/auth", "/manifest.json", "/sw.js", "/icon.svg"}
        if path == PUMP_PATH and _is_loopback(request.client.host if request.client else None):
            return await call_next(request)
        if path.startswith("/api/") and path not in open_paths:
            pin = request.headers.get(PIN_HEADER)
            if not _pin_ok(settings, pin):
                return _json_error(401, "shop PIN required")
        return await call_next(request)

    def client() -> Mach3Client:
        return state["client"]

    def watchdog() -> JogWatchdog:
        return state["watchdog"]

    @app.get("/api/config")
    async def api_config():
        return {
            "backend": settings.backend,
            "pin_required": settings.pin_required,
            "watchdog_ms": settings.watchdog_ms,
            "dro_hz": settings.dro_hz,
        }

    @app.post("/api/auth")
    async def api_auth(body: PinBody):
        if not _pin_ok(settings, body.pin):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid PIN")
        return {"ok": True}

    @app.get("/api/status")
    async def api_status():
        status_obj = await asyncio.to_thread(client().get_status)
        payload = status_obj.as_dict()
        payload["watchdog_tripped"] = watchdog().trip_count
        return payload

    @app.post("/api/heartbeat")
    async def api_heartbeat():
        watchdog().heartbeat()
        return {"ok": True}

    @app.post("/api/jog/on")
    async def api_jog_on(body: JogAxisBody):
        try:
            await asyncio.to_thread(client().jog_on, body.axis, body.direction)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        watchdog().mark_jog_on(body.axis)
        return {"ok": True}

    @app.post("/api/jog/off")
    async def api_jog_off(body: JogAxisBody):
        await asyncio.to_thread(client().jog_off, body.axis)
        watchdog().mark_jog_off(body.axis)
        return {"ok": True}

    @app.post("/api/jog/off-all")
    async def api_jog_off_all():
        await asyncio.to_thread(client().jog_off_all)
        watchdog().mark_jog_off_all()
        return {"ok": True}

    @app.post("/api/jog/step")
    async def api_jog_step(body: StepJogBody):
        size = body.step_size
        if size is None:
            status_obj = await asyncio.to_thread(client().get_status)
            size = status_obj.step_size
        try:
            await asyncio.to_thread(client().step_jog, body.axis, body.direction, size)
        except PermissionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        watchdog().heartbeat()
        return {"ok": True}

    @app.post("/api/jog/mode")
    async def api_jog_mode(body: ModeBody):
        try:
            await asyncio.to_thread(client().set_jog_mode, body.mode)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if body.mode == "step":
            watchdog().mark_jog_off_all()
        return {"ok": True, "mode": body.mode}

    @app.post("/api/jog/step-size")
    async def api_step_size(body: StepSizeBody):
        await asyncio.to_thread(client().set_step_size, body.size)
        return {"ok": True}

    @app.post("/api/jog/rate")
    async def api_jog_rate(body: PercentBody):
        await asyncio.to_thread(client().set_jog_rate, body.percent)
        return {"ok": True}

    @app.post("/api/feed-override")
    async def api_feed_override(body: PercentBody):
        await asyncio.to_thread(client().set_feed_override, body.percent)
        return {"ok": True}

    @app.post("/api/stop")
    async def api_stop():
        await asyncio.to_thread(client().do_stop)
        watchdog().mark_jog_off_all()
        return {"ok": True}

    @app.post("/api/reset")
    async def api_reset():
        await asyncio.to_thread(client().do_reset)
        watchdog().mark_jog_off_all()
        return {"ok": True}

    @app.post("/api/mach3/pump")
    async def api_mach3_pump(request: Request):
        host = request.client.host if request.client else None
        if not _is_loopback(host):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "pump is localhost only")
        mach3 = client()
        exchange = getattr(mach3, "exchange_pump", None)
        if exchange is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "MACH3_BACKEND is not pump")
        body = (await request.body()).decode("ascii", errors="replace")
        try:
            reply = await asyncio.to_thread(exchange, body)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return PlainTextResponse(reply)

    @app.websocket("/ws/state")
    async def ws_state(
        ws: WebSocket,
        pin: str | None = Query(default=None),
        x_shop_pin: str | None = Header(default=None, alias="x-shop-pin"),
    ):
        provided = pin or x_shop_pin
        if not _pin_ok(settings, provided):
            await ws.close(code=1008)
            return
        await ws.accept()
        state["ws_clients"].add(ws)
        watchdog().heartbeat()
        period = 1.0 / max(settings.dro_hz, 1.0)
        try:
            sender = asyncio.create_task(_ws_send_loop(ws, client(), watchdog(), period))
            try:
                while True:
                    raw = await ws.receive_text()
                    _handle_ws_message(raw, watchdog())
            finally:
                sender.cancel()
                try:
                    await sender
                except asyncio.CancelledError:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            state["ws_clients"].discard(ws)
            if not state["ws_clients"] and watchdog().is_jogging():
                await asyncio.to_thread(client().jog_off_all)
                watchdog().mark_jog_off_all()

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


def _handle_ws_message(raw: str, watchdog: JogWatchdog) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        watchdog.heartbeat()
        return
    if msg.get("type") in (None, "heartbeat", "pong"):
        watchdog.heartbeat()


async def _ws_send_loop(
    ws: WebSocket,
    mach3: Mach3Client,
    watchdog: JogWatchdog,
    period: float,
) -> None:
    while True:
        status_obj = await asyncio.to_thread(mach3.get_status)
        payload = status_obj.as_dict()
        payload["watchdog_tripped"] = watchdog.trip_count
        await ws.send_json(payload)
        await asyncio.sleep(period)


async def _watchdog_loop(state: dict[str, Any]) -> None:
    while True:
        await asyncio.sleep(0.05)
        watchdog: JogWatchdog | None = state.get("watchdog")
        client: Mach3Client | None = state.get("client")
        if watchdog is None or client is None:
            continue
        try:
            await asyncio.to_thread(watchdog.trip_if_expired)
        except Exception:
            try:
                await asyncio.to_thread(client.jog_off_all)
            except Exception:
                pass


def _json_error(code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": detail}, status_code=code)


app = create_app()
