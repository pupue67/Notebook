"""Модели данных проекта: пользователи, бизнесы, услуги, расписание, клиенты, записи, история, избранное и уведомления."""

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id():
    return uuid4().hex[:16]


class Entity(BaseModel):
    id: str = Field(default_factory=new_id)


class User(Entity):
    telegram_id: int
    name: str


class Business(Entity):
    owner_id: str
    name: str
    specialist: str = ""
    description: str = ""
    location: Literal["at_master", "at_client", "online", "variable"] = "at_master"
    address: str = ""
    comment: str = ""
    accepts_new: bool = True
    buffer_min: int = 0
    increment_min: int = 10
    notice_min: int = 120
    horizon_days: int = 30
    cancel_notice_min: int = 720
    move_notice_min: int = 360
    payment_details: str = ""


class Service(Entity):
    business_id: str
    name: str
    price_rub: int = 0
    mode: Literal["fixed", "personal"] = "fixed"
    duration_min: int = 60
    active: bool = True
    prepay_rub: int = 0


class Interval(BaseModel):
    start: int
    end: int


class DayRule(Entity):
    business_id: str
    weekday: int
    intervals: list[Interval] = Field(default_factory=list)
    limit: int | None = None
    comment: str = ""


class DateOverride(Entity):
    business_id: str
    day: date
    intervals: list[Interval] | None = None
    closed: list[Interval] = Field(default_factory=list)
    day_off: bool = False
    limit_override: bool = False
    limit: int | None = None
    comment: str = ""
    address: str = ""


class TimeNote(Entity):
    business_id: str
    day: date
    minute: int
    comment: str = ""
    address: str = ""


class Client(Entity):
    business_id: str
    user_id: str | None = None
    name: str
    note: str = ""


class PersonalDuration(Entity):
    client_id: str
    service_id: str
    minutes: int


class Appointment(Entity):
    business_id: str
    client_id: str
    service_id: str
    start: datetime
    duration_min: int
    service_name: str
    price_rub: int
    status: Literal[
        "awaiting_duration",
        "pending_payment",
        "booked",
        "cancelled_client",
        "cancelled_master",
        "completed",
        "no_show",
        "expired",
    ] = "booked"
    buffer_min: int = 0
    prepay_rub: int = 0
    payment: Literal["none", "pending", "reported", "verified"] = "none"
    expires_at: datetime | None = None
    move_requested: bool = False
    required_duration: int | None = None
    save_personal_on_move: bool = False
    revision: int = 0
    reminders_sent: list[str] = Field(default_factory=list)


class History(Entity):
    appointment_id: str
    actor_id: str
    action: str
    at: datetime
    old_start: datetime | None = None
    new_start: datetime | None = None
    reason: str = ""


class Favorite(Entity):
    user_id: str
    business_id: str


class Notification(Entity):
    user_id: str
    text: str
    appointment_id: str | None = None
    sent: bool = False
    attempts: int = 0
    next_attempt: datetime | None = None


TABLES = {
    c.__name__: c
    for c in (
        User,
        Business,
        Service,
        DayRule,
        DateOverride,
        TimeNote,
        Client,
        PersonalDuration,
        Appointment,
        History,
        Favorite,
        Notification,
    )
}
