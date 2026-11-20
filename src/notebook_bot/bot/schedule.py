"""Интерфейс расписания: рабочая неделя, интервалы, лимиты, исключения дат, закрытые промежутки, комментарии, адреса и проверка конфликтов"""
from datetime import date
from secrets import token_hex

from aiogram import F, Router
from aiogram.fsm.context import FSMContext

from ..logic.rules import RuleError, hhmm
from ..models import Interval
from .common import HOME, form, kb, one, send, start_form

router = Router(name="schedule")
DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def render_intervals(items):
    return ", ".join(f"{hhmm(i.start)}–{hhmm(i.end)}" for i in items) or "Нет рабочих интервалов"


@router.callback_query(F.data.startswith("week:"))
async def week(callback, app, actor):
    id = callback.data.split(":")[1]
    rules = {r.weekday: r for r in await app.schedule(actor, id)}
    await send(
        callback,
        "Рабочая неделя\nИзменения здесь относятся к выбранному дню недели.\n\n"
        + "\n".join(
            f"{name}: {render_intervals(rules[i].intervals) if i in rules else 'Выходной'}"
            for i, name in enumerate(DAYS)
        ),
        one(
            *[(name, f"dayrule:{id}:w{i}") for i, name in enumerate(DAYS)],
            ("Календарь дат", f"mastermonth:{id}"),
            ("Последняя дата записи", f"horizon:{id}"),
            HOME,
        ),
    )


@router.callback_query(F.data.startswith("calendar:"))
async def calendar(callback, app, actor):
    from .calendar import show_master_month

    today = app.clock().date()
    await show_master_month(
        callback, app, actor, callback.data.split(":")[1], today.year, today.month
    )


@router.callback_query(F.data.startswith("calendartext:"))
async def calendar_text(callback, state: FSMContext):
    await start_form(
        callback,
        state,
        "calendar",
        [("day", "Открыть дату (ДД.ММ.ГГГГ):", "date")],
        {"business_id": callback.data.split(":")[1]},
    )


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
        description = f"{DAYS[weekday]} — обычный график\n\n{render_intervals(rule.intervals if rule else [])}\nЛимит: {rule.limit if rule and rule.limit is not None else 'без лимита'}\nКомментарий: {rule.comment if rule else '—'}"
        buttons = [
            ("Изменить рабочие интервалы", "intervals"),
            ("Изменить лимит", "limit"),
            ("Комментарий дня недели", "comment"),
        ]
        await send(
            event,
            description,
            one(
                *[(label, f"sched:{id}:{key}:{field}") for label, field in buttons],
                ("Рабочая неделя", f"week:{id}"),
                HOME,
            ),
        )
    else:
        day = date.fromisoformat(key)
        work, closed, limit, rule, override = await app.schedule(actor, id, day)
        appointments = await app.appointments(actor, id, day)
        count = sum(a.status in ("booked", "pending_payment", "awaiting_duration") for a in appointments)
        description = f"{DAYS[day.weekday()]}, {day:%d.%m.%Y}\n\nРабочие интервалы: {render_intervals(work)}\nЗакрыто: {render_intervals(closed) if closed else 'нет'}\nЗаписей: {count} / {limit if limit is not None else 'без лимита'}\nКомментарий недели: {rule.comment if rule else '—'}\nКомментарий даты: {override.comment if override else '—'}"
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
        await send(
            event,
            description,
            one(
                *[(label, f"sched:{id}:{key}:{field}") for label, field in buttons],
                ("Комментарий / адрес конкретного времени", f"timenote:{id}:{key}"),
                ("Записать клиента вручную", f"manual:{id}:{key}"),
                ("Открыть записи этой даты", f"dayapps:{id}:{key}"),
                (f"Изменить обычный график: {DAYS[day.weekday()]}", f"dayrule:{id}:w{day.weekday()}"),
                HOME,
            ),
        )


@router.callback_query(F.data.startswith("dayrule:"))
async def day(callback, app, actor):
    _, id, key = callback.data.split(":")
    await render_day(callback, app, actor, id, key)


@router.callback_query(F.data.startswith("sched:"))
async def edit(callback, state: FSMContext, app, actor):
    _, id, key, field = callback.data.split(":")
    if field in ("day_off", "clear_closed", "reset"):
        conflicts = await app.edit_schedule(
            actor, id, int(key[1:]) if key.startswith("w") else date.fromisoformat(key), field, None
        )
        await show_conflicts(callback, conflicts, id, key)
        return
    if field in ("intervals", "closed"):
        await start_interval_picker(callback, state, app, actor, id, key, field)
        return
    await manual_schedule_form(callback, state, id, key, field)


async def manual_schedule_form(event, state, id, key, field):
    prompts = {
        "intervals": "Введите полный набор интервалов:\n10:00-13:00, 15:00-20:00\nВыходной: -",
        "closed": "Введите один или несколько промежутков:\n16:30-17:30, 19:00-19:30\nПричина не требуется.",
        "limit": "Максимум клиентов (без лимита: -):",
        "comment": "Комментарий (очистить: -):",
        "address": "Адрес этой даты (вернуть общий: -):",
    }
    await start_form(
        event,
        state,
        "schedule",
        [("value", prompts[field], "intervals" if field in ("intervals", "closed") else "text")],
        {"id": id, "key": key, "field": field},
    )


async def start_interval_picker(event, state, app, actor, id, key, field):
    await app.owned_business(actor, id)
    await state.clear()
    await state.update_data(
        picker={"id": id, "key": key, "field": field, "stage": "start_hour", "items": [],
                "nonce": token_hex(3)}
    )
    await render_interval_picker(event, state)


async def render_interval_picker(event, state):
    picker = (await state.get_data())["picker"]
    nonce, stage = picker["nonce"], picker["stage"]
    name = "Рабочие интервалы" if picker["field"] == "intervals" else "Закрытые промежутки"
    selected = ", ".join(f"{hhmm(item['start'])}–{hhmm(item['end'])}" for item in picker["items"])
    message = f"{name}\nВыбрано: {selected or 'пока ничего'}\n"
    if stage == "review":
        message += "Добавьте ещё интервал или сохраните набор."
        rows = [
            [("Добавить интервал", f"ipick:{nonce}:add")],
            [("Сохранить", f"ipick:{nonce}:save", "success")],
        ]
    else:
        is_hour = stage.endswith("hour")
        start = stage.startswith("start")
        message += ("Начало" if start else "Конец") + (": выберите час." if is_hour else ": выберите минуты.")
        if is_hour:
            values = range(24 if start else 25)
            if not start:
                values = [value for value in values if value * 60 >= picker["start"]]
            buttons = [(f"{value:02d}", f"ipick:{nonce}:value:{value}") for value in values]
            columns = 6
        else:
            values = [0] if picker.get("hour") == 24 else range(0, 60, 10)
            buttons = [(f"{value:02d}", f"ipick:{nonce}:value:{value}") for value in values]
            columns = 3
        rows = [buttons[index:index + columns] for index in range(0, len(buttons), columns)]
    if picker["field"] == "intervals":
        rows.append([("Сделать выходным", f"ipick:{nonce}:off")])
    rows.append([("Ввести интервал текстом", f"ipick:{nonce}:manual")])
    rows.append([("Отмена", f"dayrule:{picker['id']}:{picker['key']}")])
    await send(event, message, kb(*rows))


@router.callback_query(F.data.startswith("ipick:"))
async def interval_pick(callback, state: FSMContext, app, actor):
    parts = callback.data.split(":")
    picker = (await state.get_data()).get("picker")
    if not picker or parts[1] != picker["nonce"]:
        raise RuleError("Выбор интервала устарел. Откройте день заново.")
    await app.owned_business(actor, picker["id"])
    action = parts[2]
    if action == "manual":
        await manual_schedule_form(callback, state, picker["id"], picker["key"], picker["field"])
        return
    if action == "off" and picker["field"] == "intervals":
        picker["items"] = []
        action = "save"
    if action == "save":
        if not picker["items"] and picker["field"] == "closed":
            raise RuleError("Сначала выберите закрытый промежуток.")
        key = picker["key"]
        conflicts = await app.edit_schedule(
            actor, picker["id"], int(key[1:]) if key.startswith("w") else date.fromisoformat(key),
            picker["field"], [Interval(**item) for item in picker["items"]],
        )
        await state.clear()
        await show_conflicts(callback, conflicts, picker["id"], key)
        return
    if action == "add" and picker["stage"] == "review":
        picker["stage"] = "start_hour"
    elif action == "value":
        value = int(parts[3])
        stage = picker["stage"]
        if stage.endswith("hour"):
            if not 0 <= value <= (23 if stage == "start_hour" else 24):
                raise RuleError("Выберите час кнопкой.")
            if stage == "end_hour" and value * 60 < picker["start"]:
                raise RuleError("Конец интервала должен быть позже начала.")
        elif value not in range(0, 60, 10) or (picker.get("hour") == 24 and value != 0):
            raise RuleError("Выберите минуты кнопкой.")
        if stage == "start_hour":
            picker["hour"], picker["stage"] = value, "start_minute"
        elif stage == "start_minute":
            picker["start"], picker["stage"] = picker["hour"] * 60 + value, "end_hour"
        elif stage == "end_hour":
            picker["hour"], picker["stage"] = value, "end_minute"
        elif stage == "end_minute":
            end = picker["hour"] * 60 + value
            if end <= picker["start"]:
                raise RuleError("Конец интервала должен быть позже начала.")
            picker["items"].append({"start": picker["start"], "end": end})
            picker["stage"] = "review"
        else:
            raise RuleError("Выбор интервала устарел.")
    else:
        raise RuleError("Выбор интервала устарел.")
    picker["nonce"] = token_hex(3)
    await state.update_data(picker=picker)
    await render_interval_picker(callback, state)


async def show_conflicts(event, conflicts, id, key):
    message = "Изменения сохранены для новых записей."
    if conflicts:
        message += f"\nЕсть конфликты с {len(conflicts)} существующими записями. Они сохранены. Откройте карточки и предложите перенос."
    await send(
        event,
        message,
        one(
            *[(f"Открыть / перенести {a.start:%d.%m %H:%M}", f"appt:{a.id}") for a in conflicts[:15]],
            ("Вернуться к графику", f"dayrule:{id}:{key}"),
            HOME,
        ),
    )


@form("schedule")
async def save(message, app, actor, values, context):
    key, field = context["key"], context["field"]
    value = [Interval(**x) for x in values["value"]] if field in ("intervals", "closed") else values["value"]
    conflicts = await app.edit_schedule(
        actor, context["id"], int(key[1:]) if key.startswith("w") else date.fromisoformat(key), field, value
    )
    await show_conflicts(message, conflicts, context["id"], key)


@router.callback_query(F.data.startswith("timenote:"))
async def note(callback, state: FSMContext):
    _, id, key = callback.data.split(":")
    await start_form(
        callback,
        state,
        "timenote",
        [
            ("minute", "Время начала, ЧЧ:ММ:", "time"),
            ("comment", "Комментарий этого времени (очистить: -):", "optional"),
            ("address", "Адрес этого времени (использовать адрес даты: -):", "optional"),
        ],
        {"id": id, "day": key},
    )


@form("timenote")
async def save_note(message, app, actor, values, context):
    await app.set_time_note(actor, context["id"], date.fromisoformat(context["day"]), **values)
    await send(
        message,
        "Комментарий времени сохранён.",
        one(("К дате", f"dayrule:{context['id']}:{context['day']}"), HOME),
    )
