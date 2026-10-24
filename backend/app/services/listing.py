"""Shared helpers for paginated lists and date-range purges."""

from datetime import datetime, timezone

from app.errors import AppError

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def page_bounds(page: int, page_size: int) -> tuple[int, int, int]:
    """Returns (page, page_size, offset) clamped to sane values. Pages start at 1."""
    size = max(1, min(int(page_size or PAGE_SIZE), MAX_PAGE_SIZE))
    number = max(1, int(page or 1))
    return number, size, (number - 1) * size


def parse_instant(value: str, label: str) -> datetime | None:
    """Parses an ISO timestamp from the browser into naive UTC (how the database stores time)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AppError(400, f"{label} is not a valid date/time.") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def date_range(date_from: str, date_to: str, column: str, required: bool = False) -> tuple[list[str], list]:
    """SQL conditions for `date_from <= column < date_to`. Purges require at least one bound."""
    start = parse_instant(date_from, "Start date")
    end = parse_instant(date_to, "End date")
    if start and end and start >= end:
        raise AppError(400, "The start date must be before the end date.")
    if required and not (start or end):
        raise AppError(400, "Choose a start date, an end date, or both.")
    where, params = [], []
    if start:
        where.append(f"{column} >= ?")
        params.append(start)
    if end:
        where.append(f"{column} < ?")
        params.append(end)
    return where, params
