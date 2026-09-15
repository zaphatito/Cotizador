"""Issue dates are historical evidence, never the time of a retry."""
from __future__ import annotations

import datetime
import math
import re


def issue_datetime(value: object) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value
    raw = str(value or '').strip()
    if re.fullmatch(r'\d+(\.\d+)?', raw):
        number = float(raw)
        if math.isfinite(number) and number > 0:
            try:
                return datetime.datetime.fromtimestamp(
                    number / 1000 if number >= 10_000_000_000 else number,
                    tz=datetime.timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', raw):
        try:
            return datetime.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            pass
    for pattern in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(raw, pattern)
        except ValueError:
            pass
    raise ValueError('La fecha de emisión guardada no es válida; no se sustituye por la fecha actual.')


def same_issue_datetime(left: object, right: object) -> bool:
    if left == right:
        return True
    try:
        first, second = issue_datetime(left), issue_datetime(right)
    except ValueError:
        return False
    # Older local snapshots lack an offset: compare their recorded wall clock.
    if first.tzinfo is None or second.tzinfo is None:
        first, second = first.replace(tzinfo=None), second.replace(tzinfo=None)
    return first == second
