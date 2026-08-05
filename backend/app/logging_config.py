from __future__ import annotations

import logging
import sys


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = record.msg.replace("mongodb+srv://", "mongodb+srv://<redacted>@")
            record.msg = record.msg.replace("mongodb://", "mongodb://<redacted>@")
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(SecretRedactionFilter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )

