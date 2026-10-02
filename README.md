# OurSplit Telegram Bot

OurSplit — 5 kishilik kvartira uchun Telegram xarajat va qarz botidir. Qarzlar **faqat ikki odam o‘rtasida** hisoblanadi; qarzlar uchinchi odam orqali avtomatik yo‘naltirilmaydi.

## Asosiy imkoniyatlar

- Bir Telegram guruh = bir kvartira.
- `/setup` — guruhni ro‘yxatdan o‘tkazadi.
- `/join` — xonadoshni kvartiraga qo‘shadi.
- Inline xarajat: `@botusername 100000 oziq-ovqat` yoki `@botusername 100k oziq-ovqat`.
- Xarajat tasdiqlanishidan oldin qatnashchilarni qo‘shish/chiqarish mumkin.
- `@botusername` yozilganda tezkor menyu chiqadi: Xarajat, Balans, Qarzni to‘lash.
- To‘liq yoki qisman qarz to‘lash mumkin.
- `/history` — foydalanuvchi qatnashgan oxirgi 15 ta xarajatni ko‘rsatadi.
- Botning private chatida `/start` bosilganda **Balansim** va **Xarajatlar tarixi** tugmalari chiqadi.
- SQLite bazasi ishlatiladi.

## Qarz hisoblash qoidasi

Agar P1 P2 uchun 20 000 so‘m to‘lagan bo‘lsa, P1 `P2: +20 000`, P2 esa `P1: -20 000` ko‘radi.

Agar keyin P2 P1 uchun 10 000 so‘m to‘lasa, shu ikki odamning o‘zaro balansi `20 000 - 10 000 = 10 000` bo‘ladi. P3/P4/P5 orqali qayta yo‘naltirish yo‘q.

## Ishga tushirish

Python 3.10+ kerak.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

`.env` ichida BotFather tokenini kiriting:

```env
BOT_TOKEN=YOUR_TOKEN_HERE
DATABASE_PATH=data/oursplit.db
```

BotFather’da inline mode yoqilgan bo‘lishi kerak: `/setinline`.

## Guruhni sozlash

1. Botni kvartira guruhiga qo‘shing.
2. Bitta odam `/setup` yuborsin.
3. Har bir xonadosh `/join` yuborsin.
4. `/members` bilan tekshirish mumkin.

## Xarajat qo‘shish

Guruhda:

```text
@botusername 100000 oziq-ovqat
```

Natijani tanlang, kerak bo‘lmagan xonadoshlarni chiqarib tashlang va **Xarajatni tasdiqlash** tugmasini bosing.

## Balans

Tezkor usul: guruhda `@botusername` yozing va **Balansim** ni tanlang.

Backup usul:

```text
/balance
```

`+` — u sizga qarz. `−` — siz unga qarzsiz.

## Qarzni to‘lash

Guruhda `@botusername` → **Qarzni to‘lash** → xonadoshni tanlang.

- **To‘liq to‘lash** — butun qarzni yopadi.
- **Qisman to‘lash** — bot private chatini ochadi; masalan `20000` yoki `20k` yuborasiz. Bot qarzni kamaytiradi va guruhga xabar yuboradi.

Backup buyruq:

```text
/settle @username 10000
```

## Xarajatlar tarixi

Private chatda botga `/start` yuboring va **📜 Xarajatlar tarixi** tugmasini bosing.

Yoki:

```text
/history
```

Bot foydalanuvchi qatnashgan oxirgi 15 ta xarajatni ko‘rsatadi: xarajat nomi, kim to‘lagani, umumiy summa, sizning ulushingiz va vaqt.

## Database

Default fayl:

```text
data/oursplit.db
```

Muhim jadvallar:

- `expenses` — tasdiqlangan xarajatlar.
- `expense_shares` — har bir xarajatdagi qatnashchilar va ulushlar.
- `pair_balances` — hozirgi ikki kishilik qarz holati.
- `settlements` — qaytarilgan pullar.
- `users`, `households`, `memberships` — foydalanuvchi va kvartira ma’lumotlari.

## GitHub private repo uchun

Repo ichiga quyidagilarni saqlash kerak:

```text
oursplit/
  __init__.py
  bot.py
  config.py
  db.py
  formatting.py
  parsing.py
tests/
run.py
requirements.txt
README.md
.env.example
.gitignore
```

**Repo'ga qo‘ymang:**

- `.env` — BotFather tokeni bor.
- `data/oursplit.db` — real xarajatlar va Telegram ID’lari bor.
- `.venv/`, `__pycache__/`, `.pytest_cache/`.

`.gitignore` bularni avtomatik ignore qiladi.

## Testlar

```bash
PYTHONPATH=. python -m pytest -q
```

Hozirgi versiyada 6 ta test bor: parsing, pairwise debt, qatnashchi tanlash, qisman to‘lov va history.
