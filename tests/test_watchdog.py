import time

from src.mach3.mock_client import MockMach3Client
from src.mach3.oem import Axis, JOG_DIR_POS
from src.server.watchdog import JogWatchdog


def test_watchdog_does_not_trip_while_heartbeats_arrive():
    mock = MockMach3Client()
    wd = JogWatchdog(mock, timeout_s=0.08)
    mock.jog_on(Axis.X, JOG_DIR_POS)
    wd.mark_jog_on(Axis.X)
    for _ in range(4):
        time.sleep(0.03)
        wd.heartbeat()
        assert not wd.trip_if_expired()
    assert mock.get_status().jogging
    mock.jog_off_all()
    wd.mark_jog_off_all()


def test_watchdog_trips_and_calls_jog_off_all():
    mock = MockMach3Client()
    wd = JogWatchdog(mock, timeout_s=0.05)
    mock.jog_on(Axis.X, JOG_DIR_POS)
    wd.mark_jog_on(Axis.X)
    assert mock.get_status().jogging
    time.sleep(0.08)
    assert wd.trip_if_expired()
    assert wd.trip_count == 1
    assert not mock.get_status().jogging
    assert not wd.is_jogging()


def test_watchdog_idle_does_not_trip():
    mock = MockMach3Client()
    wd = JogWatchdog(mock, timeout_s=0.01)
    time.sleep(0.03)
    assert not wd.trip_if_expired()
    assert wd.trip_count == 0
