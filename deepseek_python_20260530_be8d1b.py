import asyncio
import os
import sys
import subprocess
import shutil
import zipfile
import tempfile
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Document, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import logging

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # ← ВСТАВЬ ТОКЕН
ADMIN_ID = 8347013883               # ← ТВОЙ ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Папки для проектов
PROJECTS_DIR = "deployed_bots"
os.makedirs(PROJECTS_DIR, exist_ok=True)

# ========== СОСТОЯНИЯ ДЛЯ ЗАГРУЗКИ ==========
class DeployState(StatesGroup):
    waiting_for_main_file = State()      # ждём главный .py файл
    waiting_for_requirements = State()   # ждём requirements.txt
    waiting_for_env = State()            # ждём .env файл
    waiting_for_extra = State()          # ждём дополнительные файлы
    confirming = State()                 # подтверждение перед запуском

# ========== КЛАВИАТУРЫ ==========
def get_main_menu():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 ЗАЛИТЬ НОВОГО БОТА", callback_data="deploy_new")],
        [InlineKeyboardButton(text="📋 СПИСОК БОТОВ", callback_data="list_bots")],
        [InlineKeyboardButton(text="🛑 ОСТАНОВИТЬ БОТА", callback_data="stop_bot")],
        [InlineKeyboardButton(text="📄 ЛОГИ БОТА", callback_data="logs_bot")],
        [InlineKeyboardButton(text="🗑 УДАЛИТЬ БОТА", callback_data="delete_bot")]
    ])
    return kb

def get_bots_keyboard():
    """Создаёт клавиатуру со списком ботов"""
    projects = [d for d in os.listdir(PROJECTS_DIR) 
                if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
    if not projects:
        return None
    buttons = []
    for proj in projects[:10]:
        buttons.append([InlineKeyboardButton(text=f"📁 {proj}", callback_data=f"bot_select:{proj}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_bot_actions_keyboard(bot_name: str):
    """Клавиатура действий для конкретного бота"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 ЗАПУСТИТЬ", callback_data=f"bot_start:{bot_name}"),
         InlineKeyboardButton(text="🛑 ОСТАНОВИТЬ", callback_data=f"bot_stop:{bot_name}")],
        [InlineKeyboardButton(text="📄 ЛОГИ", callback_data=f"bot_logs:{bot_name}"),
         InlineKeyboardButton(text="🗑 УДАЛИТЬ", callback_data=f"bot_delete:{bot_name}")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_menu")]
    ])
    return kb

# ========== ФУНКЦИИ УПРАВЛЕНИЯ БОТАМИ ==========
def extract_requirements(project_path: str) -> list:
    """Извлекает зависимости из requirements.txt"""
    req_path = os.path.join(project_path, "requirements.txt")
    if not os.path.exists(req_path):
        return []
    with open(req_path, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

async def install_dependencies(project_path: str, requirements: list):
    """Устанавливает зависимости"""
    for req in requirements:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", req,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_path
        )
        await proc.wait()
    return True

async def run_bot_process(project_path: str, bot_file: str, chat_id: int, bot_name: str):
    """Запускает бота в фоне"""
    log_file = os.path.join(project_path, "output.log")
    error_file = os.path.join(project_path, "error.txt")
    pid_file = os.path.join(project_path, "pid.txt")
    
    # Проверяем, не запущен ли уже
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        # Можно проверить, жив ли процесс, но для простоты перезапускаем
        await bot.send_message(chat_id, f"⚠️ Бот {bot_name} уже был запущен. Перезапускаю...")
    
    # Запускаем процесс
    process = await asyncio.create_subprocess_exec(
        sys.executable, bot_file,
        stdout=open(log_file, 'w'),
        stderr=open(error_file, 'w'),
        cwd=project_path
    )
    
    # Сохраняем PID
    with open(pid_file, 'w') as f:
        f.write(str(process.pid))
    
    await bot.send_message(chat_id, f"✅ Бот {bot_name} ЗАПУЩЕН!\n📁 Папка: {project_path}\n📄 Лог: {log_file}")
    
    # Мониторим процесс в фоне
    async def monitor():
        await process.wait()
        if process.returncode != 0:
            await bot.send_message(chat_id, f"❌ Бот {bot_name} УПАЛ с ошибкой!\nЛог в /logs {bot_name}")
    
    asyncio.create_task(monitor())
    return True

async def stop_bot_process(project_path: str, bot_name: str, chat_id: int):
    """Останавливает бота"""
    pid_file = os.path.join(project_path, "pid.txt")
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
        try:
            # Для Windows
            subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
        except:
            try:
                # Для Linux
                os.kill(int(pid), 9)
            except:
                pass
        os.remove(pid_file)
        await bot.send_message(chat_id, f"🛑 Бот {bot_name} ОСТАНОВЛЕН")
        return True
    await bot.send_message(chat_id, f"⚠️ Бот {bot_name} не был запущен")
    return False

async def get_logs(project_path: str, bot_name: str) -> str:
    """Получает последние строки лога"""
    log_file = os.path.join(project_path, "output.log")
    if not os.path.exists(log_file):
        return "Логов пока нет"
    with open(log_file, 'r') as f:
        lines = f.readlines()
        # Последние 30 строк
        return ''.join(lines[-30:]) if lines else "Лог пуст"

async def get_error(project_path: str) -> str:
    """Получает ошибку, если есть"""
    error_file = os.path.join(project_path, "error.txt")
    if not os.path.exists(error_file):
        return None
    with open(error_file, 'r') as f:
        content = f.read()
        if content.strip():
            return content
    return None

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ ДОСТУП ЗАПРЕЩЁН")
        return
    await message.answer(
        "🤖 *ДЕПЛОЙ-БОТ v2.0*\n\n"
        "Заливай и запускай ботов прямо в Telegram\n\n"
        "📌 *Возможности:*\n"
        "• Пошаговая загрузка файлов\n"
        "• Автоустановка зависимостей\n"
        "• Управление запущенными ботами\n"
        "• Логи и ошибки\n\n"
        "👇 *Выбери действие:*",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# ========== ГЛАВНОЕ МЕНЮ ==========
@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤖 *ДЕПЛОЙ-БОТ*\n\n👇 Выбери действие:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "list_bots")
async def list_bots(callback: CallbackQuery):
    projects = [d for d in os.listdir(PROJECTS_DIR) 
                if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
    
    if not projects:
        await callback.message.edit_text(
            "📭 *НЕТ ЗАГРУЖЕННЫХ БОТОВ*\n\nНажми «ЗАЛИТЬ НОВОГО БОТА»",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return
    
    text = "📁 *СПИСОК БОТОВ:*\n\n"
    for i, proj in enumerate(projects, 1):
        # Проверяем, запущен ли
        pid_file = os.path.join(PROJECTS_DIR, proj, "pid.txt")
        status = "🟢" if os.path.exists(pid_file) else "🔴"
        text += f"{status} {i}. `{proj}`\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 ВЫБРАТЬ БОТА", callback_data="select_bot")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "select_bot")
async def select_bot(callback: CallbackQuery):
    kb = get_bots_keyboard()
    if not kb:
        await callback.answer("Нет ботов", show_alert=True)
        return
    await callback.message.edit_text(
        "🔍 *ВЫБЕРИ БОТА:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("bot_select:"))
async def bot_selected(callback: CallbackQuery):
    bot_name = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"📁 *БОТ: {bot_name}*\n\nВыбери действие:",
        reply_markup=get_bot_actions_keyboard(bot_name),
        parse_mode="Markdown"
    )
    await callback.answer()

# ========== УПРАВЛЕНИЕ БОТАМИ ==========
@dp.callback_query(F.data.startswith("bot_start:"))
async def bot_start(callback: CallbackQuery):
    bot_name = callback.data.split(":")[1]
    project_path = os.path.join(PROJECTS_DIR, bot_name)
    bot_file = os.path.join(project_path, f"{bot_name}.py")
    
    if not os.path.exists(bot_file):
        await callback.answer("Главный файл не найден", show_alert=True)
        return
    
    await callback.message.edit_text(f"🚀 *ЗАПУСК {bot_name}...*", parse_mode="Markdown")
    
    # Устанавливаем зависимости
    requirements = extract_requirements(project_path)
    if requirements:
        await callback.message.edit_text(f"📦 Устанавливаю зависимости для {bot_name}...", parse_mode="Markdown")
        await install_dependencies(project_path, requirements)
    
    await run_bot_process(project_path, bot_file, callback.message.chat.id, bot_name)
    await callback.answer()

@dp.callback_query(F.data.startswith("bot_stop:"))
async def bot_stop(callback: CallbackQuery):
    bot_name = callback.data.split(":")[1]
    project_path = os.path.join(PROJECTS_DIR, bot_name)
    await stop_bot_process(project_path, bot_name, callback.message.chat.id)
    await callback.answer()

@dp.callback_query(F.data.startswith("bot_logs:"))
async def bot_logs(callback: CallbackQuery):
    bot_name = callback.data.split(":")[1]
    project_path = os.path.join(PROJECTS_DIR, bot_name)
    logs = await get_logs(project_path, bot_name)
    error = await get_error(project_path)
    
    if error:
        logs += f"\n\n⚠️ *ОШИБКА:*\n{error[:500]}"
    
    if len(logs) > 4000:
        logs = logs[-3500:]
    
    await callback.message.answer(
        f"📄 *ЛОГИ {bot_name}:*\n```\n{logs}\n```",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("bot_delete:"))
async def bot_delete(callback: CallbackQuery):
    bot_name = callback.data.split(":")[1]
    project_path = os.path.join(PROJECTS_DIR, bot_name)
    
    # Останавливаем если запущен
    await stop_bot_process(project_path, bot_name, callback.message.chat.id)
    
    # Удаляем папку
    shutil.rmtree(project_path, ignore_errors=True)
    await callback.message.edit_text(f"🗑 *БОТ {bot_name} УДАЛЁН*", reply_markup=get_main_menu(), parse_mode="Markdown")
    await callback.answer()

# ========== ПРОЦЕСС ЗАГРУЗКИ ==========
@dp.callback_query(F.data == "deploy_new")
async def deploy_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeployState.waiting_for_main_file)
    await callback.message.edit_text(
        "🚀 *ЗАГРУЗКА НОВОГО БОТА*\n\n"
        "📌 *ШАГ 1 из 4*\n\n"
        "Отправь *ГЛАВНЫЙ ФАЙЛ* бота (`.py`):\n\n"
        "Пример: `my_bot.py`\n\n"
        "❌ Отмена — /cancel",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(DeployState.waiting_for_main_file, F.document)
async def deploy_main_file(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith('.py'):
        await message.answer("❌ Отправь `.py` файл!")
        return
    
    bot_name = doc.file_name[:-3]
    project_path = os.path.join(PROJECTS_DIR, bot_name)
    os.makedirs(project_path, exist_ok=True)
    
    # Сохраняем главный файл
    file_path = os.path.join(project_path, doc.file_name)
    await bot.download(doc, destination=file_path)
    
    await state.update_data(bot_name=bot_name, project_path=project_path)
    await state.set_state(DeployState.waiting_for_requirements)
    
    await message.answer(
        f"✅ *ФАЙЛ {doc.file_name} ЗАГРУЖЕН*\n\n"
        "📌 *ШАГ 2 из 4*\n\n"
        "Отправь `requirements.txt` (или нажми «ПРОПУСТИТЬ»):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏩ ПРОПУСТИТЬ", callback_data="skip_requirements")]
        ])
    )

@dp.callback_query(F.data == "skip_requirements")
async def skip_requirements(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeployState.waiting_for_env)
    await callback.message.edit_text(
        "📌 *ШАГ 3 из 4*\n\n"
        "Отправь `.env` файл с переменными (или нажми «ПРОПУСТИТЬ»):\n\n"
        "Пример:\n"
        "`BOT_TOKEN=123456:ABC...`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏩ ПРОПУСТИТЬ", callback_data="skip_env")]
        ])
    )
    await callback.answer()

@dp.message(DeployState.waiting_for_requirements, F.document)
async def deploy_requirements(message: Message, state: FSMContext):
    data = await state.get_data()
    project_path = data.get('project_path')
    
    if message.document.file_name == 'requirements.txt':
        file_path = os.path.join(project_path, 'requirements.txt')
        await bot.download(message.document, destination=file_path)
        await message.answer(f"✅ `requirements.txt` ЗАГРУЖЕН", parse_mode="Markdown")
    
    await state.set_state(DeployState.waiting_for_env)
    await message.answer(
        "📌 *ШАГ 3 из 4*\n\n"
        "Отправь `.env` файл (или нажми «ПРОПУСТИТЬ»):\n\n"
        "Пример:\n"
        "`BOT_TOKEN=123456:ABC...`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏩ ПРОПУСТИТЬ", callback_data="skip_env")]
        ])
    )

@dp.callback_query(F.data == "skip_env")
async def skip_env(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DeployState.confirming)
    data = await state.get_data()
    bot_name = data.get('bot_name')
    await callback.message.edit_text(
        f"📌 *ШАГ 4 из 4 — ПОДТВЕРЖДЕНИЕ*\n\n"
        f"📁 Бот: `{bot_name}`\n"
        f"📂 Путь: `{data.get('project_path')}`\n\n"
        f"✅ *ВСЁ ГОТОВО!*\n\n"
        f"Запустить бота сейчас?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 ЗАПУСТИТЬ СЕЙЧАС", callback_data="confirm_deploy")],
            [InlineKeyboardButton(text="📋 В МЕНЮ", callback_data="back_to_menu")]
        ])
    )
    await callback.answer()

@dp.message(DeployState.waiting_for_env, F.document)
async def deploy_env(message: Message, state: FSMContext):
    data = await state.get_data()
    project_path = data.get('project_path')
    
    if message.document.file_name == '.env':
        file_path = os.path.join(project_path, '.env')
        await bot.download(message.document, destination=file_path)
        await message.answer(f"✅ `.env` ЗАГРУЖЕН", parse_mode="Markdown")
    
    await state.set_state(DeployState.confirming)
    bot_name = data.get('bot_name')
    await message.answer(
        f"📌 *ПОДТВЕРЖДЕНИЕ*\n\n"
        f"📁 Бот: `{bot_name}`\n\n"
        f"✅ *ВСЁ ГОТОВО!*\n\n"
        f"Запустить бота сейчас?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 ЗАПУСТИТЬ СЕЙЧАС", callback_data="confirm_deploy")],
            [InlineKeyboardButton(text="📋 В МЕНЮ", callback_data="back_to_menu")]
        ])
    )

@dp.callback_query(F.data == "confirm_deploy")
async def confirm_deploy(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bot_name = data.get('bot_name')
    project_path = data.get('project_path')
    bot_file = os.path.join(project_path, f"{bot_name}.py")
    
    await callback.message.edit_text(f"🚀 *ЗАПУСК {bot_name}...*", parse_mode="Markdown")
    
    # Устанавливаем зависимости
    requirements = extract_requirements(project_path)
    if requirements:
        await callback.message.edit_text(f"📦 Устанавливаю зависимости...", parse_mode="Markdown")
        await install_dependencies(project_path, requirements)
    
    await run_bot_process(project_path, bot_file, callback.message.chat.id, bot_name)
    await state.clear()
    await callback.answer()

# ========== ОСТАНОВКА БОТА ИЗ МЕНЮ ==========
@dp.callback_query(F.data == "stop_bot")
async def stop_bot_menu(callback: CallbackQuery):
    kb = get_bots_keyboard()
    if not kb:
        await callback.answer("Нет ботов", show_alert=True)
        return
    await callback.message.edit_text(
        "🛑 *ВЫБЕРИ БОТА ДЛЯ ОСТАНОВКИ:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "logs_bot")
async def logs_bot_menu(callback: CallbackQuery):
    kb = get_bots_keyboard()
    if not kb:
        await callback.answer("Нет ботов", show_alert=True)
        return
    await callback.message.edit_text(
        "📄 *ВЫБЕРИ БОТА ДЛЯ ПРОСМОТРА ЛОГОВ:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "delete_bot")
async def delete_bot_menu(callback: CallbackQuery):
    kb = get_bots_keyboard()
    if not kb:
        await callback.answer("Нет ботов", show_alert=True)
        return
    await callback.message.edit_text(
        "🗑 *ВЫБЕРИ БОТА ДЛЯ УДАЛЕНИЯ:*",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ ДЕЙСТВИЕ ОТМЕНЕНО", reply_markup=get_main_menu(), parse_mode="Markdown")

# ========== ЗАПУСК ==========
async def main():
    print("✅ ДЕПЛОЙ-БОТ ЗАПУЩЕН")
    print(f"👑 АДМИН: {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())