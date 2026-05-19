import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from config import Config

logger = logging.getLogger(__name__)

PRICE_PATTERNS = [
    r"(\d[\d\s.,]*)\s*(?:млн\.?\s*руб|млн\.?\s*₽|млн)",
    r"(\d[\d\s.,]*)\s*(?:тыс\.?\s*руб|тыс\.?\s*₽|тыс)",
    r"(\d[\d\s.,]*)\s*(?:руб|₽|рублей)",
    r"(?:цена|стоимость)[:\s]+(\d[\d\s.,]+)",
]


class TelegramChannelParser:
    def __init__(self, config: Config):
        self.config = config

    def parse_channel(self) -> list:
        if not self.config.TG_API_ID or not self.config.TG_API_HASH:
            logger.error(
                "TG_API_ID / TG_API_HASH не заданы! "
                "Зарегистрируй приложение на https://my.telegram.org "
                "и добавь переменные в .env"
            )
            return []

        try:
            return asyncio.run(self._async_parse())
        except RuntimeError as exc:
            logger.warning("asyncio.run() не сработал (%s), пробую через поток", exc)
            return self._run_in_new_thread()

    def _run_in_new_thread(self) -> list:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: asyncio.run(self._async_parse()))
            return future.result(timeout=120)

    async def _async_parse(self) -> list:
        client = TelegramClient(
            self.config.TG_SESSION,
            self.config.TG_API_ID,
            self.config.TG_API_HASH,
        )

        listings = []
        try:
            await client.start()
            entity = await client.get_entity(self.config.CHANNEL_URL)
            channel_name = getattr(entity, "username", "") or str(entity.id)

            async for message in client.iter_messages(entity, limit=self.config.MAX_POSTS):
                if not message.text:
                    continue
                item = self._parse_message(message, channel_name)
                if item:
                    listings.append(item)

        except SessionPasswordNeededError:
            logger.error(
                "Аккаунт защищён двухфакторной аутентификацией. "
                "Используй бот-аккаунт или убери 2FA."
            )
        except Exception as exc:
            logger.error("Ошибка парсинга: %s", exc, exc_info=True)
        finally:
            await client.disconnect()

        logger.info("Найдено объявлений: %d", len(listings))
        return listings

    def _parse_message(self, message, channel_name: str) -> Optional[dict]:
        text = message.text or ""
        if not text.strip():
            return None

        price = self._extract_price(text)

        # Пропускаем объявления без цены, если включён соответствующий флаг
        if self.config.REQUIRE_PRICE and price is None:
            logger.debug("Пропущено сообщение %s — цена не найдена", message.id)
            return None

        # Извлекаем username отправителя (если есть)
        sender = getattr(message, "sender", None)
        if sender is None:
            try:
                sender = message.post_author  # иногда доступен как строка
            except Exception:
                sender = None
        if isinstance(sender, str):
            sender_username = sender
        elif sender is not None:
            sender_username = getattr(sender, "username", None) or getattr(sender, "first_name", None)
        else:
            sender_username = None

        return {
            "listing_id": hashlib.md5(text.encode("utf-8")).hexdigest(),
            "channel": channel_name,
            "message_id": message.id,
            "sender_username": sender_username,
            "title": text[:120].replace("\n", " "),
            "description": text,
            "price": price,
            "parsed_at": datetime.now(tz=timezone.utc).isoformat(),
            "message_date": (
                message.date.isoformat() if message.date else None
            ),
        }

    def _extract_price(self, text: str) -> Optional[int]:
        for pattern in PRICE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = re.sub(r"\s", "", match.group(1))
                try:
                    val = float(raw.replace(",", "."))
                    if "млн" in pattern:
                        val *= 1_000_000
                    elif "тыс" in pattern:
                        val *= 1_000
                    return int(val)
                except (ValueError, TypeError):
                    pass
        return None

    def matches_filter(
        self,
        item: dict,
        keywords: list,
        min_price: Optional[int],
        max_price: Optional[int],
    ) -> bool:
        text = (item.get("description") or "").lower()
        price = item.get("price")

        if keywords and not any(kw.lower() in text for kw in keywords):
            return False
        if price is not None:
            if min_price is not None and price < min_price:
                return False
            if max_price is not None and price > max_price:
                return False
        elif min_price is not None or max_price is not None:
            # Если ценовой фильтр задан, а цены нет — объявление не проходит
            return False
        return True
