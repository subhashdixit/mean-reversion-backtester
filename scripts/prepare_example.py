"""Create a clearly labeled synthetic price fixture, not market performance."""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def prepare() -> None:
    """Generate repeatable positive prices with trend, cycles and noise."""
    generator = np.random.default_rng(42)
    steps = np.arange(700)
    log_price = (
        4.6
        + 0.0004 * steps
        + 0.12 * np.sin(steps / 16)
        + np.cumsum(generator.normal(0, 0.008, 700))
    )
    frame = pd.DataFrame(
        {"date": pd.bdate_range("2020-01-01", periods=700), "close": np.exp(log_price)}
    )
    (ROOT / "data").mkdir(exist_ok=True)
    frame.to_csv(ROOT / "data/synthetic-prices.csv", index=False)


if __name__ == "__main__":
    prepare()
