"""Causality, cost accounting, risk arithmetic and real HTTP backtest tests."""

import io
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from backtester.core import (
    StrategyConfig,
    run_backtest,
    select_period,
    summary,
    validate_prices,
)
from backtester.web import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="prices")
def fixture_prices():
    """Use the explicitly synthetic, deterministic fixture."""
    return pd.read_csv(ROOT / "data/synthetic-prices.csv")


@pytest.fixture(name="client")
def fixture_client():
    """Create an isolated Flask client."""
    return create_app({"TESTING": True, "DATA_DIR": ROOT / "data"}).test_client()


@pytest.mark.parametrize(
    "parameters",
    [
        {"window": 1},
        {"window": True},
        {"lookback": 999},
        {"lower": 0.9, "upper": 0.1},
        {"cost_bps": -1},
        {"cost_bps": float("nan")},
        {"periods_per_year": 0},
    ],
)
def test_invalid_config(parameters):
    """Reject invalid assumptions instead of silently coercing them."""
    with pytest.raises(ValueError):
        StrategyConfig(**parameters).validate()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "short",
        "long",
        "negative",
        "zero",
        "infinite",
        "duplicate",
        "unordered",
        "bad_date",
        "boolean",
    ],
)
def test_invalid_prices(prices, case):
    """Reject malformed price sequences before computing returns."""
    if case == "missing":
        prices = prices.drop(columns="close")
    elif case == "short":
        prices = prices.head(10)
    elif case == "long":
        prices = pd.concat([prices] * 8)
    elif case == "negative":
        prices.loc[0, "close"] = -1
    elif case == "zero":
        prices.loc[0, "close"] = 0
    elif case == "infinite":
        prices.loc[0, "close"] = np.inf
    elif case == "duplicate":
        prices.loc[1, "date"] = prices.loc[0, "date"]
    elif case == "unordered":
        prices = prices.iloc[::-1]
    elif case == "bad_date":
        prices.loc[0, "date"] = "not-a-date"
    else:
        prices["close"] = True
    with pytest.raises(ValueError):
        validate_prices(prices)


def test_no_future_lookahead(prices):
    """Changing future prices cannot change earlier thresholds or signals."""
    original, _ = run_backtest(prices)
    changed = prices.copy()
    changed.loc[500:, "close"] *= 2
    modified, _ = run_backtest(changed)
    before = original[original.index < 500]
    after = modified[modified.index < 500]
    pd.testing.assert_frame_equal(
        before[["lower_threshold", "upper_threshold", "signal", "position"]],
        after[["lower_threshold", "upper_threshold", "signal", "position"]],
    )


def test_full_bar_execution_delay(prices):
    """Today's exposure uses the signal from two observations earlier."""
    curve, _ = run_backtest(prices)
    comparison = curve.iloc[2:]
    assert (
        comparison["position"].to_numpy().tolist()
        == curve["signal"].iloc[:-2].to_numpy().tolist()
    )


def test_costs_reduce_terminal_wealth(prices):
    """With unchanged signals, higher costs cannot improve terminal equity."""
    _, free = run_backtest(prices, StrategyConfig(cost_bps=0))
    _, costly = run_backtest(prices, StrategyConfig(cost_bps=50))
    assert costly["strategy"]["total_return"] <= free["strategy"]["total_return"]
    assert costly["benchmark"]["total_return"] < free["benchmark"]["total_return"]


def test_initial_capital_in_drawdown():
    """A first-period loss must count relative to initial wealth 1."""
    result = summary([-0.1, 0.05], 252)
    assert result["max_drawdown"] == pytest.approx(-0.1)
    assert result["total_return"] == pytest.approx(0.9 * 1.05 - 1)
    assert summary([0.0, 0.0], 252)["sharpe_zero_rf"] is None


def test_selected_period(prices):
    """Date filtering happens before warm-up and rejects malformed dates."""
    subset = select_period(prices, "2021-01-01", "2021-12-31")
    assert str(subset.iloc[0]["date"].date()) == "2021-01-01"
    assert str(subset.iloc[-1]["date"].date()) == "2021-12-31"
    with pytest.raises(ValueError):
        select_period(prices, "bad")


def test_html_and_json(client, prices):
    """Exercise an actual backtest and PNG chart through Flask."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Synthetic demonstration" in response.data
    assert b"data:image/png;base64," in response.data
    response = client.post(
        "/api/backtest", json={"records": prices.to_dict(orient="records")}
    )
    assert response.status_code == 200
    expected, report = run_backtest(prices)
    assert response.json["report"] == report
    assert len(response.json["curve"]) == len(expected)


def test_csv_and_form(client):
    """Upload the sample and download an actual curve without persisting input."""
    sample = client.get("/sample.csv")
    response = client.post(
        "/backtest", data={"file": (io.BytesIO(sample.data), "prices.csv")}
    )
    assert (
        response.status_code == 200 and b"Your uploaded price series" in response.data
    )
    response = client.post(
        "/backtest-csv", data={"file": (io.BytesIO(sample.data), "prices.csv")}
    )
    assert response.status_code == 200 and b"strategy_equity" in response.data
    assert "attachment" in response.headers["Content-Disposition"]


def test_errors_and_limits(client):
    """Malformed requests are client errors; never a successful empty backtest."""
    assert client.post("/api/backtest", json={}).status_code == 400
    assert client.post("/api/backtest", json={"records": []}).status_code == 400
    assert client.post("/backtest", data={"window": "bad"}).status_code == 400
    assert (
        client.post(
            "/api/backtest",
            data=b"x" * (1024 * 1024 + 1),
            content_type="application/json",
        ).status_code
        == 413
    )
    assert client.get("/missing").status_code == 404
    ready = client.get("/health/ready")
    assert ready.json["status"] == "ready"
    assert ready.headers["X-Content-Type-Options"] == "nosniff"
