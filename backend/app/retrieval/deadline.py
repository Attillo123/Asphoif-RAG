from __future__ import annotations

import time


class Deadline:
    def __init__(self, total_seconds: float) -> None:
        self.started = time.monotonic()
        self.total_seconds = total_seconds

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed_seconds)

    @property
    def remaining_ms(self) -> int:
        return int(self.remaining_seconds * 1000)

    @property
    def total_ms(self) -> int:
        return int(self.total_seconds * 1000)
