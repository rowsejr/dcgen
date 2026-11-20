"""Small numeric helpers shared by the engines."""
from __future__ import annotations


def interp(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear lookup, clamped at the ends (xs ascending)."""
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return float(ys[i - 1] + f * (ys[i] - ys[i - 1]))
    return float(ys[-1])
