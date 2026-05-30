import asyncio
import subprocess
import sys
import os
import shutil
import zipfile
import tempfile
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Document
from aiogram.filters import Command
import logging

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # ← ТВОЙ ТОКЕН
ADMIN_ID = 8347013883               # ← ТВОЙ ID

# ========== НАСТРОЙКИ ЛОГОВ ==========
logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Папки для проектов
PROJECTS_DIR = "deployed_bots"
os.makedirs(PROJECTS_DIR, exist_ok=True)


def extract_requirements(file_path: str) -> list:
    """Извлекает зависимости из файла requirements.txt"""
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


async def run_bot_process(project_path: str, bot_file: str, chat_id: int):
    """Запускает бота в фоновом процессе и логирует ошибки"""
    log_file = os.path.join(project_path, "output.log")
    error_file = os.path.join(project_path, "error.txt")
    
    process = await asyncio.create_subprocess_exec(
        sys.executable, bot_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=project_path
    )
    
    # Ждём немного, чтобы бот успел запуститься или выдать ошибку
    await asyncio.sleep(3)
    
    # Проверяем, не завершился ли процесс с ошибкой
    if process.returncode is not None and process.returncode != 0:
        stderr = await process.stderr.read()
        error_text = stderr.decode('utf-8', errors='replace')
        with open(error_file, 'w') as f:
            f.write(error_text)
        await bot.send_document(chat_id=chat_id, document=open(error_file, 'rb'), caption="❌ Ошибка запуска")
        return False
    
    # Если запустился успешно, отправляем подтверждение
    await bot.send_message(chat_id, f"✅ Бот запущен!\n📁 Папка: {project_path}\n📄 Лог: {log_file}")
    
    # Фоновое ожидание завершения процесса (если упадёт)
    async def monitor():
        await process.wait()
        if process.returncode != 0:
            stderr = await process.stderr.read()
            error_text = stderr.decode('utf-8', errors='replace')
            with open(error_file, 'w') as f:
                f.write(error_text)
            await bot.send_document(chat_id, document=open(error_file, 'rb'), caption="⚠️ Бот упал с ошибкой")
    
    asyncio.create_task(monitor())
    return True


@dp.message(Command("start"))
async def start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён")
        return
    await message.answer(
        "🤖 *Деплой-бот*\n\n"
        "Отправь мне `.py` файл с кодом бота.\n"
        "Если есть зависимости — добавь `requirements.txt`\n\n"
        "📁 *Команды:*\n"
        "/list — список запущенных ботов\n"
        "/stop [название] — остановить бота\n"
        "/logs [название] — получить логи\n"
        "/error [название] — получить ошибку",
        parse_mode="Markdown"
    )


@dp.message(Command("list"))
async def list_bots(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    projects = os.listdir(PROJECTS_DIR)
    if not projects:
        await message.answer("📭 Нет запущенных ботов")
        return
    text = "📁 *Запущенные боты:*\n"
    for p in projects:
        if os.path.isdir(os.path.join(PROJECTS_DIR, p)):
            text += f"• `{p}`\n"
    await message.answer(text, parse_mode="Markdown")


@dp.message(F.document)
async def handle_document(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён")
        return
    
    doc: Document = message.document
    if not doc.file_name.endswith('.py'):
        await message.answer("❌ Отправь `.py` файл с кодом бота")
        return
    
    # Создаём папку для проекта
    project_name = doc.file_name[:-3]
    project_path = os.path.join(PROJECTS_DIR, project_name)
    os.makedirs(project_path, exist_ok=True)
    
    # Скачиваем главный файл
    file_path = os.path.join(project_path, doc.file_name)
    await bot.download(doc, destination=file_path)
    
    # Проверяем, есть ли в сообщении ещё файлы (requirements.txt)
    requirements_path = os.path.join(project_path, "requirements.txt")
    if message.document and message.document.file_name == "requirements.txt":
        await bot.download(message.document, destination=requirements_path)
    
    await message.answer(f"📁 Проект `{project_name}` создан\n⏳ Устанавливаю зависимости...", parse_mode="Markdown")
    
    # Устанавливаем зависимости
    requirements = extract_requirements(requirements_path)
    for req in requirements:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", req,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
    
    await message.answer("✅ Зависимости установлены\n🚀 Запускаю бота...")
    
    # Запускаем бота
    success = await run_bot_process(project_path, file_path, message.chat.id)
    if success:
        await message.answer(f"✅ Бот `{project_name}` запущен\n📁 Папка: `{project_path}`", parse_mode="Markdown")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())