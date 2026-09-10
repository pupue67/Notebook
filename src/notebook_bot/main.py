"""Точка запуска приложения."""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from .bot import business, menu
from .bot.common import ContextMiddleware, HOME, forms_router, one, send
from .config import Settings
from .logic.catalog import Catalog


def build_app(settings):
    """Создаёт временную логику в памяти для демонстрации интерфейса."""

    return Catalog(settings.timezone)


def build_dispatcher(app):
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    middleware = ContextMiddleware(app)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.include_router(business.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(forms_router)

    fallback = Router(name="future_sections")

    @fallback.callback_query()
    async def future_section(callback):
        await send(
            callback,
            "Этот раздел предусмотрен архитектурой и будет реализован в следующей задаче.",
            one(HOME),
        )

    @fallback.message(F.text)
    async def unknown(message):
        await send(message, "Используйте /start или кнопки меню.", one(HOME))

    dispatcher.include_router(fallback)
    return dispatcher


async def main():
    settings = Settings.load()
    logging.basicConfig(level=logging.INFO)
    app = build_app(settings)
    dispatcher = build_dispatcher(app)
    async with Bot(settings.token, session=AiohttpSession(proxy=settings.proxy, timeout=30)) as bot:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
