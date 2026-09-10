"""Реализация хранилища: чтение, запись и удаление данных в памяти с сохранением в JSON"""

import asyncio
import copy
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from .models import TABLES


class MemoryTransaction:
    def __init__(self, tables):
        self.tables = tables

    async def get(self, model, id):
        value = self.tables.get(model.__name__, {}).get(id)
        return copy.deepcopy(value)

    async def list(self, model, **filters):
        return [
            copy.deepcopy(value)
            for value in self.tables.get(model.__name__, {}).values()
            if all(getattr(value, key) == item for key, item in filters.items())
        ]

    async def put(self, value):
        self.tables.setdefault(type(value).__name__, {})[value.id] = copy.deepcopy(value)

    async def delete(self, model, id):
        self.tables.setdefault(model.__name__, {}).pop(id, None)


class MemoryRepository:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.tables = {}
        self.lock = asyncio.Lock()
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("schema_version") != 1:
                    raise ValueError("Unknown schema version")
                self.tables = {
                    name: {id: TABLES[name].model_validate(value) for id, value in rows.items()}
                    for name, rows in data["tables"].items()
                }
            except (ValueError, KeyError) as exc:
                raise RuntimeError("JSON повреждён: исходный файл сохранён, запуск остановлен") from exc

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            snapshot = copy.deepcopy(self.tables)
            tx = MemoryTransaction(snapshot)
            yield tx
            if snapshot != self.tables:
                if self.path:
                    await asyncio.to_thread(self._save, snapshot)
                self.tables = snapshot

    def _save(self, tables):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "tables": {
                name: {id: item.model_dump(mode="json") for id, item in rows.items()}
                for name, rows in tables.items()
            },
        }
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as file:
                name = file.name
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
