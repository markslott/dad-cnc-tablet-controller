from src.mach3.mock_client import MockMach3Client
from src.mach3.oem import Axis, JOG_DIR_NEG, JOG_DIR_POS


def test_mock_starts_ready_to_jog():
    m = MockMach3Client()
    status = m.get_status()
    assert status.backend == "mock"
    assert status.can_jog
    assert status.dro.x == 0
    assert status.feed_override == 100


def test_continuous_jog_moves_x():
    m = MockMach3Client()
    m.set_jog_rate(100)
    m.jog_on(Axis.X, JOG_DIR_POS)
    import time

    time.sleep(0.05)
    status = m.get_status()
    assert status.jogging
    assert status.dro.x > 0
    m.jog_off(Axis.X)
    assert not m.get_status().jogging


def test_step_jog_moves_exact_increment():
    m = MockMach3Client()
    m.step_jog(Axis.Y, JOG_DIR_POS, 0.1)
    assert abs(m.get_dro().y - 0.1) < 1e-9
    m.step_jog(Axis.Y, JOG_DIR_NEG, 0.1)
    assert abs(m.get_dro().y) < 1e-9


def test_stop_blocks_jog_until_reset():
    m = MockMach3Client()
    m.do_stop()
    status = m.get_status()
    assert status.stopped
    assert not status.can_jog
    try:
        m.jog_on(Axis.X, JOG_DIR_POS)
        raise AssertionError("jog should have been refused")
    except PermissionError:
        pass
    m.do_reset()
    assert m.can_jog()
    m.jog_on(Axis.X, JOG_DIR_POS)
    m.jog_off_all()


def test_feed_override_clamped():
    m = MockMach3Client()
    m.set_feed_override(999)
    assert m.get_status().feed_override == 200
    m.set_feed_override(-5)
    assert m.get_status().feed_override == 0


def test_step_size_snaps_to_allowed():
    m = MockMach3Client()
    m.set_step_size(0.012)
    assert m.get_status().step_size == 0.01
