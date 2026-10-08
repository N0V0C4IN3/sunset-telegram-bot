import logging

# Request lines carry the subscriber's coordinates as query parameters, so the
# HTTP client logs only its warnings, whatever LOG_LEVEL says.
_QUIET_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
