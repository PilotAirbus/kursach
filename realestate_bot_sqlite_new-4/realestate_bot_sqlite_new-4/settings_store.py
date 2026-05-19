import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "keywords": ["сдам", "продам", "квартира"],
    "min_price": 10_000,
    "max_price": 100_000_000,
}


class SettingsStore:
    def __init__(self, path: str = "data/settings.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            for k, v in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, _serialize(v)),
                )

    def get(self) -> dict:
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT key, value FROM settings").fetchall()
            data = {r["key"]: _deserialize(r["key"], r["value"]) for r in rows}
            return {**DEFAULT_SETTINGS, **data}
        except Exception as exc:
            logger.error("Ошибка чтения настроек: %s", exc)
            return dict(DEFAULT_SETTINGS)

    def update(self, **kwargs) -> None:
        with self._connect() as conn:
            for k, v in kwargs.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (k, _serialize(v)),
                )
        logger.info("Настройки обновлены: %s", kwargs)


def _serialize(value) -> str:
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def _deserialize(key: str, value: str):
    if key == "keywords":
        return [v.strip() for v in value.split(",") if v.strip()]
    if key in ("min_price", "max_price"):
        return int(value)
    return value
