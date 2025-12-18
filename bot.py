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

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = "/app/data/bot_database.db"
os.makedirs("/app/data", exist_ok=True)

# Храним ID сообщения панели
last_msg: Dict[int, int] = {}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, email TEXT, 
        passmail TEXT, username TEXT, tiktok_password TEXT, imap_host TEXT, imap_port INTEGER)''')
    conn.commit(); conn.close()

init_db()

FIRSTMAIL_DOMAINS = ["firstmail.ltd", "consfml.com", "ferstmail.com", "tubermail.com", "hotm.com"]

async def fetch_code(email_addr, password, host):
    def _sync(target_host):
        try:
            mail = imaplib.IMAP4_SSL(target_host, 993, timeout=15)
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
                else: body = msg.get_payload(decode=True).decode(errors='ignore')
                code = re.search(r"\b(\d{6})\b", body)
                if code: return code.group(1)
            mail.logout()
        except: return None
    
    res = await asyncio.to_thread(_sync, host)
    if not res: res = await asyncio.to_thread(_sync, "imap.firstmail.ltd")
    return res

router = Router()

async def ui_panel(bot: Bot, chat_id: int, user_id: int, text: str, kb: InlineKeyboardMarkup):
    """Главная функция обновления интерфейса"""
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
    btns = []
    for i, a in enumerate(accs[start:start+per_page], start=start+1):
        btns.append([InlineKeyboardButton(text=f"{i}. 👤 {a['username']}", callback_data=f"v:{a['id']}:{page}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"p:{page-1}"))
    if start + per_page < len(accs): nav.append(InlineKeyboardButton(text="➡️", callback_data=f"p:{page+1}"))
    if nav: btns.append(nav)
    
    btns.append([InlineKeyboardButton(text="🗑 Удалить ВСЁ", callback_data="confirm_wipe")])
    btns.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

class Form(StatesGroup): add = State()

@router.message(Command("start"))
async def cmd_start(m: Message, bot: Bot):
    await m.delete()
    await ui_panel(bot, m.chat.id, m.from_user.id, "🤖 **TikTok IMAP Panel**", 
                  InlineKeyboardMarkup(inline_keyboard=[
                      [InlineKeyboardButton(text="➕ Добавить пачку", callback_data="add")],
                      [InlineKeyboardButton(text="📂 Мои аккаунты", callback_data="p:0")]
                  ]))

@router.callback_query(F.data == "main")
async def back_main(cb: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()
    await ui_panel(bot, cb.message.chat.id, cb.from_user.id, "🤖 **TikTok IMAP Panel**", 
                  InlineKeyboardMarkup(inline_keyboard=[
                      [InlineKeyboardButton(text="➕ Добавить пачку", callback_data="add")],
                      [InlineKeyboardButton(text="📂 Мои аккаунты", callback_data="p:0")]
                  ]))

@router.callback_query(F.data == "add")
async def add_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await ui_panel(bot, cb.message.chat.id, cb.from_user.id, "📥 **Режим добавления**\n\nПришли данные: `почта|пароль
