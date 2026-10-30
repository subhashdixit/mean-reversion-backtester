"""Causal rolling signals, delayed exposure and transparent cost accounting."""

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategyConfig:
    """Explicit bounded assumptions for a long-or-cash learning strategy."""

    window: int = 21
    lookback: int = 126
    lower: float = 0.15
    upper: float = 0.85
    cost_bps: float = 10
    periods_per_year: int = 252

    def validate(self) -> None:
        """Reject unsupported parameter combinations instead of coercing silently."""
        for name, low, high in [
            ("window", 5, 100),
            ("lookback", 20, 252),
            ("periods_per_year", 1, 366),
        ]:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise ValueError(f"{name} must be an integer from {low} to {high}.")
        if not 0.01 <= self.lower < self.upper <= 0.99:
            raise ValueError("Require 0.01 <= lower < upper <= 0.99.")
        if (
            isinstance(self.cost_bps, bool)
            or not np.isfinite(self.cost_bps)
            or not 0 <= self.cost_bps <= 100
        ):
            raise ValueError("Cost must be between 0 and 100 basis points.")


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate source-format daily close observations without inferring missing
    prices."""
    if not isinstance(frame, pd.DataFrame) or not {"date", "close"} <= set(frame):
        raise ValueError("Provide date and close columns.")
    if not 50 <= len(frame) <= 5000:
        raise ValueError("Provide 50 to 5000 observations.")
    result = frame[["date", "close"]].copy()
    if result["close"].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Prices cannot be boolean values.")
    try:
        result["date"] = pd.to_datetime(
            result["date"], errors="raise", format="ISO8601"
        )
        result["close"] = pd.to_numeric(result["close"], errors="raise")
    except (ValueError, TypeError) as exc:
        raise ValueError("Dates must be parseable; close must be numeric.") from exc
    if (
        result["date"].isna().any()
        or result["date"].duplicated().any()
        or not result["date"].is_monotonic_increasing
    ):
        raise ValueError("Dates must be nonmissing, unique and increasing.")
    if (
        not np.isfinite(result["close"]).all()
        or not result["close"].between(0.000001, 1e9).all()
    ):
        raise ValueError("Prices must be finite and positive, below one billion.")
    if result["date"].dt.tz is not None or not result["date"].equals(
        result["date"].dt.normalize()
    ):
        raise ValueError("Use date-only observations without timezones.")
    return result.reset_index(drop=True)


def summary(returns: object, periods_per_year: int) -> dict[str, float | None]:
    """Include initial capital in drawdown and disclose zero-risk-free Sharpe."""
    values = np.asarray(returns, dtype=float)
    wealth = np.cumprod(1 + values)
    if not np.isfinite(wealth).all() or (wealth <= 0).any():
        raise ValueError("Price changes produce unstable or nonpositive equity.")
    peaks = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
    deviation = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    annualized = float(wealth[-1] ** (periods_per_year / len(values)) - 1)
    if not np.isfinite(annualized):
        raise ValueError("Annualization is unstable for this input range.")
    return {
        "total_return": float(wealth[-1] - 1),
        "annualized_return": annualized,
        "max_drawdown": float(np.min(wealth / peaks - 1)),
        "sharpe_zero_rf": (
            float(values.mean() / deviation * np.sqrt(periods_per_year))
            if deviation > 0
            else None
        ),
    }


def run_backtest(
    frame: pd.DataFrame, config: StrategyConfig | None = None
) -> tuple[pd.DataFrame, dict]:
    """Use past-only thresholds and a full-bar delay before close-to-close exposure."""
    config = config or StrategyConfig()
    config.validate()
    data = validate_prices(frame)
    if len(data) < config.window + config.lookback + 5:
        raise ValueError("Need more history for this SMA and calibration window.")
    close = data["close"]
    data["sma"] = close.rolling(config.window).mean()
    ratio = close / data["sma"]
    data["ratio"] = ratio
    data["lower_threshold"] = (
        ratio.rolling(config.lookback).quantile(config.lower).shift(1)
    )
    data["upper_threshold"] = (
        ratio.rolling(config.lookback).quantile(config.upper).shift(1)
    )
    signal = pd.Series(np.nan, index=data.index)
    signal.loc[ratio < data["lower_threshold"]] = 1.0
    signal.loc[ratio > data["upper_threshold"]] = 0.0
    data["signal"] = signal.ffill().fillna(0)
    # Signal after close t; trade at close t+1; earn return from t+1 to t+2.
    data["position"] = data["signal"].shift(2).fillna(0)
    turnover = data["position"].diff().abs().fillna(data["position"])
    returns = close.pct_change().fillna(0)
    fee = config.cost_bps / 10000
    data["strategy_return"] = (1 - fee * turnover) * (
        1 + data["position"] * returns
    ) - 1
    active = data["lower_threshold"].notna()
    result = data.loc[active].copy()
    result["benchmark_return"] = returns.loc[active]
    first, last = result.index[0], result.index[-1]
    # Benchmark pays an entry and exit fee on the same comparison window.
    result.loc[first, "benchmark_return"] = (1 - fee) * (
        1 + result.loc[first, "benchmark_return"]
    ) - 1
    result.loc[last, "benchmark_return"] = (
        1 + result.loc[last, "benchmark_return"]
    ) * (1 - fee) - 1
    if result.loc[last, "position"]:
        result.loc[last, "strategy_return"] = (
            1 + result.loc[last, "strategy_return"]
        ) * (1 - fee) - 1
    result["strategy_equity"] = (1 + result["strategy_return"]).cumprod()
    result["benchmark_equity"] = (1 + result["benchmark_return"]).cumprod()
    result["strategy_drawdown"] = (
        result["strategy_equity"]
        / np.maximum.accumulate(np.r_[1.0, result["strategy_equity"]])[1:]
        - 1
    )
    report = {
        "parameters": asdict(config),
        "observations": len(data),
        "evaluation_periods": len(result),
        "first_return_date": str(result.iloc[0]["date"].date()),
        "last_date": str(result.iloc[-1]["date"].date()),
        "strategy": summary(result["strategy_return"], config.periods_per_year),
        "benchmark": summary(result["benchmark_return"], config.periods_per_year),
        "position_changes": int(
            turnover.loc[active].sum() + bool(result.loc[last, "position"])
        ),
        "exposure_fraction": float(result["position"].mean()),
        "execution": (
            "signal after close t, trade at close t+1, exposure from " "t+1 to t+2"
        ),
        "cost_bps": config.cost_bps,
        "periods_per_year": config.periods_per_year,
    }
    return result, report


def select_period(
    frame: pd.DataFrame, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Select an optional explicit date range before strategy warm-up."""
    data = validate_prices(frame)
    for value, operator in [(start, "start"), (end, "end")]:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ValueError("Date range values must use YYYY-MM-DD strings.")
        date = pd.to_datetime(value, format="%Y-%m-%d", errors="raise")
        data = (
            data.loc[data["date"] >= date]
            if operator == "start"
            else data.loc[data["date"] <= date]
        )
    return data.reset_index(drop=True)
