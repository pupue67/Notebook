"""Конфигурация приложения: загрузка токена бота, хранилища, часового пояса и других настроек из .env"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.request import getproxies
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    storage: str
    data_file: Path
    timezone: ZoneInfo
    proxy: str | None = None

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "")
        if not token or token.startswith("put_"):
            raise ValueError("Укажите BOT_TOKEN в локальном .env")
        storage = os.getenv("STORAGE", "json")
        if storage not in ("memory", "json"):
            raise ValueError("В этой версии STORAGE должен быть json или memory")
        proxy = os.getenv("BOT_PROXY", "system")
        if proxy == "system":
            proxies = getproxies()
            proxy = proxies.get("https") or proxies.get("http")
        elif proxy == "direct":
            proxy = None
        return cls(
            token=token,
            storage=storage,
            data_file=Path(os.getenv("DATA_FILE", "data/notebook.json")),
            timezone=ZoneInfo(os.getenv("CITY_TIMEZONE", "Asia/Yekaterinburg")),
            proxy=proxy or None,
        )

