"""Создание и просмотр бизнеса, настройки, клиентская ссылка и избранное."""
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from .common import HOME, form, one, send, start_form

router = Router(name="business")


async def show_profile(event, app, actor, business_id, bot, manage=False):
    b = await app.business(business_id)
    services = await app.services(b.id, actor if manage else None)
    message = f"{b.name}\n{b.specialist}\n\n{b.description}\n\nАдрес / подключение: {b.address or 'Уточните у специалиста'}\n{b.comment}\n\nУслуги:\n"
    message += (
        "\n".join(
            f"• {s.name} — {s.price_rub} ₽ · "
            + (f"{s.duration_min} мин" if s.mode == "fixed" else "персональная длительность")
            for s in services
        )
        or "Пока нет услуг."
    )
    buttons = []
    if manage and b.owner_id == actor:
        username = (await bot.get_me()).username
        message += f"\n\nСсылка клиентам: https://t.me/{username}?start=b_{b.id}"
        message += f"\nКод: {b.id}\nНовые записи: {'включены' if b.accepts_new else 'выключены'}"
        buttons += [
            ("Услуги", f"services:{b.id}"),
            ("Рабочая неделя", f"week:{b.id}"),
            ("Календарь / конкретная дата", f"calendar:{b.id}"),
            ("Записи клиентов", f"ownerapps:{b.id}"),
            ("Клиенты / ручная запись", f"clients:{b.id}"),
            ("Настройки профиля и записи", f"settings:{b.id}"),
            ("Вкл/выкл новые записи", f"toggle:{b.id}"),
            ("Посмотреть как клиент", f"profile:{b.id}"),
        ]
    else:
        if b.accepts_new:
            buttons.append(("Записаться", f"choose:{b.id}"))
        else:
            message += "\n\nСпециалист временно не принимает новые записи."
        buttons.append(("Добавить / убрать из моих специалистов", f"favorite:{b.id}"))
    await send(event, message, one(*buttons, HOME))


@router.message(CommandStart(deep_link=True))
async def deep_link(message, command, state: FSMContext, app, actor, bot):
    await state.clear()
    code = command.args.removeprefix("b_")
    await show_profile(message, app, actor, code, bot)


@router.callback_query(F.data.startswith("nav:business:"))
async def manage(callback, state: FSMContext, app, actor, bot):
    await state.clear()
    await show_profile(callback, app, actor, callback.data.split(":")[-1], bot, True)


@router.callback_query(F.data.startswith("profile:"))
async def profile(callback, app, actor, bot):
    await show_profile(callback, app, actor, callback.data.split(":")[1], bot)


@router.callback_query(F.data == "nav:create")
async def create(callback, state: FSMContext, app):
    data = await app.get_main_menu_data(callback.from_user.id)
    if not data.can_create_business:
        await state.clear()
        await send(callback, "Можно создать не более двух бизнесов.", one(HOME))
        return
    await start_form(
        callback,
        state,
        "business",
        [
            ("name", "Название бизнеса:", "text"),
            ("specialist", "Имя специалиста:", "text"),
            ("description", "Краткое описание:", "text"),
            (
                "location",
                "Формат работы — введите номер:\n1. У мастера\n2. У клиента / выезд\n3. Онлайн\n4. Переменное место",
                "number",
            ),
            ("address", "Адрес или инструкция подключения (пропустить: -):", "optional"),
            ("comment", "Общий комментарий клиентам (пропустить: -):", "optional"),
        ],
    )


@form("business")
async def save_business(message, app, actor, values, context):
    from ..logic.rules import number

    values["location"] = ["at_master", "at_client", "online", "variable"][
        number(values["location"], 1, 4) - 1
    ]
    b = await app.create_business(actor, **values)
    await send(
        message,
        "Бизнес создан. Добавьте первую услугу и настройте рабочую неделю.",
        one(
            ("Добавить услугу", f"serviceadd:{b.id}"),
            ("Рабочая неделя", f"week:{b.id}"),
            ("Мой бизнес", f"nav:business:{b.id}"),
            HOME,
        ),
    )


@router.callback_query(F.data == "nav:specialists")
async def specialists(callback, state: FSMContext, app, actor):
    await state.clear()
    values = await app.favorites(actor)
    await send(
        callback,
        "Мои специалисты"
        if values
        else "Пока нет сохранённых специалистов. Откройте ссылку мастера или введите его код.",
        one(*[(b.name, f"profile:{b.id}") for b in values], ("Найти по коду", "findbusiness"), HOME),
    )


@router.callback_query(F.data == "findbusiness")
async def find(callback, state: FSMContext):
    await start_form(callback, state, "findbusiness", [("code", "Введите код специалиста:", "text")])


@form("findbusiness")
async def found(message, app, actor, values, context):
    b = await app.business(values["code"].removeprefix("b_"))
    await send(message, b.name, one(("Открыть профиль", f"profile:{b.id}"), HOME))


@router.callback_query(F.data.startswith("favorite:"))
async def favorite(callback, app, actor):
    await app.favorite(actor, callback.data.split(":")[1])
    await send(callback, "Список специалистов обновлён.", one(("Мои специалисты", "nav:specialists"), HOME))


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(callback, app, actor, bot):
    id = callback.data.split(":")[1]
    b = await app.business(id)
    await app.update_business(actor, id, "accepts_new", not b.accepts_new)
    await show_profile(callback, app, actor, id, bot, True)


FIELDS = {
    "name": "Название",
    "specialist": "Имя специалиста",
    "description": "Описание",
    "address": "Адрес / подключение",
    "comment": "Общий комментарий",
    "payment_details": "Реквизиты предоплаты",
    "buffer_min": "Перерыв между клиентами (минуты)",
    "notice_min": "Минимум до записи (минуты)",
    "horizon_days": "Горизонт записи (дни)",
    "cancel_notice_min": "Срок самостоятельной отмены (минуты)",
    "move_notice_min": "Срок самостоятельного переноса (минуты)",
}


@router.callback_query(F.data.startswith("settings:"))
async def settings(callback, app, actor):
    id = callback.data.split(":")[1]
    b = await app.owned_business(actor, id)
    await send(
        callback,
        "Настройки\n\n" + "\n".join(f"{label}: {getattr(b, key) or '—'}" for key, label in FIELDS.items()),
        one(
            *[(label, f"setting:{id}:{key}") for key, label in FIELDS.items()],
            ("Расширенные настройки", f"advanced:{id}"),
            HOME,
        ),
    )


@router.callback_query(F.data.startswith("advanced:"))
async def advanced(callback):
    id = callback.data.split(":")[1]
    await send(
        callback,
        "Кратность времени начала по умолчанию 10 минут.",
        one(("Изменить кратность", f"setting:{id}:increment_min"), ("Назад", f"settings:{id}")),
    )


@router.callback_query(F.data.startswith("setting:"))
async def edit_setting(callback, state: FSMContext):
    _, id, field = callback.data.split(":")
    await start_form(
        callback,
        state,
        "setting",
        [("value", "Новое значение (очистить текст: -):", "text")],
        {"id": id, "field": field},
    )


@form("setting")
async def save_setting(message, app, actor, values, context):
    await app.update_business(actor, context["id"], context["field"], values["value"])
    await send(message, "Настройка сохранена.", one(("Настройки", f"settings:{context['id']}"), HOME))
