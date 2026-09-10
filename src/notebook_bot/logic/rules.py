"""Минимальные преобразования ввода для работающих демонстрационных форм. В разработке PO"""

from dataclasses import asdict, dataclass
from datetime import date


class RuleError(Exception):
    """Ожидаемая ошибка пользовательского ввода."""


@dataclass(frozen=True)
class DemoInterval:
    """Простой интервал времени для формы без зависимости от моделей данных."""

    start: int
    end: int

    def model_dump(self) -> dict[str, int]:
        """Возвращает словарь в формате, который ожидает общая форма."""

        return asdict(self)


def text(value, limit=1000):
    """Убирает пробелы и ограничивает длину демонстрационного текста."""

    result = str(value).strip()
    if not result:
        raise RuleError("Значение не может быть пустым.")
    if len(result) > limit:
        raise RuleError(f"Допустимо не более {limit} символов.")
    return result


def number(value, minimum=0, maximum=1_000_000):
    """Преобразует ввод в целое число в простом допустимом диапазоне."""

    try:
        result = int(value)
    except (TypeError, ValueError):
        raise RuleError("Введите целое число.") from None
    if not minimum <= result <= maximum:
        raise RuleError(f"Введите число от {minimum} до {maximum}.")
    return result


def minute(value):
    """Преобразует время ЧЧ:ММ в количество минут от начала суток."""

    try:
        hour, minute_value = map(int, str(value).strip().split(":"))
    except (TypeError, ValueError):
        raise RuleError("Введите время в формате ЧЧ:ММ.") from None
    if hour not in range(25) or minute_value not in range(60):
        raise RuleError("Введите корректное время.")
    result = hour * 60 + minute_value
    if result > 1440:
        raise RuleError("Введите корректное время.")
    return result


def intervals(value):
    """Разбирает интервалы для будущих форм без проверки бизнес-правил расписания."""

    result = []
    for part in str(value).replace(";", ",").split(","):
        try:
            start, end = part.strip().split("-")
        except ValueError:
            raise RuleError("Введите интервалы, например: 10:00-13:00, 15:00-20:00.") from None
        result.append(DemoInterval(start=minute(start), end=minute(end)))
    return result


def local_date(value):
    """Преобразует дату из формата ГГГГ-ММ-ДД."""

    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise RuleError("Введите дату в формате ГГГГ-ММ-ДД.") from None
