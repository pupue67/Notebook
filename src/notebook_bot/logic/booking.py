""" Логика записей: создание, отмена и перенос записей, длительность, предоплата, завершение визита и напоминания."""
from datetime import timedelta
from ..models import Appointment, Business, Client, History, PersonalDuration, Service
from .availability import available_times, duration_for, occupies, visit_context
from .catalog import Catalog
from .rules import RuleError, number


class BookingService(Catalog):
    async def _access(self, tx, actor, id):
        a = await tx.get(Appointment, id)
        if not a:
            raise RuleError("Запись не найдена.")
        b = await tx.get(Business, a.business_id)
        c = await tx.get(Client, a.client_id)
        if actor not in (b.owner_id, c.user_id):
            raise RuleError("Запись недоступна.")
        return a, b, c

    async def _personal(self, tx, a, minutes):
        values = await tx.list(PersonalDuration, client_id=a.client_id, service_id=a.service_id)
        p = (
            values[0]
            if values
            else PersonalDuration(client_id=a.client_id, service_id=a.service_id, minutes=minutes)
        )
        p.minutes = minutes
        await tx.put(p)

    async def _history(self, tx, a, actor, action, old=None, reason=""):
        await tx.put(
            History(
                appointment_id=a.id,
                actor_id=actor,
                action=action,
                at=self.clock(),
                old_start=old,
                new_start=a.start,
                reason=reason,
            )
        )

    async def create(self, actor, service_id, start, *, client_id=None, client_name=None, duration=None):
        if start.tzinfo is None:
            raise RuleError("Время должно содержать внутренний часовой пояс.")
        start = start.astimezone(self.timezone)
        async with self.repo.transaction() as tx:
            s = await tx.get(Service, service_id)
            if not s:
                raise RuleError("Услуга недоступна.")
            b = await tx.get(Business, s.business_id)
            manual = client_id is not None or client_name is not None
            if manual:
                await self.owner(tx, actor, b.id)
                c = (
                    await tx.get(Client, client_id)
                    if client_id
                    else await self.client(tx, b.id, name=client_name)
                )
                if not c or c.business_id != b.id:
                    raise RuleError("Клиент недоступен.")
            else:
                c = await self.client(tx, b.id, user_id=actor)
            # Repeated callbacks return the original reservation while it remains active.
            for existing in await tx.list(Appointment, client_id=c.id, service_id=s.id):
                if existing.start == start and occupies(existing, self.clock()):
                    return existing
            minutes, known = await duration_for(tx, s, c.id)
            if duration is not None:
                if not manual:
                    raise RuleError("Длительность задаёт мастер.")
                minutes, known = number(duration, 1, 1440), True
            if start not in await available_times(
                tx, b, s, c.id, start.date(), self.clock(), duration=minutes
            ):
                raise RuleError("Это время больше недоступно. Выберите другую дату и время.")
            status = "awaiting_duration" if not known else ("pending_payment" if s.prepay_rub else "booked")
            a = Appointment(
                business_id=b.id,
                client_id=c.id,
                service_id=s.id,
                start=start,
                duration_min=minutes,
                service_name=s.name,
                price_rub=s.price_rub,
                buffer_min=b.buffer_min,
                status=status,
                prepay_rub=s.prepay_rub,
                payment="pending" if s.prepay_rub else "none",
                expires_at=self.clock() + timedelta(minutes=30) if status != "booked" else None,
            )
            await tx.put(a)
            if manual and s.mode == "personal" and known:
                await self._personal(tx, a, minutes)
            await self._history(tx, a, actor, "manual_create" if manual else "create")
            await self.notify(
                tx, a, "Новая запись" if known else "Мастеру нужно указать длительность занятия"
            )
            return a

    async def set_duration(self, actor, id, minutes, future=False, expected_revision=None):
        minutes = number(minutes, 1, 1440)
        async with self.repo.transaction() as tx:
            a, b, _ = await self._access(tx, actor, id)
            await self.owner(tx, actor, b.id)
            if expected_revision is not None and a.revision != expected_revision:
                raise RuleError("Карточка устарела. Откройте запись заново.")
            if not occupies(a, self.clock()) or a.start <= self.clock():
                raise RuleError("Эту запись уже нельзя изменить.")
            s = await tx.get(Service, a.service_id)
            first = a.status == "awaiting_duration"
            times = await available_times(
                tx,
                b,
                s,
                a.client_id,
                a.start.date(),
                self.clock(),
                exclude=a.id,
                duration=minutes,
                existing=True,
            )
            if a.start not in times:
                a.required_duration, a.save_personal_on_move = minutes, future or first
                a.revision += 1
                await tx.put(a)
                return a, False
            a.duration_min, a.required_duration = minutes, None
            a.save_personal_on_move = False
            if first:
                a.status = "pending_payment" if a.prepay_rub else "booked"
                a.expires_at = self.clock() + timedelta(minutes=30) if a.prepay_rub else None
            if s.mode == "personal" and (future or first):
                await self._personal(tx, a, minutes)
            a.revision += 1
            await tx.put(a)
            await self._history(tx, a, actor, "duration")
            await self.notify(tx, a, f"Длительность записи: {minutes} минут")
            return a, True

    async def get_appointment(self, actor, id):
        async with self.repo.transaction() as tx:
            a, b, c = await self._access(tx, actor, id)
            address, comment = await visit_context(tx, b, a.start)
            return a, b, c, address, comment

    async def appointments(self, actor, business_id=None, day=None):
        async with self.repo.transaction() as tx:
            if business_id:
                await self.owner(tx, actor, business_id)
                items = await tx.list(Appointment, business_id=business_id)
            else:
                clients = await tx.list(Client, user_id=actor)
                ids = {c.id for c in clients}
                items = [a for a in await tx.list(Appointment) if a.client_id in ids]
            return sorted([a for a in items if day is None or a.start.date() == day], key=lambda a: a.start)

    async def cancel(self, actor, id, revision, reason=""):
        async with self.repo.transaction() as tx:
            a, b, _ = await self._access(tx, actor, id)
            if a.status.startswith("cancelled"):
                return a
            if a.revision != revision:
                raise RuleError("Карточка устарела. Откройте запись заново.")
            if not occupies(a, self.clock()):
                raise RuleError("Эта запись уже завершена или истекла.")
            if actor != b.owner_id and a.start < self.clock() + timedelta(minutes=b.cancel_notice_min):
                raise RuleError("Для отмены по правилам специалиста свяжитесь с ним напрямую.")
            a.status = "cancelled_master" if actor == b.owner_id else "cancelled_client"
            a.revision += 1
            a.expires_at = None
            await tx.put(a)
            await self._history(tx, a, actor, "cancel", reason=reason[:500])
            await self.notify(tx, a, "Запись отменена. Вопрос возврата предоплаты решается со специалистом.")
            return a

    async def request_move(self, actor, id):
        async with self.repo.transaction() as tx:
            a, b, c = await self._access(tx, actor, id)
            await self.owner(tx, actor, b.id)
            if not occupies(a, self.clock()) or a.start <= self.clock():
                raise RuleError("Эту запись уже нельзя перенести.")
            if not c.user_id:
                raise RuleError(
                    "У ручной карточки нет Telegram. Согласуйте время с клиентом и перенесите вручную."
                )
            a.move_requested = True
            a.revision += 1
            await tx.put(a)
            await self._history(tx, a, actor, "request_move")
            await self.notify(tx, a, "Мастер просит выбрать новую дату и время. Откройте запись → Перенести.")

    async def move(self, actor, id, start, revision):
        if start.tzinfo is None:
            raise RuleError("Неверное время.")
        start = start.astimezone(self.timezone)
        async with self.repo.transaction() as tx:
            a, b, c = await self._access(tx, actor, id)
            if a.revision != revision or not occupies(a, self.clock()) or a.start <= self.clock():
                raise RuleError("Карточка устарела. Откройте запись заново.")
            if actor == b.owner_id and c.user_id and c.user_id != actor:
                raise RuleError("Новую дату и время выбирает клиент. Отправьте запрос переноса.")
            if (
                actor != b.owner_id
                and not a.move_requested
                and a.start < self.clock() + timedelta(minutes=b.move_notice_min)
            ):
                raise RuleError("Для переноса по правилам специалиста свяжитесь с ним напрямую.")
            if start == a.start:
                raise RuleError("Выберите новое время; текущая запись остаётся на месте.")
            s = await tx.get(Service, a.service_id)
            minutes = a.required_duration or a.duration_min
            if start not in await available_times(
                tx,
                b,
                s,
                a.client_id,
                start.date(),
                self.clock(),
                exclude=a.id,
                duration=minutes,
                existing=True,
            ):
                raise RuleError("Время больше недоступно. Старая запись сохранена; выберите другой вариант.")
            old = a.start
            a.start, a.duration_min = start, minutes
            if a.save_personal_on_move and s.mode == "personal":
                await self._personal(tx, a, minutes)
            if a.status == "awaiting_duration" and a.required_duration:
                a.status = "pending_payment" if a.prepay_rub else "booked"
                a.expires_at = self.clock() + timedelta(minutes=30) if a.prepay_rub else None
            a.required_duration, a.save_personal_on_move, a.move_requested = None, False, False
            a.reminders_sent = []
            a.revision += 1
            await tx.put(a)
            await self._history(tx, a, actor, "move", old)
            await self.notify(tx, a, "Запись перенесена на выбранные дату и время")
            return a

    async def payment(self, actor, id, verify=False):
        async with self.repo.transaction() as tx:
            a, b, c = await self._access(tx, actor, id)
            if not occupies(a, self.clock()) or a.status != "pending_payment":
                raise RuleError("Запись не ожидает предоплату или время резерва истекло.")
            if verify:
                await self.owner(tx, actor, b.id)
                a.payment, a.status, a.expires_at = "verified", "booked", None
            else:
                if actor != c.user_id:
                    raise RuleError("Об оплате сообщает клиент.")
                a.payment = "reported"
            a.revision += 1
            await tx.put(a)
            await self._history(tx, a, actor, "verify_payment" if verify else "report_payment")
            await self.notify(
                tx,
                a,
                "Мастер подтвердил оплату"
                if verify
                else "Клиент сообщил об оплате. Мастеру нужно проверить перевод.",
            )

    async def finish(self, actor, id, status):
        async with self.repo.transaction() as tx:
            a, b, _ = await self._access(tx, actor, id)
            await self.owner(tx, actor, b.id)
            if a.status != "booked" or status not in ("completed", "no_show"):
                raise RuleError("Статус недоступен.")
            if self.clock() < a.start + timedelta(minutes=a.duration_min):
                raise RuleError("Отметить результат можно после окончания записи.")
            a.status, a.revision = status, a.revision + 1
            await tx.put(a)
            await self._history(tx, a, actor, status)

    async def tick(self):
        async with self.repo.transaction() as tx:
            now = self.clock()
            for a in await tx.list(Appointment):
                if (
                    a.status in ("awaiting_duration", "pending_payment")
                    and a.expires_at
                    and now >= a.expires_at
                ):
                    a.status, a.revision = "expired", a.revision + 1
                    await tx.put(a)
                    await self.notify(
                        tx, a, "Временный резерв истёк. Если перевод уже сделан, свяжитесь с мастером."
                    )
                if a.status != "booked" or a.start <= now:
                    continue
                for key, minutes in (("day", 1440), ("two_hours", 120)):
                    if key not in a.reminders_sent and now >= a.start - timedelta(minutes=minutes):
                        a.reminders_sent.append(key)
                        # A booking made shortly before a visit gets only the nearest reminder.
                        if key == "day" and now >= a.start - timedelta(minutes=120):
                            continue
                        await self.notify(tx, a, "Напоминание о предстоящем визите")
                await tx.put(a)

