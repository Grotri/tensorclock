from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Any


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(*, app_name: str, level: str | int = "INFO", logs_root: str = "logs") -> None:
    level_value = getattr(logging, str(level).upper(), logging.INFO) if isinstance(level, str) else int(level)

    root = logging.getLogger()
    root.setLevel(level_value)

    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    console = logging.StreamHandler()
    console.setLevel(level_value)
    console.setFormatter(formatter)
    root.addHandler(console)

    app_dir = Path(logs_root) / app_name
    app_dir.mkdir(parents=True, exist_ok=True)
    date_name = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dated_log = app_dir / f"{date_name}.log"
    latest_log = app_dir / "latest_log"

    f_dated = logging.FileHandler(dated_log, encoding="utf-8", mode="a")
    f_dated.setLevel(level_value)
    f_dated.setFormatter(formatter)
    root.addHandler(f_dated)

    f_latest = logging.FileHandler(latest_log, encoding="utf-8", mode="w")
    f_latest.setLevel(level_value)
    f_latest.setFormatter(formatter)
    root.addHandler(f_latest)


def uvicorn_log_config() -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "loggers": {
            "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
            "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": True},
        },
    }

