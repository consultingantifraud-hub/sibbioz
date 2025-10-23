from __future__ import annotations

import logging
from typing import Optional


def setup_logging(level: int = logging.INFO) -> None:
    """Configure basic logging for the CLI.

    The configuration is idempotent – calling it multiple times keeps the
    existing handlers intact. The formatter includes the log level and the
    message which is sufficient for command line usage.
    """

    if logging.getLogger().handlers:
        logging.getLogger().setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a module level logger."""

    return logging.getLogger(name if name else __name__)
