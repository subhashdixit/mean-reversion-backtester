"""Verify real HTTP backtesting, not just a running server."""

import argparse
import csv
import io
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def check(base, attempts, expected_revision=None):
    """Verify readiness and a sample backtest; allow bounded cold-start retries."""
    for attempt in range(attempts):
        try:
            with urlopen(base + "/health/ready", timeout=20) as response:
                ready = json.load(response)
            if (
                ready["status"] != "ready"
                or expected_revision
                and ready["revision"] != expected_revision
            ):
                raise ValueError("Unexpected readiness/revision")
            with urlopen(base + "/sample.csv", timeout=20) as response:
                records = list(csv.DictReader(io.StringIO(response.read().decode())))
            request = Request(
                base + "/api/backtest",
                data=json.dumps({"records": records}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
            if result["report"]["evaluation_periods"] < 1 or not result["curve"]:
                raise ValueError("No backtest output")
            print("Readiness and real HTTP backtest passed:", base)
            return
        except (URLError, TimeoutError, ValueError) as exc:
            if attempt + 1 == attempts:
                raise SystemExit(str(exc)) from exc
            time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--expected-revision")
    args = parser.parse_args()
    check(args.url.rstrip("/"), args.attempts, args.expected_revision)
