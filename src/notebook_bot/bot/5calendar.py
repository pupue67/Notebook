from calendar import monthrange
from datetime import date, timedelta

from aiogram import F, Router

from ..logic.rules import RuleError
from .common import HOME, kb, send

router = Router(name="booking_horizon_calendar")
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


def shift_month(year, month, offset):
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


async def show_month(event, app, actor, business_id, year, month):
    async with app.repo.transaction() as tx:
        business = await app.owner(tx, actor, business_id)
    today = app.clock().date()
    last_allowed = today + timedelta(days=180)
    first = date(year, month, 1)
    if first > last_allowed or shift_month(year, month, 1) <= (today.year, today.month):
        raise RuleError("Этот месяц недоступен для настройки записи.")
    rows = [[(name, "hnoop") for name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")]]
    cells = [(" ", "hnoop")] * first.weekday()
    for day_number in range(1, monthrange(year, month)[1] + 1):
        day = date(year, month, day_number)
        if today <= day <= last_allowed:
            mark = " ✓" if business.booking_until == day else ""
            cells.append((f"{day_number}{mark}", f"hend:{business_id}:{day.isoformat()}"))
        else:
            cells.append(("·", "hnoop"))
    cells.extend([(" ", "hnoop")] * (-len(cells) % 7))
    rows.extend(cells[i:i + 7] for i in range(0, len(cells), 7))
    navigation = []
    previous = shift_month(year, month, -1)
    following = shift_month(year, month, 1)
    if previous >= (today.year, today.month):
        navigation.append(("◀", f"hmonth:{business_id}:{previous[0]}:{previous[1]}"))
    if date(*following, 1) <= last_allowed:
        navigation.append(("▶", f"hmonth:{business_id}:{following[0]}:{following[1]}"))
    if navigation:
        rows.append(navigation)
    rows.append([("Убрать конечную дату", f"hclear:{business_id}")])
    rows.append([HOME])
    selected = business.booking_until.strftime("%d.%m.%Y") if business.booking_until else "не задана"
    await send(event,
        f"Последняя дата записи: {selected}\nВыберите последний доступный день на календаре. "
        f"После него новые записи не появятся.\n\n{MONTHS[month - 1].capitalize()} {year}",
        kb(*rows))


@router.callback_query(F.data.startswith("horizon:"))
async def open_calendar(callback, app, actor):
    today = app.clock().date()
    await show_month(callback, app, actor, callback.data.split(":")[1], today.year, today.month)


@router.callback_query(F.data.startswith("hmonth:"))
async def navigate(callback, app, actor):
    _, business_id, year, month = callback.data.split(":")
    await show_month(callback, app, actor, business_id, int(year), int(month))


@router.callback_query(F.data.startswith("hend:"))
async def choose_last_day(callback, app, actor):
    _, business_id, selected = callback.data.split(":")
    business = await app.set_booking_until(actor, business_id, date.fromisoformat(selected))
    await send(callback, f"Клиенты могут выбрать дату до {business.booking_until:%d.%m.%Y} включительно.",
        kb([("Изменить дату", f"horizon:{business_id}")], [HOME]))


@router.callback_query(F.data.startswith("hclear:"))
async def clear_last_day(callback, app, actor):
    business_id = callback.data.split(":")[1]
    business = await app.set_booking_until(actor, business_id, None)
    await send(callback,
        f"Конечная дата убрана. Сейчас действует горизонт записи: {business.horizon_days} дней.",
        kb([("Выбрать дату", f"horizon:{business_id}")], [HOME]))


@router.callback_query(F.data == "hnoop")
async def empty_cell(callback):
    return None
