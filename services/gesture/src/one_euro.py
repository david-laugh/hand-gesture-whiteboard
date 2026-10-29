"""One-Euro 필터 (순수 Python).

천천히 움직일 땐 강하게 다듬어 떨림을 없애고, 빠르게 움직일 땐 약하게 다듬어 늦게 따라오지 않게 한다.
참고: https://gery.casiez.net/1euro/
"""
import math


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """값 하나(예: x좌표)를 다듬는 필터."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self) -> None:
        self._x: float | None = None
        self._dx = 0.0

    def __call__(self, x: float, dt: float) -> float:
        """dt: 이전 값과의 시간 간격(초). 0보다 커야 한다."""
        if self._x is None or dt <= 0:
            self._x = x
            return x
        dx = (x - self._x) / dt
        self._dx += _alpha(self.d_cutoff, dt) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x += _alpha(cutoff, dt) * (x - self._x)
        return self._x


class PointFilter:
    """(x, y) 좌표용. x와 y에 필터를 하나씩 쓴다."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float):
        self._fx = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self._fy = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()

    def __call__(self, x: float, y: float, dt: float) -> tuple[float, float]:
        return self._fx(x, dt), self._fy(y, dt)
