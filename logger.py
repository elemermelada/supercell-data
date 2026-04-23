import logging
import sys

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(log_file: str) -> None:
    """Configure root logger with console + file output. Call from main.py."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(_FORMAT)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def setup_console_logging() -> None:
    """Configure root logger for console-only output. Call from __main__ blocks."""
    logging.basicConfig(level=logging.DEBUG, format=_FORMAT, stream=sys.stdout)
