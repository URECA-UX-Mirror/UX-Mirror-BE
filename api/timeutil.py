"""시각은 timezone offset을 포함한 ISO 8601 (계약 3.3)."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="milliseconds")


def now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d%H%M%S")
