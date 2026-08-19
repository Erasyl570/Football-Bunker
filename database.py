import aiosqlite
import json

DB_NAME = "bunker.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                chat_id INTEGER PRIMARY KEY,
                host_id INTEGER,
                status TEXT,
                scenario TEXT,
                current_round INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                pack_json TEXT,
                is_alive INTEGER DEFAULT 1,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                chat_id INTEGER,
                voter_id INTEGER,
                target_id INTEGER,
                PRIMARY KEY (chat_id, voter_id)
            )
        """)
        await db.commit()

async def create_lobby(chat_id: int, host_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM lobbies WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM players WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT INTO lobbies (chat_id, host_id, status) VALUES (?, ?, ?)",
            (chat_id, host_id, "lobby")
        )
        await db.commit()

async def get_lobby(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT status, host_id, scenario, current_round FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return (row[0], row[1], row[2], 0, row[3]) # Совместимость индексов c bot.py
            return None

async def set_lobby_status(chat_id: int, status: str, current_round: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        if current_round is not None:
            await db.execute("UPDATE lobbies SET status = ?, current_round = ? WHERE chat_id = ?", (status, current_round, chat_id))
        else:
            await db.execute("UPDATE lobbies SET status = ? WHERE chat_id = ?", (status, chat_id))
        await db.commit()

async def update_lobby_scenario(chat_id: int, scenario_text: str, current_round: int = 1):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE lobbies SET scenario = ?, current_round = ? WHERE chat_id = ?", (scenario_text, current_round, chat_id))
        await db.commit()

async def add_player(chat_id: int, user_id: int, user_name: str, pack: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        pack_json = json.dumps(pack, ensure_ascii=False)
        await db.execute("""
            INSERT OR REPLACE INTO players (chat_id, user_id, user_name, pack_json, is_alive)
            VALUES (?, ?, ?, ?, 1)
        """, (chat_id, user_id, user_name, pack_json))
        await db.commit()

async def get_players(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_name, user_id, pack_json FROM players WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_alive_players(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_name, user_id FROM players WHERE chat_id = ? AND is_alive = 1", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_player_card(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_name, pack_json, is_alive FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            # Для валидации выбывания в bot.py (индекс 10 -> is_alive)
            return [row[0], row[1], 0, 0, 0, 0, 0, 0, 0, 0, row[2]]

async def get_player_pack(chat_id: int, user_id: int) -> dict:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT pack_json FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return {}

async def clear_votes(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def has_user_voted(chat_id: int, voter_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM votes WHERE chat_id = ? AND voter_id = ?", (chat_id, voter_id)) as cursor:
            return await cursor.fetchone() is not None

async def add_vote(chat_id: int, voter_id: int, target_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO votes (chat_id, voter_id, target_id) VALUES (?, ?, ?)", (chat_id, voter_id, target_id))
        await db.commit()

async def get_username(chat_id: int, user_id: int) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_name FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "Игрок"

async def get_voters_count(chat_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM votes WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_votes_detailed(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        query = """
            SELECT v.target_id, p.user_name, COUNT(v.voter_id) as cnt
            FROM votes v
            JOIN players p ON v.chat_id = p.chat_id AND v.target_id = p.user_id
            WHERE v.chat_id = ?
            GROUP BY v.target_id
            ORDER BY cnt DESC
        """
        async with db.execute(query, (chat_id,)) as cursor:
            return await cursor.fetchall()

async def eliminate_player(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET is_alive = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        await db.commit()
