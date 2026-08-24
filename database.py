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
                );
                CREATE TABLE IF NOT EXISTS bunker_economy (
                    user_id BIGINT PRIMARY KEY,
                    coins INTEGER NOT NULL DEFAULT 0,
                    daily_date TEXT NOT NULL DEFAULT '',
                    daily_games INTEGER NOT NULL DEFAULT 0,
                    daily_wins INTEGER NOT NULL DEFAULT 0,
                    daily_reveals INTEGER NOT NULL DEFAULT 0,
                    daily_votes INTEGER NOT NULL DEFAULT 0,
                    equipped_title TEXT NOT NULL DEFAULT '',
                    equipped_frame TEXT NOT NULL DEFAULT '',
                    equipped_card_theme TEXT NOT NULL DEFAULT 'classic',
                    equipped_victory TEXT NOT NULL DEFAULT 'classic',
                    equipped_badge TEXT NOT NULL DEFAULT '',
                    owner_granted INTEGER NOT NULL DEFAULT 0,
                    premium_until TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS bunker_purchases (
                    user_id BIGINT NOT NULL,
                    item_id TEXT NOT NULL,
                    PRIMARY KEY (user_id, item_id)
                )
            """)
            # Мягкая миграция для экономики v9 → v10.
            await conn.execute("ALTER TABLE bunker_economy ADD COLUMN IF NOT EXISTS equipped_badge TEXT NOT NULL DEFAULT ''")
            await conn.execute("ALTER TABLE bunker_economy ADD COLUMN IF NOT EXISTS premium_until TEXT NOT NULL DEFAULT ''")
            await conn.execute("CREATE TABLE IF NOT EXISTS bunker_star_payments (charge_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, product_id TEXT NOT NULL, stars INTEGER NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())")
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

        # Экономика: мягкая миграция для уже существующих SQLite-инсталляций.
        try:
            await db.execute("CREATE TABLE IF NOT EXISTS bunker_economy (user_id INTEGER PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0, daily_date TEXT NOT NULL DEFAULT '', daily_games INTEGER NOT NULL DEFAULT 0, daily_wins INTEGER NOT NULL DEFAULT 0, daily_reveals INTEGER NOT NULL DEFAULT 0, daily_votes INTEGER NOT NULL DEFAULT 0, equipped_title TEXT NOT NULL DEFAULT '', equipped_frame TEXT NOT NULL DEFAULT '', equipped_card_theme TEXT NOT NULL DEFAULT 'classic', equipped_victory TEXT NOT NULL DEFAULT 'classic', equipped_badge TEXT NOT NULL DEFAULT '', owner_granted INTEGER NOT NULL DEFAULT 0, premium_until TEXT NOT NULL DEFAULT '')")
            await db.execute("ALTER TABLE bunker_economy ADD COLUMN equipped_badge TEXT NOT NULL DEFAULT ''")
            await db.execute("CREATE TABLE IF NOT EXISTS bunker_star_payments (charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, product_id TEXT NOT NULL, stars INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            try:
                await db.execute("ALTER TABLE bunker_economy ADD COLUMN premium_until TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass
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


# --- ЭКОНОМИКА / МАГАЗИН ---

def _economy_date():
    from datetime import datetime, timedelta, timezone
    # Казахстанский часовой пояс по умолчанию; можно изменить через ECONOMY_TZ_OFFSET.
    offset = int(os.getenv("ECONOMY_TZ_OFFSET", "5"))
    return (datetime.now(timezone.utc) + timedelta(hours=offset)).date().isoformat()

async def _ensure_economy_user_sqlite(db, user_id: int):
    await db.execute("INSERT OR IGNORE INTO users (user_id, username, games_played, wins) VALUES (?, '', 0, 0)", (user_id,))

async def get_coins(user_id: int) -> int:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT coins FROM bunker_economy WHERE user_id = $1", user_id)
            if not row:
                await conn.execute("INSERT INTO bunker_economy (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
                return 0
            return int(row["coins"])
    async with connect_db() as db:
        await _ensure_economy_user_sqlite(db, user_id)
        # SQLite fallback stores economy in a dedicated table.
        await db.execute("CREATE TABLE IF NOT EXISTS bunker_economy (user_id INTEGER PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0, daily_date TEXT NOT NULL DEFAULT '', daily_games INTEGER NOT NULL DEFAULT 0, daily_wins INTEGER NOT NULL DEFAULT 0, daily_reveals INTEGER NOT NULL DEFAULT 0, daily_votes INTEGER NOT NULL DEFAULT 0, equipped_title TEXT NOT NULL DEFAULT '', equipped_frame TEXT NOT NULL DEFAULT '', equipped_card_theme TEXT NOT NULL DEFAULT 'classic', equipped_victory TEXT NOT NULL DEFAULT 'classic', equipped_badge TEXT NOT NULL DEFAULT '', owner_granted INTEGER NOT NULL DEFAULT 0, premium_until TEXT NOT NULL DEFAULT '')")
        await db.execute("INSERT OR IGNORE INTO bunker_economy (user_id) VALUES (?)", (user_id,))
        async with db.execute("SELECT coins FROM bunker_economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        await db.commit()
        return int(row[0]) if row else 0

async def add_coins(user_id: int, amount: int):
    if amount <= 0:
        return await get_coins(user_id)
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            await conn.execute("INSERT INTO bunker_economy (user_id, coins) VALUES ($1, $2) ON CONFLICT(user_id) DO UPDATE SET coins = bunker_economy.coins + $2", user_id, amount)
            row = await conn.fetchrow("SELECT coins FROM bunker_economy WHERE user_id = $1", user_id)
            return int(row["coins"])
    async with connect_db() as db:
        await get_coins(user_id)
        await db.execute("UPDATE bunker_economy SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        return await get_coins(user_id)

async def spend_coins(user_id: int, amount: int) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            result = await conn.execute("UPDATE bunker_economy SET coins = coins - $2 WHERE user_id = $1 AND coins >= $2", user_id, amount)
            return result.endswith("1")
    async with connect_db() as db:
        await get_coins(user_id)
        cur = await db.execute("UPDATE bunker_economy SET coins = coins - ? WHERE user_id = ? AND coins >= ?", (amount, user_id, amount))
        await db.commit()
        return cur.rowcount == 1

async def has_purchase(user_id: int, item_id: str) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM bunker_purchases WHERE user_id = $1 AND item_id = $2", user_id, item_id)
            return row is not None
    async with connect_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bunker_purchases (user_id INTEGER NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(user_id,item_id))")
        async with db.execute("SELECT 1 FROM bunker_purchases WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cur:
            return await cur.fetchone() is not None

async def purchase_item(user_id: int, item_id: str, price: int) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchrow("SELECT 1 FROM bunker_purchases WHERE user_id = $1 AND item_id = $2", user_id, item_id)
                if exists:
                    return True
                updated = await conn.execute("UPDATE bunker_economy SET coins = coins - $2 WHERE user_id = $1 AND coins >= $2", user_id, price)
                if not updated.endswith("1"):
                    return False
                await conn.execute("INSERT INTO bunker_purchases (user_id, item_id) VALUES ($1, $2)", user_id, item_id)
                return True
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT 1 FROM bunker_purchases WHERE user_id = ? AND item_id = ?", (user_id, item_id)) as cur:
            if await cur.fetchone():
                return True
        cur = await db.execute("UPDATE bunker_economy SET coins = coins - ? WHERE user_id = ? AND coins >= ?", (price, user_id, price))
        if cur.rowcount != 1:
            await db.rollback()
            return False
        await db.execute("INSERT INTO bunker_purchases (user_id, item_id) VALUES (?, ?)", (user_id, item_id))
        await db.commit()
        return True

async def equip_item(user_id: int, item_type: str, item_id: str):
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            await conn.execute(f"UPDATE bunker_economy SET equipped_{item_type} = $2 WHERE user_id = $1", user_id, item_id)
        return
    async with connect_db() as db:
        await get_coins(user_id)
        await db.execute(f"UPDATE bunker_economy SET equipped_{item_type} = ? WHERE user_id = ?", (item_id, user_id))
        await db.commit()

async def get_equipped(user_id: int):
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT equipped_title, equipped_frame, equipped_card_theme, equipped_victory, equipped_badge FROM bunker_economy WHERE user_id = $1", user_id)
            if not row:
                await conn.execute("INSERT INTO bunker_economy (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
                return {"title":"", "frame":"", "card_theme":"classic", "victory":"classic", "badge":""}
            return {"title":row["equipped_title"], "frame":row["equipped_frame"], "card_theme":row["equipped_card_theme"], "victory":row["equipped_victory"], "badge":row["equipped_badge"]}
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT equipped_title, equipped_frame, equipped_card_theme, equipped_victory, equipped_badge FROM bunker_economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return {"title":row[0], "frame":row[1], "card_theme":row[2], "victory":row[3], "badge":row[4]}

async def record_daily_event(user_id: int, event: str, amount: int = 1):
    rewards = {"game": 30, "win": 55, "reveal": 15, "vote": 10}
    limits = {"game": 1, "win": 1, "reveal": 3, "vote": 5}
    if event not in rewards:
        return 0
    today = _economy_date()
    col = {"game":"daily_games", "win":"daily_wins", "reveal":"daily_reveals", "vote":"daily_votes"}[event]
    reward = 0
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT daily_date, daily_games, daily_wins, daily_reveals, daily_votes FROM bunker_economy WHERE user_id = $1", user_id)
            if not row:
                await conn.execute("INSERT INTO bunker_economy (user_id, daily_date) VALUES ($1, $2)", user_id, today)
                row = {"daily_date": today, "daily_games":0, "daily_wins":0, "daily_reveals":0, "daily_votes":0}
            if row["daily_date"] != today:
                await conn.execute("UPDATE bunker_economy SET daily_date=$2, daily_games=0, daily_wins=0, daily_reveals=0, daily_votes=0 WHERE user_id=$1", user_id, today)
                current = 0
            else:
                current = int(row[col])
            new_value = min(limits[event], current + amount)
            delta = new_value - current
            if delta > 0:
                await conn.execute(f"UPDATE bunker_economy SET {col}=$2, coins=coins+$3 WHERE user_id=$1", user_id, new_value, rewards[event] * delta)
                reward = rewards[event] * delta
            return reward
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT daily_date, %s FROM bunker_economy WHERE user_id = ?" % col, (user_id,)) as cur:
            row = await cur.fetchone()
        current = int(row[1] or 0) if row and row[0] == today else 0
        if not row or row[0] != today:
            await db.execute("UPDATE bunker_economy SET daily_date=?, daily_games=0, daily_wins=0, daily_reveals=0, daily_votes=0 WHERE user_id=?", (today, user_id))
        new_value = min(limits[event], current + amount)
        delta = new_value - current
        if delta > 0:
            await db.execute(f"UPDATE bunker_economy SET {col}=?, coins=coins+? WHERE user_id=?", (new_value, rewards[event]*delta, user_id))
            reward = rewards[event]*delta
        await db.commit()
        return reward

async def get_daily_progress(user_id: int):
    today = _economy_date()
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT daily_date,daily_games,daily_wins,daily_reveals,daily_votes FROM bunker_economy WHERE user_id=$1", user_id)
            if not row or row["daily_date"] != today:
                return {"game":0,"win":0,"reveal":0,"vote":0}
            return {"game":row["daily_games"],"win":row["daily_wins"],"reveal":row["daily_reveals"],"vote":row["daily_votes"]}
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT daily_date,daily_games,daily_wins,daily_reveals,daily_votes FROM bunker_economy WHERE user_id=?", (user_id,)) as cur:
            row=await cur.fetchone()
        if not row or row[0] != today:
            return {"game":0,"win":0,"reveal":0,"vote":0}
        return {"game":row[1],"win":row[2],"reveal":row[3],"vote":row[4]}

async def grant_purchase(user_id: int, item_id: str) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            result = await conn.execute("INSERT INTO bunker_purchases (user_id, item_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", user_id, item_id)
            return result.endswith("1")
    async with connect_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bunker_purchases (user_id INTEGER NOT NULL, item_id TEXT NOT NULL, PRIMARY KEY(user_id,item_id))")
        cur=await db.execute("INSERT OR IGNORE INTO bunker_purchases (user_id,item_id) VALUES (?,?)",(user_id,item_id))
        await db.commit()
        return cur.rowcount == 1

async def extend_premium(user_id: int, days: int):
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row=await conn.fetchrow("SELECT premium_until FROM bunker_economy WHERE user_id=$1",user_id)
            current=None
            if row and row["premium_until"]:
                try: current=datetime.fromisoformat(row["premium_until"])
                except Exception: current=None
            base=max(now,current) if current else now
            until=base+timedelta(days=days)
            await conn.execute("INSERT INTO bunker_economy(user_id,premium_until) VALUES($1,$2) ON CONFLICT(user_id) DO UPDATE SET premium_until=$2",user_id,until.isoformat())
            return until
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT premium_until FROM bunker_economy WHERE user_id=?",(user_id,)) as cur: row=await cur.fetchone()
        current=None
        if row and row[0]:
            try: current=datetime.fromisoformat(row[0])
            except Exception: current=None
        base=max(now,current) if current else now
        until=base+timedelta(days=days)
        await db.execute("UPDATE bunker_economy SET premium_until=? WHERE user_id=?",(until.isoformat(),user_id))
        await db.commit()
        return until

async def get_premium_until(user_id: int):
    from datetime import datetime, timezone
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row=await conn.fetchrow("SELECT premium_until FROM bunker_economy WHERE user_id=$1",user_id)
            value=row["premium_until"] if row else ""
    else:
        async with connect_db() as db:
            await get_coins(user_id)
            async with db.execute("SELECT premium_until FROM bunker_economy WHERE user_id=?",(user_id,)) as cur: row=await cur.fetchone()
            value=row[0] if row else ""
    if not value: return None
    try:
        dt=datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

async def is_premium(user_id: int) -> bool:
    from datetime import datetime, timezone
    dt=await get_premium_until(user_id)
    return bool(dt and dt > datetime.now(timezone.utc))

async def record_star_payment(charge_id: str, user_id: int, product_id: str, stars: int) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            result = await conn.execute("INSERT INTO bunker_star_payments(charge_id,user_id,product_id,stars) VALUES($1,$2,$3,$4) ON CONFLICT(charge_id) DO NOTHING", charge_id,user_id,product_id,stars)
            return result.endswith("1")
    async with connect_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bunker_star_payments (charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, product_id TEXT NOT NULL, stars INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        cur=await db.execute("INSERT OR IGNORE INTO bunker_star_payments(charge_id,user_id,product_id,stars) VALUES(?,?,?,?)",(charge_id,user_id,product_id,stars))
        await db.commit()
        return cur.rowcount == 1

async def owner_grant_if_needed(user_id: int, amount: int) -> bool:
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT owner_granted FROM bunker_economy WHERE user_id=$1", user_id)
            if row and row["owner_granted"]:
                return False
            await conn.execute("INSERT INTO bunker_economy(user_id,coins,owner_granted) VALUES($1,$2,1) ON CONFLICT(user_id) DO UPDATE SET coins=bunker_economy.coins+$2, owner_granted=1", user_id, amount)
            return True
    async with connect_db() as db:
        await get_coins(user_id)
        async with db.execute("SELECT owner_granted FROM bunker_economy WHERE user_id=?", (user_id,)) as cur:
            row=await cur.fetchone()
        if row and row[0]: return False
        await db.execute("UPDATE bunker_economy SET coins=coins+?, owner_granted=1 WHERE user_id=?", (amount,user_id))
        await db.commit()
        return True

async def health_check():
    async with connect_db() as db:
        await db.execute("SELECT 1")
    if _stats_pool:
        async with _stats_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

# --- ЛОГИКА ЛОББИ ---

async def create_lobby(chat_id: int, host_id: int) -> bool:
    """Создаёт лобби только если в чате нет активной игры. Возвращает True/False."""
    async with connect_db() as db:
        async with db.execute("SELECT status FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
        if row and row[0] not in ("ended", "cancelled"):
            return False

        await db.execute("DELETE FROM lobbies WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM players WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM votes WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM reveals WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM hidden_captains WHERE chat_id = ?", (chat_id,))
        await db.execute(
            "INSERT INTO lobbies (chat_id, host_id, status, current_round, current_turn_user_id) VALUES (?, ?, ?, 1, 0)",
            (chat_id, host_id, "lobby")
        )
        await db.commit()
        return True

async def try_start_lobby(chat_id: int) -> bool:
    """Атомарно переводит лобби из lobby в starting. Защищает от двойного старта."""
    async with connect_db() as db:
        cursor = await db.execute(
            "UPDATE lobbies SET status = 'starting' WHERE chat_id = ? AND status = 'lobby'",
            (chat_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


async def get_lobby(chat_id: int):
    async with connect_db() as db:
        async with db.execute("SELECT status, host_id, scenario, current_round FROM lobbies WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return (row[0], row[1], row[2], 0, row[3])
            return None

async def transition_lobby_status(chat_id: int, new_status: str, expected_statuses, current_round: int = None) -> bool:
    placeholders = ",".join("?" for _ in expected_statuses)
    async with connect_db() as db:
        if current_round is None:
            cur = await db.execute(f"UPDATE lobbies SET status = ? WHERE chat_id = ? AND status IN ({placeholders})", (new_status, chat_id, *expected_statuses))
        else:
            cur = await db.execute(f"UPDATE lobbies SET status = ?, current_round = ? WHERE chat_id = ? AND status IN ({placeholders})", (new_status, current_round, chat_id, *expected_statuses))
        await db.commit()
        return cur.rowcount == 1

async def cancel_lobby(chat_id: int) -> bool:
    return await transition_lobby_status(chat_id, "cancelled", ["lobby", "starting", "reveal_phase", "discussion", "voting", "finishing"])

async def try_end_game(chat_id: int) -> bool:
    return await transition_lobby_status(chat_id, "ended", ["reveal_phase", "discussion", "voting", "finishing"])

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

async def find_active_player_chat(user_id: int):
    """Возвращает чат активной игры, в которой участвует пользователь."""
    async with connect_db() as db:
        async with db.execute(
            """SELECT p.chat_id
               FROM players p
               JOIN lobbies l ON l.chat_id = p.chat_id
              WHERE p.user_id = ?
                AND l.status NOT IN ('ended', 'cancelled')
              ORDER BY CASE WHEN l.status = 'lobby' THEN 0 ELSE 1 END, l.current_round DESC
              LIMIT 1""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


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
        query = """
            SELECT COALESCE(SUM(CASE WHEN hc.user_id = v.voter_id THEN 2 ELSE 1 END), 0)
            FROM votes v
            LEFT JOIN hidden_captains hc
              ON hc.chat_id = v.chat_id
             AND hc.round_num = (SELECT current_round FROM lobbies WHERE chat_id = v.chat_id)
            WHERE v.chat_id = ? AND v.target_id = 0
        """
        async with db.execute(query, (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return int(row[0] or 0)

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