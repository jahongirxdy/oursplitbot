from __future__ import annotations

from html import escape


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " UZS"


def display_name(first_name: str | None, last_name: str | None, username: str | None) -> str:
    full = " ".join(part for part in [first_name, last_name] if part).strip()
    if full:
        return full
    if username:
        return f"@{username}"
    return "Unknown user"


def h(value: str) -> str:
    return escape(value, quote=False)
