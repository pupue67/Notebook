"""Сквозной сценарий задач 3–6 с настоящим JSON-хранилищем."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from notebook_bot.config import Settings
from notebook_bot.bot.notifications import deliver
from notebook_bot.logic.rules import RuleError
from notebook_bot.main import build_app, build_dispatcher, process_lock
from notebook_bot.models import Appointment, History, Interval, Notification


@pytest.mark.asyncio
async def test_client_booking_survives_restart_and_releases_slot(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 11, 20, 9, 0, tzinfo=timezone)
    settings = Settings("test:token", "json", tmp_path / "notebook.json", timezone)
    app = build_app(settings)
    app.clock = lambda: now
    dispatcher = build_dispatcher(app)
    assert "booking" in [router.name for router in dispatcher.sub_routers]
    assert "appointments" in [router.name for router in dispatcher.sub_routers]

    owner = await app.user(101, "Мастер")
    client = await app.user(102, "Клиент")
    second = await app.user(103, "Другой клиент")
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    service = await app.save_service(owner.id, business.id, "Стрижка", 1000, "fixed", 60)
    day = now.date() + timedelta(days=3)
    await app.edit_schedule(owner.id, business.id, day.weekday(), "intervals", [Interval(start=600, end=780)])
    start = next(slot for slot in await app.times(client.id, service.id, day) if slot.hour == 10)

    appointment = await app.create(client.id, service.id, start)
    assert (await app.create(client.id, service.id, start)).id == appointment.id
    with pytest.raises(RuleError, match="недоступно"):
        await app.create(second.id, service.id, start)

    reopened = build_app(settings)
    reopened.clock = lambda: now
    assert len(await reopened.appointments(client.id)) == 1
    assert (await reopened.get_appointment(client.id, appointment.id))[0].status == "booked"
    assert len(await reopened.clients(owner.id, business.id)) == 1
    assert settings.data_file.exists()

    moved_start = next(slot for slot in await reopened.times(client.id, service.id, day, appointment.id) if slot.hour == 11)
    moved = await reopened.move(client.id, appointment.id, moved_start, appointment.revision)
    with pytest.raises(RuleError, match="устарела"):
        await reopened.move(client.id, appointment.id, start, appointment.revision)
    cancelled = await reopened.cancel(client.id, moved.id, moved.revision)
    assert cancelled.status == "cancelled_client"
    assert start in await reopened.times(second.id, service.id, day)

    again = build_app(settings)
    again.clock = lambda: now
    async with again.repo.transaction() as tx:
        assert len(await tx.list(Appointment, business_id=business.id)) == 1
        assert [row.action for row in await tx.list(History, appointment_id=appointment.id)] == [
            "create", "move", "cancel"
        ]
        assert len(await tx.list(Notification, appointment_id=appointment.id)) == 6

    class FakeBot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, message, **kwargs):
            self.messages.append((chat_id, message))

    bot = FakeBot()
    await deliver(bot, again)
    assert len(bot.messages) == 6
    await deliver(bot, build_app(settings))
    assert len(bot.messages) == 6


@pytest.mark.asyncio
async def test_memory_mode_does_not_write_json(tmp_path):
    settings = Settings("test:token", "memory", tmp_path / "notebook.json", ZoneInfo("Asia/Yekaterinburg"))
    app = build_app(settings)
    await app.user(101, "Мастер")
    assert not settings.data_file.exists()


@pytest.mark.asyncio
async def test_date_limit_blocks_fifth_client_with_free_work_hours(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 11, 20, 9, tzinfo=timezone)
    settings = Settings("test:token", "json", tmp_path / "notebook.json", timezone)
    app = build_app(settings)
    app.clock = lambda: now
    owner = await app.user(201, "Мастер")
    clients = [await app.user(202 + index, f"Клиент {index}") for index in range(5)]
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    service = await app.save_service(owner.id, business.id, "Услуга", 1000, "fixed", 60)
    day = now.date() + timedelta(days=3)
    await app.edit_schedule(owner.id, business.id, day.weekday(), "intervals", [Interval(start=600, end=1080)])
    await app.edit_schedule(owner.id, business.id, day, "limit", "4")

    for client, hour in zip(clients[:4], range(10, 14)):
        await app.create(client.id, service.id, datetime(2026, 11, 23, hour, tzinfo=timezone))
    assert (await app.schedule(owner.id, business.id, day))[2] == 4
    assert await app.times(clients[4].id, service.id, day) == []
    assert (await app.calendar_days(clients[4].id, business.id, day.year, day.month, service.id))[day] == "busy"
    with pytest.raises(RuleError, match="недоступно"):
        await app.create(clients[4].id, service.id, datetime(2026, 11, 23, 14, tzinfo=timezone))

    reopened = build_app(settings)
    reopened.clock = lambda: now
    assert (await reopened.schedule(owner.id, business.id, day))[2] == 4
    assert any(slot.hour == 14 for slot in await reopened.times(clients[4].id, service.id, day + timedelta(days=7)))


@pytest.mark.asyncio
async def test_date_change_reports_existing_booking_and_reset_restores_week(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 11, 20, 9, tzinfo=timezone)
    settings = Settings("test:token", "json", tmp_path / "notebook.json", timezone)
    app = build_app(settings)
    app.clock = lambda: now
    owner = await app.user(301, "Мастер")
    client = await app.user(302, "Клиент")
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    service = await app.save_service(owner.id, business.id, "Услуга", 1000, "fixed", 60)
    day = now.date() + timedelta(days=3)
    await app.edit_schedule(owner.id, business.id, day.weekday(), "intervals", [Interval(start=600, end=780)])
    appointment = await app.create(client.id, service.id, datetime(2026, 11, 23, 10, tzinfo=timezone))

    conflicts = await app.edit_schedule(owner.id, business.id, day, "intervals", [Interval(start=720, end=780)])
    assert [item.id for item in conflicts] == [appointment.id]
    assert (await app.get_appointment(client.id, appointment.id))[0].status == "booked"
    assert (await app.schedule(owner.id, business.id, day))[0] == [Interval(start=720, end=780)]
    assert (await app.schedule(owner.id, business.id, day + timedelta(days=7)))[0] == [
        Interval(start=600, end=780)
    ]

    await app.edit_schedule(owner.id, business.id, day, "reset", None)
    reopened = build_app(settings)
    reopened.clock = lambda: now
    assert (await reopened.schedule(owner.id, business.id, day))[0] == [Interval(start=600, end=780)]
    assert (await reopened.get_appointment(client.id, appointment.id))[0].status == "booked"


@pytest.mark.asyncio
async def test_prepayment_expiry_and_reminders_survive_json_restart(tmp_path):
    timezone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 11, 20, 9, tzinfo=timezone)
    settings = Settings("test:token", "json", tmp_path / "notebook.json", timezone)
    app = build_app(settings)
    app.clock = lambda: now
    owner = await app.user(401, "Мастер")
    client = await app.user(402, "Клиент")
    second = await app.user(403, "Другой клиент")
    business = await app.create_business(owner.id, "Студия", "Мастер", "Услуги", "at_master", "Адрес", "")
    await app.update_business(owner.id, business.id, "payment_details", "Перевод мастеру")
    service = await app.save_service(owner.id, business.id, "Услуга", 1000, "fixed", 60, prepay=200)
    day = now.date() + timedelta(days=3)
    await app.edit_schedule(owner.id, business.id, day.weekday(), "intervals", [Interval(start=600, end=780)])

    confirmed = await app.create(client.id, service.id, datetime(2026, 11, 23, 10, tzinfo=timezone))
    assert confirmed.status == "pending_payment"
    await app.payment(client.id, confirmed.id)
    assert (await app.get_appointment(client.id, confirmed.id))[0].payment == "reported"
    await app.payment(owner.id, confirmed.id, verify=True)
    assert (await app.get_appointment(client.id, confirmed.id))[0].status == "booked"

    expired = await app.create(second.id, service.id, datetime(2026, 11, 23, 11, tzinfo=timezone))
    app.clock = lambda: now + timedelta(minutes=31)
    await app.tick()
    assert (await app.get_appointment(second.id, expired.id))[0].status == "expired"
    assert any(slot.hour == 11 for slot in await app.times(second.id, service.id, day))

    reopened = build_app(settings)
    reopened.clock = lambda: datetime(2026, 11, 22, 10, tzinfo=timezone)
    await reopened.tick()
    reopened.clock = lambda: datetime(2026, 11, 23, 8, tzinfo=timezone)
    await reopened.tick()
    await reopened.tick()
    saved = (await reopened.get_appointment(client.id, confirmed.id))[0]
    assert saved.reminders_sent == ["day", "two_hours"]
    async with reopened.repo.transaction() as tx:
        notifications = await tx.list(Notification, appointment_id=confirmed.id)
    reminders = [item for item in notifications if "Напоминание" in item.text]
    assert len(reminders) == 4
    assert {item.user_id for item in reminders} == {owner.id, client.id}


def test_second_process_cannot_lock_same_file(tmp_path):
    path = tmp_path / "notebook.json.lock"
    with process_lock(path):
        with pytest.raises(RuntimeError, match="уже запущен"):
            with process_lock(path):
                pass
