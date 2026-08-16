from __future__ import annotations

import os

from src.mach3.client import Mach3Client


def create_client(backend: str | None = None) -> Mach3Client:
    name = (backend or os.environ.get("MACH3_BACKEND", "mock")).strip().lower()
    if name == "mock":
        from src.mach3.mock_client import MockMach3Client

        return MockMach3Client()
    if name == "com":
        from src.mach3.com_client import ComMach3Client

        return ComMach3Client()
    raise ValueError(f"unknown MACH3_BACKEND {name!r}; use 'mock' or 'com'")
