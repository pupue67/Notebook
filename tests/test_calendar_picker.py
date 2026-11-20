from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from notebook_bot.bot.calendar import month_rows
from notebook_bot.bot.schedule import interval_pick, start_interval_picker
from notebook_bot.config import Settings
from notebook_bot.main import build_app
from notebook_bot.models import Interval


class State:
    def __init__(self):
        self.data = {}

    async def clear(self):
        self.data = {}

    async def update_data(self, **values):
        self.data.update(values)

    async def get_data(self):
        return self.data


class Event:
    def __init__(self):
        self.data = ""
        self.messages = []

    async def answer(self, text, **kwargs):
        self.messages.append((text, kwargs.get("reply_markup")))


@pytest.mark.asyncio
async def test_coloured_calendar_and_interval_buttons(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 11, 20, 9, tzinfo=timezone)
    app = build_app(Settings("test:token", "memory", tmp_path / "unused.json", timezone))
    app.clock = lambda: now
    owner = await app.user(101, "Мастер")
    client = await app.user(102, "Клиент")
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    service = await app.save_service(owner.id, business.id, "Услуга", 1000, "fixed", 60)
    day = now.date() + timedelta(days=3)

    state, event = State(), Event()
    await start_interval_picker(event, state, app, owner.id, business.id, f"w{day.weekday()}", "intervals")
    for value in (10, 0, 11, 0):
        picker = (await state.get_data())["picker"]
        event.data = f"ipick:{picker['nonce']}:value:{value}"
        await interval_pick(event, state, app, owner.id)
    picker = (await state.get_data())["picker"]
    event.data = f"ipick:{picker['nonce']}:save"
    await interval_pick(event, state, app, owner.id)
    rules = await app.schedule(owner.id, business.id)
    assert rules[0].intervals == [Interval(start=600, end=660)]

    statuses = await app.calendar_days(client.id, business.id, day.year, day.month, service.id)
    assert statuses[day] == "free"
    assert statuses[day + timedelta(days=1)] == "off"
    rows = month_rows(day.year, day.month, statuses, lambda value: value.isoformat())
    button = next(button for row in rows for button in row if button[0] == str(day.day))
    assert button == (str(day.day), day.isoformat(), "primary")

    await app.create(client.id, service.id, datetime(2026, 11, 23, 10, tzinfo=timezone))
    statuses = await app.calendar_days(client.id, business.id, day.year, day.month, service.id)
    assert statuses[day] == "busy"
    rows = month_rows(day.year, day.month, statuses, lambda value: value.isoformat())
    button = next(button for row in rows for button in row if button[0] == str(day.day))
    assert button == (str(day.day), day.isoformat(), "danger")
