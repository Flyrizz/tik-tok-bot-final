# -*- coding: utf-8 -*-
import os
import sqlite3
import logging
import re
import imaplib
import email
import asyncio
from typing import Optional, Tuple, Dict, List

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

# Токен берем из Railway Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "/app/data/bot_database.db"
os.makedirs("/app/data", exist_ok=True)

# Храним ID сообщения панели, чтобы обновлять его
last_msg: Dict[int, int] = {}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email TEXT,
            passmail TEXT,
            username TEXT,
            tiktok_password TEXT,
            imap_host TEXT,
            imap_port INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Список доменов, которые точно относятся к FirstMail
FIRSTMAIL_DOMAINS = ["firstmail.ltd", "consfml.com", "ferstmail.com", "tubermail.com", "hotm.com"]

async def fetch_code(email_addr, password, host, port=993):
    def _sync(target_host):
        try:
            mail = imaplib.IMAP4_SSL(target_host, port, timeout=20)
            mail.login(email_addr, password)
            mail.select("INBOX")
            _, data = mail.search(None, 'ALL')
            ids = data[0].split()
            if not ids: return None
            
            for m_id in reversed(ids[-10:]):
                _, m_data = mail.fetch(m_id, '(RFC822)')
                msg = email.message_from_bytes(m_data[0][1])
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body += part.get_payload(decode=True).decode(errors='ignore')
                else:
                    body = msg.get_payload(decode=True).decode(errors='ignore')
                
                code = re.search(r"\b(\d{6})\b", body)
                if code: return code.group(1)
            mail.logout()
        except Exception:
            return None
        return None

    # Сначала пробуем родной хост, если не вышло — принудительно imap.firstmail.ltd
    res = await asyncio.to_thread(_sync, host)
    if not res:
        res = await asyncio.to_thread(_sync, "imap.firstmail.ltd")
    return res

router = Router()

async def send_or_edit(bot: Bot, chat_id: int, user_id: int, text: str, kb: InlineKeyboardMarkup):
    if user_id in last_msg:
        try:
            await bot.edit_message_text(text, chat_id, last_msg[user_id], reply_markup=kb)
            return
        except Exception: pass
    sent = await bot.send_message(chat_id, text, reply_markup=kb)
    last_msg[user_id] = sent.message_id

def get_kb_list(user_id, page):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    accs = conn.execute('SELECT * FROM accounts WHERE user_id = ?', (user_id,)).fetchall(); conn.close()
    
    per_page = 10
    start = page * per_page
    current = accs[start:start+per_page]
    
    btns = []
    for i, a in enumerate(current, start=start+1):
        btns.append([InlineKeyboardButton(text=f"{i}. 👤 {a['username']}", callback_data=f"v:{a['id']}:{page}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:{page-1}"))
    if start + per_page < len(accs): nav.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"p:{page+1}"))
    if nav: btns.append(nav)
    btns.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

class Form(StatesGroup): add = State()

@router.message(Command("start"))
async def cmd_start(m: Message, bot: Bot):
    await m.delete()
    await send_or_edit(bot, m.chat.id, m.from_user.id, "🤖 **Панель управления TikTok**", 
                       InlineKeyboardMarkup(inline_keyboard=[
                           [InlineKeyboardButton(text="➕ Добавить пачку", callback_data="add")],
                           [InlineKeyboardButton(text="📂 Мои аккаунты", callback_data="p:0")]
                       ]))

@router.callback_query(F.data == "main")
async def back_main(cb: CallbackQuery, bot: Bot):
    await send_or_edit(bot, cb.message.chat.id, cb.from_user.id, "🤖 **Панель управления TikTok**", 
                       InlineKeyboardMarkup(inline_keyboard=[
                           [InlineKeyboardButton(text="➕ Добавить пачку", callback_data="add")],
                           [InlineKeyboardButton(text="📂 Мои аккаунты", callback_data="p:0")]
                       ]))

@router.callback_query(F.data == "add")
async def add_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await send_or_edit(bot, cb.message.chat.id, cb.from_user.id, "📥 Пришли данные в формате:\n`почта|пароль_почты|юзер|пароль_тт`", None)
    await state.set_state(Form.add)

@router.message(Form.add)
async def process_add(m: Message, state: FSMContext, bot: Bot):
    lines = m.text.splitlines()
    conn = sqlite3.connect(DB_PATH)
    for line in lines:
        p = line.split("|")
        if len(p) >= 4:
            domain = p[0].split("@")[-1].lower()
            host = "imap.firstmail.ltd" if domain in FIRSTMAIL_DOMAINS else f"imap.{domain}"
            conn.execute('INSERT INTO accounts (user_id, email, passmail, username, tiktok_password, imap_host, imap_port) VALUES (?,?,?,?,?,?,?)',
                         (m.from_user.id, p[0].strip(), p[1].strip(), p[2].strip(), p[3].strip(), host, 993))
    conn.commit(); conn.close()
    await m.delete(); await state.clear()
    await cmd_start(m, bot)

@router.callback_query(F.data.startswith("p:"))
async def show_list(cb: CallbackQuery, bot: Bot):
    page = int(cb.data.split(":")[1])
    await send_or_edit(bot, cb.message.chat.id, cb.from_user.id, f"📂 **Список аккаунтов (Стр. {page+1})**", get_kb_list(cb.from_user.id, page))

@router.callback_query(F.data.startswith("v:"))
async def view_acc(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    text = f"👤 **{a['username']}**\n📧 `{a['email']}`\n🔑 ТТ Пароль: `{a['tiktok_password']}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Взять код", callback_data=f"get:{aid}:{page}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{aid}:{page}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"p:{page}")]
    ])
    await send_or_edit(bot, cb.message.chat.id, cb.from_user.id, text, kb)

@router.callback_query(F.data.startswith("get:"))
async def get_mail(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    await cb.answer("⏳ Проверяю почту (через Firstmail)...")
    code = await fetch_code(a['email'], a['passmail'], a['imap_host'])
    
    text = f"👤 **{a['username']}**\n🔢 Код: `{code if code else 'Не найден'}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"v:{aid}:{page}")]])
    await send_or_edit(bot, cb.message.chat.id, cb.from_user.id, text, kb)

@router.callback_query(F.data.startswith("del:"))
async def del_acc(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.execute('DELETE FROM accounts WHERE id = ?', (aid,)); conn.commit(); conn.close()
    await show_list(cb, bot)

async def main():
    bot_obj = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage()); dp.include_router(router)
    await dp.start_polling(bot_obj)

if __name__ == "__main__":
    asyncio.run(main())
