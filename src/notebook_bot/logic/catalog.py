"""Временная логика в памяти для демонстрации Telegram-интерфейса."""

from dataclasses import dataclass

from .rules import RuleError, number, text


@dataclass
class DemoUser:
    """Пользователь, существующий только до перезапуска приложения."""

    id: str
    telegram_id: int
    name: str


@dataclass
class DemoBusiness:
    """Минимальный набор данных бизнеса, необходимый текущим экранам."""

    id: str
    owner_id: str
    name: str
    specialist: str
    description: str
    location: str
    address: str = ""
    comment: str = ""
    accepts_new: bool = True
    payment_details: str = ""
    buffer_min: int = 0
    notice_min: int = 120
    horizon_days: int = 30
    cancel_notice_min: int = 720
    move_notice_min: int = 360
    increment_min: int = 10


@dataclass(frozen=True)
class MenuBusiness:
    """Краткие данные бизнеса для кнопки главного меню."""

    id: str
    name: str


@dataclass(frozen=True)
class MainMenuData:
    """Данные, которые нужны Telegram-слою для построения главного меню."""

    businesses: tuple[MenuBusiness, ...]
    can_create_business: bool


class Catalog:
    """Заглушка сервисного слоя без базы данных и файлового хранилища."""

    MAX_BUSINESSES_PER_OWNER = 2

    def __init__(self, timezone: str):
        self.timezone = timezone
        self._users: dict[int, DemoUser] = {}
        self._businesses: dict[str, DemoBusiness] = {}
        self._favorites: set[tuple[str, str]] = set()
        self._next_business_id = 1

    async def user(self, telegram_id: int, name: str) -> DemoUser:
        """Возвращает демонстрационного пользователя или создаёт его в памяти."""

        user = self._users.get(telegram_id)
        if user is None:
            user = DemoUser(id=f"user-{telegram_id}", telegram_id=telegram_id, name=name)
            self._users[telegram_id] = user
        else:
            user.name = name
        return user

    async def get_main_menu_data(self, telegram_user_id: int) -> MainMenuData:
        """Подготавливает список бизнесов для главного меню."""

        user = self._users.get(telegram_user_id)
        businesses = (
            [item for item in self._businesses.values() if item.owner_id == user.id]
            if user
            else []
        )
        return MainMenuData(
            businesses=tuple(MenuBusiness(item.id, item.name) for item in businesses),
            can_create_business=len(businesses) < self.MAX_BUSINESSES_PER_OWNER,
        )

    async def owned_business(self, actor: str, business_id: str) -> DemoBusiness:
        """Проверяет, что выбранный бизнес принадлежит пользователю."""

        business = self._businesses.get(business_id)
        if business is None or business.owner_id != actor:
            raise RuleError("Этот бизнес недоступен.")
        return business

    async def create_business(
        self,
        actor: str,
        name: str,
        specialist: str,
        description: str,
        location: str,
        address: str,
        comment: str,
    ) -> DemoBusiness:
        """Создаёт бизнес в памяти, чтобы можно было пройти сценарий интерфейса."""

        owned = [item for item in self._businesses.values() if item.owner_id == actor]
        if len(owned) >= self.MAX_BUSINESSES_PER_OWNER:
            raise RuleError("Можно создать не более двух бизнесов.")
        if location not in ("at_master", "at_client", "online", "variable"):
            raise RuleError("Выберите формат работы.")

        business_id = f"demo-{self._next_business_id}"
        self._next_business_id += 1
        business = DemoBusiness(
            id=business_id,
            owner_id=actor,
            name=text(name, 80),
            specialist=text(specialist, 80),
            description=text(description, 1000),
            location=location,
            address=str(address).strip()[:500],
            comment=str(comment).strip()[:1000],
        )
        self._businesses[business.id] = business
        return business

    async def business(self, business_id: str) -> DemoBusiness:
        """Возвращает бизнес по демонстрационному публичному коду."""

        business = self._businesses.get(business_id)
        if business is None:
            raise RuleError("Специалист не найден. Проверьте ссылку или код.")
        return business

    async def services(self, business_id: str, actor: str | None = None) -> list:
        """Возвращает пустой список до реализации сценария услуг."""

        await self.business(business_id)
        return []

    async def favorites(self, actor: str) -> list[DemoBusiness]:
        """Возвращает избранных специалистов из памяти текущего процесса."""

        result = []
        for user_id, business_id in self._favorites:
            if user_id == actor and business_id in self._businesses:
                result.append(self._businesses[business_id])
        return result

    async def favorite(self, actor: str, business_id: str) -> None:
        """Переключает состояние избранного для демонстрации кнопки."""

        await self.business(business_id)
        key = (actor, business_id)
        if key in self._favorites:
            self._favorites.remove(key)
        else:
            self._favorites.add(key)

    async def update_business(
        self, actor: str, business_id: str, field: str, value
    ) -> DemoBusiness:
        """Обновляет поддерживаемое поле демонстрационного бизнеса."""

        business = await self.owned_business(actor, business_id)
        if field in ("name", "specialist"):
            value = text(value, 80)
        elif field in ("description", "address", "comment", "payment_details"):
            value = "" if value == "-" else text(value, 1000)
        elif field in ("buffer_min", "notice_min", "cancel_notice_min", "move_notice_min"):
            value = number(value, 0, 43200)
        elif field == "horizon_days":
            value = number(value, 1, 180)
        elif field == "increment_min":
            value = number(value, 1, 60)
        elif field == "accepts_new":
            value = bool(value)
        else:
            raise RuleError("Настройка недоступна.")
        setattr(business, field, value)
        return business
