from datetime import datetime, time, timedelta
from ..models import Appointment, DateOverride, DayRule, PersonalDuration, TimeNote

OCCUPYING = {"booked", "pending_payment", "awaiting_duration"}


def occupies(a, now):
    return a.status in OCCUPYING and (a.expires_at is None or a.expires_at > now)


async def effective_day(tx, business, day):
    rules = await tx.list(DayRule, business_id=business.id, weekday=day.weekday())
    rule = rules[0] if rules else None
    overrides = await tx.list(DateOverride, business_id=business.id, day=day)
    override = overrides[0] if overrides else None
    intervals = rule.intervals if rule else []
    limit = rule.limit if rule else None
    closed = []
    if override:
        if override.intervals is not None:
            intervals = override.intervals
        if override.limit_override:
            limit = override.limit
        closed = override.closed
        if override.day_off:
            intervals = []
    return intervals, closed, limit, rule, override


async def duration_for(tx, service, client_id):
    if service.mode == "personal" and client_id:
        values = await tx.list(PersonalDuration, client_id=client_id, service_id=service.id)
        if values:
            return values[0].minutes, True
    return service.duration_min, service.mode == "fixed"


async def available_times(tx, business, service, client_id, day, now, *,
                          exclude=None, duration=None, existing=False):
    if not existing and (not business.accepts_new or not service.active):
        return []
    if (day < now.date()
            or day > now.date() + timedelta(days=business.horizon_days)
            or (business.booking_until is not None and day > business.booking_until)):
        return []
    work, closed, limit, _, _ = await effective_day(tx, business, day)
    appointments = [a for a in await tx.list(Appointment, business_id=business.id)
                    if a.id != exclude and a.start.date() == day and occupies(a, now)]
    if limit is not None and len(appointments) >= limit:
        return []
    if duration is None:
        duration, _ = await duration_for(tx, service, client_id)
    buffer = business.buffer_min
    result = []
    for interval in work:
        first = ((interval.start + business.increment_min - 1) // business.increment_min) * business.increment_min
        for start in range(first, interval.end - duration + 1, business.increment_min):
            end = start + duration
            stamp = datetime.combine(day, time(start // 60, start % 60), tzinfo=now.tzinfo)
            if stamp < now + timedelta(minutes=business.notice_min) or stamp <= now:
                continue
            if any(start < block.end and end > block.start for block in closed):
                continue
            clash = False
            for appointment in appointments:
                other = appointment.start.hour * 60 + appointment.start.minute
                gap = max(buffer, appointment.buffer_min)
                if start < other + appointment.duration_min + gap and end + gap > other:
                    clash = True
                    break
            if not clash:
                result.append(stamp)
    return sorted(set(result))


async def visit_context(tx, business, start):
    _, _, _, rule, override = await effective_day(tx, business, start.date())
    notes = await tx.list(TimeNote, business_id=business.id, day=start.date(),
                          minute=start.hour * 60 + start.minute)
    note = notes[0] if notes else None
    comments = [business.comment, rule.comment if rule else "",
                override.comment if override else "", note.comment if note else ""]
    address = (note.address if note else "") or (override.address if override else "") or business.address
    return address, "\n".join(dict.fromkeys(c for c in comments if c))
