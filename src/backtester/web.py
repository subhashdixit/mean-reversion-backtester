"""Bounded Flask interfaces for an educational, long-or-cash backtest."""

from dataclasses import asdict
import logging
import os
from pathlib import Path

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from backtester.charts import chart
from backtester.core import StrategyConfig, run_backtest, select_period


def parse_config(values: dict) -> StrategyConfig:
    """Parse form or JSON parameters with a fixed allowlist."""
    if not isinstance(values, dict):
        raise ValueError("Parameters must be an object.")
    result = {}
    defaults = asdict(StrategyConfig())
    for key, default in defaults.items():
        value = values.get(key, default)
        try:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if key in ["window", "lookback", "periods_per_year"]:
                if number != int(number):
                    raise ValueError
                number = int(number)
            result[key] = number
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid numeric parameter: {key}.") from exc
    config = StrategyConfig(**result)
    config.validate()
    return config


def create_app(config: dict | None = None) -> Flask:
    """Create a server without account credentials, trading or persistent uploads."""
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=1024 * 1024, DATA_DIR=str(Path.cwd() / "data"))
    app.config.update(config or {})
    logging.basicConfig(level=logging.INFO)
    sample = pd.read_csv(Path(app.config["DATA_DIR"]) / "synthetic-prices.csv")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self' "
            "data:; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def render(frame, parameters, label, error=None, status=200):
        result, report = run_backtest(frame, parameters)
        return (
            render_template(
                "index.html",
                values=asdict(parameters),
                report=report,
                image=chart(result),
                label=label,
                error=error,
            ),
            status,
        )

    @app.get("/")
    def home():
        return render(
            sample, StrategyConfig(), "Synthetic demonstration · seeded simulation"
        )

    @app.post("/backtest")
    def backtest_form():
        try:
            parameters = parse_config(request.form.to_dict())
            uploaded = request.files.get("file")
            if uploaded and uploaded.filename:
                frame = pd.read_csv(uploaded, nrows=5001)
                label = "Your uploaded price series · provenance supplied by you"
            else:
                frame = sample
                label = "Synthetic demonstration · seeded simulation"
            frame = select_period(
                frame, request.form.get("start"), request.form.get("end")
            )
            return render(frame, parameters, label)
        except (ValueError, UnicodeError, pd.errors.ParserError) as exc:
            return render(
                sample, StrategyConfig(), "Synthetic demonstration", str(exc), 400
            )

    @app.post("/api/backtest")
    def api():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("records"), list
        ):
            raise ValueError("Expected a records list with date and close objects.")
        frame = select_period(
            pd.DataFrame(payload["records"]), payload.get("start"), payload.get("end")
        )
        result, report = run_backtest(
            frame, parse_config(payload.get("parameters", {}))
        )
        export = result.copy()
        export["date"] = export["date"].dt.strftime("%Y-%m-%d")
        return jsonify(
            report=report,
            curve=export.where(export.notna(), None).to_dict(orient="records"),
        )

    @app.get("/sample.csv")
    def sample_csv():
        return Response(
            sample.to_csv(index=False),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=synthetic-prices.csv"
            },
        )

    @app.post("/backtest-csv")
    def export_csv():
        uploaded = request.files.get("file")
        frame = (
            pd.read_csv(uploaded, nrows=5001)
            if uploaded and uploaded.filename
            else sample
        )
        frame = select_period(frame, request.form.get("start"), request.form.get("end"))
        result, _ = run_backtest(frame, parse_config(request.form.to_dict()))
        return Response(
            result.to_csv(index=False),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=backtest.csv"},
        )

    @app.get("/health/ready")
    def ready():
        return jsonify(
            status="ready",
            version="1.0.0",
            revision=os.environ.get("RENDER_GIT_COMMIT", "local"),
        )

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.error("Unhandled error", exc_info=error)
        return jsonify(error="Unexpected server error."), 500

    return app
