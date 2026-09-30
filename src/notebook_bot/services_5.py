from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from .common import HOME, form, one, send, start_form

router = Router(name="services")


@router.callback_query(F.data.startswith("services:"))
async def services(callback, app, actor):
    id = callback.data.split(":")[1]
    async with app.repo.transaction() as tx:
        await app.owner(tx, actor, id)
    items = await app.services(id, actor)
    await send(callback, "Услуги", one(
        *[(f"{'✓' if s.active else '○'} {s.name}", f"service:{s.id}") for s in items],
        ("Добавить услугу", f"serviceadd:{id}"),
        ("Мой бизнес", f"nav:business:{id}"),
        HOME,
    ))


@router.callback_query(F.data.startswith("service:"))
async def service(callback, app, actor):
    s = await app.service(callback.data.split(":")[1])
    async with app.repo.transaction() as tx:
        await app.owner(tx, actor, s.business_id)
    await send(callback,
        f"{s.name}\n{s.price_rub} ₽\nРежим: {'фиксированная' if s.mode == 'fixed' else 'персональная'} длительность\nБазовое время: {s.duration_min} мин\nПредоплата: {s.prepay_rub} ₽\n{'Активна' if s.active else 'Скрыта'}",
        one(
            ("Редактировать", f"serviceedit:{s.id}"),
            ("Скрыть / активировать", f"servicehide:{s.id}"),
            ("Услуги", f"services:{s.business_id}"),
            HOME,
        ))


@router.callback_query(F.data.startswith("servicehide:"))
async def hide(callback, app, actor):
    id = callback.data.split(":")[1]
    await app.toggle_service(actor, id)
    await send(callback, "Видимость услуги изменена.", one(("Открыть услугу", f"service:{id}"), HOME))


@router.callback_query(F.data.startswith("serviceadd:"))
@router.callback_query(F.data.startswith("serviceedit:"))
async def edit(callback, state: FSMContext, app):
    action, id = callback.data.split(":")
    context = {"business_id": id}
    if action == "serviceedit":
        s = await app.service(id)
        context = {"business_id": s.business_id, "id": s.id}
    await start_form(callback, state, "service", [
        ("name", "Название услуги:", "text"),
        ("price", "Цена в рублях (целое число):", "number"),
        ("mode", "Режим длительности — введите:\n1 — фиксированная\n2 — персональная", "number"),
        ("duration", "Длительность в минутах. Для персональной — предварительный резерв нового клиента:", "minutes"),
        ("prepay", "Предоплата в рублях (0 — без предоплаты):", "number"),
    ], context)


@form("service")
async def save(message, app, actor, values, context):
    from ..logic.rules import number
    values["mode"] = ["fixed", "personal"][number(values["mode"], 1, 2) - 1]
    s = await app.save_service(actor, **context, **values)
    await send(message, "Услуга сохранена.", one(
        ("Услуги", f"services:{s.business_id}"),
        ("Настроить неделю", f"week:{s.business_id}"),
        HOME,
    ))
