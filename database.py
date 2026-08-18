import aiosqlite

DB_NAME = "bunker.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                chat_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'waiting',
                host_id INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                position TEXT,
                health TEXT,
                skill TEXT,
                inventory TEXT,
                secret TEXT,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        await db.commit()

async def create_lobby(chat_id: int, host_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM players WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT OR REPLACE INTO lobbies (chat_id, status, host_id) VALUES (?, 'waiting', ?)",
            (chat_id, host_id)
        )
        await db.commit()

async def add_player(chat_id: int, user_id: int, username: str, pack: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO players 
            (chat_id, user_id, username, position, health, skill, inventory, secret)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, user_id, username, pack["position"], pack["health"], pack["skill"], pack["inventory"], pack["secret"]))
        await db.commit()

async def get_players(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, user_id FROM players WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_player_card(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT position, health, skill, inventory, secret FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            return await cursor.fetchone()
