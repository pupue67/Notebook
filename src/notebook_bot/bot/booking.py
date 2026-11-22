from datetime import date, datetime
from secrets import token_hex

from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.availability import duration_for, visit_context
from ..logic.rules import RuleError
from .calendar import month_rows, shift_month
from .common import HOME, kb, one, send

router = Router(name="booking")


@router.callback_query(F.data.startswith("choose:"))
async def choose(callback, state: FSMContext, app):
    await state.clear()
    id = callback.data.split(":")[1]
    services = await app.services(id)
    await send(
        callback,
        "Выберите услугу:" if services else "Специалист пока не добавил услуги.",
        one(*[(f"{s.name} · {s.price_rub} ₽", f"bookservice:{s.id}") for s in services], HOME),
    )


@router.callback_query(F.data.startswith("bookservice:"))
async def start(callback, state: FSMContext, app, actor):
    await state.clear()
    await state.update_data(service_id=callback.data.split(":")[1])
    await dates(callback, state, app, actor)


async def dates(event, state, app, actor, page=0):
    data = await state.get_data()
    choices = await app.dates(actor, data["service_id"], appointment_id=data.get("appointment_id"))
    await state.update_data(
        choices=[x.isoformat() for x in choices], view="dates", nonce=token_hex(3), page=page
    )
    await render_choices(event, state, app, actor)


async def render_choices(event, state, app, actor):
    data = await state.get_data()
    choices, page, view, nonce = data["choices"], data["page"], data["view"], data["nonce"]
    if view == "dates":
        today = app.clock().date()
        year, month = shift_month(today.year, today.month, page)
        service = await app.service(data["service_id"])
        statuses = await app.calendar_days(
            actor, service.business_id, year, month, service.id, data.get("appointment_id")
        )
        indices = {date.fromisoformat(value): index for index, value in enumerate(choices)}
        statuses = {
            day: (status if day in indices or status != "free" else "busy")
            for day, status in statuses.items()
        }
        rows = month_rows(
            year, month, statuses,
            lambda day: (
                f"pick:{nonce}:select:{indices[day]}"
                if day in indices and statuses[day] == "free" else "hnoop"
            ),
        )
        last = date.fromisoformat(choices[-1]) if choices else today
        max_page = (last.year - today.year) * 12 + last.month - today.month
        nav = []
        if page:
            nav.append(("Назад", f"pick:{nonce}:page:{page - 1}"))
        if page < max_page:
            nav.append(("Далее", f"pick:{nonce}:page:{page + 1}"))
        if nav:
            rows.append(nav)
    else:
        size = 24
        if page * size >= len(choices):
            page = 0
            await state.update_data(page=0)
        items = [
            (datetime.fromisoformat(choices[index]).strftime("%H:%M"), f"pick:{nonce}:select:{index}")
            for index in range(page * size, min((page + 1) * size, len(choices)))
        ]
        rows = [items[index:index + 3] for index in range(0, len(items), 3)]
        nav = []
        if page:
            nav.append(("Назад", f"pick:{nonce}:page:{page - 1}"))
        if (page + 1) * size < len(choices):
            nav.append(("Далее", f"pick:{nonce}:page:{page + 1}"))
        if nav:
            rows.append(nav)
    if view == "times":
        rows.append([("Другая дата", f"pick:{nonce}:dates:0")])
    rows.append([HOME])
    message = (
        f"{month:02d}.{year}: синий — есть время, красный — мест нет, обычный — выходной. Выберите дату:"
        if view == "dates"
        else f"{date.fromisoformat(data['day']):%d.%m.%Y} — выберите время:"
    )
    if data.get("appointment_id") and view == "times":
        message += "\nНажатие на время сразу применит перенос."
    if not choices:
        message = "Сейчас нет свободного времени. Попробуйте позже или свяжитесь со специалистом."
    await send(event, message, kb(*rows))


@router.callback_query(F.data.startswith("pick:"))
async def pick(callback, state: FSMContext, app, actor):
    _, nonce, action, value = callback.data.split(":")
    data = await state.get_data()
    if data.get("nonce") != nonce:
        raise RuleError("Этот список устарел. Откройте запись или услугу заново.")
    if action == "dates":
        await dates(callback, state, app, actor)
        return
    if action == "page":
        await state.update_data(page=max(0, int(value)))
        await render_choices(callback, state, app, actor)
        return
    if action == "confirm":
        if data.get("view") != "confirm":
            raise RuleError("Выбор устарел.")
        a = await app.create(actor, data["service_id"], datetime.fromisoformat(data["selected_start"]))
        await state.clear()
        from .appointments import show_appointment

        await show_appointment(callback, app, actor, a.id)
        return
    index = int(value)
    if not 0 <= index < len(data["choices"]):
        raise RuleError("Выбор устарел.")
    chosen = data["choices"][index]
    if data["view"] == "dates":
        choices = await app.times(
            actor, data["service_id"], date.fromisoformat(chosen), data.get("appointment_id")
        )
        await state.update_data(
            day=chosen, choices=[x.isoformat() for x in choices], view="times", page=0, nonce=token_hex(3)
        )
        await render_choices(callback, state, app, actor)
        return
    if data["view"] != "times":
        raise RuleError("Выбор устарел.")
    stamp = datetime.fromisoformat(chosen)
    if data.get("appointment_id"):
        a = await app.move(actor, data["appointment_id"], stamp, data["revision"])
        await state.clear()
        from .appointments import show_appointment

        await show_appointment(callback, app, actor, a.id)
        return
    s = await app.service(data["service_id"])
    b = await app.business(s.business_id)
    async with app.repo.transaction() as tx:
        from ..models import Client

        clients = await tx.list(Client, business_id=b.id, user_id=actor)
        duration, known = await duration_for(tx, s, clients[0].id if clients else None)
        address, comment = await visit_context(tx, b, stamp)
    nonce = token_hex(3)
    await state.update_data(view="confirm", nonce=nonce, selected_start=chosen)
    message = f"{s.name}\n{stamp:%d.%m.%Y %H:%M}\n{s.price_rub} ₽\nДлительность: {str(duration) + ' мин' if known else 'уточнит мастер после выбора'}\nАдрес: {address or 'уточните у специалиста'}\n{comment}\n\nОтмена самостоятельно: за {b.cancel_notice_min} мин.\nПеренос самостоятельно: за {b.move_notice_min} мин."
    if not known:
        message += f"\nПредварительно резервируется {duration} минут на 30 минут ожидания ответа мастера."
    if s.prepay_rub:
        message += f"\nПредоплата {s.prepay_rub} ₽. После уточнения длительности: {b.payment_details}\nРезерв на проверку оплаты — 30 минут."
    await send(
        callback,
        message,
        one(
            ("Создать запись" if known else "Отправить запрос мастеру", f"pick:{nonce}:confirm:0"),
            ("Другая дата", f"pick:{nonce}:dates:0"),
            HOME,
        ),
    )
