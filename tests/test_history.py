from types import SimpleNamespace

import pytest

from oursplit.db import Database


@pytest.mark.asyncio
async def test_user_history_contains_only_participated_expenses(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.init()

    users = [
        SimpleNamespace(id=1, username="p1", first_name="P1", last_name=None),
        SimpleNamespace(id=2, username="p2", first_name="P2", last_name=None),
        SimpleNamespace(id=3, username="p3", first_name="P3", last_name=None),
    ]
    for user in users:
        await db.upsert_user(user)

    household = await db.setup_household(-100, "Flat")
    for user in users:
        await db.join_household(household.id, user.id)

    token = await db.create_pending_expense(household.id, 1, 90000, "Food", [1, 2])
    await db.confirm_pending_expense(token, 1)

    history_p2 = await db.get_user_expense_history(household.id, 2)
    history_p3 = await db.get_user_expense_history(household.id, 3)

    assert len(history_p2) == 1
    assert history_p2[0]["description"] == "Food"
    assert history_p2[0]["user_share"] == 45000
    assert history_p3 == []
