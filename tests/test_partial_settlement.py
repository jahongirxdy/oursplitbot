from pathlib import Path
from types import SimpleNamespace

import pytest

from oursplit.db import Database


@pytest.mark.asyncio
async def test_partial_settlement_reduces_only_pair(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()

    p1 = SimpleNamespace(id=1, username="p1", first_name="P1", last_name=None)
    p2 = SimpleNamespace(id=2, username="p2", first_name="P2", last_name=None)
    for u in (p1, p2):
        await db.upsert_user(u)

    household = await db.setup_household(-100123, "Apartment")
    await db.join_household(household.id, 1)
    await db.join_household(household.id, 2)

    token = await db.create_pending_expense(household.id, 1, 100_000, "groceries", [1, 2])
    await db.confirm_pending_expense(token, 1)
    assert await db.get_pair_balance(household.id, 2, 1) == -50_000

    await db.set_pending_partial_settlement(household.id, 2, 1, "inline-123")
    pending = await db.get_pending_partial_settlement(2)
    assert pending == {
        "household_id": household.id,
        "creditor_id": 1,
        "inline_message_id": "inline-123",
    }

    await db.record_settlement(household.id, debtor_id=2, creditor_id=1, amount=20_000)
    assert await db.get_pair_balance(household.id, 2, 1) == -30_000

    await db.clear_pending_partial_settlement(2)
    assert await db.get_pending_partial_settlement(2) is None
