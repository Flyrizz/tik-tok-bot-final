# -*- coding: utf-8 -*-
import os
import sqlite3
import logging
import re
import imaplib
import email
from email.header import decode_header
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

# Хранилище ID последнего сообщения бота для обновления панели
last_panel_msg: Dict[int, int] = {}

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

# Расширенный список IMAP серверов
IMAP_DOMAIN_MAP = {
    "firstmail.ltd": ("imap.firstmail.ltd", 993),
    "consfml.com": ("imap.firstmail.ltd", 993),
    "ferstmail.com": ("imap.firstmail.ltd", 993),
    "tubermail.com": ("imap.firstmail.ltd", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "outlook.com": ("imap-mail.outlook.com", 993),
    "hotmail.com": ("imap-mail.outlook.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "rambler.ru": ("imap.rambler.ru", 993),
    "mail.ru": ("imap.mail.ru", 993),
}

def guess_imap(email_addr: str) -> Tuple[str, int]:
    domain = email_addr.split("@")[-1].lower()
    return IMAP_DOMAIN_MAP.get(domain, (f"imap.{domain}", 993))

async def fetch_code(email_addr, password, host, port=993):
    def _sync():
        try:
            # Пытаемся подключиться с таймаутом
            mail = imaplib.IMAP4_SSL(host, port, timeout=20)
            mail.login(email_addr, password)
            mail.select("INBOX")
            # Ищем все письма, берем последние 15
            _, data = mail.search(None, 'ALL')
            ids = data[0].split()
            if not ids: return "Писем не найдено"
            
            for m_id in reversed(ids[-15:]):
                _, m_data = mail.fetch(m_id, '(RFC822)')
                msg = email.message_from_bytes(m_data[0][1])
                # Проверяем тему и тело на наличие кода
                content_parts = []
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ["text/plain", "text/html"]:
                            content_parts.append(part.get_payload(decode=True).decode(errors='ignore'))
                else:
                    content_parts.append(msg.get_payload(decode=True).decode(errors='ignore'))
                
                full_text = " ".join(content_parts)
                # Ищем 6-значный код
                code = re.search(r"\b(\d{6})\b", full_text)
                if code: return code.group(1)
            mail.logout()
        except Exception as e:
            return f"Ошибка связи: {str(e)}"
        return "Код не найден (проверьте спам)"
    return await asyncio.to_thread(_sync)

router = Router()

# Функция для обновления панели (удаляет старое, шлет новое или правит текущее)
async def update_panel(bot: Bot, user_id: int, chat_id: int, text: str, kb: InlineKeyboardMarkup):
    if user_id in last_panel_msg:
        try:
            await bot.edit_message_text(text, chat_id, last_panel_msg[user_id], reply_markup=kb)
            return
        except Exception:
            pass # Если сообщение удалено или не изменилось
    
    sent = await bot.send_message(chat_id, text, reply_markup=kb)
    last_panel_msg[user_id] = sent.message_id

def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пачкой", callback_data="add")],
        [InlineKeyboardButton(text="📂 Список аккаунтов", callback_data="list:0")]
    ])

def kb_list(user_id, page):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    accs = conn.execute('SELECT * FROM accounts WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()

    per_page = 10
    start = page * per_page
    end = start + per_page
    current_accs = accs[start:end]

    buttons = []
    # Нумерация аккаунтов в кнопках
    for i, a in enumerate(current_accs, start=start + 1):
        buttons.append([InlineKeyboardButton(text=f"{i}. 👤 {a['username']}", callback_data=f"view:{a['id']}:{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"list:{page-1}"))
    if end < len(accs):
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"list:{page+1}"))
    
    if nav_row: buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="🏠 В меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

class States(StatesGroup):
    add = State()

@router.message(Command("start"))
async def start(m: Message, bot: Bot):
    await m.delete() # Удаляем сообщение пользователя
    await update_panel(bot, m.from_user.id, m.chat.id, "🤖 **TikTok IMAP Panel**\n\nВыберите действие:", kb_main())

@router.callback_query(F.data == "home")
async def home_cb(cb: CallbackQuery, bot: Bot):
    await update_panel(bot, cb.from_user.id, cb.message.chat.id, "🤖 **TikTok IMAP Panel**\n\nВыберите действие:", kb_main())
    await cb.answer()

@router.callback_query(F.data == "add")
async def add_start(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await update_panel(bot, cb.from_user.id, cb.message.chat.id, 
                       "📥 **Добавление аккаунтов**\n\nПришлите данные в формате:\n`почта|пароль_почты|юзер|пароль_тт` (каждый с новой строки)", kb_main())
    await state.set_state(States.add)
    await cb.answer()

@router.message(States.add)
async def process_add(m: Message, state: FSMContext, bot: Bot):
    lines = m.text.splitlines()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    added = 0
    for line in lines:
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 4:
            host, port = guess_imap(p[0])
            cursor.execute('INSERT INTO accounts (user_id, email, passmail, username, tiktok_password, imap_host, imap_port) VALUES (?,?,?,?,?,?,?)',
                           (m.from_user.id, p[0], p[1], p[2], p[3], host, port))
            added += 1
    conn.commit()
    conn.close()
    await m.delete() # Удаляем список, который прислал юзер
    await state.clear()
    await update_panel(bot, m.from_user.id, m.chat.id, f"✅ Успешно добавлено: {added} шт.", kb_main())

@router.callback_query(F.data.startswith("list:"))
async def list_accs(cb: CallbackQuery, bot: Bot):
    page = int(cb.data.split(":")[1])
    await update_panel(bot, cb.from_user.id, cb.message.chat.id, f"📂 **Ваши аккаунты (Стр. {page+1})**", kb_list(cb.from_user.id, page))
    await cb.answer()

@router.callback_query(F.data.startswith("view:"))
async def view_acc(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    
    text = f"👤 **Аккаунт: {a['username']}**\n\n📧 Почта: `{a['email']}`\n🔑 Пароль: `{a['tiktok_password']}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Взять код", callback_data=f"get_mail:{aid}:{page}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{aid}:{page}")],
        [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"list:{page}")]
    ])
    await update_panel(bot, cb.from_user.id, cb.message.chat.id, text, kb)
    await cb.answer()

@router.callback_query(F.data.startswith("get_mail:"))
async def mail_code(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    a = conn.execute('SELECT * FROM accounts WHERE id = ?', (aid,)).fetchone(); conn.close()
    
    await cb.answer("⏳ Подключаюсь к почте...")
    code = await fetch_code(a['email'], a['passmail'], a['imap_host'], a['imap_port'])
    
    # Показываем результат и кнопку назад
    text = f"👤 **{a['username']}**\n📬 Почта: `{a['email']}`\n\n🔢 КОД: `{code}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view:{aid}:{page}")]])
    await update_panel(bot, cb.from_user.id, cb.message.chat.id, text, kb)

@router.callback_query(F.data.startswith("del:"))
async def delete_acc(cb: CallbackQuery, bot: Bot):
    _, aid, page = cb.data.split(":")
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM accounts WHERE id = ?', (aid,))
    conn.commit(); conn.close()
    await cb.answer("Удалено")
    await list_accs(cb, bot)

async def main():
    bot_obj = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot_obj)

if __name__ == "__main__":
    asyncio.run(main())
