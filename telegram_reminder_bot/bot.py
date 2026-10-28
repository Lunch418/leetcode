"""Telegram-бот: личный ассистент по задачам и дедлайнам с напоминаниями и голосовыми."""

import logging
from datetime import datetime, time, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from assistant import Assistant, format_dt
from storage import Storage
from transcribe import Transcriber

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

TZ = config.TIMEZONE
storage = Storage(config.DB_PATH)
assistant = Assistant(storage, TZ, config.CLAUDE_MODEL, config.CLAUDE_EFFORT)
transcriber = Transcriber(config.WHISPER_MODEL)

TG_LIMIT = 4000


async def send_long(update: Update, text: str) -> None:
    for i in range(0, len(text), TG_LIMIT):
        await update.effective_message.reply_text(text[i:i + TG_LIMIT])


def tasks_text(chat_id: int) -> str:
    tasks = storage.list_tasks(chat_id)
    if not tasks:
        return "Задач нет 🎉"
    now = datetime.now(TZ)
    lines = []
    for t in tasks:
        overdue = " ⚠️ просрочено" if t["due_at"] and t["due_at"] < now else ""
        lines.append(f"#{t['id']} {t['title']} — {format_dt(t['due_at'], TZ)}{overdue}")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"Привет, {user.first_name}! Я твой ассистент по делам и дедлайнам.\n\n"
        "Пиши или присылай голосовые в свободной форме, например:\n"
        "• «Сдать курсовую до пятницы»\n"
        "• «Завтра в 15:00 созвон с куратором, напомни за час»\n"
        "• «Что мне надо сделать на этой неделе?»\n"
        "• «Курсовую сдал»\n\n"
        "Команды: /tasks — список задач, /help — помощь."
    )
    if config.OWNER_ID is None:
        text += (f"\n\n🔒 Твой Telegram id: {user.id}. Пропиши OWNER_ID={user.id} в .env, "
                 "чтобы ботом мог пользоваться только ты.")
    await update.message.reply_text(text)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_long(update, tasks_text(update.effective_chat.id))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await answer(update, context, update.message.text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    media = msg.voice or msg.audio or msg.video_note
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
    file = await media.get_file()
    audio = bytes(await file.download_as_bytearray())
    try:
        text = await transcriber.transcribe(audio)
    except Exception:
        log.exception("Ошибка расшифровки")
        text = ""
    if not text:
        await msg.reply_text("Не расслышал, повтори, пожалуйста 🙏")
        return
    await msg.reply_text(f"🎙 {text}")
    await answer(update, context, text)


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    try:
        reply = await assistant.reply(chat_id, text)
    except Exception:
        log.exception("Ошибка ассистента")
        reply = "Что-то пошло не так, попробуй ещё раз чуть позже."
    await send_long(update, reply)


def reminder_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Сделано", callback_data=f"done:{task_id}"),
        InlineKeyboardButton("⏰ +1 час", callback_data=f"snooze:{task_id}:60"),
        InlineKeyboardButton("⏰ Завтра", callback_data=f"snooze:{task_id}:1440"),
    ]])


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ)
    for r in storage.due_reminders(now):
        due = r["due_at"]
        when = f"\nСрок: {format_dt(due, TZ)}" if due else ""
        if due and due < now:
            when += " (уже прошёл!)"
        notes = f"\n{r['notes']}" if r["notes"] else ""
        try:
            await context.bot.send_message(
                r["chat_id"], f"🔔 Напоминаю: {r['title']}{when}{notes}",
                reply_markup=reminder_keyboard(r["id"]),
            )
        except Exception:
            log.exception("Не удалось отправить напоминание %s", r["reminder_id"])
            continue
        storage.mark_reminder_sent(r["reminder_id"])


async def daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    for chat_id in storage.chats_with_open_tasks():
        await context.bot.send_message(chat_id, "☀️ Доброе утро! Что впереди:\n\n" + tasks_text(chat_id))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    action, task_id, *rest = query.data.split(":")
    task_id = int(task_id)
    task = storage.get_task(chat_id, task_id)
    if task is None:
        await query.answer("Задача уже удалена")
        return
    if action == "done":
        storage.set_done(chat_id, task_id)
        await query.answer("Отмечено!")
        await query.edit_message_text(f"✅ {task['title']} — сделано")
    elif action == "snooze":
        at = datetime.now(TZ) + timedelta(minutes=int(rest[0]))
        storage.add_reminder(task_id, at)
        await query.answer("Ок, напомню позже")
        await query.edit_message_text(f"⏰ {task['title']} — напомню {format_dt(at, TZ)}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Необработанная ошибка", exc_info=context.error)


def main() -> None:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()

    allowed = filters.User(config.OWNER_ID) if config.OWNER_ID else filters.ALL
    if config.OWNER_ID is None:
        log.warning("OWNER_ID не задан — бот отвечает любому пользователю")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start, filters=allowed))
    app.add_handler(CommandHandler("tasks", cmd_tasks, filters=allowed))
    app.add_handler(MessageHandler(allowed & (filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE), handle_voice))
    app.add_handler(MessageHandler(allowed & filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(check_reminders, interval=60, first=5)
    if config.DAILY_DIGEST_AT:
        hh, mm = map(int, config.DAILY_DIGEST_AT.split(":"))
        app.job_queue.run_daily(daily_digest, time(hh, mm, tzinfo=TZ))

    log.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
