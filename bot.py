import asyncio
import html
import json
import os
import random
import time
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, LabeledPrice, PreCheckoutQuery
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import google.generativeai as genai

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ЗАЩИТА ОТ СПАМА / АВТОКЛИКЕРОВ ---
class CallbackRateLimitMiddleware(BaseMiddleware):
    def __init__(self, interval: float = 0.65):
        self.interval = interval
        self.last_seen = {}

    async def __call__(self, handler, event, data):
        user_id = getattr(event.from_user, "id", None)
        if user_id is not None:
            now = time.monotonic()
            key = (user_id, getattr(event.message, "chat", None).id if getattr(event, "message", None) else 0)
            previous = self.last_seen.get(key, 0.0)
            if now - previous < self.interval:
                try:
                    await event.answer("⏳ Слишком быстро. Подожди немного.")
                except Exception:
                    pass
                return
            self.last_seen[key] = now
            # Не даём словарю расти бесконечно.
            if len(self.last_seen) > 10000:
                cutoff = now - 60
                self.last_seen = {k: v for k, v in self.last_seen.items() if v >= cutoff}
        return await handler(event, data)

dp.callback_query.outer_middleware(CallbackRateLimitMiddleware())

# Один запрос Gemini одновременно. При 429/503/временных сбоях — повтор с backoff.
GEMINI_SEMAPHORE = asyncio.Semaphore(1)
GEMINI_CACHE = {}
FINALIZING_GAMES = set()
# Один управляемый игровой task на чат. Не даём старым раундам накапливаться.
GAME_TASKS = {}


def schedule_game_task(chat_id: int, coro):
    old = GAME_TASKS.get(chat_id)
    current = asyncio.current_task()
    # Если текущий игровой task сам планирует следующий раунд, его нельзя отменять.
    if old and old is not current and not old.done():
        old.cancel()
    task = asyncio.create_task(coro)
    GAME_TASKS[chat_id] = task

    def _cleanup(done_task):
        if GAME_TASKS.get(chat_id) is done_task:
            GAME_TASKS.pop(chat_id, None)
    task.add_done_callback(_cleanup)
    return task


def cancel_game_task(chat_id: int):
    task = GAME_TASKS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


CARD_IMAGES = {
    "position": "https://i.ibb.co/rRQQn3Jk/E17684-E2-56-F4-4-D66-8727-A0411741-F102.png", 
    "age": "https://i.ibb.co/SXVZG1m8/E13-D392-F-B727-4782-BE64-4-B3-FF6460-AE0.png",
    "price": "https://i.ibb.co/gbtmrfbt/2-E013-C9-A-F562-435-D-8445-057199144-D69.png",
    "health": "https://i.ibb.co/RktLxj4k/BE52-B7-D1-DE73-4-E85-9641-0242-E4-FA816-F.png",
    "skill": "https://i.ibb.co/jPN42k2b/0-F0274-A2-6320-4-EE8-9-F2-F-9-CB9-E2-C35209.png",
    "inventory": "https://i.ibb.co/qL8JH60p/5-A3-AD710-D4-C9-443-B-AD86-14029-ED7-D237.png",
    "secret": "https://i.ibb.co/LzJ1zstj/7-DC7-EFD5-B294-4-EC8-BE3-D-EDF77-BFD93-DF.png"
}

SPECIAL_CARD_NAMES = {
    "swap_position": "🔄 Фиксированный обмен [Позиция]",
    "swap_age": "🔄 Фиксированный обмен [Возраст]",
    "swap_price": "🔄 Фиксированный обмен [Цена]",
    "swap_health": "🔄 Фиксированный обмен [Здоровье]",
    "swap_skill": "🔄 Фиксированный обмен [Навык]",
    "swap_inventory": "🔄 Фиксированный обмен [Багаж]",
    "swap_secret": "🔄 Фиксированный обмен [Секрет]",
    "swap_budget": "🔄 Фиксированный обмен [Бюджет]",
    "swap_squad": "🔄 Фиксированный обмен [Состав]",
    "swap_finance": "🔄 Фиксированный обмен [Финансы]",
    "swap_infrastructure": "🔄 Фиксированный обмен [Инфраструктура]",
    "swap_reputation": "🔄 Фиксированный обмен [Репутация]",
    "swap_problem": "🔄 Фиксированный обмен [Проблема]",
    "spy": "👁 Шпионаж",
    "yellow_card": "🟨 Желтая карточка",
    "flash": "📸 Вспышка (Публичность)",
    "mirror": "🪞 Зеркальный щит",
    "chaos": "🎲 Трансферный хаос",
    "vote_redirect": "🃏 Подмена голосов",
    "mute": "🤫 Тихий трибунал",
    "captain": "©️ Скрытый капитан"
}

SPECIAL_CARD_DESCRIPTIONS = {
    "swap_position": "Обменивает твою карту «Позиция» на позицию любого игрока.",
    "swap_age": "Обменивает твою карту «Возраст» на возраст любого игрока.",
    "swap_price": "Обменивает твою карту «Цена» на цену любого игрока.",
    "swap_health": "Обменивает твою карту «Здоровье» на здоровье любого игрока.",
    "swap_skill": "Обменивает твою карту «Навык» на навык любого игрока.",
    "swap_inventory": "Обменивает твой «Багаж» на багаж любого игрока.",
    "swap_secret": "Обменивает твой «Секрет» на секрет любого игрока.",
    "swap_budget": "Обменивает твой «Бюджет» на бюджет любого клуба.",
    "swap_squad": "Обменивает твою карту «Состав» на состав любого клуба.",
    "swap_finance": "Обменивает твою карту «Финансы» на финансы любого клуба.",
    "swap_infrastructure": "Обменивает твою «Инфраструктуру» на инфраструктуру любого клуба.",
    "swap_reputation": "Обменивает твою «Репутацию» на репутацию любого клуба.",
    "swap_problem": "Обменивает твою «Проблему» на проблему любого клуба.",
    "spy": "Позволяет скрыто подсмотреть 1 закрытую карту любого соперника.",
    "yellow_card": "Блокирует использование спец-карты выбранному игроку до конца игры.",
    "flash": "Принудительно вскрывает любую выбранную тобой закрытую карту соперника в общий чат.",
    "mirror": "Защита: отражает действие следующей примененной против тебя спец-карты обратно.",
    "chaos": "Случайно меняет одну твою закрытую карту с закрытой картой соперника.",
    "vote_redirect": "Используется ДО голосования. Если в конце голосования ты набрал больше всех голосов единолично, все голоса против тебя тайно переводятся на выбранного тобой игрока. Если ты не лидер голосования — карта сгорает.",
    "mute": "Используется во время обсуждения перед голосованием. Тайно лишает выбранного игрока возможности писать сообщения в группе до конца текущего раунда. Его голоса при этом сохраняются.",
    "captain": "Тайно назначает одного другого игрока капитаном. В текущем голосовании его голос считается за два. Сам капитан не получает уведомления о назначении."
}

TRAIT_LABELS = {
    "position": "Позиция", "age": "Возраст", "price": "Трансферная цена",
    "health": "Здоровье", "skill": "Навык", "inventory": "Багаж", "secret": "Секрет",
    "club": "Клуб", "budget": "Бюджет", "squad": "Состав", "finance": "Финансы",
    "infrastructure": "Инфраструктура", "reputation": "Репутация", "problem": "Проблема"
}

PLAYER_TRAITS = [("position", "💼 Позицию"), ("age", "👤 Возраст"), ("price", "💰 Цену"), ("health", "❤️ Здоровье"), ("skill", "🎯 Навык"), ("inventory", "🎒 Багаж"), ("secret", "🔍 Секрет")]
CLUB_TRAITS = [("budget", "💰 Бюджет"), ("squad", "👥 Состав"), ("finance", "📊 Финансы"), ("infrastructure", "🏟 Инфраструктуру"), ("reputation", "🏆 Репутацию"), ("problem", "⚠️ Проблему"), ("secret", "🔍 Секрет")]

ECONOMY_OWNER_ID = int(os.getenv("ECONOMY_OWNER_ID", "1624967415"))
OWNER_STARTING_COINS = 10000
SHOP_ITEMS = {
    # Титулы
    "title_tactician": {"type": "title", "name": "🧠 Тактик", "price": 450, "desc": "Отображается рядом с именем в профиле."},
    "title_legend": {"type": "title", "name": "👑 Легенда Бункера", "price": 1200, "desc": "Редкий титул для профиля."},
    "title_strategist": {"type": "title", "name": "🎯 Стратег", "price": 650, "desc": "Отображается рядом с именем в профиле."},
    "title_diplomat": {"type": "title", "name": "🕴️ Дипломат", "price": 700, "desc": "Отображается рядом с именем в профиле."},
    "title_authority": {"type": "title", "name": "👑 Авторитет", "price": 900, "desc": "Отображается рядом с именем в профиле."},
    "title_cold": {"type": "title", "name": "🧊 Хладнокровный", "price": 950, "desc": "Отображается рядом с именем в профиле."},
    "title_fox": {"type": "title", "name": "🦊 Лис", "price": 800, "desc": "Отображается рядом с именем в профиле."},
    "title_comb": {"type": "title", "name": "🧩 Комбинатор", "price": 850, "desc": "Отображается рядом с именем в профиле."},
    "title_winner": {"type": "title", "name": "🏆 Победитель", "price": 1000, "desc": "Отображается рядом с именем в профиле."},
    "title_veteran": {"type": "title", "name": "💎 Ветеран", "price": 1100, "desc": "Отображается рядом с именем в профиле."},
    "title_phenomenon": {"type": "title", "name": "⚡ Феномен", "price": 1400, "desc": "Отображается рядом с именем в профиле."},
    "title_maestro": {"type": "title", "name": "🐐 Маэстро", "price": 1600, "desc": "Отображается рядом с именем в профиле."},
    "title_manipulator": {"type": "title", "name": "🧠 Манипулятор", "price": 1250, "desc": "Отображается рядом с именем в профиле."},

    # Фразы победы
    "victory_fire": {"type": "victory", "name": "🎆 Фейерверк победы", "price": 1300, "desc": "После победы бот оформляет сообщение особым эффектом."},
    "victory_gold": {"type": "victory", "name": "🏆 Золотой финал", "price": 2500, "desc": "Победа с более заметной подачей."},
    "victory_goodbye": {"type": "victory", "name": "🚪 До свидания, дилетанты", "price": 1800, "desc": "Фраза после победы."},
    "victory_whose_left": {"type": "victory", "name": "😈 Ну и кто тут лишний?", "price": 2000, "desc": "Фраза после победы."},
    "victory_i_told": {"type": "victory", "name": "🗿 Я же говорил", "price": 1600, "desc": "Фраза после победы."},
    "victory_cold": {"type": "victory", "name": "🧊 Без лишних слов", "price": 1800, "desc": "Сдержанная подача после победы."},
    "victory_last": {"type": "victory", "name": "🚪 Последний в бункере", "price": 2200, "desc": "Фраза после победы."},
}
# Служебный титул создателя: не отображается в магазине, но считается покупкой владельца.
SHOP_ITEMS["title_creator"] = {"type": "title", "name": "👑 Создатель игры", "price": 0, "desc": "Уникальный титул владельца Football Bunker."}

# Платные товары только за Telegram Stars (XTR). Никакого P2W.
STAR_PRODUCTS = {
    "coins_1500": {"kind": "coins", "name": "Получить 1 500 🪙", "stars": 25, "coins": 1500, "desc": "1 500 🪙 на баланс. Тратятся на косметику в обычном магазине."},
    "coins_5000": {"kind": "coins", "name": "Получить 5 000 🪙", "stars": 65, "coins": 5000, "desc": "5 000 🪙 на баланс. Тратятся на косметику в обычном магазине."},
    "coins_12000": {"kind": "coins", "name": "Получить 12 000 🪙", "stars": 140, "coins": 12000, "desc": "12 000 🪙 на баланс. Тратятся на косметику в обычном магазине."},
    "pack_cosmetic": {"kind": "pack", "name": "Пакет «После финала»", "stars": 150, "items": ["title_phenomenon", "victory_goodbye"], "desc": "⚡ Феномен + 🚪 До свидания, дилетанты. Без игровых преимуществ."},
    "premium_30": {"kind": "premium", "name": "Premium · 30 дней", "stars": 100, "days": 30, "desc": "Статус Premium на 30 дней. Без игровых преимуществ."},
}


CREATOR_ID = 1624967415


CARD_THEME_STYLE = {"classic": ("⚽", "───────────────────")}

async def economy_owner_grant():
    if await db.owner_grant_if_needed(ECONOMY_OWNER_ID, OWNER_STARTING_COINS):
        print(f"[ECONOMY] Owner {ECONOMY_OWNER_ID} received {OWNER_STARTING_COINS} coins")
    # Титул создателя не покупается и всегда возвращается владельцу игры.
    await db.grant_purchase(ECONOMY_OWNER_ID, "title_creator")
    await db.equip_item(ECONOMY_OWNER_ID, "title", "title_creator")

async def economy_profile_text(user_id: int, username: str) -> str:
    games, wins = await db.get_user_profile(user_id, username)
    coins = await db.get_coins(user_id)
    eq = await db.get_equipped(user_id)
    title = SHOP_ITEMS.get(eq["title"], {}).get("name", "") if eq.get("title") else ""
    victory = SHOP_ITEMS.get(eq.get("victory"), {}).get("name", "") if eq.get("victory") and eq.get("victory") != "classic" else "Классика"
    icon, line, badge = "⚽", "────────────────────", ""
    rate = wins / games * 100 if games else 0
    premium_until = await db.get_premium_until(user_id)
    premium_line = f"\n⭐ <b>Premium:</b> до {premium_until.astimezone().strftime('%d.%m.%Y')}" if premium_until and premium_until.timestamp() > time.time() else ""
    return (
        f"{icon} <b>ПРОФИЛЬ ИГРОКА</b> {html.escape(badge)}\n{line}\n"
        f"👤 <b>{html.escape(username)}</b> {html.escape(title)}\n"
        f"{line}\n"
        f"🎮 Игр: <b>{games}</b>\n🏆 Побед: <b>{wins}</b>\n📈 Винрейт: <b>{rate:.1f}%</b>\n🪙 Монеты: <b>{coins}</b>{premium_line}\n\n"
        f"🎨 <b>Оформление</b>\n"
        f"🏆 Эффект победы: <b>{html.escape(victory or 'По умолчанию')}</b>"
    )

async def economy_shop_text(user_id: int, category: str = "home") -> str:
    coins = await db.get_coins(user_id)
    if category == "titles":
        items = [(i,x) for i,x in SHOP_ITEMS.items() if x["type"] == "title"]
        title = "🏷 <b>ТИТУЛЫ</b>"
        note = "Титул отображается рядом с именем в профиле."
    elif category == "phrases":
        items = [(i,x) for i,x in SHOP_ITEMS.items() if x["type"] == "victory"]
        title = "💬 <b>ФРАЗЫ ПОБЕДЫ</b>"
        note = "Выбранная фраза появляется после твоей победы."
    else:
        return (
            "🛍 <b>МАГАЗИН БУНКЕРА</b>\n"
            "───────────────────\n"
            f"🪙 Баланс: <b>{coins}</b>\n\n"
            "Здесь только косметика — она не влияет на исход игры.\n\n"
            "🏷 <b>Титулы</b>\n<i>То, что будет стоять рядом с твоим именем.</i>\n\n"
            "💬 <b>Фразы победы</b>\n<i>То, что бот скажет после твоей победы.</i>"
        )
    lines = [title, "───────────────────", f"🪙 Баланс: <b>{coins}</b>", note, ""]
    for item_id, item in items:
        owned = await db.has_purchase(user_id, item_id)
        eq = await db.get_equipped(user_id)
        equipped = eq.get(item["type"]) == item_id
        status = "⚙️ Экипировано" if equipped else ("✅ Куплено" if owned else f"🪙 {item['price']}")
        lines.append(f"<b>{item['name']}</b> — {status}\n<i>{html.escape(item['desc'])}</i>\n")
    return "\n".join(lines)

async def shop_keyboard(user_id: int, category: str = "home"):
    builder = InlineKeyboardBuilder()
    if category == "home":
        builder.button(text="🏷 Титулы", callback_data="shopcat:titles")
        builder.button(text="💬 Фразы победы", callback_data="shopcat:phrases")
        builder.button(text="⭐ Купить за Stars", callback_data="menu:stars")
        builder.button(text="🎒 Мои покупки", callback_data="menu:inventory")
    else:
        eq = await db.get_equipped(user_id)
        for item_id, item in SHOP_ITEMS.items():
            if item["type"] != ("title" if category == "titles" else "victory"):
                continue
            owned = await db.has_purchase(user_id, item_id)
            equipped = eq.get(item["type"]) == item_id
            if equipped:
                text = f"✅ {item['name']}"
            elif owned:
                text = f"⚙️ Экипировать · {item['name']}"
            else:
                text = f"🛒 {item['name']} · {item['price']} 🪙"
            builder.button(text=text, callback_data=f"shop:{'equip' if owned else 'buy'}:{item_id}")
        builder.button(text="◀️ Назад в магазин", callback_data="menu:shop")
    builder.button(text="🪙 Кошелёк", callback_data="menu:wallet")
    builder.button(text="👤 Профиль", callback_data="menu:profile")
    builder.adjust(1)
    return builder.as_markup()

async def wallet_text(user_id: int) -> str:
    coins = await db.get_coins(user_id); p = await db.get_daily_progress(user_id)
    return (f"🪙 <b>КОШЕЛЁК</b>\n───────────────────\nБаланс: <b>{coins} 🪙</b>\n\n"
            f"📅 <b>Ежедневные задания</b>\n🎮 Сыграть 1 игру: <b>{min(p['game'],1)}/1</b> · +30 🪙\n"
            f"🏆 Победить 1 раз: <b>{min(p['win'],1)}/1</b> · +55 🪙\n"
            f"🃏 Вскрыть 3 карты: <b>{min(p['reveal'],3)}/3</b> · +15 🪙\n"
            f"🗳 Отдать 5 голосов: <b>{min(p['vote'],5)}/5</b> · +10 🪙\n\nНаграды начисляются автоматически. P2W-механик нет.")

async def victory_effect(user_id: int, name: str) -> str:
    effect = (await db.get_equipped(user_id))["victory"]
    n = html.escape(name)
    phrases = {
        "victory_fire": f"🎆✨ {n} — ПОБЕДИТЕЛЬ! ✨🎆",
        "victory_gold": f"🏆💛 {n} — ЗОЛОТОЙ ФИНАЛ! 💛🏆",
        "victory_goodbye": f"🚪 {n}: «До свидания, дилетанты.»",
        "victory_whose_left": f"😈 {n}: «Ну и кто тут лишний?»",
        "victory_i_told": f"🗿 {n}: «Я же говорил.»",
        "victory_cold": f"🧊 {n} — без лишних слов.",
        "victory_last": f"🚪 {n} — последний в бункере.",
    }
    return phrases.get(effect, f"🏆 {n} — победитель!")

async def is_game_active(chat_id: int) -> bool:
    lobby = await db.get_lobby(chat_id)
    return lobby is not None and lobby[0] not in ("ended", "cancelled")

# --- ОЦЕНКА ИТОГОВ С GEMINI AI (ЧЕРЕЗ SDK С ТАЙМАУТОМ) ---
async def evaluate_game_outcome(scenario_text: str, winners_data: list) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ <i>GEMINI_API_KEY не задан. Оценка сценария недоступна.</i>"
    game_type = "club" if winners_data and "budget" in winners_data[0][1] and "club" in winners_data[0][1] else "player"
    if game_type == "club":
        summary = "".join(
            f"- Кандидат {name}: Клуб={pack.get('club')}, Бюджет={pack.get('budget')}, Состав={pack.get('squad')}, "
            f"Финансы={pack.get('finance')}, Инфраструктура={pack.get('infrastructure')}, Репутация={pack.get('reputation')}, "
            f"Проблема={pack.get('problem')}, Секрет={pack.get('secret')}\n"
            for name, pack in winners_data
        )
        rules = ("Это режим КЛУБОВ. Здесь нет навыка, здоровья, возраста или цены игрока. "
                 "Главное числовое условие — бюджет клуба. Остальные условия сценария оценивай по карточкам клуба. "
                 "Не придумывай отсутствующие характеристики. Бюджет проверяй как обязательное минимальное требование. "
                 "Если клуб проходит бюджет и в целом соответствует проекту, выбирай УСПЕХ. В спорных случаях — УСПЕХ.")
    else:
        summary = "".join(
            f"- Игрок {name}: Позиция={pack.get('position')}, Возраст={pack.get('age')}, Цена={pack.get('price')}, "
            f"Здоровье={pack.get('health')}, Навык={pack.get('skill')}, Багаж={pack.get('inventory')}, Секрет={pack.get('secret')}\n"
            for name, pack in winners_data
        )
        rules = ("Это режим ИГРОКОВ. Контракт всегда заключается ИМЕННО С ДВУМЯ финалистами. "
                 "Если указан общий бюджет на двоих, сравни сумму цен пары с бюджетом, а не цену каждого отдельно. "
                 "Проверяй только явно написанные ключевые требования. Описательные условия оценивай гибко. "
                 "Неизвестная характеристика нейтральна. Сильная сторона одного может компенсировать второстепенный недостаток другого. "
                 "ПРОВАЛ ставь только при явном нарушении ключевого требования или бюджета. В спорных случаях — УСПЕХ.")
    prompt=("Ты — спортивный директор футбольной игры. Твоя задача — вынести честный, но не душный вердикт.\n\n"
            f"СЦЕНАРИЙ:\n{scenario_text}\n\nКАНДИДАТЫ:\n{summary}\n{rules}\n\n"
            "Формат строго: 📌 <b>ВЕРДИКТ ИИ:</b> [УСПЕХ или ПРОВАЛ]\n📝 <b>Причина:</b> [2-4 коротких предложения]")
    cache_key=str(hash(prompt))
    if cache_key in GEMINI_CACHE: return GEMINI_CACHE[cache_key]
    async with GEMINI_SEMAPHORE:
        last_error=None
        for attempt in range(3):
            try:
                model=genai.GenerativeModel(GEMINI_MODEL)
                response=await model.generate_content_async(prompt, request_options={"timeout": 25})
                result=(response.text or "").strip()
                if result:
                    GEMINI_CACHE[cache_key]=result
                    return result
            except Exception as e:
                last_error=e
                if attempt<2: await asyncio.sleep(2**attempt)
    print(f"[GEMINI] Ошибка после 3 попыток: {last_error}")
    return "⚠️ <i>ИИ-судья временно недоступен. Игра завершена без вердикта.</i>"

async def build_reveal_keyboard(chat_id: int, user_id: int):
    builder = InlineKeyboardBuilder()
    game_type = await get_game_type(chat_id)
    traits = CLUB_TRAITS if game_type == "club" else PLAYER_TRAITS
    for trait_key, trait_label in traits:
        if not await db.is_trait_revealed(chat_id, user_id, trait_key):
            builder.button(text=trait_label, callback_data=f"reveal:{trait_key}:{chat_id}")
    spec_info = await db.get_player_special_info(chat_id, user_id)
    if spec_info and not spec_info[1]:
        builder.button(text="✨ Спецкарта", callback_data=f"use_spec:{chat_id}")
    builder.adjust(2)
    return builder.as_markup()

async def build_private_card_text(chat_id: int, user_id: int) -> str:
    pack = await db.get_player_pack(chat_id, user_id) or {}
    spec_info = await db.get_player_special_info(chat_id, user_id)
    spec_code = spec_info[0] if spec_info else ""
    spec_used = bool(spec_info[1]) if spec_info else False
    spec_title = SPECIAL_CARD_NAMES.get(spec_code, "Спецкарта")
    spec_desc = SPECIAL_CARD_DESCRIPTIONS.get(spec_code, "Описание отсутствует.")
    if spec_used:
        spec_title = f"{spec_title} (уже использована)"
    game_type = await get_game_type(chat_id)
    if game_type == "club":
        title = html.escape(str(pack.get("club", "Клуб")))
        lines = [
            f"🏟️ <b>КАРТОЧКА КЛУБА — {title}</b>",
            "───────────────────",
            f"💰 <b>Бюджет:</b> <code>{html.escape(str(pack.get('budget','-')))}</code>",
            f"👥 <b>Состав:</b> <code>{html.escape(str(pack.get('squad','-')))}</code>",
            f"📊 <b>Финансы:</b> <code>{html.escape(str(pack.get('finance','-')))}</code>",
            f"🏟️ <b>Инфраструктура:</b> <code>{html.escape(str(pack.get('infrastructure','-')))}</code>",
            f"🏆 <b>Репутация:</b> <code>{html.escape(str(pack.get('reputation','-')))}</code>",
            f"⚠️ <b>Проблема:</b> <code>{html.escape(str(pack.get('problem','-')))}</code>",
            f"🔍 <b>Секрет:</b> <i>{html.escape(str(pack.get('secret','-')))}</i>",
        ]
    else:
        title_name = await db.get_username(chat_id, user_id) or "Игрок"
        lines = [
            f"⚽ <b>КАРТОЧКА ИГРОКА — {html.escape(title_name)}</b>",
            "───────────────────",
            f"💼 <b>Позиция:</b> <code>{html.escape(str(pack.get('position','-')))}</code>",
            f"👤 <b>Возраст:</b> <code>{pack.get('age','-')} лет</code>",
            f"💰 <b>Цена:</b> <code>{html.escape(str(pack.get('price','-')))}</code>",
            f"❤️ <b>Здоровье:</b> <code>{html.escape(str(pack.get('health','-')))}</code>",
            f"🎯 <b>Навык:</b> <code>{html.escape(str(pack.get('skill','-')))}</code>",
            f"🎒 <b>Багаж:</b> <code>{html.escape(str(pack.get('inventory','-')))}</code>",
            f"🔍 <b>Секрет:</b> <i>{html.escape(str(pack.get('secret','-')))}</i>",
        ]
    lines += ["───────────────────", f"✨ <b>Спец-карта:</b> <b>{html.escape(spec_title)}</b>", f"ℹ️ <b>Что делает:</b> <i>{html.escape(spec_desc)}</i>", "───────────────────", "👇 <i>Используй кнопки ниже во время своего хода.</i>"]
    return "\n".join(lines)

async def refresh_private_card(chat_id: int, user_id: int):
    message_id = await db.get_private_card_message_id(chat_id, user_id)
    if not message_id:
        return
    try:
        text = await build_private_card_text(chat_id, user_id)
        markup = await build_reveal_keyboard(chat_id, user_id)
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception as e:
        # Старое сообщение может быть удалено/недоступно. Это не должно ломать игру.
        print(f"[CARD REFRESH] Не удалось обновить ЛС игрока {user_id}: {e}")

async def build_players_summary(chat_id: int) -> str:
    alive_players = await db.get_alive_players(chat_id)
    if not alive_players:
        return ""

    trait_structure = [
        ("position", "💼", "Позиция"), ("age", "👤", "Возраст"),
        ("price", "💰", "Цена"), ("health", "❤️", "Здоровье"),
        ("skill", "🎯", "Навык"), ("inventory", "🎒", "Багаж"), ("secret", "🔍", "Секрет")
    ]

    players_blocks = []
    for idx, (p_name, p_id) in enumerate(alive_players, 1):
        pack = await db.get_player_pack(chat_id, p_id) or {}
        player_lines = [f"👤 <b>{idx}. {html.escape(p_name)}</b>"]

        total_traits = len(trait_structure)
        for t_idx, (trait_key, emoji, title) in enumerate(trait_structure):
            is_last = (t_idx == total_traits - 1)
            prefix = "└" if is_last else "├"
            
            is_revealed = await db.is_trait_revealed(chat_id, p_id, trait_key)
            if is_revealed:
                val = pack.get(trait_key, "-")
                if trait_key == "age" and val != "-":
                    val = f"{val} лет"
                player_lines.append(f"{prefix} {emoji} {title}: <b>{html.escape(str(val))}</b>")
            else:
                player_lines.append(f"{prefix} {emoji} {title}: <i>🔒 Скрыто</i>")

        players_blocks.append("\n\n".join(player_lines))

    return "\n\n".join(players_blocks)

async def auto_reveal_single_player(chat_id: int, user_id: int, user_name: str, current_round: int):
    unrevealed = await db.get_unrevealed_traits(chat_id, user_id)
    if unrevealed:
        chosen_trait = random.choice(unrevealed)
        pack = await db.get_player_pack(chat_id, user_id)
        
        val = pack.get(chosen_trait, "-")
        if chosen_trait == "age" and val != "-":
            val = f"{val} лет"
        
        await db.record_reveal(chat_id, user_id, chosen_trait, current_round)
        await db.record_daily_event(user_id, "reveal")
        
        safe_name = html.escape(user_name)
        safe_val = html.escape(str(val))
        trait_title = TRAIT_LABELS.get(chosen_trait, chosen_trait)
        image_url = CARD_IMAGES.get(chosen_trait, "")
        
        msg_text = (
            f'<a href="{image_url}">&#8203;</a>⏱ <b>{safe_name}</b> не успел открыть карту!\n'
            f'└ 🎲 Бот автоматически вскрыл <b>[{trait_title}]</b>: <b>{safe_val}</b>'
        )
        preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)
        await bot.send_message(chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)

async def announce_winners_and_end(chat_id: int, alive_players: list):
    if not await db.try_end_game(chat_id):
        return
    game_stats = await db.get_game_stats(chat_id)

    all_players = await db.get_players(chat_id)
    alive_ids = {p[1] for p in alive_players}

    for p_name, p_id, _ in all_players:
        is_win = (p_id in alive_ids) and (len(alive_players) > 0)
        await db.update_user_stats(p_id, p_name, is_win)
        await db.record_daily_event(p_id, "game")
        if is_win:
            await db.record_daily_event(p_id, "win")

    # После завершения показываем всем полные карточки всех участников.
    all_cards_blocks = []
    game_type = await get_game_type(chat_id)
    for idx, (p_name, p_id, _) in enumerate(all_players, 1):
        pack = await db.get_player_pack(chat_id, p_id) or {}
        spec_info = await db.get_player_special_info(chat_id, p_id)
        spec_code = spec_info[0] if spec_info else ""
        spec_used = bool(spec_info[1]) if spec_info else False
        spec_title = SPECIAL_CARD_NAMES.get(spec_code, "—")
        if spec_used and spec_title != "—": spec_title += " (использована)"
        alive_mark = "🏆" if p_id in alive_ids else "❌"
        if game_type == "club":
            all_cards_blocks.append(
                f"{alive_mark} <b>{idx}. {html.escape(p_name)}</b> — 🏟️ <b>{html.escape(str(pack.get('club','-')))}</b>\n"
                f"├ 💰 Бюджет: <b>{html.escape(str(pack.get('budget','-')))}</b>\n"
                f"├ 👥 Состав: <b>{html.escape(str(pack.get('squad','-')))}</b>\n"
                f"├ 📊 Финансы: <b>{html.escape(str(pack.get('finance','-')))}</b>\n"
                f"├ 🏟️ Инфраструктура: <b>{html.escape(str(pack.get('infrastructure','-')))}</b>\n"
                f"├ 🏆 Репутация: <b>{html.escape(str(pack.get('reputation','-')))}</b>\n"
                f"├ ⚠️ Проблема: <b>{html.escape(str(pack.get('problem','-')))}</b>\n"
                f"├ 🔍 Секрет: <b>{html.escape(str(pack.get('secret','-')))}</b>\n"
                f"└ ✨ Спецкарта: <b>{html.escape(spec_title)}</b>"
            )
        else:
            age = pack.get("age", "-")
            age_text = f"{age} лет" if age != "-" else "-"
            all_cards_blocks.append(
                f"{alive_mark} <b>{idx}. {html.escape(p_name)}</b>\n"
                f"├ 💼 Позиция: <b>{html.escape(str(pack.get('position','-')))}</b>\n"
                f"├ 👤 Возраст: <b>{html.escape(str(age_text))}</b>\n"
                f"├ 💰 Цена: <b>{html.escape(str(pack.get('price','-')))}</b>\n"
                f"├ ❤️ Здоровье: <b>{html.escape(str(pack.get('health','-')))}</b>\n"
                f"├ 🎯 Навык: <b>{html.escape(str(pack.get('skill','-')))}</b>\n"
                f"├ 🎒 Багаж: <b>{html.escape(str(pack.get('inventory','-')))}</b>\n"
                f"├ 🔍 Секрет: <b>{html.escape(str(pack.get('secret','-')))}</b>\n"
                f"└ ✨ Спецкарта: <b>{html.escape(spec_title)}</b>"
            )

    if not alive_players:
        await bot.send_message(chat_id, "❌ <b>ИГРА ОКОНЧЕНА</b>\n───────────────────\nВсе претенденты выбыли! Победителей нет.", parse_mode="HTML")
    else:
        winners_str = "\n".join([f"├ 🏆 <b>{html.escape(p[0])}</b>" for p in alive_players[:-1]] + [f"└ 🏆 <b>{html.escape(alive_players[-1][0])}</b>"])
        await bot.send_message(
            chat_id,
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n───────────────────\nОставшиеся игроки претендуют на контракт:\n{winners_str}\n\n🤖 <i>ИИ-эксперт изучает все карточки и формирует вердикт...</i>",
            parse_mode="HTML"
        )

    await bot.send_chat_action(chat_id, action="typing")

    lobby = await db.get_lobby(chat_id)
    scenario_text = lobby[2] if (lobby and len(lobby) > 2 and lobby[2]) else "Цель сценария не указана."

    winners_data = []
    for p_name, p_id in alive_players:
        pack = await db.get_player_pack(chat_id, p_id) or {}
        winners_data.append((p_name, pack))

    ai_verdict = await evaluate_game_outcome(scenario_text, winners_data)

    await bot.send_message(
        chat_id,
        f"📊 <b>ИТОГИ СЦЕНАРИЯ</b>\n───────────────────\n{ai_verdict}\n\n"
        f"📈 <b>СТАТИСТИКА ИГРЫ</b>\n"
        f"🎮 Раундов: <b>{game_stats['rounds']}</b>\n"
        f"🗳 Голосов: <b>{game_stats['votes']}</b>\n"
        f"🤝 Ничьих: <b>{game_stats['ties']}</b>\n"
        f"⏭️ Скипов: <b>{game_stats['skips']}/3</b>\n"
        f"🃏 Спецкарт использовано: <b>{game_stats['special_cards']}</b>",
        parse_mode="HTML"
    )

    if alive_players:
        effects = "\n".join([await victory_effect(p_id, p_name) for p_name,p_id in alive_players])
        await bot.send_message(chat_id, f"✨ <b>ПОБЕДНЫЕ ЭФФЕКТЫ</b>\n{effects}", parse_mode="HTML")

    await bot.send_message(
        chat_id,
        "🔓 <b>КАРТЫ РАСКРЫТЫ</b>\n───────────────────\n"
        "Теперь можно узнать, что на самом деле скрывал каждый игрок:\n\n"
        + "\n\n".join(all_cards_blocks),
        parse_mode="HTML"
    )

async def start_round_flow(chat_id: int, current_round: int):
    try:
        if not await is_game_active(chat_id): return

        alive_players = await db.get_alive_players(chat_id)
        if len(alive_players) <= 2:
            await announce_winners_and_end(chat_id, alive_players)
            return

        all_players = await db.get_players(chat_id)
        total_count = len(all_players)
        bot_info = await bot.get_me()

        if not await db.transition_lobby_status(chat_id, "reveal_phase", ["starting", "discussion", "voting", "finishing"], current_round):
            return
        await bot.send_message(
            chat_id,
            f"📢 <b>РАУНД {current_round} | ВСКРЫТИЕ КАРТ</b>\n───────────────────\nИгроки вытягивают по 1 карте строго по очереди.",
            parse_mode="HTML"
        )

        for p_name, p_id in alive_players:
            if not await is_game_active(chat_id): return
            await db.set_current_turn(chat_id, p_id)
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📩 Открыть карту / Спец-карту", url=f"https://t.me/{bot_info.username}")
            
            safe_p_name = html.escape(p_name)
            await bot.send_message(
                chat_id,
                f"🎲 <b>ОЧЕРЕДЬ ИГРОКА: {safe_p_name}</b>\n───────────────────\n👉 <a href='tg://user?id={p_id}'><b>{safe_p_name}</b></a>, перейди в ЛС и открой 1 карту!\n⏳ <i>У тебя 40 секунд...</i>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )

            settings = await db.get_lobby_settings(chat_id)
            reveal_time = int(settings.get("reveal_time", 40)) if settings else 40
            for _ in range(reveal_time):
                if not await is_game_active(chat_id): return
                if await db.has_revealed_in_round(chat_id, p_id, current_round): break
                await asyncio.sleep(1)

            if not await is_game_active(chat_id): return
            if not await db.has_revealed_in_round(chat_id, p_id, current_round):
                await auto_reveal_single_player(chat_id, p_id, p_name, current_round)

            await asyncio.sleep(2)

        if not await is_game_active(chat_id): return
        await db.set_current_turn(chat_id, 0)

        if not await db.transition_lobby_status(chat_id, "discussion", ["reveal_phase"], current_round):
            return
        has_voting = not (total_count in (3, 4) and current_round < 3)
        settings = await db.get_lobby_settings(chat_id)
        discussion_time = int(settings.get("discussion_time", 60)) if settings else (60 if has_voting else 30)
        if not has_voting:
            discussion_time = min(discussion_time, 60)

        discussion_msg = f"💬 <b>РАУНД {current_round} | ОБСУЖДЕНИЕ ({discussion_time} СЕКУНД)</b>\n───────────────────\n"
        discussion_msg += "Обсуждайте открытые карты и готовьтесь к голосованию!" if has_voting else "Первое голосование откроется в Раунде 3."
        
        await bot.send_message(chat_id, discussion_msg, parse_mode="HTML")
        await asyncio.sleep(discussion_time)

        if not await is_game_active(chat_id): return
        check_lobby = await db.get_lobby(chat_id)
        if check_lobby and check_lobby[0] == "discussion":
            if not has_voting:
                schedule_game_task(chat_id, start_round_flow(chat_id, current_round + 1))
            else:
                schedule_game_task(chat_id, start_voting_flow(chat_id, current_round))

    except asyncio.CancelledError:
        print(f"[GAME] Игровая задача чата {chat_id} отменена (раунд {current_round}).")
        raise
    except Exception as e:
        print(f"[ERROR] Сбой в Раунде {current_round} чата {chat_id}: {type(e).__name__}: {e}")
        try:
            if await is_game_active(chat_id):
                await db.cancel_lobby(chat_id)
                await bot.send_message(chat_id, "⚠️ <b>Игровой поток остановлен из-за технической ошибки.</b>\nИгра не будет запускать бесконечные фоновые задачи. Администратор может запустить новую игру после остановки текущей.", parse_mode="HTML")
        except Exception as notify_error:
            print(f"[ERROR] Не удалось уведомить чат {chat_id}: {notify_error}")

# --- СПЕЦ-КАРТЫ ---

@dp.callback_query(F.data.startswith("use_spec:"))
async def handle_use_special_init(callback: types.CallbackQuery):
    target_chat_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    if not await is_game_active(target_chat_id):
        return await callback.answer("❌ Игра не активна!", show_alert=True)

    alive_players = await db.get_alive_players(target_chat_id)
    alive_ids = {p[1] for p in alive_players}
    if user_id not in alive_ids:
        return await callback.answer("❌ Выбывшие участники не могут использовать спец-карты!", show_alert=True)

    spec_info = await db.get_player_special_info(target_chat_id, user_id)
    if not spec_info or spec_info[1]:
        return await callback.answer("❌ Ты уже использовал свою спец-карту!", show_alert=True)

    if spec_info[2]:
        return await callback.answer("🟨 На тебя наложена Желтая карточка! Спец-карта заблокирована.", show_alert=True)

    card_code = spec_info[0]
    other_players = [p for p in alive_players if p[1] != user_id]
    lobby = await db.get_lobby(target_chat_id)
    phase = lobby[0] if lobby else ""

    # Эти карты активируются только в фазе обсуждения, непосредственно перед РЕАЛЬНЫМ голосованием.
    # Для 3-4 игроков в раундах 1-2 голосования ещё нет, поэтому карту там использовать нельзя.
    total_players = len(await db.get_players(target_chat_id))
    has_voting_this_round = not (total_players in (3, 4) and lobby[4] < 3)
    if card_code in ("vote_redirect", "mute", "captain"):
        if phase != "discussion" or not has_voting_this_round:
            if phase == "discussion" and not has_voting_this_round:
                return await callback.answer("⏳ В этом раунде голосования ещё не будет. Карта доступна перед голосованием.", show_alert=True)
            return await callback.answer("⏳ Эту спец-карту можно использовать только во время обсуждения перед голосованием.", show_alert=True)

    if card_code == "vote_redirect":
        builder = InlineKeyboardBuilder()
        for p_name, p_id in other_players:
            builder.button(text=f"👤 {p_name}", callback_data=f"secret_target:vote_redirect:{p_id}:{target_chat_id}")
        builder.adjust(2)
        await callback.message.answer("🃏 <b>Подмена голосов</b>\nВыбери игрока, на которого тайно будут перенаправлены голоса против тебя, если ты станешь единоличным лидером голосования.", reply_markup=builder.as_markup(), parse_mode="HTML")
        return await callback.answer()

    if card_code == "mute":
        builder = InlineKeyboardBuilder()
        for p_name, p_id in other_players:
            builder.button(text=f"👤 {p_name}", callback_data=f"secret_target:mute:{p_id}:{target_chat_id}")
        builder.adjust(2)
        await callback.message.answer("🤫 <b>Тихий трибунал</b>\nВыбери игрока. Он не получит уведомление, а его сообщения в группе будут молча удаляться до конца текущего раунда.", reply_markup=builder.as_markup(), parse_mode="HTML")
        return await callback.answer()

    if card_code == "captain":
        # Владелец карты сам выбирает капитана, но выбранный игрок ничего не узнаёт.
        builder = InlineKeyboardBuilder()
        for p_name, p_id in other_players:
            builder.button(text=f"👤 {p_name}", callback_data=f"secret_target:captain:{p_id}:{target_chat_id}")
        builder.adjust(2)
        await callback.message.answer(
            "©️ <b>Скрытый капитан</b>\nВыбери игрока, который тайно получит двойной вес голоса.",
            reply_markup=builder.as_markup(), parse_mode="HTML"
        )
        return await callback.answer()

    if card_code == "mirror":
        await db.update_player_special_status(target_chat_id, user_id, special_used=1, shield_active=1)
        await callback.answer("🪞 Зеркальный щит активирован!", show_alert=True)
        await bot.send_message(
            target_chat_id,
            f"🪞 <b>{html.escape(callback.from_user.first_name)}</b> активировал <b>«Зеркальный щит»</b>! Вражеский эффект отразится обратно.",
            parse_mode="HTML"
        )
        return

    builder = InlineKeyboardBuilder()
    for p_name, p_id in other_players:
        builder.button(text=f"👤 {p_name}", callback_data=f"target_spec:{card_code}:{p_id}:{target_chat_id}")
    builder.adjust(2)

    await callback.message.answer(
        f"✨ <b>Применение: {SPECIAL_CARD_NAMES.get(card_code, card_code)}</b>\nВыбери игрока-цель:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("secret_target:"))
async def process_secret_target(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    card_code = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])
    actor_id = callback.from_user.id

    alive = await db.get_alive_players(chat_id)
    alive_ids = {p[1] for p in alive}
    if actor_id not in alive_ids or target_user_id not in alive_ids or actor_id == target_user_id:
        return await callback.answer("❌ Некорректная цель.", show_alert=True)
    spec_info = await db.get_player_special_info(chat_id, actor_id)
    if not spec_info or spec_info[1] or spec_info[0] != card_code:
        return await callback.answer("❌ Эта спец-карта уже недоступна.", show_alert=True)
    lobby = await db.get_lobby(chat_id)
    if not lobby or lobby[0] != "discussion":
        return await callback.answer("⏳ Эту карту можно активировать только во время обсуждения.", show_alert=True)

    total_players = len(await db.get_players(chat_id))
    has_voting_this_round = not (total_players in (3, 4) and lobby[4] < 3)
    if not has_voting_this_round:
        return await callback.answer("⏳ В этом раунде голосования ещё не будет. Карта не потрачена.", show_alert=True)

    if card_code == "vote_redirect":
        await db.set_vote_redirect(chat_id, actor_id, target_user_id, lobby[4])
    elif card_code == "mute":
        await db.set_muted_round(chat_id, target_user_id, lobby[4])
    elif card_code == "captain":
        await db.set_captain(chat_id, target_user_id, lobby[4])
    else:
        return await callback.answer("❌ Неизвестная карта.", show_alert=True)

    await db.update_player_special_status(chat_id, actor_id, special_used=1)
    await callback.message.edit_text("✅ Спец-карта активирована тайно.")
    await callback.answer()

@dp.callback_query(F.data.startswith("target_spec:"))
async def process_special_target(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    card_code = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])
    actor = callback.from_user

    alive_players = await db.get_alive_players(chat_id)
    if actor.id not in {p[1] for p in alive_players}:
        return await callback.answer("❌ Выбывшие игроки не могут использовать спец-карты!", show_alert=True)

    target_info = await db.get_player_special_info(chat_id, target_user_id)
    if target_info and target_info[3] == 1:
        await db.update_player_special_status(chat_id, target_user_id, shield_active=0)
        await bot.send_message(
            chat_id,
            f"🪞 <b>ЗЕРКАЛЬНЫЙ ЩИТ!</b> Игрок <a href='tg://user?id={target_user_id}'>цель</a> отразил спец-карту <b>{SPECIAL_CARD_NAMES.get(card_code, card_code)}</b> обратно в <b>{html.escape(actor.first_name)}</b>!",
            parse_mode="HTML"
        )
        target_user_id = actor.id

    target_name = await db.get_username(chat_id, target_user_id)
    await db.update_player_special_status(chat_id, actor.id, special_used=1)

    if card_code.startswith("swap_"):
        category = card_code.replace("swap_", "")
        actor_pack = await db.get_player_pack(chat_id, actor.id)
        target_pack = await db.get_player_pack(chat_id, target_user_id)

        actor_val = actor_pack.get(category, "-")
        target_val = target_pack.get(category, "-")

        actor_pack[category] = target_val
        target_pack[category] = actor_val

        await db.update_player_pack(chat_id, actor.id, actor_pack)
        await db.update_player_pack(chat_id, target_user_id, target_pack)
        await refresh_private_card(chat_id, actor.id)
        await refresh_private_card(chat_id, target_user_id)

        actor_revealed = await db.is_trait_revealed(chat_id, actor.id, category)
        target_revealed = await db.is_trait_revealed(chat_id, target_user_id, category)

        cat_title = TRAIT_LABELS.get(category, category)

        if actor_revealed or target_revealed:
            await bot.send_message(
                chat_id,
                f"🔄 <b>ОБМЕН ХАРАКТЕРИСТИКАМИ!</b>\n"
                f"<b>{html.escape(actor.first_name)}</b> применил спец-карту «Обмен [{cat_title}]» на <b>{html.escape(target_name)}</b>!\n"
                f"Их карты <b>[{cat_title}]</b> официально поменялись местами.",
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                actor.id,
                f"🔄 <b>Анонимный обмен выполнен!</b>\nТы поменялся картами [{cat_title}] с игроком <b>{html.escape(target_name)}</b>.\n"
                f"Твое новое значение: <b>{target_val}</b>", parse_mode="HTML"
            )
            await bot.send_message(
                target_user_id,
                f"⚠️ <b>Внимание!</b> Кто-то анонимно применил на тебя «Обмен [{cat_title}]»!\n"
                f"Твое новое скрытое значение: <b>{actor_val}</b>", parse_mode="HTML"
            )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "spy":
        target_pack = await db.get_player_pack(chat_id, target_user_id)
        unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)
        if not unrevealed:
            await callback.message.answer("👁 Все карты этого игрока уже открыты!")
        else:
            chosen = random.choice(unrevealed)
            val = target_pack.get(chosen, "-")
            await callback.message.answer(
                f"👁 <b>ШПИОНАЖ:</b> У игрока <b>{html.escape(target_name)}</b> карта <b>[{TRAIT_LABELS.get(chosen, chosen)}]</b>: <code>{val}</code>",
                parse_mode="HTML"
            )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "yellow_card":
        await db.update_player_special_status(chat_id, target_user_id, is_blocked=1)
        await bot.send_message(
            chat_id,
            f"🟨 <b>ЖЕЛТАЯ КАРТОЧКА!</b> <b>{html.escape(actor.first_name)}</b> заблокировал спец-карту игрока <b>{html.escape(target_name)}</b> до конца игры!",
            parse_mode="HTML"
        )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "flash":
        unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)
        if not unrevealed:
            await callback.message.edit_text(f"📸 У игрока {html.escape(target_name)} нет закрытых карт!")
        else:
            builder = InlineKeyboardBuilder()
            for u_trait in unrevealed:
                builder.button(
                    text=f"📸 {TRAIT_LABELS.get(u_trait, u_trait)}", 
                    callback_data=f"act_flash:{u_trait}:{target_user_id}:{chat_id}"
                )
            builder.adjust(2)
            await callback.message.edit_text(
                f"📸 <b>ВСПЫШКА:</b> Выбери, какую закрытую карту игрока <b>{html.escape(target_name)}</b> принудительно вскрыть в общий чат:",
                reply_markup=builder.as_markup(), parse_mode="HTML"
            )

    elif card_code == "chaos":
        actor_unrevealed = await db.get_unrevealed_traits(chat_id, actor.id)
        target_unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)

        if actor_unrevealed and target_unrevealed:
            a_trait = random.choice(actor_unrevealed)
            t_trait = random.choice(target_unrevealed)

            actor_pack = await db.get_player_pack(chat_id, actor.id)
            target_pack = await db.get_player_pack(chat_id, target_user_id)

            actor_pack[a_trait], target_pack[t_trait] = target_pack[t_trait], actor_pack[a_trait]

            await db.update_player_pack(chat_id, actor.id, actor_pack)
            await db.update_player_pack(chat_id, target_user_id, target_pack)
            await refresh_private_card(chat_id, actor.id)
            await refresh_private_card(chat_id, target_user_id)

            await bot.send_message(actor.id, f"🎲 <b>Трансферный хаос:</b> Твоя закрытая карта [{TRAIT_LABELS.get(a_trait, a_trait)}] случайно поменялась с закрытой картой {target_name}!", parse_mode="HTML")
            await bot.send_message(target_user_id, f"🎲 <b>Трансферный хаос:</b> Твоя закрытая карта [{TRAIT_LABELS.get(t_trait, t_trait)}] случайно поменялась с чужой закрытой картой!", parse_mode="HTML")
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    await callback.answer()

@dp.callback_query(F.data.startswith("act_flash:"))
async def process_flash_reveal(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    trait = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])
    actor = callback.from_user

    alive_players = await db.get_alive_players(chat_id)
    if actor.id not in {p[1] for p in alive_players}:
        return await callback.answer("❌ Выбывшие игроки не могут вскрывать карты!", show_alert=True)

    target_name = await db.get_username(chat_id, target_user_id)
    pack = await db.get_player_pack(chat_id, target_user_id)
    val = pack.get(trait, "-")
    if trait == "age" and val != "-":
        val = f"{val} лет"

    await db.record_reveal(chat_id, target_user_id, trait, 0)

    image_url = CARD_IMAGES.get(trait, "")
    msg_text = (
        f'<a href="{image_url}">&#8203;</a>📸 <b>ВСПЫШКА!</b> ' if image_url else '📸 <b>ВСПЫШКА!</b> '
    ) + (
        f'<b>{html.escape(actor.first_name)}</b> принудительно вскрывает у '
        f'<b>{html.escape(target_name)}</b> карту <b>[{TRAIT_LABELS.get(trait, trait)}]</b>:\n'
        f'└ 👉 <b>{html.escape(str(val))}</b>'
    )
    if image_url:
        preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)
        await bot.send_message(chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)
    else:
        await bot.send_message(chat_id, msg_text, parse_mode="HTML")
    await callback.message.edit_text(f"✅ Ты успешно вскрыл карту [{TRAIT_LABELS.get(trait, trait)}] игрока {html.escape(target_name)}!")
    await callback.answer()

# --- СТАРТ ИГРЫ И ВЫДАЧА СПЕЦ-КАРТ ---

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        lobby = await db.get_lobby(chat_id)
        if not lobby:
            return await callback.answer("❌ Это лобби уже не существует.", show_alert=True)
        if lobby[0] != "lobby":
            return await callback.answer("⚠️ Игра уже запущена или завершена.", show_alert=True)

        # Атомарный lock: два быстрых нажатия «Старт» не смогут создать две игры.
        if not await db.try_start_lobby(chat_id):
            return await callback.answer("⚠️ Игра уже запускается.", show_alert=True)

        players = await db.get_players(chat_id)
        num_players = len(players)
        
        if num_players < 3:
            await db.set_lobby_status(chat_id, "lobby")
            return await callback.answer(f"⚠️ Сейчас участников: {num_players}. Нужно минимум 3 человека!", show_alert=True)

        await callback.answer("Игра начинается!")
        
        settings = await db.get_lobby_settings(chat_id)
        game_type = settings.get("game_type", "player") if settings else "player"
        scen = cards.generate_scenario(num_players, game_type=game_type)
        await db.update_lobby_scenario(chat_id, scen["text"], 1)

        packs = cards.generate_game_packs(num_players, scen["line"], game_type=game_type)
        # Баланс спец-карт: карта обмена характеристикой (swap_*)
        # может достаться только ОДНОМУ игроку за матч.
        # Остальные игроки получают карты из пула без обменов.
        settings = await db.get_lobby_settings(chat_id)
        game_type = settings.get("game_type", "player") if settings else "player"
        allowed_swaps = (["swap_budget", "swap_squad", "swap_finance", "swap_infrastructure", "swap_reputation", "swap_problem", "swap_secret"]
                         if game_type == "club" else ["swap_position", "swap_age", "swap_price", "swap_health", "swap_skill", "swap_inventory", "swap_secret"])
        swap_cards = allowed_swaps
        non_swap_cards = [code for code in SPECIAL_CARD_NAMES if not code.startswith("swap_")]

        if num_players >= 1:
            # Ровно один случайный игрок получает один из 7 видов обмена.
            exchange_card = random.choice(swap_cards)
            assigned_cards = [None] * num_players
            exchange_idx = random.randrange(num_players)
            assigned_cards[exchange_idx] = exchange_card

            remaining = num_players - 1
            if remaining <= len(non_swap_cards):
                other_cards = random.sample(non_swap_cards, remaining)
            else:
                other_cards = random.sample(non_swap_cards, len(non_swap_cards))
                other_cards += random.choices(non_swap_cards, k=remaining - len(non_swap_cards))

            random.shuffle(other_cards)
            other_idx = 0
            for i in range(num_players):
                if assigned_cards[i] is None:
                    assigned_cards[i] = other_cards[other_idx]
                    other_idx += 1

        failed_pm_players = []

        for idx, (p_name, p_id, _) in enumerate(players):
            pack = packs[idx]
            await db.add_player(chat_id, p_id, p_name, pack)

            assigned_spec = assigned_cards[idx]
            await db.set_player_special_card(chat_id, p_id, assigned_spec)
            
            pm_card_text = await build_private_card_text(chat_id, p_id)
            markup = await build_reveal_keyboard(chat_id, p_id)
            try:
                pm_message = await bot.send_message(p_id, pm_card_text, reply_markup=markup, parse_mode="HTML")
                await db.set_private_card_message_id(chat_id, p_id, pm_message.message_id)
            except Exception:
                failed_pm_players.append(f"<a href='tg://user?id={p_id}'>{html.escape(p_name)}</a>")

        players_list_str = "\n".join([f"├ 👤 {html.escape(p[0])}" for p in players[:-1]] + [f"└ 👤 {html.escape(players[-1][0])}"])
        pm_warning = f"\n\n⚠️ <b>ВНИМАНИЕ!</b> Не получили карты в ЛС:\n" + ", ".join(failed_pm_players) if failed_pm_players else ""

        scenario_msg = (
            f"🎬 <b>СЦЕНАРИЙ ИГРЫ</b>\n───────────────────\n"
            f"{scen['text']}\n\n"
            f"👥 <b>ПРЕТЕНДЕНТЫ НА КОНТРАКТ:</b>\n{players_list_str}\n"
            f"───────────────────\n"
            f"📩 <i>Карточки и спец-карты отправлены в ЛС.</i>{pm_warning}\n"
            f"⏳ <i>Раунд 1 начнется через 10 секунд!</i>"
        )
        await bot.send_message(chat_id, scenario_msg, parse_mode="HTML")

        await asyncio.sleep(10)
        schedule_game_task(chat_id, start_round_flow(chat_id, 1))

    except Exception as e:
        try:
            lobby = await db.get_lobby(chat_id)
            if lobby and lobby[0] == "starting":
                await db.set_lobby_status(chat_id, "cancelled")
        except Exception:
            pass
        await callback.answer(f"❌ Ошибка старта: {e}", show_alert=True)

# --- КОМАНДЫ БОТА ---

def get_rank(wins: int) -> str:
    if wins >= 50:
        return "🐐 Икона Бункера"
    if wins >= 35:
        return "💎 Легенда"
    if wins >= 20:
        return "🏆 Мастер Бункера"
    if wins >= 10:
        return "👑 Ветеран Бункера"
    if wins >= 6:
        return "🔥 Опасный соперник"
    if wins >= 3:
        return "🏃 Опытный игрок"
    if wins >= 1:
        return "⚽ Любитель"
    return "🥾 Новичок"

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    await message.answer(await economy_profile_text(message.from_user.id, message.from_user.first_name), parse_mode="HTML")

async def private_menu_markup():
    builder = InlineKeyboardBuilder()
    for text, data in [("👤 Профиль","menu:profile"),("🪙 Валюта / Квесты","menu:wallet"),("🛍 Магазин","menu:shop"),("⭐ За Stars","menu:stars"),("🎒 Мои покупки","menu:inventory"),("🃏 Моя карточка","menu:card"),("📜 Правила","menu:rules")]:
        builder.button(text=text, callback_data=data)
    try:
        me = await bot.get_me(); builder.button(text="➕ Добавить в группу", url=f"https://t.me/{me.username}?startgroup=true")
    except Exception: pass
    builder.adjust(2,2,2,1)
    return builder.as_markup()

async def private_welcome_text():
    return (
        "⚽️ <b>ФУТБОЛЬНЫЙ БУНКЕР</b>\n"
        "───────────────────\n"
        "Социальная футбольная игра на блеф, обсуждение и голосование.\n\n"
        "👇 Выбери нужный раздел:"
    )

@dp.callback_query(F.data == "menu:profile")
async def menu_profile(callback: types.CallbackQuery):
    await callback.message.edit_text(await economy_profile_text(callback.from_user.id, callback.from_user.first_name), reply_markup=await private_menu_markup(), parse_mode="HTML"); await callback.answer()

@dp.callback_query(F.data == "menu:wallet")
async def menu_wallet(callback: types.CallbackQuery):
    await callback.message.edit_text(await wallet_text(callback.from_user.id), reply_markup=await private_menu_markup(), parse_mode="HTML"); await callback.answer()

async def stars_store_text() -> str:
    return (
        "⭐ <b>ПОКУПКА ЗА STARS</b>\n"
        "───────────────────\n"
        "Здесь указано, <b>что ты получишь</b>, а ниже — <b>сколько ⭐ это стоит</b>.\n\n"
        "🪙 Игровая валюта → тратится в магазине Бункера.\n"
        "🎁 Косметические наборы → предметы сразу попадут в твои покупки.\n"
        "⭐ Premium → отдельный статус без игровых преимуществ.\n\n"
        "Все покупки добровольные и не дают преимущества в матче."
    )

async def stars_store_keyboard():
    b=InlineKeyboardBuilder()
    for pid, product in STAR_PRODUCTS.items():
        b.button(text=f"{product['name']}  ·  ⭐ {product['stars']}", callback_data=f"stars:buy:{pid}")
    b.button(text="🛍 Магазин за 🪙", callback_data="menu:shop")
    b.button(text="🎒 Мои покупки", callback_data="menu:inventory")
    b.adjust(1)
    return b.as_markup()

def star_invoice_payload(product_id: str) -> str:
    return f"bunker:stars:{product_id}"

async def send_star_invoice(message: types.Message, product_id: str):
    product=STAR_PRODUCTS.get(product_id)
    if not product:
        return await message.answer("Товар не найден.")
    await bot.send_invoice(
        chat_id=message.chat.id,
        title=product["name"],
        description=f"Цена: {product['stars']} Telegram Stars. {product['desc']}",
        payload=star_invoice_payload(product_id),
        currency="XTR",
        prices=[LabeledPrice(label=product["name"], amount=product["stars"])],
        provider_token="",
    )

@dp.callback_query(F.data == "menu:stars")
async def menu_stars(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        return await callback.answer("⭐ Покупки доступны только в личке бота.", show_alert=True)
    await callback.message.edit_text(await stars_store_text(), reply_markup=await stars_store_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("stars:buy:"))
async def stars_buy(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        return await callback.answer("⭐ Покупки доступны только в личке бота.", show_alert=True)
    product_id=callback.data.split(":",2)[2]
    await send_star_invoice(callback.message, product_id)
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery):
    payload=query.invoice_payload
    if payload not in {star_invoice_payload(pid) for pid in STAR_PRODUCTS}:
        return await query.answer(ok=False, error_message="Товар больше недоступен.")
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_star_payment(message: types.Message):
    payment=message.successful_payment
    payload=payment.invoice_payload
    if not payload.startswith("bunker:stars:"):
        return
    product_id=payload.split(":",2)[2]
    product=STAR_PRODUCTS.get(product_id)
    if not product:
        return await message.answer("Платёж получен, но товар не найден. Напиши в поддержку бота.")
    user_id=message.from_user.id
    charge_id = payment.telegram_payment_charge_id
    if not await db.record_star_payment(charge_id, user_id, product_id, product["stars"]):
        return
    if product["kind"] == "coins":
        await db.add_coins(user_id, product["coins"])
        text=f"Готово. На баланс добавлено <b>{product['coins']} 🪙</b>."
    elif product["kind"] == "pack":
        added=[]
        for item_id in product["items"]:
            if not await db.has_purchase(user_id,item_id):
                await db.grant_purchase(user_id,item_id)
                added.append(SHOP_ITEMS[item_id]["name"])
        text="Набор добавлен.\n" + ("\n".join(f"• {x}" for x in added) if added else "Все предметы уже были у тебя.")
    else:
        until=await db.extend_premium(user_id, product["days"])
        await db.add_coins(user_id, 1000)
        text=f"Premium активирован до <b>{until.astimezone().strftime('%d.%m.%Y')}</b>. На баланс добавлено <b>1 000 🪙</b>."
    await message.answer(f"⭐ <b>Оплата прошла</b>\n\n{text}", parse_mode="HTML", reply_markup=await private_menu_markup())

@dp.callback_query(F.data == "menu:shop")
async def menu_shop(callback: types.CallbackQuery):
    await callback.message.edit_text(await economy_shop_text(callback.from_user.id, "home"), reply_markup=await shop_keyboard(callback.from_user.id, "home"), parse_mode="HTML"); await callback.answer()

@dp.callback_query(F.data.startswith("shopcat:"))
async def shop_category(callback: types.CallbackQuery):
    category = callback.data.split(":", 1)[1]
    if category not in ("titles", "phrases"):
        return await callback.answer("Категория не найдена.", show_alert=True)
    await callback.message.edit_text(await economy_shop_text(callback.from_user.id, category), reply_markup=await shop_keyboard(callback.from_user.id, category), parse_mode="HTML")
    await callback.answer()

async def inventory_keyboard(user_id: int):
    b = InlineKeyboardBuilder()
    eq = await db.get_equipped(user_id)
    for category, label in (("title", "🏷 Титулы"), ("victory", "💬 Фразы победы")):
        b.button(text=label, callback_data=f"invcat:{category}")
    b.button(text="🛍 Магазин", callback_data="menu:shop")
    b.button(text="⭐ За Stars", callback_data="menu:stars")
    b.adjust(1)
    return b.as_markup()

@dp.callback_query(F.data == "menu:inventory")
async def menu_inventory(callback: types.CallbackQuery):
    eq = await db.get_equipped(callback.from_user.id)
    title = SHOP_ITEMS.get(eq.get("title"), {}).get("name", "Не выбран")
    phrase = SHOP_ITEMS.get(eq.get("victory"), {}).get("name", "Не выбрана")
    text = (
        "🎒 <b>МОИ ПОКУПКИ</b>\n───────────────────\n"
        f"🏷 Сейчас надето: <b>{html.escape(title)}</b>\n"
        f"💬 Сейчас выбрано: <b>{html.escape(phrase)}</b>\n\n"
        "Выбери категорию — там будут только твои купленные предметы.\n"
        "Экипировка занимает один клик."
    )
    await callback.message.edit_text(text, reply_markup=await inventory_keyboard(callback.from_user.id), parse_mode="HTML"); await callback.answer()

@dp.callback_query(F.data.startswith("invcat:"))
async def inventory_category(callback: types.CallbackQuery):
    category = callback.data.split(":",1)[1]
    items = [(i,x) for i,x in SHOP_ITEMS.items() if x["type"] == category and await db.has_purchase(callback.from_user.id, i)]
    eq = await db.get_equipped(callback.from_user.id)
    label = "🏷 <b>МОИ ТИТУЛЫ</b>" if category == "title" else "💬 <b>МОИ ФРАЗЫ</b>"
    lines = [label, "───────────────────"]
    b = InlineKeyboardBuilder()
    for item_id, item in items:
        equipped = eq.get(category) == item_id
        lines.append(f"{('✅' if equipped else '•')} {html.escape(item['name'])}")
        if equipped:
            b.button(text=f"✅ {item['name']}", callback_data="invnoop")
        else:
            b.button(text=f"⚙️ Экипировать · {item['name']}", callback_data=f"invequip:{category}:{item_id}")
    if not items:
        lines.append("Пока ничего нет.")
    b.button(text="◀️ Мои покупки", callback_data="menu:inventory")
    b.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"); await callback.answer()

@dp.callback_query(F.data == "invnoop")
async def inventory_noop(callback: types.CallbackQuery):
    await callback.answer("Уже экипировано.")

@dp.callback_query(F.data.startswith("invequip:"))
async def inventory_equip(callback: types.CallbackQuery):
    _, category, item_id = callback.data.split(":",2)
    item = SHOP_ITEMS.get(item_id)
    if not item or item["type"] != category or not await db.has_purchase(callback.from_user.id, item_id):
        return await callback.answer("Предмет не найден в твоих покупках.", show_alert=True)
    await db.equip_item(callback.from_user.id, category, item_id)
    await callback.answer(f"Экипировано: {item['name']}")
    await inventory_category(callback)

@dp.callback_query(F.data.startswith("shop:"))
async def shop_action(callback: types.CallbackQuery):
    _, action, item_id = callback.data.split(":",2); item=SHOP_ITEMS.get(item_id)
    if not item: return await callback.answer("❌ Товар не найден.", show_alert=True)
    if action == "buy":
        if not await db.purchase_item(callback.from_user.id,item_id,item["price"]): return await callback.answer("🪙 Не хватает монет или предмет уже куплен.", show_alert=True)
        await db.equip_item(callback.from_user.id,item["type"],item_id); await callback.answer(f"✅ Куплено: {item['name']}")
    else:
        if not await db.has_purchase(callback.from_user.id,item_id): return await callback.answer("Сначала купи предмет.", show_alert=True)
        await db.equip_item(callback.from_user.id,item["type"],item_id); await callback.answer(f"⚙️ Экипировано: {item['name']}")
    category = "titles" if item["type"] == "title" else "phrases"
    await callback.message.edit_text(await economy_shop_text(callback.from_user.id, category), reply_markup=await shop_keyboard(callback.from_user.id, category), parse_mode="HTML")

@dp.callback_query(F.data == "menu:card")
async def menu_card(callback: types.CallbackQuery):
    if callback.message.chat.type != "private":
        return await callback.answer("🃏 Карточка доступна только в личке бота.", show_alert=True)
    active = await db.find_active_player_chat(callback.from_user.id)
    if not active:
        return await callback.answer("ℹ️ Сейчас ты не участвуешь в активной игре.", show_alert=True)
    chat_id = active[0]
    await callback.message.edit_text(
        await build_private_card_text(chat_id, callback.from_user.id),
        reply_markup=await build_reveal_keyboard(chat_id, callback.from_user.id),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu:rules")
async def menu_rules(callback: types.CallbackQuery):
    await callback.message.edit_text(
        await get_rules_text(), reply_markup=await private_menu_markup(), parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu:home")
async def menu_home(callback: types.CallbackQuery):
    await callback.message.edit_text(await private_welcome_text(), reply_markup=await private_menu_markup(), parse_mode="HTML")
    await callback.answer()


def settings_keyboard(settings):
    b = InlineKeyboardBuilder()
    game_type = settings.get("game_type", "player")
    b.button(text=f"{'👤 Игроки' if game_type == 'player' else '🏟️ Клубы'}", callback_data="settings:toggle_type")
    b.button(text=f"💬 Обсуждение: {settings.get('discussion_time',60)}с", callback_data="settings:discussion")
    b.button(text=f"🗳 Голосование: {settings.get('voting_time',105)}с", callback_data="settings:voting")
    b.button(text=f"🎴 Вскрытие: {settings.get('reveal_time',40)}с", callback_data="settings:reveal")
    b.button(text="🔄 Сбросить", callback_data="settings:reset")
    b.adjust(1)
    return b.as_markup()

async def settings_text(chat_id: int) -> str:
    st = await db.get_lobby_settings(chat_id)
    gt = "👤 За игроков" if st.get("game_type") == "player" else "🏟️ За клубы"
    return ("⚙️ <b>НАСТРОЙКИ БУНКЕРА</b>\n───────────────────\n"
            f"🎮 <b>Тип игры:</b> {gt}\n"
            f"💬 <b>Обсуждение:</b> {st.get('discussion_time',60)} сек.\n"
            f"🗳 <b>Голосование:</b> {st.get('voting_time',105)} сек.\n"
            f"🎴 <b>Вскрытие карты:</b> {st.get('reveal_time',40)} сек.\n\n"
            "Настройки применяются к следующей игре. Менять их может создатель лобби.")

async def settings_owner_ok(message_or_callback) -> bool:
    chat = message_or_callback.message.chat if hasattr(message_or_callback, "message") else message_or_callback.chat
    user = message_or_callback.from_user
    lobby = await db.get_lobby(chat.id)
    if not lobby or lobby[0] != "lobby":
        return False
    if lobby[1] == user.id:
        return True
    try:
        member = await bot.get_chat_member(chat.id, user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("⚙️ /settings работает только в группе во время набора игры.")
    if not await settings_owner_ok(message):
        return await message.answer("⛔ Настройки лобби может менять только создатель игры или администратор.")
    st = await db.get_lobby_settings(message.chat.id)
    await message.answer(await settings_text(message.chat.id), reply_markup=settings_keyboard(st), parse_mode="HTML")

@dp.callback_query(F.data.startswith("settings:"))
async def settings_callback(callback: types.CallbackQuery):
    if callback.message.chat.type == "private":
        return await callback.answer("⚙️ Только в группе.", show_alert=True)
    if not await settings_owner_ok(callback):
        return await callback.answer("⛔ Менять настройки может только создатель лобби или администратор.", show_alert=True)
    action=callback.data.split(":",1)[1]
    chat_id=callback.message.chat.id
    st=await db.get_lobby_settings(chat_id)
    if action == "toggle_type":
        await db.set_lobby_setting(chat_id, "game_type", "club" if st.get("game_type") == "player" else "player")
    elif action == "reset":
        await db.reset_lobby_settings(chat_id)
    elif action in ("discussion", "voting", "reveal"):
        key, values = {"discussion": ("discussion_time", [30,45,60,90,120,180,240,300]), "voting": ("voting_time", [45,60,75,90,105,120,150,180]), "reveal": ("reveal_time", [20,30,40,50,60])}[action]
        current=int(st.get(key, values[0])); nxt=values[(values.index(current)+1)%len(values)] if current in values else values[0]
        await db.set_lobby_setting(chat_id, key, nxt)
    st=await db.get_lobby_settings(chat_id)
    await callback.message.edit_text(await settings_text(chat_id), reply_markup=settings_keyboard(st), parse_mode="HTML")
    await callback.answer("Настройки сохранены")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.chat.type == "private":
        await message.answer(await private_welcome_text(), reply_markup=await private_menu_markup(), parse_mode="HTML")
    else:
        await message.answer("⚡️ <b>ФУТБОЛЬНЫЙ БУНКЕР</b> ⚽️\n───────────────────\nВведи <code>/game</code>, чтобы создать игру.", parse_mode="HTML")

async def get_rules_text():
    return (
        "📜 <b>ПРАВИЛА «ФУТБОЛЬНОГО БУНКЕРА»</b>\n───────────────────\n"
        "⚽ <b>Цель:</b> остаться в финальной паре и доказать ИИ, что команда подходит под сценарий.\n\n"
        "🃏 <b>Карточки:</b> позиция, возраст, цена, здоровье, навык, багаж и секрет. В каждом раунде по очереди раскрывается одна карта.\n\n"
        "💬 <b>Обсуждение:</b> после вскрытия игроки обсуждают состав. Некоторые спецкарты можно использовать скрытно именно здесь.\n\n"
        "🗳 <b>Голосование:</b> нельзя голосовать за себя. Игрок с наибольшим числом голосов выбывает.\n\n"
        "⏭️ <b>СКИП:</b> можно выбрать максимум 3 раза за игру. Если скип набирает максимум или сравнивается с лидером, никто не выбывает. Пенальти за скип не запускается.\n\n"
        "🤝 <b>Ничья:</b> при равенстве лидеров никто не выбывает. Две ничьи подряд запускают пенальти.\n\n"
        "🎴 <b>Спецкарты:</b> одноразовые и могут менять ход игры. Некоторые действия скрыты от группы. Карты обмена характеристиками получает только один игрок за матч.\n\n"
        "🏆 <b>Финал:</b> оставшиеся игроки проходят проверку ИИ-судьи. После окончания бот раскрывает карточки всех участников.\n\n"
        "📋 <b>Команды:</b> /game — игра в группе, /profile — статистика, /card — актуальная карточка в ЛС, /stop — остановить игру."
    )

@dp.message(Command("rules"))
async def cmd_rules(message: types.Message):
    text = await get_rules_text()
    if message.chat.type == "private":
        await message.answer(text, reply_markup=await private_menu_markup(), parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")

@dp.message(Command("card"))
async def cmd_card(message: types.Message):
    if message.chat.type != "private":
        return await message.answer("🃏 <b>/card</b> работает только в личных сообщениях с ботом.", parse_mode="HTML")
    active = await db.find_active_player_chat(message.from_user.id)
    if not active:
        return await message.answer("ℹ️ Сейчас ты не участвуешь в активной игре.")
    chat_id = active[0]
    await message.answer(
        await build_private_card_text(chat_id, message.from_user.id),
        reply_markup=await build_reveal_keyboard(chat_id, message.from_user.id),
        parse_mode="HTML"
    )

@dp.message(Command("wallet"))
async def cmd_wallet(message: types.Message):
    if message.chat.type != "private": return await message.answer("🪙 Кошелёк доступен только в ЛС бота.")
    await message.answer(await wallet_text(message.from_user.id), reply_markup=await private_menu_markup(), parse_mode="HTML")

@dp.message(Command("stars"))
async def cmd_stars(message: types.Message):
    if message.chat.type != "private":
        return
    await message.answer(await stars_store_text(), reply_markup=await stars_store_keyboard(), parse_mode="HTML")

@dp.message(Command("shop"))
async def cmd_shop(message: types.Message):
    if message.chat.type != "private": return await message.answer("🛍 Магазин доступен только в ЛС бота.")
    await message.answer(await economy_shop_text(message.from_user.id), reply_markup=await shop_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    if message.chat.type == "private": return await message.answer("Команду /stop нужно вводить в групповом чате игры!")
    chat_id=message.chat.id
    if not await is_game_active(chat_id): return await message.answer("⚠️ В этом чате нет активной игры.")
    participant_ids={p[1] for p in await db.get_players(chat_id)}
    if message.from_user.id not in participant_ids: return await message.answer("⛔ Остановить игру может только участник текущей игры.")
    if not await db.cancel_lobby(chat_id): return await message.answer("⚠️ Игра уже остановлена или завершена.")
    cancel_game_task(chat_id)
    FINALIZING_GAMES.discard(chat_id)
    await message.answer("🛑 <b>ИГРА ПРИНУДИТЕЛЬНО ОСТАНОВЛЕНА!</b>\n───────────────────\nУчастник остановил текущую сессию.", parse_mode="HTML")
@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    if await is_game_active(message.chat.id):
        lobby = await db.get_lobby(message.chat.id)
        status = lobby[0] if lobby else "active"
        return await message.answer(
            "⚠️ <b>В этом чате уже есть активная игра.</b>\n"
            f"Текущий статус: <code>{html.escape(str(status))}</code>\n\n"
            "Сначала завершите её или используйте /stop.",
            parse_mode="HTML"
        )

    created = await db.create_lobby(message.chat.id, message.from_user.id)
    if not created:
        return await message.answer("⚠️ В этой группе уже идёт игра или идёт набор. Дождитесь её окончания.")
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>НАБОР В ФУТБОЛЬНЫЙ БУНКЕР</b>\n───────────────────\n👑 <b>Организатор:</b> {html.escape(message.from_user.first_name)}\n👥 <b>Участников:</b> 0",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        user = callback.from_user
        empty_pack = {"position": "-", "age": "-", "price": "-", "health": "-", "skill": "-", "inventory": "-", "secret": "-"}
        await db.add_player(chat_id, user.id, user.first_name, empty_pack)
        players = await db.get_players(chat_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
        builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
        builder.adjust(1)

        player_list = "\n".join([f"├ 👤 {html.escape(p[0])}" for p in players[:-1]] + [f"└ 👤 {html.escape(players[-1][0])}"])
        await callback.message.edit_text(f"🎮 <b>НАБОР В ФУТБОЛЬНЫЙ БУНКЕР</b>\n───────────────────\n👥 <b>Участники ({len(players)}):</b>\n{player_list}", reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer("Ты успешно вступил в игру!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка входа: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("reveal:"))
async def process_reveal(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(":")
        trait = parts[1]
        target_chat_id = int(parts[2])
        user = callback.from_user

        if not await is_game_active(target_chat_id): return await callback.answer("❌ Игра завершена!", show_alert=True)
        
        alive_players = await db.get_alive_players(target_chat_id)
        if user.id not in {p[1] for p in alive_players}:
            return await callback.answer("❌ Выбывшие игроки не могут вскрывать карты!", show_alert=True)

        lobby = await db.get_lobby(target_chat_id)
        current_round = lobby[4]

        if await db.get_current_turn(target_chat_id) != user.id:
            return await callback.answer("⚠️ Сейчас не твой ход!", show_alert=True)

        if await db.is_trait_revealed(target_chat_id, user.id, trait):
            return await callback.answer("⚠️ Карта уже открыта!", show_alert=True)

        if await db.has_revealed_in_round(target_chat_id, user.id, current_round):
            return await callback.answer(f"⚠️ В этом раунде ты уже вскрывал карту!", show_alert=True)

        pack = await db.get_player_pack(target_chat_id, user.id)
        val = pack.get(trait, "-")
        if trait == "age" and val != "-": val = f"{val} лет"

        await db.record_reveal(target_chat_id, user.id, trait, current_round)
        await db.record_daily_event(user.id, "reveal")
        image_url = CARD_IMAGES.get(trait, "")
        msg_text = ((f'<a href="{image_url}">&#8203;</a>🔓 ' if image_url else '🔓 ') +
                    f'<b>{html.escape(user.first_name)}</b> вскрывает <b>[{TRAIT_LABELS.get(trait, trait)}]</b>:\n└ 👉 <b>{html.escape(str(val))}</b>')
        if image_url:
            preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)
            await bot.send_message(target_chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)
        else:
            await bot.send_message(target_chat_id, msg_text, parse_mode="HTML")
        new_markup = await build_reveal_keyboard(target_chat_id, user.id)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
        await callback.answer(f"Карта {TRAIT_LABELS.get(trait, trait)} открыта!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

# --- ГОЛОСОВАНИЕ И ИТОГИ ---

async def start_voting_flow(chat_id: int, round_num: int):
    if not await is_game_active(chat_id): return
    alive_players = await db.get_alive_players(chat_id)
    if len(alive_players) <= 2:
        await announce_winners_and_end(chat_id, alive_players)
        return

    if not await db.transition_lobby_status(chat_id, "voting", ["discussion"], round_num):
        return
    await db.clear_votes(chat_id)
    settings = await db.get_lobby_settings(chat_id)
    voting_time = int(settings.get("voting_time", 105)) if settings else 105
    summary_text = await build_players_summary(chat_id)

    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote:{p_id}")

    skip_count = await db.get_skip_count(chat_id)
    if skip_count < 3:
        builder.button(text=f"⏭️ СКИП ({skip_count}/3)", callback_data="vote:0")
    builder.adjust(2)

    await bot.send_message(
        chat_id,
        f"📋 <b>ОТКРЫТЫЕ ХАРАКТЕРИСТИКИ</b>\n\n{summary_text}\n───────────────────\n🗳 <b>ГОЛОСОВАНИЕ (Раунд {round_num})</b>\n⏭️ <b>Скип:</b> можно пропустить максимум 3 голосования за игру. Скип работает как ничья — никто не выбывает, пенальти за него не будет.\n⏳ Время: 1 минута 45 секунд.",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await asyncio.sleep(voting_time)

    lobby = await db.get_lobby(chat_id)
    if lobby and lobby[0] == "voting" and lobby[4] == round_num:
        non_voters = await db.get_non_voted_alive_players(chat_id)
        for nv_id, nv_name in non_voters:
            await db.eliminate_player(chat_id, nv_id)
            await bot.send_message(chat_id, f"👞 <b>{html.escape(nv_name)}</b> исключен за AFK!", parse_mode="HTML")
        await finish_voting_flow(chat_id)

@dp.callback_query(F.data.startswith("vote:"))
async def process_vote(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        voter = callback.from_user
        if not await is_game_active(chat_id): return await callback.answer("❌ Нет активной игры!", show_alert=True)
        
        alive_players = await db.get_alive_players(chat_id)
        alive_ids = {p[1] for p in alive_players}
        if voter.id not in alive_ids:
            return await callback.answer("❌ Выбывшие участники и зрители не могут голосовать!", show_alert=True)

        target_id = int(callback.data.split(":")[1])
        if target_id != 0 and voter.id == target_id:
            return await callback.answer("⚠️ За себя голосовать нельзя!", show_alert=True)
        if target_id != 0 and target_id not in alive_ids:
            return await callback.answer("❌ Этот игрок уже выбыл.", show_alert=True)
        if await db.has_user_voted(chat_id, voter.id):
            return await callback.answer("⚠️ Ты уже проголосовал!", show_alert=True)

        if target_id == 0:
            skip_count = await db.get_skip_count(chat_id)
            if skip_count >= 3:
                return await callback.answer("⛔ Лимит скипов (3) уже исчерпан.", show_alert=True)
            await db.add_vote(chat_id, voter.id, 0)
            await db.record_daily_event(voter.id, "vote")
            await callback.answer("⏭️ Скип принят!")
            # Не раскрываем в группе, кто именно выбрал скип.
            await bot.send_message(chat_id, f"⏭️ <b>{html.escape(voter.first_name)}</b> выбрал СКИП.", parse_mode="HTML")
        else:
            target_name = await db.get_username(chat_id, target_id)
            await db.add_vote(chat_id, voter.id, target_id)
            await db.record_daily_event(voter.id, "vote")
            await callback.answer("Голос принят!")
            await bot.send_message(chat_id, f"🗳 <b>{html.escape(voter.first_name)}</b> проголосовал против <b>{html.escape(target_name)}</b>!", parse_mode="HTML")

        if await db.get_voters_count(chat_id) >= len(alive_players):
            await finish_voting_flow(chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка голосования: {e}", show_alert=True)

async def finish_voting_flow(chat_id: int):
    lobby = await db.get_lobby(chat_id)
    if not lobby or lobby[0] not in ("voting", "discussion"):
        return
    if not await db.transition_lobby_status(chat_id, "finishing", ["voting", "discussion"]):
        return

    votes_data = await db.get_votes_detailed(chat_id)
    alive = await db.get_alive_players(chat_id)

    if len(alive) <= 2:
        await announce_winners_and_end(chat_id, alive)
        return

    # Если все голоса отданы за СКИП, votes_data пустой — этот случай тоже должен считаться скипом.
    skip_votes = await db.get_skip_votes_count(chat_id)
    if not votes_data and skip_votes > 0:
        await db.increment_skip_count(chat_id)
        await db.reset_tie_count(chat_id)
        used_skips = await db.get_skip_count(chat_id)
        await db.clear_votes(chat_id)
        await bot.send_message(
            chat_id,
            f"⏭️ <b>СКИП!</b> Все голоса — за пропуск. Никто не выбывает.\nИспользовано скипов: <b>{used_skips}/3</b>.",
            parse_mode="HTML"
        )
        schedule_game_task(chat_id, start_round_flow(chat_id, lobby[4] + 1))
        return

    if not votes_data:
        await db.clear_votes(chat_id)
        schedule_game_task(chat_id, start_round_flow(chat_id, lobby[4] + 1))
        return

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    # «Подмена голосов» проверяется только после полного голосования.
    # Никто в группе не узнает, что карта вообще была активирована.
    redirect = await db.get_vote_redirect(chat_id, lobby[4])
    if redirect:
        owner_id, target_id = redirect
        # В votes_data первая колонка — ТОТ, ПРОТИВ КОГО голосовали,
        # поэтому нельзя искать owner_id среди кандидатов: владелец карты голосует против кого-то другого.
        # Проверяем, что владелец карты действительно получил единоличное большинство.
        owner_row = next((v for v in votes_data if v[0] == owner_id), None)
        if owner_row is not None and owner_row[2] == max_votes and len(top_candidates) == 1:
            await db.redirect_votes(chat_id, owner_id, target_id)
            votes_data = await db.get_votes_detailed(chat_id)
        await db.clear_vote_redirect(chat_id, lobby[4])

    # СКИП — отдельный результат голосования. Если он набрал максимум
    # (в том числе вровень с игроком), никто не выбывает и пенальти не запускается.
    skip_votes = await db.get_skip_votes_count(chat_id)
    max_votes = votes_data[0][2] if votes_data else 0
    if skip_votes > 0 and skip_votes >= max_votes:
        await db.increment_skip_count(chat_id)
        await db.reset_tie_count(chat_id)
        used_skips = await db.get_skip_count(chat_id)
        await db.clear_votes(chat_id)
        await bot.send_message(
            chat_id,
            f"⏭️ <b>СКИП!</b> Голосование пропущено — никто не выбывает.\n"
            f"Использовано скипов: <b>{used_skips}/3</b>.",
            parse_mode="HTML"
        )
        schedule_game_task(chat_id, start_round_flow(chat_id, lobby[4] + 1))
        return

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    if len(top_candidates) > 1:
        await db.clear_votes(chat_id)
        await db.increment_tie_count(chat_id)
        tie_count = await db.get_tie_count(chat_id)
        if tie_count >= 2:
            # Две ничьи подряд — пенальти. Выбираем одного из лидеров.
            chosen = random.choice(top_candidates)
            kicked_id, kicked_name = chosen[0], chosen[1]
            await db.eliminate_player(chat_id, kicked_id)
            await db.reset_tie_count(chat_id)
            await bot.send_message(
                chat_id,
                f"⚽ <b>ПЕНАЛЬТИ!</b> Две ничьи подряд. По пенальти из игры выбыл: <b>{html.escape(kicked_name)}</b>",
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id,
                f"🤝 <b>НИЧЬЯ!</b> Никто не выбывает. Переходим к Раунду {lobby[4] + 1}.",
                parse_mode="HTML"
            )
            schedule_game_task(chat_id, start_round_flow(chat_id, lobby[4] + 1))
            return
    else:
        kicked_id, kicked_name = top_candidates[0][0], top_candidates[0][1]
        await db.reset_tie_count(chat_id)
        await db.eliminate_player(chat_id, kicked_id)
        await db.clear_votes(chat_id)
        await bot.send_message(chat_id, f"❌ Из команды изгнан: <b>{html.escape(kicked_name)}</b>", parse_mode="HTML")

    if len(await db.get_alive_players(chat_id)) <= 2:
        await announce_winners_and_end(chat_id, await db.get_alive_players(chat_id))
    else:
        schedule_game_task(chat_id, start_round_flow(chat_id, lobby[4] + 1))

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_silent_mute(message: types.Message):
    # Молчаливое подавление сообщений только в фазах обсуждения/голосования.
    lobby = await db.get_lobby(message.chat.id)
    if not lobby or lobby[0] not in ("discussion", "voting"):
        return
    if await db.is_muted_for_round(message.chat.id, message.from_user.id, lobby[4]):
        try:
            await message.delete()
        except Exception:
            # Если бот не администратор с правом удаления сообщений, скрытое молчание невозможно.
            pass

async def handle_ping(request):
    return web.Response(text="Bot Alive")

async def handle_health(request):
    try:
        await db.health_check()
        return web.json_response({"status": "ok", "service": "football-bunker", "db": "ok"})
    except Exception as e:
        print(f"[HEALTH] DB error: {type(e).__name__}: {e}")
        return web.json_response({"status": "degraded", "service": "football-bunker", "db": "error"}, status=503)

async def main():
    await db.init_db()
    await economy_owner_grant()
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
