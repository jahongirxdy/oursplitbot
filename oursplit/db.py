from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Household:
    id: int
    chat_id: int
    title: str


@dataclass(frozen=True)
class Member:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None

    @property
    def name(self) -> str:
        full = " ".join(x for x in [self.first_name, self.last_name] if x).strip()
        return full or (f"@{self.username}" if self.username else str(self.telegram_id))


class Database:
    """Small SQLite store.

    Methods are async so handlers can await them, but SQLite access itself is synchronous.
    For a five-person household this keeps the MVP simple and is more than fast enough.
    """

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    async def init(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS households (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS memberships (
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                    active INTEGER NOT NULL DEFAULT 1,
                    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (household_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS pending_expenses (
                    token TEXT PRIMARY KEY,
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    payer_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    description TEXT NOT NULL,
                    participant_ids TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    expense_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    payer_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS expense_shares (
                    expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    amount INTEGER NOT NULL CHECK(amount >= 0),
                    PRIMARY KEY (expense_id, user_id)
                );

                -- For each unordered pair, balance_low is from the lower Telegram ID's perspective.
                -- Positive: user_high owes user_low. Negative: user_low owes user_high.
                CREATE TABLE IF NOT EXISTS pair_balances (
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    user_low INTEGER NOT NULL REFERENCES users(telegram_id),
                    user_high INTEGER NOT NULL REFERENCES users(telegram_id),
                    balance_low INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (household_id, user_low, user_high),
                    CHECK (user_low < user_high)
                );

                CREATE TABLE IF NOT EXISTS settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    debtor_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    creditor_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    amount INTEGER NOT NULL CHECK(amount > 0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS pending_settlements (
                    debtor_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                    household_id INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
                    creditor_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    inline_message_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    async def upsert_user(self, user: Any) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, last_name, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (user.id, user.username, user.first_name, user.last_name),
            )

    async def setup_household(self, chat_id: int, title: str) -> Household:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO households (chat_id, title) VALUES (?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title
                """,
                (chat_id, title),
            )
            row = db.execute("SELECT id, chat_id, title FROM households WHERE chat_id=?", (chat_id,)).fetchone()
            return Household(*row)

    async def get_household_by_chat(self, chat_id: int) -> Household | None:
        with self.connect() as db:
            row = db.execute("SELECT id, chat_id, title FROM households WHERE chat_id=?", (chat_id,)).fetchone()
            return Household(*row) if row else None

    async def join_household(self, household_id: int, user_id: int) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO memberships (household_id, user_id, active)
                VALUES (?, ?, 1)
                ON CONFLICT(household_id, user_id) DO UPDATE SET active=1
                """,
                (household_id, user_id),
            )

    async def get_single_household_for_user(self, user_id: int) -> Household | None:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT h.id, h.chat_id, h.title
                FROM memberships m
                JOIN households h ON h.id=m.household_id
                WHERE m.user_id=? AND m.active=1
                ORDER BY h.id
                """,
                (user_id,),
            ).fetchall()
            if len(rows) != 1:
                return None
            return Household(*rows[0])

    async def count_households_for_user(self, user_id: int) -> int:
        with self.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) FROM memberships WHERE user_id=? AND active=1",
                (user_id,),
            ).fetchone()
            return int(row[0])

    async def get_members(self, household_id: int) -> list[Member]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT u.telegram_id, u.username, u.first_name, u.last_name
                FROM memberships m
                JOIN users u ON u.telegram_id=m.user_id
                WHERE m.household_id=? AND m.active=1
                ORDER BY COALESCE(u.first_name, u.username, CAST(u.telegram_id AS TEXT))
                """,
                (household_id,),
            ).fetchall()
            return [Member(*row) for row in rows]

    async def find_member_by_username(self, household_id: int, username: str) -> Member | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT u.telegram_id, u.username, u.first_name, u.last_name
                FROM memberships m
                JOIN users u ON u.telegram_id=m.user_id
                WHERE m.household_id=? AND m.active=1 AND lower(u.username)=lower(?)
                """,
                (household_id, username),
            ).fetchone()
            return Member(*row) if row else None

    async def create_pending_expense(
        self,
        household_id: int,
        payer_id: int,
        amount: int,
        description: str,
        participant_ids: list[int],
    ) -> str:
        token = secrets.token_urlsafe(9)
        with self.connect() as db:
            db.execute("DELETE FROM pending_expenses WHERE status='pending' AND created_at < datetime('now', '-1 day')")
            db.execute(
                """
                INSERT INTO pending_expenses
                    (token, household_id, payer_id, amount, description, participant_ids)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token, household_id, payer_id, amount, description, json.dumps(participant_ids)),
            )
        return token

    async def get_pending_expense(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT household_id, payer_id, amount, description, participant_ids, status
                FROM pending_expenses WHERE token=?
                """,
                (token,),
            ).fetchone()
        if not row:
            return None
        household_id, payer_id, amount, description, participant_json, status = row
        return {
            "household_id": household_id,
            "payer_id": payer_id,
            "amount": amount,
            "description": description,
            "participants": json.loads(participant_json),
            "status": status,
        }

    async def toggle_pending_participant(
        self, token: str, actor_id: int, target_id: int
    ) -> dict[str, Any] | None:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT household_id, payer_id, amount, description, participant_ids, status
                FROM pending_expenses WHERE token=?
                """,
                (token,),
            ).fetchone()
            if not row:
                db.rollback()
                return None

            household_id, payer_id, amount, description, participant_json, status = row
            if payer_id != actor_id:
                db.rollback()
                raise PermissionError("Ishtirokchilarni faqat to‘lovchi tanlay oladi.")
            if status != "pending":
                db.rollback()
                return None
            if target_id == payer_id:
                db.rollback()
                raise ValueError("To‘lovchi xarajat ishtirokchilari orasida bo‘lishi shart.")

            member = db.execute(
                """
                SELECT 1 FROM memberships
                WHERE household_id=? AND user_id=? AND active=1
                """,
                (household_id, target_id),
            ).fetchone()
            if not member:
                db.rollback()
                raise ValueError("Bu odam kvartiraning faol a’zosi emas.")

            participants = list(dict.fromkeys(json.loads(participant_json)))
            if payer_id not in participants:
                participants.append(payer_id)
            if target_id in participants:
                participants.remove(target_id)
            else:
                participants.append(target_id)

            db.execute(
                "UPDATE pending_expenses SET participant_ids=? WHERE token=?",
                (json.dumps(participants), token),
            )
            db.commit()
            return {
                "household_id": household_id,
                "payer_id": payer_id,
                "amount": amount,
                "description": description,
                "participants": participants,
                "status": status,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _apply_pair_delta(
        db: sqlite3.Connection,
        household_id: int,
        creditor_id: int,
        debtor_id: int,
        amount_delta: int,
    ) -> None:
        if creditor_id == debtor_id or amount_delta == 0:
            return
        low, high = sorted((creditor_id, debtor_id))
        delta_low = amount_delta if creditor_id == low else -amount_delta
        db.execute(
            """
            INSERT INTO pair_balances (household_id, user_low, user_high, balance_low)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(household_id, user_low, user_high)
            DO UPDATE SET balance_low = balance_low + excluded.balance_low
            """,
            (household_id, low, high, delta_low),
        )

    async def confirm_pending_expense(self, token: str, actor_id: int) -> dict[str, Any] | None:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT household_id, payer_id, amount, description, participant_ids, status, expense_id
                FROM pending_expenses WHERE token=?
                """,
                (token,),
            ).fetchone()
            if not row:
                db.rollback()
                return None

            household_id, payer_id, amount, description, participant_json, status, existing_expense_id = row
            if payer_id != actor_id:
                db.rollback()
                raise PermissionError("Xarajatni faqat to‘lovchi tasdiqlay oladi.")
            if status == "confirmed":
                db.rollback()
                return {"already_confirmed": True, "expense_id": existing_expense_id}
            if status != "pending":
                db.rollback()
                return None

            participant_ids: list[int] = json.loads(participant_json)
            if payer_id not in participant_ids or len(participant_ids) < 2:
                db.rollback()
                raise ValueError("To‘lovchidan tashqari kamida bitta xonadoshni tanlang.")

            cur = db.execute(
                "INSERT INTO expenses (household_id, payer_id, amount, description) VALUES (?, ?, ?, ?)",
                (household_id, payer_id, amount, description),
            )
            expense_id = cur.lastrowid

            base = amount // len(participant_ids)
            remainder = amount % len(participant_ids)
            for uid in participant_ids:
                share = base + (remainder if uid == payer_id else 0)
                db.execute(
                    "INSERT INTO expense_shares (expense_id, user_id, amount) VALUES (?, ?, ?)",
                    (expense_id, uid, share),
                )
                if uid != payer_id:
                    self._apply_pair_delta(db, household_id, payer_id, uid, share)

            db.execute(
                "UPDATE pending_expenses SET status='confirmed', expense_id=? WHERE token=?",
                (expense_id, token),
            )
            db.commit()
            return {
                "already_confirmed": False,
                "expense_id": expense_id,
                "household_id": household_id,
                "payer_id": payer_id,
                "amount": amount,
                "description": description,
                "participants": participant_ids,
                "base_share": base,
                "remainder": remainder,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def cancel_pending_expense(self, token: str, actor_id: int) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT payer_id, status FROM pending_expenses WHERE token=?",
                (token,),
            ).fetchone()
            if not row:
                return False
            payer_id, status = row
            if payer_id != actor_id:
                raise PermissionError
            if status != "pending":
                return False
            db.execute("UPDATE pending_expenses SET status='cancelled' WHERE token=?", (token,))
            return True

    async def get_user_balances(self, household_id: int, user_id: int) -> dict[int, int]:
        members = await self.get_members(household_id)
        balances = {m.telegram_id: 0 for m in members if m.telegram_id != user_id}
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT user_low, user_high, balance_low
                FROM pair_balances
                WHERE household_id=? AND (user_low=? OR user_high=?)
                """,
                (household_id, user_id, user_id),
            ).fetchall()
            for low, high, balance_low in rows:
                if user_id == low:
                    balances[high] = balance_low
                else:
                    balances[low] = -balance_low
        return balances

    async def get_pair_balance(self, household_id: int, user_id: int, other_id: int) -> int:
        low, high = sorted((user_id, other_id))
        with self.connect() as db:
            row = db.execute(
                """
                SELECT balance_low FROM pair_balances
                WHERE household_id=? AND user_low=? AND user_high=?
                """,
                (household_id, low, high),
            ).fetchone()
        if not row:
            return 0
        balance_low = int(row[0])
        return balance_low if user_id == low else -balance_low

    async def record_settlement(
        self,
        household_id: int,
        debtor_id: int,
        creditor_id: int,
        amount: int,
    ) -> None:
        if debtor_id == creditor_id:
            raise ValueError("O‘zingiz bilan qarz hisob-kitobini qila olmaysiz.")
        current = await self.get_pair_balance(household_id, debtor_id, creditor_id)
        if current >= 0:
            raise ValueError("Siz hozir bu odamga qarzdor emassiz.")
        owed = -current
        if amount > owed:
            raise ValueError(f"To‘lov joriy qarzdan katta ({owed}).")

        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO settlements (household_id, debtor_id, creditor_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                (household_id, debtor_id, creditor_id, amount),
            )
            self._apply_pair_delta(db, household_id, creditor_id, debtor_id, -amount)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def get_user_expense_history(
        self, household_id: int, user_id: int, limit: int = 15
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT
                    e.id, e.description, e.amount, e.payer_id,
                    COALESCE(u.first_name || CASE WHEN u.last_name IS NOT NULL AND u.last_name != '' THEN ' ' || u.last_name ELSE '' END,
                             CASE WHEN u.username IS NOT NULL THEN '@' || u.username ELSE CAST(e.payer_id AS TEXT) END) AS payer_name,
                    es.amount AS user_share, e.created_at
                FROM expenses e
                JOIN expense_shares es ON es.expense_id=e.id AND es.user_id=?
                JOIN users u ON u.telegram_id=e.payer_id
                WHERE e.household_id=?
                ORDER BY e.id DESC
                LIMIT ?
                """,
                (user_id, household_id, limit),
            ).fetchall()
        return [
            {
                "id": int(r[0]),
                "description": r[1],
                "amount": int(r[2]),
                "payer_id": int(r[3]),
                "payer_name": r[4],
                "user_share": int(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]

    async def set_pending_partial_settlement(
        self,
        household_id: int,
        debtor_id: int,
        creditor_id: int,
        inline_message_id: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM pending_settlements WHERE created_at < datetime('now', '-1 day')"
            )
            db.execute(
                """
                INSERT INTO pending_settlements
                    (debtor_id, household_id, creditor_id, inline_message_id, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(debtor_id) DO UPDATE SET
                    household_id=excluded.household_id,
                    creditor_id=excluded.creditor_id,
                    inline_message_id=excluded.inline_message_id,
                    created_at=CURRENT_TIMESTAMP
                """,
                (debtor_id, household_id, creditor_id, inline_message_id),
            )

    async def get_pending_partial_settlement(self, debtor_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT household_id, creditor_id, inline_message_id
                FROM pending_settlements
                WHERE debtor_id=? AND created_at >= datetime('now', '-1 day')
                """,
                (debtor_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "household_id": int(row[0]),
            "creditor_id": int(row[1]),
            "inline_message_id": row[2],
        }

    async def clear_pending_partial_settlement(self, debtor_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM pending_settlements WHERE debtor_id=?",
                (debtor_id,),
            )

