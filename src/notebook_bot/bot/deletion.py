"""Удаление бизнеса или услуги и обработка оставшихся записей."""

from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.availability import occupies
from ..logic.rules import RuleError
from .common import HOME, kb, one, send

router = Router(name="deletion")


def back_button(kind, id):
    return ("Продолжить удаление", f"del:{kind}:{id}:0")


async def show_deletion(event, app, actor, kind, id, page=0):
    is_business = kind == "b"
    item = await (app.owned_business(actor, id) if is_business else app.owned_service(actor, id))
    records = await app.deletion_appointments(actor, "business" if is_business else "service", id)
    back = ("Отказаться от удаления", f"settings:{id}" if is_business else f"service:{id}")
    buttons = []
    if records:
        page = min(page, (len(records) - 1) // 10)
        buttons.extend(
            (f"{a.start:%d.%m %H:%M} · {a.service_name}", f"delvisit:{kind}:{a.id}")
            for a in records[page * 10:(page + 1) * 10]
        )
        if page:
            buttons.append(("Предыдущие записи", f"del:{kind}:{id}:{page - 1}"))
        if (page + 1) * 10 < len(records):
            buttons.append(("Следующие записи", f"del:{kind}:{id}:{page + 1}"))
        message = (f"«{item.name}». Действующих записей: {len(records)}. Откройте запись, "
                   "чтобы отменить визит или выбрать другую услугу.")
        if not is_business:
            buttons.append(("Удалить услугу и сохранить визиты", f"delok:s:{id}:keep", "danger"))
    else:
        if is_business:
            message = (f"Удалить бизнес «{item.name}»? Его услуги, расписание, карточки клиентов "
                       "и история записей будут удалены без возможности восстановления.")
        else:
            message = f"Удалить услугу «{item.name}»? Новые записи на неё станут недоступны."
        buttons.append(("Да, удалить", f"delok:{kind}:{id}:normal", "danger"))
    await send(event, message, one(*buttons, back))


@router.callback_query(F.data.startswith("businessdeleteask:"))
@router.callback_query(F.data.startswith("servicedeleteask:"))
async def begin(callback, state: FSMContext, app, actor):
    action, id = callback.data.split(":")
    await state.clear()
    await show_deletion(callback, app, actor, "b" if action == "businessdeleteask" else "s", id)


@router.callback_query(F.data.startswith("del:"))
async def page(callback, app, actor):
    _, kind, id, index = callback.data.split(":")
    await show_deletion(callback, app, actor, kind, id, max(0, int(index)))


@router.callback_query(F.data.startswith("delok:"))
async def confirm(callback, state: FSMContext, app, actor):
    _, kind, id, mode = callback.data.split(":")
    if kind == "b":
        await app.delete_business(actor, id)
        await send(callback, "Бизнес удалён.", one(HOME))
    else:
        service = await app.delete_service(actor, id, keep_visits=mode == "keep")
        await send(callback, "Услуга удалена. Существующие визиты сохранены.",
                   one(("Услуги", f"services:{service.business_id}"), HOME))
    await state.clear()


async def owned_visit(app, actor, id):
    appointment, business, client, *_ = await app.get_appointment(actor, id)
    if business.owner_id != actor:
        raise RuleError("Действие доступно только владельцу бизнеса.")
    return appointment, business, client


@router.callback_query(F.data.startswith("delvisit:"))
async def visit(callback, app, actor):
    _, kind, id = callback.data.split(":")
    a, _, client = await owned_visit(app, actor, id)
    buttons = []
    now = app.clock()
    if occupies(a, now):
        buttons.append(("Отменить визит", f"delcancel:{kind}:{id}:{a.revision}:ask"))
        if a.start > now:
            buttons.append(("Перезаписать на другую услугу", f"delservices:{kind}:{id}"))
    await send(callback, f"{client.name}\n{a.service_name}\n{a.start:%d.%m.%Y %H:%M}",
               one(*buttons, back_button(kind, a.business_id if kind == "b" else a.service_id)))


@router.callback_query(F.data.startswith("delcancel:"))
async def cancel_visit(callback, app, actor):
    _, kind, id, revision, action = callback.data.split(":")
    a, _, _ = await owned_visit(app, actor, id)
    if action == "ask":
        await send(callback, f"Отменить визит {a.start:%d.%m.%Y в %H:%M}?",
                   one(("Да, отменить визит", f"delcancel:{kind}:{id}:{revision}:yes", "danger"),
                       ("Оставить визит", f"delvisit:{kind}:{id}")))
        return
    await app.cancel(actor, id, int(revision))
    await show_deletion(callback, app, actor, kind, a.business_id if kind == "b" else a.service_id)


@router.callback_query(F.data.startswith("delservices:"))
async def replacements(callback, app, actor):
    _, kind, id = callback.data.split(":")
    a, source, _ = await owned_visit(app, actor, id)
    if kind == "b":
        menu = await app.get_main_menu_data(callback.from_user.id)
        businesses = [business for business in menu.businesses if business.id != source.id]
    else:
        businesses = [source]
    buttons = []
    for business in businesses:
        buttons.extend(
            (f"{business.name} · {service.name}", f"delslots:{kind}:{id}:{service.id}:0")
            for service in await app.services(business.id)
            if service.id != a.service_id
        )
    await send(callback, "Выберите услугу для переноса." if buttons else "Других активных услуг нет.",
               one(*buttons, ("К записи", f"delvisit:{kind}:{id}")))


@router.callback_query(F.data.startswith("delslots:"))
async def slots(callback, app, actor):
    _, kind, id, service_id, index = callback.data.split(":")
    a, _, _ = await owned_visit(app, actor, id)
    back = ("Выбрать другую услугу", f"delservices:{kind}:{id}")
    try:
        service, business, duration, times = await app.service_transfer_times(actor, id, service_id)
    except RuleError as error:
        await send(callback, str(error), one(back))
        return
    if kind == "b" and business.id == a.business_id:
        raise RuleError("Выберите услугу в другом бизнесе.")
    page = max(0, int(index))
    options = [
        (f"{stamp:%H:%M}", f"delmove:{kind}:{id}:{service_id}:{stamp.hour * 60 + stamp.minute}:{a.revision}")
        for stamp in times[page * 24:(page + 1) * 24]
    ]
    rows = [options[i:i + 3] for i in range(0, len(options), 3)]
    if page:
        rows.append([("Назад", f"delslots:{kind}:{id}:{service_id}:{page - 1}")])
    if (page + 1) * 24 < len(times):
        rows.append([("Далее", f"delslots:{kind}:{id}:{service_id}:{page + 1}")])
    prompt = ("Нажатие на время перенесёт визит. Клиент получит уведомление в боте, "
              "если у него есть Telegram." if times else "На эту дату свободного времени нет.")
    await send(callback, f"{business.name} · {service.name}\n{a.start:%d.%m.%Y}, {duration} мин\n"
               f"Стоимость: {service.price_rub} ₽\n{prompt}", kb(*rows, [back]))


@router.callback_query(F.data.startswith("delmove:"))
async def transfer(callback, app, actor):
    _, kind, id, service_id, minute, revision = callback.data.split(":")
    a, _, _ = await owned_visit(app, actor, id)
    start = a.start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=int(minute))
    try:
        await app.transfer_service(actor, id, service_id, start, int(revision))
    except RuleError as error:
        await send(callback, str(error), one(("Обновить время", f"delslots:{kind}:{id}:{service_id}:0")))
        return
    await show_deletion(callback, app, actor, kind, a.business_id if kind == "b" else a.service_id)
