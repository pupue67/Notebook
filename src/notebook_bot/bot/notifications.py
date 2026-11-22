import asyncio
import logging
from datetime import timedelta

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from ..models import Appointment, Business, Client, Notification, User
from ..logic.availability import visit_context
from .common import one

log = logging.getLogger(__name__)


async def deliver(bot, app):
    async with app.repo.transaction() as tx:
        pending = [
            n
            for n in await tx.list(Notification)
            if not n.sent and n.attempts < 10 and (n.next_attempt is None or n.next_attempt <= app.clock())
        ][:30]
    for n in pending:
        async with app.repo.transaction() as tx:
            n = await tx.get(Notification, n.id)
            if not n:
                continue
            user = await tx.get(User, n.user_id)
            a = await tx.get(Appointment, n.appointment_id) if n.appointment_id else None
            message = n.text
            if a:
                b = await tx.get(Business, a.business_id)
                c = await tx.get(Client, a.client_id)
                address, comment = await visit_context(tx, b, a.start)
                message += f"\n\n{b.name}\n{a.service_name}\nКлиент: {c.name}\n{a.start:%d.%m.%Y %H:%M} · {a.duration_min} мин\nАдрес: {address or 'уточните у мастера'}\n{comment}"
        delay, permanent = 30, False
        try:
            if not user:
                permanent = True
            else:
                buttons = [("Открыть запись", f"appt:{a.id}")] if a else []
                if a and a.status in ("booked", "pending_payment", "awaiting_duration"):
                    buttons += [
                        (
                            "Перенести",
                            f"move:{a.id}"
                            if user.id != b.owner_id or not c.user_id or c.user_id == user.id
                            else f"requestmove:{a.id}",
                        ),
                        ("Отменить", f"cancelask:{a.id}"),
                    ]
                await bot.send_message(
                    user.telegram_id,
                    message[:4000],
                    reply_markup=one(*buttons) if buttons else None,
                    parse_mode=None,
                )
                n.sent = True
        except TelegramRetryAfter as exc:
            delay = exc.retry_after
        except TelegramForbiddenError:
            permanent = True
            log.warning("Recipient blocked bot; notification %s remains undelivered", n.id)
        except TelegramAPIError:
            log.warning("Notification %s delivery failed; retry scheduled", n.id)
        async with app.repo.transaction() as tx:
            current = await tx.get(Notification, n.id)
            if not current:
                continue
            current.sent = n.sent
            current.attempts = 10 if permanent else current.attempts + 1
            current.next_attempt = app.clock() + timedelta(seconds=delay)
            await tx.put(current)


async def worker(bot, app):
    while True:
        try:
            await app.tick()
            await deliver(bot, app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Reminder worker failed; will retry")
        await asyncio.sleep(10)
