"""
Nexsu Anti-Scam Telegram bot (aiogram 3.x)

Запуск:
    pip install -r requirements.txt
    cp .env.example .env   # заполнить
    python bot.py
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from html import escape

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ───────────────────────── Настройки ─────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MODERATOR_IDS = {int(x) for x in os.getenv("MODERATOR_IDS", "").replace(" ", "").split(",") if x}
MOD_CHAT_ID = int(os.getenv("MOD_CHAT_ID", "0") or 0)  # чат модераторов (необязательно)
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "NexsuBase")  # без @
CHANNEL_ID = os.getenv("CHANNEL_ID", f"@{CHANNEL_USERNAME}")  # куда бот публикует скамеров
DB_PATH = os.getenv("DB_PATH", "nexsu.db")
BOT_USERNAME = ""  # заполняется при старте

# ───────────────────────── Кнопки ─────────────────────────
BTN_COMPLAINT = "📝 Подать жалобу"
BTN_ADD_GROUP = "➕ Добавить в группу"
BTN_PROJECTS = "📂 Наши проекты"
BTN_GUARANTORS = "🛡 Гаранты"
BTN_ACCOUNT = "👤 Мой аккаунт"
BTN_CHECK = "🔎 Проверить Аккаунт"
BTN_HOME = "🏠Главное Меню"
BTN_CANCEL = "❌ Отмена"

# ───────────────────────── Тексты ─────────────────────────
WELCOME = (
    "👋 <b>Добро пожаловать в Nexsu Anti-Scam!</b>\n\n"
    "Здесь вы можете подать жалобу на мошенника, проверить продавца "
    "и найти надёжного гаранта для сделки.\n\n"
    "Выберите действие в меню ниже 👇"
)
STEP1 = (
    "👊 <b>Подача жалобы: Шаг 1 из 3</b>\n\n"
    "Отправьте боту юзернейм, ID или ссылку на мошенника.\n"
    "⚠️ Убедитесь, что он указан верно — это необходимо для точной идентификации.\n\n"
    "Форматы: <code>@username</code>, <code>123456789</code>, "
    "<code>https://t.me/username</code>, <code>t.me/username</code>"
)
STEP2 = (
    "👊 <b>Подача жалобы: Шаг 2 из 3</b>\n\n"
    "Опишите ситуацию.\n"
    "Кратко изложите суть мошенничества. Укажите, что именно пошло не так, "
    "сумму и ключевые детали."
)
STEP3 = (
    "👊 <b>Подача жалобы: Шаг 3 из 3</b>\n\n"
    "Отправьте до 10 фото или видео в качестве доказательств.\n"
    "Можно отправить несколько файлов сразу или по одному.\n"
    "Когда закончите, нажмите кнопку «Далее»."
)
PROJECTS = (
    "📂 <b>Наши проекты</b>\n\n"
    "База Скамеров — @NexsuBase\n"
    "Подать жалобу — @NexsuChat"
)
GUARANTORS = (
    "🛡 <b>Вы можете использовать данных гарантов:</b>\n"
    "1. @Xertnos\n"
    "2. @ecanok\n\n"
    "Хочешь быть гарантом? Тогда покупай поручительство у владельца: @xertnos\n"
    "Уточнить поручительство у владельца или @NexsuRuch"
)
CHECK_PROMPT = (
    "🔎 Хочешь посмотреть профиль подозрительного продавца?\n"
    "Тогда води его Юзер/Айди."
)
ERR_UNKNOWN = (
    "❌ <b>Ошибка</b>\n"
    "Неизвестная команда или действие. Воспользуйтесь кнопками меню ниже."
)
ERR_FORMAT = (
    "❌ <b>Ошибка</b>\n"
    "Неверный формат. Отправьте <code>@username</code>, ID или ссылку <code>t.me/username</code>."
)

# ───────────────────────── База данных ─────────────────────────
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    first_seen TEXT
);
CREATE TABLE IF NOT EXISTS scammers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    reason TEXT,
    proof_link TEXT,
    added_by INTEGER,
    added_at TEXT,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS complaints(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER,
    target_raw TEXT,
    target_id INTEGER,
    target_username TEXT,
    description TEXT,
    media TEXT,
    status TEXT DEFAULT 'pending',
    moderator_id INTEGER,
    created_at TEXT
);
"""
)
db.commit()


def now():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def q_all(sql, p=()):
    return db.execute(sql, p).fetchall()


def q_one(sql, p=()):
    return db.execute(sql, p).fetchone()


def q_exec(sql, p=()):
    cur = db.execute(sql, p)
    db.commit()
    return cur


def is_mod(user_id: int) -> bool:
    return user_id in MODERATOR_IDS


# ───────────────────────── Утилиты ─────────────────────────
def parse_target(raw: str, bare: bool = False):
    """Возвращает (user_id, username) или None."""
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{5,15}", raw):
        return int(raw), None
    m = re.fullmatch(r"(?:https?://)?(?:www\.)?t\.me/@?([A-Za-z0-9_]{4,32})/?(?:\?.*)?", raw, re.I)
    if m:
        return None, m.group(1).lower()
    m = re.fullmatch(r"@([A-Za-z0-9_]{4,32})", raw)
    if m:
        return None, m.group(1).lower()
    if bare:
        m = re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", raw)
        if m:
            return None, raw.lower()
    return None


def upsert_user(user_id, username, first_name):
    username = username.lower() if username else None
    if username:
        q_exec("UPDATE users SET username=NULL WHERE username=? AND user_id!=?", (username, user_id))
    q_exec(
        "INSERT INTO users(user_id, username, first_name, first_seen) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
        (user_id, username, first_name, now()),
    )


async def resolve(bot: Bot, uid, uname):
    """Пытается дополнить пару (id, username)."""
    if uid and not uname:
        row = q_one("SELECT username FROM users WHERE user_id=?", (uid,))
        if row and row["username"]:
            uname = row["username"]
    elif uname and not uid:
        row = q_one("SELECT user_id FROM users WHERE username=?", (uname,))
        if row:
            uid = row["user_id"]
        else:
            try:
                chat = await bot.get_chat("@" + uname)
                uid = chat.id
            except Exception:
                pass
    return uid, uname


def cond(uid, uname, col_id="user_id", col_un="username"):
    parts, params = [], []
    if uid:
        parts.append(f"{col_id}=?")
        params.append(uid)
    if uname:
        parts.append(f"{col_un}=?")
        params.append(uname)
    return (" OR ".join(parts) or "0"), params


def find_scam(uid, uname):
    c, p = cond(uid, uname)
    return q_one(f"SELECT * FROM scammers WHERE active=1 AND ({c}) ORDER BY id DESC LIMIT 1", p)


def add_scammer(uid, uname, reason, link, by):
    """True — добавлен новый, False — уже был (данные дополнены)."""
    old = find_scam(uid, uname)
    if old:
        q_exec(
            "UPDATE scammers SET user_id=COALESCE(user_id,?), username=COALESCE(username,?), "
            "proof_link=COALESCE(proof_link,?) WHERE id=?",
            (uid, uname, link, old["id"]),
        )
        return False
    q_exec(
        "INSERT INTO scammers(user_id, username, reason, proof_link, added_by, added_at) VALUES(?,?,?,?,?,?)",
        (uid, uname, reason, link, by, now()),
    )
    return True


def remove_scammer(uid, uname):
    c, p = cond(uid, uname)
    return q_exec(f"UPDATE scammers SET active=0 WHERE active=1 AND ({c})", p).rowcount


def complaint_stats(uid, uname):
    c, p = cond(uid, uname, "target_id", "target_username")
    against = q_one(
        f"SELECT COUNT(*) t, SUM(status='accepted') a, SUM(status='pending') w FROM complaints WHERE {c}", p
    )
    filed = (
        q_one(
            "SELECT COUNT(*) t, SUM(status='accepted') a FROM complaints WHERE reporter_id=?",
            (uid,),
        )
        if uid
        else None
    )
    return against, filed


async def build_profile(bot: Bot, uid, uname, title="👤 <b>Профиль</b>", advice=False):
    uid, uname = await resolve(bot, uid, uname)
    user = None
    if uid:
        user = q_one("SELECT * FROM users WHERE user_id=?", (uid,))
    elif uname:
        user = q_one("SELECT * FROM users WHERE username=?", (uname,))
    scam = find_scam(uid, uname)
    against, filed = complaint_stats(uid, uname)

    lines = [title, ""]
    if user and user["first_name"]:
        lines.append(f"Имя: {escape(user['first_name'])}")
    lines.append(f"Юзернейм: {'@' + escape(uname) if uname else '—'}")
    lines.append(f"ID: <code>{uid}</code>" if uid else "ID: не определён")
    lines.append("")
    if scam:
        lines.append("🚨 <b>СТАТУС: СКАМЕР (находится в анти-скаме)</b>")
        if scam["reason"]:
            lines.append(f"Причина: {escape(scam['reason'][:500])}")
        if scam["proof_link"]:
            lines.append(f"Доказательства: {escape(scam['proof_link'])}")
        lines.append(f"Добавлен: {scam['added_at']}")
    else:
        lines.append("✅ <b>Статус:</b> в базе скамеров не найден")
    lines += ["", "📊 <b>Статистика в боте</b>"]
    lines.append(f"В боте с: {user['first_seen'] if user else 'не пользовался ботом'}")
    lines.append(
        f"Жалоб на него: {against['t'] or 0} (принято: {against['a'] or 0}, на рассмотрении: {against['w'] or 0})"
    )
    if filed is not None:
        lines.append(f"Подал жалоб: {filed['t'] or 0} (принято: {filed['a'] or 0})")
    if advice and not scam:
        lines += ["", "⚠️ Рекомендуем проводить сделки только через гаранта (кнопка «Гаранты»)."]
    elif not scam:
        lines += ["", "ℹ️ Отсутствие в базе не гарантирует честность — лучше проводить сделку через гаранта."]
    return "\n".join(lines)


async def send_media(bot: Bot, chat_id, media, caption=None):
    if not media:
        return [await bot.send_message(chat_id, caption)] if caption else []
    if len(media) == 1:
        kind, fid = media[0]
        if kind == "photo":
            return [await bot.send_photo(chat_id, fid, caption=caption)]
        return [await bot.send_video(chat_id, fid, caption=caption)]
    group = []
    for i, (kind, fid) in enumerate(media):
        cap = caption if i == 0 else None
        group.append(InputMediaPhoto(media=fid, caption=cap) if kind == "photo" else InputMediaVideo(media=fid, caption=cap))
    return await bot.send_media_group(chat_id, group)


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_COMPLAINT), KeyboardButton(text=BTN_ADD_GROUP)],
            [KeyboardButton(text=BTN_PROJECTS), KeyboardButton(text=BTN_GUARANTORS)],
            [KeyboardButton(text=BTN_ACCOUNT), KeyboardButton(text=BTN_CHECK)],
        ],
        resize_keyboard=True,
    )


def home_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_HOME)]], resize_keyboard=True)


def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)


# ───────────────────────── FSM ─────────────────────────
class Complaint(StatesGroup):
    target = State()
    description = State()
    proofs = State()


class CheckSt(StatesGroup):
    wait = State()


router = Router()


class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if u and not u.is_bot:
            try:
                upsert_user(u.id, u.username, u.first_name)
            except Exception:
                logging.exception("track user")
        return await handler(event, data)


# ───────────────────────── Главное меню (личка) ─────────────────────────
priv = Router()
priv.message.filter(F.chat.type == ChatType.PRIVATE)


@priv.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(WELCOME, reply_markup=main_kb())


@priv.message(F.text.in_({BTN_HOME, BTN_CANCEL}))
async def go_home(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🏠 Главное меню", reply_markup=main_kb())


@priv.message(F.text == BTN_ADD_GROUP)
async def add_group(m: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить бота в группу", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")]
        ]
    )
    await m.answer(
        "Добавьте бота в свой чат — в нём будут работать команды:\n"
        "<code>Чек @username</code> / <code>Чек ID</code> / <code>Чек me</code>",
        reply_markup=kb,
    )


@priv.message(F.text == BTN_PROJECTS)
async def projects(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(PROJECTS, reply_markup=main_kb())


@priv.message(F.text == BTN_GUARANTORS)
async def guarantors(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(GUARANTORS, reply_markup=main_kb())


@priv.message(F.text == BTN_ACCOUNT)
async def my_account(m: Message, state: FSMContext):
    await state.clear()
    text = await build_profile(
        m.bot, m.from_user.id, (m.from_user.username or "").lower() or None, "👤 <b>Мой аккаунт</b>", advice=True
    )
    await m.answer(text, reply_markup=main_kb(), disable_web_page_preview=True)


@priv.message(F.text == BTN_CHECK)
async def check_start(m: Message, state: FSMContext):
    await state.set_state(CheckSt.wait)
    await m.answer(CHECK_PROMPT, reply_markup=home_kb())


@priv.message(CheckSt.wait, F.text)
async def check_input(m: Message):
    t = parse_target(m.text, bare=True)
    if not t:
        await m.answer(ERR_FORMAT, reply_markup=home_kb())
        return
    text = await build_profile(m.bot, t[0], t[1], "🔎 <b>Результат проверки</b>")
    await m.answer(text, reply_markup=home_kb(), disable_web_page_preview=True)


# ───────────────────────── Подача жалобы ─────────────────────────
@priv.message(F.text == BTN_COMPLAINT)
async def complaint_start(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Complaint.target)
    await m.answer(STEP1, reply_markup=cancel_kb())


@priv.message(Complaint.target, F.text)
async def complaint_target(m: Message, state: FSMContext):
    t = parse_target(m.text)
    if not t:
        await m.answer(ERR_FORMAT)
        return
    uid, uname = await resolve(m.bot, *t)
    await state.update_data(raw=m.text.strip(), tid=uid, tun=uname)
    await state.set_state(Complaint.description)
    await m.answer(STEP2)


@priv.message(Complaint.description, F.text)
async def complaint_desc(m: Message, state: FSMContext):
    if len(m.text) < 10:
        await m.answer("❌ <b>Ошибка</b>\nОпишите ситуацию подробнее (минимум 10 символов).")
        return
    if len(m.text) > 2000:
        await m.answer("❌ <b>Ошибка</b>\nСлишком длинное описание (максимум 2000 символов).")
        return
    await state.update_data(desc=m.text, media=[], last_group=None)
    await state.set_state(Complaint.proofs)
    await m.answer(STEP3, reply_markup=proofs_kb())


def proofs_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➡️ Далее", callback_data="proofs:done"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="proofs:cancel"),
            ]
        ]
    )


@priv.message(Complaint.proofs, F.photo | F.video)
async def complaint_proof(m: Message, state: FSMContext):
    data = await state.get_data()
    media = data.get("media", [])
    group = m.media_group_id
    same_album = bool(group) and group == data.get("last_group")
    if len(media) >= 10:
        if not same_album:
            await m.answer("⚠️ Максимум 10 файлов. Нажмите «Далее».", reply_markup=proofs_kb())
        await state.update_data(last_group=group)
        return
    media.append(["photo", m.photo[-1].file_id] if m.photo else ["video", m.video.file_id])
    await state.update_data(media=media, last_group=group)
    if not same_album:
        await m.answer(f"✅ Принято файлов: {len(media)}/10. Можно отправить ещё или нажать «Далее».", reply_markup=proofs_kb())


@priv.message(Complaint.proofs)
async def complaint_proof_wrong(m: Message):
    await m.answer("❌ <b>Ошибка</b>\nОтправьте фото или видео, либо нажмите «Далее».", reply_markup=proofs_kb())


@priv.callback_query(F.data == "proofs:cancel")
async def proofs_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    await cb.message.answer("❌ Подача жалобы отменена.", reply_markup=main_kb())


@priv.callback_query(F.data == "proofs:done")
async def proofs_done(cb: CallbackQuery, state: FSMContext):
    if await state.get_state() != Complaint.proofs.state:
        await cb.answer("Жалоба уже отправлена или отменена.", show_alert=True)
        return
    data = await state.get_data()
    media = data.get("media", [])
    if not media:
        await cb.answer("Отправьте хотя бы одно фото или видео.", show_alert=True)
        return
    cid = q_exec(
        "INSERT INTO complaints(reporter_id,target_raw,target_id,target_username,description,media,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (cb.from_user.id, data["raw"], data["tid"], data["tun"], data["desc"], json.dumps(media), now()),
    ).lastrowid
    await state.clear()
    await cb.answer()
    await cb.message.answer(
        f"✅ Жалоба №{cid} отправлена модераторам. Мы сообщим о решении.", reply_markup=main_kb()
    )
    await notify_moderators(cb.bot, cid, cb.from_user)


async def notify_moderators(bot: Bot, cid: int, reporter):
    c = q_one("SELECT * FROM complaints WHERE id=?", (cid,))
    media = json.loads(c["media"])
    target = (f"@{c['target_username']}" if c["target_username"] else "") + (
        f"  ID <code>{c['target_id']}</code>" if c["target_id"] else ""
    )
    text = (
        f"🚨 <b>Новая жалоба №{cid}</b>\n\n"
        f"От: <a href=\"tg://user?id={reporter.id}\">{escape(reporter.full_name)}</a> (<code>{reporter.id}</code>)\n"
        f"На: {target or escape(c['target_raw'])}\n"
        f"Ввод пользователя: {escape(c['target_raw'])}\n\n"
        f"📝 {escape(c['description'])}\n\n"
        f"📎 Файлов: {len(media)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"mod:accept:{cid}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{cid}"),
            ]
        ]
    )
    targets = [MOD_CHAT_ID] if MOD_CHAT_ID else list(MODERATOR_IDS)
    if not targets:
        logging.warning("Не заданы MODERATOR_IDS / MOD_CHAT_ID — некому отправить жалобу")
    for chat in targets:
        try:
            await send_media(bot, chat, media)
            await bot.send_message(chat, text, reply_markup=kb)
        except Exception:
            logging.exception("notify moderator %s", chat)


# ───────────────────────── Решение модератора ─────────────────────────
@router.	callback_query(F.data.startswith("mod:"))
async def moderate(cb: CallbackQuery):
    if not is_mod(cb.from_user.id):
        await cb.answer("⛔ У вас нет прав модератора.", show_alert=True)
        return
    _, action, cid = cb.data.split(":")
    cid = int(cid)
    c = q_one("SELECT * FROM complaints WHERE id=?", (cid,))
    if not c or c["status"] != "pending":
        await cb.answer("Эта жалоба уже обработана.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return		

    bot = cb.bot
    if action == "accept":
        link = None
        if CHANNEL_ID:
            try:
                post = (
                    "🚨 <b>СКАМЕР</b>\n\n"
                    + (f"Юзернейм: @{c['target_username']}\n" if c["target_username"] else "")
                    + (f"ID: <code>{c['target_id']}</code>\n" if c["target_id"] else "")
                    + f"\n📝 {escape(c['description'])}\n\nЖалоба №{cid}"
                )
                first = await bot.send_message(CHANNEL_ID, post)
                await send_media(bot, CHANNEL_ID, json.loads(c["media"]))
                link = f"https://t.me/{CHANNEL_USERNAME}/{first.message_id}"
            except Exception:
                logging.exception("post to channel")
        add_scammer(c["target_id"], c["target_username"], c["description"][:500], link, cb.from_user.id)
        q_exec("UPDATE complaints SET status='accepted', moderator_id=? WHERE id=?", (cb.from_user.id, cid))
        verdict = "✅ Принято"
        note = f"✅ Ваша жалоба №{cid} принята. Мошенник добавлен в анти-скам." + (f"\n{link}" if link else "")
    else:
        q_exec("UPDATE complaints SET status='rejected', moderator_id=? WHERE id=?", (cb.from_user.id, cid))
        verdict = "❌ Отклонено"
        note = f"❌ Ваша жалоба №{cid} отклонена модератором."

    try:
        await cb.message.edit_text(
            f"{cb.message.html_text}\n\n<b>{verdict}</b> — {escape(cb.from_user.full_name)}", reply_markup=None
        )
    except Exception:
        pass
    await cb.answer(verdict)
    try:
        await bot.send_message(c["reporter_id"], note)
    except Exception:
        pass


# ───────────────────────── Команды в чатах ─────────────────────────
CHAT_CMD = re.compile(r"(?is)^\s*(чек|[+\-]\s*скам)\b\s*(.*)$")
ME_WORDS = {"me", "я", "мой", "моё", "мое", "my"}


@router.message(StateFilter(None), F.text.regexp(CHAT_CMD))
async def chat_commands(m: Message):
    mm = CHAT_CMD.match(m.text)
    cmd = re.sub(r"\s", "", mm.group(1)).lower()
    rest = mm.group(2).strip().split(maxsplit=1)
    token = rest[0] if rest else ""
    extra = rest[1] if len(rest) > 1 else ""

    if cmd == "чек":
        uid = uname = None
        if token.lower() in ME_WORDS:
            uid, uname = m.from_user.id, (m.from_user.username or "").lower() or None
        elif not token and m.reply_to_message and m.reply_to_message.from_user:
            r = m.reply_to_message.from_user
            upsert_user(r.id, r.username, r.first_name)
            uid, uname = r.id, (r.username or "").lower() or None
        else:
            t = parse_target(token, bare=True)
            if not t:
                await m.reply("❌ <b>Ошибка</b>\nФормат: <code>Чек @username</code>, <code>Чек 123456789</code> или <code>Чек me</code>")
                return
            uid, uname = t
        text = await build_profile(m.bot, uid, uname, "🔎 <b>Профиль</b>")
        await m.reply(text, disable_web_page_preview=True)
        return

    # +скам / -скам — только для модераторов
    if not is_mod(m.from_user.id):
        await m.reply("⛔ <b>Ошибка</b>\nУ вас нет прав модератора.")
        return
    t = parse_target(token, bare=True)
    if not t:
        await m.reply("❌ <b>Ошибка</b>\nФормат: <code>+скам @username [причина]</code> или <code>-скам ID</code>")
        return
    uid, uname = await resolve(m.bot, *t)
    who = (f"@{uname} " if uname else "") + (f"<code>{uid}</code>" if uid else "")
    if cmd.startswith("+"):
        added = add_scammer(uid, uname, extra or "Добавлен модератором", None, m.from_user.id)
        await m.reply(f"✅ {who} добавлен в анти-скам." if added else f"ℹ️ {who} уже есть в базе (данные дополнены).")
    else:
        n = remove_scammer(uid, uname)
        await m.reply(f"✅ {who} удалён из анти-скама." if n else f"ℹ️ {who} не найден в базе.")


# ───────────────────────── Индексация канала ─────────────────────────
@router.channel_post()
async def on_channel_post(m: Message):
    """Бот должен быть админом канала: тогда он видит посты и сам находит в них скамеров."""
    if (m.chat.username or "").lower() != CHANNEL_USERNAME.lower():
        return
    text = m.text or m.caption or ""
    if "скам" not in text.lower():
        return
    ignore = {CHANNEL_USERNAME.lower(), BOT_USERNAME.lower(), "nexsuchat", "xertnos", "ecanok", "nexsuruch"}
    unames = {u.lower() for u in re.findall(r"@([A-Za-z0-9_]{4,32})", text)} - ignore
    ids = {int(x) for x in re.findall(r"(?i)\b(?:id|айди)\s*[:\-]?\s*(\d{5,15})", text)}
    link = f"https://t.me/{CHANNEL_USERNAME}/{m.message_id}"
    reason = " ".join(text.split())[:300]
    if len(unames) == 1 and len(ids) == 1:
        add_scammer(next(iter(ids)), next(iter(unames)), reason, link, 0)
        return
    for u in unames:
        add_scammer(None, u, reason, link, 0)
    for i in ids:
        add_scammer(i, None, reason, link, 0)


# ───────────────────────── Ошибки / неизвестные действия ─────────────────────────
last = Router()
last.message.filter(F.chat.type == ChatType.PRIVATE)


@last.message()
async def fallback(m: Message, state: FSMContext):
    await m.answer(ERR_UNKNOWN, reply_markup=main_kb() if not await state.get_state() else None)


async def main():
    global BOT_USERNAME
    if not BOT_TOKEN:
        raise SystemExit("Укажите BOT_TOKEN в .env")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(Track())
    # порядок важен: сначала личка (меню/FSM), затем общие команды чатов
    dp.include_router(priv)
    dp.include_router(router)
    dp.include_router(last)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
 
		
	
