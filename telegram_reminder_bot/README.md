# Ассистент-напоминалка в Telegram

Личный бот-секретарь: записывает дела и дедлайны из текста или голосовых, напоминает о них
и отвечает, что и когда нужно сделать или сдать.

- Пишешь или наговариваешь в свободной форме: «сдать курсовую до пятницы», «завтра в 15:00 созвон,
  напомни за час», «что у меня на неделе?», «курсовую сдал», «перенеси лабу на понедельник».
- Голосовые расшифровываются локально через [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  это бесплатно, и аудио никуда не уходит.
- Смысл понимает Claude: сам ставит задачи, сроки и напоминания через инструменты.
- Напоминания: по умолчанию за день до дедлайна (в 10:00) и за 2 часа. Если дедлайн меньше чем через
  сутки, то за 2 часа и за 30 минут. Под напоминанием есть кнопки «✅ Сделано», «⏰ +1 час» и «⏰ Завтра».
- Каждое утро бот присылает сводку задач (время задаётся в `DAILY_DIGEST_AT`).
- Команды: `/tasks` — список задач, `/start` и `/help` — помощь.

## Запуск

Боту нужно работать постоянно (на компьютере, который не выключается, на VPS или Raspberry Pi),
иначе напоминания не придут.

1. Получи ключ Claude API: https://console.anthropic.com/settings/keys
2. Создай файл с настройками:
   ```bash
   cd telegram_reminder_bot
   cp .env.example .env
   # впиши в .env TELEGRAM_BOT_TOKEN и ANTHROPIC_API_KEY
   ```
3. Запусти бота одним из двух способов.

   **Python 3.10+:**
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python bot.py
   ```
   **Docker:**
   ```bash
   docker build -t reminder-bot .
   docker run -d --restart=always --env-file .env -v reminder-data:/data --name reminder-bot reminder-bot
   ```
4. Напиши боту `/start`. Он покажет твой Telegram id: впиши его в `OWNER_ID` в `.env` и перезапусти,
   чтобы ботом мог пользоваться только ты.

При первом голосовом бот скачает модель распознавания (`small` весит около 500 МБ). На слабом сервере
поставь `WHISPER_MODEL=base`: распознаёт быстрее, но чуть менее точно.

## Настройки (`.env`)

| Переменная | По умолчанию | Что это |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | токен от @BotFather |
| `ANTHROPIC_API_KEY` | — | ключ Claude API |
| `OWNER_ID` | пусто | твой Telegram id; если пусто, бот отвечает всем |
| `TIMEZONE` | `Europe/Moscow` | часовой пояс |
| `DAILY_DIGEST_AT` | `09:00` | время утренней сводки, пусто — выключить |
| `WHISPER_MODEL` | `small` | модель распознавания: `tiny`/`base`/`small`/`medium`/`large-v3` |
| `CLAUDE_MODEL` | `claude-opus-5-5` | модель Claude |
| `CLAUDE_EFFORT` | `low` | глубина размышлений: `low`/`medium`/`high` |
| `DB_PATH` | `reminders.db` | файл базы SQLite |

## Тесты

```bash
pip install pytest && python -m pytest -q tests
```

## Файлы

- `bot.py` — Telegram: обработчики, напоминания, утренняя сводка
- `assistant.py` — Claude и инструменты для задач (добавить, список, изменить, выполнить, удалить)
- `storage.py` — SQLite с задачами и напоминаниями
- `transcribe.py` — расшифровка голосовых
