from src.mach3.factory import create_client
from src.mach3.mock_client import MockMach3Client


def test_factory_mock():
    client = create_client("mock")
    assert isinstance(client, MockMach3Client)
    client.close()


def test_factory_rejects_unknown():
    try:
        create_client("modbus")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "modbus" in str(exc)
