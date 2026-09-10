"""Общие формы, клавиатуры и промежуточный обработчик контекста."""

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..logic.rules import RuleError, intervals, local_date, minute, number, text

forms_router = Router(name="forms")
SUBMITS = {}


def kb(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def one(*buttons):
    return kb(*[[button] for button in buttons])


HOME = ("⌂ Главное меню", "home")


async def send(event, text_value, markup=None):
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(text_value[:4000], reply_markup=markup, parse_mode=None)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, app):
        self.app = app

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        if not message or message.chat.type != "private":
            return None
        if isinstance(event, CallbackQuery):
            await event.answer()
        user = await self.app.user(event.from_user.id, event.from_user.full_name)
        data["app"], data["actor"] = self.app, user.id
        try:
            return await handler(event, data)
        except RuleError as exc:
            await send(event, str(exc), one(HOME))
        except (ValueError, IndexError):
            await send(event, "Кнопка или ввод устарели. Откройте раздел заново.", one(HOME))


class Form(StatesGroup):
    waiting = State()


def form(name):
    def register(function):
        SUBMITS[name] = function
        return function

    return register


async def start_form(event, state, kind, fields, context=None):
    await state.clear()
    await state.set_state(Form.waiting)
    await state.set_data(
        {"kind": kind, "fields": fields, "index": 0, "values": {}, "context": context or {}}
    )
    await ask(event, state)


async def ask(event, state):
    data = await state.get_data()
    field = data["fields"][data["index"]]
    buttons = [("Отмена", "form:cancel")]
    if data["index"]:
        buttons.insert(0, ("Назад", "form:back"))
    await send(event, field[1], one(*buttons))


@forms_router.callback_query(F.data == "form:cancel")
async def cancel(callback, state: FSMContext):
    await state.clear()
    await send(callback, "Ввод отменён.", one(HOME))


@forms_router.callback_query(F.data == "form:back")
async def back(callback, state: FSMContext):
    data = await state.get_data()
    if "index" not in data:
        raise RuleError("Форма уже закрыта.")
    await state.update_data(index=max(0, data["index"] - 1))
    await ask(callback, state)


@forms_router.message(Form.waiting, F.text)
async def input_form(message, state: FSMContext, app, actor):
    data = await state.get_data()
    key, _, parser = data["fields"][data["index"]]
    raw = message.text.strip()
    if raw.startswith("/"):
        await send(message, "Завершите форму или нажмите «Отмена».", one(("Отмена", "form:cancel")))
        return
    try:
        if parser == "number":
            value = number(raw)
        elif parser == "minutes":
            value = number(raw, 1, 1440)
        elif parser == "intervals":
            value = [item.model_dump() for item in intervals(raw)]
        elif parser == "date":
            value = local_date(raw).isoformat()
        elif parser == "time":
            value = minute(raw)
            if value == 1440:
                raise RuleError("Для начала выберите время до 24:00.")
        elif parser == "optional":
            value = "" if raw == "-" else text(raw, 1000)
        else:
            value = text(raw, 1000)
    except RuleError as exc:
        await send(message, str(exc))
        await ask(message, state)
        return
    values = {**data["values"], key: value}
    if data["index"] + 1 < len(data["fields"]):
        await state.update_data(values=values, index=data["index"] + 1)
        await ask(message, state)
        return
    try:
        await SUBMITS[data["kind"]](message, app, actor, values, data["context"])
    except RuleError as exc:
        await send(message, str(exc) + "\nФорма не сохранена. Исправьте ввод или вернитесь назад.")
        await ask(message, state)
        return
    await state.clear()

