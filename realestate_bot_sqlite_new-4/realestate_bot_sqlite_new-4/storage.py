import logging
import sqlite3
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class SqliteStorage:
    def __init__(self, path: str = "data/listings.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='listings'")
            old_table_exists = cur.fetchone() is not None
            if old_table_exists:
                cur = conn.execute("PRAGMA table_info(listings)")
                columns = [col[1] for col in cur.fetchall()]
                if "channel" in columns and "channel_id" not in columns:
                    logger.warning("Обнаружена старая схема БД. Выполняется миграция...")
                    conn.executescript("""
                        CREATE TABLE IF NOT EXISTS parsing_log (
                            id               INTEGER PRIMARY KEY AUTOINCREMENT,
                            parsed_at        TEXT DEFAULT CURRENT_TIMESTAMP,
                            total_found      INTEGER,
                            new_listings     INTEGER,
                            updated_listings INTEGER
                        );
                        CREATE TABLE IF NOT EXISTS channels (
                            id         INTEGER PRIMARY KEY AUTOINCREMENT,
                            name       TEXT UNIQUE NOT NULL,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE TABLE IF NOT EXISTS users (
                            id         INTEGER PRIMARY KEY AUTOINCREMENT,
                            username   TEXT UNIQUE NOT NULL,
                            first_seen TEXT DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE TABLE IF NOT EXISTS price_history (
                            id          INTEGER PRIMARY KEY AUTOINCREMENT,
                            listing_id  TEXT NOT NULL,
                            price       INTEGER,
                            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                    conn.execute("""
                        CREATE TABLE listings_new (
                            listing_id   TEXT PRIMARY KEY,
                            channel_id   INTEGER NOT NULL,
                            user_id      INTEGER NOT NULL,
                            run_id       INTEGER,
                            title        TEXT,
                            description  TEXT,
                            price        INTEGER,
                            message_id   INTEGER,
                            message_date TEXT,
                            parsed_at    TEXT,
                            FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                            FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE CASCADE,
                            FOREIGN KEY (run_id)     REFERENCES parsing_log(id) ON DELETE SET NULL
                        )
                    """)
                    conn.execute("INSERT OR IGNORE INTO channels (name) SELECT DISTINCT channel FROM listings WHERE channel IS NOT NULL")
                    conn.execute("INSERT OR IGNORE INTO users (username) SELECT DISTINCT sender_username FROM listings WHERE sender_username IS NOT NULL")
                    conn.execute("""
                        INSERT INTO listings_new (listing_id, channel_id, user_id, title, description, price, message_id, message_date, parsed_at)
                        SELECT 
                            l.listing_id,
                            (SELECT id FROM channels WHERE name = l.channel),
                            (SELECT id FROM users WHERE username = l.sender_username),
                            l.title, l.description, l.price, l.message_id, l.message_date, l.parsed_at
                        FROM listings l
                    """)
                    conn.execute("DROP TABLE listings")
                    conn.execute("ALTER TABLE listings_new RENAME TO listings")
                    logger.info("Миграция завершена. Старые данные перенесены в новую структуру.")
                    return

                # Миграция: добавить run_id в уже нормализованную схему (без run_id)
                if "run_id" not in columns and "channel_id" in columns:
                    logger.warning("Добавление колонки run_id в таблицу listings...")
                    conn.execute("ALTER TABLE listings ADD COLUMN run_id INTEGER REFERENCES parsing_log(id) ON DELETE SET NULL")
                    logger.info("Колонка run_id добавлена.")

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS parsing_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    parsed_at        TEXT DEFAULT CURRENT_TIMESTAMP,
                    total_found      INTEGER,
                    new_listings     INTEGER,
                    updated_listings INTEGER
                );
                CREATE TABLE IF NOT EXISTS channels (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS users (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    username   TEXT UNIQUE NOT NULL,
                    first_seen TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS listings (
                    listing_id   TEXT PRIMARY KEY,
                    channel_id   INTEGER NOT NULL,
                    user_id      INTEGER NOT NULL,
                    run_id       INTEGER,
                    title        TEXT,
                    description  TEXT,
                    price        INTEGER,
                    message_id   INTEGER,
                    message_date TEXT,
                    parsed_at    TEXT,
                    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id)    REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (run_id)     REFERENCES parsing_log(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id  TEXT NOT NULL,
                    price       INTEGER,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE
                );
            """)

    def _get_or_create_channel(self, conn: sqlite3.Connection, channel_name: str) -> int:
        if not channel_name:
            channel_name = "unknown"
        cur = conn.execute("SELECT id FROM channels WHERE name = ?", (channel_name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO channels (name) VALUES (?)",
            (channel_name,)
        )
        return cur.lastrowid

    def _get_or_create_user(self, conn: sqlite3.Connection, username: str) -> int:
        if not username:
            username = "unknown"
        cur = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO users (username, first_seen) VALUES (?, ?)",
            (username, datetime.now().isoformat())
        )
        return cur.lastrowid

    def save(self, listings: list) -> dict:
        new_count = 0
        updated_count = 0
        total_found = len(listings)
        now = datetime.now().isoformat()

        with self._connect() as conn:
            # Создаём запись о запуске заранее, чтобы иметь run_id для привязки листингов.
            # Счётчики new_listings / updated_listings обновим после обхода.
            cur = conn.execute("""
                INSERT INTO parsing_log (parsed_at, total_found, new_listings, updated_listings)
                VALUES (?, ?, 0, 0)
            """, (now, total_found))
            run_id = cur.lastrowid

            for item in listings:
                lid = item.get("listing_id")
                if not lid:
                    continue

                channel_id = self._get_or_create_channel(conn, item.get("channel"))
                user_id = self._get_or_create_user(conn, item.get("sender_username"))

                existing = conn.execute(
                    "SELECT price FROM listings WHERE listing_id = ?", (lid,)
                ).fetchone()

                if existing:
                    old_price = existing["price"]
                    new_price = item.get("price")

                    conn.execute("""
                        UPDATE listings
                        SET channel_id = ?, user_id = ?, run_id = ?, title = ?, description = ?,
                            price = ?, message_id = ?, message_date = ?, parsed_at = ?
                        WHERE listing_id = ?
                    """, (
                        channel_id, user_id, run_id,
                        item.get("title"), item.get("description"),
                        new_price, item.get("message_id"),
                        item.get("message_date"), item.get("parsed_at"),
                        lid
                    ))
                    if old_price != new_price:
                        conn.execute("""
                            INSERT INTO price_history (listing_id, price, recorded_at)
                            VALUES (?, ?, ?)
                        """, (lid, new_price, datetime.now().isoformat()))
                    updated_count += 1
                else:
                    conn.execute("""
                        INSERT INTO listings
                            (listing_id, channel_id, user_id, run_id, title, description,
                             price, message_id, message_date, parsed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        lid, channel_id, user_id, run_id,
                        item.get("title"), item.get("description"),
                        item.get("price"), item.get("message_id"),
                        item.get("message_date"), item.get("parsed_at")
                    ))
                    if item.get("price") is not None:
                        conn.execute("""
                            INSERT INTO price_history (listing_id, price, recorded_at)
                            VALUES (?, ?, ?)
                        """, (lid, item.get("price"), datetime.now().isoformat()))
                    new_count += 1

            # Обновляем финальные счётчики в уже созданной записи запуска
            conn.execute("""
                UPDATE parsing_log
                SET new_listings = ?, updated_listings = ?
                WHERE id = ?
            """, (new_count, updated_count, run_id))

        total = self.count()
        logger.info(
            "SQLite-хранилище (5 таблиц): всего %d объявлений (новых: %d, обновлено: %d)",
            total, new_count, updated_count,
        )
        return {"new": new_count, "updated": updated_count}

    def load(self) -> list:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    l.listing_id,
                    c.name AS channel,
                    u.username AS sender_username,
                    l.message_id,
                    l.title,
                    l.description,
                    l.price,
                    l.parsed_at,
                    l.message_date
                FROM listings l
                JOIN channels c ON l.channel_id = c.id
                JOIN users u    ON l.user_id = u.id
                ORDER BY l.parsed_at DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def get_latest(self, n: int = 5) -> list:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    l.listing_id,
                    c.name AS channel,
                    u.username AS sender_username,
                    l.message_id,
                    l.title,
                    l.description,
                    l.price,
                    l.parsed_at,
                    l.message_date
                FROM listings l
                JOIN channels c ON l.channel_id = c.id
                JOIN users u    ON l.user_id = u.id
                ORDER BY l.parsed_at DESC
                LIMIT ?
            """, (n,)).fetchall()
        return [dict(r) for r in rows]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM listings")
            conn.execute("DELETE FROM parsing_log")
        logger.info("SQLite-хранилище очищено (listings + parsing_log)")