import aiosqlite
import json
import os

DB_NAME = os.getenv("SQLITE_DB_PATH", "bunker.db")
STATS_DATABASE_URL = os.getenv("STATS_DATABASE_URL") or os.getenv("DATABASE_URL")
_stats_pool = None

def connect_db():
    return aiosqlite.connect(DB_NAME, timeout=10.0)

async def _init_stats_db():
    global _stats_pool
    if not STATS_DATABASE_URL:
        return
    try:
        import asyncpg
        _stats_pool = await asyncpg.create_pool(STATS_DATABASE_URL, min_size=1, max_size=3, command_timeout=10)
        async with _stats_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bunker_users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    games_played INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0
                )
            """)
    except Exception as e:
        print(f"[STATS DB] Не удалось подключить внешнюю БД: {e}")
        _stats_pool = None

async def init_db():
    await _init_stats_db()
    async with connect_db() as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                chat_id INTEGER PRIMARY KEY,
                host_id INTEGER,
                status TEXT,
                scenario TEXT,
                current_round INTEGER DEFAULT 1,
                current_turn_user_id INTEGER DEFAULT 0,
                tie_count INTEGER DEFAULT 0,
                skip_count INTEGER DEFAULT 0,
                total_ties INTEGER DEFAULT 0,
                total_votes INTEGER DEFAULT 0
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                pack_json TEXT,
                is_alive INTEGER DEFAULT 1,
                special_card TEXT DEFAULT '',
                special_used INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                shield_active INTEGER DEFAULT 0,
                muted_round INTEGER DEFAULT 0,
                vote_redirect_target INTEGER DEFAULT 0,
                vote_redirect_round INTEGER DEFAULT 0,
                private_card_message_id INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        
        # Миграция колонок для спец-карт, если таблица уже существовала
        for col, col_type in [
            ("special_card", "TEXT DEFAULT ''"),
            ("special_used", "INTEGER DEFAULT 0"),
            ("is_blocked", "INTEGER DEFAULT 0"),
            ("shield_active", "INTEGER DEFAULT 0"),
            ("muted_round", "INTEGER DEFAULT 0"),
            ("vote_redirect_target", "INTEGER DEFAULT 0"),
            ("vote_redirect_round", "INTEGER DEFAULT 0"),
            ("private_card_message_id", "INTEGER DEFAULT 0")
        ]:
            try:
                await db.execute(f"ALTER TABLE players ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        try:
            await db.execute("ALTER TABLE lobbies ADD COLUMN tie_count INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE lobbies ADD COLUMN skip_count INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE lobbies ADD COLUMN total_ties INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE lobbies ADD COLUMN total_votes INTEGER DEFAULT 0")
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                chat_id INTEGER,
                voter_id INTEGER,
                target_id INTEGER,
                PRIMARY KEY (chat_id, voter_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reveals (
                chat_id INTEGER,
                user_id INTEGER,
                trait TEXT,
                round_num INTEGER,
                PRIMARY KEY (chat_id, user_id, trait)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hidden_captains (
                chat_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                round_num INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# --- СТАТИСТИКА ---

async def get_user_profile(user_id: int, username: str):
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT games_played, wins FROM bunker_users WHERE user_id = $1", user_id)
            if not row:
                await conn.execute("INSERT INTO bunker_users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username", user_id, username)
                return (0, 0)
            await conn.execute("UPDATE bunker_users SET username = $2 WHERE user_id = $1", user_id, username)
            return (row["games_played"], row["wins"])
    async with connect_db() as db:
        async with db.execute("SELECT games_played, wins FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.execute("INSERT INTO users (user_id, username, games_played, wins) VALUES (?, ?, 0, 0)", (user_id, username))
                await db.commit()
                return (0, 0)
            return (row[0], row[1])

async def update_user_stats(user_id: int, username: str, won: bool):
    inc_win = 1 if won else 0
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bunker_users (user_id, username, games_played, wins)
                VALUES ($1, $2, 1, $3)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    games_played = bunker_users.games_played + 1,
                    wins = bunker_users.wins + EXCLUDED.wins
            """, user_id, username, inc_win)
        return
    async with connect_db() as db:
        await db.execute("""
            INSERT INTO users (user_id, username, games_played, wins)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                games_played = games_played + 1,
                wins = wins + excluded.wins
        """, (user_id, username, inc_win))
        await db.commit()

# --- ЛОГИКА ЛОББИ ---

async def create_lobby(chat_id: int, host_id: int):
    async with connect_db() as db:
        await db.execute("DELETE FROM lobbies WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM players WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM reveals WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT INTO lobbies (chat_id, host_id, status, current_round, current_turn_user_id) VALUES (?, ?, ?, 1, 0)",
            (chat_id, host_id, "lobby")
        )
        await db.commit()

async def get_lobby(chat_id: int):
    async with connect_db() as db:
        async with db.execute("SELECT status, host_id, scenario, current_round FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return (row[0], row[1], row[2], 0, row[3])
            return None

async def set_lobby_status(chat_id: int, status: str, current_round: int = None):
    async with connect_db() as db:
        if current_round is not None:
            await db.execute("UPDATE lobbies SET status = ?, current_round = ? WHERE chat_id = ?", (status, current_round, chat_id))
        else:
            await db.execute("UPDATE lobbies SET status = ? WHERE chat_id = ?", (status, chat_id))
        await db.commit()

async def set_current_turn(chat_id: int, user_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET current_turn_user_id = ? WHERE chat_id = ?", (user_id, chat_id))
        await db.commit()

async def get_current_turn(chat_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT current_turn_user_id FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_lobby_scenario(chat_id: int, scenario_text: str, current_round: int = 1):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET scenario = ?, current_round = ? WHERE chat_id = ?", (scenario_text, current_round, chat_id))
        await db.commit()

async def get_tie_count(chat_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT COALESCE(tie_count, 0) FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def increment_tie_count(chat_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET tie_count = COALESCE(tie_count, 0) + 1, total_ties = COALESCE(total_ties, 0) + 1 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def reset_tie_count(chat_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET tie_count = 0 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def get_skip_count(chat_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT COALESCE(skip_count, 0) FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def increment_skip_count(chat_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET skip_count = COALESCE(skip_count, 0) + 1 WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def get_game_stats(chat_id: int):
    async with connect_db() as db:
        async with db.execute("SELECT current_round, COALESCE(total_votes, 0), COALESCE(total_ties, 0), COALESCE(skip_count, 0) FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            lobby_row = await cursor.fetchone()
        async with db.execute("SELECT COUNT(*) FROM players WHERE chat_id = ? AND special_used = 1", (chat_id,)) as cursor:
            special_used = (await cursor.fetchone())[0]
        return {
            "rounds": lobby_row[0] if lobby_row else 0,
            "votes": lobby_row[1] if lobby_row else 0,
            "ties": lobby_row[2] if lobby_row else 0,
            "skips": lobby_row[3] if lobby_row else 0,
            "special_cards": special_used,
        }

# --- ИГРОКИ И ПАКЕТЫ ХАРАКТЕРИСТИК ---

async def add_player(chat_id: int, user_id: int, user_name: str, pack: dict):
    async with connect_db() as db:
        pack_json = json.dumps(pack, ensure_ascii=False)
        await db.execute("""
            INSERT OR REPLACE INTO players (chat_id, user_id, user_name, pack_json, is_alive)
            VALUES (?, ?, ?, ?, 1)
        """, (chat_id, user_id, user_name, pack_json))
        await db.commit()

async def get_players(chat_id: int):
    async with connect_db() as db:
        async with db.execute("SELECT user_name, user_id, pack_json FROM players WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_alive_players(chat_id: int):
    async with connect_db() as db:
        async with db.execute("SELECT user_name, user_id FROM players WHERE chat_id = ? AND is_alive = 1", (chat_id,)) as cursor:
            return await cursor.fetchall()

async def get_player_pack(chat_id: int, user_id: int) -> dict:
    async with connect_db() as db:
        async with db.execute("SELECT pack_json FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return {}

async def update_player_pack(chat_id: int, user_id: int, pack: dict):
    async with connect_db() as db:
        pack_json = json.dumps(pack, ensure_ascii=False)
        await db.execute("UPDATE players SET pack_json = ? WHERE chat_id = ? AND user_id = ?", (pack_json, chat_id, user_id))
        await db.commit()

async def set_private_card_message_id(chat_id: int, user_id: int, message_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE players SET private_card_message_id = ? WHERE chat_id = ? AND user_id = ?", (message_id, chat_id, user_id))
        await db.commit()

async def get_private_card_message_id(chat_id: int, user_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT private_card_message_id FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

# --- СПЕЦ-КАРТЫ (МЕТОДЫ) ---

async def set_player_special_card(chat_id: int, user_id: int, card_code: str):
    async with connect_db() as db:
        await db.execute("""
            UPDATE players 
            SET special_card = ?, special_used = 0, is_blocked = 0, shield_active = 0 
            WHERE chat_id = ? AND user_id = ?
        """, (card_code, chat_id, user_id))
        await db.commit()

async def get_player_special_info(chat_id: int, user_id: int):
    async with connect_db() as db:
        async with db.execute(
            "SELECT special_card, special_used, is_blocked, shield_active FROM players WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        ) as cursor:
            return await cursor.fetchone()

async def update_player_special_status(chat_id: int, user_id: int, special_used: int = None, is_blocked: int = None, shield_active: int = None):
    async with connect_db() as db:
        if special_used is not None:
            await db.execute("UPDATE players SET special_used = ? WHERE chat_id = ? AND user_id = ?", (special_used, chat_id, user_id))
        if is_blocked is not None:
            await db.execute("UPDATE players SET is_blocked = ? WHERE chat_id = ? AND user_id = ?", (is_blocked, chat_id, user_id))
        if shield_active is not None:
            await db.execute("UPDATE players SET shield_active = ? WHERE chat_id = ? AND user_id = ?", (shield_active, chat_id, user_id))
        await db.commit()

async def set_muted_round(chat_id: int, user_id: int, round_num: int):
    async with connect_db() as db:
        await db.execute("UPDATE players SET muted_round = ? WHERE chat_id = ? AND user_id = ?", (round_num, chat_id, user_id))
        await db.commit()

async def is_muted_for_round(chat_id: int, user_id: int, round_num: int) -> bool:
    async with connect_db() as db:
        async with db.execute("SELECT muted_round FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0] == round_num)

async def set_captain(chat_id: int, user_id: int, round_num: int):
    async with connect_db() as db:
        await db.execute("UPDATE lobbies SET current_turn_user_id = current_turn_user_id WHERE chat_id = ?", (chat_id,))
        # Храним скрытого капитана в отдельной таблице.
        await db.execute("CREATE TABLE IF NOT EXISTS hidden_captains (chat_id INTEGER PRIMARY KEY, user_id INTEGER, round_num INTEGER)")
        await db.execute("INSERT OR REPLACE INTO hidden_captains (chat_id, user_id, round_num) VALUES (?, ?, ?)", (chat_id, user_id, round_num))
        await db.commit()

async def get_captain(chat_id: int, round_num: int):
    async with connect_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS hidden_captains (chat_id INTEGER PRIMARY KEY, user_id INTEGER, round_num INTEGER)")
        async with db.execute("SELECT user_id FROM hidden_captains WHERE chat_id = ? AND round_num = ?", (chat_id, round_num)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_vote_redirect(chat_id: int, user_id: int, target_id: int, round_num: int):
    async with connect_db() as db:
        await db.execute("UPDATE players SET vote_redirect_target = ?, vote_redirect_round = ? WHERE chat_id = ? AND user_id = ?", (target_id, round_num, chat_id, user_id))
        await db.commit()

async def get_vote_redirect(chat_id: int, round_num: int):
    async with connect_db() as db:
        async with db.execute("SELECT user_id, vote_redirect_target FROM players WHERE chat_id = ? AND vote_redirect_round = ? AND vote_redirect_target != 0", (chat_id, round_num)) as cursor:
            row = await cursor.fetchone()
            return (row[0], row[1]) if row else None

async def clear_vote_redirect(chat_id: int, round_num: int):
    async with connect_db() as db:
        await db.execute("UPDATE players SET vote_redirect_target = 0, vote_redirect_round = 0 WHERE chat_id = ? AND vote_redirect_round = ?", (chat_id, round_num))
        await db.commit()

async def redirect_votes(chat_id: int, owner_id: int, target_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE votes SET target_id = ? WHERE chat_id = ? AND target_id = ?", (target_id, chat_id, owner_id))
        await db.commit()

# --- ВСКРЫТИЕ ХАРАКТЕРИСТИК ---

async def record_reveal(chat_id: int, user_id: int, trait: str, round_num: int):
    async with connect_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO reveals (chat_id, user_id, trait, round_num) VALUES (?, ?, ?, ?)",
            (chat_id, user_id, trait, round_num)
        )
        await db.commit()

async def is_trait_revealed(chat_id: int, user_id: int, trait: str) -> bool:
    async with connect_db() as db:
        async with db.execute("SELECT 1 FROM reveals WHERE chat_id = ? AND user_id = ? AND trait = ?", (chat_id, user_id, trait)) as cursor:
            return await cursor.fetchone() is not None

async def has_revealed_in_round(chat_id: int, user_id: int, round_num: int) -> bool:
    async with connect_db() as db:
        async with db.execute("SELECT 1 FROM reveals WHERE chat_id = ? AND user_id = ? AND round_num = ?", (chat_id, user_id, round_num)) as cursor:
            return await cursor.fetchone() is not None

async def get_unrevealed_traits(chat_id: int, user_id: int):
    all_traits = ["position", "age", "price", "health", "skill", "inventory", "secret"]
    async with connect_db() as db:
        async with db.execute("SELECT trait FROM reveals WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            revealed = [row[0] for row in await cursor.fetchall()]
    return [t for t in all_traits if t not in revealed]

# --- ГОЛОСОВАНИЕ И ИСКЛЮЧЕНИЕ ---

async def clear_votes(chat_id: int):
    async with connect_db() as db:
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def has_user_voted(chat_id: int, voter_id: int) -> bool:
    async with connect_db() as db:
        async with db.execute("SELECT 1 FROM votes WHERE chat_id = ? AND voter_id = ?", (chat_id, voter_id)) as cursor:
            return await cursor.fetchone() is not None

async def get_non_voted_alive_players(chat_id: int):
    async with connect_db() as db:
        query = """
            SELECT p.user_id, p.user_name 
            FROM players p
            WHERE p.chat_id = ? AND p.is_alive = 1
            AND p.user_id NOT IN (SELECT voter_id FROM votes WHERE chat_id = ?)
        """
        async with db.execute(query, (chat_id, chat_id)) as cursor:
            return await cursor.fetchall()

async def add_vote(chat_id: int, voter_id: int, target_id: int):
    async with connect_db() as db:
        await db.execute("INSERT OR REPLACE INTO votes (chat_id, voter_id, target_id) VALUES (?, ?, ?)", (chat_id, voter_id, target_id))
        await db.execute("UPDATE lobbies SET total_votes = COALESCE(total_votes, 0) + 1 WHERE chat_id = ?", (chat_id,))
        await db.commit()

async def get_username(chat_id: int, user_id: int) -> str:
    async with connect_db() as db:
        async with db.execute("SELECT user_name FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "Игрок"

async def get_voters_count(chat_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT COUNT(*) FROM votes WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_skip_votes_count(chat_id: int) -> int:
    async with connect_db() as db:
        async with db.execute("SELECT COUNT(*) FROM votes WHERE chat_id = ? AND target_id = 0", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_votes_detailed(chat_id: int):
    async with connect_db() as db:
        query = """
            SELECT v.target_id, p.user_name,
                   SUM(CASE WHEN hc.user_id = v.voter_id THEN 2 ELSE 1 END) as cnt
            FROM votes v
            JOIN players p ON v.chat_id = p.chat_id AND v.target_id = p.user_id
            LEFT JOIN hidden_captains hc
              ON hc.chat_id = v.chat_id
             AND hc.round_num = (SELECT current_round FROM lobbies WHERE chat_id = v.chat_id)
            WHERE v.chat_id = ? AND v.target_id != 0
            GROUP BY v.target_id, p.user_name
            ORDER BY cnt DESC
        """
        async with db.execute(query, (chat_id,)) as cursor:
            return await cursor.fetchall()

async def eliminate_player(chat_id: int, user_id: int):
    async with connect_db() as db:
        await db.execute("UPDATE players SET is_alive = 0 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        await db.commit()