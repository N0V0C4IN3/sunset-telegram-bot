"""Request logs must not carry user data.

httpx logs every request line at INFO, and the Sunsethue and Open-Meteo URLs
carry the subscriber's coordinates as query parameters.
"""

import logging

import pytest

from app.logging_config import configure_logging


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_http_client_request_lines_are_not_logged(name, level, monkeypatch):
    # basicConfig does nothing while the root logger has handlers, and pytest
    # installs its own, so start from a bare root as the app process does.
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    for logger_name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(logger_name), "level", logging.NOTSET)

    configure_logging(level)
    logger = logging.getLogger(name)
    assert not logger.isEnabledFor(logging.INFO)
    assert logger.isEnabledFor(logging.WARNING), "its failures should still surface"
