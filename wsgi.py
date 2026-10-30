"""Gunicorn and local Flask entry point."""

from backtester.web import create_app

app = create_app()
