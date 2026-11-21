from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from notebook_bot.bot.calendar import month_rows, show_master_month
from notebook_bot.bot.schedule import (
    calendar as open_calendar,
    interval_pick,
    save_interval_pair,
    save_bulk_week_intervals,
    apply_bulk_week_intervals,
    choose_weekdays,
    select_weekdays,
    start_interval_form,
    start_interval_picker,
)
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

    async def set_data(self, values):
        self.data = values

    async def set_state(self, _value):
        pass

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

    await start_interval_form(event, state, business.id, f"w{day.weekday()}", "intervals", append=True)
    form_data = await state.get_data()
    assert [field[2] for field in form_data["fields"]] == ["time", "end_time"]
    await save_interval_pair(
        event, app, owner.id, {"start": 720, "end": 780}, form_data["context"]
    )
    rules = await app.schedule(owner.id, business.id)
    assert rules[0].intervals == [Interval(start=600, end=660), Interval(start=720, end=780)]

    event.data = f"calendar:{business.id}"
    await open_calendar(event, app, owner.id)
    calendar_buttons = [button for row in event.messages[-1][1].inline_keyboard for button in row]
    assert any(button.callback_data == f"dayrule:{business.id}:{day.isoformat()}" for button in calendar_buttons)

    await show_master_month(event, app, owner.id, business.id, 2027, 1)
    january_buttons = [button for row in event.messages[-1][1].inline_keyboard for button in row]
    assert any(
        button.text == "1" and button.callback_data == f"dayrule:{business.id}:2027-01-01"
        for button in january_buttons
    )

    statuses = await app.calendar_days(client.id, business.id, day.year, day.month, service.id)
    assert statuses[day] == "free"
    assert statuses[day + timedelta(days=1)] == "off"
    rows = month_rows(day.year, day.month, statuses, lambda value: value.isoformat())
    button = next(button for row in rows for button in row if button[0] == str(day.day))
    assert button == (str(day.day), day.isoformat(), "primary")

    await app.create(client.id, service.id, datetime(2026, 11, 23, 10, tzinfo=timezone))
    # The day remains free while the later interval still has room.
    assert (await app.calendar_days(client.id, business.id, day.year, day.month, service.id))[day] == "free"
    await app.create(client.id, service.id, datetime(2026, 11, 23, 12, tzinfo=timezone))
    statuses = await app.calendar_days(client.id, business.id, day.year, day.month, service.id)
    assert statuses[day] == "busy"
    rows = month_rows(day.year, day.month, statuses, lambda value: value.isoformat())
    button = next(button for row in rows for button in row if button[0] == str(day.day))
    assert button == (str(day.day), day.isoformat(), "danger")


@pytest.mark.asyncio
async def test_one_interval_applies_to_selected_weekdays(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    app = build_app(Settings("test:token", "memory", tmp_path / "unused.json", timezone))
    app.clock = lambda: datetime(2026, 11, 20, 9, tzinfo=timezone)
    owner = await app.user(101, "Мастер")
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    state, event = State(), Event()
    event.data = f"weekselect:{business.id}"
    await select_weekdays(event, state, app, owner.id)
    selection = (await state.get_data())["week_selection"]
    event.data = f"weekpick:{selection['nonce']}:toggle:0"
    await choose_weekdays(event, state, app, owner.id)
    markup = event.messages[-1][1]
    assert any(button.text == "Рабочее время: кнопками" for row in markup.inline_keyboard for button in row)

    context = {"business_id": business.id, "days": [0, 1, 2, 4], "append": False}
    await save_bulk_week_intervals(event, app, owner.id, {"start": 600, "end": 1080}, context)
    rules = {rule.weekday: rule for rule in await app.schedule(owner.id, business.id)}
    assert set(rules) == {0, 1, 2, 4}
    assert all(rule.intervals == [Interval(start=600, end=1080)] for rule in rules.values())
    await apply_bulk_week_intervals(
        event, app, owner.id, business.id, [0, 1, 2, 4],
        [Interval(start=780, end=840)], "break",
    )
    rules = {rule.weekday: rule for rule in await app.schedule(owner.id, business.id)}
    assert all(
        rule.intervals == [Interval(start=600, end=780), Interval(start=840, end=1080)]
        for rule in rules.values()
    )
