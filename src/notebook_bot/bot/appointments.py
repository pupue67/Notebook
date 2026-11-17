from datetime import date, datetime, time, timedelta
from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.rules import RuleError, number
from .common import HOME, form, one, send, start_form

router = Router(name="appointments")
STATUSES = {
    "awaiting_duration": "Мастер уточняет длительность",
    "pending_payment": "Ожидается предоплата",
    "booked": "Вы записаны",
    "cancelled_client": "Отменена клиентом",
    "cancelled_master": "Отменена мастером",
    "completed": "Визит состоялся",
    "no_show": "Неявка",
    "expired": "Резерв истёк",
}
async def show_appointment(event, app, actor, id):
    a, b, c, address, comment = await app.get_appointment(actor, id)
    end = a.start + timedelta(minutes=a.duration_min)
    owner = actor == b.owner_id
    message = f"{STATUSES[a.status]}\n\n{b.name}\n{a.service_name}\nКлиент: {c.name}\n{a.start:%d.%m.%Y %H:%M}–{end:%H:%M}\n{a.duration_min} мин · {a.price_rub} ₽\nАдрес: {address or 'уточните у специалиста'}\n{comment}"
    if a.expires_at and a.status in ("awaiting_duration", "pending_payment"):
        message += f"\nРезерв действует до {a.expires_at:%d.%m %H:%M}."
    if a.required_duration:
        message += f"\nНовая длительность {a.required_duration} мин не помещается. Для неё нужен перенос. Текущее время сохранено."
    if a.move_requested:
        message += "\nМастер просит выбрать другую дату и время."
    buttons = []
    if a.status in ("booked", "pending_payment", "awaiting_duration"):
        if a.start > app.clock():
            if owner and c.user_id and c.user_id != actor:
                buttons.append(("Попросить клиента выбрать новое время", f"requestmove:{a.id}"))
            else:
                buttons.append(("Перенести", f"move:{a.id}"))
            if owner:
                buttons.append(("Указать / изменить длительность", f"duration:{a.id}"))
        buttons.append(("Отменить запись", f"cancelask:{a.id}"))
        if a.status == "pending_payment":
            message += f"\nПредоплата: {a.prepay_rub} ₽\n{b.payment_details}\nОплата: {a.payment}"
            if owner:
                buttons.append(("Оплату вижу", f"verify:{a.id}"))
            else:
                buttons.append(("Я оплатил", f"paid:{a.id}"))
        if owner and a.status == "booked" and end <= app.clock():
            buttons += [("Визит состоялся", f"finish:{a.id}:completed"), ("Неявка", f"finish:{a.id}:no_show")]
    buttons.append(("Обновить карточку", f"appt:{a.id}"))
    await send(event, message, one(*buttons, HOME))


@router.callback_query(F.data.startswith("appt:"))
async def detail(callback, state: FSMContext, app, actor):
    await state.clear()
    await show_appointment(callback, app, actor, callback.data.split(":")[1])


async def list_page(event, state, app, actor, business_id=None, day=None, page=0):
    items = await app.appointments(actor, business_id, day)
    # Upcoming records first; history remains accessible on later pages.
    items.sort(
        key=lambda a: (a.start < app.clock(), a.start if a.start >= app.clock() else -a.start.timestamp())
    )
    await state.update_data(list_business=business_id, list_day=day.isoformat() if day else None)
    subset = items[page * 12 : (page + 1) * 12]
    buttons = [
        (f"{a.start:%d.%m %H:%M} · {a.service_name} · {STATUSES[a.status]}", f"appt:{a.id}") for a in subset
    ]
    if page:
        buttons.append(("◀ Назад", f"appspage:{page - 1}"))
    if (page + 1) * 12 < len(items):
        buttons.append(("Далее ▶", f"appspage:{page + 1}"))
    await send(event, "Записи" if items else "Пока нет записей.", one(*buttons, HOME))


@router.callback_query(F.data == "nav:appointments")
@router.callback_query(F.data.startswith("ownerapps:"))
@router.callback_query(F.data.startswith("dayapps:"))
async def list_apps(callback, state: FSMContext, app, actor):
    await state.clear()
    parts = callback.data.split(":")
    b = parts[1] if parts[0] in ("ownerapps", "dayapps") else None
    d = date.fromisoformat(parts[2]) if parts[0] == "dayapps" else None
    await list_page(callback, state, app, actor, b, d)


@router.callback_query(F.data.startswith("appspage:"))
async def apps_page(callback, state: FSMContext, app, actor):
    data = await state.get_data()
    await list_page(
        callback,
        state,
        app,
        actor,
        data.get("list_business"),
        date.fromisoformat(data["list_day"]) if data.get("list_day") else None,
        max(0, int(callback.data.split(":")[1])),
    )


@router.callback_query(F.data.startswith("cancelask:"))
async def cancel_ask(callback, app, actor):
    a, *_ = await app.get_appointment(actor, callback.data.split(":")[1])
    await send(
        callback,
        f"Отменить запись {a.start:%d.%m %H:%M}?",
        one(("Да, отменить", f"cancel:{a.id}:{a.revision}"), ("Оставить запись", f"appt:{a.id}")),
    )


@router.callback_query(F.data.startswith("cancel:"))
async def cancel(callback, app, actor):
    _, id, revision = callback.data.split(":")
    await app.cancel(actor, id, int(revision))
    await show_appointment(callback, app, actor, id)


@router.callback_query(F.data.startswith("requestmove:"))
async def request_move(callback, app, actor):
    id = callback.data.split(":")[1]
    await app.request_move(actor, id)
    await send(
        callback,
        "Клиенту отправлен запрос выбора новой даты и времени. Старая запись сохранена.",
        one(("Запись", f"appt:{id}"), HOME),
    )


@router.callback_query(F.data.startswith("move:"))
async def move(callback, state: FSMContext, app, actor):
    from .booking import dates

    a, b, c, *_ = await app.get_appointment(actor, callback.data.split(":")[1])
    if actor == b.owner_id and c.user_id and c.user_id != actor:
        raise RuleError("Новое время выбирает клиент. Отправьте запрос переноса.")
    await state.clear()
    await state.update_data(service_id=a.service_id, appointment_id=a.id, revision=a.revision)
    await dates(callback, state, app, actor)


@router.callback_query(F.data.startswith("duration:"))
async def duration(callback, state: FSMContext, app, actor):
    a, b, *_ = await app.get_appointment(actor, callback.data.split(":")[1])
    if b.owner_id != actor:
        raise RuleError("Длительность задаёт мастер.")
    await start_form(
        callback,
        state,
        "duration",
        [
            ("minutes", "Сколько минут зарезервировать?", "minutes"),
            (
                "future",
                "Применить:\n1 — только к этой записи\n2 — к этой и использовать при создании следующих записей",
                "number",
            ),
        ],
        {"id": a.id, "revision": a.revision},
    )


@form("duration")
async def save_duration(message, app, actor, values, context):
    a, fits = await app.set_duration(
        actor, context["id"], values["minutes"], number(values["future"], 1, 2) == 2, context["revision"]
    )
    if not fits:
        await send(
            message,
            "Новая длительность не помещается. Она не была урезана; откройте запись и предложите перенос.",
            one(("Открыть запись", f"appt:{a.id}"), HOME),
        )
    else:
        await show_appointment(message, app, actor, a.id)


@router.callback_query(F.data.startswith("paid:"))
@router.callback_query(F.data.startswith("verify:"))
async def payment(callback, app, actor):
    action, id = callback.data.split(":")
    await app.payment(actor, id, action == "verify")
    await show_appointment(callback, app, actor, id)


@router.callback_query(F.data.startswith("finish:"))
async def finish(callback, app, actor):
    _, id, status = callback.data.split(":")
    await app.finish(actor, id, status)
    await show_appointment(callback, app, actor, id)


@router.callback_query(F.data.startswith("clients:"))
async def clients(callback, app, actor):
    id = callback.data.split(":")[1]
    values = await app.clients(actor, id)
    await send(
        callback,
        "Клиенты",
        one(
            *[(c.name, f"client:{id}:{c.id}") for c in values[:30]],
            ("Записать нового клиента", f"manual:{id}:-"),
            HOME,
        ),
    )


@router.callback_query(F.data.startswith("client:"))
async def client(callback, app, actor):
    _, b, id = callback.data.split(":")
    c = next((c for c in await app.clients(actor, b) if c.id == id), None)
    if not c:
        raise RuleError("Клиент не найден.")
    items = [a for a in await app.appointments(actor, b) if a.client_id == id]
    await send(
        callback,
        f"{c.name}\nЗаписей в истории: {len(items)}\n{c.note}",
        one(
            ("Записать клиента вручную", f"manualclient:{b}:{id}"),
            *[(f"{a.start:%d.%m %H:%M} {a.service_name}", f"appt:{a.id}") for a in items[-10:]],
            HOME,
        ),
    )


@router.callback_query(F.data.startswith("manual:"))
@router.callback_query(F.data.startswith("manualclient:"))
async def manual(callback, state: FSMContext, app, actor):
    action, b, extra = callback.data.split(":")
    async with app.repo.transaction() as tx:
        await app.owner(tx, actor, b)
    services = await app.services(b)
    if not services:
        raise RuleError("Сначала добавьте услугу.")
    context = {"business_id": b, "service_ids": [s.id for s in services]}
    fields = []
    if action == "manualclient":
        context["client_id"] = extra
    else:
        fields.append(
            ("client_name", "Имя нового клиента (для существующего откройте его карточку):", "text")
        )
    fields.append(
        (
            "service",
            "Номер услуги:\n" + "\n".join(f"{i + 1}. {s.name}" for i, s in enumerate(services)),
            "number",
        )
    )
    if action == "manual" and extra != "-":
        context["day"] = extra
    else:
        fields.append(("day", "Дата (ДД.ММ.ГГГГ):", "date"))
    fields += [("minute", "Время начала (ЧЧ:ММ):", "time"), ("duration", "Длительность (минуты):", "minutes")]
    await start_form(callback, state, "manual", fields, context)


@form("manual")
async def save_manual(message, app, actor, values, context):
    index = number(values["service"], 1, len(context["service_ids"])) - 1
    minute = values["minute"]
    stamp = datetime.combine(
        date.fromisoformat(context.get("day") or values["day"]),
        time(minute // 60, minute % 60),
        tzinfo=app.timezone,
    )
    a = await app.create(
        actor,
        context["service_ids"][index],
        stamp,
        client_id=context.get("client_id"),
        client_name=values.get("client_name"),
        duration=values["duration"],
    )
    await show_appointment(message, app, actor, a.id)

