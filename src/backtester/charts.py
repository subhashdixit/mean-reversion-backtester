"""Render deterministic local PNG charts without a browser CDN dependency."""

import base64
import io
from threading import Lock

from matplotlib.figure import Figure

_PLOT_LOCK = Lock()


def _render_chart(frame):
    """Return a base64 PNG comparing equity and measured strategy drawdown."""
    figure = Figure(figsize=(10, 6), layout="constrained", facecolor="#f4f6f5")
    top, bottom = figure.subplots(2, 1, sharex=True)
    top.plot(
        frame["date"],
        frame["strategy_equity"],
        label="Strategy after costs",
        color="#24664e",
    )
    top.plot(
        frame["date"],
        frame["benchmark_equity"],
        label="Buy and hold after costs",
        color="#8b6b3c",
    )
    top.set_ylabel("Growth of 1 initial unit")
    top.legend()
    bottom.fill_between(
        frame["date"], frame["strategy_drawdown"] * 100, 0, color="#bf6956", alpha=0.5
    )
    bottom.set_ylabel("Strategy drawdown (%)")
    for axis in [top, bottom]:
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(alpha=0.2)
    output = io.BytesIO()
    figure.savefig(output, format="png", dpi=120)
    return base64.b64encode(output.getvalue()).decode()


def chart(frame):
    """Serialize figure rendering when two web requests arrive concurrently."""
    with _PLOT_LOCK:
        return _render_chart(frame)
