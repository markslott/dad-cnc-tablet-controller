from __future__ import annotations

import threading
import time

from src.mach3.client import Mach3Client


class JogWatchdog:
    """If a jog is active and heartbeats stop, JogOff every axis.

    The tablet must send heartbeats (~50 ms) while a jog button is held. If the
    WebSocket drops or Wi-Fi stalls longer than timeout_s, motion is cancelled
    on the server — do not rely on the browser sending jog_off.
    """

    def __init__(self, client: Mach3Client, timeout_s: float = 0.2) -> None:
        self._client = client
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._last_heartbeat = time.monotonic()
        self._jogging_axes: set[int] = set()
        self._trip_count = 0

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def trip_count(self) -> int:
        return self._trip_count

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def mark_jog_on(self, axis: int) -> None:
        with self._lock:
            self._jogging_axes.add(axis)
            self._last_heartbeat = time.monotonic()

    def mark_jog_off(self, axis: int) -> None:
        with self._lock:
            self._jogging_axes.discard(axis)

    def mark_jog_off_all(self) -> None:
        with self._lock:
            self._jogging_axes.clear()

    def is_jogging(self) -> bool:
        with self._lock:
            return bool(self._jogging_axes)

    def heartbeat_age_s(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_heartbeat

    def expired(self) -> bool:
        with self._lock:
            if not self._jogging_axes:
                return False
            return (time.monotonic() - self._last_heartbeat) > self._timeout_s

    def trip_if_expired(self) -> bool:
        """Stop all jogging if the heartbeat timed out. Returns True if tripped."""
        with self._lock:
            if not self._jogging_axes:
                return False
            if (time.monotonic() - self._last_heartbeat) <= self._timeout_s:
                return False
            self._jogging_axes.clear()
            self._trip_count += 1
            should_stop = True
        if should_stop:
            self._client.jog_off_all()
        return True
