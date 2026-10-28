"""Мозг бота: Claude понимает свободный текст и управляет задачами через инструменты."""

import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

from storage import Storage

log = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

SYSTEM_PROMPT = """\
Ты — личный ассистент-секретарь в Telegram. Пользователь пишет или наговаривает голосовые \
(они приходят тебе уже расшифрованными, возможны ошибки распознавания — додумывай по смыслу).

Твоя работа:
- Записывать задачи, дедлайны, сдачи, встречи и дела через инструменты. Не выдумывай задачи, \
которых пользователь не называл.
- Отвечать, что и когда нужно сделать/сдать, опираясь на list_tasks — не по памяти.
- Отмечать выполненное, переносить сроки, удалять ненужное.
- Помогать расставить приоритеты и спланировать день, если просят.

Время:
- Текущее время и часовой пояс указаны в начале каждого сообщения пользователя.
- Все даты в инструментах — локальное время пользователя в формате YYYY-MM-DDTHH:MM.
- «До пятницы» без времени — считай 23:59 того дня. «Утром» — 09:00, «днём» — 13:00, \
«вечером» — 19:00. Если дата неоднозначна, выбери ближайшую в будущем и скажи об этом.
- Напоминания: передавай reminders=null, тогда бот поставит их сам (за день до дедлайна \
в 10:00 и за 2 часа; если дедлайн ближе суток — за 2 часа и за 30 минут). Если пользователь \
просит напомнить в конкретное время — передай именно его.

Стиль ответа: коротко, по-русски, дружелюбно, без канцелярита. Это чат в Telegram — \
никаких markdown-таблиц и заголовков; списки можно. После добавления задачи кратко подтверди: \
что, к какому сроку и когда напомнишь (возьми время из ответа инструмента).
"""

_DT = {"type": ["string", "null"], "description": "Локальное время YYYY-MM-DDTHH:MM или null"}
_REMINDERS = {
    "type": ["array", "null"],
    "items": {"type": "string"},
    "description": "Моменты напоминаний, локальное время YYYY-MM-DDTHH:MM. "
                   "null — по умолчанию, [] — без напоминаний.",
}

TOOLS = [
    {
        "name": "add_task",
        "description": "Добавить задачу/дедлайн/дело с напоминаниями.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Коротко, что сделать/сдать"},
                "due": _DT,
                "notes": {"type": ["string", "null"], "description": "Детали, если есть"},
                "reminders": _REMINDERS,
            },
            "required": ["title", "due", "notes", "reminders"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "description": "Получить список задач пользователя с id, сроками и статусом.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"include_done": {"type": "boolean"}},
            "required": ["include_done"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_task",
        "description": "Изменить задачу: название, срок, заметки, напоминания. "
                       "null в поле — не менять его (при смене срока напоминания пересчитаются).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": ["string", "null"]},
                "due": _DT,
                "notes": {"type": ["string", "null"]},
                "reminders": _REMINDERS,
            },
            "required": ["task_id", "title", "due", "notes", "reminders"],
            "additionalProperties": False,
        },
    },
    {
        "name": "complete_task",
        "description": "Отметить задачу выполненной (или вернуть в работу, done=false).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}, "done": {"type": "boolean"}},
            "required": ["task_id", "done"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_task",
        "description": "Удалить задачу совсем.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
]


def format_dt(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "без срока"
    local = dt.astimezone(tz)
    return f"{WEEKDAYS[local.weekday()][:2]} {local:%d.%m %H:%M}"


def default_reminders(due: datetime, now: datetime) -> list[datetime]:
    if due - now > timedelta(days=1):
        day_before = (due - timedelta(days=1)).replace(hour=10, minute=0)
        return [day_before, due - timedelta(hours=2)]
    return [due - timedelta(hours=2), due - timedelta(minutes=30)]


class Assistant:
    MAX_HISTORY = 20  # сообщений (user + assistant парами) на чат
    MAX_STEPS = 8     # итераций tool-loop на один запрос

    def __init__(self, storage: Storage, tz: ZoneInfo, model: str, effort: str,
                 client: anthropic.AsyncAnthropic | None = None):
        self.storage = storage
        self.tz = tz
        self.model = model
        self.effort = effort
        self.client = client or anthropic.AsyncAnthropic()
        self.history: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.MAX_HISTORY))

    def _parse_local(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt

    def _reminders(self, raw: list[str] | None, due: datetime | None) -> list[datetime]:
        if raw is not None:
            return [self._parse_local(r) for r in raw]
        if due is not None:
            return default_reminders(due, datetime.now(self.tz))
        return []

    def run_tool(self, chat_id: int, name: str, args: dict) -> str:
        s = self.storage
        if name == "add_task":
            due = self._parse_local(args["due"]) if args.get("due") else None
            reminders = self._reminders(args.get("reminders"), due)
            task_id = s.add_task(chat_id, args["title"], due, args.get("notes"), reminders)
            planned = [format_dt(r, self.tz) for r in reminders if r > datetime.now(self.tz)]
            return _json({"ok": True, "task_id": task_id, "reminders": planned})

        if name == "list_tasks":
            tasks = s.list_tasks(chat_id, include_done=args.get("include_done", False))
            return _json([
                {"id": t["id"], "title": t["title"], "due": format_dt(t["due_at"], self.tz),
                 "notes": t["notes"], "done": t["done"]}
                for t in tasks
            ])

        if name == "update_task":
            task_id = args["task_id"]
            due = self._parse_local(args["due"]) if args.get("due") else None
            if not s.update_task(chat_id, task_id, args.get("title"), due, args.get("notes")):
                return _json({"ok": False, "error": "задача не найдена"})
            planned = None
            if args.get("reminders") is not None or due is not None:
                reminders = self._reminders(args.get("reminders"), due)
                s.set_reminders(task_id, reminders)
                planned = [format_dt(r, self.tz) for r in reminders if r > datetime.now(self.tz)]
            return _json({"ok": True, "reminders": planned})

        if name == "complete_task":
            return _json({"ok": s.set_done(chat_id, args["task_id"], args.get("done", True))})

        if name == "delete_task":
            return _json({"ok": s.delete_task(chat_id, args["task_id"])})

        return _json({"ok": False, "error": f"неизвестный инструмент {name}"})

    async def reply(self, chat_id: int, text: str) -> str:
        now = datetime.now(self.tz)
        stamped = f"[Сейчас: {WEEKDAYS[now.weekday()]}, {now:%Y-%m-%d %H:%M} ({self.tz.key})]\n{text}"
        history = self.history[chat_id]
        messages = [*history, {"role": "user", "content": stamped}]

        answer = None
        for _ in range(self.MAX_STEPS):
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                output_config={"effort": self.effort},
                # При отказе модели запрос автоматически уходит на запасную модель
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=messages,
            )
            if response.stop_reason == "refusal":
                answer = "Не могу с этим помочь. Давай про задачи и дедлайны 🙂"
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                answer = "\n".join(b.text for b in response.content if b.type == "text").strip()
                break

            results = []
            for block in tool_uses:
                try:
                    output = self.run_tool(chat_id, block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                except Exception as e:  # кривая дата и т.п. — сообщаем модели, пусть исправится
                    log.exception("Ошибка инструмента %s", block.name)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"Ошибка: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})

        answer = answer or "Готово."
        # В историю кладём только текст — этого хватает для контекста диалога
        history.append({"role": "user", "content": stamped})
        history.append({"role": "assistant", "content": answer})
        return answer


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)
