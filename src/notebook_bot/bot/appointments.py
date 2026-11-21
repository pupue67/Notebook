from datetime import date, datetime, timedelta
from secrets import token_hex
from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.availability import occupies
from ..logic.rules import RuleError, number
from .calendar import month_rows, shift_month
from .common import HOME, form, kb, one, send, start_form

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
    await app.owned_business(actor, b)
    await state.clear()
    selection = {"business_id": b, "page": 0}
    if action == "manualclient":
        if not any(client.id == extra for client in await app.clients(actor, b)):
            raise RuleError("Клиент не найден.")
        selection["client_id"] = extra
    if action == "manual" and extra != "-":
        selection["day"] = date.fromisoformat(extra).isoformat()
    await state.update_data(manual=selection)
    await show_manual_services(callback, state, app)


async def show_manual_services(event, state, app):
    selection = (await state.get_data())["manual"]
    services = await app.services(selection["business_id"])
    if not services:
        raise RuleError("Сначала добавьте услугу.")
    nonce = token_hex(3)
    selection.update(nonce=nonce, service_ids=[service.id for service in services])
    await state.update_data(manual=selection)
    await send(event, "Ручная запись: выберите услугу.", one(
        *[(f"{service.name} · {service.duration_min} мин", f"manualpick:{nonce}:service:{index}")
          for index, service in enumerate(services)], HOME,
    ))


async def show_manual_duration(event, state, app):
    selection = (await state.get_data())["manual"]
    service = await app.service(selection["service_id"])
    nonce = token_hex(3)
    selection["nonce"] = nonce
    await state.update_data(manual=selection)
    options = sorted({service.duration_min, 30, 45, 60, 90, 120, 180})
    await send(event, f"{service.name}: выберите длительность. После записи её можно изменить в карточке.",
               kb(*[[(f"{minutes} мин", f"manualpick:{nonce}:duration:{minutes}")
                     for minutes in options[index:index + 3]]
                    for index in range(0, len(options), 3)], [HOME]))


async def show_manual_calendar(event, state, app, actor):
    selection = (await state.get_data())["manual"]
    business = await app.owned_business(actor, selection["business_id"])
    today = app.clock().date()
    page = selection.get("page", 0)
    year, month = shift_month(today.year, today.month, page)
    statuses = await app.calendar_days(actor, business.id, year, month,
                                       selection["service_id"], duration=selection["duration"])
    nonce = token_hex(3)
    selection["nonce"] = nonce
    await state.update_data(manual=selection)
    rows = month_rows(year, month, statuses,
                      lambda day: f"manualpick:{nonce}:day:{day.isoformat()}"
                      if statuses[day] == "free" else "hnoop")
    last = min(today + timedelta(days=business.horizon_days), today + timedelta(days=180))
    if business.booking_until:
        last = min(last, business.booking_until)
    navigation = []
    if page:
        navigation.append(("◀", f"manualpick:{nonce}:page:{page - 1}"))
    if date(*shift_month(year, month, 1), 1) <= last:
        navigation.append(("▶", f"manualpick:{nonce}:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([HOME])
    await send(event, f"Ручная запись · {month:02d}.{year}\n"
               "Синий — есть свободное время; красный — мест нет. Выберите дату:", kb(*rows))


async def show_manual_times(event, state, app, actor):
    selection = (await state.get_data())["manual"]
    day = date.fromisoformat(selection["day"])
    slots = await app.times(actor, selection["service_id"], day, duration=selection["duration"])
    appointments = [item for item in await app.appointments(actor, selection["business_id"], day)
                    if occupies(item, app.clock())]
    clients = {client.id: client.name for client in await app.clients(actor, selection["business_id"])}
    nonce = token_hex(3)
    selection.update(nonce=nonce, slots=[slot.isoformat() for slot in slots])
    await state.update_data(manual=selection)
    page = selection.get("time_page", 0)
    items = [(f"{slot:%H:%M}", f"manualpick:{nonce}:time:{index}")
             for index, slot in enumerate(slots)
             if page * 24 <= index < (page + 1) * 24]
    rows = [items[index:index + 3] for index in range(0, len(items), 3)]
    navigation = []
    if page:
        navigation.append(("◀", f"manualpick:{nonce}:times:{page - 1}"))
    if (page + 1) * 24 < len(slots):
        navigation.append(("▶", f"manualpick:{nonce}:times:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([("Посмотреть записи этой даты", f"dayapps:{selection['business_id']}:{day.isoformat()}")])
    rows.append([("Другая дата", f"manualpick:{nonce}:calendar:0")])
    summary = "\n".join(
        f"{item.start:%H:%M} {clients.get(item.client_id, 'Клиент')} · {item.service_name}"
        for item in appointments[:10]
    ) or "нет"
    availability = "Выберите время:" if slots else "Свободного времени нет. Выберите другую дату."
    await send(event, f"{day:%d.%m.%Y} · уже записаны:\n{summary}\n\n"
               f"Свободное время для {selection['duration']} мин. {availability}", kb(*rows))


@router.callback_query(F.data.startswith("manualpick:"))
async def manual_pick(callback, state: FSMContext, app, actor):
    _, nonce, action, value = callback.data.split(":")
    selection = (await state.get_data()).get("manual")
    if not selection or selection.get("nonce") != nonce:
        raise RuleError("Выбор устарел. Откройте ручную запись заново.")
    await app.owned_business(actor, selection["business_id"])
    if action == "service":
        index = int(value)
        if not 0 <= index < len(selection["service_ids"]):
            raise RuleError("Услуга недоступна.")
        selection["service_id"] = selection["service_ids"][index]
        await state.update_data(manual=selection)
        await show_manual_duration(callback, state, app)
    elif action == "duration":
        selection["duration"] = number(value, 1, 1440)
        await state.update_data(manual=selection)
        if selection.get("day"):
            await show_manual_times(callback, state, app, actor)
        else:
            await show_manual_calendar(callback, state, app, actor)
    elif action in ("page", "calendar"):
        selection["page"] = max(0, int(value))
        selection.pop("day", None)
        await state.update_data(manual=selection)
        await show_manual_calendar(callback, state, app, actor)
    elif action == "day":
        selection.update(day=date.fromisoformat(value).isoformat(), time_page=0)
        await state.update_data(manual=selection)
        await show_manual_times(callback, state, app, actor)
    elif action == "times":
        selection["time_page"] = max(0, int(value))
        await state.update_data(manual=selection)
        await show_manual_times(callback, state, app, actor)
    elif action == "time":
        index = int(value)
        if not 0 <= index < len(selection["slots"]):
            raise RuleError("Время устарело. Выберите дату заново.")
        selection["start"] = selection["slots"][index]
        new_nonce = token_hex(3)
        selection["nonce"] = new_nonce
        await state.update_data(manual=selection)
        service = await app.service(selection["service_id"])
        await send(callback, f"Ручная запись\n{service.name}\n"
                   f"{datetime.fromisoformat(selection['start']):%d.%m.%Y %H:%M} · "
                   f"{selection['duration']} мин\nВремя проверится ещё раз при сохранении.",
                   one(("Сохранить запись", f"manualpick:{new_nonce}:save:0"),
                       ("Другое время", f"manualpick:{new_nonce}:back:0")))
    elif action == "back":
        await show_manual_times(callback, state, app, actor)
    elif action == "save":
        if selection.get("client_id"):
            appointment = await app.create(actor, selection["service_id"],
                                           datetime.fromisoformat(selection["start"]),
                                           client_id=selection["client_id"], duration=selection["duration"])
            await state.clear()
            await show_appointment(callback, app, actor, appointment.id)
        else:
            await start_form(callback, state, "manual_name",
                             [("client_name", "Имя клиента:", "text")], selection)
    else:
        raise RuleError("Выбор устарел.")


@form("manual_name")
async def save_manual_name(message, app, actor, values, context):
    a = await app.create(
        actor,
        context["service_id"],
        datetime.fromisoformat(context["start"]),
        client_id=context.get("client_id"),
        client_name=values["client_name"],
        duration=context["duration"],
    )
    await show_appointment(message, app, actor, a.id)

