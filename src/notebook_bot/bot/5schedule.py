from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.rules import RuleError, hhmm
from ..models import Interval
from .common import HOME, form, one, send, start_form

router = Router(name="schedule")
DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def render_intervals(items):
    return ", ".join(f"{hhmm(i.start)}–{hhmm(i.end)}" for i in items) or "Нет рабочих интервалов"


@router.callback_query(F.data.startswith("week:"))
async def week(callback, app, actor):
    id = callback.data.split(":")[1]
    rules = {r.weekday: r for r in await app.schedule(actor, id)}
    await send(callback,
        "Рабочая неделя\nИзменения здесь относятся к выбранному дню недели.\n\n"
        + "\n".join(f"{name}: {render_intervals(rules[i].intervals) if i in rules else 'Выходной'}"
                    for i, name in enumerate(DAYS)),
        one(
            *[(name, f"dayrule:{id}:w{i}") for i, name in enumerate(DAYS)],
            ("Последняя дата записи", f"horizon:{id}"),
            HOME,
        ))


@router.callback_query(F.data.startswith("calendar:"))
async def calendar(callback, state: FSMContext):
    await start_form(callback, state, "calendar",
        [("day", "Открыть дату (ДД.ММ.ГГГГ):", "date")],
        {"business_id": callback.data.split(":")[1]})


@form("calendar")
async def open_date(message, app, actor, values, context):
    await render_day(message, app, actor, context["business_id"], values["day"])


async def render_day(event, app, actor, id, key):
    if key.startswith("w"):
        weekday = int(key[1:])
        if not 0 <= weekday <= 6:
            raise RuleError("Неизвестный день недели.")
        rules = await app.schedule(actor, id)
        rule = next((r for r in rules if r.weekday == weekday), None)
        description = (f"{DAYS[weekday]} — обычный график\n\n"
                       f"{render_intervals(rule.intervals if rule else [])}\n"
                       f"Лимит: {rule.limit if rule and rule.limit is not None else 'без лимита'}\n"
                       f"Комментарий: {rule.comment if rule else '—'}")
        buttons = [
            ("Изменить рабочие интервалы", "intervals"),
            ("Изменить лимит", "limit"),
            ("Комментарий дня недели", "comment"),
        ]
        await send(event, description, one(
            *[(label, f"sched:{id}:{key}:{field}") for label, field in buttons],
            ("Рабочая неделя", f"week:{id}"),
            HOME,
        ))
    else:
        day = date.fromisoformat(key)
        work, closed, limit, rule, override = await app.schedule(actor, id, day)
        appointments = await app.appointments(actor, id, day)
        count = sum(a.status in ("booked", "pending_payment", "awaiting_duration") for a in appointments)
        description = (f"{DAYS[day.weekday()]}, {day:%d.%m.%Y}\n\n"
                       f"Рабочие интервалы: {render_intervals(work)}\n"
                       f"Закрыто: {render_intervals(closed) if closed else 'нет'}\n"
                       f"Записей: {count} / {limit if limit is not None else 'без лимита'}\n"
                       f"Комментарий недели: {rule.comment if rule else '—'}\n"
                       f"Комментарий даты: {override.comment if override else '—'}")
        buttons = [
            ("Изменить интервалы этого дня", "intervals"),
            ("Закрыть часть дня", "closed"),
            ("Изменить лимит", "limit"),
            ("Добавить комментарий", "comment"),
            ("Адрес этой даты", "address"),
            ("Сделать день выходным", "day_off"),
            ("Убрать разовые закрытия", "clear_closed"),
            ("Вернуть обычный график", "reset"),
        ]
        await send(event, description, one(
            *[(label, f"sched:{id}:{key}:{field}") for label, field in buttons],
            ("Комментарий / адрес конкретного времени", f"timenote:{id}:{key}"),
            ("Записать клиента вручную", f"manual:{id}:{key}"),
            ("Открыть записи этой даты", f"dayapps:{id}:{key}"),
            (f"Изменить обычный график: {DAYS[day.weekday()]}", f"dayrule:{id}:w{day.weekday()}"),
            HOME,
        ))


@router.callback_query(F.data.startswith("dayrule:"))
async def day(callback, app, actor):
    _, id, key = callback.data.split(":")
    await render_day(callback, app, actor, id, key)


@router.callback_query(F.data.startswith("sched:"))
async def edit(callback, state: FSMContext, app, actor):
    _, id, key, field = callback.data.split(":")
    if field in ("day_off", "clear_closed", "reset"):
        conflicts = await app.edit_schedule(actor, id,
            int(key[1:]) if key.startswith("w") else date.fromisoformat(key), field, None)
        await show_conflicts(callback, conflicts, id, key)
        return
    prompts = {
        "intervals": "Введите полный набор интервалов:\n10:00-13:00, 15:00-20:00\nВыходной: -",
        "closed": "Введите один или несколько промежутков:\n16:30-17:30, 19:00-19:30\nПричина не требуется.",
        "limit": "Максимум клиентов (без лимита: -):",
        "comment": "Комментарий (очистить: -):",
        "address": "Адрес этой даты (вернуть общий: -):",
    }
    await start_form(callback, state, "schedule",
        [("value", prompts[field], "intervals" if field in ("intervals", "closed") else "text")],
        {"id": id, "key": key, "field": field})


async def show_conflicts(event, conflicts, id, key):
    message = "Изменения сохранены для новых записей."
    if conflicts:
        message += f"\nЕсть конфликты с {len(conflicts)} существующими записями. Они сохранены."
    await send(event, message, one(
        *[(f"Открыть / перенести {a.start:%d.%m %H:%M}", f"appt:{a.id}") for a in conflicts[:15]],
        ("Вернуться к графику", f"dayrule:{id}:{key}"),
        HOME,
    ))


@form("schedule")
async def save(message, app, actor, values, context):
    key, field = context["key"], context["field"]
    value = [Interval(**x) for x in values["value"]] if field in ("intervals", "closed") else values["value"]
    conflicts = await app.edit_schedule(actor, context["id"],
        int(key[1:]) if key.startswith("w") else date.fromisoformat(key), field, value)
    await show_conflicts(message, conflicts, context["id"], key)


@router.callback_query(F.data.startswith("timenote:"))
async def note(callback, state: FSMContext):
    _, id, key = callback.data.split(":")
    await start_form(callback, state, "timenote", [
        ("minute", "Время начала, ЧЧ:ММ:", "time"),
        ("comment", "Комментарий этого времени (очистить: -):", "optional"),
        ("address", "Адрес этого времени (использовать адрес даты: -):", "optional"),
    ], {"id": id, "day": key})


@form("timenote")
async def save_note(message, app, actor, values, context):
    await app.set_time_note(actor, context["id"], date.fromisoformat(context["day"]), **values)
    await send(message, "Комментарий времени сохранён.",
        one(("К дате", f"dayrule:{context['id']}:{context['day']}"), HOME))
