import logging
import os
import threading

import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from telebot import apihelper

from config import Config
from parser import TelegramChannelParser
from storage import SqliteStorage
from settings_store import SettingsStore

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

config = Config()
bot = telebot.TeleBot(config.BOT_TOKEN)
storage = SqliteStorage(config.DB_PATH)
settings_store = SettingsStore(config.SETTINGS_DB_PATH)
parser = TelegramChannelParser(config)
scheduler = BackgroundScheduler(timezone="UTC")

_parse_lock = threading.Lock()


def run_parsing_job() -> dict:
    if not _parse_lock.acquire(blocking=False):
        logger.warning("Парсинг уже запущен, пропускаем")
        return {"new": 0, "updated": 0}

    try:
        logger.info("Запуск парсинга канала %s", config.CHANNEL_URL)
        settings = settings_store.get()

        all_listings = parser.parse_channel()

        filtered = [
            item for item in all_listings
            if parser.matches_filter(
                item,
                keywords=settings["keywords"],
                min_price=settings["min_price"],
                max_price=settings["max_price"],
            )
        ]

        logger.info(
            "Всего получено: %d, после фильтрации: %d",
            len(all_listings), len(filtered),
        )

        result = storage.save(filtered)
        return result

    except Exception as exc:
        logger.error("Ошибка в run_parsing_job: %s", exc, exc_info=True)
        return {"new": 0, "updated": 0}
    finally:
        _parse_lock.release()


@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    text = (
        "🏠 <b>Бот парсинга недвижимости</b>\n\n"
        "Собираю объявления из Telegram-канала и сохраняю в SQLite.\n\n"
        "<b>Команды:</b>\n"
        "/parse — запустить парсинг прямо сейчас\n"
        "/latest — последние 5 объявлений\n"
        "/stats — статистика хранилища\n"
        "/settings — текущие настройки фильтров\n"
        "/set_keywords кв,сдам — задать ключевые слова\n"
        "/set_price 5000 50000 — задать диапазон цен\n"
        "/clear — очистить хранилище\n"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(commands=["parse"])
def cmd_parse(message):
    bot.send_message(message.chat.id, "🔄 Парсинг запущен, подождите...")
    result = run_parsing_job()
    bot.send_message(
        message.chat.id,
        f"✅ Готово!\n"
        f"Новых объявлений: <b>{result['new']}</b>\n"
        f"Обновлено: <b>{result['updated']}</b>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["latest"])
def cmd_latest(message):
    items = storage.get_latest(5)
    if not items:
        return bot.send_message(message.chat.id, "📭 Хранилище пустое. Запусти /parse")

    for item in items:
        price_str = f"{item['price']:,} ₽".replace(",", " ") if item.get("price") else "цена не указана"
        date_str = (item.get("parsed_at") or "")[:10]

        sender = item.get("sender_username")
        if sender:
            author_str = f"👤 @{sender}\n📢 Канал: @{item.get('channel', '—')}"
        else:
            author_str = f"📢 @{item.get('channel', '—')}"

        text = (
            f"🏠 <b>{item.get('title', 'Без заголовка')}</b>\n"
            f"💰 {price_str}\n"
            f"📅 {date_str}\n"
            f"{author_str}"
        )
        bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    total = storage.count()
    settings = settings_store.get()
    keywords = ", ".join(settings["keywords"]) or "не заданы"
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"📦 Всего в хранилище: <b>{total}</b>\n"
        f"🔑 Ключевые слова: {keywords}\n"
        f"💰 Диапазон цен: {settings['min_price']:,} — {settings['max_price']:,} ₽\n"
        f"🔄 Автопарсинг каждые {config.PARSE_INTERVAL_HOURS} ч."
    ).replace(",", " ")
    bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    settings = settings_store.get()
    keywords = ", ".join(settings["keywords"]) or "не заданы"
    text = (
        f"⚙️ <b>Настройки фильтров</b>\n\n"
        f"🔑 Ключевые слова: <code>{keywords}</code>\n"
        f"💰 Мин. цена: <code>{settings['min_price']}</code>\n"
        f"💰 Макс. цена: <code>{settings['max_price']}</code>\n\n"
        f"Чтобы изменить:\n"
        f"/set_keywords слово1,слово2\n"
        f"/set_price 5000 50000"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(commands=["set_keywords"])
def cmd_set_keywords(message):
    args = message.text.replace("/set_keywords", "").strip()
    keywords = [k.strip() for k in args.split(",") if k.strip()]

    if not keywords:
        return bot.send_message(
            message.chat.id,
            "❌ Укажи ключевые слова через запятую.\n"
            "Пример: /set_keywords квартира,сдам,аренда"
        )

    settings_store.update(keywords=keywords)
    bot.send_message(
        message.chat.id,
        f"✅ Ключевые слова обновлены:\n<code>{', '.join(keywords)}</code>",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["set_price"])
def cmd_set_price(message):
    parts = message.text.split()
    try:
        if len(parts) != 3:
            raise ValueError
        min_price = int(parts[1])
        max_price = int(parts[2])
        if min_price >= max_price:
            raise ValueError
    except (ValueError, IndexError):
        return bot.send_message(
            message.chat.id,
            "❌ Неверный формат.\n"
            "Пример: /set_price 10000 5000000"
        )

    settings_store.update(min_price=min_price, max_price=max_price)
    bot.send_message(
        message.chat.id,
        f"✅ Диапазон цен обновлён: {min_price:,} — {max_price:,} ₽".replace(",", " "),
    )


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    storage.clear()
    bot.send_message(message.chat.id, "🗑 Хранилище очищено.")


@bot.message_handler(func=lambda m: True)
def unknown_command(message):
    bot.send_message(
        message.chat.id,
        "❓ Неизвестная команда. Введи /help для списка команд."
    )


def main():
    logger.info("═══ Бот запускается ═══")

    try:
        bot.delete_webhook()
        logger.info("Webhook успешно удалён через bot.delete_webhook()")

        apihelper.delete_webhook(config.BOT_TOKEN)
        logger.info("Webhook успешно удалён через apihelper")
    except Exception as e:
        logger.warning(f"Ошибка при удалении webhook: {e}")

    logger.info("Первый запуск парсинга...")
    result = run_parsing_job()
    logger.info(
        "Первый парсинг завершён: новых=%d, обновлено=%d",
        result["new"], result["updated"],
    )

    scheduler.add_job(
        run_parsing_job,
        "interval",
        hours=config.PARSE_INTERVAL_HOURS,
        id="auto_parse",
    )
    scheduler.start()
    logger.info(
        "Планировщик запущен (интервал: %d ч.)",
        config.PARSE_INTERVAL_HOURS,
    )

    logger.info("Бот готов к работе!")

    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"Ошибка при polling: {e}")
        raise


if __name__ == "__main__":
    main()