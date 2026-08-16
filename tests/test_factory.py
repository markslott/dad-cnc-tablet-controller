import pytest

from src.mach3.factory import create_client
from src.mach3.mock_client import MockMach3Client
from src.mach3.modbus_client import ModbusMach3Client


def test_factory_mock():
    client = create_client("mock")
    assert isinstance(client, MockMach3Client)
    client.close()


def test_factory_modbus():
    client = create_client("modbus", start_server=False)
    assert isinstance(client, ModbusMach3Client)
    client.close()


def test_factory_rejects_unknown():
    with pytest.raises(ValueError) as excinfo:
        create_client("plc")
    assert "plc" in str(excinfo.value)
    assert "pump" in str(excinfo.value)
