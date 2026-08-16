from __future__ import annotations

import os

from src.mach3.client import Mach3Client


def create_client(
    backend: str | None = None,
    *,
    modbus_host: str | None = None,
    modbus_port: int | None = None,
    start_server: bool = True,
) -> Mach3Client:
    name = (backend or os.environ.get("MACH3_BACKEND", "mock")).strip().lower()
    if name == "mock":
        from src.mach3.mock_client import MockMach3Client

        return MockMach3Client()
    if name == "com":
        from src.mach3.com_client import ComMach3Client

        return ComMach3Client()
    if name == "modbus":
        from src.mach3.modbus_client import ModbusMach3Client

        host = modbus_host or os.environ.get("MACH3_MODBUS_HOST", "127.0.0.1")
        port_raw = os.environ.get("MACH3_MODBUS_PORT", "1502")
        port = modbus_port if modbus_port is not None else int(port_raw)
        return ModbusMach3Client(host=host, port=port, start_server=start_server)
    raise ValueError(f"unknown MACH3_BACKEND {name!r}; use 'mock', 'modbus', or 'com'")
