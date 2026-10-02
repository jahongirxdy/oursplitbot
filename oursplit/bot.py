from __future__ import annotations

import asyncio
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from aiogram.client.default import DefaultBotProperties

from .config import load_settings
from .db import Database, Member
from .formatting import money
from .parsing import parse_amount, parse_inline_query, parse_settle_command

router = Router()
DB: Database
BOT_USERNAME: str = ""


def member_label(member: Member) -> str:
    return escape(member.name)


def participant_keyboard(token: str, members: list[Member], payer_id: int, selected_ids: list[int]) -> InlineKeyboardMarkup:
    selected = set(selected_ids)
    buttons: list[InlineKeyboardButton] = []
    for member in members:
        if member.telegram_id == payer_id:
            prefix = "💳"
        else:
            prefix = "✅" if member.telegram_id in selected else "⬜"
        label = f"{prefix} {member.name}"
        if len(label) > 28:
            label = label[:27] + "…"
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"expense:toggle:{token}:{member.telegram_id}",
            )
        )

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append(
        [
            InlineKeyboardButton(text="✅ Xarajatni tasdiqlash", callback_data=f"expense:confirm:{token}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"expense:cancel:{token}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pending_expense_text(description: str, payer_name: str, amount: int, participant_count: int) -> str:
    share = amount // participant_count if participant_count else 0
    return (
        f"🧾 <b>{escape(description)}</b>\n"
        f"To‘lagan: <b>{escape(payer_name)}</b>\n"
        f"Summa: <b>{money(amount)}</b>\n"
        f"Tanlangan: <b>{participant_count} kishi</b> (~{money(share)} har biriga)\n\n"
        "Quyidagi ismlarni bosib, ishtirokchilarni qo‘shing yoki chiqaring.\n"
        "💳 = to‘lovchi (har doim kiritiladi)\n"
        "Holat: ⏳ to‘lovchi tasdiqlashi kutilmoqda"
    )


def balance_text(household_title: str, members: list[Member], user_id: int, balances: dict[int, int]) -> str:
    member_map = {m.telegram_id: m for m in members}
    lines = [f"🏠 <b>{escape(household_title)}</b>", "", "<b>Sizning shaxsiy balanslaringiz</b>"]
    for other_id, amount in balances.items():
        other = member_map[other_id]
        sign = "+" if amount > 0 else ""
        lines.append(f"{member_label(other)}: <b>{sign}{money(amount)}</b>")

    total_positive = sum(v for v in balances.values() if v > 0)
    total_negative = -sum(v for v in balances.values() if v < 0)
    net = sum(balances.values())
    net_sign = "+" if net > 0 else ""
    lines += [
        "",
        f"Sizga qarz: <b>{money(total_positive)}</b>",
        f"Sizning qarzingiz: <b>{money(total_negative)}</b>",
        f"Umumiy balans: <b>{net_sign}{money(net)}</b>",
        "",
        "+ — u sizga qarz. − — siz unga qarzsiz.",
    ]
    return "\n".join(lines)


def balance_inline_description(members: list[Member], user_id: int, balances: dict[int, int]) -> str:
    member_map = {m.telegram_id: m for m in members}
    parts: list[str] = []
    for other_id, amount in balances.items():
        if amount == 0:
            continue
        other = member_map.get(other_id)
        if not other:
            continue
        sign = "+" if amount > 0 else "−"
        parts.append(f"{other.name}: {sign}{money(abs(amount)).replace(' UZS', '')}")
    return " • ".join(parts)[:250] if parts else "Hammasi yopilgan — hozir qarz yo‘q"


def settle_picker_keyboard(debtor_id: int, members: list[Member], balances: dict[int, int]) -> InlineKeyboardMarkup:
    member_map = {m.telegram_id: m for m in members}
    buttons: list[InlineKeyboardButton] = []
    for creditor_id, amount in balances.items():
        if amount >= 0:
            continue
        creditor = member_map.get(creditor_id)
        if not creditor:
            continue
        label = f"{creditor.name} • {money(-amount)}"
        if len(label) > 40:
            label = label[:39] + "…"
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"settle:pick:{debtor_id}:{creditor_id}",
            )
        )
    rows = [[button] for button in buttons]
    if not rows:
        rows = [[InlineKeyboardButton(text="✅ To‘lanadigan qarz yo‘q", callback_data=f"settle:none:{debtor_id}")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)



def private_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Balansim", callback_data="profile:balance"),
                InlineKeyboardButton(text="📜 Xarajatlar tarixi", callback_data="profile:history"),
            ]
        ]
    )


def history_text(household_title: str, items: list[dict], user_id: int) -> str:
    lines = [f"🏠 <b>{escape(household_title)}</b>", "", "📜 <b>Oxirgi xarajatlaringiz</b>"]
    if not items:
        lines += ["", "Hali siz qatnashgan xarajat yo‘q."]
        return "\n".join(lines)

    for item in items:
        paid_by_you = item["payer_id"] == user_id
        role = "Siz to‘ladingiz" if paid_by_you else f"{escape(item['payer_name'])} to‘ladi"
        lines += [
            "",
            f"🧾 <b>{escape(item['description'])}</b>",
            f"{role} • {money(item['amount'])}",
            f"Sizning ulushingiz: <b>{money(item['user_share'])}</b>",
            f"🕒 {escape(item['created_at'])}",
        ]
    return "\n".join(lines)

async def household_or_explanation(user_id: int) -> tuple[object | None, str | None]:
    count = await DB.count_households_for_user(user_id)
    if count == 0:
        return None, "Siz hali kvartiraga qo‘shilmagansiz. Guruhda bir marta /setup, keyin /join yuboring."
    if count > 1:
        return None, "Siz bir nechta kvartiraga ulangan ekansiz. Hozircha har bir odam uchun bitta faol kvartira qo‘llanadi."
    return await DB.get_single_household_for_user(user_id), None


@router.message(CommandStart())
async def start(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    start_text = (message.text or "").strip()
    start_payload = start_text.split(maxsplit=1)[1] if len(start_text.split(maxsplit=1)) == 2 else ""

    if start_payload == "settlepart":
        pending = await DB.get_pending_partial_settlement(message.from_user.id)
        if not pending:
            await message.answer(
                "Qisman to‘lov so‘rovi eskirgan. Guruhga qayting va <b>Qarzni to‘lash → Qisman to‘lash</b> ni yana tanlang."
            )
            return

        household = await DB.get_single_household_for_user(message.from_user.id)
        if not household or household.id != pending["household_id"]:
            await DB.clear_pending_partial_settlement(message.from_user.id)
            await message.answer("Bu to‘lovni kvartirangiz bilan bog‘lay olmadim. Guruhdan qaytadan boshlang.")
            return

        current = await DB.get_pair_balance(household.id, message.from_user.id, pending["creditor_id"])
        if current >= 0:
            await DB.clear_pending_partial_settlement(message.from_user.id)
            await message.answer("Bu qarz allaqachon yopilgan.")
            return

        members = await DB.get_members(household.id)
        member_map = {m.telegram_id: m for m in members}
        creditor = member_map.get(pending["creditor_id"])
        creditor_name = member_label(creditor) if creditor else "xonadoshingiz"
        await message.answer(
            f"🤝 <b>Qisman to‘lov</b>\n\n"
            f"Siz <b>{creditor_name}</b>ga <b>{money(-current)}</b> qarzsiz.\n"
            "Qancha to‘ladingiz?\n\n"
            "Faqat summani yuboring, masalan <code>20000</code> yoki <code>20k</code>."
        )
        return

    if start_text.lower().endswith(" balance"):
        household, error = await household_or_explanation(message.from_user.id)
        if error:
            await message.answer(error)
            return
        members = await DB.get_members(household.id)
        balances = await DB.get_user_balances(household.id, message.from_user.id)
        await message.answer(balance_text(household.title, members, message.from_user.id, balances))
        return

    await message.answer(
        "<b>OurSplit</b> xonadoshlar o‘rtasidagi qarzlarni alohida hisoblaydi.\n\n"
        "Kvartira guruhida shunchaki <code>@" + escape(BOT_USERNAME or "yourbot") + "</code>.\n"
        "Darhol quyidagilar chiqadi:\n"
        "➕ Xarajat qo‘shish\n"
        "💰 Balansim\n"
        "🤝 Qarzni to‘lash\n\n"
        "Tezkor xarajat: <code>@" + escape(BOT_USERNAME or "yourbot") + " 100000 oziq-ovqat</code>\n\n"
        "Eski /balance, /settle va /history buyruqlari ham ishlaydi.",
        reply_markup=private_menu_keyboard(),
    )


@router.message(Command("setup"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def setup_group(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    household = await DB.setup_household(message.chat.id, message.chat.title or "Kvartira")
    await DB.join_household(household.id, message.from_user.id)
    await message.answer(
        f"✅ <b>{escape(household.title)}</b> ro‘yxatdan o‘tdi.\n"
        "Sizni ham birinchi a’zo sifatida qo‘shdim.\n\n"
        "Endi har bir xonadosh shu guruhda /join yuborsin."
    )


@router.message(Command("join"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def join_group(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    household = await DB.get_household_by_chat(message.chat.id)
    if not household:
        await message.answer("Avval shu guruhda /setup yuboring.")
        return
    await DB.join_household(household.id, message.from_user.id)
    members = await DB.get_members(household.id)
    await message.answer(f"✅ {escape(message.from_user.full_name)} OurSplit’ga qo‘shildi. A’zolar: {len(members)}")


@router.message(Command("members"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def members_group(message: Message) -> None:
    household = await DB.get_household_by_chat(message.chat.id)
    if not household:
        await message.answer("Bu guruh hali sozlanmagan. /setup yuboring.")
        return
    members = await DB.get_members(household.id)
    lines = [f"<b>{escape(household.title)}</b>"] + [f"• {member_label(m)}" for m in members]
    await message.answer("\n".join(lines))


@router.message(Command("balance"))
async def balance(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    household, error = await household_or_explanation(message.from_user.id)
    if error:
        await message.answer(error)
        return

    members = await DB.get_members(household.id)
    balances = await DB.get_user_balances(household.id, message.from_user.id)
    await message.answer(balance_text(household.title, members, message.from_user.id, balances))


@router.message(Command("history"))
async def history(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    household, error = await household_or_explanation(message.from_user.id)
    if error:
        await message.answer(error)
        return
    items = await DB.get_user_expense_history(household.id, message.from_user.id)
    await message.answer(history_text(household.title, items, message.from_user.id))


@router.callback_query(F.data == "profile:balance")
async def profile_balance(callback: CallbackQuery) -> None:
    household, error = await household_or_explanation(callback.from_user.id)
    if error:
        await callback.answer(error, show_alert=True)
        return
    members = await DB.get_members(household.id)
    balances = await DB.get_user_balances(household.id, callback.from_user.id)
    if callback.message:
        await callback.message.answer(balance_text(household.title, members, callback.from_user.id, balances))
    await callback.answer()


@router.callback_query(F.data == "profile:history")
async def profile_history(callback: CallbackQuery) -> None:
    household, error = await household_or_explanation(callback.from_user.id)
    if error:
        await callback.answer(error, show_alert=True)
        return
    items = await DB.get_user_expense_history(household.id, callback.from_user.id)
    if callback.message:
        await callback.message.answer(history_text(household.title, items, callback.from_user.id))
    await callback.answer()


@router.message(Command("settle"))
async def settle(message: Message) -> None:
    await DB.upsert_user(message.from_user)
    parsed = parse_settle_command(message.text or "")
    if not parsed:
        await message.answer("Foydalanish: <code>/settle @username 10000</code>")
        return

    username, amount = parsed
    household, error = await household_or_explanation(message.from_user.id)
    if error:
        await message.answer(error)
        return

    target = await DB.find_member_by_username(household.id, username)
    if not target:
        await message.answer("Bu username kvartira a’zolari orasidan topilmadi.")
        return
    if target.telegram_id == message.from_user.id:
        await message.answer("O‘zingiz bilan hisob-kitob qila olmaysiz.")
        return

    try:
        await DB.record_settlement(household.id, message.from_user.id, target.telegram_id, amount)
    except ValueError as exc:
        await message.answer(escape(str(exc)))
        return

    remaining = await DB.get_pair_balance(household.id, message.from_user.id, target.telegram_id)
    await message.answer(
        f"✅ {member_label(target)}ga <b>{money(amount)}</b> to‘lov saqlandi.\n"
        f"U bilan balansingiz endi <b>{money(remaining)}</b>."
    )


@router.inline_query()
async def inline_expense(query: InlineQuery) -> None:
    await DB.upsert_user(query.from_user)
    raw = query.query.strip()
    parsed = parse_inline_query(raw)

    household, error = await household_or_explanation(query.from_user.id)
    if error:
        result = InlineQueryResultArticle(
            id="not-ready",
            title="OurSplit hisobingiz hali tayyor emas",
            description=error,
            input_message_content=InputTextMessageContent(message_text=escape(error), parse_mode=ParseMode.HTML),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    members = await DB.get_members(household.id)
    balances = await DB.get_user_balances(household.id, query.from_user.id)

    # Empty query = fast OurSplit menu. The balance is visible right in Telegram's
    # inline picker, so simply typing @oursplitbot is enough to check it.
    if not raw:
        total_positive = sum(v for v in balances.values() if v > 0)
        total_negative = -sum(v for v in balances.values() if v < 0)
        net = sum(balances.values())
        net_sign = "+" if net > 0 else ""

        expense_result = InlineQueryResultArticle(
            id="menu-expense",
            title="➕ Xarajat qo‘shish",
            description="Davom eting: 100000 oziq-ovqat (yoki 100k oziq-ovqat)",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "➕ <b>Xarajat qo‘shish</b>\n\n"
                    f"Yozing: <code>@{escape(BOT_USERNAME or 'oursplitbot')} 100000 oziq-ovqat</code>."
                ),
                parse_mode=ParseMode.HTML,
            ),
        )

        balance_result = InlineQueryResultArticle(
            id="menu-balance",
            title=f"💰 Balansim • Umumiy {net_sign}{money(net)}",
            description=balance_inline_description(members, query.from_user.id, balances),
            input_message_content=InputTextMessageContent(
                message_text=balance_text(household.title, members, query.from_user.id, balances),
                parse_mode=ParseMode.HTML,
            ),
        )

        debts = [(uid, -amount) for uid, amount in balances.items() if amount < 0]
        owed_total = sum(amount for _, amount in debts)
        settle_desc = (
            f"Siz {len(debts)} xonadoshga • jami {money(owed_total)} qarzsiz"
            if debts else "Hozir to‘lanadigan qarz yo‘q"
        )
        settle_result = InlineQueryResultArticle(
            id="menu-settle",
            title="🤝 Qarzni to‘lash",
            description=settle_desc,
            input_message_content=InputTextMessageContent(
                message_text=(
                    "🤝 <b>Qarzni to‘lash</b>\n\n"
                    "Pul bergan xonadoshingizni tanlang.\n"
                    "Faqat bu hisob-kitobni ochgan odam tasdiqlashi mumkin."
                    if debts else "✅ <b>Hamma qarzlar yopilgan.</b>"
                ),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=settle_picker_keyboard(query.from_user.id, members, balances),
        )
        await query.answer([expense_result, balance_result, settle_result], cache_time=0, is_personal=True)
        return

    # Optional text shortcuts keep the menu useful for keyboard-heavy users.
    if raw.lower() in {"balance", "bal"}:
        result = InlineQueryResultArticle(
            id="balance",
            title="💰 Balansim",
            description=balance_inline_description(members, query.from_user.id, balances),
            input_message_content=InputTextMessageContent(
                message_text=balance_text(household.title, members, query.from_user.id, balances),
                parse_mode=ParseMode.HTML,
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    if raw.lower() in {"settle", "pay"}:
        debts = [(uid, -amount) for uid, amount in balances.items() if amount < 0]
        result = InlineQueryResultArticle(
            id="settle",
            title="🤝 Qarzni to‘lash",
            description=(f"Kimga to‘laganingizni tanlang • jami qarz {money(sum(x[1] for x in debts))}" if debts else "To‘lanadigan qarz yo‘q"),
            input_message_content=InputTextMessageContent(
                message_text="🤝 <b>Qarzni to‘lash</b>\n\nPul bergan xonadoshingizni tanlang." if debts else "✅ <b>Hamma qarzlar yopilgan.</b>",
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=settle_picker_keyboard(query.from_user.id, members, balances),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    if not parsed:
        result = InlineQueryResultArticle(
            id="help",
            title="➕ Xarajat qo‘shish",
            description="Summani kiriting, masalan 100000 oziq-ovqat yoki 100k oziq-ovqat",
            input_message_content=InputTextMessageContent(
                message_text=f"<code>@{escape(BOT_USERNAME or 'oursplitbot')} 100000 oziq-ovqat</code> deb yozib umumiy xarajat qo‘shing.",
                parse_mode=ParseMode.HTML,
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    amount, description = parsed
    if len(members) < 2:
        result = InlineQueryResultArticle(
            id="need-members",
            title="Kamida 2 ta kvartira a’zosi kerak",
            description="Xonadoshlaringizdan guruhda /join yuborishni so‘rang.",
            input_message_content=InputTextMessageContent(
                message_text="Xarajatni bo‘lish uchun kamida 2 ta a’zo bo‘lishi kerak."
            ),
        )
        await query.answer([result], cache_time=0, is_personal=True)
        return

    participant_ids = [m.telegram_id for m in members]
    token = await DB.create_pending_expense(
        household.id,
        query.from_user.id,
        amount,
        description,
        participant_ids,
    )
    share = amount // len(members)

    keyboard = participant_keyboard(
        token, members, query.from_user.id, participant_ids
    )
    text = pending_expense_text(
        description, query.from_user.full_name, amount, len(participant_ids)
    )

    result = InlineQueryResultArticle(
        id=token,
        title=f"Qo‘shish: {money(amount)}",
        description=f"{description} • bo‘linadi: {len(members)} kishi",
        input_message_content=InputTextMessageContent(message_text=text, parse_mode=ParseMode.HTML),
        reply_markup=keyboard,
    )
    await query.answer([result], cache_time=0, is_personal=True)


@router.callback_query(F.data.startswith("settle:none:"))
async def settle_none(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3 or int(parts[2]) != callback.from_user.id:
        await callback.answer("Bu hisob-kitob menyusi boshqa xonadoshga tegishli.", show_alert=True)
        return
    await callback.answer("To‘lanadigan qarz yo‘q ✅")


@router.callback_query(F.data.startswith("settle:pick:"))
async def pick_settlement(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Noto‘g‘ri hisob-kitob.", show_alert=True)
        return
    debtor_id = int(parts[2])
    creditor_id = int(parts[3])
    if callback.from_user.id != debtor_id:
        await callback.answer("Bu hisob-kitob menyusi boshqa xonadoshga tegishli.", show_alert=True)
        return

    household, error = await household_or_explanation(debtor_id)
    if error:
        await callback.answer(error, show_alert=True)
        return
    current = await DB.get_pair_balance(household.id, debtor_id, creditor_id)
    if current >= 0:
        await callback.answer("Siz bu odamga endi qarzdor emassiz.", show_alert=True)
        return

    members = await DB.get_members(household.id)
    member_map = {m.telegram_id: m for m in members}
    creditor = member_map.get(creditor_id)
    if not creditor:
        await callback.answer("Xonadosh topilmadi.", show_alert=True)
        return
    owed = -current
    text = (
        "🤝 <b>To‘lovni tasdiqlash</b>\n\n"
        f"Siz hozir <b>{member_label(creditor)}</b>ga <b>{money(owed)}</b> qarzsiz.\n"
        "Faqat pulni haqiqatan berganingizdan keyin quyidagini bosing."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"✅ To‘liq to‘lash {money(owed)}",
                callback_data=f"settle:full:{debtor_id}:{creditor_id}",
            )],
            [InlineKeyboardButton(
                text="💸 Qisman to‘lash",
                callback_data=f"settle:part:{debtor_id}:{creditor_id}",
            )],
        ]
    )
    if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    elif callback.message:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("settle:part:"))
async def begin_partial_settlement(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Noto‘g‘ri hisob-kitob.", show_alert=True)
        return

    debtor_id = int(parts[2])
    creditor_id = int(parts[3])
    if callback.from_user.id != debtor_id:
        await callback.answer("Bu hisob-kitobdan faqat qarzdor foydalana oladi.", show_alert=True)
        return

    household, error = await household_or_explanation(debtor_id)
    if error:
        await callback.answer(error, show_alert=True)
        return

    current = await DB.get_pair_balance(household.id, debtor_id, creditor_id)
    if current >= 0:
        await callback.answer("Bu qarz allaqachon yopilgan.", show_alert=True)
        return

    await DB.set_pending_partial_settlement(
        household.id,
        debtor_id,
        creditor_id,
        callback.inline_message_id,
    )
    deep_link = f"https://t.me/{BOT_USERNAME}?start=settlepart"
    await callback.answer(url=deep_link)


@router.callback_query(F.data.startswith("settle:full:"))
async def confirm_full_settlement(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Noto‘g‘ri hisob-kitob.", show_alert=True)
        return
    debtor_id = int(parts[2])
    creditor_id = int(parts[3])
    if callback.from_user.id != debtor_id:
        await callback.answer("Bu to‘lovni faqat qarzdor tasdiqlashi mumkin.", show_alert=True)
        return

    household, error = await household_or_explanation(debtor_id)
    if error:
        await callback.answer(error, show_alert=True)
        return
    current = await DB.get_pair_balance(household.id, debtor_id, creditor_id)
    if current >= 0:
        await callback.answer("Bu qarz allaqachon yopilgan.", show_alert=True)
        return
    amount = -current
    try:
        await DB.record_settlement(household.id, debtor_id, creditor_id, amount)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    members = await DB.get_members(household.id)
    member_map = {m.telegram_id: m for m in members}
    creditor = member_map.get(creditor_id)
    creditor_name = member_label(creditor) if creditor else "xonadosh"
    text = f"✅ <b>Qarz yopildi</b>\n\n<b>{creditor_name}</b>ga <b>{money(amount)}</b> to‘landi.\nO‘zaro balans endi 0 UZS."
    if callback.inline_message_id:
        await bot.edit_message_text(inline_message_id=callback.inline_message_id, text=text, parse_mode=ParseMode.HTML)
    elif callback.message:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    await callback.answer("To‘lov saqlandi ✅")


@router.message(F.chat.type == ChatType.PRIVATE, F.text)
async def partial_settlement_amount(message: Message, bot: Bot) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return

    pending = await DB.get_pending_partial_settlement(message.from_user.id)
    if not pending:
        return

    amount = parse_amount(text)
    if amount is None:
        await message.answer("Faqat to‘lagan summangizni yuboring, masalan <code>20000</code> yoki <code>20k</code>.")
        return

    household = await DB.get_single_household_for_user(message.from_user.id)
    if not household or household.id != pending["household_id"]:
        await DB.clear_pending_partial_settlement(message.from_user.id)
        await message.answer("Bu to‘lovni kvartirangiz bilan bog‘lay olmadim. Guruhdan qaytadan boshlang.")
        return

    creditor_id = pending["creditor_id"]
    current = await DB.get_pair_balance(household.id, message.from_user.id, creditor_id)
    if current >= 0:
        await DB.clear_pending_partial_settlement(message.from_user.id)
        await message.answer("Bu qarz allaqachon yopilgan.")
        return

    owed = -current
    if amount > owed:
        await message.answer(
            f"Siz hozir faqat <b>{money(owed)}</b> qarzsiz. Shu summagacha bo‘lgan miqdorni kiriting."
        )
        return

    try:
        await DB.record_settlement(household.id, message.from_user.id, creditor_id, amount)
    except ValueError as exc:
        await message.answer(escape(str(exc)))
        return


    await DB.clear_pending_partial_settlement(message.from_user.id)
    remaining_balance = await DB.get_pair_balance(household.id, message.from_user.id, creditor_id)
    remaining = max(0, -remaining_balance)

    members = await DB.get_members(household.id)
    member_map = {m.telegram_id: m for m in members}
    debtor = member_map.get(message.from_user.id)
    creditor = member_map.get(creditor_id)
    debtor_name = member_label(debtor) if debtor else escape(message.from_user.full_name)
    creditor_name = member_label(creditor) if creditor else "xonadosh"

    await message.answer(
        f"✅ <b>To‘lov saqlandi</b>\n\n"
        f"To‘landi: <b>{creditor_name}</b>: <b>{money(amount)}</b>\n"
        f"Qolgan qarz: <b>{money(remaining)}</b>"
    )

    group_text = (
        f"🤝 <b>{'Qarz to‘liq yopildi' if remaining == 0 else 'Qisman to‘lov'}</b>\n\n"
        f"<b>{debtor_name}</b> → <b>{creditor_name}</b>: <b>{money(amount)}</b> to‘ladi\n"
        f"Qolgan qarz: <b>{money(remaining)}</b>"
    )
    try:
        await bot.send_message(household.chat_id, group_text, parse_mode=ParseMode.HTML)
    except Exception:
        # The private payment is still valid even if Telegram temporarily prevents
        # the bot from posting the notification in the apartment group.
        await message.answer("To‘lov saqlandi, lekin guruhga yangilanishni yubora olmadim.")

    inline_message_id = pending.get("inline_message_id")
    if inline_message_id:
        try:
            if remaining == 0:
                await bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=(
                        f"✅ <b>Qarz yopildi</b>\n\n"
                        f"<b>{debtor_name}</b> <b>{creditor_name}</b>ga to‘ladi.\n"
                        "O‘zaro balans endi 0 UZS."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            else:
                updated_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text=f"✅ To‘liq to‘lash {money(remaining)}",
                            callback_data=f"settle:full:{message.from_user.id}:{creditor_id}",
                        )],
                        [InlineKeyboardButton(
                            text="💸 Qisman to‘lash",
                            callback_data=f"settle:part:{message.from_user.id}:{creditor_id}",
                        )],
                    ]
                )
                await bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=(
                        f"🤝 <b>Qarz yangilandi</b>\n\n"
                        f"Hozir to‘landi: <b>{money(amount)}</b>\n"
                        f"Siz hali <b>{creditor_name}</b>ga <b>{money(remaining)}</b> qarzsiz."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=updated_keyboard,
                )
        except Exception:
            pass


@router.callback_query(F.data.startswith("expense:toggle:"))
async def toggle_expense_participant(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Noto‘g‘ri xarajat amali.", show_alert=True)
        return
    token = parts[2]
    try:
        target_id = int(parts[3])
        pending = await DB.toggle_pending_participant(token, callback.from_user.id, target_id)
    except PermissionError:
        await callback.answer("Ishtirokchilarni faqat to‘lovchi tanlay oladi.", show_alert=True)
        return
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not pending:
        await callback.answer("Bu xarajat endi tasdiqlashni kutmayapti.", show_alert=True)
        return

    members = await DB.get_members(pending["household_id"])
    member_map = {m.telegram_id: m for m in members}
    payer = member_map.get(pending["payer_id"])
    payer_name = payer.name if payer else callback.from_user.full_name
    text = pending_expense_text(
        pending["description"], payer_name, pending["amount"], len(pending["participants"])
    )
    keyboard = participant_keyboard(
        token, members, pending["payer_id"], pending["participants"]
    )

    if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    elif callback.message:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer("Ishtirokchilar yangilandi")


@router.callback_query(F.data.startswith("expense:confirm:"))
async def confirm_expense(callback: CallbackQuery, bot: Bot) -> None:
    token = callback.data.rsplit(":", 1)[-1]
    try:
        result = await DB.confirm_pending_expense(token, callback.from_user.id)
    except PermissionError:
        await callback.answer("Xarajatni faqat to‘lov qilgan odam tasdiqlashi mumkin.", show_alert=True)
        return
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not result:
        await callback.answer("Bu xarajat endi mavjud emas.", show_alert=True)
        return
    if result.get("already_confirmed"):
        await callback.answer("Allaqachon qo‘shilgan.")
        return

    household = await DB.get_single_household_for_user(callback.from_user.id)
    members = await DB.get_members(household.id) if household else []
    member_map = {m.telegram_id: m for m in members}
    payer = member_map.get(callback.from_user.id)
    payer_name = payer.name if payer else callback.from_user.full_name

    text = (
        f"✅ <b>{escape(result['description'])}</b>\n"
        f"To‘lagan: <b>{escape(payer_name)}</b>\n"
        f"Summa: <b>{money(result['amount'])}</b>\n"
        f"Bo‘lingan: <b>{len(result['participants'])} kishi</b>\n"
        f"Har bir boshqa xonadosh qarzi: <b>{money(result['base_share'])}</b>"
    )

    if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    elif callback.message:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    await callback.answer("Xarajat qo‘shildi ✅")


@router.callback_query(F.data.startswith("expense:cancel:"))
async def cancel_expense(callback: CallbackQuery, bot: Bot) -> None:
    token = callback.data.rsplit(":", 1)[-1]
    try:
        cancelled = await DB.cancel_pending_expense(token, callback.from_user.id)
    except PermissionError:
        await callback.answer("Faqat to‘lovchi bekor qila oladi.", show_alert=True)
        return

    if not cancelled:
        await callback.answer("Bu xarajat endi tasdiqlashni kutmayapti.")
        return

    text = "❌ <b>Xarajat bekor qilindi</b>"
    if callback.inline_message_id:
        await bot.edit_message_text(
            inline_message_id=callback.inline_message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    elif callback.message:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    await callback.answer("Bekor qilindi")


async def main() -> None:
    global DB, BOT_USERNAME
    settings = load_settings()
    DB = Database(settings.database_path)
    await DB.init()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    BOT_USERNAME = me.username or "oursplitbot"
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
