# -*- coding: utf-8 -*-
import os
import sqlite3
import logging
import re
import imaplib
import email
from email.header import decode_header
import asyncio
import socket
from typing import Optional, Tuple, Dict, List

import pyotp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)

# =============================
# Настройки и БД
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "/app/data/bot_database.db"

os.makedirs("/app/data", exist_ok=True)

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
            country TEXT,
            auth TEXT,
            imap_host TEXT,
            imap_port INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# =============================
# IMAP Helpers
# =============================
IMAP_DOMAIN_MAP = {
    "firstmail.ltd": [("imap.firstmail.ltd", 993)],
    "consfml.com": [("imap.firstmail.ltd", 993)],
    "ferstmail.com": [("imap.firstmail.ltd", 993)],
    "tubermail.com": [("imap.firstmail.ltd", 993)],
    "gmail.com": [("imap.gmail.com", 993)],
}

def guess_imap_host(email_addr: str) -> Tuple[str, int]:
    domain = email_addr.split("@")[-1].lower()
    if domain in IMAP_DOMAIN_MAP:
        return IMAP_DOMAIN_MAP[domain][0]
    return f"imap.{domain}", 993

def _extract_code(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else None

async def fetch_code(email_addr, password, host, port=993):
    def _sync():
        try:
            mail = imaplib.IMAP4_SSL(host, port, timeout=15)
            mail.login(email_addr, password)
            mail.select("INBOX")
            _, data = mail.search(None, 'ALL')
            ids = data[0].split()
            for m_id in reversed(ids[-15:]):
                _, m_data = mail.fetch(m_id, '(RFC822)')
                msg = email.message_from_bytes(m_data[0][1])
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(errors='ignore')
                        code = _extract_code(body)
                        if code: return code
            mail.logout()
        except Exception as e:
            return f"Error: {str(e)}"
        return None
    return await asyncio.to_thread(_sync)

# =============================
# Handlers & UI
# =============================
router = Router()
panels = {}

class States(StatesGroup):
    add = State()

def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить аккаунты", callback_data="add")],
        [InlineKeyboardButton(text="📂 Мои аккаунты", callback_data="list:0")]
    ])

@router.message(Command("start"))
async def start(m: Message):
    sent = await m.answer("🤖 TikTok IMAP Bot активен", reply_markup=kb_main())
    panels[m.from_user.id] = sent.message_id

@router.callback_query(F.data == "add")
async def add_start(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Пришли данные в формате:\n`почта|пароль_почты|юзер|пароль_тт|страна|2fa`", reply_markup=kb_main())
    await state.set_state(States.add)

@router.message(States.add)
async def process_add(m: Message, state: FSMContext):
    lines = m.text.splitlines()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for line in lines:
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 4:
            host, port = guess_imap_host(p[0])
            cursor.execute('INSERT INTO accounts (user_id, email, passmail, username, tiktok_password, country, auth, imap_host, imap_port) VALUES (?,?,?,?,?,?,?,?,?)',
                           (m.from_user.id, p[0], p[1], p[2], p[3], p[4] if len(p)>4 else "", p[5] if len(p)>5 else "", host, port))
    conn.commit()
    conn.close()
    await state.clear()
    await m.answer("✅ Аккаунты добавлены")

@router.callback_query(F.data.startswith("list:"))
async def list_accs(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    accs = conn.execute('SELECT * FROM accounts WHERE user_id = ?', (cb.from_user.id,)).fetchall()
    conn.close()
    
    kb = []
    for a in accs[page*10:(page+1)*10]:
        kb.append([InlineKeyboardButton(text=f"{a['username']} (@{a['email']})", callback_data=f"view:{a['id']}")])
    kb.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    await cb.message.edit_text(f"Список аккаунтов (всего: {len(accs)}):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("view:"))
async def view_acc(cb: CallbackQuery):
    aid = cb.data.split(":")[1]
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone()
    conn.close()
    
    text = f"👤 **{a['username']}**\n📧 `{a['email']}`\n🔑 Pass: `{a['tiktok_password']}`\n🌍 Country: {a['country']}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Код из почты", callback_data=f"get_mail:{aid}")],
        [InlineKeyboardButton(text="🔐 TOTP (2FA)", callback_data=f"get_totp:{aid}")],
        [InlineKeyboardButton(text="❌ Удалить", callback_data=f"del:{aid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="list:0")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data.startswith("get_mail:"))
async def mail_code(cb: CallbackQuery):
    aid = cb.data.split(":")[1]
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    await cb.answer("🔍 Ищу код...")
    code = await fetch_code(a['email'], a['passmail'], a['imap_host'], a['imap_port'])
    await cb.message.answer(f"📬 Код для {a['email']}:\n`{code if code else 'Не найден'}`")

@router.callback_query(F.data.startswith("del:"))
async def delete_acc(cb: CallbackQuery):
    aid = cb.data.split(":")[1]
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM accounts WHERE id = ?', (aid,))
    conn.commit(); conn.close()
    await cb.answer("Удалено")
    await list_accs(cb)

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await cb.message.edit_text("🤖 TikTok IMAP Bot активен", reply_markup=kb_main())

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
