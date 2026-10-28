"""Офлайн-тесты: хранилище, напоминания и tool-loop ассистента с подставным клиентом Claude."""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant import Assistant, default_reminders  # noqa: E402
from storage import Storage  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")


class FakeClient:
    """Отдаёт заранее заданные ответы вместо Claude API."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def text(t):
    return SimpleNamespace(type="text", text=t)


def resp(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def test_storage_and_reminders(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    now = datetime.now(TZ)
    due = now + timedelta(days=3)
    tid = s.add_task(1, "Курсовая", due, None, [now - timedelta(minutes=1), now + timedelta(hours=1)])
    # прошедшее напоминание не сохраняется, будущее ещё не наступило
    assert s.due_reminders(now) == []
    assert len(s.due_reminders(now + timedelta(hours=2))) == 1
    s.add_reminder(tid, now - timedelta(seconds=1))
    due_now = s.due_reminders(now)
    assert [r["title"] for r in due_now] == ["Курсовая"]
    s.mark_reminder_sent(due_now[0]["reminder_id"])
    assert s.due_reminders(now) == []
    # чужой чат не видит и не трогает задачу
    assert s.list_tasks(2) == [] and not s.set_done(2, tid) and not s.delete_task(2, tid)
    assert s.set_done(1, tid)
    assert s.due_reminders(now + timedelta(days=10)) == []
    assert s.list_tasks(1) == [] and len(s.list_tasks(1, include_done=True)) == 1


def test_default_reminders():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
    far = default_reminders(datetime(2026, 10, 31, 23, 59, tzinfo=TZ), now)
    assert far == [datetime(2026, 10, 30, 10, 0, tzinfo=TZ), datetime(2026, 10, 31, 21, 59, tzinfo=TZ)]
    near = default_reminders(now + timedelta(hours=5), now)
    assert near == [now + timedelta(hours=3), now + timedelta(hours=4, minutes=30)]


def test_assistant_tool_loop(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    due = (datetime.now(TZ) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    client = FakeClient([
        resp("tool_use", text("Записываю"),
             tool_use("add_task", {"title": "Сдать лабу", "due": due, "notes": None, "reminders": None})),
        resp("end_turn", text("Записал: сдать лабу.")),
        resp("tool_use", tool_use("list_tasks", {"include_done": False}, "t2")),
        resp("end_turn", text("У тебя одна задача.")),
        resp("tool_use", tool_use("complete_task", {"task_id": 999, "done": True}, "t3")),
        resp("end_turn", text("Не нашёл такую.")),
    ])
    a = Assistant(s, TZ, "claude-opus-5-5", "low", client=client)

    assert asyncio.run(a.reply(42, "сдать лабу через два дня")) == "Записал: сдать лабу."
    tasks = s.list_tasks(42)
    assert [t["title"] for t in tasks] == ["Сдать лабу"]
    # дефолтные напоминания поставлены
    assert len(s.due_reminders(datetime.now(TZ) + timedelta(days=3))) == 2
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and '"ok": true' in tool_result["content"]

    assert asyncio.run(a.reply(42, "что у меня?")) == "У тебя одна задача."
    assert "Сдать лабу" in client.calls[3]["messages"][-1]["content"][0]["content"]
    # история: только текст, парами user/assistant
    assert [m["role"] for m in a.history[42]] == ["user", "assistant", "user", "assistant"]

    assert asyncio.run(a.reply(42, "сделал 999")) == "Не нашёл такую."
    assert '"ok": false' in client.calls[5]["messages"][-1]["content"][0]["content"]


def test_bad_tool_input_reported_as_error(tmp_path):
    s = Storage(str(tmp_path / "db.sqlite"))
    client = FakeClient([
        resp("tool_use", tool_use("add_task", {"title": "x", "due": "послезавтра", "notes": None, "reminders": None})),
        resp("end_turn", text("Уточни дату")),
    ])
    a = Assistant(s, TZ, "claude-opus-5-5", "low", client=client)
    assert asyncio.run(a.reply(1, "x")) == "Уточни дату"
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True
