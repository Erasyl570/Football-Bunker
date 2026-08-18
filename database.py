import aiosqlite

DB_NAME = "bunker.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                chat_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'waiting',
                host_id INTEGER,
                scenario TEXT,
                winners_needed INTEGER DEFAULT 2,
                current_round INTEGER DEFAULT 1
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
                is_alive INTEGER DEFAULT 1,
                rev_pos INTEGER DEFAULT 0,
                rev_health INTEGER DEFAULT 0,
                rev_skill INTEGER DEFAULT 0,
                rev_inv INTEGER DEFAULT 0,
                rev_secret INTEGER DEFAULT 0,
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
        await db.execute("DELETE FROM players WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT OR REPLACE INTO lobbies (chat_id, status, host_id, current_round) VALUES (?, 'waiting', ?, 1)",
            (chat_id, host_id)
        )
        await db.commit()

async def update_lobby_scenario(chat_id: int, scenario: str, winners_needed: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE lobbies SET scenario = ?, winners_needed = ?, status = 'round1' WHERE chat_id = ?",
            (scenario, winners_needed, chat_id)
        )
        await db.commit()

async def set_lobby_status(chat_id: int, status: str, round_num: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        if round_num:
            await db.execute("UPDATE lobbies SET status = ?, current_round = ? WHERE chat_id = ?", (status, round_num, chat_id))
        else:
            await db.execute("UPDATE lobbies SET status = ? WHERE chat_id = ?", (status, chat_id))
        await db.commit()

async def get_lobby(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT status, host_id, scenario, winners_needed, current_round FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchone()

async def add_player(chat_id: int, user_id: int, username: str, pack: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO players 
            (chat_id, user_id, username, position, health, skill, inventory, secret, is_alive, rev_pos, rev_health, rev_skill, rev_inv, rev_secret)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 0, 0, 0)
        """, (chat_id, user_id, username, pack["position"], pack["health"], pack["skill"], pack["inventory"], pack["secret"]))
        await db.commit()

async def get_players(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, user_id, is_alive FROM players WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_alive_players(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, user_id FROM players WHERE chat_id = ? AND is_alive = 1", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_player_card(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT position, health, skill, inventory, secret,
                   rev_pos, rev_health, rev_skill, rev_inv, rev_secret, is_alive
            FROM players WHERE chat_id = ? AND user_id = ?
        """, (chat_id, user_id)) as cursor:
            return await cursor.fetchone()

async def reveal_trait(chat_id: int, user_id: int, trait: str):
    col_map = {
        "position": "rev_pos",
        "health": "rev_health",
        "skill": "rev_skill",
        "inventory": "rev_inv",
        "secret": "rev_secret"
    }
    if trait not in col_map:
        return
    col = col_map[trait]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE players SET {col} = 1 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        await db.commit()

async def add_vote(chat_id: int, voter_id: int, target_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO votes (chat_id, voter_id, target_id) VALUES (?, ?, ?)", (chat_id, voter_id, target_id))
        await db.commit()

async def get_voters(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT p.username FROM votes v 
            JOIN players p ON v.voter_id = p.user_id AND v.chat_id = p.chat_id
            WHERE v.chat_id = ?
        """, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

async def get_votes_detailed(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT target_id, p_target.username, COUNT(*) as cnt 
            FROM votes v
            JOIN players p_target ON v.target_id = p_target.user_id AND v.chat_id = p_target.chat_id
            WHERE v.chat_id = ?
            GROUP BY target_id
            ORDER BY cnt DESC
        """, (chat_id,)) as cursor:
            return await cursor.fetchall()

async def clear_votes(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def eliminate_player(chat_id: int, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET is_alive = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        await db.commit()
