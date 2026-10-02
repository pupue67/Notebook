"""Сборка зависимостей и запуск минимальной Telegram-версии Notebook."""

import asyncio
import contextlib
import logging
import re
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from .bot import business, menu
from .bot.common import ContextMiddleware, HOME, forms_router, one, send
from .config import Settings
from .logic.catalog import Catalog
from .storage import MemoryRepository


class RedactSecrets(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        return re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b", "[BOT_TOKEN]", message)


@contextlib.contextmanager
def process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        file.seek(0)
        if not file.read(1):
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(
                "Notebook уже запущен с этим хранилищем. Остановите первый процесс."
            ) from None
        try:
            yield
        finally:
            file.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def build_app(settings: Settings) -> Catalog:


    data_file = settings.data_file if settings.storage == "json" else None
    repository = MemoryRepository(data_file)
    return Catalog(repository, settings.timezone)


def build_dispatcher(app: Catalog) -> Dispatcher:
    

    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    middleware = ContextMiddleware(app)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)

    # Deep link обрабатывается раньше обычной команды /start.
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

    @fallback.errors()
    async def unexpected(event):
        logging.getLogger(__name__).error(
            "Непредвиденная ошибка Telegram-обработчика",
            exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
        )
        update = event.update
        target = update.callback_query or update.message
        if target:
            with contextlib.suppress(Exception):
                await send(
                    target,
                    "Не удалось выполнить действие. Откройте меню и попробуйте снова.",
                    one(HOME),
                )
        return True

    dispatcher.include_router(fallback)
    return dispatcher


async def main():
    settings = Settings.load()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactSecrets("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    lock_path = (
        settings.data_file.with_suffix(settings.data_file.suffix + ".lock")
        if settings.storage == "json"
        else Path("data/notebook.lock")
    )
    with process_lock(lock_path):
        app = build_app(settings)
        dispatcher = build_dispatcher(app)
        async with Bot(
            settings.token,
            session=AiohttpSession(proxy=settings.proxy, timeout=30),
        ) as bot:
            info = await bot.get_me()
            webhook = await bot.get_webhook_info()
            if webhook.url:
                raise RuntimeError(
                    "У бота установлен webhook. Для polling сначала отключите прежний запуск."
                )
            logging.info("Notebook запущен как @%s", info.username)
            try:
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                    close_bot_session=False,
                )
            finally:
                await dispatcher.storage.close()


def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
