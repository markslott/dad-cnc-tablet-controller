from src.mach3.mock_client import MockMach3Client
from src.server.app import create_app
from src.server.config import Settings


def make_settings(**overrides) -> Settings:
    data = dict(
        backend="mock",
        host="127.0.0.1",
        port=8080,
        pin=None,
        watchdog_ms=200,
        dro_hz=10,
    )
    data.update(overrides)
    return Settings(**data)


def make_app(mach3: MockMach3Client | None = None, **setting_overrides):
    client = mach3 or MockMach3Client()
    app = create_app(mach3=client, settings=make_settings(**setting_overrides))
    return app, client
