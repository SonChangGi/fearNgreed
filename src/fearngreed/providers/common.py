from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class ProviderError(RuntimeError):
    """A deliberately sanitized external-provider failure."""


def parse_number(value: Any, *, field: str) -> float:
    if value is None:
        raise ProviderError(f"provider response is missing {field}")
    text = str(value).replace(",", "").strip()
    if not text:
        raise ProviderError(f"provider response is missing {field}")
    try:
        return float(Decimal(text))
    except InvalidOperation:
        raise ProviderError(f"provider response has invalid {field}") from None


def first_present(row: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        if field in row and row[field] not in {None, ""}:
            return row[field]
    raise ProviderError(f"provider response is missing {fields[0]}")
