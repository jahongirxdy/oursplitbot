from pathlib import Path
from types import SimpleNamespace

import pytest

from oursplit.db import Database


@pytest.mark.asyncio
async def test_pairwise_example(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()

    users = [SimpleNamespace(id=i, username=f"p{i}", first_name=f"P{i}", last_name=None) for i in range(1, 6)]
    for u in users:
        await db.upsert_user(u)

    household = await db.setup_household(-100123, "Apartment")
    for u in users:
        await db.join_household(household.id, u.id)

    token1 = await db.create_pending_expense(household.id, 1, 100_000, "groceries", [1, 2, 3, 4, 5])
    await db.confirm_pending_expense(token1, 1)

    p1 = await db.get_user_balances(household.id, 1)
    p2 = await db.get_user_balances(household.id, 2)
    assert p1 == {2: 20_000, 3: 20_000, 4: 20_000, 5: 20_000}
    assert p2[1] == -20_000
    assert p2[3] == 0
    assert p2[4] == 0
    assert p2[5] == 0

    token2 = await db.create_pending_expense(household.id, 2, 50_000, "utilities", [1, 2, 3, 4, 5])
    await db.confirm_pending_expense(token2, 2)

    p1 = await db.get_user_balances(household.id, 1)
    p2 = await db.get_user_balances(household.id, 2)

    assert p1 == {2: 10_000, 3: 20_000, 4: 20_000, 5: 20_000}
    assert p2 == {1: -10_000, 3: 10_000, 4: 10_000, 5: 10_000}

    await db.record_settlement(household.id, debtor_id=2, creditor_id=1, amount=10_000)
    assert await db.get_pair_balance(household.id, 2, 1) == 0
