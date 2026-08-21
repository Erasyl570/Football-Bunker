import json
import aiosqlite

DB_NAME = "game.db"


def connect_db():
    return aiosqlite.connect(DB_NAME)


async def init_db():
    async with connect_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER,
                user_id INTEGER,
                full_name TEXT,
                pack_json TEXT DEFAULT '{}',
                special_card TEXT DEFAULT '',
                special_used INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                shield_active INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """
        )

        # Миграции на случай, если таблица уже существовала без новых колонок
        for column, col_type in [
            ("special_card", "TEXT DEFAULT ''"),
            ("special_used", "INTEGER DEFAULT 0"),
            ("is_blocked", "INTEGER DEFAULT 0"),
            ("shield_active", "INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(
                    f"ALTER TABLE players ADD COLUMN {column} {col_type}"
                )
            except Exception:
                pass

        await db.commit()


async def set_player_special_card(chat_id: int, user_id: int, card_code: str):
    async with connect_db() as db:
        await db.execute(
            """
            UPDATE players 
            SET special_card = ?, special_used = 0, is_blocked = 0, shield_active = 0 
            WHERE chat_id = ? AND user_id = ?
        """,
            (card_code, chat_id, user_id),
        )
        await db.commit()


async def get_player_special_info(chat_id: int, user_id: int):
    async with connect_db() as db:
        async with db.execute(
            """
            SELECT special_card, special_used, is_blocked, shield_active 
            FROM players 
            WHERE chat_id = ? AND user_id = ?
        """,
            (chat_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row if row else ("", 0, 0, 0)


async def update_player_special_status(
    chat_id: int,
    user_id: int,
    special_used: int = None,
    is_blocked: int = None,
    shield_active: int = None,
):
    async with connect_db() as db:
        if special_used is not None:
            await db.execute(
                "UPDATE players SET special_used = ? WHERE chat_id = ? AND user_id = ?",
                (special_used, chat_id, user_id),
            )
        if is_blocked is not None:
            await db.execute(
                "UPDATE players SET is_blocked = ? WHERE chat_id = ? AND user_id = ?",
                (is_blocked, chat_id, user_id),
            )
        if shield_active is not None:
            await db.execute(
                "UPDATE players SET shield_active = ? WHERE chat_id = ? AND user_id = ?",
                (shield_active, chat_id, user_id),
            )
        await db.commit()


async def update_player_pack(chat_id: int, user_id: int, pack: dict):
    async with connect_db() as db:
        pack_json = json.dumps(pack, ensure_ascii=False)
        await db.execute(
            "UPDATE players SET pack_json = ? WHERE chat_id = ? AND user_id = ?",
            (pack_json, chat_id, user_id),
        )
        await db.commit()


async def get_all_players_in_chat(chat_id: int):
    async with connect_db() as db:
        async with db.execute(
            "SELECT user_id, full_name FROM players WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            return await cursor.fetchall()
