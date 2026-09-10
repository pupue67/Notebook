"""Главное меню Notebook."""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from .common import one, send

router = Router(name="menu")


def build_keyboard(data):
    buttons = [("📅 Мои записи", "nav:appointments"), ("⭐ Мои специалисты", "nav:specialists")]
    buttons.extend((f"💼 {business.name}", f"nav:business:{business.id}") for business in data.businesses)
    if data.can_create_business:
        buttons.append(("💼 Создать бизнес", "nav:create"))
    return one(*buttons)


@router.message(CommandStart(deep_link=False))
@router.callback_query(F.data == "home")
async def home(event, state: FSMContext, app):
    await state.clear()
    data = await app.get_main_menu_data(event.from_user.id)
    heading = "\n\nМой бизнес:" if len(data.businesses) == 1 else "\n\nМои бизнесы:" if data.businesses else ""
    await send(event, "Notebook\n\nВыберите нужный раздел." + heading, build_keyboard(data))
