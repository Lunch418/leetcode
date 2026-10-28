import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Telegram user id владельца. Если пусто — бот отвечает всем (и подскажет id в /start).
OWNER_ID = int(os.environ["OWNER_ID"]) if os.environ.get("OWNER_ID") else None

TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Moscow"))
DB_PATH = os.environ.get("DB_PATH", "reminders.db")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "low")

# Модель faster-whisper: tiny / base / small / medium / large-v3
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

# Время утренней сводки (HH:MM, по TIMEZONE). Пусто — сводка выключена.
DAILY_DIGEST_AT = os.environ.get("DAILY_DIGEST_AT", "09:00")
