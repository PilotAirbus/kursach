import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str = field(
        default_factory=lambda: os.getenv("BOT_TOKEN", "")
    )
    CHANNEL_URL: str = field(
        default_factory=lambda: os.getenv(
            "CHANNEL_URL", "https://t.me/+VkHpZFnmtvVlNDUy"
        )
    )
    MAX_POSTS: int = field(
        default_factory=lambda: int(os.getenv("MAX_POSTS", "50"))
    )
    TG_API_ID: int = field(
        default_factory=lambda: int(os.getenv("TG_API_ID", "0"))
    )
    TG_API_HASH: str = field(
        default_factory=lambda: os.getenv("TG_API_HASH", "")
    )
    TG_SESSION: str = field(
        default_factory=lambda: os.getenv("TG_SESSION", "realestate_bot")
    )
    DB_PATH: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "data/listings.db")
    )
    SETTINGS_DB_PATH: str = field(
        default_factory=lambda: os.getenv("SETTINGS_DB_PATH", "data/settings.db")
    )
    PARSE_INTERVAL_HOURS: int = field(
        default_factory=lambda: int(os.getenv("PARSE_INTERVAL_HOURS", "1"))
    )
    DEFAULT_KEYWORDS: list = field(
        default_factory=lambda: ["сдам", "продам", "квартира"]
    )
    DEFAULT_MIN_PRICE: int = 10_000
    DEFAULT_MAX_PRICE: int = 100_000_000
    REQUIRE_PRICE: bool = field(
        default_factory=lambda: os.getenv("REQUIRE_PRICE", "true").lower() in ("1", "true", "yes")
    )

    def __post_init__(self):
        if not self.BOT_TOKEN:
            raise ValueError(
                "BOT_TOKEN не задан. "
                "Создайте файл .env и укажите BOT_TOKEN=... "
                "или задайте переменную окружения."
            )
