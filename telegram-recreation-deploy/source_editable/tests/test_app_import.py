"""Regression: importing the web app must not fail due to circular imports."""
import importlib.util
import pytest


@pytest.mark.skipif(importlib.util.find_spec("psycopg") is None,
                    reason="psycopg only installed inside runtime image")
def test_web_app_imports_without_external_credentials():
    from recreation.main import app
    assert app.title == "Telegram Recreation Studio"
    assert any(getattr(route, "path", None) == "/health" for route in app.routes)
