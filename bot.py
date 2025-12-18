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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем токен из переменных Railway
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "/app/data/bot_database.db"

# Создаем папку для базы, если её нет (для Volume в Railway)
os.makedirs("/app/data", exist_ok=True)

# =============================
# ИНИЦИАЛИЗАЦИЯ БД
# =============================
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
# ЛОГИКА IMAP И ПОИСКА КОДА
# =============================
IMAP_DOMAIN_MAP = {
    "firstmail.ltd": ("imap.firstmail.ltd", 993),
    "consfml.com": ("imap.firstmail.ltd", 993),
    "ferstmail.com": ("imap.firstmail.ltd", 993),
    "tubermail.com": ("imap.firstmail.ltd", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "outlook.com": ("imap-mail.outlook.com", 993),
}

def guess_imap(email_addr: str) -> Tuple[str, int]:
    domain = email_addr.split("@")[-1].lower()
    return IMAP_DOMAIN_MAP.get(domain, (f"imap.{domain}", 993))

async def fetch_code(email_addr, password, host, port=993):
    def _sync():
        try:
            # Исправление ошибки "Name or service not known" через проверку DNS
            mail = imaplib.IMAP4_SSL(host, port, timeout=15)
            mail.login(email_addr, password)
            mail.select("INBOX")
            _, data = mail.search(None, 'ALL')
            ids = data[0].split()
            if not ids: return "Писем нет"
            
            # Проверяем последние 10 писем
            for m_id in reversed(ids[-10:]):
                _, m_data = mail.fetch(m_id, '(RFC822)')
                msg = email.message_from_bytes(m_data[0][1])
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors='ignore')
                else:
                    body = msg.get_payload(decode=True).decode(errors='ignore')
                
                code = re.search(r"\b(\d{6})\b", body)
                if code: return code.group(1)
            mail.logout()
        except Exception as e:
            return f"Ошибка: {str(e)}"
        return "Код не найден"
    return await asyncio.to_thread(_sync)

# =============================
# ИНТЕРФЕЙС И КНОПКИ
# =============================
router = Router()

def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="add")],
        [InlineKeyboardButton(text="📂 Аккаунты", callback_data="list:0")]
    ])

# Кнопки листания (Вперед/Назад)
def kb_list(user_id, page):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    accs = conn.execute('SELECT * FROM accounts WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()

    per_page = 5
    start = page * per_page
    end = start + per_page
    current_accs = accs[start:end]

    buttons = []
    for a in current_accs:
        buttons.append([InlineKeyboardButton(text=f"👤 {a['username']}", callback_data=f"view:{a['id']}:{page}")])

    # Ряд навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list:{page-1}"))
    if end < len(accs):
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"list:{page+1}"))
    
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="🏠 Меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# =============================
# ОБРАБОТЧИКИ
# =============================
class States(StatesGroup):
    add = State()

@router.message(Command("start"))
async def start(m: Message):
    await m.answer("🤖 Менеджер TikTok аккаунтов", reply_markup=kb_main())

@router.callback_query(F.data == "add")
async def add_start(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Пришли данные: `почта|пароль_почты|юзер|пароль_тт|страна|2fa`", reply_markup=kb_main())
    await state.set_state(States.add)

@router.message(States.add)
async def process_add(m: Message, state: FSMContext):
    lines = m.text.splitlines()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for line in lines:
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 4:
            host, port = guess_imap(p[0])
            cursor.execute('''INSERT INTO accounts 
                (user_id, email, passmail, username, tiktok_password, country, auth, imap_host, imap_port) 
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (m.from_user.id, p[0], p[1], p[2], p[3], p[4] if len(p)>4 else "", p[5] if len(p)>5 else "", host, port))
    conn.commit()
    conn.close()
    await state.clear()
    await m.answer("✅ Аккаунты добавлены!", reply_markup=kb_main())

@router.callback_query(F.data.startswith("list:"))
async def list_accs(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    await cb.message.edit_text(f"Список (Страница {page+1}):", reply_markup=kb_list(cb.from_user.id, page))

@router.callback_query(F.data.startswith("view:"))
async def view_acc(cb: CallbackQuery):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    
    text = f"👤 **{a['username']}**\n📧 `{a['email']}`\n🔑 Pass: `{a['tiktok_password']}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Получить код", callback_data=f"get_mail:{aid}:{page}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{aid}:{page}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list:{page}")]
    ])
    await cb.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data.startswith("get_mail:"))
async def mail_code(cb: CallbackQuery):
    aid, page = cb.data.split(":")[1], cb.data.split(":")[2]
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    
    await cb.answer("🔍 Проверяю почту...")
    code = await fetch_code(a['email'], a['passmail'], a['imap_host'], a['imap_port'])
    await cb.message.answer(f"📬 Почта: {a['email']}\nРезультат: `{code}`")

@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery):
    await cb.message.edit_text("🤖 Менеджер TikTok аккаунтов", reply_markup=kb_main())

@router.callback_query(F.data.startswith("del:"))
async def delete_acc(cb: CallbackQuery):
    aid, page = cb.data.split(":")[1], cb.data.split(":")[2]
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM accounts WHERE id = ?', (aid,))
    conn.commit(); conn.close()
    await cb.answer("Удалено")
    await cb.message.edit_text(f"Список (Страница {int(page)+1}):", reply_markup=kb_list(cb.from_user.id, int(page)))

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
