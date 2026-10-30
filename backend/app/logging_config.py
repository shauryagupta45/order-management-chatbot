from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = record.msg.replace("mongodb+srv://", "mongodb+srv://<redacted>@")
            record.msg = record.msg.replace("mongodb://", "mongodb://<redacted>@")
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(SecretRedactionFilter())
    formatter = logging.Formatter("%(asctime)s IST %(levelname)s %(name)s %(message)s")
    formatter.converter = lambda *_: datetime.now(IST).timetuple()  # log timestamps are always IST
    handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )

