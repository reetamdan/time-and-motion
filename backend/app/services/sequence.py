"""Thread-safe monotonically increasing snapshot sequence and resync watermark."""

from __future__ import annotations

import threading


class SequenceState:
    def __init__(self) -> None:
        self._sequence = 0
        self._lock = threading.Lock()

    def bump(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    @property
    def current(self) -> int:
        with self._lock:
            return self._sequence

    @property
    def watermark(self) -> int:
        return self.current
