from __future__ import annotations

import re

_AMOUNT_RE = re.compile(r"^(?P<num>\d+(?:[.,]\d+)?)\s*(?P<suffix>[kKmM]?)$")


def parse_amount(raw: str) -> int | None:
    cleaned = raw.strip().replace("_", "")
    match = _AMOUNT_RE.fullmatch(cleaned)
    if not match:
        return None

    number = float(match.group("num").replace(",", "."))
    suffix = match.group("suffix").lower()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[suffix]
    amount = int(round(number * multiplier))
    return amount if amount > 0 else None


def parse_inline_query(query: str) -> tuple[int, str] | None:
    parts = query.strip().split(maxsplit=1)
    if not parts:
        return None
    amount = parse_amount(parts[0])
    if amount is None:
        return None
    description = parts[1].strip() if len(parts) > 1 else "Shared expense"
    return amount, description[:120]


def parse_settle_command(text: str) -> tuple[str, int] | None:
    # /settle @username 10000
    parts = text.strip().split()
    if len(parts) != 3 or not parts[1].startswith("@"):
        return None
    amount = parse_amount(parts[2])
    if amount is None:
        return None
    return parts[1][1:].lower(), amount
