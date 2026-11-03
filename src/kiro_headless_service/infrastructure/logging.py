import json
import logging
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage()
            if record.name.startswith("kiro_headless_service")
            else record.name,
        }
        for name in (
            "job_id",
            "session_id",
            "consumer_id",
            "correlation_id",
            "state",
            "error_code",
            "reservation_accepted",
            "event_id",
        ):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("uvicorn.access").disabled = True
