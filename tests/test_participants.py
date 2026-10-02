from pathlib import Path
from types import SimpleNamespace

import pytest

from oursplit.db import Database


@pytest.mark.asyncio
async def test_excluded_roommate_gets_no_share(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()

    users = [
        SimpleNamespace(id=101, username="p1", first_name="P1", last_name=None),
        SimpleNamespace(id=102, username="p2", first_name="P2", last_name=None),
        SimpleNamespace(id=103, username="p3", first_name="P3", last_name=None),
        SimpleNamespace(id=104, username="p4", first_name="P4", last_name=None),
        SimpleNamespace(id=105, username="p5", first_name="P5", last_name=None),
    ]
    for user in users:
        await db.upsert_user(user)

    household = await db.setup_household(-1001, "Apartment")
    for user in users:
        await db.join_household(household.id, user.id)

    token = await db.create_pending_expense(
        household.id,
        payer_id=101,
        amount=100_000,
        description="groceries",
        participant_ids=[101, 102, 103, 104, 105],
    )

    pending = await db.toggle_pending_participant(token, actor_id=101, target_id=104)
    assert 104 not in pending["participants"]

    result = await db.confirm_pending_expense(token, actor_id=101)
    assert result["participants"] == [101, 102, 103, 105]
    assert result["base_share"] == 25_000

    assert await db.get_pair_balance(household.id, 101, 102) == 25_000
    assert await db.get_pair_balance(household.id, 101, 103) == 25_000
    assert await db.get_pair_balance(household.id, 101, 104) == 0
    assert await db.get_pair_balance(household.id, 101, 105) == 25_000
