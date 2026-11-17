"""Сервисные операции главного меню, бизнеса, профиля и избранного."""

from dataclasses import dataclass

from ..models import Business, Favorite, Service, User
from ..repository import Repository
from .rules import RuleError, number, text


@dataclass(frozen=True)
class MenuBusiness:
    id: str
    name: str


@dataclass(frozen=True)
class MainMenuData:
    businesses: tuple[MenuBusiness, ...]
    can_create_business: bool


MAX_BUSINESSES_PER_OWNER = 2


class Catalog:
    """Связывает Telegram-интерфейс с предметными моделями и Repository."""

    def __init__(self, repo: Repository, timezone):
        self.repo = repo
        self.timezone = timezone

    async def user(self, telegram_id, name):
        async with self.repo.transaction() as transaction:
            users = await transaction.list(User, telegram_id=telegram_id)
            user = users[0] if users else User(telegram_id=telegram_id, name=name)
            user.name = name
            await transaction.put(user)
            return user

    async def get_main_menu_data(self, telegram_user_id):
        async with self.repo.transaction() as transaction:
            users = await transaction.list(User, telegram_id=telegram_user_id)
            businesses = (
                await transaction.list(Business, owner_id=users[0].id) if users else []
            )
            return MainMenuData(
                businesses=tuple(MenuBusiness(item.id, item.name) for item in businesses),
                can_create_business=len(businesses) < MAX_BUSINESSES_PER_OWNER,
            )

    async def owner(self, transaction, actor, business_id):
        business = await transaction.get(Business, business_id)
        if not business or business.owner_id != actor:
            raise RuleError("Этот бизнес недоступен.")
        return business

    async def owned_business(self, actor, business_id):
        """Совместимый метод для текущего обработчика настроек бизнеса."""

        async with self.repo.transaction() as transaction:
            return await self.owner(transaction, actor, business_id)

    async def create_business(
        self,
        actor,
        name,
        specialist,
        description,
        location,
        address,
        comment,
    ):
        async with self.repo.transaction() as transaction:
            businesses = await transaction.list(Business, owner_id=actor)
            if len(businesses) >= MAX_BUSINESSES_PER_OWNER:
                raise RuleError("Можно создать не более двух бизнесов.")
            if location not in ("at_master", "at_client", "online", "variable"):
                raise RuleError("Выберите формат работы кнопкой.")
            business = Business(
                owner_id=actor,
                name=text(name, 80),
                specialist=text(specialist, 80),
                description=text(description, 1000),
                location=location,
                address=address[:500],
                comment=comment[:1000],
            )
            await transaction.put(business)
            return business

    async def business(self, id):
        async with self.repo.transaction() as transaction:
            business = await transaction.get(Business, id)
            if not business:
                raise RuleError("Специалист не найден. Проверьте ссылку или код.")
            return business

    async def update_business(self, actor, id, field, value):
        async with self.repo.transaction() as transaction:
            business = await self.owner(transaction, actor, id)
            if field in ("name", "specialist"):
                value = text(value, 80)
            elif field in ("description", "address", "comment", "payment_details"):
                value = "" if value == "-" else text(value, 1000)
            elif field in ("buffer_min", "notice_min", "cancel_notice_min", "move_notice_min"):
                value = number(value, 0, 43_200)
            elif field == "horizon_days":
                value = number(value, 1, 180)
            elif field == "increment_min":
                value = number(value, 1, 60)
            elif field == "accepts_new":
                value = bool(value)
            else:
                raise RuleError("Настройка недоступна.")
            setattr(business, field, value)
            await transaction.put(business)
            return business

    async def services(self, business_id, actor=None):
        async with self.repo.transaction() as transaction:
            business = await transaction.get(Business, business_id)
            if not business:
                raise RuleError("Бизнес не найден.")
            services = await transaction.list(Service, business_id=business_id)
            return [
                service
                for service in services
                if service.active or business.owner_id == actor
            ]

    async def favorites(self, actor):
        async with self.repo.transaction() as transaction:
            result = []
            for favorite in await transaction.list(Favorite, user_id=actor):
                business = await transaction.get(Business, favorite.business_id)
                if business:
                    result.append(business)
            return result

    async def favorite(self, actor, business_id):
        async with self.repo.transaction() as transaction:
            if not await transaction.get(Business, business_id):
                raise RuleError("Специалист не найден.")
            values = await transaction.list(
                Favorite,
                user_id=actor,
                business_id=business_id,
            )
            if values:
                await transaction.delete(Favorite, values[0].id)
            else:
                await transaction.put(Favorite(user_id=actor, business_id=business_id))
